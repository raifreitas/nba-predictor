import os
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scipy.stats import norm

import config
import features_nba
import db_utils

RUTA_MODELO = config.DIR_MODELOS / "modelo_nba_totales.pkl"
RUTA_CALIBRACION = config.DIR_MODELOS / "calibracion_totales.pkl"
RUTA_COLUMNAS = config.DIR_MODELOS / "columnas_totales.pkl"

PROB_OVER_MIN = 0.62
PROB_UNDER_MAX = 0.38
EDGE_MINIMO = 4.0
EDGE_ALTO = 6.0
PROB_EXTREMA_ALTA = 0.66
PROB_EXTREMA_BAJA = 0.34
SIGMA_TOTAL = 18.5
CUOTA_FALLBACK = 1.90
CUOTA_MINIMA = 1.80
LINEA_ENTERA_MARGEN = 1.0
LINEA_EXIGENTE_OVER = 238.0
LINEA_FRAGIL_UNDER = 211.0
MOVIMIENTO_CONTRA_MAX = 1.5
AJUSTE_B2B = -0.8
AJUSTE_DESCANSO_POR_DIA = 0.25
AJUSTE_TOPE = 1.5


def modo_observacion():
    return os.environ.get("NBA_MODO_OBSERVACION", "0") == "1"


def _cargar_artefactos():
    if not RUTA_MODELO.exists():
        return None, None, None
    return joblib.load(RUTA_MODELO), joblib.load(RUTA_CALIBRACION), joblib.load(RUTA_COLUMNAS)


def _ajuste_contexto(info):
    ajuste = 0.0
    dl = info.get("descanso_local")
    dv = info.get("descanso_visita")
    if dl is not None and dv is not None:
        ajuste += np.clip(dl - dv, -3, 3) * AJUSTE_DESCANSO_POR_DIA
    if info.get("b2b_local"):
        ajuste += AJUSTE_B2B
    if info.get("b2b_visita"):
        ajuste += AJUSTE_B2B
    return float(np.clip(ajuste, -AJUSTE_TOPE, AJUSTE_TOPE))


def proyectar_partido(fecha, equipo_local, equipo_visita):
    modelo, iso, columnas = _cargar_artefactos()
    info = features_nba.fila_para_partido(equipo_local, equipo_visita, fecha)
    if not info.get("ok"):
        return {"ok": False, "motivo": "sin_datos_suficientes"}
    if modelo is None:
        return {"ok": False, "motivo": "modelo_no_entrenado"}

    x = pd.DataFrame([info["x"]])[columnas]
    p_proxy = float(iso.predict(modelo.predict_proba(x)[:, 1])[0])
    p_proxy = float(np.clip(p_proxy, 0.05, 0.95))
    mu = info["proxy_linea"] + norm.ppf(p_proxy) * SIGMA_TOTAL
    ajuste = _ajuste_contexto(info)
    proyeccion = round(float(mu + ajuste), 2)

    return {
        "ok": True,
        "proyeccion": proyeccion,
        "proxy_linea": info["proxy_linea"],
        "p_modelo": round(float(p_proxy), 4),
        "ajuste": round(ajuste, 2),
        "descanso_local": info["descanso_local"],
        "descanso_visita": info["descanso_visita"],
        "b2b_local": info["b2b_local"],
        "b2b_visita": info["b2b_visita"],
    }


def prob_para_linea(proyeccion, linea):
    z = (linea - proyeccion) / SIGMA_TOTAL
    return round(float(norm.sf(z)), 4)


def _movimiento_linea(game_id_odds):
    df = db_utils.leer_sql(
        "SELECT Linea FROM LineaSnapshots WHERE GameId=? ORDER BY SnapshotUtc",
        {"game_id": game_id_odds},
    )
    if len(df) < 2:
        return None
    return round(float(df["Linea"].iloc[-1]) - float(df["Linea"].iloc[0]), 2)


def decidir_jugada(proyeccion, linea, cuota=None, b2b_local=0, b2b_visita=0, game_id_odds=None):
    edge = round(proyeccion - linea, 2)
    prob_over = prob_para_linea(proyeccion, linea)
    ctx = {
        "edge": edge,
        "prob_over": prob_over,
        "direccion": None,
        "apostar": False,
        "unidades": 0.0,
        "motivo": "",
    }

    es_entera = abs(linea - round(linea)) < 1e-9
    if es_entera and abs(edge) < LINEA_ENTERA_MARGEN:
        ctx["motivo"] = "riesgo_push"
        return ctx

    if cuota is not None and cuota < CUOTA_MINIMA:
        ctx["motivo"] = "cuota_baja"
        return ctx

    direccion = None
    if prob_over >= PROB_OVER_MIN and edge >= EDGE_MINIMO:
        direccion = "OVER"
    elif prob_over <= PROB_UNDER_MAX and -edge >= EDGE_MINIMO:
        direccion = "UNDER"

    if direccion is None:
        ctx["motivo"] = "sin_senal_direccional"
        return ctx

    ctx["direccion"] = direccion
    mov = _movimiento_linea(game_id_odds) if game_id_odds else None
    if mov is not None:
        contra = (direccion == "OVER" and mov <= -MOVIMIENTO_CONTRA_MAX) or (
            direccion == "UNDER" and mov >= MOVIMIENTO_CONTRA_MAX
        )
        if contra:
            ctx["motivo"] = f"mercado_en_contra_mov_{mov}"
            return ctx

    if direccion == "OVER" and linea >= LINEA_EXIGENTE_OVER and edge < EDGE_ALTO:
        ctx["motivo"] = "over_en_linea_exigente"
        return ctx
    if direccion == "UNDER" and linea <= LINEA_FRAGIL_UNDER:
        ctx["motivo"] = "under_en_linea_fragil"
        return ctx

    b2b = b2b_local or b2b_visita
    if b2b and edge < EDGE_ALTO:
        ctx["motivo"] = "b2b_exige_edge_alto"
        return ctx

    extremo = prob_over >= PROB_EXTREMA_ALTA or prob_over <= PROB_EXTREMA_BAJA
    ctx["unidades"] = 1.0 if (edge >= EDGE_ALTO and extremo and not b2b) else 0.5
    ctx["apostar"] = True
    ctx["motivo"] = "senal_confirmada"
    return ctx
