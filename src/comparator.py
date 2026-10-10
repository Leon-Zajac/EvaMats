import re
import logging
from typing import List, Dict, Any

def parse_price_value(val: Any) -> float:
    """Извлекает строго числовое значение цены для исключения строковых ложных срабатываний."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    
    clean = re.sub(r"[^\d.,]", "", str(val)).replace(",", ".")
    try:
        if clean.count(".") > 1:
            parts = clean.split(".")
            clean = "".join(parts[:-1]) + "." + parts[-1]
        return float(clean) if clean else 0.0
    except ValueError:
        return 0.0

def normalize_title(title: str) -> str:
    """Убирает стоп-слова и лишние символы для устойчивого сопоставления карточек."""
    t = title.lower()
    t = re.sub(r"(dywaniki|eva|samochodowe|mata|do|\bomevo\b|®|™)", "", t)
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()

def detect_changes(old_data: List[Dict], new_data: List[Dict]) -> List[Dict]:
    """
    Интеллектуальное сравнение срезов каталога:
    - Двустороннее сопоставление (ID + нормализованный заголовок).
    - Защита от ложных срабатываний 'удален / добавлен' при смене URL.
    - Фильтрация шума и классификация важности согласно разделу 6.3 ТЗ.
    """
    changes = []
    
    # 1. Быстрые хэш-индексы старых данных
    old_by_id = {str(item.get("id")): item for item in old_data if item.get("id")}
    old_by_title = {normalize_title(item.get("title", "")): item for item in old_data if item.get("title")}
    
    # Хэш-индексы новых данных
    new_by_id = {str(item.get("id")): item for item in new_data if item.get("id")}
    new_by_title = {normalize_title(item.get("title", "")): item for item in new_data if item.get("title")}
    
    # Множество найденных совпадений старых товаров (чтобы гарантированно не отправлять их в 'удаленные')
    matched_old_ids = set()
    matched_old_titles = set()
    
    # 2. Поиск изменений цен и добавления новых товаров
    for item in new_data:
        item_id = str(item.get("id"))
        raw_title = item.get("title", "")
        norm_title = normalize_title(raw_title)
        
        # Товар считается существующим, если совпал либо его ID, либо нормализованное имя
        old_item = old_by_id.get(item_id) or old_by_title.get(norm_title)
        
        if old_item:
            # Отмечаем старый товар как успешно найденный
            matched_old_ids.add(str(old_item.get("id")))
            matched_old_titles.add(normalize_title(old_item.get("title", "")))
            
            old_variants = {v.get("title", "Standard"): v for v in old_item.get("variants", [])}
            new_variants = {v.get("title", "Standard"): v for v in item.get("variants", [])}
            
            for v_title, n_var in new_variants.items():
                if v_title in old_variants:
                    o_var = old_variants[v_title]
                    old_price_num = parse_price_value(o_var.get("price"))
                    new_price_num = parse_price_value(n_var.get("price"))
                    
                    # Проверяем математическое изменение цены (порог >= 1.0 zł отсекает шум округлений)
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
                            "product": f"{raw_title} ({v_title})",
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
                "product": raw_title if raw_title else "Неизвестный товар",
                "before": "Не было в каталоге",
                "after": str(price_repr),
                "type": "new_product",
                "importance": "средняя",
                "url": item.get("url", "")
            })
            
    # 3. Поиск действительно удаленных товаров
    for old_item in old_data:
        old_id = str(old_item.get("id"))
        old_title = old_item.get("title", "")
        old_norm_title = normalize_title(old_title)
        
        # Товар считается удаленным ТОЛЬКО если он не совпал ни по ID, ни по имени, ни в первом проходе
        is_still_present = (
            old_id in matched_old_ids or 
            old_norm_title in matched_old_titles or
            old_id in new_by_id or 
            old_norm_title in new_by_title
        )
        
        if not is_still_present:
            changes.append({
                "product": old_title if old_title else "Товар",
                "before": "Был в каталоге",
                "after": "Удален / Снят с продажи",
                "type": "deleted_product",
                "importance": "средняя",
                "url": old_item.get("url", "")
            })
            
    return changes