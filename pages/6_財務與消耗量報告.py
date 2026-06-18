# pages/6_財務與消耗量報告.py
import streamlit as st
import pandas as pd
import sqlite3
import re
import json
import plotly.express as px
from datetime import datetime, timedelta
from database.db_core import show_pending_toast

show_pending_toast()

st.subheader("📊 門市營收、成本與損益分析報告")

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
# 📊 資料庫核心撈取與智慧歸帳解析
# ==========================================
conn = sqlite3.connect('inventory.db')

# 只撈取「作廢動作發生在當前查詢區間內」的紀錄，用來扣減當日營收
df_current_void_logs = pd.read_sql_query('''
    SELECT id, action, details, timestamp FROM history 
    WHERE action = '訂單作廢成功'
      AND timestamp BETWEEN ? AND ?
''', conn, params=(start_str, end_str))

# 將時間過濾完全交給下方的 Python 歸帳邏輯處理，確保跨日更正 100% 能夠對上。
df_history_sales = pd.read_sql_query('''
    SELECT id, action, details, timestamp FROM history 
    WHERE action IN ('多品項收銀結帳', '更正點餐數量', '多品項收銀結帳-已微調更正')
''', conn)

df_expenses_raw = pd.read_sql_query('''
    SELECT action, details, timestamp FROM history 
    WHERE action LIKE '手動調整庫存-%' OR action LIKE '採購進貨-%' OR action = '採購單更正'
''', conn)

# 【修改點】撈取當前選擇時間區間內的精準實際進貨歷史明細（支援 R、S、C 所有大類）
df_actual_purchase_details = pd.read_sql_query('''
    SELECT s.batch_id, s.prod_id, p.prod_name, s.original_qty, s.cost, s.inbound_date, p.purchase_unit
    FROM stock_batches s
    JOIN products p ON s.prod_id = p.prod_id
    WHERE (s.prod_id LIKE 'R%' OR s.prod_id LIKE 'S%' OR s.prod_id LIKE 'C%')
      AND s.inbound_date BETWEEN ? AND ?
    ORDER BY s.inbound_date DESC, s.batch_id DESC
''', conn, params=(start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")))

conn.close()

total_revenue = 0.0
total_food_cost = 0.0
total_op_expense = 0.0
total_stock_loss = 0.0
total_purchase_cost = 0.0  

# 計算進貨總金額與明細建立
purchase_records = []
for _, row in df_actual_purchase_details.iterrows():
    this_purchase_amt = float(row['original_qty'] * row['cost'])
    total_purchase_cost += this_purchase_amt
    
    # 根據代號前綴給予易讀的分類標籤
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

dish_sales = {}
material_usage = {}

# --- 處理正向銷售營收（修正跨日微調 Bug 版） ---
for _, row in df_history_sales.iterrows():
    if row['action'] == '多品項收銀結帳-已微調更正':
        continue
        
    txt = row['details']
    
    if "||STRUCT_DATA||" in txt:
        try:
            json_part = txt.split("||STRUCT_DATA||")[1]
            payload = json.loads(json_part)
            
            record_target_time = payload.get("orig_timestamp", row['timestamp'])
            if not (start_str <= record_target_time <= end_str):
                continue  
                
            total_revenue += float(payload.get("total_revenue", 0.0))
            total_food_cost += float(payload.get("total_cost", 0.0))
            
            for d in payload.get("dishes", []):
                d_name = d.get("prod_name")
                d_qty = float(d.get("qty", 0.0))
                if d_name:
                    dish_sales[d_name] = dish_sales.get(d_name, 0.0) + d_qty
                    
            for m in payload.get("materials", []):
                m_name = m.get("mat_name")
                m_qty = float(m.get("qty", 0.0))
                if m_name:
                    material_usage[m_name] = material_usage.get(m_name, 0.0) + m_qty
            continue
        except:
            pass

    if not (start_str <= row['timestamp'] <= end_str):
        continue

    revenue_match = re.search(r"總金額 \$(\d+\.?\d*)", txt)
    if revenue_match:
        total_revenue += float(revenue_match.group(1))
        
    cost_match = re.search(r"精準食材成本 \$([\d\.]+)", txt)
    if cost_match:
        total_food_cost += float(cost_match.group(1))
        
    dish_items = re.findall(r"【(.+?) x ([\d\.]+)份】", txt)
    for dish_name, qty_val in dish_items:
        dish_sales[dish_name] = dish_sales.get(dish_name, 0.0) + float(qty_val)
        
    if "消耗食材:" in txt:
        mats_part = txt.split("消耗食材:")[1].strip()
        mats_list = mats_part.split(", ")
        for m_str in mats_list:
            match = re.match(r"([^\s_]+)_([RS]\d+)\(([\d\.]+)([^\)]+)\)", m_str)
            if match:
                m_name = match.group(1)
                m_qty = float(match.group(3))
                material_usage[m_name] = material_usage.get(m_name, 0.0) + m_qty

# --- 處理當期作廢扣減 ---
for _, row in df_current_void_logs.iterrows():
    txt = row['details']
    if "||STRUCT_DATA||" in txt:
        try:
            json_part = txt.split("||STRUCT_DATA||")[1]
            payload = json.loads(json_part)
            total_revenue -= float(payload.get("total_revenue", 0.0))
            total_food_cost -= float(payload.get("total_cost", 0.0))
            for d in payload.get("dishes", []):
                d_name = d.get("prod_name")
                d_qty = float(d.get("qty", 0.0))
                if d_name:
                    dish_sales[d_name] = dish_sales.get(d_name, 0.0) - d_qty
            for m in payload.get("materials", []):
                m_name = m.get("mat_name")
                m_qty = float(m.get("qty", 0.0))
                if m_name:
                    material_usage[m_name] = material_usage.get(m_name, 0.0) - m_qty
            continue
        except:
            pass

    revenue_match = re.search(r"總金額 \$(\d+\.?\d*)", txt)
    if revenue_match:
        total_revenue -= float(revenue_match.group(1))
    cost_match = re.search(r"精準食材成本 \$([\d\.]+)", txt)
    if cost_match:
        total_food_cost -= float(cost_match.group(1))
    dish_items = re.findall(r"【(.+?) x ([\d\.]+)份】", txt)
    for dish_name, qty_val in dish_items:
        dish_sales[dish_name] = dish_sales.get(dish_name, 0.0) - float(qty_val)
    if "消耗食材:" in txt:
        mats_part = txt.split("消耗食材:")[1].strip()
        mats_list = mats_part.split(", ")
        for m_str in mats_list:
            match = re.match(r"([^\s_]+)_([RS]\d+)\(([\d\.]+)([^\)]+)\)", m_str)
            if match:
                m_name = match.group(1)
                m_qty = float(match.group(3))
                material_usage[m_name] = material_usage.get(m_name, 0.0) - m_qty

dish_sales = {k: v for k, v in dish_sales.items() if v > 0}
material_usage = {k: v for k, v in material_usage.items() if v > 0}

# --- 費用與帳單歸帳資料處理 ---
c_expense_records = []
for _, row in df_expenses_raw.iterrows():
    if "手動調整庫存" in row['action'] and "品項:C" not in row['action']:
        details = row['details']
        timestamp_str = row['timestamp']
        if start_str <= timestamp_str <= end_str:
            amt_match = re.search(r"總值變動:?\s*\$?(-?[\d\.]+)", details)
            if amt_match:
                change_amt = float(amt_match.group(1))
                total_stock_loss += abs(change_amt) if change_amt < 0 else 0.0

conn = sqlite3.connect('inventory.db')
df_c_batches = pd.read_sql_query('''
    SELECT s.batch_id, s.prod_id, p.prod_name, s.qty, s.cost, s.inbound_date
    FROM stock_batches s
    JOIN products p ON s.prod_id = p.prod_id
    WHERE s.prod_id LIKE 'C%'
''', conn)

df_c_history = pd.read_sql_query('''
    SELECT id, action, details, timestamp FROM history
    WHERE details LIKE '%目標歸帳月份:%'
    ORDER BY id ASC
''', conn)
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
left_col, right_col = st.columns(2)

with left_col:
    st.markdown("### 餐點銷售排行")
    if dish_sales:
        rank_df = pd.DataFrame(dish_sales.items(), columns=["餐點名稱", "銷售份數"]).sort_values(by="銷售份數", ascending=False)
        st.dataframe(rank_df, hide_index=True, use_container_width=True)
    else:
        st.info("💡 當前選定期間內尚無餐點銷售紀錄。")

with right_col:
    st.markdown("### 原物料消耗排行")  
    if material_usage:
        mat_df = pd.DataFrame(material_usage.items(), columns=["食材物料", "消耗總數量"]).sort_values(by="消耗總數量", ascending=False)
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
    # 【新增分類篩選組件】
    filter_cate = st.radio(
        "📂 依分類篩選進貨明細：",
        ["顯示全部", "食材 (R)", "用品 (S)" ,"營運帳單 (C)"],
        horizontal=True,
        key="purchase_category_filter"
    )
    
    df_purchase_view = pd.DataFrame(purchase_records)
    
    # 進行前端 DataFrame 的過濾處理
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