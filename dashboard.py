import streamlit as st
import sqlite3
import json
import glob
import os
import pandas as pd
import plotly.express as px

st.set_page_config(
    page_title="EvaMats — Competitor Intelligence",
    page_icon="🚗",
    layout="wide"
)

st.title("🚗 EvaMats — Мониторинг конкурентов (Польша)")
st.markdown("Дашборд аналитики цен, структуры каталогов и истории изменений.")

DB_PATH = "data/tracking.db"
STORAGE_DIR = "storage"

# --- 1. ЗАГРУЗКА ТЕКУЩЕГО СОСТОЯНИЯ КАТАЛОГОВ (SQLite) ---
@st.cache_data(ttl=60)
def load_current_catalogs():
    if not os.path.exists(DB_PATH):
        return {}
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT competitor, data FROM state WHERE competitor NOT LIKE '%_promo'")
        rows = cursor.fetchall()
        catalogs = {}
        for comp, raw_data in rows:
            catalogs[comp] = json.loads(raw_data)
        return catalogs

catalogs = load_current_catalogs()

# --- 2. МЕТРИКИ КАТАЛОГОВ ---
st.subheader("📊 Текущее состояние витрин")

cols = st.columns(len(catalogs) if catalogs else 1)
total_products = 0

all_products_flat = []
for idx, (comp, items) in enumerate(catalogs.items()):
    total_products += len(items)
    with cols[idx]:
        st.metric(label=comp, value=f"{len(items)} товаров")
    
    for item in items:
        for v in item.get("variants", []):
            try:
                # Извлекаем числовую цену
                p_str = v.get("price", "0").replace("zł", "").replace(",", ".").strip()
                p_val = float(p_str)
            except ValueError:
                p_val = 0.0
            
            if p_val > 0:
                all_products_flat.append({
                    "Конкурент": comp,
                    "Товар": item.get("title", ""),
                    "Модификация": v.get("title", ""),
                    "Цена (PLN)": p_val
                })

df_products = pd.DataFrame(all_products_flat)

if not df_products.empty:
    col_chart1, col_chart2 = st.columns(2)
    with col_chart1:
        fig_box = px.box(
            df_products, 
            x="Конкурент", 
            y="Цена (PLN)", 
            color="Конкурент",
            title="Распределение цен по конкурентам"
        )
        st.plotly_chart(fig_box, use_container_width=True)

    with col_chart2:
        avg_prices = df_products.groupby("Конкурент")["Цена (PLN)"].mean().reset_index()
        fig_bar = px.bar(
            avg_prices,
            x="Конкурент",
            y="Цена (PLN)",
            color="Конкурент",
            text_auto=".1f",
            title="Средняя цена позиции (PLN)"
        )
        st.plotly_chart(fig_bar, use_container_width=True)

# --- 3. ИСТОРИЯ ИЗМЕНЕНИЙ (JSONL) ---
st.divider()
st.subheader("📜 Журнал зафиксированных изменений")

jsonl_files = glob.glob(os.path.join(STORAGE_DIR, "*_changes.jsonl"))
all_changes = []

for file_path in jsonl_files:
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    all_changes.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

if all_changes:
    df_changes = pd.DataFrame(all_changes)
    
    # Сортировка по времени (свежие сверху)
    if "timestamp" in df_changes.columns:
        df_changes = df_changes.sort_values(by="timestamp", ascending=False)

    # Фильтры
    filter_col1, filter_col2, filter_col3 = st.columns(3)
    with filter_col1:
        comp_filter = st.multiselect(
            "Конкурент:", 
            options=df_changes["competitor"].unique(), 
            default=df_changes["competitor"].unique()
        )
    with filter_col2:
        type_filter = st.multiselect(
            "Тип события:", 
            options=df_changes["type"].unique(), 
            default=df_changes["type"].unique()
        )
    with filter_col3:
        imp_filter = st.multiselect(
            "Важность:", 
            options=df_changes["importance"].unique() if "importance" in df_changes.columns else ["все"], 
            default=df_changes["importance"].unique() if "importance" in df_changes.columns else ["все"]
        )

    # Применение фильтров
    filtered_df = df_changes[
        (df_changes["competitor"].isin(comp_filter)) &
        (df_changes["type"].isin(type_filter))
    ]
    if "importance" in df_changes.columns:
        filtered_df = filtered_df[filtered_df["importance"].isin(imp_filter)]

    # Отображение таблицы
    columns_to_show = ["timestamp", "competitor", "type", "importance", "product", "before", "after"]
    existing_cols = [c for c in columns_to_show if c in filtered_df.columns]
    
    st.dataframe(
        filtered_df[existing_cols],
        use_container_width=True,
        hide_index=True
    )
else:
    st.info("История изменений пока пуста. Запустите парсер повторно для фиксации событий.")