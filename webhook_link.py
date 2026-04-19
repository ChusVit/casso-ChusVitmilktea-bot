import os
import json
import logging
import sqlite3
from fastapi import FastAPI, Request, HTTPException
from payos import PayOS
from dotenv import load_dotenv
import httpx 
from database.db_handler import DB_PATH, get_telegram_id_by_order_id

load_dotenv()

app = FastAPI()

# Cấu hình payOS
payos = PayOS(
    client_id=os.getenv("PAYOS_CLIENT_ID"),
    api_key=os.getenv("PAYOS_API_KEY"),
    checksum_key=os.getenv("PAYOS_CHECKSUM_KEY")
)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# --- HÀM TRỢ GIÚP DATABASE ---
def update_db_and_get_user(description):
    """Cập nhật trạng thái và lấy telegram_id để báo tin"""
    try:
        print(f"DEBUG - Description nhận được: '{description}'")
        
        # Trích xuất mã đơn hàng từ description (Ví dụ: "CSZZ67RVHR5 PAYOd038" -> "Od-038")
        if "PAY" in description:
            # Lấy phần sau "PAY" (VD: "Od038")
            idx = description.find("PAY") + 3
            remaining = description[idx:]
            # Lọc chỉ lấy ký tự chữ và số
            safe_id = ''.join(c for c in remaining if c.isalnum())
            # Format: "Od038" -> "Od-038"
            order_id = f"{safe_id[:2]}-{safe_id[2:]}"
        else:
            print(f"❌ Không tìm thấy 'PAY' trong description!")
            return None, None
        
        print(f"DEBUG - safe_id: '{safe_id}'")
        print(f"DEBUG - order_id trích xuất: '{order_id}'")
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 1. Cập nhật trạng thái thành 'Đã thanh toán'
        cursor.execute("UPDATE orders SET status = 'Đã thanh toán' WHERE order_id = ?", (order_id,))
        conn.commit()
        
        # 2. Lấy telegram_id từ order_id
        user_id = get_telegram_id_by_order_id(order_id)
        print(f"DEBUG - user_id: {user_id}")
        
        conn.close()
        return user_id, order_id
    except Exception as e:
        print(f"❌ Lỗi Database Webhook: {e}")
        return None, None

async def notify_telegram_user(user_id, order_id):
    """Gửi tin nhắn báo thành công trực tiếp cho khách"""
    if not user_id: return
    
    try:
        async with httpx.AsyncClient() as client:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            text = f"✅ **THANH TOÁN THÀNH CÔNG!**\n\nDạ Vịt đã nhận được tiền cho đơn hàng **{order_id}**. Quán đang bắt đầu làm món, Bạn đợi xíu nhé! 🧋✨"
            data = {"chat_id": user_id, "text": text, "parse_mode": "Markdown"}
            await client.post(url, json=data)
            print(f"🚀 Đã gửi tin nhắn xác nhận cho khách {user_id}")
    except Exception as e:
        print(f"⚠️ Lỗi gửi thông báo Telegram: {e}")

@app.post("/payos-webhook")
async def payos_webhook(request: Request):
    body = await request.json()
    try:
        # 1. Xác thực Webhook (dùng API mới thay vì deprecated)
        webhook_data = payos.webhooks.verify(body)
        description = webhook_data.description
        
        # 2. Cập nhật DB và lấy thông tin khách
        user_id, order_id = update_db_and_get_user(description)
        
        if order_id:
            print(f"🔔 Đơn hàng {order_id} đã thanh toán xong!")
            # 3. Nhắn tin cho khách ngay lập tức
            await notify_telegram_user(user_id, order_id)
            return {"status": "success"}
        
        return {"status": "order_not_found"}
        
    except Exception as e:
        print(f"⚠️ Webhook Error: {e}")
        raise HTTPException(status_code=400, detail="Invalid Webhook Data")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)