import os
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
DIR_DATOS = RAIZ / "data"
DIR_WEB = RAIZ / "web"
DIR_MODELOS = RAIZ / "models"
DIR_ETL = RAIZ / "src" / "EtlPython"
RUTA_SQLITE = DIR_DATOS / "nba.db"
RUTA_HORARIOS = DIR_DATOS / "horarios.json"


def usar_sqlite():
    return os.environ.get("NBA_SQLITE", "1") == "1"


def hoy_str():
    return datetime.now().strftime("%Y-%m-%d")


def ahora_utc():
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def temporada_actual(fecha=None):
    f = fecha or hoy_str()
    y = int(f[:4])
    m = int(f[5:7])
    if m >= 8:
        return f"{y}-{str(y + 1)[2:]}"
    return f"{y - 1}-{str(y)[2:]}"


def a_utc_naive(texto):
    import pandas as pd

    ts = pd.to_datetime(texto)
    if ts.tzinfo is not None:
        return ts.tz_convert("UTC").tz_localize(None).to_pydatetime()
    return ts.to_pydatetime()
