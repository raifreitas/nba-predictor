import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import db_utils

VENTANA_MIN = 12


def _enviar(token, chat_id, texto):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": chat_id, "text": texto, "parse_mode": "HTML"},
        timeout=30,
    )
    return resp.status_code == 200


def notificar():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[telegram] sin secrets configurados (aviso, no rompe)")
        return 0

    limite = (
        datetime.utcnow() - timedelta(minutes=VENTANA_MIN)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    picks = db_utils.leer_sql(
        "SELECT Id, Fecha, Partido, Apuesta, Linea, Cuota, Unidades, Edge, ProbOver "
        "FROM Predicciones WHERE Estado='PENDIENTE' AND CreadoUtc>=? ORDER BY Id",
        {"limite": limite},
    )
    if picks.empty:
        return 0

    ya_enviados = set(
        db_utils.leer_sql("SELECT IdPick FROM Notificaciones")["IdPick"].astype(int)
    )
    enviados = 0
    for _, p in picks.iterrows():
        pid = int(p["Id"])
        if pid in ya_enviados:
            continue
        prob_txt = f"{100 * p['ProbOver']:.1f}%" if p["ProbOver"] is not None else "n/d"
        edge_txt = f"{p['Edge']:+.1f} pts" if p["Edge"] is not None else "n/d"
        texto = (
            f"<b>NUEVO PICK NBA</b>\n"
            f"{p['Partido']} ({p['Fecha']})\n"
            f"Apuesta: <b>{p['Apuesta']} {p['Linea']}</b>\n"
            f"Cuota: {p['Cuota']} | Unidades: {p['Unidades']}\n"
            f"P(Over): {prob_txt} | Edge: {edge_txt}"
        )
        if _enviar(token, chat_id, texto):
            db_utils.ejecutar(
                "INSERT OR IGNORE INTO Notificaciones(IdPick) VALUES(?)", (pid,)
            )
            enviados += 1
    print(f"[telegram] enviados: {enviados}")
    return enviados


if __name__ == "__main__":
    notificar()
