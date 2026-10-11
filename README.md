# EvaMats — Агент мониторинга e-commerce конкурентов (Польша)

Автономный AI-агент для непрерывного отслеживания цен, структуры каталогов, модификаций комплектаций, скидок и маркетинговых акций ключевых производителей автомобильных EVA-ковриков.

---

## 🏗 Архитектура системы

### Текстовая схема потоков данных

```text
       [ Shopify API ]         [ Sitemap.xml (lastmod) ]        [ HTML Crawl / Schema.org ]
       (primeeva.pl)              (evadywaniki.pl)                     (evasota.pl)
              │                               │                               │
              └───────────────────────┬───────┴───────────────────────────────┘
                                      │
                                      ▼
                        [ 1. Data Ingestion & Scraper ]
                         - Дедупликация и нормализация URL
                         - Генерация стабильных ID (-ID-pl)
                                      │
                                      ▼
                         [ 2. Comparator Engine ]
                         - Проверка на отвал сети (len < 0.5)
                         - Отсечение шума (< 1 zł)
                         - Классификация (низкая / средняя / высокая)
                                      │
                         ┌────────────┴────────────┐
                         │                         │
                         ▼                         ▼
             [ SQLite: tracking.db ]     [ JSONL History Logs ]
             (Текущее состояние и        (storage/*_changes.jsonl)
              метаданные lastmod)                  │
                         │                         │
                         └────────────┬────────────┘
                                      │
                                      ▼
                        [ 3. OpenRouter LLM Extractor ]
                         (google/gemini-2.0-flash-001)
                         - Бизнес-выводы и контекст
                                      │
                        ┌─────────────┴─────────────┐
                        ▼                           ▼
            [ Telegram Alerting ]       [ Streamlit Dashboard ]
            (Алерты с HTML-форматом)    (dashboard.py / Plotly)
```

---

## 📁 Структура проекта

```text
EvaMats/
├── data/                  # База данных SQLite (tracking.db)
├── storage/               # Логи истории изменений (*.jsonl)
├── tools/                 # Вспомогательные утилиты для разработки
│   └── inspect_html.py    # Инспектор HTML-разметки для отладки селекторов
├── src/
│   ├── main.py            # Основной пайплайн мониторинга
│   ├── scraper.py         # Модули скрапинга (Shopify, Sitemap, Crawl, Promo)
│   ├── comparator.py      # Детектор изменений и классификатор важности
│   ├── database.py        # Управление состоянием SQLite
│   ├── storage.py         # Запись журнала событий в JSONL
│   ├── notifier.py        # Отправка сообщений в Telegram
│   └── llm_extractor.py   # Интеграция с LLM через OpenRouter API
├── dashboard.py           # Аналитический веб-дашборд на Streamlit
├── config.yaml            # Конфигурация сайтов, типов скрапинга и моделей
├── requirements.txt       # Зависимости Python
├── .env.example           # Шаблон переменных окружения
├── .gitignore             # Исключения Git (.env, *.html, venv/, data/)
└── README.md              # Документация проекта
```

---

## 🚀 Как запустить проект

### 1. Системные требования и окружение
- Python 3.10+
- Виртуальное окружение `venv`

### 2. Клонирование и установка зависимостей
```bash
git clone https://github.com/Leon-Zajac/EvaMats
cd EvaMats

# Создание виртуального окружения
python -m venv venv

# Активация:
# Windows (PowerShell):
.\venv\Scripts\Activate.ps1
# Linux / macOS:
source venv/bin/activate

# Установка пакетов
pip install -r requirements.txt
playwright install
```

### 3. Настройка конфигурации и секретов
Создайте файл `.env` на основе шаблона `.env.example`:
```bash
cp .env.example .env
```
Заполните переменные:
```env
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxxxxx
TELEGRAM_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=123456789
```

### 4. Запуск мониторинга

- **Тестовый запуск (Dry-Run):**  
  Собирает данные, проводит детекцию и сохраняет снимок в базу, **не расходуя токены LLM** и **не отправляя сообщений в Telegram**:
  ```bash
  python src/main.py --dry-run
  ```

- **Боевой запуск:**  
  Полный цикл мониторинга с отправкой алертов в Telegram и сохранением аналитики:
  ```bash
  python src/main.py
  ```

### 5. Запуск веб-дашборда
```bash
streamlit run dashboard.py
```
Интерфейс откроется в браузере по адресу `http://localhost:8501`.

---

## ➕ Как добавить нового конкурента

Все конкуренты описываются декларативно в файле **`config.yaml`**. Код `src/main.py` не требует изменений — диспетчер автоматически выберет нужный модуль скрапинга на основе поля `type`.

### Вариант 1. Магазин на Shopify
Если сайт конкурента работает на платформе Shopify (каталог доступен через публичный endpoint `/products.json`):
```yaml
competitors:
  - name: "New Competitor Shopify"
    url: "https://newcompetitor.pl"
    type: "shopify"
```

### Вариант 2. Магазин с картой сайта (Sitemap XML)
Для крупных интернет-магазинов с открытым файлом `sitemap.xml`:
```yaml
competitors:
  - name: "New Competitor Sitemap"
    url: "https://newcompetitor.pl"
    sitemap_url: "https://newcompetitor.pl/sitemap.xml"
    type: "sitemap"
    product_pattern: ".html"    # Паттерн в URL для фильтрации служебных страниц
    max_items: 500              # (Опционально) лимит страниц для первого теста
```

### Вариант 3. Многоуровневый каталог (HTML Crawl)
Для каталогов на платформах OpenCart, PrestaShop и кастомных CMS, где требуется обход по категориям:
```yaml
competitors:
  - name: "New Competitor Crawl"
    url: "https://newcompetitor.pl/katalog"
    type: "crawl"
    rules:
      follow_patterns: ["/marka-", "/model-"]
      product_pattern: "dywaniki-"
      max_categories: 50
```

### Вариант 4. Прямая витрина / лендинг (Listing)
Для одностраничных каталогов и разделов со списком карточек:
```yaml
competitors:
  - name: "New Competitor Listing"
    url: "https://newcompetitor.pl/akcesoria"
    type: "listing"
    rules:
      card: ".product-item"
      title: ".product-title"
      price: ".price"
      link: "a"
```

После добавления нового блока в `config.yaml` запустите проверку:
```bash
python src/main.py --dry-run
```

---

## ⏱ Автоматизация и запуск по расписанию

### Linux / macOS (`cron`)
Откройте планировщик задач:
```bash
crontab -e
```
Добавьте задачу ежедневного запуска в 09:00:
```bash
0 9 * * * cd /home/user/EvaMats && ./venv/bin/python src/main.py >> cron.log 2>&1
```

### Windows (Планировщик заданий / Task Scheduler)
1. Откройте **Планировщик заданий (Task Scheduler)** $\rightarrow$ **Создать простую задачу (Create Basic Task)**.
2. Имя задачи: `EvaMats Competitor Monitoring`.
3. Триггер: **Ежедневно (Daily)** в 09:00.
4. Действие: **Запустить программу (Start a program)**:
   - **Программа или сценарий:** `C:\полный\путь\к\EvaMats\venv\Scripts\python.exe`
   - **Добавить аргументы:** `src/main.py`
   - **Рабочая папка:** `C:\полный\путь\к\EvaMats`

---

## 🛠 Инструменты разработки (Tools)

В папке `tools/` размещены скрипты для отладки селекторов и структуры целевых витрин:
- **`tools/inspect_html.py`** — скачивает исходный HTML витрины и форматирует его с отступами (`prettify`). Утилита для локального сохранения и форматирования разметки страницы при подборе CSS-селекторов комплектаций и микроразметки Schema.org

Запуск утилиты:
```bash
python tools/inspect_html.py
```

---

