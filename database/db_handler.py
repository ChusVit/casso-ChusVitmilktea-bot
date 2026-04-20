import os
import re
import json
import psycopg2
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.environ.get("DATABASE_URL")

def get_connection():
    """Tạo kết nối tới PostgreSQL"""
    return psycopg2.connect(DB_URL)

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        telegram_id BIGINT,
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
    
    # ĐÃ SỬA: Thêm buyer_telegram_id vào bảng orders
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS orders (
        order_id TEXT PRIMARY KEY,
        order_details TEXT,
        user_id TEXT,
        buyer_telegram_id BIGINT, 
        total_amount INTEGER,
        status TEXT DEFAULT 'Đang chờ thanh toán',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    ''')
    
    # ĐÃ SỬA: Tự động cập nhật thêm cột nếu bảng orders đã tồn tại từ trước
    cursor.execute('ALTER TABLE orders ADD COLUMN IF NOT EXISTS buyer_telegram_id BIGINT')

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
    cursor.execute("SELECT item_id FROM items WHERE item_name = %s LIMIT 1", (item_name,))
    row = cursor.fetchone()
    if row: return row[0]
    cursor.execute("SELECT COUNT(DISTINCT item_name) FROM items")
    count = cursor.fetchone()[0] + 1
    return f"Itm-{count:03d}"

def check_customer_db(phone_number):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT full_name, membership, point FROM users WHERE phone_number = %s", (phone_number,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {"is_exists": True, "name": row[0], "membership": row[1], "point": row[2]}
    return {"is_exists": False}

def get_telegram_id_by_order_id(order_id):
    """Lấy telegram_id của người trực tiếp đặt đơn"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT buyer_telegram_id FROM orders WHERE order_id = %s", (order_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def process_checkout(telegram_id, telegram_name, phone_number, customer_name, is_agree_membership, total_amount, items_list):
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        points_earned = int(total_amount / 10000) * 1000
        
        cursor.execute("SELECT user_id, membership FROM users WHERE phone_number = %s", (phone_number,))
        row = cursor.fetchone()
        
        if row: # KHÁCH CŨ
            user_id = row[0]
            current_membership = row[1]
            
            new_membership = 'Yes' if (is_agree_membership or current_membership == 'Yes') else 'No'
            points_to_add = points_earned if new_membership == 'Yes' else 0
            
            # ĐÃ SỬA: KHÔNG CẬP NHẬT telegram_id, user_name, full_name ĐỂ BẢO TOÀN CHỦ SĐT
            cursor.execute('''
                UPDATE users 
                SET membership = %s, point = point + %s, 
                    total_order = total_order + 1, total_spent = total_spent + %s
                WHERE user_id = %s
            ''', (new_membership, points_to_add, total_amount, user_id))
        else: # KHÁCH MỚI
            cursor.execute("SELECT COUNT(*) FROM users")
            user_id = f"KH-{cursor.fetchone()[0] + 1:03d}"
            
            new_membership = 'Yes' if is_agree_membership else 'No'
            initial_point = points_earned if new_membership == 'Yes' else 0
            
            cursor.execute('''
                INSERT INTO users (user_id, telegram_id, phone_number, full_name, user_name, membership, point, total_order, total_spent) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s)
            ''', (user_id, telegram_id, phone_number, customer_name, telegram_name, new_membership, initial_point, total_amount))

        # ĐÃ SỬA: Lưu order_id kèm buyer_telegram_id (telegram_id của người đang chat)
        cursor.execute("SELECT COUNT(*) FROM orders")
        order_id = f"Od-{cursor.fetchone()[0] + 1:03d}"
        cursor.execute('''
            INSERT INTO orders (order_id, order_details, user_id, buyer_telegram_id, total_amount) 
            VALUES (%s, %s, %s, %s, %s)
        ''', (order_id, json.dumps(items_list, ensure_ascii=False), user_id, telegram_id, total_amount))

        current_month = datetime.now().strftime("%m/%Y")
        for item in items_list:
            item_id = get_or_create_item_id(item.get('item_name'), cursor)
            qty = item.get('quantity', 1)
            item_total = item.get('price', 0) * qty
            cursor.execute('''
                INSERT INTO items (item_id, item_name, month, size, price, sales_quantity, total_amount)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (item_name, month, size) DO UPDATE SET
                sales_quantity = items.sales_quantity + EXCLUDED.sales_quantity, 
                total_amount = items.total_amount + EXCLUDED.total_amount
            ''', (item_id, item.get('item_name'), current_month, item.get('size', 'None'), item.get('price', 0), qty, item_total))

        conn.commit()
        return order_id, user_id
    except Exception as e:
        print(f"Lỗi Database: {e}")
        conn.rollback() 
        return None, None
    finally:
        conn.close()

def cancel_order_if_unpaid(order_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE orders SET status = 'Hủy' WHERE order_id = %s AND status = 'Đang chờ thanh toán'", (order_id,))
    conn.commit()
    conn.close()

def update_order_status(order_id, status="Đã thanh toán"):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE orders SET status = %s WHERE order_id = %s", (status, order_id))
    conn.commit()
    conn.close()

def update_db_and_get_user(description):
    """Cập nhật trạng thái và lấy telegram_id để báo tin"""
    try:
        # Tìm chính xác chuỗi bắt đầu bằng PAYOd và theo sau là các con số
        match = re.search(r'PAY(Od\d+)', description, re.IGNORECASE)
        
        if not match:
            print(f"⚠️ Webhook: Không tìm thấy mã đơn hàng hợp lệ trong nội dung: {description}")
            return None, None
            
        safe_id = match.group(1) # Kết quả sẽ rút ra chuẩn xác chữ "Od002"
        order_id = f"{safe_id[:2]}-{safe_id[2:]}" # Định dạng lại thành "Od-002"
        
        conn = get_connection()
        cursor = conn.cursor()
        
        # Cập nhật trạng thái
        cursor.execute("UPDATE orders SET status = 'Đã thanh toán' WHERE order_id = %s", (order_id,))
        
        if cursor.rowcount == 0:
            print(f"⚠️ Webhook: Không tìm thấy đơn hàng {order_id} trong database.")
        else:
            print(f"✅ Webhook: Đã cập nhật thành công Database cho đơn {order_id}.")
            
        conn.commit()
        conn.close()
        
        user_id = get_telegram_id_by_order_id(order_id)
        return user_id, order_id
    except Exception as e:
        print(f"Lỗi Database Webhook: {e}")
        return None, None

if __name__ == "__main__":
    init_db()