# pages/6_財務與消耗量報告.py
import streamlit as st
import pandas as pd
import sqlite3
import re
import json
import plotly.express as px
from datetime import datetime, timedelta
# ✨ 關鍵相容修正：引入 get_db_conn
from database.db_core import show_pending_toast, get_db_conn

# 檢查 session_state 中的登入狀態，若未登入則阻斷畫面並提示
# if not st.session_state.get("password_correct", False):
#     st.warning("🔒 請先前往首頁登入管理系統！")
#     st.stop()
show_pending_toast()

st.subheader("📊 門市營收、成本與損益分析報告")

# 加入手機模式切換開關
use_mobile_view = st.toggle("📱 切換為手機/平板專用排版", value=False, key="finance_mobile_toggle")

# ==========================================
# 🔍 頂部複合時間篩選面板
# ==========================================
report_option = st.selectbox(
    "📅 請選擇財務統計區間：", 
    ["今天", "過去 7 天", "過去 30 天", "自訂區間 (自選起訖日期)"], 
    key="finance_time_filter"
)

now = datetime.now()

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

current_ptr = start_date
covered_target_months = set()
while current_ptr <= end_date:
    covered_target_months.add(current_ptr.strftime("%Y-%m"))
    current_ptr += timedelta(days=1)

# ==========================================
# 📊 資料庫核心撈取與高速 SQL 底層聚合
# ==========================================
# ✨ 關鍵相容修正：改為 get_db_conn
conn = get_db_conn()
cursor = conn.cursor()

# 1. 營業額與精準成本摘要
cursor.execute('''
    SELECT COALESCE(SUM(total_revenue), 0.0) AS rev, COALESCE(SUM(total_cost), 0.0) AS cst
    FROM orders 
    WHERE status = 1 AND timestamp BETWEEN ? AND ?
''', (start_str, end_str))
r_summary = cursor.fetchall()
c_summary = [desc[0] for desc in cursor.description]
df_sales_summary = pd.DataFrame(r_summary, columns=c_summary)

total_revenue = float(df_sales_summary.iloc[0]['rev'])
total_food_cost = float(df_sales_summary.iloc[0]['cst'])

# 2. 餐點排行明細
cursor.execute('''
    SELECT oi.prod_name AS 餐點名稱, SUM(oi.qty) AS 銷售份數
    FROM order_items oi
    JOIN orders o ON oi.order_id = o.order_id
    WHERE o.status = 1 AND o.timestamp BETWEEN ? AND ?
    GROUP BY oi.prod_name
    ORDER BY 銷售份數 DESC
''', (start_str, end_str))
r_dish = cursor.fetchall()
c_dish = [desc[0] for desc in cursor.description]
df_dish_rank_raw = pd.DataFrame(r_dish, columns=c_dish)
dish_sales = dict(zip(df_dish_rank_raw['餐點名稱'], df_dish_rank_raw['銷售份數']))

# 3. 原物料消耗明細
cursor.execute('''
    SELECT om.mat_name AS 食材物料, SUM(om.qty) AS 消耗總數量
    FROM order_materials om
    JOIN orders o ON om.order_id = o.order_id
    WHERE o.status = 1 AND o.timestamp BETWEEN ? AND ?
    GROUP BY om.mat_name
    ORDER BY 消耗總數量 DESC
''', (start_str, end_str))
r_mat = cursor.fetchall()
c_mat = [desc[0] for desc in cursor.description]
df_mat_rank_raw = pd.DataFrame(r_mat, columns=c_mat)
material_usage = dict(zip(df_mat_rank_raw['食材物料'], df_mat_rank_raw['消耗總數量']))

# 4. 損耗記錄
cursor.execute('''
    SELECT action, details, timestamp FROM history 
    WHERE action LIKE '手動調整庫存-%'
''')
r_exp = cursor.fetchall()
c_exp = [desc[0] for desc in cursor.description]
df_expenses_raw = pd.DataFrame(r_exp, columns=c_exp)

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

# 5. 物料進貨明細
cursor.execute('''
    SELECT s.batch_id, s.prod_id, p.prod_name, s.original_qty, s.cost, s.inbound_date, p.purchase_unit
    FROM stock_batches s
    JOIN products p ON s.prod_id = p.prod_id
    WHERE (s.prod_id LIKE 'R%' OR s.prod_id LIKE 'S%' OR s.prod_id LIKE 'C%')
      AND s.inbound_date BETWEEN ? AND ?
    ORDER BY s.inbound_date DESC, s.batch_id DESC
''', (start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")))
r_actual = cursor.fetchall()
c_actual = [desc[0] for desc in cursor.description]
df_actual_purchase_details = pd.DataFrame(r_actual, columns=c_actual)

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

# 6. 固定費用特殊歸帳歷史
cursor.execute('''
    SELECT s.batch_id, s.prod_id, p.prod_name, s.qty, s.cost, s.inbound_date
    FROM stock_batches s
    JOIN products p ON s.prod_id = p.prod_id
    WHERE s.prod_id LIKE 'C%'
''')
r_cb = cursor.fetchall()
c_cb = [desc[0] for desc in cursor.description]
df_c_batches = pd.DataFrame(r_cb, columns=c_cb)

cursor.execute('''
    SELECT id, action, details, timestamp FROM history
    WHERE details LIKE '%目標歸帳月份:%'
    ORDER BY id ASC
''')
r_ch = cursor.fetchall()
c_ch = [desc[0] for desc in cursor.description]
df_c_history = pd.DataFrame(r_ch, columns=c_ch)
conn.close()

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
        expense_val = float(row['qty'] * row['cost'])
        if expense_val > 0:
            total_op_expense += expense_val
            c_expense_records.append({
                "費用項目": f"{row['prod_name']} (批次 {b_id})",
                "金額": expense_val
            })

gross_profit = total_revenue - total_food_cost
net_profit = gross_profit - total_op_expense - total_stock_loss

margin = (net_profit / total_revenue * 100) if total_revenue > 0 else 0.0
gross_margin = (gross_profit / total_revenue * 100) if total_revenue > 0 else 0.0

# ==========================================
# 🏪 面板呈現區 1：經營損益平衡總覽
# ==========================================
st.markdown("### 🧾 門市動態損益")
st.info(f"💡 **會計帳生效中：** 目前選擇的區間涵蓋了 {', '.join(covered_target_months)} 的帳單 顯示當前固定資產與費用。")

if use_mobile_view:
    row1_c1, row1_c2, row1_c3 = st.columns(3)
    with row1_c1: st.metric("🏪 營業總收入", f"${total_revenue:,.0f}")
    with row1_c2: st.metric("🥩 食材消耗成本", f"${total_food_cost:,.0f}")
    with row1_c3: st.metric("⚡ 帳單費用", f"${total_op_expense:,.1f}")
        
    row2_c1, row2_c2, row2_c3 = st.columns(3)
    with row2_c1: st.metric("📥 期間進貨總額", f"${total_purchase_cost:,.0f}")
    with row2_c2: st.metric("🔥 最終真實淨利", f"${net_profit:,.1f}")
    with row2_c3: st.metric("📈 門市淨利率", f"{margin:.1f}%")
else:
    a, b, c, po_box, d, e = st.columns(6)
    a.metric("🏪 營業總收入", f"${total_revenue:,.0f}")
    b.metric("🥩 食材消耗成本", f"${total_food_cost:,.0f}")
    c.metric("⚡ 帳單費用", f"${total_op_expense:,.1f}")
    po_box.metric("📥 期間進貨總額", f"${total_purchase_cost:,.0f}")  
    d.metric("🔥 最終真實淨利", f"${net_profit:,.1f}")
    e.metric("📈 門市淨利率", f"{margin:.1f}%")

st.divider()

# ==========================================
# 🏆 面板呈現區 2：餐點排行與原物料消耗
# ==========================================
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

# ==========================================
# 📥 面板呈現區 4：採購進貨明細追蹤
# ==========================================
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