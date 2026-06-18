# pages/3_庫存調整.py
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
from database.db_core import log_history, trigger_toast, show_pending_toast

show_pending_toast()

st.subheader("🔧 庫存管理面板")

current_user = st.session_state.get('current_user', '老 闆')

stock_adj_cate = st.radio("🗂️ 請選擇要調整的項目類別：", [" 食材 (R)", " 用品 (S)"], horizontal=True)

if "食材" in stock_adj_cate: 
    prefix_filter = "R%"
else: 
    prefix_filter = "S%"

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
    item_options = df_unique_items.apply(lambda r: f"{r['prod_id']} - {r['prod_name']}", axis=1).tolist()
    selected_item_str = st.selectbox("🔍 1. 請先選取欲調整的項目名稱：", item_options)
    target_prod_id = selected_item_str.split(" - ")[0]
    
    conn = sqlite3.connect('inventory.db')
    df_batches = pd.read_sql_query('''
        SELECT s.batch_id, s.qty, p.use_unit, s.expiry_date, s.inbound_date, s.vendor_name 
        FROM stock_batches s 
        JOIN products p ON s.prod_id = p.prod_id
        WHERE s.prod_id = ? AND s.qty > 0
        ORDER BY s.expiry_date ASC, s.inbound_date ASC
    ''', conn, params=(target_prod_id,))
    conn.close()
    
    if not df_batches.empty:
        batch_options = df_batches.apply(
            lambda r: f"【批次編號: {r['batch_id']}】進貨日: {r['inbound_date']} | 現存: {r['qty']}{r['use_unit']} | 效期: {r['expiry_date'] if r['expiry_date'] else '無'}", 
            axis=1
        ).tolist()
        
        selected_batch_row = st.selectbox("🎯 2. 請選擇該品項欲更動的進貨批次：", batch_options)
        
        batch_id_part = int(selected_batch_row.split("【批次編號: ")[1].split("】")[0])
        
        # 🛠️ 核心優化：使用安全判斷式，防止 iloc[0] 瞬間找不到資料造成紅色錯誤閃爍
        matched_rows = df_batches[df_batches['batch_id'] == batch_id_part]
        if not matched_rows.empty:
            matched_row = matched_rows.iloc[0]
            
            item_name = selected_item_str.split(" - ")[1]
            current_qty = float(matched_row['qty'])
            unit_label = matched_row['use_unit']
            orig_inbound_date = matched_row['inbound_date']
            
            st.markdown(f"> 📊 **當前選擇批次狀態：** **{item_name}** (進貨日期: {orig_inbound_date}) ｜ 目前系統登記庫存量： **{current_qty} {unit_label}**")
            
            with st.form("inventory_adjustment_form"):
                adj_type = st.radio("動作選擇", ["過期損耗/報廢 (扣減庫存)", "手動補正(增加庫存)"], horizontal=True)
                # 🎯 依據您的要求，此處移除 min_value 限制，允許輸入負數
                adj_qty = st.number_input(f"請輸入異動變更的數量 ({unit_label})  ", value=1.0, step=1.0)
                
                st.markdown("###### 📅 請指定此筆損耗歸屬之完整年份與月份（確保跨年財報精確）：")
                col_adj_y, col_adj_m = st.columns(2)
                with col_adj_y:
                    this_year = datetime.now().year
                    adj_year_options = [this_year, this_year - 1]
                    selected_adj_year = st.selectbox("歸帳年份", adj_year_options, index=0)
                with col_adj_m:
                    bill_months_options = [f"{i}月" for i in range(1, 13)]
                    current_month_idx = max(0, min(datetime.now().month - 1, 11))
                    adj_month_str = st.selectbox("歸帳月份", bill_months_options, index=current_month_idx)
                
                reason_txt = st.text_input("請填寫微調/報廢原因說明 (選填)", value="")
                submit_adj = st.form_submit_button("🔧 確認執行庫存異動")
                
                if submit_adj:
                    # 🛑 核心防呆：只要輸入小於或等於 0 的數字，立刻拋出錯誤並全面阻斷後續流程
                    if adj_qty <= 0:
                        st.error(f"❌ 錯誤：異動變更的數量必須大於 0！您目前的輸入數值為 {adj_qty}")
                    else:
                        final_qty_change = -adj_qty if "扣減" in adj_type or "報廢" in adj_type else adj_qty
                        new_total_qty = current_qty + final_qty_change
                        
                        if new_total_qty < 0:
                            st.error("❌ 錯誤：扣減數量不能大於現有庫存量！")
                        else:
                            final_reason = reason_txt.strip() if reason_txt.strip() != "" else "未填寫原因"

                            conn = sqlite3.connect('inventory.db')
                            cursor = conn.cursor()
                            cursor.execute("SELECT cost FROM products WHERE prod_id = ?", (target_prod_id,))
                            unit_cost = cursor.fetchone()[0] or 0.0
                            total_value_change = final_qty_change * unit_cost

                            cursor.execute("UPDATE stock_batches SET qty = ? WHERE batch_id = ?", (new_total_qty, batch_id_part))
                            conn.commit()
                            conn.close()
                            
                            month_digits = int(adj_month_str.replace("月", ""))
                            formatted_target_month = f"{selected_adj_year}-{month_digits:02d}"

                            log_details = (
                                f"庫存微調【{item_name}】(批次編號 {batch_id_part}，進貨日: {orig_inbound_date})。"
                                f"動作：{adj_type}，數量變動：{final_qty_change} {unit_label}，異動後現存：{new_total_qty} {unit_label}，"
                                f"總值變動: ${total_value_change:.2f}。原因：{final_reason}。 目標歸帳月份: {formatted_target_month}"
                            )
                            log_history(current_user, f"手動調整庫存-品項:{target_prod_id}", log_details)
                            
                            trigger_toast(f"🛠️ 批次庫存微調完畢！品項：{item_name}，變動量：{final_qty_change:+,.1f}", icon="🔧")
                            st.success(f"🎉 批次庫存調整成功！已成功紀錄於歷史動作審計軌跡，並歸帳至 {formatted_target_month}。")
                            st.rerun()
        else:
            st.stop()
    else:
        st.info("💡 該品項目前無有效批次。")
else:
    st.info(f"💡 目前 【{stock_adj_cate}】 類別中沒有任何在庫庫存批次可供微調。")