import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import db_utils


def verificar(fecha=None):
    filtros = "WHERE Estado='PENDIENTE'"
    params = {}
    if fecha:
        filtros += " AND Fecha=?"
        params["fecha"] = fecha

    picks = db_utils.leer_sql(f"SELECT * FROM Predicciones {filtros}", params)
    if picks.empty:
        print("[verificar] sin picks pendientes")
        return 0

    resueltos = 0
    for _, p in picks.iterrows():
        cal = db_utils.leer_sql(
            "SELECT HoraUtc, TotalFinal FROM Calendario WHERE GameId=?",
            {"game_id": p["GameId"]},
        )
        if cal.empty:
            continue
        total_final = cal["TotalFinal"].iloc[0]
        if pd.isna(total_final):
            continue

        hora_inicio = cal["HoraUtc"].iloc[0]
        estado_nuevo, motivo = _resolver(p, total_final, hora_inicio)
        db_utils.ejecutar(
            "UPDATE Predicciones SET Estado=?, ResueltoUtc=? WHERE Id=?",
            (estado_nuevo, config.ahora_utc(), int(p["Id"])),
        )
        print(f"[verificar] {p['Partido']} total={total_final} linea={p['Linea']} -> {estado_nuevo} {motivo}")
        resueltos += 1
    return resueltos


def _resolver(pick, total_final, hora_utc_inicio):
    creado = datetime.strptime(str(pick["CreadoUtc"]), "%Y-%m-%dT%H:%M:%SZ")
    try:
        inicio = config.a_utc_naive(hora_utc_inicio)
        if creado > inicio:
            return "NO_VALIDA", "(creado tras el inicio)"
    except (TypeError, ValueError):
        pass

    linea = float(pick["Linea"])
    total = float(total_final)
    es_entera = abs(linea - round(linea)) < 1e-9
    if es_entera and abs(total - linea) < 1e-9:
        return "PUSH", ""

    gana_over = total > linea
    gana_under = total < linea
    if pick["Apuesta"] == "OVER":
        return ("GANADA", "") if gana_over else ("PERDIDA", "")
    if pick["Apuesta"] == "UNDER":
        return ("GANADA", "") if gana_under else ("PERDIDA", "")
    return "NO_VALIDA", "(apuesta desconocida)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fecha", default=None)
    args = ap.parse_args()
    n = verificar(args.fecha)
    print(f"[verificar] resueltos: {n}")


if __name__ == "__main__":
    main()
