"""app/metrics.py — Tracker de latencia/tokens/RAG queries por turno.

Adaptado de jada/tools/metrics.py. Alimenta las métricas obligatorias del
README (rubrica-evaluacion.md): P50/P95 latencia, tokens in/out, invocaciones
de modelo por turno, queries RAG por llamada, costo estimado por llamada.
"""
import logging
import sqlite3
import statistics
from datetime import datetime, timezone
from typing import Any

from app.config import METRICS_DB_PATH

logger = logging.getLogger("metrics")


def init_metrics_db() -> None:
    conn = sqlite3.connect(METRICS_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS turn_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            call_id TEXT,
            turn_index INTEGER,
            model_id TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            model_invocations INTEGER,
            rag_queries INTEGER,
            speech_end_to_audio_start_ms REAL,
            escalation_level TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def track_turn(
    call_id: str,
    turn_index: int,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    model_invocations: int,
    rag_queries: int,
    speech_end_to_audio_start_ms: float,
    escalation_level: str = "",
) -> None:
    try:
        conn = sqlite3.connect(METRICS_DB_PATH)
        conn.execute(
            """INSERT INTO turn_metrics
            (call_id, turn_index, model_id, input_tokens, output_tokens,
             model_invocations, rag_queries, speech_end_to_audio_start_ms,
             escalation_level, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (call_id, turn_index, model_id, input_tokens, output_tokens,
             model_invocations, rag_queries, speech_end_to_audio_start_ms,
             escalation_level, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Error guardando métrica de turno: {e}")


def summary() -> dict[str, Any]:
    """P50/P95 de latencia + promedios de tokens/rag_queries. Usar para llenar el README."""
    conn = sqlite3.connect(METRICS_DB_PATH)
    rows = conn.execute(
        "SELECT speech_end_to_audio_start_ms, input_tokens, output_tokens, "
        "model_invocations, rag_queries FROM turn_metrics"
    ).fetchall()
    conn.close()

    if not rows:
        return {"turns": 0}

    latencies = sorted(r[0] for r in rows if r[0] is not None)
    input_tok = [r[1] for r in rows]
    output_tok = [r[2] for r in rows]
    invocations = [r[3] for r in rows]
    rag_q = [r[4] for r in rows]

    def pct(data: list[float], p: float) -> float:
        if not data:
            return 0.0
        k = int(round((p / 100) * (len(data) - 1)))
        return data[k]

    return {
        "turns": len(rows),
        "latency_p50_ms": pct(latencies, 50),
        "latency_p95_ms": pct(latencies, 95),
        "avg_input_tokens": statistics.mean(input_tok) if input_tok else 0,
        "avg_output_tokens": statistics.mean(output_tok) if output_tok else 0,
        "avg_model_invocations_per_turn": statistics.mean(invocations) if invocations else 0,
        "avg_rag_queries_per_call": statistics.mean(rag_q) if rag_q else 0,
    }


init_metrics_db()
