# database/db_core.py
import re
from datetime import datetime, timedelta
import streamlit as st
import libsql  
import sqlite3
import os
import pandas as pd
import pytz  # 引入時區套件

def get_taiwan_now():
    """獲取精準的台灣時間"""
    tw_tz = pytz.timezone('Asia/Taipei')
    return datetime.now(tw_tz)

def get_db_conn():
    """
    動態獲取資料庫連線：
    優先檢查 Streamlit Secrets 是否有 Turso 設定，若有則連線上資料庫；
    若無（例如在本機開發且沒設定秘密鍵），則連線到本地的 inventory.db。
    """
    if "TURSO_DATABASE_URL" in st.secrets and "TURSO_AUTH_TOKEN" in st.secrets:
        import libsql
        url = st.secrets["TURSO_DATABASE_URL"]
        token = st.secrets["TURSO_AUTH_TOKEN"]
        return libsql.connect(url, auth_token=token)
    else:
        db_path = os.path.join(os.path.dirname(__file__), "..", "inventory.db")
        if os.path.exists(db_path):
            return sqlite3.connect(db_path)
        return sqlite3.connect("inventory.db")

def init_db():
    conn = get_db_conn()
    cursor = conn.cursor()
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS products (
                        prod_id TEXT PRIMARY KEY, 
                        prod_name TEXT, 
                        cost REAL, 
                        price REAL,
                        safety_stock REAL DEFAULT 0,
                        purchase_unit TEXT DEFAULT '',
                        use_unit TEXT DEFAULT '',
                        conversion_factor REAL DEFAULT 1.0,
                        status INTEGER DEFAULT 1)''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS stock_batches (
                        batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        prod_id TEXT, 
                        qty REAL, 
                        expiry_date TEXT,
                        inbound_date TEXT,
                        vendor_name TEXT DEFAULT '',
                        vendor_phone TEXT DEFAULT '',
                        cost REAL DEFAULT 0.0,
                        original_qty REAL DEFAULT 0.0)''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS bom (
                        parent_id TEXT, 
                        child_id TEXT, 
                        qty_needed REAL,
                        PRIMARY KEY (parent_id, child_id))''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, 
                        timestamp TEXT, 
                        user TEXT, 
                        action TEXT, 
                        details TEXT,
                        main_category TEXT DEFAULT '')''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS orders (
                        order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT,
                        user TEXT,
                        total_revenue REAL,
                        total_cost REAL,
                        status INTEGER DEFAULT 1,
                        history_id INTEGER)''')
                        
    cursor.execute('''CREATE TABLE IF NOT EXISTS order_items (
                        item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        order_id INTEGER,
                        prod_id TEXT,
                        prod_name TEXT,
                        qty REAL,
                        price REAL)''')
                        
    cursor.execute('''CREATE TABLE IF NOT EXISTS order_materials (
                        mat_record_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        order_id INTEGER,
                        mat_id TEXT,
                        mat_name TEXT,
                        qty REAL,
                        unit TEXT,
                        deducted_batches_json TEXT)''')
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_timestamp_action ON history (timestamp, action);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_main_category ON history (main_category, timestamp);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_stock_batches_prod_date ON stock_batches (prod_id, inbound_date);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_timestamp_status ON orders (timestamp, status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items (order_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_materials_order_id ON order_materials (order_id);")
        
    conn.commit()
    conn.close()

def log_history(user, action, details, main_category="", shared_cursor=None):
    now = get_taiwan_now().strftime("%Y-%m-%d %H:%M:%S")
    
    if not main_category:
        if action in ['多品項收銀結帳', '多品項收銀結帳-已微調更正', '訂單作廢成功', '更正點餐數量']:
            main_category = "🛒 餐點收銀結帳"
        elif action.startswith("修正餐點參數-"):
            main_category = "⚙️ 餐點參數修正"
        elif action in ['採購進貨', '採購單更正']:
            main_category = "📥 採購進貨登記"
        elif action == "帳單支出登記":
            main_category = "💰 帳單費用登記"
        elif action.startswith("手動調整庫存-") or action.startswith("存貨盤點-") or action.startswith("庫存微調"):
            main_category = "📋 庫存微調/報廢/盤點"

    try:
        if shared_cursor is not None:
            shared_cursor.execute(
                "INSERT INTO history (timestamp, user, action, details, main_category) VALUES (?, ?, ?, ?, ?)", 
                (now, user, action, details, main_category)
            )
            return shared_cursor.lastrowid
        else:
            conn = get_db_conn()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO history (timestamp, user, action, details, main_category) VALUES (?, ?, ?, ?, ?)", 
                (now, user, action, details, main_category)
            )
            last_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return last_id
    except Exception as e:
        print(f"⚠️ log_history 寫入暫時失敗: {e}")
        return None

def deduct_stock_fifo(prod_id, qty_to_deduct, cursor):
    cursor.execute("SELECT batch_id, qty, cost FROM stock_batches WHERE prod_id = ? AND qty > 0 ORDER BY expiry_date ASC, inbound_date ASC", (prod_id,))
    batches = cursor.fetchall()
    total_available = sum([b[1] for b in batches])
    if total_available < qty_to_deduct:
        return False, 0.0, []
    
    remains = qty_to_deduct
    total_deducted_cost = 0.0
    deducted_batches = []
    
    for batch_id, batch_qty, batch_cost in batches:
        if remains <= 0: break
        
        deduct_qty = min(remains, batch_qty)
        total_deducted_cost += deduct_qty * batch_cost
        deducted_batches.append({"batch_id": batch_id, "qty": deduct_qty, "cost": batch_cost})
        
        if batch_qty >= remains:
            cursor.execute("UPDATE stock_batches SET qty = qty - ? WHERE batch_id = ?", (remains, batch_id))
            remains = 0
        else:
            cursor.execute("UPDATE stock_batches SET qty = 0 WHERE batch_id = ?", (batch_id,))
            remains -= batch_qty
            
    return True, total_deducted_cost, deducted_batches

def get_next_raw_id():
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT prod_id FROM products WHERE prod_id LIKE 'R%'")
    ids = cursor.fetchall()
    conn.close()
    max_num = max([int(re.findall(r'\d+', pid)[0]) for (pid,) in ids if re.findall(r'\d+', pid)] + [0])
    return f"R{max_num + 1:04d}"

def get_next_dish_id():
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT prod_id FROM products WHERE prod_id LIKE 'P%'")
    ids = cursor.fetchall()
    conn.close()
    max_num = max([int(re.findall(r'\d+', pid)[0]) for (pid,) in ids if re.findall(r'\d+', pid)] + [0])
    return f"P{max_num + 1:04d}"

def get_next_supply_id():
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT prod_id FROM products WHERE prod_id LIKE 'S%'")
    ids = cursor.fetchall()
    conn.close()
    max_num = max([int(re.findall(r'\d+', pid)[0]) for (pid,) in ids if re.findall(r'\d+', pid)] + [0])
    return f"S{max_num + 1:04d}"

def get_next_bill_id():
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT prod_id FROM products WHERE prod_id LIKE 'C%'")
    ids = cursor.fetchall()
    conn.close()
    max_num = max([int(re.findall(r'\d+', pid)[0]) for (pid,) in ids if re.findall(r'\d+', pid)] + [0])
    return f"C{max_num + 1:04d}"

def update_purchase_batch(batch_id, prod_id, new_original_qty, new_cost, p_unit, u_unit, c_factor, s_stock, v_name, v_phone, exp_str):
    conn = get_db_conn()
    cursor = conn.cursor()
    
    cursor.execute("SELECT qty, original_qty FROM stock_batches WHERE batch_id = ?", (batch_id,))
    batch_info = cursor.fetchone()
    
    if batch_info:
        old_qty = batch_info[0]
        old_orig_qty = batch_info[1] if batch_info[1] > 0 else old_qty
        consumed_qty = max(0.0, old_orig_qty - old_qty)
        new_qty = max(0.0, new_original_qty - consumed_qty)
    else:
        new_qty = new_original_qty
    
    cursor.execute("SELECT SUM(qty), SUM(qty * cost) FROM stock_batches WHERE prod_id = ? AND batch_id != ? AND qty > 0", (prod_id, batch_id))
    other_stock_info = cursor.fetchone()
    other_qty = other_stock_info[0] if (other_stock_info and other_stock_info[0]) else 0.0
    other_val = other_stock_info[1] if (other_stock_info and other_stock_info[1]) else 0.0
    
    final_total_qty = other_qty + new_qty
    final_moving_avg_cost = (other_val + (new_qty * new_cost)) / final_total_qty if final_total_qty > 0 else new_cost

    cursor.execute('''UPDATE products SET 
                        cost = ?, safety_stock = ?, purchase_unit = ?, use_unit = ?, conversion_factor = ?
                      WHERE prod_id = ?''', (final_moving_avg_cost, s_stock, p_unit, u_unit, c_factor, prod_id))
                      
    cursor.execute('''UPDATE stock_batches SET 
                        qty = ?, original_qty = ?, expiry_date = ?, vendor_name = ?, vendor_phone = ?, cost = ?
                      WHERE batch_id = ?''', (new_qty, new_original_qty, exp_str, v_name, v_phone, new_cost, batch_id))
    conn.commit()
    conn.close()

def update_dish_and_bom(dish_id, new_price, recipe_list):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("UPDATE products SET price = ? WHERE prod_id = ?", (new_price, dish_id))
    cursor.execute("DELETE FROM bom WHERE parent_id = ?", (dish_id,))
    for item in recipe_list:
        cursor.execute("INSERT INTO bom VALUES (?, ?, ?)", (dish_id, item['食材編號'], item['單位用量']))
    conn.commit()
    conn.close()

def trigger_toast(text, icon="🔔"):
    st.session_state.toast_queue = {"text": text, "icon": icon}

def show_pending_toast():
    if 'toast_queue' in st.session_state and st.session_state.toast_queue:
        q = st.session_state.toast_queue
        st.toast(q["text"], icon=q["icon"])
        st.session_state.toast_queue = None


# ============================================================================
# 集中快取定義區（以下為新移入之快取函式，皆清楚備註對應分頁）
# ============================================================================

@st.cache_data()
def cached_fetch_safety_items():
    """快取獲取供安全庫存設定的品項列表 (R和S)"""
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM products WHERE status = 1 AND (prod_id LIKE 'R%' OR prod_id LIKE 'S%')")
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return pd.DataFrame(rows, columns=cols)

@st.cache_data(ttl=60)
def cached_fetch_low_stock_alerts():
    """快取檢測低庫存補貨預警"""
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT p.prod_name, 
               COALESCE((SELECT SUM(s.qty) FROM stock_batches s WHERE s.prod_id = p.prod_id AND s.qty > 0), 0) as total_qty, 
               p.safety_stock, p.use_unit
        FROM products p 
        WHERE p.status = 1 AND (p.prod_id LIKE 'R%' OR p.prod_id LIKE 'S%')
        GROUP BY p.prod_id 
        HAVING total_qty < p.safety_stock
    ''')
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return pd.DataFrame(rows, columns=cols)

@st.cache_data(ttl=30)
def cached_fetch_merged_stock(stock_filter):
    """快取獲取目前庫存明細"""
    if stock_filter == "僅看食材 (R)":
        query_condition = "WHERE p.prod_id LIKE 'R%'"
    elif stock_filter == "僅看用品 (S)":
        query_condition = "WHERE p.prod_id LIKE 'S%'"
    else:
        query_condition = "WHERE (p.prod_id LIKE 'R%' OR p.prod_id LIKE 'S%')"

    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute(f'''
        SELECT p.prod_id as 編號, 
               p.prod_name as 商品名稱, 
               COALESCE(SUM(CASE WHEN s.qty > 0 THEN s.qty ELSE 0 END), 0) as 總庫存量, 
               p.use_unit as 單位, 
               CASE 
                 WHEN COALESCE(SUM(CASE WHEN s.qty > 0 THEN s.qty ELSE 0 END), 0) > 0 
                 THEN (SUM(CASE WHEN s.qty > 0 THEN s.qty * s.cost ELSE 0 END) / SUM(CASE WHEN s.qty > 0 THEN s.qty ELSE 0 END))
                 ELSE p.cost 
               END as 移動平均單位成本, 
               COALESCE(SUM(CASE WHEN s.qty > 0 THEN s.qty * s.cost ELSE 0 END), 0) as 庫存總價值,
               p.safety_stock as 安全庫存, 
               p.status as 狀態碼
        FROM products p 
        LEFT JOIN stock_batches s ON p.prod_id = s.prod_id
        {query_condition}
        GROUP BY p.prod_id, p.prod_name, p.use_unit, p.safety_stock, p.status
        ORDER BY p.status DESC, p.prod_id
    ''')
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return pd.DataFrame(rows, columns=cols)

@st.cache_data(ttl=120)
def cached_fetch_batch_details(target_prod_id):
    """快取獲取指定品項的有效進貨批次明細"""
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.batch_id as 批次編號, 
               s.inbound_date as 進貨日期, 
               s.qty as 剩餘庫存量, 
               (s.qty * s.cost) as 當次進貨總金額,
               s.expiry_date as 有效期限, 
               s.vendor_name as 原始供應商,
               s.vendor_phone as 供應商電話
        FROM stock_batches s
        WHERE s.prod_id = ? AND s.qty > 0
        ORDER BY s.inbound_date ASC, s.batch_id ASC
    ''', (target_prod_id,))
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return pd.DataFrame(rows, columns=cols)

@st.cache_data()
def cached_fetch_disabled_items_with_stock():
    """快取獲取包含殘留庫存的已下架商品清單"""
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DISTINCT p.prod_id, p.prod_name
        FROM products p
        JOIN stock_batches s ON p.prod_id = s.prod_id
        WHERE p.status = 0 AND s.qty > 0
        ORDER BY p.prod_id
    ''')
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return pd.DataFrame(rows, columns=cols)

@st.cache_data()
def cached_fetch_disabled_batches(target_disabled_prod_id):
    """快取獲取特定下架品項的殘留批次明細"""
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.batch_id, s.qty, s.original_qty, p.use_unit, s.inbound_date, s.expiry_date
        FROM stock_batches s 
        JOIN products p ON s.prod_id = p.prod_id 
        WHERE s.prod_id = ? AND s.qty > 0
        ORDER BY s.inbound_date ASC, s.batch_id ASC
    ''', (target_disabled_prod_id,))
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return pd.DataFrame(rows, columns=cols)

@st.cache_data()
def cached_fetch_active_dishes():
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT prod_id, prod_name, price FROM products WHERE status = 1 AND prod_id LIKE 'P%'")
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return pd.DataFrame(rows, columns=cols)

@st.cache_data(ttl=30)
def cached_fetch_active_materials():
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT prod_id, prod_name, use_unit, cost FROM products WHERE status = 1 AND (prod_id LIKE 'R%' OR prod_id LIKE 'S%')")
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return pd.DataFrame(rows, columns=cols)

@st.cache_data(ttl=30)
def cached_fetch_today_orders(start_str, end_str):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, timestamp, user, details FROM history 
        WHERE action IN ('多品項收銀結帳', '更正點餐數量') AND timestamp BETWEEN ? AND ?
        ORDER BY id DESC
    ''', (start_str, end_str))
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return pd.DataFrame(rows, columns=cols)

@st.cache_data()
def cached_fetch_dish_bom_recipe(target_dish_id):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT p.prod_name as 食材名稱, b.child_id as 食材編號, b.qty_needed as 單位用量, p.use_unit as 單位
        FROM bom b JOIN products p ON b.child_id = p.prod_id WHERE b.parent_id = ?
    ''', (target_dish_id,))
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return pd.DataFrame(rows, columns=cols)

@st.cache_data()
def cached_fetch_all_dishes_raw():
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT prod_id, prod_name,cost, price, status FROM products WHERE prod_id LIKE 'P%'")
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return pd.DataFrame(rows, columns=cols)

@st.cache_data()
def cached_fetch_all_materials_raw():
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT prod_id, prod_name, use_unit, cost, status FROM products WHERE prod_id LIKE 'R%' OR prod_id LIKE 'S%'")
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return pd.DataFrame(rows, columns=cols)

@st.cache_data()
def cached_fetch_existing_items_for_po(prefix):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT prod_id, prod_name, purchase_unit, use_unit, conversion_factor, safety_stock, status FROM products WHERE prod_id LIKE ?", 
        (f"{prefix}%",)
    )
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return pd.DataFrame(rows, columns=cols)

@st.cache_data()
def cached_fetch_history_batches(where_clause, params_tuple):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute(f'''
        SELECT s.batch_id as 批次編號, s.prod_id as 商品編號, p.prod_name as 商品名稱, 
               s.qty as 當前小單位庫存, s.original_qty as 原始小單位庫存, p.purchase_unit as 進貨單位, p.use_unit as 使用單位,
               p.conversion_factor as 轉換率, (s.original_qty / p.conversion_factor) as 進貨大包裝數,
               (s.qty / p.conversion_factor) as 剩餘大包裝數,
               (s.qty * s.cost) as 推估總金額, s.expiry_date as 有效期限, s.vendor_name as 供應商, s.vendor_phone as 供應商電話,
               s.inbound_date as 進貨日期, p.safety_stock as 安全庫存
        FROM stock_batches s JOIN products p ON s.prod_id = p.prod_id 
        {where_clause}
        ORDER BY s.batch_id DESC
    ''', list(params_tuple))
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return pd.DataFrame(rows, columns=cols)

@st.cache_data(ttl=60)
def cached_fetch_unique_items_to_adjust(prefix):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DISTINCT p.prod_id, p.prod_name 
        FROM products p
        JOIN stock_batches s ON p.prod_id = s.prod_id
        WHERE p.prod_id LIKE ? AND p.status = 1 AND s.qty > 0
        ORDER BY p.prod_id
    ''', (prefix,))
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return pd.DataFrame(rows, columns=cols)

@st.cache_data(ttl=60)
def cached_fetch_batches_by_prod(target_prod_id):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.batch_id, s.qty, p.use_unit, s.expiry_date, s.inbound_date, s.vendor_name 
        FROM stock_batches s 
        JOIN products p ON s.prod_id = p.prod_id
        WHERE s.prod_id = ? AND s.qty > 0
        ORDER BY s.expiry_date ASC, s.inbound_date ASC
    ''', (target_prod_id,))
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return pd.DataFrame(rows, columns=cols)

@st.cache_data()
def cached_fetch_products_in_stock_for_audit(prefix):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DISTINCT s.prod_id as 商品編號, p.prod_name as 商品名稱 
        FROM stock_batches s 
        JOIN products p ON s.prod_id = p.prod_id 
        WHERE s.prod_id LIKE ? AND s.qty > 0
    ''', (prefix,))
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return pd.DataFrame(rows, columns=cols)

@st.cache_data(ttl=60)
def cached_fetch_batch_details_for_audit(target_prod_id):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.batch_id, s.qty, s.expiry_date, s.inbound_date, s.vendor_name, s.vendor_phone, p.use_unit, p.cost
        FROM stock_batches s
        JOIN products p ON s.prod_id = p.prod_id
        WHERE s.prod_id = ? AND s.qty > 0
        ORDER BY s.inbound_date ASC, s.batch_id ASC
    ''', (target_prod_id,))
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return pd.DataFrame(rows, columns=cols)

@st.cache_data(ttl=60)
def cached_fetch_audit_history(start_str, end_str, selected_main_action):
    conn = get_db_conn()
    cursor = conn.cursor()
    
    sql_query = "SELECT timestamp AS 時間, user AS 操作人, action AS 動作, details AS 詳細說明 FROM history WHERE timestamp BETWEEN ? AND ?"
    sql_params = [start_str, end_str]

    if selected_main_action != "--- 全部動作項目 ---":
        sql_query += " AND main_category = ?"
        sql_params.append(selected_main_action)

    sql_query += " ORDER BY id DESC"

    cursor.execute(sql_query, sql_params)
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return pd.DataFrame(rows, columns=cols)

@st.cache_data(ttl=60)
def cached_get_sales_summary(start_str, end_str):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT COALESCE(SUM(total_revenue), 0.0) AS rev, COALESCE(SUM(total_cost), 0.0) AS cst
        FROM orders 
        WHERE status = 1 AND timestamp BETWEEN ? AND ?
    ''', (start_str, end_str))
    r = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return pd.DataFrame(r, columns=cols)

@st.cache_data(ttl=60)
def cached_get_dish_rank(start_str, end_str):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT oi.prod_name AS 餐點名稱, SUM(oi.qty) AS 銷售份數
        FROM order_items oi
        JOIN orders o ON oi.order_id = o.order_id
        WHERE o.status = 1 AND o.timestamp BETWEEN ? AND ?
        GROUP BY oi.prod_name
        ORDER BY 銷售份數 DESC
    ''', (start_str, end_str))
    r = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return pd.DataFrame(r, columns=cols)

@st.cache_data(ttl=60)
def cached_get_material_usage(start_str, end_str):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT om.mat_name AS 食材物料, SUM(om.qty) AS 消耗總數量
        FROM order_materials om
        JOIN orders o ON om.order_id = o.order_id
        WHERE o.status = 1 AND o.timestamp BETWEEN ? AND ?
        GROUP BY om.mat_name
        ORDER BY 消耗總數量 DESC
    ''', (start_str, end_str))
    r = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return pd.DataFrame(r, columns=cols)

@st.cache_data(ttl=60)
def cached_get_expenses_raw():
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT action, details, timestamp FROM history WHERE action LIKE '手動調整庫存-%'")
    r = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return pd.DataFrame(r, columns=cols)

@st.cache_data(ttl=60)
def cached_get_actual_purchase_details(start_date_str, end_date_str):
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.batch_id, s.prod_id, p.prod_name, s.original_qty, s.cost, s.inbound_date, p.purchase_unit
        FROM stock_batches s
        JOIN products p ON s.prod_id = p.prod_id
        WHERE (s.prod_id LIKE 'R%' OR s.prod_id LIKE 'S%' OR s.prod_id LIKE 'C%')
          AND s.inbound_date BETWEEN ? AND ?
        ORDER BY s.inbound_date DESC, s.batch_id DESC
    ''', (start_date_str, end_date_str))
    r = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return pd.DataFrame(r, columns=cols)

@st.cache_data(ttl=60)
def cached_get_operational_expenses_base():
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT s.batch_id, s.prod_id, p.prod_name, s.qty, s.cost, s.inbound_date FROM stock_batches s JOIN products p ON s.prod_id = p.prod_id WHERE s.prod_id LIKE 'C%'")
    r_cb = cursor.fetchall()
    cols_cb = [desc[0] for desc in cursor.description]
    
    cursor.execute("SELECT id, action, details, timestamp FROM history WHERE details LIKE '%目標歸帳月份:%' ORDER BY id ASC")
    r_ch = cursor.fetchall()
    cols_ch = [desc[0] for desc in cursor.description]
    conn.close()
    return pd.DataFrame(r_cb, columns=cols_cb), pd.DataFrame(r_ch, columns=cols_ch)

def auto_recovery_monitor():
    """
    15秒網頁前端自動計時自救器
    核心邏輯：如果網頁卡死超過15秒，透過瀏覽器 JavaScript 強制加上清快取參數重整。
    """
    # 1. 優先檢查網址參數是否有強制清理訊號
    query_params = st.query_params
    if "clear_cache" in query_params:
        st.cache_data.clear()       # 清除全域快取
        st.session_state.clear()    # 清除死鎖的 state
        st.query_params.clear()     # 清空網址參數避免無限循環
        st.rerun()

    # 2. 嵌入前端 JavaScript 15秒計時自救 html (微調優化版)
    st.components.v1.html("""
    <script>
        // 1. 啟動 15 秒倒數計時器
        window.streamlitTimeout = setTimeout(function() {
            var url = new URL(window.location.href);
            // 防無限循環：只有在網址還沒有 clear_cache 時才加上並跳轉
            if (!url.searchParams.get('clear_cache')) {
                url.searchParams.set('clear_cache', 'true');
                // 強制 Streamlit 最外層的母視窗跳轉網址
                if (window.parent) {
                    window.parent.location.href = url.href;
                } else {
                    window.location.href = url.href;
                }
            }
        }, 15000); // 15000 毫秒 = 15 秒

        # 2. 安全防護：不論是普通載入還是 Streamlit 內部的渲染完成，只要有反應就立刻取消計時
        window.addEventListener('load', function() {
            clearTimeout(window.streamlitTimeout);
        });
        window.parent.addEventListener('message', function(e) {
            if (e.data && e.data.type === 'streamlit:render') {
                clearTimeout(window.streamlitTimeout);
            }
        });
    </script>
    """, height=0, width=0) # 設定寬高為 0，完全不影響老闆娘看畫面排版


def setup_sidebar(is_home=False):
    """全站共用的側邊欄設定"""
    # 1. 確保全域變數 current_user 隨時都有初始預設值
    if 'current_user' not in st.session_state:
        st.session_state.current_user = "老闆娘"

    st.sidebar.markdown("---")
    st.sidebar.subheader("👤 操作人員資訊")
    
    if is_home:
        # 🌟 如果是首頁：顯示下拉選單讓老闆娘切換人員
        user_list = ["老闆娘", "老闆", "育睿", "堃原", "芹媖"]
        
        # 為了防止切換頁面時丟失選擇，先計算當前使用者在選單中的位置（Index）
        try:
            default_idx = user_list.index(st.session_state.current_user)
        except ValueError:
            default_idx = 0
            
        # 關鍵：這裡的 key 改用 "user_select_widget"，不要直接用 "current_user"
        selected_user = st.sidebar.selectbox(
            "請選擇目前操作人員：", 
            options=user_list, 
            index=default_idx,
            key="user_select_widget"
        )
        
        # 手動將選單結果同步到全域持久的變數中
        st.session_state.current_user = selected_user
    else:
        # 🌟 如果是其他功能分頁：不顯示選單（防止丟失狀態），只用純文字漂亮地秀出是誰
        st.sidebar.markdown(f"當前操作人員：**{st.session_state.current_user}**")
        st.sidebar.caption("💡 如需更換人員，請返回「首頁」調整")
        
    st.sidebar.markdown("---")  