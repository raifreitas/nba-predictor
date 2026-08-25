import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "Modeling"))

import config
import db_utils


def _reintentar(fn, intentos=3, pausa=5):
    for i in range(intentos):
        try:
            return fn()
        except Exception:
            if i == intentos - 1:
                raise
            time.sleep(pausa * (i + 1))


def _df_gamelog(temporada):
    from nba_api.stats.endpoints import LeagueGameLog

    def _llamar():
        log = LeagueGameLog(
            season=temporada,
            season_type_all_star="Regular Season",
            player_or_team_abbreviation="T",
            timeout=90,
        )
        return log.get_data_frames()[0]

    return _reintentar(_llamar)


def _fila_desde_registro(r, temporada):
    matchup = str(r["MATCHUP"])
    es_local = 0 if "@" in matchup else 1
    rival = matchup.split()[-1]
    return {
        "GameId": str(r["GAME_ID"]),
        "Fecha": str(r["GAME_DATE"])[:10],
        "Temporada": temporada,
        "Equipo": r["TEAM_ABBREVIATION"],
        "Rival": rival,
        "EsLocal": es_local,
        "Puntos": int(r["PTS"]),
        "PuntosRival": None,
        "Total": None,
        "Min": float(r.get("MIN", 240) or 240),
        "FGM": int(r.get("FGM", 0) or 0),
        "FGA": int(r.get("FGA", 0) or 0),
        "FG3M": int(r.get("FG3M", 0) or 0),
        "FG3A": int(r.get("FG3A", 0) or 0),
        "FTM": int(r.get("FTM", 0) or 0),
        "FTA": int(r.get("FTA", 0) or 0),
        "OREB": int(r.get("OREB", 0) or 0),
        "TOV": int(r.get("TOV", 0) or 0),
    }


def _emparejar_totales(filas):
    por_juego = {}
    for f in filas:
        por_juego.setdefault(f["GameId"], []).append(f)
    for par in por_juego.values():
        if len(par) != 2:
            continue
        a, b = par
        a["PuntosRival"] = b["Puntos"]
        b["PuntosRival"] = a["Puntos"]
        total = a["Puntos"] + b["Puntos"]
        a["Total"] = total
        b["Total"] = total
    return [f for f in filas if f["Total"] is not None]


def _filas_de_gamelog(df, temporada):
    filas = [_fila_desde_registro(r, temporada) for _, r in df.iterrows()]
    return _emparejar_totales(filas)


def backfill_temporada(temporada):
    df = _df_gamelog(temporada)
    if df.empty:
        return 0, 0
    filas = _filas_de_gamelog(df, temporada)
    n = db_utils.insertar_filas("GameLog_NBA", filas)
    return len(filas), n


def cargar_resultados(fecha):
    temporada = config.temporada_actual(fecha)
    df = _df_gamelog(temporada)
    df["FECHA"] = df["GAME_DATE"].astype(str).str[:10]
    del_dia = df[df["FECHA"] == fecha]
    filas = _filas_de_gamelog(del_dia, temporada)
    n = db_utils.insertar_filas("GameLog_NBA", filas)
    _cerrar_calendario(fecha)
    return len(filas), n


def _cerrar_calendario(fecha):
    totales = db_utils.leer_sql(
        "SELECT GameId, SUM(Puntos) AS TotalFinal FROM GameLog_NBA WHERE Fecha=? GROUP BY GameId",
        {"fecha": fecha},
    )
    for _, r in totales.iterrows():
        db_utils.ejecutar(
            "UPDATE Calendario SET TotalFinal=?, Estado='finalizado' WHERE GameId=?",
            (int(r["TotalFinal"]), r["GameId"]),
        )


def obtener_calendario(fecha):
    from nba_api.live.nba.endpoints import scoreboard

    def _llamar():
        board = scoreboard.Scoreboard(timeout=60).get_dict()
        return board["scoreboard"]["games"]

    juegos = _reintentar(_llamar())
    filas = []
    for g in juegos:
        hora = (g.get("gameTimeUTC") or "")[:19]
        if not hora.startswith(fecha):
            continue
        filas.append(
            {
                "GameId": f"LIVE-{g['gameId']}",
                "Fecha": fecha,
                "HoraUtc": hora + "Z" if hora and not hora.endswith("Z") else hora,
                "EquipoVisita": g["awayTeam"]["teamAbbr"],
                "EquipoLocal": g["homeTeam"]["teamAbbr"],
            }
        )
    n = db_utils.insertar_filas("Calendario", filas)
    return len(filas), n


def cargar_rango(inicio, fin):
    d0 = datetime.strptime(inicio, "%Y-%m-%d")
    d1 = datetime.strptime(fin, "%Y-%m-%d")
    hoy = datetime.utcnow().strftime("%Y-%m-%d")
    cargados = []
    d = d0
    while d <= d1:
        f = d.strftime("%Y-%m-%d")
        if f < hoy:
            n_total, n_nuevos = cargar_resultados(f)
            cargados.append((f, n_total, n_nuevos))
        elif f == hoy:
            n_total, n_nuevos = obtener_calendario(f)
            cargados.append((f, n_total, n_nuevos))
        d += timedelta(days=1)
    return cargados


def backfill_rango_temporadas(inicio, fin):
    y0 = int(inicio[:4])
    y1 = int(fin[:4])
    resumen = []
    for y in range(y0, y1 + 1):
        temporada = f"{y}-{str(y + 1)[2:]}"
        total, nuevos = backfill_temporada(temporada)
        resumen.append((temporada, total, nuevos))
        time.sleep(2)
    return resumen


if __name__ == "__main__":
    print("Usar etl_main.py como punto de entrada")
