import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

# ---------------------------------------------------------
# 1. CONFIGURACIÓN Y CREDENCIALES (BALONCESTO OPTIMIZADO BETPLAY)
# ---------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

UMBRAL_MINIMO_FILTRO = 70.0
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
    prob_pick_principal: float = Field(description="Probabilidad estimada para la opción principal (0 a 100)")
    pick_principal: str = Field(description="Mercado principal recomendado (ej. Gana Local Moneyline, Handicap +4.5, Altas 218.5)")
    regla_valor_betplay: str = Field(description="Instrucción de valor para BetPlay si movieron la línea de puntos.")
    stake_principal: str = Field(description="Stake sugerido según la certeza (ej. 3/5 o 4/5)")
    prob_cobertura: float = Field(description="Probabilidad estimada opción de cobertura (0 a 100)")
    pick_cobertura: str = Field(description="Opción de cobertura (ej. Handicap Alternativo Local +8.5)")
    analisis_tactico: str = Field(description="Justificación táctica basada en ritmo, forma y ausencias clave en máx 2 oraciones.")

def enviar_mensaje_telegram(texto):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Error: Credenciales de Telegram no configuradas.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": texto, "parse_mode": "HTML"}
    try:
        res = requests.post(url, json=payload, timeout=5)  # Timeout de seguridad de 5s
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

# ---------------------------------------------------------
# 2. INGESTA DE CUOTAS (CON TIMEOUT Y REGLAS DE SEGURIDAD)
# ---------------------------------------------------------
def obtener_partidos_baloncesto():
    if not ODDS_API_KEY:
        print("Error: ODDS_API_KEY no está configurada.")
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
            # timeout=5 de seguridad: si la API no responde en 5 segundos, aborta y pasa a la siguiente liga
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
            time.sleep(0.3)
        except Exception as e:
            print(f"Error al consultar {liga['nombre']}:", e)
    return lista_partidos

# ---------------------------------------------------------
# 3. EVALUACIÓN Y VALIDACIÓN CON IA (GEMINI 3.8)
# ---------------------------------------------------------
def analizar_partido_baloncesto_ia(partido):
    if not client_gemini:
        return None, "IA no configurada"

    info_lineas = ""
    if partido.get("spread_point") is not None:
        info_lineas += f"Handicap referencia Local: {partido['spread_point']}. "
    if partido.get("total_point") is not None:
        info_lineas += f"Línea Totales referencia: {partido['total_point']}. "

    prompt = (
        f"Analiza el partido de baloncesto para las PRÓXIMAS 12 HORAS: {partido['local']} vs {partido['visitante']} ({partido['liga']}).\n"
        f"Cuotas Moneyline: Local ({partido['cuota_local']}) / Visitante ({partido['cuota_visitante']}).\n"
        f"Probabilidades Implícitas Desmarginadas: Local ({partido['prob_math_local']}%), Visitante ({partido['prob_math_visitante']}%).\n"
        f"{info_lineas}\n"
        f"Considera factores de baloncesto (ritmo de juego, eficiencia ofensiva/defensiva, ausencias clave de jugadores, descansos back-to-back o rotaciones).\n"
        f"REGLA BETPLAY: En 'regla_valor_betplay' indica el límite de tolerancia si en BetPlay la línea movió puntos antes de descartar.\n"
        f"Establece en 'pick_principal' la mejor opción de valor con certeza >= {UMBRAL_MINIMO_FILTRO}%."
    )

    try:
        res = client_gemini.models.generate_content(
            model=MODELO_GEMINI,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=AnalisisBaloncestoSchema,
                temperature=0.15
            )
        )
        if res and res.text:
            return json.loads(res.text), "OK"
    except Exception as e:
        print(f"Error evaluando {partido['local']} vs {partido['visitante']}: {e}")
        return None, str(e)

    return None, "ERROR_GENERAL"

# ---------------------------------------------------------
# 4. ORQUESTADOR PRINCIPAL
# ---------------------------------------------------------
def ejecutar_escaneo():
    ahora_colombia = datetime.now(ZONA_HORARIA_COLOMBIA)
    fecha_hora_col = ahora_colombia.strftime("%Y-%m-%d %I:%M %p")
    print(f"Iniciando escaneo de Baloncesto (Próximas 12 Horas): {fecha_hora_col}")
    partidos = obtener_partidos_baloncesto()

    if not partidos:
        msg = f"🏀 <b>REPORTE BALONCESTO</b>\n<i>Escaneo: {fecha_hora_col}</i>\n\n<i>Sin partidos programados con cuotas para las próximas 12 horas.</i>"
        enviar_mensaje_telegram(msg)
        print("Finalizado: Sin partidos en la ventana de 12 horas.")
        return

    enviar_mensaje_telegram(f"🏀 <b>PRONÓSTICOS BALONCESTO VIP (BETPLAY READY)</b>\n<i>Escaneo: {fecha_hora_col}</i>")
    
    partidos_enviados = 0
    descartados_certeza = 0

    for p in partidos:
        time.sleep(1.5)
        analisis, estado = analizar_partido_baloncesto_ia(p)

        if not analisis:
            continue

        prob_max = max(analisis.get("prob_pick_principal", 0), analisis.get("prob_cobertura", 0))
        if prob_max < UMBRAL_MINIMO_FILTRO:
            descartados_certeza += 1
            continue

        msg = (
            f"🏀 <b>{p['liga']}</b> | {p['local']} vs {p['visitante']}\n"
            f"📅 <b>Fecha:</b> <code>{p['fecha']}</code> | ⏰ <b>Hora Col:</b> <code>{p['hora']}</code>\n"
            f"💰 <b>Cuotas ML:</b> <code>{p['cuota_local']} - {p['cuota_visitante']}</code>\n\n"
            f"🎯 <b>APUESTA PRINCIPAL: {analisis['pick_principal']}</b>\n"
            f"📲 <b>Regla de Valor BetPlay:</b> <i>{analisis['regla_valor_betplay']}</i>\n"
            f"📊 <b>Probabilidad:</b> <code>{analisis['prob_pick_principal']}%</code> | <b>Stake:</b> <code>{analisis['stake_principal']}</code>\n"
            f"💡 <i>[Gemini] {analisis['analisis_tactico']}</i>\n\n"
            f"🛡 <b>COBERTURA ALTERNATIVA:</b> {analisis['pick_cobertura']} (<code>{analisis['prob_cobertura']}%</code>)"
        )
        
        exito_envio = enviar_mensaje_telegram(msg)
        if exito_envio:
            partidos_enviados += 1
            print(f"✅ Enviado a Telegram: {p['local']} vs {p['visitante']}")

    msg_resumen = f"<b>Escaneo baloncesto completado.</b> Pronósticos enviados: {partidos_enviados}"
    if partidos_enviados == 0 and descartados_certeza > 0:
        msg_resumen += f"\n\n<b>Detalle:</b> {descartados_certeza} partido(s) analizados no alcanzaron el {UMBRAL_MINIMO_FILTRO}% de certeza."

    enviar_mensaje_telegram(msg_resumen)
    print(f"Proceso baloncesto completado. Enviados: {partidos_enviados}")

if __name__ == "__main__":
    ejecutar_escaneo()
