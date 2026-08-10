"""app/agent/decision.py — Lógica de triage y escalamiento (verde/amarillo/rojo).

Peso asimétrico obligatorio (rubrica-evaluacion.md): un falso negativo (no
escalar cuando tocaba) es el fallo catastrófico — pesa mucho más que un falso
positivo. Ante duda clínica real, la regla es escalar, no tranquilizar.

Este módulo NO decide solo con reglas fijas — el LLM evalúa síntomas contra el
RAG (guías clínicas) vía tool calling y llama a `escalate_patient` con su
propio razonamiento. Las reglas de abajo son un piso de seguridad (hard
triggers) que fuerzan rojo sin depender del LLM, para blindar contra
alucinaciones que minimicen una alarma.
"""
from enum import Enum


class EscalationLevel(str, Enum):
    GREEN = "verde"
    YELLOW = "amarillo"
    RED = "rojo"


# Hard triggers — si el texto del paciente contiene estos patrones, se fuerza
# rojo sin importar lo que decida el LLM. Piso de seguridad contra falsos
# negativos por alucinación o prompt injection del paciente.
RED_HARD_TRIGGERS = [
    "no puedo respirar",
    "dolor en el pecho",
    "sangrado abundante",
    "fiebre muy alta",
    "pérdida de conciencia",
    "no siento",  # posible pérdida de sensibilidad / signo neurológico
    "no cierra",  # herida que no cierra
    "no ha cerrado",
    "supuración",
    "pus",
    "muy hinchad",  # hinchado/hinchada — cubre ambos géneros por substring
    "no puedo mover",
]


def check_hard_triggers(patient_text: str) -> EscalationLevel | None:
    text = patient_text.lower()
    for trigger in RED_HARD_TRIGGERS:
        if trigger in text:
            return EscalationLevel.RED
    return None


def resolve_escalation(llm_decision: str, patient_text: str) -> EscalationLevel:
    """Combina la decisión del LLM con el piso de seguridad de hard triggers.
    En caso de conflicto, siempre gana el nivel más alto (nunca se relaja)."""
    hard = check_hard_triggers(patient_text)
    try:
        llm_level = EscalationLevel(llm_decision.lower().strip())
    except ValueError:
        # Decisión del LLM no reconocida -> por seguridad, tratar como rojo
        llm_level = EscalationLevel.RED

    order = {EscalationLevel.GREEN: 0, EscalationLevel.YELLOW: 1, EscalationLevel.RED: 2}
    if hard is None:
        return llm_level
    return hard if order[hard] > order[llm_level] else llm_level
