import json
import os
from datetime import datetime

STORAGE_DIR = "storage"

def init_storage():
    """Создает директорию для долгосрочного хранения, если ее нет."""
    if not os.path.exists(STORAGE_DIR):
        os.makedirs(STORAGE_DIR)

def log_changes(competitor_name, changes):
    """Дописывает историю изменений в лог-файл конкурента."""
    init_storage()
    file_path = os.path.join(STORAGE_DIR, f"{competitor_name.lower().replace(' ', '_')}_changes.jsonl")
    timestamp = datetime.now().isoformat()
    
    with open(file_path, "a", encoding="utf-8") as f:
        for change in changes:
            log_entry = {
                "timestamp": timestamp,
                "competitor": competitor_name,
                "market": "PL",
                "currency": "PLN",
                **change
            }
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")