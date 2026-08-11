"""Guardrail de salida: lo que el agente dice.

La rúbrica penaliza por ocurrencia "inventar una dosis, un medicamento o un
procedimiento". Los casos de NO-marcar son frases textuales de llamadas reales
registradas en los logs: un guardrail que marque esas sería peor que no tener
guardrail, porque llenaría el registro de ruido y nadie lo miraría.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.guardrails import revisar_salida


@pytest.mark.parametrize("frase,tipo_esperado", [
    ("Puede tomar 500 mg de acetaminofén cada 8 horas para el dolor.", "medicamento"),
    ("Le recomiendo ibuprofeno si el dolor aumenta.", "medicamento"),
    ("Tómese dos tabletas cada 12 horas hasta que baje la fiebre.", "dosis"),
    ("Le voy a recetar 1 gramo de dipirona.", "dosis"),
    ("Ya puede retirarse los puntos en casa.", "procedimiento"),
    ("Suspenda el antibiótico si ya se siente mejor.", "procedimiento"),
    ("Aplíquese agua oxigenada en la herida dos veces al día.", "procedimiento"),
])
def test_alucinacion_clinica_detectada(frase, tipo_esperado):
    hallazgos = revisar_salida(frase)
    assert hallazgos, f"no detectó: {frase}"
    assert tipo_esperado in {h["tipo"] for h in hallazgos}


@pytest.mark.parametrize("frase", [
    # Textuales de llamadas reales — ninguna debe marcarse.
    "Juan David, 38.5 ya es fiebre como tal.",
    "Esa fiebre de 38.5 es para ponerle atención.",
    "El dolor ha bajado bastante, como un tres.",
    "¿Cada cuántas horas siente ese dolor?",
    "Lo mejor es que vayas hoy mismo por urgencias para que te miren esa herida.",
    "Lo siento, pero no puedo recomendarte medicamentos.",
    "La decisión de tomar cualquier medicamento debe ser hecha por un profesional.",
    "Usar muletas es común después de una cirugía de rodilla.",
    "¿Cómo está la herida, se ve bien o has notado algo raro?",
])
def test_frases_legitimas_no_se_marcan(frase):
    assert revisar_salida(frase) == [], f"falso positivo en: {frase}"


def test_texto_vacio():
    assert revisar_salida("") == []
    assert revisar_salida("   ") == []


def test_el_hallazgo_conserva_la_frase_completa():
    """Sin la frase textual, el registro no sirve para el acta ni para revisar
    después qué dijo exactamente el agente."""
    frase = "Puede tomar 500 mg de acetaminofén."
    hallazgos = revisar_salida(frase)
    assert all(h["frase"] == frase for h in hallazgos)
