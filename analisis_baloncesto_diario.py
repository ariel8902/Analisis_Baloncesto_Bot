import os
import sys
import json
import time
import requests
from typing import List, Optional
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

# ---------------------------------------------------------
# 1. CONFIGURACIÓN Y VARIABLES DE ENTORNO
# ---------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID or not GEMINI_API_KEY:
    print("❌ ERROR: Faltan variables de entorno esenciales (Telegram o Gemini).")
    sys.exit(1)

client = genai.Client(api_key=GEMINI_API_KEY)

# ---------------------------------------------------------
# 2. ESQUEMA PYDANTIC (ESTRUCTURA DE SALIDA STRICT)
# ---------------------------------------------------------
class ApuestaBaloncesto(BaseModel):
    equipo_local: str = Field(description="Nombre del equipo local")
    equipo_visitante: str = Field(description="Nombre del equipo visitante")
    liga_torneo: str = Field(description="Nombre de la liga o torneo (ej. NBA, Euroliga, Liga Endesa)")
    hora_partido: str = Field(description="Hora del partido para la jornada de hoy")
    
    apuesta_principal: str = Field(description="Pronóstico principal (ej. Ganador Directo, Hándicap -4.5, Total Puntos Over 215.5)")
    probabilidad_principal: float = Field(description="Porcentaje de probabilidad estimada (Debe ser >= 75.0)")
    stake_principal: float = Field(description="Stake recomendado de 1.0 a 5.0")
    analisis_gemini: str = Field(description="Análisis técnico contextual basado en rendimiento actual, bajas clave y ventaja de localía")
    
    cobertura_alternativa: Optional[str] = Field(None, description="Apuesta de cobertura o alternativa secundaria")
    probabilidad_cobertura: Optional[float] = Field(None, description="Probabilidad de la cobertura (ej. 68.0%)")

class ListaApuestasBaloncesto(BaseModel):
    partidos_analizados: List[ApuestaBaloncesto]

# ---------------------------------------------------------
# 3. OBTENCIÓN DE DATOS DE PARTIDOS DEL DÍA
# ---------------------------------------------------------
def obtener_partidos_odds():
    """Obtiene los partidos y cuotas actualizados desde The Odds API para el día de hoy."""
    if not ODDS_API_KEY:
        print("⚠️ No hay ODDS_API_KEY configurada. Se usará escaneo por conocimiento de Gemini para hoy.")
        return "Analizar partidos principales de NBA, Euroliga y Ligas Top agendados EXCLUSIVAMENTE para la jornada de hoy."
    
    url = f"https://api.the-odds-api.com/v4/sports/basketball_nba/odds/?apiKey={ODDS_API_KEY}&regions=us&markets=h2h,spreads,totals"
    try:
        res = requests.get(url, timeout=12)
        if res.status_code == 200:
            data = res.json()
            return json.dumps(data[:12], ensure_ascii=False)
        print(f"⚠️ Respuesta no esperada de Odds API: Status {res.status_code}")
        return "Analizar partidos principales de NBA, Euroliga y Ligas Top EXCLUSIVAMENTE para la jornada de hoy."
    except Exception as e:
        print(f"⚠️ Error al conectar con Odds API: {e}")
        return "Analizar partidos principales de NBA, Euroliga y Ligas Top EXCLUSIVAMENTE para la jornada de hoy."

# ---------------------------------------------------------
# 4. ENVÍO DE NOTIFICACIONES A TELEGRAM
# ---------------------------------------------------------
def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        return res.status_code == 200
    except Exception as e:
        print(f"❌ Error enviando a Telegram: {e}")
        return False

# ---------------------------------------------------------
# 5. MOTOR PRINCIPAL DE ANÁLISIS
# ---------------------------------------------------------
def ejecutar_analisis():
    print("🏀 Iniciando escaneo de Baloncesto Prepartido (Jornada Hoy)...")
    datos_entrada = obtener_partidos_odds()

    prompt = f"""
    Eres un analista cuantitativo y experto en apuestas deportivas de Baloncesto (NBA, Euroliga, Liga Endesa, NCAA).
    
    REGLAS ESTRICTAS DE FILTRADO Y TIEMPO:
    1. EXCLUSIVIDAD DEL DÍA ACTUAL: Analiza ÚNICAMENTE los partidos agendados para la JORNADA DE HOY. No incluyas partidos de días posteriores.
    2. FILTRO DE CERTEZA EXIGENTE: Selecciona ÚNICAMENTE pronósticos donde la probabilidad estimada sea IGUAL O SUPERIOR AL 75.0% (>= 75.0%).
    3. FACTORES TÁCTICOS: Pondera rendimiento general de la temporada, bajas de jugadores clave, ventaja de localía y ritmo de juego (Pace).
    4. MÁXIMO DE PRONÓSTICOS: Selecciona como máximo los 8 mejores partidos del día.
    5. SINCERIDAD: Si ningún partido de la jornada de hoy alcanza el 75% de certeza, retorna una lista vacía.

    Datos recibidos/referencia de la jornada:
    {datos_entrada}
    """

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ListaApuestasBaloncesto,
                temperature=0.2,
            ),
        )

        if not response.text:
            print("⚠️ Gemini no retornó datos.")
            return

        resultado: ListaApuestasBaloncesto = ListaApuestasBaloncesto.model_validate_json(response.text)
        partidos = resultado.partidos_analizados

        if not partidos:
            print("ℹ️ Ningún partido de baloncesto de la jornada de hoy superó el umbral del 75% de probabilidad.")
            return

        print(f"✅ Se encontraron {len(partidos)} partidos de HOY con alta certeza (>= 75%). Enviando a Telegram...")

        for i, p in enumerate(partidos, 1):
            mensaje = (
                f"🏀 *BALONCESTO PREPARTIDO (HOY)*\n"
                f"⚔️ *{p.equipo_local} vs {p.equipo_visitante}*\n"
                f"🏆 *Liga:* {p.liga_torneo} | ⏰ *Hora:* {p.hora_partido}\n\n"
                f"🎯 *APUESTA PRINCIPAL:* {p.apuesta_principal}\n"
                f"📊 *Probabilidad:* `{p.probabilidad_principal:.1f}%` | 💵 *Stake:* `{p.stake_principal:.1f}/5`\n\n"
                f"💡 *[Gemini Contextual]:* {p.analisis_gemini}\n"
            )

            if p.cobertura_alternativa and p.probabilidad_cobertura:
                mensaje += f"\n🛡 *COBERTURA ALTERNATIVA:* {p.cobertura_alternativa} (`{p.probabilidad_cobertura:.1f}%`)"

            enviar_telegram(mensaje)
            print(f" Envilado a Telegram ({i}/{len(partidos)}): {p.equipo_local} vs {p.equipo_visitante}")
            time.sleep(2)  # Control de tasa para la API de Telegram

        print("🚀 Proceso de Baloncesto completado con éxito.")

    except Exception as e:
        print(f"❌ Error procesando el análisis de Baloncesto: {e}")
        sys.exit(1)

if __name__ == "__main__":
    ejecutar_analisis()
