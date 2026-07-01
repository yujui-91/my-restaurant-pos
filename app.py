import streamlit as st
import pandas as pd
from database.db_core import init_db, trigger_toast, show_pending_toast, log_history,auto_recovery_monitor
from database.db_core import get_db_conn,setup_sidebar
# 從 db_core 載入所需的快取函式
from database.db_core import (
    cached_fetch_safety_items,
    cached_fetch_low_stock_alerts,
    cached_fetch_merged_stock,
    cached_fetch_batch_details,
    cached_fetch_disabled_items_with_stock,
    cached_fetch_disabled_batches
)

st.set_page_config(layout="wide")
auto_recovery_monitor()
st.markdown("""
    <style>
        [data-testid="stDataFrame"] td, [data-testid="stDataFrame"] th {
            font-size: 15px !important;
            padding: 6px 8px !important;
        }
        .stAlert p {
            font-size: 15px !important;
            font-weight: 500;
        }
        .mobile-card {
            border: 1px solid #e6e6e6;
            border-radius: 8px;
            padding: 12px;
            margin-bottom: 10px;
            background-color: #ffffff;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }
        .mobile-card-disabled {
            border: 1px solid #ffcccc;
            border-radius: 8px;
            padding: 12px;
            margin-bottom: 10px;
            background-color: #fff2f2;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }
    </style>
""", unsafe_allow_html=True)

show_pending_toast()

st.title("🍳 赤山堡砂鍋 後台管理")

# --- 修改處：設定預設操作人員為「老闆娘」 ---
if 'current_user' not in st.session_state:
    st.session_state.current_user = "老闆娘"

setup_sidebar()
init_db()

st.sidebar.markdown("### 👤 操作人員設定")


st.sidebar.markdown("---")
st.sidebar.subheader("⚙️ 快速微調安全庫存線")

all_items_for_safety = cached_fetch_safety_items()

if not all_items_for_safety.empty:
    selected_safety_item = st.sidebar.selectbox("選擇調整品項", all_items_for_safety['prod_id'] + " - " + all_items_for_safety['prod_name'], key="sb_safety_item_box")
    target_safety_id = selected_safety_item.split(" - ")[0]
    matched_safety_row = all_items_for_safety[all_items_for_safety['prod_id'] == target_safety_id].iloc[0]
    
    new_safety_value = st.sidebar.number_input(
        f"設定最低安全線 ({matched_safety_row['use_unit']})", 
        min_value=0.0, 
        value=float(matched_safety_row['safety_stock']), 
        step=1.0, 
        key="sb_safety_num_input"
    )
    
    if st.sidebar.button("💾 儲存新安全線設定"):
        conn = get_db_conn()
        cursor = conn.cursor()
        cursor.execute("UPDATE products SET safety_stock = ? WHERE prod_id = ?", (new_safety_value, target_safety_id))
        conn.commit()
        conn.close()
        
        # 精準清除受影響的快取，避免影響其他無關頁面的讀取效能
        cached_fetch_safety_items.clear()
        cached_fetch_low_stock_alerts.clear()
        cached_fetch_merged_stock.clear()
        
        # 改善處：直接將 st.session_state.current_user 作為參數傳入
        log_history(
            st.session_state.current_user, 
            f"修正餐點參數-安全庫存變更", 
            f"操作人員微調了安全庫存線：【{matched_safety_row['prod_name']}】({target_safety_id})，新安全線設定為: {new_safety_value} {matched_safety_row['use_unit']}。",
            main_category="⚙️ 餐點參數修正"
        )
        
        trigger_toast(f"已將 【{matched_safety_row['prod_name']}】 的安全線更新為 {new_safety_value}", icon="⚙️")
        st.rerun()

df_alert_check = cached_fetch_low_stock_alerts()

if not df_alert_check.empty:
    alert_messages = []
    for _, row in df_alert_check.iterrows():
        alert_messages.append(f"【{row['prod_name']}】僅剩 {row['total_qty']:.1f}{row['use_unit']} (安全線: {row['safety_stock']:.1f})")
    st.warning("⚠️ **【低庫存補貨預警跑慢燈】** 🚨 " + " ｜ " + " ｜ ".join(alert_messages))

st.subheader("📊 目前庫存明細")

use_mobile_view = st.toggle("📱 切換為手機/平板專用排版", value=False, key="home_mobile_toggle")

stock_filter = st.selectbox("🔍 篩選庫存類別", ["顯示全部明細", "僅看食材 (R)", "僅看用品 (S)"], key="home_stock_filter")

df_merged_stock = cached_fetch_merged_stock(stock_filter)

if not df_merged_stock.empty:
    if use_mobile_view:
        for _, row in df_merged_stock.iterrows():
            card_class = "mobile-card" if row['狀態碼'] == 1 else "mobile-card-disabled"
            disabled_text = " (已下架)" if row['狀態碼'] == 0 else ""
            
            with st.container():
                st.markdown(f"""
                <div class="{card_class}">
                    <strong style='font-size:16px;'>【{row['編號']}】 {row['商品名稱']}{disabled_text}</strong>
                </div>
                """, unsafe_allow_html=True)
                
                col1, col2 = st.columns(2)
                with col1:
                    st.metric(label="目前庫存", value=f"{row['總庫存量']:,.1f} {row['單位']}")
                with col2:
                    st.metric(label="安全線", value=f"{row['安全庫存']:,.1f} {row['單位']}")
                
                col3, col4 = st.columns(2)
                with col3:
                    st.caption(f"單位成本: **${row['移動平均單位成本']:,.4f}**")
                with col4:
                    st.caption(f"庫存總價值: **${row['庫存總價值']:,.1f}**")
                    
                st.markdown("<div style='margin-bottom: 15px;'></div>", unsafe_allow_html=True)
    else:
        def highlight_disabled(row):
            styles = [''] * len(row)
            name_idx = row.index.get_loc('商品名稱')
            status_idx = row.index.get_loc('狀態碼')
            if row.iloc[status_idx] == 0:
                styles[name_idx] = 'background-color: #ffcccc; color: #cc0000; font-weight: bold;'
            return styles

        st.dataframe(
            df_merged_stock.style.apply(highlight_disabled, axis=1)
                         .format({"總庫存量": "{:,.1f}", "移動平均單位成本": "${:,.4f}", "庫存總價值": "${:,.1f}", "安全庫存": "{:,.1f}"}), 
            use_container_width=True, 
            column_config={
                "狀態碼": None,
                "編號": st.column_config.TextColumn("編號", width="small"),
                "商品名稱": st.column_config.TextColumn("商品名稱", width="medium"),
                "總庫存量": st.column_config.NumberColumn("總庫存量", width="small"),
                "單位": st.column_config.TextColumn("單位", width="small"),
                "移動平均單位成本": st.column_config.NumberColumn("單位成本", width="small"),
                "庫存總價值": st.column_config.NumberColumn("總價值", width="small"),
                "安全庫存": st.column_config.NumberColumn("安全線", width="small"),
            },
            hide_index=True
        )
    
    st.markdown("---")
    st.markdown("### 🔍 歷史進貨面板")
    
    valid_detail_items = df_merged_stock[df_merged_stock['總庫存量'] > 0]
    
    if not valid_detail_items.empty:
        selected_stock_item = st.selectbox(
            "🎯 請選取下方品項，系統將列出每一筆進貨批次明細：",
            valid_detail_items['編號'] + " - " + valid_detail_items['商品名稱']
        )
        
        target_prod_id = selected_stock_item.split(" - ")[0]
        df_batch_details = cached_fetch_batch_details(target_prod_id)
        
        matched_item_row = df_merged_stock[df_merged_stock['編號'] == target_prod_id].iloc[0]
        base_cost = matched_item_row['移動平均單位成本']
        unit_str = matched_item_row['單位']
        
        if not df_batch_details.empty:
            st.caption(f"💡 目前 【{selected_stock_item}】 共由以下 {len(df_batch_details)} 個有效進貨批次組成")
            
            st.dataframe(
                df_batch_details.style.format({"剩餘庫存量": f"{{:,.1f}} {unit_str}", "當次進貨總金額": "${:,.1f}"}),
                use_container_width=True,
                column_config={
                    "批次編號": st.column_config.NumberColumn("批次", width="small"),
                    "進貨日期": st.column_config.TextColumn("進貨日期", width="small"),
                    "剩餘庫存量": st.column_config.TextColumn("在庫數量", width="small"),
                    "當次進貨總金額": st.column_config.NumberColumn("當次剩餘總價值", width="small"),
                    "有效期限": st.column_config.TextColumn("效期", width="small"),
                    "原始供應商": st.column_config.TextColumn("原始供應商", width="medium"),
                    "供應商電話": st.column_config.TextColumn("聯絡電話", width="medium"),
                },
                hide_index=True
            )
            
            st.info(f"此品項目前的「加權移動平均單位成本」為 **${base_cost:,.4f}** / {unit_str}。")
        else:
            st.info("該品項目前無有效批次庫存。")
            
    df_unique_disabled_items = cached_fetch_disabled_items_with_stock()
    
    if not df_unique_disabled_items.empty:
        st.markdown("---")
        st.markdown("##### 🗑️ 清理已下架品項的殘留庫存")
        
        disabled_item_options = df_unique_disabled_items.apply(lambda r: f"{r['prod_id']} - {r['prod_name']}", axis=1).tolist()
        selected_disabled_item_str = st.selectbox("🔍 1. 選取欲清理的下架商品/食材：", disabled_item_options, key="clean_disabled_item_box")
        target_disabled_prod_id = selected_disabled_item_str.split(" - ")[0]
        
        df_disabled_batches = cached_fetch_disabled_batches(target_disabled_prod_id)
        
        if not df_disabled_batches.empty:
            def format_batch_label(r):
                exp_label = r['expiry_date'] if (r['expiry_date'] and r['expiry_date'].strip() != "") else "無填寫"
                return f"【批次 {int(r['batch_id'])}】進貨日: {r['inbound_date']} | 有效日期: {exp_label} | 殘留數量: {r['qty']}{r['use_unit']}"
            
            batch_options = df_disabled_batches.apply(format_batch_label, axis=1).tolist()
            selected_batch_str = st.selectbox("🎯 2. 選擇欲清空歸零的特定殘留批次：", batch_options, key="clean_disabled_batch_box")
            
            target_batch_id = int(selected_batch_str.split("【批次 ")[1].split("】")[0])
            matched_del_row = df_disabled_batches[df_disabled_batches['batch_id'] == target_batch_id].iloc[0]
            item_name = selected_disabled_item_str.split(" - ")[1]
            
            if st.button("❌ 確認將此下架批次數量歸零（移出明細）", type="primary", key="clean_disabled_submit_btn"):
                conn = get_db_conn()
                cursor = conn.cursor()
                new_orig_qty = max(0.0, float(matched_del_row['original_qty']) - float(matched_del_row['qty']))
                cursor.execute("UPDATE stock_batches SET qty = 0, original_qty = ? WHERE batch_id = ?", (new_orig_qty, target_batch_id))
                conn.commit()
                conn.close()
                
                # 精準清除清理殘留庫存所影響的快取函式
                cached_fetch_merged_stock.clear()
                cached_fetch_batch_details.clear()
                cached_fetch_disabled_items_with_stock.clear()
                cached_fetch_disabled_batches.clear()
                cached_fetch_low_stock_alerts.clear()
                
                log_history(
                    st.session_state.current_user, 
                    "手動調整庫存-下架殘留清理", 
                    f"清理了已下架品項的殘留庫存量：{item_name} (批次:{target_batch_id}，原數量:{matched_del_row['qty']}{matched_del_row['use_unit']}，歷史 original_qty 已修正為已消耗量: {new_orig_qty}{matched_del_row['use_unit']})",
                    main_category="📋 庫存微調/報廢/盤點"
                )
                
                trigger_toast(f"已成功將 【{item_name}】 批次 {target_batch_id} 的庫存量歸零清除，並重整原始登記量！", icon="🗑️")
                st.rerun()
        else:
            st.info("該品項目前無有效殘留批次。")
else:
    st.info("目前此類別無庫存，請先辦理採購進貨。")