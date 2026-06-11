from fastapi import FastAPI, Request, HTTPException, Depends, Security
from fastapi.security import APIKeyHeader
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import json
import os
import random
import time
import hmac
import hashlib
import asyncio
from urllib.parse import parse_qs
from typing import Dict

import aiosqlite
from telegram import Update, LabeledPrice
from telegram.ext import Application, CommandHandler, PreCheckoutQueryHandler, MessageHandler, filters

# ======================
# CONFIG
# ======================
BOT_TOKEN = "8922972247:AAGbc4tYV51F3zxAGA3SuLcBY7PCyGRbXoE"
ADMIN_IDS = ["7092015279"]

API_KEY_HEADER = APIKeyHeader(name="Authorization", auto_error=True)
DB_PATH = "game.db"

# ======================
# CRASH MULTIPLAYER STATE
# ======================
class CrashGame:
    def __init__(self):
        self.active = False
        self.multiplier = 1.0
        self.crash_point = 0.0
        self.bets: Dict[str, int] = {}
        self.cashed: Dict[str, float] = {}
        self.betting_open = False
        self.stage = "waiting"
        self.next_round_time = time.time() + 5
        self.betting_open_until = 0

crash_game = CrashGame()

async def crash_loop():
    while True:
        await asyncio.sleep(0.5)
        now = time.time()
        if not crash_game.active:
            if now >= crash_game.next_round_time:
                crash_game.active = True
                crash_game.stage = "betting"
                crash_game.betting_open = True
                crash_game.bets.clear()
                crash_game.cashed.clear()
                crash_game.multiplier = 1.0
                crash_game.crash_point = round(random.uniform(1.5, 10.0), 2)
                crash_game.betting_open_until = now + 10
                print(f"🆕 Новый раунд краша! Краш на {crash_game.crash_point}x")
        else:
            if crash_game.stage == "betting" and now >= crash_game.betting_open_until:
                crash_game.stage = "playing"
                crash_game.betting_open = False
                print("🔒 Ставки закрыты, игра началась")
            elif crash_game.stage == "playing":
                crash_game.multiplier = round(crash_game.multiplier + 0.03, 2)
                if crash_game.multiplier >= crash_game.crash_point:
                    crash_game.active = False
                    crash_game.stage = "crashed"
                    crash_game.next_round_time = time.time() + 5
                    print(f"💥 КРАШ на {crash_game.multiplier}x!")

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
        return json.loads(parsed_data["user"][0])
    except Exception:
        raise HTTPException(401, "Unauthorized")

# ======================
# FASTAPI APP
# ======================
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

tg_app = None
crash_task = None

# ======================
# STARTUP / SHUTDOWN
# ======================
@app.on_event("startup")
async def startup():
    global tg_app, crash_task
    await init_db()
    crash_task = asyncio.create_task(crash_loop())
    tg_app = Application.builder().token(BOT_TOKEN).updater(None).build()
    await tg_app.initialize()
    await setup_bot_handlers(tg_app)
    await tg_app.start()
    print("✅ Бот и API запущены")

@app.on_event("shutdown")
async def shutdown():
    global tg_app, crash_task
    if tg_app:
        await tg_app.stop()
        await tg_app.shutdown()
    if crash_task:
        crash_task.cancel()

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

CASES = {
    50: [{"name": "🌟 10 Stars", "type": "stars", "value": 10, "chance": 25},
         {"name": "🌟 20 Stars", "type": "stars", "value": 20, "chance": 20},
         {"name": "🌟 30 Stars", "type": "stars", "value": 30, "chance": 15},
         {"name": "🌟 50 Stars", "type": "stars", "value": 50, "chance": 10},
         {"name": "🖼️ NFT Common", "type": "nft", "value": 80, "chance": 15},
         {"name": "🖼️ NFT Rare", "type": "nft", "value": 120, "chance": 8},
         {"name": "🖼️ NFT Epic", "type": "nft", "value": 200, "chance": 5},
         {"name": "🖼️ NFT Legendary", "type": "nft", "value": 350, "chance": 2}],
    200: [{"name": "🌟 50 Stars", "value": 50, "chance": 20},
          {"name": "🌟 100 Stars", "value": 100, "chance": 15},
          {"name": "🌟 150 Stars", "value": 150, "chance": 12},
          {"name": "🌟 200 Stars", "value": 200, "chance": 8},
          {"name": "🖼️ NFT Rare", "value": 250, "chance": 20},
          {"name": "🖼️ NFT Epic", "value": 400, "chance": 12},
          {"name": "🖼️ NFT Legendary", "value": 600, "chance": 8},
          {"name": "🖼️ NFT Mythic", "value": 900, "chance": 5}],
    1000: [{"name": "🌟 300 Stars", "value": 300, "chance": 20},
           {"name": "🌟 500 Stars", "value": 500, "chance": 15},
           {"name": "🌟 700 Stars", "value": 700, "chance": 10},
           {"name": "🌟 1000 Stars", "value": 1000, "chance": 5},
           {"name": "🖼️ NFT Epic", "value": 1200, "chance": 20},
           {"name": "🖼️ NFT Legendary", "value": 1800, "chance": 15},
           {"name": "🖼️ NFT Mythic", "value": 2500, "chance": 10},
           {"name": "🖼️ NFT Godly", "value": 4000, "chance": 5}],
    5000: [{"name": "🌟 2000 Stars", "value": 2000, "chance": 30},
           {"name": "🌟 3000 Stars", "value": 3000, "chance": 20},
           {"name": "🌟 4000 Stars", "value": 4000, "chance": 15},
           {"name": "🌟 5000 Stars", "value": 5000, "chance": 10},
           {"name": "🖼️ NFT Legendary", "value": 6000, "chance": 10},
           {"name": "🖼️ NFT Mythic", "value": 8000, "chance": 8},
           {"name": "🖼️ NFT Godly", "value": 12000, "chance": 5},
           {"name": "🖼️ NFT Divine", "value": 20000, "chance": 2}]
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
        weights = [item["chance"] for item in items]
        reward = random.choices(items, weights=weights, k=1)[0].copy()
        reward["image"] = reward.get("image", "https://i.imgur.com/star.png")
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
        raise HTTPException(400, "Минимум 10 звёзд")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id=?", (uid,)) as cursor:
            row = await cursor.fetchone()
        if not row or row[0] < amount:
            raise HTTPException(400, "Недостаточно средств")
        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (amount, uid))
        await db.commit()
    crash_game.bets[uid] = amount
    return {"status": "bet placed"}

@app.post("/api/crash/cashout")
async def crash_cashout(auth_header: str = Security(API_KEY_HEADER)):
    tg_user = verify_telegram_data(auth_header)
    uid = str(tg_user["id"])
    if crash_game.stage != "playing":
        raise HTTPException(400, "Нельзя забрать сейчас")
    if uid not in crash_game.bets or uid in crash_game.cashed:
        raise HTTPException(400, "Ставка не найдена")
    bet = crash_game.bets[uid]
    win = int(bet * crash_game.multiplier)
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
    if amount < 100 or amount > 5000:
        raise HTTPException(400, "Лимит 100-5000 звёзд")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance, withdraw_time FROM users WHERE user_id=?", (uid,)) as cursor:
            row = await cursor.fetchone()
        if not row or row[0] < amount:
            raise HTTPException(400, "Недостаточно средств")
        balance, last_withdraw = row
        if time.time() - last_withdraw < 86400:
            raise HTTPException(400, "Вывод раз в 24 часа")
        payout = int(amount * 0.95)
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
        raise HTTPException(400, "Минимум 50 звёзд")
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
    return CASES.get(price, [])

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
# ADMIN PANEL
# ======================
@app.get("/admin.html", response_class=HTMLResponse)
async def admin_panel(request: Request):
    init_data = request.headers.get("Authorization") or request.query_params.get("initData")
    if not init_data:
        return HTMLResponse("<h1>Unauthorized</h1>", status_code=401)
    try:
        tg_user = verify_telegram_data(init_data)
        if str(tg_user["id"]) not in ADMIN_IDS:
            return HTMLResponse("<h1>Access denied</h1>", status_code=403)
    except:
        return HTMLResponse("<h1>Invalid auth</h1>", status_code=401)
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, user_id, amount, payout, status, time FROM withdraws ORDER BY time DESC") as cursor:
            withdraws = await cursor.fetchall()
    html = """<!DOCTYPE html>
    <html><head><title>Admin Panel</title><meta name="viewport" content="width=device-width,initial-scale=1">
    <style>body{background:#0a0f1e;color:white;font-family:sans-serif;padding:20px;} table{border-collapse:collapse;width:100%} th,td{padding:8px;border:1px solid #333} button{margin:2px;padding:5px 10px}</style>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    </head><body>
    <h1>Заявки на вывод</h1>
    <table><tr><th>ID</th><th>User ID</th><th>Сумма</th><th>Выплата</th><th>Статус</th><th>Действие</th></tr>
    """
    for w in withdraws:
        wid, uid, amt, payout, status, _ = w
        html += f"<tr><td>{wid}</td><td>{uid}</td><td>{amt}</td><td>{payout}</td><td>{status}</td><td>"
        if status == "pending":
            html += f'<button onclick="action({wid},\'approve\')">✅</button> <button onclick="action({wid},\'reject\')">❌</button>'
        else:
            html += status
        html += "</td></tr>"
    html += """</table>
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
    </body></html>"""
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
            raise HTTPException(400, "Invalid")
        uid, amount, _ = row
        if action == "approve":
            await db.execute("UPDATE withdraws SET status='approved' WHERE id=?", (wid,))
        else:
            await db.execute("UPDATE withdraws SET status='rejected' WHERE id=?", (wid,))
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, uid))
        await db.commit()
    return {"ok": True}

# ======================
# BOT HANDLERS
# ======================
async def setup_bot_handlers(application: Application):
    async def start(update: Update, context):
        await update.message.reply_text("👋 Открой Mini App через меню бота.")
    async def pre_checkout(update: Update, context):
        await update.pre_checkout_query.answer(ok=True)
    async def successful_payment(update: Update, context):
        payload = json.loads(update.message.successful_payment.invoice_payload)
        uid = payload["uid"]
        stars = payload["stars"]
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (stars, uid))
            await db.commit()
        await update.message.reply_text(f"✅ +{stars} звёзд!")
    async def admin_panel_bot(update: Update, context):
        if str(update.effective_user.id) not in ADMIN_IDS:
            return
        await update.message.reply_text("Админ-панель: https://casefight-osnova-miniapp.onrender.com/admin.html")
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

# ======================
# ROOT HTML (полный клиент)
# ======================
@app.get("/")
async def root():
    html_content = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>Case Fight</title>
    <style>
        *{margin:0;padding:0;box-sizing:border-box;user-select:none;}
        body{background:linear-gradient(145deg,#0a0f1e,#0c1222);font-family:'Segoe UI',system-ui;color:white;padding:16px;}
        .app-title{text-align:center;font-size:2rem;font-weight:800;background:linear-gradient(135deg,#FFD700,#FF8C00,#FF1493);-webkit-background-clip:text;background-clip:text;color:transparent;margin-bottom:20px;}
        .user-info{background:rgba(255,255,255,0.05);backdrop-filter:blur(10px);border-radius:28px;padding:12px 20px;display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;}
        .balance{background:linear-gradient(95deg,#f0b90b,#ffd966);padding:6px 16px;border-radius:40px;font-weight:bold;color:#1e2a3a;}
        .nav-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(80px,1fr));gap:12px;margin-bottom:24px;}
        .nav-btn{background:linear-gradient(145deg,#1f2a3e,#141b2b);border:none;border-radius:32px;padding:12px 0;font-weight:bold;color:white;transition:0.2s;cursor:pointer;text-align:center;}
        .nav-btn.active{background:linear-gradient(135deg,#ffb347,#ff8c00);color:#111;box-shadow:0 0 12px rgba(255,140,0,0.6);}
        .section{display:none;animation:fadeIn 0.3s;}
        .section.active{display:block;}
        @keyframes fadeIn{from{opacity:0;}to{opacity:1;}}
        .case-card{background:rgba(20,25,45,0.7);backdrop-filter:blur(12px);border-radius:36px;padding:20px;margin-bottom:20px;text-align:center;}
        .slot-machine{background:#01050f;border-radius:48px;padding:20px;margin:20px 0;min-height:120px;display:flex;justify-content:center;align-items:center;font-size:1.5rem;gap:10px;flex-wrap:wrap;}
        .item-spin{background:#1e2a3e;border-radius:20px;padding:10px;text-align:center;}
        .prize-list{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:15px;}
        .prize-item{background:#1f2a3e;border-radius:20px;padding:6px;font-size:0.7rem;text-align:center;}
        button{background:linear-gradient(95deg,#ff9800,#ffc107);border:none;padding:10px 20px;border-radius:60px;font-weight:bold;cursor:pointer;margin:5px;}
        .crash-game,.mines-game{background:#000000aa;border-radius:40px;padding:20px;text-align:center;}
        .multiplier{font-size:3rem;font-weight:800;color:#ffd966;}
        .bet-control{display:flex;gap:10px;justify-content:center;margin:15px 0;flex-wrap:wrap;}
        .bet-control input{background:#1f2a3e;border:none;padding:10px;border-radius:60px;color:white;width:120px;text-align:center;}
        .mines-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:20px 0;}
        .mine-cell{aspect-ratio:1;background:#1e2a3e;border-radius:20px;display:flex;align-items:center;justify-content:center;font-size:2rem;cursor:pointer;}
        .inventory-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;}
        .inventory-card{background:#1f2a3e;border-radius:24px;padding:12px;display:flex;align-items:center;justify-content:space-between;}
        .last-wins{margin-top:20px;background:#0a0f1e99;border-radius:28px;padding:12px;font-size:0.8rem;}
        .case-select{display:flex;gap:10px;justify-content:center;margin-bottom:15px;flex-wrap:wrap;}
        .case-price-btn{background:#2a3a5e;padding:8px 16px;border-radius:40px;cursor:pointer;}
        .case-price-btn.active{background:#ff9800;color:#111;}
    </style>
</head>
<body>
<div class="app-title">⚔️ CASE FIGHT ⚔️</div>
<div class="user-info"><span id="userName">Загрузка...</span><span class="balance" id="balance">0</span></div>
<div class="nav-grid">
    <button class="nav-btn" data-section="cases">🎁 Кейсы</button>
    <button class="nav-btn" data-section="crash">🚀 Краш</button>
    <button class="nav-btn" data-section="mines">💣 Мины</button>
    <button class="nav-btn" data-section="upgrade">⬆️ Апгрейд</button>
    <button class="nav-btn" data-section="profile">👤 Профиль</button>
</div>

<div id="cases" class="section active">
    <div class="case-card">
        <div class="case-select" id="casePrices"></div>
        <div id="spinResult" class="slot-machine">Нажми "Крутить"</div>
        <button id="openCaseBtn">✨ ОТКРЫТЬ КЕЙС ✨</button>
        <button id="showItemsBtn">📋 Возможные выпадения</button>
        <div class="prize-list" id="prizePreview"></div>
    </div>
</div>

<div id="crash" class="section">
    <div class="crash-game">
        <div class="multiplier" id="crashMultiplier">1.00x</div>
        <div id="crashTimer">⚡ Ставки открыты: <span id="bettingTimer">0</span>с</div>
        <div class="bet-control">
            <input type="number" id="crashBet" placeholder="Ставка" value="100">
            <button id="placeCrashBet">Сделать ставку</button>
            <button id="cashoutCrashBtn" disabled>Забрать</button>
        </div>
        <div id="crashStatus">Ожидание раунда...</div>
    </div>
</div>

<div id="mines" class="section">
    <div class="mines-game">
        <div class="multiplier" id="minesMultiplier">1.00x</div>
        <div class="bet-control">
            <input type="number" id="minesBet" value="100">
            <button id="startMinesBtn">Начать игру</button>
            <button id="minesCashoutBtn" disabled>Забрать</button>
        </div>
        <div id="minesGrid" class="mines-grid"></div>
        <div id="minesStatus"></div>
    </div>
</div>

<div id="upgrade" class="section">
    <div class="case-card">
        <h3>Улучшить NFT</h3>
        <select id="upgradeSelect"></select>
        <button id="upgradeBtn">Улучшить (шанс 40%)</button>
        <div id="upgradeMsg"></div>
    </div>
</div>

<div id="profile" class="section">
    <div class="case-card">
        <h3>Инвентарь</h3>
        <div id="inventoryList" class="inventory-grid"></div>
        <div style="margin-top:20px;">
            <button id="withdrawBtn">💸 Вывести звёзды</button>
            <button id="depositBtn">⭐ Пополнить звёздами</button>
        </div>
        <div id="withdrawPanel" style="display:none; margin-top:15px;">
            <input type="number" id="withdrawAmount" placeholder="Сумма (100-5000)" min="100" max="5000">
            <button id="submitWithdraw">Отправить заявку</button>
        </div>
    </div>
    <div class="last-wins" id="lastWins">🏆 Последние выигрыши: загрузка...</div>
</div>

<script>
    const API_BASE = window.location.origin;
    const tg = window.Telegram?.WebApp;
    if(tg) tg.expand();
    
    let currentUser = null;
    let balance = 0;
    let inventory = [];
    let selectedCasePrice = 50;
    let myCrashBet = 0;
    let minesActive = false, minesData = null, minesRevealed = [], minesBetAmount = 0;
    
    async function apiCall(endpoint, method='GET', body=null) {
        const initData = tg ? tg.initData : '';
        const headers = {'Authorization': initData, 'Content-Type':'application/json'};
        const options = {method, headers};
        if(body) options.body = JSON.stringify(body);
        const res = await fetch(API_BASE+endpoint, options);
        if(!res.ok) throw new Error(await res.text());
        return res.json();
    }
    
    async function loadProfile() {
        const data = await apiCall('/api/profile');
        currentUser = data;
        balance = data.balance;
        inventory = data.inventory || [];
        document.getElementById('userName').innerText = data.name || 'Игрок';
        document.getElementById('balance').innerText = balance;
        renderInventory();
        renderUpgradeSelect();
    }
    
    function renderInventory() {
        const container = document.getElementById('inventoryList');
        if(!container) return;
        if(inventory.length===0) {container.innerHTML='<
