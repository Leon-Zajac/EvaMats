import sqlite3
import json
import os
import logging

# Папка для базы данных
os.makedirs("data", exist_ok=True)
DB_PATH = "data/tracking.db"

def init_db():
    """Создает таблицы для хранения состояния конкурентов и метаданных страниц."""
    with sqlite3.connect(DB_PATH) as conn:
        # Таблица общего состояния для конкурентов
        conn.execute('''
            CREATE TABLE IF NOT EXISTS state (
                competitor TEXT PRIMARY KEY,
                data JSON
            )
        ''')
        # Таблица для отслеживания дат изменения страниц из sitemap
        conn.execute('''
            CREATE TABLE IF NOT EXISTS page_metadata (
                url TEXT PRIMARY KEY,
                lastmod TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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

# --- Функции отслеживания изменений по lastmod (Sitemap) ---

def get_saved_lastmod(url: str) -> str | None:
    """Возвращает сохраненный lastmod для конкретного URL."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute('SELECT lastmod FROM page_metadata WHERE url = ?', (url,))
        row = cursor.fetchone()
        return row[0] if row else None

def save_page_lastmod(url: str, lastmod: str):
    """Сохраняет или обновляет lastmod для URL."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
            INSERT INTO page_metadata (url, lastmod) 
            VALUES (?, ?)
            ON CONFLICT(url) DO UPDATE SET lastmod = excluded.lastmod, updated_at = CURRENT_TIMESTAMP
        ''', (url, lastmod))