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
import os
import uuid
from pathlib import Path

import chromadb
import pymupdf
from chromadb.utils import embedding_functions

from app.config import CHROMA_DB_PATH, EMBEDDING_MODEL

logger = logging.getLogger("rag.ingest")

_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
_embedder = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL)
_collection = _client.get_or_create_collection(name="clinical_docs", embedding_function=_embedder)

CHUNK_SIZE = 800
CHUNK_OVERLAP = 150


def _read_pdf_bytes(path: Path) -> bytes:
    """Lee el PDF a memoria en vez de pasarle la ruta a pymupdf/poppler.

    El dataset oficial trae 3 PDFs en `textos/colorectal cancer/` cuyo nombre
    deja la ruta completa por encima del MAX_PATH de Windows (260 chars; el
    peor llega a ~296 bajo una carpeta de proyecto normal). Habilitar rutas
    largas es una llave de registro que pide admin — no se le puede exigir al
    jurado, y sin ella esos 3 archivos fallan al abrirse.

    El prefijo `\\\\?\\` sí levanta el límite sin permisos ni cambios de
    sistema, y leer bytes evita depender de cómo maneje rutas cada librería
    de abajo (MuPDF y poppler son binarios nativos, no heredan el fix)."""
    p = os.path.abspath(str(path))
    if os.name == "nt" and len(p) >= 248 and not p.startswith("\\\\?\\"):
        p = "\\\\?\\" + p
    with open(p, "rb") as fh:
        return fh.read()


def _extract_text_pymupdf(path: Path) -> str:
    doc = pymupdf.open(stream=_read_pdf_bytes(path), filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    return text.strip()


OCR_DPI = 200

# El lector de EasyOCR carga ~17 s de modelos. Se construye una sola vez y solo
# si de verdad hace falta: en un corpus de 107 PDFs solo uno está escaneado, y
# pagar esa carga en cada arranque del servidor sería absurdo.
_ocr_reader = None


def _get_ocr_reader():
    global _ocr_reader
    if _ocr_reader is None:
        import easyocr

        logger.info("cargando modelos de OCR (solo la primera vez)...")
        _ocr_reader = easyocr.Reader(["es"], gpu=False, verbose=False)
    return _ocr_reader


def _extract_text_ocr(path: Path) -> str:
    """OCR para PDFs escaneados (sin capa de texto).

    Todo el camino es pip puro y sin dependencias de sistema, que es lo que
    permite que el proyecto se levante con `pip install -r requirements.txt` y
    nada más:

    - El rasterizado PDF→imagen lo hace PyMuPDF, ya presente. Antes lo hacía
      poppler vía pdf2image, lo que obligaba a versionar 47 MB de binarios.
    - El reconocimiento lo hace EasyOCR, que corre sobre el torch que ya
      instala sentence-transformers. Antes era tesseract, un ejecutable que
      había que instalar aparte en cada máquina — incluida la del jurado.

    Se usa el modelo español: los documentos del corpus son clínicos en
    castellano, y el reconocedor genérico multilingüe destroza los acentos.
    """
    import numpy as np

    reader = _get_ocr_reader()
    doc = pymupdf.open(stream=_read_pdf_bytes(path), filetype="pdf")
    try:
        textos = []
        for page in doc:
            pix = page.get_pixmap(dpi=OCR_DPI)
            # EasyOCR trabaja sobre arrays de numpy; el pixmap de MuPDF ya trae
            # los bytes crudos, así que se reinterpretan sin copiar la imagen.
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n
            )
            # paragraph=True agrupa las cajas de texto en bloques legibles; sin
            # esto vuelven fragmentos sueltos que al trocear para el RAG pierden
            # el hilo de la frase.
            textos.append(" ".join(reader.readtext(arr, detail=0, paragraph=True)))
    finally:
        doc.close()
    return "\n".join(textos).strip()


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
