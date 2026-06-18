# app.py
import streamlit as st
import pandas as pd
import sqlite3
from database.db_core import init_db, trigger_toast, show_pending_toast, log_history

st.set_page_config(layout="wide")

st.markdown("""
    <style>
        [data-testid="stDataFrame"] td, [data-testid="stDataFrame"] th {
            font-size: 15px !important;
            padding: 6px 8px !important;
        }
        .stAlert p {
            font-size: 15px !important;
            font-weight: 500;
        }
    </style>
""", unsafe_allow_html=True)

show_pending_toast()

st.title("🍳 赤山堡砂鍋 後台管理")

init_db()

st.sidebar.header("系統參數")
if 'current_user' not in st.session_state:
    st.session_state.current_user = "老闆"

st.session_state.current_user = st.sidebar.text_input("操作人員", value=st.session_state.current_user)

st.sidebar.markdown("---")
st.sidebar.subheader("⚙️ 快速微調安全庫存線")

conn = sqlite3.connect('inventory.db')
all_items_for_safety = pd.read_sql_query("SELECT prod_id, prod_name, safety_stock, use_unit FROM products WHERE (prod_id LIKE 'R%' OR prod_id LIKE 'S%') AND price >= 0", conn)
conn.close()

if not all_items_for_safety.empty:
    selected_safety_item = st.sidebar.selectbox("選擇調整品項", all_items_for_safety['prod_id'] + " - " + all_items_for_safety['prod_name'], key="sb_safety_item_box")
    target_safety_id = selected_safety_item.split(" - ")[0]
    matched_safety_row = all_items_for_safety[all_items_for_safety['prod_id'] == target_safety_id].iloc[0]
    
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
        
        # 🔥 【歷史紀錄埋點優化】改為指定大分類為：⚙️ 餐點參數修正
        log_history(
            st.session_state.current_user, 
            f"修正餐點參數-安全庫存變更", 
            f"操作人員微調了安全庫存線：【{matched_safety_row['prod_name']}】({target_safety_id})，新安全線設定為: {new_safety_value} {matched_safety_row['use_unit']}。",
            main_category="⚙️ 餐點參數修正"
        )
        
        trigger_toast(f"已將 【{matched_safety_row['prod_name']}】 的安全線更新為 {new_safety_value}", icon="⚙️")
        st.rerun()

conn = sqlite3.connect('inventory.db')
df_alert_check = pd.read_sql_query('''
    SELECT p.prod_name, 
           COALESCE((SELECT SUM(s.qty) FROM stock_batches s WHERE s.prod_id = p.prod_id AND s.qty > 0), 0) as total_qty, 
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

st.subheader("📊 目前庫存明細")

stock_filter = st.selectbox("🔍 篩選庫存類別", ["顯示全部明細", "僅看食材 (R)", "僅看用品 (S)"], key="home_stock_filter")

conn = sqlite3.connect('inventory.db')

if stock_filter == "僅看食材 (R)":
    query_condition = "WHERE p.prod_id LIKE 'R%'"
elif stock_filter == "僅看用品 (S)":
    query_condition = "WHERE p.prod_id LIKE 'S%'"
else:
    query_condition = "WHERE (p.prod_id LIKE 'R%' OR p.prod_id LIKE 'S%')"

# 核心優化：計算各批次 (剩餘量 * 該批單價) 加總，回推真正無誤的浮動移動平均單位成本
df_merged_stock = pd.read_sql_query(f'''
    SELECT p.prod_id as 編號, 
           p.prod_name as 商品名稱, 
           COALESCE(SUM(CASE WHEN s.qty > 0 THEN s.qty ELSE 0 END), 0) as 總庫存量, 
           p.use_unit as 單位, 
           CASE 
             WHEN COALESCE(SUM(CASE WHEN s.qty > 0 THEN s.qty ELSE 0 END), 0) > 0 
             THEN (SUM(CASE WHEN s.qty > 0 THEN s.qty * s.cost ELSE 0 END) / SUM(CASE WHEN s.qty > 0 THEN s.qty ELSE 0 END))
             ELSE p.cost 
           END as 移動平均單位成本, 
           COALESCE(SUM(CASE WHEN s.qty > 0 THEN s.qty * s.cost ELSE 0 END), 0) as 庫存總價值,
           p.safety_stock as 安全庫存, 
           p.status as 狀態碼
    FROM products p 
    LEFT JOIN stock_batches s ON p.prod_id = s.prod_id
    {query_condition}
    GROUP BY p.prod_id, p.prod_name, p.use_unit, p.safety_stock, p.status
    ORDER BY p.status DESC, p.prod_id
''', conn)

# 核心同步更新：將資料庫 products 中的移動平均單位成本與即時加權算出來的數字進行健康同步
cursor = conn.cursor()
for _, row in df_merged_stock.iterrows():
    cursor.execute("UPDATE products SET cost = ? WHERE prod_id = ?", (float(row['移動平均單位成本']), row['編號']))

# ==================== 【成品餐點 P 類 BOM 成本自動即時重算更新邏輯】 ====================
cursor.execute('''
    UPDATE products
    SET cost = COALESCE((
        SELECT SUM(b.qty_needed * child.cost)
        FROM bom b
        JOIN products child ON b.child_id = child.prod_id
        WHERE b.parent_id = products.prod_id
    ), 0.0)
    WHERE products.prod_id LIKE 'P%'
''')
# ====================================================================================

conn.commit()
conn.close()

if not df_merged_stock.empty:
    def highlight_disabled(row):
        styles = [''] * len(row)
        name_idx = row.index.get_loc('商品名稱')
        status_idx = row.index.get_loc('狀態碼')
        if row.iloc[status_idx] == 0:
            styles[name_idx] = 'background-color: #ffcccc; color: #cc0000; font-weight: bold;'
        return styles

    st.dataframe(
        df_merged_stock.style.apply(highlight_disabled, axis=1)
                     .format({"總庫存量": "{:,.1f}", "移動平均單位成本": "${:,.4f}", "庫存總價值": "${:,.1f}", "安全庫存": "{:,.1f}"}), 
        use_container_width=True, 
        column_config={
            "狀態碼": None,
            "編號": st.column_config.TextColumn("編號", width="small"),
            "商品名稱": st.column_config.TextColumn("商品名稱", width="medium"),
            "總庫存量": st.column_config.NumberColumn("總庫存量", width="small"),
            "單位": st.column_config.TextColumn("單位", width="small"),
            "移動平均單位成本": st.column_config.NumberColumn("單位成本", width="small"),
            "庫存總價值": st.column_config.NumberColumn("總價值", width="small"),
            "安全庫存": st.column_config.NumberColumn("安全線", width="small"),
        },
        hide_index=True
    )
    
    st.markdown("---")
    st.markdown("### 🔍 歷史進貨面板")
    
    valid_detail_items = df_merged_stock[df_merged_stock['總庫存量'] > 0]
    
    if not valid_detail_items.empty:
        selected_stock_item = st.selectbox(
            "🎯 請選取下方品項，系統將列出每一筆進貨批次明細：",
            valid_detail_items['編號'] + " - " + valid_detail_items['商品名稱']
        )
        
        target_prod_id = selected_stock_item.split(" - ")[0]
        
        conn = sqlite3.connect('inventory.db')
        df_batch_details = pd.read_sql_query('''
            SELECT s.batch_id as 批次編號, 
                   s.inbound_date as 進貨日期, 
                   s.qty as 剩餘庫存量, 
                   (s.qty * s.cost) as 當次進貨總金額,
                   s.expiry_date as 有效期限, 
                   s.vendor_name as 原始供應商,
                   s.vendor_phone as 供應商電話
            FROM stock_batches s
            WHERE s.prod_id = ? AND s.qty > 0
            ORDER BY s.inbound_date ASC, s.batch_id ASC
        ''', conn, params=(target_prod_id,))
        
        matched_item_row = df_merged_stock[df_merged_stock['編號'] == target_prod_id].iloc[0]
        base_cost = matched_item_row['移動平均單位成本']
        unit_str = matched_item_row['單位']
        conn.close()
        
        if not df_batch_details.empty:
            st.caption(f"💡 目前 【{selected_stock_item}】 共由以下 {len(df_batch_details)} 個有效進貨批次組成")
            
            st.dataframe(
                df_batch_details.style.format({"剩餘庫存量": f"{{:,.1f}} {unit_str}", "當次進貨總金額": "${:,.1f}"}),
                use_container_width=True,
                column_config={
                    "批次編號": st.column_config.NumberColumn("批次", width="small"),
                    "進貨日期": st.column_config.TextColumn("進貨日期", width="small"),
                    "剩餘庫存量": st.column_config.TextColumn("在庫數量", width="small"),
                    "當次進貨總金額": st.column_config.NumberColumn("當次剩餘總價值", width="small"),
                    "有效期限": st.column_config.TextColumn("效期", width="small"),
                    "原始供應商": st.column_config.TextColumn("原始供應商", width="medium"),
                    "供應商電話": st.column_config.TextColumn("聯絡電話", width="medium"),
                },
                hide_index=True
            )
            
            st.info(f"此品項目前的「加權移動平均單位成本」為 **${base_cost:,.4f}** / {unit_str}。")
        else:
            st.info("該品項目前無有效批次庫存。")
            
    conn = sqlite3.connect('inventory.db')
    df_unique_disabled_items = pd.read_sql_query('''
        SELECT DISTINCT p.prod_id, p.prod_name
        FROM products p
        JOIN stock_batches s ON p.prod_id = s.prod_id
        WHERE p.status = 0 AND s.qty > 0
        ORDER BY p.prod_id
    ''', conn)
    conn.close()
    
    if not df_unique_disabled_items.empty:
        st.markdown("---")
        st.markdown("##### 🗑️ 清理已下架品項的殘留庫存")
        
        disabled_item_options = df_unique_disabled_items.apply(lambda r: f"{r['prod_id']} - {r['prod_name']}", axis=1).tolist()
        selected_disabled_item_str = st.selectbox("🔍 1. 選取欲清理的下架商品/食材：", disabled_item_options, key="clean_disabled_item_box")
        target_disabled_prod_id = selected_disabled_item_str.split(" - ")[0]
        
        conn = sqlite3.connect('inventory.db')
        df_disabled_batches = pd.read_sql_query('''
            SELECT s.batch_id, s.qty, s.original_qty, p.use_unit, s.inbound_date, s.expiry_date
            FROM stock_batches s 
            JOIN products p ON s.prod_id = p.prod_id 
            WHERE s.prod_id = ? AND s.qty > 0
            ORDER BY s.inbound_date ASC, s.batch_id ASC
        ''', conn, params=(target_disabled_prod_id,))
        conn.close()
        
        if not df_disabled_batches.empty:
            def format_batch_label(r):
                exp_label = r['expiry_date'] if (r['expiry_date'] and r['expiry_date'].strip() != "") else "無填寫"
                return f"【批次 {int(r['batch_id'])}】進貨日: {r['inbound_date']} | 有效日期: {exp_label} | 殘留數量: {r['qty']}{r['use_unit']}"
            
            batch_options = df_disabled_batches.apply(format_batch_label, axis=1).tolist()
            selected_batch_str = st.selectbox("🎯 2. 選擇欲清空歸零的特定殘留批次：", batch_options, key="clean_disabled_batch_box")
            
            target_batch_id = int(selected_batch_str.split("【批次 ")[1].split("】")[0])
            matched_del_row = df_disabled_batches[df_disabled_batches['batch_id'] == target_batch_id].iloc[0]
            item_name = selected_disabled_item_str.split(" - ")[1]
            
            if st.button("❌ 確認將此下架批次數量歸零（移出明細）", type="primary", key="clean_disabled_submit_btn"):
                conn = sqlite3.connect('inventory.db')
                cursor = conn.cursor()
                new_orig_qty = max(0.0, float(matched_del_row['original_qty']) - float(matched_del_row['qty']))
                cursor.execute("UPDATE stock_batches SET qty = 0, original_qty = ? WHERE batch_id = ?", (new_orig_qty, target_batch_id))
                conn.commit()
                conn.close()
                
                # 🔥 【優化動作與指定大類】完美落入 📋 庫存微調/報廢/盤點 分類中
                log_history(
                    st.session_state.current_user, 
                    "手動調整庫存-下架殘留清理", 
                    f"清理了已下架品項的殘留庫存量：{item_name} (批次:{target_batch_id}，原數量:{matched_del_row['qty']}{matched_del_row['use_unit']}，歷史 original_qty 已修正為已消耗量: {new_orig_qty}{matched_del_row['use_unit']})",
                    main_category="📋 庫存微調/報廢/盤點"
                )
                
                trigger_toast(f"已成功將 【{item_name}】 批次 {target_batch_id} 的庫存量歸零清除，並重整原始登記量！", icon="🗑️")
                st.rerun()
        else:
            st.info("該品項目前無有效殘留批次。")
else:
    st.info("目前此類別無庫存，請先辦理採購進貨。")