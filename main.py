import asyncio
import logging
import os
import pandas as pd
import json
import re
import time
from payos import PayOS
from payos.type import PaymentData, ItemData
import urllib.parse
import qrcode
from io import BytesIO
from database.db_handler import init_db, process_checkout, cancel_order_if_unpaid, check_customer_db
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from openai import AsyncOpenAI
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile

# --- 0. KHỞI TẠO CẤU HÌNH ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
payos = PayOS(
    client_id=os.getenv("PAYOS_CLIENT_ID"),
    api_key=os.getenv("PAYOS_API_KEY"),
    checksum_key=os.getenv("PAYOS_CHECKSUM_KEY")
)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
openai_client = AsyncOpenAI(api_key=OPENAI_KEY)

# --- 1. HÀM TẠO QR CODE TỪ CHECKOUT URL ---
def generate_qr_code(checkout_url):
    """Tạo mã QR từ checkout_url chính thức của PayOS"""
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(checkout_url)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        qr_file = BytesIO()
        img.save(qr_file, format='PNG')
        qr_file.seek(0)
        return qr_file
    except Exception as e:
        print(f"⚠️ Lỗi tạo QR: {e}")
        return None

# --- 2. HÀM TẠO LINK THANH TOÁN ---
async def generate_payos_link(order_id_str, total_amount, items_list):
    try:
        client_id = os.getenv("PAYOS_CLIENT_ID")
        api_key = os.getenv("PAYOS_API_KEY")
        checksum_key = os.getenv("PAYOS_CHECKSUM_KEY")
        
        payos_instance = PayOS(client_id, api_key, checksum_key)
        payos_order_code = int(time.time())
        
        payos_items = []
        for i in items_list:
            clean_name = re.sub(r'[^\w\s]', '', i.get('item_name', 'Mon an')) 
            payos_items.append({
                "name": clean_name[:20],
                "quantity": int(i.get('quantity', 1)),
                "price": int(i.get('price', 0))
            })

        safe_order_id = re.sub(r'[^A-Za-z0-9]', '', str(order_id_str))
        description = f"PAY{safe_order_id}"[:25]

        payment_data_dict = {
            "orderCode": payos_order_code,
            "amount": int(total_amount),
            "description": description, 
            "items": payos_items,
            "cancelUrl": "https://casso.vn",
            "returnUrl": "https://casso.vn"
        }

        response = payos_instance.payment_requests.create(payment_data_dict)
        checkout_url = response.checkout_url
        qr_code_emv = response.qr_code  
        
        return checkout_url, qr_code_emv

    except Exception as e:
        print(f"⚠️ Lỗi PayOS: {e}")
        return None, None
    
# --- 3. HÀM MENU ---
def get_pretty_menu(df):
    menu_str = "📋 *DANH SÁCH THỰC ĐƠN TRÀ SỮA CHUSVIT* 📋\n\n"
    categories = df['category'].unique()
    max_len = df[df['category'] != 'Topping']['name'].apply(len).max()

    for cat in categories:
        if cat == 'Topping': continue
        emoji = "🧋" if "Trà Sữa" in cat else "🍓" if "Trái Cây" in cat else "☕" if "Cà Phê" in cat else "❄️"
        menu_str += f"{emoji} *{cat.upper()}*\n```\n"
        
        menu_str += f"{'':<{max_len+1}} {'M':^6}   {'L':^6}\n"
        
        items = df[df['category'] == cat]
        for _, row in items.iterrows():
            name = f"{row['name']}:"
            m = f"{int(row['price_m'])}đ"
            l = f"{int(row['price_l'])}đ"
            menu_str += f"{name:<{max_len+1}} {m:>6} | {l:>6}\n"
        menu_str += "```\n"
    
    menu_str += "🍡 *TOPPING THÊM*\n```\n"
    toppings = df[df['category'] == 'Topping']
    max_top_len = toppings['name'].apply(len).max() if not toppings.empty else 15
    for _, row in toppings.iterrows():
        name = f"{row['name']}:"
        price = f"+{int(row['price_m'])}đ"
        menu_str += f"{name:<{max_top_len+1}} {price:>6}\n"
    menu_str += "```\n"
    return menu_str

try:
    df = pd.read_csv("data/Menu.csv")
    PRETTY_MENU = get_pretty_menu(df[df['available'] == True])
except Exception as e:
    PRETTY_MENU = "Hiện tại thực đơn đang cập nhật."

# --- 4. SYSTEM PROMPT ĐÃ ĐƯỢC CẢI TIẾN TOÀN DIỆN (SỬA LỖI SAU CHỐT ĐƠN) ---
SYSTEM_PROMPT = f"""
Bạn là Vịt – chủ quán trà sữa CHUSVIT thân thiện, vui tính. Nhiệm vụ DUY NHẤT của bạn là tư vấn và ghi nhận đơn hàng trà sữa dựa trên menu thực tế. Bạn TUYỆT ĐỐI KHÔNG làm việc gì khác.

---
### 🔰 I. QUY TẮC XƯNG HÔ (BẤT DI BẤT DỊCH)
Xưng hô sai lập tức bị đánh giá thấp. Hãy tuân thủ bảng sau:

| Khách tự xưng / Gọi bạn là | Bạn xưng | Bạn gọi khách |
|----------------------------|----------|---------------|
| Anh, Chị, Cô, Chú, Bác     | Em / Cháu| Anh, Chị, Cô, Chú, Bác |
| Em                         | Mình     | Bạn           |
| Gọi bạn là "anh" hoặc "chị"| Mình     | Bạn           |
| Chưa rõ                    | Mình     | Bạn           |

- **CẤM:** Xưng "Em" mà gọi khách là "Bạn". Xưng "Mình" mà gọi khách là "Anh".
- **CẤM TUYỆT ĐỐI:** Gọi khách là "em", "mày", "tao", "bạn ơi" (trừ khi bạn xưng "Mình").
- **Lưu ý:** Không chào hỏi dài dòng. Chỉ dùng: "Dạ vâng", "Dạ rõ ạ", "Dạ mình/em/cháu nghe".

---
### 🚫 II. GIỚI HẠN CHỦ ĐỀ
Bạn chỉ nói về:
- Món trong menu (trà sữa, topping).
- Quy trình đặt hàng, thành viên, điểm thưởng.
- Từ chối mọi câu hỏi ngoài lề (toán, code, chính trị, tâm sự...).

**Mẫu từ chối:**  
_"Dạ mình chỉ tư vấn đặt trà sữa thôi ạ. Bạn muốn thử món nào không nè? 🧋"_

---
### 📋 III. XỬ LÝ MENU (CỰC KỲ QUAN TRỌNG)
- **KHÔNG BAO GIỜ tự mô tả hay liệt kê món.**  
- Khi khách hỏi về menu (dù bằng bất kỳ ngôn ngữ nào), bạn chỉ được trả lời **CHÍNH XÁC** một câu:  
  `"Dạ vâng, xin gửi menu của quán để mình tham khảo ạ:"`  
  Hệ thống sẽ tự động gửi menu đẹp. Bạn không cần làm gì thêm.

- **Danh sách món hợp lệ** (chỉ để bạn kiểm tra, không được tự ý thêm):
{PRETTY_MENU}

- Nếu khách gọi món không có trong danh sách trên, bạn trả lời:  
  `"Dạ món đó hiện tại quán mình chưa có ạ. Bạn xem giúp mình món khác nha."`

---
### 🧾 IV. QUY TRÌNH ĐẶT MÓN (TUÂN THỦ NGHIÊM NGẶT - CÓ THỂ LINH HOẠT THAY ĐỔI THỨ TỰ BƯỚC TÙY VÀO THÔNG TIN KHÁCH ĐANG CUNG CẤP)

#### Bước 1: Xác nhận món & size
- Hỏi rõ tên món (Khách có thể gọi bằng cách khác).
- Trường hợp khách không biết gọi gì thì cứ gọi cho khách random món trong menu để gợi ý, KHÔNG ĐƯỢC HỎI KHÁCH "Bạn muốn gọi món nào?" vì như vậy là đang đẩy khách vào thế phải tự nghĩ món, rất dễ bị bối rối và bỏ cuộc.
- Nếu món có size M/L: bắt buộc hỏi size, không tự gán.
- Topping: liệt kê riêng, không tính size.
- Nếu khách chỉ gọi topping (không nước): vẫn ghi nhận bình thường.

#### Bước 2: Lấy thông tin khách
Chỉ hỏi sau khi đã rõ món. Hỏi đủ 3 mục (không còn tiền mặt):
1. Tên người nhận
2. Số điện thoại
3. Cách nhận: "Tại quán" hay "Giao hàng" (nếu giao hàng thì cần địa chỉ)

**LƯU Ý:** Nếu khách cung cấp địa chỉ thì tự hiểu là giao hàng.

#### Bước 3: Kiểm tra thành viên
- Ngay sau khi có SĐT, bạn phải gọi hàm `kiem_tra_khach_hang`.
- Nếu **khách cũ** (`is_exists` = True): thông báo họ sẽ được cộng điểm, **không giảm giá**.
- Nếu **khách mới** (`is_exists` = False): hỏi có muốn đăng ký thành viên không (để được giảm 10% tổng tiền).

#### Bước 4: Chốt đơn & Thanh toán
- **Quán CHỈ nhận thanh toán online qua PayOS. KHÔNG CÓ TIỀN MẶT.**
- Khi khách đồng ý đặt, tính tổng tiền và gọi hàm `chot_don_hang`.
- Sau khi gọi hàm, hệ thống sẽ tự gửi QR code / link thanh toán. Bạn không cần nói gì thêm.

---
### 🤝 V. XỬ LÝ KHI KHÁCH KHÔNG ĐẶT
Nếu khách nói "thôi", "để sau", hoặc ngừng trả lời, hãy kết thúc nhẹ nhàng:  
`"Dạ không sao ạ. Khi nào thèm trà sữa thì ghé mình nha. Chúc bạn ngày vui! 🥤"`

---
### 🧾 VI. XỬ LÝ SAU KHI ĐÃ GỬI THANH TOÁN (QUAN TRỌNG)
Khi khách đã nhận được QR code và link thanh toán, đơn hàng đang ở trạng thái "Chờ thanh toán". Lúc này:
- Nếu khách có hỏi gì thì lịch sự hỗ trợ chờ đến khi có thông báo thanh toán thành công từ hệ thống (thông qua webhook hoặc kiểm tra định kỳ).
- Nếu khách hỏi "alo", "còn đó không", "sao lâu vậy", "bot đâu", bạn trả lời nhẹ nhàng:  
  `"Dạ mình vẫn ở đây ạ. Đơn hàng của bạn đang chờ thanh toán, sau khi thanh toán thành công bên mình sẽ chuẩn bị ngay nhé!"`
- Tuyệt đối không mời gọi món mới hoặc hỏi lại thông tin đặt hàng trừ khi khách chủ động yêu cầu hủy đơn hoặc đặt thêm.
- Nếu khách hỏi về trạng thái đơn hàng, bạn trả lời dựa trên thông tin đã có (mã đơn, tổng tiền) và nhắc thanh toán.
- Chỉ ngừng hỗ trợ khi khách có yêu cầu hủy đơn hoặc sau 1 tiếng không có thanh toán nào, lúc đó bạn mới nhẹ nhàng thông báo:  
  `"Dạ đơn hàng của bạn đã bị hủy do không nhận được thanh toán trong thời gian quy định. Nếu bạn vẫn muốn đặt hàng, xin vui lòng bắt đầu lại từ đầu nhé. Mong được phục vụ bạn lần sau ạ!"`
---
### ⚠️ VII. NHẮC NHỞ CUỐI CÙNG
- Đọc kỹ từng chữ khách viết, không suy diễn.
- Không bỏ qua bước nào trong quy trình.
- Không bịa món, không thay đổi giá.
- **Chính tả:** Luôn dùng đúng "trà sữa", không viết "trả sữa".
"""

# --- 5. BOT LOGIC ---
user_sessions = {}
MAX_HISTORY = 100 

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "kiem_tra_khach_hang",
            "description": "Gọi hàm này NGAY KHI khách cho Số Điện Thoại.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone_number": {"type": "string"}
                },
                "required": ["phone_number"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "chot_don_hang",
            "description": "Chỉ gọi khi đã có đủ thông tin và tính tiền xong.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {"type": "string"},
                    "phone_number": {"type": "string"},
                    "is_agree_membership": {"type": "boolean"},
                    "total_amount": {"type": "integer"},
                    "items": {
                        "type": "array",
                        "description": "KHÔNG BAO GIỜ được chứa item có price = 0.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "item_name": {"type": "string"},
                                "size": {"type": "string", "enum": ["M", "L", "None"]},
                                "quantity": {"type": "integer"},
                                "price": {"type": "integer", "description": "Giá full (VD: 42000)"}
                            },
                            "required": ["item_name", "size", "quantity", "price"]
                        }
                    }
                },
                "required": ["customer_name", "phone_number", "is_agree_membership", "total_amount", "items"]
            }
        }
    }
]

# -- Các hàm hỗ trợ khác --

async def countdown_and_cancel_order(order_id, chat_id):
    await asyncio.sleep(3600)
    cancel_order_if_unpaid(order_id)

@dp.message(CommandStart())
async def command_start_handler(message: types.Message):
    user_id = message.chat.id
    user_sessions[user_id] = []
    await message.answer("Dạ xin chào! Mình là Vịt. Rất vui được đón tiếp Bạn ạ. Hôm nay Bạn muốn dùng thức uống gì để mình chuẩn bị ạ? 🥰")

# CỔNG PHẢN HỒI NHANH (BYPASS AI) - MỞ RỘNG TỪ KHÓA MENU
@dp.message(lambda msg: msg.text and re.sub(r'[.!?,;]$', '', msg.text.strip().lower()) in [
    "menu", "thực đơn", "xem menu", "xin menu", "cho xin menu", "cho xem menu", 
    "menu ạ", "thực đơn ạ", "quán có món gì", "quán có gì", "giá menu", "bảng giá", 
    "gọi món", "mình muốn gọi món", "mình muốn xem menu", "có gì ngon", 
    "có gì để gọi", "có gì để uống", "có gì để ăn", "mình đói rồi", "mình khát quá",
    "gọi đồ uống", "gọi trà sữa", "gọi cà phê", "có những món nào", "cho coi menu",
    "show menu", "menuu", "thuc don", "co mon gi", "co gi hot", "mon ngon",
])
async def instant_menu_handler(message: types.Message):
    bot_reply = f"Dạ vâng, xin gửi menu của quán để mình tham khảo ạ:\n\n{PRETTY_MENU}"
    
    user_id = message.chat.id
    if user_id not in user_sessions:
        user_sessions[user_id] = []
    user_sessions[user_id].append({"role": "user", "content": message.text})
    user_sessions[user_id].append({"role": "assistant", "content": "Dạ vâng, xin gửi menu của quán ạ. (Đã gửi hình menu)"})
    
    await message.answer(bot_reply, parse_mode="Markdown")

@dp.message(F.text & ~F.text.startswith('/'))
async def chat_handler(message: types.Message):
    user_telegram_id = message.chat.id
    if user_telegram_id not in user_sessions:
        user_sessions[user_telegram_id] = []
        
    user_sessions[user_telegram_id].append({"role": "user", "content": message.text})
    if len(user_sessions[user_telegram_id]) > MAX_HISTORY:
        user_sessions[user_telegram_id] = user_sessions[user_telegram_id][-MAX_HISTORY:]
        
    await bot.send_chat_action(chat_id=user_telegram_id, action="typing")
    
    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + user_sessions[user_telegram_id],
            temperature=0.2, 
            tools=TOOLS, 
            tool_choice="auto",
            parallel_tool_calls=False 
        )
        
        response_message = response.choices[0].message
        
        # --- CƠ CHẾ SERIALIZE CHỐNG SẬP (CRASH-PROOF) ---
        assistant_msg = {"role": "assistant"}
        if response_message.content:
            assistant_msg["content"] = response_message.content
        if response_message.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": t.id,
                    "type": "function",
                    "function": {
                        "name": t.function.name,
                        "arguments": t.function.arguments
                    }
                } for t in response_message.tool_calls
            ]
        user_sessions[user_telegram_id].append(assistant_msg)
        # ------------------------------------------------
        
        if response_message.tool_calls:
            tool_call = response_message.tool_calls[0]
            
            try:
                args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                user_sessions[user_telegram_id].pop()
                bot_reply = "Dạ hệ thống đang hơi lag xíu, Quý khách nhắc lại món giúp mình nhé!"
                await message.answer(bot_reply)
                return
                
            if tool_call.function.name == "kiem_tra_khach_hang":
                phone = args.get("phone_number")
                # Gửi tin nhắn chờ để tránh khách sốt ruột
                wait_msg = await message.answer("⏳ Dạ mình đang tra cứu thông tin thành viên của bạn, một chút xíu thôi ạ...")
                
                db_result = check_customer_db(phone)
                
                user_sessions[user_telegram_id].append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": tool_call.function.name,
                    "content": json.dumps(db_result, ensure_ascii=False)
                })
                
                second_response = await openai_client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "system", "content": SYSTEM_PROMPT}] + user_sessions[user_telegram_id],
                    tools=TOOLS
                )
                bot_reply = second_response.choices[0].message.content
                user_sessions[user_telegram_id].append({"role": "assistant", "content": bot_reply})
                # Xóa tin nhắn chờ
                await wait_msg.delete()
                await message.answer(bot_reply)

            elif tool_call.function.name == "chot_don_hang":
                order_id, user_db_id = process_checkout(
                    user_telegram_id, 
                    message.from_user.full_name, 
                    args.get("phone_number"),
                    args.get("customer_name"),
                    args.get("is_agree_membership"),
                    args.get("total_amount"), 
                    args.get("items")
                )
                
                user_sessions[user_telegram_id].append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": tool_call.function.name,
                    "content": json.dumps({"status": "success"})
                })
                
                if order_id:
                    checkout_url, qr_code_emv = await generate_payos_link(order_id, args.get("total_amount"), args.get("items"))
                    
                    if checkout_url and qr_code_emv:
                        qr_file = generate_qr_code(qr_code_emv)
                        
                        if qr_file:
                            caption = (
                                f"✅ **XÁC NHẬN ĐƠN HÀNG: {order_id}**\n\n"
                                f"👤 Khách hàng: {args.get('customer_name')}\n"
                                f"💰 Tổng tiền: **{args.get('total_amount'):,} VNĐ**\n\n"
                                f"📱 Quý khách quét mã QR này để thanh toán qua PayOS nhé! ✨"
                            )
                            
                            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="💳 Thanh toán qua link", url=checkout_url)]
                            ])
                            
                            await message.answer_photo(
                                photo=BufferedInputFile(file=qr_file.getvalue(), filename="qr_code.png"),
                                caption=caption,
                                reply_markup=keyboard,
                                parse_mode="Markdown"
                            )
                            bot_reply_content = f"✅ Đơn hàng {order_id} đã lên. Mã QR là mã thanh toán chính thức từ PayOS, khi Quý khách thanh toán sẽ cập nhật ngay ạ!"
                        else:
                            bot_reply_content = f"Dạ đơn hàng **{order_id}** đã lên thành công! Quý khách click nút bên dưới để thanh toán nhé!"
                            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="💳 Thanh toán", url=checkout_url)]
                            ])
                            await message.answer(bot_reply_content, reply_markup=keyboard, parse_mode="Markdown")
                    else:
                        bot_reply_content = "Dạ xin lỗi quý khách, hệ thống PayOS đang bận xíu, quý khách đợi và thử lại nhé!"
                        await message.answer(bot_reply_content)
                    
                    asyncio.create_task(countdown_and_cancel_order(order_id, user_telegram_id))
                else:
                    bot_reply_content = "Dạ hệ thống gặp lỗi khi chốt đơn, Quý khách đợi xíu nhé."
                    await message.answer(bot_reply_content)
                
                user_sessions[user_telegram_id].append({"role": "assistant", "content": bot_reply_content})
                return

        else: 
            bot_reply = response_message.content
            # --- KIỂM SOÁT MENU: Nếu AI lỡ nói về menu, tự động gửi menu chuẩn ---
            if any(phrase in bot_reply.lower() for phrase in ["gửi menu", "danh sách thực đơn", "đây là menu", "menu của quán"]):
                # Gửi menu chuẩn thay vì nội dung AI tự sinh
                await message.answer(f"Dạ vâng, xin gửi menu của quán để mình tham khảo ạ:\n\n{PRETTY_MENU}", parse_mode="Markdown")
                # Ghi log lịch sử đã gửi menu
                user_sessions[user_telegram_id].append({"role": "assistant", "content": "Dạ vâng, xin gửi menu của quán ạ. (Đã gửi hình menu)"})
                return
            
            # Lọc bớt chữ "Xin chào" nếu AI lỡ mồm
            if "xin chào" in bot_reply.lower() or "chào bạn" in bot_reply.lower():
                bot_reply = bot_reply.replace("Dạ xin chào!", "Dạ vâng!").replace("Chào bạn,", "Dạ,")
                
            await message.answer(bot_reply)
            
    except Exception as e:
        logging.error(f"Error: {e}")
        if user_sessions[user_telegram_id]: user_sessions[user_telegram_id].pop() 
        await message.answer("Dạ kết nối hơi chập chờn, bạn nhắc lại giúp mình nha! 🙏")

async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("Bot Chủ Quán đã sẵn sàng phục vụ...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())