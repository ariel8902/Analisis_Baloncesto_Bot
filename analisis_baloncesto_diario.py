async def extraer_cuotas_scraping_betplay():
    """Navega visualmente la web de BetPlay con Playwright en modo headless"""
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
            # Cargar la página y esperar que el contenedor principal de Kambi renderice
            await page.goto(BETPLAY_BASKETBALL_URL, wait_until="domcontentloaded", timeout=60000)
            
            # Esperar a que el contenedor dinámico de eventos cargue sus elementos
            await page.wait_for_timeout(8000)

            # Hacer scroll hacia abajo para forzar la carga lazy-loading de los partidos
            await page.evaluate("window.scrollBy(0, 1500)")
            await page.wait_for_timeout(3000)

            # Capturar los bloques de eventos específicos del DOM de Kambi
            event_cards = await page.query_selector_all('[class*="KambiBC-event-item__event-wrapper"], .KambiBC-event-item, [class*="event-item"]')
            print(f"Eventos detectados en pantalla: {len(event_cards)}")

            for card in event_cards:
                try:
                    texto_card = await card.inner_text()
                    lineas = [l.strip() for l in texto_card.split('\n') if l.strip()]

                    # Validar que existan cuotas numéricas y nombres de equipos en el bloque
                    cuotas = []
                    equipos = []

                    for item in lineas:
                        # Identificar cuotas numéricas
                        try:
                            val = float(item.replace(',', '.'))
                            if 1.01 <= val <= 30.0:
                                cuotas.append(val)
                            continue
                        except ValueError:
                            pass
                        
                        # Excluir etiquetas del sistema y conservar nombres de equipos
                        if item.lower() not in ["más", "menos", "hándicap", "total", "ganador", "vs", "1", "2"] and len(item) > 2:
                            if not any(char.isdigit() for char in item):
                                equipos.append(item)

                    if len(equipos) >= 2 and len(cuotas) >= 2:
                        home_team = equipos[0]
                        away_team = equipos[1]
                        c_loc = cuotas[0]
                        c_vis = cuotas[1]

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
