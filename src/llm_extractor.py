import os
import json
import requests
import logging
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

def evaluate_change_with_llm(competitor: str, product_name: str, change_type: str, before: str, after: str) -> dict:
    """Оценивает важность изменения и формирует вывод через LLM."""
    url = "https://openrouter.ai/api/v1/chat/completions"
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    system_prompt = """
    Ты - аналитик конкурентов. Твоя задача: оценить изменение на сайте конкурента и вернуть ответ СТРОГО в формате JSON.
    Правила важности (importance):
    - high: изменение цены более чем на 10%, новая акция.
    - medium: добавлен/удален товар, мелкое изменение цены.
    - low: косметические правки.
    
    Формат ответа (только валидный JSON, без markdown-разметки):
    {
        "importance": "high|medium|low",
        "summary": "Краткий вывод на русском языке: что изменилось и почему это важно для нас."
    }
    """

    user_prompt = f"Конкурент: {competitor}. Товар: {product_name}. Тип изменения: {change_type}. Было: {before}, Стало: {after}."

    payload = {
        "model": "openai/gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=20)
        
        # Если статус не 200, принудительно вызываем исключение
        response.raise_for_status()
        
        result_json = response.json()
        content = result_json['choices'][0]['message']['content']
        
        # Очищаем строку от markdown (если модель вернет ```json ... ```)
        clean_content = content.replace("```json", "").replace("```", "").strip()
        
        return json.loads(clean_content)
        
    except requests.exceptions.HTTPError as http_err:
        # Логируем точную причину от сервера при ошибке 400
        logging.error(f"HTTP Ошибка от OpenRouter: {http_err.response.text}")
        return {"importance": "low", "summary": "Ошибка HTTP при запросе к LLM."}
    except Exception as e:
        logging.error(f"Внутренняя ошибка парсинга: {e}")
        return {"importance": "low", "summary": "Ошибка парсинга ответа LLM."}

if __name__ == "__main__":
    if not OPENROUTER_API_KEY:
        logging.error("Ключ OPENROUTER_API_KEY не найден! Проверьте файл .env")
    else:
        logging.info("Тестируем запрос к LLM через OpenRouter...")
        
        evaluation = evaluate_change_with_llm(
            competitor="evasota.pl",
            product_name="Комплект ковриков VW Golf",
            change_type="price_change",
            before="189 PLN",
            after="149 PLN"
        )
        
        print("\n--- Ответ от LLM ---")
        print(json.dumps(evaluation, indent=2, ensure_ascii=False))
        print("--------------------\n")