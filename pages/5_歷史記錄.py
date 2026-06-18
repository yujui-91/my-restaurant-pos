# pages/5_歷史記錄.py
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, timedelta
import streamlit as st

# 檢查 session_state 中的登入狀態，若未登入則阻斷畫面並提示
# if not st.session_state.get("password_correct", False):
#     st.warning("🔒 請先前往首頁登入管理系統！")
#     st.stop()
st.subheader("📜 歷史動作審計軌跡")

current_user = st.session_state.get('current_user', '老 闆')

# ==========================================
# 🔍 頂部複合篩選面板 (大方向動作篩選)
# ==========================================
col_f1, col_f2 = st.columns(2)

with col_f1:
    # 篩選器 1：時間區間篩選
    history_time_option = st.selectbox(
        "📅 選擇查看時間區間",
        ["今天", "過去 7 天", "過去 30 天", "自訂區間 (自選起訖日期)"],
        index=1,  # 修正防呆
        key="history_filter"
    )
    # 若不小心選到錯字，自動導回
    if history_time_option == "今天":
        pass
    elif history_time_option in ["過去 7 天"]:
        history_time_option = "過去 7 天"

# 處理時間區間邏輯
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
    # 💡 篩選器 2：高度歸納的「大方向動作」選單
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

# ==========================================
# 📊 依據「大方向」條件組合 SQL 撈出最終歷史紀錄（🔥 改為精準索引查詢）
# ==========================================
conn = sqlite3.connect("inventory.db")

sql_query = "SELECT timestamp AS 時間, user AS 操作人, action AS 動作, details AS 詳細說明 FROM history WHERE timestamp BETWEEN ? AND ?"
sql_params = [start_str, end_str]

# 核心優化：不再使用 LIKE 進行字串模糊匹配，改用全表主索引 main_category 進行等值精準查詢
if selected_main_action != "--- 全部動作項目 ---":
    sql_query += " AND main_category = ?"
    sql_params.append(selected_main_action)

sql_query += " ORDER BY id DESC"

df_hist = pd.read_sql_query(sql_query, conn, params=sql_params)
conn.close()

# ==========================================
# 🛠️ 核心改善：將詳細說明內干擾閱讀的 ||STRUCT_DATA|| JSON 字串切除
# ==========================================
if not df_hist.empty:
    df_hist['詳細說明'] = df_hist['詳細說明'].apply(lambda x: str(x).split("||STRUCT_DATA||")[0] if "||STRUCT_DATA||" in str(x) else x)

# ==========================================
# 📱 畫面呈現區 (細項與詳細說明在此輸出)
# ==========================================
if not df_hist.empty:
    st.metric("符合條件紀錄數", len(df_hist))
    
    use_mobile_view = st.toggle("📱 切換為手機/平板專用排版（防止長文字被遮擋）", value=False)
    
    if not use_mobile_view:
        # 電腦版檢視：大表格 (此時「詳細說明」欄位已無 JSON 干擾，非常乾淨整潔)
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
        # 手機/平板版檢視：直式卡片清單
        st.markdown("---")
        for idx, row in df_hist.iterrows():
            with st.expander(f"⏰ {row['時間']} | {row['動作']} ({row['操作人']})"):
                st.markdown("**📄 詳細更動軌跡說明：**")
                st.info(row['詳細說明'])
else:
    st.info("💡 目前此大方向篩選條件與時間區間內，沒有任何歷史操作紀錄。")