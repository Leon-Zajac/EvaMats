import sqlite3
import json
import os
import logging

# Папка для базы данных
os.makedirs("data", exist_ok=True)
DB_PATH = "data/tracking.db"

def init_db():
    """Создает таблицу для хранения состояния конкурентов, если её еще нет."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS state (
                competitor TEXT PRIMARY KEY,
                data JSON
            )
        ''')
    logging.info("База данных SQLite инициализирована.")

def save_state(competitor: str, data: list):
    """Сохраняет или обновляет данные конкурента в БД."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
            INSERT OR REPLACE INTO state (competitor, data) 
            VALUES (?, ?)
        ''', (competitor, json.dumps(data, ensure_ascii=False)))

def load_state(competitor: str) -> list:
    """Загружает предыдущее состояние конкурента из БД."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute('SELECT data FROM state WHERE competitor = ?', (competitor,))
        row = cursor.fetchone()
        return json.loads(row[0]) if row else []