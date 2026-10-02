import requests
import hashlib
import json
import logging
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_data_hash(data: list) -> str:
    """Создает хэш от структуры данных для быстрой проверки изменений."""
    data_string = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(data_string.encode('utf-8')).hexdigest()

# --- 1. SHOPIFY СКРАПЕР ---
def fetch_shopify_products(base_url: str) -> list:
    url = f"{base_url.rstrip('/')}/products.json"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json().get("products", [])
    except Exception as e:
        logging.error(f"Ошибка Shopify скрапера для {base_url}: {e}")
        return []

def extract_shopify_data(raw_products: list) -> list:
    essential = []
    for p in raw_products:
        variants = []
        for v in p.get("variants", []):
            variants.append({
                "title": v.get("title"),
                "price": str(v.get("price"))
            })
        essential.append({
            "id": p.get("id"),
            "title": p.get("title"),
            "variants": variants
        })
    return essential

# --- 2. WORDPRESS / WOOCOMMERCE API СКРАПЕР ---
def fetch_wordpress_products(base_url: str) -> list:
    url = f"{base_url.rstrip('/')}/wp-json/wc/store/v1/products"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            products = response.json()
            essential = []
            for p in products:
                essential.append({
                    "id": p.get("id"),
                    "title": p.get("name"),
                    "variants": [{
                        "title": "Default",
                        "price": str(p.get("prices", {}).get("price", "0") ) / 100
                    }]
                })
            return essential
    except Exception:
        pass
    return []

# --- 3. SITEMAP ПАРСЕР (Для поиска прямых ссылок) ---
def fetch_urls_from_sitemap(sitemap_url: str, include_pattern: str = "/eva-mats/") -> list:
    """Скачивает sitemap.xml и возвращает список нужных ссылок."""
    logging.info(f"Скачиваем карту сайта: {sitemap_url}")
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(sitemap_url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'xml') 
        urls = [loc.text for loc in soup.find_all('loc') if include_pattern in loc.text]
        
        logging.info(f"Найдено {len(urls)} ссылок по паттерну {include_pattern}")
        return urls
    except Exception as e:
        logging.error(f"Ошибка при парсинге sitemap {sitemap_url}: {e}")
        return []

# --- 4. УМНЫЙ HTML ПАРСЕР (Playwright + BeautifulSoup) ---
def fetch_html_products(base_url: str, type_name: str) -> list:
    """Использует реальный браузер для обхода JS-рендеринга и лоадеров."""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            page.goto(base_url, wait_until="networkidle", timeout=30000)
            html_content = page.content()
            browser.close()
            
        soup = BeautifulSoup(html_content, 'html.parser')
        essential = []
        
        # Заменили .good-card на правильный .good_wrapper
        product_cards = soup.select('.product-layout, .product-item, .item, .product-miniature, li.product, .set-card__details, .good_wrapper')
        
        if not product_cards:
            title_elem = soup.select_one('h1.product_title, h1.product-title, .product-details h1, h1')
            price_elem = soup.select_one('p.price, span.price, .woocommerce-Price-amount, .current-price, .price-new')
            
            if title_elem and price_elem:
                title = title_elem.get_text(strip=True)
                price = price_elem.get_text(strip=True)
                
                if price.lower().startswith("od "):
                    price = price[3:].strip()
                    
                essential.append({
                    "id": f"{type_name}_{hashlib.md5(title.encode()).hexdigest()[:8]}",
                    "title": title,
                    "variants": [{
                        "title": "Standard",
                        "price": price
                    }]
                })
            return essential
        
        for idx, card in enumerate(product_cards):
            title_elem = card.select_one('.name a, .title, h4 a, .product-title, h2.woocommerce-loop-product__title, .set-card__title, .catname')
            price_elem = card.select_one('.price, .price-new, span.price, .option-price__current, .woocommerce-Price-amount, .set-card__price, .new')
            
            if title_elem and price_elem:
                title = title_elem.get_text(strip=True)
                price = price_elem.get_text(strip=True)
                
                if price.lower().startswith("od "):
                    price = price[3:].strip()
                
                essential.append({
                    "id": f"{type_name}_{idx}",
                    "title": title,
                    "variants": [{
                        "title": "Standard",
                        "price": price
                    }]
                })
        return essential
    except Exception as e:
        logging.error(f"Ошибка Playwright парсера для {base_url}: {e}")
        return []