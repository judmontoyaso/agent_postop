"""app/patient_routes.py — Opciones de patología y día para el formulario de llamada."""
from fastapi import APIRouter

from app.patients import DIAS_POSTOP, list_procedures

router = APIRouter(prefix="/api", tags=["call-setup"])


@router.get("/procedures")
def get_procedures():
    """Patologías disponibles (las 5 del corpus clínico) y días ofrecidos.
    La interfaz de llamada arma sus selects con esto, así que agregar una
    patología nueva al corpus es tocar solo app/patients.py."""
    return {
        "procedures": [
            {"key": p["key"], "label": p["label"]} for p in list_procedures()
        ],
        "dias_postop": DIAS_POSTOP,
    }
