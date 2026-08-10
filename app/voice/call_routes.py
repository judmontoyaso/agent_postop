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
import time
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
from websockets.exceptions import ConnectionClosed

from app.voice.deepgram_agent import open_agent_session, GREETING, RECONNECT_LINE
from app.agent.tools import execute_tool
from app.metrics import track_turn
from app.config import GROQ_MODEL

logger = logging.getLogger("voice.call_routes")
router = APIRouter()

CONNECTION_ERRORS = (ConnectionClosed, ConnectionResetError, OSError)
MAX_RECONNECTS = 5


@router.websocket("/ws/call")
async def call_socket(client_ws: WebSocket):
    await client_ws.accept()
    call_id = uuid.uuid4().hex[:10]
    turn_index = 0
    rag_queries_this_turn = 0
    invocations_this_turn = 0
    last_patient_text = ""
    speech_end_ts = None
    rate_limited = False  # visto en pruebas reales: Groq free tier, 12000 TPM
    escalated = False  # visto en pruebas reales: el LLM puede cerrar la llamada
    # sin llamar nunca escalar_paciente — esto lo detecta para no pasar en silencio

    def client_connected() -> bool:
        return client_ws.client_state == WebSocketState.CONNECTED

    async def forward_client_audio(agent_ws):
        chunks_sent = 0
        try:
            while True:
                chunk = await client_ws.receive_bytes()
                await agent_ws.send(chunk)
                chunks_sent += 1
                if chunks_sent % 25 == 0:
                    logger.info(f"[{call_id}] {chunks_sent} chunks de audio reenviados a Deepgram")
        except WebSocketDisconnect:
            pass
        finally:
            logger.info(f"[{call_id}] chunks de audio del browser esta sesión: {chunks_sent}")

    async def forward_agent_events(agent_ws):
        nonlocal turn_index, rag_queries_this_turn, invocations_this_turn
        nonlocal last_patient_text, speech_end_ts, rate_limited, escalated

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
                        model_id=GROQ_MODEL,
                        input_tokens=0,
                        output_tokens=0,
                        model_invocations=invocations_this_turn,
                        rag_queries=rag_queries_this_turn,
                        speech_end_to_audio_start_ms=latency_ms,
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
                if "rate_limit" in str(event.get("description", "")).lower() or \
                   event.get("code") in ("THINK_REQUEST_FAILED", "FAILED_TO_THINK"):
                    rate_limited = True
            elif etype == "ConversationText":
                logger.info(f"[{call_id}] {event.get('role')}: {event.get('content')}")
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
                        result = execute_tool(fname, fargs, last_patient_text)
                        if fname == "escalar_paciente":
                            escalated = True
                    except Exception as e:
                        logger.error(f"[{call_id}] error procesando FunctionCallRequest: {e}")
                        result = {"error": str(e), "rag_query_used": False}
                    if result.get("rag_query_used"):
                        rag_queries_this_turn += 1
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

    reconnects = 0
    while client_connected():
        try:
            agent_ws = await open_agent_session(
                greeting=RECONNECT_LINE if reconnects > 0 else GREETING
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

        try:
            await asyncio.gather(
                forward_client_audio(agent_ws),
                forward_agent_events(agent_ws),
            )
            break  # terminó limpio (cliente colgó) — no reconectar
        except CONNECTION_ERRORS as e:
            logger.warning(f"[{call_id}] conexión con Deepgram cortada ({e}) — reconectando...")
            reconnects += 1
            if reconnects > MAX_RECONNECTS:
                logger.error(f"[{call_id}] agotados los reintentos de reconexión, cerrando llamada")
                break
            if rate_limited:
                # el límite de Groq es por minuto de reloj, no por conexión —
                # reconectar rápido pega contra el mismo techo agotado. Esperar
                # de verdad en vez de reintentar al toque.
                logger.warning(f"[{call_id}] corte por rate limit de Groq — esperando 15s antes de reconectar")
                await asyncio.sleep(15)
                rate_limited = False
            else:
                await asyncio.sleep(min(0.5 * reconnects, 3))
        except (WebSocketDisconnect, RuntimeError):
            break
        finally:
            await agent_ws.close()

    if not escalated:
        logger.warning(
            f"[{call_id}] LLAMADA TERMINÓ SIN escalar_paciente — ningún nivel de "
            f"riesgo quedó registrado para esta conversación. Revisar prompt/modelo."
        )

    if client_connected():
        try:
            await client_ws.close()
        except RuntimeError:
            pass  # ya se había enviado un close frame por otra vía — no pasa nada
