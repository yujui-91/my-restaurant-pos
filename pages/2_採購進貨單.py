# pages/2_採購進貨單.py
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
from database.db_core import log_history, get_next_raw_id, get_next_supply_id, get_next_bill_id, update_purchase_batch

st.subheader("📝 採購進貨與費用登記單")

current_user = st.session_state.get('current_user', '老 闆')

po_tabs = st.tabs(["📥 新進貨單登記", "✏️ 歷史採購單錯誤修正"])

with po_tabs[0]:
    item_type = st.radio("✨ 請選擇本次登記類別：", ["食材 (R 開頭)", "用品 (S 開頭)", "帳單費用 (C 開頭，如水電瓦斯)"], horizontal=True)

    if "食材" in item_type: prefix = 'R'
    elif "用品" in item_type: prefix = 'S'
    else: prefix = 'C'

    conn = sqlite3.connect('inventory.db')
    # 修正需求 2：不論價格為何，精準根據品項代號首字抓取完整清單，確保選單清晰
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
        chosen_input_name = st.text_input(f"【首次登記】在此直接手動打字輸入新{item_type[:2]}名稱", value="")

    if chosen_input_name.strip() != "":
        chosen_name = chosen_input_name.strip()
        conn = sqlite3.connect('inventory.db')
        cursor = conn.cursor()
        cursor.execute("SELECT prod_id, purchase_unit, use_unit, conversion_factor, safety_stock FROM products WHERE prod_name = ?", (chosen_name,))
        dup_check = cursor.fetchone()
        conn.close()
        
        if dup_check:
            default_id = dup_check[0]
            default_p_unit, default_u_unit, default_c_factor, default_safety = dup_check[1], dup_check[2], float(dup_check[3]), float(dup_check[4])
        else:
            if prefix == 'R': default_id = get_next_raw_id()
            elif prefix == 'S': default_id = get_next_supply_id()
            else: default_id = get_next_bill_id()
            default_p_unit, default_u_unit, default_c_factor, default_safety = "", "", 1.0, 0.0
            
    elif chosen_select_name != f"--- 請選擇已建立的{item_type[:2]} ---":
        chosen_name = chosen_select_name
        matched_item = existing_items_df[existing_items_df['prod_name'] == chosen_name].iloc[0]
        default_id = matched_item['prod_id']
        default_p_unit = matched_item['purchase_unit']
        default_u_unit = matched_item['use_unit']
        default_c_factor = float(matched_item['conversion_factor'])
        default_safety = float(matched_item['safety_stock'])
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
            with col_spec1: p_unit = st.text_input("大包裝進貨單位", value=default_p_unit)
            with col_spec2: u_unit = st.text_input("廚房基本使用小單位", value=default_u_unit)
            with col_spec3: c_factor = st.number_input("轉換率", min_value=0.0, value=default_c_factor, step=1.0)

            st.markdown("##### 💰 3. 請填寫本次採購的實際數據與供應商資訊：")
            col_po1, col_po2, col_po3 = st.columns(3)
            with col_po1: po_qty = st.number_input(f"進貨大包裝總數量", min_value=0.0, value=0.0, step=1.0)
            with col_po2: total_invoice_amount = st.number_input("本次進貨【採購總金額】($)", min_value=0.0, value=0.0, step=10.0)
            with col_po3: s_stock = st.number_input(f"設定最低安全預警量", min_value=0.0, value=default_safety, step=1.0)
                
            col_vendor1, col_vendor2, col_vendor3 = st.columns(3)
            with col_vendor1: v_name = st.text_input("供應商店名 (選填)", value="")
            with col_vendor2: v_phone = st.text_input("供應商電話 (選填)", value="")
            with col_vendor3:
                expiry_input = st.date_input("請選取有效期限", value=None, key="po_exp_date")
                exp_str = expiry_input.strftime("%Y-%m-%d") if expiry_input is not None else ""

        # pages/2_採購進貨單.py (新進貨單登記送出按鈕區塊優化)

        total_use_units = po_qty * c_factor
        calculated_single_cost = total_invoice_amount / total_use_units if total_use_units > 0 else 0.0
        
        submit_po = st.form_submit_button("📥 確認無誤，送出登記")
        
        if submit_po:
            if not chosen_name or total_invoice_amount <= 0 or (prefix != 'C' and po_qty <= 0):
                st.error("❌ 錯誤：請確認品名、數量與金額皆已填寫！")
            else:
                today_str = datetime.now().strftime("%Y-%m-%d")
                conn = sqlite3.connect('inventory.db')
                cursor = conn.cursor()
                
                # --- 【已刪除阻擋重複登記判別式】 ---
                # 允許同一天、進相同的蔬菜。每次進貨都視為獨立的一筆「新批次庫存」。
                
                # 更新品項基本資料：將此批次的單價更新為該原物料的最新參考單價，但不影響舊批次的庫存成本
                cursor.execute('''INSERT OR REPLACE INTO products 
                                  (prod_id, prod_name, cost, price, safety_stock, purchase_unit, use_unit, conversion_factor)
                                  VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', 
                               (default_id, chosen_name, calculated_single_cost, 0.0, s_stock, p_unit, u_unit, c_factor))
                
                # 寫入獨立的庫存批次表，這能確保不同供應商(v_name)與不同價格能獨立並存
                cursor.execute('''INSERT INTO stock_batches 
                                  (prod_id, qty, expiry_date, inbound_date, vendor_name, vendor_phone) 
                                  VALUES (?, ?, ?, ?, ?, ?)''', 
                               (default_id, total_use_units, exp_str, today_str, v_name, v_phone))
                conn.commit()
                conn.close()
                
                log_action = "帳單支出登記" if prefix == 'C' else "採購進貨"
                vendor_info = f" (供應商: {v_name})" if v_name else ""
                log_history(current_user, log_action, f"新單登記：{chosen_name}，數量：{po_qty}{p_unit}，總金額：${total_invoice_amount}{vendor_info}")
                
                st.toast(f"📦 採購登記完成！【{chosen_name}】總金額：${total_invoice_amount}", icon="📥")
                st.rerun()

with po_tabs[1]:
    st.markdown("##### ✏️ 2. 歷史採購單精準修正：")
    conn = sqlite3.connect('inventory.db')
    df_all_batches = pd.read_sql_query('''
        SELECT s.batch_id as 批次編號, s.prod_id as 商品編號, p.prod_name as 商品名稱, 
               s.qty as 當前小單位庫存, p.purchase_unit as 進貨單位, p.use_unit as 使用單位,
               p.conversion_factor as 轉換率, (s.qty / p.conversion_factor) as 進貨大包裝數,
               (s.qty * p.cost) as 推估總金額, s.expiry_date as 有效期限, s.vendor_name as 供應商, s.vendor_phone as 供應商電話
        FROM stock_batches s JOIN products p ON s.prod_id = p.prod_id ORDER BY s.batch_id DESC
    ''', conn)
    conn.close()
    
    if df_all_batches.empty:
        st.info("目前系統內尚無任何進貨批次紀錄。")
    else:
        batch_options = df_all_batches.apply(lambda r: f"【批次 {int(r['批次編號'])}】{r['商品編號']}-{r['商品名稱']} (進貨:{r['進貨大包裝數']:.1f}{r['進貨單位']}, 金額:${r['推估總金額']:.0f})", axis=1).tolist()
        selected_batch_str = st.selectbox("🎯 請選取您想要修改或補正的採購單批次：", batch_options)
        
        target_batch_id = int(selected_batch_str.split("【批次 ")[1].split("】")[0])
        matched_batch_row = df_all_batches[df_all_batches['批次編號'] == target_batch_id].iloc[0]
        
        st.markdown("---")
        col_edit1, col_edit2, col_edit3 = st.columns(3)
        with col_edit1:
            new_p_unit = st.text_input("修正大包裝單位", value=str(matched_batch_row['進貨單位']))
            new_po_qty = st.number_input("更正後的【進貨大包裝數量】", min_value=0.1, value=float(matched_batch_row['進貨大包裝數']), step=1.0)
        with col_edit2:
            new_u_unit = st.text_input("修正廚房使用單位", value=str(matched_batch_row['使用單位']))
            new_total_amount = st.number_input("更正後的【採購總金額】($)", min_value=0.0, value=float(matched_batch_row['推估總金額']), step=10.0)
        with col_edit3:
            new_c_factor = st.number_input("修正轉換率", min_value=1.0, value=float(matched_batch_row['轉換率']), step=1.0)
            new_safety = st.number_input("修正最低安全預警量", min_value=0.0, value=0.0, step=1.0)
            
        col_edit4, col_edit5, col_edit6 = st.columns(3)
        with col_edit4: new_v_name = st.text_input("更正供應商店名", value=str(matched_batch_row['供應商']))
        with col_edit5: new_v_phone = st.text_input("更正供應商電話", value=str(matched_batch_row['供應商電話']))
        with col_edit6:
            try: orig_date = datetime.strptime(matched_batch_row['有效期限'], "%Y-%m-%d").date()
            except: orig_date = None
            new_exp_input = st.date_input("更正有效期限", value=orig_date, key="edit_exp_date")
            new_exp_str = new_exp_input.strftime("%Y-%m-%d") if new_exp_input is not None else ""
            
        if st.button("💾 確認覆蓋並修正此筆採購資料"):
            new_total_use_units = new_po_qty * new_c_factor
            new_calculated_cost = new_total_amount / new_total_use_units if new_total_use_units > 0 else 0.0
            
            # 需求 5 審計歷史追蹤：細緻對比修改前後數據差異並寫入細節
            audit_trail = f"採購歷史修正【批次 {target_batch_id} - {matched_batch_row['商品名稱']}】:\n"
            if float(matched_batch_row['進貨大包裝數']) != new_po_qty:
                audit_trail += f" * 進貨數量：自 {matched_batch_row['進貨大包裝數']} 修改為 {new_po_qty}\n"
            if float(matched_batch_row['推估總金額']) != new_total_amount:
                audit_trail += f" * 採購總額：自 ${matched_batch_row['推估總金額']:.0f} 修改為 ${new_total_amount:.0f}\n"

            update_purchase_batch(target_batch_id, matched_batch_row['商品編號'], new_total_use_units, new_calculated_cost, new_p_unit, new_u_unit, new_c_factor, new_safety, new_v_name, new_v_phone, new_exp_str)
            log_history(current_user, "採購單更正", audit_trail)
            
            # 需求 3：修正完畢彈出懸浮提示
            st.toast(f"💾 批次 {target_batch_id} 資料覆蓋成功！", icon="✏️")
            st.rerun()  