# database/db_core.py
import sqlite3
import re
from datetime import datetime, timedelta

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
        
        cursor.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
            ('R001', '澳洲牛肉', 0.5, 0.0, 1000, '箱(20kg)', 'g', 20000.0),
            ('R002', '麵條', 5.0, 0.0, 50, '箱(100份)', '份', 100.0),
            ('R003', '高湯', 0.02, 0.0, 5000, '桶(20L)', 'ml', 20000.0),
            ('R004', '蔥花', 0.1, 0.0, 200, '袋(1kg)', 'g', 1000.0),
            ('S001', '外帶紙盒', 3.5, 0.0, 100, '束(50個)', '個', 50.0),
            ('S002', '免洗筷', 0.5, 0.0, 200, '包(100雙)', '雙', 100.0),
            ('P001', '招牌牛肉麵(成品)', 0.0, 180.0, 0, '碗', '碗', 1.0)
        ])
        
        cursor.executemany("INSERT INTO stock_batches (prod_id, qty, expiry_date, inbound_date, vendor_name, vendor_phone) VALUES (?, ?, ?, ?, ?, ?)", [
            ('R001', 500, exp_1, today, '豪好吃肉品批發', '0912-345678'),   
            ('R001', 2000, exp_2, today, '豪好吃肉品批發', '0912-345678'),  
            ('R002', 60, exp_2, today, '大豐製麵廠', ''), 
            ('R003', 10000, exp_2, today, '', ''),         
            ('R004', 150, exp_1, today, '全聯農產', '02-22334455'),
            ('S001', 100, '', today, '大同包裝材料行', '0988-111222'), 
            ('S002', 200, '', today, '大同包裝材料行', '0988-111222')
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
    """
    執行 FIFO 食材扣料，並回傳此原物料在本次扣除中所產生的【總實際成本】
    """
    # 撈出該品項有庫存的批次，並一併取出當時進貨的成本（p.cost 紀錄在商品表，或是此處從某處推算，因產品表成本代表最新單價）
    # 為了最精準，我們結合 products 表與 stock_batches 表
    cursor.execute('''
        SELECT s.batch_id, s.qty, p.cost 
        FROM stock_batches s 
        JOIN products p ON s.prod_id = p.prod_id 
        WHERE s.prod_id = ? AND s.qty > 0 
        ORDER BY s.expiry_date ASC, s.inbound_date ASC
    ''', (prod_id,))
    
    batches = cursor.fetchall()
    total_available = sum([b[1] for b in batches])
    if total_available < qty_to_deduct:
        return False, 0.0  # 庫存不足
    
    remains = qty_to_deduct
    total_deducted_cost = 0.0
    
    for batch_id, batch_qty, batch_cost in batches:
        if remains <= 0: 
            break
        if batch_qty >= remains:
            # 當前批次夠扣
            cursor.execute("UPDATE stock_batches SET qty = qty - ? WHERE batch_id = ?", (remains, batch_id))
            total_deducted_cost += remains * batch_cost
            remains = 0
        else:
            # 當前批次不夠扣，吃乾淨這個批次
            cursor.execute("UPDATE stock_batches SET qty = 0 WHERE batch_id = ?", (batch_id,))
            total_deducted_cost += batch_qty * batch_cost
            remains -= batch_qty
            
    cursor.execute("DELETE FROM stock_batches WHERE qty <= 0")
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

def update_purchase_batch(batch_id, prod_id, new_qty, new_cost, p_unit, u_unit, c_factor, s_stock, v_name, v_phone, exp_str):
    """資深優化：允許更正歷史採購單資訊，連帶更新產品基準成本"""
    conn = sqlite3.connect('inventory.db')
    cursor = conn.cursor()
    
    # 1. 更新產品規格與單價基準
    cursor.execute('''UPDATE products SET 
                        cost = ?, safety_stock = ?, purchase_unit = ?, use_unit = ?, conversion_factor = ?
                      WHERE prod_id = ?''', (new_cost, s_stock, p_unit, u_unit, c_factor, prod_id))
    
    # 2. 更新特定庫存批次的數量與明細
    cursor.execute('''UPDATE stock_batches SET 
                        qty = ?, expiry_date = ?, vendor_name = ?, vendor_phone = ?
                      WHERE batch_id = ?''', (new_qty, exp_str, v_name, v_phone, batch_id))
    
    conn.commit()
    conn.close()

def update_dish_and_bom(dish_id, new_price, recipe_list):
    """更新成品餐點的售價，並重新寫入配方 BOM 表"""
    conn = sqlite3.connect('inventory.db')
    cursor = conn.cursor()
    # 1. 更新價格
    cursor.execute("UPDATE products SET price = ? WHERE prod_id = ?", (new_price, dish_id))
    # 2. 刪除舊有配方
    cursor.execute("DELETE FROM bom WHERE parent_id = ?", (dish_id,))
    # 3. 重新寫入新配方
    for item in recipe_list:
        cursor.execute("INSERT INTO bom VALUES (?, ?, ?)", (dish_id, item['食材編號'], item['單位用量']))
    conn.commit()
    conn.close()