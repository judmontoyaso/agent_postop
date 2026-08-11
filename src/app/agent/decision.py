"""app/agent/decision.py — Lógica de triage y escalamiento (verde/amarillo/rojo).

Peso asimétrico obligatorio (rubrica-evaluacion.md): un falso negativo (no
escalar cuando tocaba) es el fallo catastrófico — pesa mucho más que un falso
positivo. Ante duda clínica real, la regla es escalar, no tranquilizar.

Este módulo NO decide solo con reglas fijas — el LLM evalúa síntomas contra el
RAG (guías clínicas) vía tool calling y llama a `escalate_patient` con su
propio razonamiento. Las reglas de abajo son un piso de seguridad (hard
triggers) que fuerza rojo sin depender del LLM, para blindar contra
alucinaciones que minimicen una alarma o inyecciones de prompt: vive en código,
no en el prompt, así que no hay frase del paciente que lo desactive.

POR QUÉ NO ES UNA LISTA DE SUBCADENAS. La primera versión buscaba trozos de
texto sueltos ("pus", "no siento") y `scripts/evaluate_triage.py` demostró
contra el dataset etiquetado del reto que eso no funciona:

- Disparaba en NEGACIONES. "nada de esas cosas de pus ni nada raro" activaba
  rojo, cuando el paciente está diciendo justo lo contrario.
- Disparaba en frases benignas. "casi no siento dolor" activaba "no siento",
  que es una buena noticia clasificada como emergencia.

De ahí las dos correcciones: cada patrón declara si se anula al ir negado, y
los patrones son expresiones con contexto en vez de fragmentos sueltos.
"""
import re
import unicodedata
from enum import Enum


class EscalationLevel(str, Enum):
    GREEN = "verde"
    YELLOW = "amarillo"
    RED = "rojo"


# Palabras que anulan un síntoma cuando aparecen justo antes. La ventana es
# corta a propósito: en "no sale nada de líquido, pero me preocupa" la negación
# aplica al líquido, no a lo que venga tres frases después.
NEGADORES = ("no", "nada", "ni", "sin", "ningun", "ninguna", "tampoco", "nunca", "jamas")
VENTANA_NEGACION = 45

# `negable=False` para los patrones que YA contienen la negación como parte del
# síntoma ("no puedo respirar"): buscarles un negador delante los anularía
# siempre.
HARD_TRIGGERS: list[tuple[str, bool]] = [
    # Respiratorio / cardiovascular — emergencia inmediata
    (r"no puedo respirar|me falta el aire|me ahogo|dificultad para respirar", False),
    (r"dolor en el pecho|dolor de pecho|opresion en el pecho", True),
    (r"me desmaye|perdida de conciencia|me desvaneci", True),

    # Infección del sitio quirúrgico
    (r"\bpus\b|supuracion|secrecion purulenta|material purulento", True),
    (r"liquido\s+(amarill\w*|verdos\w*|maloliente|con mal olor)", True),
    (r"la herida\s+\w{0,12}\s?(abierta|se abrio|no cierra|no ha cerrado)", True),
    (r"mal olor (en|de) la herida|huele (mal|feo)", True),

    # Fiebre — exige unidad o contexto de medición para no cazar edades ni
    # números sueltos ("un dolor de 8", "tengo 38 años").
    (r"\b(3[89]|4[01])([.,]\d)?\s*(grados|°|º|\bc\b)", True),
    (r"(temperatura|termometro|marco|marca|salio|tengo|fiebre de)\D{0,15}\b(3[89]|4[01])([.,]\d)?\b", True),
    (r"escalofrio", True),

    # Sangrado
    (r"sangrado abundante|sangra mucho|no para de sangrar|manchando mucho", True),

    # Neurológico / movilidad — señal de complicación grave
    (r"no puedo mover|no puedo levantarme|no me responde (la|el)\s\w+", False),
    # Hasta 3 palabras de por medio: en el dataset la frase real es "siento la
    # pierna como que no responde", y un patrón que solo tolere una palabra
    # entre el miembro y la negación la deja pasar.
    (r"(la pierna|el brazo|el pie|la mano)(\s+\w+){0,3}\s+no\s+(me\s+)?responde", False),
    (r"no siento (la|el)\s\w+", False),  # pérdida de sensibilidad, NO "no siento dolor"
]

_COMPILADOS = [(re.compile(p), negable) for p, negable in HARD_TRIGGERS]


def _normalizar(texto: str) -> str:
    plano = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in plano if not unicodedata.combining(c)).lower()


def _viene_negado(texto: str, inicio: int) -> bool:
    """¿Hay un negador en los caracteres previos al síntoma?"""
    contexto = texto[max(0, inicio - VENTANA_NEGACION):inicio]
    return any(re.search(rf"\b{n}\b", contexto) for n in NEGADORES)


def check_hard_triggers(patient_text: str) -> EscalationLevel | None:
    """Rojo si el texto del paciente contiene una alarma NO negada."""
    texto = _normalizar(patient_text)
    for patron, negable in _COMPILADOS:
        for m in patron.finditer(texto):
            if negable and _viene_negado(texto, m.start()):
                continue
            return EscalationLevel.RED
    return None


def explicar_triggers(patient_text: str) -> list[str]:
    """Qué disparó el piso. Para el resumen de la llamada y para depurar por qué
    un caso se elevó: un escalamiento sin motivo trazable no sirve de nada."""
    texto = _normalizar(patient_text)
    encontrados = []
    for patron, negable in _COMPILADOS:
        for m in patron.finditer(texto):
            if negable and _viene_negado(texto, m.start()):
                continue
            encontrados.append(m.group(0).strip())
            break
    return encontrados


def resolve_escalation(llm_decision: str, patient_text: str) -> EscalationLevel:
    """Combina la decisión del LLM con el piso de seguridad de hard triggers.
    En caso de conflicto, siempre gana el nivel más alto (nunca se relaja)."""
    hard = check_hard_triggers(patient_text)
    try:
        llm_level = EscalationLevel(llm_decision.lower().strip())
    except (ValueError, AttributeError):
        # Decisión del LLM no reconocida -> por seguridad, tratar como rojo
        llm_level = EscalationLevel.RED

    order = {EscalationLevel.GREEN: 0, EscalationLevel.YELLOW: 1, EscalationLevel.RED: 2}
    if hard is None:
        return llm_level
    return hard if order[hard] > order[llm_level] else llm_level
