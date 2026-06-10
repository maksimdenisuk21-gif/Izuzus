from fastapi import FastAPI, Request, HTTPException, Security, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
import json
import os
import random
import time
import hmac
import hashlib
from urllib.parse import parse_qs
from contextlib import asynccontextmanager
import aiosqlite
from datetime import datetime
from collections import deque
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, PreCheckoutQueryHandler, MessageHandler, filters

# ======================
# CONFIG & AUTH
# ======================
BOT_TOKEN = "8922972247:AAGbc4tYV51F3zxAGA3SuLcBY7PCyGRbXoE"
ADMIN_IDS = ["7092015279"]

API_KEY_HEADER = APIKeyHeader(name="Authorization", auto_error=True)

# Глобальные переменные для мультиплеера
recent_wins = deque(maxlen=50)
active_connections = []

def verify_telegram_data(init_data: str) -> dict:
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
# GAME CONFIGS - 8 КЕЙСОВ
# ======================
CASES_CONFIG = {
    "stars_1": {
        "cost": 100,
        "type": "stars",
        "name": "Обычный ⭐",
        "image": "https://example.com/case1.png",
        "items": [
            {"name": "10 звёзд", "value": 10, "rarity": "common"},
            {"name": "25 звёзд", "value": 25, "rarity": "uncommon"},
            {"name": "50 звёзд", "value": 50, "rarity": "rare"},
            {"name": "100 звёзд", "value": 100, "rarity": "epic"},
            {"name": "150 звёзд", "value": 150, "rarity": "legendary"},
            {"name": "200 звёзд", "value": 200, "rarity": "mythic"},
            {"name": "5 звёзд", "value": 5, "rarity": "trash"},
            {"name": "75 звёзд", "value": 75, "rarity": "rare"}
        ]
    },
    "stars_2": {
        "cost": 500,
        "type": "stars",
        "name": "Премиум ⭐⭐",
        "image": "https://example.com/case2.png",
        "items": [
            {"name": "500 звёзд", "value": 500, "rarity": "legendary"},
            {"name": "750 звёзд", "value": 750, "rarity": "epic"},
            {"name": "250 звёзд", "value": 250, "rarity": "rare"},
            {"name": "100 звёзд", "value": 100, "rarity": "uncommon"},
            {"name": "1000 звёзд", "value": 1000, "rarity": "mythic"},
            {"name": "400 звёзд", "value": 400, "rarity": "epic"},
            {"name": "50 звёзд", "value": 50, "rarity": "common"},
            {"name": "600 звёзд", "value": 600, "rarity": "legendary"}
        ]
    },
    "stars_3": {
        "cost": 1000,
        "type": "stars",
        "name": "Элитный ⭐⭐⭐",
        "image": "https://example.com/case3.png",
        "items": [
            {"name": "2000 звёзд", "value": 2000, "rarity": "mythic"},
            {"name": "1500 звёзд", "value": 1500, "rarity": "legendary"},
            {"name": "1000 звёзд", "value": 1000, "rarity": "epic"},
            {"name": "500 звёзд", "value": 500, "rarity": "rare"},
            {"name": "2500 звёзд", "value": 2500, "rarity": "mythic"},
            {"name": "800 звёзд", "value": 800, "rarity": "epic"},
            {"name": "200 звёзд", "value": 200, "rarity": "uncommon"},
            {"name": "1200 звёзд", "value": 1200, "rarity": "legendary"}
        ]
    },
    "stars_4": {
        "cost": 2000,
        "type": "stars",
        "name": "Божественный ⭐⭐⭐⭐",
        "image": "https://example.com/case4.png",
        "items": [
            {"name": "5000 звёзд", "value": 5000, "rarity": "mythic"},
            {"name": "3000 звёзд", "value": 3000, "rarity": "legendary"},
            {"name": "2000 звёзд", "value": 2000, "rarity": "epic"},
            {"name": "1000 звёзд", "value": 1000, "rarity": "rare"},
            {"name": "7500 звёзд", "value": 7500, "rarity": "mythic"},
            {"name": "2500 звёзд", "value": 2500, "rarity": "epic"},
            {"name": "500 звёзд", "value": 500, "rarity": "uncommon"},
            {"name": "4000 звёзд", "value": 4000, "rarity": "legendary"}
        ]
    },
    "nft_1": {
        "cost": 500,
        "type": "nft",
        "name": "NFT Кейс 1 🎨",
        "image": "https://example.com/nft1.png",
        "items": [
            {"name": "Bored Ape", "rarity": "rare", "nft_id": "nft_ape_1"},
            {"name": "Cryptopunk", "rarity": "epic", "nft_id": "nft_punk_1"},
            {"name": "Pudgy Penguin", "rarity": "uncommon", "nft_id": "nft_penguin_1"},
            {"name": "Cool Cat", "rarity": "rare", "nft_id": "nft_cat_1"},
            {"name": "Art Blocks", "rarity": "legendary", "nft_id": "nft_artblocks_1"},
            {"name": "Doodle", "rarity": "uncommon", "nft_id": "nft_doodle_1"},
            {"name": "Loot", "rarity": "epic", "nft_id": "nft_loot_1"},
            {"name": "Goblin Town", "rarity": "common", "nft_id": "nft_goblin_1"}
        ]
    },
    "nft_2": {
        "cost": 1000,
        "type": "nft",
        "name": "NFT Кейс 2 💎",
        "image": "https://example.com/nft2.png",
        "items": [
            {"name": "Azuki", "rarity": "epic", "nft_id": "nft_azuki_1"},
            {"name": "CloneX", "rarity": "legendary", "nft_id": "nft_clonex_1"},
            {"name": "World of Women", "rarity": "rare", "nft_id": "nft_wow_1"},
            {"name": "Pudgy Penguin Rare", "rarity": "epic", "nft_id": "nft_penguin_rare"},
            {"name": "Moonbirds", "rarity": "legendary", "nft_id": "nft_moonbirds_1"},
            {"name": "Murakami Flower", "rarity": "rare", "nft_id": "nft_murakami_1"},
            {"name": "Chromie Squiggle", "rarity": "epic", "nft_id": "nft_chromie_1"},
            {"name": "Invisible Friends", "rarity": "uncommon", "nft_id": "nft_inv_friends_1"}
        ]
    },
    "nft_3": {
        "cost": 2000,
        "type": "nft",
        "name": "NFT Кейс 3 👑",
        "image": "https://example.com/nft3.png",
        "items": [
            {"name": "Bored Ape Gold", "rarity": "mythic", "nft_id": "nft_ape_gold"},
            {"name": "Cryptopunk Alien", "rarity": "mythic", "nft_id": "nft_punk_alien"},
            {"name": "Doge NFT", "rarity": "legendary", "nft_id": "nft_doge_1"},
            {"name": "ENS Name", "rarity": "epic", "nft_id": "nft_ens_1"},
            {"name": "Bitcoin Ordinal", "rarity": "legendary", "nft_id": "nft_ordinal_1"},
            {"name": "Phantom", "rarity": "rare", "nft_id": "nft_phantom_1"},
            {"name": "Pudgy Penguin Ultra", "rarity": "mythic", "nft_id": "nft_penguin_ultra"},
            {"name": "Blur Beta", "rarity": "epic", "nft_id": "nft_blur_1"}
        ]
    },
    "nft_4": {
        "cost": 5000,
        "type": "nft",
        "name": "NFT Кейс 4 🌟",
        "image": "https://example.com/nft4.png",
        "items": [
            {"name": "Bored Ape Genesis", "rarity": "mythic", "nft_id": "nft_ape_genesis"},
            {"name": "Cryptopunk #1", "rarity": "mythic", "nft_id": "nft_punk_1_rare"},
            {"name": "Nakamigos", "rarity": "legendary", "nft_id": "nft_nakamigos_1"},
            {"name": "VeeFriends", "rarity": "epic", "nft_id": "nft_veefriends_1"},
            {"name": "Yuga Labs Metaverse", "rarity": "mythic", "nft_id": "nft_yuga_meta"},
            {"name": "Autoglyphs", "rarity": "legendary", "nft_id": "nft_autoglyphs_1"},
            {"name": "Larva Lads", "rarity": "rare", "nft_id": "nft_larva_1"},
            {"name": "Hashmasks", "rarity": "legendary", "nft_id": "nft_hashmask_1"}
        ]
    }
}

# ======================
# DB MANAGEMENT
# ======================
DB_PATH = "game.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            username TEXT,
            balance INTEGER DEFAULT 0,
            inventory TEXT DEFAULT '[]',
            nft_inventory TEXT DEFAULT '[]',
            withdraw_time INTEGER DEFAULT 0,
            profile_photo TEXT,
            created_at INTEGER DEFAULT 0
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            amount INTEGER,
            status TEXT,
            time INTEGER
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS game_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            username TEXT,
            game_type TEXT,
            result TEXT,
            amount INTEGER,
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
    tg_app = Application.builder().token(BOT_TOKEN).build()
    await setup_bot_handlers(tg_app)
    await tg_app.initialize()
    await tg_app.start()
    print("🚀 API & BOT RUNNING")
    yield
    await tg_app.stop()
    await tg_app.shutdown()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ======================
# API ENDPOINTS
# ======================

@app.get("/api/profile")
async def get_profile(auth_header: str = Security(API_KEY_HEADER)):
    tg_user = verify_telegram_data(auth_header)
    uid = str(tg_user["id"])
    username = tg_user.get("username", f"User{uid[-4:]}")
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance, inventory, nft_inventory, profile_photo FROM users WHERE user_id=?", (uid,)) as cursor:
            row = await cursor.fetchone()
            
        if not row:
            await db.execute(
                "INSERT INTO users (user_id, username, balance, created_at) VALUES (?, ?, ?, ?)",
                (uid, username, 1000, int(time.time()))
            )
            await db.commit()
            return {"user_id": uid, "username": username, "balance": 1000, "inventory": [], "nft_inventory": [], "profile_photo": None}
            
        balance, inv, nft_inv, photo = row
        return {
            "user_id": uid,
            "username": username,
            "balance": balance,
            "inventory": json.loads(inv) if inv else [],
            "nft_inventory": json.loads(nft_inv) if nft_inv else [],
            "profile_photo": photo
        }

@app.get("/api/cases")
async def get_cases():
    """Получить все кейсы"""
    return CASES_CONFIG

@app.post("/api/case/open")
async def case_open(case_id: str, auth_header: str = Security(API_KEY_HEADER)):
    """Открыть кейс"""
    tg_user = verify_telegram_data(auth_header)
    uid = str(tg_user["id"])
    username = tg_user.get("username", f"User{uid[-4:]}")
    
    if case_id not in CASES_CONFIG:
        raise HTTPException(status_code=400, detail="Invalid case")
    
    case = CASES_CONFIG[case_id]
    cost = case["cost"]
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance, inventory, nft_inventory FROM users WHERE user_id=?", (uid,)) as cursor:
            row = await cursor.fetchone()
            
        if not row or row[0] < cost:
            raise HTTPException(status_code=400, detail="Insufficient balance")
            
        balance, inv_raw, nft_inv_raw = row
        inv = json.loads(inv_raw) if inv_raw else []
        nft_inv = json.loads(nft_inv_raw) if nft_inv_raw else []
        
        # Выбираем предмет
        reward = random.choice(case["items"])
        
        new_balance = balance - cost
        
        if case["type"] == "stars":
            new_balance += reward["value"]
        else:
            reward["level"] = 1
            nft_inv.append(reward)
        
        await db.execute(
            "UPDATE users SET balance=?, inventory=?, nft_inventory=? WHERE user_id=?",
            (new_balance, json.dumps(inv), json.dumps(nft_inv), uid)
        )
        
        # Добавляем в историю
        await db.execute(
            "INSERT INTO game_history (user_id, username, game_type, result, amount, time) VALUES (?,?,?,?,?,?)",
            (uid, username, "case", json.dumps(reward), cost, int(time.time()))
        )
        
        await db.commit()
        
        # Добавляем в мультиплеер
        win_data = {
            "username": username,
            "item": reward.get("name", "Предмет"),
            "case": case["name"],
            "time": datetime.now().strftime("%H:%M:%S")
        }
        recent_wins.append(win_data)
        await broadcast_multiplayer()
        
        return {"reward": reward, "balance": new_balance}

@app.post("/api/crash/play")
async def crash_play(bet: int, multiplier: float, auth_header: str = Security(API_KEY_HEADER)):
    """Играть в Краш"""
    tg_user = verify_telegram_data(auth_header)
    uid = str(tg_user["id"])
    username = tg_user.get("username", f"User{uid[-4:]}")
    
    if bet <= 0 or multiplier < 1.0:
        raise HTTPException(status_code=400, detail="Invalid parameters")
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id=?", (uid,)) as cursor:
            row = await cursor.fetchone()
            
        if not row or row[0] < bet:
            raise HTTPException(status_code=400, detail="Insufficient balance")
        
        # Симуляция краша - случайная точка краша
        crash_point = round(random.uniform(1.05, 5.5), 2)
        win = multiplier <= crash_point
        
        if win:
            winnings = int(bet * multiplier)
            new_balance = row[0] - bet + winnings
        else:
            winnings = 0
            new_balance = row[0] - bet
        
        await db.execute("UPDATE users SET balance=? WHERE user_id=?", (new_balance, uid))
        
        await db.execute(
            "INSERT INTO game_history (user_id, username, game_type, result, amount, time) VALUES (?,?,?,?,?,?)",
            (uid, username, "crash", "WIN" if win else "LOSE", bet, int(time.time()))
        )
        
        await db.commit()
        
        if win:
            win_data = {
                "username": username,
                "item": f"Краш x{multiplier:.2f}",
                "case": "🚀 Краш",
                "time": datetime.now().strftime("%H:%M:%S")
            }
            recent_wins.append(win_data)
            await broadcast_multiplayer()
        
        return {
            "win": win,
            "crash_point": crash_point,
            "multiplier": multiplier,
            "winnings": winnings,
            "balance": new_balance
        }

@app.post("/api/mines/play")
async def mines_play(bet: int, multiplier: float, auth_header: str = Security(API_KEY_HEADER)):
    """Играть в Мины"""
    tg_user = verify_telegram_data(auth_header)
    uid = str(tg_user["id"])
    username = tg_user.get("username", f"User{uid[-4:]}")
    
    if bet <= 0 or multiplier < 1.0:
        raise HTTPException(status_code=400, detail="Invalid parameters")
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id=?", (uid,)) as cursor:
            row = await cursor.fetchone()
            
        if not row or row[0] < bet:
            raise HTTPException(status_code=400, detail="Insufficient balance")
        
        # 30% шанс попасть на мину
        hit_mine = random.random() < 0.30
        
        if not hit_mine:
            winnings = int(bet * multiplier)
            new_balance = row[0] - bet + winnings
            result = "WIN"
        else:
            new_balance = row[0] - bet
            winnings = 0
            result = "LOSE"
        
        await db.execute("UPDATE users SET balance=? WHERE user_id=?", (new_balance, uid))
        
        await db.execute(
            "INSERT INTO game_history (user_id, username, game_type, result, amount, time) VALUES (?,?,?,?,?,?)",
            (uid, username, "mines", result, bet, int(time.time()))
        )
        
        await db.commit()
        
        if result == "WIN":
            win_data = {
                "username": username,
                "item": f"Мины x{multiplier:.2f}",
                "case": "⛏️ Мины",
                "time": datetime.now().strftime("%H:%M:%S")
            }
            recent_wins.append(win_data)
            await broadcast_multiplayer()
        
        return {
            "hit_mine": hit_mine,
            "multiplier": multiplier,
            "winnings": winnings,
            "balance": new_balance
        }

@app.post("/api/upgrade/nft")
async def upgrade_nft(nft_index: int, auth_header: str = Security(API_KEY_HEADER)):
    """Апгрейдить NFT"""
    tg_user = verify_telegram_data(auth_header)
    uid = str(tg_user["id"])
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance, nft_inventory FROM users WHERE user_id=?", (uid,)) as cursor:
            row = await cursor.fetchone()
            
        if not row:
            raise HTTPException(status_code=400, detail="User not found")
        
        balance, nft_inv_raw = row
        nft_inv = json.loads(nft_inv_raw) if nft_inv_raw else []
        
        if nft_index < 0 or nft_index >= len(nft_inv):
            raise HTTPException(status_code=400, detail="Invalid NFT index")
        
        nft = nft_inv[nft_index]
        current_level = nft.get("level", 1)
        
        # Стоимость и шанс зависят от уровня
        upgrade_costs = {1: 200, 2: 400, 3: 800, 4: 1600, 5: 3200}
        upgrade_chances = {1: 0.9, 2: 0.8, 3: 0.7, 4: 0.5, 5: 0.3}
        
        upgrade_cost = upgrade_costs.get(current_level, 5000)
        success_chance = upgrade_chances.get(current_level, 0.1)
        
        if balance < upgrade_cost:
            raise HTTPException(status_code=400, detail="Insufficient balance")
        
        success = random.random() < success_chance
        new_balance = balance - upgrade_cost
        
        if success:
            nft["level"] = current_level + 1
            result = "SUCCESS"
        else:
            result = "FAILED"
        
        await db.execute("UPDATE users SET balance=?, nft_inventory=? WHERE user_id=?", (new_balance, json.dumps(nft_inv), uid))
        await db.commit()
        
        return {
            "success": success,
            "nft": nft,
            "cost": upgrade_cost,
            "balance": new_balance,
            "result": result
        }

@app.post("/api/inventory/sell")
async def sell_item(item_index: int, item_type: str, auth_header: str = Security(API_KEY_HEADER)):
    """Продать предмет"""
    tg_user = verify_telegram_data(auth_header)
    uid = str(tg_user["id"])
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance, inventory, nft_inventory FROM users WHERE user_id=?", (uid,)) as cursor:
            row = await cursor.fetchone()
            
        if not row:
            raise HTTPException(status_code=400, detail="User not found")
        
        balance, inv_raw, nft_inv_raw = row
        inv = json.loads(inv_raw) if inv_raw else []
        nft_inv = json.loads(nft_inv_raw) if nft_inv_raw else []
        
        sell_price = 50
        
        if item_type == "nft":
            if item_index < 0 or item_index >= len(nft_inv):
                raise HTTPException(status_code=400, detail="Invalid item index")
            nft_inv.pop(item_index)
        else:
            if item_index < 0 or item_index >= len(inv):
                raise HTTPException(status_code=400, detail="Invalid item index")
            inv.pop(item_index)
        
        new_balance = balance + sell_price
        
        await db.execute(
            "UPDATE users SET balance=?, inventory=?, nft_inventory=? WHERE user_id=?",
            (new_balance, json.dumps(inv), json.dumps(nft_inv), uid)
        )
        await db.commit()
        
        return {"balance": new_balance, "sold_for": sell_price}

@app.post("/api/withdraw")
async def withdraw(amount: int, auth_header: str = Security(API_KEY_HEADER)):
    """Вывести звёзды"""
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
        if last_withdraw and time.time() - last_withdraw < 86400:
            raise HTTPException(status_code=400, detail="Cooldown 24h active")
            
        new_balance = balance - amount
        
        await db.execute("UPDATE users SET balance=?, withdraw_time=? WHERE user_id=?", (new_balance, int(time.time()), uid))
        await db.execute("INSERT INTO withdrawals (user_id, amount, status, time) VALUES (?,?,?,?)",
                         (uid, amount, "pending", int(time.time())))
        await db.commit()
        
        return {"status": "pending", "amount": amount, "new_balance": new_balance}

@app.get("/api/multiplayer/recent")
async def get_recent_wins():
    """Получить последние выигрыши"""
    return list(recent_wins)

@app.websocket("/ws/multiplayer")
async def websocket_multiplayer(websocket: WebSocket):
    """WebSocket для мультиплеера"""
    await websocket.accept()
    active_connections.append(websocket)
    try:
        while True:
            await websocket.send_json({"recent_wins": list(recent_wins)})
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        active_connections.remove(websocket)

async def broadcast_multiplayer():
    """Отправить обновление мультиплеера всем"""
    for connection in active_connections:
        try:
            await connection.send_json({"recent_wins": list(recent_wins)})
        except:
            pass

# ======================
# BOT HANDLERS
# ======================
async def setup_bot_handlers(application: Application):
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("👋 Открой Mini App через меню бота!")

    async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.pre_checkout_query.answer(ok=True)

    async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
        payload = json.loads(update.message.successful_payment.invoice_payload)
        uid = payload["uid"]
        coins_to_add = payload["coins"]
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (coins_to_add, uid))
            await db.commit()
        await update.message.reply_text(f"🌟 Начислено {coins_to_add} звёзд.")

    async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if str(update.effective_user.id) not in ADMIN_IDS:
            return
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT * FROM withdrawals WHERE status='pending'") as cursor:
                rows = await cursor.fetchall()
        if not rows:
            await update.message.reply_text("Нет активных заявок.")
            return
        for w in rows:
            wid, uid, amount, status, t = w
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✔ Одобрить", callback_data=f"ap_{wid}"),
                InlineKeyboardButton("✖ Отклонить", callback_data=f"re_{wid}")
            ]])
            await update.message.reply_text(f"📥 Заявка #{wid}\nЮзер: {uid}\nСумма: {amount} ⭐", reply_markup=kb)

    async def cb_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()
        if str(q.from_user.id) not in ADMIN_IDS:
            return
        
        data = q.data.split("_")
        action, wid = data[0], data[1]
        
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT user_id, amount FROM withdrawals WHERE id=?", (wid,)) as cursor:
                row = await cursor.fetchone()
            if not row:
                return
            uid, amount = row
            if action == "ap":
                await db.execute("UPDATE withdrawals SET status='paid' WHERE id=?", (wid,))
            else:
                await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, uid))
                await db.execute("UPDATE withdrawals SET status='rejected' WHERE id=?", (wid,))
            await db.commit()
            await q.edit_message_text(f"✅ Выполнено для заявки #{wid}")

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CallbackQueryHandler(cb_admin))
    application.add_handler(PreCheckoutQueryHandler(pre_checkout))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

@app.post("/telegram")
async def telegram_webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return {"ok": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
