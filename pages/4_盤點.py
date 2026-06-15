# pages/4_盤點.py
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
from database.db_core import log_history

st.subheader("📋 存貨盤點核實")

current_user = st.session_state.get('current_user', '老 闆')

# 💡 新增分類篩選器 (R / S / C)
audit_cate_filter = st.radio("🗂️ 請選擇盤點項目類別：", ["食材 (R)", "用品 (S)", "帳單費用 (C)"], horizontal=True)
prefix_char = "R%" if "食材" in audit_cate_filter else ("S%" if "用品" in audit_cate_filter else "C%")

conn = sqlite3.connect('inventory.db')
# 連同產品基準單價 p.cost 一起撈出來，並根據分類進行篩選
df_audit = pd.read_sql_query('''
    SELECT s.prod_id as 食品編號, p.prod_name as 商品名稱, 
           SUM(s.qty) as 系統理論庫存, p.use_unit as 單位, p.cost as 單位成本 
    FROM stock_batches s 
    JOIN products p ON s.prod_id = p.prod_id 
    WHERE s.prod_id LIKE ?
    GROUP BY s.prod_id
''', conn, params=(prefix_char,))
conn.close()

if not df_audit.empty:
    selected_row = st.selectbox("🔍 選擇要盤點的項目：", df_audit['食品編號'] + " - " + df_audit['商品名稱'])
    actual_qty = st.number_input("現場實盤總數量", min_value=0.0, value=0.0, step=1.0)
    
    if st.button("提交盤點數據"):
        prod_id_part = selected_row.split(" - ")[0]
        matched_item = df_audit[df_audit['食品編號'] == prod_id_part].iloc[0]
        theoretical_qty = float(matched_item['系統理論庫存'])
        unit_label = matched_item['單位']
        item_name = matched_item['商品名稱']
        current_base_cost = float(matched_item['單位成本'])
        
        diff_qty = actual_qty - theoretical_qty
        if diff_qty > 0:
            audit_status = f"盤盈 (多了 {abs(diff_qty):,.2f} {unit_label})"
        elif diff_qty < 0:
            audit_status = f"盤虧 (少了 {abs(diff_qty):,.2f} {unit_label})"
        else:
            audit_status = "完全吻合 (無誤差)"
        
        conn = sqlite3.connect('inventory.db')
        cursor = conn.cursor()
        # 清除舊有批次
        cursor.execute("DELETE FROM stock_batches WHERE prod_id = ?", (prod_id_part,))
        
        # 寫入新數量，並自動繼承原本單價基準
        cursor.execute('''
            INSERT INTO stock_batches (prod_id, qty, expiry_date, inbound_date, vendor_name) 
            VALUES (?, ?, ?, ?, '盤點核實覆蓋')
        ''', (prod_id_part, actual_qty, (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d")))
        
        conn.commit()
        conn.close()
        
        log_details = f"針對【{item_name}({prod_id_part})】進行庫存盤點。系統理論庫存: {theoretical_qty:,.2f} {unit_label}，現場實盤總數: {actual_qty:,.2f} {unit_label}。盤點結果: {audit_status}，繼承基準單價: ${current_base_cost:.4f}/每單位。"
        log_history(current_user, f"存貨盤點-{item_name}", log_details)
        
        st.toast(f"📋 盤點覆蓋完成！品項：{item_name} | 結果：{audit_status}", icon="🔍")
        st.success(f"🎉 盤點覆蓋完成！結果為：{audit_status}")
        st.rerun()
else:
    st.info(f"💡 目前 【{audit_cate_filter}】 類別中沒有任何庫存資料可供盤點。")