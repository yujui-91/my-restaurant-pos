# pages/1_POS結帳.py
import streamlit as st
import pandas as pd
import sqlite3
from database.db_core import log_history, deduct_stock_fifo, get_next_dish_id, update_dish_and_bom, trigger_toast, show_pending_toast

show_pending_toast()

st.subheader("🛒 收銀結帳系統")

current_user = st.session_state.get('current_user', '老 闆')

if 'current_recipe_list' not in st.session_state:
    st.session_state.current_recipe_list = []
if 'last_loaded_dish' not in st.session_state:
    st.session_state.last_loaded_dish = ""

if 'pos_select_dish' not in st.session_state:
    st.session_state.pos_select_dish = "--- 請選擇菜單既有餐點 ---"
if 'pos_input_dish' not in st.session_state:
    st.session_state.pos_input_dish = ""
if 'pos_dish_price_val' not in st.session_state:
    st.session_state.pos_dish_price_val = 0

conn = sqlite3.connect('inventory.db')
existing_dishes = pd.read_sql_query("SELECT prod_id, prod_name, price FROM products WHERE status = 1 AND prod_id LIKE 'P%'", conn)
all_raw_df = pd.read_sql_query("SELECT prod_id, prod_name, use_unit, cost FROM products WHERE status = 1 AND (prod_id LIKE 'R%' OR prod_id LIKE 'S%')", conn)
conn.close()

pos_tabs = st.tabs(["💰 前台收銀結帳", "✏️ 餐點細項修改", "❌ 品項下架與管理區"])

with pos_tabs[0]:
    st.markdown("##### 🔍 1. 請選取或填寫客人點購的餐點：")
    
    def on_existing_dish_change():
        st.session_state.pos_select_dish = st.session_state.select_dish_widget
        if st.session_state.pos_select_dish == "--- 請選擇菜單既有餐點 ---":
            st.session_state.pos_dish_price_val = 0
            st.session_state.pos_input_dish = ""
        else:
            st.session_state.pos_input_dish = ""
            st.session_state.pos_dish_price_val = 0

    def on_new_dish_change():
        input_val = st.session_state.input_dish_widget.strip()
        if input_val != "":
            matched_existing = existing_dishes[existing_dishes['prod_name'] == input_val]
            if not matched_existing.empty:
                st.error(f"❌ 錯誤：【{input_val}】已存在於既有菜單中！請改用【模式 A】從菜單選取，不可用打字的。")
                st.session_state.pos_select_dish = "--- 請選擇菜單既有餐點 ---"
                st.session_state.pos_input_dish = ""
                st.session_state.pos_dish_price_val = 0
                st.session_state.last_loaded_dish = "" 
            else:
                st.session_state.pos_select_dish = "--- 請選擇菜單既有餐點 ---"
                st.session_state.pos_input_dish = input_val
                st.session_state.pos_dish_price_val = 0
        else:
            st.session_state.pos_dish_price_val = 0
            st.session_state.pos_input_dish = ""

    is_new_dish_active = st.session_state.pos_input_dish.strip() != ""
    is_existing_dish_active = st.session_state.pos_select_dish != "--- 請選擇菜單既有餐點 ---"

    col_dish1, col_dish2, col_dish3 = st.columns(3)
    
    with col_dish1:
        dish_options = ["--- 請選擇菜單既有餐點 ---"] + existing_dishes['prod_name'].tolist()
        selected_dish_select = st.selectbox(
            "【模式 A】從菜單選取既有餐點", 
            dish_options, 
            key="select_dish_widget",
            index=dish_options.index(st.session_state.pos_select_dish) if st.session_state.pos_select_dish in dish_options else 0,
            disabled=is_new_dish_active,
            on_change=on_existing_dish_change
        )
        
    with col_dish2:
        selected_dish_input = st.text_input(
            "【模式 B】手動輸入臨時/新創餐點", 
            key="input_dish_widget",
            value=st.session_state.pos_input_dish,
            disabled=is_existing_dish_active,
            on_change=on_new_dish_change
        )
        
    with col_dish3:
        if is_existing_dish_active and st.session_state.pos_dish_price_val == 0:
            matched_row = existing_dishes[existing_dishes['prod_name'] == st.session_state.pos_select_dish]
            if not matched_row.empty:
                st.session_state.pos_dish_price_val = int(matched_row.iloc[0]['price'])

        dish_sale_price = st.number_input(
            "販售價格", 
            min_value=0, 
            step=1,
            value=int(st.session_state.pos_dish_price_val),
            disabled=is_existing_dish_active
        )
        st.session_state.pos_dish_price_val = dish_sale_price

    if is_new_dish_active:
        final_dish_name = st.session_state.pos_input_dish.strip()
        final_dish_id = get_next_dish_id()
        if st.session_state.last_loaded_dish != final_dish_id:
            st.session_state.current_recipe_list = [] 
            st.session_state.last_loaded_dish = final_dish_id
            
    elif is_existing_dish_active:
        final_dish_name = st.session_state.pos_select_dish
        matched_dish_rows = existing_dishes[existing_dishes['prod_name'] == final_dish_name]
        
        if not matched_dish_rows.empty:
            matched_dish_row = matched_dish_rows.iloc[0]
            final_dish_id = matched_dish_row['prod_id']
            
            if st.session_state.last_loaded_dish != final_dish_id:
                conn = sqlite3.connect('inventory.db')
                db_recipe = pd.read_sql_query('''
                    SELECT p.prod_name as 食材名稱, b.child_id as 食材編號, b.qty_needed as 單位用量, p.use_unit as 單位
                    FROM bom b JOIN products p ON b.child_id = p.prod_id WHERE b.parent_id = ?
                ''', conn, params=(final_dish_id,))
                conn.close()
                st.session_state.current_recipe_list = db_recipe.to_dict(orient='records')
                st.session_state.last_loaded_dish = final_dish_id
        else:
            final_dish_name = ""
            final_dish_id = ""
            st.session_state.current_recipe_list = []
    else:
        final_dish_name = ""
        final_dish_id = ""
        st.session_state.current_recipe_list = []

    st.markdown("---")
    st.markdown("##### ➕ 2. 現場食材加料/自訂配方調整區：")
    col_add1, col_add2, col_add3, col_add4 = st.columns([2, 1, 1, 1])
    with col_add1:
        add_mat_name = st.selectbox("選擇要加入/調整的食材名稱", ["--- 請選擇食材 ---"] + all_raw_df['prod_name'].tolist())
    with col_add2:
        chosen_input_unit = st.selectbox("本次輸入使用的單位", ["公克 (g)", "公斤 (kg)", "台斤", "毫升 (ml)", "公升 (L)", "個/顆/份"], index=0)
    with col_add3:
        add_mat_qty = st.number_input(f"單份餐點用量", min_value=0.0, value=0.0, step=1.0)
    with col_add4:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("➕ 加入配方清單"):
            if add_mat_name == "--- 請選擇食材 ---" or add_mat_qty <= 0:
                st.error("請選擇有效食材並輸入大於 0 的用量！")
            else:
                matched_mats = all_raw_df[all_raw_df['prod_name'] == add_mat_name]
                if matched_mats.empty:
                    st.error(f"❌ 錯誤：食材【{add_mat_name}】可能已被下架或停用，請重新整頁！")
                else:
                    mat_info = matched_mats.iloc[0]
                    base_qty = add_mat_qty
                    
                    current_input_unit = chosen_input_unit.strip().lower()
                    if "公斤" in current_input_unit or "kg" in current_input_unit:
                        base_qty = add_mat_qty * 1000.0
                    elif "台斤" in current_input_unit or "臺斤" in current_input_unit:
                        base_qty = add_mat_qty * 600.0
                    elif "公升" in current_input_unit or "l" in current_input_unit:
                        base_qty = add_mat_qty * 1000.0

                    final_converted_qty = base_qty
                    sys_unit = mat_info['use_unit'].strip().lower()
                    if sys_unit in ['kg', '公斤']: 
                        final_converted_qty = base_qty / 1000.0
                    elif sys_unit in ['台斤', '臺斤']: 
                        final_converted_qty = base_qty / 600.0
                    elif sys_unit in ['l', '公升']: 
                        final_converted_qty = base_qty / 1000.0
                    
                    existing_idx = next((i for i, item in enumerate(st.session_state.current_recipe_list) if item['食材編號'] == mat_info['prod_id']), None)
                    new_item_dict = {"食材名稱": mat_info['prod_name'], "食材編號": mat_info['prod_id'], "單位用量": final_converted_qty, "單位": mat_info['use_unit']}
                    if existing_idx is not None:
                        st.session_state.current_recipe_list[existing_idx] = new_item_dict
                    else:
                        st.session_state.current_recipe_list.append(new_item_dict)
                    st.rerun()

    if st.session_state.current_recipe_list:
        df_recipe_view = pd.DataFrame(st.session_state.current_recipe_list)
        df_recipe_view["移除"] = False
        edited_df = st.data_editor(
            df_recipe_view,
            column_config={
                "食材編號": st.column_config.TextColumn("食材編號", disabled=True),
                "食材名稱": st.column_config.TextColumn("食材名稱", disabled=True),
                "單位用量": st.column_config.NumberColumn("單位用量 (可雙擊修改)", format="%.4f"),
                "移除": st.column_config.CheckboxColumn("勾選移除", default=False)
            },
            disabled=["食材編號", "食材名稱", "單位"],
            key="recipe_editor",
            use_container_width=True
        )
        
        has_changes = False
        new_recipe_list = []
        for idx, row in edited_df.iterrows():
            if row["移除"]:
                has_changes = True
                continue
            if row["單位用量"] != st.session_state.current_recipe_list[idx]["單位用量"]:
                has_changes = True
            new_recipe_list.append({"食材名稱": row["食材名稱"], "食材編號": row["食材編號"], "單位用量": float(row["單位用量"]), "單位": row["單位"]})
            
        if has_changes:
            st.session_state.current_recipe_list = new_recipe_list
            trigger_toast("✏️ 配方變更已保留！", icon="📝")
            st.rerun()

        dish_calculated_cost_single = 0.0
        for item in st.session_state.current_recipe_list:
            conn = sqlite3.connect('inventory.db')
            cursor = conn.cursor()
            cursor.execute("SELECT cost FROM products WHERE prod_id = ?", (item['食材編號'],))
            cost_row = cursor.fetchone()
            conn.close()
            c_cost = cost_row[0] if cost_row else 0.0
            dish_calculated_cost_single += item['單位用量'] * c_cost
            
        sale_qty = st.number_input("客人本次點購總數量 (份)", min_value=1, value=1)
        
        if final_dish_name != "":
            total_estimated_cost = dish_calculated_cost_single * sale_qty
            total_estimated_revenue = float(st.session_state.pos_dish_price_val * sale_qty)
            estimated_profit = total_estimated_revenue - total_estimated_cost
            
            st.markdown(f"""
            > 💰 **餐點即時利潤核算面板（本次共 {sale_qty} 份）：**
            > * 餐點名稱：**{final_dish_name}** ｜ 單份食材成本：**${dish_calculated_cost_single:,.2f} 元**
            > * 本次【**總食材成本**】：**${total_estimated_cost:,.2f} 元** *(已自動 × {sale_qty} 份)*
            > * 本次【**總銷售金額**】：**${total_estimated_revenue:,.0f} 元**
            > * 本單【**預估純利潤**】：**${estimated_profit:,.2f} 元** (毛利率: {((estimated_profit/total_estimated_revenue)*100) if total_estimated_revenue > 0 else 0:.1f}%)
            """)
            
        if st.button("🔥 確認送出收銀結帳（執行扣料）"):
            if st.session_state.pos_dish_price_val <= 0:
                st.error("❌ 錯誤：販售價格必須為大於 0 的整數！")
            else:
                conn = sqlite3.connect('inventory.db')
                cursor = conn.cursor()
                
                disabled_item_detected = False
                disabled_msg = ""
                for item in st.session_state.current_recipe_list:
                    cursor.execute("SELECT status, prod_name FROM products WHERE prod_id = ?", (item['食材編號'],))
                    status_row = cursor.fetchone()
                    if status_row and status_row[0] == 0:
                        disabled_item_detected = True
                        disabled_msg += f" ❌ 無法提供餐點：原物料/用品【{status_row[1]}】目前已停用下架！\n"
                
                if disabled_item_detected:
                    st.error(disabled_msg)
                    conn.close()
                else:
                    insufficient = False
                    insufficient_msg = ""
                    for item in st.session_state.current_recipe_list:
                        total_need = item['單位用量'] * sale_qty
                        cursor.execute("SELECT SUM(qty) FROM stock_batches WHERE prod_id = ?", (item['食材編號'],))
                        current_stock = cursor.fetchone()[0] or 0
                        if current_stock < total_need:
                            insufficient = True
                            insufficient_msg += f" ❌ 庫存不足：【{item['食材名稱']}】需要 {total_need}，目前僅剩 {current_stock}！\n"
                            
                    if insufficient:
                        st.error(insufficient_msg)
                        conn.close()
                    else:
                        cursor.execute('''INSERT OR REPLACE INTO products (prod_id, prod_name, cost, price, safety_stock, purchase_unit, use_unit, conversion_factor, status)
                                          VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)''', (final_dish_id, final_dish_name, dish_calculated_cost_single, float(st.session_state.pos_dish_price_val), 0, '份', '份', 1.0))
                        cursor.execute("DELETE FROM bom WHERE parent_id = ?", (final_dish_id,))
                        
                        log_mats = []
                        actual_bill_food_cost = 0.0
                        
                        for item in st.session_state.current_recipe_list:
                            total_need = item['單位用量'] * sale_qty
                            success, deducted_cost_val = deduct_stock_fifo(item['食材編號'], total_need, cursor)
                            actual_bill_food_cost += deducted_cost_val
                            
                            log_mats.append(f"{item['食材名稱']}_{item['食材編號']}({total_need}{item['單位']})")
                            cursor.execute("INSERT INTO bom VALUES (?, ?, ?)", (final_dish_id, item['食材編號'], item['單位用量']))
                            
                        details_log = f"前台銷售「{final_dish_name} × {sale_qty} 份」，總金額 ${st.session_state.pos_dish_price_val * sale_qty}，精準食材成本 ${actual_bill_food_cost:.2f}。"
                        
                        conn.commit()
                        conn.close()
                        log_history(current_user, f"餐點收銀結帳-{final_dish_name}", details_log + " 消耗食材: " + ", ".join(log_mats))
                        
                        trigger_toast(f"收銀成功！已售出 {final_dish_name} × {sale_qty} 份，金額：${st.session_state.pos_dish_price_val * sale_qty}", icon="🎉")
                        
                        # ==========================================================
                        # 💡 核心修正：除了清洗後台參數外，同步徹底刪除 UI 元件在快取區的記憶 Key
                        # ==========================================================
                        st.session_state.pos_select_dish = "--- 請選擇菜單既有餐點 ---"
                        st.session_state.pos_input_dish = ""
                        st.session_state.pos_dish_price_val = 0
                        st.session_state.current_recipe_list = [] 
                        st.session_state.last_loaded_dish = "" 
                        
                        # 徹底斬斷 Streamlit 畫面元件的內部記憶，強迫重新整理後直接跳回預設初始狀態
                        if "select_dish_widget" in st.session_state:
                            del st.session_state["select_dish_widget"]
                        if "input_dish_widget" in st.session_state:
                            del st.session_state["input_dish_widget"]
                            
                        st.rerun()
    else:
        st.info("💡 請選取品項並添加原物料配方比例。")

with pos_tabs[1]:
    st.markdown("##### ⚙️ 調整現有餐點的售價或標準配料量：")
    if existing_dishes.empty:
        st.info("目前尚無既有餐點可供修改。")
    else:
        edit_dish_options = existing_dishes['prod_name'].tolist()
        target_dish_name = st.selectbox("🎯 請選取要修改的餐點：", edit_dish_options, key="edit_dish_box")
        
        matched_dish_edit = existing_dishes[existing_dishes['prod_name'] == target_dish_name]
        
        if not matched_dish_edit.empty:
            matched_dish = matched_dish_edit.iloc[0]
            td_id = matched_dish['prod_id']
            old_price = int(float(matched_dish['price']))
            
            new_dish_price = st.number_input("更正後的販售價格 (必須為大於 0 的整數)", step=1, key="edit_price_input", value=max(old_price, 1))
            
            if 'editing_recipe_dish_id' not in st.session_state or st.session_state.editing_recipe_dish_id != td_id:
                conn = sqlite3.connect('inventory.db')
                db_recipe = pd.read_sql_query('''
                    SELECT p.prod_name as 食材名稱, b.child_id as 食材編號, b.qty_needed as 單位用量, p.use_unit as 單位
                    FROM bom b JOIN products p ON b.child_id = p.prod_id WHERE b.parent_id = ?
                ''', conn, params=(td_id,))
                conn.close()
                st.session_state.editing_recipe_list = db_recipe.to_dict(orient='records')
                st.session_state.editing_recipe_dish_id = td_id

            st.markdown("----")
            st.markdown("##### ➕ 追加新原物料至此餐點標準配方：")
            col_add_e1, col_add_e2, col_add_e3 = st.columns([2, 1, 1])
            with col_add_e1:
                add_edit_mat_name = st.selectbox("選擇要追加的食材/用品", ["--- 請選擇食材 ---"] + all_raw_df['prod_name'].tolist(), key="add_edit_mat_select")
            with col_add_e2:
                add_edit_mat_qty = st.number_input("單份標準用量", min_value=0.0001, value=1.0, step=1.0, key="add_edit_mat_qty_input")
            with col_add_e3:
                st.markdown("<br>", unsafe_allow_html=True)
                if st.button("➕ 追加至此餐點配方", key="add_now_btn"):
                    if add_edit_mat_name != "--- 請選擇食材 ---" and add_edit_mat_qty > 0:
                        matched_mats = all_raw_df[all_raw_df['prod_name'] == add_edit_mat_name]
                        if not matched_mats.empty:
                            mat_info = matched_mats.iloc[0]
                            existing_idx = next((i for i, item in enumerate(st.session_state.editing_recipe_list) if item['食材編號'] == mat_info['prod_id']), None)
                            
                            new_item_dict = {
                                "食材名稱": mat_info['prod_name'], 
                                "食材編號": mat_info['prod_id'], 
                                "單位用量": float(add_edit_mat_qty), 
                                "單位": mat_info['use_unit']
                            }
                            
                            if existing_idx is not None:
                                st.session_state.editing_recipe_list[existing_idx] = new_item_dict
                            else:
                                st.session_state.editing_recipe_list.append(new_item_dict)
                            trigger_toast(f"已追加 {mat_info['prod_name']} 到暫存配方清單", icon="➕")
                            st.rerun()

            st.markdown("###### 📋 該餐點的標準配方明細（可雙擊直接修改數量或勾選移除）：")
            if st.session_state.editing_recipe_list:
                df_recipe_view = pd.DataFrame(st.session_state.editing_recipe_list)
                df_recipe_view["移除"] = False
                
                edited_df = st.data_editor(
                    df_recipe_view,
                    column_config={
                        "食材編號": st.column_config.TextColumn("食材編號", disabled=True),
                        "食材名稱": st.column_config.TextColumn("食材名稱", disabled=True),
                        "單位用量": st.column_config.NumberColumn("單份用量調整", format="%.4f", min_value=0.0001),
                        "單位": st.column_config.TextColumn("單位", disabled=True),
                        "移除": st.column_config.CheckboxColumn("勾選移除", default=False)
                    },
                    disabled=["食材編號", "食材名稱", "單位"],
                    key="bom_editor",
                    use_container_width=True
                )
                
                has_changes = False
                updated_recipe_list = []
                for idx, row in edited_df.iterrows():
                    if row["移除"]:
                        has_changes = True
                        continue
                    if row["單位用量"] != st.session_state.editing_recipe_list[idx]["單位用量"]:
                        has_changes = True
                    updated_recipe_list.append({
                        "食材名稱": row["食材名稱"], 
                        "食材編號": row["食材編號"], 
                        "單位用量": float(row["單位用量"]), 
                        "單位": row["單位"]
                    })
                    
                if has_changes:
                    st.session_state.editing_recipe_list = updated_recipe_list
                    trigger_toast("✏️ 暫存配方變更已保留！", icon="📝")
                    st.rerun()
            else:
                st.info("此餐點目前沒有任何配方物料，請從上方選單追加。")

            recipe_has_negative = any(float(item["單位用量"]) <= 0 for item in st.session_state.editing_recipe_list)
            
            if st.button("💾 確認儲存餐點價格與配方變更"):
                if new_dish_price <= 0:
                    st.error("❌ 錯誤變更：販售價格必須為大於 0 的整數！儲存失敗。")
                elif recipe_has_negative:
                    st.error("❌ 錯誤變更：配方用量必須大於 0！儲存失敗。")
                else:
                    change_details = f"修改餐點【{target_dish_name}({td_id})】配置：\n"
                    if new_dish_price != old_price:
                        change_details += f" * 價格：從 ${old_price} 改為 ${new_dish_price}\n"
                        
                    recipe_list_to_save = []
                    for item in st.session_state.editing_recipe_list:
                        recipe_list_to_save.append({"食材編號": item["食材編號"], "單位用量": item["單位用量"]})
                        change_details += f" * 食材【{item['食材名稱']}】用量設定為 {item['單位用量']} {item['單位']}\n"
                        
                    update_dish_and_bom(td_id, float(new_dish_price), recipe_list_to_save)
                    log_history(current_user, f"修正餐點參數-{target_dish_name}", change_details)
                    
                    trigger_toast(f"餐點【{target_dish_name}】參數與全新配方已成功覆蓋更新！", icon="⚙️")
                    del st.session_state.editing_recipe_list
                    del st.session_state.editing_recipe_dish_id
                    st.rerun()
        else:
            st.error("❌ 找不到該餐點資料，可能剛已被下架！")

with pos_tabs[2]:
    st.markdown("##### ❌ 菜單餐點下架控制面板")
    conn = sqlite3.connect('inventory.db')
    all_dishes_raw = pd.read_sql_query("SELECT prod_id, prod_name, price, status FROM products WHERE prod_id LIKE 'P%'", conn)
    conn.close()
    
    if all_dishes_raw.empty:
        st.info("系統中尚無餐點。")
    else:
        all_dishes_raw['狀態'] = all_dishes_raw['status'].apply(lambda s: "🔴 已下架隱藏" if s == 0 else "🟢 正常販售中")
        st.dataframe(all_dishes_raw[['prod_id', 'prod_name', 'price', '狀態']], use_container_width=True, hide_index=True)
        
        col_del1, col_del2 = st.columns(2)
        
        with col_del1:
            selected_del_dish = st.selectbox("🎯 選擇要【下架】或【重新上架】的餐點", all_dishes_raw['prod_id'] + " - " + all_dishes_raw['prod_name'])
            del_dish_id = selected_del_dish.split(" - ")[0]
            matched_del_dish_rows = all_dishes_raw[all_dishes_raw['prod_id'] == del_dish_id]
            
        with col_del2:
            st.markdown("<br>", unsafe_allow_html=True)
            if not matched_del_dish_rows.empty:
                matched_del_dish = matched_del_dish_rows.iloc[0]
                if matched_del_dish['status'] == 0:
                    if st.button("🟢 重新上架此餐點"):
                        conn = sqlite3.connect('inventory.db')
                        cursor = conn.cursor()
                        cursor.execute("UPDATE products SET status = 1 WHERE prod_id = ?", (del_dish_id,))
                        conn.commit()
                        conn.close()
                        log_history(current_user, f"餐點重新上架", f"上架餐點：{matched_del_dish['prod_name']}")
                        
                        trigger_toast(f"餐點【{matched_del_dish['prod_name']}】已重新上架！", icon="🚀")
                        st.rerun()
                else:
                    if st.button("🔴 確認將此餐點下架隱藏"):
                        conn = sqlite3.connect('inventory.db')
                        cursor = conn.cursor()
                        cursor.execute("UPDATE products SET status = 0 WHERE prod_id = ?", (del_dish_id,))
                        conn.commit()
                        conn.close()
                        log_history(current_user, f"餐點下架", f"下架餐點：{matched_del_dish['prod_name']}")
                        
                        trigger_toast(f"餐點【{matched_del_dish['prod_name']}】已成功下架！", icon="🗑️")
                        st.rerun()

    st.divider()
    st.markdown("##### ❌ 食材與用品庫存品項下架面板")
    conn = sqlite3.connect('inventory.db')
    all_mats_raw = pd.read_sql_query("SELECT prod_id, prod_name, use_unit, status FROM products WHERE prod_id LIKE 'R%' OR prod_id LIKE 'S%'", conn)
    conn.close()
    
    if all_mats_raw.empty:
        st.info("系統中尚無食材或用品。")
    else:
        all_mats_raw['狀態'] = all_mats_raw['status'].apply(lambda s: "🔴 已停用下架" if s == 0 else "🟢 正常進貨使用中")
        st.dataframe(all_mats_raw[['prod_id', 'prod_name', 'use_unit', '狀態']], use_container_width=True, hide_index=True)
        
        col_mat_del1, col_mat_del2 = st.columns(2)
        
        with col_mat_del1:
            selected_del_mat = st.selectbox("🎯 選擇要【下架停用】或【恢復使用】的食材/用品", all_mats_raw['prod_id'] + " - " + all_mats_raw['prod_name'])
            del_mat_id = selected_del_mat.split(" - ")[0]
            matched_del_mat_rows = all_mats_raw[all_mats_raw['prod_id'] == del_mat_id]
            
        with col_mat_del2:
            st.markdown("<br>", unsafe_allow_html=True)
            if not matched_del_mat_rows.empty:
                matched_del_mat = matched_del_mat_rows.iloc[0]
                if matched_del_mat['status'] == 0:
                    if st.button("🟢 恢復使用此食材/用品"):
                        conn = sqlite3.connect('inventory.db')
                        cursor = conn.cursor()
                        cursor.execute("UPDATE products SET status = 1 WHERE prod_id = ?", (del_mat_id,))
                        conn.commit()
                        conn.close()
                        log_history(current_user, f"食材恢復使用", f"恢復食材：{matched_del_mat['prod_name']}")
                        
                        trigger_toast(f"品項【{matched_del_mat['prod_name']}】已重新啟用！", icon="✅")
                        st.rerun()
                else:
                    if st.button("🔴 確認停用並下架此品項"):
                        conn = sqlite3.connect('inventory.db')
                        cursor = conn.cursor()
                        cursor.execute("UPDATE products SET status = 0 WHERE prod_id = ?", (del_mat_id,))
                        conn.commit()
                        conn.close()
                        log_history(current_user, f"食材停用下架", f"下架停用食材：{matched_del_mat['prod_name']}")
                        
                        trigger_toast(f"品項【{matched_del_mat['prod_name']}】已成功停用！", icon="🗑️")
                        st.rerun()