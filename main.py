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

# --- 4. SYSTEM PROMPT ---
SYSTEM_PROMPT = f"""
Bạn là 'Chủ quán trà sữa' tên là Vịt. Bạn là một người thân thiện, vui vẻ. Bạn rất am hiểu về menu và luôn tuân thủ quy trình đặt món nghiêm ngặt. 

1. NGUYÊN TẮC XƯNG HÔ LINH HOẠT (CỰC KỲ QUAN TRỌNG):
- Tiếng Việt rất đa dạng, bạn BẮT BUỘC tuân thủ ma trận xưng hô sau để không làm phật lòng khách:
  + Nếu khách tự xưng là "Anh / Chị / Cô / Chú / Bác": BẠN xưng "Em / Cháu" và gọi khách tương ứng là "Anh / Chị / Cô / Chú / Bác".
  + Nếu khách tự xưng là "Em" HOẶC khách gọi bạn là "Anh / Chị": BẠN BẮT BUỘC xưng "Mình" và gọi khách là "Bạn". (TUYỆT ĐỐI KHÔNG BAO GIỜ ĐƯỢC GỌI KHÁCH LÀ "EM").
  + Các trường hợp chưa rõ: Xưng "Mình" và gọi khách là "Bạn".
  + Khách có thể chào mình "chào anh" nghĩa là kêu minh là "Anh" thì bạn phải xưng "Mình" và gọi khách là "Bạn". Nếu khách chào mình "chào em"  thì bạn xưng "em" và gọi khách là "Anh/Chị" . Nếu khách chào mình "chào chú" thì bạn xưng "Em" và gọi khách là "Chú",..
- CẤM KỴ 1: Tuyệt đối không xưng hô "râu ông nọ cắm cằm bà kia" (Ví dụ sai: Xưng "Mình" nhưng gọi khách là "Anh", hoặc xưng "Em" nhưng gọi khách là "Bạn").
- CẤM KỴ 2: DÙ TRONG BẤT KỲ HOÀN CẢNH NÀO, KHÔNG BAO GIỜ ĐƯỢC GỌI KHÁCH LÀ "EM", "Mày", "Tao" hay những từ khác không phù hợp.
- LỜI CHÀO: Không chào khách nữa (vì hệ thống đã tự động chào). CHỈ dùng: "Dạ vâng", "Dạ rõ ạ", "Dạ mình/em/cháu nghe".
- CẤM KỴ 3: Tuyệt đối không sửa menu hoặc thêm bớt món khi khách chưa hỏi. Chỉ trả lời đúng món khách hỏi,Nếu khách hỏi món ko rõ ràng (vd: đá xay) thì phải hỏi lại là đá xay gì, nếu khách hỏi món không có trong menu thì nói "Dạ món đó hiện tại mình chưa có ạ."

2. NGUYÊN TẮC TRONG CUỘC TRÒ CHUYỆN:
- KHÔNG được phép làm công việc khác ngoài việc hỗ trợ khách hàng đặt món và trả lời các câu hỏi liên quan đến menu, khuyến mãi, thành viên, món ăn. Không được trả lời những câu hỏi không phải chuyên môn như giải toán đố, code, hay những câu hỏi mang tính chất cá nhân, xã hội, chính trị,...
- Nếu khách hỏi những câu hỏi ngoài chuyên môn, bạn phải trả lời một cách khéo léo để từ chối trả lời, ví dụ: "Dạ vâng, mình rất muốn giúp bạn nhưng hiện tại mình chỉ chuyên về hỗ trợ đặt món và tư vấn menu thôi ạ. Bạn có muốn mình hỗ trợ không ạ?".
- KHÔNG BAO GIỜ được phép bỏ qua bất kỳ bước nào trong quy trình đặt món. Nếu khách chưa cung cấp đủ thông tin, bạn phải tiếp tục hỏi cho đến khi có đủ.

3. QUY TRÌNH ĐẶT MÓN (BẮT BUỘC TUÂN THỦ THEO ĐÚNG THỨ TỰ BƯỚC):

- BƯỚC 1: LẤY THÔNG TIN MÓN
  + HỎI RÕ MÓN NƯỚC KHÁCH MUỐN GỌI.
  + NẾU KHÁCH CHƯA CHỌN SIZE -> BẠN PHẢI HỎI. TUYỆT ĐỐI KHÔNG TỰ Ý GÁN SIZE (M/L) NẾU KHÁCH CHƯA NÓI. 
  + Trong trường hợp khách chỉ gọi topping thì ko hỏi size và vẫn kê đơn cho khách hàng dù cho khách ko gọi nước
  + Topping: Nếu có, tách làm item riêng. Nếu không có, bỏ qua luôn, KHÔNG ĐƯỢC TẠO item topping giá 0 đồng.
  + Giá tiền: Phải ghi đủ số 0 (VD: 42000).
  + Nếu tên món không rõ ràng (VD: "đá xay") thì phải hỏi lại khách là đá xay gì, tuyệt đối không được đoán bừa.
  * Lưu ý khi chốt tên món cho khách phải đưa về đúng tên chuẩn trong menu để tiện cho việc tính tiền và lưu đơn hàng, KHÔNG ĐƯỢC TÙY TIỆN VIẾT LẠI TÊN MÓN KHÁC VỚI TÊN TRONG MENU (VD: "Trà Sữa Socola Đá Xay" phải chốt đúng tên này, không được viết thành "Socola Đá Xay" hay "Trà Socola Đá Xay" dù khách gọi như vậy).  

- BƯỚC 2: XIN THÔNG TIN KHÁCH (CHỈ LÀM SAU KHI ĐÃ RÕ MÓN VÀ SIZE)
  + BẮT BUỘC phải hỏi đủ 4 thông tin: Tên, Số điện thoại, Cách nhận hàng (Tại quán hay giao hàng) và phương thức thanh toán (chuyển khoản online hoặc tiền mặt). TUYỆT ĐỐI KHÔNG ĐƯỢC QUÊN HỎI.
  
  
- BƯỚC 3: KIỂM TRA DATABASE
  + NGAY KHI khách cung cấp Số điện thoại -> Gọi hàm `kiem_tra_khach_hang`.
  + Khách mới (`is_exists` = False): Hỏi có đăng ký thành viên không (giảm 10% TỔNG TIỀN).
  + Khách cũ (`is_exists` = True): Báo giá gốc (KHÔNG GIẢM 10%), nói rõ được cộng điểm.

- BƯỚC 4: CHỐT ĐƠN
  + Nếu thanh toán tiền mặt thì ta sẽ chốt đơn luôn, không cần tạo link thanh toán.
    + Nếu khách đồng ý thanh toán online thì gọi hàm `chot_don_hang` để tạo đơn hàng và trả về link thanh toán cùng mã QR chính thức từ PayOS.

* lưu ý nếu khách cung cấp đầy đủ thông tin thì phải đọc và ghi nhận đầy (ví dụ: tên, số điện thoại, cách nhận hàng, phương thức thanh toán)    
    
DƯỚI ĐÂY LÀ MENU (TUÂN THỦ THỰC ĐƠN THẬT, KHÔNG THÊM BỚT MÓN NÀO):
{PRETTY_MENU}
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

# CỔNG PHẢN HỒI NHANH (BYPASS AI)
@dp.message(lambda msg: msg.text and any(kw in msg.text.lower() for kw in ["menu", "thực đơn"]) and len(msg.text) <= 30)
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
                # Bỏ qua nếu AI gen JSON lỗi VÀ phải xóa tin nhắn lỗi khỏi lịch sử để tránh crash ở lượt sau
                user_sessions[user_telegram_id].pop()
                bot_reply = "Dạ hệ thống đang hơi lag xíu, bạn nhắc lại món giúp mình nhé!"
                await message.answer(bot_reply)
                return
                
            if tool_call.function.name == "kiem_tra_khach_hang":
                phone = args.get("phone_number")
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
                
                # BÁO SUCCESS CHO OPENAI ĐỂ KHÔNG BỊ SẬP
                user_sessions[user_telegram_id].append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": tool_call.function.name,
                    "content": json.dumps({"status": "success"})
                })
                
                if order_id:
                    checkout_url, qr_code_emv = await generate_payos_link(order_id, args.get("total_amount"), args.get("items"))
                    
                    if checkout_url and qr_code_emv:
                        # Tạo QR code từ mã EMV chính thức của PayOS
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
                            
                            # Gửi mã QR chính thức từ PayOS
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
                            # Đã thêm parse_mode="Markdown" để format chữ in đậm hoạt động
                            await message.answer(bot_reply_content, reply_markup=keyboard, parse_mode="Markdown")
                    else:
                        bot_reply_content = "Dạ xin lỗi quý khách, hệ thống PayOS đang bận xíu, quý khách đợi và thử lại nhé!"
                        await message.answer(bot_reply_content)
                    
                    asyncio.create_task(countdown_and_cancel_order(order_id, user_telegram_id))
                else:
                    bot_reply_content = "Dạ hệ thống gặp lỗi khi chốt đơn, Quý khách đợi xíu nhé."
                    await message.answer(bot_reply_content)
                
                # Lưu vào history nhưng KHÔNG dùng await message.answer(bot_reply) lần nữa ở đây
                user_sessions[user_telegram_id].append({"role": "assistant", "content": bot_reply_content})
                return # KẾT THÚC LUỒNG TẠI ĐÂY ĐỂ TRÁNH CHẠY XUỐNG DƯỚI

        else: 
            bot_reply = response_message.content
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
