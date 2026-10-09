import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

# ---------------------------------------------------------
# 1. CONFIGURACIÓN Y CREDENCIALES (NATIVO BETPLAY / KAMBI)
# ---------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

UMBRAL_MINIMO_FILTRO = 75.0
PISO_MINIMO_CUOTA = 1.40
ZONA_HORARIA_COLOMBIA = timezone(timedelta(hours=-5))

MODELO_GEMINI = "gemini-3.8-flash"

# URL PÚBLICA REAL DEL SERVIDOR CDN DE KAMBI PARA BETPLAY COLOMBIA
KAMBI_BETPLAY_URL = "https://offering-api.kambi.com/offering/v2018/betplay/listView/basketball.json?lang=es_CO&market=CO"

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

def obtener_partidos_kambi_betplay():
    """Extrae las cuotas directas desde el servidor nativo de Kambi/BetPlay"""
    lista_partidos = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }

    try:
        res = session.get(KAMBI_BETPLAY_URL, headers=headers, timeout=20)
        if res.status_code != 200:
            print(f"Error accediendo a Kambi: Status HTTP {res.status_code}")
            return []

        data = res.json()
        eventos = data.get("events", [])

        ahora_utc = datetime.now(timezone.utc)
        fin_ventana_utc = ahora_utc + timedelta(hours=12)

        for item in eventos:
            event = item.get("event", {})
            if not event:
                continue

            # Validar ventana de tiempo (próximas 12 horas)
            start_raw = event.get("start", "")
            if not start_raw:
                continue
            dt_utc = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
            if not (ahora_utc <= dt_utc <= fin_ventana_utc):
                continue

            dt_colombia = dt_utc.astimezone(ZONA_HORARIA_COLOMBIA)
            home_team = event.get("homeName", "").strip()
            away_team = event.get("awayName", "").strip()
            group_name = event.get("group", "Baloncesto")

            c_loc, c_vis = None, None
            spread_point, total_point = None, None

            # Extraer ofertas de apuestas directas de Kambi
            offer_categories = item.get("betOffers", [])
            for offer in offer_categories:
                offer_type = offer.get("betOfferType", {}).get("name", "")
                
                # MERCADO MONEYLINE (GANADOR DEL PARTIDO CON PRÓRROGA INCLUIDA)
                if offer_type in ["Match", "Moneyline", "Ganador - Prórroga incluida", "12"]:
                    outcomes = offer.get("outcomes", [])
                    for out in outcomes:
                        label = out.get("label", "")
                        type_out = out.get("type", "")
                        price = out.get("odds", 0) / 1000.0  # Kambi maneja cuotas en milésimas (ej. 1830 -> 1.83)

                        if type_out == "OT_ONE" or label.lower() == home_team.lower():
                            c_loc = round(price, 2)
                        elif type_out == "OT_TWO" or label.lower() == away_team.lower():
                            c_vis = round(price, 2)

                # MERCADO HÁNDICAP
                elif offer_type in ["Handicap", "Hándicap de Puntos - Prórroga incluida"]:
                    outcomes = offer.get("outcomes", [])
                    for out in outcomes:
                        if out.get("type") == "OT_ONE":
                            spread_point = out.get("line", 0) / 1000.0

                # MERCADO TOTALES
                elif offer_type in ["Total", "Total de puntos - Prórroga incluida"]:
                    outcomes = offer.get("outcomes", [])
                    if outcomes:
                        total_point = outcomes[0].get("line", 0) / 1000.0

            if not c_loc or not c_vis:
                continue

            prob_impl_home = (1 / c_loc) / ((1 / c_loc) + (1 / c_vis))
            prob_impl_away = (1 / c_vis) / ((1 / c_loc) + (1 / c_vis))

            lista_partidos.append({
                "liga": f"🏀 {group_name}",
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

    except Exception as e:
        print("Error procesando feed nativo de Kambi:", e)

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
                time.sleep(4)
            else:
                break
        except Exception as e:
            time.sleep(3)
            
    return None

def analizar_partido_baloncesto_ia(p):
    linea_total_str = f"{p['total_point']}" if p.get("total_point") is not None else "N/A"
    linea_spread_str = f"{p['spread_point']}" if p.get("spread_point") is not None else "N/A"

    prompt_triangulacion = (
        f"EVALUACIÓN DE TRIANGULACIÓN DE BALONCESTO NATIVA BETPLAY ({p['equipo_local']} vs {p['equipo_visitante']} - {p['liga']}):\n\n"
        f"DATOS DIRECTOS DE KAMBI/BETPLAY:\n"
        f"- LOCAL: {p['equipo_local']} -> CUOTA BETPLAY: {p['cuota_local']}\n"
        f"- VISITANTE: {p['equipo_visitante']} -> CUOTA BETPLAY: {p['cuota_visita']}\n"
        f"- LÍNEA TOTAL PUNTOS: {linea_total_str}\n"
        f"- LÍNEA HÁNDICAP LOCAL: {linea_spread_str}\n\n"
        f"REGLA OBLIGATORIA DE ASIGNACIÓN:\n"
        f"1. Si tu pronóstico es la victoria de {p['equipo_local']}, la 'cuota_evaluada' TIENE QUE SER {p['cuota_local']}.\n"
        f"2. Si tu pronóstico es la victoria de {p['equipo_visitante']}, la 'cuota_evaluada' TIENE QUE SER {p['cuota_visita']}.\n"
        f"3. ESTÁ PROHIBIDO intercambiar las cuotas entre los dos equipos.\n"
        f"4. Requisito estricto: Probabilidad >= {UMBRAL_MINIMO_FILTRO}% y cuota >= {PISO_MINIMO_CUOTA}."
    )

    for intento in range(2):
        res = llamar_gemini_rest(prompt_triangulacion)
        if res:
            return res
        time.sleep(2)

    return None

def ejecutar_escaneo():
    ahora_colombia = datetime.now(ZONA_HORARIA_COLOMBIA)
    fecha_hora_col = ahora_colombia.strftime("%Y-%m-%d %I:%M %p")
    print(f"Iniciando escaneo Nativo Kambi/BetPlay: {fecha_hora_col}")
    
    partidos = obtener_partidos_kambi_betplay()

    if not partidos:
        msg = f"🏀 <b>REPORTE BALONCESTO BETPLAY</b>\n<i>Escaneo: {fecha_hora_col}</i>\n\n<i>Sin partidos programados en las próximas 12 horas en la parrilla de BetPlay.</i>"
        enviar_mensaje_telegram(msg)
        return

    enviar_mensaje_telegram(f"🏀 <b>PRONÓSTICOS BALONCESTO VIP (NATIVO BETPLAY)</b>\n<i>Escaneo: {fecha_hora_col}</i>")

    for p in partidos[:12]:
        analisis = analizar_partido_baloncesto_ia(p)

        if not analisis:
            continue

        prob_max = max(analisis.get("prob_pick_principal", 0), analisis.get("prob_cobertura", 0))
        cuota_evaluada = analisis.get("cuota_evaluada", 0.0)

        if prob_max < UMBRAL_MINIMO_FILTRO or cuota_evaluada < PISO_MINIMO_CUOTA:
            print(f"⛔ Descartado {p['equipo_local']} vs {p['equipo_visitante']} (Prob: {prob_max}%, Cuota: {cuota_evaluada})")
            continue

        msg = (
            f"🏀 <b>{p['liga']}</b>\n"
            f"⚔️ <b>{p['equipo_local']} vs {p['equipo_visitante']}</b>\n"
            f"📅 <b>Fecha:</b> <code>{p['fecha']}</code> | ⏰ <b>Hora Col:</b> <code>{p['hora']}</code>\n"
            f"💰 <b>Cuotas BetPlay:</b> <code>{p['cuota_local']} - {p['cuota_visita']}</code>\n\n"
            f"🎯 <b>APUESTA PRINCIPAL: {analisis['pick_principal']}</b> (<code>Cuota: {cuota_evaluada}</code>)\n"
            f"📏 <b>Margen de Operatividad:</b> <i>{analisis['margen_operatividad_universal']}</i>\n"
            f"📲 <b>Regla de Valor:</b> <i>{analisis['regla_valor_betplay']}</i>\n"
            f"📈 <b>Probabilidad:</b> <code>{analisis['prob_pick_principal']}%</code>\n"
            f"💡 <i>[Gemini Triangulado] {analisis['analisis_tactico']}</i>\n\n"
            f"🛡 <b>COBERTURA ALTERNATIVA:</b> {analisis['pick_cobertura']} (<code>{analisis['prob_cobertura']}%</code>)"
        )
        
        enviar_mensaje_telegram(msg)

if __name__ == "__main__":
    ejecutar_escaneo()
