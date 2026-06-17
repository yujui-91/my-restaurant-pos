# pages/1_POS結帳.py
import streamlit as st
import pandas as pd
import sqlite3
import re
import json
from datetime import datetime
from database.db_core import log_history, deduct_stock_fifo, get_next_dish_id, update_dish_and_bom, trigger_toast, show_pending_toast

show_pending_toast()

st.subheader("🛒 收銀結帳與出餐管理系統")

current_user = st.session_state.get('current_user', '老 闆')

if 'pos_shopping_cart' not in st.session_state:
    st.session_state.pos_shopping_cart = []

conn = sqlite3.connect('inventory.db')
existing_dishes = pd.read_sql_query("SELECT prod_id, prod_name, price FROM products WHERE status = 1 AND prod_id LIKE 'P%'", conn)
all_raw_df = pd.read_sql_query("SELECT prod_id, prod_name, use_unit, cost FROM products WHERE status = 1 AND (prod_id LIKE 'R%' OR prod_id LIKE 'S%')", conn)
conn.close()

def calculate_cart_estimated_cost(cart_items):
    if not cart_items:
        return 0.0, {}
        
    conn = sqlite3.connect('inventory.db')
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
        
    conn.close()
    return cart_total_cost, mats_status

pos_tabs = st.tabs(["💰 前台收銀結帳", "✏️ 修改當日出餐數量", "✏️ 餐點細項修改", "❌ 品項下架與管理區"])

# ==========================================
# 分頁 1：前台收銀結帳
# ==========================================
with pos_tabs[0]:
    st.markdown("##### 🔍 1. 品項點購區：")
    
    col_cart1, col_cart2, col_cart3 = st.columns([2, 1, 1])
    with col_cart1:
        dish_select_options = ["--- 請選擇餐點 ---"] + existing_dishes['prod_name'].tolist()
        selected_cart_dish = st.selectbox("請選取欲加入點餐單的品項", dish_select_options, key="cart_dish_selector")
    with col_cart2:
        cart_dish_qty = st.number_input("點購數量 (份)", min_value=1, value=1, step=1, key="cart_qty_input")
    with col_cart3:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("➕ 加入點餐單", use_container_width=True):
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
                trigger_toast(f"已將 {matched_dish['prod_name']} x {cart_dish_qty} 份加入點餐單！", icon="🛒")
                st.rerun()

    st.markdown("---")
    st.markdown("##### 📋 當前點餐單明細：")
    
    if st.session_state.pos_shopping_cart:
        df_cart = pd.DataFrame(st.session_state.pos_shopping_cart)
        df_cart['小計'] = df_cart['price'] * df_cart['qty']
        df_cart['刪除'] = False
        
        edited_cart_df = st.data_editor(
            df_cart,
            column_config={
                "prod_id": st.column_config.TextColumn("餐點編號", disabled=True),
                "prod_name": st.column_config.TextColumn("餐點名稱", disabled=True),
                "price": st.column_config.NumberColumn("單價 ($)", disabled=True),
                "qty": st.column_config.NumberColumn("數量", min_value=1, step=1),
                "小計": st.column_config.NumberColumn("金額小計 ($)", disabled=True),
                "刪除": st.column_config.CheckboxColumn("勾選刪除", default=False)
            },
            use_container_width=True,
            hide_index=True,
            key="cart_data_editor"
        )
        
        cart_changed = False
        updated_cart = []
        total_bill_amount = 0
        
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
        > 💰 **本單商業智能即時核算面板：**
        > * 本單【**總銷售金額**】： **${total_bill_amount:,.0f} 元**
        > * 本單【**預估即時原物料成本**】： **${estimated_cart_cost:,.2f} 元** *(已模擬 FIFO 精確批次成本)*
        > * 本單【**預估純利潤**】： **${estimated_profit:,.2f} 元** ｜ 毛利率: **{estimated_margin:.1f}%**
        """)
        
        if st.button("🗑️ 清空整單重新點餐"):
            st.session_state.pos_shopping_cart = []
            if 'show_checkout_confirm' in st.session_state:
                st.session_state.show_checkout_confirm = False
            trigger_toast("已清空當前點餐單！", icon="🗑️")
            st.rerun()
            
        st.markdown("---")
        if st.button("🔥 確定點驗完畢，執行出餐結帳", type="primary", use_container_width=True):
            st.session_state.show_checkout_confirm = True

        if 'show_checkout_confirm' in st.session_state and st.session_state.show_checkout_confirm:
            st.warning("🔔 **【出餐前點單明細覆核通知】** 請再次核對下方餐點：")
            
            confirm_msg = ""
            for item in st.session_state.pos_shopping_cart:
                st.write(f"🔹 品項： **{item['prod_name']}** ｜ 數量： **{item['qty']} 份** ｜ 單價： ${item['price']} ｜ 小計： ${item['price']*item['qty']}")
                confirm_msg += f"【{item['prod_name']} x {item['qty']}份】"
            
            st.info(f"📊 **財務發貨預告：** 此次出餐預計消耗全店物料資產淨值 **${estimated_cart_cost:,.2f} 元**。")
                
            col_conf1, col_conf2 = st.columns(2)
            with col_conf1:
                if st.button("✅ 核准出餐（執行批量庫存扣料）", type="primary", use_container_width=True):
                    conn = sqlite3.connect('inventory.db')
                    cursor = conn.cursor()
                    
                    all_mats_needed = {}
                    insufficient_flag = False
                    insufficient_msg = ""
                    disabled_item_detected = False
                    disabled_msg = ""
                    
                    for cart_item in st.session_state.pos_shopping_cart:
                        d_id = cart_item['prod_id']
                        d_qty = cart_item['qty']
                        
                        db_bom = pd.read_sql_query("SELECT child_id, qty_needed FROM bom WHERE parent_id = ?", conn, params=(d_id,))
                        for _, bom_row in db_bom.iterrows():
                            c_id = bom_row['child_id']
                            needed_units = float(bom_row['qty_needed']) * d_qty
                            all_mats_needed[c_id] = all_mats_needed.get(c_id, 0.0) + needed_units
                            
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
                        st.session_state.show_checkout_confirm = False  # 改善功能 2：強迫關閉確認視窗
                        st.error(disabled_msg)
                        conn.close()
                        st.button("🔄 重新載入畫面以調整數量", on_click=st.rerun) # 協助刷新介面
                    elif insufficient_flag:
                        st.session_state.show_checkout_confirm = False  # 改善功能 2：強迫關閉確認視窗
                        st.error(insufficient_msg)
                        conn.close()
                        st.button("🔄 重新載入畫面以調整數量", on_click=st.rerun) # 協助刷新介面
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
                                
                            conn.commit()
                            conn.close()
                            
                            details_log = f"合併前台收銀：出餐明細 {confirm_msg}，總金額 ${total_bill_amount}，精準食材成本 ${actual_total_cost:.2f}。 消耗食材: " + ", ".join(log_mats_summary)
                            
                            structured_payload = {
                                "dishes": st.session_state.pos_shopping_cart,
                                "materials": mats_json_list,
                                "total_revenue": total_bill_amount,
                                "total_cost": actual_total_cost
                            }
                            final_log_entry = details_log + " ||STRUCT_DATA||" + json.dumps(structured_payload, ensure_ascii=False)
                            
                            log_history(current_user, "多品項收銀結帳", final_log_entry)
                            
                            trigger_toast(f"🎉 批量出餐結帳成功！總金額：${total_bill_amount}，實際成本：${actual_total_cost:.2f}", icon="🎉")
                            st.session_state.pos_shopping_cart = []
                            st.session_state.show_checkout_confirm = False
                            st.rerun()
                        except Exception as e:
                            conn.rollback()
                            conn.close()
                            st.error(f"🚨 會計核心異常：交易已安全回滾。原因：{e}")
            with col_conf2:
                if st.button("❌ 返回點餐單微調", use_container_width=True):
                    st.session_state.show_checkout_confirm = False
                    st.rerun()
    else:
        st.info("💡 目前點餐購物車為空，請從上方選取餐點並加入點餐單。")


# ==========================================
# 分頁 2：修改當日出餐數量與作廢（優化健康同步與時間邊界版）
# ==========================================
with pos_tabs[1]:
    st.markdown("##### 📝 當日成功核准出餐紀錄管理面版")
    
    today_start = datetime.now().strftime("%Y-%m-%d 00:00:00")
    today_end = datetime.now().strftime("%Y-%m-%d 23:59:59")

    conn = sqlite3.connect('inventory.db')
    df_today_orders = pd.read_sql_query('''
        SELECT id, timestamp, user, details FROM history 
        WHERE action = '多品項收銀結帳' AND timestamp BETWEEN ? AND ?
        ORDER BY id DESC
    ''', conn, params=(today_start, today_end))
    conn.close()

    if df_today_orders.empty:
        st.info("💡 今天目前尚無收銀出餐紀錄可供修改。")
    else:
        order_options = []
        parsed_orders_cache = {}
        
        for idx, row in df_today_orders.iterrows():
            raw_text = row['details']
            hist_id = row['id']
            orig_time = row['timestamp'] # 擷取原始交易時間
            
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
                
            brief_match = re.search(r"出餐明細 (.+?)，總金額", raw_text)
            brief = brief_match.group(1) if brief_match else "明細解析失敗"
            order_options.append(f"單號 {hist_id} | 時間: {row['timestamp'].split(' ')[1]} | 明細: {brief}")

        selected_order_str = st.selectbox("🎯 請選擇欲更正或作廢的當日出餐紀錄：", order_options)
        target_hist_id = int(selected_order_str.split("單號 ")[1].split(" |")[0])
        matched_order_row = df_today_orders[df_today_orders['id'] == target_hist_id].iloc[0]
        order_details_text = matched_order_row['details']

        st.info(f"📋 **選定訂單完整原始日誌：**\n{order_details_text.split('||STRUCT_DATA||')[0]}")

        order_data = parsed_orders_cache[target_hist_id]
        orig_order_timestamp = order_data["orig_timestamp"] # 安全備用時間標籤

        if order_data["is_structured"]:
            parsed_dishes = [(d["prod_name"], d["qty"], d["prod_id"]) for d in order_data["dishes"]]
            parsed_total_revenue = order_data["total_revenue"]
            parsed_total_cost = order_data["total_cost"]
            parsed_mats = order_data["materials"]
        else:
            raw_dishes = re.findall(r"【(.+?) x (\d+)份】", order_details_text)
            parsed_dishes = []
            conn_temp = sqlite3.connect('inventory.db')
            cursor_temp = conn_temp.cursor()
            for name, qty in raw_dishes:
                cursor_temp.execute("SELECT prod_id FROM products WHERE prod_name = ?", (name,))
                pid_row = cursor_temp.fetchone()
                pid = pid_row[0] if pid_row else ""
                parsed_dishes.append((name, int(qty), pid))
            conn_temp.close()
            
            parsed_total_revenue = float(re.search(r"總金額 \$(\d+)", order_details_text).group(1))
            parsed_total_cost = float(re.search(r"精準食材成本 \$([\d\.]+)", order_details_text).group(1))
            raw_mats = re.findall(r"([^\s_,\(]+)_([RS]\d+)\(([\d\.]+)([^\)]+)\)", order_details_text)
            parsed_mats = [{"mat_name": m[0], "mat_id": m[1], "qty": float(m[2]), "unit": m[3], "deducted_batches": []} for m in raw_mats]

        st.markdown("##### ⚙️ 選擇維護動作")
        manage_action = st.radio("請選擇維護類型：", ["❌ 整單作廢（全數退款並回補庫存）", "✏️ 數量微調（更正點餐數量）"], horizontal=True)

        if "整單作廢" in manage_action:
            st.warning("⚠️ **注意：** 作廢將依據當時結帳發貨時的「精確原始批次」完整歸還至庫存中。")
            if st.button("🔥 確定執行整單作廢", type="primary", use_container_width=True):
                conn = sqlite3.connect('inventory.db')
                cursor = conn.cursor()
                try:
                    for mat in parsed_mats:
                        mat_id = mat["mat_id"]
                        refund_qty = float(mat["qty"])
                        batches_info = mat.get("deducted_batches", [])
                        
                        if batches_info:
                            for b_info in batches_info:
                                # 【功能改善 1】作廢退回時，只加回即時庫存量 qty，絕對不可更動歷史進貨總量 original_qty
                                cursor.execute(
                                    "UPDATE stock_batches SET qty = qty + ? WHERE batch_id = ?", 
                                    (float(b_info["qty"]), b_info["batch_id"])
                                )
                        else:
                            cursor.execute("SELECT batch_id FROM stock_batches WHERE prod_id = ? ORDER BY inbound_date DESC, batch_id DESC LIMIT 1", (mat_id,))
                            b_row = cursor.fetchone()
                            if b_row:
                                # 【功能改善 1】無批次紀錄時回補最新批次，同樣只加回 qty
                                cursor.execute("UPDATE stock_batches SET qty = qty + ? WHERE batch_id = ?", (refund_qty, b_row[0]))
                            else:
                                today_str = datetime.now().strftime("%Y-%m-%d")
                                cursor.execute("SELECT cost FROM products WHERE prod_id = ?", (mat_id,))
                                p_cost = cursor.fetchone()[0] or 0.0
                                cursor.execute("INSERT INTO stock_batches (prod_id, qty, original_qty, expiry_date, inbound_date, vendor_name, cost) VALUES (?, ?, ?, '', ?, '前台作廢退回', ?)", (mat_id, refund_qty, refund_qty, today_str, p_cost))
                    
                    cursor.execute("DELETE FROM history WHERE id = ?", (target_hist_id,))
                    conn.commit()
                    
                    log_history(current_user, "訂單作廢成功", f"老闆作廢了單號 {target_hist_id} 的當日訂單，成功退回營業額 ${parsed_total_revenue} 元，庫存原物料已精準完整回補。 [原始訂單交易時間: {orig_order_timestamp}]")
                    trigger_toast(f"已成功作廢單號 {target_hist_id} 的點餐紀錄，庫存已同步回補！", icon="🗑️")
                    st.rerun()
                except Exception as e:
                    conn.rollback()
                    st.error(f"執行作廢失敗：{e}")
                finally:
                    conn.close()

        elif "數量微調" in manage_action:
            st.markdown("###### 📝 請在下方輸入該單「正確」的餐點數量：")
            new_dish_qtys = {}
            has_qty_changed = False
            
            for d_name, d_qty, d_id in parsed_dishes:
                new_q = st.number_input(f"【{d_name}】之正確出餐份數 (原為 {d_qty} 份)", min_value=0, value=int(d_qty), step=1, key=f"edit_qty_{d_name}")
                new_dish_qtys[d_name] = new_q
                if new_q != int(d_qty):
                    has_qty_changed = True

            if st.button("💾 儲存出餐數量變更", type="primary", use_container_width=True):
                if not has_qty_changed:
                    st.info("數量沒有任何變動，無需修正。")
                else:
                    conn = sqlite3.connect('inventory.db')
                    cursor = conn.cursor()
                    try:
                        for mat in parsed_mats:
                            if mat.get("deducted_batches", []):
                                for b_info in mat["deducted_batches"]:
                                    # 【功能改善 1】微調回補時，只加回即時庫存量 qty，不更動 original_qty
                                    cursor.execute("UPDATE stock_batches SET qty = qty + ? WHERE batch_id = ?", (float(b_info["qty"]), b_info["batch_id"]))
                            else:
                                # 【功能改善 1】同上，只加回 qty
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
                                insufficient_msg += f"❌ 修正失敗：微調後共需要物料【{m_name}】{total_need:.1f}，回補後全庫僅剩 {avail:.1f}！\n"
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
                        else:
                            details_text_part = f"合併前台收銀：出餐明細 {new_confirm_msg}，總金額 ${new_total_bill:.0f}，精準食材成本 ${final_new_cost:.2f}。 消耗食材: " + ", ".join(log_mats_summary)
                            new_payload_struct = {
                                "dishes": new_cart_payload, 
                                "materials": new_mats_payload, 
                                "total_revenue": new_total_bill, 
                                "total_cost": final_new_cost,
                                "orig_timestamp": orig_order_timestamp
                            }
                            updated_full_log = details_text_part + " ||STRUCT_DATA||" + json.dumps(new_payload_struct, ensure_ascii=False)
                            
                            cursor.execute("UPDATE history SET details = ? WHERE id = ?", (updated_full_log, target_hist_id))
                            
                            for m_id in total_mats_needed_new.keys():
                                matched_hist_mat = next((m for m in parsed_mats if m["mat_id"] == m_id), None)
                                hist_cost_fallback = 0.0
                                if matched_hist_mat and "deducted_batches" in matched_hist_mat and matched_hist_mat["deducted_batches"]:
                                    hist_cost_fallback = float(matched_hist_mat["deducted_batches"][0].get("cost", 0.0))
                                else:
                                    hist_cost_fallback = 0.0

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
                            
                            conn.commit()
                            trigger_toast(f"🎉 單號 {target_hist_id} 的數量已成功更正，且後台庫存成本已完美同步！", icon="✏️")
                            st.rerun()
                    except Exception as e:
                        conn.rollback()
                        st.error(f"更新數量時發生錯誤，資料庫已安全復原：{e}")
                    finally:
                        conn.close()


# ==========================================
# 分頁 3：餐點配方微調與臨時餐點創立 (已移除手動單位，改為自動追隨)
# ==========================================
with pos_tabs[2]:
    st.markdown("##### 🆕 1. 現場食材加料 / 臨時自訂新餐點創立區")
    
    creation_mode = st.radio("🛠️ 請選擇餐點建立模式：", ["A模式：單份獨立餐點建立（原有功能）", "B模式：整鍋物料拆分建立（大/小碗成本攤算）"], horizontal=True)

    if creation_mode == "A模式：單份獨立餐點建立（原有功能）":
        with st.expander("🛠️ 展開自訂臨時餐點與即時配方調配面板 (A模式)", expanded=True):
            col_new_dish1, col_new_dish2 = st.columns(2)
            with col_new_dish1:
                pos_custom_name = st.text_input("手動輸入臨時/新創餐點名稱", value="", key="custom_dish_name_input").strip()
            with col_new_dish2:
                pos_custom_price = st.number_input("設定販售價格 (必須大於 0 的整數)", min_value=0, value=0, step=1, key="custom_dish_price_input")
                
            st.markdown("###### ➕ 請調配此項客製餐點的專屬物料與用量：")
            col_cus_mat1, col_cus_mat2, col_cus_mat3 = st.columns([2, 1, 1])
            with col_cus_mat1:
                dish_select_list = ["--- 請選擇食材 ---"] + all_raw_df['prod_name'].tolist()
                cus_mat_name = st.selectbox("選擇要加入的食材/用品名稱", dish_select_list, key="cus_mat_selector")
            
            db_unit_a = ""
            if cus_mat_name != "--- 請選擇食材 ---":
                matched_row_a = all_raw_df[all_raw_df['prod_name'] == cus_mat_name].iloc[0]
                db_unit_a = matched_row_a['use_unit'].strip()
                
            with col_cus_mat2:
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown(f"**使用單位：** `{db_unit_a if db_unit_a else '未選擇'}`")
                
            with col_cus_mat3:
                cus_mat_qty = st.number_input("單份餐點用量", min_value=0.0, value=0.0, step=1.0, key="cus_qty_selector")
                
            if 'custom_recipe_pool' not in st.session_state:
                st.session_state.custom_recipe_pool = []
                
            if st.button("➕ 將此原物料揉入暫存配方", key="add_cus_recipe_btn"):
                if cus_mat_name == "--- 請選擇食材 ---" or cus_mat_qty <= 0:
                    st.error("請選擇有效原物料並輸入大於 0 的用量！")
                else:
                    mat_info = all_raw_df[all_raw_df['prod_name'] == cus_mat_name].iloc[0]
                    final_conv = cus_mat_qty 
                    
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
                    column_config={"食材編號": st.column_config.TextColumn("編號", disabled=True), "食材名稱": st.column_config.TextColumn("名稱", disabled=True), "單位用量": st.column_config.NumberColumn("用量", format="%.4f"), "移除": st.column_config.CheckboxColumn("移除")},
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
                > 💡 **🆕 新創餐點定價與配方成本動態預估試算：**
                > * 餐點暫定售價： **${pos_custom_price} 元**
                > * 依目前庫存推算【**單份標準原物料成本**】： **${custom_custom_dish_calc_cost:,.2f} 元**
                > * 預估【**單份毛利**】： **${custom_profit:,.2f} 元** ｜ 預估毛利率: **{custom_margin:.1f}%**
                """)
                    
                if st.button("💾 確定打包此新創餐點並寫入正式菜單", type="primary"):
                    if not pos_custom_name:
                        st.error("❌ 錯誤：請輸入臨時/新創餐點名稱！")
                    elif pos_custom_price <= 0:
                        st.error("❌ 錯誤：販售價格必須為大於 0 的整數！")
                    elif not st.session_state.custom_recipe_pool:
                        st.error("❌ 錯誤變更：新創餐點必須至少包含一項原物料配方，不可做「無本生意」！")
                    else:
                        conn = sqlite3.connect('inventory.db')
                        cursor = conn.cursor()
                        cursor.execute("SELECT prod_id FROM products WHERE prod_name = ? AND status = 1", (pos_custom_name,))
                        if cursor.fetchone():
                            st.error(f"❌ 錯誤：【{pos_custom_name}】已存在於正式菜單中，請直接至下方區塊修正參數，切勿重複建立！")
                            conn.close()
                        else:
                            new_d_id = get_next_dish_id()
                            cursor.execute("INSERT INTO products VALUES (?, ?, ?, ?, 0.0, '份', '份', 1.0, 1)", (new_d_id, pos_custom_name, custom_custom_dish_calc_cost, float(pos_custom_price)))
                            for item in st.session_state.custom_recipe_pool:
                                cursor.execute("INSERT INTO bom VALUES (?, ?, ?)", (new_d_id, item['食材編號'], item['單位用量']))
                            conn.commit()
                            conn.close()
                            # 【功能改善 2】修正寫入與日誌調用的變數，統一使用正確定義的 custom_custom_dish_calc_cost
                            log_history(
                                current_user, 
                                f"修正餐點參數-新創自訂餐點-{pos_custom_name}", 
                                f"創立了全新的新菜色：{pos_custom_name}({new_d_id})，定價 ${pos_custom_price}，設定基本單位配方成本 ${custom_custom_dish_calc_cost:.2f}。"
                            )
                            trigger_toast(f"成功建立餐點 【{pos_custom_name}】 並加入菜單選單！", icon="🚀")
                            st.session_state.custom_recipe_pool = []
                            st.rerun()

    elif creation_mode == "B模式：整鍋物料拆分建立（大/小碗成本攤算）":
        with st.expander("🛠️ 展開自訂臨時餐點與即時配方調配面板 (B模式)", expanded=True):
            col_b_name, col_b_price1, col_b_price2 = st.columns([2, 1, 1])
            with col_b_name:
                pot_base_name = st.text_input("輸入此鍋餐點基底名稱 (如: 招牌麻辣火鍋)", value="", key="pot_base_name_input").strip()
            with col_b_price1:
                pot_large_price = st.number_input("設定【大碗】販售價格", min_value=0, value=0, step=1, key="pot_large_price_input")
            with col_b_price2:
                pot_small_price = st.number_input("設定【小碗】販售價格", min_value=0, value=0, step=1, key="pot_small_price_input")

            st.markdown("###### 📊 填寫此整鍋預計可拆分的銷售碗數：")
            col_split1, col_split2 = st.columns(2)
            with col_split1:
                pot_large_servings = st.number_input("整鍋可做成【大碗】的總碗數", min_value=0.0, value=0.0, step=1.0, key="pot_large_servings")
            with col_split2:
                pot_small_servings = st.number_input("整鍋可做成【小碗】的總碗數", min_value=0.0, value=0.0, step=1.0, key="pot_small_servings")

            st.markdown("###### ➕ 請添加此「整鍋」投入的所有食材與總用量：")
            col_b_mat1, col_b_mat2, col_b_mat3 = st.columns([2, 1, 1])
            with col_b_mat1:
                b_dish_select_list = ["--- 請選擇食材 ---"] + all_raw_df['prod_name'].tolist()
                b_mat_name = st.selectbox("選擇投入此鍋的食材/用品項目", b_dish_select_list, key="b_mat_selector")
            
            db_unit_b = ""
            if b_mat_name != "--- 請選擇食材 ---":
                matched_row_b = all_raw_df[all_raw_df['prod_name'] == b_mat_name].iloc[0]
                db_unit_b = matched_row_b['use_unit'].strip()

            with col_b_mat2:
                st.markdown("<br>", unsafe_allow_html=True)
                st.markdown(f"**投入單位：** `{db_unit_b if db_unit_b else '未選擇'}`")
            with col_b_mat3:
                b_mat_qty = st.number_input("投入此整鍋的總用量", min_value=0.0, value=0.0, step=1.0, key="b_qty_selector")

            if 'pot_recipe_pool' not in st.session_state:
                st.session_state.pot_recipe_pool = []

            if st.button("➕ 將食材計入整鍋總配方中", key="add_pot_recipe_btn"):
                if b_mat_name == "--- 請選擇食材 ---" or b_mat_qty <= 0:
                    st.error("請選擇有效原物料並輸入大於 0 的投入量！")
                else:
                    mat_info = all_raw_df[all_raw_df['prod_name'] == b_mat_name].iloc[0]
                    final_conv = b_mat_qty

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
                    column_config={"食材編號": st.column_config.TextColumn("編號", disabled=True), "食材名稱": st.column_config.TextColumn("名稱", disabled=True), "單位用量": st.column_config.NumberColumn("整鍋總用量", format="%.4f"), "移除": st.column_config.CheckboxColumn("移除")},
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

                total_pot_cost = 0.0
                for p_item in st.session_state.pot_recipe_pool:
                    matched_raw = all_raw_df[all_raw_df['prod_id'] == p_item['食材編號']]
                    r_cost = float(matched_raw.iloc[0]['cost']) if not matched_raw.empty else 0.0
                    total_pot_cost += p_item['單位用量'] * r_cost

                total_shares = (pot_large_servings * 1.5) + (pot_small_servings * 1.0)
                
                if total_shares > 0:
                    cost_per_share = total_pot_cost / total_shares
                    single_large_cost = cost_per_share * 1.5
                    single_small_cost = cost_per_share * 1.0
                else:
                    single_large_cost, single_small_cost = 0.0, 0.0

                st.markdown(f"""
                > 📊 **🥣 整鍋成本拆分攤算即時面板：**
                > * 投入這整鍋的【**原物料總成本**】： **${total_pot_cost:,.2f} 元**
                > * 拆分估算：**【單碗大碗成本】**： **${single_large_cost:,.2f} 元** (售價:${pot_large_price}，預估毛利率:{(pot_large_price-single_large_cost)/pot_large_price*100 if pot_large_price>0 else 0:.1f}%)
                > * 拆分估算：**【單碗小碗成本】**： **${single_small_cost:,.2f} 元** (售價:${pot_small_price}，預估毛利率:{(pot_small_price-single_small_cost)/pot_small_price*100 if pot_small_price>0 else 0:.1f}%)
                """)

                if st.button("💾 打包打包大/小碗餐點同時寫入菜單", type="primary", key="save_pot_dishes_btn"):
                    if not pot_base_name:
                        st.error("❌ 錯誤：請輸入餐點基底名稱！")
                    elif pot_large_servings <= 0 and pot_small_servings <= 0:
                        st.error("❌ 錯誤：大碗與小碗的預計可做數量不能同時為 0！")
                    elif (pot_large_servings > 0 and pot_large_price <= 0) or (pot_small_servings > 0 and pot_small_price <= 0):
                        st.error("❌ 錯誤：只要有分配碗數，對應的販售價格必須大於 0！")
                    else:
                        conn = sqlite3.connect('inventory.db')
                        cursor = conn.cursor()
                        
                        if pot_large_servings > 0:
                            l_name = f"{pot_base_name}(大碗)"
                            cursor.execute("SELECT prod_id FROM products WHERE prod_name = ? AND status = 1", (l_name,))
                            if cursor.fetchone():
                                st.error(f"❌ 錯誤：【{l_name}】已存在於菜單中，請更換名稱或刪除舊品項！")
                                conn.close()
                                st.stop()
                            l_id = get_next_dish_id()
                            cursor.execute("INSERT INTO products VALUES (?, ?, ?, ?, 0.0, '碗', '碗', 1.0, 1)", (l_id, l_name, single_large_cost, float(pot_large_price)))
                            for item in st.session_state.pot_recipe_pool:
                                single_l_qty = (item['單位用量'] / total_shares) * 1.5
                                cursor.execute("INSERT INTO bom VALUES (?, ?, ?)", (l_id, item['食材編號'], single_l_qty))
                        
                        if pot_small_servings > 0:
                            s_name = f"{pot_base_name}(小碗)"
                            cursor.execute("SELECT prod_id FROM products WHERE prod_name = ? AND status = 1", (s_name,))
                            if cursor.fetchone():
                                st.error(f"❌ 錯誤：【{s_name}】已存在於菜單中，請更換名稱或刪除舊品項！")
                                conn.close()
                                st.stop()
                            s_id = get_next_dish_id()
                            cursor.execute("INSERT INTO products VALUES (?, ?, ?, ?, 0.0, '碗', '碗', 1.0, 1)", (s_id, s_name, single_small_cost, float(pot_small_price)))
                            for item in st.session_state.pot_recipe_pool:
                                single_s_qty = (item['單位用量'] / total_shares) * 1.0
                                cursor.execute("INSERT INTO bom VALUES (?, ?, ?)", (s_id, item['食材編號'], single_s_qty))
                                
                        conn.commit()
                        conn.close()
                        
                        log_history(
                            current_user, 
                            f"修正餐點參數-整鍋拆分配方-{pot_base_name}", 
                            f"透過 B模式 創立整鍋基底餐點：{pot_base_name}。整鍋物料總成本 ${total_pot_cost:.2f}。成功產出大碗成本 ${single_large_cost:.2f}/小碗成本 ${single_small_cost:.2f}。"
                        )
                        trigger_toast(f"🎉 成功批次打包建立 【{pot_base_name}】 大/小碗成品餐點並加入菜單！", icon="🥣")
                        st.session_state.pot_recipe_pool = []
                        st.rerun()

    st.markdown("---")
    st.markdown("##### 📋 2. 調整與管理現有餐點配方與售價：")
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
                conn = sqlite3.connect('inventory.db')
                db_recipe = pd.read_sql_query('''
                    SELECT p.prod_name as 食材名稱, b.child_id as 食材編號, b.qty_needed as 單位用量, p.use_unit as 單位
                    FROM bom b JOIN products p ON b.child_id = p.prod_id WHERE b.parent_id = ?
                ''', conn, params=(td_id,))
                conn.close()
                st.session_state.editing_recipe_list = db_recipe.to_dict(orient='records')
                st.session_state.editing_recipe_dish_id = td_id

            st.markdown("###### ➕ 追加全新原物料至此餐點中：")
            col_add_e1, col_add_e2 = st.columns([3, 1])
            with col_add_e1:
                add_edit_mat_name = st.selectbox("選擇要追加的原物料項目", ["--- 請選擇食材/用品 ---"] + all_raw_df['prod_name'].tolist(), key="add_edit_mat_select")
            with col_add_e2:
                add_edit_mat_qty = st.number_input("設定單份標準用量", min_value=0.0001, value=1.0, step=1.0, key="add_edit_mat_qty_input")
                
            if st.button("➕ 確定將此原物料塞入配方清單", use_container_width=True):
                if add_edit_mat_name != "--- 請選擇食材/用品 ---" and add_edit_mat_qty > 0:
                    matched_mats = all_raw_df[all_raw_df['prod_name'] == add_edit_mat_name]
                    if not matched_mats.empty:
                        mat_info = matched_mats.iloc[0]
                        existing_idx = next((i for i, item in enumerate(st.session_state.editing_recipe_list) if item['食材編號'] == mat_info['prod_id']), None)
                        
                        new_item_dict = {"食材名稱": mat_info['prod_name'], "食材編號": mat_info['prod_id'], "單位用量": float(add_edit_mat_qty), "單位": mat_info['use_unit']}
                        
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
                        "單位用量": st.column_config.NumberColumn("單份標準用量", format="%.4f", min_value=0.0001),
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

            new_dish_price = st.number_input("💵 調整此餐點最終門市售價 (必須為大於 0 的整數)", step=1, key="edit_price_input", value=max(old_price, 1))
            recipe_has_negative = any(float(item["單位用量"]) <= 0 for item in st.session_state.editing_recipe_list)
            
            if st.button("💾 確認儲存餐點售價與完整配方變更", type="primary", use_container_width=True):
                if new_dish_price <= 0:
                    st.error("❌ 錯誤變更：販售價格必須為大於 0 的整數！儲存失敗。")
                elif recipe_has_negative:
                    st.error("❌ 錯誤變更：配方用量必須大於 0！儲存失敗。")
                elif not st.session_state.editing_recipe_list:
                    st.error("❌ 錯誤變更：修改後的配方清單不能為空！儲存失敗。")
                else:
                    change_details = f"修改餐點【{target_dish_name}({td_id})】配置：\n"
                    if new_dish_price != old_price:
                        change_details += f" * 價格：從 ${old_price} 改為 ${new_dish_price}\n"
                        
                    recipe_list_to_save = []
                    updated_dish_base_cost = 0.0
                    for item in st.session_state.editing_recipe_list:
                        recipe_list_to_save.append({"食材編號": item["食材編號"], "單位用量": item["單位用量"]})
                        change_details += f" * 食材【{item['食材名稱']}】用量設定為 {item['單位用量']} {item['單位']}\n"
                        
                        matched_raw = all_raw_df[all_raw_df['prod_id'] == item['食材編號']]
                        r_cost = float(matched_raw.iloc[0]['cost']) if not matched_raw.empty else 0.0
                        updated_dish_base_cost += item['單位用量'] * r_cost
                        
                    conn = sqlite3.connect('inventory.db')
                    cursor = conn.cursor()
                    cursor.execute("UPDATE products SET price = ?, cost = ? WHERE prod_id = ?", (float(new_dish_price), updated_dish_base_cost, td_id))
                    cursor.execute("DELETE FROM bom WHERE parent_id = ?", (td_id,))
                    for b_save in recipe_list_to_save:
                        cursor.execute("INSERT INTO bom VALUES (?, ?, ?)", (td_id, b_save['食材編號'], b_save['單位用量']))
                    conn.commit()
                    conn.close()
                    
                    log_history(current_user, f"修正餐點參數-{target_dish_name}", change_details + f" * 同步重算標準原物料配方成本為: ${updated_dish_base_cost:.2f}")
                    trigger_toast(f"餐點【{target_dish_name}】售價與合併配方已成功覆蓋更新！", icon="⚙️")
                    del st.session_state.editing_recipe_list
                    del st.session_state.editing_recipe_dish_id
                    st.rerun()
        else:
            st.error("❌ 找不到該餐點資料，可能剛已被下架！")


# ==========================================
# 分頁 4：品項下架管理控制區
# ==========================================
with pos_tabs[3]:
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
                        log_history(current_user, "修正餐點參數-餐點重新上架", f"上架餐點菜單品項：{matched_del_dish['prod_name']} ({del_dish_id})")
                        trigger_toast(f"餐點【{matched_del_dish['prod_name']}】已重新上架！", icon="🚀")
                        st.rerun()
                else:
                    if st.button("🔴 確認將此餐點下架隱藏"):
                        conn = sqlite3.connect('inventory.db')
                        cursor = conn.cursor()
                        cursor.execute("UPDATE products SET status = 0 WHERE prod_id = ?", (del_dish_id,))
                        conn.commit()
                        conn.close()
                        log_history(current_user, "修正餐點參數-餐點下架隱藏", f"下架隱藏餐點菜單品項：{matched_del_dish['prod_name']} ({del_dish_id})")
                        trigger_toast(f"餐點【{matched_del_dish['prod_name']}】已成功下架！", icon="🗑️")
                        st.rerun()

    st.divider()
    st.markdown("##### ❌ 食材與用品庫存品項下架面板")
    conn = sqlite3.connect('inventory.db')
    all_mats_raw = pd.read_sql_query("SELECT prod_id, prod_name, use_unit, status FROM products WHERE prod_id LIKE 'R%' OR prod_id LIKE 'S%'", conn)
    conn.close()
    
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
                        conn = sqlite3.connect('inventory.db')
                        cursor = conn.cursor()
                        cursor.execute("UPDATE products SET status = 1 WHERE prod_id = ?", (del_mat_id,))
                        conn.commit()
                        conn.close()
                        log_history(current_user, "修正餐點參數-物料恢復使用", f"重新啟用後台物料/用品：{matched_del_mat['prod_name']} ({del_mat_id})")
                        trigger_toast(f"品項【{matched_del_mat['prod_name']}】已重新啟用！", icon="✅")
                        st.rerun()
                else:
                    if st.button("🔴 確認停用並下架此品項"):
                        conn = sqlite3.connect('inventory.db')
                        cursor = conn.cursor()
                        cursor.execute("UPDATE products SET status = 0 WHERE prod_id = ?", (del_mat_id,))
                        conn.commit()
                        conn.close()
                        log_history(current_user, "修正餐點參數-物料停用下架", f"停用並下架後台物料/用品：{matched_del_mat['prod_name']} ({del_mat_id})")
                        trigger_toast(f"品項【{matched_del_mat['prod_name']}】已成功停用！", icon="🗑️")
                        st.rerun()