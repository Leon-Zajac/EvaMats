import requests
import hashlib
import json
import logging

# Настраиваем базовое логирование (требование ТЗ: агент не падает, а пишет ошибку в лог)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Получает список товаров через открытый JSON API Shopify.
def fetch_shopify_products(base_url: str) -> list:
    url = f"{base_url.rstrip('/')}/products.json"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get('products', [])
    except Exception as e:
        # Логируем ошибку, чтобы агент не падал
        logging.error(f"Ошибка при загрузке JSON с {url}: {e}")
        return []

# Оставляет только важные поля для отслеживания изменений (цены, скидки).
def extract_essential_data(products: list) -> list:
    essential_data = []
    for p in products:
        item = {
            "id": p.get("id"),
            "title": p.get("title"),
            "updated_at": p.get("updated_at"),
            "variants": [
                {
                    "title": v.get("title"),
                    "price": v.get("price"),
                    # Если compare_at_price не null, значит на товар есть скидка
                    "compare_at_price": v.get("compare_at_price"), 
                    "available": v.get("available")
                } for v in p.get("variants", [])
            ]
        }
        essential_data.append(item)
    return essential_data

# Создает хэш данных для быстрой проверки, менялось ли что-то с прошлого запуска.
def get_data_hash(data: list) -> str:
    if not data:
        return ""
    # Сортируем ключи, чтобы при одинаковых данных хэш всегда был идентичным
    json_string = json.dumps(data, sort_keys=True)
    return hashlib.md5(json_string.encode('utf-8')).hexdigest()

# Блок для быстрого тестирования кода
if __name__ == "__main__":
    target_url = "https://primeeva.pl/"
    logging.info(f"Сбор данных с {target_url}")
    
    raw_products = fetch_shopify_products(target_url)
    
    if raw_products:
        clean_data = extract_essential_data(raw_products)
        data_hash = get_data_hash(clean_data)
        
        logging.info(f"Успешно собрано товаров: {len(clean_data)}")
        logging.info(f"Хэш текущего состояния: {data_hash}")
        
        print("\n--- Пример очищенных данных первого товара ---")
        print(json.dumps(clean_data[0], indent=2, ensure_ascii=False))
        print("----------------------------------------------\n")