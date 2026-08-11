"""app/config.py — Carga de variables de entorno, centralizado."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Raíz del repositorio, no del paquete. Con el layout src/ el paquete vive en
# src/app/, así que hay tres niveles hasta la raíz — y de acá cuelgan data/,
# dataset/, bin/ y static/, que NO son parte del paquete instalable.
BASE_DIR = Path(__file__).resolve().parents[2]

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
# nova-3 y no nova-2: nova-2 ya no aparece en la tabla de precios de Deepgram
# (sigue existiendo como modelo legacy) y nova-3 es la generación vigente, con
# mejor precisión. Se verificó que el Voice Agent API lo acepta en español
# antes de cambiarlo. Importa más de lo que parece: una transcripción que
# convierte "la herida" en "la árida" puede costar un escalamiento.
DEEPGRAM_STT_MODEL = os.getenv("DEEPGRAM_STT_MODEL", "nova-3")
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

# El OCR de los PDFs escaneados no necesita configuración: rasteriza PyMuPDF y
# reconoce EasyOCR, ambos instalados por pip. Ya no hay ninguna dependencia de
# sistema que localizar (antes había dos: poppler y tesseract).

for _p in (Path(CHROMA_DB_PATH).parent, Path(ADMIN_DB_PATH).parent, Path(METRICS_DB_PATH).parent):
    _p.mkdir(parents=True, exist_ok=True)
