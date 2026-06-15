# pages/6_💰_財務與消耗量報告.py
import streamlit as st
import pandas as pd
import sqlite3
import re
from datetime import datetime, timedelta

st.subheader("📊 門市商業智能：營收、成本與損益分析")

report_option = st.selectbox(
    "📅 選擇統計區間",
    ["今天", "過去 7 天", "過去 30 天", "自訂區間 (自選起訖日期)"],
    key="finance_time"
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
    # 需求 4：同步改進財務報告日期自選面板
    c1, c2 = st.columns(2)
    with c1: start_date = st.date_input("自訂開始日期", value=now.date() - timedelta(days=1), key="finance_start")
    with c2: end_date = st.date_input("自訂結束日期", value=now.date(), key="finance_end")

start_str = datetime.combine(start_date, datetime.min.time()).strftime("%Y-%m-%d %H:%M:%S")
end_str = datetime.combine(end_date, datetime.max.time()).strftime("%Y-%m-%d %H:%M:%S")
inbound_start = start_date.strftime("%Y-%m-%d")
inbound_end = end_date.strftime("%Y-%m-%d")

st.caption(f"統計區間：{start_date} ～ {end_date}")

conn = sqlite3.connect("inventory.db")
df_sales = pd.read_sql_query('''
    SELECT details FROM history WHERE action LIKE '餐點收銀結帳-%' AND timestamp BETWEEN ? AND ?
''', conn, params=(start_str, end_str))

df_bill = pd.read_sql_query('''
    SELECT p.prod_name, SUM(s.qty * p.cost) amount FROM stock_batches s
    JOIN products p ON s.prod_id = p.prod_id
    WHERE p.prod_id LIKE 'C%' AND s.inbound_date BETWEEN ? AND ? GROUP BY p.prod_id
''', conn, params=(inbound_start, inbound_end))

cursor = conn.cursor()
total_revenue, total_food_cost, total_bill = 0, 0, 0
material_usage, dish_sales = {}, {}

for _, row in df_sales.iterrows():
    txt = row["details"]
    revenue_match = re.search(r'總金額 \$(\d+\.?\d*)', txt)
    if revenue_match:
        total_revenue += float(revenue_match.group(1))

    dish_match = re.search(r'前台銷售「(.+?) × ([\d\.]+) 份」', txt)
    if dish_match:
        dish_name = dish_match.group(1)
        qty = float(dish_match.group(2))
        dish_sales[dish_name] = dish_sales.get(dish_name, 0) + qty

    mats = re.findall(r'([^_]+)_(R\d+)\(([\d\.]+)', txt)
    for m_name, m_id, qty in mats:
        qty = float(qty)
        cursor.execute("SELECT cost FROM products WHERE prod_id=?", (m_id,))
        res = cursor.fetchone()
        if res:
            total_food_cost += qty * float(res[0])
        material_usage[m_name] = material_usage.get(m_name, 0) + qty

conn.close()

if not df_bill.empty:
    total_bill = float(df_bill["amount"].sum())

gross_profit = total_revenue - total_food_cost
net_profit = gross_profit - total_bill
margin = (net_profit / total_revenue) * 100 if total_revenue > 0 else 0

a, b, c, d, e = st.columns(5)
a.metric("🏪 營業額", f"${total_revenue:,.0f}")
b.metric("🥩 食材成本", f"${total_food_cost:,.0f}")
c.metric("⚡ 帳單支出", f"${total_bill:,.0f}")
d.metric("🔥 淨利", f"${net_profit:,.0f}")
e.metric("📈 毛利率", f"{margin:.1f}%")

st.divider()
left, right = st.columns(2)

with left:
    st.markdown("### 🍩 食材消耗占比")
    if material_usage:
        pie_df = pd.DataFrame(material_usage.items(), columns=["食材", "數量"])
        st.vega_lite_chart(pie_df, {
            "mark": "arc",
            "encoding": {
                "theta": {"field": "數量"},
                "color": {"field": "食材"}
            }
        }, use_container_width=True)
    else:
        st.info("目前沒有消耗資料")

with right:
    st.markdown("### 🏆 餐點銷售排行")
    if dish_sales:
        rank_df = pd.DataFrame(dish_sales.items(), columns=["餐點", "銷售份數"]).sort_values("銷售份數", ascending=False)
        st.dataframe(rank_df, hide_index=True, use_container_width=True)
    else:
        st.info("目前沒有銷售資料")

st.divider()
st.markdown("### 📌 損益摘要")
st.info(f"""
營收：${total_revenue:,.0f}
－ 食材成本：${total_food_cost:,.0f}
－ 固定帳單：${total_bill:,.0f}
＝ 最終淨利：${net_profit:,.0f}
淨利率：{margin:.1f}%
""")