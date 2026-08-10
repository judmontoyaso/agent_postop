# Agente de voz — Seguimiento postoperatorio (Tech Sphere Challenge 2026)

Agente conversacional de voz en tiempo real que hace seguimiento
postoperatorio a pacientes en español colombiano, evalúa síntomas contra una
base de conocimiento clínico (RAG) y decide nivel de escalamiento
(verde/amarillo/rojo) con sesgo explícito contra falsos negativos.

## Modelo declarado (G3)

**Meta Llama vía Groq**, servido como `llama-3.3-70b-versatile`. La lista de G3
fija *familias*, no versiones: Groq descontinuó `llama-3.1-70b-versatile` en
ene 2025 y este es su sucesor vigente en el mismo proveedor.

El agente soporta además **Google Gemini gama Flash**, la otra familia de nube
permitida, con `THINK_PROVIDER=google` en `.env`. Se implementó para poder
comparar las dos con datos propios:

| | Groq / Llama 70B | Gemini Flash |
|---|---|---|
| Latencia | Menor (LPU) | Mayor |
| Techo de tokens | 12 000 TPM (medido) | Mucho más alto |
| Corta llamadas largas | Sí, verificado en logs | No observado |

Groq gana en latencia, que puntúa en *Calidad de la conversación (voz)*; Gemini
aguanta conversaciones largas sin cortarse. Cambiar entre ambos es una variable
de entorno, sin tocar código.

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

## Contexto del paciente

La interfaz de llamada pide tres datos antes de marcar: **nombre** (texto
libre), **cirugía** (las 5 del corpus) y **día del postoperatorio**. Las
opciones salen de `GET /api/procedures`.

Sin esto el agente llama a ciegas y no puede juzgar nada: un dolor en el pie
es irrelevante tras una apendicectomía y es justo lo que hay que vigilar tras
un reemplazo de rodilla; fiebre de 37.6 el día 1 es esperable y el día 14 es
alarma. Con el contexto puesto:

- El agente arranca sabiendo a quién llama, de qué lo operaron y en qué día va
  (`app/patients.py`), y lo dice en el saludo.
- `consultar_guia_clinica` acota la búsqueda al corpus de esa patología
  (metadata `category`), en vez de mezclar las 5 del dataset.

Dejando nombre o cirugía en blanco la llamada sigue siendo genérica y el
agente los pregunta él mismo — es el modo con el que se demostró el pipeline
antes de existir el formulario.

Los perfiles de paciente del dataset (`perfiles_*.xlsx`, 40 pacientes con
comorbilidades y trayectorias) **no** los usa la app: se probó un selector
poblado desde ahí y se descartó porque para demostrar importa poder inventar
el caso en el momento. Siguen disponibles para evaluación offline.

## Métricas obligatorias

Ver `GET /api/metrics/summary` tras una sesión de llamadas de prueba, y la
plantilla llenada en `docs/final-report.md`.

| Métrica | Cómo se mide |
|---|---|
| Latencia P50/P95 (fin de habla -> inicio audio) | `app/metrics.py::summary()` — medición real |
| Tokens input/output por turno | `app/tokens.py` — **estimación**, ver nota abajo |
| Invocaciones de modelo por turno | `app/metrics.py::summary()` — medición real |
| RAG queries por llamada | idem |
| Costo estimado por llamada | tokens estimados × pricing público — ver `docs/final-report.md` |

### Nota sobre los tokens: son estimados, no medidos

En esta arquitectura el backend **nunca ve el `usage` real**. Deepgram habla
directo con Groq/Gemini en modo BYOM y solo reenvía eventos de conversación; el
`usage` se queda entre Deepgram y el proveedor. Interceptarlo con un proxy
propio en `endpoint.url` exigiría exponer esta máquina a Internet con un túnel
público solo para medir.

Lo que se hace: reconstruir el prompt que Deepgram arma en cada turno (prompt de
sistema + schema de tools + historial completo + resultados de tools) y contarlo
localmente a ~3.7 caracteres por token. `GET /api/metrics/summary` devuelve
`tokens_son_estimados: true` para que quede explícito.

**Cómo se verifica:** cuando Groq responde 429 incluye el conteo real de esa
petición (`"Used 10271, Requested 3735"`). Cada vez que ocurre, el sistema
compara contra su propia estimación del mismo instante y registra el error en el
log (`app/tokens.py::calibrate`). El número reportado tiene contraste contra el
proveedor, no es una cifra suelta.

## Resumen de llamada

Al colgar, el sistema genera y persiste un resumen estructurado con: paciente y
procedimiento, día postoperatorio, síntomas reportados textualmente, decisión de
escalamiento (con el nivel que propuso el modelo si un disparador de seguridad
lo elevó), documentos del corpus que sustentaron las respuestas, próximos pasos
y la transcripción completa.

- Se muestra en pantalla al colgar, sin salir de la interfaz de llamada.
- Se persiste en SQLite y se consulta en `GET /api/calls` y `GET /api/calls/{id}`.
- `GET /api/calls` incluye `sin_decision`: cuántas llamadas terminaron sin
  ningún nivel de riesgo registrado. Una llamada sin decisión no es un "verde
  por defecto", es un fallo del sistema, y se marca como tal.

El resumen es un registro estructurado, no un párrafo generado por el LLM: así
las referencias clínicas son verificables contra la fuente real, no cuesta
tokens al final de la llamada (cuando el presupuesto por minuto ya está casi
agotado) y no puede alucinar un síntoma ni suavizar una decisión.

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
│   ├── patients.py             # Contexto de la llamada: cirugía, día postop, RAG por patología
│   ├── patient_routes.py       # GET /api/procedures para el formulario de llamada
│   ├── calls.py                # Resumen estructurado y persistente de cada llamada
│   ├── call_routes_api.py      # GET /api/calls — historial de resúmenes
│   ├── tokens.py               # Estimación de tokens por turno + calibración vs Groq
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

RAG: **106/107 PDFs indexados sin instalar nada extra**. El 107 es el PDF
escaneado sin capa de texto (la trampa del dataset) y necesita OCR: con
tesseract instalado en el sistema sube a 107/107 (poppler ya viene portable en
`bin/`). Sin tesseract el ingest lo reporta como fallido y sigue con el resto,
no se cae.

Nota de Windows: 3 PDFs de `textos/colorectal cancer/` tienen nombres tan
largos que la ruta pasa el límite de 260 caracteres. `app/rag/ingest.py` los
lee a bytes con el prefijo `\\?\` en vez de pasarle la ruta a MuPDF/poppler,
así que no hace falta habilitar rutas largas (que pide permisos de admin).

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
