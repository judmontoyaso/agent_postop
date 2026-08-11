"""Contexto de la llamada, memoria de reconexión y costo.

Cubre regresiones concretas que ya ocurrieron en pruebas en vivo, no casos
hipotéticos: cada test de aquí corresponde a algo que se rompió de verdad.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.patients import (
    build_context_prompt,
    build_greeting,
    build_memory_prompt,
    build_reconnect_line,
    get_procedure,
)
from app.tokens import CallTokenAccounting, costo_llamada, estimate_tokens

# --- Contexto parcial -------------------------------------------------------
# Regresión real: la interfaz exigía nombre Y cirugía, y si faltaba una
# descartaba las dos. Quien escribía solo el nombre acababa en llamada genérica
# y sin registro del paciente.

def test_solo_nombre_se_usa():
    ctx = build_context_prompt("Luis Gómez", None, None)
    assert ctx and "Luis Gómez" in ctx
    assert "no sabes de qué lo operaron" in ctx.lower()


def test_solo_procedimiento_se_usa():
    ctx = build_context_prompt(None, "Apendicectomía", 7)
    assert ctx and "apendicectomía" in ctx.lower()
    assert "DÍA 7" in ctx


def test_sin_datos_no_hay_contexto():
    assert build_context_prompt(None, None, None) is None


def test_saludo_avisa_del_registro():
    """Ley 1581: el paciente debe saber que sus datos se están recogiendo."""
    for args in [("Ana", "Apendicectomía"), ("Ana", None), (None, "Apendicectomía"), (None, None)]:
        assert "queda registrada" in build_greeting(*args)


# --- Memoria de reconexión --------------------------------------------------
# Regresión real: al cambiar de proveedor por cuota agotada, el agente
# saludaba de nuevo y volvía a preguntar de qué habían operado a la paciente,
# hasta que ella respondió "ya te respondí esa pregunta".

def test_memoria_conserva_lo_que_conto_el_paciente():
    mem = build_memory_prompt(
        ["Me operaron hace diez días.", "La herida está roja y me arde."],
        "¿Cómo está la herida?",
        "",
    )
    assert "diez días" in mem
    assert "roja y me arde" in mem
    assert "NO se lo vuelvas a preguntar" in mem


def test_memoria_acotada():
    """Se reenvía en cada turno de la sesión nueva: no puede crecer sin techo."""
    mem = build_memory_prompt([f"reporte {i}" for i in range(40)], "", "")
    assert mem.count("\n- ") == 10


def test_memoria_vacia_si_no_hablo():
    assert build_memory_prompt([], "", "") == ""


def test_reconexion_repite_lo_ultimo_oido():
    """Le confirma al paciente que no perdió el hilo y le da ocasión de
    corregir una transcripción equivocada."""
    linea = build_reconnect_line(["La herida está un poquito roja, a veces me arde."])
    assert "roja" in linea


def test_reconexion_no_repite_monosilabos():
    """"alcancé a escucharle: sí" no confirma nada."""
    assert "escucharle" not in build_reconnect_line(["Sí"])
    assert "escucharle" not in build_reconnect_line([])


# --- Procedimientos y RAG ---------------------------------------------------

def test_cada_procedimiento_mapea_a_una_carpeta_del_corpus():
    """Un typo acá deja el filtro del RAG sin resultados y nadie se entera."""
    esperadas = {"Appendicitis", "cholecystitis", "colorectal cancer",
                 "breast_cancer", "total joint replacement"}
    for key in ("appendicitis", "cholecystitis", "colorectal_cancer",
                "breast_cancer", "total_joint_replacement"):
        proc = get_procedure(key)
        assert proc is not None
        assert proc["category"] in esperadas


def test_procedimiento_desconocido():
    assert get_procedure("cirugia_inventada") is None


# --- Tokens y costo ---------------------------------------------------------

def test_el_historial_encarece_cada_turno():
    """Es la razón por la que las conversaciones largas revientan la cuota."""
    acc = CallTokenAccounting("prompt de sistema", "[]")
    primero = acc.turn_input()
    acc.add_history("el paciente cuenta algo bastante largo sobre su herida")
    assert acc.turn_input() > primero


def test_costo_cuenta_la_voz_aparte():
    """Sin la voz el número sale engañosamente bajo: en una llamada corta
    Deepgram pesa mucho más que el LLM."""
    c = costo_llamada("llama-3.3-70b-versatile", 24000, 400, 180)
    assert c["voz_usd"] > 0
    assert c["total_usd"] == round(c["llm_usd"] + c["voz_usd"], 6)


def test_costo_con_failover_tarifa_el_principal():
    c = costo_llamada("llama-3.3-70b-versatile → gemini-3.1-flash-lite", 1000, 100, 60)
    assert c["modelo_tarifado"] == "llama-3.3-70b-versatile"


def test_costo_modelo_desconocido_no_revienta():
    c = costo_llamada("modelo-que-no-existe", 1000, 100, 60)
    assert c["llm_usd"] is None
    assert c["voz_usd"] > 0


def test_estimacion_de_tokens_es_proporcional():
    assert estimate_tokens("hola") < estimate_tokens("hola " * 50)
    assert estimate_tokens("") == 0
