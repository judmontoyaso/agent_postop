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


# Aviso de tratamiento de datos. Una llamada que graba y transcribe a un
# paciente cae bajo la Ley 1581 de 2012 (habeas data) en Colombia: el titular
# tiene que saber que sus datos se están recogiendo y para qué. Va en la
# apertura, corto y en lenguaje normal — no un párrafo legal leído en voz alta,
# que nadie escucha y suena a robot.
AVISO_GRABACION = "Le cuento que esta llamada queda registrada en su historia clínica."


def build_greeting(nombre: str | None, procedimiento: str | None) -> str:
    """Apertura de la llamada, ajustada a lo que se sabe. Preguntar "¿con quién
    hablo?" cuando el nombre ya está en pantalla suena a robot; darlo por
    sabido cuando no lo tenemos, a error."""
    if nombre and procedimiento:
        cuerpo = (f"¿Hablo con {nombre}? Le llamo del seguimiento postoperatorio "
                  f"por su {procedimiento.lower()}.")
        pregunta = "¿Cómo se ha sentido?"
    elif nombre:
        cuerpo = f"¿Hablo con {nombre}? Le llamo del seguimiento postoperatorio de la clínica."
        pregunta = "¿De qué cirugía se está recuperando?"
    elif procedimiento:
        cuerpo = (f"Le habla el asistente de seguimiento postoperatorio, "
                  f"le llamo por su {procedimiento.lower()}.")
        pregunta = "¿Con quién tengo el gusto?"
    else:
        cuerpo = "Le habla el asistente de seguimiento postoperatorio."
        pregunta = "¿Con quién tengo el gusto de hablar?"

    return f"Hola, buenas. {cuerpo} {AVISO_GRABACION} {pregunta}"


GREETING_GENERICO = (
    "Hola, buenas. Le habla el asistente de seguimiento postoperatorio. "
    "¿Con quién tengo el gusto de hablar?"
)

MAX_REPORTES_EN_MEMORIA = 10
MAX_CHARS_POR_REPORTE = 130
MAX_CHARS_ECO = 120


def build_reconnect_line(sintomas: list[str], ultima_pregunta: str = "") -> str:
    """Lo que dice el agente al retomar tras un corte.

    Le devuelve al paciente lo último que le oyó, en vez de un "se cortó" a
    secas. Dos motivos, y el segundo es clínico:

    1. Un "perdón, se cortó" sin más deja al paciente sin saber si tiene que
       repetirlo todo — y repetir cuesta turnos, que es justo lo que agota la
       cuota que provocó el corte.
    2. Si el STT entendió mal ("la árida está roja" por "la herida está roja"),
       devolvérselo es la única forma de que lo corrija. Seguir en silencio
       sobre una transcripción equivocada es peor que perder el turno.
    """
    # SIEMPRE termina en una pregunta. Deepgram trata esta frase como saludo:
    # la dice y cede el turno, esperando al paciente. Una línea que acaba en
    # "sigo con usted" suena a que el agente va a continuar y a continuación se
    # queda callado — parece que se colgó. Repitiendo la pregunta que tenía
    # pendiente, el paciente sabe exactamente qué contestar y la conversación
    # arranca sola.
    pendiente = (ultima_pregunta or "").strip()
    if len(pendiente) > MAX_CHARS_ECO:
        pendiente = pendiente[:MAX_CHARS_ECO].rsplit(" ", 1)[0] + "…"
    retoma = f" Le preguntaba: {pendiente}" if pendiente else " ¿Cómo se ha sentido?"

    # El último turno útil, no literalmente el último: repetirle "alcancé a
    # escucharle: sí" no le confirma nada. Se busca hacia atrás la última frase
    # con contenido real.
    ultimo = next(
        (s.strip() for s in reversed(sintomas) if len(s.strip()) >= 15),
        next((s.strip() for s in reversed(sintomas) if s.strip()), ""),
    )
    if len(ultimo) < 15:
        return f"Perdón, se cortó un segundo.{retoma}"

    eco = ultimo.rstrip(" .,;")
    if len(eco) > MAX_CHARS_ECO:
        # Cortar en una coma o punto antes del límite, no a mitad de frase: el
        # TTS lo lee en voz alta y un corte arbitrario suena a que divaga.
        recorte = eco[:MAX_CHARS_ECO]
        corte = max(recorte.rfind(","), recorte.rfind("."), recorte.rfind(";"))
        if corte > 40:
            eco = recorte[:corte].rstrip(" .,;")
        else:
            # Frase corrida sin puntuación: no hay dónde cortar limpio, así que
            # se marca la cita como incompleta. El TTS lee los puntos
            # suspensivos como una pausa, y así no suena a que se quedó colgado
            # a mitad de palabra.
            eco = recorte.rsplit(" ", 1)[0].rstrip(" .,;") + "…"
    eco = eco[0].lower() + eco[1:]
    cierre = "" if eco.endswith("…") else "."

    return f"Perdón, se cortó un segundo, pero sí alcancé a escucharle: {eco}{cierre}{retoma}"


def build_memory_prompt(
    sintomas: list[str], ultima_pregunta: str = "", nivel_ya_decidido: str = ""
) -> str:
    """Memoria de la llamada, para inyectar al RECONECTAR.

    Cada sesión nueva con Deepgram arranca solo con el prompt de sistema: el
    proveedor no recuerda nada de la anterior. Sin esto, un cambio de proveedor
    por cuota agotada dejaba al agente empezando la llamada de cero — llegó a
    saludar otra vez y a preguntar de qué habían operado a una paciente que ya
    le había contado todo, hasta que ella respondió "ya te respondí esa
    pregunta". Peor aún: la llamada terminó sin decisión de escalamiento pese a
    haber recogido 13 reportes, porque los perdió por el camino.

    Se prioriza lo que dijo el PACIENTE, que es el dato clínico; las frases del
    agente se pueden reconstruir solas. Va acotado porque este bloque se
    reenvía en cada turno de la sesión nueva."""
    if not sintomas:
        return ""

    recientes = [s.strip()[:MAX_CHARS_POR_REPORTE]
                 for s in sintomas[-MAX_REPORTES_EN_MEMORIA:] if s.strip()]
    if not recientes:
        return ""

    bloque = ("\n\nESTA LLAMADA YA VENÍA EN CURSO (se cortó la conexión y la retomás)."
              "\nEl paciente YA te contó esto — NO se lo vuelvas a preguntar:\n")
    bloque += "\n".join(f"- {s}" for s in recientes)

    if ultima_pregunta:
        bloque += f"\nLo último que le preguntaste: \"{ultima_pregunta[:MAX_CHARS_POR_REPORTE]}\""
    if nivel_ya_decidido:
        bloque += (f"\nYa registraste nivel {nivel_ya_decidido}. Solo volvés a llamar a"
                   " `escalar_paciente` si aparece algo que lo empeore.")

    bloque += ("\nRetomá donde ibas, sin saludar de nuevo ni pedirle que repita:"
               " seguí con el siguiente tema que te falte.")
    return bloque
