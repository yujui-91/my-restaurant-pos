# pages/2_📝_採購進貨單.py
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
from database.db_core import log_history, get_next_raw_id, get_next_supply_id, get_next_bill_id

st.subheader("📝 採購進貨與費用登記單")

current_user = st.session_state.get('current_user', '老 闆')
item_type = st.radio("✨ 請選擇本次登記類別：", ["食材 (R 開頭)", "用品 (S 開頭)", "帳單費用 (C 開頭，如水電瓦斯)"], horizontal=True)

if "食材" in item_type:
    prefix = 'R'
elif "用品" in item_type:
    prefix = 'S'
else:
    prefix = 'C'

conn = sqlite3.connect('inventory.db')
existing_items_df = pd.read_sql_query(
    "SELECT prod_id, prod_name, purchase_unit, use_unit, conversion_factor, safety_stock FROM products WHERE prod_id LIKE ?", 
    conn, params=(f"{prefix}%",)
)
conn.close()

st.markdown("##### 🔍 1. 品項選取（二選一）：")
col_choice1, col_choice2 = st.columns(2)
with col_choice1:
    options_list = [f"--- 請選擇已建立的{item_type[:2]} ---"] + (existing_items_df['prod_name'].tolist())
    chosen_select_name = st.selectbox("【重複登記】從這裡直接下拉搜尋既有品項", options_list, index=0)
with col_choice2:
    chosen_input_name = st.text_input(f"【首次登記】在此直接手動打字輸入新{item_type[:2]}名稱 (如：水費)", value="")

if chosen_input_name.strip() != "":
    chosen_name = chosen_input_name.strip()
    if prefix == 'R': default_id = get_next_raw_id()
    elif prefix == 'S': default_id = get_next_supply_id()
    else: default_id = get_next_bill_id()
    
    default_p_unit, default_u_unit, default_c_factor, default_safety = "", "", 1.0, 0.0
    st.warning(f"✨ 偵測到新品項！自動發放【{item_type[:2]}】專屬編號：**{default_id}**")
elif chosen_select_name != f"--- 請選擇已建立的{item_type[:2]} ---":
    chosen_name = chosen_select_name
    matched_item = existing_items_df[existing_items_df['prod_name'] == chosen_name].iloc[0]
    default_id = matched_item['prod_id']
    default_p_unit = matched_item['purchase_unit']
    default_u_unit = matched_item['use_unit']
    default_c_factor = float(matched_item['conversion_factor'])
    default_safety = float(matched_item['safety_stock'])
    st.info(f"💡 識別成功：編號為 {default_id}。")
else:
    chosen_name, default_id, default_p_unit, default_u_unit, default_c_factor, default_safety = "", "", "", "", 1.0, 0.0

with st.form("clean_po_form"):
    final_id = st.text_input("項目編號", value=default_id, disabled=True)
    
    if prefix == 'C':
        st.markdown("##### 💰 2. 請輸入本次帳單金額：")
        total_invoice_amount = st.number_input("本次帳單【繳費總金額】($)", min_value=0.0, value=0.0, step=10.0)
        p_unit, u_unit, c_factor, po_qty, s_stock = "次", "次", 1.0, 1.0, 0.0
        v_name, v_phone, exp_str = "公共事業/其他", "", ""
    else:
        st.markdown("##### 📦 2. 確認或填寫本批次的包裝規格與單位：")
        col_spec1, col_spec2, col_spec3 = st.columns(3)
        with col_spec1: p_unit = st.text_input("大包裝進貨單位 (如：包、箱)", value=default_p_unit)
        with col_spec2: u_unit = st.text_input("廚房基本使用小單位 (如：g、個)", value=default_u_unit)
        with col_spec3: c_factor = st.number_input("轉換率 (1大包裝內含多少基本小單位)", min_value=0.0, value=default_c_factor, step=1.0)

        st.markdown("##### 💰 3. 請填寫本次採購的實際數據與供應商資訊：")
        col_po1, col_po2, col_po3 = st.columns(3)
        with col_po1: po_qty = st.number_input(f"進貨大包裝總數量", min_value=0.0, value=0.0, step=1.0)
        with col_po2: total_invoice_amount = st.number_input("本次進貨【採購總金額】($)", min_value=0.0, value=0.0, step=10.0)
        with col_po3: s_stock = st.number_input(f"設定最低安全預警量", min_value=0.0, value=default_safety, step=1.0)
            
        col_vendor1, col_vendor2, col_vendor3 = st.columns(3)
        with col_vendor1: v_name = st.text_input("供應商店名 (選填)", value="")
        with col_vendor2: v_phone = st.text_input("供應商電話 (選填)", value="")
        with col_vendor3:
            expiry_input = st.date_input("請選取有效期限 (用品無效期可不選)", value=None, key="po_exp_date")
            exp_str = expiry_input.strftime("%Y-%m-%d") if expiry_input is not None else ""

    total_use_units = po_qty * c_factor
    calculated_single_cost = total_invoice_amount / total_use_units if total_use_units > 0 else 0.0
    
    submit_po = st.form_submit_button("📥 確認無誤，送出登記")
    
    if submit_po:
        if not chosen_name or total_invoice_amount <= 0:
            st.error("❌ 錯誤：請確認品名與金額皆已確實填寫！")
        else:
            conn = sqlite3.connect('inventory.db')
            cursor = conn.cursor()
            cursor.execute('''INSERT OR REPLACE INTO products (prod_id, prod_name, cost, price, safety_stock, purchase_unit, use_unit, conversion_factor) 
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', (final_id, chosen_name, calculated_single_cost, 0.0, s_stock, p_unit, u_unit, c_factor))
            cursor.execute('''INSERT INTO stock_batches (prod_id, qty, expiry_date, inbound_date, vendor_name, vendor_phone) 
                              VALUES (?, ?, ?, ?, ?, ?)''', (final_id, total_use_units, exp_str, datetime.now().strftime("%Y-%m-%d"), v_name, v_phone))
            conn.commit()
            conn.close()
            
            log_action = "帳單支出登記" if prefix == 'C' else "採購進貨"
            log_history(current_user, log_action, f"登記 {chosen_name}，金額 ${total_invoice_amount}")
            st.success(f"🎉【登記成功】{item_type[:2]}「{chosen_name}」已成功記錄！")
            st.rerun()