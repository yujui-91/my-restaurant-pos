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
    
    # 2. 庫存批次明細表
    cursor.execute('''CREATE TABLE IF NOT EXISTS stock_batches (
                        batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        prod_id TEXT, 
                        qty REAL, 
                        expiry_date TEXT,
                        inbound_date TEXT)''')
    
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
        
        # 初始原物料： cost 以「使用小單位(g/ml)」為計價基準
        cursor.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
            ('R001', '澳洲牛肉', 0.5, 0.0, 1000, '箱(20kg)', 'g', 20000.0),
            ('R002', '麵條', 5.0, 0.0, 50, '箱(100份)', '份', 100.0),
            ('R003', '高湯', 0.02, 0.0, 5000, '桶(20L)', 'ml', 20000.0),
            ('R004', '蔥花', 0.1, 0.0, 200, '袋(1kg)', 'g', 1000.0),
            ('P001', '招牌牛肉麵(成品)', 0.0, 180.0, 0, '碗', '碗', 1.0)
        ])
        
        cursor.executemany("INSERT INTO stock_batches (prod_id, qty, expiry_date, inbound_date) VALUES (?, ?, ?, ?)", [
            ('R001', 500, exp_1, today),   
            ('R001', 2000, exp_2, today),  
            ('R002', 60, exp_2, today),
            ('R003', 10000, exp_2, today),
            ('R004', 150, exp_1, today)
        ])
        
        cursor.executemany("INSERT INTO bom VALUES (?, ?, ?)", [
            ('P001', 'R001', 150.0),
            ('P001', 'R002', 1.0),
            ('P001', 'R003', 300.0),
            ('P001', 'R004', 5.0)
        ])
        
    conn.commit()
    conn.close()

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

# ==========================================
# 網頁介面設計
# ==========================================
st.set_page_config(layout="wide")
st.title("🍳 智能餐飲進銷存與精準成本分析系統")

st.sidebar.header("系統參數")
current_user = st.sidebar.text_input("操作人員", value="店長-阿龍")

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
# Tab 0: 目前庫存檢視
# ==========================================
with tabs[0]:
    st.subheader("📊 目前即時庫存明細 (依批次/效期)")
    conn = sqlite3.connect('inventory.db')
    df_stock = pd.read_sql_query('''
        SELECT s.batch_id as 批次編號, s.prod_id as 食材編號, p.prod_name as 商品名稱, 
               s.qty as 庫存量, p.use_unit as 單位, s.expiry_date as 有效期限, p.safety_stock as 安全庫存
        FROM stock_batches s JOIN products p ON s.prod_id = p.prod_id ORDER BY s.prod_id, s.expiry_date ASC
    ''', conn)
    prods_quick = pd.read_sql_query("SELECT prod_id, prod_name FROM products WHERE price = 0", conn)
    conn.close()
    
    if not df_stock.empty:
        st.dataframe(df_stock, use_container_width=True)
    else:
        st.info("目前無庫存，請先辦理採購進貨。")

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
        df_recipe_view = pd.DataFrame(st.session_state.current_recipe_list)
        
        dish_calculated_cost_single = 0.0
        for item in st.session_state.current_recipe_list:
            c_cost = all_raw_df[all_raw_df['prod_id'] == item['食材編號']]['cost'].values[0]
            dish_calculated_cost_single += item['單位用量'] * c_cost
            
        st.dataframe(df_recipe_view, use_container_width=True)
        
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
# Tab 2: 採購進貨 (維持不變)
# ==========================================
with tabs[2]:
    st.subheader("📝 採購進貨單 (自由選取現有品項，或填寫新食材自動生成編號)")
    conn = sqlite3.connect('inventory.db')
    existing_items_df = pd.read_sql_query("SELECT prod_id, prod_name, purchase_unit, use_unit, conversion_factor, safety_stock FROM products WHERE price = 0", conn)
    conn.close()
    
    st.markdown("##### 🔍 1. 品項選取（二選一）：")
    col_choice1, col_choice2 = st.columns(2)
    with col_choice1:
        options_list = ["--- 請選擇已建立的食材 ---"] + (existing_items_df['prod_name'].tolist())
        chosen_select_name = st.selectbox("【重複進貨】從這裡直接下拉搜尋既有食材", options_list, index=0)
    with col_choice2:
        chosen_input_name = st.text_input("【首次進貨】在此直接手動打字輸入新食材品名", value="")

    if chosen_input_name.strip() != "":
        is_new = True
        chosen_name = chosen_input_name.strip()
        default_id = get_next_raw_id()
        default_p_unit, default_u_unit, default_c_factor, default_safety = "", "", 0.0, 0.0
        st.warning(f"✨ 檢測到新食材！流水號編號：**{default_id}**")
    elif chosen_select_name != "--- 請選擇已建立的食材 ---":
        is_new = False
        chosen_name = chosen_select_name
        matched_item = existing_items_df[existing_items_df['prod_name'] == chosen_name].iloc[0]
        default_id = matched_item['prod_id']
        default_p_unit = matched_item['purchase_unit']
        default_u_unit = matched_item['use_unit']
        default_c_factor = float(matched_item['conversion_factor'])
        default_safety = float(matched_item['safety_stock'])
        st.info(f"💡 識別成功：編號為 {default_id}。")
    else:
        is_new = True
        chosen_name, default_id, default_p_unit, default_u_unit, default_c_factor, default_safety = "", "", "", "", 0.0, 0.0

    with st.form("clean_po_form"):
        st.markdown("##### 📦 2. 確認或填寫本批次的包裝規格與單位：")
        col_spec1, col_spec2, col_spec3, col_spec4 = st.columns(4)
        with col_spec1:
            final_id = st.text_input("食材編號", value=default_id, disabled=True)
        with col_spec2:
            p_unit = st.text_input("大包裝進貨單位 (如：包、箱)", value=default_p_unit)
        with col_spec3:
            u_unit = st.text_input("廚房基本使用小單位 (如：g、顆)", value=default_u_unit)
        with col_spec4:
            c_factor = st.number_input("轉換率 (1大包裝內含多少基本小單位)", min_value=0.0, value=default_c_factor, step=1.0)

        st.markdown("##### 💰 3. 請填寫本次採購的實際數據（不能為 0）：")
        col_po1, col_po2, col_po3 = st.columns(3)
        with col_po1:
            po_qty = st.number_input(f"進貨大包裝總數量", min_value=0.0, value=0.0, step=1.0)
        with col_po2:
            total_invoice_amount = st.number_input("本次進貨【採購總金額】($)", min_value=0.0, value=0.0, step=10.0)
        with col_po3:
            s_stock = st.number_input(f"設定最低安全預警量", min_value=0.0, value=default_safety, step=1.0)
            
        expiry_input = st.date_input("請點擊右側日曆選取此批食材有效期限", value=None, key="po_exp_date")

        total_use_units = po_qty * c_factor
        calculated_single_cost = total_invoice_amount / total_use_units if total_use_units > 0 else 0.0
        
        display_unit_label = u_unit if u_unit.strip() != "" else "單位"
        if total_use_units > 0 and total_invoice_amount > 0:
            st.markdown(f"""
            > 📊 **進貨成本拆算即時分析報告：**
            > * 本次進貨大包裝折合總量為： **{total_use_units:,.1f} {display_unit_label}**
            > * 經由總金額 ${total_invoice_amount} 自動反推： 平均每 1 **{display_unit_label}** 的廚房核心食材成本為 **${calculated_single_cost:,.4f} 元**
            """)
        
        submit_po = st.form_submit_button("📥 確認採購單無誤，送出入庫")
        
        if submit_po:
            if not chosen_name or c_factor <= 0 or po_qty <= 0 or total_invoice_amount <= 0 or expiry_input is None:
                st.error("❌ 錯誤：請確認所有進貨欄位皆已確實填寫！")
            else:
                exp_str = expiry_input.strftime("%Y-%m-%d")
                conn = sqlite3.connect('inventory.db')
                cursor = conn.cursor()
                cursor.execute('''INSERT OR REPLACE INTO products (prod_id, prod_name, cost, price, safety_stock, purchase_unit, use_unit, conversion_factor) 
                                  VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', (final_id, chosen_name, calculated_single_cost, 0.0, s_stock, p_unit, u_unit, c_factor))
                cursor.execute("INSERT INTO stock_batches (prod_id, qty, expiry_date, inbound_date) VALUES (?, ?, ?, ?)", (final_id, total_use_units, exp_str, datetime.now().strftime("%Y-%m-%d")))
                conn.commit()
                conn.close()
                log_history(current_user, "採購進貨", f"購入 {chosen_name} x{total_use_units} {display_unit_label}")
                st.success(f"🎉【採購進貨單確認成功】食材「{chosen_name}」已成功入庫！")
                st.rerun()

# ==========================================
# Tab 3 & Tab 4 & Tab 5 (微調、盤點、歷史略，維持不變)
# ==========================================
with tabs[3]:
    st.subheader("🛠️ 批次庫存調整")
    conn = sqlite3.connect('inventory.db')
    prods_df = pd.read_sql_query("SELECT prod_id, prod_name FROM products WHERE price = 0", conn)
    conn.close()
    col_a1, col_a2, col_a3 = st.columns(3)
    with col_a1:
        adj_prod = st.selectbox("1. 選擇要調整的商品", prods_df['prod_id'] + " - " + prods_df['prod_name'], key="adj_p")
        ap_id = adj_prod.split(" - ")[0]
    with col_a2:
        conn = sqlite3.connect('inventory.db')
        df_adj_batches = pd.read_sql_query("SELECT batch_id, qty, expiry_date FROM stock_batches WHERE prod_id = ?", conn, params=(ap_id,))
        conn.close()
        if not df_adj_batches.empty:
            adj_batch_options = df_adj_batches.apply(lambda r: f"批次 {int(r['batch_id'])} (庫存:{r['qty']}, 效期:{r['expiry_date']})", axis=1).tolist()
            selected_adj_batch_str = st.selectbox("2. 指定要微調的批次編號", adj_batch_options)
            target_adj_batch_id = int(selected_adj_batch_str.split(" (")[0].replace("批次 ", ""))
        else:
            target_adj_batch_id = None
    with col_a3:
        adj_type = st.selectbox("調整原因", ["商品損壞/打翻", "過期報廢"])
        adj_qty = st.number_input("調整數量", value=0.0)
    if st.button("確認微調此特定批次庫存"):
        if target_adj_batch_id and adj_qty != 0:
            conn = sqlite3.connect('inventory.db')
            cursor = conn.cursor()
            cursor.execute("UPDATE stock_batches SET qty = qty + ? WHERE batch_id = ?", (adj_qty, target_adj_batch_id))
            conn.commit()
            conn.close()
            st.success("🎉 調整成功！")
            st.rerun()

with tabs[4]:
    st.subheader("📋 存貨盤點")
    conn = sqlite3.connect('inventory.db')
    df_audit = pd.read_sql_query('SELECT s.prod_id as 食品編號, p.prod_name as 商品名稱, SUM(s.qty) as 系統理論庫存, p.use_unit as 單位, p.cost as 單位成本 FROM stock_batches s JOIN products p ON s.prod_id = p.prod_id GROUP BY s.prod_id', conn)
    conn.close()
    if not df_audit.empty:
        selected_row = st.selectbox("選擇要盤點的項目", df_audit['食品編號'] + " - " + df_audit['商品名稱'])
        actual_qty = st.number_input("現場實盤總數量", min_value=0.0, value=0.0)
        if st.button("提交盤點數據"):
            prod_id_part = selected_row.split(" - ")[0]
            conn = sqlite3.connect('inventory.db')
            cursor = conn.cursor()
            cursor.execute("DELETE FROM stock_batches WHERE prod_id = ?", (prod_id_part,))
            cursor.execute("INSERT INTO stock_batches (prod_id, qty, expiry_date, inbound_date) VALUES (?, ?, ?, ?)", (prod_id_part, actual_qty, (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d")))
            conn.commit()
            conn.close()
            st.success("🎉 盤點覆蓋完成！")
            st.rerun()

with tabs[5]:
    st.subheader("📜 歷史動作審計軌跡")
    conn = sqlite3.connect('inventory.db')
    df_hist = pd.read_sql_query("SELECT timestamp as 時間, user as 操作人, action as 動作, details as 詳細說明 FROM history ORDER BY id DESC", conn)
    conn.close()
    st.dataframe(df_hist, use_container_width=True)

# ==========================================
# Tab 6: 財務與消耗量報告 (圓餅圖精準抓取算法修正版)
# ==========================================
with tabs[6]:
    st.subheader("💰 門市存貨價值資產報告")
    conn = sqlite3.connect('inventory.db')
    df_valuation = pd.read_sql_query('''
        SELECT p.prod_id as 食材編號, p.prod_name as 食材名稱, SUM(s.qty) as 目前庫存量, p.use_unit as 單位, p.cost as 單位成本, SUM(s.qty * p.cost) as 存貨總價值
        FROM stock_batches s JOIN products p ON s.prod_id = p.prod_id WHERE p.price = 0 GROUP BY s.prod_id
    ''', conn)
    conn.close()
    
    if not df_valuation.empty:
        st.dataframe(df_valuation, use_container_width=True)
        st.metric("📦 目前全倉庫壓金總資產成本", f"${df_valuation['存貨總價值'].sum():,.2f}")
        
    st.markdown("---")
    st.subheader("📊 商業智能分析：自訂區間【食材消耗圓餅圖】與【餐點銷量大盤點】")
    
    col_d1, col_d2 = st.columns(2)
    with col_d1:
        start_date = st.date_input("選擇統計開始日期", value=datetime.now() - timedelta(days=30))
    with col_d2:
        end_date = st.date_input("選擇統計結束日期", value=datetime.now())
        
    if start_date and end_date:
        start_str = start_date.strftime("%Y-%m-%d 00:00:00")
        end_str = end_date.strftime("%Y-%m-%d 23:59:59")
        
        conn = sqlite3.connect('inventory.db')
        # 🟢 修正：圓餅圖精準撈取包含了「餐點收銀結帳」動態扣料的所有歷史詳情
        df_logs_range = pd.read_sql_query("SELECT details FROM history WHERE action LIKE '餐點收銀結帳-%' AND timestamp BETWEEN ? AND ?", conn, params=(start_str, end_str))
        conn.close()
        
        material_usage_dict = {}
        dish_sales_dict = {}
        
        for idx, row in df_logs_range.iterrows():
            log_text = row['details']
            
            # 1. 統計餐點數量
            dish_match = re.search(r'前台銷售「(.+?) × (\d+?) 份」', log_text)
            if dish_match:
                d_name = dish_match.group(1)
                d_qty = int(dish_match.group(2))
                dish_sales_dict[d_name] = dish_sales_dict.get(d_name, 0) + d_qty
            
            # 2. 精準解析食材消耗 (對應新格式：名稱_編號(數量單位))
            matches = re.findall(r'([\u4e00-\u9fa5a-zA-Z0-9_]+)_R\d+\(([\d\.]+)([\u4e00-\u9fa5a-zA-Z]+)\)', log_text)
            for m_name, m_qty, m_unit in matches:
                qty_f = float(m_qty)
                key_lbl = f"{m_name} ({m_unit})"
                material_usage_dict[key_lbl] = material_usage_dict.get(key_lbl, 0.0) + qty_f

        col_report1, col_report2 = st.columns(2)
        
        with col_report1:
            st.markdown(f"##### 🍩 食材消耗佔比圓餅圖 ({start_date} ~ {end_date})")
            if material_usage_dict:
                df_pie = pd.DataFrame(list(material_usage_dict.items()), columns=["食材項目", "消耗總量"])
                st.vega_lite_chart(df_pie, {
                    'mark': {'type': 'arc', 'innerRadius': 40, 'tooltip': True},
                    'encoding': {
                        'theta': {'field': '消耗總量', 'type': 'quantitative'},
                        'color': {'field': '食材項目', 'type': 'nominal'}
                    }
                }, use_container_width=True)
                st.dataframe(df_pie, use_container_width=True)
            else:
                st.info("💡 該時間區間內，前台沒有餐點扣料紀錄，因此無食材消耗。")
                
        with col_report2:
            st.markdown(f"##### 📈 餐點銷售總量統計表 ({start_date} ~ {end_date})")
            if dish_sales_dict:
                df_dish_sales = pd.DataFrame(list(dish_sales_dict.items()), columns=["餐點名稱", "累計賣出份數"]).sort_values(by="累計賣出份數", ascending=False)
                for _, r in df_dish_sales.iterrows():
                    st.metric(label=f"🔥 {r['餐點名稱']} 總銷量", value=f"{r['累計賣出份數']} 份")
                st.markdown("---")
                st.dataframe(df_dish_sales, use_container_width=True)
            else:
                st.info("💡 該時間區間內，前台沒有餐點結帳營收紀錄。")