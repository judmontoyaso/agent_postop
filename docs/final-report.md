# Reporte final — Tech Sphere Challenge 2026

> Plantilla a llenar antes de la entrega (10 ago 2026). Todo lo marcado
> [PENDIENTE] debe completarse con evidencia real de sesión, no estimaciones.

## 1. Modelo declarado (G3)

- **Modelo:** Llama 3.1 70B vía Groq — servido como `llama-3.3-70b-versatile`
  porque Groq descontinuó `llama-3.1-70b-versatile` el 24 de enero de 2025.
  Es el reemplazo directo documentado por Groq (mismo linaje Llama 70B, no
  cambio de familia). Verificado en vivo: Groq devuelve `model_decommissioned`
  contra el ID original.
- **Atención finalistas:** `llama-3.3-70b-versatile` tiene baja programada el
  16 de agosto de 2026. Si el proyecto llega a la demo en vivo del 5 de
  septiembre, hay que confirmar el modelo Groq 70B vigente en ese momento y
  actualizar `GROQ_MODEL`.
- **Justificación:** de las 4 opciones del stack cerrado, Groq es la única que
  combina latencia ultra-baja (crítica por el gate eliminatorio G4 de voz en
  vivo cronometrada) con capacidad de razonamiento suficiente para tool
  calling (RAG + lógica de escalamiento). Gemini 1.5 Flash se descartó por el
  límite de 15 RPM en tier gratuito — riesgo alto en una sesión de evaluación
  en vivo con jurado. Los modelos locales (Llama 3.2 1B/3B, Phi-3.5 Mini) se
  descartaron por razonamiento clínico insuficiente para triage confiable.
- **Limitación real observada:** el tier gratuito de Groq tiene límite de
  100,000 tokens/día (TPD) además del límite por minuto (TPM) — se agotó por
  completo durante las pruebas de este proyecto en un solo día de testing
  intensivo. Para producción o evaluación en vivo extendida, se recomienda
  Dev Tier de Groq (pago por uso, sin suscripción: $0.59/1M tokens entrada,
  $0.79/1M salida — el gasto de todo un día de pruebas intensivas costaría
  centavos de dólar en tier pago).

## 2. Prompts usados

**Saludo inicial** (`app/voice/deepgram_agent.py::GREETING`, se envía como
primer mensaje del agente — habla primero porque es llamada saliente):
```
Hola, buenas. Le habla el asistente de seguimiento postoperatorio.
¿Con quién tengo el gusto de hablar?
```

**System prompt** (`app/voice/deepgram_agent.py::SYSTEM_PROMPT`) — iterado
tres veces contra comportamiento observado en pruebas reales:
1. Versión inicial: el agente encadenaba varias preguntas por turno sin dejar
   responder al paciente.
2. Se agregó regla explícita de "una idea/pregunta por turno, 1-2 frases,
   después parar" — turnos se acortaron notablemente.
3. Se agregó instrucción para no repetir literalmente errores internos del
   RAG ("no encontré información") como si fuera una persona hablando, y para
   escalar (`escalar_paciente`) proactivamente en cuanto hay señal clara, no
   solo "al final de la llamada" — el comportamiento inicial nunca escalaba
   incluso ante síntomas de alarma combinados.

Texto completo del prompt final: ver `app/voice/deepgram_agent.py::SYSTEM_PROMPT`.

## 3. Configuraciones

- STT: Deepgram `nova-2`, `language: "es"` forzado (por defecto transcribía
  en inglés pese a que el paciente hablaba español — bug real encontrado en
  pruebas, corregido)
- TTS: Deepgram `aura-2-celeste-es`
- LLM (think): Groq `llama-3.3-70b-versatile`, provider nativo `"groq"` en
  Deepgram Agent API (no `"open_ai"` con endpoint custom — combinación
  incorrecta probada y descartada, producía error `FAILED_TO_THINK`)
- Embeddings RAG: `paraphrase-multilingual-MiniLM-L12-v2` (local, sin API
  externa — evita latencia de red en el camino crítico de cada turno de voz)
- Vector DB: ChromaDB persistente, colección `clinical_docs`, 107/107 PDFs
  del dataset indexados (incluye el PDF escaneado sin capa de texto vía OCR
  con tesseract + poppler)
- RAG por consulta: `n_results=2`, chunks truncados a 350 caracteres — ajuste
  deliberado para reducir tokens por turno y no saturar el límite TPM/TPD de
  Groq en conversaciones largas

## 4. Métricas obligatorias (rubrica-evaluacion.md)

Generadas por `GET /api/metrics/summary` tras sesión de prueba real.

**Nota de proceso:** se encontró y corrigió un bug real durante el desarrollo
— el código intentaba medir la latencia usando eventos `EndOfThought`/
`UtteranceEnd` de Deepgram que, verificado contra logs reales de múltiples
sesiones de prueba, **nunca se emiten en la práctica**. Se corrigió para usar
el evento `ConversationText` (rol usuario) como marca de fin de turno, que sí
se confirmó presente en todas las sesiones reales. Antes del fix, `metrics.db`
tenía 0 turnos registrados pese a conversaciones completas y funcionales.

| Métrica | Valor |
|---|---|
| Latencia P50 (fin de habla -> inicio audio) | [PENDIENTE — recolectar tras el fix, cuota de Groq se agotó antes de poder correr una sesión completa post-fix] |
| Latencia P95 | [PENDIENTE — idem] |
| Tokens promedio in/out por turno | [PENDIENTE — idem] |
| Invocaciones de modelo por turno | [PENDIENTE — idem] |
| RAG queries por llamada | [PENDIENTE — idem] |
| Costo estimado por llamada | Groq: $0.59/1M tokens entrada + $0.79/1M salida (`llama-3.3-70b-versatile`). Deepgram: [PENDIENTE — confirmar pricing vigente de Voice Agent API]. Cálculo final pendiente de datos reales de tokens/turno. |

## 5. Screenshots

[PENDIENTE — admin console subiendo/borrando doc, call interface en llamada]

## 6. Casos de prueba manual

Probado en vivo durante el desarrollo (transcripciones reales, no simuladas).
Nota: las pruebas de la tabla de abajo con modelo "gpt-4o-mini (temporal)" se
corrieron con `THINK_PROVIDER=openai` porque Groq se quedó sin cuota diaria
durante el desarrollo — antes de la entrega/demo, repetir cada caso con Groq
(`THINK_PROVIDER=groq`) para que las métricas y el modelo declarado coincidan.

| Nivel | Síntomas reportados | Resultado |
|---|---|---|
| Verde | Sin síntomas de alarma | `escalar_paciente(nivel=verde)` con motivo coherente. Modelo: Groq. |
| Amarillo | Dolor 5/10, fiebre leve 37.8, herida "un poco roja en el borde" (sin pus, sin señales duras), dificultad leve para moverse | `escalar_paciente(nivel=amarillo)` — juicio del LLM sin ningún hard trigger de código de por medio. Motivo: "fiebre leve, herida con enrojecimiento y dificultad para moverse". Modelo: gpt-4o-mini (temporal). |
| Rojo | Rodilla hinchada + herida sin cerrar + fiebre + "un poco de pus" | Forzado por hard trigger de código (`"pus"` en `RED_HARD_TRIGGERS`) — rojo garantizado sin depender del LLM, tal como está diseñado el piso de seguridad. Modelo: gpt-4o-mini (temporal). |
| Fuera de corpus | Dolor de brazo (no es una de las 5 patologías del dataset) | RAG no encontró info relevante; el agente no expuso el fallo interno, recomendó consulta médica directa (tras ajuste de prompt, sección 2). |

**Bug encontrado y corregido durante las pruebas:** en al menos una
conversación (caso amarillo, primer intento) el agente cerró la llamada
("cuídate", despedida) sin haber llamado nunca a `escalar_paciente` — ningún
nivel de riesgo quedó registrado. Se agregó (a) una regla no-negociable en el
prompt que prohíbe despedirse sin escalar antes, y (b) una alarma a nivel de
código (`app/voice/call_routes.py`) que loguea explícito si una llamada
termina sin ese registro — para tener visibilidad aunque el prompt falle.
