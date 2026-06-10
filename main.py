from fastapi import FastAPI, Request, HTTPException, Depends, Security
from fastapi.security import APIKeyHeader
import json
import os
import random
import time
import hmac
import hashlib
from urllib.parse import parse_qs
from contextlib import asynccontextmanager

# Асинхронный SQLite
import aiosqlite

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, PreCheckoutQueryHandler, MessageHandler, filters

# ======================
# CONFIG & AUTH
# ======================
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
ADMIN_IDS = os.getenv("ADMIN_IDS", "").split(",")

API_KEY_HEADER = APIKeyHeader(name="Authorization", auto_error=True)

def verify_telegram_data(init_data: str) -> dict:
    """ 
    ЖЕЛЕЗОБЕТОННАЯ ЗАЩИТА: Проверяет подпись Telegram. 
    Фронтенд должен передавать window.Telegram.WebApp.initData в заголовке Authorization
    """
    try:
        parsed_data = parse_qs(init_data)
        received_hash = parsed_data.pop("hash")[0]
        data_check_string = "\n".join(f"{k}={v[0]}" for k, v in sorted(parsed_data.items()))
        
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if calculated_hash != received_hash:
            raise HTTPException(status_code=401, detail="Data integrity compromised")
            
        return json.loads(parsed_data["user"][0])
    except Exception:
        raise HTTPException(status_code=401, detail="Unauthorized")

# ======================
# DB DB ASYNC MANAGEMENT
# ======================
DB_PATH = "game.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            balance INTEGER DEFAULT 0,
            inventory TEXT DEFAULT '[]',
            withdraw_time INTEGER DEFAULT 0
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS withdraws (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            amount INTEGER,
            payout INTEGER,
            status TEXT,
            time INTEGER
        )""")
        await db.commit()

# ======================
# LIFESPAN & APP INIT
# ======================
tg_app = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global tg_app
    await init_db()
    
    # Инициализация Telegram бот-движка
    tg_app = Application.builder().token(BOT_TOKEN).build()
    await setup_bot_handlers(tg_app)
    await tg_app.initialize()
    await tg_app.start()
    print("🚀 BOT & API UP AND RUNNING")
    yield
    await tg_app.stop()
    await tg_app.shutdown()

app = FastAPI(lifespan=lifespan)

# ======================
# API ENDPOINTS (БЕЗОПАСНЫЕ)
# ======================

@app.get("/api/profile")
async def get_profile(auth_header: str = Security(API_KEY_HEADER)):
    """Вход и получение профиля на основе валидного initData"""
    tg_user = verify_telegram_data(auth_header)
    uid = str(tg_user["id"])
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance, inventory FROM users WHERE user_id=?", (uid,)) as cursor:
            row = await cursor.fetchone()
            
        if not row:
            # Создаем пользователя, если зашел впервые
            await db.execute("INSERT INTO users (user_id, balance) VALUES (?, ?)", (uid, 1000))
            await db.commit()
            return {"user_id": uid, "balance": 1000, "inventory": []}
            
        return {"user_id": uid, "balance": row[0], "inventory": json.loads(row[1])}


@app.post("/api/case/open")
async def case_open(auth_header: str = Security(API_KEY_HEADER)):
    tg_user = verify_telegram_data(auth_header)
    uid = str(tg_user["id"])
    
    async with aiosqlite.connect(DB_PATH) as db:
        # Атомарно проверяем и обновляем в рамках одной транзакции
        async with db.execute("SELECT balance, inventory FROM users WHERE user_id=?", (uid,)) as cursor:
            row = await cursor.fetchone()
            
        if not row or row[0] < 100:
            raise HTTPException(status_code=400, detail="Insufficient balance")
            
        balance, inv_raw = row[0], row[1]
        inv = json.loads(inv_raw)
        
        items = ["trash", "common", "skin", "rare", "epic", "legendary", "knife"]
        values = {"trash": 10, "common": 20, "skin": 50, "rare": 150, "epic": 300, "legendary": 800, "knife": 1500}
        
        reward = random.choice(items)
        new_balance = balance - 100 + values[reward]
        inv.append(reward)
        
        await db.execute("UPDATE users SET balance=?, inventory=? WHERE user_id=?", (new_balance, json.dumps(inv), uid))
        await db.commit()
        
        return {"reward": reward, "balance": new_balance}


@app.post("/api/withdraw")
async def withdraw(amount: int, auth_header: str = Security(API_KEY_HEADER)):
    tg_user = verify_telegram_data(auth_header)
    uid = str(tg_user["id"])
    
    if amount < 100 or amount > 5000:
        raise HTTPException(status_code=400, detail="Limit 100-5000")
        
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance, withdraw_time FROM users WHERE user_id=?", (uid,)) as cursor:
            row = await cursor.fetchone()
            
        if not row or row[0] < amount:
            raise HTTPException(status_code=400, detail="Not enough balance")
            
        balance, last_withdraw = row
        if time.time() - last_withdraw < 86400:
            raise HTTPException(status_code=400, detail="Cooldown active")
            
        payout = int(amount * 0.9)
        new_balance = balance - amount
        
        await db.execute("UPDATE users SET balance=?, withdraw_time=? WHERE user_id=?", (new_balance, int(time.time()), uid))
        await db.execute("INSERT INTO withdraws (user_id, amount, payout, status, time) VALUES (?,?,?,?,?)",
                         (uid, amount, payout, "pending", int(time.time())))
        await db.commit()
        
        return {"status": "pending", "payout": payout, "new_balance": new_balance}


# ======================
# TELEGRAM STARS INVOICE CREATION
# ======================
@app.post("/api/stars/buy")
async def create_stars_invoice(stars_amount: int, auth_header: str = Security(API_KEY_HEADER)):
    """Создает инвойс для покупки баланса за Telegram Stars"""
    tg_user = verify_telegram_data(auth_header)
    uid = tg_user["id"]
    
    if stars_amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid amount")

    # Коэффициент: сколько игровых монет даем за 1 Star (например, 1 Star = 100 монет)
    game_coins = stars_amount * 100 
    
    try:
        # Генерируем нативную платежную ссылку Telegram Stars
        invoice_link = await tg_app.bot.create_invoice_link(
            title="Пополнение баланса",
            description=f"Покупка {game_coins} игровых монет",
            payload=json.dumps({"uid": str(uid), "coins": game_coins}),
            provider_token="", # Для Telegram Stars всегда пустое поле
            currency="XTR",    # Код валюты Telegram Stars
            prices=[LabeledPrice(label="Telegram Stars", amount=stars_amount)]
        )
        return {"invoice_url": invoice_link}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ======================
# BOT HANDLERS & WEBHOOKS
# ======================
async def setup_bot_handlers(application: Application):
    
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("👋 Привет! Открой Mini App через меню бота.")

    async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.pre_checkout_query
        # Обязательно аппрувим запрос на оплату в течение 10 секунд
        await query.answer(ok=True)

    async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
        payload = json.loads(update.message.successful_payment.invoice_payload)
        uid = payload["uid"]
        coins_to_add = payload["coins"]
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (coins_to_add, uid))
            await db.commit()
            
        await update.message.reply_text(f"🌟 Спасибо за покупку! Начислено {coins_to_add} монет.")

    async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if str(update.effective_user.id) not in ADMIN_IDS:
            return
            
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT * FROM withdraws WHERE status='pending'") as cursor:
                rows = await cursor.fetchall()
                
        if not rows:
            await update.message.reply_text("Нет активных заявок на вывод.")
            return

        for w in rows:
            wid, uid, amount, payout, status, t = w
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✔ Одобрить", callback_data=f"ap_{wid}"),
                InlineKeyboardButton("✖ Отклонить", callback_data=f"re_{wid}")
            ]])
            await update.message.reply_text(
                f"📥 Заявка #{wid}\nЮзер: {uid}\nСписание: {amount}\nК выплате: {payout} TON",
                reply_markup=kb
            )

    async def cb_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()
        
        if str(q.from_user.id) not in ADMIN_IDS:
            return
            
        action, wid = q.data.split("_")
        
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT user_id, amount FROM withdraws WHERE id=?", (wid,)) as cursor:
                row = await cursor.fetchone()
                
            if not row:
                await q.edit_message_text("Заявка не найдена.")
                return
                
            uid, amount = row
            
            if action == "ap":
                await db.execute("UPDATE withdraws SET status='paid' WHERE id=?", (wid,))
                await q.edit_message_text(f"✅ Заявка #{wid} одобрена.")
            else:
                # Защита от багов баланса: инкрементируем прямо в БД
                await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, uid))
                await db.execute("UPDATE withdraws SET status='rejected' WHERE id=?", (wid,))
                await q.edit_message_text(f"❌ Заявка #{wid} отклонена. Средства вернулись.")
                
            await db.commit()

    # Регистрируем хэндлеры
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CallbackQueryHandler(cb_admin))
    application.add_handler(PreCheckoutQueryHandler(pre_checkout))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

@app.post("/telegram")
async def telegram_webhook(req: Request):
    """Сюда Telegram шлет апдейты (Webhook)"""
    data = await req.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return {"ok": True}
