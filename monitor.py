"""
monitor.py
------------
FASE 3 -- RECONSTRUIDO durante la migracion a ESPN.

AVISO IMPORTANTE, leelo antes de desplegar: el monitor.py ORIGINAL
nunca llego a esta conversacion (se subio el archivo pero su contenido
no paso al chat -- limitacion de la plataforma, no un olvido). Todo lo
que sigue se reconstruyo a partir de:
  (a) la tabla de tipos de alerta descrita en README.md,
  (b) las funciones ya confirmadas de momentum.py,
  (c) el formato de datos de partidos_hoy.json que ya usan
      seleccionar_partidos.py, resumen.py y cerrar_resultados.py.

Los UMBRALES exactos (ej. "momentum >= 65% para alertar") son valores
de partida razonables, NO los que ya tenias calibrados con evidencia
real en el Excel. Si todavia tienes acceso al monitor.py original
(tu computadora, o el historial de git del repo viejo), compara la
logica exacta de cuando NO repetir una alerta ya enviada -- aqui se
simplifico a "no repetir el mismo tipo dentro de una ventana de N
minutos", que puede no ser exactamente lo que ya tenias afinado.

Qué SI cambio de forma segura (evidencia real de esta migracion):
  - Tarjeta roja y penal se detectan del MISMO boxscore de tiros/
    corners (momentum.hubo_tarjeta_roja / hubo_penal) -- ya no hace
    falta la peticion aparte de eventos que mencionaba el README viejo.
"""

import json
import datetime
from pathlib import Path

from fetch_data import obtener_boxscore_en_vivo
from telegram_utils import enviar_mensaje_telegram, escapar_html
import momentum

DATA_DIR = Path(__file__).parent / "data"
ARCHIVO_PARTIDOS = DATA_DIR / "partidos_hoy.json"

DIFERENCIA_TECHO = 3
MINUTO_INICIO_CIERRE = 75
DOMINANCIA_CIERRE = 0.75


def _cargar():
    if not ARCHIVO_PARTIDOS.exists():
        return None
    return json.loads(ARCHIVO_PARTIDOS.read_text(encoding="utf-8"))


def _guardar(datos):
    ARCHIVO_PARTIDOS.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")


def _en_ventana_horaria(partido):
    """Chequeo local, gratis: da margen razonable antes/despues del
    kickoff -- misma filosofia de siempre, nunca gastar una peticion
    si se puede evitar en frio."""
    try:
        inicio = datetime.datetime.fromisoformat(partido["kickoff_utc"].replace("Z", "+00:00"))
    except Exception:
        return True
    ahora = datetime.datetime.now(datetime.timezone.utc)
    minutos_desde_inicio = (ahora - inicio).total_seconds() / 60
    return -10 <= minutos_desde_inicio <= 130


def _registrar_alerta(partido, tipo, texto, minuto):
    partido.setdefault("alertas_enviadas", []).append({"tipo": tipo, "minuto": minuto, "texto": texto})


def _ya_se_envio_reciente(partido, tipo, minuto_actual, ventana=10):
    minuto_actual_int = momentum._minuto_a_entero(minuto_actual)
    for a in reversed(partido.get("alertas_enviadas", [])):
        if a["tipo"] != tipo:
            continue
        minuto_previo_int = momentum._minuto_a_entero(a["minuto"])
        if minuto_actual_int is None or minuto_previo_int is None:
            return True
        return abs(minuto_actual_int - minuto_previo_int) <= ventana
    return False


def _evaluar_alertas(partido, snap_actual, snap_anterior, minuto):
    favorito_es_local = partido["favorito_es_local"]
    gl, gv = snap_actual["goles_local"], snap_actual["goles_visitante"]
    goles_favorito = gl if favorito_es_local else gv
    goles_rival = gv if favorito_es_local else gl
    diferencia = goles_favorito - goles_rival

    lado_favorito = "local" if favorito_es_local else "visitante"
    lado_rival = "visitante" if favorito_es_local else "local"

    if abs(diferencia) >= DIFERENCIA_TECHO:
        if partido.get("diferencia_maxima_alcanzada", 0) < DIFERENCIA_TECHO:
            partido["diferencia_maxima_alcanzada"] = abs(diferencia)
            return [("partido_resuelto", "\U0001F3C1 Seguimiento cerrado -- diferencia de 3+ goles.")]
        return []

    if momentum.hubo_tarjeta_roja(snap_actual, snap_anterior, lado_rival) or \
       momentum.hubo_tarjeta_roja(snap_actual, snap_anterior, lado_favorito):
        if not _ya_se_envio_reciente(partido, "tarjeta_roja", minuto, ventana=999):
            return [("tarjeta_roja", "\U0001F7E5 Tarjeta roja detectada.")]

    if momentum.hubo_penal(snap_actual, snap_anterior, lado_favorito) or \
       momentum.hubo_penal(snap_actual, snap_anterior, lado_rival):
        if not _ya_se_envio_reciente(partido, "penal", minuto, ventana=15):
            return [("penal", "\U0001F3AF Penal detectado.")]

    presion_fav, _ = momentum.calcular_presion(snap_actual, snap_anterior, lado_favorito)
    presion_riv, _ = momentum.calcular_presion(snap_actual, snap_anterior, lado_rival)
    mom_favorito = momentum.momentum_relativo(presion_fav, presion_riv)
    zona = momentum.zona_momentum(mom_favorito)
    minuto_int = momentum._minuto_a_entero(minuto) or 45

    alertas = []

    if zona == "paridad" and (presion_fav + presion_riv) > 0:
        if not _ya_se_envio_reciente(partido, "partido_abierto", minuto_int):
            alertas.append(("partido_abierto", "\u26A1 Partido abierto -- momentum parejo, peligro real de ambos lados."))
        return alertas

    if zona == "favorito":
        tipo, texto = None, None
        if diferencia < 0:
            tipo, texto = "posible_empate", "\U0001F7E0 Posible empate -- momentum a favor del favorito."
        elif diferencia == 0 and minuto_int < 30:
            tipo, texto = "gana_favorito_1er_tiempo", "\u23F1\uFE0F El favorito domina antes del minuto 30."
        elif diferencia == 0:
            tipo, texto = "posible_victoria_favorito", "\U0001F7E2 Posible victoria del favorito -- domina el momentum."
        elif diferencia > 0:
            tipo, texto = "ampliacion_marcador", "\U0001F535 Posible ampliacion de marcador."

        if diferencia <= 0 and minuto_int >= MINUTO_INICIO_CIERRE and mom_favorito >= DOMINANCIA_CIERRE:
            tipo, texto = "gol_de_cierre", "\u23F0 Posible gol de cierre -- dominancia acumulada alta en el tramo final."

        if tipo and not _ya_se_envio_reciente(partido, tipo, minuto_int):
            alertas.append((tipo, texto))

    elif zona == "rival":
        if diferencia <= 0:
            tipo, texto = "cuidado_rival_presiona", "\u26A0\uFE0F Cuidado -- el rival esta presionando."
        else:
            tipo, texto = "posible_gol_no_favorito", "\U0001F534 Posible gol del no favorito."
        if not _ya_se_envio_reciente(partido, tipo, minuto_int):
            alertas.append((tipo, texto))

    return alertas


def _mensaje_partido(partido, minuto, snap_actual, texto):
    stats_fav = snap_actual["stats_local"] if partido["favorito_es_local"] else snap_actual["stats_visitante"]
    stats_riv = snap_actual["stats_visitante"] if partido["favorito_es_local"] else snap_actual["stats_local"]
    lineas = [
        texto,
        f"<b>{escapar_html(partido['partido'])}</b> -- min {minuto}",
        f"Marcador: {snap_actual['goles_local']}-{snap_actual['goles_visitante']}",
        f"Favorito: {escapar_html(partido['favorito'])} (cuota inicial {partido['cuota_inicial']})",
        f"Tiros a puerta: {stats_fav.get('shotsOnTarget','?')} vs {stats_riv.get('shotsOnTarget','?')}",
        f"Posesion: {stats_fav.get('possessionPct','?')}% vs {stats_riv.get('possessionPct','?')}%",
    ]
    return "\n".join(lineas)


def vigilar():
    datos = _cargar()
    if not datos:
        print("No hay partidos_hoy.json todavia. Se reintentara en el proximo ciclo.")
        return

    hubo_cambios = False
    for partido in datos["partidos"]:
        if partido.get("acierto") is not None or not partido.get("fixture_id"):
            continue
        if not _en_ventana_horaria(partido):
            continue

        liga_slug = partido.get("liga_slug")
        if not liga_slug:
            print(f"[AVISO] {partido['partido']} no tiene liga_slug guardado, no se puede vigilar.")
            continue

        box = obtener_boxscore_en_vivo(liga_slug, partido["fixture_id"])
        if box is None or box.get("estado") != "in":
            continue

        snap_actual = {
            "minuto": box["minuto"], "goles_local": box["goles_local"],
            "goles_visitante": box["goles_visitante"],
            "stats_local": box["stats_local"], "stats_visitante": box["stats_visitante"],
        }
        historial = partido.setdefault("historial_snapshots", [])
        snap_anterior = historial[-1] if historial else None
        historial.append(snap_actual)
        hubo_cambios = True

        alertas = _evaluar_alertas(partido, snap_actual, snap_anterior, box["minuto"])
        for tipo, texto in alertas:
            mensaje = _mensaje_partido(partido, box["minuto"], snap_actual, texto)
            if enviar_mensaje_telegram(mensaje):
                _registrar_alerta(partido, tipo, texto, box["minuto"])

    if hubo_cambios:
        _guardar(datos)


if __name__ == "__main__":
    vigilar()
