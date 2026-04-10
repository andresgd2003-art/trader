import asyncio
import logging
from datetime import datetime
import pytz
import os
from alpaca.trading.client import TradingClient
from engine.notifier import TelegramNotifier

logger = logging.getLogger(__name__)

async def run_daily_summary_loop():
    """
    Loop en segundo plano que vigila el reloj cada minuto.
    A las 16:05 ET (cuando el mercado regular ha cerrado con seguridad),
    calcula el Resumen Diario de la cuenta y lo empuja a Telegram.
    """
    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
    paper = os.environ.get("PAPER_TRADING", "True").lower() == "true"
    
    # Cliente para consultar equity global (se inicializa una sola vez)
    try:
        client = TradingClient(api_key, secret_key, paper=paper)
    except Exception as e:
        logger.error(f"[DailyReporter] Error inicializando cliente: {e}")
        return

    notifier = TelegramNotifier()
    if not notifier.enabled:
        logger.warning("[DailyReporter] Telegram desactivado. El resumen no se enviará.")
        return

    ny_tz = pytz.timezone('America/New_York')

    logger.info("[DailyReporter] Loop de Resumen Diario iniciado. (Objetivo: 16:05 ET)")

    while True:
        try:
            now_ny = datetime.now(ny_tz)
            
            # Checar si es Lunes-Viernes y la hora exacta es 16:05 (4:05 PM ET)
            if now_ny.weekday() < 5 and now_ny.hour == 16 and now_ny.minute == 5:
                # Recopilar la informacion
                account = client.get_account()
                equity = float(account.equity)
                last_equity = float(account.last_equity)
                daily_pnl = equity - last_equity
                
                # Resumen de Posiciones
                positions = client.get_all_positions()
                num_positions = len(positions)
                
                # Emoji indicador
                if daily_pnl > 0:
                    status_icon = "🟢"
                    pnl_text = f"+${daily_pnl:.2f}"
                elif daily_pnl < 0:
                    status_icon = "🔴"
                    pnl_text = f"-${abs(daily_pnl):.2f}"
                else:
                    status_icon = "⚪"
                    pnl_text = "$0.00"

                mensaje = (
                    f"📊 <b>[Corte de Caja Diario - Alpaca]</b>\n"
                    f"<i>{now_ny.strftime('%Y-%m-%d')}</i>\n\n"
                    f"💳 <b>Portfolio Total:</b> ${equity:,.2f}\n"
                    f"📈 <b>P&L de Hoy:</b> {status_icon} {pnl_text}\n\n"
                    f"📌 <b>Posiciones Abiertas:</b> {num_positions}\n"
                    f"🤖 <i>Estado del Engine: Operando (OK)</i>"
                )
                
                logger.info("[DailyReporter] Disparando Resumen Diario a Telegram.")
                notifier.send_message(mensaje)
                
                # Dormir 60 segundos para evitar doble disparo en el mismo minuto
                await asyncio.sleep(60)
            else:
                # Revisar cada 20 segundos
                await asyncio.sleep(20)
                
        except Exception as e:
            logger.error(f"[DailyReporter] Error crítico en el loop de resumen: {e}")
            await asyncio.sleep(60)
