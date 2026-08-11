"""app/rag/query.py — Consulta a la colección ChromaDB desde las tools del agente."""
import logging

from app.rag.ingest import _collection

logger = logging.getLogger("rag.query")


def _where_filter(category: str | None) -> dict | None:
    """Restringe la búsqueda al corpus de la cirugía del paciente.

    Sin filtro, "dolor al caminar" trae guías de apendicectomía para alguien
    con un reemplazo de rodilla: el corpus tiene 5 patologías mezcladas y el
    embedder no sabe cuál es la que importa.

    El filtro incluye a propósito los documentos con category vacía. Los que
    sube el jurado por la consola admin (G5) entran sin categoría — si el
    filtro fuera `{"category": cat}` a secas, un documento recién subido sería
    invisible para el agente y el gate G5 fallaría en la demo.
    """
    if not category:
        return None
    return {"$or": [{"category": category}, {"category": ""}]}


def query_knowledge_base(
    query_text: str, n_results: int = 2, category: str | None = None
) -> list[dict]:
    # n_results bajo a propósito: cada chunk que vuelve entra al contexto del
    # think provider (Groq) en la siguiente llamada — con free tier de Groq
    # (12000 TPM) el contexto crece rápido y satura el límite a mitad de
    # conversación. Menos resultados = conversaciones más largas sin cortarse.
    where = _where_filter(category)
    results = _collection.query(
        query_texts=[query_text], n_results=n_results, **({"where": where} if where else {})
    )
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    if not docs and where:
        # Mejor un resultado de otra patología que ninguno: quedarse sin
        # contexto haría que el agente conteste de memoria, que es justo lo
        # que el RAG existe para evitar.
        logger.info(f"sin resultados en category={category!r} — reintentando sin filtro")
        return query_knowledge_base(query_text, n_results=n_results, category=None)

    return [
        {"text": doc[:350], "source": meta.get("source"), "relevance": 1 - dist}
        # strict=False a propósito: si ChromaDB devolviera las tres listas
        # descuadradas, cortar por la más corta es preferible a lanzar una
        # excepción en mitad de una llamada de voz por un resultado de menos.
        for doc, meta, dist in zip(docs, metas, distances, strict=False)
    ]
