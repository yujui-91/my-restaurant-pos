# pages/1_POS結帳.py
import streamlit as st
import pandas as pd
import sqlite3
from database.db_core import log_history, deduct_stock_fifo, get_next_dish_id, update_dish_and_bom

st.subheader("🛒 收銀結帳系統")

current_user = st.session_state.get('current_user', '老 闆')

# 初始化 Session State
if 'current_recipe_list' not in st.session_state:
    st.session_state.current_recipe_list = []
if 'last_loaded_dish' not in st.session_state:
    st.session_state.last_loaded_dish = ""

conn = sqlite3.connect('inventory.db')
existing_dishes = pd.read_sql_query("SELECT prod_id, prod_name, price FROM products WHERE price > 0", conn)
all_raw_df = pd.read_sql_query("SELECT prod_id, prod_name, use_unit, cost FROM products WHERE price = 0", conn)
conn.close()

# 建立功能分頁：一個收銀、一個餐點參數修改
pos_tabs = st.tabs(["💰 前台收銀結帳", "✏️ 餐點細項修改"])

with pos_tabs[0]:
    st.markdown("##### 🔍 1. 請選取客人點購的餐點：")
    col_dish1, col_dish2, col_dish3 = st.columns(3)
    with col_dish1:
        dish_options = ["--- 請選擇菜單既有餐點 ---"] + existing_dishes['prod_name'].tolist()
        selected_dish_select = st.selectbox("【既有餐點】直接下拉點餐", dish_options, index=0)
    with col_dish2:
        selected_dish_input = st.text_input("【新創/臨時餐點】", value="")
    with col_dish3:
        # 限制只能輸入整數步長
        dish_sale_price = st.number_input("販售價格", min_value=0, value=0, step=1)

    # 判定新餐點或既有餐點加載邏輯
    is_new_dish = selected_dish_input.strip() != ""
    if is_new_dish:
        final_dish_name = selected_dish_input.strip()
        final_dish_id = get_next_dish_id()
        if st.session_state.last_loaded_dish != final_dish_id:
            st.session_state.current_recipe_list = [] 
            st.session_state.last_loaded_dish = final_dish_id
    elif selected_dish_select != "--- 請選擇菜單既有餐點 ---":
        final_dish_name = selected_dish_select
        matched_dish_row = existing_dishes[existing_dishes['prod_name'] == final_dish_name].iloc[0]
        final_dish_id = matched_dish_row['prod_id']
        if dish_sale_price == 0:
            dish_sale_price = int(matched_dish_row['price'])
            
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
                mat_info = all_raw_df[all_raw_df['prod_name'] == add_mat_name].iloc[0]
                base_qty = add_mat_qty
                current_input_unit = chosen_input_unit.strip()
                
                if "公斤" in current_input_unit or "(kg)" in current_input_unit.lower(): base_qty = add_mat_qty * 1000.0
                elif "台斤" in current_input_unit: base_qty = add_mat_qty * 600.0
                elif "公升" in current_input_unit or "(l)" in current_input_unit.lower(): base_qty = add_mat_qty * 1000.0

                final_converted_qty = base_qty
                sys_unit = mat_info['use_unit'].strip().lower()
                if sys_unit in ['kg', '公斤']: final_converted_qty = base_qty / 1000.0
                elif sys_unit in ['台斤']: final_converted_qty = base_qty / 600.0
                elif sys_unit in ['l', '公升']: final_converted_qty = base_qty / 1000.0
                
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
                "單位用量": st.column_config.NumberColumn("單位用量 (可雙擊修改)", min_value=0.0001, format="%.4f"),
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
            st.toast("✏️ 配方變更已保留！")
            st.rerun()

        dish_calculated_cost_single = 0.0
        for item in st.session_state.current_recipe_list:
            c_cost = all_raw_df[all_raw_df['prod_id'] == item['食材編號']]['cost'].values[0]
            dish_calculated_cost_single += item['單位用量'] * c_cost
            
        sale_qty = st.number_input("客人本次點購總數量 (份)", min_value=1, value=1)
        
        if final_dish_name != "":
            st.markdown(f"> 💰 **餐點毛利核算：** **{final_dish_name}** | 單份預估成本：**${dish_calculated_cost_single:,.2f} 元** | 本次總銷售額：**${float(dish_sale_price * sale_qty):,.0f} 元**")
            
        if st.button("🔥 確認送出收銀結帳（執行扣料）"):
            # 需求 1 限制：價格必須大於 0 且為整數
            if dish_sale_price <= 0:
                st.error("❌ 錯誤：販售價格必須為大於 0 的整數！")
            else:
                conn = sqlite3.connect('inventory.db')
                cursor = conn.cursor()
                
                # 檢查庫存
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
                    cursor.execute('''INSERT OR REPLACE INTO products (prod_id, prod_name, cost, price, safety_stock, purchase_unit, use_unit, conversion_factor)
                                      VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', (final_dish_id, final_dish_name, dish_calculated_cost_single, float(dish_sale_price), 0, '份', '份', 1.0))
                    cursor.execute("DELETE FROM bom WHERE parent_id = ?", (final_dish_id,))
                    
                    details_log = f"前台銷售「{final_dish_name} × {sale_qty} 份」，總金額 ${dish_sale_price * sale_qty}。"
                    log_mats = []
                    for item in st.session_state.current_recipe_list:
                        total_need = item['單位用量'] * sale_qty
                        deduct_stock_fifo(item['食材編號'], total_need, cursor)
                        log_mats.append(f"{item['食材名稱']}_{item['食材編號']}({total_need}{item['單位']})")
                        cursor.execute("INSERT INTO bom VALUES (?, ?, ?)", (final_dish_id, item['食材編號'], item['單位用量']))
                        
                    conn.commit()
                    conn.close()
                    log_history(current_user, f"餐點收銀結帳-{final_dish_name}", details_log + " 消耗食材: " + ", ".join(log_mats))
                    
                    # 需求 3：懸浮小視窗提示
                    st.toast(f"🔔 收銀成功！已售出 {final_dish_name} × {sale_qty} 份，金額：${dish_sale_price * sale_qty}", icon="🎉")
                    st.session_state.current_recipe_list = [] 
                    st.rerun()
    else:
        st.info("💡 請選取品項並添加原物料配方比例。")

# 需求 1 新增：餐點係數（價格與配料）隨時修改面板
with pos_tabs[1]:
    st.markdown("##### ⚙️ 調整現有餐點的售價或標準配料量：")
    if existing_dishes.empty:
        st.info("目前尚無既有餐點可供修改。")
    else:
        edit_dish_options = existing_dishes['prod_name'].tolist()
        target_dish_name = st.selectbox("🎯 請選取要修改的餐點：", edit_dish_options, key="edit_dish_box")
        
        matched_dish = existing_dishes[existing_dishes['prod_name'] == target_dish_name].iloc[0]
        td_id = matched_dish['prod_id']
        old_price = int(float(matched_dish['price']))
        
        # 1. 修改價格
        new_dish_price = st.number_input("更正後的販售價格 (必須為大於 0 的整數)", min_value=1, value=old_price, step=1, key="edit_price_input")
        
        # 2. 顯示並載入目前配方進 Data Editor
        conn = sqlite3.connect('inventory.db')
        current_bom_df = pd.read_sql_query('''
            SELECT p.prod_name as 食材名稱, b.child_id as 食材編號, b.qty_needed as 單位用量, p.use_unit as 單位
            FROM bom b JOIN products p ON b.child_id = p.prod_id WHERE b.parent_id = ?
        ''', conn, params=(td_id,))
        conn.close()
        
        st.markdown("###### 📋 該餐點的標準配方明細：")
        edited_bom_df = st.data_editor(
            current_bom_df,
            column_config={
                "食材編號": st.column_config.TextColumn("食材編號", disabled=True),
                "食材名稱": st.column_config.TextColumn("食材名稱", disabled=True),
                "單位用量": st.column_config.NumberColumn("單份用量調整", min_value=0.0, format="%.4f"),
                "單位": st.column_config.TextColumn("單位", disabled=True)
            },
            key="bom_editor",
            use_container_width=True
        )
        
        if st.button("💾 確認儲存餐點價格與配方變更"):
            # 建立詳細歷史更動紀錄 (需求 5 核心)
            change_details = f"修改餐點【{target_dish_name}({td_id})】配置：\n"
            if new_dish_price != old_price:
                change_details += f" * 價格：從 ${old_price} 改為 ${new_dish_price}\n"
                
            recipe_list_to_save = []
            for idx, row in edited_bom_df.iterrows():
                orig_qty = current_bom_df.iloc[idx]["單位用量"]
                new_qty = float(row["單位用量"])
                if orig_qty != new_qty:
                    change_details += f" * 食材【{row['食材名稱']}】用量：從 {orig_qty} 改為 {new_qty} {row['單位']}\n"
                recipe_list_to_save.append({"食材編號": row["食材編號"], "單位用量": new_qty})
                
            update_dish_and_bom(td_id, float(new_dish_price), recipe_list_to_save)
            log_history(current_user, f"修正餐點參數-{target_dish_name}", change_details)
            
            # 需求 3：懸浮視窗通知
            st.toast(f"💾 餐點【{target_dish_name}】參數與配方已成功覆蓋更新！", icon="✅")
            st.rerun()