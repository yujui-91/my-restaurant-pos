# pages/3_🛠️_批次庫存調整.py
import streamlit as st
import pandas as pd
import sqlite3
from database.db_core import log_history

st.subheader("🛠️ 批次庫存微調與報廢管理")

current_user = st.session_state.get('current_user', '老 闆')
conn = sqlite3.connect('inventory.db')
prods_df = pd.read_sql_query("SELECT prod_id, prod_name, use_unit FROM products WHERE price = 0", conn)
conn.close()

col_a1, col_a2, col_a3 = st.columns(3)
with col_a1:
    adj_prod = st.selectbox("1. 選擇要調整的商品", prods_df['prod_id'] + " - " + prods_df['prod_name'], key="adj_p")
    ap_id = adj_prod.split(" - ")[0]
    matched_prod = prods_df[prods_df['prod_id'] == ap_id].iloc[0]
    unit_label = matched_prod['use_unit']
    item_name = matched_prod['prod_name']
    
with col_a2:
    conn = sqlite3.connect('inventory.db')
    df_adj_batches = pd.read_sql_query("SELECT batch_id, qty, expiry_date FROM stock_batches WHERE prod_id = ?", conn, params=(ap_id,))
    conn.close()
    if not df_adj_batches.empty:
        adj_batch_options = df_adj_batches.apply(lambda r: f"批次 {int(r['batch_id'])} (現存庫存:{r['qty']}, 效期:{r['expiry_date']})", axis=1).tolist()
        selected_adj_batch_str = st.selectbox("2. 指定要微調的批次編號", adj_batch_options)
        target_adj_batch_id = int(selected_adj_batch_str.split(" (")[0].replace("批次 ", ""))
    else:
        target_adj_batch_id = None
        st.warning("⚠️ 該品項目前在後台沒有任何庫存批次可供調整！")
        
with col_a3:
    adj_type = st.selectbox("3. 調整原因/名義", ["商品損壞/打翻 (變少)", "過期報廢 (變少)", "人工補登/廠商多送 (變多)", "其他原因調整"])
    adj_qty = st.number_input(f"4. 調整數量 (輸入正數為增加，負數為減少，單位: {unit_label})", value=0.0, step=1.0)
    
if st.button("確認微調此特定批次庫存"):
    if target_adj_batch_id is None:
        st.error("❌ 錯誤：沒有可調整的批次！")
    elif adj_qty == 0:
        st.error("❌ 錯誤：調整數量不能為 0！")
    else:
        conn = sqlite3.connect('inventory.db')
        cursor = conn.cursor()
        cursor.execute("SELECT qty, expiry_date FROM stock_batches WHERE batch_id = ?", (target_adj_batch_id,))
        batch_res = cursor.fetchone()
        
        if batch_res:
            old_qty = float(batch_res[0])
            expiry_str = batch_res[1]
            new_qty = old_qty + adj_qty
            
            if new_qty < 0:
                st.error(f"❌ 錯誤：調整後的庫存量不能為負數！(當前庫存: {old_qty}, 預計扣除: {abs(adj_qty)})")
                conn.close()
            else:
                cursor.execute("UPDATE stock_batches SET qty = ? WHERE batch_id = ?", (new_qty, target_adj_batch_id))
                if new_qty == 0:
                    cursor.execute("DELETE FROM stock_batches WHERE batch_id = ?", (target_adj_batch_id,))
                    
                conn.commit()
                conn.close()
                
                direction = "【庫存變多 ➕】" if adj_qty > 0 else "【庫存變少 ➖】"
                log_details = (
                    f"微調特定批次庫存。品項：{item_name}({ap_id}) | 指定批次: {target_adj_batch_id}號 "
                    f"| 效期: {expiry_str if expiry_str else '無'} | 調整名義: {adj_type} | 變動方向: {direction} "
                    f"| 調整前數量: {old_qty:,.2f} {unit_label} | 異動量: {adj_qty:+,.2f} {unit_label} "
                    f"| 調整後終點庫存: {new_qty:,.2f} {unit_label}。"
                )
                log_history(current_user, f"庫存微調-{item_name}", log_details)
                
                st.success(f"🎉 批次庫存調整成功！已成功紀錄於歷史動作審計軌跡。")
                st.rerun()
        else:
            st.error("❌ 錯誤：找不到該指定的批次資料！")
            conn.close()