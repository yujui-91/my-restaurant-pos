# pages/6_財務與消耗量報告.py
import streamlit as st
import pandas as pd
import sqlite3
import re
import json
import calendar
import plotly.express as px
from datetime import datetime, timedelta
from database.db_core import show_pending_toast, get_db_conn
# 從 db_core 載入所需的快取函式
from database.db_core import (
    cached_get_sales_summary,
    cached_get_dish_rank,
    cached_get_material_usage,
    cached_get_expenses_raw,
    cached_get_actual_purchase_details,
    cached_get_operational_expenses_base,
    get_taiwan_now
)

show_pending_toast()

st.subheader("📊 門市營收、成本與損益分析報告")

use_mobile_view = st.toggle("📱 切換為手機/平板專用排版", value=False, key="finance_mobile_toggle")

report_option = st.selectbox(
    "📅 請選擇財務統計區間：", 
    ["今天", "過去 7 天", "過去 30 天", "自訂區間 (自選起訖日期)"], 
    key="finance_time_filter"
)

now = get_taiwan_now()

# --- 日期判定邏輯 ---
if report_option == "今天":
    start_date = now.date()
    end_date = now.date()
elif report_option == "過去 7 天":
    start_date = (now - timedelta(days=7)).date()
    end_date = now.date()
elif report_option == "過去 30 天":
    start_date = (now - timedelta(days=30)).date()
    end_date = now.date()
else:
    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input("自訂開始日期", value=now.date() - timedelta(days=1), key="finance_start_day")
    with c2:
        end_date = st.date_input("自訂結束日期", value=now.date(), key="finance_end_day")

start_str = datetime.combine(start_date, datetime.min.time()).strftime("%Y-%m-%d %H:%M:%S")
end_str = datetime.combine(end_date, datetime.max.time()).strftime("%Y-%m-%d %H:%M:%S")

st.caption(f"📈 目前統計審計區間：{start_date} ～ {end_date}")

# 計算查詢區間內，每天分別落在哪個月份，用來做精確的天數均攤比對
query_days_by_month = {}
current_ptr = start_date
while current_ptr <= end_date:
    m_str = current_ptr.strftime("%Y-%m")
    query_days_by_month[m_str] = query_days_by_month.get(m_str, 0) + 1
    current_ptr += timedelta(days=1)

covered_target_months = set(query_days_by_month.keys())

df_sales_summary = cached_get_sales_summary(start_str, end_str)
total_revenue = float(df_sales_summary.iloc[0]['rev'])
total_food_cost = float(df_sales_summary.iloc[0]['cst'])

df_dish_rank_raw = cached_get_dish_rank(start_str, end_str)
dish_sales = dict(zip(df_dish_rank_raw['餐點名稱'], df_dish_rank_raw['銷售份數']))

df_mat_rank_raw = cached_get_material_usage(start_str, end_str)
material_usage = dict(zip(df_mat_rank_raw['食材物料'], df_mat_rank_raw['消耗總數量']))

# 報廢損失計算保留於背景（若其他功能需要），但不再計入最終真實淨利公式
df_expenses_raw = cached_get_expenses_raw()
total_stock_loss = 0.0
for _, row in df_expenses_raw.iterrows():
    if "品項:C" not in row['action']:
        details = row['details']
        target_month_match = re.search(r"目標歸帳月份:\s*(\d{4}-\d{2})", details)
        if target_month_match:
            assigned_month = target_month_match.group(1)
        else:
            try:
                assigned_month = datetime.strptime(row['timestamp'], "%Y-%m-%d %H:%M:%S").strftime("%Y-%m")
            except:
                assigned_month = ""
        
        if assigned_month in covered_target_months:
            amt_match = re.search(r"總值變動:?\s*\$?(-?[\d\.]+)", details)
            if amt_match:
                change_amt = float(amt_match.group(1))
                total_stock_loss += abs(change_amt) if change_amt < 0 else 0.0

df_actual_purchase_details = cached_get_actual_purchase_details(start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
total_purchase_cost = 0.0
purchase_records = []
for _, row in df_actual_purchase_details.iterrows():
    this_purchase_amt = float(row['original_qty'] * row['cost'])
    total_purchase_cost += this_purchase_amt
    
    p_id = row['prod_id']
    if p_id.startswith('R'):
        cate_label = "食材 (R)"
    elif p_id.startswith('C'):
        cate_label = "營運帳單 (C)"
    elif p_id.startswith('S'):
        cate_label = "用品 (S)"
    else:
        cate_label = "其他"

    purchase_records.append({
        "進貨日期": row['inbound_date'],
        "分類": cate_label,
        "品項編號": p_id,
        "商品名稱": row['prod_name'],
        "進貨總額": this_purchase_amt
    })

df_c_batches, df_c_history = cached_get_operational_expenses_base()

batch_target_months = {}
for _, log_row in df_c_history.iterrows():
    details = log_row['details']
    target_month_match = re.search(r"目標歸帳月份:\s*(\d{4}-\d{2})", details)
    if target_month_match:
        assigned_month = target_month_match.group(1)
        batch_ids_found = re.findall(r"賬單批次:\s*(\d+)|批次編號\s*(\d+)", details)
        for b_id_tuple in batch_ids_found:
            b_id_str = b_id_tuple[0] if b_id_tuple[0] else b_id_tuple[1]
            if b_id_str:
                batch_target_months[int(b_id_str)] = assigned_month

total_op_expense = 0.0
c_expense_records = []
for _, row in df_c_batches.iterrows():
    b_id = int(row['batch_id'])
    if b_id in batch_target_months:
        assigned_month = batch_target_months[b_id]
    else:
        try:
            assigned_month = datetime.strptime(row['inbound_date'], "%Y-%m-%d").strftime("%Y-%m")
        except:
            assigned_month = ""
            
    if assigned_month in covered_target_months:
        full_expense_val = float(row['qty'] * row['cost'])
        if full_expense_val > 0:
            # 實作天數均攤 (Pro-rata)
            try:
                yr, mn = map(int, assigned_month.split("-"))
                days_in_month = calendar.monthrange(yr, mn)[1]
            except:
                days_in_month = 30 # 備用防錯
            
            # 每日均攤費用
            daily_cost = full_expense_val / days_in_month
            # 乘上該月份在本次查詢區間內實際包含的天數
            days_in_query = query_days_by_month.get(assigned_month, 0)
            prorated_expense_val = daily_cost * days_in_query
            
            total_op_expense += prorated_expense_val
            c_expense_records.append({
                "費用項目": f"{row['prod_name']} (批次 {b_id})",
                "全月總額": full_expense_val,
                "期間均攤金額": prorated_expense_val
            })

# 最終真實淨利公式修改（現金流水制）：總營業額 - 當期進貨成本 - 帳單費用均攤
net_profit = total_revenue - total_purchase_cost - total_op_expense

st.markdown("### 橫 門市動態損益")
st.info(f"💡 **現金流水與均攤制生效中：** 目前選擇的區間涵蓋了 {', '.join(covered_target_months)} 的帳單，已依查詢天數比例精準均攤費用。")

if use_mobile_view:
    row1_c1, row1_c2, row1_c3 = st.columns(3)
    with row1_c1: st.metric("🏪 營業總收入", f"${total_revenue:,.0f}")
    with row1_c2: st.metric("🥩 食材消耗成本", f"${total_food_cost:,.0f}")
    with row1_c3: st.metric("⚡ 帳單費用 (月均攤)", f"${total_op_expense:,.1f}")
        
    row2_c1, row2_c2 = st.columns(2)
    with row2_c1: st.metric("📥 期間進貨總額", f"${total_purchase_cost:,.0f}")
    with row2_c2: st.metric("🔥 最終真實淨利(總收入-進貨-均分帳單費用)", f"${net_profit:,.1f}")
else:
    a, b, c, po_box, d = st.columns(5)
    a.metric("🏪 營業總收入", f"${total_revenue:,.0f}")
    b.metric("🥩 食材消耗成本", f"${total_food_cost:,.0f}")
    c.metric("⚡ 帳單費用 (月均攤)", f"${total_op_expense:,.1f}")
    po_box.metric("📥 期間進貨總額", f"${total_purchase_cost:,.0f}")  
    d.metric("🔥 最終真實淨利(總收入-進貨-均分帳單費用)", f"${net_profit:,.1f}")

st.divider()

if use_mobile_view:
    st.markdown("### 餐點銷售排行")
    if dish_sales:
        rank_df = pd.DataFrame(list(dish_sales.items()), columns=["餐點名稱", "銷售份數"]).sort_values(by="銷售份數", ascending=False)
        st.dataframe(rank_df, hide_index=True, use_container_width=True)
    else:
        st.info("💡 當前選定期間內尚無餐點銷售紀錄。")
        
    st.markdown("<br>", unsafe_allow_html=True)

    st.markdown("### 原物料消耗排行")  
    if material_usage:
        mat_df = pd.DataFrame(list(material_usage.items()), columns=["食材物料", "消耗總數量"]).sort_values(by="消耗總數量", ascending=False)
        st.dataframe(mat_df, hide_index=True, use_container_width=True)
    else:
        st.info("💡 當前選定期間內尚無食材消耗數據。")
else:
    left_col, right_col = st.columns(2)

    with left_col:
        st.markdown("### 餐點銷售排行")
        if dish_sales:
            rank_df = pd.DataFrame(list(dish_sales.items()), columns=["餐點名稱", "銷售份數"]).sort_values(by="銷售份數", ascending=False)
            st.dataframe(rank_df, hide_index=True, use_container_width=True)
        else:
            st.info("💡 當前選定期間內尚無餐點銷售紀錄。")

    with right_col:
        st.markdown("### 原物料消耗排行")  
        if material_usage:
            mat_df = pd.DataFrame(list(material_usage.items()), columns=["食材物料", "消耗總數量"]).sort_values(by="消耗總數量", ascending=False)
            st.dataframe(mat_df, hide_index=True, use_container_width=True)
        else:
            st.info("💡 當前選定期間內尚無食材消耗數據。")

st.divider()

st.markdown("### 📥 採購進貨明細追蹤")

if not purchase_records:
    st.info(f"💡 當前選定日期區間（{start_date} ～ {end_date}）內沒有任何物料採購進貨紀錄。")
else:
    filter_cate = st.radio(
        "📂 依分類篩選進貨明細：",
        ["顯示全部", "食材 (R)", "用品 (S)" ,"營運帳單 (C)"],
        horizontal=True,
        key="purchase_category_filter"
    )
    
    df_purchase_view = pd.DataFrame(purchase_records)
    if filter_cate != "顯示全部":
        df_purchase_view = df_purchase_view[df_purchase_view["分類"] == filter_cate]
        
    if df_purchase_view.empty:
        st.info(f"💡 當前選定區間內，沒有符合「{filter_cate}」的進貨明細。")
    else:
        st.dataframe(
            df_purchase_view,
            column_config={
                "進貨日期": st.column_config.TextColumn("進貨日期"),
                "分類": st.column_config.TextColumn("大類"),
                "品項編號": st.column_config.TextColumn("項目編號"),
                "商品名稱": st.column_config.TextColumn("進貨品項名稱"),
                "進貨總額": st.column_config.NumberColumn("當次採購金額 ($)", format="$%.1f")
            },
            use_container_width=True,
            hide_index=True
        )

# 【優化項目】新增精準重新整理按鈕，只刷新本財務頁面的快取數據，完全不卡前台
st.markdown("---")
if st.button("🔄 重新計算財務報表數據", use_container_width=True):
    cached_get_sales_summary.clear()
    cached_get_dish_rank.clear()
    cached_get_material_usage.clear()
    cached_get_expenses_raw.clear()
    cached_get_actual_purchase_details.clear()
    cached_get_operational_expenses_base.clear()
    st.rerun()