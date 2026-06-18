# pages/4_盤點.py
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
from database.db_core import log_history, trigger_toast, show_pending_toast
import streamlit as st

# 檢查 session_state 中的登入狀態，若未登入則阻斷畫面並提示
# if not st.session_state.get("password_correct", False):
#     st.warning("🔒 請先前往首頁登入管理系統！")
#     st.stop()
show_pending_toast()

st.subheader("📋 存貨盤點核實")

# 加入手機模式切換開關
use_mobile_view = st.toggle("📱 切換為手機/平板專用排版", value=False, key="audit_mobile_toggle")

current_user = st.session_state.get('current_user', '老 闆')

audit_cate_filter = st.radio("🗂️ 請選擇盤點項目類別：", ["食材 (R)", "用品 (S)"], horizontal=True)
prefix_char = "R%" if "食材" in audit_cate_filter else "S%"

conn = sqlite3.connect('inventory.db')
df_products_in_stock = pd.read_sql_query('''
    SELECT DISTINCT s.prod_id as 商品編號, p.prod_name as 商品名稱 
    FROM stock_batches s 
    JOIN products p ON s.prod_id = p.prod_id 
    WHERE s.prod_id LIKE ? AND s.qty > 0
''', conn, params=(prefix_char,))
conn.close()

if not df_products_in_stock.empty:
    selected_product_str = st.selectbox(
        "🔍 1. 請選擇要盤點的商品項目：", 
        df_products_in_stock['商品編號'] + " - " + df_products_in_stock['商品名稱']
    )
    target_prod_id = selected_product_str.split(" - ")[0]
    
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
        
        # 根據是否啟用手機模式，決定第二步「選擇批次」的渲染外觀
        if use_mobile_view:
            # 📱 手機模式排版：將字串優化換行，並改用直式單選鈕鋪開，方便大拇指直接點擊
            st.markdown("🎯 **2. 請點擊欲核實數量的特定進貨批次：**")
            
            # 建立易讀的對照字典，將簡化且換行的直式格式作為 radio 的標籤顯示
            mobile_options_map = {}
            for _, r in df_batches.iterrows():
                label = (
                    f"📦 【批次 {int(r['batch_id'])}】\n"
                    f"  🗓️ 進貨: {r['inbound_date']} | ⏳ 效期: {r['expiry_date'] if r['expiry_date'] else '無'}\n"
                    f"  🚨 現存量: {r['qty']} {r['use_unit']}"
                )
                mobile_options_map[label] = int(r['batch_id'])
            
            selected_mobile_label = st.radio(
                "批次清單", 
                options=list(mobile_options_map.keys()), 
                label_visibility="collapsed", 
                key="audit_batch_radio"
            )
            target_batch_id = mobile_options_map[selected_mobile_label]
            
        else:
            # 💻 桌機傳統模式排版：保留傳統一長條下拉選單
            batch_options = df_batches.apply(
                lambda r: f"【批次 {int(r['batch_id'])}】進貨日: {r['inbound_date']} | 現存: {r['qty']}{r['use_unit']} | 效期: {r['expiry_date'] if r['expiry_date'] else '無'} | 供應商: {r['vendor_name'] if r['vendor_name'] else '未填'}", 
                axis=1
            ).tolist()
            
            selected_batch_str = st.selectbox("🎯 2. 請選擇欲核實數量的特定批次編號：", batch_options)
            target_batch_id = int(selected_batch_str.split("【批次 ")[1].split("】")[0])
        
        # 🛠️ 核心優化：使用安全判斷式，防止 iloc[0] 瞬間找不到資料造成頁面底部紅色錯誤閃爍
        matched_rows = df_batches[df_batches['batch_id'] == target_batch_id]
        if not matched_rows.empty:
            matched_batch = matched_rows.iloc[0]
            
            theoretical_qty = float(matched_batch['qty'])
            unit_label = matched_batch['use_unit']
            current_base_cost = float(matched_batch['cost'])
            orig_vendor = matched_batch['vendor_name']
            orig_inbound = matched_batch['inbound_date']
            
            item_name = selected_product_str.split(" - ")[1]
            
            st.markdown(f"""
            > 📊 **當前選定批次防呆面板：**
            > * 商品名稱：**{item_name}** ({target_prod_id})
            > * 盤點批次：**批次編號 {target_batch_id}** (進貨日期: {orig_inbound})
            > * 系統理論庫存：**{theoretical_qty:,.2f} {unit_label}**
            """)
            
            with st.form("precise_audit_form"):
                # 🎯 移除 min_value 限制，並加上明確且唯一的 key="audit_qty_input" 以綁定會話狀態
                st.number_input(
                    f"填寫該批次現場數量 ({unit_label})", 
                    value=theoretical_qty, 
                    step=1.0,
                    key="audit_qty_input"
                )
                
                submit_audit = st.form_submit_button("💾 更新此批次庫存")
                
                if submit_audit:
                    # 🛑 核心優化：改為讀取 st.session_state 確保同步獲取最新實盤輸入值
                    actual_qty_val = st.session_state.audit_qty_input
                    
                    # 🛑 核心防呆：現場實盤數量絕不可能低於 0，若小於 0 則立刻拋錯阻斷
                    if actual_qty_val < 0:
                        st.error(f"❌ 錯誤：現場【實盤總數量】絕對不能小於 0！您目前的輸入數值為 {actual_qty_val}")
                    else:
                        diff_qty = actual_qty_val - theoretical_qty
                        
                        if diff_qty > 0:
                            audit_status = f"盤盈 (該批次多了 {abs(diff_qty):,.2f} {unit_label})"
                        elif diff_qty < 0:
                            audit_status = f"盤虧 (該批次少了 {abs(diff_qty):,.2f} {unit_label})"
                        else:
                            audit_status = "完全吻合 (無誤差)"
                        
                        conn = sqlite3.connect('inventory.db')
                        cursor = conn.cursor()
                        cursor.execute('''
                            UPDATE stock_batches 
                            SET qty = ? 
                            WHERE batch_id = ?
                        ''', (actual_qty_val, target_batch_id))
                        
                        conn.commit()
                        conn.close()
                        
                        log_details = (
                            f"盤點核實覆蓋。品項：【{item_name}({target_prod_id})】的[批次 {target_batch_id}]。習得歷史進貨日: {orig_inbound}，原登記供應商: {orig_vendor if orig_vendor else '無'}。"
                            f"該批次系統理論數: {theoretical_qty:,.2f} {unit_label} -> 現場實盤數: {actual_qty_val:,.2f} {unit_label}。盤點結果：{audit_status}，持續繼承單價基準: ${current_base_cost:.4f}/{unit_label}。"
                        )
                        log_history(current_user, f"存貨盤點-{item_name}", log_details)
                        
                        trigger_toast(f"📋 批次 {target_batch_id} 盤點修正完成！結果：{audit_status}", icon="🔍")
                        st.success(f"🎉 [批次 {target_batch_id}] 數據更新成功！盤點結果：{audit_status}")
                        st.rerun()
        else:
            st.stop()  # 若在頁面刷新的斷層瞬間找不到該批次，優雅中斷防止噴出紅色錯誤
    else:
        st.warning("⚠️ 找不到該商品的有效庫存批次，請重新整理頁面。")
else:
    st.info(f"💡 目前 【{audit_cate_filter}】 類別中沒有任何在庫庫存資料可供盤點。")