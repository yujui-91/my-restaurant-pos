# pages/6_財務與消耗量報告.py
from datetime import datetime, timedelta
import re
import sqlite3
import pandas as pd
import streamlit as st

st.subheader("📊 門市商業智能：營收、成本與損益分析")

report_option = st.selectbox(
    "📅 選擇統計區間", ["今天", "過去 7 天", "過去 30 定", "自訂區間 (自選起訖日期)"], key="finance_time"
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
        start_date = st.date_input(
            "自訂開始日期", value=now.date() - timedelta(days=1), key="finance_start"
        )
    with c2:
        end_date = st.date_input(
            "自訂結束日期", value=now.date(), key="finance_end"
        )

start_str = datetime.combine(start_date, datetime.min.time()).strftime(
    "%Y-%m-%d %H:%M:%S"
)
end_str = datetime.combine(end_date, datetime.max.time()).strftime(
    "%Y-%m-%d %H:%M:%S"
)
inbound_start = start_date.strftime("%Y-%m-%d")
inbound_end = end_date.strftime("%Y-%m-%d")

st.caption(f"統計區間：{start_date} ～ {end_date}")

conn = sqlite3.connect("inventory.db")
# 1. 讀取前台餐點收銀結帳紀錄
df_sales = pd.read_sql_query(
    """
    SELECT details FROM history WHERE action LIKE '餐點收銀結帳-%' AND timestamp BETWEEN ? AND ?
""",
    conn,
    params=(start_str, end_str),
)

# 2. 讀取固定帳單費用 (C開頭)
df_bill = pd.read_sql_query(
    """
    SELECT p.prod_name, SUM(s.qty * p.cost) amount FROM stock_batches s
    JOIN products p ON s.prod_id = p.prod_id
    WHERE p.prod_id LIKE 'C%' AND s.inbound_date BETWEEN ? AND ? GROUP BY p.prod_id
""",
    conn,
    params=(inbound_start, inbound_end),
)

# 改善需求 3：撈取此段時間區間內，系統真正「進貨登記」的所有歷史批次資料，用以解析實際進貨大包裝總金額
df_purchase_history = pd.read_sql_query(
    """
    SELECT s.prod_id, s.qty, p.cost, p.prod_name
    FROM stock_batches s
    JOIN products p ON s.prod_id = p.prod_id
    WHERE s.inbound_date BETWEEN ? AND ? AND (s.prod_id LIKE 'R%' OR s.prod_id LIKE 'S%')
""",
    conn,
    params=(inbound_start, inbound_end),
)
conn.close()

# 計算進貨成本 (拆分食材 R 與 用品 S)
total_purchase_r = 0.0
total_purchase_s = 0.0

for _, row in df_purchase_history.iterrows():
    p_id = row["prod_id"]
    # 進貨當下的批次總金額 = 小單位數量 * 移動平均單價
    p_amount = float(row["qty"]) * float(row["cost"])
    if p_id.startswith('R'):
        total_purchase_r += p_amount
    elif p_id.startswith('S'):
        total_purchase_s += p_amount

total_purchase_all = total_purchase_r + total_purchase_s

total_revenue, total_food_cost, total_bill = 0, 0, 0
material_usage, dish_sales = {}, {}

for _, row in df_sales.iterrows():
    txt = row["details"]

    # 1. 營業額解析
    revenue_match = re.search(r"總金額 \$(\d+\.?\d*)", txt)
    if revenue_match:
        total_revenue += float(revenue_match.group(1))

    # 2. 食材精準移動加權歷史實際成本解析
    cost_match = re.search(r"精準食材成本 \$(\d+\.?\d*)", txt)
    if cost_match:
        total_food_cost += float(cost_match.group(1))

    # 3. 成品餐點銷售數量排行解析
    dish_match = re.search(r"前台銷售「(.+?) × ([\d\.]+) 份」", txt)
    if dish_match:
        dish_name = dish_match.group(1)
        qty = float(dish_match.group(2))
        dish_sales[dish_name] = dish_sales.get(dish_name, 0) + qty

    # 食材圓餅圖精確限制字元解析
    mats = re.findall(r"([^\s_,「」（）()]+)_([RS]\d+)\(([\d\.]+)", txt)
    for m_name, m_id, qty_val in mats:
        qty_val = float(qty_val)
        material_usage[m_name] = material_usage.get(m_name, 0) + qty_val

if not df_bill.empty:
    total_bill = float(df_bill["amount"].sum())

gross_profit = total_revenue - total_food_cost
net_profit = gross_profit - total_bill
margin = (net_profit / total_revenue) * 100 if total_revenue > 0 else 0

# 呈現核心關鍵指標
a, b, c, d, e = st.columns(5)
a.metric("🏪 營業額", f"${total_revenue:,.0f}")
b.metric("🥩 食材消耗成本", f"${total_food_cost:,.0f}")
c.metric("⚡ 固定帳單支出", f"${total_bill:,.0f}")
d.metric("🔥 門市純利", f"${net_profit:,.0f}")
e.metric("📈 淨利率", f"{margin:.1f}%")

# ==========================================
# 改善需求 3：新增獨立的進貨成本數據與現金流看板
# ==========================================
st.markdown("### 📥 期間採購進貨總支出統計（現金流參考指標）")
st.caption("💡 商學知識：開店利潤是以「食材實際消耗量」計算。下方進貨金額代表您本月『花費多少現金去補貨囤貨』，屬於現金流掌控指標。")
p_col1, p_col2, p_col3 = st.columns(3)
p_col1.metric("📦 總進貨補貨金額", f"${total_purchase_all:,.0f}")
p_col2.metric("🥬 食材類進貨 (R)", f"${total_purchase_r:,.0f}")
p_col3.metric("🥢 用品類進貨 (S)", f"${total_purchase_s:,.0f}")

st.divider()
left, right = st.columns(2)

with left:
    st.markdown("### 🍩 食材純原物料消耗占比")
    if material_usage:
        pie_df = pd.DataFrame(material_usage.items(), columns=["食材物料", "消耗總數量"])
        st.vega_lite_chart(
            pie_df,
            {
                "mark": "arc",
                "encoding": {
                    "theta": {"field": "消耗總數量", "type": "quantitative"},
                    "color": {"field": "食材物料", "type": "nominal"},
                },
            },
            use_container_width=True,
        )
    else:
        st.info("目前沒有食材消耗資料")

with right:
    st.markdown("### 🏆 成品餐點銷售排行")
    if dish_sales:
        rank_df = pd.DataFrame(dish_sales.items(), columns=["餐點名稱", "銷售份數"]).sort_values(
            "銷售份數", ascending=False
        )
        st.dataframe(rank_df, hide_index=True, use_container_width=True)
    else:
        st.info("目前沒有餐點銷售資料")

st.divider()
st.markdown("### 📌 門市商業會計損益摘要")
st.info(f"""
營業收入（客單進帳）：${total_revenue:,.0f}
－ 食材消耗成本（FIFO 精確扣料）：${total_food_cost:,.0f}
－ 固定帳單費用（水電瓦斯開銷）：${total_bill:,.0f}
＝ 最終真實淨利潤：${net_profit:,.0f}
門市最終淨利率：{margin:.1f}%

--------------------------------------------------
備視現金流狀況：
期間內補貨總採購金額（付給廠商的現金總額）：${total_purchase_all:,.0f}
""")