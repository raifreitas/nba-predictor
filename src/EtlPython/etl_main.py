import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Modeling"))

import config
import db_utils
import nba_data_fetcher as fetcher
import odds_fetcher


def cargar_dias(inicio, fin):
    dias = fetcher.cargar_rango(inicio, fin)
    for fecha, total, nuevos in dias:
        print(f"[etl] {fecha}: {total} filas leidas, {nuevos} insertadas")
    db_utils.meta_set("ultimo_etl", config.ahora_utc())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", nargs=2, metavar=("TEMP_INICIO", "TEMP_FIN"))
    ap.add_argument("--solo-odds", action="store_true")
    ap.add_argument("fechas", nargs="*", help="FECHA_INI FECHA_FIN (YYYY-MM-DD)")
    args = ap.parse_args()

    if args.backfill:
        ini = f"{args.backfill[0][:4]}-08-01"
        resumen = fetcher.backfill_rango_temporadas(args.backfill[0], args.backfill[1])
        for temporada, total, nuevos in resumen:
            print(f"[backfill] {temporada}: {total} filas, {nuevos} nuevas")
        db_utils.meta_set("ultimo_etl", config.ahora_utc())
        return

    if len(args.fechas) != 2:
        ap.error("Indicar FECHA_INI FECHA_FIN o --backfill")

    inicio, fin = args.fechas[:2]

    if args.solo_odds:
        d = datetime.strptime(inicio, "%Y-%m-%d")
        fin_dt = datetime.strptime(fin, "%Y-%m-%d")
        while d <= fin_dt:
            f = d.strftime("%Y-%m-%d")
            cuotas = odds_fetcher.snapshot_odds(f)
            print(f"[odds] {f}: {len(cuotas)} eventos")
            d += timedelta(days=1)
        return

    hoy = config.hoy_str()
    cargar_dias(inicio, fin)
    if fin >= hoy:
        n, _ = fetcher.obtener_calendario(hoy)
        print(f"[calendario] {hoy}: {n} juegos programados")


if __name__ == "__main__":
    main()
