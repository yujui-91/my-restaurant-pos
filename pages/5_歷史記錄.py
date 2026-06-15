# pages/5_歷史記錄.py
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, timedelta

st.subheader("📜 歷史動作審計軌跡")

current_user = st.session_state.get('current_user', '老 闆')

# 選擇查看時間區間
history_time_option = st.selectbox(
    "📅 選擇查看時間區間",
    ["今天", "過去 7 天", "過去 30 天", "自訂區間 (自選起訖日期)"],
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
    c1, c2 = st.columns(2)
    with c1: start_date_input = st.date_input("開始日期", value=now.date() - timedelta(days=1), key="hist_start_day")
    with c2: end_date_input = st.date_input("結束日期", value=now.date(), key="hist_end_day")
    start_dt = datetime.combine(start_date_input, datetime.min.time())
    end_dt = datetime.combine(end_date_input, datetime.max.time())

start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")

st.caption(f"目前查看審計區間：{start_dt.strftime('%Y-%m-%d %H:%M:%S')} ～ {end_dt.strftime('%Y-%m-%d %H:%M:%S')}")

conn = sqlite3.connect("inventory.db")
df_hist = pd.read_sql_query('''
    SELECT timestamp AS 時間, user AS 操作人, action AS 動作, details AS 詳細說明
    FROM history WHERE timestamp BETWEEN ? AND ? ORDER BY id DESC
''', conn, params=(start_str, end_str))
conn.close()

if not df_hist.empty:
    st.metric("符合條件紀錄數", len(df_hist))
    
    # 📱 為手機/平板新增優化閱讀切換開關
    use_mobile_view = st.toggle("📱 切換為手機/平板專用排版（防止長文字被遮擋）", value=False)
    
    if not use_mobile_view:
        # 電腦版檢視：大表格
        st.data_editor(
            df_hist,
            column_config={
                "時間": st.column_config.TextColumn("時間", width="medium"),
                "操作人": st.column_config.TextColumn("操作人", width="small"),
                "動作": st.column_config.TextColumn("動作", width="medium"),
                "詳細說明": st.column_config.TextColumn("詳細說明 (包含數值更動軌跡)", width="large"),
            },
            disabled=True,
            use_container_width=True,
            hide_index=True,
            key="history_view_table"
        )
    else:
        # 手機/平板版檢視：直式卡片清單（文字會自動換行，絕對不會被看漏）
        st.markdown("---")
        for idx, row in df_hist.iterrows():
            with st.expander(f"⏰ {row['時間']} | {row['動作']} ({row['操作人']})"):
                st.markdown("**📄 詳細更動軌跡說明：**")
                # 使用 code 區塊或是一般 markdown 確保冗長文字在小螢幕也能完美呈現
                st.info(row['詳細說明'])
else:
    st.info("💡 此時間區間內沒有任何歷史操作紀錄。")