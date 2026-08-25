import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Modeling"))

import db_utils
import etl_main

HORAS_LIMITE = 20


def falta_matinal():
    ultimo = db_utils.meta_get("ultimo_etl")
    if not ultimo:
        return 1
    from datetime import datetime

    try:
        dt = datetime.strptime(ultimo, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return 1
    horas = (datetime.utcnow() - dt).total_seconds() / 3600
    return 0 if horas < HORAS_LIMITE else 1


def ejecutar_respaldo():
    ayer = (datetime.utcnow() - __import__("datetime").timedelta(days=1)).strftime("%Y-%m-%d")
    hoy = datetime.utcnow().strftime("%Y-%m-%d")
    etl_main.cargar_dias(ayer, hoy)


if __name__ == "__main__":
    print(f"FALTA={falta_matinal()}")
