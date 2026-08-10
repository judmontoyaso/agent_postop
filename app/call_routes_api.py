"""app/call_routes_api.py — Consulta de los resúmenes de llamada persistidos.

Separado del WS de voz (app/voice/call_routes.py) a propósito: esto es la
superficie de lectura para el jurado y para el informe final, no parte del
camino crítico de la conversación.
"""
from fastapi import APIRouter, HTTPException

from app.calls import list_summaries, get_summary

router = APIRouter(prefix="/api/calls", tags=["calls"])


@router.get("")
def get_calls(limit: int = 50):
    """Historial de llamadas, la más reciente primero."""
    resumenes = list_summaries(limit=limit)
    return {
        "calls": resumenes,
        # Contador de llamadas que terminaron sin decisión de escalamiento.
        # La rúbrica trata el falso negativo como la falla catastrófica, y una
        # llamada sin decisión es peor: no hubo ni siquiera un juicio.
        "sin_decision": sum(1 for r in resumenes if not r["escalado"]),
    }


@router.get("/{call_id}")
def get_call(call_id: str):
    resumen = get_summary(call_id)
    if resumen is None:
        raise HTTPException(404, f"No hay resumen para la llamada {call_id}")
    return resumen
