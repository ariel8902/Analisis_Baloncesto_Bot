import os
import sys
import json
import requests
from google import genai

# 1. Configuración de variables de entorno
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID or not GEMINI_API_KEY:
    print("❌ ERROR: Faltan variables de entorno esenciales (Telegram o Gemini).")
    sys.exit(1)

# 2. Inicializar Cliente de Gemini
client = genai.Client(api_key=GEMINI_API_KEY)

def obtener_partidos_odds():
    """Obtiene la cartelera de baloncesto del día si ODDS_API_KEY está presente."""
    if not ODDS_API_KEY:
        return "No hay API Key configurada para Odds API. Analizar jornadas principales de NBA / Euroliga de hoy."
    
    url = f"https://api.the-odds-api.com/v4/sports/basketball_nba/odds/?apiKey={ODDS_API_KEY}&regions=us&markets=h2h,spreads,totals"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return json.dumps(data[:10], ensure_ascii=False)
        return "Analizar jornadas principales de NBA / Euroliga del día."
    except Exception as e:
        print(f"⚠️ Advertencia obteniendo datos de Odds API: {e}")
        return "Analizar jornadas principales de NBA / Euroliga del día."

def enviar_telegram(mensaje):
    """Envía la notificación formateada a Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje,
        "parse_mode": "Markdown"
    }
    res = requests.post(url, json=payload, timeout=10)
    return res.status_code == 200

def ejecutar_analisis():
    print("🏀 Iniciando análisis diario de Baloncesto...")
    datos_partidos = obtener_partidos_odds()

    prompt = f"""
    Eres un analista experto en Baloncesto (NBA, Euroliga, Liga Endesa).
    Analiza los partidos más relevantes agendados para la jornada de hoy basándote en la siguiente información de entrada o en tu conocimiento actualizado:
    {datos_partidos}

    REGLAS ESTRICTAS:
    1. Filtra solo pronósticos con una PROBABILIDAD DE CERTEZA IGUAL O SUPERIOR AL 75% (>= 75.0%).
    2. Si ningún partido supera el 75%, no inventes datos.
    3. Analiza rendimiento reciente, racha de los últimos 10 partidos, bajas clave y ventajas de localía.
    4. Formatea la respuesta en Markdown limpio para Telegram con este estilo:

    🏀 *APUESTA PRINCIPAL BALONCESTO*
    ⚔️ *[Local] vs [Visitante]*
    🏆 *Competición:* [Nombre Liga]
    🎯 *Pronóstico:* [Ganador / Hándicap / Total Puntos]
    📊 *Probabilidad:* [X]% | *Stake:* [X]/5
    💡 *[Gemini Contextual]:* [Análisis conciso de racha y ventaja]
    """

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        analisis_texto = response.text

        if analisis_texto:
            print("✅ Análisis generado correctamente. Enviando a Telegram...")
            if enviar_telegram(analisis_texto):
                print("🚀 Mensaje enviado con éxito a Telegram.")
            else:
                print("❌ Error al enviar el mensaje a Telegram.")
        else:
            print("⚠️ Gemini no retornó contenido.")

    except Exception as e:
        print(f"❌ Error durante la generación del análisis: {e}")
        sys.exit(1)

if __name__ == "__main__":
    ejecutar_analisis()
