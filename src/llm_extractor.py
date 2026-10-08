import os
import json
import requests
import logging
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
DEFAULT_MODEL = "google/gemini-3.8-flash"


def _clean_json_response(content: str) -> dict:
    """Универсально очищает и валидирует JSON-ответ от LLM."""
    if not content:
        return {}
    clean = content.strip()
    if clean.startswith("```json"):
        clean = clean[7:]
    elif clean.startswith("```"):
        clean = clean[3:]
    if clean.endswith("```"):
        clean = clean[:-3]
    return json.loads(clean.strip())


def evaluate_change_with_llm(
    competitor: str, 
    product_name: str, 
    change_type: str, 
    before: str, 
    after: str,
    model_name: str = DEFAULT_MODEL
) -> dict:
    """Оценивает важность изменения цены или карточки товара и формирует бизнес-вывод."""
    if not OPENROUTER_API_KEY:
        logging.warning("OPENROUTER_API_KEY не задан. LLM анализ пропущен.")
        return {"importance": "low", "summary": "API ключ не настроен."}

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    system_prompt = """
    Ты — ведущий e-commerce аналитик на рынке автомобильных EVA-ковриков в Польше.
    Твоя задача — оценить действие конкурента и дать короткий бизнес-вывод (1–2 предложения).
    
    ПРАВИЛА ДЛЯ SUMMARY:
    - Не повторяй буквально название товара и цену — они уже указаны в карточке выше.
    - Сфокусируйся на сути и рекомендации: это демпинг, расширение редкой ниши, вывод неликвида, сигнал скорректировать наши цены или запустить рекламу на эту модель авто.
    
    Правила важности (importance):
    - high: изменение цены более чем на 10%, появление ощутимой скидки.
    - medium: добавлен/удален товар, изменение цены от 3% до 10%.
    - low: косметические правки, колебания до 3%.
    
    Отвечай СТРОГО в формате JSON без markdown-разметки:
    {
        "importance": "high|medium|low",
        "summary": "Четкий бизнес-вывод с рекомендацией к действию."
    }
    """

    user_prompt = f"Конкурент: {competitor}. Товар: {product_name}. Тип изменения: {change_type}. Было: {before}, Стало: {after}."

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.2
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        response.raise_for_status()
        
        result_json = response.json()
        message = result_json.get('choices', [{}])[0].get('message', {})
        content = message.get('content')
        
        if content is None:
            logging.error(f"Пустой ответ LLM: {json.dumps(result_json, ensure_ascii=False)}")
            return {"importance": "low", "summary": "Ответ заблокирован фильтром модели."}
        
        return _clean_json_response(content)
        
    except requests.exceptions.HTTPError as http_err:
        logging.error(f"HTTP ошибка от OpenRouter: {http_err.response.text}")
        return {"importance": "low", "summary": "Ошибка HTTP при запросе к LLM."}
    except Exception as e:
        logging.error(f"Ошибка парсинга ответа LLM: {e}")
        return {"importance": "low", "summary": "Ошибка парсинга ответа модели."}


def evaluate_promo_change_with_llm(
    competitor: str, 
    before: str, 
    after: str, 
    model_name: str = DEFAULT_MODEL
) -> dict:
    """Анализирует смену главного оффера, УТП, баннера акции или условий доставки на главной странице."""
    if not OPENROUTER_API_KEY:
        logging.warning("OPENROUTER_API_KEY не задан. LLM анализ промо пропущен.")
        return {"importance": "medium", "summary": "Обнаружено обновление промо-блока (без LLM)."}

    url = "[https://openrouter.ai/api/v1/chat/completions](https://openrouter.ai/api/v1/chat/completions)"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    system_prompt = """
    Ты - старший аналитик e-commerce рынка автомобильных ковриков.
    Твоя задача: оценить изменение промо-баннеров, главного заголовка или акций на главной странице конкурента.
    
    Правила важности (importance):
    - high: запуск новой акции, изменение условий бесплатной доставки, появление промокода/скидки, кардинальная смена УТП.
    - medium: обновление сезонного слогана, добавление нового преимущества без изменения скидок.
    - low: косметические правки формулировок, смена порядка слов.
    
    Верни ответ СТРОГО в валидном JSON без разметки markdown:
    {
        "importance": "high|medium|low",
        "summary": "Краткий вывод на русском языке (1-2 предложения): суть изменения и что оно означает для нашей стратегии."
    }
    """

    user_prompt = f"Конкурент: {competitor}.\nБЫЛО на главной:\n{before}\n\nСТАЛО на главной:\n{after}"

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.2
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        response.raise_for_status()
        
        content = response.json().get('choices', [{}])[0].get('message', {}).get('content', '')
        return _clean_json_response(content)
    except Exception as e:
        logging.error(f"Ошибка LLM при оценке промо-акции: {e}")
        return {
            "importance": "medium", 
            "summary": "Обнаружена смена главного баннера или УТП на сайте конкурента."
        }


if __name__ == "__main__":
    if not OPENROUTER_API_KEY:
        logging.error("Ключ OPENROUTER_API_KEY не найден! Проверьте файл .env")
    else:
        logging.info("1. Тестируем запрос по изменению цены товара...")
        price_eval = evaluate_change_with_llm(
            competitor="evasota.pl",
            product_name="Комплект ковриков VW Golf",
            change_type="price_change",
            before="189 PLN",
            after="149 PLN"
        )
        print(json.dumps(price_eval, indent=2, ensure_ascii=False))

        logging.info("\n2. Тестируем запрос по смене главного промо-оффера...")
        promo_eval = evaluate_promo_change_with_llm(
            competitor="primeeva.pl",
            before="Darmowa dostawa od 250 zł | Dywaniki samochodowe EVA",
            after="Wiosenna promocja: -15% z kodem SPRING | Darmowa dostawa od 200 zł"
        )
        print(json.dumps(promo_eval, indent=2, ensure_ascii=False))