import os
import time
import threading
import requests
import logging
from collections import deque

logger = logging.getLogger(__name__)

class TelegramNotifier:
    """
    Notifier no-bloqueante: encola mensajes y los emite desde un thread daemon.
    Throttle de 25 msg/min (Telegram permite 30) para evitar 429.
    Drop silencioso si la cola excede 200 (overflow protection).
    """
    MAX_QUEUE = 200
    RATE_LIMIT_PER_MIN = 25

    def __init__(self):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        self.enabled = bool(self.token and self.chat_id)
        self._queue: deque = deque()
        self._lock = threading.Lock()
        self._sent_ts: deque = deque(maxlen=self.RATE_LIMIT_PER_MIN)

        if self.enabled:
            t = threading.Thread(target=self._worker, daemon=True, name="TelegramNotifier")
            t.start()
            logger.info("[Notifier] Telegram activado correctamente.")
        else:
            logger.info("[Notifier] Telegram desactivado (Faltan variables TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en .env).")

    def send_message(self, text: str):
        if not self.enabled:
            return
        with self._lock:
            if len(self._queue) >= self.MAX_QUEUE:
                self._queue.popleft()
            self._queue.append(text)

    def send_message_sync(self, text: str):
        """Alias for send_message — non-blocking enqueue, safe to call from sync contexts."""
        self.send_message(text)

    def _worker(self):
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        while True:
            try:
                with self._lock:
                    msg = self._queue.popleft() if self._queue else None
                if msg is None:
                    time.sleep(0.5)
                    continue
                now = time.time()
                while self._sent_ts and now - self._sent_ts[0] > 60:
                    self._sent_ts.popleft()
                if len(self._sent_ts) >= self.RATE_LIMIT_PER_MIN:
                    time.sleep(60 - (now - self._sent_ts[0]) + 0.1)
                    continue
                payload = {"chat_id": self.chat_id, "text": msg, "parse_mode": "HTML"}
                try:
                    res = requests.post(url, json=payload, timeout=5)
                    self._sent_ts.append(time.time())
                    if res.status_code == 429:
                        retry = int(res.json().get("parameters", {}).get("retry_after", 5))
                        logger.warning(f"[Notifier] 429 Telegram. Retry en {retry}s")
                        time.sleep(retry)
                        with self._lock:
                            self._queue.appendleft(msg)
                    elif res.status_code != 200:
                        logger.error(f"[Notifier] HTTP {res.status_code}: {res.text[:200]}")
                except requests.RequestException as e:
                    logger.error(f"[Notifier] Net error: {e}")
                    time.sleep(2)
            except Exception as e:
                logger.error(f"[Notifier] Worker exc: {e}")
                time.sleep(1)
