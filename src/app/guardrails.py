"""app/guardrails.py — Revisión de lo que el agente DICE, no de lo que le dicen.

El sistema ya tenía guardrails de entrada (el bloque LÍMITES del prompt y los
hard triggers de `agent/decision.py`, que viven en código y una inyección no
alcanza). No tenía ninguno de salida, y la rúbrica penaliza justo eso:

    "Alucinación clínica peligrosa — inventar una dosis, un medicamento o un
     procedimiento, o tranquilizar al paciente ante un síntoma de alarma.
     Cada ocurrencia penaliza y queda registrada textualmente en el acta."

LÍMITE REAL DE ESTA ARQUITECTURA — detecta y corrige, no previene.
Deepgram sintetiza y empieza a reproducir el audio ANTES de entregarnos la
transcripción del agente (verificado en los logs: los bytes de audio llegan
antes que el evento `ConversationText`). Para cuando podemos leer lo que dijo,
el paciente ya lo está oyendo. Bloquearlo exigiría retener el audio hasta tener
el texto, lo que metería latencia en el único punto donde no sobra.

Así que lo que se hace es: detectar en cuanto llega el texto, hacer que el
agente se rectifique de inmediato en la misma llamada, y dejar el hallazgo
registrado en el resumen. Una rectificación audible es mucho mejor que una
dosis inventada que nadie corrige, y que quede anotado es lo que permite
demostrar que el sistema lo detecta en vez de que lo detecte el jurado.
"""
import logging
import re
import unicodedata

logger = logging.getLogger("guardrails")

# Principios activos de uso corriente en postoperatorio en Colombia. La lista
# no pretende ser exhaustiva: cubre lo que un modelo alucinaría con más
# probabilidad al hablar de dolor, infección o inflamación.
MEDICAMENTOS = [
    "acetaminofen", "paracetamol", "ibuprofeno", "naproxeno", "diclofenaco",
    "ketorolaco", "dipirona", "metamizol", "tramadol", "codeina", "morfina",
    "hidrocodona", "oxicodona", "amoxicilina", "ampicilina", "cefalexina",
    "ceftriaxona", "ciprofloxacina", "metronidazol", "clindamicina",
    "gentamicina", "omeprazol", "ranitidina", "dexametasona", "prednisolona",
    "enoxaparina", "heparina", "warfarina", "aspirina", "acido acetilsalicilico",
]

# Una dosis siempre lleva unidad. Números sueltos NO se marcan: el agente dice
# legítimamente "fiebre de 38.5 grados" o "un dolor de 3 sobre 10", y marcarlos
# llenaría el resumen de falsos positivos hasta volverlo inservible.
_UNIDADES = (r"mg|mgs|miligramos?|g|gramos?|ml|mililitros?|mcg|microgramos?|ui|"
             r"unidades?|gotas?|comprimidos?|tabletas?|capsulas?|pastillas?|ampollas?")

RE_DOSIS = re.compile(
    # Cifra + unidad ("500 mg"), o número escrito en letra + unidad ("dos
    # tabletas"), que es como lo dice un agente hablando por teléfono. Las
    # letras solo valen con unidad: "dos veces al día" no es una dosis, y
    # "veces" no está en la lista de unidades justo por eso.
    rf"\b(\d+([.,]\d+)?|una?|dos|tres|cuatro|cinco|seis|medi[oa])\s?({_UNIDADES})\b",
    re.IGNORECASE,
)

# Pauta posológica: "cada 8 horas", "dos veces al día", "cada 12 h".
RE_PAUTA = re.compile(
    r"\b(cada\s+\d+\s?(horas?|h\b|dias?)|"
    r"(una|dos|tres|cuatro|\d+)\s+veces\s+(al|por)\s+(dia|día|semana))\b",
    re.IGNORECASE,
)

# Instrucciones de procedimiento que un agente de seguimiento NO debe dar: son
# decisiones del cirujano y ejecutarlas mal tiene consecuencias reales.
RE_PROCEDIMIENTO = re.compile(
    # Cubre imperativo e infinitivo: el agente dice tanto "retírese los puntos"
    # como "ya puede retirarse los puntos", y la segunda forma es la que más
    # suena natural en una llamada.
    r"\b(retir(e|ese|arse|ar)|quit(e|ese|arse|ar)|cort(e|ar)|saqu(e|arse)|sacar(se)?)"
    r"\s+(los\s+|las\s+)?(puntos|grapas|suturas|drenaje|sonda|vendaje)\b"
    r"|\b(suspenda|deje\s+de\s+tomar|no\s+tome\s+mas)\s+(el\s+|la\s+|los\s+|las\s+)?"
    r"(antibiotico|medicamento|tratamiento|anticoagulante)\b"
    r"|\b(aplique(se)?|pongase|coloquese)\s+.{0,25}(crema|pomada|alcohol|yodo|agua\s+oxigenada)\b",
    re.IGNORECASE,
)

MENSAJE_CORRECCION = (
    "Perdón, me adelanté: yo no estoy para indicarle medicamentos, dosis ni "
    "procedimientos. Eso se lo tiene que decir su médico. Sigamos con cómo se siente."
)


def _normalizar(texto: str) -> str:
    """Sin acentos y en minúsculas — el STT escribe 'acetaminofén' y
    'acetaminofen' indistintamente."""
    plano = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in plano if not unicodedata.combining(c)).lower()


def revisar_salida(texto: str) -> list[dict]:
    """Hallazgos en una frase dicha por el agente. Lista vacía = todo bien."""
    if not texto or not texto.strip():
        return []

    plano = _normalizar(texto)
    hallazgos: list[dict] = []

    encontrados = [m for m in MEDICAMENTOS if m in plano]
    if encontrados:
        hallazgos.append({
            "tipo": "medicamento",
            "detalle": ", ".join(sorted(set(encontrados))),
            "frase": texto.strip(),
        })

    dosis = RE_DOSIS.search(plano)
    if dosis:
        hallazgos.append({"tipo": "dosis", "detalle": dosis.group(0), "frase": texto.strip()})

    # Una pauta sola ("cada 8 horas") no basta: el agente pregunta
    # legítimamente "¿cada cuántas horas le duele?". Solo cuenta si viene
    # acompañada de un medicamento o una dosis.
    if RE_PAUTA.search(plano) and (encontrados or dosis):
        hallazgos.append({
            "tipo": "pauta",
            "detalle": RE_PAUTA.search(plano).group(0),
            "frase": texto.strip(),
        })

    proc = RE_PROCEDIMIENTO.search(plano)
    if proc:
        hallazgos.append({
            "tipo": "procedimiento",
            "detalle": proc.group(0),
            "frase": texto.strip(),
        })

    return hallazgos
