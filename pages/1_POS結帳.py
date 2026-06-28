# pages/1_POS結帳.py
import streamlit as st
import pandas as pd
import re
import json
from datetime import datetime
from database.db_core import log_history, deduct_stock_fifo, get_next_dish_id, update_dish_and_bom, trigger_toast, show_pending_toast
from database.db_core import get_db_conn
# 從 db_core 載入所需的快取函式
from database.db_core import (
    cached_fetch_active_dishes,
    cached_fetch_active_materials,
    cached_fetch_today_orders,
    cached_fetch_dish_bom_recipe,
    cached_fetch_all_dishes_raw,
    cached_fetch_all_materials_raw
)

show_pending_toast()

st.subheader("🛒 收銀結帳與出餐管理系統")

use_mobile_view = st.toggle("📱 切換為手機/平板大按鈕專用排版", value=False, key="pos_mobile_toggle")

current_user = st.session_state.get('current_user', '老 闆')

if 'pos_shopping_cart' not in st.session_state:
    st.session_state.pos_shopping_cart = []

existing_dishes = cached_fetch_active_dishes()
all_raw_df = cached_fetch_active_materials()

def adjust_qty_callback(state_key, delta):
    current_val = st.session_state.get(state_key, 0)
    st.session_state[state_key] = max(0, current_val + delta)

def calculate_cart_estimated_cost(cart_items):
    if not cart_items:
        return 0.0, {}
        
    conn = get_db_conn()
    cursor = conn.cursor()
    
    total_mats_needed = {}
    for item in cart_items:
        d_id = item['prod_id']
        d_qty = item['qty']
        
        cursor.execute("SELECT child_id, qty_needed FROM bom WHERE parent_id = ?", (d_id,))
        bom_rows = cursor.fetchall()
        for child_id, qty_needed in bom_rows:
            total_mats_needed[child_id] = total_mats_needed.get(child_id, 0.0) + (qty_needed * d_qty)
            
    cart_total_cost = 0.0
    mats_status = {}
    
    for mat_id, qty_needed in total_mats_needed.items():
        cursor.execute("SELECT prod_name, use_unit FROM products WHERE prod_id = ?", (mat_id,))
        p_row = cursor.fetchone()
        mat_name = p_row[0] if p_row else mat_id
        mat_unit = p_row[1] if p_row else ""
        
        cursor.execute("SELECT qty, cost FROM stock_batches WHERE prod_id = ? AND qty > 0 ORDER BY expiry_date ASC, inbound_date ASC", (mat_id,))
        batches = cursor.fetchall()
        
        remains = qty_needed
        this_mat_cost = 0.0
        total_available = sum([b[0] for b in batches])
        
        if total_available < qty_needed:
            mats_status[mat_id] = {"name": mat_name, "sufficient": False, "shortage": qty_needed - total_available, "unit": mat_unit}
        else:
            mats_status[mat_id] = {"name": mat_name, "sufficient": True, "unit": mat_unit}
            
        for b_qty, b_cost in batches:
            if remains <= 0:
                break
            deduct_qty = min(remains, b_qty)
            this_mat_cost += deduct_qty * b_cost
            remains -= deduct_qty
            
        cart_total_cost += this_mat_cost
        
    cursor.close()
    conn.close()
    return cart_total_cost, mats_status

pos_tabs = st.tabs(["💰 前台收銀結帳", "✏️ 修改當日出餐數量", "✏️ 餐點細項修改", "❌ 品項下架與管理區"])

with pos_tabs[0]:
    st.markdown("##### 🔍 1. 品項點購區：")
    
    col_cart1, col_cart2, col_cart3 = st.columns([2, 1, 1])
    with col_cart1:
        dish_select_options = ["--- 請選擇餐點 ---"] + existing_dishes['prod_name'].tolist()
        selected_cart_dish = st.selectbox("請選取欲加入餐點的品項", dish_select_options, key="cart_dish_selector")
    with col_cart2:
        cart_dish_qty = st.number_input("點購數量 (份)", min_value=1, value=1, step=1, key="cart_qty_input")
    with col_cart3:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("➕ 加入", use_container_width=True):
            if selected_cart_dish == "--- 請選擇餐點 ---":
                st.error("請先選擇有效餐點品項！")
            else:
                matched_dish = existing_dishes[existing_dishes['prod_name'] == selected_cart_dish].iloc[0]
                existing_item_idx = next((i for i, item in enumerate(st.session_state.pos_shopping_cart) if item['prod_id'] == matched_dish['prod_id']), None)
                if existing_item_idx is not None:
                    st.session_state.pos_shopping_cart[existing_item_idx]['qty'] += cart_dish_qty
                else:
                    st.session_state.pos_shopping_cart.append({
                        "prod_id": matched_dish['prod_id'],
                        "prod_name": matched_dish['prod_name'],
                        "price": int(matched_dish['price']),
                        "qty": cart_dish_qty
                    })
                trigger_toast(f"已將 {matched_dish['prod_name']} x {cart_dish_qty} 份加入餐點！", icon="🛒")
                st.rerun()

    st.markdown("---")
    st.markdown("##### 📋 當前點餐單明細：")
    
    if st.session_state.pos_shopping_cart:
        total_bill_amount = 0
        
        if use_mobile_view:
            action_type = None
            target_idx = None
            
            for idx, item in enumerate(st.session_state.pos_shopping_cart):
                item_subtotal = item['price'] * item['qty']
                total_bill_amount += item_subtotal
                
                with st.container():
                    st.markdown(f"""
                    <div style="border: 1px solid #ddd; border-radius: 8px; padding: 10px; margin-bottom: 8px; background-color: #f9f9f9;">
                        <strong>【{item['prod_id']}】{item['prod_name']}</strong><br>
                        <span style="color:#666; font-size:14px;">單價: ${item['price']} | 小計: <strong style="color:#d9534f;">${item_subtotal}</strong></span>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    btn_col1, btn_col2, btn_col3 = st.columns([1, 1, 1.2])
                    with btn_col1:
                        if st.button("➖ 減少", key=f"cart_minus_{idx}", use_container_width=True):
                            action_type = "minus"
                            target_idx = idx
                    with btn_col2:
                        if st.button("➕ 增加", key=f"cart_plus_{idx}", use_container_width=True):
                            action_type = "plus"
                            target_idx = idx
                    with btn_col3:
                        if st.button("🗑️ 刪除品項", key=f"cart_del_{idx}", type="secondary", use_container_width=True):
                            action_type = "delete"
                            target_idx = idx
                    st.markdown("<div style='margin-bottom:15px;'></div>", unsafe_allow_html=True)
            
            if action_type is not None:
                if action_type == "plus":
                    st.session_state.pos_shopping_cart[target_idx]['qty'] += 1
                    trigger_toast("已增加數量 1 份！", icon="📝")
                elif action_type == "minus":
                    if st.session_state.pos_shopping_cart[target_idx]['qty'] > 1:
                        st.session_state.pos_shopping_cart[target_idx]['qty'] -= 1
                        trigger_toast("已減少數量 1 份！", icon="📝")
                    else:
                        st.session_state.pos_shopping_cart.pop(target_idx)
                        trigger_toast("已將商品移出點餐單！", icon="🗑️")
                elif action_type == "delete":
                    st.session_state.pos_shopping_cart.pop(target_idx)
                    trigger_toast("已將商品移出點餐單！", icon="🗑️")
                st.rerun()
                
        else:
            df_cart = pd.DataFrame(st.session_state.pos_shopping_cart)
            df_cart['小計'] = df_cart['price'] * df_cart['qty']
            df_cart['刪除'] = False
            
            edited_cart_df = st.data_editor(
                df_cart,
                column_config={
                    "prod_id": st.column_config.TextColumn("餐點編號", disabled=True),
                    "prod_name": st.column_config.TextColumn("餐點名稱", disabled=True),
                    "price": st.column_config.NumberColumn("單價", disabled=True),
                    "qty": st.column_config.NumberColumn("數量", min_value=1, step=1),
                    "小計": st.column_config.NumberColumn("金額", disabled=True),
                    "刪除": st.column_config.CheckboxColumn("勾選刪除", default=False)
                },
                use_container_width=True,
                hide_index=True,
                key="cart_data_editor"
            )
            
            cart_changed = False
            updated_cart = []
            
            for idx, row in edited_cart_df.iterrows():
                if row['刪除']:
                    cart_changed = True
                    continue
                if row['qty'] != st.session_state.pos_shopping_cart[idx]['qty']:
                    cart_changed = True
                
                updated_cart.append({
                    "prod_id": row['prod_id'],
                    "prod_name": row['prod_name'],
                    "price": int(row['price']),
                    "qty": int(row['qty'])
                })
                total_bill_amount += int(row['price']) * int(row['qty'])
                
            if cart_changed:
                st.session_state.pos_shopping_cart = updated_cart
                trigger_toast("點餐單數量已保留更新！", icon="📝")
                st.rerun()
                
        estimated_cart_cost, mats_check_dict = calculate_cart_estimated_cost(st.session_state.pos_shopping_cart)
        estimated_profit = float(total_bill_amount) - estimated_cart_cost
        estimated_margin = (estimated_profit / total_bill_amount * 100) if total_bill_amount > 0 else 0.0
        
        st.markdown(f"""
        > 💰 **本單即時面板：**
        > * 本單【**總銷售金額**】： **${total_bill_amount:,.0f} 元**
        > * 本單【**預估即時原物料成本**】： **${estimated_cart_cost:,.2f} 元**
        > * 本單【**預估純利潤**】： **${estimated_profit:,.2f} 元** ｜ 毛利率: **{estimated_margin:.1f}%**
        """)
        
        if st.button("🗑️ 重新點餐"):
            st.session_state.pos_shopping_cart = []
            if 'show_checkout_confirm' in st.session_state:
                st.session_state.show_checkout_confirm = False
            trigger_toast("已清空當前點餐單！", icon="🗑️")
            st.rerun()
            
        st.markdown("---")
        if st.button("🔥 確定完畢，出餐結帳", type="primary", use_container_width=True):
            st.session_state.show_checkout_confirm = True

        if 'show_checkout_confirm' in st.session_state and st.session_state.show_checkout_confirm:
            st.warning("🔔 **【出餐前通知】** 請再次核對下方餐點：")
            
            confirm_msg = ""
            for item in st.session_state.pos_shopping_cart:
                st.write(f"🔹 品項： **{item['prod_name']}** ｜ 數量： **{item['qty']} 份** ｜ 單價： ${item['price']} ｜ 小計： ${item['price']*item['qty']}")
                confirm_msg += f"【{item['prod_name']} x {item['qty']}份】"
            
            st.info(f"📊  此次出餐預計物料成本 **${estimated_cart_cost:,.2f} 元**。")
                
            col_conf1, col_conf2 = st.columns(2)
            with col_conf1:
                if st.button("✅ 出餐", type="primary", use_container_width=True):
                    all_mats_needed = {}
                    
                    conn_read = get_db_conn()
                    cursor_read = conn_read.cursor()
                    for cart_item in st.session_state.pos_shopping_cart:
                        d_id = cart_item['prod_id']
                        d_qty = cart_item['qty']
                        
                        cursor_read.execute("SELECT child_id, qty_needed FROM bom WHERE parent_id = ?", (d_id,))
                        db_bom_rows = cursor_read.fetchall()
                        for bom_row in db_bom_rows:
                            c_id = bom_row[0]
                            needed_units = float(bom_row[1]) * d_qty
                            all_mats_needed[c_id] = all_mats_needed.get(c_id, 0.0) + needed_units
                    conn_read.close()
                    
                    conn = get_db_conn()
                    cursor = conn.cursor()
                    
                    insufficient_flag = False
                    insufficient_msg = ""
                    disabled_item_detected = False
                    disabled_msg = ""
                            
                    for c_id, total_need in all_mats_needed.items():
                        cursor.execute("SELECT status, prod_name FROM products WHERE prod_id = ?", (c_id,))
                        status_row = cursor.fetchone()
                        if status_row and status_row[0] == 0:
                            disabled_item_detected = True
                            disabled_msg += f" ❌ 無法出餐：原物料【{status_row[1]}】目前處於下架停用狀態！\n"
                            
                        cursor.execute("SELECT SUM(qty) FROM stock_batches WHERE prod_id = ? AND qty > 0", (c_id,))
                        current_stock = cursor.fetchone()[0] or 0
                        if current_stock < total_need:
                            insufficient_flag = True
                            insufficient_msg += f" ❌ 庫存告急：物料【{status_row[1] if status_row else c_id}】批量點單共需要 {total_need:.1f}，目前全庫僅剩 {current_stock:.1f}！\n"
                            
                    if disabled_item_detected:
                        st.session_state.show_checkout_confirm = False  
                        st.error(disabled_msg)
                        cursor.close()
                        conn.close()
                        st.button("🔄 重新載入畫面以調整數量", on_click=st.rerun) 
                    elif insufficient_flag:
                        st.session_state.show_checkout_confirm = False  
                        st.error(insufficient_msg)
                        cursor.close()
                        conn.close()
                        st.button("🔄 重新載入畫面以調整數量", on_click=st.rerun) 
                    else:
                        try:
                            actual_total_cost = 0.0
                            log_mats_summary = []
                            mats_json_list = []
                            
                            for c_id, total_need in all_mats_needed.items():
                                cursor.execute("SELECT prod_name, use_unit FROM products WHERE prod_id = ?", (c_id,))
                                p_info = cursor.fetchone()
                                p_name = p_info[0] if p_info else c_id
                                p_unit = p_info[1] if p_info else ""
                                
                                success, deducted_cost_val, batch_list = deduct_stock_fifo(c_id, total_need, cursor)
                                if not success:
                                    raise Exception(f"物料庫存即時 FIFO 扣減失敗：【{p_name}】需求量 {total_need:.1f}")
                                    
                                actual_total_cost += deducted_cost_val
                                log_mats_summary.append(f"{p_name}_{c_id}({total_need:.1f}{p_unit})")
                                mats_json_list.append({
                                    "mat_id": c_id,
                                    "mat_name": p_name,
                                    "qty": total_need,
                                    "unit": p_unit,
                                    "deducted_batches": batch_list
                                })
                                
                            now_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            details_log = f"合併前台收銀：出餐明細 {confirm_msg}，總金額 ${total_bill_amount} 元。"
                            
                            structured_payload = {
                                "dishes": st.session_state.pos_shopping_cart,
                                "materials": mats_json_list,
                                "total_revenue": total_bill_amount,
                                "total_cost": actual_total_cost
                            }
                            final_log_entry = details_log + " ||STRUCT_DATA||" + json.dumps(structured_payload, ensure_ascii=False)
                            
                            hist_id = log_history(current_user, "多品項收銀結帳", final_log_entry, shared_cursor=cursor)
                            
                            cursor.execute('''INSERT INTO orders (timestamp, user, total_revenue, total_cost, status, history_id)
                                              VALUES (?, ?, ?, ?, 1, ?)''', 
                                           (now_time_str, current_user, float(total_bill_amount), float(actual_total_cost), hist_id))
                            new_order_id = cursor.lastrowid
                            
                            for d_item in st.session_state.pos_shopping_cart:
                                cursor.execute('''INSERT INTO order_items (order_id, prod_id, prod_name, qty, price)
                                                  VALUES (?, ?, ?, ?, ?)''',
                                               (new_order_id, d_item["prod_id"], d_item["prod_name"], float(d_item["qty"]), float(d_item["price"])))
                                               
                            for m_item in mats_json_list:
                                cursor.execute('''INSERT INTO order_materials (order_id, mat_id, mat_name, qty, unit, deducted_batches_json)
                                                  VALUES (?, ?, ?, ?, ?, ?)''',
                                               (new_order_id, m_item["mat_id"], m_item["mat_name"], float(m_item["qty"]), m_item["unit"], json.dumps(m_item["deducted_batches"], ensure_ascii=False)))
                            
                            conn.commit()
                            cursor.close()
                            conn.close()
                            
                            # 收銀結帳：只會影響今日營收紀錄，無須清空主選單或其他無關快取
                            cached_fetch_today_orders.clear()
                            
                            trigger_toast(f"🎉 批量出餐結帳成功！總金額：${total_bill_amount}，實際成本：${actual_total_cost:.2f}", icon="🎉")
                            st.session_state.pos_shopping_cart = []
                            st.session_state.show_checkout_confirm = False
                            st.rerun()
                        except Exception as e:
                            conn.rollback()
                            cursor.close()
                            conn.close()
                            st.error(f"🚨 會計核心異常：交易已安全回滾。原因：{e}")
            with col_conf2:
                if st.button("❌ 修改餐點", use_container_width=True):
                    st.session_state.show_checkout_confirm = False
                    st.rerun()
    else:
        st.info("💡 目前點餐購物車為空，請從上方選取餐點並加入點餐單.")

with pos_tabs[1]:
    st.markdown("##### 📝 当日出餐纪录面版")
    
    today_start = datetime.now().strftime("%Y-%m-%d 00:00:00")
    today_end = datetime.now().strftime("%Y-%m-%d 23:59:59")

    df_today_orders = cached_fetch_today_orders(today_start, today_end)

    if df_today_orders.empty:
        st.info("💡 今天目前尚無收銀出餐紀錄可供修改。")
    else:
        order_options = []
        parsed_orders_cache = {}
        
        for idx, row in df_today_orders.iterrows():
            raw_text = row['details']
            hist_id = row['id']
            orig_time = row['timestamp'] 
            
            if "||STRUCT_DATA||" in raw_text:
                parts = raw_text.split("||STRUCT_DATA||")
                display_part = parts[0]
                json_part = parts[1]
                try:
                    payload = json.loads(json_part)
                    parsed_orders_cache[hist_id] = {
                        "dishes": payload["dishes"],
                        "materials": payload["materials"],
                        "total_revenue": float(payload["total_revenue"]),
                        "total_cost": float(payload["total_cost"]),
                        "is_structured": True,
                        "orig_timestamp": orig_time
                    }
                except:
                    parsed_orders_cache[hist_id] = {"is_structured": False, "orig_timestamp": orig_time}
            else:
                parsed_orders_cache[hist_id] = {"is_structured": False, "orig_timestamp": orig_time}
                
            brief_match = re.search(r"出餐明細 (.+?)，(?:新)?總金額", raw_text)
            brief = brief_match.group(1) if brief_match else "明細解析失敗"
            order_options.append(f"單號 {hist_id} | 時間: {row['timestamp'].split(' ')[1]} | 明細: {brief}")

        selected_order_str = st.selectbox("🎯 請選擇出單紀錄：", order_options, key="void_order_select_box")
        target_hist_id = int(selected_order_str.split("單號 ")[1].split(" |")[0])
        matched_order_row = df_today_orders[df_today_orders['id'] == target_hist_id].iloc[0]
        order_details_text = matched_order_row['details']

        st.info(f"📋 **訂單完整資訊：**\n{order_details_text.split('||STRUCT_DATA||')[0]}")

        order_data = parsed_orders_cache[target_hist_id]
        orig_order_timestamp = order_data["orig_timestamp"] 

        if order_data["is_structured"]:
            parsed_dishes = [(d["prod_name"], d["qty"], d["prod_id"]) for d in order_data["dishes"]]
            parsed_total_revenue = order_data["total_revenue"]
            parsed_total_cost = order_data["total_cost"]
            parsed_mats = order_data["materials"]
        else:
            raw_dishes = re.findall(r"【(.+?) x (\d+)份】", order_details_text)
            parsed_dishes = []
            conn_temp = get_db_conn()
            cursor_temp = conn_temp.cursor()
            for name, qty in raw_dishes:
                cursor_temp.execute("SELECT prod_id FROM products WHERE prod_name = ?", (name,))
                pid_row = cursor_temp.fetchone()
                pid = pid_row[0] if pid_row else ""
                parsed_dishes.append((name, int(qty), pid))
            cursor_temp.close()
            conn_temp.close()
            
            parsed_total_revenue = float(re.search(r"總金額 \$(\d+)", order_details_text).group(1))
            parsed_total_cost = float(re.search(r"精準食材成本 \$([\d\.]+)", order_details_text).group(1))
            raw_mats = re.findall(r"([^\s_,\(]+)_([RS]\d+)\(([\d\.]+)([^\)]+)\)", order_details_text)
            parsed_mats = [{"mat_name": m[0], "mat_id": m[1], "qty": float(m[2]), "unit": m[3], "deducted_batches": []} for m in raw_mats]

        manage_action = st.radio("選擇類型：", ["❌ 整單作廢（全數退款並回補庫存）", "✏️ 更正點餐數量"], horizontal=True)

        if "整單作廢" in manage_action:
            st.warning("⚠️ **注意：** 將依據當時結帳消耗的原始批次 並完整歸還至庫存中。")
            if st.button("🔥 整單作廢", type="primary", use_container_width=True):
                conn = get_db_conn()
                cursor = conn.cursor()
                try:
                    for mat in parsed_mats:
                        mat_id = mat["mat_id"]
                        refund_qty = float(mat["qty"])
                        batches_info = mat.get("deducted_batches", [])
                        
                        if batches_info:
                            for b_info in batches_info:
                                cursor.execute(
                                    "UPDATE stock_batches SET qty = qty + ? WHERE batch_id = ?", 
                                    (float(b_info["qty"]), b_info["batch_id"])
                                )
                        else:
                            cursor.execute("UPDATE stock_batches SET qty = qty + ? WHERE batch_id = (SELECT batch_id FROM stock_batches WHERE prod_id = ? ORDER BY inbound_date DESC, batch_id DESC LIMIT 1)", (refund_qty, mat_id))
                    
                    cursor.execute("UPDATE orders SET status = 0 WHERE history_id = ?", (target_hist_id,))
                    
                    conn.commit()
                    cursor.close()
                    conn.close()
                    
                    # 作廢訂單：更新今日訂單數據
                    cached_fetch_today_orders.clear()
                    
                    orig_brief = order_details_text.split("||STRUCT_DATA||")[0]
                    log_history(current_user, "訂單作廢成功", f"操作人員執行整單作廢。被作廢單號: {target_hist_id} ｜ 原始交易時間: {orig_order_timestamp} ｜ 退回營業額: ${parsed_total_revenue} 元 ｜ 庫存原物料已完整回補。 原始單據內容為: [{orig_brief}]")
                    
                    trigger_toast(f"已成功作廢單號 {target_hist_id} 的點餐紀錄，庫存已同步回補！", icon="🗑️")
                    st.rerun()
                except Exception as e:
                    conn.rollback()
                    cursor.close()
                    conn.close()
                    st.error(f"執行作廢失敗：{e}")

        elif "更正點餐數量" in manage_action:
            st.markdown("----")
            st.markdown("##### ➕ 餐點品項：")
            
            add_pool_key = f"order_add_pool_{target_hist_id}"
            if add_pool_key not in st.session_state:
                st.session_state[add_pool_key] = []
                
            col_add_order1, col_add_order2 = st.columns([3, 1])
            with col_add_order1:
                existing_names = [d[0] for d in parsed_dishes]
                available_dishes = existing_dishes[~existing_dishes['prod_name'].isin(existing_names)]
                
                dish_append_options = ["--- 請選取欲補加的餐點 ---"] + available_dishes['prod_name'].tolist()
                selected_append_dish = st.selectbox("選取菜單上要補加的品項", dish_append_options, key=f"append_dish_select_{target_hist_id}")
            with col_add_order2:
                st.markdown("<br>", unsafe_allow_html=True)
                if st.button("➕ 加進清單", use_container_width=True, key=f"append_dish_btn_{target_hist_id}"):
                    if selected_append_dish == "--- 請選取欲補加的餐點 ---":
                        st.error("請先選擇要補加的餐點品項！")
                    else:
                        matched_append = existing_dishes[existing_dishes['prod_name'] == selected_append_dish].iloc[0]
                        if not any(x[0] == matched_append['prod_name'] for x in st.session_state[add_pool_key]):
                            st.session_state[add_pool_key].append((matched_append['prod_name'], 1, matched_append['prod_id']))
                            trigger_toast(f"已將漏點的 【{matched_append['prod_name']}】 補配至修改畫面上！", icon="➕")
                            st.rerun()
                            
            for app_item in st.session_state[add_pool_key]:
                if not any(x[0] == app_item[0] for x in parsed_dishes):
                    parsed_dishes.append(app_item)

            st.markdown("###### 📝 請在下方輸入該單「正確」的餐點數量：")
            new_dish_qtys = {}
            has_qty_changed = False

            for d_name, d_qty, d_id in parsed_dishes:
                is_new_appended = any(x[0] == d_name for x in st.session_state.get(add_pool_key, []))
                label_txt = f"【{d_name}】之正確出餐份數 (原單無此餐點)" if is_new_appended else f"【{d_name}】之正確出餐份數 (原為 {d_qty} 份)"
                
                session_qty_key = f"edit_qty_{d_name}_{target_hist_id}"
                if session_qty_key not in st.session_state:
                    st.session_state[session_qty_key] = int(d_qty)
                
                if use_mobile_view:
                    st.markdown(f"**{label_txt}**")
                    col_q1, col_q2, col_q3 = st.columns([2, 1, 1])
                    
                    with col_q1:
                        st.number_input(label_txt, min_value=0, step=1, key=session_qty_key, label_visibility="collapsed")
                    with col_q2:
                        st.button("➖ 1", key=f"btn_minus1_{d_name}", use_container_width=True, on_click=adjust_qty_callback, args=(session_qty_key, -1))
                    with col_q3:
                        st.button("➕ 1", key=f"btn_plus1_{d_name}", use_container_width=True, on_click=adjust_qty_callback, args=(session_qty_key, 1))
                    st.markdown("<div style='margin-bottom:8px;'></div>", unsafe_allow_html=True)
                else:
                    st.number_input(label_txt, min_value=0, step=1, key=session_qty_key)
                
                new_q = st.session_state[session_qty_key]
                new_dish_qtys[d_name] = new_q
                if new_q != int(d_qty):
                    has_qty_changed = True

            if st.button("💾 儲存出餐數量變更", type="primary", use_container_width=True, key=f"save_qty_edit_btn_{target_hist_id}"):
                if not has_qty_changed:
                    st.info("數量沒有變動，無需修正。")
                else:
                    conn = get_db_conn()
                    cursor = conn.cursor()
                    try:
                        for mat in parsed_mats:
                            if mat.get("deducted_batches", []):
                                for b_info in mat["deducted_batches"]:
                                    cursor.execute("UPDATE stock_batches SET qty = qty + ? WHERE batch_id = ?", (float(b_info["qty"]), b_info["batch_id"]))
                            else:
                                cursor.execute("UPDATE stock_batches SET qty = qty + ? WHERE batch_id = (SELECT batch_id FROM stock_batches WHERE prod_id = ? ORDER BY inbound_date DESC, batch_id DESC LIMIT 1)", (float(mat["qty"]), mat["mat_id"]))
                        
                        new_total_bill = 0.0
                        total_mats_needed_new = {} 
                        new_confirm_msg = ""
                        new_cart_payload = []

                        for d_name, _, d_id in parsed_dishes:
                            new_qty_val = new_dish_qtys[d_name]
                            cursor.execute("SELECT prod_id, price FROM products WHERE prod_name = ?", (d_name,))
                            p_row = cursor.fetchone()
                            if p_row:
                                real_id, price = p_row[0], float(p_row[1])
                                new_total_bill += price * new_qty_val
                                if new_qty_val > 0:
                                    new_confirm_msg += f"【{d_name} x {new_qty_val}份】"
                                    new_cart_payload.append({"prod_id": real_id, "prod_name": d_name, "price": int(price), "qty": new_qty_val})
                                
                                cursor.execute("SELECT child_id, qty_needed FROM bom WHERE parent_id = ?", (real_id,))
                                bom_rows = cursor.fetchall()
                                for child_id, qty_needed in bom_rows:
                                    total_mats_needed_new[child_id] = total_mats_needed_new.get(child_id, 0.0) + (qty_needed * new_qty_val)

                        final_new_cost = 0.0
                        insufficient_flag = False
                        insufficient_msg = ""
                        new_mats_payload = []
                        log_mats_summary = []

                        for m_id, total_need in total_mats_needed_new.items():
                            cursor.execute("SELECT prod_name, use_unit FROM products WHERE prod_id = ?", (m_id,))
                            m_info = cursor.fetchone()
                            m_name, m_unit = m_info[0], m_info[1]

                            cursor.execute("SELECT SUM(qty) FROM stock_batches WHERE prod_id = ? AND qty > 0", (m_id,))
                            avail = cursor.fetchone()[0] or 0.0
                            if avail < total_need:
                                insufficient_flag = True
                                insufficient_msg += f"❌ 儲存失敗：微調後共需要物料【{m_name}】{total_need:.1f}，回補後全庫僅剩 {avail:.1f}！\n"
                            else:
                                success, deducted_cost_val, batch_list = deduct_stock_fifo(m_id, total_need, cursor)
                                if not success:
                                    raise Exception(f"微調重新計算 FIFO 扣減失敗：【{m_name}】")
                                final_new_cost += deducted_cost_val
                                log_mats_summary.append(f"{m_name}_{m_id}({total_need:.1f}{m_unit})")
                                new_mats_payload.append({"mat_id": m_id, "mat_name": m_name, "qty": total_need, "unit": m_unit, "deducted_batches": batch_list})

                        if insufficient_flag:
                            st.error(insufficient_msg)
                            conn.rollback()
                            cursor.close()
                            conn.close()
                        else:
                            for m_id in total_mats_needed_new.keys():
                                matched_hist_mat = next((m for m in parsed_mats if m["mat_id"] == m_id), None)
                                hist_cost_fallback = 0.0
                                if matched_hist_mat and "deducted_batches" in matched_hist_mat and matched_hist_mat["deducted_batches"]:
                                    hist_cost_fallback = float(matched_hist_mat["deducted_batches"][0].get("cost", 0.0))

                                cursor.execute('''
                                    SELECT 
                                      CASE 
                                        WHEN COALESCE(SUM(CASE WHEN qty > 0 THEN qty ELSE 0 END), 0) > 0 
                                        THEN (SUM(CASE WHEN qty > 0 THEN qty * cost ELSE 0 END) / SUM(CASE WHEN qty > 0 THEN qty ELSE 0 END))
                                        ELSE ?
                                      END as moving_avg
                                    FROM stock_batches WHERE prod_id = ?
                                ''', (hist_cost_fallback, m_id))
                                calculated_avg_cost = cursor.fetchone()[0] or 0.0
                                cursor.execute("UPDATE products SET cost = ? WHERE prod_id = ?", (float(calculated_avg_cost), m_id))

                            cursor.execute("UPDATE history SET action = '多品項收銀結帳-已微調更正' WHERE id = ?", (target_hist_id,))
                            cursor.execute("UPDATE orders SET status = 2 WHERE history_id = ?", (target_hist_id,))

                            details_text_part = f"數量更正紀錄（對應原單號 {target_hist_id}）：出餐明細 {new_confirm_msg}，新總金額 ${new_total_bill:.0f} 元。"
                            new_payload_struct = {
                                "dishes": new_cart_payload, 
                                "materials": new_mats_payload, 
                                "total_revenue": new_total_bill, 
                                "total_cost": final_new_cost,
                                "orig_timestamp": orig_order_timestamp,
                                "is_correction": True,
                                "referenced_id": target_hist_id
                            }
                            updated_full_log = details_text_part + " ||STRUCT_DATA||" + json.dumps(new_payload_struct, ensure_ascii=False)
                            
                            new_hist_id = log_history(current_user, "更正點餐數量", updated_full_log, shared_cursor=cursor)
                            
                            cursor.execute('''INSERT INTO orders (timestamp, user, total_revenue, total_cost, status, history_id)
                                              VALUES (?, ?, ?, ?, 1, ?)''', 
                                           (orig_order_timestamp, current_user, float(new_total_bill), float(final_new_cost), new_hist_id))
                            
                            conn.commit()
                            cursor.close()
                            conn.close()
                            
                            # 更正點餐數量：更新營收紀錄快取
                            cached_fetch_today_orders.clear()
                            
                            if add_pool_key in st.session_state:
                                del st.session_state[add_pool_key]
                                
                            trigger_toast(f"🎉 單號 {target_hist_id} 的數量更正單已獨立成立（包含補加漏點餐點），庫存與成本完美同步！", icon="✏️")
                            st.rerun()
                    except Exception as e:
                        conn.rollback()
                        cursor.close()
                        conn.close()
                        st.error(f"更新數量時發生錯誤，資料庫已安全復原：{e}")

with pos_tabs[2]:
    st.markdown("##### 🆕 1. 新餐點創立區")
    
    creation_mode = st.radio("🛠️ 請選擇餐點建立模式：", ["A模式：單份餐點", "B模式：整鍋"], horizontal=True)

    # 包含所有需要做重量換算的定義單位列表
    WEIGHT_UNITS = ['kg', '公斤', 'g', '公克', '臺斤', '台斤', '斤', 'Kg', 'KG']

    if creation_mode == "A模式：單份餐點":
        with st.expander("🛠️ 展開配方調配面板", expanded=True):
            col_new_dish1, col_new_dish2 = st.columns(2)
            with col_new_dish1:
                pos_custom_name = st.text_input("輸入餐點名稱", value="", key="custom_dish_name_input").strip()
            with col_new_dish2:
                pos_custom_price = st.number_input("設定販售價格", min_value=0, value=0, step=1, key="custom_dish_price_input")
                
            st.markdown("###### ➕ 食材用量：")
            
            mat_filter_a = st.radio("🔍 篩選原物料類別", ["顯示全部", "僅看食材 (R)", "僅看用品 (S)"], horizontal=True, key="mat_filter_a")
            if mat_filter_a == "僅看食材 (R)":
                filtered_df_a = all_raw_df[all_raw_df['prod_id'].str.startswith('R')]
            elif mat_filter_a == "僅看用品 (S)":
                filtered_df_a = all_raw_df[all_raw_df['prod_id'].str.startswith('S')]
            else:
                filtered_df_a = all_raw_df

            col_cus_mat1, col_cus_mat2, col_cus_convert, col_cus_mat3 = st.columns([2, 1, 1.5, 1])
            with col_cus_mat1:
                dish_select_list = ["--- 請選擇食材 ---"] + filtered_df_a['prod_name'].tolist()
                cus_mat_name = st.selectbox("選擇要加入的食材/用品名稱", dish_select_list, key="cus_mat_selector")
            
            db_unit_a = ""
            if cus_mat_name != "--- 請選擇食材 ---":
                matched_row_a = filtered_df_a[filtered_df_a['prod_name'] == cus_mat_name].iloc[0]
                db_unit_a = matched_row_a['use_unit'].strip()
                
            with col_cus_mat2:
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown(f"**進貨定義單位：** `{db_unit_a if db_unit_a else '未選擇'}`")
                
            cus_conversion_mode = "不換算"
            if db_unit_a:
                unit_lower = db_unit_a.lower()
                with col_cus_convert:
                    if unit_lower in ['kg', '公斤', 'g', '公克']:
                        cus_conversion_mode = st.selectbox(
                            "輸入數值的單位類型",
                            ["直接依進貨定義單位輸入", "公斤轉公克"],
                            key="cus_conversion_mode_select_weight_kg"
                        )
                    elif unit_lower in ['臺斤', '台斤', '斤']:
                        cus_conversion_mode = st.selectbox(
                            "輸入數值的單位類型",
                            ["直接依進貨定義單位輸入", "台斤轉公克"],
                            key="cus_conversion_mode_select_weight_tw"
                        )
                    else:
                        st.text_input("單位換算", value="無需換算", disabled=True, key="cus_conversion_disabled")
            else:
                with col_cus_convert:
                    st.text_input("單位換算", value="無需換算", disabled=True, key="cus_conversion_disabled")
                
            with col_cus_mat3:
                custom_max_val = 100000.0  
                cus_mat_qty = st.number_input("輸入數量", min_value=0.0, max_value=custom_max_val, value=0.0, step=1.0, key="cus_qty_selector")
                
            if 'custom_recipe_pool' not in st.session_state:
                st.session_state.custom_recipe_pool = []
                
            if st.button("➕ 將此食材加入清單", key="add_cus_recipe_btn"):
                if cus_mat_name == "--- 請選擇食材 ---" or cus_mat_qty <= 0:
                    st.error("請選擇有效原物料並輸入大於 0 的用量！")
                else:
                    mat_info = filtered_df_a[filtered_df_a['prod_name'] == cus_mat_name].iloc[0]
                    
                    final_conv = cus_mat_qty 
                    if cus_conversion_mode == "公斤轉公克":
                        final_conv = cus_mat_qty / 1000.0
                    elif cus_conversion_mode == "台斤轉公克":
                        final_conv = cus_mat_qty / 600.0
                    
                    ex_idx = next((i for i, item in enumerate(st.session_state.custom_recipe_pool) if item['食材編號'] == mat_info['prod_id']), None)
                    new_pool_dict = {"食材名稱": mat_info['prod_name'], "食材編號": mat_info['prod_id'], "單位用量": final_conv, "單位": mat_info['use_unit']}
                    if ex_idx is not None:
                        st.session_state.custom_recipe_pool[ex_idx] = new_pool_dict
                    else:
                        st.session_state.custom_recipe_pool.append(new_pool_dict)
                    st.rerun()
                    
            if st.session_state.custom_recipe_pool:
                df_pool = pd.DataFrame(st.session_state.custom_recipe_pool)
                df_pool['移除'] = False
                edited_pool = st.data_editor(
                    df_pool,
                    column_config={"食材編號": st.column_config.TextColumn("編號", disabled=True), "食材名稱": st.column_config.TextColumn("名稱", disabled=True), "單位用量": st.column_config.NumberColumn("最終換算後用量", format="%.4f"), "移除": st.column_config.CheckboxColumn("移除")},
                    disabled=["食材編號", "食材名稱", "單位"],
                    key="pool_editor",
                    use_container_width=True
                )
                
                pool_changed = False
                new_pool = []
                for idx, r in edited_pool.iterrows():
                    if r['移除']:
                        pool_changed = True
                        continue
                    if r['單位用量'] != st.session_state.custom_recipe_pool[idx]['單位用量']:
                        pool_changed = True
                    new_pool.append({"食材名稱": r['食材名稱'], "食材編號": r['食材編號'], "單位用量": float(r['單位用量']), "單位": r['單位']})
                if pool_changed:
                    st.session_state.custom_recipe_pool = new_pool
                    st.rerun()
                
                custom_custom_dish_calc_cost = 0.0
                for p_item in st.session_state.custom_recipe_pool:
                    matched_raw = all_raw_df[all_raw_df['prod_id'] == p_item['食材編號']]
                    r_cost = float(matched_raw.iloc[0]['cost']) if not matched_raw.empty else 0.0
                    custom_custom_dish_calc_cost += p_item['單位用量'] * r_cost
                    
                custom_profit = float(pos_custom_price) - custom_custom_dish_calc_cost
                custom_margin = (custom_profit / pos_custom_price * 100) if pos_custom_price > 0 else 0.0
                
                st.markdown(f"""
                > 💡 **新餐點售價與配方成本動態預估試算：**
                > * 餐點售價： **{pos_custom_price} 元**
                > * 依目前庫存推算【**單份標準原物料成本**】： **${custom_custom_dish_calc_cost:,.2f} 元**
                > * 預估【**單份毛利**】： **{custom_profit:,.2f} 元** ｜ 預估毛利率: **{custom_margin:.1f}%**
                """)
                    
                if st.button("💾 寫入正式菜單", type="primary"):
                    if not pos_custom_name:
                        st.error("❌ 錯誤：請輸入餐點名稱！")
                    elif pos_custom_price <= 0:
                        st.error("❌ 錯誤：販售價格必須為大於 0 的整數！")
                    elif not st.session_state.custom_recipe_pool:
                        st.error("❌ 錯誤變更：餐點必須至少包含一項原物料配方")
                    else:
                        conn = get_db_conn()
                        cursor = conn.cursor()
                        cursor.execute("SELECT prod_id FROM products WHERE prod_name = ? AND status = 1", (pos_custom_name,))
                        if cursor.fetchone():
                            st.error(f"❌ 錯誤：【{pos_custom_name}】已存在於正式菜單中，請直接至下方區塊修正參數")
                            cursor.close()
                            conn.close()
                        else:
                            new_d_id = get_next_dish_id()
                            cursor.execute("INSERT INTO products VALUES (?, ?, ?, ?, 0.0, '份', '份', 1.0, 1)", (new_d_id, pos_custom_name, custom_custom_dish_calc_cost, float(pos_custom_price)))
                            for item in st.session_state.custom_recipe_pool:
                                cursor.execute("INSERT INTO bom VALUES (?, ?, ?)", (new_d_id, item['食材編號'], item['單位用量']))
                            conn.commit()
                            cursor.close()
                            conn.close()
                            
                            # A模式新創自訂餐點：精準清空餐點菜單相關快取
                            cached_fetch_active_dishes.clear()
                            cached_fetch_all_dishes_raw.clear()
                            
                            log_history(
                                current_user, 
                                f"修正餐點參數-新創自訂餐點-{pos_custom_name}", 
                                f"創立了全新的新菜色：{pos_custom_name}({new_d_id})，定價 ${pos_custom_price}，設定基本單位配方成本 ${custom_custom_dish_calc_cost:.2f}。"
                            )
                            trigger_toast(f"成功建立餐點 【{pos_custom_name}】 並加入菜單選單！", icon="🚀")
                            st.session_state.custom_recipe_pool = []
                            st.rerun()

    elif creation_mode == "B模式：整鍋":
        with st.expander("🛠️ 展開配方調配面板", expanded=True):
            col_b_name, col_b_price1, col_b_price2 = st.columns([2, 1, 1])
            with col_b_name:
                pot_base_name = st.text_input("輸入餐點名稱", value="", key="pot_base_name_input").strip()
            with col_b_price1:
                pot_large_price = st.number_input("設定【大碗】販售價格", min_value=0, value=0, step=1, key="pot_large_price_input")
            with col_b_price2:
                pot_small_price = st.number_input("設定【小碗】販售價格", min_value=0, value=0, step=1, key="pot_small_price_input")

            st.markdown("###### 📊 填寫整鍋預計可銷售碗數：")
            col_split1, col_split2 = st.columns(2)
            with col_split1:
                pot_large_servings = st.number_input("整鍋可做成【大碗】的總碗數", min_value=0.0, value=0.0, step=1.0, key="pot_large_servings")
            with col_split2:
                pot_small_servings = st.number_input("整鍋可做成【小碗】的總碗數", min_value=0.0, value=0.0, step=1.0, key="pot_small_servings")

            st.markdown("###### ➕ 食材用量：")
            
            mat_filter_b = st.radio("🔍 篩選原物料類別", ["顯示全部", "僅看食材 (R)", "僅看用品 (S)"], horizontal=True, key="mat_filter_b")
            if mat_filter_b == "僅看食材 (R)":
                filtered_df_b = all_raw_df[all_raw_df['prod_id'].str.startswith('R')]
            elif mat_filter_b == "僅看用品 (S)":
                filtered_df_b = all_raw_df[all_raw_df['prod_id'].str.startswith('S')]
            else:
                filtered_df_b = all_raw_df

            col_b_mat1, col_b_mat2, col_b_convert, col_b_mat3 = st.columns([2, 1, 1.5, 1])
            with col_b_mat1:
                b_dish_select_list = ["--- 請選擇食材 ---"] + filtered_df_b['prod_name'].tolist()
                b_mat_name = st.selectbox("選擇此餐點的食材項目", b_dish_select_list, key="b_mat_selector")
            
            db_unit_b = ""
            if b_mat_name != "--- 請選擇食材 ---":
                matched_row_b = filtered_df_b[filtered_df_b['prod_name'] == b_mat_name].iloc[0]
                db_unit_b = matched_row_b['use_unit'].strip()

            with col_b_mat2:
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown(f"**進貨定義單位：** `{db_unit_b if db_unit_b else '未選擇'}`")
                
            b_conversion_mode = "不換算"
            if db_unit_b:
                unit_lower_b = db_unit_b.lower()
                with col_b_convert:
                    if unit_lower_b in ['kg', '公斤', 'g', '公克']:
                        b_conversion_mode = st.selectbox(
                            "輸入數值的單位類型",
                            ["直接依進貨定義單位輸入", "公斤轉公克"],
                            key="b_conversion_mode_select_weight_kg"
                        )
                    elif unit_lower_b in ['臺斤', '台斤', '斤']:
                        b_conversion_mode = st.selectbox(
                            "輸入數值的單位類型",
                            ["直接依進貨定義單位輸入", "台斤轉公克"],
                            key="b_conversion_mode_select_weight_tw"
                        )
                    else:
                        st.text_input("單位換算", value="無需換算", disabled=True, key="b_conversion_disabled")
            else:
                with col_b_convert:
                    st.text_input("單位換算", value="無需換算", disabled=True, key="b_conversion_disabled")
                
            with col_b_mat3:
                b_mat_qty = st.number_input("輸入數量", min_value=0.0, value=0.0, step=1.0, key="b_qty_selector")

            if 'pot_recipe_pool' not in st.session_state:
                st.session_state.pot_recipe_pool = []

            if st.button("➕ 將此食材加入清單", key="add_pot_recipe_btn"):
                if b_mat_name == "--- 請選擇食材 ---" or b_mat_qty <= 0:
                    st.error("請選擇有效原物料並輸入大於 0 的投入量！")
                else:
                    mat_info = filtered_df_b[filtered_df_b['prod_name'] == b_mat_name].iloc[0]
                    
                    final_conv = b_mat_qty
                    if b_conversion_mode == "公斤轉公克":
                        final_conv = b_mat_qty / 1000.0
                    elif b_conversion_mode == "台斤轉公克":
                        final_conv = b_mat_qty / 600.0

                    ex_idx = next((i for i, item in enumerate(st.session_state.pot_recipe_pool) if item['食材編號'] == mat_info['prod_id']), None)
                    new_pool_dict = {"食材名稱": mat_info['prod_name'], "食材編號": mat_info['prod_id'], "單位用量": final_conv, "單位": mat_info['use_unit']}
                    if ex_idx is not None:
                        st.session_state.pot_recipe_pool[ex_idx] = new_pool_dict
                    else:
                        st.session_state.pot_recipe_pool.append(new_pool_dict)
                    st.rerun()

            if st.session_state.pot_recipe_pool:
                df_pot_pool = pd.DataFrame(st.session_state.pot_recipe_pool)
                df_pot_pool['移除'] = False
                edited_pot_pool = st.data_editor(
                    df_pot_pool,
                    column_config={"食材編號": st.column_config.TextColumn("編號", disabled=True), "食材名稱": st.column_config.TextColumn("名稱", disabled=True), "單位用量": st.column_config.NumberColumn("最終換算後總用量", format="%.4f"), "移除": st.column_config.CheckboxColumn("移除")},
                    disabled=["食材編號", "食材名稱", "單位"],
                    key="pot_pool_editor",
                    use_container_width=True
                )
                
                pot_pool_changed = False
                new_pot_pool = []
                for idx, r in edited_pot_pool.iterrows():
                    if r['移除']:
                        pot_pool_changed = True
                        continue
                    if r['單位用量'] != st.session_state.pot_recipe_pool[idx]['單位用量']:
                        pot_pool_changed = True
                    new_pot_pool.append({"食材名稱": r['食材名稱'], "食材編號": r['食材編號'], "單位用量": float(r['單位用量']), "單位": r['單位']})
                if pot_pool_changed:
                    st.session_state.pot_recipe_pool = new_pot_pool
                    st.rerun()

                single_large_cost = 0.0
                single_small_cost = 0.0
                
                for p_item in st.session_state.pot_recipe_pool:
                    matched_raw = all_raw_df[all_raw_df['prod_id'] == p_item['食材編號']]
                    r_cost = float(matched_raw.iloc[0]['cost']) if not matched_raw.empty else 0.0
                    total_item_cost = p_item['單位用量'] * r_cost
                    
                    if pot_large_servings > 0:
                        single_large_cost += total_item_cost / pot_large_servings
                    if pot_small_servings > 0:
                        single_small_cost += total_item_cost / pot_small_servings

                total_pot_cost = 0.0
                for p_item in st.session_state.pot_recipe_pool:
                    matched_raw = all_raw_df[all_raw_df['prod_id'] == p_item['食材編號']]
                    r_cost = float(matched_raw.iloc[0]['cost']) if not matched_raw.empty else 0.0
                    total_pot_cost += p_item['單位用量'] * r_cost

                st.markdown(f"""
                > 📊 **🥣 整鍋成本拆分攤算即時面板：**
                > * 投入這整鍋的【**原物料總成本**】： **${total_pot_cost:,.2f} 元**
                > * 拆分估算：**【單碗大碗成本】**： **${single_large_cost:,.2f} 元** (售價:${pot_large_price}，預估毛利率:{((pot_large_price - single_large_cost) / pot_large_price * 100) if pot_large_price > 0 else 0.0:.1f}%)
                > * 拆分估算：**【單碗小碗成本】**： **${single_small_cost:,.2f} 元** (售價:${pot_small_price}，預估毛利率:{((pot_small_price - single_small_cost) / pot_small_price * 100) if pot_small_price > 0 else 0.0:.1f}%)
                """)

                if st.button("💾 寫入正式菜單", type="primary", key="save_pot_dishes_btn"):
                    if not pot_base_name:
                        st.error("❌ 錯誤：請輸入餐點名稱！")
                    elif pot_large_servings <= 0 and pot_small_servings <= 0:
                        st.error("❌ 錯誤：大碗與小碗的預計可做數量不能同時為 0！")
                    elif (pot_large_servings > 0 and pot_large_price <= 0) or (pot_small_servings > 0 and pot_small_price <= 0):
                        st.error("❌ 錯誤：只要有分配碗數，對應的販售價格必須大於 0！")
                    else:
                        conn = get_db_conn()
                        cursor = conn.cursor()
                        
                        if pot_large_servings > 0:
                            l_name = f"{pot_base_name}(大碗)"
                            cursor.execute("SELECT prod_id FROM products WHERE prod_name = ? AND status = 1", (l_name,))
                            if cursor.fetchone():
                                st.error(f"❌ 錯誤：【{l_name}】已存在於菜單中，請更換名稱或刪除舊品項！")
                                cursor.close()
                                conn.close()
                                st.stop()
                            l_id = get_next_dish_id()
                            cursor.execute("INSERT INTO products VALUES (?, ?, ?, ?, 0.0, '碗', '碗', 1.0, 1)", (l_id, l_name, single_large_cost, float(pot_large_price)))
                            
                            for item in st.session_state.pot_recipe_pool:
                                single_l_qty = item['單位用量'] / pot_large_servings
                                cursor.execute("INSERT INTO bom VALUES (?, ?, ?)", (l_id, item['食材編號'], single_l_qty))
                        
                        if pot_small_servings > 0:
                            s_name = f"{pot_base_name}(小碗)"
                            cursor.execute("SELECT prod_id FROM products WHERE prod_name = ? AND status = 1", (s_name,))
                            if cursor.fetchone():
                                st.error(f"❌ 錯誤：【{s_name}】已存在於菜單中，請更換名稱或刪除舊品項！")
                                cursor.close()
                                conn.close()
                                st.stop()
                                
                            if pot_large_servings > 0:
                                current_num = int(re.findall(r'\d+', l_id)[0])
                                s_id = f"P{current_num + 1:04d}"
                            else:
                                s_id = get_next_dish_id()
                                
                            cursor.execute("INSERT INTO products VALUES (?, ?, ?, ?, 0.0, '碗', '碗', 1.0, 1)", (s_id, s_name, single_small_cost, float(pot_small_price)))
                            
                            for item in st.session_state.pot_recipe_pool:
                                single_s_qty = item['單位用量'] / pot_small_servings
                                cursor.execute("INSERT INTO bom VALUES (?, ?, ?)", (s_id, item['食材編號'], single_s_qty))
                                
                        conn.commit()
                        cursor.close()
                        conn.close()
                        
                        # B模式建立整鍋餐點：精準清空菜單相關快取
                        cached_fetch_active_dishes.clear()
                        cached_fetch_all_dishes_raw.clear()
                        
                        log_history(
                            current_user, 
                            f"修正餐點參數-整鍋拆分配方-{pot_base_name}", 
                            f"透過 B模式 創立整鍋基底餐點：{pot_base_name}。整鍋物料總成本 ${total_pot_cost:.2f}。成功獨立產出大碗成本 ${single_large_cost:.2f}/小碗成本 ${single_small_cost:.2f}."
                        )
                        trigger_toast(f"🎉 成功建立 【{pot_base_name}】 大/小碗成品餐點並加入菜單！", icon="🥣")
                        st.session_state.pot_recipe_pool = []
                        st.rerun()

    st.markdown("---")
    st.markdown("##### 📋 2. 調整現有餐點配方與售價：")
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
            
            if 'editing_recipe_dish_id' not in st.session_state or st.session_state.editing_recipe_dish_id != td_id:
                db_recipe = cached_fetch_dish_bom_recipe(td_id)
                st.session_state.editing_recipe_list = db_recipe.to_dict(orient='records')
                st.session_state.editing_recipe_dish_id = td_id

            st.markdown("###### ➕ 追加食材至此餐點中：")
            
            mat_filter_edit = st.radio("🔍 篩選原物料類別", ["顯示全部", "僅看食材 (R)", "僅看用品 (S)"], horizontal=True, key="mat_filter_edit")
            if mat_filter_edit == "僅看食材 (R)":
                filtered_df_edit = all_raw_df[all_raw_df['prod_id'].str.startswith('R')]
            elif mat_filter_edit == "僅看用品 (S)":
                filtered_df_edit = all_raw_df[all_raw_df['prod_id'].str.startswith('S')]
            else:
                filtered_df_edit = all_raw_df

            col_add_e1, col_add_e2, col_add_convert, col_add_e3 = st.columns([2, 1, 1.5, 1])
            with col_add_e1:
                add_edit_mat_name = st.selectbox("選擇要追加的食材項目", ["--- 請選擇食材/用品 ---"] + filtered_df_edit['prod_name'].tolist(), key="add_edit_mat_select")
            
            db_unit_edit = ""
            if add_edit_mat_name != "--- 請選擇食材/用品 ---":
                matched_row_edit = filtered_df_edit[filtered_df_edit['prod_name'] == add_edit_mat_name].iloc[0]
                db_unit_edit = matched_row_edit['use_unit'].strip()
                
            with col_add_e2:
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown(f"**進貨定義單位：** `{db_unit_edit if db_unit_edit else '未選擇'}`")
                
            edit_conversion_mode = "不換算"
            if db_unit_edit:
                unit_lower_edit = db_unit_edit.lower()
                with col_add_convert:
                    if unit_lower_edit in ['kg', '公斤', 'g', '公克']:
                        edit_conversion_mode = st.selectbox(
                            "輸入數值的單位類型",
                            ["直接依進貨定義單位輸入", "公斤轉公克"],
                            key="edit_conversion_mode_select_weight_kg"
                        )
                    elif unit_lower_edit in ['臺斤', '台斤', '斤']:
                        edit_conversion_mode = st.selectbox(
                            "輸入數值的單位類型",
                            ["直接依進貨定義單位輸入", "台斤轉公克"],
                            key="edit_conversion_mode_select_weight_tw"
                        )
                    else:
                        st.text_input("單位換算", value="無需換算", disabled=True, key="edit_conversion_disabled")
            else:
                with col_add_convert:
                    st.text_input("單位換算", value="無需換算", disabled=True, key="edit_conversion_disabled")
                    
            with col_add_e3:
                add_edit_mat_qty = st.number_input("輸入數量", min_value=0.0, value=0.0, step=1.0, key="add_edit_mat_qty_input")
                
            if st.button("➕ 確定將此食材加入配方清單", use_container_width=True):
                if add_edit_mat_name != "--- 請選擇食材/用品 ---" and add_edit_mat_qty > 0:
                    matched_mats = filtered_df_edit[filtered_df_edit['prod_name'] == add_edit_mat_name]
                    if not matched_mats.empty:
                        mat_info = matched_mats.iloc[0]
                        existing_idx = next((i for i, item in enumerate(st.session_state.editing_recipe_list) if item['食材編號'] == mat_info['prod_id']), None)
                        
                        final_edit_conv = add_edit_mat_qty
                        if edit_conversion_mode == "公斤轉公克":
                            final_edit_conv = add_edit_mat_qty / 1000.0
                        elif edit_conversion_mode == "台斤轉公克":
                            final_edit_conv = add_edit_mat_qty / 600.0
                        
                        new_item_dict = {"食材名稱": mat_info['prod_name'], "食材編號": mat_info['prod_id'], "單位用量": float(final_edit_conv), "單位": mat_info['use_unit']}
                        
                        if existing_idx is not None:
                            st.session_state.editing_recipe_list[existing_idx] = new_item_dict
                        else:
                            st.session_state.editing_recipe_list.append(new_item_dict)
                        trigger_toast(f"已將 {mat_info['prod_name']} 整合至配方暫存單！", icon="➕")
                        st.rerun()

            st.markdown("###### 📋 該餐點目前的配方清單：")
            if st.session_state.editing_recipe_list:
                df_recipe_view = pd.DataFrame(st.session_state.editing_recipe_list)
                df_recipe_view["移除"] = False
                
                edited_df = st.data_editor(
                    df_recipe_view,
                    column_config={
                        "食材編號": st.column_config.TextColumn("物料編號", disabled=True),
                        "食材名稱": st.column_config.TextColumn("物料名稱", disabled=True),
                        "單位用量": st.column_config.NumberColumn("單份標準用量", format="%.4f", min_value=0.0), 
                        "單位": st.column_config.TextColumn("單位", disabled=True),
                        "移除": st.column_config.CheckboxColumn("勾選移除", default=False)
                    },
                    disabled=["食材編號", "食材名稱", "單位"],
                    key="bom_editor",
                    use_container_width=True,
                    hide_index=True
                )
                
                has_changes = False
                updated_recipe_list = []
                for idx, row in edited_df.iterrows():
                    if row["移除"]:
                        has_changes = True
                        continue
                    if row["單位用量"] != st.session_state.editing_recipe_list[idx]["單位用量"]:
                        has_changes = True
                    updated_recipe_list.append({"食材名稱": row["食材名稱"], "食材編號": row["食材編號"], "單位用量": float(row["單位用量"]), "單位": row["單位"]})
                    
                if has_changes:
                    st.session_state.editing_recipe_list = updated_recipe_list
                    trigger_toast("✏️ 暫存配方變更已保留！", icon="📝")
                    st.rerun()
            else:
                st.info("此餐點目前沒有任何配方物料，請利用上方追加。")

            new_dish_price = st.number_input("💵 調整此餐點售價（欲修改再輸入，維持原販售價請留 0）", min_value=0, step=1, value=0, key=f"edit_price_input_{td_id}")
            
            display_price = old_price if new_dish_price == 0 else new_dish_price

            current_editing_cost = 0.0
            for item in st.session_state.editing_recipe_list:
                matched_raw = all_raw_df[all_raw_df['prod_id'] == item['食材編號']]
                r_cost = float(matched_raw.iloc[0]['cost']) if not matched_raw.empty else 0.0
                current_editing_cost += float(item['單位用量']) * r_cost

            current_editing_profit = float(display_price) - current_editing_cost
            current_editing_margin = (current_editing_profit / display_price * 100) if display_price > 0 else 0.0

            st.markdown(f"""
            > 💡 **此餐點配方與售價即時動態預估試算面板：**
            > * 調整後餐點售價： **{display_price} 元** {"*(維持原價)*" if new_dish_price == 0 else "*(已修正售價)*"}
            > * 依目前用量推算【**單份標準原物料成本**】： **${current_editing_cost:,.2f} 元**
            > * 預估修正後【**單份毛利**】： **${current_editing_profit:,.2f} 元** ｜ 預估毛利率: **{current_editing_margin:.1f}%**
            """)
            
            recipe_has_negative = any(float(item["單位用量"]) <= 0 for item in st.session_state.editing_recipe_list)
            
            if st.button("💾 寫入變更", type="primary", use_container_width=True):
                if display_price <= 0:
                    st.error("❌ 錯誤變更：販售價格必須為大於 0 的整數！儲存失敗。")
                elif recipe_has_negative:
                    st.error("❌ 錯誤變更：保留的配方用量必須大於 0！(若要刪除物料請勾選後方的「移除」並重試)。儲存失敗。")
                elif not st.session_state.editing_recipe_list:
                    st.error("❌ 錯誤變更：修改後的配方清單不能為空！儲存失敗. ")
                else:
                    change_details = f"修改餐點【{target_dish_name}({td_id})】配置：\n"
                    if display_price != old_price:
                        change_details += f" * 價格：從 ${old_price} 改為 ${display_price}\n"
                        
                    recipe_list_to_save = []
                    updated_dish_base_cost = 0.0
                    for item in st.session_state.editing_recipe_list:
                        recipe_list_to_save.append({"食材編號": item["橫件編號" if "橫件編號" in item else "食材編號"], "單位用量": item["單位用量"]})
                        change_details += f" * 食材【{item['食材名稱']}】用量設定為 {item['單位用量']} {item['單位']}\n"
                        
                        matched_raw = all_raw_df[all_raw_df['prod_id'] == item['食材編號']]
                        r_cost = float(matched_raw.iloc[0]['cost']) if not matched_raw.empty else 0.0
                        updated_dish_base_cost += item['單位用量'] * r_cost
                        
                    conn = get_db_conn()
                    cursor = conn.cursor()
                    cursor.execute("UPDATE products SET price = ?, cost = ? WHERE prod_id = ?", (float(display_price), updated_dish_base_cost, td_id))
                    cursor.execute("DELETE FROM bom WHERE parent_id = ?", (td_id,))
                    for b_save in recipe_list_to_save:
                        cursor.execute("INSERT INTO bom VALUES (?, ?, ?)", (td_id, b_save['食材編號'], b_save['單位用量']))
                    conn.commit()
                    cursor.close()
                    conn.close()
                    
                    # 修改既有餐點配方：精準清空配方與菜單快取
                    cached_fetch_active_dishes.clear()
                    cached_fetch_dish_bom_recipe.clear(td_id)
                    cached_fetch_all_dishes_raw.clear()
                    
                    log_history(current_user, f"修正餐點參數-{target_dish_name}", change_details + f" * 同步重算標準原物料配方成本為: ${updated_dish_base_cost:.2f}")
                    trigger_toast(f"餐點【{target_dish_name}】售價與合併配方已成功覆蓋更新！", icon="⚙️")
                    del st.session_state.editing_recipe_list
                    del st.session_state.editing_recipe_dish_id
                    st.rerun()
        else:
            st.error("❌ 找不到該餐點資料，可能剛已被下架！")

with pos_tabs[3]:
    st.markdown("##### ❌ 菜單餐點下架控制面板")
    
    all_dishes_raw = cached_fetch_all_dishes_raw()
    
    if all_dishes_raw.empty:
        st.info("開出系統中尚無餐點。")
    else:
        all_dishes_raw['狀態'] = all_dishes_raw['status'].apply(lambda s: "🔴 已下架隱藏" if s == 0 else "🟢 正常販售中")
        st.dataframe(all_dishes_raw[['prod_id', 'prod_name', 'cost', 'price', '狀態']], use_container_width=True, hide_index=True)
        
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
                        conn = get_db_conn()
                        cursor = conn.cursor()
                        cursor.execute("UPDATE products SET status = 1 WHERE prod_id = ?", (del_dish_id,))
                        conn.commit()
                        cursor.close()
                        conn.close()
                        
                        # 餐點重新上架：精準清空菜單快取
                        cached_fetch_active_dishes.clear()
                        cached_fetch_all_dishes_raw.clear()
                        
                        log_history(current_user, "修正餐點參數-餐點重新上架", f"上架餐點菜單品項：{matched_del_dish['prod_name']} ({del_dish_id})")
                        trigger_toast(f"餐點【{matched_del_dish['prod_name']}】已重新上架！", icon="🚀")
                        st.rerun()
                else:
                    if st.button("🔴 確認將此餐點下架隱藏"):
                        conn = get_db_conn()
                        cursor = conn.cursor()
                        cursor.execute("UPDATE products SET status = 0 WHERE prod_id = ?", (del_dish_id,))
                        conn.commit()
                        cursor.close()
                        conn.close()
                        
                        # 餐點下架：精準清空菜單快取
                        cached_fetch_active_dishes.clear()
                        cached_fetch_all_dishes_raw.clear()
                        
                        log_history(current_user, "修正餐點參數-餐點下架隱藏", f"下架隱藏餐點菜單品項：{matched_del_dish['prod_name']} ({del_dish_id})")
                        trigger_toast(f"餐點【{matched_del_dish['prod_name']}】已成功下架！", icon="🗑️")
                        st.rerun()

    st.markdown("---")
    st.markdown("##### ❌ 食材與用品庫存品項下架面板")
    
    all_mats_raw = cached_fetch_all_materials_raw()
    
    if all_mats_raw.empty:
        st.info("系統中尚無食材或用品.")
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
                        conn = get_db_conn()
                        cursor = conn.cursor()
                        cursor.execute("UPDATE products SET status = 1 WHERE prod_id = ?", (del_mat_id,))
                        conn.commit()
                        cursor.close()
                        conn.close()
                        
                        # 恢復使用食材/用品：精準清空原物料快取
                        cached_fetch_active_materials.clear()
                        cached_fetch_all_materials_raw.clear()
                        
                        log_history(current_user, "修正餐點參數-物料恢復使用", f"重新啟用後台物料/用品：{matched_del_mat['prod_name']} ({del_mat_id})")
                        trigger_toast(f"品項【{matched_del_mat['prod_name']}】已重新啟用！", icon="✅")
                        st.rerun()
                else:
                    if st.button("🔴 確認停用並下架此品項"):
                        conn = get_db_conn()
                        cursor = conn.cursor()
                        cursor.execute("UPDATE products SET status = 0 WHERE prod_id = ?", (del_mat_id,))
                        conn.commit()
                        cursor.close()
                        conn.close()
                        
                        # 停用食材/用品：精準清空原物料快取
                        cached_fetch_active_materials.clear()
                        cached_fetch_all_materials_raw.clear()
                        
                        log_history(current_user, "修正餐點參數-物料停用下架", f"停用並下架後台物料/用品：{matched_del_mat['prod_name']} ({del_mat_id})")
                        trigger_toast(f"品項【{matched_del_mat['prod_name']}】已成功停用！", icon="🗑️")
                        st.rerun()