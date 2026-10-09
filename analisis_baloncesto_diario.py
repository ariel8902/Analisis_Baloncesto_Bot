import os
import json
import time
import asyncio
from datetime import datetime, timezone, timedelta
import requests
from playwright.async_api import async_playwright

# ---------------------------------------------------------
# 1. CONFIGURACIÓN Y CREDENCIALES
# ---------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

UMBRAL_MINIMO_FILTRO = 75.0
PISO_MINIMO_CUOTA = 1.40
ZONA_HORARIA_COLOMBIA = timezone(timedelta(hours=-5))

MODELO_GEMINI = "gemini-3.8-flash"
BETPLAY_BASKETBALL_URL = "https://betplay.com.co/apuestas#sports-hub/basketball"

def enviar_mensaje_telegram(texto):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Error: Credenciales de Telegram no configuradas.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": texto, "parse_mode": "HTML"}
    try:
        res = requests.post(url, json=payload, timeout=15)
        return res.status_code == 200
    except Exception as e:
        print("Error enviando mensaje a Telegram:", e)
        return False

async def extraer_cuotas_scraping_betplay():
    """Navega visualmente la web de BetPlay con Playwright y extrae el texto puro"""
    lista_partidos = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-blink-features=AutomationControlled',
                '--window-size=1920,1080'
            ]
        )
        
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={'width': 1920, 'height': 1080},
            locale='es-CO',
            timezone_id='America/Bogota'
        )
        
        page = await context.new_page()

        print(f"Abriendo navegador e ingresando a BetPlay: {BETPLAY_BASKETBALL_URL}")
        try:
            await page.goto(BETPLAY_BASKETBALL_URL, wait_until="networkidle", timeout=60000)
            
            try:
                await page.wait_for_selector('.KambiBC-mod-event-line, [class*="event-item"]', timeout=15000)
            except Exception:
                print("Tiempo de espera agotado buscando selectores de Kambi, intentando lectura general...")

            await page.evaluate("window.scrollBy(0, 800)")
            await page.wait_for_timeout(4000)

            event_cards = await page.query_selector_all('.KambiBC-mod-event-line, .KambiBC-event-item')
            print(f"Eventos detectados en pantalla: {len(event_cards)}")

            for idx, card in enumerate(event_cards[:20]):
                try:
                    texto_card = await card.inner_text()
                    lineas = [l.strip() for l in texto_card.split('\n') if l.strip()]

                    if idx < 3:
                        print(f"--- DEBUG TARJETA {idx+1} ---")
                        print(lineas)

                    cuotas = []
                    for item in lineas:
                        try:
                            val = float(item.replace(',', '.'))
                            if 1.01 <= val <= 30.0:
                                cuotas.append(val)
                        except ValueError:
                            pass

                    if len(cuotas) >= 2:
                        c_loc = cuotas[0]
                        c_vis = cuotas[1]

                        nombres = [
                            x for x in lineas 
                            if not any(char.isdigit() for char in x) 
                            and x.lower() not in ["más", "menos", "hándicap", "total", "ganador", "1", "2", "vs"]
                            and len(x) > 2
                        ]

                        if len(nombres) >= 2:
                            home_team = nombres[0]
                            away_team = nombres[1]

                            dt_colombia = datetime.now(ZONA_HORARIA_COLOMBIA)
                            prob_impl_home = (1 / c_loc) / ((1 / c_loc) + (1 / c_vis))
                            prob_impl_away = (1 / c_vis) / ((1 / c_loc) + (1 / c_vis))

                            lista_partidos.append({
                                "liga": "🏀 Baloncesto BetPlay",
                                "equipo_local": home_team,
                                "equipo_visitante": away_team,
                                "fecha": dt_colombia.strftime("%Y-%m-%d"),
                                "hora": dt_colombia.strftime("%I:%M %p"),
                                "cuota_local": c_loc,
                                "cuota_visita": c_vis,
                                "prob_real_local": round(prob_impl_home * 100, 1),
                                "prob_real_visita": round(prob_impl_away * 100, 1),
                                "spread_point": "N/A",
                                "total_point": "N/A"
                            })
                except Exception as err_card:
                    continue

        except Exception as e:
            print("Error durante la navegación con Playwright:", e)
        finally:
            await browser.close()

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
            res = requests.post(url, headers=headers, json=payload, timeout=60)
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
    prompt_triangulacion = (
        f"EVALUACIÓN DE TRIANGULACIÓN DE BALONCESTO SCRAPING NATIVO ({p['equipo_local']} vs {p['equipo_visitante']} - {p['liga']}):\n\n"
        f"DATOS EXTRAÍDOS DE PANTALLA BETPLAY:\n"
        f"- LOCAL: {p['equipo_local']} -> CUOTA BETPLAY: {p['cuota_local']}\n"
        f"- VISITANTE: {p['equipo_visitante']} -> CUOTA BETPLAY: {p['cuota_visita']}\n\n"
        f"REGLA OBLIGATORIA DE ASIGNACIÓN:\n"
        f"1. Si tu pronóstico es la victoria de {p['equipo_local']}, la 'cuota_evaluada' DEBE SER EXACTAMENTE {p['cuota_local']}.\n"
        f"2. Si tu pronóstico es la victoria de {p['equipo_visitante']}, la 'cuota_evaluada' DEBE SER EXACTAMENTE {p['cuota_visita']}.\n"
        f"3. ESTÁ ABSOLUTAMENTE PROHIBIDO INTERCAMBIAR LAS CUOTAS.\n"
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
    print(f"Iniciando escaneo Scraping Nativo Playwright (BetPlay): {fecha_hora_col}")
    
    partidos = asyncio.run(extraer_cuotas_scraping_betplay())

    if not partidos:
        msg = f"🏀 <b>REPORTE BALONCESTO BETPLAY (SCRAPING)</b>\n<i>Escaneo: {fecha_hora_col}</i>\n\n<i>Sin partidos cargados en la pantalla de BetPlay en este momento.</i>"
        enviar_mensaje_telegram(msg)
        return

    enviar_mensaje_telegram(f"🏀 <b>PRONÓSTICOS BALONCESTO VIP (SCRAPING BETPLAY)</b>\n<i>Escaneo: {fecha_hora_col}</i>")

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
