# pages/4_盤點.py
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
from database.db_core import log_history, trigger_toast, show_pending_toast, get_db_conn
# 從 db_core 載入所需的快取函式
from database.db_core import cached_fetch_products_in_stock_for_audit, cached_fetch_batch_details_for_audit

if 'db_update_trigger' not in st.session_state:
    st.session_state.db_update_trigger = 0

show_pending_toast()

st.subheader("📋 存貨盤點核實")

use_mobile_view = st.toggle("📱 切換為手機/平板專用排版", value=False, key="audit_mobile_toggle")

current_user = st.session_state.get('current_user', '老 鎖')

audit_cate_filter = st.radio("🗂️ 請選擇盤點項目類別：", ["食材 (R)", "用品 (S)"], horizontal=True)
prefix_char = "R%" if "食材" in audit_cate_filter else "S%"

df_products_in_stock = cached_fetch_products_in_stock_for_audit(prefix_char, cache_key=st.session_state.db_update_trigger)

if not df_products_in_stock.empty:
    selected_product_str = st.selectbox(
        "🔍 1. 請選擇要盤點的商品項目：", 
        df_products_in_stock['商品編號'] + " - " + df_products_in_stock['商品名稱']
    )
    target_prod_id = selected_product_str.split(" - ")[0]
    
    df_batches = cached_fetch_batch_details_for_audit(target_prod_id, cache_key=st.session_state.db_update_trigger)
    
    if not df_batches.empty:
        if use_mobile_view:
            st.markdown("🎯 **2. 請點擊欲核實數量的特定進貨批次：**")
            
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
            batch_options = df_batches.apply(
                lambda r: f"【批次 {int(r['batch_id'])}】進貨日: {r['inbound_date']} | 現存: {r['qty']}{r['use_unit']} | 效期: {r['expiry_date'] if r['expiry_date'] else '無'} | 供應商: {r['vendor_name'] if r['vendor_name'] else '未填'}", 
                axis=1
            ).tolist()
            
            selected_batch_str = st.selectbox("🎯 2. 請選擇欲核實數量的特定批次編號：", batch_options)
            target_batch_id = int(selected_batch_str.split("【批次 ")[1].split("】")[0])
        
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
            > * 盤點批次：**盤點批次編號 {target_batch_id}** (進貨日期: {orig_inbound})
            > * 系統理論庫存：**{theoretical_qty:,.2f} {unit_label}**
            """)
            
            with st.form("precise_audit_form"):
                st.number_input(
                    f"填寫該批次現場數量 ({unit_label})", 
                    value=theoretical_qty, 
                    step=1.0,
                    key="audit_qty_input"
                )
                
                submit_audit = st.form_submit_button("💾 更新此批次庫存")
                
                if submit_audit:
                    actual_qty_val = st.session_state.audit_qty_input
                    
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
                        
                        conn = get_db_conn()
                        cursor = conn.cursor()
                        cursor.execute('''
                            UPDATE stock_batches 
                            SET qty = ? 
                            WHERE batch_id = ?
                        ''', (actual_qty_val, target_batch_id))
                        
                        conn.commit()
                        conn.close()
                        
                        # 替換全域快取清空
                        st.session_state.db_update_trigger += 1
                        
                        log_details = (
                            f"盤點核實覆蓋。品項：【{item_name}({target_prod_id})】的[批次 {target_batch_id}]。習得歷史進貨日: {orig_inbound}，原登記供應商: {orig_vendor if orig_vendor else '無'}。"
                            f"該批次系統理論數: {theoretical_qty:,.2f} {unit_label} -> 現場實盤數: {actual_qty_val:,.2f} {unit_label}。盤點結果：{audit_status}，持續繼承單價基準: ${current_base_cost:.4f}/{unit_label}。"
                        )
                        log_history(current_user, f"存貨盤點-{item_name}", log_details)
                        
                        trigger_toast(f"📋 批次 {target_batch_id} 盤點修正完成！結果：{audit_status}", icon="🔍")
                        st.success(f"🎉 [批次 {target_batch_id}] 數據更新成功！盤點結果：{audit_status}")
                        st.rerun()
        else:
            st.stop()
    else:
        st.warning("⚠️ 找不到該商品的有效庫存批次，請重新整理頁面。")
else:
    st.info(f"💡 目前 【{audit_cate_filter}】 類別中沒有任何在庫庫存資料可供盤點。")