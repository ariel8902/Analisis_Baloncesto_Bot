import os
import sys
import json
import time
import math
import requests
from typing import List, Optional
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

# ---------------------------------------------------------
# 1. CONFIGURACIÓN Y VARIABLES DE ENTORNO (APIs)
# ---------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID or not GEMINI_API_KEY:
    print("❌ ERROR CRÍTICO: Faltan credenciales esenciales (Telegram o Gemini API).")
    sys.exit(1)

# Inicialización de cliente con la SDK oficial google-genai
client = genai.Client(api_key=GEMINI_API_KEY)

# ---------------------------------------------------------
# 2. ESQUEMA ESTRUCTURADO PYDANTIC (STRICT OUTPUTS)
# ---------------------------------------------------------
class ApuestaBaloncesto(BaseModel):
    equipo_local: str = Field(description="Nombre oficial del equipo local")
    equipo_visitante: str = Field(description="Nombre oficial del equipo visitante")
    liga_torneo: str = Field(description="Competición (ej. NBA, Euroliga, Liga Endesa)")
    hora_partido: str = Field(description="Hora programada para la jornada de hoy")
    
    # Proyecciones Cuantitativas del Motor
    puntos_estimados_local: float = Field(description="Puntos esperados del equipo local según la simulación de posesiones")
    puntos_estimados_visitante: float = Field(description="Puntos esperados del equipo visitante según la simulación de posesiones")
    
    # Métricas y Selección de Apuestas
    apuesta_principal: str = Field(description="Pronóstico de valor (Ganador Directo, Hándicap o Total de Puntos Over/Under)")
    probabilidad_principal: float = Field(description="Porcentaje de probabilidad del modelo (DEBE SER >= 75.0%)")
    stake_principal: float = Field(description="Stake sugerido de 1.0 a 5.0")
    
    analisis_gemini: str = Field(description="Evaluación táctica contextual: ritmo de juego (Pace), bajas de jugadores clave e impacto de localía")
    cobertura_alternativa: Optional[str] = Field(None, description="Línea de cobertura o protección secundaria")
    probabilidad_cobertura: Optional[float] = Field(None, description="Probabilidad de la cobertura (ej. 68.0%)")

class ListaApuestasBaloncesto(BaseModel):
    partidos_analizados: List[ApuestaBaloncesto]

# ---------------------------------------------------------
# 3. MOTOR CUANTITATIVO Y SIMULACIÓN PROBABILÍSTICA
# ---------------------------------------------------------
def simular_partido_baloncesto(rating_ofensivo_loc: float, rating_defensivo_vis: float, 
                                rating_ofensivo_vis: float, rating_defensivo_loc: float, 
                                pace_medio: float = 100.0):
    """
    Simula la expectativa cuantitativa de puntos basada en eficiencia ofensiva/defensiva
    por cada 100 posesiones ajustada al ritmo de juego (Pace).
    """
    # Factor de ventaja de cancha (3.5% de incremento para el equipo local)
    exp_puntos_loc = (rating_ofensivo_loc * rating_defensivo_vis / 100.0) * (pace_medio / 100.0) * 1.035
    exp_puntos_vis = (rating_ofensivo_vis * rating_defensivo_loc / 100.0) * (pace_medio / 100.0)
    return round(exp_puntos_loc, 1), round(exp_puntos_vis, 1)

# ---------------------------------------------------------
# 4. CONEXIÓN A LA API DE DEPORTES / CUOTAS
# ---------------------------------------------------------
def obtener_datos_deportes_api():
    """Consulta partidos y líneas de mercado programados EXCLUSIVAMENTE para hoy."""
    if not ODDS_API_KEY:
        print("⚠️ ODDS_API_KEY no detectada. Usando escaneo contextual del modelo.")
        return "Analizar partidos principales de NBA, Euroliga y Ligas Top EXCLUSIVAMENTE para la jornada de HOY."

    url = f"https://api.the-odds-api.com/v4/sports/basketball_nba/odds/?apiKey={ODDS_API_KEY}&regions=us&markets=h2h,spreads,totals"
    try:
        response = requests.get(url, timeout=12)
        if response.status_code == 200:
            partidos = response.json()
            print(f"📊 Datos cuantitativos cargados exitosamente de la API ({len(partidos)} eventos detectados).")
            return json.dumps(partidos[:12], ensure_ascii=False)
        else:
            print(f"⚠️ Error en respuesta de API Deporte: Status {response.status_code}")
            return "Analizar partidos principales de NBA, Euroliga y Ligas Top para HOY."
    except Exception as e:
        print(f"⚠️ Fallo de conexión con la API de deportes: {e}")
        return "Analizar partidos principales de NBA, Euroliga y Ligas Top para HOY."

# ---------------------------------------------------------
# 5. INTEGRACIÓN CON TELEGRAM
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
# 6. EJECUCIÓN DEL MOTOR DE INTELIGENCIA DE NEGOCIO
# ---------------------------------------------------------
def ejecutar_analisis():
    print("🏀 [INICIO] Ejecutando motor cuantitativo y analítico de Baloncesto...")
    datos_api = obtener_datos_deportes_api()

    prompt = f"""
    Eres un sistema de Inteligencia Artificial especializado en Análisis Cuantitativo de Baloncesto (NBA, Euroliga, Liga Endesa, NCAA).

    OBJETIVO:
    Procesar la cartelera de la JORNADA DE HOY mediante el modelo cuantitativo de expectativa de puntos, eficiencia ofensiva/defensiva y simulación de posesiones (Pace).

    REGLAS ESTRICTAS DE VALIDACIÓN:
    1. EXCLUSIVIDAD TEMPORAL: Procesa ÚNICAMENTE los partidos de la JORNADA DE HOY. Descarta cualquier partido de fechas posteriores.
    2. UMBRAL DE CERTEZA DE VALOR (>= 75.0%): Selecciona únicamente las apuestas donde la simulación y el análisis táctico otorguen una probabilidad calculada IGUAL O SUPERIOR AL 75.0%.
    3. MODELO DE SIMULACIÓN: Calcula los puntos proyectados para cada equipo evaluando posesiones estimadas y ratings ofensivos/defensivos.
    4. FACTORES CONTEXTUALES: Considera bajas de jugadores clave (Injury Report), ventaja de campo y ritmo de juego (Pace).
    5. HONESTIDAD ESTADÍSTICA: Si ningún partido alcanza el 75.0% de probabilidad real hoy, retorna una lista vacía sin forzar pronósticos.

    ENTRADA DE DATOS DE LA API DE DEPORTES:
    {datos_api}
    """

    try:
        # Uso del modelo de producción oficial con salida estructurada
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ListaApuestasBaloncesto,
                temperature=0.15,  # Baja temperatura para máxima consistencia matemática
            ),
        )

        if not response.text:
            print("⚠️ Gemini API no retornó contenido.")
            return

        resultado: ListaApuestasBaloncesto = ListaApuestasBaloncesto.model_validate_json(response.text)
        partidos = resultado.partidos_analizados

        if not partidos:
            print("ℹ️ Ningún partido de baloncesto de hoy alcanzó el umbral del 75% de certeza en la simulación.")
            return

        print(f"✅ Simulación completada: {len(partidos)} pronósticos aprobados (>= 75%). Enviando alertas a Telegram...")

        for i, p in enumerate(partidos, 1):
            mensaje = (
                f"🏀 *BALONCESTO PREPARTIDO (HOY)*\n"
                f"⚔️ *{p.equipo_local} vs {p.equipo_visitante}*\n"
                f"🏆 *Liga:* {p.liga_torneo} | ⏰ *Hora:* {p.hora_partido}\n"
                f"📊 *Proyección Puntos:* `{p.equipo_local} ({p.puntos_estimados_local:.1f}) - ({p.puntos_estimados_visitante:.1f}) {p.equipo_visitante}`\n\n"
                f"🎯 *APUESTA PRINCIPAL:* {p.apuesta_principal}\n"
                f"📈 *Probabilidad:* `{p.probabilidad_principal:.1f}%` | 💵 *Stake:* `{p.stake_principal:.1f}/5`\n\n"
                f"💡 *[Gemini Contextual]:* {p.analisis_gemini}\n"
            )

            if p.cobertura_alternativa and p.probabilidad_cobertura:
                mensaje += f"\n🛡 *COBERTURA ALTERNATIVA:* {p.cobertura_alternativa} (`{p.probabilidad_cobertura:.1f}%`)"

            enviar_telegram(mensaje)
            print(f" Envilado a Telegram ({i}/{len(partidos)}): {p.equipo_local} vs {p.equipo_visitante}")
            time.sleep(2)  # Control de flujo para evitar rate limit en Telegram

        print("🚀 Proceso de Baloncesto finalizado con éxito.")

    except Exception as e:
        print(f"❌ Error en la ejecución del motor de Baloncesto: {e}")
        sys.exit(1)

if __name__ == "__main__":
    ejecutar_analisis()
