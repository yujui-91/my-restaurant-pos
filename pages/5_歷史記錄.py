# pages/5_歷史記錄.py
import streamlit as st
import pandas as pd
import sqlite3
import re
from datetime import datetime, timedelta
from database.db_core import get_db_conn
# 從 db_core 載入所需的快取函式
from database.db_core import cached_fetch_audit_history

st.subheader("📜 歷史動作審計軌跡")

current_user = st.session_state.get('current_user', '老 闆')

col_f1, col_f2 = st.columns(2)

with col_f1:
    history_time_option = st.selectbox(
        "📅 選擇查看時間區間",
        ["今天", "過去 7 天", "過去 30 天", "自訂區間 (自選起訖日期)"],
        index=1,
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

with col_f2:
    selected_main_action = st.selectbox(
        "⚡ 篩選大方向動作類別",
        [
            "--- 全部動作項目 ---",
            "🛒 餐點收銀結帳",
            "⚙️ 餐點參數修正",
            "📥 採購進貨登記",
            "💰 帳單費用登記",
            "📋 庫存微調/報廢/盤點"
        ],
        key="history_main_action_filter"
    )

st.caption(f"目前查看審計區間：{start_dt.strftime('%Y-%m-%d %H:%M:%S')} ～ {end_dt.strftime('%Y-%m-%d %H:%M:%S')}")

df_hist = cached_fetch_audit_history(start_str, end_str, selected_main_action)

# 建立將日誌文字內的目標歸帳月份轉換為中文顯示的輔助函式
def format_log_details_zh(text):
    if not isinstance(text, str):
        return text
    # 分離原本的結構化 JSON 資料（若存在）
    main_text = text.split("||STRUCT_DATA||")[0]
    
    # 透過正則表達式，將 "目標歸帳月份: YYYY-MM" 替換為 "目標歸帳月份: YYYY年M月"
    def repl(match):
        yr = match.group(1)
        mn = int(match.group(2))
        return f"目標歸帳月份: {yr}年{mn}月"
        
    return re.sub(r"目標歸帳月份:\s*(\d{4})-(\d{2})", repl, main_text)

if not df_hist.empty:
    # 套用中文格式化與移除結構化尾碼
    df_hist['詳細說明'] = df_hist['詳細說明'].apply(format_log_details_zh)

if not df_hist.empty:
    st.metric("符合條件紀錄數", len(df_hist))
    
    use_mobile_view = st.toggle("📱 切換為手機/平板專用排版（防止長文字被遮擋）", value=False)
    
    if not use_mobile_view:
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
        st.markdown("---")
        for idx, row in df_hist.iterrows():
            with st.expander(f"⏰ {row['時間']} | {row['動作']} ({row['操作人']})"):
                st.markdown("**📄 詳細更動軌跡說明：**")
                st.info(row['詳細說明'])
else:
    st.info("💡 目前此大方向篩選條件與時間區間內，沒有任何歷史操作紀錄。")