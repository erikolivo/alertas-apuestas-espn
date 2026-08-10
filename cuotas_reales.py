"""
cuotas_reales.py
------------------
SIN CAMBIOS DE LOGICA por la migracion a ESPN -- sigue siendo funcional
como fuente SECUNDARIA de cuota real (The Odds API), llamada desde
seleccionar_partidos.py DESPUES de intentar primero con las cuotas de
DraftKings embebidas en ESPN (gratis, sin cupo). Este modulo solo llena
huecos que ESPN no cubrio.

Filosofia (igual que siempre): nunca gastar una peticion si se puede
evitar. Solo se consultan ligas que tienen partidos hoy, se valida el
sport_key contra deportes activos antes de gastar una peticion real, y
hay tope diario + margen de seguridad en el cupo mensual.
"""

from fetch_data import obtener_deportes_odds_api, obtener_cuotas_liga, buscar_equipo_similar, ODDS_API_KEY
from mapeo_ligas_odds_api import sport_key_para
import cuota_odds_api

TOPE_LIGAS_POR_DIA = 20
UMBRAL_FAVORITO_CUOTA_REAL = 0.60


def _favorito_desde_evento(evento):
    bookmakers = evento.get("bookmakers", [])
    if not bookmakers:
        return None

    bk = bookmakers[0]
    mercado_h2h = next((m for m in bk.get("markets", []) if m.get("key") == "h2h"), None)
    if not mercado_h2h:
        return None

    precios = {o["name"]: o["price"] for o in mercado_h2h.get("outcomes", [])}
    home_team = evento.get("home_team")
    away_team = evento.get("away_team")
    cuota_home = precios.get(home_team)
    cuota_away = precios.get(away_team)
    if not cuota_home or not cuota_away:
        return None

    if cuota_home <= cuota_away:
        lado, cuota = "local", cuota_home
    else:
        lado, cuota = "visitante", cuota_away

    probabilidad = round(1 / cuota, 4)
    return lado, probabilidad, cuota, bk.get("title", "?")


def obtener_favoritos_cuota_real(fixtures_api):
    if not ODDS_API_KEY:
        return {}

    if not cuota_odds_api.hay_cupo_suficiente():
        print("[AVISO] Cupo de The Odds API insuficiente este mes, se omite la verificacion por cuota real.")
        return {}

    ligas_hoy = {}
    for f in fixtures_api:
        pais = f.get("league", {}).get("country", "")
        liga = f.get("league", {}).get("name", "")
        ligas_hoy.setdefault((pais, liga), []).append(f)

    deportes_activos = obtener_deportes_odds_api()

    resultado = {}
    ligas_consultadas = 0

    for (pais, liga), fixtures_de_la_liga in ligas_hoy.items():
        if ligas_consultadas >= TOPE_LIGAS_POR_DIA:
            print(f"[INFO] Tope diario de {TOPE_LIGAS_POR_DIA} ligas para The Odds API alcanzado.")
            break
        if not cuota_odds_api.hay_cupo_suficiente():
            print("[AVISO] Cupo de The Odds API se agoto durante esta corrida, se detiene aqui.")
            break

        sport_key = sport_key_para(pais, liga, deportes_activos)
        if not sport_key:
            continue

        try:
            eventos = obtener_cuotas_liga(sport_key)
        except Exception as e:
            print(f"[AVISO] No se pudo obtener cuotas de {pais} - {liga} ({sport_key}): {e}")
            continue
        ligas_consultadas += 1

        nombres_fixtures_liga = {}
        for f in fixtures_de_la_liga:
            nombres_fixtures_liga[f["teams"]["home"]["name"]] = f
            nombres_fixtures_liga[f["teams"]["away"]["name"]] = f

        for evento in eventos:
            favorito = _favorito_desde_evento(evento)
            if not favorito:
                continue
            lado, probabilidad, cuota, casa = favorito
            if probabilidad < UMBRAL_FAVORITO_CUOTA_REAL:
                continue

            home_odds = evento.get("home_team", "")
            away_odds = evento.get("away_team", "")
            match_home = buscar_equipo_similar(home_odds, list(nombres_fixtures_liga.keys()), n=1, corte=0.75)
            match_away = buscar_equipo_similar(away_odds, list(nombres_fixtures_liga.keys()), n=1, corte=0.75)
            fixture = None
            if match_home:
                fixture = nombres_fixtures_liga[match_home[0]]
            elif match_away:
                fixture = nombres_fixtures_liga[match_away[0]]
            if not fixture:
                continue

            resultado[fixture["fixture"]["id"]] = {
                "lado": lado, "probabilidad": probabilidad, "cuota": cuota, "casa_apuestas": casa,
            }

    print(f"Cuotas reales (The Odds API): {ligas_consultadas} liga(s) consultada(s), "
          f"{len(resultado)} favorito(s) claro(s) detectado(s).")
    return resultado
