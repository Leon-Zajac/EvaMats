import os
import requests
import logging
from dotenv import load_dotenv

# Загружаем переменные из .env файла
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram_message(message: str):
    """Отправляет текстовое сообщение в Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Ключи Telegram не найдены. Сообщение не отправлено.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"  # Позволяет использовать жирный текст и курсив
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        logging.info("✅ Уведомление успешно отправлено в Telegram.")
    except Exception as e:
        logging.error(f"❌ Ошибка при отправке в Telegram: {e}")