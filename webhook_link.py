import os
import logging
from fastapi import FastAPI, Request, HTTPException
from payos import PayOS
from dotenv import load_dotenv
import httpx 
from database.db_handler import update_db_and_get_user

load_dotenv()

app = FastAPI()

app = FastAPI()

@app.get("/")
async def root():
    return {"message": "Server Webhook của quán Trà Sữa ChusVit đang hoạt động mượt mà!"}
# ---------------------------------------------------

# Cấu hình payOS
payos = PayOS(
    client_id=os.getenv("PAYOS_CLIENT_ID"),
    api_key=os.getenv("PAYOS_API_KEY"),
    checksum_key=os.getenv("PAYOS_CHECKSUM_KEY")
)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

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
        # 1. Xác thực Webhook
        webhook_data = payos.webhooks.verify(body)
        description = webhook_data.description
        
        # 2. Cập nhật DB và lấy thông tin khách bằng hàm của db_handler 
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