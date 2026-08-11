"""scripts/ingest_dataset.py — Indexa dataset/textos/*.pdf en ChromaDB.
Correr una vez tras descargar el dataset oficial del reto."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DATASET_DIR
from app.rag.ingest import ingest_directory


def main():
    textos_dir = Path(DATASET_DIR) / "textos"
    if not textos_dir.exists():
        print(f"No existe {textos_dir} — descarga el dataset oficial primero.")
        return

    results = ingest_directory(textos_dir)
    ok = [r for r in results if not r.get("error")]
    failed = [r for r in results if r.get("error")]
    ocr_used = [r for r in ok if r.get("used_ocr")]

    by_category: dict[str, int] = {}
    for r in ok:
        cat = r.get("category") or "(sin categoría)"
        by_category[cat] = by_category.get(cat, 0) + 1

    print(f"Indexados: {len(ok)}/{len(results)}")
    print(f"Via OCR (PDFs escaneados sin texto): {len(ocr_used)}")
    for cat, count in sorted(by_category.items()):
        print(f"  {cat}: {count}")
    if ocr_used:
        print("PDFs que requirieron OCR:")
        for r in ocr_used:
            print(f"  - {r['file']}")
    if failed:
        print("Fallidos:")
        for f in failed:
            print(f"  - {f['file']}: {f['error']}")


if __name__ == "__main__":
    main()
