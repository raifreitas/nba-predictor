import os
import statistics
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Modeling"))

import config
import db_utils

API_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds/"
DEPORTE_FALLBACK = "basketball_nba"


def obtener_cuotas(fecha=None):
    key = os.environ.get("THE_ODDS_API_KEY")
    if not key:
        print("AVISO: THE_ODDS_API_KEY no definida")
        return []

    params = {
        "apiKey": key,
        "regions": "us",
        "markets": "totals",
        "oddsFormat": "decimal",
    }
    resp = requests.get(API_URL, params=params, timeout=45)
    resp.raise_for_status()
    eventos = resp.json()

    salida = []
    for ev in eventos:
        commence = (ev.get("commence_time") or "")[:10]
        if fecha and commence != fecha:
            continue
        lineas, overs, unders = [], [], []
        equipos_txt = f"{ev['away_team']} @ {ev['home_team']}"
        game_id = f"OAA-{ev['id']}"
        for bk in ev.get("bookmakers", []):
            for mk in bk.get("markets", []):
                if mk.get("key") != "totals":
                    continue
                over = next((o for o in mk["outcomes"] if o["name"] == "Over"), None)
                under = next((o for o in mk["outcomes"] if o["name"] == "Under"), None)
                if over is None or under is None or over.get("point") is None:
                    continue
                lineas.append(float(over["point"]))
                overs.append(float(over.get("price", 1.9)))
                unders.append(float(under.get("price", 1.9)))
        if not lineas:
            continue
        linea = statistics.mode(lineas)
        idx = min(range(len(lineas)), key=lambda i: abs(lineas[i] - linea))
        salida.append(
            {
                "GameId": game_id,
                "Equipos": equipos_txt,
                "Linea": linea,
                "OverCuota": overs[idx],
                "UnderCuota": unders[idx],
                "Casas": len(lineas),
            }
        )
    return salida


def snapshot_odds(fecha):
    cuotas = obtener_cuotas(fecha)
    ahora = config.ahora_utc()
    filas = [
        {
            "Fecha": fecha,
            "GameId": c["GameId"],
            "Equipos": c["Equipos"],
            "SnapshotUtc": ahora,
            "Casa": "MODA",
            "Linea": c["Linea"],
            "OverCuota": c["OverCuota"],
            "UnderCuota": c["UnderCuota"],
        }
        for c in cuotas
    ]
    db_utils.insertar_filas("LineaSnapshots", filas)
    db_utils.meta_set("ultimo_snapshot_odds", ahora)
    print(f"[odds] {len(filas)} snapshots guardados para {fecha}")
    return cuotas


if __name__ == "__main__":
    f = sys.argv[1] if len(sys.argv) > 1 else None
    snapshot_odds(f or config.hoy_str())
