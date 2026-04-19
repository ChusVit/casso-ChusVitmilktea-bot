import sqlite3
import os
import json
from datetime import datetime

os.makedirs("database", exist_ok=True)
DB_PATH = "database/casso_bot.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Đã thêm cột 'point'
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        telegram_id INTEGER,
        phone_number TEXT UNIQUE, 
        full_name TEXT,
        user_name TEXT,
        membership TEXT DEFAULT 'No',
        point INTEGER DEFAULT 0,
        total_order INTEGER DEFAULT 0,
        total_spent INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS orders (
        order_id TEXT PRIMARY KEY,
        order_details TEXT,
        user_id TEXT,
        total_amount INTEGER,
        status TEXT DEFAULT 'Đang chờ thanh toán',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    ''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS items (
        item_id TEXT,
        item_name TEXT,
        month TEXT,
        size TEXT,
        price INTEGER,
        sales_quantity INTEGER DEFAULT 0,
        total_amount INTEGER DEFAULT 0,
        PRIMARY KEY (item_name, month, size)
    )
    ''')
    conn.commit()
    conn.close()

def get_or_create_item_id(item_name, cursor):
    cursor.execute("SELECT item_id FROM items WHERE item_name = ? LIMIT 1", (item_name,))
    row = cursor.fetchone()
    if row: return row[0]
    cursor.execute("SELECT COUNT(DISTINCT item_name) FROM items")
    count = cursor.fetchone()[0] + 1
    return f"Itm-{count:03d}"

# HÀM MỚI: Dành cho AI tra cứu trước khi chốt đơn
def check_customer_db(phone_number):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT full_name, membership, point FROM users WHERE phone_number = ?", (phone_number,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {"is_exists": True, "name": row[0], "membership": row[1], "point": row[2]}
    return {"is_exists": False}

def get_telegram_id_by_order_id(order_id):
    """Lấy telegram_id từ order_id"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT u.telegram_id FROM users u
        JOIN orders o ON u.user_id = o.user_id
        WHERE o.order_id = ?
    """, (order_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def process_checkout(telegram_id, telegram_name, phone_number, customer_name, is_agree_membership, total_amount, items_list):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # Tính điểm: 10.000đ = 1000 điểm
        points_earned = int(total_amount / 10000) * 1000
        
        cursor.execute("SELECT user_id, membership FROM users WHERE phone_number = ?", (phone_number,))
        row = cursor.fetchone()
        
        if row: # KHÁCH CŨ
            user_id = row[0]
            current_membership = row[1]
            
            # Nếu trước đây là No, nay đồng ý thì lên Yes. Nếu đã Yes thì giữ Yes.
            new_membership = 'Yes' if (is_agree_membership or current_membership == 'Yes') else 'No'
            points_to_add = points_earned if new_membership == 'Yes' else 0
            
            cursor.execute('''
                UPDATE users 
                SET telegram_id = ?, user_name = ?, full_name = ?, membership = ?,
                    point = point + ?, total_order = total_order + 1, total_spent = total_spent + ?
                WHERE user_id = ?
            ''', (telegram_id, telegram_name, customer_name, new_membership, points_to_add, total_amount, user_id))
        else: # KHÁCH MỚI
            cursor.execute("SELECT COUNT(*) FROM users")
            user_id = f"KH-{cursor.fetchone()[0] + 1:03d}"
            
            new_membership = 'Yes' if is_agree_membership else 'No'
            initial_point = points_earned if new_membership == 'Yes' else 0
            
            cursor.execute('''
                INSERT INTO users (user_id, telegram_id, phone_number, full_name, user_name, membership, point, total_order, total_spent) 
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            ''', (user_id, telegram_id, phone_number, customer_name, telegram_name, new_membership, initial_point, total_amount))

        # Lưu Order
        cursor.execute("SELECT COUNT(*) FROM orders")
        order_id = f"Od-{cursor.fetchone()[0] + 1:03d}"
        cursor.execute('''
            INSERT INTO orders (order_id, order_details, user_id, total_amount) 
            VALUES (?, ?, ?, ?)
        ''', (order_id, json.dumps(items_list, ensure_ascii=False), user_id, total_amount))

        # Lưu Items
        current_month = datetime.now().strftime("%m/%Y")
        for item in items_list:
            item_id = get_or_create_item_id(item.get('item_name'), cursor)
            qty = item.get('quantity', 1)
            item_total = item.get('price', 0) * qty
            cursor.execute('''
                INSERT INTO items (item_id, item_name, month, size, price, sales_quantity, total_amount)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_name, month, size) DO UPDATE SET
                sales_quantity = sales_quantity + ?, total_amount = total_amount + ?
            ''', (item_id, item.get('item_name'), current_month, item.get('size', 'None'), item.get('price', 0), qty, item_total, qty, item_total))

        conn.commit()
        return order_id, user_id
    except Exception as e:
        print(f"Lỗi Database: {e}")
        return None, None
    finally:
        conn.close()

def cancel_order_if_unpaid(order_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE orders SET status = 'Hủy' WHERE order_id = ? AND status = 'Đang chờ thanh toán'", (order_id,))
    conn.commit()
    conn.close()

def update_order_status(order_id, status="Đã thanh toán"):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE orders SET status = ? WHERE order_id = ?", (status, order_id))
    conn.commit()
    conn.close()

def get_telegram_id_by_order_id(order_id):
    """Lấy telegram_id từ order_id"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT u.telegram_id FROM users u
        JOIN orders o ON u.user_id = o.user_id
        WHERE o.order_id = ?
    """, (order_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def update_db_and_get_user(description):
    """Cập nhật trạng thái và lấy telegram_id để báo tin"""
    try:
        safe_id = description.replace("PAY", "")
        order_id = f"{safe_id[:2]}-{safe_id[2:]}"
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("UPDATE orders SET status = 'Đã thanh toán' WHERE order_id = ?", (order_id,))
        conn.commit()
        conn.close()
        
        # Lấy telegram_id từ user thông qua order
        user_id = get_telegram_id_by_order_id(order_id)
        return user_id, order_id
    except Exception as e:
        print(f"❌ Lỗi Database Webhook: {e}")
        return None, None

if __name__ == "__main__":
    init_db()