# app.py
import streamlit as st
import pandas as pd
import sqlite3
from database.db_core import init_db

st.set_page_config(layout="wide")
st.title("🍳 赤山堡砂鍋 後台管理")

# 執行初始化
init_db()

# 系統全域參數設定
st.sidebar.header("系統參數")
if 'current_user' not in st.session_state:
    st.session_state.current_user = "老闆"

st.session_state.current_user = st.sidebar.text_input("操作人員", value=st.session_state.current_user)

# 全局安全庫存預警
conn = sqlite3.connect('inventory.db')
df_alert_check = pd.read_sql_query('''
    SELECT p.prod_name, SUM(s.qty) as total_qty, p.safety_stock, p.use_unit
    FROM stock_batches s JOIN products p ON s.prod_id = p.prod_id
    GROUP BY s.prod_id HAVING total_qty < p.safety_stock
''', conn)
conn.close()

if not df_alert_check.empty:
    st.sidebar.subheader("⚠️ 缺貨補貨預警")
    for _, row in df_alert_check.iterrows():
        st.sidebar.error(f"【{row['prod_name']}】庫存僅剩 {row['total_qty']}{row['use_unit']} (安全線: {row['safety_stock']})")

# ==========================================
# 方案 B：直接在首頁呈現即時庫存
# ==========================================
st.subheader("📊 目前庫存明細")

# 新增分類篩選下拉選單
stock_filter = st.selectbox("🔍 篩選庫存類別", ["顯示全部明細", "僅看食材 (R)", "僅看用品 (S)"], key="home_stock_filter")

conn = sqlite3.connect('inventory.db')

# 根據選單動態調整 SQL 語法
if stock_filter == "僅看食材 (R)":
    query_condition = "WHERE s.prod_id LIKE 'R%'"
elif stock_filter == "僅看用品 (S)":
    query_condition = "WHERE s.prod_id LIKE 'S%'"
else:
    query_condition = "" # 全部顯示
    
df_stock = pd.read_sql_query(f'''
    SELECT s.batch_id as 批次編號, s.prod_id as 編號, p.prod_name as 商品名稱, 
           s.qty as 庫存量, p.use_unit as 單位, s.expiry_date as 有效期限, 
           p.safety_stock as 安全庫存, s.vendor_name as 供應商, s.vendor_phone as 供應商電話
    FROM stock_batches s JOIN products p ON s.prod_id = p.prod_id 
    {query_condition}
    ORDER BY s.prod_id, s.expiry_date ASC
''', conn)
conn.close()

if not df_stock.empty:
    st.dataframe(df_stock, use_container_width=True)
else:
    st.info("目前此類別無庫存，請先辦理採購進貨。")