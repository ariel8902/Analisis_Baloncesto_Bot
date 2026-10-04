import os
import sys
import json
import time
import requests
from datetime import datetime, timedelta, timezone
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
    print("❌ ERROR CRÍTICO: Faltan credenciales esenciales (Telegram o Gemini API).")
    sys.exit(1)

client = genai.Client(api_key=GEMINI_API_KEY)

# ---------------------------------------------------------
# 2. ESQUEMA ESTRUCTURADO PYDANTIC (STRICT OUTPUTS)
# ---------------------------------------------------------
class ApuestaBaloncesto(BaseModel):
    equipo_local: str = Field(description="Nombre oficial del equipo local")
    equipo_visitante: str = Field(description="Nombre oficial del equipo visitante")
    liga_torneo: str = Field(description="Competición (ej. NBA, Euroliga, Liga Endesa)")
    hora_partido_colombia: str = Field(description="Hora programada convertida a Hora Colombia (ej. 06:00 PM)")
    fecha_partido: str = Field(description="Fecha del partido en formato DD/MM/AAAA")
    
    puntos_estimados_local: float = Field(description="Puntos esperados del equipo local")
    puntos_estimados_visitante: float = Field(description="Puntos esperados del equipo visitante")
    
    apuesta_principal: str = Field(description="Pronóstico principal (ej. Ganador Directo, Hándicap -4.5)")
    probabilidad_principal: float = Field(description="Porcentaje de probabilidad del modelo")
    stake_principal: float = Field(description="Stake sugerido de 1.0 a 5.0")
    
    nivel_confianza: str = Field(description="'ALTA CERTEZA (>=75%)' o 'RIESGO MODERADO (<75%)'")
    analisis_resumido: str = Field(description="Breve análisis contextual táctico de máximo 3 frases concisas")
    cobertura_alternativa: Optional[str] = Field(None, description="Línea de cobertura o protección secundaria")
    probabilidad_cobertura: Optional[float] = Field(None, description="Probabilidad de la cobertura")

class ListaApuestasBaloncesto(BaseModel):
    partidos_analizados: List[ApuestaBaloncesto]

# ---------------------------------------------------------
# 3. OBTENCIÓN DE DATOS Y CONVERSIÓN HORARIA
# ---------------------------------------------------------
def obtener_datos_deportes_api():
    utc_now = datetime.now(timezone.utc)
    colombia_now = utc_now - timedelta(hours=5)
    fecha_hoy_str = colombia_now.strftime("%Y-%m-%d")

    if not ODDS_API_KEY:
        print("⚠️ ODDS_API_KEY no detectada. Usando escaneo contextual.")
        return f"Analizar partidos principales de NBA y Ligas Top para la jornada de hoy ({fecha_hoy_str})."

    url = f"https://api.the-odds-api.com/v4/sports/basketball_nba/odds/?apiKey={ODDS_API_KEY}&regions=us&markets=h2h,spreads,totals"
    try:
        response = requests.get(url, timeout=12)
        if response.status_code == 200:
            partidos = response.json()
            print(f"📊 Datos cargados de la API ({len(partidos)} eventos detectados). Fecha Col: {fecha_hoy_str}")
            return json.dumps(partidos[:12], ensure_ascii=False)
        else:
            print(f"⚠️ Error en API Deporte: Status {response.status_code}")
            return f"Analizar partidos principales de hoy ({fecha_hoy_str})."
    except Exception as e:
        print(f"⚠️ Fallo de conexión con la API: {e}")
        return f"Analizar partidos principales de hoy ({fecha_hoy_str})."

# ---------------------------------------------------------
# 4. ENVÍO A TELEGRAM
# ---------------------------------------------------------
def enviar_telegram(mensaje: str) -> bool:
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
        print(f"❌ Error al enviar mensaje a Telegram: {e}")
        return False

# ---------------------------------------------------------
# 5. EJECUCIÓN DEL MOTOR ANALÍTICO
# ---------------------------------------------------------
def ejecutar_analisis():
    print("🏀 [INICIO] Ejecutando motor analítico de Baloncesto...")
    datos_api = obtener_datos_deportes_api()
    
    utc_now = datetime.now(timezone.utc)
    colombia_now = utc_now - timedelta(hours=5)
    fecha_actual_col = colombia_now.strftime("%d/%m/%Y")

    prompt = f"""
    Eres un analista cuantitativo de Baloncesto profesional (NBA, Euroliga, Ligas Top).
    FECHA ACTUAL DE EVALUACIÓN (COLOMBIA): {fecha_actual_col}

    REGLAS ESTRICTAS:
    1. EXCLUSIVIDAD DE HOY: Procesa ÚNICAMENTE los partidos de la JORNADA DE HOY ({fecha_actual_col}). Descarta días futuros.
    2. HORARIO COLOMBIA: Convierte la hora del partido a Hora Colombia (UTC-5) en formato 12 horas (ej. 06:00 PM, 08:30 PM).
    3. EVALUACIÓN Y SELECCIÓN:
       - Prioriza partidos con probabilidad >= 75.0% (Nivel de confianza: 'ALTA CERTEZA (>=75%)').
       - Si ningún partido alcanza el 75%, selecciona HASTA 2 MEJORES PARTIDOS con probabilidad >= 68.0% (Nivel de confianza: 'RIESGO MODERADO (<75%)').
    4. SINTESIS CONCISA: Breve análisis contextual táctico de máximo 3 frases.

    DATOS DE ENTRADA:
    {datos_api}
    """

    try:
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ListaApuestasBaloncesto,
                temperature=0.15,
            ),
        )

        if not response.text:
            print("⚠️ Gemini API no retornó contenido.")
            return

        resultado: ListaApuestasBaloncesto = ListaApuestasBaloncesto.model_validate_json(response.text)
        partidos = resultado.partidos_analizados

        if not partidos:
            print("ℹ️ Ningún partido de baloncesto alcanzó el umbral mínimo de análisis hoy.")
            return

        print(f"✅ Se encontraron {len(partidos)} pronósticos para hoy. Enviando a Telegram...")

        for i, p in enumerate(partidos, 1):
            mensaje = (
                f"🏀 *ANÁLISIS DE BALONCESTO*\n"
                f"📅 *Fecha:* `{p.fecha_partido}` | ⏰ *Hora Col:* `{p.hora_partido_colombia}`\n"
                f"🏆 *Liga:* {p.liga_torneo} | 🛡 *Estado:* `{p.nivel_confianza}`\n"
                f"───────────────────────────\n"
                f"⚔️ *{p.equipo_local} vs {p.equipo_visitante}*\n"
                f"📊 *Proyección:* {p.equipo_local} ({p.puntos_estimados_local:.1f}) - ({p.puntos_estimados_visitante:.1f}) {p.equipo_visitante}\n\n"
                f"🎯 *APUESTA PRINCIPAL:* `{p.apuesta_principal}`\n"
                f"📈 *Probabilidad:* `{p.probabilidad_principal:.1f}%` | 💵 *Stake:* `{p.stake_principal:.1f}/5`\n"
                f"───────────────────────────\n"
                f"💡 *[Análisis Táctico]:* {p.analisis_resumido}\n"
            )

            if p.cobertura_alternativa and p.probabilidad_cobertura:
                mensaje += f"🛡 *Cobertura:* `{p.cobertura_alternativa}` (`{p.probabilidad_cobertura:.1f}%`)\n"

            enviar_telegram(mensaje)
            print(f" Enviado a Telegram ({i}/{len(partidos)}): {p.equipo_local} vs {p.equipo_visitante}")
            time.sleep(2)

        print("🚀 Proceso de Baloncesto completado con éxito.")

    except Exception as e:
        print(f"❌ Error en la ejecución del motor de Baloncesto: {e}")
        sys.exit(1)

if __name__ == "__main__":
    ejecutar_analisis()
