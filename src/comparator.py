import re
import logging
from typing import List, Dict, Any

def parse_price_value(val: Any) -> float:
    """Извлекает строго числовое значение цены для исключения строковых ложных срабатываний."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    
    # Очистка строки: удаляем валюты, пробелы, заменяем запятую
    clean = re.sub(r"[^\d.,]", "", str(val)).replace(",", ".")
    try:
        # Если в строке несколько точек, оставляем последнюю
        if clean.count(".") > 1:
            parts = clean.split(".")
            clean = "".join(parts[:-1]) + "." + parts[-1]
        return float(clean) if clean else 0.0
    except ValueError:
        return 0.0

def normalize_title(title: str) -> str:
    """Убирает мусорные слова для точного сопоставления карточек."""
    t = title.lower()
    t = re.sub(r"(dywaniki|eva|samochodowe|mata|do|\bomevo\b|®|™)", "", t)
    return re.sub(r"\s+", " ", t).strip()

def detect_changes(old_data: List[Dict], new_data: List[Dict]) -> List[Dict]:
    """Интеллектуальное сравнение данных с защитой от ложных срабатываний."""
    changes = []
    
    # Строим маппинг как по ID, так и по нормализованному названию
    old_by_id = {str(item.get("id")): item for item in old_data}
    old_by_title = {normalize_title(item.get("title", "")): item for item in old_data if item.get("title")}
    
    new_by_id = {str(item.get("id")): item for item in new_data}
    
    # 1. Поиск изменений цен и модификаций
    for item in new_data:
        item_id = str(item.get("id"))
        norm_title = normalize_title(item.get("title", ""))
        
        # Находим старую версию товара (по ID или по нормализованному имени)
        old_item = old_by_id.get(item_id) or old_by_title.get(norm_title)
        
        if old_item:
            old_variants = {v.get("title", "Standard"): v for v in old_item.get("variants", [])}
            new_variants = {v.get("title", "Standard"): v for v in item.get("variants", [])}
            
            for v_title, n_var in new_variants.items():
                if v_title in old_variants:
                    o_var = old_variants[v_title]
                    old_price_num = parse_price_value(o_var.get("price"))
                    new_price_num = parse_price_value(n_var.get("price"))
                    
                    # Проверяем реальное математическое изменение цены (порог > 1 zł, чтобы игнорировать округления)
                    if abs(new_price_num - old_price_num) >= 1.0 and old_price_num > 0:
                        diff_pct = round(((new_price_num - old_price_num) / old_price_num) * 100, 1)
                        
                        # Классификация важности согласно п. 6.3 ТЗ
                        if abs(diff_pct) >= 10.0:
                            importance = "высокая"
                        elif abs(diff_pct) >= 3.0:
                            importance = "средняя"
                        else:
                            importance = "низкая"
                            
                        changes.append({
                            "product": f"{item.get('title')} ({v_title})",
                            "before": f"{old_price_num:.2f} zł",
                            "after": f"{new_price_num:.2f} zł",
                            "diff_percent": f"{diff_pct:+}%",
                            "type": "price_change",
                            "importance": importance,
                            "url": item.get("url", "")
                        })
        else:
            # Новый товар в каталоге
            price_repr = item.get("variants", [{}])[0].get("price", "Уточняется")
            changes.append({
                "product": item.get("title", "Неизвестный товар"),
                "before": "Не было в каталоге",
                "after": str(price_repr),
                "type": "new_product",
                "importance": "средняя",  # п. 6.3 ТЗ: Добавлен товар -> средняя
                "url": item.get("url", "")
            })
            
    # 2. Поиск удаленных товаров
    for old_item in old_data:
        old_id = str(old_item.get("id"))
        old_norm_title = normalize_title(old_item.get("title", ""))
        
        if old_id not in new_by_id and old_norm_title not in [normalize_title(x.get("title", "")) for x in new_data]:
            changes.append({
                "product": old_item.get("title", "Товар"),
                "before": "Был в каталоге",
                "after": "Удален / Снят с продажи",
                "type": "deleted_product",
                "importance": "средняя",  # п. 6.3 ТЗ: Удален товар -> средняя
                "url": old_item.get("url", "")
            })
            
    return changes