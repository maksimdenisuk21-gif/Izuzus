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

BOT_TOKEN = "8922972247:AAGbc4tYV51F3zxAGA3SuLcBY7PCyGRbXoE"
ADMIN_IDS = ["7092015279"]

API_KEY_HEADER = APIKeyHeader(name="Authorization", auto_error=True)

recent_wins = deque(maxlen=50)
active_connections = []
crash_games = {}

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

def get_weighted_item(items):
    total = sum(item["chance"] for item in items)
    r = random.uniform(0, total)
    current = 0
    for item in items:
        current += item["chance"]
        if r <= current:
            return item
    return items[-1]

CASES_CONFIG = {
    "stars_cheap": {
        "cost": 50,
        "type": "stars",
        "name": "Стартовый ⭐",
        "items": [
            {"name": "10 звёзд", "value": 10, "rarity": "common", "chance": 0.35},
            {"name": "15 звёзд", "value": 15, "rarity": "common", "chance": 0.25},
            {"name": "30 звёзд", "value": 30, "rarity": "uncommon", "chance": 0.20},
            {"name": "50 звёзд", "value": 50, "rarity": "rare", "chance": 0.12},
            {"name": "100 звёзд", "value": 100, "rarity": "epic", "chance": 0.05},
            {"name": "5 звёзд", "value": 5, "rarity": "trash", "chance": 0.03},
            {"name": "25 звёзд", "value": 25, "rarity": "uncommon", "chance": 0.0},
            {"name": "75 звёзд", "value": 75, "rarity": "rare", "chance": 0.0},
        ]
    },
    "stars_medium": {
        "cost": 200,
        "type": "stars",
        "name": "Стандартный ⭐⭐",
        "items": [
            {"name": "50 звёзд", "value": 50, "rarity": "common", "chance": 0.25},
            {"name": "100 звёзд", "value": 100, "rarity": "uncommon", "chance": 0.30},
            {"name": "200 звёзд", "value": 200, "rarity": "rare", "chance": 0.20},
            {"name": "300 звёзд", "value": 300, "rarity": "epic", "chance": 0.15},
            {"name": "500 звёзд", "value": 500, "rarity": "legendary", "chance": 0.08},
            {"name": "30 звёзд", "value": 30, "rarity": "trash", "chance": 0.02},
            {"name": "150 звёзд", "value": 150, "rarity": "uncommon", "chance": 0.0},
            {"name": "400 звёзд", "value": 400, "rarity": "epic", "chance": 0.0},
        ]
    },
    "stars_premium": {
        "cost": 500,
        "type": "stars",
        "name": "Премиум ⭐⭐⭐",
        "items": [
            {"name": "200 звёзд", "value": 200, "rarity": "common", "chance": 0.15},
            {"name": "400 звёзд", "value": 400, "rarity": "uncommon", "chance": 0.25},
            {"name": "750 звёзд", "value": 750, "rarity": "rare", "chance": 0.25},
            {"name": "1000 звёзд", "value": 1000, "rarity": "epic", "chance": 0.20},
            {"name": "1500 звёзд", "value": 1500, "rarity": "legendary", "chance": 0.12},
            {"name": "2000 звёзд", "value": 2000, "rarity": "mythic", "chance": 0.03},
            {"name": "300 звёзд", "value": 300, "rarity": "uncommon", "chance": 0.0},
            {"name": "600 звёзд", "value": 600, "rarity": "rare", "chance": 0.0},
        ]
    },
    "stars_legendary": {
        "cost": 5000,
        "type": "stars",
        "name": "Легендарный ⭐⭐⭐⭐",
        "items": [
            {"name": "2000 звёзд", "value": 2000, "rarity": "common", "chance": 0.10},
            {"name": "4000 звёзд", "value": 4000, "rarity": "uncommon", "chance": 0.15},
            {"name": "7500 звёзд", "value": 7500, "rarity": "rare", "chance": 0.25},
            {"name": "10000 звёзд", "value": 10000, "rarity": "epic", "chance": 0.25},
            {"name": "15000 звёзд", "value": 15000, "rarity": "legendary", "chance": 0.15},
            {"name": "25000 звёзд", "value": 25000, "rarity": "mythic", "chance": 0.10},
            {"name": "5000 звёзд", "value": 5000, "rarity": "uncommon", "chance": 0.0},
            {"name": "12000 звёзд", "value": 12000, "rarity": "epic", "chance": 0.0},
        ]
    },
    "nft_starter": {
        "cost": 500,
        "type": "nft",
        "name": "NFT Стартер 🎨",
        "items": [
            {"name": "Doodles", "value": 500, "rarity": "common", "chance": 0.30},
            {"name": "Pudgy Penguin", "value": 800, "rarity": "uncommon", "chance": 0.25},
            {"name": "Cool Cats NFT", "value": 600, "rarity": "rare", "chance": 0.20},
            {"name": "Goblin Town", "value": 300, "rarity": "common", "chance": 0.15},
            {"name": "Loot", "value": 1000, "rarity": "epic", "chance": 0.08},
            {"name": "DigiDaigaku", "value": 250, "rarity": "trash", "chance": 0.02},
            {"name": "World of Women", "value": 700, "rarity": "uncommon", "chance": 0.0},
            {"name": "Art Blocks", "value": 900, "rarity": "rare", "chance": 0.0},
        ]
    },
    "nft_premium": {
        "cost": 1000,
        "type": "nft",
        "name": "NFT Премиум 💎",
        "items": [
            {"name": "Azuki", "value": 1500, "rarity": "rare", "chance": 0.25},
            {"name": "CloneX", "value": 2000, "rarity": "epic", "chance": 0.20},
            {"name": "World of Women", "value": 1200, "rarity": "rare", "chance": 0.20},
            {"name": "Moonbirds", "value": 2500, "rarity": "legendary", "chance": 0.15},
            {"name": "Invisible Friends", "value": 800, "rarity": "uncommon", "chance": 0.15},
            {"name": "Blur Blur", "value": 600, "rarity": "common", "chance": 0.05},
            {"name": "Pudgy Penguin Rare", "value": 1800, "rarity": "epic", "chance": 0.0},
            {"name": "Murakami Flower", "value": 950, "rarity": "rare", "chance": 0.0},
        ]
    },
    "nft_elite": {
        "cost": 2000,
        "type": "nft",
        "name": "NFT Элит 👑",
        "items": [
            {"name": "Bored Ape Yacht Club", "value": 5000, "rarity": "legendary", "chance": 0.20},
            {"name": "Cryptopunks", "value": 4500, "rarity": "epic", "chance": 0.20},
            {"name": "Art Blocks", "value": 3000, "rarity": "epic", "chance": 0.20},
            {"name": "Pudgy Penguin Rare", "value": 2500, "rarity": "rare", "chance": 0.20},
            {"name": "ENS Names", "value": 1500, "rarity": "uncommon", "chance": 0.15},
            {"name": "Murakami Flower", "value": 1000, "rarity": "common", "chance": 0.05},
            {"name": "Autoglyphs", "value": 4000, "rarity": "legendary", "chance": 0.0},
            {"name": "Nakamigos", "value": 2800, "rarity": "epic", "chance": 0.0},
        ]
    },
    "nft_mythic": {
        "cost": 5000,
        "type": "nft",
        "name": "NFT Мифик 🌟",
        "items": [
            {"name": "Bored Ape Genesis", "value": 8000, "rarity": "mythic", "chance": 0.15},
            {"name": "Cryptopunk Alien", "value": 7500, "rarity": "mythic", "chance": 0.15},
            {"name": "Doge NFT", "value": 6000, "rarity": "legendary", "chance": 0.15},
            {"name": "Autoglyphs", "value": 5000, "rarity": "legendary", "chance": 0.15},
            {"name": "VeeFriends", "value": 3000, "rarity": "epic", "chance": 0.20},
            {"name": "Bitcoin Ordinals", "value": 4000, "rarity": "epic", "chance": 0.20},
            {"name": "Moonbirds Odd", "value": 4500, "rarity": "legendary", "chance": 0.0},
            {"name": "Yuga Labs Metaverse", "value": 6500, "rarity": "mythic", "chance": 0.0},
        ]
    }
}

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
            created_at INTEGER DEFAULT 0
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            amount INTEGER,
            status TEXT DEFAULT 'pending',
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

@app.get("/api/profile")
async def get_profile(auth_header: str = Security(API_KEY_HEADER)):
    tg_user = verify_telegram_data(auth_header)
    uid = str(tg_user["id"])
    username = tg_user.get("username", f"User{uid[-4:]}")
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance, inventory, nft_inventory FROM users WHERE user_id=?", (uid,)) as cursor:
            row = await cursor.fetchone()
            
        if not row:
            await db.execute(
                "INSERT INTO users (user_id, username, balance, created_at) VALUES (?, ?, ?, ?)",
                (uid, username, 0, int(time.time()))
            )
            await db.commit()
            return {"user_id": uid, "username": username, "balance": 0, "inventory": [], "nft_inventory": []}
            
        balance, inv, nft_inv = row
        return {
            "user_id": uid,
            "username": username,
            "balance": balance,
            "inventory": json.loads(inv) if inv else [],
            "nft_inventory": json.loads(nft_inv) if nft_inv else []
        }

@app.get("/api/cases")
async def get_cases():
    return CASES_CONFIG

@app.post("/api/case/open")
async def case_open(case_id: str, auth_header: str = Security(API_KEY_HEADER)):
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
        
        reward = get_weighted_item(case["items"])
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
        
        await db.execute(
            "INSERT INTO game_history (user_id, username, game_type, result, amount, time) VALUES (?,?,?,?,?,?)",
            (uid, username, "case", json.dumps(reward), cost, int(time.time()))
        )
        
        await db.commit()
        
        win_data = {
            "username": username,
            "item": reward.get("name", "Предмет"),
            "case": case["name"],
            "time": datetime.now().strftime("%H:%M:%S")
        }
        recent_wins.append(win_data)
        await broadcast_multiplayer()
        
        return {"reward": reward, "balance": new_balance}

@app.post("/api/crash/start")
async def crash_start(bet: int, auth_header: str = Security(API_KEY_HEADER)):
    tg_user = verify_telegram_data(auth_header)
    uid = str(tg_user["id"])
    
    if bet <= 0:
        raise HTTPException(status_code=400, detail="Invalid bet")
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id=?", (uid,)) as cursor:
            row = await cursor.fetchone()
            
        if not row or row[0] < bet:
            raise HTTPException(status_code=400, detail="Insufficient balance")
        
        new_balance = row[0] - bet
        await db.execute("UPDATE users SET balance=? WHERE user_id=?", (new_balance, uid))
        await db.commit()
    
    game_id = f"{uid}_{int(time.time() * 1000)}"
    crash_point = round(random.uniform(1.05, 5.5), 2)
    
    crash_games[game_id] = {
        "user_id": uid,
        "bet": bet,
        "crash_point": crash_point,
        "start_time": time.time(),
        "status": "active"
    }
    
    return {"game_id": game_id, "bet": bet, "balance_after_bet": new_balance}

@app.get("/api/crash/update/{game_id}")
async def crash_update(game_id: str):
    if game_id not in crash_games:
        raise HTTPException(status_code=404, detail="Game not found")
    
    game = crash_games[game_id]
    elapsed = time.time() - game["start_time"]
    
    multiplier = 1.0 + (elapsed * 0.5) + (elapsed ** 1.5 * 0.1)
    multiplier = round(multiplier, 2)
    
    crashed = multiplier >= game["crash_point"]
    
    return {
        "multiplier": multiplier,
        "crashed": crashed,
        "crash_point": game["crash_point"] if crashed else None
    }

@app.post("/api/crash/cashout")
async def crash_cashout(game_id: str, multiplier: float, auth_header: str = Security(API_KEY_HEADER)):
    tg_user = verify_telegram_data(auth_header)
    uid = str(tg_user["id"])
    username = tg_user.get("username", f"User{uid[-4:]}")
    
    if game_id not in crash_games:
        raise HTTPException(status_code=404, detail="Game not found")
    
    game = crash_games[game_id]
    
    if game["user_id"] != uid:
        raise HTTPException(status_code=403, detail="Not your game")
    
    if game["status"] != "active":
        raise HTTPException(status_code=400, detail="Game already finished")
    
    crashed = multiplier >= game["crash_point"]
    
    if crashed:
        winnings = 0
        result = "LOSE"
    else:
        winnings = int(game["bet"] * multiplier)
        result = "WIN"
    
    game["status"] = "finished"
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id=?", (uid,)) as cursor:
            row = await cursor.fetchone()
        
        new_balance = row[0] + winnings
        await db.execute("UPDATE users SET balance=? WHERE user_id=?", (new_balance, uid))
        
        await db.execute(
            "INSERT INTO game_history (user_id, username, game_type, result, amount, time) VALUES (?,?,?,?,?,?)",
            (uid, username, "crash", result, game["bet"], int(time.time()))
        )
        
        await db.commit()
    
    if result == "WIN":
        win_data = {
            "username": username,
            "item": f"Краш x{multiplier:.2f}",
            "case": "🚀 Краш",
            "time": datetime.now().strftime("%H:%M:%S")
        }
        recent_wins.append(win_data)
        await broadcast_multiplayer()
    
    del crash_games[game_id]
    
    return {
        "result": result,
        "multiplier": multiplier,
        "crash_point": game["crash_point"],
        "bet": game["bet"],
        "winnings": winnings,
        "balance": new_balance
    }

@app.post("/api/mines/play")
async def mines_play(bet: int, multiplier: float, auth_header: str = Security(API_KEY_HEADER)):
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
        
        return {"hit_mine": hit_mine, "multiplier": multiplier, "winnings": winnings, "balance": new_balance}

@app.post("/api/upgrade/nft")
async def upgrade_nft(nft_index: int, auth_header: str = Security(API_KEY_HEADER)):
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
        
        await db.execute("UPDATE users SET balance=?, nft_inventory=? WHERE user_id=?", (new_balance, json.dumps(nft_inv), uid))
        await db.commit()
        
        return {"success": success, "nft": nft, "cost": upgrade_cost, "balance": new_balance}

@app.post("/api/inventory/sell")
async def sell_item(item_index: int, item_type: str, auth_header: str = Security(API_KEY_HEADER)):
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
        
        if item_type == "nft":
            if item_index < 0 or item_index >= len(nft_inv):
                raise HTTPException(status_code=400, detail="Invalid item index")
            item = nft_inv.pop(item_index)
            sell_price = int(item.get("value", 500) * 0.7)
        else:
            if item_index < 0 or item_index >= len(inv):
                raise HTTPException(status_code=400, detail="Invalid item index")
            inv.pop(item_index)
            sell_price = 25
        
        new_balance = balance + sell_price
        
        await db.execute(
            "UPDATE users SET balance=?, inventory=?, nft_inventory=? WHERE user_id=?",
            (new_balance, json.dumps(inv), json.dumps(nft_inv), uid)
        )
        await db.commit()
        
        return {"balance": new_balance, "sold_for": sell_price}

@app.post("/api/stars/buy")
async def create_stars_invoice(stars_amount: int, auth_header: str = Security(API_KEY_HEADER)):
    tg_user = verify_telegram_data(auth_header)
    uid = tg_user["id"]
    
    if stars_amount < 50 or stars_amount > 5000:
        raise HTTPException(status_code=400, detail="Amount 50-5000")

    commission = int(stars_amount * 0.05)
    game_coins = (stars_amount - commission) * 10
    
    try:
        invoice_link = await tg_app.bot.create_invoice_link(
            title="Пополнение Case Fight",
            description=f"Покупка {game_coins} звёзд (комиссия 5%)",
            payload=json.dumps({"uid": str(uid), "coins": game_coins}),
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="Telegram Stars", amount=stars_amount)]
        )
        return {"invoice_url": invoice_link, "game_coins": game_coins}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/withdraw")
async def withdraw(amount: int, auth_header: str = Security(API_KEY_HEADER)):
    tg_user = verify_telegram_data(auth_header)
    uid = str(tg_user["id"])
    
    if amount < 100 or amount > 50000:
        raise HTTPException(status_code=400, detail="Limit 100-50000")
        
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance, withdraw_time FROM users WHERE user_id=?", (uid,)) as cursor:
            row = await cursor.fetchone()
            
        if not row or row[0] < amount:
            raise HTTPException(status_code=400, detail="Not enough balance")
            
        balance, last_withdraw = row
        if last_withdraw and time.time() - last_withdraw < 86400:
            raise HTTPException(status_code=400, detail="Cooldown 24h")
        
        commission = int(amount * 0.05)
        amount_after_commission = amount - commission
        new_balance = balance - amount
        
        await db.execute("UPDATE users SET balance=?, withdraw_time=? WHERE user_id=?", (new_balance, int(time.time()), uid))
        await db.execute("INSERT INTO withdrawals (user_id, amount, status, time) VALUES (?,?,?,?)",
                         (uid, amount_after_commission, "pending", int(time.time())))
        await db.commit()
        
        return {"status": "pending", "amount": amount, "commission": commission, "amount_after_commission": amount_after_commission, "new_balance": new_balance}

@app.get("/api/multiplayer/recent")
async def get_recent_wins():
    return list(recent_wins)

@app.websocket("/ws/multiplayer")
async def websocket_multiplayer(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    try:
        while True:
            await websocket.send_json({"recent_wins": list(recent_wins)})
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        active_connections.remove(websocket)

async def broadcast_multiplayer():
    for connection in active_connections:
        try:
            await connection.send_json({"recent_wins": list(recent_wins)})
        except:
            pass

async def setup_bot_handlers(application: Application):
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("👋 Открой Mini App!")

    async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.pre_checkout_query.answer(ok=True)

    async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
        payload = json.loads(update.message.successful_payment.invoice_payload)
        uid = payload["uid"]
        coins_to_add = payload["coins"]
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (coins_to_add, uid))
            await db.commit()
        
        await update.message.reply_text(f"✅ Пополнено +{coins_to_add} ⭐")

    async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if str(update.effective_user.id) not in ADMIN_IDS:
            return
        
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT * FROM withdrawals WHERE status='pending'") as cursor:
                rows = await cursor.fetchall()
        
        if not rows:
            await update.message.reply_text("📭 Нет заявок")
            return
        
        for w in rows:
            wid, uid, amount, status, t = w
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✔️ Дать", callback_data=f"ap_{wid}"),
                InlineKeyboardButton("❌ Отказать", callback_data=f"re_{wid}")
            ]])
            await update.message.reply_text(f"💰 Заявка #{wid}\nЮзер: {uid}\nСумма: {amount} ⭐", reply_markup=kb)

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
                msg = f"✅ Выплачено {amount} ⭐"
            else:
                refund = int(amount / 0.95)
                await db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (refund, uid))
                await db.execute("UPDATE withdrawals SET status='rejected' WHERE id=?", (wid,))
                msg = f"❌ Отклонено"
            
            await db.commit()
            await q.edit_message_text(msg)

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
