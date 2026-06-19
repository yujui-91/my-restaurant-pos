# database/db_core.py
import re
from datetime import datetime, timedelta
import streamlit as st
import libsql  
import sqlite3
import os
import pandas as pd

def get_db_conn():
    """
    動態獲取資料庫連線：
    優先檢查 Streamlit Secrets 是否有 Turso 設定，若有則連線上資料庫；
    若無（例如在本機開發且沒設定秘密鍵），則連線到本地的 inventory.db。
    """
    # 檢查是否設定了 Turso 的 Secrets
    if "TURSO_DATABASE_URL" in st.secrets and "TURSO_AUTH_TOKEN" in st.secrets:
        import libsql
        
        url = st.secrets["TURSO_DATABASE_URL"]
        token = st.secrets["TURSO_AUTH_TOKEN"]
        
        # 建立 Turso 的標準同步連線
        return libsql.connect(url, auth_token=token)
    else:
        # 降級使用本地資料庫
        db_path = os.path.join(os.path.dirname(__file__), "..", "inventory.db")
        if os.path.exists(db_path):
            return sqlite3.connect(db_path)
        return sqlite3.connect("inventory.db")

def init_db():
    conn = get_db_conn()
    cursor = conn.cursor()
    
    # 1. 商品/物料資料表 (status 欄位：1=啟用, 0=下架/停用)
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
    
    # 2. 庫存批次明細表 (引進 original_qty 欄位防止庫存憑空復活)
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
    
    # 3. BOM 組裝配方表
    cursor.execute('''CREATE TABLE IF NOT EXISTS bom (
                        parent_id TEXT, 
                        child_id TEXT, 
                        qty_needed REAL,
                        PRIMARY KEY (parent_id, child_id))''')
    
    # 4. 歷史紀錄表 (新增大方向分類欄位 main_category 提升多年查詢效能)
    cursor.execute('''CREATE TABLE IF NOT EXISTS history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, 
                        timestamp TEXT, 
                        user TEXT, 
                        action TEXT, 
                        details TEXT,
                        main_category TEXT DEFAULT '')''')

    # ==========================================
    # 🔥 核心優化：建立獨立結構化銷售表（應對多年大數據防卡死）
    # ==========================================
    # 訂單主表 (status: 1=正常, 2=已更正, 0=已作廢)
    cursor.execute('''CREATE TABLE IF NOT EXISTS orders (
                        order_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT,
                        user TEXT,
                        total_revenue REAL,
                        total_cost REAL,
                        status INTEGER DEFAULT 1,
                        history_id INTEGER)''')
                        
    # 銷貨餐點明細表
    cursor.execute('''CREATE TABLE IF NOT EXISTS order_items (
                        item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        order_id INTEGER,
                        prod_id TEXT,
                        prod_name TEXT,
                        qty REAL,
                        price REAL)''')
                        
    # 物料消耗明細表 (包含當次 FIFO 批次扣減資訊的 JSON 結構，以便完美回補或更正)
    cursor.execute('''CREATE TABLE IF NOT EXISTS order_materials (
                        mat_record_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        order_id INTEGER,
                        mat_id TEXT,
                        mat_name TEXT,
                        qty REAL,
                        unit TEXT,
                        deducted_batches_json TEXT)''')
    
    # 建立與升級優化索引（確保大分類等查詢均走高精準索引）
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_timestamp_action ON history (timestamp, action);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_main_category ON history (main_category, timestamp);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_stock_batches_prod_date ON stock_batches (prod_id, inbound_date);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_timestamp_status ON orders (timestamp, status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items (order_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_materials_order_id ON order_materials (order_id);")
        
    conn.commit()
    conn.close()

def log_history(user, action, details, main_category="", shared_cursor=None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 智慧型自動大方向類別歸納（防呆防漏傳補位機制）
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

    # 🔥 關鍵防呆修正：加上 try...except，防止在資料表初始化完成前寫入造成 App 死鎖
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


