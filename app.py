import asyncio
import sqlite3
import datetime
import os
import random
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

MY_USERNAME = "@frosyyy"
REVIEWS_CHANNEL = "https://t.me/otzfrosy"
# ===============================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

conn = sqlite3.connect("deals.db", check_same_thread=False)
cursor = conn.cursor()

# Таблица для сделок (теперь ID случайный, а не по порядку)
cursor.execute("""
CREATE TABLE IF NOT EXISTS deals (
    id INTEGER PRIMARY KEY,
    buyer_id INTEGER,
    buyer_username TEXT,
    stars_amount INTEGER,
    recipient_username TEXT,
    price_uah REAL,
    status TEXT DEFAULT 'pending',
    created_at TEXT,
    expires_at TEXT,
    receipt_file_id TEXT
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    rating INTEGER
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS banned_users (
    user_id INTEGER PRIMARY KEY
)
""")
conn.commit()

class BanMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[types.TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: types.TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = getattr(event, 'from_user', None)
        if user and user.id != ADMIN_ID:
            cursor.execute("SELECT * FROM banned_users WHERE user_id = ?", (user.id,))
            if cursor.fetchone():
                return
        return await handler(event, data)

dp.message.middleware(BanMiddleware())
dp.callback_query.middleware(BanMiddleware())

class DealForm(StatesGroup):
    waiting_stars = State()
    waiting_recipient = State()
    waiting_receipt = State()
    waiting_report = State()

def calculate_price(stars: int) -> float:
    if stars < 100:
        return stars * 0.8
    return stars * 0.75

def get_deal(deal_id: int):
    cursor.execute("SELECT * FROM deals WHERE id = ?", (deal_id,))
    return cursor.fetchone()

def update_deal_status(deal_id: int, status: str, receipt_file_id: str = None):
    if receipt_file_id:
        cursor.execute("UPDATE deals SET status = ?, receipt_file_id = ? WHERE id = ?", (status, receipt_file_id, deal_id))
    else:
        cursor.execute("UPDATE deals SET status = ? WHERE id = ?", (status, deal_id))
    conn.commit()

async def close_expired_deal(deal_id: int, buyer_id: int):
    await asyncio.sleep(DEAL_TIMEOUT_MINUTES * 60)
    deal = get_deal(deal_id)
    if deal and deal[6] == 'pending':
        update_deal_status(deal_id, 'expired')
        try:
            await bot.send_message(buyer_id, f"❌ Сделка #{deal_id} закрыта: истекло {DEAL_TIMEOUT_MINUTES} мин.")
        except: pass
        try:
            await bot.send_message(ADMIN_ID, f"⏰ Сделка #{deal_id} закрыта по таймауту.")
        except: pass

@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    await state.clear()
    text = (
        f"Здравствуйте!\n\n"
        f"Это бот для покупки звезд.\n\n"
        f"💵 <b>Курс:</b>\n"
        f"50-99 звезд — 0.8 грн\n"
        f"от 100 — 0.75 грн\n\n"
        f"👤 Мой профиль: {MY_USERNAME}\n"
        f"🌟 Отзывы: @otzfrosy\n\n"
        f"Если есть дополнительные вопросы, обращайтесь."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Купить звёзды", callback_data="new_deal")],
        [InlineKeyboardButton(text="📝 Оценить бота", callback_data="show_reviews")]
    ])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.message(Command("buy"))
async def cmd_buy(message: types.Message, state: FSMContext):
    await message.answer("Введи количество звёзд (минимум 50):")
    await state.set_state(DealForm.waiting_stars)

@dp.message(Command("review"))
async def cmd_review(message: types.Message):
    cursor.execute("SELECT COUNT(*), AVG(rating) FROM reviews")
    result = cursor.fetchone()
    count = result[0]
    avg_rating = round(result[1], 1) if result[1] else 0.0

    text = f"Пожалуйста, оставьте отзыв о нашей работе в канале!\n\n"
    text += f"📊 <b>Средняя оценка:</b> {avg_rating} ⭐ (Всего оценок: {count})\n\n"
    text += f"Выберите оценку ниже:"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="1⭐", callback_data="rate_1"),
            InlineKeyboardButton(text="2⭐", callback_data="rate_2"),
            InlineKeyboardButton(text="3⭐", callback_data="rate_3"),
            InlineKeyboardButton(text="4⭐", callback_data="rate_4"),
            InlineKeyboardButton(text="5⭐", callback_data="rate_5")
        ],
        [InlineKeyboardButton(text="Перейти в канал с отзывами", url=REVIEWS_CHANNEL)]
    ])
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("rate_"))
async def process_rating(callback: types.CallbackQuery):
    rating = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    
    cursor.execute("SELECT * FROM reviews WHERE user_id = ?", (user_id,))
    if cursor.fetchone():
        await callback.answer("Вы уже оставляли оценку!", show_alert=True)
        return

    cursor.execute("INSERT INTO reviews (user_id, rating) VALUES (?, ?)", (user_id, rating))
    review_id = cursor.lastrowid
    conn.commit()
    
    await callback.answer("Спасибо за ваш отзыв! ⭐", show_alert=True)
    
    user_info = f"@{callback.from_user.username}" if callback.from_user.username else f"ID: {user_id}"
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить оценку", callback_data=f"delrev_{review_id}"),
         InlineKeyboardButton(text="🚫 Забанить", callback_data=f"ban_{user_id}")]
    ])
    try:
        await bot.send_message(ADMIN_ID, f"⭐ Новая оценка от {user_info}: {rating} звёзд!", reply_markup=admin_kb)
    except: pass

@dp.message(Command("report"))
async def cmd_report(message: types.Message, state: FSMContext):
    await message.answer("Если что-то сломалось или не пришли звёзды, напишите нам одним сообщением, и мы решим вопрос за 1 час.")
    await state.set_state(DealForm.waiting_report)

@dp.message(DealForm.waiting_report)
async def process_report(message: types.Message, state: FSMContext):
    report_text = message.text
    user_info = f"@{message.from_user.username}" if message.from_user.username else f"ID: {message.from_user.id}"
    
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 Забанить", callback_data=f"ban_{message.from_user.id}")]
    ])
    
    try:
        await bot.send_message(ADMIN_ID, f"🚨 <b>Новая жалоба!</b>\n\nОт: {user_info}\nТекст: {report_text}", reply_markup=admin_kb, parse_mode="HTML")
    except: pass

    await message.answer("Спасибо за ваше обращение! Мы свяжемся с вами и решим вопрос в течение 1 часа.")
    await state.clear()

@dp.callback_query(F.data.startswith("delrev_"))
async def delete_review(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    review_id = int(callback.data.split("_")[1])
    cursor.execute("DELETE FROM reviews WHERE id = ?", (review_id,))
    conn.commit()
    await callback.message.edit_text(callback.message.text + "\n\n🗑 <b>Оценка удалена.</b>", parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data.startswith("ban_"))
async def ban_user(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    user_id = int(callback.data.split("_")[1])
    cursor.execute("INSERT OR IGNORE INTO banned_users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    await callback.message.edit_text(callback.message.text + "\n\n🚫 <b>Пользователь забанен.</b>", parse_mode="HTML")
    try:
        await bot.send_message(user_id, "🚫 Вы были заблокированы администратором за нарушение правил.")
    except: pass
    await callback.answer()

@dp.callback_query(F.data == "show_reviews")
async def show_reviews(callback: types.CallbackQuery):
    await cmd_review(callback.message)
    await callback.answer()

# --- ЛОГИКА СДЕЛОК ---

@dp.callback_query(F.data == "new_deal")
async def new_deal(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введи количество звёзд (минимум 50):")
    await state.set_state(DealForm.waiting_stars)
    await callback.answer()

@dp.message(DealForm.waiting_stars)
async def process_stars(message: types.Message, state: FSMContext):
    try:
        stars = int(message.text)
        if stars < 50:
            await message.answer("Минимум 50. Попробуй ещё:")
            return
        await state.update_data(stars=stars)
        await message.answer("Введи свой @username, на который зачислить звёзды:")
        await state.set_state(DealForm.waiting_recipient)
    except ValueError:
        await message.answer("Введи число:")

@dp.message(DealForm.waiting_recipient)
async def process_recipient(message: types.Message, state: FSMContext):
    recipient = message.text.strip().replace("@", "")
    data = await state.get_data()
    stars = data['stars']
    price = calculate_price(stars)
    now = datetime.datetime.now()
    expires = now + datetime.timedelta(minutes=DEAL_TIMEOUT_MINUTES)
    
    # Генерируем случайный 5-значный ID сделки
    deal_id = random.randint(10000, 99999)
    while get_deal(deal_id): # Проверка, чтобы не было совпадений
        deal_id = random.randint(10000, 99999)

    cursor.execute("""
        INSERT INTO deals (id, buyer_id, buyer_username, stars_amount, recipient_username, price_uah, status, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
    """, (deal_id, message.from_user.id, message.from_user.username or "нет", stars, recipient, price, now.isoformat(), expires.isoformat()))
    conn.commit()

    await message.answer(
        f"✅ Сделка #{deal_id} создана.\nЗвёзд: {stars}\nК оплате: {price:.2f} грн\n"
        f"Получатель: @{recipient}\n\nЖди подтверждения от админа. Время: {DEAL_TIMEOUT_MINUTES} мин."
    )

    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_{deal_id}"),
         InlineKeyboardButton(text="❌ Отказаться", callback_data=f"decline_{deal_id}")]
    ])
    await bot.send_message(ADMIN_ID,
        f"🔔 Новая сделка #{deal_id}\nПокупатель: @{message.from_user.username or message.from_user.id}\n"
        f"Звёзд: {stars}\nЦена: {price:.2f} грн\nПолучатель: @{recipient}",
        reply_markup=admin_kb
    )
    asyncio.create_task(close_expired_deal(deal_id, message.from_user.id))
    await state.clear()

@dp.callback_query(F.data.startswith("accept_"))
async def admin_accept(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True); return
    deal_id = int(callback.data.split("_")[1])
    deal = get_deal(deal_id)
    if not deal or deal[6] != 'pending':
        await callback.answer("Сделка неактивна (возможно, база данных сервера обновилась)", show_alert=True); return

    update_deal_status(deal_id, 'accepted')
    buyer_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 Я оплатил, прикрепить чек", callback_data=f"paid_{deal_id}")]
    ])
    await bot.send_message(deal[1],
        f"✅ Сделка #{deal_id} принята!\nПереведи {deal[5]:.2f} грн на карту:\n`{CARD_NUMBER}`\n{CARD_NAME}\n\nПосле оплаты нажми кнопку и прикрепи скриншот.",
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
    if not deal or deal[6] != 'pending':
        await callback.answer("Сделка неактивна", show_alert=True); return

    update_deal_status(deal_id, 'declined')
    await bot.send_message(deal[1], f"❌ Сделка #{deal_id} отклонена. Попробуй позже.")
    await callback.message.edit_text(callback.message.text + "\n\n❌ Ты отклонил сделку.")
    await callback.answer()

@dp.callback_query(F.data.startswith("paid_"))
async def buyer_paid(callback: types.CallbackQuery, state: FSMContext):
    deal_id = int(callback.data.split("_")[1])
    deal = get_deal(deal_id)
    if not deal or deal[6] != 'accepted':
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
        [InlineKeyboardButton(text="✅ Звёзды выданы, завершить", callback_data=f"complete_{deal_id}")]
    ])
    await bot.send_photo(ADMIN_ID, photo=file_id,
        caption=f"💰 Чек по сделке #{deal_id}\nЗвёзд: {deal[3]}\nПолучатель: @{deal[4]}\nСумма: {deal[5]:.2f} грн\n\nПроверь оплату и отправь звёзды через Fragment.",
        reply_markup=admin_kb
    )
    await state.clear()

@dp.callback_query(F.data.startswith("complete_"))
async def admin_complete(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True); return
    deal_id = int(callback.data.split("_")[1])
    deal = get_deal(deal_id)
    if not deal or deal[6] != 'paid':
        await callback.answer("Сделка не в статусе 'оплачено'", show_alert=True); return

    update_deal_status(deal_id, 'completed')
    await bot.send_message(deal[1], f"🎉 Сделка #{deal_id} завершена! Звёзды отправлены на @{deal[4]}.")
    await callback.message.edit_caption(callback.message.caption + "\n\n✅ Завершено.")
    await callback.answer()

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
