"""scripts/setup_check.py — Verifica que el setup cumple el gate G2 (<=15 min).
Corre esto después de seguir el README desde cero, antes de grabar el demo."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def check(name: str, condition: bool, hint: str = "") -> bool:
    status = "OK" if condition else "FALTA"
    print(f"[{status}] {name}" + (f" — {hint}" if not condition and hint else ""))
    return condition


def main():
    ok = True
    ok &= check("GROQ_API_KEY", bool(os.getenv("GROQ_API_KEY")), "definir en .env")
    ok &= check("DEEPGRAM_API_KEY", bool(os.getenv("DEEPGRAM_API_KEY")), "definir en .env")

    dataset_dir = Path(os.getenv("DATASET_DIR", "./dataset"))
    ok &= check("dataset/textos existe", (dataset_dir / "textos").exists(),
                "descargar dataset del repo oficial a dataset/")

    try:
        import chromadb, sentence_transformers, groq, websockets, fastapi  # noqa
        check("dependencias Python instaladas", True)
    except ImportError as e:
        check("dependencias Python instaladas", False, str(e))
        ok = False

    print("\nResultado:", "LISTO" if ok else "REVISAR PENDIENTES ARRIBA")


if __name__ == "__main__":
    main()
