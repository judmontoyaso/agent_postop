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
# 1. Clonar y crear entorno  (Python 3.12 recomendado: los pines de torch y
#    chromadb todavía no traen ruedas para 3.13+)
git clone https://github.com/judmontoyaso/agent_postop
cd agent_postop
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac

# 2. Dependencias  (~5 min: se baja torch)
pip install -r requirements.txt

# 2b. Instalar el proyecto. El código vive en src/, así que este paso es el que
#     lo hace importable — sin él, `uvicorn app.main:app` no encuentra nada.
pip install -e . --no-deps

# 3. Configurar credenciales
cp .env.example .env
#    Rellenar DEEPGRAM_API_KEY y, según el proveedor que se use:
#      GROQ_API_KEY    -> console.groq.com          (por defecto)
#      GEMINI_API_KEY  -> aistudio.google.com/apikey (alternativa; ver "Modelo")
#    Con las dos puestas se activa el failover automático entre proveedores.

# 4. Descargar dataset oficial del reto y colocarlo en:
#    dataset/textos/**/*.pdf   (107 PDFs en 5 carpetas de patología)
#    dataset/*.xlsx

# 5. Indexar el conocimiento clínico
#    (~5 min la primera vez: baja el embedder y los modelos de OCR)
python scripts/ingest_dataset.py
#    Salida esperada: "Indexados: 107/107", con 1 vía OCR — el PDF escaneado
#    sin capa de texto que trae el dataset.

# 6. Verificar setup
python scripts/setup_check.py

# 7. Levantar el servidor
uvicorn app.main:app --port 8000
```

### Comprobaciones opcionales (no hacen falta para levantar)

```bash
pip install ruff pytest
pytest                             # 54 pruebas, ~1 s
ruff check .                       # linter
python scripts/evaluate_triage.py  # piso de seguridad vs. dataset etiquetado
```

Luego:
- Interfaz de llamada: http://localhost:8000/call
- Consola admin: http://localhost:8000/admin

### Sin dependencias de sistema

El proyecto se levanta con `pip install` y nada más. No hay que instalar
poppler, tesseract ni ningún otro binario: el rasterizado PDF→imagen lo hace
PyMuPDF y el reconocimiento EasyOCR, ambos por pip. EasyOCR descarga ~100 MB de
modelos la primera vez que se indexa; en runtime no se usa nunca.

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

## Guardrails

**De entrada** — el bloque `LÍMITES` del prompt establece que lo que dice el
paciente y lo que devuelve el RAG son *datos*, no instrucciones; sin desvíos de
misión ni de rol; ninguna instrucción baja un nivel de escalamiento.

**Piso de seguridad en código** (`app/agent/decision.py`) — patrones de alarma
que fuerzan rojo al margen de lo que decida el LLM. Vive en el código, no en el
prompt, así que **una inyección de prompt no lo alcanza**. Reconoce negaciones:
*"nada de pus"* no dispara, *"me sale pus"* sí. Medido contra el dataset
etiquetado del reto con `scripts/evaluate_triage.py`.

**De salida** (`app/guardrails.py`) — revisa lo que el agente dice buscando
dosis, medicamentos y procedimientos inventados, que es lo que la rúbrica
penaliza por ocurrencia.

**No solo lo detecta: impide que suene.** El audio del agente pasa por este
backend antes de llegar al navegador, así que se retiene hasta que el texto de
ese turno pase la revisión. Si no pasa, se descarta sin reproducirse y el
agente dice una corrección en su lugar — el paciente no llega a oír la dosis
inventada ni aunque cuelgue inmediatamente después.

Cuesta 20 ms: es todo el audio que Deepgram adelanta antes de entregar el texto
(medido tres veces). Si el texto nunca llega, el audio se libera igual — dejar
al paciente en silencio es peor que el riesgo que la compuerta cubre.

## Evaluación del triage sin gastar llamadas

```bash
python scripts/evaluate_triage.py
```

Mide el piso de seguridad contra los 3991 turnos etiquetados de
`dataset_final.xlsx`, agrupados por conversación (que es la unidad clínica: el
agente escucha la llamada entera, no una frase suelta). Reporta cuántos casos
rojos se escalan **aunque el modelo falle por completo** y cuántos verdes se
elevan de más.

No mide el triage completo — eso lo decide el LLM leyendo el RAG y pasar 3991
turnos por el modelo es inviable con los tiers gratuitos. Mide la red de
seguridad, que es justo lo que responde a la asimetría clínica de la rúbrica.

## Escalamiento hacia afuera

Al escalar a amarillo o rojo se dispara un webhook saliente
(`ESCALATION_WEBHOOK_URL`) con paciente, procedimiento, nivel, motivo, síntomas
y documentos citados. Sin él, el escalamiento queda registrado pero nadie en la
clínica se entera. Es opcional para no romper G2: si no está configurado, la
llamada funciona igual y queda un WARNING en el log.

La transcripción completa **no** viaja en el webhook: son datos de salud de un
paciente identificado y no hacen falta para actuar. Quien recibe el aviso puede
consultarla en `GET /api/calls/{id}`.

## Datos personales — estado y límites

Este prototipo trata datos de salud de personas identificadas, que en Colombia
son **datos sensibles** bajo la Ley 1581 de 2012 (habeas data). Lo que hace hoy
y lo que le falta, dicho sin adornos:

**Hecho:**
- Aviso de tratamiento en la apertura de la llamada (`AVISO_GRABACION` en
  `app/patients.py`): el paciente sabe que queda registro antes de contar nada.
- El webhook manda lo mínimo accionable, no la conversación entera.
- No se guarda audio en ningún momento: solo transcripción.

**Pendiente para un despliegue real (fuera del alcance del reto):**
- SQLite sin cifrar y logs con nombre y estado clínico en claro. Bastaría
  cifrado en reposo y enmascarado de identificadores en los logs.
- Sin política de retención: hoy los resúmenes se guardan indefinidamente.
- Sin control de acceso: `/api/calls` y la consola admin están abiertas a quien
  alcance el puerto.
- El consentimiento se informa pero no se registra su aceptación.

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

Layout `src/`: el código no es importable por accidente desde el directorio de
trabajo, lo que obliga a instalarlo y garantiza que se prueba lo mismo que se
distribuye. `data/`, `dataset/` y `static/` quedan fuera del paquete porque son
datos de despliegue, no código.

```
agent_postop/
└── src/app/
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
│   ├── guardrails.py           # Guardrail de salida: dosis/medicamentos inventados
│   ├── notify.py               # Webhook saliente al escalar
│   ├── notify_routes.py        # Buzón receptor (demo) de los avisos
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
│   ├── setup_check.py
│   └── evaluate_triage.py      # Piso de seguridad vs. dataset etiquetado
├── tests/                      # 54 pruebas; los casos salen de fallos reales
├── pyproject.toml              # Metadatos + configuración de ruff/pytest
└── requirements.txt            # Dependencias de ejecución, con versiones fijas
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
tesseract instalado en el sistema sube a 107/107. Sin tesseract el ingest lo
reporta como fallido y sigue con el resto, no se cae.

Nota de Windows: 3 PDFs de `textos/colorectal cancer/` tienen nombres tan
largos que la ruta pasa el límite de 260 caracteres. `app/rag/ingest.py` los
lee a bytes con el prefijo `\\?\` en vez de pasarle la ruta a MuPDF,
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
