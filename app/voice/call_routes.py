"""app/voice/call_routes.py — WS endpoint que bridgea navegador <-> Deepgram Agent.

Flujo por llamada:
1. Browser abre WS a /ws/call, empieza a mandar chunks PCM16 del mic.
2. Este backend abre su propia sesión WS a Deepgram Agent API y reenvía el audio.
3. Deepgram maneja STT + turn-detection + llama a Groq (BYOM) + TTS streaming.
4. Cuando Deepgram emite FunctionCallRequest (RAG o escalar), este backend
   ejecuta la tool localmente (app/agent/tools.py) y responde FunctionCallResponse.
5. El audio de respuesta (TTS) se reenvía tal cual al browser.
6. Cada turno se registra en app/metrics.py (latencia habla-fin -> audio-inicio,
   tokens, invocaciones, rag_queries).

Reconexión: la red de este entorno corta conexiones WS largas de forma
intermitente (visto también contra HuggingFace y Voyage AI durante el build,
no es específico de Deepgram). Si la sesión con Deepgram se cae mid-llamada,
se reabre sola con RECONNECT_LINE en vez de repetir el saludo completo — un
corte de red no debería tumbar toda la llamada.
"""
import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
from websockets.exceptions import ConnectionClosed

from app.voice.deepgram_agent import (
    open_agent_session,
    available_providers,
    GREETING,
    RECONNECT_LINE,
)
from app.agent.tools import execute_tool
from app.metrics import track_turn
from app.calls import build_summary, save_summary
from app.tokens import CallTokenAccounting
from app.voice.deepgram_agent import SYSTEM_PROMPT, APERTURA_SIN_PACIENTE
from app.agent.tools import TOOLS_SCHEMA
from app.config import GROQ_MODEL, GEMINI_MODEL, THINK_PROVIDER
from app.patients import get_procedure, build_context_prompt, build_greeting, DIAS_POSTOP

logger = logging.getLogger("voice.call_routes")
router = APIRouter()

CONNECTION_ERRORS = (ConnectionClosed, ConnectionResetError, OSError)
MAX_RECONNECTS = 5

# Groq dice en el propio cuerpo del 429 cuánto falta para que se libere la
# ventana ("Please try again in 355ms"). Casi siempre es menos de un segundo:
# el límite es por minuto deslizante, no un castigo fijo. Antes acá se
# esperaban 15s a ciegas, o sea ~40 veces más silencio del necesario, con el
# paciente hablándole a un agente que ya podía responder.
# Cada proveedor lo dice a su manera: Groq "Please try again in 355ms",
# Google "Please retry in 25.8s". Un solo patrón para los dos.
_RETRY_AFTER_RE = re.compile(r"(?:try again|retry) in\s+([\d.]+)\s*(ms|s)\b", re.IGNORECASE)
RATE_LIMIT_FALLBACK_WAIT = 3.0
RATE_LIMIT_MAX_WAIT = 15.0


def _parse_retry_after(description: str) -> float:
    """Segundos a esperar según el 429 de Groq. Cae al fallback si el mensaje
    cambia de formato — nunca reintentar al toque, eso pega contra el mismo
    techo y quema otro intento de reconexión."""
    match = _RETRY_AFTER_RE.search(description or "")
    if not match:
        return RATE_LIMIT_FALLBACK_WAIT
    value = float(match.group(1))
    seconds = value / 1000 if match.group(2).lower() == "ms" else value
    # Margen: la ventana se libera justo en ese instante, y reconectar y
    # reenviar el Settings toma su tiempo. Piso de 0.5s para no martillar.
    return min(max(seconds + 0.3, 0.5), RATE_LIMIT_MAX_WAIT)


def _load_call_context(client_ws: WebSocket) -> dict:
    """Resuelve el contexto de esta llamada desde los query params del WS
    (?nombre=Juan+Pérez&procedimiento=appendicitis&dia=3).

    Si falta el nombre o la patología la llamada sigue funcionando en modo
    genérico — el agente los pregunta él mismo. Se mantiene ese camino a
    propósito: es el que se demostró en vivo antes de existir el formulario."""
    params = client_ws.query_params
    nombre = (params.get("nombre") or "").strip()
    proc = get_procedure((params.get("procedimiento") or "").strip())

    dia = None
    if proc is not None:
        try:
            dia = int(params.get("dia", DIAS_POSTOP[0]))
        except (TypeError, ValueError):
            dia = DIAS_POSTOP[0]
        # Solo se acota a un rango sano; no se exige que esté en DIAS_POSTOP
        # para no romper si alguien llama la API con un día que el select no
        # ofrece.
        dia = max(1, min(dia, 365))

    # Cada dato se usa por separado. Antes esto exigía los dos y si faltaba uno
    # descartaba ambos, así que un nombre escrito a mano sin escoger cirugía
    # acababa en una llamada genérica y sin registro del paciente.
    etiqueta = " — ".join(filter(None, [
        nombre or "(sin nombre)",
        f"{proc['label']}, día {dia}" if proc else "(sin cirugía)",
    ]))

    return {
        "patient_context": build_context_prompt(nombre or None, proc["label"] if proc else None, dia),
        "greeting": build_greeting(nombre or None, proc["label"] if proc else None),
        "rag_category": proc["category"] if proc else None,
        "nombre": nombre or "(no identificado en la llamada)",
        "procedimiento": proc["label"] if proc else "(no declarado)",
        "dia_postop": dia,
        "label": etiqueta,
    }


@router.websocket("/ws/call")
async def call_socket(client_ws: WebSocket):
    await client_ws.accept()
    call_id = uuid.uuid4().hex[:10]
    ctx = _load_call_context(client_ws)
    patient_context = ctx["patient_context"]
    greeting = ctx["greeting"]
    rag_category = ctx["rag_category"]
    paciente_nombre = ctx["nombre"]
    procedimiento_label = ctx["procedimiento"]
    dia_postop = ctx["dia_postop"]
    logger.info(f"[{call_id}] llamada iniciada — {ctx['label']} (corpus RAG: {rag_category or 'todos'})")
    turn_index = 0
    rag_queries_this_turn = 0
    invocations_this_turn = 0
    last_patient_text = ""
    speech_end_ts = None
    rate_limited = False  # visto en pruebas reales: Groq free tier, 12000 TPM
    rate_limit_wait = RATE_LIMIT_FALLBACK_WAIT  # lo dice el propio 429 de Groq
    escalated = False  # visto en pruebas reales: el LLM puede cerrar la llamada
    # sin llamar nunca escalar_paciente — esto lo detecta para no pasar en silencio

    # Material del resumen final (criterio de 20 pts de la rúbrica). Se acumula
    # durante toda la llamada, incluidas las reconexiones: el paciente no tiene
    # por qué perder su historia porque a Groq se le acabó la cuota un segundo.
    started_at = datetime.now(timezone.utc)
    sintomas: list[str] = []       # lo que reportó el paciente, textual
    referencias: list[dict] = []   # documentos del RAG que sustentaron respuestas
    transcripcion: list[dict] = []
    nivel_final = ""
    nivel_llm = ""
    motivo_escalamiento = ""
    rag_queries_total = 0
    turno_input_tokens = 0
    turno_output_tokens = 0
    # Failover entre proveedores permitidos por G3. Groq limita por TOKENS por
    # minuto y Gemini por PETICIONES por minuto: son cuotas independientes, así
    # que cuando una se agota la otra casi nunca lo está. Antes, agotar la única
    # cuota configurada mataba la llamada — y en la prueba real eso pasó justo
    # DESPUÉS de que el agente decidiera "rojo" y ANTES de que se lo dijera al
    # paciente, que es la peor forma posible de fallar en este dominio.
    proveedores = available_providers()
    proveedor_idx = 0
    proveedor_actual = proveedores[0] if proveedores else THINK_PROVIDER

    def modelo_de(prov: str) -> str:
        return GEMINI_MODEL if prov == "google" else GROQ_MODEL

    modelo = modelo_de(proveedor_actual)
    modelos_usados = [modelo]

    # El prompt real que ve el proveedor es el de sistema MÁS el bloque del
    # paciente (o la apertura genérica) — hay que contarlo tal cual se manda.
    tokens = CallTokenAccounting(
        system_prompt=SYSTEM_PROMPT + (patient_context or APERTURA_SIN_PACIENTE),
        tools_schema_json=json.dumps([t["function"] for t in TOOLS_SCHEMA], ensure_ascii=False),
    )

    def client_connected() -> bool:
        return client_ws.client_state == WebSocketState.CONNECTED

    # El micrófono del browser se lee en UNA sola tarea que vive toda la llamada
    # y deja los chunks en esta cola; el envío a Deepgram es otra tarea, por
    # sesión. Antes ambas cosas estaban en la misma corrutina, atada a la
    # sesión, y eso rompía las reconexiones de dos formas:
    #  - al reconectar quedaban dos lectores compitiendo por receive_bytes(),
    #    así que cada uno se llevaba la mitad de los frames y Deepgram recibía
    #    audio picado que nunca llegaba a transcribir;
    #  - durante la espera del rate limit nadie leía el socket, el browser
    #    seguía grabando y al volver se le disparaba a Deepgram un golpe de
    #    audio viejo que arrastraba la conversación varios segundos atrás.
    # Con la cola, el lector nunca se detiene y lo viejo se descarta explícito.
    audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)

    def drop_stale_audio() -> int:
        """Tira el audio grabado mientras no había sesión — es de hace varios
        segundos y mandarlo confunde la detección de turnos de Deepgram."""
        dropped = 0
        while not audio_queue.empty():
            audio_queue.get_nowait()
            dropped += 1
        return dropped

    async def read_client_audio():
        """Único lector de client_ws durante toda la llamada.

        Usa receive() en crudo y no receive_bytes() porque el browser manda dos
        cosas por el mismo socket: audio (binario) y el aviso de colgar (texto).
        Con receive_bytes() un mensaje de texto revienta con KeyError."""
        chunks_read = 0
        try:
            while True:
                message = await client_ws.receive()
                if message["type"] == "websocket.disconnect":
                    break

                texto = message.get("text")
                if texto is not None:
                    # Colgar es un mensaje, no un cierre de socket: el servidor
                    # necesita seguir vivo un instante más para mandar el
                    # resumen de la llamada. Si el browser cerrara el WS de
                    # una, el resumen se generaría contra un socket muerto y el
                    # jurado nunca lo vería en pantalla.
                    try:
                        aviso = json.loads(texto)
                        if aviso.get("type") == "Hangup":
                            logger.info(f"[{call_id}] el paciente colgó desde la interfaz")
                            break
                        if aviso.get("type") == "MicStatus":
                            # Deja en el log si el micrófono estaba mandando voz
                            # o silencio. Es lo que permite distinguir "el mic
                            # está mudo" de "Deepgram no transcribe", que desde
                            # el servidor son indistinguibles: en ambos casos
                            # llegan chunks y no llega ninguna transcripción.
                            silencio = aviso.get("silencio_s", 0)
                            if silencio >= 6:
                                logger.warning(
                                    f"[{call_id}] MIC SIN VOZ hace {silencio}s "
                                    f"({aviso.get('chunks')} chunks enviados) — "
                                    f"el audio que sale del browser es silencio"
                                )
                            else:
                                logger.info(
                                    f"[{call_id}] mic OK — voz hace {silencio}s, "
                                    f"{aviso.get('chunks')} chunks"
                                )
                    except (json.JSONDecodeError, AttributeError):
                        pass
                    continue

                chunk = message.get("bytes")
                if chunk is None:
                    continue
                chunks_read += 1
                if audio_queue.full():
                    # Cola llena = la sesión con Deepgram no está drenando
                    # (caída o reconectando). Se descarta lo más viejo para no
                    # bloquear al lector ni crecer sin límite en memoria.
                    audio_queue.get_nowait()
                audio_queue.put_nowait(chunk)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            logger.info(f"[{call_id}] chunks de audio leídos del browser: {chunks_read}")

    async def forward_client_audio(agent_ws):
        chunks_sent = 0
        while True:
            chunk = await audio_queue.get()
            await agent_ws.send(chunk)
            chunks_sent += 1
            if chunks_sent % 25 == 0:
                logger.info(f"[{call_id}] {chunks_sent} chunks de audio reenviados a Deepgram")

    async def forward_agent_events(agent_ws):
        nonlocal turn_index, rag_queries_this_turn, invocations_this_turn
        nonlocal last_patient_text, speech_end_ts, rate_limited, escalated
        nonlocal rate_limit_wait, nivel_final, nivel_llm, motivo_escalamiento
        nonlocal rag_queries_total, turno_input_tokens, turno_output_tokens

        async for message in agent_ws:
            if not client_connected():
                break

            if isinstance(message, bytes):
                try:
                    await client_ws.send_bytes(message)
                except (WebSocketDisconnect, RuntimeError):
                    break
                if speech_end_ts is not None:
                    latency_ms = (time.monotonic() - speech_end_ts) * 1000
                    track_turn(
                        call_id=call_id,
                        turn_index=turn_index,
                        model_id=modelo,
                        input_tokens=turno_input_tokens,
                        output_tokens=turno_output_tokens,
                        model_invocations=invocations_this_turn,
                        rag_queries=rag_queries_this_turn,
                        speech_end_to_audio_start_ms=latency_ms,
                        # La columna existía pero nunca se llenaba: sin esto no
                        # hay forma de cruzar una decisión de escalamiento con
                        # el turno en que se tomó.
                        escalation_level=nivel_final,
                    )
                    turn_index += 1
                    rag_queries_this_turn = 0
                    invocations_this_turn = 0
                    speech_end_ts = None
                continue

            event = json.loads(message)
            etype = event.get("type")
            if etype in ("Warning", "Error"):
                logger.warning(f"[{call_id}] evento Deepgram: {etype} — {event}")
                description = str(event.get("description", ""))
                if "rate_limit" in description.lower() or \
                   event.get("code") in ("THINK_REQUEST_FAILED", "FAILED_TO_THINK"):
                    rate_limited = True
                    if "rate_limit" in description.lower():
                        rate_limit_wait = _parse_retry_after(description)
                        # Groq revela el tamaño REAL de la petición en el 429.
                        # Es la única ventana a la cifra verdadera, así que se
                        # aprovecha para medir cuánto se desvía la estimación.
                        real = re.search(r"Requested\s+(\d+)", description)
                        if real:
                            tokens.calibrate(int(real.group(1)))
            elif etype == "ConversationText":
                logger.info(f"[{call_id}] {event.get('role')}: {event.get('content')}")
                transcripcion.append({
                    "role": event.get("role"),
                    "content": event.get("content", ""),
                })
                tokens.add_history(event.get("content", ""))
                if event.get("role") == "assistant":
                    # El turno se cierra cuando el agente responde: es ahí donde
                    # se sabe qué costó de entrada y qué produjo de salida.
                    ultimo_turno_tokens = tokens.record_turn(event.get("content", ""))
                    turno_input_tokens, turno_output_tokens = ultimo_turno_tokens
            else:
                logger.info(f"[{call_id}] evento Deepgram: {etype}")

            if etype == "UserStartedSpeaking":
                speech_end_ts = None
            elif etype == "ConversationText" and event.get("role") == "user":
                # Marca real de "el usuario terminó de hablar" — Deepgram no emite
                # EndOfThought/UtteranceEnd en la práctica (se confirmó en logs
                # reales); la transcripción final del turno del usuario sí llega
                # siempre y es el proxy correcto para medir la latencia P50/P95
                # que pide la rúbrica (fin de habla -> inicio de audio del agente).
                last_patient_text = event.get("content", "")
                speech_end_ts = time.monotonic()
                if last_patient_text.strip():
                    sintomas.append(last_patient_text.strip())
            elif etype == "FunctionCallRequest":
                invocations_this_turn += 1
                logger.info(f"[{call_id}] FunctionCallRequest crudo: {event}")
                for call in event.get("functions", [event]):
                    try:
                        fname = call.get("function_name") or call.get("name")
                        raw_args = (
                            call.get("input")
                            or call.get("arguments")
                            or call.get("parameters")
                            or "{}"
                        )
                        fargs = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                        result = execute_tool(fname, fargs, last_patient_text, rag_category)
                        if fname == "escalar_paciente":
                            escalated = True
                            # Se queda el nivel MÁS ALTO de toda la llamada: si
                            # el agente escaló a rojo y después dijo verde, lo
                            # que importa clínicamente es el rojo.
                            nuevo = result.get("nivel_final", "")
                            orden = {"": 0, "verde": 1, "amarillo": 2, "rojo": 3}
                            if orden.get(nuevo, 0) >= orden.get(nivel_final, 0):
                                nivel_final = nuevo
                                nivel_llm = result.get("nivel_llm", "")
                                motivo_escalamiento = result.get("motivo", "")
                    except Exception as e:
                        logger.error(f"[{call_id}] error procesando FunctionCallRequest: {e}")
                        result = {"error": str(e), "rag_query_used": False}
                    if result.get("rag_query_used"):
                        rag_queries_this_turn += 1
                        rag_queries_total += 1
                        for fuente in result.get("_fuentes", []):
                            referencias.append({
                                "consulta": result.get("_consulta", ""),
                                "documento": fuente["source"],
                                "relevancia": fuente["relevance"],
                            })
                    # Las claves internas no viajan al modelo: son para el
                    # resumen, y mandarlas solo gastaría tokens del turno.
                    result = {k: v for k, v in result.items() if not k.startswith("_")}
                    # El resultado de la tool entra al historial del proveedor
                    # y se reenvía en cada turno posterior — es una de las
                    # cosas que más engorda la conversación.
                    tokens.add_history(json.dumps(result, ensure_ascii=False))
                    # Schema real (developers.deepgram.com/docs/voice-agent-function-call-response):
                    # {"type": "FunctionCallResponse", "id": ..., "name": ..., "content": ...}
                    await agent_ws.send(json.dumps({
                        "type": "FunctionCallResponse",
                        "id": call.get("id"),
                        "name": fname,
                        "content": json.dumps(result),
                    }))
            elif etype == "Error":
                logger.error(f"[{call_id}] Deepgram Agent error: {event}")

            if client_connected():
                try:
                    await client_ws.send_text(json.dumps(event))
                except (WebSocketDisconnect, RuntimeError):
                    break

    reader_task = asyncio.create_task(read_client_audio())

    reconnects = 0
    cierre_sin_excepcion = False
    while client_connected():
        try:
            agent_ws = await open_agent_session(
                greeting=RECONNECT_LINE if reconnects > 0 else greeting,
                patient_context=patient_context,
                provider=proveedor_actual,
            )
        except Exception as e:
            logger.error(f"[{call_id}] No se pudo abrir sesión Deepgram Agent: {e}")
            if reconnects == 0:
                await client_ws.close(code=1011, reason="voice backend unavailable")
                return
            reconnects += 1
            if reconnects > MAX_RECONNECTS:
                logger.error(f"[{call_id}] agotados los reintentos de reconexión, cerrando llamada")
                await client_ws.close(code=1011, reason="voice backend unstable")
                return
            await asyncio.sleep(min(0.5 * reconnects, 3))
            continue

        if reconnects > 0:
            # Solo al reconectar. En la primera conexión lo que hay en la cola
            # es audio recién grabado mientras abría la sesión, no audio viejo:
            # tirarlo recortaría el arranque de la llamada.
            dropped = drop_stale_audio()
            if dropped:
                logger.info(f"[{call_id}] descartados {dropped} chunks de audio viejo antes de reanudar")

        session_tasks = (
            asyncio.create_task(forward_client_audio(agent_ws)),
            asyncio.create_task(forward_agent_events(agent_ws)),
        )
        try:
            # FIRST_COMPLETED, no FIRST_EXCEPTION: forward_client_audio ya no
            # toca el socket del browser, se queda esperando en la cola, así
            # que si la sesión con Deepgram termina SIN excepción (cierre
            # limpio) FIRST_EXCEPTION esperaría a las dos y colgaría el
            # apagado del servidor para siempre. Con FIRST_COMPLETED, en
            # cuanto cualquiera de las tres termina se desarma la sesión.
            # reader_task entra en la espera pero nunca se cancela acá: vive
            # toda la llamada y es quien avisa que el browser colgó.
            done, _ = await asyncio.wait(
                [*session_tasks, reader_task], return_when=asyncio.FIRST_COMPLETED
            )
            for task in session_tasks:
                task.cancel()
            await asyncio.gather(*session_tasks, return_exceptions=True)

            fallo = None
            for task in session_tasks:
                if not task.cancelled() and task.exception() is not None:
                    fallo = task.exception()

            if reader_task in done:
                logger.info(f"[{call_id}] el browser colgó — cerrando llamada")
                break
            if not client_connected():
                break

            # Llegar acá significa que la sesión con Deepgram terminó pero el
            # paciente sigue en línea. Puede haber sido con excepción o SIN
            # ella: tras FAILED_TO_THINK, Deepgram cierra el WS limpiamente, y
            # antes ese camino se tomaba por "la llamada acabó bien" y salía
            # del bucle — el failover quedaba en una rama inalcanzable. Los dos
            # finales son la misma cosa: se cayó el proveedor, hay que recuperar.
            cierre_sin_excepcion = fallo is None
            raise fallo if fallo is not None else ConnectionClosed(None, None)
        except CONNECTION_ERRORS as e:
            motivo = ("cerrada por el proveedor sin excepción (típico tras FAILED_TO_THINK)"
                      if cierre_sin_excepcion else f"cortada ({e})")
            cierre_sin_excepcion = False
            logger.warning(f"[{call_id}] sesión con Deepgram {motivo} — recuperando...")
            reconnects += 1
            if reconnects > MAX_RECONNECTS:
                logger.error(f"[{call_id}] agotados los reintentos de reconexión, cerrando llamada")
                break
            if rate_limited:
                # Primero intentar CAMBIAR de proveedor, que recupera la llamada
                # en el acto. Solo si no hay otro se espera, porque el límite es
                # por minuto de reloj y reconectar al mismo pega contra el mismo
                # techo agotado.
                if len(proveedores) > 1:
                    proveedor_idx = (proveedor_idx + 1) % len(proveedores)
                    anterior, proveedor_actual = proveedor_actual, proveedores[proveedor_idx]
                    modelo = modelo_de(proveedor_actual)
                    if modelo not in modelos_usados:
                        modelos_usados.append(modelo)
                    logger.warning(
                        f"[{call_id}] cuota agotada en {anterior} — cambiando a "
                        f"{proveedor_actual} ({modelo}) y siguiendo la llamada"
                    )
                    if client_connected():
                        try:
                            await client_ws.send_text(json.dumps({
                                "type": "ProviderSwitch",
                                "from": anterior,
                                "to": proveedor_actual,
                                "model": modelo,
                                "description": f"Cuota agotada en {anterior}; "
                                               f"la llamada sigue con {modelo}.",
                            }))
                        except (WebSocketDisconnect, RuntimeError):
                            pass
                    rate_limited = False
                    rate_limit_wait = RATE_LIMIT_FALLBACK_WAIT
                    continue

                logger.warning(
                    f"[{call_id}] corte por rate limit y no hay otro proveedor — esperando "
                    f"{rate_limit_wait:.2f}s (según el propio 429) antes de reconectar"
                )
                # Avisarle al browser: sin esto el usuario ve el orbe quieto y
                # sigue hablándole a un agente que no está escuchando.
                if client_connected():
                    try:
                        await client_ws.send_text(json.dumps({
                            "type": "AgentPaused",
                            "reason": "rate_limit",
                            "seconds": round(rate_limit_wait, 1),
                            "description": "Se alcanzó el límite por minuto de Groq. "
                                           f"Reanudando en {rate_limit_wait:.1f}s.",
                        }))
                    except (WebSocketDisconnect, RuntimeError):
                        pass
                await asyncio.sleep(rate_limit_wait)
                rate_limited = False
                rate_limit_wait = RATE_LIMIT_FALLBACK_WAIT
            else:
                await asyncio.sleep(min(0.5 * reconnects, 3))
        except (WebSocketDisconnect, RuntimeError):
            break
        finally:
            # Con timeout: cerrar un WS de websockets implica un handshake de
            # cierre, y como la tarea que lo estaba leyendo se acaba de
            # cancelar a mitad de un recv, ese handshake puede no completarse
            # nunca. Sin el límite, colgar desde la interfaz dejaba el handler
            # bloqueado acá y el resumen de la llamada no se generaba jamás.
            try:
                await asyncio.wait_for(agent_ws.close(), timeout=3)
            except (asyncio.TimeoutError, *CONNECTION_ERRORS):
                logger.warning(f"[{call_id}] la sesión Deepgram no cerró limpio — se abandona")

    reader_task.cancel()
    await asyncio.gather(reader_task, return_exceptions=True)
    logger.info(f"[{call_id}] sesión desarmada, generando resumen")

    if not escalated:
        logger.warning(
            f"[{call_id}] LLAMADA TERMINÓ SIN escalar_paciente — ningún nivel de "
            f"riesgo quedó registrado para esta conversación. Revisar prompt/modelo."
        )

    resumen = build_summary(
        call_id=call_id,
        started_at=started_at,
        paciente=paciente_nombre,
        procedimiento=procedimiento_label,
        dia_postop=dia_postop,
        # Si hubo failover quedan los dos: el informe tiene que poder decir qué
        # modelo razonó cada parte de la llamada.
        modelo=" → ".join(modelos_usados),
        nivel_final=nivel_final,
        nivel_llm=nivel_llm,
        motivo=motivo_escalamiento,
        sintomas=sintomas,
        referencias=referencias,
        transcripcion=transcripcion,
        turnos=turn_index,
        rag_queries=rag_queries_total,
    )
    save_summary(resumen)

    # Mandarlo antes de cerrar: el jurado cuelga y ve en pantalla qué quedó
    # registrado de la llamada, sin tener que ir a buscar a la base de datos.
    if client_connected():
        try:
            await client_ws.send_text(json.dumps({"type": "CallSummary", "summary": resumen}))
        except (WebSocketDisconnect, RuntimeError):
            pass

    if client_connected():
        try:
            await client_ws.close()
        except RuntimeError:
            pass  # ya se había enviado un close frame por otra vía — no pasa nada
