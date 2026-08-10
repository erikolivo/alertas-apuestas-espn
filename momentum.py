"""
momentum.py
-----------
ADAPTADO a los campos reales que expone ESPN, confirmados EN VIVO el
09-ago-2026 contra Queretaro @ Seattle Sounders (Leagues Cup, minuto
77'). Mismo espiritu que la version anterior (separar por completo la
expectativa pre-partido del momentum en vivo -- ver monitor.py), pero
con menos granularidad de entrada.

CAMBIO DE FONDO vs la version anterior (API-Football):
  - ANTES: tiros dentro del area / fuera del area, pesados distinto.
  - AHORA: ESPN solo da tiros totales (totalShots) y tiros a puerta
    (shotsOnTarget) -- no hay desglose por ubicacion en la cancha. Se
    colapsa a un solo peso para "tiro que no fue a puerta". Se pierde
    matiz de calidad de la ocasion, no la logica de fondo.

GANANCIA que no tenia la version anterior: tarjeta roja (redCards) y
penal (penaltyKickShots) vienen en el MISMO boxscore que ya se pide
para tiros/corners -- ya no hace falta una peticion aparte de eventos
(antes costaba 1 peticion extra por revision).

SUSTITUCIONES: pendiente. El boxscore por equipo de ESPN no trae un
contador de cambios recientes -- bonus_sustituciones() se deja definida
por compatibilidad pero devuelve siempre 0 hasta confirmar si el array
de "plays" del summary trae esa informacion de forma utilizable. No se
inventa un valor sin evidencia real.
"""

import math

# --- Pesos de presion (simplificados: ya no hay tiros dentro/fuera del area) ---
PESO_TIRO_PUERTA = 3
PESO_TIRO_NO_PUERTA = 0.8    # antes: PESO_TIRO_AREA (2) + PESO_TIRO_FUERA_AREA (0.5), promediado
PESO_CORNER = 1
PESO_TIRO_BLOQUEADO = 0.5     # NUEVO -- ESPN lo da (blockedShots), no existia antes

ALPHA_SUAVIZADO = 1.0

TASA_CONVERSION_TIRO_PUERTA = 0.11
TASA_CONVERSION_TIRO_NO_PUERTA = 0.02
TASA_CONVERSION_CORNER = 0.02

FACTOR_URGENCIA_TRAMO_FINAL = 1.2
MINUTO_INICIO_URGENCIA_1ER_TIEMPO = 30
MINUTO_FIN_URGENCIA_1ER_TIEMPO = 45
MINUTO_INICIO_URGENCIA_2DO_TIEMPO = 75

BONUS_POR_CAMBIO_RECIENTE = 0.5
TOPE_BONUS_CAMBIOS = 2.0

VENTANA_MINUTOS_DEFECTO = 15
ZONA_PARIDAD_BAJA = 0.35
ZONA_PARIDAD_ALTA = 0.65


def _minuto_a_entero(minuto):
    try:
        return int(str(minuto).rstrip("'").split("+")[0])
    except (TypeError, ValueError):
        return None


def _stat(stats_dict, nombre, default=0.0):
    """Lee un valor del boxscore de ESPN (dict ya aplanado por
    fetch_data.obtener_boxscore_en_vivo: {nombre_stat: displayValue})."""
    valor = stats_dict.get(nombre)
    if valor is None:
        return default
    try:
        return float(valor)
    except (TypeError, ValueError):
        return default


def _delta_stat(stats_actual, stats_anterior, nombre):
    actual = _stat(stats_actual, nombre)
    if stats_anterior is None:
        return max(0.0, actual)
    anterior = _stat(stats_anterior, nombre)
    return max(0.0, actual - anterior)


def _factor_urgencia(minuto_actual):
    minuto = _minuto_a_entero(minuto_actual)
    if minuto is None:
        return 1.0
    if MINUTO_INICIO_URGENCIA_1ER_TIEMPO <= minuto <= MINUTO_FIN_URGENCIA_1ER_TIEMPO:
        return FACTOR_URGENCIA_TRAMO_FINAL
    if minuto >= MINUTO_INICIO_URGENCIA_2DO_TIEMPO:
        return FACTOR_URGENCIA_TRAMO_FINAL
    return 1.0


def calcular_presion(snap_actual, snap_anterior, lado, xg_disponible=False):
    """
    Score de presion para un lado ('local' o 'visitante'), leyendo del
    boxscore real de ESPN guardado en snap_actual['stats_local'/
    'stats_visitante']. xg_disponible se deja por compatibilidad de
    firma -- ESPN no confirmo traer xG para futbol, siempre cae al
    proxy por tiros.
    """
    clave = "stats_local" if lado == "local" else "stats_visitante"
    stats_actual = snap_actual.get(clave, {})
    stats_anterior = snap_anterior.get(clave, {}) if snap_anterior else None

    total_tiros = _delta_stat(stats_actual, stats_anterior, "totalShots")
    tiros_puerta = _delta_stat(stats_actual, stats_anterior, "shotsOnTarget")
    tiros_no_puerta = max(0.0, total_tiros - tiros_puerta)
    tiros_bloqueados = _delta_stat(stats_actual, stats_anterior, "blockedShots")
    corners = _delta_stat(stats_actual, stats_anterior, "wonCorners")
    posesion = _stat(stats_actual, "possessionPct")

    score = (tiros_puerta * PESO_TIRO_PUERTA) + (tiros_no_puerta * PESO_TIRO_NO_PUERTA) + \
            (corners * PESO_CORNER) + (tiros_bloqueados * PESO_TIRO_BLOQUEADO)

    detalle = {
        "tiros_puerta": tiros_puerta, "tiros_no_puerta": tiros_no_puerta,
        "tiros_bloqueados": tiros_bloqueados, "corners": corners, "posesion": posesion,
    }
    return score, detalle


def bonus_sustituciones(n_cambios_recientes):
    """Pendiente de confirmar con evidencia real si ESPN expone
    sustituciones utilizables en vivo (ver docstring del modulo).
    Devuelve 0 hasta entonces -- no suma ni resta nada al momentum."""
    return 0.0


def momentum_relativo(presion_a, presion_b, alpha=ALPHA_SUAVIZADO):
    """Suavizado tipo Laplace -- evita que una muestra minuscula de una
    lectura de dominio total. Sin presion de ningun lado, da 0.5."""
    return (presion_a + alpha) / (presion_a + presion_b + 2 * alpha)


def probabilidad_gol_ventana(snap_actual, snap_anterior, lado, minuto_actual, xg_disponible=False):
    clave = "stats_local" if lado == "local" else "stats_visitante"
    stats_actual = snap_actual.get(clave, {})
    stats_anterior = snap_anterior.get(clave, {}) if snap_anterior else None

    total_tiros = _delta_stat(stats_actual, stats_anterior, "totalShots")
    tiros_puerta = _delta_stat(stats_actual, stats_anterior, "shotsOnTarget")
    tiros_no_puerta = max(0.0, total_tiros - tiros_puerta)
    corners = _delta_stat(stats_actual, stats_anterior, "wonCorners")

    lam = (tiros_puerta * TASA_CONVERSION_TIRO_PUERTA) + \
          (tiros_no_puerta * TASA_CONVERSION_TIRO_NO_PUERTA) + \
          (corners * TASA_CONVERSION_CORNER)

    lam *= _factor_urgencia(minuto_actual)
    return 1 - math.exp(-lam)


def zona_momentum(momentum_favorito):
    if momentum_favorito >= ZONA_PARIDAD_ALTA:
        return "favorito"
    if momentum_favorito <= ZONA_PARIDAD_BAJA:
        return "rival"
    return "paridad"


def hubo_tarjeta_roja(snap_actual, snap_anterior, lado):
    """NUEVO vs la version anterior: viene del MISMO boxscore de
    tiros/corners, sin peticion aparte de eventos."""
    clave = "stats_local" if lado == "local" else "stats_visitante"
    stats_anterior = snap_anterior.get(clave, {}) if snap_anterior else None
    return _delta_stat(snap_actual.get(clave, {}), stats_anterior, "redCards") > 0


def hubo_penal(snap_actual, snap_anterior, lado):
    """NUEVO vs la version anterior: idem, mismo boxscore."""
    clave = "stats_local" if lado == "local" else "stats_visitante"
    stats_anterior = snap_anterior.get(clave, {}) if snap_anterior else None
    return _delta_stat(snap_actual.get(clave, {}), stats_anterior, "penaltyKickShots") > 0
