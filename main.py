# main.py - GiftUpgrader с админ-панелью (исправленные кейсы с окупаемостью)
import os
import hmac
import hashlib
import json
import urllib.parse
import random
import time
import uuid
import asyncio
import math
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from fastapi import FastAPI, Header, HTTPException, Depends, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import aiosqlite
import httpx
import socketio

# ==================== APP INIT ====================
app = FastAPI(title="GiftUpgrader Admin Panel", version="2.0.0")

sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins='*',
    ping_timeout=10,
    ping_interval=5
)
socket_app = socketio.ASGIApp(sio, app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== CONFIG ====================
BOT_TOKEN = "8922972247:AAGbc4tYV51F3zxAGA3SuLcBY7PCyGRbXoE"
ADMIN_TG_ID = 7092015279
DB_NAME = "database.db"
HOUSE_EDGE = 0.05
REFERRAL_PERCENT = 7
MAX_WITHDRAW_AMOUNT = 50000
WITHDRAW_FEE = 0.05

# ==================== NFT NAMES ====================
NFT_NAMES = [
    "Astral Shard", "B-Day Candle", "Berry Box", "Big Year",
    "Bonded Ring", "Bow Tie", "Bunny Muffin", "Candy Cane",
    "Cookie Heart", "Crystal Ball", "Cupid Charm", "Diamond Ring",
    "Durov's Cap", "Electric Skull", "Eternal Rose", "Flying Broom",
    "Genie Lamp", "Ginger Cookie", "Heart Locket", "Heroic Helmet",
    "Hex Pot", "Holiday Drink", "Ion Gem", "Jack-in-the-Box",
    "Jester Hat", "Khabib's Papakha", "Light Sword", "Loot Bag",
    "Love Potion", "Lunar Snake", "Magic Potion", "Mini Oscar",
    "Moon Pendant", "Nail Bracelet", "Neko Helmet", "Onyx Black",
    "Perfume Bottle", "Plush Pepe", "Precious Peach", "Restless Jar",
    "Rocket", "Santa Hat", "Scared Cat", "Signet Ring",
    "Skull Flower", "Snow Globe", "Spiced Wine", "Star Notepad",
    "Swiss Watch", "Top Hat", "Toy Bear", "Trapped Heart",
    "Vintage Cigar", "Voodoo Doll", "Witch Hat", "Xmas Stocking",
    "Happy Brownie", "Sakura Flower", "Easter Egg", "Westside Sign",
    "Fresh Socks", "Low Rider", "Gingerbread House", "Record Player",
    "Desk Calendar", "Spy Agaric", "Holiday Perfume", "Sharp Tongue",
    "Evil Eye", "Faith Amulet", "Diamond Hand", "Mad Pumpkin",
    "Eternal Candle", "Golden Horse", "Hypno Lollipop", "Jolly Chimp",
    "Magic Mouse", "Mighty Arm", "Pretty Posy", "Swag Bag"
]

# ==================== RARITY COLORS ====================
RARITY_COLORS = {
    "Common": {"gradient": "from-gray-600 to-gray-400", "border": "#8B8B8B", "glow": "rgba(139,139,139,0.3)", "color": "#8B8B8B"},
    "Uncommon": {"gradient": "from-green-700 to-green-400", "border": "#4CAF50", "glow": "rgba(76,175,80,0.3)", "color": "#4CAF50"},
    "Rare": {"gradient": "from-blue-700 to-blue-400", "border": "#2196F3", "glow": "rgba(33,150,243,0.3)", "color": "#2196F3"},
    "Epic": {"gradient": "from-purple-700 to-purple-400", "border": "#9C27B0", "glow": "rgba(156,39,176,0.3)", "color": "#9C27B0"},
    "Legendary": {"gradient": "from-amber-700 to-amber-400", "border": "#FFC107", "glow": "rgba(255,193,7,0.3)", "color": "#FFC107"},
    "Mythic": {"gradient": "from-red-700 to-red-400", "border": "#F44336", "glow": "rgba(244,67,54,0.3)", "color": "#F44336"}
}

def get_emoji_for_name(name: str) -> str:
    emoji_map = {
        "Astral Shard": "💫", "B-Day Candle": "🎂", "Berry Box": "🫐", "Big Year": "📅",
        "Bonded Ring": "💍", "Bow Tie": "🎀", "Bunny Muffin": "🐰", "Candy Cane": "🍭",
        "Cookie Heart": "🍪", "Crystal Ball": "🔮", "Cupid Charm": "💘", "Diamond Ring": "💎",
        "Durov's Cap": "🧢", "Electric Skull": "⚡", "Eternal Rose": "🌹", "Flying Broom": "🧹",
        "Genie Lamp": "🪔", "Ginger Cookie": "🍪", "Heart Locket": "❤️", "Heroic Helmet": "⛑️",
        "Hex Pot": "🧪", "Holiday Drink": "🥂", "Ion Gem": "💠", "Jack-in-the-Box": "📦",
        "Jester Hat": "🎭", "Khabib's Papakha": "🧢", "Light Sword": "⚔️", "Loot Bag": "💰",
        "Love Potion": "💗", "Lunar Snake": "🐍", "Magic Potion": "🧙", "Mini Oscar": "🏆",
        "Moon Pendant": "🌙", "Nail Bracelet": "📿", "Neko Helmet": "🐱", "Onyx Black": "🖤",
        "Perfume Bottle": "🧴", "Plush Pepe": "🐸", "Precious Peach": "🍑", "Restless Jar": "🏺",
        "Rocket": "🚀", "Santa Hat": "🎅", "Scared Cat": "😱", "Signet Ring": "💍",
        "Skull Flower": "💀", "Snow Globe": "❄️", "Spiced Wine": "🍷", "Star Notepad": "📒",
        "Swiss Watch": "⌚", "Top Hat": "🎩", "Toy Bear": "🧸", "Trapped Heart": "💔",
        "Vintage Cigar": "🚬", "Voodoo Doll": "🪆", "Witch Hat": "🧙", "Xmas Stocking": "🧦",
        "Happy Brownie": "🍫", "Sakura Flower": "🌸", "Easter Egg": "🥚", "Westside Sign": "🤙",
        "Fresh Socks": "🧦", "Low Rider": "🚗", "Gingerbread House": "🏠", "Record Player": "🎵",
        "Desk Calendar": "📆", "Spy Agaric": "🍄", "Holiday Perfume": "🌸", "Sharp Tongue": "👅",
        "Evil Eye": "🧿", "Faith Amulet": "📿", "Diamond Hand": "💎", "Mad Pumpkin": "🎃",
        "Eternal Candle": "🕯️", "Golden Horse": "🐴", "Hypno Lollipop": "🍭", "Jolly Chimp": "🐵",
        "Magic Mouse": "🐭", "Mighty Arm": "💪", "Pretty Posy": "💐", "Swag Bag": "👜"
    }
    return emoji_map.get(name, "🎁")

# ==================== NFT ПОДАРКИ (8 В КАЖДОЙ РЕДКОСТИ) ====================
def build_nft_gifts():
    gifts = {}
    rarity_counts = {
        "Common": 8,
        "Uncommon": 8,
        "Rare": 8,
        "Epic": 8,
        "Legendary": 8,
        "Mythic": 8
    }
    # Цены увеличены, чтобы кейсы окупались
    value_ranges = {
        "Common": (15, 80),
        "Uncommon": (100, 350),
        "Rare": (400, 900),
        "Epic": (1000, 2500),
        "Legendary": (3000, 8000),
        "Mythic": (10000, 60000)
    }
    name_index = 0
    for rarity, count in rarity_counts.items():
        gifts[rarity] = []
        min_val, max_val = value_ranges[rarity]
        # Генерируем уникальные значения для каждой редкости
        values = []
        for i in range(count):
            if rarity == "Common":
                v = random.randint(min_val, max_val)
                v = round(v / 5) * 5
            elif rarity in ["Uncommon", "Rare"]:
                v = random.randint(min_val, max_val)
                v = round(v / 10) * 10
            elif rarity in ["Epic", "Legendary"]:
                v = random.randint(min_val, max_val)
                v = round(v / 50) * 50
            else:
                v = random.randint(min_val, max_val)
                v = round(v / 100) * 100
            if v < 1:
                v = 1
            values.append(v)
        # Сортируем по возрастанию
        values.sort()
        for i in range(count):
            if name_index >= len(NFT_NAMES):
                name_index = 0
            name = NFT_NAMES[name_index]
            name_index += 1
            gifts[rarity].append({
                "id": name.lower().replace(" ", "_").replace("'", ""),
                "name": name,
                "value": values[i],
                "emoji": get_emoji_for_name(name)
            })
    return gifts

NFT_GIFTS = build_nft_gifts()

# ==================== КЕЙСЫ (ВСЁ ЗА ЗВЁЗДЫ, С ОКУПАЕМОСТЬЮ) ====================
CASES = {
    "free_daily": {
        "name": "🎁 FREE DAILY CASE",
        "price": 0,
        "cooldown": 86400,
        "rarities": ["Common"],
        "weights": [100],
        "min_stars": 0.5,
        "max_stars": 15,
        "description": "Бесплатно каждый день"
    },
    "tg_starter": {
        "name": "🚀 TG STARTER CASE",
        "price": 50,
        "rarities": ["Common", "Uncommon"],
        "weights": [50, 50],
        "min_stars": 10,
        "max_stars": 80,
        "description": "Отличный старт"
    },
    "pepe_memes": {
        "name": "🐸 PEPE & MEMES CASE",
        "price": 200,
        "rarities": ["Uncommon", "Rare"],
        "weights": [45, 55],
        "min_stars": 50,
        "max_stars": 400,
        "description": "Мемы и легенды"
    },
    "telegram_gifts": {
        "name": "🎁 TELEGRAM GIFTS CASE",
        "price": 500,
        "rarities": ["Rare", "Epic"],
        "weights": [40, 60],
        "min_stars": 100,
        "max_stars": 1200,
        "description": "Эксклюзивные подарки"
    },
    "fragment_nft": {
        "name": "💎 FRAGMENT NFT CASE",
        "price": 1500,
        "rarities": ["Epic", "Legendary"],
        "weights": [45, 55],
        "min_stars": 300,
        "max_stars": 4000,
        "description": "NFT от Fragment"
    },
    "durov_selection": {
        "name": "👑 DUROV'S SELECTION",
        "price": 5000,
        "rarities": ["Legendary", "Mythic"],
        "weights": [50, 50],
        "min_stars": 1000,
        "max_stars": 30000,
        "description": "Топовая коллекция"
    }
}

# ==================== GIFT UPGRADER MATH ====================
def calculate_upgrade_chance(input_value: int, target_value: int) -> float:
    base_chance = (input_value / target_value) * 100
    final_chance = base_chance * (1 - HOUSE_EDGE)
    if final_chance > 60:
        final_chance = 60
    if final_chance < 1:
        final_chance = 1
    return round(final_chance, 1)

# ==================== MINES MATH ====================
MINES_GRID_SIZE = 5

def calculate_mines_multiplier(mines: int, opened: int) -> float:
    total = MINES_GRID_SIZE * MINES_GRID_SIZE
    safe = total - mines
    if opened >= safe:
        return round((1 - HOUSE_EDGE) * 100, 2)
    prob = 1.0
    for i in range(opened):
        prob *= (safe - i) / (total - i)
    multiplier = (1 - HOUSE_EDGE) / prob
    caps = {1: 5, 3: 15, 5: 40, 10: 150, 15: 500, 20: 1500, 24: 3000}
    cap_key = min(caps.keys(), key=lambda k: abs(k - mines))
    return round(min(multiplier, caps[cap_key]), 2)

# ==================== CRASH MATH ====================
CRASH_MIN_BET = 25
CRASH_MAX_BET = 5000
CRASH_BETTING_TIME = 6
CRASH_COOLDOWN = 3
CRASH_SPEED = 0.08
SERVER_SEED = os.getenv("CRASH_SERVER_SEED", str(uuid.uuid4()))
SERVER_SEED_HASH = hashlib.sha256(SERVER_SEED.encode()).hexdigest()
crash_nonce = 0

def generate_crash_point() -> tuple:
    global crash_nonce, SERVER_SEED, SERVER_SEED_HASH
    crash_nonce += 1
    message = f"{SERVER_SEED}:{crash_nonce}"
    hash_hex = hashlib.sha256(message.encode()).hexdigest()
    h = int(hash_hex[:16], 16)
    r = h / (2**64)
    if r < 0.30:
        cp = 1.01 + (r / 0.30) * 0.09
    elif r < 0.60:
        cp = 1.10 + ((r - 0.30) / 0.30) * 0.20
    elif r < 0.82:
        cp = 1.30 + ((r - 0.60) / 0.22) * 0.50
    elif r < 0.94:
        cp = 1.80 + ((r - 0.82) / 0.12) * 1.20
    elif r < 0.98:
        cp = 3.00 + ((r - 0.94) / 0.04) * 5.00
    elif r < 0.995:
        cp = 8.00 + ((r - 0.98) / 0.015) * 12.00
    else:
        cp = 20.00 + ((r - 0.995) / 0.005) * 30.00
    return round(min(cp, 50.0), 2), hash_hex

# ==================== MODELS ====================
class UpgradeRequest(BaseModel):
    item_index: int
    target_value: int

class CaseOpenRequest(BaseModel):
    case_id: str

class SellItemRequest(BaseModel):
    item_index: int

class MinesStartRequest(BaseModel):
    bet: int
    mines: int

class MinesOpenRequest(BaseModel):
    game_id: str
    cell: int

class MinesCashoutRequest(BaseModel):
    game_id: str

class AdminGiveRequest(BaseModel):
    user_id: int
    amount: int

class WithdrawRequest(BaseModel):
    amount: int
    wallet: str

class PromoCreateRequest(BaseModel):
    code: str
    reward_type: str
    case_id: str = None
    stars: int = 0
    max_uses: int = 1

class AdminWithdrawStatusRequest(BaseModel):
    withdraw_id: int
    status: str

# ==================== DATABASE ====================
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                username TEXT DEFAULT 'Player',
                balance INTEGER DEFAULT 50,
                total_spent INTEGER DEFAULT 0,
                inventory TEXT DEFAULT '[]',
                games_played INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER,
                amount INTEGER,
                wallet TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                user_id INTEGER PRIMARY KEY,
                referrer_id INTEGER NOT NULL,
                total_earned INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS referral_earnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER NOT NULL,
                referral_id INTEGER NOT NULL,
                deposit_amount INTEGER NOT NULL,
                earned INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS promocodes (
                code TEXT PRIMARY KEY,
                reward_type TEXT,
                case_id TEXT,
                stars INTEGER DEFAULT 0,
                max_uses INTEGER DEFAULT 1,
                uses INTEGER DEFAULT 0,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS promo_uses (
                user_id INTEGER,
                promo_code TEXT,
                used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, promo_code)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS free_case_cooldowns (
                user_id INTEGER PRIMARY KEY,
                last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS admin_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                action TEXT,
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

async def get_user(tg_id: int) -> dict:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT balance, total_spent, inventory, games_played, wins FROM users WHERE tg_id = ?",
            (tg_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "balance": row[0],
                    "total_spent": row[1],
                    "inventory": json.loads(row[2]),
                    "games_played": row[3],
                    "wins": row[4]
                }
            else:
                await db.execute(
                    "INSERT INTO users (tg_id, balance, inventory) VALUES (?, 50, '[]')",
                    (tg_id,)
                )
                await db.commit()
                return {"balance": 50, "total_spent": 0, "inventory": [], "games_played": 0, "wins": 0}

async def log_admin_action(admin_id: int, action: str, details: str = ""):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO admin_logs (admin_id, action, details) VALUES (?, ?, ?)",
            (admin_id, action, details)
        )
        await db.commit()

# ==================== AUTH ====================
def verify_telegram(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization")
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN not configured")
    
    try:
        data = urllib.parse.parse_qs(authorization)
        hash_val = data.get('hash', [None])[0]
        if not hash_val:
            raise HTTPException(status_code=401, detail="Invalid data")
        
        sorted_data = sorted([f"{k}={v[0]}" for k, v in data.items() if k != 'hash'])
        check_string = "\n".join(sorted_data)
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc_hash = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        
        if calc_hash != hash_val:
            raise HTTPException(status_code=401, detail="Validation failed")
        
        return json.loads(data.get('user', ['{}'])[0])
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Auth error: {str(e)}")

def verify_admin(user: dict = Depends(verify_telegram)):
    if user['id'] != ADMIN_TG_ID:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

# ==================== ФРОНТЕНД ====================
FRONTEND_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
    <title>GiftUpgrader</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0D121D;
            color: #E8E8E8;
            min-height: 100vh;
            overflow-x: hidden;
            padding-bottom: 80px;
        }
        ::-webkit-scrollbar { width: 0; background: transparent; }
        .app-container { max-width: 480px; margin: 0 auto; padding: 16px; }
        .glass {
            background: rgba(22, 31, 46, 0.85);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 20px;
            padding: 20px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.4);
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 12px 0 20px;
        }
        .logo {
            font-size: 22px;
            font-weight: 800;
            background: linear-gradient(135deg, #FFC107, #FF6B00);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .balance-badge {
            background: rgba(255,193,7,0.15);
            border: 1px solid rgba(255,193,7,0.25);
            padding: 6px 16px;
            border-radius: 20px;
            font-size: 15px;
            font-weight: 600;
            color: #FFC107;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .tabs {
            display: flex;
            gap: 6px;
            background: rgba(13, 18, 29, 0.6);
            border-radius: 16px;
            padding: 4px;
            margin-bottom: 20px;
            overflow-x: auto;
            flex-wrap: nowrap;
        }
        .tab-btn {
            flex: 1;
            min-width: 60px;
            padding: 10px 6px;
            border: none;
            background: transparent;
            color: #8899AA;
            font-size: 12px;
            font-weight: 600;
            border-radius: 12px;
            cursor: pointer;
            transition: all 0.3s;
            white-space: nowrap;
            text-align: center;
        }
        .tab-btn.active {
            background: linear-gradient(135deg, #FFC107, #FF6B00);
            color: #0D121D;
            box-shadow: 0 4px 16px rgba(255,193,7,0.25);
        }
        .tab-btn:active { transform: scale(0.95); }
        .tab-content { display: none; animation: fadeUp 0.4s ease; }
        .tab-content.active { display: block; }
        @keyframes fadeUp { from { opacity:0; transform:translateY(12px); } to { opacity:1; transform:translateY(0); } }

        .upgrader-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
            margin-bottom: 20px;
        }
        .gift-card {
            background: rgba(13, 18, 29, 0.6);
            border-radius: 16px;
            padding: 16px;
            text-align: center;
            border: 1px solid rgba(255,255,255,0.05);
            transition: all 0.3s;
        }
        .gift-card.selected { border-color: #FFC107; box-shadow: 0 0 24px rgba(255,193,7,0.1); }
        .gift-card .emoji { font-size: 48px; display: block; margin-bottom: 6px; }
        .gift-card .name { font-size: 13px; font-weight: 600; color: #E8E8E8; }
        .gift-card .value { font-size: 12px; color: #8899AA; margin-top: 2px; }
        .gift-card .rarity-badge {
            display: inline-block;
            font-size: 10px;
            padding: 2px 10px;
            border-radius: 10px;
            margin-top: 4px;
            font-weight: 600;
            letter-spacing: 0.3px;
        }
        .arrow-icon { font-size: 28px; text-align: center; color: #FFC107; align-self: center; }

        .wheel-wrapper {
            position: relative;
            width: 100%;
            max-width: 340px;
            margin: 0 auto 16px;
            aspect-ratio: 1/1;
        }
        .wheel {
            width: 100%;
            height: 100%;
            border-radius: 50%;
            position: relative;
            transition: transform 4s cubic-bezier(0.15, 0.90, 0.25, 1.00);
            box-shadow: 0 0 40px rgba(255,193,7,0.05), inset 0 0 60px rgba(0,0,0,0.3);
        }
        .wheel canvas { width: 100%; height: 100%; border-radius: 50%; display: block; }
        .wheel-pointer {
            position: absolute;
            top: -12px;
            left: 50%;
            transform: translateX(-50%);
            width: 0;
            height: 0;
            border-left: 16px solid transparent;
            border-right: 16px solid transparent;
            border-top: 28px solid #FFC107;
            filter: drop-shadow(0 4px 12px rgba(255,193,7,0.5));
            z-index: 10;
        }
        .wheel-center {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            width: 56px;
            height: 56px;
            border-radius: 50%;
            background: radial-gradient(circle, #1A2A3F, #0D121D);
            border: 2px solid rgba(255,193,7,0.3);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 14px;
            font-weight: 700;
            color: #FFC107;
            z-index: 5;
            box-shadow: 0 0 30px rgba(255,193,7,0.1);
        }
        .wheel-glow {
            position: absolute;
            inset: -8px;
            border-radius: 50%;
            pointer-events: none;
            transition: all 0.6s;
            opacity: 0;
        }
        .wheel-glow.success { opacity: 1; box-shadow: 0 0 60px rgba(76,175,80,0.4), inset 0 0 60px rgba(76,175,80,0.1); }
        .wheel-glow.fail { opacity: 1; box-shadow: 0 0 60px rgba(244,67,54,0.4), inset 0 0 60px rgba(244,67,54,0.1); }

        .upgrade-info {
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 24px;
            padding: 12px 0;
        }
        .upgrade-info .stat { text-align: center; }
        .upgrade-info .stat .label { font-size: 11px; color: #8899AA; text-transform: uppercase; letter-spacing: 0.5px; }
        .upgrade-info .stat .value { font-size: 22px; font-weight: 700; }
        .upgrade-info .stat .value.green { color: #4CAF50; }
        .upgrade-info .stat .value.gold { color: #FFC107; }

        .btn-upgrade {
            width: 100%;
            padding: 16px;
            border: none;
            border-radius: 16px;
            font-size: 18px;
            font-weight: 700;
            background: linear-gradient(135deg, #FFC107, #FF6B00);
            color: #0D121D;
            cursor: pointer;
            transition: all 0.3s;
            box-shadow: 0 4px 24px rgba(255,193,7,0.2);
        }
        .btn-upgrade:active { transform: scale(0.97); }
        .btn-upgrade:disabled { opacity: 0.5; transform: scale(0.98); cursor: not-allowed; box-shadow: none; }
        .btn-upgrade .spinner { display: none; width: 20px; height: 20px; border: 2px solid rgba(13,18,29,0.2); border-top-color: #0D121D; border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto; }
        .btn-upgrade.loading .spinner { display: block; }
        .btn-upgrade.loading .label { display: none; }
        @keyframes spin { to { transform: rotate(360deg); } }

        .cases-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
        }
        .case-card {
            background: rgba(13,18,29,0.6);
            border-radius: 16px;
            padding: 16px;
            text-align: center;
            border: 1px solid rgba(255,255,255,0.05);
            cursor: pointer;
            transition: all 0.3s;
        }
        .case-card:active { transform: scale(0.96); }
        .case-card .price { font-size: 14px; font-weight: 600; color: #FFC107; }
        .case-card .name { font-size: 13px; font-weight: 600; margin: 4px 0; }
        .case-card .rarities { font-size: 11px; color: #8899AA; }
        .case-card .desc { font-size: 10px; color: #667788; margin-top: 4px; }
        .case-card.free { border-color: rgba(76,175,80,0.3); }
        .case-card .cooldown { font-size: 11px; color: #F44336; }
        .case-card .stars-range { font-size: 11px; color: #FFC107; margin-top: 2px; }

        .inv-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 10px;
        }
        .inv-item {
            background: rgba(13,18,29,0.6);
            border-radius: 14px;
            padding: 12px;
            text-align: center;
            border: 1px solid rgba(255,255,255,0.05);
            cursor: pointer;
            transition: all 0.3s;
        }
        .inv-item:active { transform: scale(0.95); }
        .inv-item .emoji { font-size: 32px; }
        .inv-item .name { font-size: 11px; font-weight: 500; margin-top: 4px; }
        .inv-item .val { font-size: 10px; color: #8899AA; }
        .inv-item .sell-btn {
            margin-top: 6px;
            padding: 4px 12px;
            border: none;
            border-radius: 8px;
            background: rgba(244,67,54,0.2);
            color: #F44336;
            font-size: 11px;
            font-weight: 600;
            cursor: pointer;
        }
        .inv-item.selected { border-color: #FFC107; box-shadow: 0 0 20px rgba(255,193,7,0.1); }

        .mines-grid {
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 6px;
            max-width: 340px;
            margin: 12px auto;
        }
        .mine-cell {
            aspect-ratio: 1/1;
            background: rgba(22,31,46,0.8);
            border-radius: 10px;
            border: 1px solid rgba(255,255,255,0.06);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 20px;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.3s;
            color: #8899AA;
        }
        .mine-cell:active { transform: scale(0.92); }
        .mine-cell.opened { background: rgba(76,175,80,0.15); border-color: rgba(76,175,80,0.2); }
        .mine-cell.bomb { background: rgba(244,67,54,0.2); border-color: rgba(244,67,54,0.3); color: #F44336; }
        .mine-cell .gem { color: #FFC107; }

        .crash-graph {
            background: rgba(13,18,29,0.6);
            border-radius: 16px;
            padding: 16px;
            height: 160px;
            position: relative;
            overflow: hidden;
            margin-bottom: 12px;
        }
        .crash-graph canvas { width: 100%; height: 100%; }
        .crash-bet-row { display: flex; gap: 10px; align-items: center; }
        .crash-bet-row input {
            flex: 1;
            padding: 12px 16px;
            background: rgba(13,18,29,0.6);
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 12px;
            color: #E8E8E8;
            font-size: 16px;
        }
        .crash-bet-row input:focus { outline: none; border-color: #FFC107; }
        .crash-bet-row .btn {
            padding: 12px 20px;
            border: none;
            border-radius: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s;
        }
        .btn-crash-bet { background: linear-gradient(135deg, #4CAF50, #2E7D32); color: white; }
        .btn-crash-cashout { background: linear-gradient(135deg, #FFC107, #FF6B00); color: #0D121D; }
        .btn:active { transform: scale(0.95); }
        .crash-multiplier { font-size: 42px; font-weight: 800; text-align: center; padding: 8px 0; color: #FFC107; }
        .crash-status { text-align: center; font-size: 14px; color: #8899AA; padding: 4px 0; }

        .promo-row { display: flex; gap: 10px; margin-top: 12px; }
        .promo-row input {
            flex: 1;
            padding: 12px 16px;
            background: rgba(13,18,29,0.6);
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 12px;
            color: #E8E8E8;
            font-size: 14px;
            text-transform: uppercase;
        }
        .promo-row input:focus { outline: none; border-color: #FFC107; }
        .promo-row .btn {
            padding: 12px 20px;
            border: none;
            border-radius: 12px;
            background: linear-gradient(135deg, #FFC107, #FF6B00);
            color: #0D121D;
            font-weight: 600;
            cursor: pointer;
        }

        .ref-card { text-align: center; padding: 20px; }
        .ref-card .big { font-size: 48px; font-weight: 800; color: #FFC107; }
        .ref-card .link {
            background: rgba(13,18,29,0.6);
            padding: 10px 16px;
            border-radius: 12px;
            margin: 12px 0;
            font-size: 14px;
            word-break: break-all;
            border: 1px solid rgba(255,255,255,0.05);
        }

        .toast-container {
            position: fixed;
            bottom: 20px;
            left: 50%;
            transform: translateX(-50%);
            z-index: 999;
            width: 90%;
            max-width: 400px;
        }
        .toast {
            padding: 14px 20px;
            border-radius: 14px;
            background: rgba(22,31,46,0.95);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255,255,255,0.08);
            text-align: center;
            font-weight: 500;
            font-size: 14px;
            animation: toastIn 0.4s ease;
            box-shadow: 0 8px 32px rgba(0,0,0,0.5);
        }
        .toast.success { border-color: #4CAF50; color: #4CAF50; }
        .toast.error { border-color: #F44336; color: #F44336; }
        .toast.info { border-color: #FFC107; color: #FFC107; }
        @keyframes toastIn { from { opacity:0; transform:translateY(20px); } to { opacity:1; transform:translateY(0); } }

        @media (max-width: 420px) {
            .upgrader-grid { gap: 8px; }
            .gift-card .emoji { font-size: 36px; }
            .wheel-wrapper { max-width: 280px; }
            .cases-grid { grid-template-columns: 1fr 1fr; }
            .inv-grid { grid-template-columns: repeat(3, 1fr); }
        }
    </style>
</head>
<body>

<div class="app-container" id="app">
    <div class="header">
        <div class="logo">🎮 GiftUpgrader</div>
        <div class="balance-badge" id="balanceDisplay">⭐ 0</div>
    </div>

    <div class="tabs" id="tabs">
        <button class="tab-btn active" data-tab="upgrader">⬆️ Апгрейд</button>
        <button class="tab-btn" data-tab="cases">🎁 Кейсы</button>
        <button class="tab-btn" data-tab="mines">💣 Мины</button>
        <button class="tab-btn" data-tab="crash">🚀 Ракетка</button>
        <button class="tab-btn" data-tab="inventory">🎒 Инвентарь</button>
        <button class="tab-btn" data-tab="profile">👤 Профиль</button>
    </div>

    <!-- UPGRADER -->
    <div class="tab-content active" id="tab-upgrader">
        <div class="glass glow-border" style="padding:16px;">
            <div class="upgrader-grid">
                <div class="gift-card selected" id="inputGift">
                    <span class="emoji" id="inputEmoji">🧸</span>
                    <div class="name" id="inputName">Plush Bear</div>
                    <div class="value" id="inputValue">⭐ 15</div>
                    <span class="rarity-badge" id="inputRarity" style="background:rgba(139,139,139,0.2);color:#8B8B8B;">Common</span>
                </div>
                <div class="arrow-icon">➡️</div>
                <div class="gift-card" id="targetGift">
                    <span class="emoji" id="targetEmoji">💎</span>
                    <div class="name" id="targetName">Telegram Premium</div>
                    <div class="value" id="targetValue">⭐ 80</div>
                    <span class="rarity-badge" id="targetRarity" style="background:rgba(76,175,80,0.2);color:#4CAF50;">Uncommon</span>
                </div>
            </div>

            <div class="wheel-wrapper">
                <div class="wheel-glow" id="wheelGlow"></div>
                <div class="wheel" id="wheel">
                    <canvas id="wheelCanvas" width="400" height="400"></canvas>
                </div>
                <div class="wheel-pointer"></div>
                <div class="wheel-center" id="wheelCenter">?</div>
            </div>

            <div class="upgrade-info">
                <div class="stat"><div class="label">Шанс</div><div class="value gold" id="chanceDisplay">35.0%</div></div>
                <div class="stat"><div class="label">Множитель</div><div class="value green" id="multiplierDisplay">2.5x</div></div>
            </div>

            <button class="btn-upgrade" id="upgradeBtn">
                <span class="label">⬆️ АПГРЕЙД</span>
                <div class="spinner"></div>
            </button>
        </div>
    </div>

    <!-- CASES -->
    <div class="tab-content" id="tab-cases">
        <div class="glass glow-border">
            <div class="cases-grid" id="casesGrid"></div>
        </div>
    </div>

    <!-- MINES -->
    <div class="tab-content" id="tab-mines">
        <div class="glass glow-border">
            <div style="display:flex; gap:10px; flex-wrap:wrap; margin-bottom:12px;">
                <input type="number" id="minesBet" placeholder="Ставка" value="10" style="flex:1; padding:10px 14px; background:rgba(13,18,29,0.6); border:1px solid rgba(255,255,255,0.06); border-radius:12px; color:#E8E8E8; font-size:14px;">
                <input type="number" id="minesCount" placeholder="Мин" value="3" min="1" max="24" style="flex:1; padding:10px 14px; background:rgba(13,18,29,0.6); border:1px solid rgba(255,255,255,0.06); border-radius:12px; color:#E8E8E8; font-size:14px;">
                <button class="btn" id="minesStartBtn" style="padding:10px 20px; background:linear-gradient(135deg,#4CAF50,#2E7D32); color:white; border:none; border-radius:12px; font-weight:600; cursor:pointer;">Старт</button>
            </div>
            <div class="mines-grid" id="minesGrid"></div>
            <div style="display:flex; justify-content:space-between; padding:8px 0; font-size:14px;">
                <span>Множитель: <strong id="minesMultiplier">1.00x</strong></span>
                <span>Открыто: <strong id="minesOpened">0</strong></span>
                <button class="btn" id="minesCashoutBtn" style="padding:8px 16px; background:linear-gradient(135deg,#FFC107,#FF6B00); color:#0D121D; border:none; border-radius:10px; font-weight:600; cursor:pointer; display:none;">Забрать</button>
            </div>
        </div>
    </div>

    <!-- CRASH -->
    <div class="tab-content" id="tab-crash">
        <div class="glass glow-border">
            <div class="crash-graph"><canvas id="crashCanvas"></canvas></div>
            <div class="crash-multiplier" id="crashMultiplier">1.00x</div>
            <div class="crash-status" id="crashStatus">Ожидание ставок...</div>
            <div class="crash-bet-row">
                <input type="number" id="crashBetInput" placeholder="Ставка" value="25" min="25" max="5000">
                <button class="btn btn-crash-bet" id="crashBetBtn">Ставка</button>
                <button class="btn btn-crash-cashout" id="crashCashoutBtn" style="display:none;">Забрать</button>
            </div>
            <div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:8px;" id="crashBetsList"></div>
        </div>
    </div>

    <!-- INVENTORY -->
    <div class="tab-content" id="tab-inventory">
        <div class="glass glow-border">
            <div class="inv-grid" id="invGrid"></div>
            <div style="margin-top:12px; text-align:center; color:#8899AA; font-size:13px;" id="invEmpty">Инвентарь пуст</div>
        </div>
    </div>

    <!-- PROFILE -->
    <div class="tab-content" id="tab-profile">
        <div class="glass glow-border">
            <div style="text-align:center; padding:12px 0;">
                <div style="font-size:48px;">👤</div>
                <div style="font-size:20px; font-weight:600;" id="profileName">Player</div>
                <div style="font-size:14px; color:#8899AA;" id="profileId">ID: 0</div>
                <div style="margin:12px 0; display:flex; justify-content:center; gap:24px;">
                    <div><div style="font-size:12px; color:#8899AA;">Баланс</div><div style="font-size:20px; font-weight:700; color:#FFC107;" id="profileBalance">0</div></div>
                    <div><div style="font-size:12px; color:#8899AA;">Игр</div><div style="font-size:20px; font-weight:700;" id="profileGames">0</div></div>
                    <div><div style="font-size:12px; color:#8899AA;">Побед</div><div style="font-size:20px; font-weight:700; color:#4CAF50;" id="profileWins">0</div></div>
                </div>
            </div>
            <div class="ref-card">
                <div class="big">🎯</div>
                <div style="font-weight:600; font-size:16px;">Реферальная программа</div>
                <div style="font-size:13px; color:#8899AA; margin:4px 0;">Получай 7% от депозитов друзей</div>
                <div style="display:flex; justify-content:center; gap:24px; margin:12px 0;">
                    <div><div style="font-size:12px; color:#8899AA;">Приглашено</div><div style="font-size:18px; font-weight:700;" id="refCount">0</div></div>
                    <div><div style="font-size:12px; color:#8899AA;">Заработано</div><div style="font-size:18px; font-weight:700; color:#FFC107;" id="refEarned">0</div></div>
                </div>
                <div class="link" id="refLink">Загрузка...</div>
                <button class="btn" id="copyRefBtn" style="padding:8px 24px; background:rgba(255,193,7,0.15); color:#FFC107; border:1px solid rgba(255,193,7,0.2); border-radius:12px; cursor:pointer;">📋 Копировать ссылку</button>
            </div>
            <div style="margin-top:12px;">
                <div class="promo-row">
                    <input type="text" id="promoInput" placeholder="Введите промокод">
                    <button class="btn" id="promoActivateBtn">Активировать</button>
                </div>
            </div>
            <div style="margin-top:12px;">
                <button class="btn" id="withdrawBtn" style="width:100%; padding:12px; background:rgba(244,67,54,0.15); color:#F44336; border:1px solid rgba(244,67,54,0.2); border-radius:12px; font-weight:600; cursor:pointer;">💳 Вывести звёзды</button>
            </div>
        </div>
    </div>
</div>

<div class="toast-container" id="toastContainer"></div>

<script>
// ============================================================
// 1. ГЛОБАЛЬНОЕ СОСТОЯНИЕ
// ============================================================
const STATE = {
    tgId: 0,
    username: 'Player',
    balance: 50,
    inventory: [],
    gamesPlayed: 0,
    wins: 0,
    isAdmin: false,
    selectedItemIndex: 0,
    targetValue: 80,
    isUpgrading: false,
    minesGameId: null,
    minesOpened: 0,
    minesMultiplier: 1,
    minesBombPositions: [],
    crashConnected: false,
    crashSocket: null,
    crashBetPlaced: false,
    casesData: {},
    freeCaseAvailable: true,
};

// ============================================================
// 2. TELEGRAM WEBAPP
// ============================================================
function initTelegram() {
    if (window.Telegram && window.Telegram.WebApp) {
        const tg = window.Telegram.WebApp;
        tg.expand();
        tg.enableClosingConfirmation();
        const user = tg.initDataUnsafe?.user;
        if (user) {
            STATE.tgId = user.id;
            STATE.username = user.first_name || 'Player';
            document.getElementById('profileName').textContent = STATE.username;
            document.getElementById('profileId').textContent = 'ID: ' + STATE.tgId;
        }
        window.tgHaptic = {
            impact: (style = 'medium') => { try { tg.HapticFeedback.impactOccurred(style); } catch(e) {} },
            notify: (type = 'success') => { try { tg.HapticFeedback.notificationOccurred(type); } catch(e) {} }
        };
    } else {
        window.tgHaptic = { impact: () => {}, notify: () => {} };
    }
}

// ============================================================
// 3. API
// ============================================================
async function apiCall(method, url, body = null) {
    const headers = { 'Content-Type': 'application/json' };
    if (window.Telegram?.WebApp?.initData) {
        headers['Authorization'] = window.Telegram.WebApp.initData;
    }
    const options = { method, headers };
    if (body) options.body = JSON.stringify(body);
    const res = await fetch(url, options);
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'API Error');
    }
    return res.json();
}

function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = 'toast ' + type;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 3500);
}

// ============================================================
// 4. ПРОФИЛЬ
// ============================================================
async function loadProfile() {
    try {
        const data = await apiCall('GET', '/api/profile');
        STATE.balance = data.balance || 0;
        STATE.inventory = data.inventory || [];
        STATE.gamesPlayed = data.games_played || 0;
        STATE.wins = data.wins || 0;
        STATE.isAdmin = data.is_admin || false;
        STATE.freeCaseAvailable = data.free_case_available !== false;
        updateBalance();
        renderInventory();
        document.getElementById('profileBalance').textContent = STATE.balance;
        document.getElementById('profileGames').textContent = STATE.gamesPlayed;
        document.getElementById('profileWins').textContent = STATE.wins;
        loadRefStats();
        loadCases();
        loadGifts();
        updateUpgraderUI();
    } catch (e) {
        console.error('Load profile error:', e);
        showToast('Ошибка загрузки профиля', 'error');
    }
}

function updateBalance() {
    document.getElementById('balanceDisplay').textContent = '⭐ ' + STATE.balance;
    document.getElementById('profileBalance').textContent = STATE.balance;
}

// ============================================================
// 5. CASES
// ============================================================
async function loadCases() {
    try {
        const data = await apiCall('GET', '/api/cases');
        STATE.casesData = data;
        renderCases();
    } catch (e) {
        console.error('Load cases error:', e);
    }
}

function renderCases() {
    const grid = document.getElementById('casesGrid');
    grid.innerHTML = '';
    for (const [id, c] of Object.entries(STATE.casesData)) {
        const card = document.createElement('div');
        card.className = 'case-card' + (id === 'free_daily' ? ' free' : '');
        const priceText = c.price === 0 ? '🎁 БЕСПЛАТНО' : '⭐ ' + c.price;
        const rarities = c.rarities.join(' • ');
        const starsRange = c.min_stars ? '⭐ ' + c.min_stars + ' - ' + c.max_stars : '';
        card.innerHTML = `
            <div style="font-size:28px;">🎁</div>
            <div class="name">${c.name}</div>
            <div class="price">${priceText}</div>
            <div class="rarities">${rarities}</div>
            <div class="stars-range">${starsRange}</div>
            <div class="desc">${c.description || ''}</div>
            ${id === 'free_daily' ? (STATE.freeCaseAvailable ? '<div class="cooldown" style="color:#4CAF50;">✅ Доступен</div>' : '<div class="cooldown">⏳ 24ч</div>') : ''}
        `;
        card.addEventListener('click', () => openCase(id));
        grid.appendChild(card);
    }
}

async function openCase(caseId) {
    try {
        const data = await apiCall('POST', '/api/case/open', { case_id: caseId });
        if (data.success) {
            STATE.balance = data.balance || STATE.balance;
            updateBalance();
            const profile = await apiCall('GET', '/api/profile');
            STATE.inventory = profile.inventory || [];
            renderInventory();
            updateUpgraderUI();
            const starsEarned = data.stars_earned || 0;
            if (starsEarned > 0) {
                showToast('⭐ Получено ' + starsEarned + ' звёзд!', 'success');
            } else if (data.gift) {
                showToast('🎁 Получен: ' + data.gift.name + ' (' + data.rarity + ')', 'success');
            }
            window.tgHaptic?.notify('success');
            if (caseId === 'free_daily') {
                STATE.freeCaseAvailable = false;
                renderCases();
            }
        }
    } catch (e) {
        showToast('Ошибка: ' + e.message, 'error');
    }
}

// ============================================================
// 6. UPGRADER
// ============================================================
let giftsData = null;

async function loadGifts() {
    try {
        const data = await apiCall('GET', '/api/gifts');
        giftsData = data;
        const uncommon = data.gifts.Uncommon;
        if (uncommon && uncommon.length > 0) {
            STATE.targetValue = uncommon[0].value;
        }
        updateUpgraderUI();
    } catch (e) {
        console.error('Load gifts error:', e);
    }
}

function updateUpgraderUI() {
    const inv = STATE.inventory;
    if (inv.length === 0) {
        document.getElementById('inputEmoji').textContent = '❌';
        document.getElementById('inputName').textContent = 'Нет предметов';
        document.getElementById('inputValue').textContent = '⭐ 0';
        document.getElementById('inputRarity').textContent = '—';
        document.getElementById('inputRarity').style.background = 'rgba(255,255,255,0.05)';
        document.getElementById('inputRarity').style.color = '#8899AA';
        document.getElementById('chanceDisplay').textContent = '0%';
        document.getElementById('multiplierDisplay').textContent = '0x';
        return;
    }
    const idx = Math.min(STATE.selectedItemIndex, inv.length - 1);
    const item = inv[idx];
    document.getElementById('inputEmoji').textContent = item.emoji || '🎁';
    document.getElementById('inputName').textContent = item.name || 'Item';
    document.getElementById('inputValue').textContent = '⭐ ' + (item.value || 0);
    const rarity = item.rarity || 'Common';
    const colors = {
        'Common': { bg: 'rgba(139,139,139,0.2)', color: '#8B8B8B' },
        'Uncommon': { bg: 'rgba(76,175,80,0.2)', color: '#4CAF50' },
        'Rare': { bg: 'rgba(33,150,243,0.2)', color: '#2196F3' },
        'Epic': { bg: 'rgba(156,39,176,0.2)', color: '#9C27B0' },
        'Legendary': { bg: 'rgba(255,193,7,0.2)', color: '#FFC107' },
        'Mythic': { bg: 'rgba(244,67,54,0.2)', color: '#F44336' }
    };
    const c = colors[rarity] || colors['Common'];
    document.getElementById('inputRarity').textContent = rarity;
    document.getElementById('inputRarity').style.background = c.bg;
    document.getElementById('inputRarity').style.color = c.color;

    let targetGift = null;
    if (giftsData) {
        for (const [r, list] of Object.entries(giftsData.gifts)) {
            for (const g of list) {
                if (g.value === STATE.targetValue) {
                    targetGift = { ...g, rarity: r };
                    break;
                }
            }
            if (targetGift) break;
        }
    }
    if (!targetGift) {
        const uncommon = giftsData?.gifts?.Uncommon?.[0];
        if (uncommon) {
            targetGift = { ...uncommon, rarity: 'Uncommon' };
            STATE.targetValue = uncommon.value;
        }
    }
    if (targetGift) {
        document.getElementById('targetEmoji').textContent = targetGift.emoji || '🎁';
        document.getElementById('targetName').textContent = targetGift.name || 'Target';
        document.getElementById('targetValue').textContent = '⭐ ' + targetGift.value;
        const tc = colors[targetGift.rarity] || colors['Common'];
        document.getElementById('targetRarity').textContent = targetGift.rarity;
        document.getElementById('targetRarity').style.background = tc.bg;
        document.getElementById('targetRarity').style.color = tc.color;
    }

    const inputVal = item.value || 1;
    const targetVal = targetGift?.value || 1;
    const chance = Math.min((inputVal / targetVal) * 100 * 0.95, 60);
    document.getElementById('chanceDisplay').textContent = chance.toFixed(1) + '%';
    const mult = (targetVal / inputVal) * 0.95;
    document.getElementById('multiplierDisplay').textContent = mult.toFixed(2) + 'x';
    drawWheel(chance);
}

function drawWheel(chancePercent) {
    const canvas = document.getElementById('wheelCanvas');
    const ctx = canvas.getContext('2d');
    const w = canvas.width, h = canvas.height;
    const cx = w/2, cy = h/2, r = w/2 - 4;
    ctx.clearRect(0, 0, w, h);
    const winAngle = (chancePercent / 100) * 2 * Math.PI;
    const loseAngle = 2 * Math.PI - winAngle;
    let start = -Math.PI/2;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, r, start, start + winAngle);
    ctx.closePath();
    const gradWin = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
    gradWin.addColorStop(0, '#4CAF50');
    gradWin.addColorStop(1, '#1B5E20');
    ctx.fillStyle = gradWin;
    ctx.fill();
    ctx.strokeStyle = 'rgba(255,255,255,0.05)';
    ctx.lineWidth = 1;
    ctx.stroke();
    start += winAngle;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, r, start, start + loseAngle);
    ctx.closePath();
    const gradLose = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
    gradLose.addColorStop(0, '#F44336');
    gradLose.addColorStop(1, '#880E4F');
    ctx.fillStyle = gradLose;
    ctx.fill();
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, 2 * Math.PI);
    ctx.strokeStyle = 'rgba(255,255,255,0.08)';
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(cx, cy, r * 0.15, 0, 2 * Math.PI);
    ctx.fillStyle = 'rgba(13,18,29,0.6)';
    ctx.fill();
    ctx.fillStyle = 'rgba(255,255,255,0.7)';
    ctx.font = 'bold 14px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    const labelAngle = -Math.PI/2 + winAngle/2;
    ctx.fillText('WIN', cx + Math.cos(labelAngle) * r * 0.6, cy + Math.sin(labelAngle) * r * 0.6);
    const loseLabelAngle = -Math.PI/2 + winAngle + loseAngle/2;
    ctx.fillText('LOSE', cx + Math.cos(loseLabelAngle) * r * 0.6, cy + Math.sin(loseLabelAngle) * r * 0.6);
    for (let i = 0; i < 24; i++) {
        const angle = (i / 24) * 2 * Math.PI - Math.PI/2;
        const r1 = r * 0.94, r2 = r * 0.98;
        ctx.beginPath();
        ctx.moveTo(cx + Math.cos(angle) * r1, cy + Math.sin(angle) * r1);
        ctx.lineTo(cx + Math.cos(angle) * r2, cy + Math.sin(angle) * r2);
        ctx.strokeStyle = 'rgba(255,255,255,0.1)';
        ctx.lineWidth = 2;
        ctx.stroke();
    }
}

let wheelRotation = 0;

async function runUpgrade() {
    if (STATE.isUpgrading) return;
    const inv = STATE.inventory;
    if (inv.length === 0) {
        showToast('Нет предметов для апгрейда', 'error');
        return;
    }
    const idx = Math.min(STATE.selectedItemIndex, inv.length - 1);
    const item = inv[idx];
    if (!item) return;
    const targetVal = STATE.targetValue;
    if (item.value >= targetVal) {
        showToast('Цель должна быть дороже', 'error');
        return;
    }
    STATE.isUpgrading = true;
    const btn = document.getElementById('upgradeBtn');
    btn.classList.add('loading');
    btn.disabled = true;
    document.getElementById('wheelGlow').className = 'wheel-glow';

    try {
        const data = await apiCall('POST', '/api/upgrade', {
            item_index: idx,
            target_value: targetVal
        });
        const finalAngle = data.angle || 0;
        const isSuccess = data.success || false;
        const totalRotation = 360 * 5 + (finalAngle % 360);
        wheelRotation += totalRotation;
        document.getElementById('wheel').style.transform = 'rotate(' + wheelRotation + 'deg)';
        
        const glow = document.getElementById('wheelGlow');
        if (isSuccess) {
            glow.className = 'wheel-glow success';
            window.tgHaptic?.notify('success');
        } else {
            glow.className = 'wheel-glow fail';
            window.tgHaptic?.notify('error');
        }
        
        if (data.balance !== undefined) {
            STATE.balance = data.balance;
            updateBalance();
        }

        setTimeout(async () => {
            try {
                const profile = await apiCall('GET', '/api/profile');
                STATE.inventory = profile.inventory || [];
                STATE.balance = profile.balance || 0;
                updateBalance();
                renderInventory();
                updateUpgraderUI();
                showToast(data.message || (isSuccess ? '🎉 Успешно!' : '💔 Неудача'), isSuccess ? 'success' : 'error');
            } catch (e) {}
            STATE.isUpgrading = false;
            btn.classList.remove('loading');
            btn.disabled = false;
            document.getElementById('wheelGlow').className = 'wheel-glow';
        }, 4500);

    } catch (e) {
        showToast('Ошибка: ' + e.message, 'error');
        STATE.isUpgrading = false;
        btn.classList.remove('loading');
        btn.disabled = false;
        document.getElementById('wheelGlow').className = 'wheel-glow';
    }
}

// ============================================================
// 7. INVENTORY
// ============================================================
function renderInventory() {
    const grid = document.getElementById('invGrid');
    const empty = document.getElementById('invEmpty');
    grid.innerHTML = '';
    if (!STATE.inventory || STATE.inventory.length === 0) {
        empty.style.display = 'block';
        return;
    }
    empty.style.display = 'none';
    STATE.inventory.forEach((item, idx) => {
        const div = document.createElement('div');
        div.className = 'inv-item' + (idx === STATE.selectedItemIndex ? ' selected' : '');
        const colors = {
            'Common': 'rgba(139,139,139,0.2)',
            'Uncommon': 'rgba(76,175,80,0.2)',
            'Rare': 'rgba(33,150,243,0.2)',
            'Epic': 'rgba(156,39,176,0.2)',
            'Legendary': 'rgba(255,193,7,0.2)',
            'Mythic': 'rgba(244,67,54,0.2)'
        };
        div.style.borderColor = colors[item.rarity] || 'rgba(255,255,255,0.05)';
        div.innerHTML = `
            <div class="emoji">${item.emoji || '🎁'}</div>
            <div class="name">${item.name}</div>
            <div class="val">⭐ ${item.value}</div>
            <div style="font-size:9px; color:#8899AA;">${item.rarity}</div>
            <button class="sell-btn" data-idx="${idx}">Продать</button>
        `;
        div.addEventListener('click', (e) => {
            if (e.target.classList.contains('sell-btn')) return;
            STATE.selectedItemIndex = idx;
            renderInventory();
            updateUpgraderUI();
        });
        const sellBtn = div.querySelector('.sell-btn');
        sellBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            await sellItem(idx);
        });
        grid.appendChild(div);
    });
}

async function sellItem(idx) {
    try {
        const data = await apiCall('POST', '/api/inventory/sell', { item_index: idx });
        if (data.success) {
            STATE.balance = data.balance || STATE.balance;
            updateBalance();
            const profile = await apiCall('GET', '/api/profile');
            STATE.inventory = profile.inventory || [];
            renderInventory();
            updateUpgraderUI();
            showToast('💰 Продано за ' + data.price + ' ⭐', 'success');
            window.tgHaptic?.impact('light');
        }
    } catch (e) {
        showToast('Ошибка: ' + e.message, 'error');
    }
}

// ============================================================
// 8. MINES
// ============================================================
let minesState = { grid: [], opened: [], bombs: [], gameId: null, bet: 0, multiplier: 1, cashedOut: false, started: false };

async function startMines() {
    const bet = parseInt(document.getElementById('minesBet').value) || 10;
    const mines = parseInt(document.getElementById('minesCount').value) || 3;
    if (mines < 1 || mines > 24) {
        showToast('Мины: 1-24', 'error');
        return;
    }
    try {
        const data = await apiCall('POST', '/api/mines/start', { bet, mines });
        STATE.balance = data.balance || STATE.balance;
        updateBalance();
        minesState.gameId = data.game_id;
        minesState.bet = data.bet;
        minesState.started = true;
        minesState.opened = [];
        minesState.cashedOut = false;
        minesState.multiplier = 1;
        minesState.bombs = [];
        minesState.grid = Array(25).fill(0);
        renderMines();
        document.getElementById('minesMultiplier').textContent = '1.00x';
        document.getElementById('minesOpened').textContent = '0';
        document.getElementById('minesCashoutBtn').style.display = 'none';
        showToast('💣 Игра начата!', 'info');
    } catch (e) {
        showToast('Ошибка: ' + e.message, 'error');
    }
}

async function openMineCell(idx) {
    if (!minesState.started || minesState.cashedOut) return;
    if (minesState.opened.includes(idx)) return;
    try {
        const data = await apiCall('POST', '/api/mines/open', {
            game_id: minesState.gameId,
            cell: idx
        });
        if (data.status === 'bomb') {
            minesState.cashedOut = true;
            minesState.bombs = data.mines || [];
            renderMines();
            showToast('💥 Бомба!', 'error');
            window.tgHaptic?.notify('error');
            document.getElementById('minesCashoutBtn').style.display = 'none';
            const profile = await apiCall('GET', '/api/profile');
            STATE.balance = profile.balance || 0;
            updateBalance();
            return;
        }
        minesState.opened = data.opened || [];
        minesState.multiplier = data.multiplier || 1;
        document.getElementById('minesMultiplier').textContent = minesState.multiplier.toFixed(2) + 'x';
        document.getElementById('minesOpened').textContent = minesState.opened.length;
        renderMines();
        if (minesState.opened.length > 0) {
            document.getElementById('minesCashoutBtn').style.display = 'block';
        }
        window.tgHaptic?.impact('light');
    } catch (e) {
        showToast('Ошибка: ' + e.message, 'error');
    }
}

async function cashoutMines() {
    if (!minesState.started || minesState.cashedOut) return;
    if (minesState.opened.length === 0) {
        showToast('Откройте хотя бы одну клетку', 'error');
        return;
    }
    try {
        const data = await apiCall('POST', '/api/mines/cashout', {
            game_id: minesState.gameId
        });
        STATE.balance = data.balance || STATE.balance;
        updateBalance();
        minesState.cashedOut = true;
        showToast('💰 Выигрыш: ' + data.win + ' ⭐ (x' + data.multiplier + ')', 'success');
        window.tgHaptic?.notify('success');
        document.getElementById('minesCashoutBtn').style.display = 'none';
        renderMines();
    } catch (e) {
        showToast('Ошибка: ' + e.message, 'error');
    }
}

function renderMines() {
    const grid = document.getElementById('minesGrid');
    grid.innerHTML = '';
    for (let i = 0; i < 25; i++) {
        const cell = document.createElement('div');
        cell.className = 'mine-cell';
        if (minesState.opened.includes(i)) {
            cell.classList.add('opened');
            cell.textContent = '💎';
        }
        if (minesState.cashedOut && minesState.bombs && minesState.bombs.includes(i)) {
            cell.classList.add('bomb');
            cell.textContent = '💣';
        }
        if (minesState.cashedOut && !minesState.opened.includes(i) && !(minesState.bombs && minesState.bombs.includes(i))) {
            cell.textContent = '💎';
            cell.style.opacity = '0.5';
        }
        cell.dataset.idx = i;
        cell.addEventListener('click', () => openMineCell(i));
        grid.appendChild(cell);
    }
}

// ============================================================
// 9. CRASH
// ============================================================
function initCrash() {
    const socket = io();
    STATE.crashSocket = socket;
    socket.on('connect', () => { STATE.crashConnected = true; });
    socket.on('crash_state', (data) => {
        document.getElementById('crashStatus').textContent = 
            data.status === 'betting' ? '⌛ Ставки: ' + data.timer + 'с' :
            data.status === 'flying' ? '🚀 Взлёт!' :
            data.status === 'crashed' ? '💥 Крах!' :
            '⏳ Ожидание...';
        if (data.status === 'betting') document.getElementById('crashMultiplier').textContent = '1.00x';
        if (data.history) drawCrashHistory(data.history);
    });
    socket.on('crash_multiplier', (data) => {
        document.getElementById('crashMultiplier').textContent = data.multiplier.toFixed(2) + 'x';
    });
    socket.on('crash_start', (data) => {
        document.getElementById('crashStatus').textContent = '🚀 Взлёт!';
        document.getElementById('crashBetBtn').disabled = true;
        document.getElementById('crashCashoutBtn').style.display = 'block';
        STATE.crashBetPlaced = true;
    });
    socket.on('crash_end', (data) => {
        document.getElementById('crashStatus').textContent = '💥 Крах на ' + data.crash_point.toFixed(2) + 'x';
        document.getElementById('crashBetBtn').disabled = false;
        document.getElementById('crashCashoutBtn').style.display = 'none';
        STATE.crashBetPlaced = false;
        loadProfile();
        if (data.bets) {
            document.getElementById('crashBetsList').innerHTML = data.bets.map(b => 
                `<span style="font-size:12px; padding:2px 10px; border-radius:10px; background:rgba(255,255,255,0.05);">
                    ${b.username}: ${b.win > 0 ? '✅+' + b.win : '❌0'}
                </span>`
            ).join('');
        }
    });
    socket.on('cashout_success', (data) => {
        showToast('💰 Забрано: ' + data.win + ' ⭐', 'success');
        window.tgHaptic?.notify('success');
        STATE.balance = data.balance || STATE.balance;
        updateBalance();
        document.getElementById('crashCashoutBtn').style.display = 'none';
    });
    socket.on('bet_placed', (data) => {
        showToast('✅ Ставка ' + data.amount + ' ⭐ принята', 'success');
        STATE.balance = data.balance || STATE.balance;
        updateBalance();
    });
    socket.on('error', (data) => {
        showToast('❌ ' + data.message, 'error');
    });
}

function placeCrashBet() {
    if (!STATE.crashConnected) { showToast('Подключение...', 'info'); return; }
    const amount = parseInt(document.getElementById('crashBetInput').value) || 25;
    if (amount < 25 || amount > 5000) { showToast('Ставка 25-5000 ⭐', 'error'); return; }
    if (amount > STATE.balance) { showToast('Недостаточно средств', 'error'); return; }
    STATE.crashSocket.emit('place_bet', { tg_id: STATE.tgId, amount, username: STATE.username });
}

function crashCashout() {
    if (!STATE.crashConnected) return;
    STATE.crashSocket.emit('cashout', { tg_id: STATE.tgId });
}

function drawCrashHistory(history) {
    const canvas = document.getElementById('crashCanvas');
    const ctx = canvas.getContext('2d');
    canvas.width = canvas.parentElement.clientWidth;
    canvas.height = canvas.parentElement.clientHeight;
    const w = canvas.width, h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    if (!history || history.length < 2) {
        ctx.fillStyle = '#8899AA';
        ctx.font = '14px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('История раундов', w/2, h/2);
        return;
    }
    const max = Math.max(2, ...history);
    const min = 1;
    const step = w / (history.length - 1);
    ctx.beginPath();
    ctx.strokeStyle = '#FFC107';
    ctx.lineWidth = 2;
    for (let i = 0; i < history.length; i++) {
        const x = i * step;
        const y = h - ((history[i] - min) / (max - min)) * (h - 20) - 10;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.lineTo(w, h);
    ctx.lineTo(0, h);
    ctx.closePath();
    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, 'rgba(255,193,7,0.15)');
    grad.addColorStop(1, 'rgba(255,193,7,0)');
    ctx.fillStyle = grad;
    ctx.fill();
}

// ============================================================
// 10. REFERRALS
// ============================================================
async function loadRefStats() {
    try {
        const data = await apiCall('GET', '/api/referral/stats');
        document.getElementById('refCount').textContent = data.referrals_count || 0;
        document.getElementById('refEarned').textContent = data.total_earned || 0;
        const link = 'https://t.me/' + (window.Telegram?.WebApp?.initDataUnsafe?.user?.username || 'GiftUpgraderBot') + '?start=ref_' + STATE.tgId;
        document.getElementById('refLink').textContent = link;
        window._refLink = link;
    } catch (e) {}
}

function copyRefLink() {
    const link = window._refLink || '';
    if (navigator.clipboard) {
        navigator.clipboard.writeText(link).then(() => showToast('📋 Ссылка скопирована!', 'success'));
    } else {
        const textarea = document.createElement('textarea');
        textarea.value = link;
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
        showToast('📋 Ссылка скопирована!', 'success');
    }
}

// ============================================================
// 11. PROMO
// ============================================================
async function activatePromo() {
    const code = document.getElementById('promoInput').value.trim().toUpperCase();
    if (!code) { showToast('Введите промокод', 'error'); return; }
    try {
        const data = await apiCall('POST', '/api/promo/activate?code=' + encodeURIComponent(code), {});
        showToast(data.message || '✅ Промокод активирован!', 'success');
        window.tgHaptic?.notify('success');
        loadProfile();
        document.getElementById('promoInput').value = '';
    } catch (e) {
        showToast('Ошибка: ' + e.message, 'error');
    }
}

// ============================================================
// 12. WITHDRAW
// ============================================================
async function withdrawFunds() {
    const amount = prompt('Введите сумму для вывода (мин 100 ⭐):', '100');
    if (!amount) return;
    const val = parseInt(amount);
    if (isNaN(val) || val < 100) { showToast('Минимальный вывод: 100 ⭐', 'error'); return; }
    const wallet = prompt('Введите адрес кошелька (TON/TRC20):', '');
    if (!wallet || wallet.length < 10) { showToast('Введите корректный адрес', 'error'); return; }
    try {
        const data = await apiCall('POST', '/api/withdraw', { amount: val, wallet });
        showToast('✅ Заявка создана!', 'success');
        STATE.balance = data.balance || STATE.balance;
        updateBalance();
    } catch (e) {
        showToast('Ошибка: ' + e.message, 'error');
    }
}

// ============================================================
// 13. TABS
// ============================================================
function initTabs() {
    const tabs = document.querySelectorAll('.tab-btn');
    tabs.forEach(btn => {
        btn.addEventListener('click', function() {
            tabs.forEach(b => b.classList.remove('active'));
            this.classList.add('active');
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            document.getElementById('tab-' + this.dataset.tab).classList.add('active');
            if (this.dataset.tab === 'inventory') renderInventory();
            if (this.dataset.tab === 'profile') loadProfile();
            if (this.dataset.tab === 'crash' && !STATE.crashConnected) initCrash();
        });
    });
}

// ============================================================
// 14. INIT
// ============================================================
document.addEventListener('DOMContentLoaded', function() {
    initTelegram();
    loadProfile();
    initTabs();
    renderInventory();
    updateUpgraderUI();
    
    document.getElementById('upgradeBtn').addEventListener('click', runUpgrade);
    document.getElementById('minesStartBtn').addEventListener('click', startMines);
    document.getElementById('minesCashoutBtn').addEventListener('click', cashoutMines);
    document.getElementById('crashBetBtn').addEventListener('click', placeCrashBet);
    document.getElementById('crashCashoutBtn').addEventListener('click', crashCashout);
    document.getElementById('promoActivateBtn').addEventListener('click', activatePromo);
    document.getElementById('copyRefBtn').addEventListener('click', copyRefLink);
    document.getElementById('withdrawBtn').addEventListener('click', withdrawFunds);
    document.getElementById('promoInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') activatePromo(); });
    
    if (document.querySelector('.tab-btn[data-tab="crash"]').classList.contains('active')) initCrash();
});

console.log('🎮 GiftUpgrader v2.0 loaded');
</script>
</body>
</html>
"""

# ==================== ADMIN PANEL HTML ====================
ADMIN_PANEL_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GiftUpgrader Admin</title>
    <style>
        * { margin:0; padding:0; box-sizing:border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0D121D;
            color: #E8E8E8;
            padding: 20px;
        }
        .container { max-width:1200px; margin:0 auto; }
        .header {
            display:flex; justify-content:space-between; align-items:center;
            padding:20px; background:#161F2E; border-radius:16px;
            margin-bottom:30px; border:1px solid #2A3A4F;
        }
        .header h1 {
            font-size:28px;
            background:linear-gradient(135deg,#FFC107,#F44336);
            -webkit-background-clip:text; -webkit-text-fill-color:transparent;
        }
        .header .admin-badge {
            background:#2A3A4F; padding:8px 16px; border-radius:20px;
            font-size:14px; border:1px solid #FFC107; color:#FFC107;
        }
        .stats-grid {
            display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
            gap:16px; margin-bottom:30px;
        }
        .stat-card {
            background:#161F2E; padding:20px; border-radius:12px;
            border:1px solid #2A3A4F; text-align:center;
        }
        .stat-card .value {
            font-size:32px; font-weight:bold;
            background:linear-gradient(135deg,#FFC107,#FF6B00);
            -webkit-background-clip:text; -webkit-text-fill-color:transparent;
        }
        .stat-card .label { font-size:14px; color:#8899AA; margin-top:4px; }
        .panel {
            background:#161F2E; border-radius:16px; padding:24px;
            margin-bottom:24px; border:1px solid #2A3A4F;
        }
        .panel h2 { font-size:20px; margin-bottom:16px; color:#FFC107; }
        .form-group { margin-bottom:16px; }
        .form-group label { display:block; font-size:14px; color:#8899AA; margin-bottom:4px; }
        .form-group input, .form-group select {
            width:100%; padding:10px 14px; background:#0D121D;
            border:1px solid #2A3A4F; border-radius:8px;
            color:#E8E8E8; font-size:14px;
        }
        .form-group input:focus, .form-group select:focus { outline:none; border-color:#FFC107; }
        .form-row { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
        .btn {
            padding:10px 24px; border:none; border-radius:8px;
            font-size:14px; font-weight:600; cursor:pointer; transition:all 0.3s;
        }
        .btn-primary { background:linear-gradient(135deg,#FFC107,#FF6B00); color:#0D121D; }
        .btn-primary:hover { transform:translateY(-2px); box-shadow:0 8px 24px rgba(255,193,7,0.3); }
        .btn-success { background:#4CAF50; color:white; }
        .btn-danger { background:#F44336; color:white; }
        .btn-danger:hover { background:#D32F2F; }
        .btn-success:hover { background:#388E3C; }
        .table-wrapper { overflow-x:auto; }
        table { width:100%; border-collapse:collapse; font-size:14px; }
        th { text-align:left; padding:12px; color:#8899AA; border-bottom:1px solid #2A3A4F; font-weight:600; }
        td { padding:12px; border-bottom:1px solid #1A2A3F; }
        .status-badge { padding:4px 12px; border-radius:12px; font-size:12px; font-weight:600; }
        .status-pending { background:#FFC107; color:#0D121D; }
        .status-approved { background:#4CAF50; color:white; }
        .status-rejected { background:#F44336; color:white; }
        .actions { display:flex; gap:8px; }
        .actions .btn { padding:4px 12px; font-size:12px; }
        .tabs { display:flex; gap:8px; margin-bottom:20px; flex-wrap:wrap; }
        .tab {
            padding:10px 20px; background:#0D121D; border:1px solid #2A3A4F;
            border-radius:8px; cursor:pointer; color:#8899AA; transition:all 0.3s;
        }
        .tab.active { border-color:#FFC107; color:#FFC107; background:#1A2A3F; }
        .tab:hover { border-color:#FFC107; }
        .tab-content { display:none; }
        .tab-content.active { display:block; }
        .empty-state { text-align:center; padding:40px; color:#8899AA; }
        .toast {
            position:fixed; bottom:20px; right:20px; padding:16px 24px;
            border-radius:12px; background:#161F2E; border:1px solid #2A3A4F;
            color:#E8E8E8; display:none; max-width:400px; z-index:1000;
        }
        .toast.success { border-color:#4CAF50; }
        .toast.error { border-color:#F44336; }
        @media (max-width:768px) {
            .form-row { grid-template-columns:1fr; }
            .header { flex-direction:column; gap:12px; text-align:center; }
        }
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>🎮 GiftUpgrader Admin</h1>
        <div class="admin-badge">👑 Admin Panel v2.0</div>
    </div>
    <div class="stats-grid" id="stats">
        <div class="stat-card"><div class="value" id="totalUsers">0</div><div class="label">Users</div></div>
        <div class="stat-card"><div class="value" id="totalWithdraws">0</div><div class="label">Pending Withdrawals</div></div>
        <div class="stat-card"><div class="value" id="totalPromos">0</div><div class="label">Promocodes</div></div>
        <div class="stat-card"><div class="value" id="totalReferrals">0</div><div class="label">Referrals</div></div>
    </div>
    <div class="tabs">
        <div class="tab active" data-tab="promocodes">🎫 Promocodes</div>
        <div class="tab" data-tab="withdrawals">💳 Withdrawals</div>
        <div class="tab" data-tab="users">👤 Users</div>
        <div class="tab" data-tab="logs">📋 Logs</div>
    </div>
    <div class="tab-content active" id="tab-promocodes">
        <div class="panel">
            <h2>Create Promocode</h2>
            <form id="promoForm">
                <div class="form-row">
                    <div class="form-group"><label>Promocode</label><input type="text" id="promoCode" placeholder="e.g. GIFT2024" required></div>
                    <div class="form-group"><label>Reward Type</label>
                        <select id="promoRewardType"><option value="stars">⭐ Stars</option><option value="gift">🎁 Gift</option></select>
                    </div>
                </div>
                <div class="form-row" id="starsField">
                    <div class="form-group"><label>Stars Amount</label><input type="number" id="promoStars" value="100" min="1"></div>
                </div>
                <div class="form-row" id="giftField" style="display:none;">
                    <div class="form-group"><label>Case ID</label>
                        <select id="promoCaseId">
                            <option value="tg_starter">🚀 TG STARTER</option>
                            <option value="pepe_memes">🐸 PEPE & MEMES</option>
                            <option value="telegram_gifts">🎁 TELEGRAM GIFTS</option>
                            <option value="fragment_nft">💎 FRAGMENT NFT</option>
                            <option value="durov_selection">👑 DUROV'S SELECTION</option>
                        </select>
                    </div>
                </div>
                <div class="form-group"><label>Max Uses</label><input type="number" id="promoMaxUses" value="1" min="1"></div>
                <button type="submit" class="btn btn-primary">Create</button>
            </form>
        </div>
        <div class="panel">
            <h2>Active Promocodes</h2>
            <div class="table-wrapper"><table><thead><tr><th>Code</th><th>Type</th><th>Reward</th><th>Uses</th><th>Max</th><th>Created</th></tr></thead><tbody id="promoList"><tr><td colspan="6" class="empty-state">No promocodes</td></tr></tbody></table></div>
        </div>
    </div>
    <div class="tab-content" id="tab-withdrawals">
        <div class="panel">
            <h2>Withdrawals</h2>
            <div class="table-wrapper"><table><thead><tr><th>ID</th><th>User</th><th>Amount</th><th>Wallet</th><th>Status</th><th>Date</th><th>Actions</th></tr></thead><tbody id="withdrawList"><tr><td colspan="7" class="empty-state">No withdrawals</td></tr></tbody></table></div>
        </div>
    </div>
    <div class="tab-content" id="tab-users">
        <div class="panel">
            <h2>Give Stars</h2>
            <form id="giveStarsForm">
                <div class="form-row">
                    <div class="form-group"><label>User ID</label><input type="number" id="giveUserId" placeholder="123456789" required></div>
                    <div class="form-group"><label>Amount</label><input type="number" id="giveAmount" value="100" min="1" required></div>
                </div>
                <button type="submit" class="btn btn-primary">Give Stars</button>
            </form>
        </div>
        <div class="panel">
            <h2>Top Users</h2>
            <div class="table-wrapper"><table><thead><tr><th>#</th><th>Username</th><th>Balance</th><th>Spent</th><th>Games</th><th>Wins</th></tr></thead><tbody id="userList"><tr><td colspan="6" class="empty-state">Loading...</td></tr></tbody></table></div>
        </div>
    </div>
    <div class="tab-content" id="tab-logs">
        <div class="panel">
            <h2>Admin Logs</h2>
            <div class="table-wrapper"><table><thead><tr><th>ID</th><th>Admin</th><th>Action</th><th>Details</th><th>Date</th></tr></thead><tbody id="logList"><tr><td colspan="5" class="empty-state">No logs</td></tr></tbody></table></div>
        </div>
    </div>
</div>
<div class="toast" id="toast"></div>
<script>
    let currentTab='promocodes';
    function showToast(m,t){const toast=document.getElementById('toast');toast.textContent=m;toast.className='toast '+t;toast.style.display='block';setTimeout(()=>{toast.style.display='none';},3000);}
    async function fetchData(url){try{const res=await fetch(url);if(!res.ok)throw new Error();return await res.json();}catch(e){return null;}}
    async function loadStats(){const data=await fetchData('/api/admin/stats');if(data){document.getElementById('totalUsers').textContent=data.total_users||0;document.getElementById('totalWithdraws').textContent=data.pending_withdrawals||0;document.getElementById('totalPromos').textContent=data.total_promos||0;document.getElementById('totalReferrals').textContent=data.total_referrals||0;}}
    async function loadPromos(){const data=await fetchData('/api/admin/promos');const tbody=document.getElementById('promoList');if(data&&data.length>0){tbody.innerHTML=data.map(p=>`<tr><td><strong>${p.code}</strong></td><td>${p.reward_type}</td><td>${p.reward_type==='stars'?'⭐ '+p.stars+' UC':'🎁 '+p.case_id}</td><td>${p.uses}/${p.max_uses}</td><td>${p.max_uses}</td><td>${new Date(p.created_at).toLocaleDateString()}</td></tr>`).join('');}else{tbody.innerHTML='<tr><td colspan="6" class="empty-state">No promocodes</td></tr>';}}
    async function loadWithdrawals(){const data=await fetchData('/api/admin/withdrawals');const tbody=document.getElementById('withdrawList');if(data&&data.length>0){tbody.innerHTML=data.map(w=>`<tr><td>#${w.id}</td><td>${w.tg_id}</td><td>⭐ ${w.amount}</td><td>${w.wallet||'N/A'}</td><td><span class="status-badge status-${w.status}">${w.status}</span></td><td>${new Date(w.created_at).toLocaleDateString()}</td><td>${w.status==='pending'?`<div class="actions"><button class="btn btn-success" onclick="updateWithdraw(${w.id},'approved')">✅</button><button class="btn btn-danger" onclick="updateWithdraw(${w.id},'rejected')">❌</button></div>`:'-'}</td></tr>`).join('');}else{tbody.innerHTML='<tr><td colspan="7" class="empty-state">No withdrawals</td></tr>';}}
    async function loadUsers(){const data=await fetchData('/api/admin/users');const tbody=document.getElementById('userList');if(data&&data.length>0){tbody.innerHTML=data.map((u,i)=>`<tr><td>#${i+1}</td><td>${u.username}</td><td>⭐ ${u.balance}</td><td>⭐ ${u.total_spent}</td><td>${u.games_played||0}</td><td>${u.wins||0}</td></tr>`).join('');}else{tbody.innerHTML='<tr><td colspan="6" class="empty-state">No users</td></tr>';}}
    async function loadLogs(){const data=await fetchData('/api/admin/logs');const tbody=document.getElementById('logList');if(data&&data.length>0){tbody.innerHTML=data.map(l=>`<tr><td>#${l.id}</td><td>${l.admin_id}</td><td>${l.action}</td><td>${l.details||'-'}</td><td>${new Date(l.created_at).toLocaleString()}</td></tr>`).join('');}else{tbody.innerHTML='<tr><td colspan="5" class="empty-state">No logs</td></tr>';}}
    async function updateWithdraw(id,status){if(!confirm('Set withdrawal #'+id+' to '+status+'?'))return;const res=await fetch('/api/admin/withdraw/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({withdraw_id:id,status})});const data=await res.json();if(data.success){showToast('✅ #'+id+' '+status,'success');loadWithdrawals();loadStats();}else{showToast('❌ Error','error');}}
    document.querySelectorAll('.tab').forEach(tab=>{tab.addEventListener('click',function(){document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));document.querySelectorAll('.tab-content').forEach(t=>t.classList.remove('active'));this.classList.add('active');document.getElementById('tab-'+this.dataset.tab).classList.add('active');currentTab=this.dataset.tab;if(currentTab==='withdrawals')loadWithdrawals();if(currentTab==='users')loadUsers();if(currentTab==='logs')loadLogs();});});
    document.getElementById('promoRewardType').addEventListener('change',function(){if(this.value==='stars'){document.getElementById('starsField').style.display='block';document.getElementById('giftField').style.display='none';}else{document.getElementById('starsField').style.display='none';document.getElementById('giftField').style.display='block';}});
    document.getElementById('promoForm').addEventListener('submit',async function(e){e.preventDefault();const data={code:document.getElementById('promoCode').value.toUpperCase(),reward_type:document.getElementById('promoRewardType').value,case_id:document.getElementById('promoCaseId').value,stars:parseInt(document.getElementById('promoStars').value)||0,max_uses:parseInt(document.getElementById('promoMaxUses').value)||1};const res=await fetch('/api/admin/promo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const result=await res.json();if(result.success){showToast('✅ '+result.message,'success');loadPromos();loadStats();document.getElementById('promoForm').reset();}else{showToast('❌ '+(result.detail||'Error'),'error');}});
    document.getElementById('giveStarsForm').addEventListener('submit',async function(e){e.preventDefault();const data={user_id:parseInt(document.getElementById('giveUserId').value),amount:parseInt(document.getElementById('giveAmount').value)};const res=await fetch('/api/admin/give',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const result=await res.json();if(result.success){showToast('✅ '+result.message,'success');loadUsers();loadStats();document.getElementById('giveStarsForm').reset();}else{showToast('❌ '+(result.detail||'Error'),'error');}});
    loadStats();loadPromos();loadWithdrawals();loadUsers();loadLogs();
    setInterval(()=>{loadStats();if(currentTab==='promocodes')loadPromos();if(currentTab==='withdrawals')loadWithdrawals();if(currentTab==='users')loadUsers();if(currentTab==='logs')loadLogs();},30000);
</script>
</body>
</html>
"""

# ==================== ROOT ====================
@app.get("/", response_class=HTMLResponse)
async def root():
    return FRONTEND_HTML

@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(user: dict = Depends(verify_admin)):
    return ADMIN_PANEL_HTML

# ==================== ADMIN API ====================
@app.get("/api/admin/stats")
async def admin_stats(user: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        total_users = await (await db.execute("SELECT COUNT(*) FROM users")).fetchone()
        pending_withdraws = await (await db.execute("SELECT COUNT(*) FROM withdrawals WHERE status='pending'")).fetchone()
        total_promos = await (await db.execute("SELECT COUNT(*) FROM promocodes")).fetchone()
        total_refs = await (await db.execute("SELECT COUNT(*) FROM referrals")).fetchone()
        return {
            "total_users": total_users[0] if total_users else 0,
            "pending_withdrawals": pending_withdraws[0] if pending_withdraws else 0,
            "total_promos": total_promos[0] if total_promos else 0,
            "total_referrals": total_refs[0] if total_refs else 0
        }

@app.get("/api/admin/promos")
async def admin_promos(user: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT code, reward_type, case_id, stars, max_uses, uses, created_at FROM promocodes ORDER BY created_at DESC") as cursor:
            rows = await cursor.fetchall()
            return [{"code": r[0], "reward_type": r[1], "case_id": r[2], "stars": r[3], "max_uses": r[4], "uses": r[5], "created_at": r[6]} for r in rows]

@app.get("/api/admin/users")
async def admin_users(user: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT username, balance, total_spent, games_played, wins FROM users ORDER BY balance DESC LIMIT 50") as cursor:
            rows = await cursor.fetchall()
            return [{"username": r[0], "balance": r[1], "total_spent": r[2], "games_played": r[3], "wins": r[4]} for r in rows]

@app.get("/api/admin/logs")
async def admin_logs(user: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, admin_id, action, details, created_at FROM admin_logs ORDER BY created_at DESC LIMIT 50") as cursor:
            rows = await cursor.fetchall()
            return [{"id": r[0], "admin_id": r[1], "action": r[2], "details": r[3], "created_at": r[4]} for r in rows]

# ==================== MAIN API ====================
@app.get("/api/profile")
async def profile(user: dict = Depends(verify_telegram)):
    tg_id = user['id']
    username = user.get('first_name', 'Player')
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET username=? WHERE tg_id=?", (username, tg_id))
        await db.commit()
    user_data = await get_user(tg_id)
    user_data["tg_id"] = tg_id
    user_data["username"] = username
    user_data["is_admin"] = (tg_id == ADMIN_TG_ID)
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT last_used FROM free_case_cooldowns WHERE user_id=?", (tg_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                last_used = datetime.fromisoformat(row[0])
                user_data["free_case_available"] = (datetime.now() - last_used).total_seconds() >= 86400
            else:
                user_data["free_case_available"] = True
    return user_data

@app.get("/api/gifts")
async def get_gifts():
    return {"rarities": RARITY_COLORS, "gifts": NFT_GIFTS}

@app.get("/api/cases")
async def get_cases():
    return CASES

@app.post("/api/case/open")
async def open_case(req: CaseOpenRequest, user: dict = Depends(verify_telegram)):
    tg_id = user['id']
    if req.case_id not in CASES:
        raise HTTPException(status_code=400, detail="Invalid case")
    case = CASES[req.case_id]
    if case["price"] > 0:
        user_data = await get_user(tg_id)
        if user_data["balance"] < case["price"]:
            raise HTTPException(status_code=400, detail="Insufficient balance")
    if req.case_id == "free_daily":
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT last_used FROM free_case_cooldowns WHERE user_id=?", (tg_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    last_used = datetime.fromisoformat(row[0])
                    if (datetime.now() - last_used).total_seconds() < 86400:
                        raise HTTPException(status_code=400, detail="Free case on cooldown")
                await db.execute("INSERT OR REPLACE INTO free_case_cooldowns (user_id, last_used) VALUES (?, ?)", (tg_id, datetime.now().isoformat()))
                await db.commit()
    # Определяем, даём ли звёзды или предмет
    if random.random() < 0.3:  # 30% шанс на звёзды
        stars = round(random.uniform(case.get("min_stars", 0), case.get("max_stars", 10)), 1)
        async with aiosqlite.connect(DB_NAME) as db:
            if case["price"] > 0:
                await db.execute("UPDATE users SET balance=balance-?, total_spent=total_spent+? WHERE tg_id=?", (case["price"], case["price"], tg_id))
            await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (int(stars), tg_id))
            await db.commit()
        return {
            "success": True,
            "stars_earned": stars,
            "balance": (await get_user(tg_id))["balance"]
        }
    else:
        rarity = random.choices(case["rarities"], weights=case["weights"], k=1)[0]
        gift = random.choice(NFT_GIFTS[rarity])
        async with aiosqlite.connect(DB_NAME) as db:
            if case["price"] > 0:
                await db.execute("UPDATE users SET balance=balance-?, total_spent=total_spent+? WHERE tg_id=?", (case["price"], case["price"], tg_id))
            user_data = await get_user(tg_id)
            inventory = user_data["inventory"]
            inventory.append({
                "id": gift["id"],
                "name": gift["name"],
                "rarity": rarity,
                "value": gift["value"],
                "emoji": gift["emoji"]
            })
            await db.execute("UPDATE users SET inventory=? WHERE tg_id=?", (json.dumps(inventory), tg_id))
            await db.commit()
        return {
            "success": True,
            "gift": gift,
            "rarity": rarity,
            "color": RARITY_COLORS[rarity],
            "balance": (await get_user(tg_id))["balance"]
        }

@app.post("/api/upgrade")
async def upgrade_item(req: UpgradeRequest, user: dict = Depends(verify_telegram)):
    tg_id = user['id']
    user_data = await get_user(tg_id)
    if req.item_index < 0 or req.item_index >= len(user_data["inventory"]):
        raise HTTPException(status_code=400, detail="Item not found")
    item = user_data["inventory"][req.item_index]
    if item["value"] >= req.target_value:
        raise HTTPException(status_code=400, detail="Target must be higher value")
    target_gift = None
    for rarity, gifts in NFT_GIFTS.items():
        for g in gifts:
            if g["value"] == req.target_value:
                target_gift = {**g, "rarity": rarity}
                break
        if target_gift:
            break
    if not target_gift:
        raise HTTPException(status_code=400, detail="Target gift not found")
    chance = calculate_upgrade_chance(item["value"], target_gift["value"]) / 100.0
    is_win = random.random() < chance
    win_angle_deg = chance * 360
    if is_win:
        margin = 3
        if win_angle_deg > margin * 2:
            final_angle = random.uniform(margin, win_angle_deg - margin)
        else:
            final_angle = win_angle_deg / 2
    else:
        margin = 3
        if win_angle_deg < 360 - margin:
            final_angle = random.uniform(win_angle_deg + margin, 360 - margin)
        else:
            final_angle = random.uniform(0, 360 - margin)
    if is_win:
        user_data["inventory"][req.item_index] = {
            "id": target_gift["id"],
            "name": target_gift["name"],
            "rarity": target_gift["rarity"],
            "value": target_gift["value"],
            "emoji": target_gift["emoji"]
        }
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET inventory=?, wins=wins+1 WHERE tg_id=?", (json.dumps(user_data["inventory"]), tg_id))
            await db.commit()
    else:
        del user_data["inventory"][req.item_index]
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET inventory=? WHERE tg_id=?", (json.dumps(user_data["inventory"]), tg_id))
            await db.commit()
    return {
        "success": is_win,
        "chance": chance * 100,
        "target": target_gift,
        "angle": final_angle,
        "win_sector": {"start": 0, "end": win_angle_deg, "degrees": win_angle_deg},
        "message": f"{'🎉 Успешно!' if is_win else '💔 Неудача!'} {item['name']} → {target_gift['name']}",
        "balance": (await get_user(tg_id))["balance"]
    }

@app.get("/api/inventory")
async def get_inventory(user: dict = Depends(verify_telegram)):
    user_data = await get_user(user['id'])
    return {"inventory": user_data["inventory"]}

@app.post("/api/inventory/sell")
async def sell_item(req: SellItemRequest, user: dict = Depends(verify_telegram)):
    tg_id = user['id']
    user_data = await get_user(tg_id)
    if req.item_index < 0 or req.item_index >= len(user_data["inventory"]):
        raise HTTPException(status_code=400, detail="Item not found")
    item = user_data["inventory"].pop(req.item_index)
    sell_price = int(item["value"] * 0.7)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance=balance+?, inventory=? WHERE tg_id=?", (sell_price, json.dumps(user_data["inventory"]), tg_id))
        await db.commit()
    return {"success": True, "sold": item["name"], "price": sell_price, "balance": (await get_user(tg_id))["balance"]}

# ==================== MINES ====================
active_mines: Dict[int, dict] = {}

@app.post("/api/mines/start")
async def mines_start(req: MinesStartRequest, user: dict = Depends(verify_telegram)):
    tg_id = user['id']
    if req.bet < 10:
        raise HTTPException(status_code=400, detail="Min 10 ⭐")
    if req.bet > 50000:
        raise HTTPException(status_code=400, detail="Max 50000 ⭐")
    if req.mines < 1 or req.mines > 24:
        raise HTTPException(status_code=400, detail="Mines 1-24")
    user_data = await get_user(tg_id)
    if user_data["balance"] < req.bet:
        raise HTTPException(status_code=400, detail="Insufficient balance")
    total = MINES_GRID_SIZE * MINES_GRID_SIZE
    grid = [0] * total
    mine_positions = random.sample(range(total), req.mines)
    for pos in mine_positions:
        grid[pos] = 1
    game_id = str(uuid.uuid4())[:8]
    active_mines[tg_id] = {"game_id": game_id, "bet": req.bet, "mines": req.mines, "grid": grid, "opened": [], "cashed_out": False, "multiplier": 1.0}
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance=balance-?, games_played=games_played+1 WHERE tg_id=?", (req.bet, tg_id))
        await db.commit()
    return {"game_id": game_id, "bet": req.bet, "mines": req.mines, "total_cells": total, "balance": (await get_user(tg_id))["balance"]}

@app.post("/api/mines/open")
async def mines_open(req: MinesOpenRequest, user: dict = Depends(verify_telegram)):
    tg_id = user['id']
    if tg_id not in active_mines:
        raise HTTPException(status_code=400, detail="No active game")
    game = active_mines[tg_id]
    if game["game_id"] != req.game_id:
        raise HTTPException(status_code=400, detail="Invalid game")
    if game["cashed_out"]:
        raise HTTPException(status_code=400, detail="Game finished")
    if req.cell in game["opened"]:
        raise HTTPException(status_code=400, detail="Already opened")
    if req.cell < 0 or req.cell >= MINES_GRID_SIZE * MINES_GRID_SIZE:
        raise HTTPException(status_code=400, detail="Invalid cell")
    if game["grid"][req.cell] == 1:
        mine_positions = [i for i, v in enumerate(game["grid"]) if v == 1]
        del active_mines[tg_id]
        return {"status": "bomb", "cell": req.cell, "opened": game["opened"], "mines": mine_positions, "balance": (await get_user(tg_id))["balance"]}
    game["opened"].append(req.cell)
    game["multiplier"] = calculate_mines_multiplier(game["mines"], len(game["opened"]))
    return {"status": "safe", "cell": req.cell, "opened": game["opened"], "opened_count": len(game["opened"]), "multiplier": game["multiplier"]}

@app.post("/api/mines/cashout")
async def mines_cashout(req: MinesCashoutRequest, user: dict = Depends(verify_telegram)):
    tg_id = user['id']
    if tg_id not in active_mines:
        raise HTTPException(status_code=400, detail="No active game")
    game = active_mines[tg_id]
    if game["game_id"] != req.game_id:
        raise HTTPException(status_code=400, detail="Invalid game")
    if game["cashed_out"]:
        raise HTTPException(status_code=400, detail="Already cashed out")
    if len(game["opened"]) == 0:
        raise HTTPException(status_code=400, detail="Open at least one cell")
    win = int(game["bet"] * game["multiplier"])
    game["cashed_out"] = True
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance=balance+?, wins=wins+1 WHERE tg_id=?", (win, tg_id))
        await db.commit()
    del active_mines[tg_id]
    return {"status": "cashed_out", "multiplier": game["multiplier"], "win": win, "profit": win - game["bet"], "balance": (await get_user(tg_id))["balance"]}

# ==================== WITHDRAW ====================
@app.post("/api/withdraw")
async def withdraw(req: WithdrawRequest, user: dict = Depends(verify_telegram)):
    tg_id = user['id']
    if req.amount < 100:
        raise HTTPException(status_code=400, detail="Min 100 ⭐")
    if req.amount > MAX_WITHDRAW_AMOUNT:
        raise HTTPException(status_code=400, detail=f"Max {MAX_WITHDRAW_AMOUNT} ⭐")
    user_data = await get_user(tg_id)
    if user_data["balance"] < req.amount:
        raise HTTPException(status_code=400, detail="Insufficient balance")
    fee = int(req.amount * WITHDRAW_FEE)
    payout = req.amount - fee
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance=balance-? WHERE tg_id=?", (req.amount, tg_id))
        await db.execute("INSERT INTO withdrawals (tg_id, amount, wallet) VALUES (?,?,?)", (tg_id, req.amount, req.wallet))
        await db.commit()
    return {"success": True, "requested": req.amount, "fee": fee, "payout": payout, "balance": (await get_user(tg_id))["balance"], "status": "pending"}

@app.get("/api/admin/withdrawals")
async def admin_withdrawals(user: dict = Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, tg_id, amount, wallet, status, created_at FROM withdrawals ORDER BY created_at DESC LIMIT 100") as cursor:
            rows = await cursor.fetchall()
            return [{"id": r[0], "tg_id": r[1], "amount": r[2], "wallet": r[3], "status": r[4], "created_at": r[5]} for r in rows]

@app.post("/api/admin/withdraw/status")
async def update_withdraw_status(req: AdminWithdrawStatusRequest, user: dict = Depends(verify_admin)):
    if req.status not in ["approved", "rejected"]:
        raise HTTPException(status_code=400, detail="Invalid status")
    async with aiosqlite.connect(DB_NAME) as db:
        if req.status == "rejected":
            async with db.execute("SELECT tg_id, amount FROM withdrawals WHERE id=?", (req.withdraw_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (row[1], row[0]))
        await db.execute("UPDATE withdrawals SET status=? WHERE id=?", (req.status, req.withdraw_id))
        await db.commit()
        await log_admin_action(user['id'], f"withdraw_{req.status}", f"Withdrawal #{req.withdraw_id} -> {req.status}")
    return {"success": True, "status": req.status}

# ==================== ADMIN GIVE ====================
@app.post("/api/admin/give")
async def admin_give(req: AdminGiveRequest, user: dict = Depends(verify_admin)):
    if req.user_id <= 0 or req.amount < 1 or req.amount > 1000000:
        raise HTTPException(status_code=400, detail="Invalid")
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (tg_id, balance) VALUES (?, 50)", (req.user_id,))
        await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (req.amount, req.user_id))
        await db.commit()
        await log_admin_action(user['id'], "give_stars", f"Gave {req.amount} ⭐ to {req.user_id}")
    return {"success": True, "message": f"Added {req.amount} ⭐ to {req.user_id}"}

# ==================== PROMO ====================
@app.post("/api/admin/promo")
async def create_promo(req: PromoCreateRequest, user: dict = Depends(verify_admin)):
    code = req.code.strip().upper()
    async with aiosqlite.connect(DB_NAME) as db:
        if await (await db.execute("SELECT code FROM promocodes WHERE code=?", (code,))).fetchone():
            raise HTTPException(status_code=400, detail="Exists")
        await db.execute("INSERT INTO promocodes (code, reward_type, case_id, stars, max_uses, created_by) VALUES (?,?,?,?,?,?)",
                         (code, req.reward_type, req.case_id, req.stars, req.max_uses, user['id']))
        await db.commit()
        await log_admin_action(user['id'], "create_promo", f"Created {code} ({req.reward_type})")
    return {"success": True, "message": f"✅ {code} created!"}

@app.post("/api/promo/activate")
async def activate_promo(code: str, user: dict = Depends(verify_telegram)):
    tg_id = user['id']
    code = code.upper()
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT reward_type, case_id, stars, max_uses, uses FROM promocodes WHERE code=?", (code,)) as cursor:
            promo = await cursor.fetchone()
            if not promo:
                raise HTTPException(status_code=400, detail="Invalid")
            if promo[3] >= promo[4]:
                raise HTTPException(status_code=400, detail="Expired")
            if await (await db.execute("SELECT 1 FROM promo_uses WHERE user_id=? AND promo_code=?", (tg_id, code))).fetchone():
                raise HTTPException(status_code=400, detail="Already used")
        reward = None
        if promo[0] == "stars":
            amount = promo[2]
            await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (amount, tg_id))
            reward = f"⭐ {amount}"
        elif promo[0] == "gift":
            case_id = promo[1]
            if case_id not in CASES:
                raise HTTPException(status_code=400, detail="Invalid case")
            case = CASES[case_id]
            rarity = random.choices(case["rarities"], weights=case["weights"], k=1)[0]
            gift = random.choice(NFT_GIFTS[rarity])
            user_data = await get_user(tg_id)
            inventory = user_data["inventory"]
            inventory.append({"id": gift["id"], "name": gift["name"], "rarity": rarity, "value": gift["value"], "emoji": gift["emoji"]})
            await db.execute("UPDATE users SET inventory=? WHERE tg_id=?", (json.dumps(inventory), tg_id))
            reward = gift["name"]
        await db.execute("INSERT INTO promo_uses (user_id, promo_code) VALUES (?,?)", (tg_id, code))
        await db.execute("UPDATE promocodes SET uses=uses+1 WHERE code=?", (code,))
        await db.commit()
    return {"success": True, "message": "🎉 Promocode activated!", "reward": reward}

# ==================== REFERRALS ====================
@app.post("/api/referral/activate")
async def activate_referral(referrer_id: int, user: dict = Depends(verify_telegram)):
    tg_id = user['id']
    if tg_id == referrer_id:
        raise HTTPException(status_code=400, detail="Cannot refer yourself")
    async with aiosqlite.connect(DB_NAME) as db:
        if await (await db.execute("SELECT 1 FROM users WHERE tg_id=?", (referrer_id,))).fetchone() is None:
            raise HTTPException(status_code=400, detail="Referrer not found")
        if await (await db.execute("SELECT 1 FROM referrals WHERE user_id=?", (tg_id,))).fetchone():
            raise HTTPException(status_code=400, detail="Already referred")
        await db.execute("INSERT INTO referrals (user_id, referrer_id) VALUES (?,?)", (tg_id, referrer_id))
        await db.commit()
    return {"success": True, "referrer": referrer_id}

@app.get("/api/referral/stats")
async def referral_stats(user: dict = Depends(verify_telegram)):
    tg_id = user['id']
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT total_earned FROM referrals WHERE user_id=?", (tg_id,)) as cursor:
            row = await cursor.fetchone()
            earned = row[0] if row else 0
        async with db.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=?", (tg_id,)) as cursor:
            count = (await cursor.fetchone())[0]
    return {"total_earned": earned, "referrals_count": count, "percent": REFERRAL_PERCENT}

# ==================== CRASH ====================
crash_state = {"status": "waiting", "round_id": "", "crash_point": 1.0, "hash": "", "start_time": 0, "bets": {}, "history": [], "timer_ends": 0, "multiplier": 1.0, "crashed": False}

async def crash_loop():
    global crash_state
    while True:
        crash_state["status"] = "betting"
        crash_state["round_id"] = str(uuid.uuid4())[:8]
        crash_state["bets"] = {}
        crash_state["crash_point"], crash_state["hash"] = generate_crash_point()
        crash_state["multiplier"] = 1.0
        crash_state["crashed"] = False
        crash_state["timer_ends"] = time.time() + CRASH_BETTING_TIME
        await sio.emit("crash_state", {"status": "betting", "round_id": crash_state["round_id"], "hash": crash_state["hash"], "timer": CRASH_BETTING_TIME, "bets": len(crash_state["bets"])})
        await asyncio.sleep(CRASH_BETTING_TIME)
        if not crash_state["bets"]:
            crash_state["status"] = "cooldown"
            await sio.emit("crash_state", {"status": "cooldown", "timer": CRASH_COOLDOWN, "history": crash_state["history"][:10]})
            await asyncio.sleep(CRASH_COOLDOWN)
            continue
        crash_state["status"] = "flying"
        crash_state["start_time"] = time.time()
        await sio.emit("crash_start", {"round_id": crash_state["round_id"], "hash": crash_state["hash"], "total_bets": len(crash_state["bets"]), "total_amount": sum(b["amount"] for b in crash_state["bets"].values())})
        last_sent = 1.0
        while True:
            elapsed = time.time() - crash_state["start_time"]
            current = 1.0 * math.exp(CRASH_SPEED * elapsed)
            crash_state["multiplier"] = current
            if current >= crash_state["crash_point"]:
                crash_state["crashed"] = True
                crash_state["status"] = "crashed"
                crash_state["history"].insert(0, crash_state["crash_point"])
                if len(crash_state["history"]) > 20:
                    crash_state["history"] = crash_state["history"][:20]
                results = []
                for bet in crash_state["bets"].values():
                    results.append({"username": bet["username"], "amount": bet["amount"], "cashed": bet.get("cashed", False), "win": int(bet["amount"] * bet.get("cashed_at", 1)) if bet.get("cashed", False) else 0})
                await sio.emit("crash_end", {"crash_point": crash_state["crash_point"], "hash": crash_state["hash"], "server_seed": SERVER_SEED, "nonce": crash_nonce, "bets": results})
                break
            if abs(current - last_sent) >= 0.01:
                await sio.emit("crash_multiplier", {"multiplier": round(current, 2), "elapsed": elapsed})
                last_sent = current
            await asyncio.sleep(0.05)
        crash_state["status"] = "cooldown"
        await sio.emit("crash_state", {"status": "cooldown", "timer": CRASH_COOLDOWN, "history": crash_state["history"][:10]})
        await asyncio.sleep(CRASH_COOLDOWN)

@sio.event
async def connect(sid, environ):
    await sio.emit("crash_state", {"status": crash_state["status"], "round_id": crash_state["round_id"], "timer": max(0, int(crash_state["timer_ends"] - time.time())), "history": crash_state["history"][:10], "bets": len(crash_state["bets"])}, to=sid)

@sio.event
async def place_bet(sid, data):
    try:
        tg_id = int(data.get("tg_id", 0))
    except:
        return await sio.emit("error", {"message": "Invalid ID"}, to=sid)
    if crash_state["status"] != "betting":
        return await sio.emit("error", {"message": "Bets closed"}, to=sid)
    try:
        amount = int(data.get("amount", 0))
    except:
        return await sio.emit("error", {"message": "Invalid amount"}, to=sid)
    if amount < CRASH_MIN_BET or amount > CRASH_MAX_BET:
        return await sio.emit("error", {"message": f"Bet {CRASH_MIN_BET}-{CRASH_MAX_BET}"}, to=sid)
    user_data = await get_user(tg_id)
    if user_data["balance"] < amount:
        return await sio.emit("error", {"message": "Insufficient balance"}, to=sid)
    key = f"{tg_id}:{crash_state['round_id']}"
    if key in crash_state["bets"]:
        return await sio.emit("error", {"message": "Already placed"}, to=sid)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance=balance-?, games_played=games_played+1 WHERE tg_id=?", (amount, tg_id))
        await db.commit()
    crash_state["bets"][key] = {"tg_id": tg_id, "amount": amount, "username": data.get("username", "Player"), "cashed": False, "cashed_at": 0}
    await sio.emit("bet_placed", {"username": data.get("username", "Player"), "amount": amount, "balance": (await get_user(tg_id))["balance"]})
    await sio.emit("bets_update", {"count": len(crash_state["bets"]), "total": sum(b["amount"] for b in crash_state["bets"].values())})

@sio.event
async def cashout(sid, data):
    try:
        tg_id = int(data.get("tg_id", 0))
    except:
        return await sio.emit("error", {"message": "Invalid ID"}, to=sid)
    if crash_state["status"] != "flying":
        return await sio.emit("error", {"message": "Not flying"}, to=sid)
    key = f"{tg_id}:{crash_state['round_id']}"
    bet = crash_state["bets"].get(key)
    if not bet:
        return await sio.emit("error", {"message": "No bet"}, to=sid)
    if bet["cashed"]:
        return await sio.emit("error", {"message": "Already cashed"}, to=sid)
    win = int(bet["amount"] * crash_state["multiplier"])
    bet["cashed"] = True
    bet["cashed_at"] = crash_state["multiplier"]
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance=balance+?, wins=wins+1 WHERE tg_id=?", (win, tg_id))
        await db.commit()
    await sio.emit("cashout_success", {"username": bet["username"], "amount": bet["amount"], "multiplier": round(crash_state["multiplier"], 2), "win": win, "balance": (await get_user(tg_id))["balance"]})

# ==================== STARTUP ====================
@app.on_event("startup")
async def startup():
    await init_db()
    asyncio.create_task(crash_loop())

# ==================== MAIN ====================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(socket_app, host="0.0.0.0", port=8000)
