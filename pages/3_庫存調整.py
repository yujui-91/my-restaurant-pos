# pages/3_庫存調整.py
import streamlit as st
import pandas as pd
import sqlite3
from database.db_core import log_history

st.subheader("🔧 庫存微調與報廢管理面板")

current_user = st.session_state.get('current_user', '老 闆')

# 💡 新增分類篩選功能
stock_adj_cate = st.radio("🗂️ 請選擇要調整的項目類別：", ["僅看 食材 (R)", "僅看 用品 (S)"], horizontal=True)
prefix_filter = "R%" if "食材" in stock_adj_cate else "S%"

conn = sqlite3.connect('inventory.db')
# 依據上方選取的分類，動態抓取資料庫批次明細
df_batches = pd.read_sql_query('''
    SELECT s.batch_id, s.prod_id, p.prod_name, s.qty, p.use_unit, s.expiry_date, s.vendor_name 
    FROM stock_batches s JOIN products p ON s.prod_id = p.prod_id
    WHERE s.prod_id LIKE ? AND p.price >= 0
    ORDER BY s.prod_id, s.expiry_date ASC
''', conn, params=(prefix_filter,))
conn.close()

if not df_batches.empty:
    options = df_batches.apply(
        lambda r: f"{r['prod_id']} - {r['prod_name']} (批次:{r['batch_id']} | 剩餘:{r['qty']}{r['use_unit']} | 效期:{r['expiry_date']} | 廠商:{r['vendor_name']})", 
        axis=1
    ).tolist()
    
    selected_row = st.selectbox("🔍 選擇要微調/報廢的特定項目與批次：", options)
    
    batch_id_part = int(selected_row.split("批次:")[1].split(" |")[0])
    matched_row = df_batches[df_batches['batch_id'] == batch_id_part].iloc[0]
    
    item_name = matched_row['prod_name']
    current_qty = float(matched_row['qty'])
    unit_label = matched_row['use_unit']
    
    st.markdown(f"> 📊 **當前選擇批次狀態：** **{item_name}** ｜ 目前系統登記庫存量： **{current_qty} {unit_label}**")
    
    with st.form("inventory_adjustment_form"):
        adj_type = st.radio("動作選擇", ["過期損耗/報廢/人為疏失 (扣減庫存)", "手動補正/盤盈回補 (增加庫存)"], horizontal=True)
        adj_qty = st.number_input(f"請輸入異動變更的數量 ({unit_label})", min_value=0.1, value=1.0, step=1.0)
        reason_txt = st.text_input("請填寫微調/報廢原因說明 (必填)", value="")
        
        submit_adj = st.form_submit_button("🔧 確認執行庫存異動")
        
        if submit_adj:
            if reason_txt.strip() == "":
                st.error("❌ 錯誤：請填寫異動原因，以供日後審計歷史追蹤。")
            else:
                final_qty_change = -adj_qty if "報廢" in adj_type else adj_qty
                new_total_qty = current_qty + final_qty_change
                
                if new_total_qty < 0:
                    st.error("❌ 錯誤：扣減數量不能大於現有庫存量！")
                else:
                    conn = sqlite3.connect('inventory.db')
                    cursor = conn.cursor()
                    cursor.execute("UPDATE stock_batches SET qty = ? WHERE batch_id = ?", (new_total_qty, batch_id_part))
                    conn.commit()
                    conn.close()
                    
                    log_details = f"庫存微調【{item_name}】(批次編號 {batch_id_part})。動作：{adj_type}，數量：{adj_qty} {unit_label}，異動後現存：{new_total_qty} {unit_label}。原因：{reason_txt.strip()}"
                    log_history(current_user, f"庫存微調-{item_name}", log_details)
                    
                    st.toast(f"🛠️ 批次庫存微調完畢！品項：{item_name}，變動量：{final_qty_change:+,.1f}", icon="🔧")
                    st.success(f"🎉 批次庫存調整成功！已成功紀錄於歷史動作審計軌跡。")
                    st.rerun()
else:
    st.info(f"💡 目前 【{stock_adj_cate}】 類別中沒有任何庫存批次可供微調。")