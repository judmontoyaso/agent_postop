"""Piso de seguridad clínico. Es lo más crítico del sistema: decide si un
paciente que el modelo minimizó se escala igual.

Los casos vienen de fallos reales encontrados con `scripts/evaluate_triage.py`
contra el dataset etiquetado del reto, no de casos inventados.
"""
import pytest

from app.agent.decision import EscalationLevel, check_hard_triggers, resolve_escalation


@pytest.mark.parametrize("frase", [
    "me está saliendo pus por la herida",
    "tengo un líquido amarillo saliendo de ahí",
    "me tomé la temperatura y marcó 38.1",
    "salió como en 38 grados",
    "he sentido escalofríos toda la noche",
    "no puedo respirar bien desde ayer",
    "siento la pierna como que no responde",
    "la herida se abrió",
])
def test_alarmas_reales_disparan(frase):
    assert check_hard_triggers(frase) is EscalationLevel.RED


@pytest.mark.parametrize("frase", [
    # Negaciones: el paciente dice que NO tiene el síntoma. La versión anterior
    # del piso disparaba en todas estas.
    "nada de esas cosas de pus ni nada raro",
    "no sale nada de líquido gracias a Dios",
    "no he tenido fiebre ni escalofríos",
    # Frases benignas que contienen palabras de alarma.
    "casi no siento dolor, diría que es un 0",
    "no siento molestia ni nada",
    "el dolor está como en un 5",
    "me tomé la temperatura y marcó 37 y algo",
    "camino normal por la casa, subo y bajo escaleras",
])
def test_frases_benignas_no_disparan(frase):
    assert check_hard_triggers(frase) is None


def test_hard_trigger_eleva_lo_que_el_modelo_subestimo():
    """El caso que protege contra el falso negativo, que la rúbrica trata como
    la falla catastrófica."""
    nivel = resolve_escalation("verde", "tengo pus en la herida y fiebre alta")
    assert nivel is EscalationLevel.RED


def test_nivel_desconocido_se_trata_como_rojo():
    """Falla hacia el lado seguro: si el modelo devuelve basura, no se asume
    que el paciente está bien."""
    assert resolve_escalation("azul", "me duele un poco") is EscalationLevel.RED
    assert resolve_escalation("", "me duele un poco") is EscalationLevel.RED
    assert resolve_escalation(None, "me duele un poco") is EscalationLevel.RED


def test_el_piso_nunca_relaja_un_nivel():
    """Solo puede subir. Un hard trigger no baja un rojo del modelo a verde."""
    assert resolve_escalation("rojo", "todo bien, sin novedad") is EscalationLevel.RED
    assert resolve_escalation("amarillo", "todo bien") is EscalationLevel.YELLOW


def test_caso_leve_real_se_queda_verde():
    """Sin esto el piso sería inútil: si elevara todo, nadie lo creería."""
    assert resolve_escalation("verde", "todo bien, solo una molestia al caminar") is EscalationLevel.GREEN
