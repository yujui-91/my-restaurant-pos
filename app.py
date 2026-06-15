import streamlit as st
import pandas as pd
import sqlite3
from database.db_core import init_db, trigger_toast, show_pending_toast

st.set_page_config(layout="wide")

# ==========================================
# 全域通知監聽器：置於最首行，確保重整完畢後平穩彈出通知
# ==========================================
show_pending_toast()

st.title("🍳 赤山堡砂鍋 後台管理")

# 執行初始化
init_db()

# 系統全域參數設定
st.sidebar.header("系統參數")
if 'current_user' not in st.session_state:
    st.session_state.current_user = "老闆"

st.session_state.current_user = st.sidebar.text_input("操作人員", value=st.session_state.current_user)

# ==========================================
# 需求 3：安全預警線隨時更改面板 (側邊欄)
# ==========================================
st.sidebar.markdown("---")
st.sidebar.subheader("⚙️ 快速微調安全庫存線")

conn = sqlite3.connect('inventory.db')
# 只對食材(R)與用品(S)且未下架停用的品項進行微調
all_items_for_safety = pd.read_sql_query("SELECT prod_id, prod_name, safety_stock, use_unit FROM products WHERE (prod_id LIKE 'R%' OR prod_id LIKE 'S%') AND price >= 0", conn)
conn.close()

if not all_items_for_safety.empty:
    selected_safety_item = st.sidebar.selectbox("選擇調整品項", all_items_for_safety['prod_id'] + " - " + all_items_for_safety['prod_name'], key="sb_safety_item_box")
    target_safety_id = selected_safety_item.split(" - ")[0]
    matched_safety_row = all_items_for_safety[all_items_for_safety['prod_id'] == target_safety_id].iloc[0]
    
    # 讓老闆可以直接在左邊微調更動安全線
    new_safety_value = st.sidebar.number_input(
        f"設定最低安全線 ({matched_safety_row['use_unit']})", 
        min_value=0.0, 
        value=float(matched_safety_row['safety_stock']), 
        step=1.0, 
        key="sb_safety_num_input"
    )
    
    if st.sidebar.button("💾 儲存新安全線設定"):
        conn = sqlite3.connect('inventory.db')
        cursor = conn.cursor()
        cursor.execute("UPDATE products SET safety_stock = ? WHERE prod_id = ?", (new_safety_value, target_safety_id))
        conn.commit()
        conn.close()
        
        # 替換為新宣告的全域通知發送器，避免被下方的 st.rerun() 刷掉
        trigger_toast(f"已將 【{matched_safety_row['prod_name']}】 的安全線更新為 {new_safety_value}", icon="⚙️")
        st.rerun()

# --- 計算當前哪些項目低於安全庫存線 (排除已下架停用項目) ---
# 技術修正：改用精確子查詢，確保當批次總庫存扣到變為 0 或是無批次資料時，亦能正確計算並拉起跑馬燈警報
conn = sqlite3.connect('inventory.db')
df_alert_check = pd.read_sql_query('''
    SELECT p.prod_name, 
           COALESCE((SELECT SUM(s.qty) FROM stock_batches s WHERE s.prod_id = p.prod_id), 0) as total_qty, 
           p.safety_stock, p.use_unit
    FROM products p 
    WHERE p.status = 1 AND (p.prod_id LIKE 'R%' OR p.prod_id LIKE 'S%')
    GROUP BY p.prod_id 
    HAVING total_qty < p.safety_stock
''', conn)
conn.close()

# ==========================================
# 需求 3：首頁頂部動態跑馬燈低庫存警告提示
# ==========================================
if not df_alert_check.empty:
    alert_messages = []
    for _, row in df_alert_check.iterrows():
        alert_messages.append(f"【{row['prod_name']}】僅剩 {row['total_qty']:.1f}{row['use_unit']} (安全線: {row['safety_stock']:.1f})")
    
    # 在網頁正上方亮起跑馬燈警告
    st.warning("⚠️ **【低庫存補貨預警跑馬燈】** 🚨 " + " ｜ " + " ｜ ".join(alert_messages))

# ==========================================
# 首頁：呈現即時庫存
# ==========================================
st.subheader("📊 目前庫存明細")

stock_filter = st.selectbox("🔍 篩選庫存類別", ["顯示全部明細", "僅看食材 (R)", "僅看用品 (S)"], key="home_stock_filter")

conn = sqlite3.connect('inventory.db')

if stock_filter == "僅看食材 (R)":
    query_condition = "WHERE s.prod_id LIKE 'R%' AND p.price >= 0"
elif stock_filter == "僅看用品 (S)":
    query_condition = "WHERE s.prod_id LIKE 'S%' AND p.price >= 0"
else:
    query_condition = "WHERE p.price >= 0"
    
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