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

# Nombres oficiales activos en Google GenAI SDK (Evita error 404/503)
MODELOS_GEMINI = ['gemini-2.5-flash', 'gemini-2.0-flash']

# Ligas de Baloncesto ampliadas para evitar vacíos de calendario
LIGAS_BASKETBALL = [
    {"nombre": "🇪🇺 EuroLeague", "sport_key": "basketball_euroleague"},
    {"nombre": "🇪🇸 Liga ACB", "sport_key": "basketball_spain_acb"},
    {"nombre": "🏀 NBA", "sport_key": "basketball_nba"},
    {"nombre": "🇩🇪 BBL Alemania", "sport_key": "basketball_germany_bbl"},
    {"nombre": "🇫🇷 LNB Francia", "sport_key": "basketball_france_lnb"},
    {"nombre": "🎓 NCAAB", "sport_key": "basketball_ncaab"}
]

class AnalisisBaloncestoSchema(BaseModel):
    prob_pick_principal: float = Field(description="Probabilidad calculada/validada para la opción principal (0 a 100)")
    pick_principal: str = Field(description="Nombre del mercado con mayor certeza (ej. Over 162.5 Puntos, Gana Real Madrid -4.5)")
    stake_principal: str = Field(description="Stake sugerido según la probabilidad (ej. 4/5)")
    prob_cobertura: float = Field(description="Probabilidad estimada de la opción de cobertura (0 a 100)")
    pick_cobertura: str = Field(description="Nombre de la opción de cobertura o alternativa (ej. Hándicap Visitante +6.5)")
    analisis_tactico: str = Field(description="Justificación técnica sintética basada en ritmo, ataque/defensa y valor de cuota en máximo 2 oraciones.")

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
# 2. MODELO MATEMÁTICO CUANTITATIVO (MOTOR LOCAL)
# ---------------------------------------------------------
def calcular_probabilidad_implicita(cuota_local, cuota_visitante):
    """Calcula probabilidad matemática desmargina (sin la comisión de la casa)."""
    if not cuota_local or not cuota_visitante:
        return 50.0, 50.0
    prob_raw_local = 1.0 / cuota_local
    prob_raw_visitante = 1.0 / cuota_visitante
    margen = prob_raw_local + prob_raw_visitante
    prob_net_local = round((prob_raw_local / margen) * 100, 1)
    prob_net_visitante = round((prob_raw_visitante / margen) * 100, 1)
    return prob_net_local, prob_net_visitante

# ---------------------------------------------------------
# 3. INGESTA DE CUOTAS (VENTANA 36 HORAS)
# ---------------------------------------------------------
def obtener_partidos_baloncesto():
    if not ODDS_API_KEY:
        print("Error: ODDS_API_KEY no está configurada.")
        return []

    lista_partidos = []
    ahora_utc = datetime.now(timezone.utc)
    limite_jornada = ahora_utc + timedelta(hours=36)

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
                    dt_colombia = dt_utc.astimezone(ZONA_HORARIA_COLOMBIA)
                    fecha_fmt = dt_colombia.strftime("%Y-%m-%d")
                    hora_fmt = dt_colombia.strftime("%H:%M")
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

                # Cálculo de motor matemático cuantitativo previo
                p_local_math, p_visitante_math = calcular_probabilidad_implicita(cuota_local, cuota_visitante)

                lista_partidos.append({
                    "liga": liga["nombre"],
                    "local": home_team,
                    "visitante": away_team,
                    "fecha": fecha_fmt,
                    "hora": hora_fmt,
                    "cuota_local": cuota_local,
                    "cuota_visitante": cuota_visitante,
                    "prob_math_local": p_local_math,
                    "prob_math_visitante": p_visitante_math,
                    "linea_total": linea_total if linea_total else "N/A",
                    "cuota_over": cuota_over if cuota_over else "N/A",
                    "cuota_under": cuota_under if cuota_under else "N/A"
                })
            time.sleep(0.4)
        except Exception as e:
            print(f"Error al consultar {liga['nombre']}:", e)

    return lista_partidos

# ---------------------------------------------------------
# 4. EVALUACIÓN CON GEMINI IA (CON RETRIES Y MODELOS OFICIALES)
# ---------------------------------------------------------
def analizar_partido_baloncesto_ia(partido):
    if not client_gemini:
        return None

    prompt = (
        f"Analiza cuantitativa y tácticamente el partido: {partido['local']} vs {partido['visitante']} ({partido['liga']}).\n"
        f"Datos de Mercado: Cuotas Ganador Local ({partido['cuota_local']}) / Visitante ({partido['cuota_visitante']}).\n"
        f"Probabilidades Implícitas Matemáticas: Local ({partido['prob_math_local']}%) vs Visitante ({partido['prob_math_visitante']}%).\n"
        f"Línea de Puntos Total: {partido['linea_total']} (Over {partido['cuota_over']} / Under {partido['cuota_under']}).\n\n"
        f"Instrucciones: Evalúa el valor real del partido combinando la estimación matemática previa con el ritmo defensivo/ofensivo. "
        f"Establece en 'pick_principal' la alternativa con mayor probabilidad/certeza (mínimo 70%) y asigna su probabilidad exacta."
    )

    tiempos_espera = [6, 12]

    for modelo in MODELOS_GEMINI:
        for intento in range(2):
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
                print(f"Aviso en {modelo} (Intento {intento+1}). Esperando {espera}s... Error: {e}")
                time.sleep(espera)

    return None

# ---------------------------------------------------------
# 5. ORQUESTADOR PRINCIPAL
# ---------------------------------------------------------
def ejecutar_escaneo():
    fecha_colombia = datetime.now(ZONA_HORARIA_COLOMBIA).strftime("%Y-%m-%d")
    print(f"Iniciando escaneo optimizado de Baloncesto (Ventana 36h): {fecha_colombia}")

    partidos = obtener_partidos_baloncesto()

    if not partidos:
        mensaje = (
            f"🏀 <b>REPORTE BALONCESTO - {fecha_colombia}</b>\n\n"
            f"<i>Sin partidos programados en la ventana de las próximas 36 horas.</i>"
        )
        enviar_mensaje_telegram(mensaje)
        print("Finalizado: Sin partidos en la ventana actual.")
        return

    enviar_mensaje_telegram(f"🏀 <b>PRONÓSTICOS BALONCESTO VIP</b> | Escaneo: <b>{fecha_colombia}</b>")
    partidos_enviados = 0

    for p in partidos:
        time.sleep(4)  # Pausa preventiva entre análisis

        analisis = analizar_partido_baloncesto_ia(p)

        if not analisis:
            print(f"No se pudo obtener análisis de IA para {p['local']} vs {p['visitante']}. Descartado.")
            continue

        prob_principal = analisis.get("prob_pick_principal", 0)
        prob_cobertura = analisis.get("prob_cobertura", 0)
        prob_max = max(prob_principal, prob_cobertura)

        # FILTRO ESTRICTO DE CERTEZA (MÍNIMO 70%)
        if prob_max < UMBRAL_MINIMO_FILTRO:
            continue

        mensaje = (
            f"🏀 <b>{p['liga']}</b> | {p['local']} vs {p['visitante']}\n"
            f"📅 <b>Fecha:</b> <code>{p['fecha']}</code> | ⏰ <b>Hora:</b> <code>{p['hora']}</code>\n"
            f"💰 <b>Cuotas:</b> <code>{p['cuota_local']} - {p['cuota_visitante']}</code> | 🎯 <b>Línea:</b> <code>{p['linea_total']}</code>\n\n"
            f"🎯 <b>APUESTA PRINCIPAL: {analisis['pick_principal']}</b>\n"
            f"📊 <b>Probabilidad:</b> <code>{analisis['prob_pick_principal']}%</code> | <b>Stake:</b> <code>{analisis['stake_principal']}</code>\n"
            f"💡 <i>[Gemini] {analisis['analisis_tactico']}</i>\n\n"
            f"🛡 <b>COBERTURA ALTERNATIVA:</b> {analisis['pick_cobertura']} (<code>{analisis['prob_cobertura']}%</code>)"
        )

        enviar_mensaje_telegram(mensaje)
        partidos_enviados += 1
        time.sleep(2)

    enviar_mensaje_telegram(f"<b>Escaneo baloncesto completado.</b> Pronósticos enviados: {partidos_enviados}")
    print(f"Proceso baloncesto completado. Enviados: {partidos_enviados}")

if __name__ == "__main__":
    ejecutar_escaneo()
