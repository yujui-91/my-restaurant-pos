# pages/3_庫存調整.py
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
from database.db_core import log_history, trigger_toast, show_pending_toast, get_db_conn, deduct_stock_fifo
# 從 db_core 載入所需的快取函式
from database.db_core import cached_fetch_unique_items_to_adjust, cached_fetch_batches_by_prod

show_pending_toast()

st.subheader("🔧 庫存管理面板")

if 'current_user' not in st.session_state:
    st.session_state.current_user = "老闆娘"
current_user = st.session_state.current_user


# ==========================================
# 區塊一：【原物料微調】Fragment 區域
# ==========================================
@st.fragment
def render_stock_adjustment_zone(current_user):
    st.markdown("### 📋 一般原物料庫存微調")
    st.caption("此區用於原料過期、損壞、打翻或盤點微調時，直接對『單一原料的特定進貨批次』進行數量加減。")
    
    use_mobile_view = st.toggle("📱 切換為手機/平板專用排版", value=False, key="adj_mobile_toggle")
    
    stock_adj_cate = st.radio("🗂️ 請選擇要調整的項目類別：", [" 食材 (R)", " 用品 (S)"], horizontal=True, key="adj_cate_radio")

    if "食材" in stock_adj_cate: 
        prefix_filter = "R%"
    else: 
        prefix_filter = "S%"

    df_unique_items = cached_fetch_unique_items_to_adjust(prefix_filter)

    if not df_unique_items.empty:
        item_options = df_unique_items.apply(lambda r: f"{r['prod_id']} - {r['prod_name']}", axis=1).tolist()
        selected_item_str = st.selectbox("🔍 1. 請先選取欲調整的項目名稱：", item_options, key="adj_item_select")
        target_prod_id = selected_item_str.split(" - ")[0]
        
        df_batches = cached_fetch_batches_by_prod(target_prod_id)
        
        if not df_batches.empty:
            if use_mobile_view:
                st.markdown("🎯 **2. 請點擊該品項欲更動的進貨批次：**")
                
                mobile_options_map = {}
                for _, r in df_batches.iterrows():
                    label = (
                        f"📦 【批次 {int(r['batch_id'])}】\n"
                        f"  🗓️ 進貨: {r['inbound_date']} | ⏳ 效期: {r['expiry_date'] if r['expiry_date'] else '無'}\n"
                        f"  🚨 目前現存: {r['qty']} {r['use_unit']}"
                    )
                    mobile_options_map[label] = int(r['batch_id'])
                
                selected_mobile_label = st.radio(
                    "批次清單", 
                    options=list(mobile_options_map.keys()), 
                    label_visibility="collapsed", 
                    key="adj_batch_radio_mobile"
                )
                batch_id_part = mobile_options_map[selected_mobile_label]
                
            else:
                batch_options = df_batches.apply(
                    lambda r: f"【批次編號: {r['batch_id']}】進貨日: {r['inbound_date']} | 現存: {r['qty']}{r['use_unit']} | 效期: {r['expiry_date'] if r['expiry_date'] else '無'}", 
                    axis=1
                ).tolist()
                
                selected_batch_row = st.selectbox("🎯 2. 請選擇該品項欲更動的進貨批次：", batch_options, key="adj_batch_select_desktop")
                batch_id_part = int(selected_batch_row.split("【批次編號: ")[1].split("】")[0])
            
            matched_rows = df_batches[df_batches['batch_id'] == batch_id_part]
            if not matched_rows.empty:
                matched_row = matched_rows.iloc[0]
                
                item_name = selected_item_str.split(" - ")[1]
                current_qty = float(matched_row['qty'])
                unit_label = matched_row['use_unit']
                orig_inbound_date = matched_row['inbound_date']
                
                st.markdown(f"> 📊 **當前選擇批次狀態：** **{item_name}** (進貨日期: {orig_inbound_date}) ｜ 目前系統登記庫存量： **{current_qty} {unit_label}**")
                
                with st.form("inventory_adjustment_form"):
                    adj_type = st.radio("動作選擇", ["過期損耗/報廢 (扣減庫存)", "手動補正(增加庫存)"], horizontal=True, key="adj_action_type")
                    st.number_input(f"請輸入異動變更的數量 ({unit_label})  ", value=1.0, step=1.0, key="adjust_qty_input")
                    
                    st.markdown("###### 📅 請指定此筆損耗歸屬之完整年份與月份（確保跨年財報精確）：")
                    col_adj_y, col_adj_m = st.columns(2)
                    with col_adj_y:
                        this_year = datetime.now().year
                        adj_year_options = [this_year, this_year - 1]
                        selected_adj_year = st.selectbox("歸帳年份", adj_year_options, index=0, key="adj_year_select")
                    with col_adj_m:
                        bill_months_options = [f"{i}月" for i in range(1, 13)]
                        current_month_idx = max(0, min(datetime.now().month - 1, 11))
                        adj_month_str = st.selectbox("歸帳月份", bill_months_options, index=current_month_idx, key="adj_month_select")
                    
                    reason_txt = st.text_input("請填寫微調/報廢原因說明 (選填)", value="", key="adj_reason_input")
                    submit_adj = st.form_submit_button("🔧 確認執行庫存異動")
                    
                    if submit_adj:
                        adj_qty_val = st.session_state.adjust_qty_input
                        
                        if adj_qty_val <= 0:
                            st.error(f"❌ 錯誤：異動變更的數量必須大於 0！您目前的輸入數值為 {adj_qty_val}")
                        else:
                            final_qty_change = -adj_qty_val if "扣減" in adj_type or "報廢" in adj_type else adj_qty_val
                            new_total_qty = current_qty + final_qty_change
                            
                            if new_total_qty < 0:
                                st.error("❌ 錯誤：扣減數量不能大於現有庫存量！")
                            else:
                                final_reason = reason_txt.strip() if reason_txt.strip() != "" else "未填寫原因"

                                conn = get_db_conn()
                                cursor = conn.cursor()
                                cursor.execute("SELECT cost FROM products WHERE prod_id = ?", (target_prod_id,))
                                unit_cost = cursor.fetchone()[0] or 0.0
                                total_value_change = final_qty_change * unit_cost

                                cursor.execute("UPDATE stock_batches SET qty = ? WHERE batch_id = ?", (new_total_qty, batch_id_part))
                                conn.commit()
                                conn.close()
                                
                                # 精準清除快取
                                cached_fetch_unique_items_to_adjust.clear()
                                cached_fetch_batches_by_prod.clear()
                                
                                month_digits = int(adj_month_str.replace("月", ""))
                                formatted_target_month = f"{selected_adj_year}-{month_digits:02d}"

                                log_details = (
                                    f"庫存微調【{item_name}】(批次編號 {batch_id_part}，進貨日: {orig_inbound_date})。"
                                    f"動作：{adj_type}，數量變動：{final_qty_change} {unit_label}，異動後現存：{new_total_qty} {unit_label}，"
                                    f"總值變動: ${total_value_change:.2f}。原因：{final_reason}。 目標歸帳月份: {formatted_target_month}"
                                )
                                log_history(current_user, f"手動調整庫存-品項:{target_prod_id}", log_details)
                                
                                trigger_toast(f"🛠️ 批次庫存微調完畢！品項：{item_name}，變動量：{final_qty_change:+,.1f}", icon="🔧")
                                st.success(f"🎉 批次庫存調整成功！已成功紀錄於歷史動作審計軌跡，並歸帳至 {formatted_target_month}。")
                                st.rerun()
            else:
                st.stop()
        else:
            st.info("💡 該品項目前無有效批次。")
    else:
        st.info(f"💡 目前 【{stock_adj_cate}】 類別中沒有任何在庫庫存批次可供微調。")


# ==========================================
# 區塊二：【成品殘餘報廢】Fragment 區域
# ==========================================
@st.fragment
def render_dish_scrap_zone(current_user):
    st.markdown("### 🥣 每日整鍋殘餘 / 成品報廢登記")
    st.caption("此功能會根據料理的配方表（BOM），自動計算並透過 FIFO（先進先出）扣除對應的原物料庫存。適合用於打烊時倒掉的殘餘砂鍋菜底、羹湯。")
    
    conn = get_db_conn()
    cursor = conn.cursor()
    
    try:
        # 動態抓取所有商品（排除原物料），讓選單保持乾淨
        cursor.execute("SELECT prod_name FROM products WHERE is_ingredient = 0 OR is_ingredient IS NULL ORDER BY prod_name")
        dish_options = [row[0] for row in cursor.fetchall()]
    except Exception:
        # 備用方案
        dish_options = ["砂鍋菜(大碗)", "砂鍋菜(小碗)", "浮水魚羹","小菜-滷豆腐","小菜-泡菜","小菜-龍鬚菜","小菜-涼拌小黃瓜","小菜-切片豆干"]
    finally:
        conn.close()

    if not dish_options:
        st.warning("目前系統中無可選擇的成品品項。")
        return

    col1, col2 = st.columns(2)
    with col1:
        selected_scrap_dish = st.selectbox("選擇今日有剩餘、需報廢的料理品項", dish_options, key="scrap_dish_select")
    with col2:
        scrap_qty = st.number_input("報廢數量 (份/碗)", min_value=1, value=1, step=1, key="scrap_qty_input")
        
    scrap_reason = st.text_input("報廢備註說明", value="打烊未售完", key="scrap_reason_input")

    if st.button("❌ 確認報廢並扣除對應原物料", type="primary", use_container_width=True, key="scrap_submit_btn"):
        conn = get_db_conn()
        cursor = conn.cursor()
        try:
            # 1. 根據成品名稱找到 prod_id
            cursor.execute("SELECT prod_id FROM products WHERE prod_name = ?", (selected_scrap_dish,))
            dish_row = cursor.fetchone()
            
            if not dish_row:
                st.error("找不到該品項的系統 ID。")
                return
                
            target_dish_id = dish_row[0]
            
            # 2. 查出這個成品當初設定的 BOM 配方量
            cursor.execute("SELECT child_id, qty_needed FROM bom WHERE parent_id = ?", (target_dish_id,))
            bom_rows = cursor.fetchall()
            
            if not bom_rows:
                st.warning(f"⚠️ 警告：『{selected_scrap_dish}』尚未設定 BOM 配方表，無法自動扣減原物料！請先至產品設定確認。")
                return
            
            log_details = f"【打烊殘餘報廢】{selected_scrap_dish} x {scrap_qty}份。自動扣減："
            success_count = 0
            
            # 3. 逐一透過 FIFO 扣除原物料庫存
            for child_id, qty_needed in bom_rows:
                total_need = qty_needed * scrap_qty
                
                # 撈取原料名稱
                cursor.execute("SELECT prod_name FROM products WHERE prod_id = ?", (child_id,))
                p_name_row = cursor.fetchone()
                p_name = p_name_row[0] if p_name_row else f"未知原料(ID:{child_id})"
                
                # 呼叫系統內建的核心 FIFO 扣減函式
                success, deducted_cost_val, batch_list = deduct_stock_fifo(child_id, total_need, cursor)
                if success:
                    log_details += f" {p_name}(扣{total_need:.1f}),"
                    success_count += 1
            
            if success_count > 0:
                # 4. 寫入歷史審計軌跡
                log_history(current_user, "手動調整庫存-成品報廢", log_details + f" 原因：{scrap_reason}")
                conn.commit()
                
                # 強制刷新快取，確保調整完後即時在系統生效
                cached_fetch_unique_items_to_adjust.clear()
                cached_fetch_batches_by_prod.clear()
                
                st.toast(f"🗑️ 報廢成功！『{selected_scrap_dish}』x {scrap_qty} 份，相關原物料已從庫存扣除！", icon="✅")
                st.success(f"🎉 成功登記報廢！已自動透過 FIFO 扣除對應原物料，並寫入審計軌跡。")
            else:
                st.error("未能成功扣除任何原物料庫存，請檢查原料庫存量是否不足。")
                
        except Exception as e:
            conn.rollback()
            st.error(f"報廢失敗，錯誤原因：{e}")
        finally:
            conn.close()


# ==========================================
# 主要頁面佈局：使用功能分頁 (Tabs) 呼叫函式
# ==========================================
tab1, tab2 = st.tabs(["📋 一般原物料微調", "🥣 整鍋成品殘餘報廢"])

with tab1:
    # 呼叫獨立的原料調整 Fragment 函式
    render_stock_adjustment_zone(current_user)

with tab2:
    # 呼叫獨立的成品報廢 Fragment 函式
    render_dish_scrap_zone(current_user)