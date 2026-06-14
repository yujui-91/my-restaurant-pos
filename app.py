import streamlit as st
import pandas as pd
import sqlite3
import re
from datetime import datetime, timedelta

# ==========================================
# 0. 資料庫初始化
# ==========================================
def init_db():
    conn = sqlite3.connect('inventory.db')
    cursor = conn.cursor()
    
    # 1. 商品/物料資料表
    cursor.execute('''CREATE TABLE IF NOT EXISTS products (
                        prod_id TEXT PRIMARY KEY, 
                        prod_name TEXT, 
                        cost REAL, 
                        price REAL,
                        safety_stock REAL DEFAULT 0,
                        purchase_unit TEXT DEFAULT '',
                        use_unit TEXT DEFAULT '',
                        conversion_factor REAL DEFAULT 1.0)''')
    
    # 2. 庫存批次明細表（已擴充店家名稱與電話欄位）
    cursor.execute('''CREATE TABLE IF NOT EXISTS stock_batches (
                        batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        prod_id TEXT, 
                        qty REAL, 
                        expiry_date TEXT,
                        inbound_date TEXT,
                        vendor_name TEXT DEFAULT '',
                        vendor_phone TEXT DEFAULT '')''')
    
    # 3. BOM 組裝配方表
    cursor.execute('''CREATE TABLE IF NOT EXISTS bom (
                        parent_id TEXT, 
                        child_id TEXT, 
                        qty_needed REAL,
                        PRIMARY KEY (parent_id, child_id))''')
    
    # 4. 歷史紀錄表
    cursor.execute('''CREATE TABLE IF NOT EXISTS history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, 
                        timestamp TEXT, 
                        user TEXT, 
                        action TEXT, 
                        details TEXT)''')
    
    # 預設測試資料 (自動建立基準物料)
    cursor.execute("SELECT COUNT(*) FROM products")
    if cursor.fetchone()[0] == 0:
        today = datetime.now().strftime("%Y-%m-%d")
        exp_1 = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        exp_2 = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        
        # 初始物料：R 開頭為食材，S 開頭為用品，P 開頭為餐點成品
        cursor.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
            ('R001', '澳洲牛肉', 0.5, 0.0, 1000, '箱(20kg)', 'g', 20000.0),
            ('R002', '麵條', 5.0, 0.0, 50, '箱(100份)', '份', 100.0),
            ('R003', '高湯', 0.02, 0.0, 5000, '桶(20L)', 'ml', 20000.0),
            ('R004', '蔥花', 0.1, 0.0, 200, '袋(1kg)', 'g', 1000.0),
            ('S001', '外帶紙盒', 3.5, 0.0, 100, '束(50個)', '個', 50.0),
            ('S002', '免洗筷', 0.5, 0.0, 200, '包(100雙)', '雙', 100.0),
            ('P001', '招牌牛肉麵(成品)', 0.0, 180.0, 0, '碗', '碗', 1.0)
        ])
        
        # 初始庫存（最後兩個欄位分別帶入：店家名稱、店家電話）
        cursor.executemany("INSERT INTO stock_batches (prod_id, qty, expiry_date, inbound_date, vendor_name, vendor_phone) VALUES (?, ?, ?, ?, ?, ?)", [
            ('R001', 500, exp_1, today, '豪好吃肉品批發', '0912-345678'),   
            ('R001', 2000, exp_2, today, '豪好吃肉品批發', '0912-345678'),  
            ('R002', 60, exp_2, today, '大豐製麵廠', ''), # 電話留空測試
            ('R003', 10000, exp_2, today, '', ''),         # 店家皆留空測試
            ('R004', 150, exp_1, today, '全聯農產', '02-22334455'),
            ('S001', 100, '', today, '大同包裝材料行', '0988-111222'), # 用品通常無效期
            ('S002', 200, '', today, '大同包裝材料行', '0988-111222')
        ])
        
        # BOM 表（牛肉麵只會消耗 R 開頭的食材，不會消耗 S 開頭的用品）
        cursor.executemany("INSERT INTO bom VALUES (?, ?, ?)", [
            ('P001', 'R001', 150.0),
            ('P001', 'R002', 1.0),
            ('P001', 'R003', 300.0),
            ('P001', 'R004', 5.0)
        ])
        
    conn.commit()
    conn.close()

# 執行初始化
init_db()

def log_history(user, action, details):
    conn = sqlite3.connect('inventory.db')
    cursor = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("INSERT INTO history (timestamp, user, action, details) VALUES (?, ?, ?, ?)", (now, user, action, details))
    conn.commit()
    conn.close()

def deduct_stock_fifo(prod_id, qty_to_deduct, cursor):
    cursor.execute("SELECT batch_id, qty FROM stock_batches WHERE prod_id = ? AND qty > 0 ORDER BY expiry_date ASC, inbound_date ASC", (prod_id,))
    batches = cursor.fetchall()
    total_available = sum([b[1] for b in batches])
    if total_available < qty_to_deduct:
        return False, total_available
    
    remains = qty_to_deduct
    for batch_id, batch_qty in batches:
        if remains <= 0: break
        if batch_qty >= remains:
            cursor.execute("UPDATE stock_batches SET qty = qty - ? WHERE batch_id = ?", (remains, batch_id))
            remains = 0
        else:
            cursor.execute("UPDATE stock_batches SET qty = 0 WHERE batch_id = ?", (batch_id,))
            remains -= batch_qty
    cursor.execute("DELETE FROM stock_batches WHERE qty <= 0")
    return True, 0

def get_next_raw_id():
    conn = sqlite3.connect('inventory.db')
    cursor = conn.cursor()
    cursor.execute("SELECT prod_id FROM products WHERE prod_id LIKE 'R%'")
    ids = cursor.fetchall()
    conn.close()
    max_num = max([int(re.findall(r'\d+', pid)[0]) for (pid,) in ids if re.findall(r'\d+', pid)] + [0])
    return f"R{max_num + 1:03d}"

def get_next_dish_id():
    conn = sqlite3.connect('inventory.db')
    cursor = conn.cursor()
    cursor.execute("SELECT prod_id FROM products WHERE prod_id LIKE 'P%'")
    ids = cursor.fetchall()
    conn.close()
    max_num = max([int(re.findall(r'\d+', pid)[0]) for (pid,) in ids if re.findall(r'\d+', pid)] + [0])
    return f"P{max_num + 1:03d}"

def get_next_supply_id():
    conn = sqlite3.connect('inventory.db')
    cursor = conn.cursor()
    cursor.execute("SELECT prod_id FROM products WHERE prod_id LIKE 'S%'")
    ids = cursor.fetchall()
    conn.close()
    max_num = max([int(re.findall(r'\d+', pid)[0]) for (pid,) in ids if re.findall(r'\d+', pid)] + [0])
    return f"S{max_num + 1:03d}"

def get_next_bill_id():
    conn = sqlite3.connect('inventory.db')
    cursor = conn.cursor()
    cursor.execute("SELECT prod_id FROM products WHERE prod_id LIKE 'C%'")
    ids = cursor.fetchall()
    conn.close()
    max_num = max([int(re.findall(r'\d+', pid)[0]) for (pid,) in ids if re.findall(r'\d+', pid)] + [0])
    return f"C{max_num + 1:03d}"
# ==========================================
# 網頁介面設計
# ==========================================
st.set_page_config(layout="wide")
st.title("🍳 智能餐飲進銷存與精準成本分析系統")

st.sidebar.header("系統參數")
current_user = st.sidebar.text_input("操作人員", value="老  闆")

# 全局安全庫存預警
conn = sqlite3.connect('inventory.db')
df_alert_check = pd.read_sql_query('''
    SELECT p.prod_name, SUM(s.qty) as total_qty, p.safety_stock, p.use_unit
    FROM stock_batches s JOIN products p ON s.prod_id = p.prod_id
    GROUP BY s.prod_id HAVING total_qty < p.safety_stock
''', conn)
conn.close()

if not df_alert_check.empty:
    st.sidebar.subheader("⚠️ 缺貨補貨預警")
    for _, row in df_alert_check.iterrows():
        st.sidebar.error(f"【{row['prod_name']}】庫存僅剩 {row['total_qty']}{row['use_unit']} (安全線: {row['safety_stock']})")

tabs = st.tabs([
    "📊 即時庫存與精準直改", "🛒 POS 前台結帳單", "📝 採購進貨單", 
    "🛠️ 批次庫存調整", "📋 盤點與損耗分析", "📜 歷史記錄", "💰 財務與消耗量報告"
])

# ==========================================
# Tab 0: 目前庫存檢視 (升級：支援 R/S 分類篩選)
# ==========================================
with tabs[0]:
    st.subheader("📊 目前即時庫存明細 (依批次/效期)")
    
    # 新增分類篩選下拉選單
    stock_filter = st.selectbox("🔍 篩選庫存類別", ["顯示全部明細", "僅看食材 (R)", "僅看用品 (S)"])
    
    conn = sqlite3.connect('inventory.db')
    
    # 根據選單動態調整 SQL 語法
    if stock_filter == "僅看食材 (R)":
        query_condition = "WHERE s.prod_id LIKE 'R%'"
    elif stock_filter == "僅看用品 (S)":
        query_condition = "WHERE s.prod_id LIKE 'S%'"
    else:
        query_condition = "" # 全部顯示
        
    df_stock = pd.read_sql_query(f'''
        SELECT s.batch_id as 批次編號, s.prod_id as 編號, p.prod_name as 商品名稱, 
               s.qty as 庫存量, p.use_unit as 單位, s.expiry_date as 有效期限, 
               p.safety_stock as 安全庫存, s.vendor_name as 供應商, s.vendor_phone as 供應商電話
        FROM stock_batches s JOIN products p ON s.prod_id = p.prod_id 
        {query_condition}
        ORDER BY s.prod_id, s.expiry_date ASC
    ''', conn)
    conn.close()
    
    if not df_stock.empty:
        st.dataframe(df_stock, use_container_width=True)
    else:
        st.info("目前此類別無庫存，請先辦理採購進貨。")

# ==========================================
# Tab 1: POS 前台結帳 (全面升級：支援前台自由切換「台斤/公斤/公克」彈性加料)
# ==========================================
if 'current_recipe_list' not in st.session_state:
    st.session_state.current_recipe_list = []
if 'last_loaded_dish' not in st.session_state:
    st.session_state.last_loaded_dish = ""

with tabs[1]:
    st.subheader("🛒 前台收銀結帳系統 (二合一智能點餐：支援現場任意切換台斤/公斤/公克)")
    
    conn = sqlite3.connect('inventory.db')
    existing_dishes = pd.read_sql_query("SELECT prod_id, prod_name, price FROM products WHERE price > 0", conn)
    all_raw_df = pd.read_sql_query("SELECT prod_id, prod_name, use_unit, cost FROM products WHERE price = 0", conn)
    conn.close()
    
    st.markdown("##### 🔍 1. 請選取或打字輸入客人點購的餐點：")
    col_dish1, col_dish2, col_dish3 = st.columns(3)
    with col_dish1:
        dish_options = ["--- 請選擇菜單既有餐點 ---"] + existing_dishes['prod_name'].tolist()
        selected_dish_select = st.selectbox("【既有餐點】直接下拉點餐", dish_options, index=0)
    with col_dish2:
        selected_dish_input = st.text_input("【新創/臨時餐點】在此直接手寫品名 (例: 椒麻雞)", value="")
    with col_dish3:
        dish_sale_price = st.number_input("這道餐點的【販售價格】($)", min_value=0.0, value=0.0, step=10.0)

    # 判定點餐與配方加載邏輯
    if selected_dish_input.strip() != "":
        final_dish_name = selected_dish_input.strip()
        final_dish_id = get_next_dish_id()
        if st.session_state.last_loaded_dish != final_dish_id:
            st.session_state.current_recipe_list = [] 
            st.session_state.last_loaded_dish = final_dish_id
    elif selected_dish_select != "--- 請選擇菜單既有餐點 ---":
        final_dish_name = selected_dish_select
        matched_dish_row = existing_dishes[existing_dishes['prod_name'] == final_dish_name].iloc[0]
        final_dish_id = matched_dish_row['prod_id']
        if dish_sale_price == 0.0:
            dish_sale_price = float(matched_dish_row['price'])
            
        if st.session_state.last_loaded_dish != final_dish_id:
            conn = sqlite3.connect('inventory.db')
            db_recipe = pd.read_sql_query('''
                SELECT p.prod_name as 食材名稱, b.child_id as 食材編號, b.qty_needed as 單位用量, p.use_unit as 單位
                FROM bom b JOIN products p ON b.child_id = p.prod_id WHERE b.parent_id = ?
            ''', conn, params=(final_dish_id,))
            conn.close()
            st.session_state.current_recipe_list = db_recipe.to_dict(orient='records')
            st.session_state.last_loaded_dish = final_dish_id
    else:
        final_dish_name = ""
        final_dish_id = ""
        st.session_state.current_recipe_list = []

    st.markdown("---")
    st.markdown("##### ➕ 2. 現場食材加料/自訂配方調整區：")
    
    col_add1, col_add2, col_add3, col_add4 = st.columns([2, 1, 1, 1])
    with col_add1:
        add_mat_name = st.selectbox("選擇要加入/調整的食材名稱", ["--- 請選擇食材 ---"] + all_raw_df['prod_name'].tolist())
    
    # 建立系統支援的現場輸入單位選單
    input_unit_options = ["公克 (g)", "公斤 (kg)", "台斤", "毫升 (ml)", "公升 (L)", "個/顆/份"]
    
    with col_add2:
        chosen_input_unit = st.selectbox("本次輸入使用的單位", input_unit_options, index=0)
    with col_add3:
        add_mat_qty = st.number_input(f"單份餐點用量", min_value=0.0, value=0.0, step=1.0)
    with col_add4:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("➕ 加入配方清單"):
            if add_mat_name == "--- 請選擇食材 ---" or add_mat_qty <= 0:
                st.error("請選擇有效食材並輸入大於 0 的用量！")
            else:
                mat_info = all_raw_df[all_raw_df['prod_name'] == add_mat_name].iloc[0]
                db_system_unit = mat_info['use_unit'].lower() # 後台實體綁定的基準小單位
                
                # =======================================================
                # 🟢 兩階段智能單位換算矩陣：將前台任意單位標準化，再對齊後台
                # =======================================================
                current_input_unit = chosen_input_unit.strip()
                base_qty = add_mat_qty # 暫存標準化數值
                
                # 第一階段：統一將前台輸入換算為最底層的「公克(g)」或「毫升(ml)」
                if "公克" in current_input_unit or "(g)" in current_input_unit.lower():
                    base_qty = add_mat_qty * 1.0
                elif "公斤" in current_input_unit or "(kg)" in current_input_unit.lower():
                    base_qty = add_mat_qty * 1000.0
                elif "台斤" in current_input_unit:
                    base_qty = add_mat_qty * 600.0
                elif "公升" in current_input_unit or "(l)" in current_input_unit.lower():
                    base_qty = add_mat_qty * 1000.0
                elif "毫升" in current_input_unit or "(ml)" in current_input_unit.lower():
                    base_qty = add_mat_qty * 1.0

                # 第二階段：將底層標準值，依據後台設定反推回目標單位總量
                final_converted_qty = base_qty # 預設一對一 (例如：個/顆/份)
                sys_unit = db_system_unit.strip().lower()

                # 如果後台是重量系列基準
                if sys_unit in ['g', '公克', 'G']:
                    final_converted_qty = base_qty
                elif sys_unit in ['kg', '公斤', 'KG', 'KgCtrl + Shift + G']:
                    final_converted_qty = base_qty / 1000.0
                elif sys_unit in ['台斤','臺斤']:
                    final_converted_qty = base_qty / 600.0
                    
                # 如果後台是體積系列基準
                elif sys_unit in ['ml', '毫升']:
                    final_converted_qty = base_qty
                elif sys_unit in ['l', '公升']:
                    final_converted_qty = base_qty / 1000.0
                # =======================================================
                
                # 檢查暫存內是否已有該食材，有的話直接覆蓋用量
                existing_idx = next((i for i, item in enumerate(st.session_state.current_recipe_list) if item['食材編號'] == mat_info['prod_id']), None)
                new_item_dict = {
                    "食材名稱": mat_info['prod_name'],
                    "食材編號": mat_info['prod_id'],
                    "單位用量": final_converted_qty, # 存入後台認得的標準換算總量
                    "單位": mat_info['use_unit']
                }
                if existing_idx is not None:
                    st.session_state.current_recipe_list[existing_idx] = new_item_dict
                else:
                    st.session_state.current_recipe_list.append(new_item_dict)
                    
                st.success(f"調整成功！已自動將 {add_mat_qty} {chosen_input_unit} 換算為 {final_converted_qty} {mat_info['use_unit']} 併入配方！")
                st.rerun()

    # 顯示目前已勾選加入的精準配方表
    st.markdown("##### 📋 當前調配餐點的物料清單確認：")
    if st.session_state.current_recipe_list:
        # 將目前的 session_state 轉為 DataFrame 供編輯
        df_recipe_view = pd.DataFrame(st.session_state.current_recipe_list)
        
        # 1. 建立一個虛擬欄位「移除」，預設為 False
        df_recipe_view["移除"] = False
        
        # 2. 使用 st.data_editor 讓使用者可以直接線上修改
        edited_df = st.data_editor(
            df_recipe_view,
            column_config={
                "食材編號": st.column_config.TextColumn("食材編號", disabled=True),
                "食材名稱": st.column_config.TextColumn("食材名稱", disabled=True),
                "單位用量": st.column_config.NumberColumn("單位用量 (可雙擊修改)", min_value=0.0001, step=0.1, format="%.4f"),
                "單位": st.column_config.TextColumn("單位", disabled=True),
                "移除": st.column_config.CheckboxColumn("勾選移除", default=False)
            },
            disabled=["食材編號", "食材名稱", "單位"], # 限制只能改用量與移除
            key="recipe_editor",
            use_container_width=True
        )
        
        # 3. 檢查使用者是否有進行「更正（刪除或改量）」
        has_changes = False
        new_recipe_list = []
        
        for idx, row in edited_df.iterrows():
            if row["移除"]:
                has_changes = True
                continue # 跳過此筆，等於刪除
            
            # 檢查數量是否有被調動
            original_qty = st.session_state.current_recipe_list[idx]["單位用量"]
            if row["單位用量"] != original_qty:
                has_changes = True
            
            new_recipe_list.append({
                "食材名稱": row["食材名稱"],
                "食材編號": row["食材編號"],
                "單位用量": float(row["單位用量"]),
                "單位": row["單位"]
            })
            
        # 若有更正，即時更新 session_state 並刷新頁面
        if has_changes:
            st.session_state.current_recipe_list = new_recipe_list
            st.toast("✏️ 配方清單已即時更正！")
            st.rerun()

        # ---- 以下維持你原有的成本計算與扣料邏輯 ----
        dish_calculated_cost_single = 0.0
        for item in st.session_state.current_recipe_list:
            c_cost = all_raw_df[all_raw_df['prod_id'] == item['食材編號']]['cost'].values[0]
            dish_calculated_cost_single += item['單位用量'] * c_cost
            
        sale_qty = st.number_input("客人本次點購總數量 (份)", min_value=1, value=1)
        
        if final_dish_name != "":
            st.markdown(f"""
            > 💰 **餐點毛利核算面版：**
            > * 餐點名稱：**{final_dish_name}** | 單份預估食材成本：**${dish_calculated_cost_single:,.2f} 元**
            > * 本次總銷售額：**${dish_sale_price * sale_qty:,.0f} 元**
            """)
            
        if st.button("🔥 確認送出收銀結帳（執行扣料）"):
            if dish_sale_price <= 0:
                st.error("❌ 請輸入大於 0 的餐點販售價格！")
            else:
                conn = sqlite3.connect('inventory.db')
                cursor = conn.cursor()
                
                cursor.execute('''INSERT OR REPLACE INTO products (prod_id, prod_name, cost, price, safety_stock, purchase_unit, use_unit, conversion_factor)
                                  VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', (final_dish_id, final_dish_name, dish_calculated_cost_single, dish_sale_price, 0, '份', '份', 1.0))
                
                insufficient = False
                insufficient_msg = ""
                for item in st.session_state.current_recipe_list:
                    total_need = item['單位用量'] * sale_qty
                    cursor.execute("SELECT SUM(qty) FROM stock_batches WHERE prod_id = ?", (item['食材編號'],))
                    current_stock = cursor.fetchone()[0] or 0
                    if current_stock < total_need:
                        insufficient = True
                        insufficient_msg += f" ❌ 庫存不足：【{item['食材名稱']}】需要 {total_need}，目前僅剩 {current_stock}！\n"
                        
                if insufficient:
                    st.error(insufficient_msg)
                    conn.close()
                else:
                    cursor.execute("DELETE FROM bom WHERE parent_id = ?", (final_dish_id,))
                    details_log = f"前台銷售「{final_dish_name} × {sale_qty} 份」，總金額 ${dish_sale_price * sale_qty}。"
                    log_details_list = []
                    
                    for item in st.session_state.current_recipe_list:
                        total_need = item['單位用量'] * sale_qty
                        deduct_stock_fifo(item['食材編號'], total_need, cursor)
                        log_details_list.append(f"{item['食材名稱']}_{item['食材編號']}({total_need}{item['單位']})")
                        cursor.execute("INSERT INTO bom VALUES (?, ?, ?)", (final_dish_id, item['食材編號'], item['單位用量']))
                        
                    conn.commit()
                    conn.close()
                    log_history(current_user, f"餐點收銀結帳-{final_dish_name}", details_log + " 消耗食材: " + ", ".join(log_details_list))
                    st.success(f"🎉【收銀結帳已完成】餐點「{final_dish_name} × {sale_qty}」收銀完成！後台已完成 FIFO 食材扣料。")
                    st.session_state.current_recipe_list = [] 
                    st.rerun()
    else:
        st.info("💡 請利用上方選單，開始為餐點添加原物料配方比例。")

# ==========================================
# Tab 2: 採購進貨與費用登記 (升級：支援帳單 C 分流、簡化欄位)
# ==========================================
with tabs[2]:
    st.subheader("📝 採購進貨與費用登記單")
    
    # 新增 C 分類選擇
    item_type = st.radio("✨ 請選擇本次登記類別：", ["食材 (R 開頭)", "用品 (S 開頭)", "帳單費用 (C 開頭，如水電瓦斯)"], horizontal=True)
    
    if "食材" in item_type:
        prefix = 'R'
    elif "用品" in item_type:
        prefix = 'S'
    else:
        prefix = 'C'
    
    conn = sqlite3.connect('inventory.db')
    existing_items_df = pd.read_sql_query(
        "SELECT prod_id, prod_name, purchase_unit, use_unit, conversion_factor, safety_stock FROM products WHERE prod_id LIKE ?", 
        conn, params=(f"{prefix}%",)
    )
    conn.close()
    
    st.markdown("##### 🔍 1. 品項選取（二選一）：")
    col_choice1, col_choice2 = st.columns(2)
    with col_choice1:
        options_list = [f"--- 請選擇已建立的{item_type[:2]} ---"] + (existing_items_df['prod_name'].tolist())
        chosen_select_name = st.selectbox("【重複登記】從這裡直接下拉搜尋既有品項", options_list, index=0)
    with col_choice2:
        chosen_input_name = st.text_input(f"【首次登記】在此直接手動打字輸入新{item_type[:2]}名稱 (如：水費)", value="")

    if chosen_input_name.strip() != "":
        chosen_name = chosen_input_name.strip()
        if prefix == 'R': default_id = get_next_raw_id()
        elif prefix == 'S': default_id = get_next_supply_id()
        else: default_id = get_next_bill_id()
        
        default_p_unit, default_u_unit, default_c_factor, default_safety = "", "", 1.0, 0.0
        st.warning(f"✨ 偵測到新品項！自動發放【{item_type[:2]}】專屬編號：**{default_id}**")
    elif chosen_select_name != f"--- 請選擇已建立的{item_type[:2]} ---":
        chosen_name = chosen_select_name
        matched_item = existing_items_df[existing_items_df['prod_name'] == chosen_name].iloc[0]
        default_id = matched_item['prod_id']
        default_p_unit = matched_item['purchase_unit']
        default_u_unit = matched_item['use_unit']
        default_c_factor = float(matched_item['conversion_factor'])
        default_safety = float(matched_item['safety_stock'])
        st.info(f"💡 識別成功：編號為 {default_id}。")
    else:
        chosen_name, default_id, default_p_unit, default_u_unit, default_c_factor, default_safety = "", "", "", "", 1.0, 0.0

    with st.form("clean_po_form"):
        final_id = st.text_input("項目編號", value=default_id, disabled=True)
        
        # 🟢 如果是帳單 (C)，隱藏複雜的規格轉換欄位，只留下金額
        if prefix == 'C':
            st.markdown("##### 💰 2. 請輸入本次帳單金額：")
            total_invoice_amount = st.number_input("本次帳單【繳費總金額】($)", min_value=0.0, value=0.0, step=10.0)
            # 帳單預設隱藏欄位值
            p_unit, u_unit, c_factor, po_qty, s_stock = "次", "次", 1.0, 1.0, 0.0
            v_name, v_phone, exp_str = "公共事業/其他", "", ""
        else:
            # 食材與用品維持原樣
            st.markdown("##### 📦 2. 確認或填寫本批次的包裝規格與單位：")
            col_spec1, col_spec2, col_spec3 = st.columns(3)
            with col_spec1: p_unit = st.text_input("大包裝進貨單位 (如：包、箱)", value=default_p_unit)
            with col_spec2: u_unit = st.text_input("廚房基本使用小單位 (如：g、個)", value=default_u_unit)
            with col_spec3: c_factor = st.number_input("轉換率 (1大包裝內含多少基本小單位)", min_value=0.0, value=default_c_factor, step=1.0)

            st.markdown("##### 💰 3. 請填寫本次採購的實際數據與供應商資訊：")
            col_po1, col_po2, col_po3 = st.columns(3)
            with col_po1: po_qty = st.number_input(f"進貨大包裝總數量", min_value=0.0, value=0.0, step=1.0)
            with col_po2: total_invoice_amount = st.number_input("本次進貨【採購總金額】($)", min_value=0.0, value=0.0, step=10.0)
            with col_po3: s_stock = st.number_input(f"設定最低安全預警量", min_value=0.0, value=default_safety, step=1.0)
                
            col_vendor1, col_vendor2, col_vendor3 = st.columns(3)
            with col_vendor1: v_name = st.text_input("供應商店名 (選填)", value="")
            with col_vendor2: v_phone = st.text_input("供應商電話 (選填)", value="")
            with col_vendor3:
                expiry_input = st.date_input("請選取有效期限 (用品無效期可不選)", value=None, key="po_exp_date")
                exp_str = expiry_input.strftime("%Y-%m-%d") if expiry_input is not None else ""

        total_use_units = po_qty * c_factor
        calculated_single_cost = total_invoice_amount / total_use_units if total_use_units > 0 else 0.0
        
        submit_po = st.form_submit_button("📥 確認無誤，送出登記")
        
        if submit_po:
            if not chosen_name or total_invoice_amount <= 0:
                st.error("❌ 錯誤：請確認品名與金額皆已確實填寫！")
            else:
                conn = sqlite3.connect('inventory.db')
                cursor = conn.cursor()
                # 寫入產品表
                cursor.execute('''INSERT OR REPLACE INTO products (prod_id, prod_name, cost, price, safety_stock, purchase_unit, use_unit, conversion_factor) 
                                  VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', (final_id, chosen_name, calculated_single_cost, 0.0, s_stock, p_unit, u_unit, c_factor))
                # 寫入庫存批次表
                cursor.execute('''INSERT INTO stock_batches (prod_id, qty, expiry_date, inbound_date, vendor_name, vendor_phone) 
                                  VALUES (?, ?, ?, ?, ?, ?)''', (final_id, total_use_units, exp_str, datetime.now().strftime("%Y-%m-%d"), v_name, v_phone))
                conn.commit()
                conn.close()
                
                log_action = "帳單支出登記" if prefix == 'C' else "採購進貨"
                log_history(current_user, log_action, f"登記 {chosen_name}，金額 ${total_invoice_amount}")
                st.success(f"🎉【登記成功】{item_type[:2]}「{chosen_name}」已成功記錄！")
                st.rerun()

# ==========================================
# Tab 3 & Tab 4 & Tab 5 (微調、盤點、歷史略，維持不變)
# ==========================================
# ==========================================
# Tab 3: 批次庫存調整 (升級：精準計算調整前後差異並寫入審計軌跡)
# ==========================================
with tabs[3]:
    st.subheader("🛠️ 批次庫存微調與報廢管理")
    conn = sqlite3.connect('inventory.db')
    prods_df = pd.read_sql_query("SELECT prod_id, prod_name, use_unit FROM products WHERE price = 0", conn)
    conn.close()
    
    col_a1, col_a2, col_a3 = st.columns(3)
    with col_a1:
        adj_prod = st.selectbox("1. 選擇要調整的商品", prods_df['prod_id'] + " - " + prods_df['prod_name'], key="adj_p")
        ap_id = adj_prod.split(" - ")[0]
        # 抓出該商品的廚房基本單位
        matched_prod = prods_df[prods_df['prod_id'] == ap_id].iloc[0]
        unit_label = matched_prod['use_unit']
        item_name = matched_prod['prod_name']
        
    with col_a2:
        conn = sqlite3.connect('inventory.db')
        df_adj_batches = pd.read_sql_query("SELECT batch_id, qty, expiry_date FROM stock_batches WHERE prod_id = ?", conn, params=(ap_id,))
        conn.close()
        if not df_adj_batches.empty:
            adj_batch_options = df_adj_batches.apply(lambda r: f"批次 {int(r['batch_id'])} (現存庫存:{r['qty']}, 效期:{r['expiry_date']})", axis=1).tolist()
            selected_adj_batch_str = st.selectbox("2. 指定要微調的批次編號", adj_batch_options)
            target_adj_batch_id = int(selected_adj_batch_str.split(" (")[0].replace("批次 ", ""))
        else:
            target_adj_batch_id = None
            st.warning("⚠️ 該品項目前在後台沒有任何庫存批次可供調整！")
            
    with col_a3:
        # 增加「人工補登/其他變多」的選項，讓老闆操作更直覺
        adj_type = st.selectbox("3. 調整原因/名義", ["商品損壞/打翻 (變少)", "過期報廢 (變少)", "人工補登/廠商多送 (變多)", "其他原因調整"])
        # 提示老闆：如果是要減少庫存，請輸入負數
        adj_qty = st.number_input(f"4. 調整數量 (輸入正數為增加，負數為減少，單位: {unit_label})", value=0.0, step=1.0)
        
    if st.button("確認微調此特定批次庫存"):
        if target_adj_batch_id is None:
            st.error("❌ 錯誤：沒有可調整的批次！")
        elif adj_qty == 0:
            st.error("❌ 錯誤：調整數量不能為 0！")
        else:
            # 🟢 1. 先反查該批次在「調整前」的精確數量
            conn = sqlite3.connect('inventory.db')
            cursor = conn.cursor()
            cursor.execute("SELECT qty, expiry_date FROM stock_batches WHERE batch_id = ?", (target_adj_batch_id,))
            batch_res = cursor.fetchone()
            
            if batch_res:
                old_qty = float(batch_res[0])
                expiry_str = batch_res[1]
                new_qty = old_qty + adj_qty
                
                if new_qty < 0:
                    st.error(f"❌ 錯誤：調整後的庫存量不能為負數！(當前庫存: {old_qty}, 預計扣除: {abs(adj_qty)})")
                    conn.close()
                else:
                    # 🟢 2. 執行資料庫更新
                    cursor.execute("UPDATE stock_batches SET qty = ? WHERE batch_id = ?", (new_qty, target_adj_batch_id))
                    
                    # 防呆：如果調整後數量剛好變為 0，直接刪除該批次以節省空間
                    if new_qty == 0:
                        cursor.execute("DELETE FROM stock_batches WHERE batch_id = ?", (target_adj_batch_id,))
                        
                    conn.commit()
                    conn.close()
                    
                    # 🟢 3. 自動判斷是變多還是變少，生成對應的文字
                    direction = "【庫存變多 ➕】" if adj_qty > 0 else "【庫存變少 ➖】"
                    
                    # 🟢 4. 強制寫入歷史動作審計軌跡
                    log_details = (
                        f"微調特定批次庫存。品項：{item_name}({ap_id}) | 指定批次: {target_adj_batch_id}號 "
                        f"| 效期: {expiry_str if expiry_str else '無'} | 調整名義: {adj_type} | 變動方向: {direction} "
                        f"| 調整前數量: {old_qty:,.2f} {unit_label} | 異動量: {adj_qty:+,.2f} {unit_label} " # <-- 修正為 +,.2f
                        f"| 調整後終點庫存: {new_qty:,.2f} {unit_label}。"
                            )
                    log_history(current_user, f"庫存微調-{item_name}", log_details)
                    
                    st.success(f"🎉 批次庫存調整成功！已成功紀錄於歷史動作審計軌跡。")
                    st.rerun()
            else:
                st.error("❌ 錯誤：找不到該指定的批次資料！")
                conn.close()

# ==========================================
# Tab 4: 存貨盤點 (升級：精準計算盤盈虧並強制寫入審計軌跡)
# ==========================================
with tabs[4]:
    st.subheader("📋 存貨盤點核實")
    conn = sqlite3.connect('inventory.db')
    df_audit = pd.read_sql_query('''
        SELECT s.prod_id as 食品編號, p.prod_name as 商品名稱, 
               SUM(s.qty) as 系統理論庫存, p.use_unit as 單位, p.cost as 單位成本 
        FROM stock_batches s 
        JOIN products p ON s.prod_id = p.prod_id 
        GROUP BY s.prod_id
    ''', conn)
    conn.close()
    
    if not df_audit.empty:
        selected_row = st.selectbox("選擇要盤點的項目", df_audit['食品編號'] + " - " + df_audit['商品名稱'])
        actual_qty = st.number_input("現場實盤總數量", min_value=0.0, value=0.0, step=1.0)
        
        if st.button("提交盤點數據"):
            prod_id_part = selected_row.split(" - ")[0]
            
            # 🟢 1. 找出該品項在盤點前的「系統理論庫存」與「單位」
            matched_item = df_audit[df_audit['食品編號'] == prod_id_part].iloc[0]
            theoretical_qty = float(matched_item['系統理論庫存'])
            unit_label = matched_item['單位']
            item_name = matched_item['商品名稱']
            
            # 🟢 2. 計算盤點差異量 (實盤 - 理論)
            diff_qty = actual_qty - theoretical_qty
            
            # 🟢 3. 根據差異量自動判定審計狀態字串
            if diff_qty > 0:
                audit_status = f"盤盈 (多了 {abs(diff_qty):,.2f} {unit_label})"
            elif diff_qty < 0:
                audit_status = f"盤虧 (少了 {abs(diff_qty):,.2f} {unit_label})"
            else:
                audit_status = "完全吻合 (無誤差)"
            
            # 🟢 4. 執行資料庫覆蓋更新
            conn = sqlite3.connect('inventory.db')
            cursor = conn.cursor()
            
            # 刪除舊有批次，改以盤點數作為新起點
            cursor.execute("DELETE FROM stock_batches WHERE prod_id = ?", (prod_id_part,))
            cursor.execute('''
                INSERT INTO stock_batches (prod_id, qty, expiry_date, inbound_date) 
                VALUES (?, ?, ?, ?)
            ''', (prod_id_part, actual_qty, (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d")))
            
            conn.commit()
            conn.close()
            
            # 🟢 5. 強制寫入歷史審計軌跡 (不論結果如何都會留下紀錄)
            log_details = f"針對【{item_name}({prod_id_part})】進行庫存盤點。系統理論庫存: {theoretical_qty:,.2f} {unit_label}，現場實盤總數: {actual_qty:,.2f} {unit_label}。盤點結果: {audit_status}。"
            log_history(current_user, f"存貨盤點-{item_name}", log_details)
            
            st.success(f"🎉 盤點覆蓋完成！結果為：{audit_status}")
            st.rerun()
    else:
        st.info("💡 目前倉庫沒有任何庫存資料可供盤點。")






# ==========================================
# Tab 5: 歷史動作審計軌跡 (升級：獨立時間篩選版)
# ==========================================
with tabs[5]:

    st.subheader("📜 歷史動作審計軌跡")

    # -----------------------------
    # 時間範圍選擇
    # -----------------------------
    history_time_option = st.selectbox(
        "📅 選擇查看時間區間",
        [
            "今天",
            "過去 7 天",
            "過去 30 天",
            "指定特定日期"
        ],
        key="history_filter"
    )

    now = datetime.now()

    # -----------------------------
    # 自動計算起訖時間
    # -----------------------------
    if history_time_option == "今天":

        start_dt = now.replace(
            hour=0,
            minute=0,
            second=0
        )

        end_dt = now.replace(
            hour=23,
            minute=59,
            second=59
        )

    elif history_time_option == "過去 7 天":

        start_dt = (
            now - timedelta(days=7)
        ).replace(
            hour=0,
            minute=0,
            second=0
        )

        end_dt = now

    elif history_time_option == "過去 30 天":

        start_dt = (
            now - timedelta(days=30)
        ).replace(
            hour=0,
            minute=0,
            second=0
        )

        end_dt = now

    else:

        selected_date = st.date_input(
            "請選擇日期",
            value=now.date(),
            key="history_date"
        )

        start_dt = datetime.combine(
            selected_date,
            datetime.min.time()
        )

        end_dt = datetime.combine(
            selected_date,
            datetime.max.time()
        )

    start_str = start_dt.strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    end_str = end_dt.strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    st.caption(
        f"目前查看區間：{start_dt.strftime('%Y-%m-%d')} ～ {end_dt.strftime('%Y-%m-%d')}"
    )

    # -----------------------------
    # 查詢歷史資料
    # -----------------------------
    conn = sqlite3.connect("inventory.db")

    df_hist = pd.read_sql_query(
        '''
        SELECT
            timestamp AS 時間,
            user AS 操作人,
            action AS 動作,
            details AS 詳細說明
        FROM history
        WHERE timestamp BETWEEN ? AND ?
        ORDER BY id DESC
        ''',
        conn,
        params=(
            start_str,
            end_str
        )
    )

    conn.close()

    # -----------------------------
    # 顯示資料
    # -----------------------------
    if not df_hist.empty:

        st.metric(
            "符合條件紀錄數",
            len(df_hist)
        )

        st.dataframe(
            df_hist,
            use_container_width=True,
            hide_index=True
        )

    else:

        st.info(
            "💡 此時間區間內沒有任何歷史操作紀錄。"
        )



# ==========================================
# Tab 6: 財務與綜合損益報告（完整版）
# ==========================================
with tabs[6]:

    st.subheader("📊 門市商業智能：營收、成本與損益分析")

    # ==========================
    # 時間區間
    # ==========================
    report_option = st.selectbox(
        "📅 選擇統計區間",
        [
            "今天",
            "過去 7 天",
            "過去 30 天",
            "自訂日期區間"
        ],
        key="finance_time"
    )

    now = datetime.now()

    if report_option == "今天":

        start_date = now.date()
        end_date = now.date()

    elif report_option == "過去 7 天":

        start_date = (
            now - timedelta(days=7)
        ).date()

        end_date = now.date()

    elif report_option == "過去 30 天":

        start_date = (
            now - timedelta(days=30)
        ).date()

        end_date = now.date()

    else:

        c1, c2 = st.columns(2)

        with c1:
            start_date = st.date_input(
                "開始日期",
                value=now.date(),
                key="finance_start"
            )

        with c2:
            end_date = st.date_input(
                "結束日期",
                value=now.date(),
                key="finance_end"
            )

    start_str = datetime.combine(
        start_date,
        datetime.min.time()
    ).strftime("%Y-%m-%d %H:%M:%S")

    end_str = datetime.combine(
        end_date,
        datetime.max.time()
    ).strftime("%Y-%m-%d %H:%M:%S")

    inbound_start = start_date.strftime("%Y-%m-%d")
    inbound_end = end_date.strftime("%Y-%m-%d")

    st.caption(
        f"統計區間：{start_date} ～ {end_date}"
    )

    # ==========================
    # 讀取資料
    # ==========================
    conn = sqlite3.connect(
        "inventory.db"
    )

    df_sales = pd.read_sql_query(
        """
        SELECT details
        FROM history
        WHERE action LIKE '餐點收銀結帳-%'
        AND timestamp BETWEEN ? AND ?
        """,
        conn,
        params=(
            start_str,
            end_str
        )
    )

    df_bill = pd.read_sql_query(
        """
        SELECT
            p.prod_name,
            SUM(
                s.qty*p.cost
            ) amount

        FROM stock_batches s

        JOIN products p
        ON s.prod_id=p.prod_id

        WHERE
            p.prod_id LIKE 'C%'
            AND s.inbound_date
            BETWEEN ? AND ?

        GROUP BY p.prod_id
        """,
        conn,
        params=(
            inbound_start,
            inbound_end
        )
    )

    cursor = conn.cursor()

    # ==========================
    # 分析
    # ==========================
    total_revenue = 0
    total_food_cost = 0
    total_bill = 0

    material_usage = {}

    dish_sales = {}

    for _, row in df_sales.iterrows():

        txt = row["details"]

        # 營收
        revenue_match = re.search(
            r'總金額 \$(\d+\.?\d*)',
            txt
        )

        if revenue_match:

            total_revenue += float(
                revenue_match.group(1)
            )

        # 餐點份數
        dish_match = re.search(
            r'前台銷售「(.+?) × ([\d\.]+) 份」',
            txt
        )

        if dish_match:

            dish_name = dish_match.group(1)

            qty = float(
                dish_match.group(2)
            )

            dish_sales[
                dish_name
            ] = (
                dish_sales.get(
                    dish_name,
                    0
                )
                + qty
            )

        # 食材消耗
        mats = re.findall(
            r'([^_]+)_(R\d+)\(([\d\.]+)',
            txt
        )

        for m_name, m_id, qty in mats:

            qty = float(qty)

            cursor.execute(
                """
                SELECT cost
                FROM products
                WHERE prod_id=?
                """,
                (
                    m_id,
                )
            )

            res = cursor.fetchone()

            if res:

                total_food_cost += (
                    qty
                    *
                    float(
                        res[0]
                    )
                )

            material_usage[
                m_name
            ] = (
                material_usage.get(
                    m_name,
                    0
                )
                +
                qty
            )

    conn.close()

    # ==========================
    # 帳單
    # ==========================
    if not df_bill.empty:

        total_bill = float(
            df_bill[
                "amount"
            ].sum()
        )

    gross_profit = (
        total_revenue
        -
        total_food_cost
    )

    net_profit = (
        gross_profit
        -
        total_bill
    )

    margin = (
        (
            net_profit
            /
            total_revenue
        )
        *
        100
        if total_revenue > 0
        else 0
    )

    # ==========================
    # KPI
    # ==========================
    a, b, c, d, e = st.columns(5)

    a.metric(
        "🏪 營業額",
        f"${total_revenue:,.0f}"
    )

    b.metric(
        "🥩 食材成本",
        f"${total_food_cost:,.0f}"
    )

    c.metric(
        "⚡ 帳單支出",
        f"${total_bill:,.0f}"
    )

    d.metric(
        "🔥 淨利",
        f"${net_profit:,.0f}"
    )

    e.metric(
        "📈 毛利率",
        f"{margin:.1f}%"
    )

    st.divider()

    # ==========================
    # 圖表
    # ==========================
    left, right = st.columns(2)

    with left:

        st.markdown(
            "### 🍩 食材消耗占比"
        )

        if material_usage:

            pie_df = pd.DataFrame(
                material_usage.items(),
                columns=[
                    "食材",
                    "數量"
                ]
            )

            st.vega_lite_chart(
                pie_df,
                {
                    "mark": "arc",
                    "encoding": {

                        "theta": {
                            "field": "數量"
                        },

                        "color": {
                            "field": "食材"
                        }
                    }
                },
                use_container_width=True
            )

        else:

            st.info(
                "目前沒有消耗資料"
            )

    with right:

        st.markdown(
            "### 🏆 餐點銷售排行"
        )

        if dish_sales:

            rank_df = (
                pd.DataFrame(
                    dish_sales.items(),
                    columns=[
                        "餐點",
                        "銷售份數"
                    ]
                )
                .sort_values(
                    "銷售份數",
                    ascending=False
                )
            )

            st.dataframe(
                rank_df,
                hide_index=True,
                use_container_width=True
            )

        else:

            st.info(
                "目前沒有銷售資料"
            )

    st.divider()

    # ==========================
    # 損益摘要
    # ==========================
    st.markdown(
        "### 📌 損益摘要"
    )

    st.info(
        f"""
        營收：${total_revenue:,.0f}

        － 食材成本：${total_food_cost:,.0f}

        － 固定帳單：${total_bill:,.0f}

        ＝ 最終淨利：${net_profit:,.0f}

        淨利率：{margin:.1f}%
        """
            )