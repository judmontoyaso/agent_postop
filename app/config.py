"""app/config.py — Carga de variables de entorno, centralizado."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# THINK_PROVIDER: qué modelo razona en el agente.
#   "groq"   -> Meta Llama vía Groq       (familia permitida, G3)
#   "google" -> Google Gemini gama Flash  (familia permitida, G3)
#   "openai" -> NO PERMITIDO por la rúbrica. Solo para depurar en local;
#               dejarlo puesto en la entrega descalifica.
# La lista de G3 fija FAMILIAS, no versiones: docs/stack-tecnico.md del reto
# permite Gemini Flash igual que Llama/Groq. Groq da menor latencia (LPU) y
# Gemini da un techo de tokens mucho más alto; el informe final debe declarar
# cuál se usó y por qué.
THINK_PROVIDER = os.getenv("THINK_PROVIDER", "groq")

# Gemini — API key de Google AI Studio. Las de Vertex AI, Workspace Gemini o
# Gemini Enterprise NO sirven acá (lo dice la doc de Deepgram explícitamente).
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# gemini-2.5-flash ya no se sirve ("no longer available", verificado contra la
# API). Los que sí responden Y están en la lista soportada por Deepgram:
# gemini-3.5-flash, gemini-3.1-flash-lite, gemini-3-flash-preview.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
DEEPGRAM_STT_MODEL = os.getenv("DEEPGRAM_STT_MODEL", "nova-2")
DEEPGRAM_TTS_MODEL = os.getenv("DEEPGRAM_TTS_MODEL", "aura-2-celeste-es")

CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", str(BASE_DIR / "data" / "chroma"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")

ADMIN_DB_PATH = os.getenv("ADMIN_DB_PATH", str(BASE_DIR / "data" / "admin.db"))
METRICS_DB_PATH = os.getenv("METRICS_DB_PATH", str(BASE_DIR / "data" / "metrics.db"))
DATASET_DIR = os.getenv("DATASET_DIR", str(BASE_DIR / "dataset"))

# Webhook al que se avisa cuando una llamada escala a amarillo o rojo. Vacío =
# no se avisa a nadie (se registra en el log). Opcional a propósito: el
# proyecto tiene que levantarse sin depender de servicios externos (G2).
ESCALATION_WEBHOOK_URL = os.getenv("ESCALATION_WEBHOOK_URL", "")
API_PORT = int(os.getenv("API_PORT", "8000"))

# OCR fallback (PDF escaneado sin texto) — ruta explícita en vez de depender
# del PATH del sistema (un server ya corriendo no ve actualizaciones de PATH
# hechas por un instalador después de que arrancó el proceso).
_poppler_candidates = [
    BASE_DIR / "bin" / "poppler" / "Library" / "bin",  # portable, sin admin
]
POPPLER_PATH = next((str(p) for p in _poppler_candidates if p.exists()), None)

_tesseract_candidates = [
    BASE_DIR / "bin" / "tesseract" / "tesseract.exe",
    Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),  # instalación real del sistema
]
TESSERACT_CMD = next((str(p) for p in _tesseract_candidates if p.exists()), None)

for _p in (Path(CHROMA_DB_PATH).parent, Path(ADMIN_DB_PATH).parent, Path(METRICS_DB_PATH).parent):
    _p.mkdir(parents=True, exist_ok=True)
