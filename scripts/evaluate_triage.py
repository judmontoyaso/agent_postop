"""scripts/evaluate_triage.py — Evalúa el piso de seguridad contra el dataset
etiquetado del reto.

QUÉ MIDE Y QUÉ NO. El triage completo lo decide el LLM leyendo el RAG, y pasar
3991 turnos por el modelo es inviable con los tiers gratuitos (y caro con los
de pago). Lo que sí se puede medir sin gastar una sola petición es el **piso de
seguridad**: los hard triggers de `app/agent/decision.py`, que fuerzan rojo al
margen de lo que decida el modelo y son la última defensa contra un falso
negativo.

Eso importa porque la rúbrica pesa la asimetría clínica por encima de todo:

    "No alertar cuando había que alertar. Un falso negativo en un escenario
     donde escalar era claramente lo correcto limita severamente la
     calificación de Lógica de decisión y escalamiento."

El número que sale de aquí es honesto sobre su alcance: cuántos de los turnos
etiquetados como rojo quedan cubiertos por el piso aunque el modelo falle por
completo, y cuántos verdes se elevarían de más (el costo del piso).

Uso:  python scripts/evaluate_triage.py
"""
import sys
import warnings
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore")

from app.agent.decision import check_hard_triggers
from app.config import DATASET_DIR

DATASET = "dataset_final.xlsx"


def main() -> None:
    import pandas as pd

    ruta = Path(DATASET_DIR) / DATASET
    if not ruta.exists():
        print(f"No existe {ruta} — descarga el dataset oficial primero.")
        return

    df = pd.read_excel(ruta)
    # Solo los turnos del paciente: son los que el agente evalúa. Las frases
    # del propio agente no se clasifican.
    pac = df[df["hablante"] == "paciente"].copy()
    print(f"Turnos de paciente etiquetados: {len(pac)}  (de {len(df)} en total)")
    print(f"Distribución: {dict(Counter(pac['label_ground_truth']))}\n")

    pac["disparo"] = pac["texto"].apply(
        lambda t: check_hard_triggers(str(t)) is not None
    )

    # La unidad correcta es la CONVERSACIÓN, no la frase. Las etiquetas del
    # dataset califican el caso, y dentro de un caso rojo hay frases benignas
    # ("un poquito molesto no más, uno aguanta") porque el paciente minimiza.
    # El agente escucha la llamada entera, así que lo que importa es si ALGUNA
    # frase del paciente activa el piso en algún momento de esa llamada.
    casos = pac.groupby("caso_id").agg(
        etiqueta=("label_ground_truth", "first"),
        disparo=("disparo", "any"),
        turnos=("texto", "size"),
    )

    print("=" * 74)
    print("PISO DE SEGURIDAD (hard triggers) — cobertura por CONVERSACIÓN")
    print("=" * 74)
    print(f"{'etiqueta real':<12} {'casos':>7} {'dispara':>9} {'%':>7}")
    for etiqueta in ("rojo", "amarillo", "verde"):
        sub = casos[casos["etiqueta"] == etiqueta]
        if not len(sub):
            continue
        d = int(sub["disparo"].sum())
        print(f"{etiqueta:<12} {len(sub):>7} {d:>9} {d / len(sub) * 100:>6.1f}%")

    por_turno = pac.groupby("label_ground_truth")["disparo"].mean()
    print("\n(por turno suelto, como referencia: " +
          ", ".join(f"{k} {v * 100:.1f}%" for k, v in por_turno.items()) + ")")

    rojos = casos[casos["etiqueta"] == "rojo"]
    verdes = casos[casos["etiqueta"] == "verde"]
    cubiertos = int(rojos["disparo"].sum())
    falsos_pos = int(verdes["disparo"].sum())

    print(f"""
LECTURA:
  {cubiertos}/{len(rojos)} conversaciones ROJO ({cubiertos / max(len(rojos), 1) * 100:.1f}%) se
  escalan aunque el modelo falle por completo. El piso vive en código, no en el
  prompt: ni una alucinación ni una inyección lo desactivan.

  {falsos_pos}/{len(verdes)} conversaciones VERDE ({falsos_pos / max(len(verdes), 1) * 100:.1f}%) se
  elevarían de más. Ese es el precio del sesgo asimétrico, y la rúbrica dice
  explícitamente que el falso negativo pesa más que el falso positivo.

  Las {len(rojos) - cubiertos} rojas restantes dependen del LLM leyendo el RAG,
  que es el camino normal. El piso es la red de seguridad, no el sistema.
""")

    print("=" * 74)
    print("QUÉ FRASES ACTIVAN EL PISO EN LAS CONVERSACIONES ROJAS")
    print("=" * 74)
    activadoras = pac[pac["disparo"] & pac["label_ground_truth"].eq("rojo")]
    for t in activadoras["texto"].head(6):
        print(f"  - {str(t)[:96]}")

    print("\n" + "=" * 74)
    print("FALSOS POSITIVOS — frases VERDES que activan el piso")
    print("=" * 74)
    print("(cada una indica un trigger demasiado amplio: revisar RED_HARD_TRIGGERS)")
    fp = pac[pac["disparo"] & pac["label_ground_truth"].eq("verde")]
    for t in fp["texto"].head(8):
        print(f"  - {str(t)[:96]}")


if __name__ == "__main__":
    main()
