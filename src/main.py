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
from notifier import send_telegram_message
from comparator import detect_changes
from storage import log_changes

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def load_config():
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

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
            from scraper import fetch_urls_from_sitemap
            target_urls = fetch_urls_from_sitemap(comp["url"], "/eva-mats/")
            
            clean_data = []
            for item_url in target_urls:
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
        
        if changes:
            # Сохраняем ВСЕ изменения в долгосрочное хранилище
            log_changes(comp['name'], changes)
            
            # Фильтруем изменения, оставляя только ВАЖНЫЕ (Средняя и Высокая)
            significant_changes = [c for c in changes if c['importance'] in ['средняя', 'высокая']]
            
            if significant_changes:
                # Формируем заголовок сообщения для Telegram
                alert_msg = f"⚠️ <b>Важные изменения у конкурента: {comp['name']}</b>\n🔗 <a href='{comp['url']}'>Ссылка на магазин</a>\n\n"
                
                # 3. Анализ через LLM
                for change in significant_changes[:10]: 
                    logging.info(f"Важное изменение: {change['product']} | {change['before']} -> {change['after']}")
                    
                    evaluation = evaluate_change_with_llm(
                        competitor=comp['name'],
                        product_name=change['product'],
                        change_type=change['type'],
                        before=change['before'],
                        after=change['after']
                    )
                    
                    if change['importance'] == 'высокая':
                        alert_msg += "🚨 <b>ВЫСОКАЯ ВАЖНОСТЬ</b>\n"
                        
                    # Добавляем информацию о конкретном товаре в Telegram-сообщение
                    if change['type'] == 'new_product':
                        alert_msg += f"✨ <b>Новинка:</b> {change['product']}\n💵 Цена: {change['after']}\n"
                    elif change['type'] == 'deleted_product':
                        alert_msg += f"🗑 <b>Удален:</b> {change['product']}\n"
                    else:
                        alert_msg += f"🔄 <b>Смена цены:</b> {change['product']}\n📉 {change['before']} ➔ {change['after']}\n"
                    
                    # Добавляем мнение LLM (если оно есть)
                    if evaluation:
                        alert_msg += f"🤖 <i>LLM: {evaluation}</i>\n"
                    
                    alert_msg += "\n" # Отступ между товарами
                    
                if len(significant_changes) > 10:
                    alert_msg += f"<i>...и еще {len(significant_changes) - 10} важных изменений. Полный список в логах.</i>\n"
                    
                # ОТПРАВЛЯЕМ УВЕДОМЛЕНИЕ
                send_telegram_message(alert_msg)
            else:
                logging.info("Изменения найдены, но их важность низкая (защита от спама). Уведомление не отправлено.")
            
        # 4. Обновление базы
        save_state(comp['name'], clean_data)
        logging.info(f"Состояние для {comp['name']} обновлено в БД.")

if __name__ == "__main__":
    main()