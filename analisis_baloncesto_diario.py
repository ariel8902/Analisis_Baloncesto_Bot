import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

# ---------------------------------------------------------
# 1. CONFIGURACIÓN Y CREDENCIALES (ESTRUCTURA DEFINITIVA)
# ---------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

UMBRAL_MINIMO_FILTRO = 75.0
PISO_MINIMO_CUOTA = 1.40  # CANDADO DURO DE RENTABILIDAD
ZONA_HORARIA_COLOMBIA = timezone(timedelta(hours=-5))

MODELO_GEMINI = "gemini-3.8-flash"

LIGAS_BALONCESTO = [
    {"nombre": "🏀 NBA Pretemporada", "sport_key": "basketball_nba_preseason"},
    {"nombre": "🏀 NBA", "sport_key": "basketball_nba"},
    {"nombre": "🏀 Euroliga", "sport_key": "basketball_euroleague"},
    {"nombre": "🏀 Liga ACB España", "sport_key": "basketball_spain_acb"},
    {"nombre": "🏀 NBL Australia", "sport_key": "basketball_nbl"}
]

session = requests.Session()
retries = Retry(total=3, backoff_factor=2, status_forcelist=[500, 502, 503, 504])
session.mount('https://', HTTPAdapter(max_retries=retries))

def enviar_mensaje_telegram(texto):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Error: Credenciales de Telegram no configuradas.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": texto, "parse_mode": "HTML"}
    try:
        res = session.post(url, json=payload, timeout=15)
        return res.status_code == 200
    except Exception as e:
        print("Error enviando mensaje a Telegram:", e)
        return False

def coincide_equipo(nombre_corto, nombre_largo):
    """Verifica si el nombre de la cuota pertenece al equipo sin depender de coincidencias del 100%"""
    n1 = nombre_corto.lower().strip()
    n2 = nombre_largo.lower().strip()
    return n1 in n2 or n2 in n1 or any(p in n2 for p in n1.split() if len(p) > 3)

def obtener_partidos_baloncesto():
    if not ODDS_API_KEY:
        print("Error: ODDS_API_KEY no configurada.")
        return []

    lista_partidos = []
    ahora_utc = datetime.now(timezone.utc)
    fin_ventana_utc = ahora_utc + timedelta(hours=12)

    for liga in LIGAS_BALONCESTO:
        url = f"https://api.the-odds-api.com/v4/sports/{liga['sport_key']}/odds/"
        params = {
            "apiKey": ODDS_API_KEY,
            "regions": "eu,us",
            "markets": "h2h,spreads,totals",
            "oddsFormat": "decimal"
        }
        try:
            res = session.get(url, params=params, timeout=15)
            if res.status_code != 200:
                continue
            eventos = res.json()
            for ev in eventos:
                commence_raw = ev.get("commence_time", "")
                if not commence_raw:
                    continue
                dt_utc = datetime.fromisoformat(commence_raw.replace("Z", "+00:00"))
                if not (ahora_utc <= dt_utc <= fin_ventana_utc):
                    continue

                dt_colombia = dt_utc.astimezone(ZONA_HORARIA_COLOMBIA)
                home_team = str(ev.get("home_team", "")).strip()
                away_team = str(ev.get("away_team", "")).strip()
                
                c_loc, c_vis = None, None
                spread_point, total_point = None, None
                
                bookmakers = ev.get("bookmakers", [])
                if bookmakers:
                    bm_seleccionado = bookmakers[0]
                    for bm in bookmakers:
                        if bm.get("key") in ["unibet", "unibet_eu", "888sport"]:
                            bm_seleccionado = bm
                            break

                    for m in bm_seleccionado.get("markets", []):
                        # AISLAMIENTO EXCLUSIVO DE MONEYLINE (H2H)
                        if m.get("key") == "h2h":
                            for o in m.get("outcomes", []):
                                name_out = str(o.get("name", "")).strip()
                                price_out = o.get("price")
                                
                                if coincide_equipo(home_team, name_out):
                                    c_loc = price_out
                                elif coincide_equipo(away_team, name_out):
                                    c_vis = price_out
                                    
                        elif m.get("key") == "spreads":
                            for o in m.get("outcomes", []):
                                name_out = str(o.get("name", "")).strip()
                                if coincide_equipo(home_team, name_out):
                                    spread_point = o.get("point")
                        elif m.get("key") == "totals":
                            outcomes = m.get("outcomes", [])
                            if outcomes:
                                total_point = outcomes[0].get("point")

                if not c_loc or not c_vis:
                    continue

                prob_impl_home = (1 / c_loc) / ((1 / c_loc) + (1 / c_vis))
                prob_impl_away = (1 / c_vis) / ((1 / c_loc) + (1 / c_vis))

                lista_partidos.append({
                    "liga": liga["nombre"],
                    "equipo_local": home_team,
                    "equipo_visitante": away_team,
                    "fecha": dt_colombia.strftime("%Y-%m-%d"),
                    "hora": dt_colombia.strftime("%I:%M %p"),
                    "cuota_local": c_loc,
                    "cuota_visita": c_vis,
                    "prob_real_local": round(prob_impl_home * 100, 1),
                    "prob_real_visita": round(prob_impl_away * 100, 1),
                    "spread_point": spread_point,
                    "total_point": total_point
                })
            time.sleep(0.1)
        except Exception as e:
            print(f"Error consultando {liga['nombre']}:", e)
    return lista_partidos

def llamar_gemini_rest(prompt):
    if not GEMINI_API_KEY:
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODELO_GEMINI}:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    
    schema_definition = {
        "type": "OBJECT",
        "properties": {
            "prob_pick_principal": {"type": "NUMBER"},
            "pick_principal": {"type": "STRING"},
            "cuota_evaluada": {"type": "NUMBER"},
            "margen_operatividad_universal": {"type": "STRING"},
            "regla_valor_betplay": {"type": "STRING"},
            "prob_cobertura": {"type": "NUMBER"},
            "pick_cobertura": {"type": "STRING"},
            "analisis_tactico": {"type": "STRING"}
        },
        "required": [
            "prob_pick_principal", "pick_principal", "cuota_evaluada",
            "margen_operatividad_universal", "regla_valor_betplay",
            "prob_cobertura", "pick_cobertura", "analisis_tactico"
        ]
    }

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": schema_definition,
            "temperature": 0.05
        }
    }

    for intento in range(3):
        try:
            res = session.post(url, headers=headers, json=payload, timeout=60)
            if res.status_code == 200:
                datos = res.json()
                texto_json = datos['candidates'][0]['content']['parts'][0]['text']
                return json.loads(texto_json)
            elif res.status_code in [503, 500, 502, 504]:
                print(f"Intento {intento + 1}: Servidor saturado ({res.status_code}). Reintentando en 4s...")
                time.sleep(4)
            else:
                print(f"Error HTTP Gemini REST: {res.status_code} - {res.text}")
                break
        except Exception as e:
            print(f"Excepción en llamada REST (Intento {intento + 1}): {e}")
            time.sleep(3)
            
    return None

def rastrear_noticias_globales(partidos):
    if not partidos:
        return "Sin novedades web previas."

    resumen = "\n".join([f"- {p['equipo_local']} vs {p['equipo_visitante']} ({p['fecha']})" for p in partidos])
    prompt = f"Resume reportes oficiales de lesiones y bajas confirmadas para los siguientes partidos de baloncesto:\n{resumen}"
    
    res = llamar_gemini_rest(prompt)
    if res and isinstance(res, dict):
        return res.get("analisis_tactico", "Sin bajas críticas reportadas.")
    return "Sin bajas críticas reportadas."

def analizar_partido_baloncesto_ia(p, noticias_globales):
    linea_total_str = f"{p['total_point']}" if p.get("total_point") is not None else "N/A"
    linea_spread_str = f"{p['spread_point']}" if p.get("spread_point") is not None else "N/A"

    prompt_triangulacion = (
        f"EVALUACIÓN DE TRIANGULACIÓN DE BALONCESTO ({p['equipo_local']} vs {p['equipo_visitante']} - {p['liga']}):\n\n"
        f"DATOS DE ENTRADA PROCESADOS DE BETPLAY:\n"
        f"- LOCAL: {p['equipo_local']} -> CUOTA MONEYLINE EXACTA: {p['cuota_local']}\n"
        f"- VISITANTE: {p['equipo_visitante']} -> CUOTA MONEYLINE EXACTA: {p['cuota_visita']}\n"
        f"- LÍNEA TOTAL PUNTOS: {linea_total_str}\n"
        f"- LÍNEA HÁNDICAP LOCAL: {linea_spread_str}\n\n"
        f"NOTICIAS DE BAJAS:\n{noticias_globales}\n\n"
        f"REGLA OBLIGATORIA:\n"
        f"1. Si seleccionas la victoria de {p['equipo_local']}, la 'cuota_evaluada' DEBE SER EXACTAMENTE {p['cuota_local']}.\n"
        f"2. Si seleccionas la victoria de {p['equipo_visitante']}, la 'cuota_evaluada' DEBE SER EXACTAMENTE {p['cuota_visita']}.\n"
        f"3. ESTÁ ABSOLUTAMENTE PROHIBIDO INTERCAMBIAR LAS CUOTAS.\n"
        f"4. Requisito de probabilidad >= {UMBRAL_MINIMO_FILTRO}% y cuota >= {PISO_MINIMO_CUOTA}."
    )

    for intento in range(2):
        res = llamar_gemini_rest(prompt_triangulacion)
        if res:
            return res, "OK"
        time.sleep(2)

    return None, "ERROR_CONEXION"

def ejecutar_escaneo():
    ahora_colombia = datetime.now(ZONA_HORARIA_COLOMBIA)
    fecha_hora_col = ahora_colombia.strftime("%Y-%m-%d %I:%M %p")
    print(f"Iniciando escaneo de Baloncesto (Estructura Limpia ML): {fecha_hora_col}")
    partidos = obtener_partidos_baloncesto()

    if not partidos:
        msg = f"🏀 <b>REPORTE BALONCESTO</b>\n<i>Escaneo: {fecha_hora_col}</i>\n\n<i>Sin partidos programados en las próximas 12 horas.</i>"
        enviar_mensaje_telegram(msg)
        return

    partidos_recortados = partidos[:12]
    noticias_globales = rastrear_noticias_globales(partidos_recortados)
    enviar_mensaje_telegram(f"🏀 <b>PRONÓSTICOS BALONCESTO VIP (TRIANGULACIÓN REAL)</b>\n<i>Escaneo: {fecha_hora_col}</i>")
    
    partidos_enviados = 0
    descartados_certeza = 0

    for p in partidos_recortados:
        analisis, estado = analizar_partido_baloncesto_ia(p, noticias_globales)

        if not analisis:
            continue

        prob_max = max(analisis.get("prob_pick_principal", 0), analisis.get("prob_cobertura", 0))
        cuota_evaluada = analisis.get("cuota_evaluada", 0.0)

        if prob_max < UMBRAL_MINIMO_FILTRO or cuota_evaluada < PISO_MINIMO_CUOTA:
            descartados_certeza += 1
            print(f"⛔ Descartado {p['equipo_local']} vs {p['equipo_visitante']} (Prob: {prob_max}%, Cuota: {cuota_evaluada})")
            continue

        msg = (
            f"🏀 <b>{p['liga']}</b>\n"
            f"⚔️ <b>{p['equipo_local']} vs {p['equipo_visitante']}</b>\n"
            f"📅 <b>Fecha:</b> <code>{p['fecha']}</code> | ⏰ <b>Hora Col:</b> <code>{p['hora']}</code>\n"
            f"💰 <b>Cuotas ML BetPlay:</b> <code>{p['cuota_local']} - {p['cuota_visita']}</code>\n\n"
            f"🎯 <b>APUESTA PRINCIPAL: {analisis['pick_principal']}</b> (<code>Cuota: {cuota_evaluada}</code>)\n"
            f"📏 <b>Margen de Operatividad BetPlay:</b> <i>{analisis['margen_operatividad_universal']}</i>\n"
            f"📲 <b>Regla de Validación BetPlay:</b> <i>{analisis['regla_valor_betplay']}</i>\n"
            f"📈 <b>Probabilidad:</b> <code>{analisis['prob_pick_principal']}%</code>\n"
            f"💡 <i>[Gemini Triangulado] {analisis['analisis_tactico']}</i>\n\n"
            f"🛡 <b>COBERTURA ALTERNATIVA:</b> {analisis['pick_cobertura']} (<code>{analisis['prob_cobertura']}%</code>)"
        )
        
        exito_envio = enviar_mensaje_telegram(msg)
        if exito_envio:
            partidos_enviados += 1

if __name__ == "__main__":
    ejecutar_escaneo()
