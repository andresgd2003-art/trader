import os
import requests
import logging

logger = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        self.enabled = bool(self.token and self.chat_id)
        
        if self.enabled:
            logger.info("[Notifier] Telegram activado correctamente.")
        else:
            logger.info("[Notifier] Telegram desactivado (Faltan variables TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en .env).")

    def send_message(self, text: str):
        if not self.enabled:
            return
        
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}
        try:
            res = requests.post(url, json=payload, timeout=5)
            if res.status_code != 200:
                logger.error(f"[Notifier] Error devolucion Telegram HTTP {res.status_code}: {res.text}")
        except Exception as e:
            logger.error(f"[Notifier] Error conectando a Telegram: {e}")
