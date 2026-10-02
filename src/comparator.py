import logging

def clean_price(price_str):
    """Очищает строку с ценой и превращает ее в число для математики."""
    if not isinstance(price_str, str):
        return float(price_str)
    # Убираем валюту, пробелы и меняем запятую на точку
    cleaned = ''.join(c for c in price_str if c.isdigit() or c in '.,')
    cleaned = cleaned.replace(',', '.')
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0

def evaluate_importance(change_type, before, after):
    """Определяет важность согласно п. 6.3 ТЗ."""
    if change_type == "price_change":
        old_f = clean_price(before)
        new_f = clean_price(after)
        
        if old_f > 0:
            diff_percent = abs(new_f - old_f) / old_f * 100
            if diff_percent > 10:
                return "высокая"  # Изменение цены > 10%
            else:
                return "низкая"   # Косметическое изменение цены
                
    elif change_type in ["new_product", "deleted_product"]:
        return "средняя"  # Добавлен или удален товар[cite: 15]
        
    return "низкая"

def detect_changes(old_data, new_data):
    """Сравнивает данные, находит разницу и классифицирует её важность."""
    changes = []
    old_dict = {str(item['id']): item for item in old_data}
    new_dict = {str(item['id']): item for item in new_data}
    
    # 1. Ищем новые товары и изменения цен
    for item_id, new_item in new_dict.items():
        if item_id in old_dict:
            old_variants = {v['title']: v for v in old_dict[item_id]['variants']}
            for new_var in new_item['variants']:
                var_title = new_var['title']
                if var_title in old_variants:
                    old_price = old_variants[var_title]['price']
                    new_price = new_var['price']
                    
                    if old_price != new_price:
                        importance = evaluate_importance("price_change", old_price, new_price)
                        changes.append({
                            "product": f"{new_item['title']} ({var_title})",
                            "before": old_price,
                            "after": new_price,
                            "type": "price_change",
                            "importance": importance
                        })
        else:
            # Товар есть в новых данных, но нет в старых
            importance = evaluate_importance("new_product", None, None)
            changes.append({
                "product": new_item['title'],
                "before": "Не было",
                "after": new_item['variants'][0]['price'] if new_item['variants'] else "Нет цены",
                "type": "new_product",
                "importance": importance
            })
            
    # 2. Ищем удаленные товары
    for item_id, old_item in old_dict.items():
        if item_id not in new_dict:
            importance = evaluate_importance("deleted_product", None, None)
            changes.append({
                "product": old_item['title'],
                "before": old_item['variants'][0]['price'] if old_item['variants'] else "Был",
                "after": "Удален",
                "type": "deleted_product",
                "importance": importance
            })
            
    return changes