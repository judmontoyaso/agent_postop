"""app/patients.py — Contexto clínico de la llamada: a quién se llama, de qué
lo operaron y en qué día del postoperatorio va.

Sin esto el agente llama a ciegas: un dolor en el pie es irrelevante tras una
apendicectomía y es justo lo que hay que vigilar tras un reemplazo de rodilla;
fiebre de 37.6 en el día 1 es esperable y en el día 14 es alarma. El mismo
síntoma, respuestas opuestas.

Los tres datos se escriben a mano en la interfaz de llamada. La versión previa
los sacaba de los perfiles del dataset (`perfiles_*.xlsx`, 40 pacientes) con un
selector de paciente; se cambió a entrada manual porque para probar y demostrar
importa poder inventar el caso en el momento, no escoger de una lista fija. El
único dato que no es libre es la patología: tiene que coincidir con una de las
5 carpetas de `dataset/textos/` para poder acotar el RAG a ese corpus.
"""
import logging

logger = logging.getLogger("patients")

# `category` es el nombre EXACTO de la carpeta en dataset/textos/, que es lo que
# ingest.py guarda como metadata `category` en ChromaDB. Los nombres de carpeta
# son inconsistentes en el dataset oficial (mayúscula suelta en "Appendicitis",
# espacios en unas y guión bajo en otras); se copian tal cual a propósito, un
# typo acá deja el filtro del RAG sin resultados.
PROCEDURES = [
    {"key": "appendicitis", "label": "Apendicectomía", "category": "Appendicitis"},
    {"key": "cholecystitis", "label": "Colecistectomía", "category": "cholecystitis"},
    {"key": "colorectal_cancer", "label": "Colectomía", "category": "colorectal cancer"},
    {"key": "breast_cancer", "label": "Mastectomía", "category": "breast_cancer"},
    {
        "key": "total_joint_replacement",
        "label": "Reemplazo de cadera/rodilla",
        "category": "total joint replacement",
    },
]

_BY_KEY = {p["key"]: p for p in PROCEDURES}

# Los días que trae el dataset en trayectorias_postop_silver.xlsx son 1, 3, 7 y
# 14; se ofrecen algunos más para poder probar casos intermedios y tardíos.
DIAS_POSTOP = [1, 2, 3, 5, 7, 10, 14, 21, 30]


def list_procedures() -> list[dict]:
    return PROCEDURES


def get_procedure(key: str) -> dict | None:
    proc = _BY_KEY.get(key)
    if proc is None and key:
        logger.warning(f"procedimiento desconocido: {key!r} — llamada sin corpus acotado")
    return proc


def _first_name(nombre: str) -> str:
    """"Mauricio Juan González" -> "Mauricio". Por teléfono sobra el resto."""
    parts = str(nombre).split()
    return parts[0] if parts else "el paciente"


def build_context_prompt(
    nombre: str | None, procedimiento: str | None, dia_postop: int | None
) -> str | None:
    """Bloque que se le añade al SYSTEM_PROMPT con la ficha de la llamada.

    Acepta datos PARCIALES a propósito. Antes exigía nombre Y cirugía, y si
    faltaba cualquiera de los dos se descartaban ambos: alguien que escribía el
    nombre pero dejaba la cirugía sin escoger terminaba en una llamada
    totalmente genérica, con el nombre que sí había puesto tirado a la basura.
    Ahora lo conocido se usa y lo que falta se le pide al agente explícitamente.

    Escrito corto a propósito. Deepgram reenvía el prompt de sistema COMPLETO
    al proveedor en cada turno, así que cada palabra de más se paga otra vez en
    cada intercambio y acerca el momento en que la llamada revienta contra el
    límite por minuto."""
    if not nombre and not procedimiento:
        return None

    sabe, pregunta = [], []

    if nombre:
        sabe.append(f"Se llama {nombre}; llámalo {_first_name(nombre)}.")
    else:
        pregunta.append("su nombre")

    if procedimiento:
        proc = procedimiento.lower()
        sabe.append(f"Le hicieron una {proc}.")
        if dia_postop:
            sabe.append(f"HOY ES EL DÍA {dia_postop} DEL POSTOPERATORIO.")
        criterio = (
            f"\nJuzga todo contra esa cirugía: un síntoma lejos del sitio de la {proc}"
            " rara vez es complicación quirúrgica, y lo normal el día 1 deja de serlo"
            " el día 14. Consulta la guía en términos de esta cirugía, no genéricos."
        )
    else:
        pregunta.append("de qué lo operaron y hace cuántos días")
        criterio = (
            "\nNo sabes de qué lo operaron: averígualo en tus primeros turnos, porque"
            " sin eso no puedes juzgar si un síntoma es esperable ni buscar bien en la guía."
        )

    bloque = "\n\nPACIENTE — esto YA lo sabes, no lo preguntes ni lo hagas confirmar:\n"
    bloque += " ".join(sabe)
    if pregunta:
        bloque += f"\nLo que NO sabes y tenés que preguntar: {' y '.join(pregunta)}."
    return bloque + criterio + "\nSi algo no cuadra, escala igual."


def build_greeting(nombre: str | None, procedimiento: str | None) -> str:
    """Apertura de la llamada, ajustada a lo que se sabe. Preguntar "¿con quién
    hablo?" cuando el nombre ya está en pantalla suena a robot; darlo por
    sabido cuando no lo tenemos, a error."""
    if nombre and procedimiento:
        return (f"Hola, buenas. ¿Hablo con {nombre}? Le llamo del seguimiento "
                f"postoperatorio por su {procedimiento.lower()}. ¿Cómo se ha sentido?")
    if nombre:
        return (f"Hola, buenas. ¿Hablo con {nombre}? Le llamo del seguimiento "
                f"postoperatorio de la clínica. ¿De qué cirugía se está recuperando?")
    if procedimiento:
        return (f"Hola, buenas. Le habla el asistente de seguimiento postoperatorio, "
                f"le llamo por su {procedimiento.lower()}. ¿Con quién tengo el gusto?")
    return GREETING_GENERICO


GREETING_GENERICO = (
    "Hola, buenas. Le habla el asistente de seguimiento postoperatorio. "
    "¿Con quién tengo el gusto de hablar?"
)
