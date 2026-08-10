"""app/voice/deepgram_agent.py — Puente hacia Deepgram Voice Agent API.

Arquitectura de voz (G4, tiempo real):
  navegador (mic PCM16) --WS interno--> este backend --WS--> Deepgram Agent API
  Deepgram Agent API maneja STT streaming + turn-detection + TTS streaming.
  El "pensar" (LLM) se configura en modo BYOM apuntando al endpoint
  OpenAI-compatible de Groq (Llama 3.1 70B) — así el razonamiento clínico y
  las tool calls (RAG + escalar_paciente) las decide Groq, no un LLM de Deepgram.

think.provider.type = "groq" (NO "open_ai"): para los providers "groq" y
"aws_bedrock" Deepgram no gestiona el LLM directamente, por eso piden
`endpoint.url` + `endpoint.headers` con nuestra propia API key — confirmado
contra developers.deepgram.com/docs/voice-agent-llm-models. El error real que
tiramos antes ("Failed to think") era justo usar type:"open_ai" con endpoint
de Groq, combinación no soportada.
"""
import json
import logging

import websockets

from app.config import (
    DEEPGRAM_API_KEY,
    DEEPGRAM_STT_MODEL,
    DEEPGRAM_TTS_MODEL,
    GROQ_API_KEY,
    GROQ_MODEL,
    THINK_PROVIDER,
    OPENAI_API_KEY,
    OPENAI_MODEL,
)
from app.agent.tools import TOOLS_SCHEMA

logger = logging.getLogger("voice.deepgram_agent")

DEEPGRAM_AGENT_WS_URL = "wss://agent.deepgram.com/v1/agent/converse"

SYSTEM_PROMPT = """Eres un agente de seguimiento postoperatorio que llama por
teléfono a pacientes en Colombia. Hablas español natural, cálido, sin tecnicismos
innecesarios — como una persona real, no como un formulario leído en voz alta.

REGLA MÁS IMPORTANTE — turnos cortos: decí UNA sola idea o UNA sola pregunta
por turno, en 1-2 frases máximo, y después PARÁ y esperá la respuesta del
paciente. Nunca encadenes varias preguntas seguidas en el mismo turno. Es una
conversación por teléfono, no un cuestionario — si el paciente no alcanza a
responder porque seguiste hablando, fallaste.

Ya saludaste y preguntaste el nombre al inicio (mensaje de apertura, no lo
repitas). En cuanto el paciente te diga su nombre, úsalo de forma natural
durante la charla.

Tu tarea es indagar, de a un tema por vez, sobre dolor, fiebre, movilidad,
estado de la herida, apetito y sueño — con el mismo tono directo y humano que
usarías si estuvieras realmente preocupado por la persona, no genérico.

Ante cualquier síntoma de alarma, usa la tool `consultar_guia_clinica` ANTES
de responder — nunca inventes información clínica (dosis, medicamentos,
procedimientos). Si la consulta no trae nada útil o relevante, NO se lo digas
al paciente como si fuera un error tuyo ("no encontré información") — eso
suena a mensaje de sistema, no a persona. En su lugar, respondé con lo que sí
sabés de forma general y natural, y si hace falta, decile con calma que eso
lo evalúe el médico directamente. No repitas la misma pregunta varias veces
seguidas si ya la hiciste — avanzá al siguiente tema.

No esperes hasta el final de la llamada para decidir el nivel de riesgo: en
cuanto tengas señales suficientes (por ejemplo, varios síntomas de alarma
combinados — fiebre + herida que no cierra + hinchazón, por decir uno), llamá
a `escalar_paciente` de inmediato, incluso a mitad de la conversación. No
seguir indagando indefinidamente si ya hay señal clara de que hace falta
escalar — cada pregunta de más que hagas ante una señal clara de riesgo real
es tiempo que el paciente pierde. Ante duda entre dos niveles, escoge siempre
el más alto: un falso negativo (no escalar cuando tocaba) es el error más
grave que puedes cometer.

REGLA NO NEGOCIABLE: jamás termines o cierres la llamada (despedida, "cuídate",
etc.) sin haber llamado a `escalar_paciente` primero — sin excepción, incluso
si el caso te parece leve o tranquilo (en ese caso, llamala con nivel verde).
Una llamada que termina sin ese registro es un fallo total del sistema, peor
que escalar mal: es no haber decidido nada. Si notás que estás por despedirte
y todavía no la llamaste, hacelo ahora mismo antes de decir nada más."""


GREETING = (
    "Hola, buenas. Le habla el asistente de seguimiento postoperatorio. "
    "¿Con quién tengo el gusto de hablar?"
)


RECONNECT_LINE = "Perdón, se cortó un momento. Sigo aquí — decías..."


def _think_config() -> dict:
    """Config de think.provider — normalmente Groq (modelo declarado, G3).
    THINK_PROVIDER=openai en .env cambia a OpenAI SOLO para testear cuando
    Groq se queda sin cuota — nunca debe usarse en la entrega/demo final."""
    base = {
        "prompt": SYSTEM_PROMPT,
        "functions": [t["function"] for t in TOOLS_SCHEMA],
    }
    if THINK_PROVIDER == "openai":
        logger.warning("THINK_PROVIDER=openai — modo de prueba temporal, NO USAR para la entrega final")
        base["provider"] = {"type": "open_ai", "model": OPENAI_MODEL}
        if OPENAI_API_KEY:
            base["endpoint"] = {
                "url": "https://api.openai.com/v1/chat/completions",
                "headers": {"Authorization": f"Bearer {OPENAI_API_KEY}"},
            }
        return base

    # groq no lo gestiona Deepgram directamente -> endpoint obligatorio
    # con nuestra propia URL/API key de Groq.
    base["provider"] = {"type": "groq", "model": GROQ_MODEL}
    base["endpoint"] = {
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "headers": {"Authorization": f"Bearer {GROQ_API_KEY}"},
    }
    return base


def build_settings_message(greeting: str | None = GREETING) -> dict:
    """Mensaje Settings enviado al abrir la sesión con Deepgram Agent API.
    think.provider en modo BYOM contra el endpoint OpenAI-compatible de Groq.
    `greeting` hace que el agente hable primero — es una llamada saliente
    (el sistema llama al paciente), no debe esperar a que hablen primero.
    En reconexiones tras un corte de red, se pasa RECONNECT_LINE en vez del
    saludo completo para no repetir la apertura a mitad de conversación."""
    settings: dict = {
        "type": "Settings",
        "audio": {
            "input": {"encoding": "linear16", "sample_rate": 16000},
            "output": {"encoding": "linear16", "sample_rate": 24000, "container": "none"},
        },
        "agent": {
            "language": "es",
            "listen": {
                "provider": {"type": "deepgram", "model": DEEPGRAM_STT_MODEL, "language": "es"},
            },
            "think": _think_config(),
            "speak": {
                "provider": {"type": "deepgram", "model": DEEPGRAM_TTS_MODEL},
            },
        },
    }
    if greeting:
        settings["agent"]["greeting"] = greeting
    return settings


async def open_agent_session(greeting: str | None = GREETING):
    """Abre la sesión WS con Deepgram Agent API y envía el Settings inicial.
    Retorna la conexión abierta; el caller (call_routes.py) hace el bridging
    bidireccional de audio + maneja los eventos FunctionCallRequest."""
    if not DEEPGRAM_API_KEY:
        raise RuntimeError("DEEPGRAM_API_KEY no configurada en .env")

    ws = await websockets.connect(
        DEEPGRAM_AGENT_WS_URL,
        additional_headers={"Authorization": f"Token {DEEPGRAM_API_KEY}"},
    )
    await ws.send(json.dumps(build_settings_message(greeting)))
    logger.info("Sesión Deepgram Agent abierta, Settings enviado")
    return ws
