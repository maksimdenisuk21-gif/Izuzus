from fastapi import FastAPI, Request, HTTPException, Depends, Security, Form
from fastapi.security import APIKeyHeader
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import json
import os
import random
import time
import hmac
import hashlib
import asyncio
from urllib.parse import parse_qs
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, PreCheckoutQueryHandler, MessageHandler, filters

# ======================
# CONFIG
# ======================
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
ADMIN_IDS = [uid.strip() for uid in os.getenv("ADMIN_IDS", "").split(",") if uid.strip()]

API_KEY_HEADER = APIKeyHeader(name="Authorization", auto_error=True)

DB_PATH = "game.db"

# ======================
# GLOBAL CRASH STATE (multiplier game)
# ======================
class CrashGame:
    def __init__(self):
        self.active = False
        self.multiplier = 1.0
        self.crash_point = 0.0
        self.bets: Dict[str, int] = {}  # user_id -> bet_amount
        self.cashed: Dict[str, float] = {}  # user_id -> win_amount
        self.betting_open = False
        self.stage = "waiting"  # waiting, betting, playing, crashed
        self.next_round_time = 0
        self.round_start_time = 0

crash_game = CrashGame()

async def crash_loop():
    """Запускаем цикл краш-игры в фоне"""
    while True:
        await asyncio.sleep(1)
        now = time.time()
        if not crash_game.active:
            if now >= crash_game.next_round_time:
                # Начинаем новый раунд
                crash_game.active = True
                crash_game.stage = "betting"
                crash_game.betting_open = True
                crash_game.bets.clear()
                crash_game.cashed.clear()
                crash_game.multiplier = 1.0
                crash_game.crash_point = round(random.uniform(1.5, 10.0), 2)
                crash_game.round_start_time = now
                crash_game.betting_open_until = now + 10
                print(f"🆕 Новый раунд краша! Краш на {crash_game.crash_point}x")
        else:
            if crash_game.stage == "betting" and now >= crash_game.betting_open_until:
                crash_game.stage = "playing"
                crash_game.betting_open = False
                print("🔒 Ставки закрыты, игра началась")
            elif crash_game.stage == "playing":
                # Рост множителя
                increment = 0.03
                crash_game.multiplier = round(crash_game.multiplier + increment, 2)
                if crash_game.multiplier >= crash_game.crash_point:
                    # Краш
                    crash_game.active = False
                    crash_game.stage = "crashed"
                    crash_game.next_round_time = time.time() + 5
                    print(f"💥 КРАШ на {crash_game.multiplier}x! Все активные ставки проиграли.")
                    # Проигравшие не получают ничего, ставки уже списаны
                else:
                    # Проверяем, не кэшаутнул ли кто-то
                    pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    global tg_app
    await init_db()
    
    # Запускаем фоновую задачу краша
    asyncio.create_task(crash_loop())
    
    tg_app = Application.builder().token(BOT_TOKEN).build()
    await setup_bot_handlers(tg_app)
    await tg_app.initialize()
    await tg_app.start()
    print("✅ Бот и API запущены")
    yield
    await tg_app.stop()
    await tg_app.shutdown()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ======================
# DATABASE
# ======================
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
        await db.execute("""
        CREATE TABLE IF NOT EXISTS last_wins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_name TEXT,
            reward TEXT,
            value INTEGER,
            timestamp INTEGER
        )""")
        await db.commit()

# ======================
# AUTH
# ======================
def verify_telegram_data(init_data: str) -> dict:
    try:
        parsed_data = parse_qs(init_data)
        if "hash" not in parsed_data:
            raise HTTPException(401, "No hash")
        received_hash = parsed_data.pop("hash")[0]
        data_check_string = "\n".join(f"{k}={v[0]}" for k, v in sorted(parsed_data.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if calculated_hash != received_hash:
            raise HTTPException(401, "Invalid hash")
        user_data = json.loads(parsed_data["user"][0])
        return user_data
    except Exception as e:
        raise HTTPException(401, "Unauthorized")

# ======================
# API ENDPOINTS
# ======================
@app.get("/api/profile")
async def get_profile(auth_header: str = Security(API_KEY_HEADER)):
    tg_user = verify_telegram_data(auth_header)
    uid = str(tg_user["id"])
    name = tg_user.get("first_name", "") + " " + tg_user.get("last_name", "")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance, inventory FROM users WHERE user_id=?", (uid,)) as cursor:
            row = await cursor.fetchone()
        if not row:
            await db.execute("INSERT INTO users (user_id, balance, inventory) VALUES (?, ?, ?)", (uid, 0, "[]"))
            await db.commit()
            return {"user_id": uid, "name": name, "balance": 0, "inventory": []}
        return {"user_id": uid, "name": name, "balance": row[0], "inventory": json.loads(row[1])}

# Словарь кейсов: цена -> список предметов
CASES = {
    50: [
        {"name": "🌟 10 Stars", "type": "stars", "value": 10, "image": "https://i.imgur.com/star.png", "chance": 25},
        {"name": "🌟 20 Stars", "type": "stars", "value": 20, "image": "https://i.imgur.com/star.png", "chance": 20},
        {"name": "🌟 30 Stars", "type": "stars", "value": 30, "image": "https://i.imgur.com/star.png", "chance": 15},
        {"name": "🌟 50 Stars", "type": "stars", "value": 50, "image": "https://i.imgur.com/star.png", "chance": 10},
        {"name": "🖼️ NFT Common", "type": "nft", "value": 80, "image": "https://i.imgur.com/nft1.png", "chance": 15},
        {"name": "🖼️ NFT Rare", "type": "nft", "value": 120, "image": "https://i.imgur.com/nft2.png", "chance": 8},
        {"name": "🖼️ NFT Epic", "type": "nft", "value": 200, "image": "https://i.imgur.com/nft3.png", "chance": 5},
        {"name": "🖼️ NFT Legendary", "type": "nft", "value": 350, "image": "https://i.imgur.com/nft4.png", "chance": 2}
    ],
    200: [
        {"name": "🌟 50 Stars", "type": "stars", "value": 50, "chance": 20},
        {"name": "🌟 100 Stars", "type": "stars", "value": 100, "chance": 15},
        {"name": "🌟 150 Stars", "type": "stars", "value": 150, "chance": 12},
        {"name": "🌟 200 Stars", "type": "stars", "value": 200, "chance": 8},
        {"name": "🖼️ NFT Rare", "type": "nft", "value": 250, "chance": 20},
        {"name": "🖼️ NFT Epic", "type": "nft", "value": 400, "chance": 12},
        {"name": "🖼️ NFT Legendary", "type": "nft", "value": 600, "chance": 8},
        {"name": "🖼️ NFT Mythic", "type": "nft", "value": 900, "chance": 5}
    ],
    1000: [
        {"name": "🌟 300 Stars", "value": 300, "chance": 20},
        {"name": "🌟 500 Stars", "value": 500, "chance": 15},
        {"name": "🌟 700 Stars", "value": 700, "chance": 10},
        {"name": "🌟 1000 Stars", "value": 1000, "chance": 5},
        {"name": "🖼️ NFT Epic", "value": 1200, "chance": 20},
        {"name": "🖼️ NFT Legendary", "value": 1800, "chance": 15},
        {"name": "🖼️ NFT Mythic", "value": 2500, "chance": 10},
        {"name": "🖼️ NFT Godly", "value": 4000, "chance": 5}
    ],
    5000: [
        {"name": "🌟 2000 Stars", "value": 2000, "chance": 30},
        {"name": "🌟 3000 Stars", "value": 3000, "chance": 20},
        {"name": "🌟 4000 Stars", "value": 4000, "chance": 15},
        {"name": "🌟 5000 Stars", "value": 5000, "chance": 10},
        {"name": "🖼️ NFT Legendary", "value": 6000, "chance": 10},
        {"name": "🖼️ NFT Mythic", "value": 8000, "chance": 8},
        {"name": "🖼️ NFT Godly", "value": 12000, "chance": 5},
        {"name": "🖼️ NFT Divine", "value": 20000, "chance": 2}
    ]
}

@app.post("/api/case/open/{price}")
async def case_open(price: int, auth_header: str = Security(API_KEY_HEADER)):
    tg_user = verify_telegram_data(auth_header)
    uid = str(tg_user["id"])
    name = tg_user.get("first_name", "User")
    
    if price not in CASES:
        raise HTTPException(400, "Неверная цена кейса")
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance, inventory FROM users WHERE user_id=?", (uid,)) as cursor:
            row = await cursor.fetchone()
        if not row or row[0] < price:
            raise HTTPException(400, "Недостаточно звёзд")
        
        balance, inv_raw = row[0], row[1]
        inv = json.loads(inv_raw)
        
        items = CASES[price]
        # Рассчитываем шансы
        weights = [item["chance"] for item in items]
        reward = random.choices(items, weights=weights, k=1)[0].copy()
        
        new_balance = balance - price + reward["value"]
        inv.append(reward)
        
        await db.execute("UPDATE users SET balance=?, inventory=? WHERE user_id=?", (new_balance, json.dumps(inv), uid))
        await db.execute("INSERT INTO last_wins (user_name, reward, value, timestamp) VALUES (?,?,?,?)",
                         (name, reward["name"], reward["value"], int(time.time())))
        await db.commit()
        
        return {"reward": reward, "balance": new_balance}

@app.post("/api/crash/bet")
async def crash_bet(amount: int, auth_header: str = Security(API_KEY_HEADER)):
    tg_user = verify_telegram_data(auth_header)
    uid = str(tg_user["id"])
    
    if not crash_game.betting_open:
        raise HTTPException(400, "Ставки закрыты")
    if amount < 10:
        raise HTTPException(400, "Минимальная ставка 10 звёзд")
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id=?", (uid,)) as cursor:
            row = await cursor.fetchone()
        if not row or row[0] < amount:
            raise HTTPException(400, "Недостаточно средств")
        
        # Списываем ставку
        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (amount, uid))
        await db.commit()
    
    crash_game.bets[uid] = amount
    return {"status": "bet placed", "multiplier": crash_game.multiplier}

@app.post("/api/crash/cashout")
async def crash_cashout(auth_header: str = Security(API_KEY_HEADER)):
    tg_user = verify_telegram_data(auth_header)
    uid = str(tg_user["id"])
    
    if crash_game.stage != "playing":
        raise HTTPException(400, "Нельзя забрать сейчас")
    if uid not in crash_game.bets:
        raise HTTPException(400, "Вы не делали ставку в этом раунде")
    if uid in crash_game.cashed:
        raise HTTPException(400, "Вы уже забрали выигрыш")
    
    bet_amount = crash_game.bets[uid]
    win = int(bet_amount * crash_game.multiplier)
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (win, uid))
        await db.commit()
    
    crash_game.cashed[uid] = win
    return {"win": win, "multiplier": crash_game.multiplier}

@app.get("/api/crash/state")
async def crash_state():
    return {
        "active": crash_game.active,
        "stage": crash_game.stage,
        "multiplier": crash_game.multiplier,
        "betting_open_until": getattr(crash_game, "betting_open_until", 0),
        "next_round_time": crash_game.next_round_time
    }

@app.post("/api/mines/bet")
async def mines_bet(amount: int, auth_header: str = Security(API_KEY_HEADER)):
    tg_user = verify_telegram_data(auth_header)
    uid = str(tg_user["id"])
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id=?", (uid,)) as cursor:
            row = await cursor.fetchone()
        if not row or row[0] < amount:
            raise HTTPException(400, "Недостаточно средств")
        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (amount, uid))
        await db.commit()
    # Возвращаем seed для мины (клиент сам будет генерировать)
    random.seed(uid + str(int(time.time())))
    mines = random.sample(range(9), 3)
    return {"mines": mines, "bet": amount}

@app.post("/api/mines/cashout")
async def mines_cashout(amount: int, multiplier: float, auth_header: str = Security(API_KEY_HEADER)):
    tg_user = verify_telegram_data(auth_header)
    uid = str(tg_user["id"])
    win = int(amount * multiplier)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (win, uid))
        await db.commit()
    return {"win": win}

@app.post("/api/withdraw")
async def withdraw(request: Request, auth_header: str = Security(API_KEY_HEADER)):
    tg_user = verify_telegram_data(auth_header)
    uid = str(tg_user["id"])
    data = await request.json()
    amount = data.get("amount", 0)
    
    if amount < 100:
        raise HTTPException(400, "Минимальный вывод 100 звёзд")
    if amount > 5000:
        raise HTTPException(400, "Максимальный вывод 5000 звёзд")
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance, withdraw_time FROM users WHERE user_id=?", (uid,)) as cursor:
            row = await cursor.fetchone()
        if not row or row[0] < amount:
            raise HTTPException(400, "Недостаточно средств")
        balance, last_withdraw = row
        if time.time() - last_withdraw < 86400:
            raise HTTPException(400, "Вывод раз в 24 часа")
        
        payout = int(amount * 0.95)  # комиссия 5%
        new_balance = balance - amount
        
        await db.execute("UPDATE users SET balance=?, withdraw_time=? WHERE user_id=?", (new_balance, int(time.time()), uid))
        await db.execute("INSERT INTO withdraws (user_id, amount, payout, status, time) VALUES (?,?,?,?,?)",
                         (uid, amount, payout, "pending", int(time.time())))
        await db.commit()
        
        return {"status": "pending", "payout": payout, "new_balance": new_balance}

@app.post("/api/stars/buy")
async def create_stars_invoice(request: Request, auth_header: str = Security(API_KEY_HEADER)):
    tg_user = verify_telegram_data(auth_header)
    uid = tg_user["id"]
    data = await request.json()
    stars_amount = data.get("stars_amount", 0)
    
    if stars_amount < 50:
        raise HTTPException(400, "Минимальная покупка 50 звёзд")
    
    try:
        invoice_link = await tg_app.bot.create_invoice_link(
            title="Пополнение баланса",
            description=f"Покупка {stars_amount} звёзд",
            payload=json.dumps({"uid": str(uid), "stars": stars_amount}),
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="Telegram Stars", amount=stars_amount)]
        )
        return {"invoice_url": invoice_link}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/api/last_wins")
async def last_wins():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_name, reward, value, timestamp FROM last_wins ORDER BY timestamp DESC LIMIT 10") as cursor:
            rows = await cursor.fetchall()
        return [{"user": r[0], "reward": r[1], "value": r[2], "time": r[3]} for r in rows]

@app.get("/api/cases/list")
async def list_cases():
    return list(CASES.keys())

@app.get("/api/items/{price}")
async def get_items(price: int):
    if price not in CASES:
        raise HTTPException(404)
    return CASES[price]

@app.post("/api/update_inventory")
async def update_inventory(request: Request, auth_header: str = Security(API_KEY_HEADER)):
    tg_user = verify_telegram_data(auth_header)
    uid = str(tg_user["id"])
    data = await request.json()
    inventory = data.get("inventory", [])
    balance = data.get("balance", 0)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET inventory=?, balance=? WHERE user_id=?", (json.dumps(inventory), balance, uid))
        await db.commit()
    return {"ok": True}

# ======================
# ADMIN WEB PANEL
# ======================
@app.get("/admin.html", response_class=HTMLResponse)
async def admin_panel(request: Request):
    # Проверяем через заголовок или query parameter initData
    init_data = request.headers.get("Authorization") or request.query_params.get("initData")
    if not init_data:
        return HTMLResponse("<h1>Unauthorized</h1>", status_code=401)
    try:
        tg_user = verify_telegram_data(init_data)
        if str(tg_user["id"]) not in ADMIN_IDS:
            return HTMLResponse("<h1>Access denied</h1>", status_code=403)
    except:
        return HTMLResponse("<h1>Invalid auth</h1>", status_code=401)
    
    # Генерируем админ-панель
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, user_id, amount, payout, status, time FROM withdraws ORDER BY time DESC") as cursor:
            withdraws = await cursor.fetchall()
    
    html = """
    <!DOCTYPE html>
    <html>
    <head><title>Admin Panel - Case Fight</title><meta name="viewport" content="width=device-width,initial-scale=1">
    <style>body{background:#0a0f1e;color:white;font-family:sans-serif;padding:20px;} table{width:100%;border-collapse:collapse;} th,td{padding:10px;border:1px solid #333;} button{padding:5px 10px;margin:2px;cursor:pointer;} .approved{color:lightgreen;} .rejected{color:salmon;}</style>
    </head>
    <body>
    <h1>📋 Заявки на вывод</h1>
    <table><tr><th>ID</th><th>User ID</th><th>Сумма (⭐)</th><th>Выплата (⭐)</th><th>Статус</th><th>Действие</th></tr>
    """
    for w in withdraws:
        wid, uid, amount, payout, status, t = w
        html += f"<tr><td>{wid}</td><td>{uid}</td><td>{amount}</td><td>{payout}</td><td>{status}</td><td>"
        if status == "pending":
            html += f'<button onclick="action({wid}, \'approve\')">✅ Одобрить</button> '
            html += f'<button onclick="action({wid}, \'reject\')">❌ Отклонить</button>'
        else:
            html += status
        html += "</td></tr>"
    html += """
    </table>
    <script>
    async function action(id, act) {
        const res = await fetch('/api/admin/withdraw', {
            method: 'POST',
            headers: {'Content-Type':'application/json', 'Authorization': window.Telegram.WebApp.initData},
            body: JSON.stringify({id, action: act})
        });
        if(res.ok) location.reload();
        else alert('Ошибка');
    }
    </script>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    </body>
    </html>
    """
    return HTMLResponse(html)

@app.post("/api/admin/withdraw")
async def admin_withdraw(request: Request, auth_header: str = Security(API_KEY_HEADER)):
    tg_user = verify_telegram_data(auth_header)
    if str(tg_user["id"]) not in ADMIN_IDS:
        raise HTTPException(403, "Not admin")
    data = await request.json()
    wid = data["id"]
    action = data["action"]
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, amount, status FROM withdraws WHERE id=?", (wid,)) as cursor:
            row = await cursor.fetchone()
        if not row or row[2] != "pending":
            raise HTTPException(400, "Invalid withdraw")
        uid, amount, _ = row
        if action == "approve":
            await db.execute("UPDATE withdraws SET status='approved' WHERE id=?", (wid,))
            # Здесь можно отправить уведомление пользователю через бот
        elif action == "reject":
            await db.execute("UPDATE withdraws SET status='rejected' WHERE id=?", (wid,))
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, uid))
        await db.commit()
    return {"ok": True}

# ======================
# BOT HANDLERS
# ======================
tg_app = None

async def setup_bot_handlers(application: Application):
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("👋 Открой Mini App через меню бота.")
    
    async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.pre_checkout_query.answer(ok=True)
    
    async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
        payload = json.loads(update.message.successful_payment.invoice_payload)
        uid = payload["uid"]
        stars = payload["stars"]
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (stars, uid))
            await db.commit()
        await update.message.reply_text(f"✅ Баланс пополнен на {stars} звёзд!")
    
    async def admin_panel_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if str(update.effective_user.id) not in ADMIN_IDS:
            return
        await update.message.reply_text("Админ-панель доступна по ссылке: https://yourdomain.com/admin.html")
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_panel_bot))
    application.add_handler(PreCheckoutQueryHandler(pre_checkout))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

@app.post("/telegram")
async def telegram_webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return {"ok": True}
