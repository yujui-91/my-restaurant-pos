# database/db_core.py
import sqlite3
import re
from datetime import datetime, timedelta

def init_db():
    conn = sqlite3.connect('inventory.db')
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
    
    # 檢查是否需要升級舊資料庫（補上 status 欄位）
    cursor.execute("PRAGMA table_info(products)")
    columns = [info[1] for info in cursor.fetchall()]
    if 'status' not in columns:
        cursor.execute("ALTER TABLE products ADD COLUMN status INTEGER DEFAULT 1")
        cursor.execute("UPDATE products SET status = 0, price = 100.0 WHERE price = -1.0")
        cursor.execute("UPDATE products SET status = 0, price = 0.0 WHERE price = -2.0")
    
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
    
    # 檢查是否需要升級舊資料庫（補上 stock_batches 的 cost 與 original_qty 欄位）
    cursor.execute("PRAGMA table_info(stock_batches)")
    sb_columns = [info[1] for info in cursor.fetchall()]
    if 'cost' not in sb_columns:
        cursor.execute("ALTER TABLE stock_batches ADD COLUMN cost REAL DEFAULT 0.0")
        cursor.execute("UPDATE stock_batches SET cost = COALESCE((SELECT cost FROM products WHERE products.prod_id = stock_batches.prod_id), 0.0)")
    if 'original_qty' not in sb_columns:
        cursor.execute("ALTER TABLE stock_batches ADD COLUMN original_qty REAL DEFAULT 0.0")
        cursor.execute("UPDATE stock_batches SET original_qty = qty")
    
    # 3. BOM 組裝配方表
    cursor.execute('''CREATE TABLE IF NOT EXISTS bom (
                        parent_id TEXT, 
                        child_id TEXT, 
                        qty_needed REAL,
                        PRIMARY KEY (parent_id, child_id))''')
    
    # 4. 歷史紀錄表 (原有欄位完全不變，維持對既有資料的向下相容)
    cursor.execute('''CREATE TABLE IF NOT EXISTS history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, 
                        timestamp TEXT, 
                        user TEXT, 
                        action TEXT, 
                        details TEXT)''')
    
    # 預設測試資料
    cursor.execute("SELECT COUNT(*) FROM products")
    if cursor.fetchone()[0] == 0:
        today = datetime.now().strftime("%Y-%m-%d")
        exp_1 = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        exp_2 = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        
        cursor.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            ('R001', '澳洲牛肉', 0.5, 0.0, 1000, '箱(20kg)', 'g', 20000.0, 1),
            ('R002', '麵條', 5.0, 0.0, 50, '箱(100份)', '份', 100.0, 1),
            ('R003', '高湯', 0.02, 0.0, 5000, '桶(20L)', 'ml', 20000.0, 1),
            ('R004', '蔥花', 0.1, 0.0, 200, '袋(1kg)', 'g', 1000.0, 1),
            ('S001', '外帶紙盒', 3.5, 0.0, 100, '束(50個)', '個', 50.0, 1),
            ('S002', '免洗筷', 0.5, 0.0, 200, '包(100雙)', '雙', 100.0, 1),
            ('P001', '招牌牛肉麵(成品)', 0.0, 180.0, 0, '碗', '碗', 1.0, 1)
        ])
        
        cursor.executemany("INSERT INTO stock_batches (prod_id, qty, expiry_date, inbound_date, vendor_name, vendor_phone, cost, original_qty) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
            ('R001', 500, exp_1, today, '豪好吃肉品批發', '0912-345678', 0.5, 500.0),   
            ('R001', 2000, exp_2, today, '豪好吃肉品批發', '0912-345678', 0.5, 2000.0),  
            ('R002', 60, exp_2, today, '大豐製麵廠', '', 5.0, 60.0), 
            ('R003', 10000, exp_2, today, '', '', 0.02, 10000.0),         
            ('R004', 150, exp_1, today, '全聯農產', '02-22334455', 0.1, 150.0),
            ('S001', 100, '', today, '大同包裝材料行', '0988-111222', 3.5, 100.0), 
            ('S002', 200, '', today, '大同包裝材料行', '0988-111222', 0.5, 200.0)
        ])
        
        cursor.executemany("INSERT INTO bom VALUES (?, ?, ?)", [
            ('P001', 'R001', 150.0),
            ('P001', 'R002', 1.0),
            ('P001', 'R003', 300.0),
            ('P001', 'R004', 5.0)
        ])
        
    conn.commit()
    conn.close()

def log_history(user, action, details):
    conn = sqlite3.connect('inventory.db')
    cursor = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("INSERT INTO history (timestamp, user, action, details) VALUES (?, ?, ?, ?)", (now, user, action, details))
    conn.commit()
    conn.close()

def deduct_stock_fifo(prod_id, qty_to_deduct, cursor):
    cursor.execute("SELECT batch_id, qty, cost FROM stock_batches WHERE prod_id = ? AND qty > 0 ORDER BY expiry_date ASC, inbound_date ASC", (prod_id,))
    batches = cursor.fetchall()
    total_available = sum([b[1] for b in batches])
    if total_available < qty_to_deduct:
        return False, 0.0
    
    remains = qty_to_deduct
    total_deducted_cost = 0.0
    
    for batch_id, batch_qty, batch_cost in batches:
        if remains <= 0: break
        
        deduct_qty = min(remains, batch_qty)
        total_deducted_cost += deduct_qty * batch_cost
        
        if batch_qty >= remains:
            cursor.execute("UPDATE stock_batches SET qty = qty - ? WHERE batch_id = ?", (remains, batch_id))
            remains = 0
        else:
            cursor.execute("UPDATE stock_batches SET qty = 0 WHERE batch_id = ?", (batch_id,))
            remains -= batch_qty
            
    return True, total_deducted_cost

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

def update_purchase_batch(batch_id, prod_id, new_original_qty, new_cost, p_unit, u_unit, c_factor, s_stock, v_name, v_phone, exp_str):
    conn = sqlite3.connect('inventory.db')
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
    conn = sqlite3.connect('inventory.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE products SET price = ? WHERE prod_id = ?", (new_price, dish_id))
    cursor.execute("DELETE FROM bom WHERE parent_id = ?", (dish_id,))
    for item in recipe_list:
        cursor.execute("INSERT INTO bom VALUES (?, ?, ?)", (dish_id, item['食材編號'], item['單位用量']))
    conn.commit()
    conn.close()

def trigger_toast(text, icon="🔔"):
    import streamlit as st
    st.session_state.toast_queue = {"text": text, "icon": icon}

def show_pending_toast():
    import streamlit as st
    if 'toast_queue' in st.session_state and st.session_state.toast_queue:
        q = st.session_state.toast_queue
        st.toast(q["text"], icon=q["icon"])
        st.session_state.toast_queue = None