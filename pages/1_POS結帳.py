# pages/1_🛒_POS 前台結帳單.py
import streamlit as st
import pandas as pd
import sqlite3
import re
from database.db_core import log_history, deduct_stock_fifo, get_next_dish_id

st.subheader("🛒 收銀結帳系統")

# 確保全域變數存在
current_user = st.session_state.get('current_user', '老 闆')

if 'current_recipe_list' not in st.session_state:
    st.session_state.current_recipe_list = []
if 'last_loaded_dish' not in st.session_state:
    st.session_state.last_loaded_dish = ""

conn = sqlite3.connect('inventory.db')
existing_dishes = pd.read_sql_query("SELECT prod_id, prod_name, price FROM products WHERE price > 0", conn)
all_raw_df = pd.read_sql_query("SELECT prod_id, prod_name, use_unit, cost FROM products WHERE price = 0", conn)
conn.close()

st.markdown("##### 🔍 1. 請選取或打字輸入客人點購的餐點：")
col_dish1, col_dish2, col_dish3 = st.columns(3)
with col_dish1:
    dish_options = ["--- 請選擇菜單既有餐點 ---"] + existing_dishes['prod_name'].tolist()
    selected_dish_select = st.selectbox("【既有餐點】直接下拉點餐", dish_options, index=0)
with col_dish2:
    selected_dish_input = st.text_input("【新創/臨時餐點】", value="")
with col_dish3:
    dish_sale_price = st.number_input("販售價格", min_value=0.0, value=0.0, step=10.0)

# 判定點餐與配方加載邏輯
if selected_dish_input.strip() != "":
    final_dish_name = selected_dish_input.strip()
    final_dish_id = get_next_dish_id()
    if st.session_state.last_loaded_dish != final_dish_id:
        st.session_state.current_recipe_list = [] 
        st.session_state.last_loaded_dish = final_dish_id
elif selected_dish_select != "--- 請選擇菜單既有餐點 ---":
    final_dish_name = selected_dish_select
    matched_dish_row = existing_dishes[existing_dishes['prod_name'] == final_dish_name].iloc[0]
    final_dish_id = matched_dish_row['prod_id']
    if dish_sale_price == 0.0:
        dish_sale_price = float(matched_dish_row['price'])
        
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

input_unit_options = ["公克 (g)", "公斤 (kg)", "台斤", "毫升 (ml)", "公升 (L)", "個/顆/份"]

with col_add2:
    chosen_input_unit = st.selectbox("本次輸入使用的單位", input_unit_options, index=0)
with col_add3:
    add_mat_qty = st.number_input(f"單份餐點用量", min_value=0.0, value=0.0, step=1.0)
with col_add4:
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("➕ 加入配方清單"):
        if add_mat_name == "--- 請選擇食材 ---" or add_mat_qty <= 0:
            st.error("請選擇有效食材並輸入大於 0 的用量！")
        else:
            mat_info = all_raw_df[all_raw_df['prod_name'] == add_mat_name].iloc[0]
            db_system_unit = mat_info['use_unit'].lower()
            
            current_input_unit = chosen_input_unit.strip()
            base_qty = add_mat_qty
            
            if "公克" in current_input_unit or "(g)" in current_input_unit.lower():
                base_qty = add_mat_qty * 1.0
            elif "公斤" in current_input_unit or "(kg)" in current_input_unit.lower():
                base_qty = add_mat_qty * 1000.0
            elif "台斤" in current_input_unit:
                base_qty = add_mat_qty * 600.0
            elif "公升" in current_input_unit or "(l)" in current_input_unit.lower():
                base_qty = add_mat_qty * 1000.0
            elif "毫升" in current_input_unit or "(ml)" in current_input_unit.lower():
                base_qty = add_mat_qty * 1.0

            final_converted_qty = base_qty
            sys_unit = db_system_unit.strip().lower()

            if sys_unit in ['g', '公克', 'G']:
                final_converted_qty = base_qty
            elif sys_unit in ['kg', '公斤', 'KG', 'Kg']:
                final_converted_qty = base_qty / 1000.0
            elif sys_unit in ['台斤','臺斤']:
                final_converted_qty = base_qty / 600.0
            elif sys_unit in ['ml', '毫升']:
                final_converted_qty = base_qty
            elif sys_unit in ['l', '公升']:
                final_converted_qty = base_qty / 1000.0
            
            existing_idx = next((i for i, item in enumerate(st.session_state.current_recipe_list) if item['食材編號'] == mat_info['prod_id']), None)
            new_item_dict = {
                "食材名稱": mat_info['prod_name'],
                "食材編號": mat_info['prod_id'],
                "單位用量": final_converted_qty,
                "單位": mat_info['use_unit']
            }
            if existing_idx is not None:
                st.session_state.current_recipe_list[existing_idx] = new_item_dict
            else:
                st.session_state.current_recipe_list.append(new_item_dict)
                
            st.success(f"調整成功！已自動將 {add_mat_qty} {chosen_input_unit} 換算為 {final_converted_qty} {mat_info['use_unit']} 併入配方！")
            st.rerun()

st.markdown("##### 📋 當前調配餐點的物料清單確認：")
if st.session_state.current_recipe_list:
    df_recipe_view = pd.DataFrame(st.session_state.current_recipe_list)
    df_recipe_view["移除"] = False
    
    edited_df = st.data_editor(
        df_recipe_view,
        column_config={
            "食材編號": st.column_config.TextColumn("食材編號", disabled=True),
            "食材名稱": st.column_config.TextColumn("食材名稱", disabled=True),
            "單位用量": st.column_config.NumberColumn("單位用量 (可雙擊修改)", min_value=0.0001, step=0.1, format="%.4f"),
            "單位": st.column_config.TextColumn("單位", disabled=True),
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
        
        original_qty = st.session_state.current_recipe_list[idx]["單位用量"]
        if row["單位用量"] != original_qty:
            has_changes = True
        
        new_recipe_list.append({
            "食材名稱": row["食材名稱"],
            "食材編號": row["食材編號"],
            "單位用量": float(row["單位用量"]),
            "單位": row["單位"]
        })
        
    if has_changes:
        st.session_state.current_recipe_list = new_recipe_list
        st.toast("✏️ 配方清單已即時更正！")
        st.rerun()

    dish_calculated_cost_single = 0.0
    for item in st.session_state.current_recipe_list:
        c_cost = all_raw_df[all_raw_df['prod_id'] == item['食材編號']]['cost'].values[0]
        dish_calculated_cost_single += item['單位用量'] * c_cost
        
    sale_qty = st.number_input("客人本次點購總數量 (份)", min_value=1, value=1)
    
    if final_dish_name != "":
        st.markdown(f"""
        > 💰 **餐點毛利核算面版：**
        > * 餐點名稱：**{final_dish_name}** | 單份預估食材成本：**${dish_calculated_cost_single:,.2f} 元**
        > * 本次總銷售額：**${dish_sale_price * sale_qty:,.0f} 元**
        """)
        
    if st.button("🔥 確認送出收銀結帳（執行扣料）"):
        if dish_sale_price <= 0:
            st.error("❌ 請輸入大於 0 的餐點販售價格！")
        else:
            conn = sqlite3.connect('inventory.db')
            cursor = conn.cursor()
            
            cursor.execute('''INSERT OR REPLACE INTO products (prod_id, prod_name, cost, price, safety_stock, purchase_unit, use_unit, conversion_factor)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', (final_dish_id, final_dish_name, dish_calculated_cost_single, dish_sale_price, 0, '份', '份', 1.0))
            
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
                cursor.execute("DELETE FROM bom WHERE parent_id = ?", (final_dish_id,))
                details_log = f"前台銷售「{final_dish_name} × {sale_qty} 份」，總金額 ${dish_sale_price * sale_qty}。"
                log_details_list = []
                
                for item in st.session_state.current_recipe_list:
                    total_need = item['單位用量'] * sale_qty
                    deduct_stock_fifo(item['食材編號'], total_need, cursor)
                    log_details_list.append(f"{item['食材名稱']}_{item['食材編號']}({total_need}{item['單位']})")
                    cursor.execute("INSERT INTO bom VALUES (?, ?, ?)", (final_dish_id, item['食材編號'], item['單位用量']))
                    
                conn.commit()
                conn.close()
                log_history(current_user, f"餐點收銀結帳-{final_dish_name}", details_log + " 消耗食材: " + ", ".join(log_details_list))
                st.success(f"🎉【收銀結帳已完成】餐點「{final_dish_name} × {sale_qty}」收銀完成！後台已完成 FIFO 食材扣料。")
                st.session_state.current_recipe_list = [] 
                st.rerun()
else:
    st.info("💡 請利用上方選單，開始為餐點添加原物料配方比例。")