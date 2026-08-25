import json
import os
import sqlite3
from contextlib import contextmanager

import pandas as pd

import config

DDL = [
    """
    CREATE TABLE IF NOT EXISTS GameLog_NBA (
        Id INTEGER PRIMARY KEY AUTOINCREMENT,
        GameId TEXT NOT NULL,
        Fecha TEXT NOT NULL,
        Temporada TEXT NOT NULL,
        Equipo TEXT NOT NULL,
        Rival TEXT NOT NULL,
        EsLocal INTEGER NOT NULL,
        Puntos INTEGER NOT NULL,
        PuntosRival INTEGER NOT NULL,
        Total INTEGER NOT NULL,
        Min REAL, FGM INTEGER, FGA INTEGER, FG3M INTEGER, FG3A INTEGER,
        FTM INTEGER, FTA INTEGER, OREB INTEGER, TOV INTEGER,
        UNIQUE(GameId, Equipo)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_gl_fecha ON GameLog_NBA(Fecha)",
    "CREATE INDEX IF NOT EXISTS idx_gl_equipo ON GameLog_NBA(Equipo)",
    """
    CREATE TABLE IF NOT EXISTS Calendario (
        GameId TEXT PRIMARY KEY,
        Fecha TEXT NOT NULL,
        HoraUtc TEXT,
        Estado TEXT DEFAULT 'pendiente',
        EquipoVisita TEXT NOT NULL,
        EquipoLocal TEXT NOT NULL,
        TotalFinal INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cal_fecha ON Calendario(Fecha)",
    """
    CREATE TABLE IF NOT EXISTS LineaSnapshots (
        Id INTEGER PRIMARY KEY AUTOINCREMENT,
        Fecha TEXT NOT NULL,
        GameId TEXT,
        Equipos TEXT,
        SnapshotUtc TEXT NOT NULL,
        Casa TEXT NOT NULL,
        Linea REAL,
        OverCuota REAL,
        UnderCuota REAL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ls_fecha ON LineaSnapshots(Fecha)",
    """
    CREATE TABLE IF NOT EXISTS Predicciones (
        Id INTEGER PRIMARY KEY AUTOINCREMENT,
        Fecha TEXT NOT NULL,
        GameId TEXT,
        Partido TEXT NOT NULL,
        Apuesta TEXT NOT NULL,
        Linea REAL NOT NULL,
        Cuota REAL,
        Unidades REAL DEFAULT 0.5,
        Edge REAL,
        ProbOver REAL,
        Proyeccion REAL,
        Estado TEXT DEFAULT 'PENDIENTE',
        CreadoUtc TEXT NOT NULL,
        ResueltoUtc TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS Evaluaciones (
        Id INTEGER PRIMARY KEY AUTOINCREMENT,
        Fecha TEXT NOT NULL,
        GameId TEXT,
        Partido TEXT NOT NULL,
        Linea REAL,
        Prediccion TEXT,
        ProbOver REAL,
        Edge REAL,
        Proyeccion REAL,
        Motivo TEXT,
        CreadoUtc TEXT NOT NULL
    )
    """,
    "CREATE TABLE IF NOT EXISTS Notificaciones (IdPick INTEGER PRIMARY KEY)",
    "CREATE TABLE IF NOT EXISTS Meta (Clave TEXT PRIMARY KEY, Valor TEXT)",
]


def conexion():
    config.DIR_DATOS.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.RUTA_SQLITE)
    con.execute("PRAGMA journal_mode=WAL")
    return con


@contextmanager
def _con():
    con = conexion()
    try:
        yield con
        con.commit()
    finally:
        con.close()


def inicializar():
    with _con() as con:
        for ddl in DDL:
            con.execute(ddl)


def _normalizar_params(sql, params):
    if isinstance(params, dict) and "?" in sql:
        valores = list(params.values())
        partes = sql.split("?")
        nuevo = ""
        for i, v in enumerate(valores):
            nuevo += partes[i] + f":arg{i}"
        nuevo += partes[-1]
        return nuevo, {f"arg{i}": v for i, v in enumerate(valores)}
    return sql, params


def ejecutar(sql, params=None):
    sql, params = _normalizar_params(sql, params or ())
    with _con() as con:
        con.execute(sql, params)


def leer_sql(sql, params=None):
    sql, params = _normalizar_params(sql, params or ())
    with _con() as con:
        return pd.read_sql_query(sql, con, params=params)


def insertar_filas(tabla, filas, ignorar=True):
    if not filas:
        return 0
    columnas = list(filas[0].keys())
    prefijo = "INSERT OR IGNORE INTO" if ignorar else "INSERT INTO"
    placeholders = ",".join("?" * len(columnas))
    sql = f"{prefijo} {tabla} ({','.join(columnas)}) VALUES ({placeholders})"
    with _con() as con:
        cur = con.executemany(sql, [tuple(f[c] for c in columnas) for f in filas])
        return cur.rowcount


def meta_get(clave):
    df = leer_sql("SELECT Valor FROM Meta WHERE Clave=?", {"clave": clave})
    return df["Valor"].iloc[0] if not df.empty else None


def meta_set(clave, valor):
    ejecutar(
        "INSERT INTO Meta(Clave,Valor) VALUES(?,?) "
        "ON CONFLICT(Clave) DO UPDATE SET Valor=excluded.Valor",
        (clave, str(valor)),
    )


def cargar_horarios():
    if not config.RUTA_HORARIOS.exists():
        return {}
    with open(config.RUTA_HORARIOS, encoding="utf-8") as f:
        return json.load(f)


def guardar_horarios(estado):
    config.DIR_DATOS.mkdir(parents=True, exist_ok=True)
    tmp = config.RUTA_HORARIOS.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=1)
    os.replace(tmp, config.RUTA_HORARIOS)


inicializar()
