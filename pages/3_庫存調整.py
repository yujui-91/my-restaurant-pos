# pages/3_庫存調整.py
import streamlit as st
import pandas as pd
import sqlite3
from database.db_core import log_history, trigger_toast, show_pending_toast

show_pending_toast()

st.subheader("🔧 庫存微調與報廢管理面板")

current_user = st.session_state.get('current_user', '老 闆')

stock_adj_cate = st.radio("🗂️ 請選擇要調整的項目類別：", ["僅看 食材 (R)", "僅看 用品 (S)"], horizontal=True)
prefix_filter = "R%" if "食材" in stock_adj_cate else "S%"

# 改善需求 2：第一步先撈取所有「狀態啟用且有庫存批次」的食材/用品獨立品項列表
conn = sqlite3.connect('inventory.db')
df_unique_items = pd.read_sql_query('''
    SELECT DISTINCT p.prod_id, p.prod_name 
    FROM products p
    JOIN stock_batches s ON p.prod_id = s.prod_id
    WHERE p.prod_id LIKE ? AND p.status = 1 AND s.qty > 0
    ORDER BY p.prod_id
''', conn, params=(prefix_filter,))
conn.close()

if not df_unique_items.empty:
    # 讓老闆先選大品項名稱
    item_options = df_unique_items.apply(lambda r: f"{r['prod_id']} - {r['prod_name']}", axis=1).tolist()
    selected_item_str = st.selectbox("🔍 1. 請先選取欲調整的食材/用品名稱：", item_options)
    target_prod_id = selected_item_str.split(" - ")[0]
    
    # 第二步：根據老闆選定的產品編號，即時去撈出這一項商品擁有的「所有有效進貨批次與進貨日期」
    conn = sqlite3.connect('inventory.db')
    df_batches = pd.read_sql_query('''
        SELECT s.batch_id, s.qty, p.use_unit, s.expiry_date, s.inbound_date, s.vendor_name 
        FROM stock_batches s 
        JOIN products p ON s.prod_id = p.prod_id
        WHERE s.prod_id = ?
        ORDER BY s.expiry_date ASC, s.inbound_date ASC
    ''', conn, params=(target_prod_id,))
    conn.close()
    
    if not df_batches.empty:
        # 將該原物料底下的所有批次包裝成精準下拉選單，並在選單內明確標記【進貨日期】
        batch_options = df_batches.apply(
            lambda r: f"【批次編號: {r['batch_id']}】進貨日: {r['inbound_date']} | 現存: {r['qty']}{r['use_unit']} | 效期: {r['expiry_date'] if r['expiry_date'] else '無'}", 
            axis=1
        ).tolist()
        
        selected_batch_row = st.selectbox("🎯 2. 請選擇該品項欲更動的精確進貨批次：", batch_options)
        
        # 解析選定的批次編號並對準該列資料
        batch_id_part = int(selected_batch_row.split("【批次編號: ")[1].split("】")[0])
        matched_row = df_batches[df_batches['batch_id'] == batch_id_part].iloc[0]
        
        item_name = selected_item_str.split(" - ")[1]
        current_qty = float(matched_row['qty'])
        unit_label = matched_row['use_unit']
        orig_inbound_date = matched_row['inbound_date']
        
        st.markdown(f"> 📊 **當前選擇批次狀態：** **{item_name}** (進貨日期: {orig_inbound_date}) ｜ 目前系統登記庫存量： **{current_qty} {unit_label}**")
        
        with st.form("inventory_adjustment_form"):
            adj_type = st.radio("動作選擇", ["過期損耗/報廢/人為疏失 (扣減庫存)", "手動補正/盤盈回補 (增加庫存)"], horizontal=True)
            adj_qty = st.number_input(f"請輸入異動變更的數量 ({unit_label})", min_value=0.1, value=1.0, step=1.0)
            
            # 說明改為選填標記
            reason_txt = st.text_input("請填寫微調/報廢原因說明 (選填)", value="")
            
            submit_adj = st.form_submit_button("🔧 確認執行庫存異動")
            
            if submit_adj:
                final_qty_change = -adj_qty if "報廢" in adj_type else adj_qty
                new_total_qty = current_qty + final_qty_change
                
                if new_total_qty < 0:
                    st.error("❌ 錯誤：扣減數量不能大於現有庫存量！")
                else:
                    # 💡 核心安全邏輯 3 修正：若沒填原因則給予預設值，轉為選填項目
                    final_reason = reason_txt.strip() if reason_txt.strip() != "" else "未填寫原因"

                    conn = sqlite3.connect('inventory.db')
                    cursor = conn.cursor()
                    cursor.execute("UPDATE stock_batches SET qty = ? WHERE batch_id = ?", (new_total_qty, batch_id_part))
                    cursor.execute("DELETE FROM stock_batches WHERE qty <= 0") # 防呆：若扣完歸零自動清理
                    conn.commit()
                    conn.close()
                    
                    log_details = f"庫存微調【{item_name}】(批次編號 {batch_id_part}，進貨日: {orig_inbound_date})。動作：{adj_type}，數量：{adj_qty} {unit_label}，異動後現存：{new_total_qty} {unit_label}。原因：{final_reason}"
                    log_history(current_user, f"庫存微調-{item_name}", log_details)
                    
                    trigger_toast(f"🛠️ 批次庫存微調完畢！品項：{item_name}，變動量：{final_qty_change:+,.1f}", icon="🔧")
                    st.success(f"🎉 批次庫存調整成功！已成功紀錄於歷史動作審計軌跡。")
                    st.rerun()
    else:
        st.info("💡 該品項目前無有效批次。")
else:
    st.info(f"💡 目前 【{stock_adj_cate}】 類別中沒有任何在庫庫存批次可供微調。")