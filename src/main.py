import yaml
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from database import (
    init_db, 
    load_state, 
    save_state, 
    get_saved_lastmod, 
    save_page_lastmod
)
from scraper import (
    fetch_shopify_products, 
    fetch_wordpress_products, 
    parse_html_listing, 
    parse_html_crawl,
    fetch_product_page_simple,
    fetch_sitemap_entries,
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

def extract_shopify_clean_data(raw_products: list, comp_name: str) -> list:
    """Приводит сырой JSON из Shopify к общему формату товаров."""
    clean_items = []
    for p in raw_products:
        p_id = str(p.get("id"))
        title = p.get("title", "")
        variants = []
        for v in p.get("variants", []):
            variants.append({
                "title": v.get("title", "Default"),
                "price": f"{v.get('price', '0')} zł"
            })
        if variants:
            clean_items.append({
                "id": f"{comp_name.lower().replace(' ', '_')}_{p_id}",
                "title": title,
                "variants": variants,
                "url": f"https://{comp_name.lower().replace(' ', '')}.pl/products/{p.get('handle', '')}"
            })
    return clean_items

def main():
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        logging.info("🔧 РЕЖИМ DRY-RUN: Telegram и LLM отключены. Только сбор данных и замер времени.")

    total_start_time = time.time()
    init_db() 
    config = load_config()
    
    for comp in config.get("competitors", []):
        comp_start_time = time.time()
        comp_name = comp["name"]
        comp_type = comp.get("type", "").lower()
        logging.info(f"--- Анализ конкурента: {comp_name} ---")
        
        # 1. Сбор данных в зависимости от движка сайта
        clean_data = []
        
        if comp_type == "shopify":
            raw_data = fetch_shopify_products(comp["url"])
            clean_data = extract_shopify_clean_data(raw_data, comp_name) if raw_data else []
            
        elif comp_type == "wordpress":
            clean_data = fetch_wordpress_products(comp["url"])
            
        elif comp_type == "listing":
            clean_data = parse_html_listing(comp)
            
        elif comp_type == "crawl":
            clean_data = parse_html_crawl(comp)
            
        elif comp_type == "sitemap":
            pattern = comp.get("product_pattern", ".html")
            logging.info(f"Скачиваем и парсим sitemap: {comp['url']}")
            sitemap_entries = fetch_sitemap_entries(comp["url"], pattern)
            logging.info(f"Всего ссылок в sitemap по фильтру: {len(sitemap_entries)}")

            # Фильтрация по lastmod (пропуск страниц, которые не менялись)
            urls_to_scrape = []
            for entry in sitemap_entries:
                url = entry["url"]
                remote_lastmod = entry["lastmod"]
                saved_lastmod = get_saved_lastmod(url)

                if not remote_lastmod or remote_lastmod != saved_lastmod:
                    urls_to_scrape.append(entry)

            skipped_count = len(sitemap_entries) - len(urls_to_scrape)
            if skipped_count > 0:
                logging.info(f"⚡ Пропущено {skipped_count} страниц (не менялись по lastmod).")

            logging.info(f"Отправляем на парсинг: {len(urls_to_scrape)} страниц.")

            def process_single_url(entry):
                item_url = entry["url"]
                data = fetch_product_page_simple(item_url, comp_name.lower().replace(" ", "_"))
                if data:
                    if entry.get("lastmod"):
                        save_page_lastmod(item_url, entry["lastmod"])
                    return data
                return []

            # Параллельный сбор страниц с карточками
            MAX_WORKERS = 6
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = [executor.submit(process_single_url, entry) for entry in urls_to_scrape]
                for future in as_completed(futures):
                    try:
                        result = future.result()
                        if result:
                            clean_data.extend(result)
                    except Exception as err:
                        logging.error(f"Ошибка при параллельном парсинге страницы: {err}")
        else:
            logging.warning(f"Неизвестный тип конкурента: {comp_type}")
            continue
            
        if not clean_data:
            logging.warning(f"Данные не собраны. Проверьте парсер для {comp['url']}")
            continue

        # 2. Загрузка старых данных и сравнение
        old_data = load_state(comp_name)
        
        if not old_data:
            logging.info(f"Предыдущих данных нет (первый запуск для {comp_name}). Сохраняем в БД.")
            save_state(comp_name, clean_data)
            logging.info(f"⏱ Время обработки {comp_name}: {round(time.time() - comp_start_time, 2)} сек.")
            continue
                        
        if get_data_hash(old_data) == get_data_hash(clean_data):
            logging.info(f"✅ Изменений на сайте {comp_name} нет.")
            logging.info(f"⏱ Время обработки {comp_name}: {round(time.time() - comp_start_time, 2)} сек.")
            continue
            
        logging.info(f"⚠️ Обнаружены изменения на {comp_name}! Ищем разницу...")
        changes = detect_changes(old_data, clean_data)
        
        if changes:
            # Сохраняем ВСЕ изменения в долгосрочное хранилище
            log_changes(comp_name, changes)
            
            # Фильтруем изменения, оставляя только важные (средняя и высокая)
            significant_changes = [c for c in changes if c.get('importance') in ['средняя', 'высокая']]
            
            if significant_changes:
                if dry_run:
                    logging.info(f"⚡ [DRY-RUN] Найдено важных изменений: {len(significant_changes)}. Пропуск запросов к LLM и Telegram.")
                else:
                    alert_msg = f"⚠️ <b>Важные изменения у конкурента: {comp_name}</b>\n🔗 <a href='{comp['url']}'>Ссылка на магазин</a>\n\n"
                    
                    # 3. Анализ через LLM
                    for change in significant_changes[:10]: 
                        logging.info(f"Важное изменение: {change['product']} | {change['before']} -> {change['after']}")
                        
                        evaluation = evaluate_change_with_llm(
                            competitor=comp_name,
                            product_name=change['product'],
                            change_type=change['type'],
                            before=str(change['before']),
                            after=str(change['after'])
                        )
                        
                        if change.get('importance') == 'высокая':
                            alert_msg += "🚨 <b>ВЫСОКАЯ ВАЖНОСТЬ</b>\n"
                            
                        if change['type'] == 'new_product':
                            alert_msg += f"✨ <b>Новинка:</b> {change['product']}\n💵 Цена: {change['after']}\n"
                        elif change['type'] == 'deleted_product':
                            alert_msg += f"🗑 <b>Удален:</b> {change['product']}\n"
                        else:
                            alert_msg += f"🔄 <b>Смена цены:</b> {change['product']}\n📉 {change['before']} ➔ {change['after']}\n"
                        
                        # Извлекаем понятный текстовый вывод LLM вместо сырого словаря
                        if evaluation and isinstance(evaluation, dict):
                            summary_text = evaluation.get("summary", "")
                            if summary_text:
                                alert_msg += f"🤖 <i>LLM: {summary_text}</i>\n"
                        
                        alert_msg += "\n"
                        
                    if len(significant_changes) > 10:
                        alert_msg += f"<i>...и еще {len(significant_changes) - 10} важных изменений. Полный список в логах.</i>\n"
                        
                    # ОТПРАВЛЯЕМ УВЕДОМЛЕНИЕ В TELEGRAM
                    send_telegram_message(alert_msg)
            else:
                logging.info("Изменения найдены, но их важность низкая (защита от спама). Уведомление не отправлено.")
            
        # 4. Обновление базы
        save_state(comp_name, clean_data)
        logging.info(f"Состояние для {comp_name} обновлено в БД.")
        logging.info(f"⏱ Время обработки {comp_name}: {round(time.time() - comp_start_time, 2)} сек.")

    total_time = round(time.time() - total_start_time, 2)
    logging.info(f"🏁 Полный цикл мониторинга завершен за {total_time} сек. ({round(total_time / 60, 2)} мин.)")

if __name__ == "__main__":
    main()