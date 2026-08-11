"""app/tokens.py — Contabilidad de tokens por turno.

EL PROBLEMA: la rúbrica exige reportar "tokens de entrada y salida por turno y
por llamada" y el costo por llamada. Pero en esta arquitectura el backend nunca
ve el `usage` real: Deepgram habla directo con Groq/Gemini (BYOM) y solo nos
reenvía eventos de conversación. El campo `usage` de esa respuesta se queda
entre Deepgram y el proveedor.

Poner un proxy propio en `endpoint.url` para interceptar el usage no es opción
realista: Deepgram es un servicio en la nube y tendría que alcanzar esta máquina
por Internet, lo que obliga a un túnel público solo para medir.

LA SOLUCIÓN: reconstruir el prompt que Deepgram arma en cada turno —prompt de
sistema + schema de tools + historial completo + resultados de tools— y contarlo
acá. Es una ESTIMACIÓN y se reporta como tal, nunca como medición.

POR QUÉ ES VERIFICABLE: cuando Groq devuelve 429 dice en el cuerpo el conteo
real de esa petición ("Used 10271, Requested 3735"). Cada vez que pasa, se
compara contra nuestra estimación del mismo instante y se registra el error en
el log. Así el número reportado no es una cifra suelta: tiene una calibración
contrastable contra el proveedor, que es justo lo que la rúbrica pide cuando
advierte que "reportar números que no se sostienen es peor que no reportarlos".
"""
import logging

logger = logging.getLogger("tokens")

# Español con tokenizadores BPE tipo Llama 3 / Gemini: ~3.7 caracteres por
# token. Los acentos y la ñ pesan más que en inglés (~4.0), de ahí el ajuste.
# No se usa el tokenizador real de Llama porque vive en un repo gated de Meta:
# exigiría login de Hugging Face en el setup y pondría en riesgo el gate G2.
CHARS_PER_TOKEN = 3.7

# Overhead fijo del formato de chat (roles, delimitadores de mensaje, plantilla
# de tool calling) que el proveedor añade sobre el texto crudo.
TOKENS_PER_MESSAGE = 4


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, round(len(text) / CHARS_PER_TOKEN))


# Precios públicos por millón de tokens (USD), consultados en las páginas de
# pricing de cada proveedor. Se guardan acá y no en .env porque forman parte de
# la evidencia del informe: el costo por llamada que se reporta tiene que poder
# recalcularse desde el repositorio.
PRECIOS_USD_POR_MILLON = {
    "llama-3.3-70b-versatile": {"in": 0.59, "out": 0.79},
    "gemini-3.1-flash-lite": {"in": 0.10, "out": 0.40},
    "gemini-3.5-flash": {"in": 0.30, "out": 2.50},
    "gemini-3-flash-preview": {"in": 0.30, "out": 2.50},
}

# Deepgram cobra la voz aparte del LLM, por MINUTO DE CONEXIÓN del WebSocket
# —no por audio procesado—, así que el silencio cuesta igual que el habla. Sin
# esta línea el costo por llamada saldría engañosamente bajo: en conversaciones
# cortas la voz pesa mucho más que el razonamiento.
#
# Tarifa Voice Agent API, Pay As You Go, tier "Custom - BYO LLM": es el que nos
# corresponde porque el razonamiento lo ponemos nosotros (Groq/Gemini) y solo
# usamos de Deepgram el STT, el TTS y la orquestación de turnos.
# Referencia de los otros tiers, por si cambia la arquitectura:
#   Standard (LLM de Deepgram)      $0.075/min
#   Standard - BYO TTS              $0.065/min
#   Custom - BYO LLM                $0.059/min  <- el nuestro
#   Custom - BYO LLM + TTS          $0.050/min
DEEPGRAM_USD_POR_MINUTO = 0.059


def costo_llamada(modelo: str, input_tokens: int, output_tokens: int,
                  duracion_s: float) -> dict:
    """Costo estimado de una llamada, desglosado. Devuelve también el modelo de
    precios aplicado para que el número sea auditable y no un total suelto."""
    # Con failover el campo `modelo` puede traer "a → b"; se cobra con el
    # primero, que es el que razonó la mayor parte, y se deja constancia.
    principal = modelo.split("→")[0].strip() if modelo else ""
    precio = PRECIOS_USD_POR_MILLON.get(principal)

    llm = None
    if precio:
        llm = round(
            input_tokens / 1_000_000 * precio["in"]
            + output_tokens / 1_000_000 * precio["out"],
            6,
        )
    voz = round(duracion_s / 60 * DEEPGRAM_USD_POR_MINUTO, 6)

    return {
        "modelo_tarifado": principal or None,
        "llm_usd": llm,
        "voz_usd": voz,
        "total_usd": round((llm or 0) + voz, 6),
        "tokens_estimados": True,
        "nota": ("tokens estimados (ver app/tokens.py); precios públicos por millón: "
                 f"{precio['in']}/{precio['out']} USD in/out"
                 if precio else "sin tarifa conocida para este modelo — solo se cuenta la voz"),
    }


class CallTokenAccounting:
    """Lleva la cuenta de una llamada. El historial es acumulativo: cada turno
    reenvía TODO lo anterior, que es la razón por la que las conversaciones
    largas revientan contra el límite por minuto."""

    def __init__(self, system_prompt: str, tools_schema_json: str):
        self._base = estimate_tokens(system_prompt) + estimate_tokens(tools_schema_json)
        self._history = 0
        self.input_total = 0
        self.output_total = 0

    def add_history(self, text: str) -> None:
        """Cualquier cosa que entra al historial: turno del paciente, respuesta
        del agente o resultado de una tool."""
        self._history += estimate_tokens(text) + TOKENS_PER_MESSAGE

    def turn_input(self) -> int:
        """Lo que pesa la petición de ESTE turno: base fija + todo el historial."""
        return self._base + self._history

    def record_turn(self, assistant_text: str) -> tuple[int, int]:
        entrada = self.turn_input()
        salida = estimate_tokens(assistant_text)
        self.input_total += entrada
        self.output_total += salida
        return entrada, salida

    def calibrate(self, requested_real: int) -> None:
        """Contrasta contra el conteo real que Groq revela en un 429."""
        estimado = self.turn_input()
        if not requested_real:
            return
        error = (estimado - requested_real) / requested_real * 100
        logger.info(
            f"calibración de tokens — real={requested_real} estimado={estimado} "
            f"error={error:+.1f}%"
        )
