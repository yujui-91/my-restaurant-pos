import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
from database.db_core import log_history, trigger_toast, show_pending_toast

# ==========================================
# 全域通知監聽器：置於網頁最首行，重整完畢後平穩彈出通知
# ==========================================
show_pending_toast()

st.subheader("📋 存貨盤點核實（精確批次盤點版）")

current_user = st.session_state.get('current_user', '老 闆')

# 項目類別選擇（食材或用品）
audit_cate_filter = st.radio("🗂️ 請選擇盤點項目類別：", ["食材 (R)", "用品 (S)"], horizontal=True)
prefix_char = "R%" if "食材" in audit_cate_filter else "S%"

# --- 第一步：撈取有庫存批次紀錄的產品列表 ---
conn = sqlite3.connect('inventory.db')
df_products_in_stock = pd.read_sql_query('''
    SELECT DISTINCT s.prod_id as 商品編號, p.prod_name as 商品名稱 
    FROM stock_batches s 
    JOIN products p ON s.prod_id = p.prod_id 
    WHERE s.prod_id LIKE ? AND s.qty > 0
''', conn, params=(prefix_char,))
conn.close()

if not df_products_in_stock.empty:
    # 讓老闆先選取大品項
    selected_product_str = st.selectbox(
        "🔍 1. 請選擇要盤點的商品項目：", 
        df_products_in_stock['商品編號'] + " - " + df_products_in_stock['商品名稱']
    )
    target_prod_id = selected_product_str.split(" - ")[0]
    
    # --- 第二步：依選定品項，即時撈出其對應的所有有效批次明細 ---
    conn = sqlite3.connect('inventory.db')
    df_batches = pd.read_sql_query('''
        SELECT s.batch_id, s.qty, s.expiry_date, s.inbound_date, s.vendor_name, s.vendor_phone, p.use_unit, p.cost
        FROM stock_batches s
        JOIN products p ON s.prod_id = p.prod_id
        WHERE s.prod_id = ? AND s.qty > 0
        ORDER BY s.inbound_date ASC, s.batch_id ASC
    ''', conn, params=(target_prod_id,))
    conn.close()
    
    if not df_batches.empty:
        # 將批次清單包裝成下拉選單供老闆精確選擇
        batch_options = df_batches.apply(
            lambda r: f"【批次 {int(r['batch_id'])}】進貨日: {r['inbound_date']} | 現存: {r['qty']}{r['use_unit']} | 效期: {r['expiry_date'] if r['expiry_date'] else '無'} | 供應商: {r['vendor_name'] if r['vendor_name'] else '未填'}", 
            axis=1
        ).tolist()
        
        selected_batch_str = st.selectbox("🎯 2. 請選擇欲核實數量的特定批次編號：", batch_options)
        
        # 解析選定的批次編號並撈出對應資料
        target_batch_id = int(selected_batch_str.split("【批次 ")[1].split("】")[0])
        matched_batch = df_batches[df_batches['batch_id'] == target_batch_id].iloc[0]
        
        theoretical_qty = float(matched_batch['qty'])
        unit_label = matched_batch['use_unit']
        current_base_cost = float(matched_batch['cost'])
        orig_vendor = matched_batch['vendor_name']
        orig_inbound = matched_batch['inbound_date']
        orig_expiry = matched_batch['expiry_date']
        
        # 取得商品名稱（防呆呈現）
        item_name = selected_product_str.split(" - ")[1]
        
        st.markdown(f"""
        > 📊 **當前選定批次防呆面板：**
        > * 商品名稱：**{item_name}** ({target_prod_id})
        > * 盤點批次：**批次編號 {target_batch_id}** (進貨日期: {orig_inbound})
        > * 系統理論庫存：**{theoretical_qty:,.2f} {unit_label}**
        """)
        
        # --- 第三步：現場實盤數量輸入表單 ---
        with st.form("precise_audit_form"):
            actual_qty = st.number_input(
                f"填寫該批次現場【實盤總數量】 ({unit_label})", 
                min_value=0.0, 
                value=theoretical_qty, 
                step=1.0
            )
            
            submit_audit = st.form_submit_button("💾 確認無誤，覆蓋並更新此批次庫存")
            
            if submit_audit:
                diff_qty = actual_qty - theoretical_qty
                
                # 計算盤盈或盤虧狀態
                if diff_qty > 0:
                    audit_status = f"盤盈 (該批次多了 {abs(diff_qty):,.2f} {unit_label})"
                elif diff_qty < 0:
                    audit_status = f"盤虧 (該批次少了 {abs(diff_qty):,.2f} {unit_label})"
                else:
                    audit_status = "完全吻合 (無誤差)"
                
                # 執行精確修改：更新特定 batch_id，保留其餘欄位原貌
                conn = sqlite3.connect('inventory.db')
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE stock_batches 
                    SET qty = ? 
                    WHERE batch_id = ?
                ''', (actual_qty, target_batch_id))
                
                # 自動防呆：如果盤點完該批次數量變為 0，則予以清除，避免殘留空批次紀錄
                cursor.execute("DELETE FROM stock_batches WHERE qty <= 0")
                
                conn.commit()
                conn.close()
                
                # 撰寫詳細的審計日誌
                log_details = (
                    f"盤點核實覆蓋。品項：【{item_name}({target_prod_id})】的[批次 {target_batch_id}]。習得歷史進貨日: {orig_inbound}，原登記供應商: {orig_vendor if orig_vendor else '無'}。"
                    f"該批次系統理論數: {theoretical_qty:,.2f} {unit_label} -> 現場實盤數: {actual_qty:,.2f} {unit_label}。盤點結果：{audit_status}，持續繼承單價基準: ${current_base_cost:.4f}/{unit_label}。"
                )
                log_history(current_user, f"存貨盤點-{item_name}", log_details)
                
                # 發送全域平穩通知並重新整頁
                trigger_toast(f"📋 批次 {target_batch_id} 盤點修正完成！結果：{audit_status}", icon="🔍")
                st.success(f"🎉 [批次 {target_batch_id}] 數據更新成功！盤點結果：{audit_status}")
                st.rerun()
    else:
        st.warning("⚠️ 找不到該商品的有效庫存批次，請重新整理頁面。")
else:
    st.info(f"💡 目前 【{audit_cate_filter}】 類別中沒有任何在庫庫存資料可供盤點。")