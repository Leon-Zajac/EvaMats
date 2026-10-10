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
    fetch_main_page_promo,
    get_data_hash
)
from llm_extractor import evaluate_change_with_llm, evaluate_promo_change_with_llm
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
    
    # Считываем настройки LLM из config.yaml (дефолт - рабочая модель)
    llm_config = config.get("llm", {})
    model_name = llm_config.get("model", "google/gemini-3.8-flash")
    
    for comp in config.get("competitors", []):
        comp_start_time = time.time()
        comp_name = comp["name"]
        comp_type = comp.get("type", "").lower()
        logging.info(f"--- Анализ конкурента: {comp_name} ---")
        
        # ---------------------------------------------------------
        # ШАГ 0. ПРОВЕРКА ГЛОБАЛЬНЫХ ОФФЕРОВ И БАННЕРОВ 
        # ---------------------------------------------------------
        promo_data = fetch_main_page_promo(comp["url"])
        if promo_data and promo_data.get("promo_text"):
            old_promo_state = load_state(f"{comp_name}_promo")
            old_promo_text = old_promo_state[0].get("promo_text", "") if old_promo_state else ""
            old_promo_hash = old_promo_state[0].get("hash", "") if old_promo_state else ""

            if not old_promo_state:
                logging.info(f"Сохранен базовый промо-оффер главной страницы для {comp_name}.")
                save_state(f"{comp_name}_promo", [promo_data])
            elif old_promo_hash != promo_data.get("hash"):
                logging.info(f"🚨 Обнаружено изменение оффера/акции у {comp_name}!")
                
                if not dry_run:
                    analysis = evaluate_promo_change_with_llm(
                        competitor=comp_name,
                        before=old_promo_text,
                        after=promo_data.get("promo_text", ""),
                        model_name=model_name
                    )
                    
                    promo_msg = (
                        f"📣 <b>Смена акции / УТП у {comp_name}</b>\n"
                        f"🔗 <a href='{comp['url']}'>Перейти на сайт</a>\n\n"
                        f"<b>Было:</b> {old_promo_text}\n\n"
                        f"<b>Стало:</b> {promo_data.get('promo_text', '')}\n\n"
                        f"🤖 <i>LLM: {analysis.get('summary', 'Обновление маркетинговых материалов.')}</i>"
                    )
                    send_telegram_message(promo_msg)
                
                save_state(f"{comp_name}_promo", [promo_data])

        # ---------------------------------------------------------
        # ШАГ 1. СБОР КАТАЛОГА ТОВАРОВ
        # ---------------------------------------------------------
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
            s_url = comp.get("sitemap_url", comp["url"])
            logging.info(f"Скачиваем и парсим sitemap: {s_url}")
            sitemap_entries = fetch_sitemap_entries(s_url, pattern)

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

            # Ограничение количества страниц (если задано в конфиге для безопасных тестов)
            max_limit = comp.get("max_items")
            if max_limit and len(urls_to_scrape) > max_limit:
                logging.info(f"Применен лимит max_items из конфига: обрабатываем первые {max_limit} ссылок.")
                urls_to_scrape = urls_to_scrape[:max_limit]

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
            freshly_scraped = []
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = [executor.submit(process_single_url, entry) for entry in urls_to_scrape]
                for future in as_completed(futures):
                    try:
                        result = future.result()
                        if result:
                            freshly_scraped.extend(result)
                    except Exception as err:
                        logging.error(f"Ошибка при параллельном парсинге страницы: {err}")

            # Инкрементальное объединение с предыдущим состоянием
            old_cached = load_state(comp_name)
            if old_cached:
                merged_dict = {str(item.get("id")): item for item in old_cached}
                for fresh_item in freshly_scraped:
                    merged_dict[str(fresh_item.get("id"))] = fresh_item
                clean_data = list(merged_dict.values())
                logging.info(f"Актуализирован снимок каталога {comp_name}: всего {len(clean_data)} товаров.")
            else:
                clean_data = freshly_scraped
        else:
            logging.warning(f"Неизвестный тип конкурента: {comp_type}")
            continue
            
        if not clean_data:
            logging.warning(f"Данные не собраны. Проверьте парсер для {comp['url']}")
            continue

        # ---------------------------------------------------------
        # ШАГ 2. ЗАГРУЗКА ПРЕДЫДУЩЕГО СНИМКА И СРАВНЕНИЕ
        # ---------------------------------------------------------
        old_data = load_state(comp_name)

        if old_data and len(clean_data) < len(old_data) * 0.5:
            logging.error(f"⚠️ Собрано слишком мало товаров ({len(clean_data)} против {len(old_data)} ранее). Вероятен сбой сети. Пропуск детектора для исключения ложных удалений.")
            continue
        
        if not old_data:
            logging.info(f"Предыдущих данных нет (первый запуск для {comp_name}). Сохраняем в БД.")
            save_state(comp_name, clean_data)
            logging.info(f"⏱ Время обработки {comp_name}: {round(time.time() - comp_start_time, 2)} сек.")
            continue
                        
        if get_data_hash(old_data) == get_data_hash(clean_data):
            logging.info(f"✅ Изменений в каталоге {comp_name} нет.")
            logging.info(f"⏱ Время обработки {comp_name}: {round(time.time() - comp_start_time, 2)} сек.")
            continue
            
        logging.info(f"⚠️ Обнаружены изменения на {comp_name}! Ищем разницу...")
        
        changes = detect_changes(old_data, clean_data)
        
        if changes:
            # Сохраняем ВСЕ изменения в постоянное хранилище
            log_changes(comp_name, changes)
            
            # Фильтруем изменения: для уведомлений оставляем среднюю и высокую важность
            significant_changes = [c for c in changes if c.get('importance') in ['средняя', 'высокая']]
            
            if significant_changes:
                if dry_run:
                    logging.info(f"⚡ [DRY-RUN] Найдено важных изменений: {len(significant_changes)}. Пропуск запросов к LLM и Telegram.")
                else:
                    alert_msg = f"⚠️ <b>Важные изменения у конкурента: {comp_name}</b>\n🔗 <a href='{comp['url']}'>Ссылка на магазин</a>\n\n"
                    
                    # ---------------------------------------------------------
                    # ШАГ 3. АНАЛИЗ ЧЕРЕЗ LLM И ОТПРАВКА УВЕДОМЛЕНИЙ
                    # ---------------------------------------------------------
                    for change in significant_changes[:10]: 
                        logging.info(f"Важное изменение: {change['product']} | {change['before']} -> {change['after']}")
                        
                        evaluation = evaluate_change_with_llm(
                            competitor=comp_name,
                            product_name=change['product'],
                            change_type=change['type'],
                            before=str(change['before']),
                            after=str(change['after']),
                            model_name=model_name
                        )
                        
                        if change.get('importance') == 'высокая':
                            alert_msg += "🚨 <b>ВЫСОКАЯ ВАЖНОСТЬ</b>\n"
                            
                        if change['type'] == 'new_product':
                            alert_msg += f"✨ <b>Новинка:</b> {change['product']}\n💵 Цена: {change['after']}\n"
                        elif change['type'] == 'deleted_product':
                            alert_msg += f"🗑 <b>Удален:</b> {change['product']}\n"
                        else:
                            diff_badge = f" ({change.get('diff_percent')})" if change.get('diff_percent') else ""
                            alert_msg += f"🔄 <b>Смена цены:</b> {change['product']}\n📉 {change['before']} ➔ {change['after']}{diff_badge}\n"
                        
                        # Человекочитаемый вывод от LLM
                        if evaluation and isinstance(evaluation, dict):
                            summary_text = evaluation.get("summary", "")
                            if summary_text:
                                alert_msg += f"🤖 <i>LLM: {summary_text}</i>\n"
                        
                        alert_msg += "\n"
                        
                    if len(significant_changes) > 10:
                        alert_msg += f"<i>...и еще {len(significant_changes) - 10} важных изменений. Полный список в storage/*.jsonl</i>\n"
                        
                    send_telegram_message(alert_msg)
            else:
                logging.info("Изменения найдены, но их важность низкая (защита от спама). Уведомление не отправлено.")
            
        # ---------------------------------------------------------
        # ШАГ 4. ОБНОВЛЕНИЕ БАЗЫ ДАННЫХ
        # ---------------------------------------------------------
        save_state(comp_name, clean_data)
        logging.info(f"Состояние для {comp_name} обновлено в БД.")
        logging.info(f"⏱ Время обработки {comp_name}: {round(time.time() - comp_start_time, 2)} сек.")

    total_time = round(time.time() - total_start_time, 2)
    logging.info(f"🏁 Полный цикл мониторинга завершен за {total_time} сек. ({round(total_time / 60, 2)} мин.)")

if __name__ == "__main__":
    main()