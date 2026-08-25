import numpy as np
import pandas as pd

import db_utils

VENTANA_MINIMA = 8


def _log_ordenado():
    df = db_utils.leer_sql("SELECT * FROM GameLog_NBA ORDER BY Equipo, Fecha, GameId")
    if df.empty:
        return df
    df["Fecha"] = df["Fecha"].astype(str).str[:10]
    return df.sort_values(["Equipo", "Temporada", "Fecha", "GameId"]).reset_index(drop=True)


def _rollings(g):
    g = g.copy()
    fga = g["FGA"].clip(lower=1)
    g["Poss"] = g["FGA"] - g["OREB"] + g["TOV"] + 0.44 * g["FTA"]
    g["Efg"] = (g["FGM"] + 0.5 * g["FG3M"]) / fga
    denom = 2 * (g["FGA"] + 0.44 * g["FTA"]).replace(0, np.nan)
    g["Ts"] = g["Puntos"] / denom
    g["Margen"] = g["Puntos"] - g["PuntosRival"]
    g["Gano"] = (g["Margen"] > 0).astype(int)

    def media(col, n):
        return g[col].shift(1).rolling(n, min_periods=n).mean()

    for n in (5, 10):
        g[f"Pts{n}"] = media("Puntos", n)
        g[f"Perm{n}"] = media("PuntosRival", n)
        g[f"Tot{n}"] = media("Total", n)
        g[f"Mrg{n}"] = media("Margen", n)
        g[f"Poss{n}"] = media("Poss", n)
        g[f"Efg{n}"] = media("Efg", n)
        g[f"Ts{n}"] = media("Ts", n)
    g["WinRate10"] = media("Gano", 10)
    prev = pd.to_datetime(g["Fecha"]).shift(1)
    g["Descanso"] = (pd.to_datetime(g["Fecha"]) - prev).dt.days.clip(upper=6)
    g["B2B"] = (g["Descanso"] == 0).astype(int)
    return g


COLUMNAS_POR_LADO = [
    "Pts10", "Perm10", "Tot10", "Tot5", "Mrg10", "Poss10", "Efg10", "Ts10",
    "WinRate10", "Descanso", "B2B",
]


def nombres_columnas():
    return ["Mes"] + [f"{c}_L" for c in COLUMNAS_POR_LADO] + [f"{c}_V" for c in COLUMNAS_POR_LADO]


def _long_features():
    df = _log_ordenado()
    if df.empty:
        return df
    partes = []
    for _, g in df.groupby(["Equipo", "Temporada"], sort=False):
        partes.append(_rollings(g))
    return pd.concat(partes, ignore_index=True).sort_values(["Fecha", "GameId"]).reset_index(drop=True)


def _indice_por_equipo():
    lf = _long_features()
    if lf.empty:
        return None
    indice = {}
    for equipo, g in lf.groupby("Equipo"):
        g = g.sort_values(["Temporada", "Fecha"]).reset_index(drop=True)
        indice[equipo] = (g["Fecha"].to_numpy(), g)
    return indice


def _lado(indice, equipo, fecha):
    par = indice.get(equipo)
    if par is None:
        return None
    fechas, g = par
    i = int(np.searchsorted(fechas, str(fecha), side="left"))
    if i < VENTANA_MINIMA:
        return None
    if i >= 1:
        temporada_actual = g.iloc[i - 1]["Temporada"]
        mismo_bloque = g.iloc[:i]
        corte = mismo_bloque[mismo_bloque["Temporada"] == temporada_actual]
        if len(corte) < VENTANA_MINIMA:
            return None
        r = corte.iloc[-1]
    else:
        return None
    vals = {c: (float(r[c]) if pd.notna(r[c]) else np.nan) for c in COLUMNAS_POR_LADO}
    return vals, r


def construir_dataset():
    indice = _indice_por_equipo()
    vacio_x = pd.DataFrame(columns=nombres_columnas())
    if not indice:
        return vacio_x, pd.Series(dtype=float), pd.DataFrame()

    juegos = db_utils.leer_sql(
        """
        SELECT l.GameId, l.Fecha, l.Temporada, l.Equipo AS EquipoL,
               v.Equipo AS EquipoV, l.Total
        FROM GameLog_NBA l
        JOIN GameLog_NBA v ON l.GameId = v.GameId AND l.EsLocal = 1 AND v.EsLocal = 0
        ORDER BY l.Fecha, l.GameId
        """
    )
    filas_X, ys, metas = [], [], []
    cache = {}
    for _, juego in juegos.iterrows():
        fecha = juego["Fecha"]
        sub_l = _lado(indice, juego["EquipoL"], fecha)
        sub_v = _lado(indice, juego["EquipoV"], fecha)
        if sub_l is None or sub_v is None:
            continue
        fl, rl = sub_l
        fv, rv = sub_v
        if any(np.isnan(v) for v in fl.values()) or any(np.isnan(v) for v in fv.values()):
            continue
        fila = {"Mes": int(fecha[5:7])}
        fila.update({f"{c}_L": fl[c] for c in COLUMNAS_POR_LADO})
        fila.update({f"{c}_V": fv[c] for c in COLUMNAS_POR_LADO})
        proxy = (fl["Pts10"] + fv["Perm10"]) / 2 + (fv["Pts10"] + fl["Perm10"]) / 2
        filas_X.append(fila)
        ys.append(1.0 if juego["Total"] > proxy else 0.0)
        metas.append(
            {
                "GameId": juego["GameId"],
                "Fecha": fecha,
                "Temporada": juego["Temporada"],
                "Total": juego["Total"],
                "ProxyLinea": round(float(proxy), 2),
                "Descanso_L": float(rl["Descanso"]),
                "Descanso_V": float(rv["Descanso"]),
                "B2B_L": int(rl["B2B"]),
                "B2B_V": int(rv["B2B"]),
            }
        )
    X = pd.DataFrame(filas_X, columns=nombres_columnas())
    y = pd.Series(ys, dtype=float)
    meta = pd.DataFrame(metas)
    return X, y, meta


def fila_para_partido(equipo_local, equipo_visita, fecha):
    indice = _indice_por_equipo()
    vacio = {"ok": False}
    if not indice:
        return vacio
    sub_l = _lado(indice, equipo_local, fecha)
    sub_v = _lado(indice, equipo_visita, fecha)
    if sub_l is None or sub_v is None:
        return vacio
    fl, rl = sub_l
    fv, rv = sub_v
    if any(np.isnan(v) for v in fl.values()) or any(np.isnan(v) for v in fv.values()):
        return vacio
    fila = {"Mes": int(str(fecha)[5:7])}
    fila.update({f"{c}_L": fl[c] for c in COLUMNAS_POR_LADO})
    fila.update({f"{c}_V": fv[c] for c in COLUMNAS_POR_LADO})
    proxy = (fl["Pts10"] + fv["Perm10"]) / 2 + (fv["Pts10"] + fl["Perm10"]) / 2
    return {
        "ok": True,
        "x": fila,
        "proxy_linea": round(float(proxy), 2),
        "descanso_local": float(rl["Descanso"]) if pd.notna(rl["Descanso"]) else 3.0,
        "descanso_visita": float(rv["Descanso"]) if pd.notna(rv["Descanso"]) else 3.0,
        "b2b_local": int(rl["B2B"]),
        "b2b_visita": int(rv["B2B"]),
    }
