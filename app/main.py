"""app/main.py — Entrypoint FastAPI. Sirve API admin, WS de llamada, y estáticos."""
import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.rag.admin_routes import router as admin_router
from app.voice.call_routes import router as call_router
from app.metrics import summary

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="TechSphere Postop Voice Agent")

app.include_router(admin_router)
app.include_router(call_router)


@app.get("/api/metrics/summary")
def metrics_summary():
    return summary()


app.mount("/admin", StaticFiles(directory="static/admin", html=True), name="admin")
app.mount("/call", StaticFiles(directory="static/call", html=True), name="call")
