"""app/agent/llm_client.py — Cliente Groq (Llama 3.1 70B), modelo declarado (G3).

Único LLM de razonamiento del sistema. NO cambiar por otro modelo fuera de la
lista cerrada (stack-tecnico.md) — G3 descalifica.
"""
import logging
from groq import Groq

from app.config import GROQ_API_KEY, GROQ_MODEL

logger = logging.getLogger("llm_client")

_client: Groq | None = None


def get_client() -> Groq:
    global _client
    if _client is None:
        if not GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY no configurada en .env")
        _client = Groq(api_key=GROQ_API_KEY)
    return _client


def chat(messages: list[dict], tools: list[dict] | None = None, tool_choice: str = "auto"):
    """Llamada síncrona a Llama 3.1 70B vía Groq. Retorna el objeto ChatCompletion completo
    (para poder extraer usage.prompt_tokens / completion_tokens hacia metrics.py)."""
    client = get_client()
    kwargs = {"model": GROQ_MODEL, "messages": messages, "temperature": 0.2}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = tool_choice
    return client.chat.completions.create(**kwargs)
