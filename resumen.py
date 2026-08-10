"""
resumen.py
----------
FASE 2. SIN CAMBIOS FUNCIONALES por la migracion a ESPN -- lee
partidos_hoy.json, que sigue teniendo la misma forma. Reintenta cada 15
min entre las 07:00 y las 08:30.
"""

import json
import datetime
from pathlib import Path

from telegram_utils import enviar_mensaje_telegram, escapar_html
from estado_diario import ya_se_hizo, marcar_hecho

ARCHIVO = Path(__file__).parent / "data" / "partidos_hoy.json"
ZONA_HORARIA_LOCAL = datetime.timezone(datetime.timedelta(hours=-5))


def _hora_local(hora_inicio_utc_iso):
    if not hora_inicio_utc_iso:
        return "?"
    try:
        dt_utc = datetime.datetime.fromisoformat(hora_inicio_utc_iso.replace("Z", "+00:00"))
        dt_local = dt_utc.astimezone(ZONA_HORARIA_LOCAL)
        return dt_local.strftime("%H:%M")
    except Exception:
        return hora_inicio_utc_iso


def enviar_resumen():
    if ya_se_hizo("resumen"):
        print("El resumen de hoy ya se envio antes. Nada que hacer.")
        return

    if not ARCHIVO.exists():
        print("Fase 1 todavia no ha generado partidos_hoy.json. Se reintentara en el proximo ciclo.")
        return

    datos = json.loads(ARCHIVO.read_text(encoding="utf-8"))
    partidos = datos.get("partidos", [])

    if not partidos:
        exito = enviar_mensaje_telegram(
            "\U0001F4CB Hoy no hay partidos con favorito de probabilidad inicial >= 60%."
        )
        if exito:
            marcar_hecho("resumen")
        print("Resumen enviado: 0 partidos hoy." if exito else "Fallo el envio del resumen.")
        return

    lineas = [f"\U0001F4CB <b>{len(partidos)} partido(s) seleccionados hoy ({datos.get('fecha','')})</b> (horas en tu horario local)"]
    n_verificados = sum(1 for p in partidos if p.get("verificado_cuota_real"))
    if n_verificados:
        lineas.append(f"\U0001F4B0 {n_verificados} confirmado(s) por cuota real de casa de apuestas\n")

    for p in partidos:
        hora = _hora_local(p.get("hora_inicio"))
        estado = "\u2705" if p["fixture_id"] else "\u26A0\uFE0F sin vigilancia en vivo"
        etiqueta = ""
        if p.get("verificado_cuota_real"):
            etiqueta = " \U0001F4B0\u2705 <b>verificado</b>"
        elif p.get("favorito_solo_por_cuota_real"):
            etiqueta = " \U0001F4B0 <b>solo cuota real</b>"
        elif p.get("discrepancia_cuota_real"):
            etiqueta = f" \u26A0\uFE0F cuota real favorece a {escapar_html(p.get('lado_favorito_cuota_real',''))}"
        lineas.append(
            f"- {hora} -- {escapar_html(p['partido'])} - favorito: {escapar_html(p['favorito'])} "
            f"(cuota inicial {p['cuota_inicial']}) {estado}{etiqueta}"
        )

    exito = enviar_mensaje_telegram("\n".join(lineas))
    if exito:
        marcar_hecho("resumen")
    print(f"Resumen enviado con {len(partidos)} partido(s)." if exito else "Fallo el envio del resumen.")


if __name__ == "__main__":
    enviar_resumen()
