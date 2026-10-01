import yaml
import logging
from database import init_db, load_state, save_state
from scraper import fetch_shopify_products, extract_essential_data, get_data_hash
from llm_extractor import evaluate_change_with_llm

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def load_config():
    """Загружает список конкурентов из конфигурационного файла."""
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def detect_changes(old_data, new_data):
    """Ищет изменившиеся цены или новые товары обычным кодом (без LLM)."""
    changes = []
    # Превращаем список старых товаров в словарь {id: данные} для быстрого поиска
    old_dict = {str(item['id']): item for item in old_data}
    
    for new_item in new_data:
        item_id = str(new_item['id'])
        if item_id in old_dict:
            # Товар уже был. Сравниваем цены на его варианты (цвета/размеры)
            old_variants = {v['title']: v for v in old_dict[item_id]['variants']}
            for new_var in new_item['variants']:
                var_title = new_var['title']
                if var_title in old_variants:
                    old_price = old_variants[var_title]['price']
                    new_price = new_var['price']
                    
                    if old_price != new_price:
                        changes.append({
                            "product": f"{new_item['title']} ({var_title})",
                            "before": old_price,
                            "after": new_price,
                            "type": "price_change"
                        })
        else:
            # Товара не было в старой базе
            changes.append({
                "product": new_item['title'],
                "before": "Не было",
                "after": new_item['variants'][0]['price'] if new_item['variants'] else "Нет цены",
                "type": "new_product"
            })
    return changes

def main():
    # Инициализируем БД при старте
    init_db() 
    config = load_config()
    
    for comp in config.get("competitors", []):
        logging.info(f"--- Анализ конкурента: {comp['name']} ---")
        
        if comp["type"] == "shopify":
            # 1. Сбор данных
            raw_data = fetch_shopify_products(comp["url"])
            if not raw_data:
                continue
                
            clean_data = extract_essential_data(raw_data)
            
            # Загружаем старые данные прямо из SQLite
            old_data = load_state(comp['name'])
            
            if not old_data:
                logging.info("Предыдущих данных нет (первый запуск). Сохраняем в БД.")
                save_state(comp['name'], clean_data)
                continue
                
            # 2. Обнаружение изменений (быстрая проверка по хэшу)
            if get_data_hash(old_data) == get_data_hash(clean_data):
                logging.info("✅ Изменений на сайте нет.")
                continue
                
            # 3. Детальное сравнение, если хэши не совпали
            logging.info("⚠️ Обнаружены изменения! Ищем разницу...")
            changes = detect_changes(old_data, clean_data)
            
            # 4. Прогон найденных изменений через LLM
            for change in changes:
                logging.info(f"Изменение: {change['product']} | {change['before']} -> {change['after']}")
                
                evaluation = evaluate_change_with_llm(
                    competitor=comp['name'],
                    product_name=change['product'],
                    change_type=change['type'],
                    before=change['before'],
                    after=change['after']
                )
                print(f"Вывод LLM: {evaluation}\n")
                
            # 5. Обновляем БД новыми данными после обработки всех изменений
            save_state(comp['name'], clean_data)
            logging.info(f"Состояние для {comp['name']} обновлено в БД.")

if __name__ == "__main__":
    main()