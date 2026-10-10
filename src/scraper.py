import hashlib
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def get_data_hash(data: list) -> str:
    """Создает хэш от структуры данных для быстрой проверки изменений."""
    data_string = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(data_string.encode("utf-8")).hexdigest()


# --- 1. SHOPIFY СКРАПЕР (С мягким обходом лимитов) ---
def fetch_shopify_products(base_url: str) -> list:
    """Выгружает все товары магазина через пагинацию Shopify API с защитой от 429 Too Many Requests."""
    all_products = []
    page = 1
    max_retries = 3

    while True:
        url = f"{base_url.rstrip('/')}/products.json?limit=50&page={page}"
        retry_count = 0
        success = False

        while retry_count < max_retries:
            try:
                response = requests.get(url, headers=DEFAULT_HEADERS, timeout=12)

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 4))
                    # Ограничиваем ожидание максимум 10 секундами, чтобы не висеть по 60 секунд
                    sleep_time = min(retry_after, 10)
                    logging.warning(f"Shopify [{base_url}]: Лимит запросов (429). Ожидание {sleep_time} сек...")
                    time.sleep(sleep_time)
                    retry_count += 1
                    continue

                response.raise_for_status()
                products = response.json().get("products", [])

                if not products:
                    logging.info(f"Shopify [{base_url}]: пагинация завершена на стр. {page}. Всего: {len(all_products)} товаров.")
                    return all_products

                all_products.extend(products)
                page += 1
                success = True

                # Пауза между страницами согласно п. 10.3 ТЗ
                time.sleep(0.6)
                break

            except requests.exceptions.RequestException as e:
                logging.error(f"Ошибка сети Shopify для {base_url} (стр. {page}, попытка {retry_count + 1}): {e}")
                retry_count += 1
                time.sleep(2)

        if not success:
            logging.error(f"Не удалось выгрузить страницу {page} для {base_url} после {max_retries} попыток.")
            break

    return all_products


# --- 2. WORDPRESS / WOOCOMMERCE API СКРАПЕР ---
def fetch_wordpress_products(base_url: str) -> list:
    url = f"{base_url.rstrip('/')}/wp-json/wc/store/v1/products"
    try:
        response = requests.get(url, headers=DEFAULT_HEADERS, timeout=10)
        if response.status_code == 200:
            products = response.json()
            essential = []
            for p in products:
                essential.append({
                    "id": p.get("id"),
                    "title": p.get("name"),
                    "variants": [{
                        "title": "Default",
                        "price": str(p.get("prices", {}).get("price", "0"))
                    }]
                })
            return essential
    except Exception as e:
        logging.error(f"Ошибка WooCommerce скрапера для {base_url}: {e}")
    return []


# --- 3. SITEMAP ПАРСЕР (С поддержкой lastmod) ---
def fetch_sitemap_entries(sitemap_url: str, include_pattern: str = ".html") -> list[dict]:
    """Скачивает sitemap.xml и возвращает список ссылок без XML namespace проблем."""
    logging.info(f"Скачиваем карту сайта: {sitemap_url}")
    try:
        response = requests.get(sitemap_url, headers=DEFAULT_HEADERS, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "xml")
        entries = []

        for url_node in soup.find_all("url"):
            loc = url_node.find("loc")
            lastmod = url_node.find("lastmod")

            if loc and loc.text:
                url_text = loc.text.strip()
                lastmod_text = lastmod.text.strip() if lastmod and lastmod.text else None

                if (not include_pattern or include_pattern in url_text) and "/blog/" not in url_text:
                    entries.append({
                        "url": url_text,
                        "lastmod": lastmod_text
                    })

        logging.info(f"Найдено {len(entries)} ссылок по паттерну {include_pattern}")
        return entries
    except Exception as e:
        logging.error(f"Ошибка при обработке sitemap {sitemap_url}: {e}")
        return []


def fetch_urls_from_sitemap(sitemap_url: str, include_pattern: str = "/eva-mats/") -> list:
    entries = fetch_sitemap_entries(sitemap_url, include_pattern)
    return [e["url"] for e in entries]


# --- 4. ПАРСЕР ОДНОЙ КАРТОЧКИ (СО СТАБИЛЬНЫМ ID) ---
def extract_schema_org_data(soup: BeautifulSoup) -> dict | None:
    """Извлекает структурированные данные Schema.org (JSON-LD)."""
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            raw = json.loads(script.string)
            nodes = raw if isinstance(raw, list) else raw.get("@graph", [raw])
            for node in nodes:
                if node.get("@type") in ["Product", "IndividualProduct"]:
                    name = node.get("name")
                    offers = node.get("offers", {})
                    if isinstance(offers, list) and offers:
                        offers = offers[0]

                    price = offers.get("price") or offers.get("lowPrice")
                    currency = offers.get("priceCurrency", "zł")
                    if currency == "PLN":
                        currency = "zł"

                    if name and price:
                        p_float = float(str(price).replace(",", "."))
                        if p_float >= 99:
                            return {
                                "title": name.strip(),
                                "price": f"{int(p_float) if p_float.is_integer() else p_float:.2f} {currency}".strip()
                            }
        except (ValueError, TypeError):
            continue
    return None


def fetch_product_page_simple(url: str, type_name: str) -> list:
    """
    Универсальный парсер карточек товаров.
    Использует артикул OpenCart или slug названия для стабильного ID, исключая мигания.
    """
    try:
        response = requests.get(url, headers=DEFAULT_HEADERS, timeout=15)
        if response.status_code != 200:
            return []

        soup = BeautifulSoup(response.content, "html.parser")
        title_elem = soup.select_one("h1.product_title, h1.product-title, .product-details h1, h1")
        if not title_elem:
            return []

        base_title = title_elem.get_text(strip=True)
        variants = []
        seen_variants = set()

        # 1. ПРИОРИТЕТ: Стандартные радиокнопки/чекбоксы опций комплектаций (Evasota, OpenCart)
        option_inputs = soup.select('input[data-price][data-name], input[name^="option"][data-price]')
        for inp in option_inputs:
            price_val = inp.get("data-price", "0")
            try:
                price_float = float(price_val)
            except ValueError:
                price_float = 0.0

            is_set_group = inp.get("data-option_code") == "set"
            if price_float > 99 or is_set_group:
                var_name = inp.get("data-name") or inp.get("value", "Komplet standard")
                clean_price = f"{int(price_float) if price_float.is_integer() else price_float:.2f} zł"
                
                v_key = (var_name.strip(), clean_price)
                if v_key not in seen_variants:
                    seen_variants.add(v_key)
                    variants.append({
                        "title": var_name.strip(),
                        "price": clean_price
                    })

        # 2. ПРИОРИТЕТ: Если явных инпутов опций нет — универсальная Schema.org JSON-LD
        if not variants:
            schema_data = extract_schema_org_data(soup)
            if schema_data and schema_data.get("price"):
                variants.append({
                    "title": "Komplet standard",
                    "price": schema_data["price"]
                })

        # 3. ФОЛБЭК: Текстовые блоки цен страницы
        if not variants:
            main_price_selectors = [
                ".option-price__current",
                ".price-new",
                ".product-price",
                "span[itemprop='price']",
                ".current-price",
                ".price"
            ]
            for sel in main_price_selectors:
                elem = soup.select_one(sel)
                if elem and "zł" in elem.get_text():
                    variants.append({
                        "title": "Komplet standard",
                        "price": elem.get_text(strip=True)
                    })
                    break

        if variants:
            # Нормализуем URL: убираем дублирующие префиксы /products/ и GET-параметры
            canonical_url = url.split("?")[0].replace("/products/", "/")

            # 1. Приоритет: извлекаем уникальный числовой ID OpenCart из URL (например, '6402' из '...-6402-pl')
            product_id_match = re.search(r"-(\d+)-pl", canonical_url)

            if product_id_match:
                stable_id = f"{type_name}_{product_id_match.group(1)}"
            else:
                # 2. Фолбэк для страниц без артикула: хэш от очищенного заголовка
                clean_title_slug = re.sub(r"[^\w\s]", "", base_title.lower()).strip()
                clean_title_slug = re.sub(r"\s+", "_", clean_title_slug)
                stable_id = f"{type_name}_{hashlib.md5(clean_title_slug.encode('utf-8')).hexdigest()[:10]}"

            return [{
                "id": stable_id,
                "title": base_title,
                "variants": variants,
                "url": canonical_url
            }]
    except Exception as e:
        logging.error(f"Ошибка парсера карточки {url}: {e}")
    return []

# --- 5. СКРАПЕР ВИТРИН (Listing / Accessories) ---
def parse_html_listing(config: dict) -> list:
    url = config["url"]
    rules = config.get("rules", {})
    try:
        response = requests.get(url, headers=DEFAULT_HEADERS, timeout=10)
        response.raise_for_status()
    except Exception as e:
        logging.error(f"Ошибка загрузки витрины {url}: {e}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    cards = soup.select(rules.get("card", ".product-card"))
    items = []

    for idx, card in enumerate(cards):
        title_el = card.select_one(rules.get("title", ".product-card__title, .name a, h4 a, .title"))
        price_el = card.select_one(rules.get("price", ".product-card__price, .price, .option-price__current"))
        link_el = card.select_one(rules.get("link", "a"))

        if title_el and price_el:
            title = title_el.get_text(strip=True)
            price = price_el.get_text(strip=True)
            if price.lower().startswith("od "):
                price = price[3:].strip()

            link = urljoin(url, link_el.get("href", "")) if (link_el and link_el.get("href")) else url
            clean_slug = re.sub(r"[^\w\s]", "", title.lower()).strip()
            item_id = f"{config.get('name', 'item')}_{hashlib.md5(clean_slug.encode()).hexdigest()[:8]}"
            
            items.append({
                "id": item_id,
                "title": title,
                "variants": [{"title": "Default", "price": price}],
                "url": link
            })

    logging.info(f"Listing [{config.get('name')}]: успешно собрано {len(items)} товаров.")
    return items


# --- 6. ОБХОДЧИК КАТАЛОГОВ (Crawl) ---
def parse_html_crawl(config: dict) -> list:
    start_url = config["url"]
    rules = config.get("rules", {})
    follow_patterns = rules.get("follow_patterns", [])

    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    logging.info(f"Crawl [{config.get('name')}]: сбор ссылок первого уровня с {start_url}...")
    try:
        resp = session.get(start_url, timeout=12)
        resp.raise_for_status()
    except Exception as e:
        logging.error(f"Ошибка открытия стартовой ссылки {start_url}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    category_links = set()
    for a in soup.find_all("a", href=True):
        full_url = urljoin(start_url, a["href"])
        if any(pat in full_url for pat in follow_patterns):
            category_links.add(full_url)

    max_cats = rules.get("max_categories")
    if max_cats:
        category_links = list(category_links)[:max_cats]

    logging.info(f"Crawl [{config.get('name')}]: найдено {len(category_links)} разделов для поиска моделей.")

    product_urls = set()
    product_pattern = rules.get("product_pattern", "")

    def find_products_in_cat(cat_url):
        try:
            r = session.get(cat_url, timeout=10)
            s = BeautifulSoup(r.text, "html.parser")
            for a in s.find_all("a", href=True):
                target = urljoin(cat_url, a["href"])
                if product_pattern:
                    if product_pattern in target:
                        product_urls.add(target)
                elif any(word in target for word in ["dywaniki-podlogowe", "model-", "/product/"]):
                    product_urls.add(target)
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=10) as executor:
        executor.map(find_products_in_cat, category_links)

    logging.info(f"Crawl [{config.get('name')}]: найдено {len(product_urls)} моделей. Запуск сбора цен...")

    results = []
    comp_name = config.get("name", "crawl_item")

    with ThreadPoolExecutor(max_workers=15) as executor:
        for res in executor.map(lambda u: fetch_product_page_simple(u, comp_name), product_urls):
            if res:
                results.extend(res)

    logging.info(f"Crawl [{config.get('name')}]: собрано данных по {len(results)} товарам.")
    return results


# --- 7. ЕДИНЫЙ ДИСПЕТЧЕР СКРАПИНГА ---
def scrape_competitor(comp_config: dict) -> list:
    c_type = comp_config.get("type", "").lower()
    url = comp_config.get("url")

    if c_type == "shopify":
        return fetch_shopify_products(url)
    elif c_type == "wordpress":
        return fetch_wordpress_products(url)
    elif c_type == "listing":
        return parse_html_listing(comp_config)
    elif c_type == "crawl":
        return parse_html_crawl(comp_config)
    elif c_type == "sitemap":
        pattern = comp_config.get("product_pattern", "/eva-mats/")
        urls = fetch_urls_from_sitemap(url, pattern)
        results = []
        name = comp_config.get("name", "sitemap_item")
        with ThreadPoolExecutor(max_workers=15) as executor:
            for item in executor.map(lambda u: fetch_product_page_simple(u, name), urls):
                if item:
                    results.extend(item)
        return results
    else:
        logging.warning(f"Неизвестный тип скрапера '{c_type}' для {comp_config.get('name')}")
        return []


# --- 8. ДЕТЕКТОР ГЛОБАЛЬНЫХ ОФФЕРОВ И ПРОМО-БАННЕРОВ (п. 6.2 ТЗ) ---
def fetch_main_page_promo(base_url: str) -> dict:
    """Извлекает промо-тексты, скидки и H1 с главной страницы."""
    if base_url.endswith(".xml"):
        p = urlparse(base_url)
        base_url = f"{p.scheme}://{p.netloc}/"
    try:
        response = requests.get(base_url, headers=DEFAULT_HEADERS, timeout=12)
        if response.status_code != 200:
            return {}

        soup = BeautifulSoup(response.content, "html.parser")

        promo_selectors = [
            ".announcement-bar",
            ".top-bar",
            ".header-banner",
            ".promo-bar",
            ".alert-banner",
            "[class*='announc']",
            "[class*='promo']",
            "[class*='banner']"
        ]

        banner_texts = []
        for sel in promo_selectors:
            for elem in soup.select(sel):
                t = elem.get_text(" ", strip=True)
                if 5 < len(t) < 250 and not any(skip in t.lower() for skip in ["cookie", "polityka", "javascript"]):
                    if t not in banner_texts:
                        banner_texts.append(t)

        h1 = [h.get_text(" ", strip=True) for h in soup.find_all("h1") if h.get_text(strip=True)]
        h2 = [h.get_text(" ", strip=True) for h in soup.find_all("h2") if 5 < len(h.get_text(strip=True)) < 150][:3]

        combined_text = " | ".join(filter(None, [
            " ".join(banner_texts[:3]),
            " ".join(h1[:2]),
            " ".join(h2[:3])
        ]))

        return {
            "url": base_url,
            "promo_text": combined_text.strip(),
            "hash": hashlib.md5(combined_text.strip().encode("utf-8")).hexdigest() if combined_text else ""
        }
    except Exception as e:
        logging.error(f"Ошибка сбора промо-оффера для {base_url}: {e}")
        return {}