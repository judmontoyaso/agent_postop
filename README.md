# Agente de voz — Seguimiento postoperatorio (Tech Sphere Challenge 2026)

Agente conversacional de voz en tiempo real que hace seguimiento
postoperatorio a pacientes en español colombiano, evalúa síntomas contra una
base de conocimiento clínico (RAG) y decide nivel de escalamiento
(verde/amarillo/rojo) con sesgo explícito contra falsos negativos.

## Modelo declarado (G3)

**Llama 3.1 70B vía Groq** — cloud, latencia ultra-baja. Servido como
`llama-3.3-70b-versatile` (Groq descontinuó el ID original en ene 2025; es su
reemplazo directo). Ver justificación completa y nota sobre próxima baja de
este modelo en `docs/architecture.md`.

## Stack

| Capa | Tecnología |
|---|---|
| LLM (razonamiento + tool calling) | Groq — Llama 3.3 70B (`llama-3.3-70b-versatile`) |
| Voz (STT + turn-detection + TTS streaming) | Deepgram Voice Agent API (BYOM hacia Groq) |
| RAG | ChromaDB + embedder local (`paraphrase-multilingual-MiniLM-L12-v2`) |
| Backend | FastAPI + WebSockets |
| Métricas | SQLite (`app/metrics.py`) |

## Setup (objetivo: <=15 minutos, gate G2)

```bash
# 1. Clonar y crear entorno
git clone <este-repo>
cd techsphere-postop
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac

# 2. Dependencias
pip install -r requirements.txt

# 3. OCR fallback — necesario solo para el PDF escaneado del dataset (trampa,
#    sin capa de texto). Poppler ya viene incluido en bin/poppler/ (portable,
#    no requiere admin ni instalación). Falta tesseract:
#    Windows: instalar desde https://github.com/UB-Mannheim/tesseract/releases
#             (default: C:\Program Files\Tesseract-OCR\tesseract.exe — si se
#             instala en otra ruta, agregarla a TESSERACT_CMD en app/config.py)
#    Linux:   sudo apt install tesseract-ocr tesseract-ocr-spa
#    Mac:     brew install tesseract tesseract-lang

# 4. Configurar variables de entorno
cp .env.example .env
# Rellenar GROQ_API_KEY y DEEPGRAM_API_KEY

# 5. Descargar dataset oficial del reto y colocarlo en:
#    dataset/textos/*.pdf
#    dataset/*.xlsx

# 6. Indexar el conocimiento clínico
python scripts/ingest_dataset.py

# 7. Verificar setup
python scripts/setup_check.py

# 8. Levantar el servidor
uvicorn app.main:app --reload --port 8000
```

Luego:
- Consola admin: http://localhost:8000/admin
- Interfaz de llamada: http://localhost:8000/call

## Métricas obligatorias

Ver `GET /api/metrics/summary` tras una sesión de llamadas de prueba, y la
plantilla llenada en `docs/final-report.md`.

| Métrica | Cómo se mide |
|---|---|
| Latencia P50/P95 (fin de habla -> inicio audio) | `app/metrics.py::summary()` |
| Tokens input/output por turno | idem |
| Invocaciones de modelo por turno | idem |
| RAG queries por llamada | idem |
| Costo estimado por llamada | calculado a mano desde pricing público Groq/Deepgram — ver `docs/final-report.md` |

## Estructura

```
techsphere-postop/
├── app/
│   ├── agent/
│   │   ├── llm_client.py       # Cliente Groq (Llama 3.3 70B)
│   │   ├── decision.py         # Triage verde/amarillo/rojo, hard triggers
│   │   └── tools.py            # Function calling: RAG + escalar_paciente
│   ├── rag/
│   │   ├── ingest.py           # ChromaDB + embedder local, OCR fallback
│   │   ├── query.py            # Consulta desde las tools
│   │   └── admin_routes.py     # G5: subir/listar/borrar documentos
│   ├── voice/
│   │   ├── deepgram_agent.py   # Sesión Deepgram Voice Agent API (BYOM -> Groq)
│   │   └── call_routes.py      # WS bridge navegador <-> Deepgram
│   ├── metrics.py              # SQLite tracker de latencia/tokens/rag
│   ├── config.py
│   └── main.py                 # Entrypoint FastAPI
├── static/
│   ├── admin/index.html        # Consola admin (funcional, no diseño)
│   └── call/index.html         # Interfaz de llamada (mic -> WS -> audio)
├── dataset/                    # Dataset oficial del reto (no versionado)
├── docs/
│   ├── architecture.md         # Diagrama + justificación de modelo
│   ├── decision-flow.md        # Flujo de escalamiento
│   └── final-report.md         # Plantilla del reporte final
├── scripts/
│   ├── ingest_dataset.py
│   └── setup_check.py
└── tests/
```

## Gates eliminatorios — estado

- [ ] G1 — 4 entregables completos
- [ ] G2 — levanta en <=15 min con solo este README
- [x] G3 — modelo declarado dentro de la lista cerrada (Llama 3.1 70B / Groq,
  servido como `llama-3.3-70b-versatile` — ver nota de deprecación arriba)
- [~] G4 — pipeline de voz confirmado en vivo: saludo, STT, transcripción,
  tool calling (RAG) y escalamiento funcionando end-to-end, incluyendo un
  caso verde completo. Bloqueado ahora mismo por límite diario de tokens de
  Groq (tier gratis, se agotó testeando) — falta una corrida limpia sin
  interrupciones tras el fix de latencia (ver docs/final-report.md).
- [x] G5 — consola admin verificado en vivo de punta a punta: subir doc nuevo
  → el agente lo encuentra en la siguiente consulta RAG → borrarlo → el
  agente lo olvida por completo. Confirmado con contenido inventado
  (no coincide con nada del corpus real) para descartar falsos positivos.

RAG: **107/107 PDFs indexados**, incluyendo el escaneado sin texto (trampa)
vía OCR (poppler portable incluido en el repo + tesseract instalado en el
sistema — ver Setup).

## Pendientes conocidos

- Prompts de `SYSTEM_PROMPT` sin iterar del todo contra el dataset real —
  probado en vivo, ajustado dos veces por comportamiento observado (turnos
  demasiado largos, exposición de errores internos del RAG como texto hablado).
- Reconexión automática implementada en `call_routes.py` para cortes de red
  intermitentes (visto en este entorno contra HuggingFace, Voyage AI y
  Deepgram por igual) — no debería tumbar la llamada completa.
- Rate limit de Groq free tier (12000 TPM) puede saturarse en conversaciones
  largas — visto en pruebas reales, causa reconexión. Si el jurado hace una
  llamada muy extensa, riesgo real; considerar tier de pago de Groq antes de
  la demo si el presupuesto lo permite.
