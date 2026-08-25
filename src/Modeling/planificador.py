import argparse
import sys
from datetime import datetime
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "EtlPython"))

import config
import db_utils


def _delta_minutos(hora_utc):
    inicio = config.a_utc_naive(hora_utc)
    return (inicio - datetime.utcnow()).total_seconds() / 60


def procesar(fecha=None, ventana_min=30, horizonte_max_min=120):
    fecha = fecha or config.hoy_str()
    juegos = db_utils.leer_sql(
        "SELECT * FROM Calendario WHERE Fecha=? ORDER BY HoraUtc", {"fecha": fecha}
    )
    if juegos.empty:
        print(f"[planificador] sin juegos en calendario {fecha}")
        return

    estado = db_utils.cargar_horarios()
    dia = estado.setdefault(fecha, {})
    pendientes_para_recomendar = []

    for _, juego in juegos.iterrows():
        gid = juego["GameId"]
        info = dia.get(gid, {})
        if info.get("estado") == "ejecutado":
            continue
        if not juego["HoraUtc"]:
            continue
        delta = _delta_minutos(juego["HoraUtc"])
        if delta > horizonte_max_min:
            if info.get("estado") != "fuera_horizonte":
                dia[gid] = {
                    "estado": "fuera_horizonte",
                    "partido": f"{juego['EquipoVisita']} @ {juego['EquipoLocal']}",
                    "hora_utc": juego["HoraUtc"],
                }
            continue
        if delta < -180:
            dia[gid] = {**info, "estado": "iniciado", "partido": f"{juego['EquipoVisita']} @ {juego['EquipoLocal']}", "hora_utc": juego["HoraUtc"]}
            continue
        if info.get("estado") != "pendiente":
            dia[gid] = {
                "estado": "pendiente",
                "partido": f"{juego['EquipoVisita']} @ {juego['EquipoLocal']}",
                "hora_utc": juego["HoraUtc"],
            }
        pendientes_para_recomendar.append(gid)

    db_utils.guardar_horarios(estado)

    hay_pendientes = any(
        i.get("estado") == "pendiente"
        for g, i in dia.items()
        if juegos["GameId"].eq(g).any()
    )
    if hay_pendientes:
        import recomendar_apuestas

        recomendar_apuestas.procesar_fecha(fecha, ventana_min)

    resumen = {}
    for g, i in dia.items():
        resumen[i.get("estado", "?")] = resumen.get(i.get("estado", "?"), 0) + 1
    print(f"[planificador] {fecha}: {resumen}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--procesar", action="store_true")
    ap.add_argument("--fecha", default=config.hoy_str())
    ap.add_argument("--ventana-min", type=int, default=30)
    ap.add_argument("--horizonte-max-min", type=int, default=120)
    args = ap.parse_args()
    procesar(args.fecha, args.ventana_min, args.horizonte_max_min)


if __name__ == "__main__":
    main()
