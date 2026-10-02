import requests
from bs4 import BeautifulSoup

def dump_site_html(url, filename):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    print(f"Скачиваем исходный код: {url}...")
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        # Форматируем HTML для удобного чтения
        soup = BeautifulSoup(response.text, 'html.parser')
        
        with open(filename, "w", encoding="utf-8") as f:
            f.write(soup.prettify())
            
        print(f"✅ Готово! Код сохранен в {filename}")
        print("💡 Подсказка: Откройте файл, нажмите Ctrl+F и введите известную цену с сайта (например, 600).")
        
    except Exception as e:
        print(f"❌ Ошибка при скачивании {url}: {e}")

if __name__ == "__main__":
    # Скачиваем HTML обоих проблемных сайтов
    dump_site_html("https://evasota.pl", "evasota_dump.html")
    dump_site_html("https://www.evadywaniki.pl", "evadywaniki_dump.html")