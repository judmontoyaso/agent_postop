"""app/rag/ingest.py — Ingesta de conocimiento clínico a ChromaDB.

Embedder local (sentence-transformers, multilingüe ES/EN) — se probó Voyage AI
primero pero la cuenta sin tarjeta cae en rate limit (3 RPM/10K TPM) que revienta
en el primer PDF. Local también evita meter otro round-trip de red en el camino
crítico de la llamada de voz en vivo (cada RAG query durante la conversación).

Alimenta la colección desde:
- dataset/textos/*.pdf (107 docs clínicos ES/EN) — incluye un PDF escaneado
  sin texto (trampa del dataset): si pymupdf no extrae texto, cae a OCR.
- Documentos subidos por la consola admin (G5) — misma función, permite
  add/delete en caliente sin reiniciar el proceso.
"""
import logging
import uuid
from pathlib import Path

import pymupdf
import chromadb
from chromadb.utils import embedding_functions

from app.config import CHROMA_DB_PATH, EMBEDDING_MODEL, POPPLER_PATH, TESSERACT_CMD

logger = logging.getLogger("rag.ingest")

_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
_embedder = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL)
_collection = _client.get_or_create_collection(name="clinical_docs", embedding_function=_embedder)

CHUNK_SIZE = 800
CHUNK_OVERLAP = 150


def _extract_text_pymupdf(path: Path) -> str:
    doc = pymupdf.open(str(path))
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    return text.strip()


def _extract_text_ocr(path: Path) -> str:
    """Fallback OCR para PDFs escaneados (sin capa de texto). Usa rutas
    explícitas a poppler/tesseract (ver app/config.py) en vez de depender
    del PATH — necesario porque el server ya corriendo no ve actualizaciones
    de PATH hechas por un instalador después de que arrancó el proceso."""
    import pytesseract
    from pdf2image import convert_from_path

    if TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

    convert_kwargs = {"dpi": 200}
    if POPPLER_PATH:
        convert_kwargs["poppler_path"] = POPPLER_PATH

    images = convert_from_path(str(path), **convert_kwargs)
    return "\n".join(pytesseract.image_to_string(img, lang="spa+eng") for img in images).strip()


def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start:start + size])
        start += size - overlap
    return [c.strip() for c in chunks if c.strip()]


def ingest_pdf(
    path: Path, source_id: str | None = None, category: str = "", display_name: str | None = None
) -> dict:
    """Ingesta un PDF individual. Usa OCR automáticamente si no hay texto extraíble.
    Nunca deja que un PDF problemático (OCR sin poppler/tesseract, corrupto, etc.)
    tumbe el resto del batch — se reporta como error y se sigue con el siguiente.
    `display_name` es el nombre a guardar/mostrar (p.ej. el nombre original que
    subió el usuario en la consola admin) — path.name sería el de un archivo
    temporal random si viene de un upload."""
    display_name = display_name or path.name
    text = _extract_text_pymupdf(path)
    used_ocr = False
    if not text:
        logger.info(f"{display_name}: sin texto extraíble, usando OCR fallback")
        try:
            text = _extract_text_ocr(path)
            used_ocr = True
        except Exception as e:
            logger.error(f"{display_name}: OCR falló ({e})")
            return {"file": display_name, "chunks": 0, "used_ocr": False, "error": f"OCR falló: {e}"}

    if not text:
        return {"file": display_name, "chunks": 0, "used_ocr": used_ocr, "error": "sin texto tras OCR"}

    source_id = source_id or path.stem
    chunks = _chunk_text(text)
    ids = [f"{source_id}_{i}" for i in range(len(chunks))]
    metadatas = [
        {"source": display_name, "source_id": source_id, "chunk": i, "category": category}
        for i in range(len(chunks))
    ]

    _collection.upsert(ids=ids, documents=chunks, metadatas=metadatas)
    return {"file": display_name, "chunks": len(chunks), "used_ocr": used_ocr, "category": category}


def ingest_directory(dir_path: Path) -> list[dict]:
    """Recorre dataset/textos/<categoria>/*.pdf recursivamente.
    El nombre de la subcarpeta (Appendicitis, breast_cancer, ...) se guarda
    como metadata `category` para poder filtrar/depurar el RAG por patología."""
    results = []
    for pdf in sorted(dir_path.rglob("*.pdf")):
        category = pdf.parent.name if pdf.parent != dir_path else ""
        results.append(ingest_pdf(pdf, category=category))
    return results


def delete_document(source_id: str) -> None:
    """G5: al borrar un documento, el agente debe dejar de usarlo de inmediato."""
    _collection.delete(where={"source_id": source_id})


def list_documents() -> list[dict]:
    data = _collection.get(include=["metadatas"])
    seen = {}
    for meta in data["metadatas"]:
        sid = meta["source_id"]
        seen.setdefault(sid, {"source_id": sid, "source": meta["source"], "chunks": 0})
        seen[sid]["chunks"] += 1
    return list(seen.values())


def new_source_id() -> str:
    return uuid.uuid4().hex[:12]
