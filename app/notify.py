"""app/notify.py — Aviso saliente cuando se escala un paciente.

Sin esto el escalamiento se queda dentro del sistema: se registra en SQLite y
se le dice al paciente, pero en la clínica nadie se entera. La rúbrica pregunta
"qué produce el sistema cuando decide alertar", y un registro que nadie lee no
produce nada.

Se implementa como webhook saliente (`ESCALATION_WEBHOOK_URL` en .env) porque
es lo que se conecta a cualquier cosa real —un HIS, una central de enfermería,
Slack, un Google Sheet— sin atarse a un proveedor. Si no está configurado, no
falla: se registra en el log y sigue, para que el proyecto se pueda levantar
sin dependencias externas (gate G2).
"""
import asyncio
import logging

import httpx

from app.config import ESCALATION_WEBHOOK_URL

logger = logging.getLogger("notify")

TIMEOUT_S = 5
NIVELES_QUE_AVISAN = {"amarillo", "rojo"}


def _payload(resumen: dict) -> dict:
    """Solo lo accionable. La transcripción completa NO viaja: son datos de
    salud de un paciente identificado y no hacen falta para actuar — quien
    reciba el aviso puede consultarla en GET /api/calls si la necesita."""
    return {
        "call_id": resumen["call_id"],
        "paciente": resumen["paciente"],
        "procedimiento": resumen["procedimiento"],
        "dia_postop": resumen["dia_postop"],
        "nivel": resumen["nivel_final"],
        "motivo": resumen["motivo"],
        "proximos_pasos": resumen["proximos_pasos"],
        "sintomas": resumen["sintomas"],
        "referencias": [r["documento"] for r in resumen.get("referencias", [])],
        "ended_at": resumen["ended_at"],
    }


async def avisar_escalamiento(resumen: dict) -> None:
    """Dispara el aviso. Nunca propaga: que el webhook esté caído no puede
    tumbar el cierre de una llamada ni impedir que se guarde el resumen."""
    nivel = (resumen.get("nivel_final") or "").lower()
    if nivel not in NIVELES_QUE_AVISAN:
        return

    if not ESCALATION_WEBHOOK_URL:
        logger.warning(
            f"[{resumen['call_id']}] ESCALAMIENTO {nivel.upper()} sin webhook configurado — "
            f"nadie en la clínica fue notificado. Definir ESCALATION_WEBHOOK_URL en .env."
        )
        return

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.post(ESCALATION_WEBHOOK_URL, json=_payload(resumen))
        logger.info(
            f"[{resumen['call_id']}] aviso de escalamiento {nivel.upper()} enviado "
            f"-> HTTP {r.status_code}"
        )
    except Exception as e:
        # Se registra como ERROR a propósito: un escalamiento rojo que no llegó
        # a la clínica es exactamente el fallo que no debe pasar en silencio.
        logger.error(
            f"[{resumen['call_id']}] NO se pudo avisar el escalamiento {nivel.upper()}: {e}"
        )


# Referencias fuertes a las tareas en vuelo. asyncio solo guarda una referencia
# débil: sin esto el recolector de basura puede llevarse el aviso a mitad de
# envío y el escalamiento no llega a la clínica sin dejar rastro del fallo.
_tareas_en_vuelo: set[asyncio.Task] = set()


def avisar_en_segundo_plano(resumen: dict) -> None:
    """Dispara sin bloquear el cierre de la llamada."""
    try:
        tarea = asyncio.create_task(avisar_escalamiento(resumen))
    except RuntimeError:
        return  # sin loop en marcha (p. ej. desde un script) — no es crítico
    _tareas_en_vuelo.add(tarea)
    tarea.add_done_callback(_tareas_en_vuelo.discard)
