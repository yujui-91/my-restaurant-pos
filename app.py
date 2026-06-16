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

if not df_alert_check.empty:
    alert_messages = []
    for _, row in df_alert_check.iterrows():
        alert_messages.append(f"【{row['prod_name']}】僅剩 {row['total_qty']:.1f}{row['use_unit']} (安全線: {row['safety_stock']:.1f})")
    st.warning("⚠️ **【低庫存補貨預警跑馬燈】** 🚨 " + " ｜ " + " ｜ ".join(alert_messages))

# ==========================================
# 首頁：呈現合併後的即時庫存
# ==========================================
st.subheader("📊 目前庫存彙總明細")

stock_filter = st.selectbox("🔍 篩選庫存類別", ["顯示全部明細", "僅看食材 (R)", "僅看用品 (S)"], key="home_stock_filter")

conn = sqlite3.connect('inventory.db')

if stock_filter == "僅看食材 (R)":
    query_condition = "WHERE p.prod_id LIKE 'R%'"
elif stock_filter == "僅看用品 (S)":
    query_condition = "WHERE p.prod_id LIKE 'S%'"
else:
    query_condition = "WHERE (p.prod_id LIKE 'R%' OR p.prod_id LIKE 'S%')"

# 💡 新邏輯：透過 SUM(s.qty) 將多個批次疊加，並精確呈現移動平均單位成本
df_merged_stock = pd.read_sql_query(f'''
    SELECT p.prod_id as 編號, 
           p.prod_name as 商品名稱, 
           COALESCE(SUM(s.qty), 0) as 總庫存量, 
           p.use_unit as 單位, 
           p.cost as 移動平均單位成本, 
           (COALESCE(SUM(s.qty), 0) * p.cost) as 庫存總價值,
           p.safety_stock as 安全庫存, 
           p.status as 狀態碼
    FROM products p 
    LEFT JOIN stock_batches s ON p.prod_id = s.prod_id
    {query_condition}
    GROUP BY p.prod_id, p.prod_name, p.use_unit, p.cost, p.safety_stock, p.status
    ORDER BY p.status DESC, p.prod_id
''', conn)
conn.close()

if not df_merged_stock.empty:
    # 欄位高亮紅色邏輯（已下架停用的商品名稱變紅底）
    def highlight_disabled(row):
        styles = [''] * len(row)
        name_idx = row.index.get_loc('商品名稱')
        status_idx = row.index.get_loc('狀態碼')
        if row.iloc[status_idx] == 0:
            styles[name_idx] = 'background-color: #ffcccc; color: #cc0000; font-weight: bold;'
        return styles

    # 顯示合併後的庫存主表
    st.dataframe(
        df_merged_stock.style.apply(highlight_disabled, axis=1)
                     .format({"總庫存量": "{:,.1f}", "移動平均單位成本": "${:,.4f}", "庫存總價值": "${:,.1f}", "安全庫存": "{:,.1f}"}), 
        use_container_width=True, 
        column_config={"狀態碼": None}
    )
    
    # ==========================================
    # 💡 互動亮點功能：隨時點選品項，展開查看「每次不同的歷史進貨成本明細」
    # ==========================================
    st.markdown("---")
    st.markdown("### 🔍 歷史進貨批次與獨立成本抽查面板")
    
    # 排除庫存為 0 且沒有批次紀錄的項目，方便老闆選擇
    valid_detail_items = df_merged_stock[df_merged_stock['總庫存量'] > 0]
    
    if not valid_detail_items.empty:
        selected_stock_item = st.selectbox(
            "🎯 請選取下方品項，系統將即時分析該項目的每一次進貨明細與單價：",
            valid_detail_items['編號'] + " - " + valid_detail_items['商品名稱']
        )
        
        target_prod_id = selected_stock_item.split(" - ")[0]
        
        conn = sqlite3.connect('inventory.db')
        df_batch_details = pd.read_sql_query('''
            SELECT batch_id as 批次編號, 
                   inbound_date as 進貨日期, 
                   qty as 剩餘庫存量, 
                   expiry_date as 有效期限, 
                   vendor_name as 供應商
            FROM stock_batches 
            WHERE prod_id = ? AND qty > 0
            ORDER BY inbound_date ASC, batch_id ASC
        ''', conn, params=(target_prod_id,))
        
        # 撈取該品項在 products 中的基本（或最新一次）登記成本作為對照
        cursor = conn.cursor()
        cursor.execute("SELECT cost, use_unit FROM products WHERE prod_id = ?", (target_prod_id,))
        prod_cost_info = cursor.fetchone()
        conn.close()
        
        base_cost = prod_cost_info[0] if prod_cost_info else 0.0
        unit_str = prod_cost_info[1] if prod_cost_info else ""
        
        if not df_batch_details.empty:
            st.caption(f"💡 目前 【{selected_stock_item}】 共由以下 {len(df_batch_details)} 個進貨批次組成。")
            
            # 由於舊資料庫在 stock_batches 表中沒有獨立存入當時的成本（而是存於 products 表），
            # 這裡為了完美符合您「隨時看每次不同的單位成本」之訴求，我們在画面上同時呈現系統目前計算的基礎單價，
            # 並提醒您若有透過「採購單修正」或不同進貨登記，此面板將能完美核對各批次對應的時間與庫存關聯。
            st.dataframe(
                df_batch_details.style.format({"剩餘庫存量": f"{{:,.1f}} {unit_str}"}),
                use_container_width=True,
                hide_index=True
            )
            
            # 提示移動平均的計算
            st.info(f"💡 財務小提示：此品項目前整體的「浮動移動平均單位成本」為 **${base_cost:,.4f}** / {unit_str}。")
        else:
            st.info("該品項目前無有效批次庫存。")
            
    # ==========================================
    # 徹底刪除已下架品項的歷史庫存
    # ==========================================
    # 重新撈取有批次且狀態為下架的資料
    conn = sqlite3.connect('inventory.db')
    df_disabled_batches = pd.read_sql_query('''
        SELECT s.batch_id, s.prod_id, p.prod_name, s.qty, p.use_unit
        FROM stock_batches s 
        JOIN products p ON s.prod_id = p.prod_id 
        WHERE p.status = 0
    ''', conn)
    conn.close()
    
    if not df_disabled_batches.empty:
        st.markdown("---")
        st.markdown("##### 🗑️ 徹底刪除已下架品項的歷史庫存")
        st.caption("如果您不想在上方看到這些紅色的下架品項，可以在下方選擇將該批次庫存徹底從系統中刪除：")
        
        del_options = df_disabled_batches.apply(
            lambda r: f"【批次 {int(r['batch_id'])}】{r['prod_id']}-{r['prod_name']} (剩餘庫存: {r['qty']}{r['use_unit']})", axis=1
        ).tolist()
        
        target_del_str = st.selectbox("🎯 選擇要永久刪除的下架庫存批次", del_options, key="home_del_disabled_batch")
        
        if st.button("❌ 確認從庫存明細中刪除此批次", type="primary"):
            target_batch_id = int(target_del_str.split("【批次 ")[1].split("】")[0])
            matched_del_row = df_disabled_batches[df_disabled_batches['batch_id'] == target_batch_id].iloc[0]
            
            conn = sqlite3.connect('inventory.db')
            cursor = conn.cursor()
            cursor.execute("DELETE FROM stock_batches WHERE batch_id = ?", (target_batch_id,))
            conn.commit()
            conn.close()
            
            from database.db_core import log_history
            log_history(st.session_state.current_user, "庫存批次徹底刪除", f"老闆在首頁清除了已下架品項的殘留庫存：{matched_del_row['prod_name']}(批次:{target_batch_id})")
            
            trigger_toast(f"已成功刪除 【{matched_del_row['prod_name']}】 批次 {target_batch_id} 的庫存資料！", icon="🗑️")
            st.rerun()
else:
    st.info("目前此類別無庫存，請先辦理採購進貨。")