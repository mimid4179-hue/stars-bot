import asyncio
import psycopg2
from psycopg2.extras import DictCursor
import datetime
import os
import random
import json
from typing import Callable, Dict, Any, Awaitable
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types, BaseMiddleware
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
CARD_NUMBER = os.environ.get("CARD_NUMBER", "0000 0000 0000 0000")
CARD_NAME = os.environ.get("CARD_NAME", "Имя Фамилия")
DEAL_TIMEOUT_MINUTES = 15
DATABASE_URL = os.environ.get("DATABASE_URL")

MY_USERNAME = "@frosyyy"
REVIEWS_CHANNEL = "https://t.me/otzfrosy"

# Список подарков
GIFTS = {
    "heart": {"name": "❤️ Сердце", "stars": 15},
    "bear": {"name": "🧸 Мишка", "stars": 15},
    "rose": {"name": "🌹 Роза", "stars": 25},
    "gift": {"name": "🎁 Подарок", "stars": 25},
    "cake": {"name": "🎂 Торт", "stars": 50},
    "bouquet": {"name": "💐 Букет", "stars": 50},
    "rocket": {"name": "🚀 Ракета", "stars": 50},
    "champagne": {"name": "🍾 Шампанское", "stars": 50},
    "cup": {"name": "🏆 Кубок", "stars": 100},
    "ring": {"name": "💍 Кольцо", "stars": 100},
    "diamond": {"name": "💎 Бриллиант", "stars": 100}
}
# ===============================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True
cursor = conn.cursor(cursor_factory=DictCursor)

cursor.execute("ALTER TABLE deals ADD COLUMN IF NOT EXISTS is_anonymous BOOLEAN DEFAULT FALSE")

cursor.execute("""
CREATE TABLE IF NOT EXISTS deals (
    id INTEGER PRIMARY KEY,
    buyer_id BIGINT,
    buyer_username TEXT,
    deal_type TEXT DEFAULT 'balance',
    gift_items TEXT,
    is_anonymous BOOLEAN DEFAULT FALSE,
    stars_amount INTEGER,
    recipient_username TEXT,
    price_uah REAL,
    status TEXT DEFAULT 'pending',
    created_at TEXT,
    expires_at TEXT,
    receipt_file_id TEXT,
    awaiting_review INTEGER DEFAULT 0
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS reviews (
    id SERIAL PRIMARY KEY,
    user_id BIGINT UNIQUE,
    rating INTEGER
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS banned_users (
    user_id BIGINT PRIMARY KEY
)
""")

class BanMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[types.TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: types.TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = getattr(event, 'from_user', None)
        if user and user.id != ADMIN_ID:
            cursor.execute("SELECT * FROM banned_users WHERE user_id = %s", (user.id,))
            if cursor.fetchone():
                return
        return await handler(event, data)

dp.message.middleware(BanMiddleware())
dp.callback_query.middleware(BanMiddleware())

class DealForm(StatesGroup):
    choosing_type = State()
    choosing_gifts = State()
    choosing_anonymity = State()
    waiting_stars = State()
    waiting_recipient = State()
    waiting_receipt = State()
    waiting_report = State()
    chat_buyer = State()
    chat_admin = State()

def calculate_price(stars: int, deal_type: str = 'balance') -> float:
    if deal_type == 'gift':
        return stars * 0.75
    if stars < 100:
        return stars * 0.8
    return stars * 0.75

def get_deal(deal_id: int):
    cursor.execute("SELECT * FROM deals WHERE id = %s", (deal_id,))
    return cursor.fetchone()

def update_deal_status(deal_id: int, status: str, receipt_file_id: str = None):
    if receipt_file_id:
        cursor.execute("UPDATE deals SET status = %s, receipt_file_id = %s WHERE id = %s", (status, receipt_file_id, deal_id))
    else:
        cursor.execute("UPDATE deals SET status = %s WHERE id = %s", (status, deal_id))

async def close_expired_deal(deal_id: int, buyer_id: int):
    await asyncio.sleep(DEAL_TIMEOUT_MINUTES * 60)
    deal = get_deal(deal_id)
    if deal and deal['status'] in ('pending', 'accepted'):
        update_deal_status(deal_id, 'expired')
        try:
            await bot.send_message(buyer_id, f"❌ Сделка #{deal_id} автоматически отменена, так как истекло время ({DEAL_TIMEOUT_MINUTES} мин).")
        except: pass
        try:
            await bot.send_message(ADMIN_ID, f"⏰ Сделка #{deal_id} отменена по таймауту.")
        except: pass

@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id == ADMIN_ID:
        await message.answer("Вы не можете купить звёзды у самого себя.")
        return

    cursor.execute("SELECT id FROM deals WHERE buyer_id = %s AND status IN ('pending', 'accepted', 'paid')", (message.from_user.id,))
    if cursor.fetchone():
        await message.answer("У вас уже есть активная сделка. Пожалуйста, дождитесь её завершения или отмены, прежде чем создавать новую.")
        return

    text = (
        f"Здравствуйте!\n\n"
        f"Тут вы можете купить моментальные звёзды на баланс либо подарком.\n"
        f"✅ Звезды приходят моментально, всё абсолютно безопасно.\n\n"
        f"💵 <b>Курс:</b>\n"
        f"50-99 звезд — 0.8 грн\n"
        f"от 100 — 0.75 грн\n\n"
        f"👤 Мой профиль: {MY_USERNAME}\n"
        f"🌟 Отзывы: @otzfrosy\n\n"
        f"Если есть дополнительные вопросы, обращайтесь."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Купить звёзды", callback_data="choose_type")],
        [InlineKeyboardButton(text="📝 Оценить бота", callback_data="show_reviews")]
    ])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.message(Command("buy"))
async def cmd_buy(message: types.Message, state: FSMContext):
    if message.from_user.id == ADMIN_ID:
        await message.answer("Вы не можете купить звёзды у самого себя.")
        return
    cursor.execute("SELECT id FROM deals WHERE buyer_id = %s AND status IN ('pending', 'accepted', 'paid')", (message.from_user.id,))
    if cursor.fetchone():
        await message.answer("У вас уже есть активная сделка. Пожалуйста, дождитесь её завершения.")
        return
    await choose_type(message, state)

async def choose_type(message_or_callback, state: FSMContext):
    text = (
        "Выберите способ покупки:\n\n"
        "⭐ <b>На баланс</b> — звёзды будут зачислены на ваш аккаунт.\n"
        "🎁 <b>Подарком</b> — вы сможете выбрать один или несколько подарков для другого пользователя."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ На баланс", callback_data="type_balance")],
        [InlineKeyboardButton(text="🎁 Подарком", callback_data="type_gift")]
    ])
    if isinstance(message_or_callback, types.Message):
        await message_or_callback.answer(text, reply_markup=kb, parse_mode="HTML")
    else:
        await message_or_callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await state.set_state(DealForm.choosing_type)

@dp.callback_query(F.data == "choose_type")
async def cb_choose_type(callback: types.CallbackQuery, state: FSMContext):
    await choose_type(callback, state)
    await callback.answer()

@dp.callback_query(F.data == "type_balance")
async def type_balance(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(deal_type='balance')
    await callback.message.answer("Введи количество звёзд (минимум 50):")
    await state.set_state(DealForm.waiting_stars)
    await callback.answer()

@dp.callback_query(F.data == "type_gift")
async def type_gift(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(deal_type='gift', selected_gifts=[])
    await show_gift_menu(callback.message, state)
    await callback.answer()

async def show_gift_menu(message: types.Message, state: FSMContext):
    data = await state.get_data()
    selected = data.get('selected_gifts', [])
    
    text = (
        "🎁 <b>Покупка подарком</b>\n\n"
        "Здесь вы можете приобрести звёзды в виде подарков для другого пользователя.\n"
        "Курс: <b>0.75 грн</b> за звезду.\n\n"
        "Выберите подарки (можно несколько):\n"
    )
    
    buttons = []
    row = []
    for key, gift in GIFTS.items():
        mark = "✅ " if key in selected else ""
        row.append(InlineKeyboardButton(text=f"{mark}{gift['name']} ({gift['stars']}⭐)", callback_data=f"gift_{key}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    
    total_stars = sum(GIFTS[k]['stars'] for k in selected)
    total_price = calculate_price(total_stars, 'gift') if total_stars > 0 else 0
    
    text += f"\nВыбрано: <b>{total_stars} ⭐</b> на сумму <b>{total_price:.2f} грн</b>"
    
    buttons.append([InlineKeyboardButton(text="🗑 Очистить выбор", callback_data="gift_clear")])
    buttons.append([InlineKeyboardButton(text="✅ Подтвердить выбор", callback_data="gift_confirm")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    if isinstance(message, types.Message):
        await message.answer(text, reply_markup=kb, parse_mode="HTML")
    else:
        await message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await state.set_state(DealForm.choosing_gifts)

@dp.callback_query(F.data.startswith("gift_"))
async def handle_gift_choice(callback: types.CallbackQuery, state: FSMContext):
    action = callback.data.split("_")[1]
    
    if action == "clear":
        await state.update_data(selected_gifts=[])
    elif action == "confirm":
        data = await state.get_data()
        selected = data.get('selected_gifts', [])
        if not selected:
            await callback.answer("Выберите хотя бы один подарок!", show_alert=True)
            return
        total_stars = sum(GIFTS[k]['stars'] for k in selected)
        await state.update_data(stars=total_stars)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👤 Нет, показать мое имя", callback_data="anon_no")],
            [InlineKeyboardButton(text="🕵️‍♂️ Да, скрыть имя", callback_data="anon_yes")]
        ])
        await callback.message.answer("Хотите ли вы скрыть свое имя при отправке подарка?", reply_markup=kb)
        await state.set_state(DealForm.choosing_anonymity)
        await callback.answer()
        return
    else:
        gift_key = action
        if gift_key in GIFTS:
            data = await state.get_data()
            selected = data.get('selected_gifts', [])
            if gift_key in selected:
                selected.remove(gift_key)
            else:
                selected.append(gift_key)
            await state.update_data(selected_gifts=selected)
    
    await show_gift_menu(callback.message, state)
    await callback.answer()

@dp.callback_query(F.data.startswith("anon_"))
async def handle_anonymity(callback: types.CallbackQuery, state: FSMContext):
    is_anon = callback.data == "anon_yes"
    await state.update_data(is_anonymous=is_anon)
    
    if is_anon:
        text = "🕵️‍♂️ Понял, отправим со скрытым именем (анонимно)."
    else:
        text = "👤 Понял, получатель увидит, что подарок от вас."
    
    await callback.message.answer(f"{text}\n\nВведи @username получателя подарков:")
    await state.set_state(DealForm.waiting_recipient)
    await callback.answer()

@dp.message(DealForm.waiting_stars)
async def process_stars(message: types.Message, state: FSMContext):
    try:
        stars = int(message.text)
        if stars < 50:
            await message.answer("Минимум 50. Попробуй ещё:")
            return
        await state.update_data(stars=stars, is_anonymous=False)
        await message.answer("Введи свой @username, на который зачислить звёзды:")
        await state.set_state(DealForm.waiting_recipient)
    except ValueError:
        await message.answer("Введи число:")

@dp.message(DealForm.waiting_recipient)
async def process_recipient(message: types.Message, state: FSMContext):
    recipient = message.text.strip().replace("@", "")
    data = await state.get_data()
    stars = data['stars']
    deal_type = data.get('deal_type', 'balance')
    selected_gifts = data.get('selected_gifts', [])
    is_anonymous = data.get('is_anonymous', False)
    
    gift_items_str = json.dumps(selected_gifts, ensure_ascii=False) if deal_type == 'gift' else None
    
    price = calculate_price(stars, deal_type)
    now = datetime.datetime.now()
    expires = now + datetime.timedelta(minutes=DEAL_TIMEOUT_MINUTES)
    
    deal_id = random.randint(10000, 99999)
    while get_deal(deal_id):
        deal_id = random.randint(10000, 99999)

    cursor.execute("""
        INSERT INTO deals (id, buyer_id, buyer_username, deal_type, gift_items, is_anonymous, stars_amount, recipient_username, price_uah, status, created_at, expires_at, awaiting_review)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s, 0)
    """, (deal_id, message.from_user.id, message.from_user.username or "нет", deal_type, gift_items_str, is_anonymous, stars, recipient, price, now.isoformat(), expires.isoformat()))

    type_text = "🎁 Подарком" if deal_type == 'gift' else "⭐ На баланс"
    gift_text = ""
    if deal_type == 'gift':
        gift_names = [GIFTS[k]['name'] for k in selected_gifts]
        anon_text = " (Анонимно 🕵️‍♂️)" if is_anonymous else ""
        gift_text = f"\nПодарки: {', '.join(gift_names)}{anon_text}"

    await message.answer(
        f"✅ Сделка #{deal_id} создана.\n"
        f"Тип: {type_text}\n"
        f"Звёзд: {stars}{gift_text}\n"
        f"К оплате: {price:.2f} грн\n"
        f"Получатель: @{recipient}\n\n"
        f"Жди подтверждения от админа. Время: {DEAL_TIMEOUT_MINUTES} мин."
    )

    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_{deal_id}"),
         InlineKeyboardButton(text="❌ Отказаться", callback_data=f"decline_{deal_id}")],
        [InlineKeyboardButton(text="💬 Связаться с покупателем", callback_data=f"contact_{message.from_user.id}"),
         InlineKeyboardButton(text="🚫 Забанить", callback_data=f"ban_{message.from_user.id}")]
    ])
    
    # Уведомление для АДМИНА (тебя)
    anon_info = ""
    if deal_type == 'gift':
        if is_anonymous:
            anon_info = "\n❗️ При отправке ВКЛЮЧИТЬ 'Скрыть имя'"
        else:
            anon_info = f"\n❗️ Отправить БЕЗ скрытия имени (от @{message.from_user.username or message.from_user.id})"

    admin_msg = (
        f"🔔 Новая сделка #{deal_id}\n"
        f"Тип: {type_text}{anon_info}\n"
        f"Покупатель: @{message.from_user.username or message.from_user.id}\n"
        f"Звёзд: {stars}{gift_text}\n"
        f"Цена: {price:.2f} грн\n"
        f"Получатель: @{recipient}"
    )
    await bot.send_message(ADMIN_ID, admin_msg, reply_markup=admin_kb)
    asyncio.create_task(close_expired_deal(deal_id, message.from_user.id))
    await state.clear()

@dp.callback_query(F.data.startswith("accept_"))
async def admin_accept(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True); return
    deal_id = int(callback.data.split("_")[1])
    deal = get_deal(deal_id)
    if not deal or deal['status'] != 'pending':
        await callback.answer("Сделка неактивна", show_alert=True); return

    update_deal_status(deal_id, 'accepted')
    
    deal_type_text = "🎁 Подарком" if deal['deal_type'] == 'gift' else "⭐ На баланс"
    gift_text = ""
    if deal['deal_type'] == 'gift' and deal['gift_items']:
        selected = json.loads(deal['gift_items'])
        gift_names = [GIFTS[k]['name'] for k in selected]
        anon_text = " (Анонимно 🕵️‍♂️)" if deal['is_anonymous'] else ""
        gift_text = f"\nПодарки: {', '.join(gift_names)}{anon_text}"

    buyer_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 Я оплатил, прикрепить чек", callback_data=f"paid_{deal_id}")],
        [InlineKeyboardButton(text="💬 Связаться с продавцом", callback_data="contact_admin")]
    ])
    await bot.send_message(deal['buyer_id'],
        f"✅ Сделка #{deal_id} принята!\n"
        f"Тип: {deal_type_text}{gift_text}\n"
        f"Переведи {deal['price_uah']:.2f} грн на карту:\n`{CARD_NUMBER}`\n{CARD_NAME}\n\n"
        f"После оплаты нажми кнопку и прикрепи скриншот.",
        reply_markup=buyer_kb, parse_mode="Markdown"
    )
    await callback.message.edit_text(callback.message.text + "\n\n✅ Ты принял сделку.")
    await callback.answer()

@dp.callback_query(F.data.startswith("decline_"))
async def admin_decline(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True); return
    deal_id = int(callback.data.split("_")[1])
    deal = get_deal(deal_id)
    if not deal or deal['status'] != 'pending':
        await callback.answer("Сделка неактивна", show_alert=True); return

    update_deal_status(deal_id, 'declined')
    await bot.send_message(deal['buyer_id'], f"❌ Сделка #{deal_id} отклонена. Попробуй позже.")
    await callback.message.edit_text(callback.message.text + "\n\n❌ Ты отклонил сделку.")
    await callback.answer()

@dp.callback_query(F.data.startswith("paid_"))
async def buyer_paid(callback: types.CallbackQuery, state: FSMContext):
    deal_id = int(callback.data.split("_")[1])
    deal = get_deal(deal_id)
    if not deal or deal['status'] != 'accepted':
        await callback.answer("Сделка не ждёт оплаты", show_alert=True); return
    await state.update_data(deal_id=deal_id)
    await callback.message.answer("Отправь скриншот или фото чека:")
    await state.set_state(DealForm.waiting_receipt)
    await callback.answer()

@dp.message(DealForm.waiting_receipt, F.photo)
async def process_receipt(message: types.Message, state: FSMContext):
    data = await state.get_data()
    deal_id = data.get('deal_id')
    if not deal_id:
        await message.answer("Ошибка."); await state.clear(); return

    file_id = message.photo[-1].file_id
    update_deal_status(deal_id, 'paid', file_id)
    deal = get_deal(deal_id)
    await message.answer(f"✅ Чек по сделке #{deal_id} получен. Жди выдачи звёзд.")

    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Звёзды выданы, завершить", callback_data=f"complete_{deal_id}")],
        [InlineKeyboardButton(text="📎 Отправить скриншот покупателю", callback_data=f"sendscreen_{deal_id}")],
        [InlineKeyboardButton(text="💬 Связаться с покупателем", callback_data=f"contact_{deal['buyer_id']}")]
    ])
    
    deal_type_text = "🎁 Подарком" if deal['deal_type'] == 'gift' else "⭐ На баланс"
    gift_text = ""
    if deal['deal_type'] == 'gift' and deal['gift_items']:
        selected = json.loads(deal['gift_items'])
        gift_names = [GIFTS[k]['name'] for k in selected]
        anon_text = " (Анонимно 🕵️‍♂️)" if deal['is_anonymous'] else ""
        gift_text = f"\nПодарки: {', '.join(gift_names)}{anon_text}"

    admin_instruction = ""
    if deal['deal_type'] == 'gift':
        if deal['is_anonymous']:
            admin_instruction = "\n❗️ При отправке ВКЛЮЧИТЬ 'Скрыть имя'"
        else:
            admin_instruction = f"\n❗️ Отправить БЕЗ скрытия имени (от @{deal['buyer_username']})"

    await bot.send_photo(ADMIN_ID, photo=file_id,
        caption=f"💰 Чек по сделке #{deal_id}\n"
                f"Тип: {deal_type_text}{gift_text}{admin_instruction}\n"
                f"Звёзд: {deal['stars_amount']}\n"
                f"Получатель: @{deal['recipient_username']}\n"
                f"Сумма: {deal['price_uah']:.2f} грн\n\n"
                f"Проверь оплату и отправь звёзды через Fragment.",
        reply_markup=admin_kb
    )
    await state.clear()

@dp.callback_query(F.data.startswith("sendscreen_"))
async def send_screen_to_buyer(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    deal_id = int(callback.data.split("_")[1])
    deal = get_deal(deal_id)
    if not deal: return
    await state.update_data(send_screen_deal_id=deal_id)
    await callback.message.answer("Отправьте скриншот или фото, которое нужно переслать покупателю:")
    await state.set_state("waiting_admin_screenshot")
    await callback.answer()

@dp.message(F.photo, F.from_user.id == ADMIN_ID)
async def admin_send_screenshot(message: types.Message, state: FSMContext):
    data = await state.get_data()
    deal_id = data.get('send_screen_deal_id')
    if deal_id:
        deal = get_deal(deal_id)
        if deal:
            file_id = message.photo[-1].file_id
            try:
                await bot.send_photo(deal['buyer_id'], photo=file_id, caption="📎 Подтверждение от продавца")
                await message.answer("✅ Скриншот отправлен покупателю.")
            except:
                await message.answer("❌ Не удалось отправить скриншот (возможно, пользователь заблокировал бота).")
        await state.clear()
        return

@dp.callback_query(F.data.startswith("complete_"))
async def admin_complete(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True); return
    deal_id = int(callback.data.split("_")[1])
    deal = get_deal(deal_id)
    if not deal or deal['status'] != 'paid':
        await callback.answer("Сделка не в статусе 'оплачено'", show_alert=True); return

    cursor.execute("UPDATE deals SET status = 'completed', awaiting_review = 1 WHERE id = %s", (deal_id,))

    await bot.send_message(
        deal['buyer_id'], 
        f"🎉 Сделка #{deal_id} завершена! Звёзды успешно отправлены на @{deal['recipient_username']}.\n\n"
        f"Пожалуйста, оставьте отзыв об этой сделке (например: {MY_USERNAME} не скам).\n"
        f"Просто напишите ваш отзыв следующим сообщением сюда:", 
    )
    await callback.message.edit_caption(callback.message.caption + "\n\n✅ Завершено.")
    await callback.answer()

@dp.callback_query(F.data == "contact_admin")
async def contact_admin(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Напишите ваше сообщение для продавца. Оно будет доставлено лично в чат.")
    await state.set_state(DealForm.chat_buyer)
    await callback.answer()

@dp.callback_query(F.data.startswith("contact_"))
async def contact_buyer(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    buyer_id = int(callback.data.split("_")[1])
    await state.update_data(target_id=buyer_id)
    await callback.message.answer("Напишите сообщение для покупателя. Оно будет отправлено ему в чат.")
    await state.set_state(DealForm.chat_admin)
    await callback.answer()

@dp.message(DealForm.chat_buyer)
async def process_chat_buyer(message: types.Message, state: FSMContext):
    user_info = f"@{message.from_user.username}" if message.from_user.username else f"ID: {message.from_user.id}"
    try:
        await bot.forward_message(chat_id=ADMIN_ID, from_chat_id=message.chat.id, message_id=message.message_id)
        await bot.send_message(ADMIN_ID, f"💬 Сообщение от клиента {user_info}")
    except: pass
    await message.answer("Сообщение отправлено продавцу.")
    await state.clear()

@dp.message(DealForm.chat_admin)
async def process_chat_admin(message: types.Message, state: FSMContext):
    data = await state.get_data()
    target_id = data.get('target_id')
    if target_id:
        try:
            await bot.send_message(target_id, f"💬 <b>Сообщение от продавца:</b>\n\n{message.text}", parse_mode="HTML")
            await message.answer("Сообщение отправлено покупателю.")
        except:
            await message.answer("Не удалось отправить сообщение (возможно, пользователь заблокировал бота).")
    await state.clear()

@dp.message(F.text)
async def handle_text_review(message: types.Message):
    if message.text.startswith('/'): return
    
    cursor.execute("SELECT id FROM deals WHERE buyer_id = %s AND awaiting_review = 1 ORDER BY id DESC LIMIT 1", (message.from_user.id,))
    deal = cursor.fetchone()
    
    if deal:
        deal_id = deal['id']
        user_info = f"@{message.from_user.username}" if message.from_user.username else f"ID: {message.from_user.id}"
        
        admin_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚫 Забанить", callback_data=f"ban_{message.from_user.id}")]
        ])
        try:
            forwarded_msg = await bot.forward_message(chat_id=ADMIN_ID, from_chat_id=message.chat.id, message_id=message.message_id)
            await bot.send_message(
                ADMIN_ID, 
                f"📝 <b>Отзыв к сделке #{deal_id}</b>\nОт: {user_info}", 
                reply_to_message_id=forwarded_msg.message_id,
                reply_markup=admin_kb,
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"Ошибка пересылки: {e}")

        cursor.execute("UPDATE deals SET awaiting_review = 0 WHERE id = %s", (deal_id,))
        await message.answer("Спасибо за ваш отзыв! ❤️")
        return

async def handle(request):
    return web.Response(text="Bot is running!")

async def main():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 8080)))
    await site.start()
    
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
