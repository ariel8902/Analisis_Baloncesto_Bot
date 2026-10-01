import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel, Field
from google import genai

# ---------------------------------------------------------
# 1. CONFIGURACIÓN Y CREDENCIALES
# ---------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")

UMBRAL_MINIMO_FILTRO = 70.0
ZONA_HORARIA_COLOMBIA = timezone(timedelta(hours=-5))

client_gemini = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# Lista de modelos (Principal y Fallback en caso de 503)
MODELOS_GEMINI = ['gemini-3.8-flash', 'gemini-2.5-flash']

# Ligas de Baloncesto en The Odds API
LIGAS_BASKETBALL = [
    {"nombre": "🏀 NBA", "sport_key": "basketball_nba"},
    {"nombre": "🇪🇺 EuroLeague", "sport_key": "basketball_euroleague"},
    {"nombre": "🎓 NCAAB", "sport_key": "basketball_ncaab"}
]

class AnalisisBaloncestoSchema(BaseModel):
    prob_pick_principal: float = Field(description="Probabilidad estimada de la opción principal (0 a 100)")
    pick_principal: str = Field(description="Nombre exacto del mercado principal (ej. Over 215.5 Puntos, Hándicap -4.5 Local)")
    stake_principal: str = Field(description="Stake sugerido (ej. 4/5)")
    prob_cobertura: float = Field(description="Probabilidad estimada de la cobertura (0 a 100)")
    pick_cobertura: str = Field(description="Nombre exacto de la cobertura (ej. Gana Visitante Hándicap +6.5)")
    analisis_tactico: str = Field(description="Justificación técnica sintética del partido en máximo 2 oraciones.")

def enviar_mensaje_telegram(texto):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Error: Credenciales de Telegram no configuradas.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": texto, "parse_mode": "HTML"}
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code != 200:
            print(f"Telegram respondió con código HTTP {res.status_code}")
    except Exception as e:
        print("Error enviando mensaje a Telegram:", e)

# ---------------------------------------------------------
# 2. INGESTA DE CUOTAS (THE ODDS API - PRÓXIMAS 20 HORAS)
# ---------------------------------------------------------
def obtener_partidos_baloncesto():
    if not ODDS_API_KEY:
        print("Error: ODDS_API_KEY no está configurada.")
        return []

    lista_partidos = []
    ahora_utc = datetime.now(timezone.utc)
    limite_jornada = ahora_utc + timedelta(hours=20)

    for liga in LIGAS_BASKETBALL:
        url = f"https://api.the-odds-api.com/v4/sports/{liga['sport_key']}/odds/"
        params = {
            "apiKey": ODDS_API_KEY,
            "regions": "us,eu",
            "markets": "h2h,totals",
            "oddsFormat": "decimal"
        }
        try:
            res = requests.get(url, params=params, timeout=10)
            if res.status_code != 200:
                continue

            eventos = res.json()
            for ev in eventos:
                commence_raw = ev.get("commence_time", "")
                if not commence_raw:
                    continue

                try:
                    dt_utc = datetime.fromisoformat(commence_raw.replace("Z", "+00:00"))
                    if not (ahora_utc <= dt_utc <= limite_jornada):
                        continue
                    hora_fmt = dt_utc.astimezone(ZONA_HORARIA_COLOMBIA).strftime("%H:%M")
                except Exception:
                    continue

                home_team = ev.get("home_team")
                away_team = ev.get("away_team")
                cuota_local, cuota_visitante = None, None
                linea_total, cuota_over, cuota_under = None, None, None

                bookmakers = ev.get("bookmakers", [])
                if bookmakers:
                    markets = bookmakers[0].get("markets", [])
                    for m in markets:
                        if m.get("key") == "h2h":
                            for outcome in m.get("outcomes", []):
                                if outcome.get("name") == home_team:
                                    cuota_local = outcome.get("price")
                                elif outcome.get("name") == away_team:
                                    cuota_visitante = outcome.get("price")
                        elif m.get("key") == "totals":
                            outcomes = m.get("outcomes", [])
                            if outcomes:
                                linea_total = outcomes[0].get("point")
                                for o in outcomes:
                                    if o.get("name") == "Over":
                                        cuota_over = o.get("price")
                                    elif o.get("name") == "Under":
                                        cuota_under = o.get("price")

                if not cuota_local or not cuota_visitante:
                    continue

                lista_partidos.append({
                    "liga": liga["nombre"],
                    "local": home_team,
                    "visitante": away_team,
                    "hora": hora_fmt,
                    "cuota_local": cuota_local,
                    "cuota_visitante": cuota_visitante,
                    "linea_total": linea_total if linea_total else "N/A",
                    "cuota_over": cuota_over if cuota_over else "N/A",
                    "cuota_under": cuota_under if cuota_under else "N/A"
                })
            time.sleep(0.5)
        except Exception as e:
            print(f"Error al consultar {liga['nombre']}:", e)

    return lista_partidos

# ---------------------------------------------------------
# 3. EVALUACIÓN CON GEMINI IA (CON FALLBACK Y RETRIES)
# ---------------------------------------------------------
def analizar_partido_baloncesto_ia(partido):
    if not client_gemini:
        return None

    prompt = (
        f"Evalúa objetivamente el partido de baloncesto: {partido['local']} vs {partido['visitante']} ({partido['liga']}).\n"
        f"Cuotas Ganador: Local ({partido['cuota_local']}) vs Visitante ({partido['cuota_visitante']}).\n"
        f"Línea de Puntos Total: {partido['linea_total']} (Over {partido['cuota_over']} / Under {partido['cuota_under']}).\n"
        f"Analiza el ritmo de juego, eficiencia ofensiva/defensiva y proyecta la probabilidad estimada (0-100) "
        f"para la mejor opción (Línea de Puntos u Hándicap/Ganador) y una opción de cobertura."
    )

    tiempos_espera = [8, 15, 25]

    for modelo in MODELOS_GEMINI:
        for intento in range(3):
            try:
                res = client_gemini.models.generate_content(
                    model=modelo,
                    contents=prompt,
                    config={
                        "response_mime_type": "application/json",
                        "response_schema": AnalisisBaloncestoSchema,
                    }
                )
                if res and res.text:
                    return json.loads(res.text)
            except Exception as e:
                espera = tiempos_espera[intento]
                print(f"Aviso en {modelo} (Intento {intento+1}). Reintentando en {espera}s... Error: {e}")
                time.sleep(espera)

    return None

# ---------------------------------------------------------
# 4. ORQUESTADOR PRINCIPAL
# ---------------------------------------------------------
def ejecutar_escaneo():
    fecha_colombia = datetime.now(ZONA_HORARIA_COLOMBIA).strftime("%Y-%m-%d")
    print(f"Iniciando escaneo de Baloncesto Prepartido: {fecha_colombia}")

    partidos = obtener_partidos_baloncesto()

    if not partidos:
        mensaje = (
            f"🏀 <b>REPORTE BALONCESTO - {fecha_colombia}</b>\n\n"
            f"<i>Sin partidos programados en la ventana de las próximas 20 horas.</i>"
        )
        enviar_mensaje_telegram(mensaje)
        print("Finalizado: Sin partidos hoy.")
        return

    enviar_mensaje_telegram(f"🏀 <b>PRONÓSTICOS BALONCESTO VIP</b> | Fecha: <b>{fecha_colombia}</b>")
    partidos_enviados = 0

    for p in partidos:
        time.sleep(5)  # Pausa preventiva entre partidos

        analisis = analizar_partido_baloncesto_ia(p)

        if not analisis:
            print(f"No se pudo obtener análisis de IA para {p['local']} vs {p['visitante']}. Descartado.")
            continue

        prob_max = max(analisis.get("prob_pick_principal", 0), analisis.get("prob_cobertura", 0))

        # FILTRO ESTRICTO DE CERTEZA (MÍNIMO 70%)
        if prob_max < UMBRAL_MINIMO_FILTRO:
            continue

        mensaje = (
            f"🏀 <b>{p['liga']}</b> | {p['local']} vs {p['visitante']}\n"
            f"⏰ <b>Hora:</b> <code>{p['hora']}</code> | <b>Cuotas:</b> <code>{p['cuota_local']} - {p['cuota_visitante']}</code>\n"
            f"🎯 <b>Línea de Puntos:</b> <code>{p['linea_total']}</code>\n\n"
            f"🎯 <b>APUESTA PRINCIPAL: {analisis['pick_principal']}</b>\n"
            f"📊 <b>Probabilidad:</b> <code>{analisis['prob_pick_principal']}%</code> | <b>Stake:</b> <code>{analisis['stake_principal']}</code>\n"
            f"💡 <i>[Gemini] {analisis['analisis_tactico']}</i>\n\n"
            f"🛡️ <b>COBERTURA ALTERNATIVA:</b> {analisis['pick_cobertura']} (<code>{analisis['prob_cobertura']}%</code>)"
        )

        enviar_mensaje_telegram(mensaje)
        partidos_enviados += 1
        time.sleep(3)

    enviar_mensaje_telegram(f"<b>Escaneo baloncesto completado.</b> Pronósticos enviados: {partidos_enviados}")
    print(f"Proceso baloncesto completado. Enviados: {partidos_enviados}")

if __name__ == "__main__":
    ejecutar_escaneo()
