"""scripts/setup_check.py — Verifica que el setup cumple el gate G2 (<=15 min).
Corre esto después de seguir el README desde cero, antes de grabar el demo."""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def check(name: str, condition: bool, hint: str = "") -> bool:
    status = "OK" if condition else "FALTA"
    print(f"[{status}] {name}" + (f" — {hint}" if not condition and hint else ""))
    return condition


def main():
    ok = True
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    py_ok = (3, 10) <= sys.version_info[:2] < (3, 13)
    ok &= check(f"Python >= 3.10 y < 3.13 (actual: {py_ver})", py_ok, "se requiere Python 3.10, 3.11 o 3.12")

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
