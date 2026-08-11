"""app/rag/admin_routes.py — Consola admin (G5): subir/listar/borrar documentos.

Gate G5 exige que subir un documento haga que el agente lo use, y borrarlo
haga que lo olvide, ambos en caliente sin reiniciar el proceso. upsert/delete
directo sobre ChromaDB (ver app/rag/ingest.py) cumple esto porque la
colección persistente se consulta en cada turno, no se cachea en memoria.
"""
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.rag.ingest import delete_document, ingest_pdf, list_documents, new_source_id

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/documents")
def get_documents():
    return {"documents": list_documents()}


@router.post("/documents")
async def upload_document(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Solo se aceptan PDFs")

    source_id = new_source_id()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    try:
        result = ingest_pdf(tmp_path, source_id=source_id, display_name=file.filename)
        result["source_id"] = source_id
        return result
    finally:
        tmp_path.unlink(missing_ok=True)


@router.delete("/documents/{source_id}")
def remove_document(source_id: str):
    delete_document(source_id)
    return {"deleted": source_id}
