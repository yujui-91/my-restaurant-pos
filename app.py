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

# 💡 滿足需求 2：強制過濾，不論何種篩選模式都只抓取 R 或 S 開頭，絕不包含 C 類型帳單費用
if stock_filter == "僅看食材 (R)":
    query_condition = "WHERE s.prod_id LIKE 'R%'"
elif stock_filter == "僅看用品 (S)":
    query_condition = "WHERE s.prod_id LIKE 'S%'"
else:
    query_condition = "WHERE (s.prod_id LIKE 'R%' OR s.prod_id LIKE 'S%')"
    
df_stock = pd.read_sql_query(f'''
    SELECT s.batch_id as 批次編號, s.prod_id as 編號, p.prod_name as 商品名稱, 
           s.qty as 庫存量, p.use_unit as 單位, s.expiry_date as 有效期限, 
           p.safety_stock as 安全庫存, s.vendor_name as 供應商, s.vendor_phone as 供應商電話,
           p.status as 狀態碼
    FROM stock_batches s JOIN products p ON s.prod_id = p.prod_id 
    {query_condition}
    ORDER BY p.status DESC, s.prod_id, s.expiry_date ASC
''', conn)
conn.close()

if not df_stock.empty:
    # 重新調整欄位順序：移除舊的「狀態」文字欄位，保留狀態碼以便進行條件上色
    show_cols = ['批次編號', '編號', '商品名稱', '庫存量', '單位', '有效期限', '安全庫存', '供應商', '供應商電話', '狀態碼']
    df_display = df_stock[show_cols]
    
    # 💡 滿足需求 2：高亮紅色邏輯（僅有商品名稱欄位變紅底白字，其餘不變）
    def highlight_disabled(row):
        styles = [''] * len(row)
        name_idx = row.index.get_loc('商品名稱')
        status_idx = row.index.get_loc('狀態碼')
        
        # 狀態碼為 0 代表已下架停用
        if row.iloc[status_idx] == 0:
            styles[name_idx] = 'background-color: #ffcccc; color: #cc0000; font-weight: bold;'
        return styles
    
    # 呼叫 st.dataframe 顯示，並隱藏內部判斷用的「狀態碼」欄位避免干擾畫面
    st.dataframe(
        df_display.style.apply(highlight_disabled, axis=1), 
        use_container_width=True, 
        column_config={"狀態碼": None}
    )
    
    # ==========================================
    # 徹底刪除已下架品項的歷史庫存
    # ==========================================
    disabled_items = df_stock[df_stock['狀態碼'] == 0]
    if not disabled_items.empty:
        st.markdown("---")
        st.markdown("##### 🗑️ 徹底刪除已下架品項的歷史庫存")
        st.caption("如果您不想在上方看到這些紅色的下架品項，可以在下方選擇將該批次庫存徹底從系統中刪除：")
        
        del_options = disabled_items.apply(
            lambda r: f"【批次 {int(r['批次編號'])}】{r['編號']}-{r['商品名稱']} (剩餘庫存: {r['庫存量']}{r['單位']})", axis=1
        ).tolist()
        
        target_del_str = st.selectbox("🎯 選擇要永久刪除的下架庫存批次", del_options, key="home_del_disabled_batch")
        
        if st.button("❌ 確認從庫存明細中刪除此批次", type="primary"):
            target_batch_id = int(target_del_str.split("【批次 ")[1].split("】")[0])
            matched_del_row = disabled_items[disabled_items['批次編號'] == target_batch_id].iloc[0]
            
            conn = sqlite3.connect('inventory.db')
            cursor = conn.cursor()
            cursor.execute("DELETE FROM stock_batches WHERE batch_id = ?", (target_batch_id,))
            conn.commit()
            conn.close()
            
            from database.db_core import log_history
            log_history(st.session_state.current_user, "庫存批次徹底刪除", f"老闆在首頁清除了已下架品項的殘留庫存：{matched_del_row['商品名稱']}(批次:{target_batch_id})")
            
            trigger_toast(f"已成功刪除 【{matched_del_row['商品名稱']}】 批次 {target_batch_id} 的庫存資料！", icon="🗑️")
            st.rerun()
else:
    st.info("目前此類別無庫存，請先辦理採購進貨。")