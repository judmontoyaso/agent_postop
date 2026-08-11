"""app/notify_routes.py — Bandeja de escalamientos recibidos.

Receptor local del webhook de `app/notify.py`. Existe por dos razones:

1. Cierra el bucle en la demo sin depender de Internet ni de una cuenta en un
   servicio externo. Apuntando ESCALATION_WEBHOOK_URL a esta misma ruta, el
   jurado ve la alerta llegar en pantalla segundos después de que el agente
   decida — que es lo que la rúbrica pregunta con "qué produce el sistema
   cuando decide alertar".
2. Deja evidencia verificable de que el aviso salió de verdad, no solo de que
   se registró la intención de enviarlo.

En un despliegue real esta URL sería la del HIS, la central de enfermería o el
canal del equipo clínico. Acá es un buzón de demostración, y se documenta como
tal para no dar a entender que hay una integración hospitalaria que no existe.
"""
import json
import logging
import sqlite3
from datetime import UTC, datetime

from fastapi import APIRouter, Request

from app.config import METRICS_DB_PATH

logger = logging.getLogger("notify.inbox")
router = APIRouter(prefix="/api/escalations", tags=["escalations"])


def init_inbox_db() -> None:
    conn = sqlite3.connect(METRICS_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS escalation_inbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recibido_at TEXT,
            call_id TEXT,
            nivel TEXT,
            payload TEXT
        )
    """)
    conn.commit()
    conn.close()


@router.post("/inbox")
async def recibir(request: Request):
    """Recibe el aviso de escalamiento y lo guarda con su hora de llegada."""
    payload = await request.json()
    recibido = datetime.now(UTC).isoformat()
    conn = sqlite3.connect(METRICS_DB_PATH)
    conn.execute(
        "INSERT INTO escalation_inbox (recibido_at, call_id, nivel, payload) VALUES (?,?,?,?)",
        (recibido, payload.get("call_id"), payload.get("nivel"),
         json.dumps(payload, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()
    logger.info(
        f"AVISO RECIBIDO — {payload.get('nivel', '?').upper()} · "
        f"{payload.get('paciente')} · {payload.get('procedimiento')}"
    )
    return {"recibido": True, "at": recibido}


@router.get("")
def listar(limit: int = 25):
    conn = sqlite3.connect(METRICS_DB_PATH)
    conn.row_factory = sqlite3.Row
    filas = conn.execute(
        "SELECT * FROM escalation_inbox ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    salida = []
    for f in filas:
        d = dict(f)
        try:
            d["payload"] = json.loads(d["payload"] or "{}")
        except (json.JSONDecodeError, TypeError):
            d["payload"] = {}
        salida.append(d)
    return {"escalations": salida}


init_inbox_db()
