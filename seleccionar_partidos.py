"""
seleccionar_partidos.py
------------------------
FASE 1, version 8 -- migrada a ESPN.

CAMBIOS de esta version respecto a la anterior (API-Football):

1. FIXTURES: obtener_fixtures_por_fecha() ahora viene de ESPN
   (fetch_data.py), recorriendo LIGAS_ESPN en vez de 1 sola llamada
   global -- ver el porque en fetch_data.py.

2. CUOTAS REALES: ESPN trae cuotas de DraftKings EMBEBIDAS en el mismo
   scoreboard que ya se pide para fixtures -- se usan como fuente
   PRIMARIA de cuota real (gratis, sin peticion adicional). The Odds
   API (cuotas_reales.py) se mantiene como respaldo SECUNDARIO, solo
   para llenar huecos en ligas que DraftKings/ESPN no cotizan.

3. LIGA_SLUG: cada partido seleccionado ahora guarda su "liga_slug" de
   ESPN -- lo necesitan monitor.py (Fase 3) y cerrar_resultados.py
   (Fase 4) para poder pedir el boxscore/resultado de ESE partido en
   ESPN (a diferencia de API-Football, ESPN exige el slug de liga en
   la URL, no alcanza con el fixture_id solo).

4. MIGRACION DE HISTORIAL: se llama a
   ratings_store.migrar_api_football_a_espn() ademas de la migracion de
   bootstrap ya existente, para no perder el rating Glicko-2 acumulado
   de equipos que el sistema ya vigilaba antes de la migracion.

5. YA NO HAY TOPE DE CUPO DIARIO real contra el cual presupuestar la
   resolucion de pais (ver team_resolver.py) -- se mantiene un tope por
   corrida como red de seguridad generica, no como proteccion de cupo.

Todo lo demas (orden de resolucion de pais: liga domestica -> Goal
Index -> API; el filtro de probabilidad >= 60%; verificado/discrepancia
por cuota real) sigue exactamente igual.
"""

import json
import datetime
from pathlib import Path

from fetch_data import (
    obtener_ranking_clubelo, obtener_fixtures_por_fecha, buscar_equipo_similar,
    obtener_info_equipo, extraer_favorito_odds_espn,
)
from goal_index import construir_goal_index_global
from poisson_model import evaluar_favorito, cumple_filtro_cuota
import ratings_store
import team_resolver
import elo_desde_goal_index
import cuotas_reales

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
ARCHIVO_SALIDA = DATA_DIR / "partidos_hoy.json"

ZONA_HORARIA_LOCAL = datetime.timezone(datetime.timedelta(hours=-5))

PAIS_A_CODIGO_CLUBELO = team_resolver.PAIS_A_CODIGO_CLUBELO

# Tope de resoluciones de pais POR API para esta corrida. Ya no protege
# un cupo diario real (ESPN no publica uno) -- es una red de seguridad
# generica contra bugs (ej. un loop que reintente sin parar).
TOPE_RESOLUCIONES_PAIS_FASE1 = 100

UMBRAL_FAVORITO_CUOTA_REAL_ESPN = 0.65  # mismo umbral que el modelo propio (PROB_MINIMA_FAVORITO), para comparar manzanas con manzanas


def fecha_local_hoy():
    return datetime.datetime.now(ZONA_HORARIA_LOCAL).date().isoformat()


def ya_se_completo_hoy():
    if not ARCHIVO_SALIDA.exists():
        return False
    try:
        datos = json.loads(ARCHIVO_SALIDA.read_text(encoding="utf-8"))
        return datos.get("fecha") == fecha_local_hoy()
    except Exception:
        return False


def _resolver_pais(team_id, nombre, pais_liga, equipo_pais_goal_index):
    """Orden: liga domestica (gratis) -> Goal Index (gratis) -> ESPN
    (best-effort, pais del estadio)."""
    if pais_liga in PAIS_A_CODIGO_CLUBELO:
        return pais_liga, True, "liga_domestica"

    match_gi = buscar_equipo_similar(nombre, list(equipo_pais_goal_index.keys()), n=1, corte=0.75)
    if match_gi:
        pais_gi = equipo_pais_goal_index[match_gi[0]]
        if pais_gi:
            return pais_gi, True, "goal_index_gratis"

    pais_api = team_resolver.resolver_pais_equipo(team_id, nombre, obtener_info_equipo)
    if pais_api:
        return pais_api, True, "espn_estadio"

    return None, False, "sin_resolver"


def _construir_favoritos_cuota_real(fixtures_api):
    """
    Fuente PRIMARIA: cuotas de DraftKings embebidas por ESPN (gratis,
    ya vinieron en la misma peticion de fixtures). Fuente SECUNDARIA
    (respaldo, solo llena huecos): The Odds API via cuotas_reales.py,
    para ligas que DraftKings no cotiza.

    Cada entrada ahora guarda la cuota/probabilidad de AMBOS lados
    (lado_favorito/probabilidad_favorito para el filtro, cuota_local/
    cuota_visitante para poder mostrar los dos en el resumen).
    """
    favoritos = {}
    for f in fixtures_api:
        fav = extraer_favorito_odds_espn(f)
        if fav and fav["probabilidad_favorito"] >= UMBRAL_FAVORITO_CUOTA_REAL_ESPN:
            favoritos[f["fixture"]["id"]] = fav
    print(f"Cuotas embebidas de ESPN (DraftKings): {len(favoritos)} favorito(s) claro(s) detectado(s).")

    try:
        favoritos_odds_api = cuotas_reales.obtener_favoritos_cuota_real(fixtures_api)
        agregados = 0
        for fid, datos_odds in favoritos_odds_api.items():
            if fid not in favoritos:
                favoritos[fid] = datos_odds
                agregados += 1
        if agregados:
            print(f"The Odds API (respaldo): {agregados} favorito(s) adicional(es) detectado(s).")
    except Exception as e:
        print(f"[AVISO] The Odds API (respaldo) no disponible: {e}")

    return favoritos


def seleccionar():
    if ya_se_completo_hoy():
        print("La seleccion de hoy ya se genero antes. Nada que hacer.")
        return

    hoy = fecha_local_hoy()
    print(f"Buscando partidos de hoy ({hoy})...")

    team_resolver.resetear_contador_corrida(limite=TOPE_RESOLUCIONES_PAIS_FASE1)

    fixtures_api = obtener_fixtures_por_fecha(hoy)
    print(f"Partidos de hoy en ESPN (todas las ligas de LIGAS_ESPN): {len(fixtures_api)}")

    favoritos_cuota_real = _construir_favoritos_cuota_real(fixtures_api)

    ranking = obtener_ranking_clubelo(hoy)
    if not ranking:
        ayer = (datetime.datetime.now(ZONA_HORARIA_LOCAL).date() - datetime.timedelta(days=1)).isoformat()
        print(f"[AVISO] Ranking de hoy vacio, probando con el de ayer ({ayer})...")
        ranking = obtener_ranking_clubelo(ayer)

    elo_por_pais = {}
    elo_global_ultimo = {}
    for fila in ranking:
        try:
            club = fila["Club"]
            elo = float(fila["Elo"])
            pais_club = fila.get("Country", "")
            elo_por_pais.setdefault(pais_club, {})[club] = elo
            elo_global_ultimo[club] = elo
        except (KeyError, ValueError):
            continue

    print(f"Equipos con Elo disponible en ClubElo: {len(elo_global_ultimo)}")

    print("Construyendo Goal Index (football-data.co.uk, forma reciente + temporada)...")
    goal_index, equipo_pais_goal_index = construir_goal_index_global()
    print(f"Equipos con Goal Index disponible: {len(goal_index)} "
          f"(con pais inferido gratis: {len(equipo_pais_goal_index)})")

    pendiente, intercepto, n_muestra_calibracion = elo_desde_goal_index.calibrar(elo_global_ultimo, goal_index)
    if pendiente is not None:
        print(f"Calibracion Goal Index -> Elo lista (muestra: {n_muestra_calibracion} equipos, "
              f"pendiente={pendiente:.2f}, intercepto={intercepto:.1f})")
    else:
        print(f"[AVISO] Muestra insuficiente para calibrar Goal Index -> Elo "
              f"({n_muestra_calibracion} equipos, se necesitan >= {elo_desde_goal_index.MUESTRA_MINIMA}).")

    seleccionados = []
    sin_elo_ni_rating_propio = 0
    sin_pais_verificado = 0
    elo_estimados_van = 0

    for f in fixtures_api:
        home_id = f["teams"]["home"]["id"]
        away_id = f["teams"]["away"]["id"]
        home = f["teams"]["home"]["name"]
        away = f["teams"]["away"]["name"]
        pais_liga = f.get("league", {}).get("country", "")
        liga_nombre = f.get("league", {}).get("name", "")
        liga_slug = f.get("_liga_slug")

        pais_home, home_ok, metodo_pais_home = _resolver_pais(home_id, home, pais_liga, equipo_pais_goal_index)
        pais_away, away_ok, metodo_pais_away = _resolver_pais(away_id, away, pais_liga, equipo_pais_goal_index)

        elo_home, home_verificado, metodo_home = team_resolver.elegir_candidato_verificado(
            home, pais_home, elo_por_pais, elo_global_ultimo, buscar_equipo_similar,
            pais_rival=pais_away if not home_ok else None,
        )
        elo_away, away_verificado, metodo_away = team_resolver.elegir_candidato_verificado(
            away, pais_away, elo_por_pais, elo_global_ultimo, buscar_equipo_similar,
            pais_rival=pais_home if not away_ok else None,
        )

        pais_verificado = home_ok and away_ok
        if not pais_verificado:
            sin_pais_verificado += 1

        gi_home_dict = gi_away_dict = None
        gi_home_val = gi_away_val = None
        cand_gi_home = buscar_equipo_similar(home, list(goal_index.keys()), n=1, corte=0.6)
        cand_gi_away = buscar_equipo_similar(away, list(goal_index.keys()), n=1, corte=0.6)
        if cand_gi_home:
            gi_home_dict = goal_index[cand_gi_home[0]]
            gi_home_val = gi_home_dict["goal_index"]
        if cand_gi_away:
            gi_away_dict = goal_index[cand_gi_away[0]]
            gi_away_val = gi_away_dict["goal_index"]

        elo_home_estimado = elo_away_estimado = False
        if elo_home is None and gi_home_val is not None:
            elo_home = elo_desde_goal_index.estimar_elo(gi_home_val, pendiente, intercepto)
            elo_home_estimado = elo_home is not None
        if elo_away is None and gi_away_val is not None:
            elo_away = elo_desde_goal_index.estimar_elo(gi_away_val, pendiente, intercepto)
            elo_away_estimado = elo_away is not None
        if elo_home_estimado or elo_away_estimado:
            elo_estimados_van += 1

        llave_home = ratings_store.llave_equipo(home_id, pais_home, home)
        llave_away = ratings_store.llave_equipo(away_id, pais_away, away)
        ratings_store.migrar_bootstrap_a_id(home, home_id, liga=liga_nombre)
        ratings_store.migrar_bootstrap_a_id(away, away_id, liga=liga_nombre)
        ratings_store.migrar_api_football_a_espn(home, home_id, liga=liga_nombre)
        ratings_store.migrar_api_football_a_espn(away, away_id, liga=liga_nombre)

        rating_home, n_home, rd_home = ratings_store.rating_combinado(
            llave_home, elo_home, nombre=home, pais=pais_home, liga=liga_nombre)
        rating_away, n_away, rd_away = ratings_store.rating_combinado(
            llave_away, elo_away, nombre=away, pais=pais_away, liga=liga_nombre)

        if elo_home is None and elo_away is None and n_home == 0 and n_away == 0:
            sin_elo_ni_rating_propio += 1
            continue

        evaluacion = evaluar_favorito(rating_home, rd_home, rating_away, rd_away, gi_home_dict, gi_away_dict)

        datos_cuota_real = favoritos_cuota_real.get(f["fixture"]["id"])
        es_favorito_por_cuota_real = datos_cuota_real is not None

        if not cumple_filtro_cuota(evaluacion) and not es_favorito_por_cuota_real:
            continue

        favorito_nombre = home if evaluacion["lado"] == "local" else away
        no_favorito_nombre = away if evaluacion["lado"] == "local" else home

        # Cuota del modelo propio para AMBOS lados (a pedido explicito,
        # antes solo se guardaba la del favorito).
        cuota_no_favorito_modelo = evaluacion["cuota_visitante"] if evaluacion["lado"] == "local" else evaluacion["cuota_local"]

        verificado_cuota_real = False
        discrepancia_cuota_real = False
        cuota_real_info = {}
        if es_favorito_por_cuota_real:
            lado_odds = datos_cuota_real["lado_favorito"]
            if lado_odds == evaluacion["lado"]:
                verificado_cuota_real = True
            else:
                discrepancia_cuota_real = True
            # Cuota real alineada al favorito SEGUN EL MODELO PROPIO
            # (no segun quien la fuente de cuota real elija) -- asi la
            # comparacion en resumen.py es directa incluso cuando hay
            # discrepancia entre las dos fuentes.
            cuota_real_favorito = datos_cuota_real["cuota_local"] if evaluacion["lado"] == "local" else datos_cuota_real["cuota_visitante"]
            cuota_real_no_favorito = datos_cuota_real["cuota_visitante"] if evaluacion["lado"] == "local" else datos_cuota_real["cuota_local"]
            cuota_real_info = {
                "cuota_real": cuota_real_favorito,
                "cuota_real_no_favorito": cuota_real_no_favorito,
                "probabilidad_cuota_real": round(datos_cuota_real["probabilidad_favorito"] * 100, 1),
                "casa_apuestas": datos_cuota_real["casa_apuestas"],
                "lado_favorito_cuota_real": lado_odds,
            }

        seleccionados.append({
            "partido": f"{home} vs {away}",
            "local": home,
            "visitante": away,
            "favorito": favorito_nombre,
            "no_favorito": no_favorito_nombre,
            "favorito_es_local": evaluacion["lado"] == "local",
            "cuota_inicial": evaluacion["cuota_inicial"],
            "cuota_no_favorito": cuota_no_favorito_modelo,
            "probabilidad_inicial": round(evaluacion["probabilidad"] * 100, 1),
            "lambda_local": evaluacion["lambda_local"],
            "lambda_visitante": evaluacion["lambda_visitante"],
            "goal_index_disponible": gi_home_val is not None and gi_away_val is not None,
            "elo_local_estimado_goal_index": elo_home_estimado,
            "elo_visitante_estimado_goal_index": elo_away_estimado,
            "pais_verificado": pais_verificado,
            "metodo_pais": f"local:{metodo_pais_home}/visitante:{metodo_pais_away}",
            "metodo_emparejamiento": f"local:{metodo_home}/visitante:{metodo_away}",
            "rating_propio_partidos_local": n_home,
            "rating_propio_partidos_visitante": n_away,
            "rd_local": rd_home,
            "rd_visitante": rd_away,
            "verificado_cuota_real": verificado_cuota_real,
            "discrepancia_cuota_real": discrepancia_cuota_real,
            "favorito_solo_por_cuota_real": es_favorito_por_cuota_real and not cumple_filtro_cuota(evaluacion),
            **cuota_real_info,
            "hora_inicio": f["fixture"]["date"],
            "fixture_id": f["fixture"]["id"],
            "liga_slug": liga_slug,
            "home_id": home_id,
            "away_id": away_id,
            "kickoff_utc": f["fixture"]["date"],
            "resultado_final": None,
            "acierto": None,
            "historial_snapshots": [],
            "alertas_enviadas": [],
            "diferencia_maxima_alcanzada": 0,
        })

    print(f"Partidos sin ninguna fuente de rating disponible (no evaluables): {sin_elo_ni_rating_propio}")
    print(f"Partidos evaluados SIN poder verificar el pais de ambos equipos: {sin_pais_verificado}")
    print(f"Partidos con al menos un Elo estimado via Goal Index: {elo_estimados_van}")
    n_verificados = sum(1 for p in seleccionados if p["verificado_cuota_real"])
    n_solo_cuota = sum(1 for p in seleccionados if p["favorito_solo_por_cuota_real"])
    n_discrepancia = sum(1 for p in seleccionados if p["discrepancia_cuota_real"])
    print(f"Verificados por cuota real (coinciden con el modelo): {n_verificados}")
    print(f"Agregados SOLO por cuota real (el modelo no los tenia): {n_solo_cuota}")
    print(f"Con discrepancia (modelo y cuota real no coinciden en el lado): {n_discrepancia}")

    seleccionados.sort(key=lambda p: not p["verificado_cuota_real"])

    ARCHIVO_SALIDA.write_text(
        json.dumps({"fecha": hoy, "partidos": seleccionados}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    sin_verificar_seleccionados = sum(1 for p in seleccionados if not p["pais_verificado"])
    print(f"Guardado en {ARCHIVO_SALIDA}. {len(seleccionados)} partidos seleccionados "
          f"(probabilidad inicial >= 65%), de los cuales {sin_verificar_seleccionados} "
          f"sin verificacion de pais.")


if __name__ == "__main__":
    seleccionar()
