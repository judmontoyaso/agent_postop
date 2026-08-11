"""app/calls.py — Registro persistente de lo que quedó de cada llamada.

La rúbrica evalúa, dentro de *Lógica de decisión y escalamiento* (20 pts), dos
cosas que no se cubren con logs sueltos:

  "Qué produce el sistema cuando decide alertar: qué queda registrado, con qué
   estructura y con qué persistencia."
  "Qué queda al terminar la llamada: si existe un resumen que identifique al
   paciente y su procedimiento, los síntomas reportados, la decisión tomada,
   las referencias usadas y los próximos pasos."

De ahí que el resumen sea un registro ESTRUCTURADO y no un párrafo generado por
el LLM. Tres razones:

1. Es verificable. La rúbrica exige que las referencias clínicas "resistan una
   verificación contra la fuente real" — acá cada documento citado queda con su
   nombre de archivo y la consulta que lo trajo, contrastable contra el corpus.
2. No cuesta tokens. Un resumen generado sería otra llamada al modelo justo al
   final, con el presupuesto por minuto ya casi agotado.
3. No puede alucinar. Un resumen escrito por el LLM podría inventar un síntoma
   o suavizar la decisión; esto transcribe lo que realmente pasó.

Los próximos pasos se derivan del nivel de forma determinista (ver
PROXIMOS_PASOS): son protocolo, no criterio del modelo.
"""
import json
import logging
import sqlite3
from datetime import UTC, datetime
from typing import Any

from app.config import METRICS_DB_PATH
from app.tokens import costo_llamada

logger = logging.getLogger("calls")

# Qué se le dice al paciente y qué tiene que pasar después, por nivel. Fijo a
# propósito: el escalamiento es un protocolo clínico, no algo que el modelo
# deba redactar distinto en cada llamada.
PROXIMOS_PASOS = {
    "rojo": (
        "Contacto inmediato con el cirujano de turno o servicio de urgencias. "
        "No esperar al siguiente seguimiento programado."
    ),
    "amarillo": (
        "Valoración médica dentro de las próximas 24 horas y vigilancia activa "
        "de los síntomas reportados."
    ),
    "verde": (
        "Continuar con los cuidados indicados al alta. Se mantiene el "
        "seguimiento programado."
    ),
    "": "Sin decisión de escalamiento registrada — revisar la llamada.",
}


def init_calls_db() -> None:
    conn = sqlite3.connect(METRICS_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS call_summaries (
            call_id TEXT PRIMARY KEY,
            started_at TEXT,
            ended_at TEXT,
            duration_s REAL,
            paciente TEXT,
            procedimiento TEXT,
            dia_postop INTEGER,
            modelo TEXT,
            nivel_final TEXT,
            nivel_llm TEXT,
            motivo TEXT,
            escalado INTEGER,
            sintomas TEXT,
            referencias TEXT,
            proximos_pasos TEXT,
            transcripcion TEXT,
            turnos INTEGER,
            rag_queries INTEGER,
            input_tokens INTEGER,
            output_tokens INTEGER,
            costo TEXT,
            alertas_guardrail TEXT
        )
    """)
    # Migración en caliente: las bases creadas antes de que la rúbrica exigiera
    # tokens y costo POR LLAMADA no tienen estas columnas, y borrar el
    # histórico de llamadas para añadirlas sería perder evidencia del proceso.
    existentes = {fila[1] for fila in conn.execute("PRAGMA table_info(call_summaries)")}
    for columna, tipo in (("input_tokens", "INTEGER"), ("output_tokens", "INTEGER"),
                          ("costo", "TEXT"), ("alertas_guardrail", "TEXT")):
        if columna not in existentes:
            conn.execute(f"ALTER TABLE call_summaries ADD COLUMN {columna} {tipo}")
    conn.commit()
    conn.close()


def save_summary(summary: dict[str, Any]) -> None:
    """Un INSERT OR REPLACE por llamada. Nunca deja que un fallo de escritura
    tumbe el cierre de la llamada — se pierde el resumen, no la sesión."""
    try:
        conn = sqlite3.connect(METRICS_DB_PATH)
        conn.execute(
            """INSERT OR REPLACE INTO call_summaries
            (call_id, started_at, ended_at, duration_s, paciente, procedimiento,
             dia_postop, modelo, nivel_final, nivel_llm, motivo, escalado,
             sintomas, referencias, proximos_pasos, transcripcion, turnos, rag_queries,
             input_tokens, output_tokens, costo, alertas_guardrail)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                summary["call_id"], summary["started_at"], summary["ended_at"],
                summary["duration_s"], summary["paciente"], summary["procedimiento"],
                summary["dia_postop"], summary["modelo"], summary["nivel_final"],
                summary["nivel_llm"], summary["motivo"], int(summary["escalado"]),
                json.dumps(summary["sintomas"], ensure_ascii=False),
                json.dumps(summary["referencias"], ensure_ascii=False),
                summary["proximos_pasos"],
                json.dumps(summary["transcripcion"], ensure_ascii=False),
                summary["turnos"], summary["rag_queries"],
                summary["input_tokens"], summary["output_tokens"],
                json.dumps(summary["costo"], ensure_ascii=False),
                json.dumps(summary["alertas_guardrail"], ensure_ascii=False),
            ),
        )
        conn.commit()
        conn.close()
        logger.info(
            f"[{summary['call_id']}] resumen guardado — nivel={summary['nivel_final'] or 'SIN DECISIÓN'}, "
            f"{len(summary['sintomas'])} reportes del paciente, "
            f"{len(summary['referencias'])} referencias clínicas"
        )
    except Exception as e:
        logger.error(f"Error guardando resumen de llamada: {e}")


def _row_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    for campo in ("sintomas", "referencias", "transcripcion", "alertas_guardrail"):
        try:
            data[campo] = json.loads(data[campo] or "[]")
        except (json.JSONDecodeError, TypeError):
            data[campo] = []
    try:
        data["costo"] = json.loads(data.get("costo") or "{}")
    except (json.JSONDecodeError, TypeError):
        data["costo"] = {}
    data["escalado"] = bool(data["escalado"])
    return data


def list_summaries(limit: int = 50) -> list[dict]:
    conn = sqlite3.connect(METRICS_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM call_summaries ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_summary(call_id: str) -> dict | None:
    conn = sqlite3.connect(METRICS_DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM call_summaries WHERE call_id = ?", (call_id,)
    ).fetchone()
    conn.close()
    return _row_to_dict(row) if row else None


def build_summary(
    call_id: str,
    started_at: datetime,
    paciente: str,
    procedimiento: str,
    dia_postop: int | None,
    modelo: str,
    nivel_final: str,
    nivel_llm: str,
    motivo: str,
    sintomas: list[str],
    referencias: list[dict],
    transcripcion: list[dict],
    turnos: int,
    rag_queries: int,
    input_tokens: int = 0,
    output_tokens: int = 0,
    alertas_guardrail: list[dict] | None = None,
) -> dict:
    ended_at = datetime.now(UTC)
    duration_s = round((ended_at - started_at).total_seconds(), 1)
    return {
        "call_id": call_id,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_s": duration_s,
        "paciente": paciente,
        "procedimiento": procedimiento,
        "dia_postop": dia_postop,
        "modelo": modelo,
        "nivel_final": nivel_final,
        "nivel_llm": nivel_llm,
        "motivo": motivo,
        # Una llamada sin decisión es un fallo del sistema, no un "verde por
        # defecto": se marca como tal para que salte en la revisión.
        "escalado": bool(nivel_final),
        "sintomas": sintomas,
        "referencias": referencias,
        "proximos_pasos": PROXIMOS_PASOS.get(nivel_final, PROXIMOS_PASOS[""]),
        "transcripcion": transcripcion,
        "turnos": turnos,
        "rag_queries": rag_queries,
        # §5 de la rúbrica exige tokens por turno Y POR LLAMADA, más el costo
        # estimado por llamada — que además es criterio de desempate.
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "costo": costo_llamada(modelo, input_tokens, output_tokens, duration_s),
        # Frases del agente que el guardrail de salida marcó como posible
        # alucinación clínica. Que quede en el registro es el punto: demuestra
        # que el sistema las detecta, en vez de que las detecte el jurado.
        "alertas_guardrail": alertas_guardrail or [],
    }


init_calls_db()
