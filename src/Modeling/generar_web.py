import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import db_utils


def _df_a_lista(df):
    return json.loads(df.to_json(orient="records", force_ascii=False))


def construir_contenido():
    hoy = config.hoy_str()

    picks = db_utils.leer_sql(
        "SELECT Fecha, Partido, Apuesta, Linea, Unidades, Edge, ProbOver, Cuota, Estado "
        "FROM Predicciones ORDER BY Fecha DESC, Id DESC"
    )
    evaluaciones = db_utils.leer_sql(
        "SELECT Fecha, Partido, Linea, Prediccion, ProbOver, Edge, Proyeccion, Motivo "
        "FROM Evaluaciones ORDER BY Fecha DESC, Id DESC"
    )

    resumen = {"ganadas": 0, "perdidas": 0, "push": 0, "pendientes": 0, "no_validas": 0}
    if not picks.empty:
        for estado, n in picks["Estado"].value_counts().items():
            clave = {
                "GANADA": "ganadas",
                "PERDIDA": "perdidas",
                "PUSH": "push",
                "PENDIENTE": "pendientes",
                "NO_VALIDA": "no_validas",
            }.get(estado)
            if clave:
                resumen[clave] = int(n)
    decididos = resumen["ganadas"] + resumen["perdidas"]
    resumen["efectividad"] = (
        round(100 * resumen["ganadas"] / decididos, 1) if decididos else None
    )

    hay_calendario = not db_utils.leer_sql(
        "SELECT 1 FROM Calendario WHERE Fecha=? LIMIT 1", {"fecha": hoy}
    ).empty
    partidos_hoy = []
    if hay_calendario:
        partidos_hoy = db_utils.leer_sql(
            """
            SELECT c.EquipoVisita, c.EquipoLocal, c.HoraUtc, c.Estado, c.TotalFinal,
                   (SELECT Linea FROM LineaSnapshots ls
                    WHERE ls.Fecha=c.Fecha AND ls.Equipos LIKE '%'||c.EquipoLocal||'%'
                    ORDER BY ls.Id DESC LIMIT 1) AS Linea
            FROM Calendario c
            WHERE c.Fecha=?
            ORDER BY c.HoraUtc
            """,
            {"fecha": hoy},
        )

    return {
        "resumen": resumen,
        "partidos_hoy": _df_a_lista(partidos_hoy) if len(partidos_hoy) else [],
        "predicciones": _df_a_lista(picks) if not picks.empty else [],
        "evaluaciones": _df_a_lista(evaluaciones) if not evaluaciones.empty else [],
    }


def generar():
    config.DIR_WEB.mkdir(parents=True, exist_ok=True)
    contenido = construir_contenido()
    ruta = config.DIR_WEB / "data.json"

    previo = {}
    if ruta.exists():
        try:
            previo = json.loads(ruta.read_text(encoding="utf-8"))
        except Exception:
            previo = {}

    if previo.get("resumen") == contenido["resumen"] \
       and previo.get("partidos_hoy") == contenido["partidos_hoy"] \
       and previo.get("predicciones") == contenido["predicciones"] \
       and previo.get("evaluaciones") == contenido["evaluaciones"]:
        print("[web] sin cambios (no se reescribe)")
        return 0

    contenido["actualizado"] = config.ahora_utc()
    ruta.write_text(
        json.dumps(contenido, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"[web] data.json regenerado ({contenido['actualizado']})")
    return 1


if __name__ == "__main__":
    sys.exit(generar())
