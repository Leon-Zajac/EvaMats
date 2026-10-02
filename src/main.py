import yaml
import logging
from database import init_db, load_state, save_state
from scraper import (
    fetch_shopify_products, 
    extract_shopify_data, 
    fetch_wordpress_products, 
    fetch_html_products, 
    get_data_hash
)
from llm_extractor import evaluate_change_with_llm

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def detect_changes(old_data, new_data):
    changes = []
    old_dict = {str(item['id']): item for item in old_data}
    
    for new_item in new_data:
        item_id = str(new_item['id'])
        if item_id in old_dict:
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
            changes.append({
                "product": new_item['title'],
                "before": "Не было",
                "after": new_item['variants'][0]['price'] if new_item['variants'] else "Нет цены",
                "type": "new_product"
            })
    return changes

def main():
    init_db() 
    config = load_config()
    
    for comp in config.get("competitors", []):
        logging.info(f"--- Анализ конкурента: {comp['name']} ---")
        
        # 1. Сбор данных в зависимости от движка сайта
        clean_data = []
        if comp["type"] == "shopify":
            raw_data = fetch_shopify_products(comp["url"])
            clean_data = extract_shopify_data(raw_data) if raw_data else []
        elif comp["type"] == "wordpress":
            clean_data = fetch_wordpress_products(comp["url"])
        elif comp["type"] in ["opencart", "html"]:
            clean_data = fetch_html_products(comp["url"], comp["type"])
        elif comp["type"] == "sitemap":
            # 1. Получаем ссылки на товары (возьмем первые 5 для скорости теста)
            from scraper import fetch_urls_from_sitemap
            target_urls = fetch_urls_from_sitemap(comp["url"], "/eva-mats/")[:5]
            
            clean_data = []
            for item_url in target_urls:
                # 2. Парсим каждую страницу (используем Playwright или обычный fetch_html_products)
                logging.info(f"Парсим товар: {item_url}")
                item_data = fetch_html_products(item_url, comp["type"])
                if item_data:
                    clean_data.extend(item_data)
        else:
            logging.warning(f"Неизвестный тип конкурента: {comp['type']}")
            continue
            
        if not clean_data:
            logging.warning(f"Данные не собраны. Проверьте парсер для {comp['url']}")
            continue

        # 2. Загрузка старых данных и сравнение
        old_data = load_state(comp['name'])
        
        if not old_data:
            logging.info("Предыдущих данных нет (первый запуск). Сохраняем в БД.")
            save_state(comp['name'], clean_data)
            continue
            
        if get_data_hash(old_data) == get_data_hash(clean_data):
            logging.info("✅ Изменений на сайте нет.")
            continue
            
        logging.info("⚠️ Обнаружены изменения! Ищем разницу...")
        changes = detect_changes(old_data, clean_data)
        
        # 3. Анализ через LLM
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
            
        # 4. Обновление базы
        save_state(comp['name'], clean_data)
        logging.info(f"Состояние для {comp['name']} обновлено в БД.")

if __name__ == "__main__":
    main()