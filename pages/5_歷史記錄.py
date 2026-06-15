# pages/5_📜_歷史記錄.py
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, timedelta

st.subheader("📜 歷史動作審計軌跡")

history_time_option = st.selectbox(
    "📅 選擇查看時間區間",
    ["今天", "過去 7 天", "過去 30 天", "指定特定日期"],
    key="history_filter"
)

now = datetime.now()

if history_time_option == "今天":
    start_dt = now.replace(hour=0, minute=0, second=0)
    end_dt = now.replace(hour=23, minute=59, second=59)
elif history_time_option == "過去 7 天":
    start_dt = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0)
    end_dt = now
elif history_time_option == "過去 30 天":
    start_dt = (now - timedelta(days=30)).replace(hour=0, minute=0, second=0)
    end_dt = now
else:
    selected_date = st.date_input("請選擇日期", value=now.date(), key="history_date")
    start_dt = datetime.combine(selected_date, datetime.min.time())
    end_dt = datetime.combine(selected_date, datetime.max.time())

start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")

st.caption(f"目前查看區間：{start_dt.strftime('%Y-%m-%d')} ～ {end_dt.strftime('%Y-%m-%d')}")

conn = sqlite3.connect("inventory.db")
df_hist = pd.read_sql_query('''
    SELECT timestamp AS 時間, user AS 操作人, action AS 動作, details AS 詳細說明
    FROM history WHERE timestamp BETWEEN ? AND ? ORDER BY id DESC
''', conn, params=(start_str, end_str))
conn.close()

if not df_hist.empty:
    st.metric("符合條件紀錄數", len(df_hist))
    st.dataframe(df_hist, use_container_width=True, hide_index=True)
else:
    st.info("💡 此時間區間內沒有任何歷史操作紀錄。")