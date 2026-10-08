import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

# ---------------------------------------------------------
# 1. CONFIGURACIÓN Y CREDENCIALES
# ---------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

UMBRAL_MINIMO_FILTRO = 75.0
PISO_MINIMO_CUOTA = 1.40  # CANDADO DE RENTABILIDAD
ZONA_HORARIA_COLOMBIA = timezone(timedelta(hours=-5))

client_gemini = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
MODELO_GEMINI = 'gemini-3.8-flash'

LIGAS_BALONCESTO = [
    {"nombre": "🏀 NBA Preseason", "sport_key": "basketball_nba_preseason"},
    {"nombre": "🏀 NBA Regular", "sport_key": "basketball_nba"},
    {"nombre": "🇪🇺 Euroleague", "sport_key": "basketball_euroleague"},
    {"nombre": "🇪🇸 Liga Endesa (España)", "sport_key": "basketball_spain_liga_endesa"},
    {"nombre": "🎓 NCAA Baloncesto", "sport_key": "basketball_ncaab"}
]

class AnalisisBaloncestoSchema(BaseModel):
    prob_pick_principal: float = Field(description="Probabilidad estimada final (0 a 100)")
    pick_principal: str = Field(description="Mercado comercial exacto evaluado (ej. Handicap -5.5, Over 218.5, Gana Local ML)")
    cuota_evaluada: float = Field(description="Cuota decimal real evaluada provista por BetPlay.")
    margen_operatividad_universal: str = Field(description="Instrucción del rango aceptable en BetPlay y cuándo ABSTENERSE por pérdida de valor.")
    regla_valor_betplay: str = Field(description="Regla de cuota en BetPlay. Exige abstenerse si cae de 1.40.")
    stake_principal: str = Field(description="Stake sugerido según certeza (ej. 3/5 o 4/5)")
    prob_cobertura: float = Field(description="Probabilidad estimada opción de cobertura (0 a 100)")
    pick_cobertura: str = Field(description="Opción de cobertura comercial en BetPlay")
    analisis_tactico: str = Field(description="Justificación táctica basada en la triangulación de noticias en vivo y cuota real en máx 2 oraciones.")

def enviar_mensaje_telegram(texto):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Error: Credenciales de Telegram no configuradas.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": texto, "parse_mode": "HTML"}
    try:
        res = requests.post(url, json=payload, timeout=5)
        return res.status_code == 200
    except Exception as e:
        print("Error enviando mensaje a Telegram:", e)
        return False

def calcular_probabilidad_implicita(cuota_local, cuota_visitante):
    if not cuota_local or not cuota_visitante:
        return 50.0, 50.0
    p_loc = 1.0 / cuota_local
    p_vis = 1.0 / cuota_visitante
    margen = p_loc + p_vis
    return round((p_loc / margen) * 100, 1), round((p_vis / margen) * 100, 1)

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
            res = requests.get(url, params=params, timeout=5)
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
                home_team, away_team = ev.get("home_team"), ev.get("away_team")
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
                        if m.get("key") == "h2h":
                            for o in m.get("outcomes", []):
                                if o.get("name") == home_team: c_loc = o.get("price")
                                elif o.get("name") == away_team: c_vis = o.get("price")
                        elif m.get("key") == "spreads":
                            for o in m.get("outcomes", []):
                                if o.get("name") == home_team: spread_point = o.get("point")
                        elif m.get("key") == "totals":
                            outcomes = m.get("outcomes", [])
                            if outcomes: total_point = outcomes[0].get("point")
                
                if not c_loc or not c_vis:
                    continue
                
                # FILTRO DURO DE CUOTA EN PYTHON: Al menos una opción debe pagar >= 1.40
                if c_loc < PISO_MINIMO_CUOTA and c_vis < PISO_MINIMO_CUOTA:
                    continue

                p_loc, p_vis = calcular_probabilidad_implicita(c_loc, c_vis)
                lista_partidos.append({
                    "liga": liga["nombre"],
                    "local": home_team,
                    "visitante": away_team,
                    "fecha": dt_colombia.strftime("%Y-%m-%d"),
                    "hora": dt_colombia.strftime("%I:%M %p"),
                    "cuota_local": c_loc,
                    "cuota_visitante": c_vis,
                    "spread_point": spread_point,
                    "total_point": total_point,
                    "prob_math_local": p_loc,
                    "prob_math_visitante": p_vis
                })
            time.sleep(0.2)
        except Exception as e:
            print(f"Error al consultar {liga['nombre']}:", e)
    return lista_partidos

def rastrear_noticias_globales(partidos):
    if not client_gemini or not partidos:
        return "Sin novedades encontradas."

    resumen = "\n".join([f"- {p['local']} vs {p['visitante']} ({p['fecha']})" for p in partidos])
    query = f"Busca novedades oficiales de lesión, resting, minutos restringidos y alineaciones confirmadas para:\n{resumen}"

    try:
        res = client_gemini.models.generate_content(
            model=MODELO_GEMINI,
            contents=query,
            config=types.GenerateContentConfig(tools=[{"google_search": {}}])
        )
        if res and res.text:
            return res.text
    except Exception as e:
        print("Advertencia en rastreo global:", e)

    return "Información médica y táctica estándar."

def analizar_partido_baloncesto_ia(partido, noticias_globales):
    if not client_gemini:
        return None, "IA no configurada"

    info_lineas = ""
    if partido.get("spread_point") is not None:
        info_lineas += f"Hándicap de Referencia BetPlay: {partido['spread_point']}. "
    if partido.get("total_point") is not None:
        info_lineas += f"Línea de Totales BetPlay: {partido['total_point']}. "

    prompt_triangulacion = (
        f"EVALUACIÓN DE TRIANGULACIÓN REAL ({partido['local']} vs {partido['visitante']}):\n\n"
        f"1. DATOS FINANCIEROS REALES DE BETPLAY (PROVISTOS POR PYTHON):\n"
        f"   - Cuotas ML Reales: Local ({partido['cuota_local']}) / Visitante ({partido['cuota_visitante']}).\n"
        f"   - Prob. Desmarginada Implícita: Local ({partido['prob_math_local']}%), Visitante ({partido['prob_math_visitante']}%).\n"
        f"   - {info_lineas}\n\n"
        f"2. NOTICIAS EN VIVO Y RASTREO WEB CONSOLIDADO:\n"
        f"   {noticias_globales}\n\n"
        f"REGLAS INVIOLABLES DE TRIANGULACIÓN:\n"
        f"A. EVALÚA ÚNICAMENTE OPCIONES CON CUOTA REAL >= {PISO_MINIMO_CUOTA}. PROHIBIDO INVENTAR O ESTIMAR CUOTAS MENORES A 1.40.\n"
        f"B. Cruza las noticias reales con la cuota de BetPlay. Si la cuota contradice las noticias o hay dudas de rotación, asigna probabilidad < 75%.\n"
        f"C. Si la certeza es menor al {UMBRAL_MINIMO_FILTRO}%, se descartará automáticamente."
    )

    try:
        res = client_gemini.models.generate_content(
            model=MODELO_GEMINI,
            contents=prompt_triangulacion,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=AnalisisBaloncestoSchema,
                temperature=0.10
            )
        )
        if res and res.text:
            return json.loads(res.text), "OK"
    except Exception as e:
        print(f"Error evaluando {partido['local']} vs {partido['visitante']}: {e}")
        return None, str(e)

    return None, "ERROR_GENERAL"

def ejecutar_escaneo():
    ahora_colombia = datetime.now(ZONA_HORARIA_COLOMBIA)
    fecha_hora_col = ahora_colombia.strftime("%Y-%m-%d %I:%M %p")
    print(f"Iniciando escaneo de Baloncesto (Cuotas Reales + Grounding): {fecha_hora_col}")
    partidos = obtener_partidos_baloncesto()

    if not partidos:
        msg = f"🏀 <b>REPORTE BALONCESTO</b>\n<i>Escaneo: {fecha_hora_col}</i>\n\n<i>Sin partidos programados que cumplan filtro de cuotas (>= 1.40) en las próximas 12 horas.</i>"
        enviar_mensaje_telegram(msg)
        return

    noticias_globales = rastrear_noticias_globales(partidos)
    enviar_mensaje_telegram(f"🏀 <b>PRONÓSTICOS BALONCESTO VIP (TRIANGULACIÓN REAL)</b>\n<i>Escaneo: {fecha_hora_col}</i>")
    
    partidos_enviados = 0
    descartados_certeza = 0

    for p in partidos:
        analisis, estado = analizar_partido_baloncesto_ia(p, noticias_globales)

        if not analisis:
            continue

        prob_max = max(analisis.get("prob_pick_principal", 0), analisis.get("prob_cobertura", 0))
        cuota_evaluada = analisis.get("cuota_evaluada", 0.0)

        # CANDADO DURO DE RENTABILIDAD Y CERTEZA EN PYTHON
        if prob_max < UMBRAL_MINIMO_FILTRO or cuota_evaluada < PISO_MINIMO_CUOTA:
            descartados_certeza += 1
            print(f"⛔ Descartado {p['local']} vs {p['visitante']} (Prob: {prob_max}%, Cuota Real: {cuota_evaluada})")
            continue

        msg = (
            f"🏀 <b>{p['liga']}</b> | {p['local']} vs {p['visitante']}\n"
            f"📅 <b>Fecha:</b> <code>{p['fecha']}</code> | ⏰ <b>Hora Col:</b> <code>{p['hora']}</code>\n"
            f"💰 <b>Cuotas ML BetPlay:</b> <code>{p['cuota_local']} - {p['cuota_visitante']}</code>\n\n"
            f"🎯 <b>APUESTA PRINCIPAL: {analisis['pick_principal']}</b> (<code>Cuota: {cuota_evaluada}</code>)\n"
            f"📏 <b>Margen de Operatividad BetPlay:</b> <i>{analisis['margen_operatividad_universal']}</i>\n"
            f"📲 <b>Regla de Valor BetPlay:</b> <i>{analisis['regla_valor_betplay']}</i>\n"
            f"📊 <b>Probabilidad:</b> <code>{analisis['prob_pick_principal']}%</code> | <b>Stake:</b> <code>{analisis['stake_principal']}</code>\n"
            f"💡 <i>[Gemini Triangulado] {analisis['analisis_tactico']}</i>\n\n"
            f"🛡 <b>COBERTURA ALTERNATIVA:</b> {analisis['pick_cobertura']} (<code>{analisis['prob_cobertura']}%</code>)"
        )
        
        exito_envio = enviar_mensaje_telegram(msg)
        if exito_envio:
            partidos_enviados += 1

    msg_resumen = f"<b>Escaneo baloncesto completado.</b> Pronósticos rentables enviados: {partidos_enviados}"
    if descartados_certeza > 0:
        msg_resumen += f"\n\n<b>Detalle:</b> {descartados_certeza} partido(s) descartados por triangulación o cuota < 1.40."

    enviar_mensaje_telegram(msg_resumen)

if __name__ == "__main__":
    ejecutar_escaneo()
