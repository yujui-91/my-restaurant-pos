# pages/2_採購進貨單.py
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
from database.db_core import log_history, get_next_raw_id, get_next_supply_id, get_next_bill_id, update_purchase_batch, trigger_toast, show_pending_toast

show_pending_toast()

st.subheader("📝 採購進貨與費用登記單")

current_user = st.session_state.get('current_user', '老 闆')

po_tabs = st.tabs(["📥 新進貨單登記", "✏️ 歷史採購單錯誤修正"])

with po_tabs[0]:
    item_type = st.radio("✨ 請選擇本次登記類別：", ["食材 (R 開頭)", "用品 (S 開頭)", "帳單費用 (C 開頭，如水電瓦斯)"], horizontal=True)

    if "食材" in item_type: prefix = 'R'
    elif "用品" in item_type: prefix = 'S'
    else: prefix = 'C'

    conn = sqlite3.connect('inventory.db')
    existing_items_df = pd.read_sql_query(
        "SELECT prod_id, prod_name, purchase_unit, use_unit, conversion_factor, safety_stock FROM products WHERE prod_id LIKE ?", 
        conn, params=(f"{prefix}%",)
    )
    conn.close()

    st.markdown("##### 🔍 1. 品項選取（二選一，具備智慧互斥防呆）：")
    if prefix in ['R', 'S']:
        st.caption("💡 知識庫：不同供應商的同名品項（例如：紙盒）建議分開建立為獨立項目，命名如 `[A廠商] 紙盒` 與 `[B廠商] 紙盒`。")

    col_choice1, col_choice2 = st.columns(2)
    has_input_text = "clean_po_input_name" in st.session_state and st.session_state.clean_po_input_name.strip() != ""
    
    with col_choice1:
        options_list = [f"--- 請選擇已建立的{item_type[:2]} ---"] + (existing_items_df['prod_name'].tolist())
        chosen_select_name = st.selectbox(
            "【重複登記】從這裡直接下拉搜尋既品項", 
            options_list, 
            index=0,
            key="clean_po_select_box",
            disabled=has_input_text  
        )
        
    has_selected_existing = chosen_select_name != f"--- 請選擇已建立的{item_type[:2]} ---"

    with col_choice2:
        chosen_input_name = st.text_input(
            f"【首次登記】在此直接手動打字輸入新{item_type[:2]}名稱", 
            value="",
            key="clean_po_input_name",
            disabled=has_selected_existing  
        )

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
            
    elif has_selected_existing:
        matched_item = existing_items_df[existing_items_df['prod_name'] == chosen_select_name].iloc[0]
        chosen_name = chosen_select_name
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
            st.markdown("##### 💰 2. 請輸入本次帳單金額與對應月份：")
            col_bill1, col_bill2 = st.columns(2)
            with col_bill1:
                total_invoice_amount = st.number_input("本次帳單【繳費總金額】($)", min_value=0.0, value=0.0, step=10.0)
            with col_bill2:
                bill_months_options = [f"{i}月" for i in range(1, 13)]
                current_month_idx = max(0, min(datetime.now().month - 1, 11))
                selected_month_str = st.selectbox("請選擇此費用【所屬月份】(必填)", bill_months_options, index=current_month_idx)
                
            p_unit, u_unit, c_factor, po_qty, s_stock = "次", "次", 1.0, 1.0, 0.0
            v_name, v_phone, exp_str = "公共事業/其他", "", ""
        else:
            st.markdown("##### 📦 2. 確認或填寫本批次的包裝規格與單位（皆為必填）：")
            col_spec1, col_spec2, col_spec3 = st.columns(3)
            with col_spec1: p_unit = st.text_input("大包裝進貨單位 (如:台斤、箱)", value=default_p_unit).strip()
            with col_spec2: u_unit = st.text_input("廚房基本使用小單位 (如:g、個)", value=default_u_unit).strip()
            with col_spec3: c_factor = st.number_input("轉換率 (一大包等於多少小單位)", min_value=0.0001, value=float(max(default_c_factor, 0.0001)), step=1.0)

            st.markdown("##### 💰 3. 請填寫本次採購的實際數據與供應商資訊：")
            col_po1, col_po2, col_po3 = st.columns(3)
            with col_po1: po_qty = st.number_input(f"進貨大包裝總數量", min_value=0.0, value=0.0, step=1.0)
            with col_po2: total_invoice_amount = st.number_input("本次進貨【採購總金額】($)", min_value=0.0, value=0.0, step=10.0)
            with col_po3: s_stock = st.number_input(f"設定最低安全預警量", min_value=0.0, value=float(default_safety), step=1.0)
                
            col_vendor1, col_vendor2, col_vendor3 = st.columns(3)
            with col_vendor1: v_name = st.text_input("供應商店名 (選填)", value="")
            with col_vendor2: v_phone = st.text_input("供應商電話 (選填)", value="")
            with col_vendor3:
                expiry_input = st.date_input("請選取有效期限", value=None, key="po_exp_date")
                exp_str = expiry_input.strftime("%Y-%m-%d") if expiry_input is not None else ""

        total_use_units = po_qty * c_factor
        calculated_single_cost = total_invoice_amount / total_use_units if total_use_units > 0 else 0.0
        
        submit_po = st.form_submit_button("📥 確認無誤，送出登記")
        
        if submit_po:
            if not chosen_name:
                st.error("❌ 錯誤：請選取重複登記項目或輸入首次登記項目名稱！")
            elif prefix != 'C' and p_unit == "":
                st.error("❌ 錯誤：【大包裝進貨單位】為必填欄位，請勿留空！")
            elif prefix != 'C' and u_unit == "":
                st.error("❌ 錯誤：【廚房基本使用小單位】為必填欄位，請勿留空！")
            elif prefix != 'C' and c_factor <= 0:
                st.error("❌ 錯誤：【轉換率】必須為大於 0 的有效數值！")
            elif prefix != 'C' and po_qty <= 0:
                st.error("❌ 錯誤：【進貨大包裝總數量】必須大於 0！")
            elif total_invoice_amount <= 0:
                st.error("❌ 錯誤：【本次進貨採購總金額】必須大於 0！")
            else:
                today_str = datetime.now().strftime("%Y-%m-%d")
                conn = sqlite3.connect('inventory.db')
                cursor = conn.cursor()
                
                cursor.execute('''INSERT OR REPLACE INTO products 
                                  (prod_id, prod_name, cost, price, safety_stock, purchase_unit, use_unit, conversion_factor, status)
                                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)''', 
                               (default_id, chosen_name, calculated_single_cost, 0.0, s_stock, p_unit, u_unit, c_factor))
                
                cursor.execute('''INSERT INTO stock_batches 
                                  (prod_id, qty, expiry_date, inbound_date, vendor_name, vendor_phone, cost, original_qty) 
                                  VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', 
                               (default_id, total_use_units, exp_str, today_str, v_name, v_phone, calculated_single_cost, total_use_units))
                conn.commit()
                conn.close()
                
                if prefix == 'C':
                    month_digits = int(selected_month_str.replace("月", ""))
                    current_year = datetime.now().year
                    formatted_target_month = f"{current_year}-{month_digits:02d}"
                    
                    log_action = "帳單支出登記"
                    log_history(current_user, log_action, f"新單登記：{chosen_name}，費用月份：{selected_month_str}，總金額：${total_invoice_amount}。 目標歸帳月份: {formatted_target_month}")
                    trigger_toast(f"帳單費用登記完成！【{chosen_name} ({selected_month_str}費用)】總金額：${total_invoice_amount}", icon="📥")
                else:
                    log_action = "採購進貨"
                    vendor_info = f" (供應商: {v_name})" if v_name else ""
                    log_history(current_user, log_action, f"新單登記：{chosen_name}，數量：{po_qty}{p_unit}，總金額：${total_invoice_amount}{vendor_info}")
                    trigger_toast(f"採購登記完成！【{chosen_name}】總金額：${total_invoice_amount}", icon="📥")
                
                st.rerun()

with po_tabs[1]:
    st.markdown("##### 🔍 歷史採購單精準篩選面板：")
    
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        time_filter = st.selectbox(
            "📅 選擇歷史進貨日期區間", 
            ["全選", "今天", "過去 7 天", "過去 1 個月", "自訂區間 (自選起訖日期)"], 
            key="po_history_time_filter"
        )
    
    now = datetime.now()
    start_date, end_date = None, None
    if time_filter == "今天":
        start_date = now.strftime("%Y-%m-%d")
        end_date = now.strftime("%Y-%m-%d")
    elif time_filter == "過去 7 天":
        start_date = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        end_date = now.strftime("%Y-%m-%d")
    elif time_filter == "過去 1 個月":
        start_date = (now - timedelta(days=30)).strftime("%Y-%m-%d")
        end_date = now.strftime("%Y-%m-%d")
    elif time_filter == "自訂區間 (自選起訖日期)":
        c_date1, c_date2 = st.columns(2)
        with c_date1: sd = st.date_input("進貨開始日", value=now.date() - timedelta(days=1), key="po_f_sd")
        with c_date2: ed = st.date_input("進貨結束日", value=now.date(), key="po_f_ed")
        start_date = sd.strftime("%Y-%m-%d")
        end_date = ed.strftime("%Y-%m-%d")

    with col_f2:
        cate_filter = st.selectbox(
            "🗂️ 篩選採購項目類別", 
            ["全部類別", "食材 (R)", "用品 (S)", "帳單費用 (C)"], 
            key="po_history_cate_filter"
        )
    
    query_conditions = []
    query_params = []
    
    if start_date and end_date:
        query_conditions.append("s.inbound_date BETWEEN ? AND ?")
        query_params.extend([start_date, end_date])
        
    if cate_filter == "食材 (R)":
        query_conditions.append("s.prod_id LIKE 'R%'")
    elif cate_filter == "用品 (S)":
        query_conditions.append("s.prod_id LIKE 'S%'")
    elif cate_filter == "帳單費用 (C)":
        query_conditions.append("s.prod_id LIKE 'C%'")
        
    where_clause = " WHERE " + " AND ".join(query_conditions) if query_conditions else ""

    conn = sqlite3.connect('inventory.db')
    df_all_batches = pd.read_sql_query(f'''
        SELECT s.batch_id as 批次編號, s.prod_id as 商品編號, p.prod_name as 商品名稱, 
               s.qty as 當前小單位庫存, s.original_qty as 原始小單位庫存, p.purchase_unit as 進貨單位, p.use_unit as 使用單位,
               p.conversion_factor as 轉換率, (s.original_qty / p.conversion_factor) as 進貨大包裝數,
               (s.qty / p.conversion_factor) as 剩餘大包裝數,
               (s.qty * s.cost) as 推估總金額, s.expiry_date as 有效期限, s.vendor_name as 供應商, s.vendor_phone as 供應商電話,
               s.inbound_date as 進貨日期
        FROM stock_batches s JOIN products p ON s.prod_id = p.prod_id 
        {where_clause}
        ORDER BY s.batch_id DESC
    ''', conn, params=query_params)
    conn.close()
    
    st.divider()
    
    if df_all_batches.empty:
        st.info("💡 沒有符合當前篩選條件的採購紀錄。")
    else:
        def format_batch_option(r):
            if str(r['商品編號']).startswith('C'):
                return f"【帳單費用】({r['進貨日期']}) {r['商品編號']}-{r['商品名稱']} (總金額:${r['推估總金額']:.0f}) — [編號:{int(r['批次編號'])}]"
            else:
                return f"【批次 {int(r['批次編號'])}】({r['進貨日期']}) {r['商品編號']}-{r['商品名稱']} (原進貨大包裝:{r['進貨大包裝數']:.1f}{r['進貨單位']}, 剩餘:{r['剩餘大包裝數']:.1f}{r['進貨單位']})"
                
        batch_options = df_all_batches.apply(format_batch_option, axis=1).tolist()
        selected_batch_str = st.selectbox("🎯 請選取您想要修改或補正的採購單批次：", batch_options)
        
        if " — [編號:" in selected_batch_str:
            target_batch_id = int(selected_batch_str.split(" — [編號:")[1].split("]")[0])
        else:
            target_batch_id = int(selected_batch_str.split("【批次 ")[1].split("】")[0])
            
        matched_batch_row = df_all_batches[df_all_batches['批次編號'] == target_batch_id].iloc[0]
        is_bill = str(matched_batch_row['商品編號']).startswith('C')
        
        st.markdown("---")
        
        if is_bill:
            st.markdown("##### 💰 填寫更正後的帳單金額與所屬月份（皆為必填）：")
            col_bill_e1, col_bill_e2 = st.columns(2)
            
            with col_bill_e1:
                new_total_amount = st.number_input(
                    "本次帳單【繳費總金額】($)", 
                    min_value=0.0, 
                    value=float(max(matched_batch_row['推估總金額'], 0.0)), 
                    step=10.0,
                    key="edit_bill_amount_input"
                )
            with col_bill_e2:
                bill_months_options = [f"{i}月" for i in range(1, 13)]
                current_month_str = str(datetime.now().month) + "月"
                
                conn = sqlite3.connect('inventory.db')
                cursor = conn.cursor()
                cursor.execute("SELECT details FROM history WHERE action = '帳單支出登記' AND details LIKE ? ORDER BY id DESC LIMIT 1", (f"%新單登記：{matched_batch_row['商品名稱']}%",))
                hist_row = cursor.fetchone()
                conn.close()
                
                if hist_row:
                    import re
                    month_match = re.search(r"費用月份：(\d+月)", hist_row[0])
                    if month_match:
                        current_month_str = month_match.group(1)
                
                if current_month_str in bill_months_options:
                    default_month_idx = bill_months_options.index(current_month_str)
                else:
                    cleaned_m = current_month_str.lstrip('0')
                    default_month_idx = bill_months_options.index(cleaned_m) if cleaned_m in bill_months_options else 0
                    
                selected_month_str = st.selectbox("請選擇此費用【所屬月份】(必填)", bill_months_options, index=default_month_idx, key="edit_bill_month_select")
                
            new_p_unit, new_u_unit, new_c_factor, new_po_qty, new_safety = "次", "次", 1.0, 1.0, 0.0
            new_v_name, new_v_phone, new_exp_str = "公共事業/其他", "", ""
            
        else:
            st.markdown("##### 📦 填寫更正後的包裝規格與採購數據：")
            col_edit1, col_edit2, col_edit3 = st.columns(3)
            with col_edit1:
                new_p_unit = st.text_input("大包裝進貨單位 (如:台斤、箱)", value=str(matched_batch_row['進貨單位'])).strip()
                new_po_qty = st.number_input("新設定的進貨大包裝總數量", min_value=0.0, value=float(max(matched_batch_row['進貨大包裝數'], 0.0)), step=1.0)
            with col_edit2:
                new_u_unit = st.text_input("廚房基本使用小單位 (如:g、個)", value=str(matched_batch_row['使用單位'])).strip()
                new_total_amount = st.number_input("本次進貨【採購總金額】($)", min_value=0.0, value=float(max(matched_batch_row['推估總金額'], 0.0)), step=10.0)
            with col_edit3:
                new_c_factor = st.number_input("轉換率 (一大包等於多少小單位)", min_value=0.0001, value=float(max(matched_batch_row['轉換率'], 0.0001)), step=1.0)
                new_safety = st.number_input("設定最低安全預警量", min_value=0.0, value=0.0, step=1.0)
                
            col_edit4, col_edit5, col_edit6 = st.columns(3)
            with col_edit4: new_v_name = st.text_input("更正供應商店名", value=str(matched_batch_row['供應商']))
            with col_edit5: new_v_phone = st.text_input("更正供應商電話", value=str(matched_batch_row['供應商電話']))
            with col_edit6:
                try: orig_date = datetime.strptime(matched_batch_row['有效期限'], "%Y-%m-%d").date()
                except: orig_date = None
                new_exp_input = st.date_input("更正有效期限", value=orig_date, key="edit_exp_date")
                new_exp_str = new_exp_input.strftime("%Y-%m-%d") if new_exp_input is not None else ""

        if st.button("💾 確認覆蓋並修正此筆採購資料"):
            if not is_bill and new_p_unit == "":
                st.error("❌ 錯誤：【大包裝進貨單位】為必填欄位，請勿留空！")
            elif not is_bill and new_u_unit == "":
                st.error("❌ 錯誤：【廚房基本使用小單位】為必填欄位，請勿留空！")
            elif not is_bill and new_c_factor <= 0:
                st.error("❌ 錯誤：【轉換率】必須為大於 0 的有效數值！")
            elif not is_bill and new_po_qty <= 0:
                st.error("❌ 錯誤：【進貨大包裝總數量】必須大於 0！")
            elif is_bill and new_total_amount <= 0:
                st.error("❌ 錯誤：【本次帳單繳費總金額】必須大於 0！")
            elif not is_bill and new_total_amount <= 0:
                st.error("❌ 錯誤：【本次進貨採購總金額】必須大於 0！")
            else:
                new_total_use_units = new_po_qty * new_c_factor if not is_bill else 1.0
                new_calculated_cost = new_total_amount / new_total_use_units if new_total_use_units > 0 else 0.0
                
                if is_bill:
                    edit_month_digits = int(selected_month_str.replace("月", ""))
                    current_year = datetime.now().year
                    formatted_edit_month = f"{current_year}-{edit_month_digits:02d}"
                    
                    audit_trail = f"歷史帳單修正【{matched_batch_row['商品名稱']}】:\n"
                    audit_trail += f" * 費用月份：覆蓋調整為 {selected_month_str}\n"
                    if float(matched_batch_row['推估總金額']) != new_total_amount:
                        audit_trail += f" * 帳單金額：自 ${matched_batch_row['推估總金額']:.0f} 修改為 ${new_total_amount:.0f}\n"
                    audit_trail += f" 目標歸帳月份: {formatted_edit_month}"
                else:
                    audit_trail = f"採購歷史修正【批次 {target_batch_id} - {matched_batch_row['商品名稱']}】:\n"
                    if float(matched_batch_row['進貨大包裝數']) != new_po_qty:
                        audit_trail += f" * 進貨數量：自 {matched_batch_row['進貨大包裝數']} 修改為 {new_po_qty}\n"
                    if float(matched_batch_row['推估總金額']) != new_total_amount:
                        audit_trail += f" * 採購總額：自 ${matched_batch_row['推估總金額']:.0f} 修改為 ${new_total_amount:.0f}\n"

                update_purchase_batch(target_batch_id, matched_batch_row['商品編號'], new_total_use_units, new_calculated_cost, new_p_unit, new_u_unit, new_c_factor, new_safety, new_v_name, new_v_phone, new_exp_str)
                log_history(current_user, "採購單更正", audit_trail)
                
                trigger_toast(f"💾 資料覆蓋成功！已防止已扣除庫存復活！", icon="✏️")
                st.rerun()