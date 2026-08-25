import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "EtlPython"))

import config
import db_utils
import predecir_hoy

_MAPA_NOMBRES = None


def mapa_nombre_equipo():
    global _MAPA_NOMBRES
    if _MAPA_NOMBRES is None:
        try:
            from nba_api.stats.static import teams

            _MAPA_NOMBRES = {
                t["abbreviation"]: t["full_name"] for t in teams.get_teams()
            }
        except Exception:
            _MAPA_NOMBRES = {}
    return _MAPA_NOMBRES


def pares_ya_ejecutados(fecha, ventana_activa=True):
    estado = db_utils.cargar_horarios()
    dia = estado.get(fecha, {})
    if not ventana_activa:
        return set()
    return {gid for gid, info in dia.items() if info.get("estado") == "ejecutado"}


def _cuotas_del_dia(fecha):
    df = db_utils.leer_sql(
        """
        SELECT GameId, Equipos, Linea, OverCuota, UnderCuota,
               COUNT(*) AS snaps
        FROM LineaSnapshots WHERE Fecha=?
        GROUP BY GameId, Equipos, Linea, OverCuota, UnderCuota
        """,
        {"fecha": fecha},
    )
    if df.empty:
        return {}
    mejor = df.sort_values("snaps").groupby("GameId", as_index=False).last()
    return {
        r["GameId"]: {
            "linea": float(r["Linea"]),
            "over": float(r["OverCuota"]),
            "under": float(r["UnderCuota"]),
            "equipos": str(r["Equipos"]),
        }
        for _, r in mejor.iterrows()
    }


def _match_odds(cuotas, equipo_local, equipo_visita):
    nombres = mapa_nombre_equipo()
    nl = nombres.get(equipo_local, equipo_local)
    nv = nombres.get(equipo_visita, equipo_visita)
    for gid, c in cuotas.items():
        if nl in c["equipos"] and nv in c["equipos"]:
            return gid, c
    return None, None


def procesar_fecha(fecha, ventana_min=30):
    juegos = db_utils.leer_sql(
        "SELECT * FROM Calendario WHERE Fecha=? AND Estado='pendiente' ORDER BY HoraUtc",
        {"fecha": fecha},
    )
    if juegos.empty:
        print(f"[recomendar] sin juegos pendientes {fecha}")
        return 0

    ahora = datetime.utcnow()
    ejecutados = pares_ya_ejecutados(fecha)
    estado_horarios = db_utils.cargar_horarios()
    dia_estado = estado_horarios.setdefault(fecha, {})
    cuotas = _cuotas_del_dia(fecha)
    creados = 0

    for _, juego in juegos.iterrows():
        gid = juego["GameId"]
        if gid in ejecutados:
            continue
        partido = f"{juego['EquipoVisita']} @ {juego['EquipoLocal']}"
        if not juego["HoraUtc"]:
            continue
        inicio = config.a_utc_naive(juego["HoraUtc"])
        delta = (inicio - ahora).total_seconds() / 60
        if delta > ventana_min or delta < -5:
            continue

        info_odds_gid, c = _match_odds(cuotas, juego["EquipoLocal"], juego["EquipoVisita"])
        linea = c["linea"] if c else None
        cuota = None
        proyeccion_res = predecir_hoy.proyectar_partido(
            fecha, juego["EquipoLocal"], juego["EquipoVisita"]
        )

        base_eval = {
            "Fecha": fecha,
            "GameId": gid,
            "Partido": partido,
            "Linea": linea,
            "Prediccion": None,
            "ProbOver": None,
            "Edge": None,
            "Proyeccion": proyeccion_res.get("proyeccion"),
            "Motivo": proyeccion_res.get("motivo", ""),
            "CreadoUtc": config.ahora_utc(),
        }

        if not proyeccion_res.get("ok"):
            base_eval["Motivo"] = proyeccion_res.get("motivo", "sin_datos_suficientes")
            db_utils.insertar_filas("Evaluaciones", [base_eval])
        elif linea is None:
            base_eval["Motivo"] = "sin_linea_de_mercado"
            db_utils.insertar_filas("Evaluaciones", [base_eval])
        else:
            prob_over = predecir_hoy.prob_para_linea(proyeccion_res["proyeccion"], linea)
            decision = predecir_hoy.decidir_jugada(
                proyeccion=proyeccion_res["proyeccion"],
                linea=linea,
                cuota=min(c["over"], c["under"]) if c else None,
                b2b_local=proyeccion_res.get("b2b_local", 0),
                b2b_visita=proyeccion_res.get("b2b_visita", 0),
                game_id_odds=info_odds_gid,
            )
            base_eval["ProbOver"] = prob_over
            base_eval["Edge"] = decision["edge"]
            base_eval["Prediccion"] = decision["direccion"]

            if not decision["apostar"]:
                base_eval["Motivo"] = decision["motivo"] or "sin_senal"
                db_utils.insertar_filas("Evaluaciones", [base_eval])
            else:
                direccion = decision["direccion"]
                if c:
                    cuota = c["over" if direccion == "OVER" else "under"]
                if predecir_hoy.modo_observacion():
                    base_eval["Motivo"] = "modo_observacion_" + decision["motivo"]
                    db_utils.insertar_filas("Evaluaciones", [base_eval])
                else:
                    pick = {
                        "Fecha": fecha,
                        "GameId": gid,
                        "Partido": partido,
                        "Apuesta": direccion,
                        "Linea": linea,
                        "Cuota": cuota or predecir_hoy.CUOTA_FALLBACK,
                        "Unidades": decision["unidades"],
                        "Edge": decision["edge"],
                        "ProbOver": prob_over,
                        "Proyeccion": proyeccion_res["proyeccion"],
                        "Estado": "PENDIENTE",
                        "CreadoUtc": config.ahora_utc(),
                    }
                    db_utils.insertar_filas("Predicciones", [pick], ignorar=False)
                print(
                    f"[pick] {partido} {direccion} {linea} "
                    f"(p={prob_over:.3f}, edge={decision['edge']}, u={decision['unidades']})"
                )

        dia_estado[gid] = {
            "estado": "ejecutado",
            "partido": partido,
            "hora_utc": juego["HoraUtc"],
            "procesado_utc": config.ahora_utc(),
        }
        db_utils.guardar_horarios(estado_horarios)
        ejecutados.add(gid)
        creados += 1

    print(f"[recomendar] {fecha}: {creados} juegos evaluados")
    return creados


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fecha", default=config.hoy_str())
    ap.add_argument("--ventana-min", type=int, default=30)
    args = ap.parse_args()
    procesar_fecha(args.fecha, args.ventana_min)


if __name__ == "__main__":
    main()
