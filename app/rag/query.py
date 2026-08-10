"""app/rag/query.py — Consulta a la colección ChromaDB desde las tools del agente."""
from app.rag.ingest import _collection


def query_knowledge_base(query_text: str, n_results: int = 2) -> list[dict]:
    # n_results bajo a propósito: cada chunk que vuelve entra al contexto del
    # think provider (Groq) en la siguiente llamada — con free tier de Groq
    # (12000 TPM) el contexto crece rápido y satura el límite a mitad de
    # conversación. Menos resultados = conversaciones más largas sin cortarse.
    results = _collection.query(query_texts=[query_text], n_results=n_results)
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]
    return [
        {"text": doc[:350], "source": meta.get("source"), "relevance": 1 - dist}
        for doc, meta, dist in zip(docs, metas, distances)
    ]
