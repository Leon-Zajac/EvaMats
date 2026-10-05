import hashlib
import json
import logging
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def get_data_hash(data: list) -> str:
    """Создает хэш от структуры данных для быстрой проверки изменений."""
    data_string = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(data_string.encode("utf-8")).hexdigest()


# --- 1. SHOPIFY СКРАПЕР ---
def fetch_shopify_products(base_url: str) -> list:
    """Выгружает все товары магазина через пагинацию Shopify API."""
    all_products = []
    page = 1

    while True:
        url = f"{base_url.rstrip('/')}/products.json?limit=50&page={page}"
        try:
            response = requests.get(url, headers=DEFAULT_HEADERS, timeout=10)
            response.raise_for_status()
            products = response.json().get("products", [])
            if not products:
                break
            all_products.extend(products)
            page += 1
        except Exception as e:
            logging.error(f"Ошибка Shopify скрапера для {base_url} (стр. {page}): {e}")
            break

    logging.info(f"Shopify [{base_url}]: успешно выгружено {len(all_products)} товаров.")
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
    """Скачивает sitemap.xml и возвращает список ссылок, игнорируя проблемы с XML namespaces."""
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

                # Исключаем статьи блога и оставляем целевые страницы
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
    """Возвращает только список URL из карты сайта."""
    entries = fetch_sitemap_entries(sitemap_url, include_pattern)
    return [e["url"] for e in entries]


# --- 4. ЛЕГКОВЕСНЫЙ ПОТОКОБЕЗОПАСНЫЙ ПАРСЕР ОДНОЙ КАРТОЧКИ ---
def extract_schema_org_data(soup: BeautifulSoup) -> dict | None:
    """Извлекает структурированные данные Schema.org (JSON-LD) без привязки к HTML-верстке."""
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
                        return {
                            "title": name.strip(),
                            "price": f"{price} {currency}".strip()
                        }
        except (ValueError, TypeError):
            continue
    return None


def fetch_product_page_simple(url: str, type_name: str) -> list:
    """Универсальный сбор данных карточки: 1. Schema.org -> 2. Конфигуратор -> 3. Резервные селекторы."""
    try:
        response = requests.get(url, headers=DEFAULT_HEADERS, timeout=15)
        if response.status_code != 200:
            return []

        soup = BeautifulSoup(response.content, "html.parser")

        # 1. ПРИОРИТЕТ: Универсальная микроразметка Schema.org (не ломается при смене верстки)
        schema_data = extract_schema_org_data(soup)
        if schema_data:
            return [{
                "id": f"{type_name}_{hashlib.md5(url.encode()).hexdigest()[:8]}",
                "title": schema_data["title"],
                "variants": [{"title": "Komplet standard", "price": schema_data["price"]}],
                "url": url
            }]

        # 2. ФОЛБЭК: Если Schema.org нет, извлекаем через H1 и блоки цен/конфигуратора
        title_elem = soup.select_one("h1.product_title, h1.product-title, .product-details h1, h1")
        if not title_elem:
            return []

        title = title_elem.get_text(strip=True)
        variants = []

        # Блоки конфигуратора опций (complect)
        complect_blocks = soup.select('div.child_block[class*="complect-"], div[class*="front-mat-option"]')
        for block in complect_blocks:
            text = block.get_text(" ", strip=True)
            if "zł" in text:
                parts = text.rsplit(" ", 2)
                if len(parts) >= 2 and ("zł" in parts[-1] or "zł" in parts[-2]):
                    price_str = f"{parts[-2]} {parts[-1]}" if "zł" in parts[-1] else parts[-1]
                    var_name = text.replace(price_str, "").strip()
                    if var_name and price_str:
                        variants.append({"title": var_name, "price": price_str})

        # Резервный поиск по стандартным классам цен
        if not variants:
            main_price_selectors = [".price-new", ".product-price", "span[itemprop='price']", ".current-price", ".price"]
            for sel in main_price_selectors:
                elem = soup.select_one(sel)
                if elem and "zł" in elem.get_text():
                    variants.append({"title": "Komplet standard", "price": elem.get_text(strip=True)})
                    break

        if variants:
            return [{
                "id": f"{type_name}_{hashlib.md5(url.encode()).hexdigest()[:8]}",
                "title": title,
                "variants": variants,
                "url": url
            }]
    except Exception as e:
        logging.error(f"Ошибка requests парсера для {url}: {e}")
    return []

# --- 5. УНИВЕРСАЛЬНЫЙ СКРАПЕР ВИТРИН (Listing / Accessories) ---
def parse_html_listing(config: dict) -> list:
    """Парсит прямые страницы со списками карточек (каталог, аксессуары, распродажи)."""
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
            items.append({
                "id": f"{config.get('name', 'item')}_{idx}",
                "title": title,
                "variants": [{"title": "Default", "price": price}],
                "url": link
            })

    logging.info(f"Listing [{config.get('name')}]: успешно собрано {len(items)} товаров.")
    return items


# --- 6. УНИВЕРСАЛЬНЫЙ МНОГОУРОВНЕВЫЙ ОБХОДЧИК КАТАЛОГОВ (Crawl) ---
def parse_html_crawl(config: dict) -> list:
    """Обходит многоуровневый каталог: входная страница -> категории -> карточки моделей."""
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

    # Сбор ссылок на карточки товаров
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
    """Маршрутизатор скраперов по типу, указанному в config.yaml."""
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