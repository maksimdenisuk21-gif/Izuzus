import os
import random
import asyncio
import sqlite3
import time
import json
import hashlib
import hmac
import urllib.parse
import math
from typing import Optional, Dict, List, Any
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
import socketio
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# ==========================================
# 1. ГЛОБАЛЬНЫЕ НАСТРОЙКИ
# ==========================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_TG_ID = int(os.getenv("ADMIN_TG_ID", "0"))

START_BALANCE: int = 20
CRASH_HOUSE_EDGE: float = 0.08
MINES_HOUSE_EDGE: float = 0.10
COLOR_DICE_HOUSE_EDGE: float = 0.05
WITHDRAW_FEE: float = 0.05
MIN_WITHDRAW: int = 60
MAX_WITHDRAW: int = 50000

DB_NAME = "database.db"

# ==========================================
# 2. ДАННЫЕ КЕЙСОВ
# ==========================================

# UC Ящики (10 шт)
STAR_CASE_PRICES = {
    "star_case_1": 25,
    "star_case_2": 75,
    "star_case_3": 200,
    "star_case_4": 450,
    "star_case_5": 900,
    "star_case_6": 1800,
    "star_case_7": 3500,
    "star_case_8": 6000,
    "star_case_9": 10000,
    "star_case_10": 25000
}

STAR_CASE_DROPS = {
    "star_case_1": [
        {"id": "s1_1", "name": "⭐ 5 UC", "price": 5, "rarity": "common"},
        {"id": "s1_2", "name": "⭐ 12 UC", "price": 12, "rarity": "common"},
        {"id": "s1_3", "name": "⭐ 20 UC", "price": 20, "rarity": "common"},
        {"id": "s1_4", "name": "⭐ 35 UC", "price": 35, "rarity": "rare"},
        {"id": "s1_5", "name": "⭐ 60 UC", "price": 60, "rarity": "rare"},
        {"id": "s1_6", "name": "⭐ 120 UC", "price": 120, "rarity": "epic"}
    ],
    "star_case_2": [
        {"id": "s2_1", "name": "⭐ 15 UC", "price": 15, "rarity": "common"},
        {"id": "s2_2", "name": "⭐ 35 UC", "price": 35, "rarity": "common"},
        {"id": "s2_3", "name": "⭐ 65 UC", "price": 65, "rarity": "rare"},
        {"id": "s2_4", "name": "⭐ 110 UC", "price": 110, "rarity": "rare"},
        {"id": "s2_5", "name": "⭐ 200 UC", "price": 200, "rarity": "epic"},
        {"id": "s2_6", "name": "⭐ 400 UC", "price": 400, "rarity": "epic"}
    ],
    "star_case_3": [
        {"id": "s3_1", "name": "⭐ 40 UC", "price": 40, "rarity": "common"},
        {"id": "s3_2", "name": "⭐ 90 UC", "price": 90, "rarity": "rare"},
        {"id": "s3_3", "name": "⭐ 175 UC", "price": 175, "rarity": "rare"},
        {"id": "s3_4", "name": "⭐ 300 UC", "price": 300, "rarity": "epic"},
        {"id": "s3_5", "name": "⭐ 550 UC", "price": 550, "rarity": "legendary"},
        {"id": "s3_6", "name": "⭐ 1100 UC", "price": 1100, "rarity": "legendary"}
    ],
    "star_case_4": [
        {"id": "s4_1", "name": "⭐ 90 UC", "price": 90, "rarity": "common"},
        {"id": "s4_2", "name": "⭐ 200 UC", "price": 200, "rarity": "rare"},
        {"id": "s4_3", "name": "⭐ 380 UC", "price": 380, "rarity": "epic"},
        {"id": "s4_4", "name": "⭐ 650 UC", "price": 650, "rarity": "epic"},
        {"id": "s4_5", "name": "⭐ 1200 UC", "price": 1200, "rarity": "legendary"},
        {"id": "s4_6", "name": "⭐ 2500 UC", "price": 2500, "rarity": "mythic"}
    ],
    "star_case_5": [
        {"id": "s5_1", "name": "⭐ 180 UC", "price": 180, "rarity": "common"},
        {"id": "s5_2", "name": "⭐ 400 UC", "price": 400, "rarity": "rare"},
        {"id": "s5_3", "name": "⭐ 780 UC", "price": 780, "rarity": "epic"},
        {"id": "s5_4", "name": "⭐ 1350 UC", "price": 1350, "rarity": "legendary"},
        {"id": "s5_5", "name": "⭐ 2500 UC", "price": 2500, "rarity": "legendary"},
        {"id": "s5_6", "name": "⭐ 5000 UC", "price": 5000, "rarity": "mythic"}
    ],
    "star_case_6": [
        {"id": "s6_1", "name": "⭐ 360 UC", "price": 360, "rarity": "rare"},
        {"id": "s6_2", "name": "⭐ 800 UC", "price": 800, "rarity": "epic"},
        {"id": "s6_3", "name": "⭐ 1500 UC", "price": 1500, "rarity": "epic"},
        {"id": "s6_4", "name": "⭐ 2700 UC", "price": 2700, "rarity": "legendary"},
        {"id": "s6_5", "name": "⭐ 5000 UC", "price": 5000, "rarity": "mythic"},
        {"id": "s6_6", "name": "⭐ 10000 UC", "price": 10000, "rarity": "mythic"}
    ],
    "star_case_7": [
        {"id": "s7_1", "name": "⭐ 700 UC", "price": 700, "rarity": "rare"},
        {"id": "s7_2", "name": "⭐ 1500 UC", "price": 1500, "rarity": "epic"},
        {"id": "s7_3", "name": "⭐ 3000 UC", "price": 3000, "rarity": "legendary"},
        {"id": "s7_4", "name": "⭐ 5200 UC", "price": 5200, "rarity": "legendary"},
        {"id": "s7_5", "name": "⭐ 10000 UC", "price": 10000, "rarity": "mythic"},
        {"id": "s7_6", "name": "⭐ 20000 UC", "price": 20000, "rarity": "mythic"}
    ],
    "star_case_8": [
        {"id": "s8_1", "name": "⭐ 1200 UC", "price": 1200, "rarity": "rare"},
        {"id": "s8_2", "name": "⭐ 2700 UC", "price": 2700, "rarity": "epic"},
        {"id": "s8_3", "name": "⭐ 5000 UC", "price": 5000, "rarity": "legendary"},
        {"id": "s8_4", "name": "⭐ 9000 UC", "price": 9000, "rarity": "legendary"},
        {"id": "s8_5", "name": "⭐ 18000 UC", "price": 18000, "rarity": "mythic"},
        {"id": "s8_6", "name": "⭐ 35000 UC", "price": 35000, "rarity": "mythic"}
    ],
    "star_case_9": [
        {"id": "s9_1", "name": "⭐ 2000 UC", "price": 2000, "rarity": "epic"},
        {"id": "s9_2", "name": "⭐ 4500 UC", "price": 4500, "rarity": "epic"},
        {"id": "s9_3", "name": "⭐ 8500 UC", "price": 8500, "rarity": "legendary"},
        {"id": "s9_4", "name": "⭐ 15000 UC", "price": 15000, "rarity": "legendary"},
        {"id": "s9_5", "name": "⭐ 30000 UC", "price": 30000, "rarity": "mythic"},
        {"id": "s9_6", "name": "⭐ 60000 UC", "price": 60000, "rarity": "mythic"}
    ],
    "star_case_10": [
        {"id": "s10_1", "name": "⭐ 5000 UC", "price": 5000, "rarity": "epic"},
        {"id": "s10_2", "name": "⭐ 11000 UC", "price": 11000, "rarity": "legendary"},
        {"id": "s10_3", "name": "⭐ 22000 UC", "price": 22000, "rarity": "legendary"},
        {"id": "s10_4", "name": "⭐ 40000 UC", "price": 40000, "rarity": "mythic"},
        {"id": "s10_5", "name": "⭐ 75000 UC", "price": 75000, "rarity": "mythic"},
        {"id": "s10_6", "name": "⭐ 150000 UC", "price": 150000, "rarity": "mythic"}
    ]
}

# NFT Скины (10 шт)
NFT_CASE_PRICES = {
    "nft_case_1": 50,
    "nft_case_2": 150,
    "nft_case_3": 350,
    "nft_case_4": 800,
    "nft_case_5": 1500,
    "nft_case_6": 3000,
    "nft_case_7": 5500,
    "nft_case_8": 9000,
    "nft_case_9": 15000,
    "nft_case_10": 30000
}

NFT_CASE_DROPS = {
    "nft_case_1": [
        {"id": "n1_1", "name": "🎽 Brown Shirt", "price": 10, "rarity": "common"},
        {"id": "n1_2", "name": "🧢 Grey Cap", "price": 22, "rarity": "common"},
        {"id": "n1_3", "name": "👖 Cargo Pants", "price": 40, "rarity": "common"},
        {"id": "n1_4", "name": "🎒 Level 1 Backpack", "price": 70, "rarity": "rare"},
        {"id": "n1_5", "name": "🪖 Steel Helmet", "price": 130, "rarity": "rare"},
        {"id": "n1_6", "name": "🥾 Military Boots", "price": 250, "rarity": "epic"}
    ],
    "nft_case_2": [
        {"id": "n2_1", "name": "🧣 Red Scarf", "price": 30, "rarity": "common"},
        {"id": "n2_2", "name": "🧤 Tactical Gloves", "price": 65, "rarity": "rare"},
        {"id": "n2_3", "name": "🦺 Police Vest", "price": 120, "rarity": "rare"},
        {"id": "n2_4", "name": "🎽 Sports Top", "price": 200, "rarity": "epic"},
        {"id": "n2_5", "name": "👒 Straw Hat", "price": 380, "rarity": "epic"},
        {"id": "n2_6", "name": "🪖 Level 2 Helmet", "price": 750, "rarity": "legendary"}
    ],
    "nft_case_3": [
        {"id": "n3_1", "name": "🧥 Leather Jacket", "price": 70, "rarity": "rare"},
        {"id": "n3_2", "name": "👖 Jeans", "price": 150, "rarity": "rare"},
        {"id": "n3_3", "name": "👟 Sneakers", "price": 280, "rarity": "epic"},
        {"id": "n3_4", "name": "🎒 Level 2 Backpack", "price": 500, "rarity": "epic"},
        {"id": "n3_5", "name": "🛡️ Riot Shield", "price": 900, "rarity": "legendary"},
        {"id": "n3_6", "name": "🪖 Level 3 Helmet", "price": 1800, "rarity": "legendary"}
    ],
    "nft_case_4": [
        {"id": "n4_1", "name": "🥋 Martial Arts", "price": 160, "rarity": "rare"},
        {"id": "n4_2", "name": "🦺 Ghillie Suit", "price": 350, "rarity": "epic"},
        {"id": "n4_3", "name": "🪖 Spetsnaz Helmet", "price": 650, "rarity": "epic"},
        {"id": "n4_4", "name": "🎒 Level 3 Backpack", "price": 1100, "rarity": "legendary"},
        {"id": "n4_5", "name": "🔫 M416 Skin", "price": 2100, "rarity": "legendary"},
        {"id": "n4_6", "name": "🎯 AWM Skin", "price": 4000, "rarity": "mythic"}
    ],
    "nft_case_5": [
        {"id": "n5_1", "name": "👑 Crown", "price": 300, "rarity": "epic"},
        {"id": "n5_2", "name": "🦅 Eagle Mask", "price": 650, "rarity": "epic"},
        {"id": "n5_3", "name": "🐯 Tiger Suit", "price": 1200, "rarity": "legendary"},
        {"id": "n5_4", "name": "🤖 Robot Suit", "price": 2100, "rarity": "legendary"},
        {"id": "n5_5", "name": "🔥 Flame Jacket", "price": 3800, "rarity": "mythic"},
        {"id": "n5_6", "name": "💎 Diamond Helmet", "price": 7500, "rarity": "mythic"}
    ],
    "nft_case_6": [
        {"id": "n6_1", "name": "🦺 Golden Ghillie", "price": 600, "rarity": "epic"},
        {"id": "n6_2", "name": "🔫 Golden AKM", "price": 1300, "rarity": "legendary"},
        {"id": "n6_3", "name": "🎯 Golden AWM", "price": 2400, "rarity": "legendary"},
        {"id": "n6_4", "name": "👑 Royal Crown", "price": 4200, "rarity": "mythic"},
        {"id": "n6_5", "name": "🐲 Dragon Suit", "price": 7500, "rarity": "mythic"},
        {"id": "n6_6", "name": "⭐ Legendary Set", "price": 15000, "rarity": "mythic"}
    ],
    "nft_case_7": [
        {"id": "n7_1", "name": "⚡ Cyber Visor", "price": 1100, "rarity": "epic"},
        {"id": "n7_2", "name": "🦾 Bionic Arm", "price": 2400, "rarity": "legendary"},
        {"id": "n7_3", "name": "🏍️ Neon Mask", "price": 4500, "rarity": "legendary"},
        {"id": "n7_4", "name": "🛸 Alien Armor", "price": 8000, "rarity": "mythic"},
        {"id": "n7_5", "name": "🌌 Galaxy Suit", "price": 14000, "rarity": "mythic"},
        {"id": "n7_6", "name": "⚡ Cyberpunk Set", "price": 28000, "rarity": "mythic"}
    ],
    "nft_case_8": [
        {"id": "n8_1", "name": "❄️ Ice Katana", "price": 1800, "rarity": "epic"},
        {"id": "n8_2", "name": "🧊 Frost Vest", "price": 3900, "rarity": "legendary"},
        {"id": "n8_3", "name": "🏔️ Glacier M416", "price": 7200, "rarity": "legendary"},
        {"id": "n8_4", "name": "❄️ Blizzard Suit", "price": 13000, "rarity": "mythic"},
        {"id": "n8_5", "name": "🧊 Sub-Zero Crown", "price": 23000, "rarity": "mythic"},
        {"id": "n8_6", "name": "❄️ Frost Lord Set", "price": 45000, "rarity": "mythic"}
    ],
    "nft_case_9": [
        {"id": "n9_1", "name": "🔥 Flame Dagger", "price": 3000, "rarity": "legendary"},
        {"id": "n9_2", "name": "🌋 Volcanic Helmet", "price": 6500, "rarity": "legendary"},
        {"id": "n9_3", "name": "💥 Infernal Armor", "price": 12000, "rarity": "mythic"},
        {"id": "n9_4", "name": "🔥 Magma M416", "price": 21000, "rarity": "mythic"},
        {"id": "n9_5", "name": "☀️ Phoenix Wings", "price": 38000, "rarity": "mythic"},
        {"id": "n9_6", "name": "🔥 Demon King Set", "price": 75000, "rarity": "mythic"}
    ],
    "nft_case_10": [
        {"id": "n10_1", "name": "🌌 Nebula Dagger", "price": 6000, "rarity": "legendary"},
        {"id": "n10_2", "name": "☄️ Meteor Armor", "price": 13000, "rarity": "mythic"},
        {"id": "n10_3", "name": "🪐 Saturn Ring", "price": 24000, "rarity": "mythic"},
        {"id": "n10_4", "name": "✨ Cosmic AWM", "price": 42000, "rarity": "mythic"},
        {"id": "n10_5", "name": "🌟 Star God Wings", "price": 75000, "rarity": "mythic"},
        {"id": "n10_6", "name": "🌌 Eternity Set", "price": 150000, "rarity": "mythic"}
    ]
}

# ==========================================
# 3. FASTAPI + SOCKET.IO
# ==========================================

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(title="PUBG Elite", version="2.0.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
socket_app = socketio.ASGIApp(sio, app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================
# 4. БАЗА ДАННЫХ
# ==========================================

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        tg_id INTEGER PRIMARY KEY,
        username TEXT NOT NULL,
        balance INTEGER DEFAULT 20,
        total_spent INTEGER DEFAULT 0,
        inventory TEXT DEFAULT '[]',
        is_admin BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        type TEXT NOT NULL,
        amount INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS withdraws (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        requisites TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Добавляем админа если указан
    if ADMIN_TG_ID:
        c.execute("INSERT OR IGNORE INTO users (tg_id, username, balance, is_admin) VALUES (?, ?, ?, ?)",
                  (ADMIN_TG_ID, "Admin", 10000, True))
    
    conn.commit()
    conn.close()

init_db()

# ==========================================
# 5. ФУНКЦИИ БД
# ==========================================

def db_get_user(tg_id: int) -> Optional[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def db_create_user(tg_id: int, username: str) -> Dict:
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO users (tg_id, username, balance, total_spent, inventory, is_admin) VALUES (?, ?, ?, ?, ?, ?)",
        (tg_id, username, START_BALANCE, 0, json.dumps([]), tg_id == ADMIN_TG_ID)
    )
    conn.commit()
    conn.close()
    return db_get_user(tg_id)

def db_update_balance(tg_id: int, amount: int, tx_type: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (amount, tg_id))
    c.execute("INSERT INTO transactions (user_id, type, amount) VALUES (?, ?, ?)", (tg_id, tx_type, amount))
    conn.commit()
    conn.close()

def db_update_inventory(tg_id: int, inventory: List):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE users SET inventory = ? WHERE tg_id = ?", (json.dumps(inventory), tg_id))
    conn.commit()
    conn.close()

# ==========================================
# 6. COLOR DICE
# ==========================================

COLOR_MULTIPLIERS = {
    "red": 2.0 * (1 - COLOR_DICE_HOUSE_EDGE),
    "black": 2.0 * (1 - COLOR_DICE_HOUSE_EDGE),
    "green": 25.0 * (1 - COLOR_DICE_HOUSE_EDGE)
}

def roll_color_dice():
    r = random.randint(0, 99)
    if r < 4:
        return "green"
    elif r < 52:
        return "red"
    return "black"

# ==========================================
# 7. MINES
# ==========================================

MINES_GRID_SIZE = 4
active_mines_games: Dict[str, Dict] = {}

MINES_MULTIPLIERS = {
    1: [1.03, 1.12, 1.23, 1.35, 1.50, 1.68, 1.90, 2.18, 2.54, 3.00, 3.50, 4.00, 4.50, 5.00],
    2: [1.08, 1.25, 1.45, 1.70, 2.00, 2.40, 2.90, 3.50, 4.20, 5.00, 6.00, 7.50, 9.00],
    3: [1.15, 1.40, 1.75, 2.25, 2.95, 4.00, 5.60, 8.00, 10.00, 13.00, 17.00, 22.00],
    4: [1.25, 1.60, 2.10, 2.85, 4.00, 5.80, 8.50, 12.50, 18.00, 25.00, 35.00],
    5: [1.30, 1.80, 2.60, 3.90, 6.00, 9.80, 16.00, 20.00, 28.00, 40.00],
    7: [1.50, 2.50, 4.50, 8.50, 17.00, 30.00, 50.00, 80.00],
    10: [2.00, 5.00, 15.00, 45.00, 100.00, 200.00]
}

def generate_mines_grid(mines_count: int):
    total = MINES_GRID_SIZE * MINES_GRID_SIZE
    grid = [0] * total
    for pos in random.sample(range(total), mines_count):
        grid[pos] = 1
    return grid

# ==========================================
# 8. CRASH STATE
# ==========================================

crash_state = {
    "status": "waiting",
    "multiplier": 1.0,
    "crash_point": 1.0,
    "bets": {},
    "history": []
}

CRASH_BETTING_TIME = 6
CRASH_COOLDOWN = 3
CRASH_SPEED = 0.08

async def crash_loop():
    global crash_state
    while True:
        crash_state["status"] = "betting"
        crash_state["multiplier"] = 1.0
        
        # Генерация crash point
        e = random.uniform(0.01, 1.0)
        crash_point = max(1.01, round((1.0 - CRASH_HOUSE_EDGE) / e, 2))
        if crash_point > 50.0:
            crash_point = 50.0
        crash_state["crash_point"] = crash_point

        # Обратный отсчёт
        for t in range(CRASH_BETTING_TIME, 0, -1):
            await sio.emit('crash_state', {'status': 'betting', 'timer': t})
            await asyncio.sleep(1)

        if len(crash_state["bets"]) == 0:
            await asyncio.sleep(CRASH_COOLDOWN)
            continue

        crash_state["status"] = "flying"
        await sio.emit('crash_start', {})
        
        current = 1.0
        while current < crash_state["crash_point"]:
            await asyncio.sleep(0.1)
            current = round(current + 0.02 + (current * 0.015), 2)
            crash_state["multiplier"] = current
            await sio.emit('crash_multiplier', {'multiplier': current})

        crash_state["status"] = "crashed"
        crash_state["history"].insert(0, crash_state["crash_point"])
        crash_state["history"] = crash_state["history"][:20]
        await sio.emit('crash_end', {'crash_point': crash_state["crash_point"]})
        crash_state["bets"].clear()
        await asyncio.sleep(CRASH_COOLDOWN)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(crash_loop())

# ==========================================
# 9. TELEGRAM AUTH
# ==========================================

def verify_telegram_data(authorization: str = Header(None)):
    if not authorization:
        return {"id": 12345678, "first_name": "TestUser"}
    
    if not BOT_TOKEN:
        return {"id": 12345678, "first_name": "TestUser"}
    
    try:
        init_data = urllib.parse.parse_qs(authorization)
        hash_value = init_data.get('hash', [None])[0]
        if not hash_value:
            raise HTTPException(status_code=401, detail="Invalid InitData")
        
        sorted_data = sorted([f"{k}={v[0]}" for k, v in init_data.items() if k != 'hash'])
        data_check_string = "\n".join(sorted_data)
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if calculated_hash != hash_value:
            raise HTTPException(status_code=401, detail="Validation Failed")
        
        user_data = json.loads(init_data.get('user', ['{}'])[0])
        return user_data
    except Exception as e:
        return {"id": 12345678, "first_name": "TestUser"}

# ==========================================
# 10. SOCKET.IO СОБЫТИЯ
# ==========================================

@sio.event
async def connect(sid, environ):
    await sio.emit('crash_state', {
        'status': crash_state["status"],
        'multiplier': crash_state["multiplier"]
    }, to=sid)

@sio.event
async def place_bet(sid, data):
    bet_amount = data.get('bet_amount', 0)
    
    if crash_state["status"] != "flying":
        await sio.emit('error', {'message': 'Игра не запущена'}, to=sid)
        return
    
    if bet_amount < 5 or bet_amount > 5000:
        await sio.emit('error', {'message': 'Ставка 5-5000 UC'}, to=sid)
        return
    
    # Используем тестовый ID для Socket
    tg_id = 12345678
    user = db_get_user(tg_id)
    if not user or user['balance'] < bet_amount:
        await sio.emit('error', {'message': 'Недостаточно средств'}, to=sid)
        return
    
    db_update_balance(tg_id, -bet_amount, "crash_bet")
    crash_state["bets"][tg_id] = bet_amount
    await sio.emit('bet_placed', {'amount': bet_amount})

@sio.event
async def cashout(sid, data):
    tg_id = 12345678
    
    if crash_state["status"] != "flying":
        await sio.emit('error', {'message': 'Игра не запущена'}, to=sid)
        return
    
    if tg_id not in crash_state["bets"]:
        await sio.emit('error', {'message': 'Ставка не найдена'}, to=sid)
        return
    
    bet = crash_state["bets"][tg_id]
    win_amount = int(bet * crash_state["multiplier"])
    
    db_update_balance(tg_id, win_amount, "crash_win")
    del crash_state["bets"][tg_id]
    
    await sio.emit('cashout_success', {
        'amount': win_amount,
        'multiplier': crash_state["multiplier"]
    })

# ==========================================
# 11. API ЭНДПОИНТЫ
# ==========================================

@app.get("/api/profile")
async def get_profile(user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id', 12345678)
    username = user.get('first_name', 'Игрок')
    user_data = db_create_user(tg_id, username)
    result = dict(user_data)
    result['inventory'] = json.loads(result['inventory'])
    result['tg_id'] = tg_id
    return result

@app.post("/api/case/open")
async def open_case(req: Request, user: dict = Depends(verify_telegram_data)):
    data = await req.json()
    case_type = data.get('case_type')
    
    tg_id = user.get('id', 12345678)
    user_data = db_get_user(tg_id)
    
    if case_type in STAR_CASE_PRICES:
        price = STAR_CASE_PRICES[case_type]
        drops = STAR_CASE_DROPS[case_type]
    elif case_type in NFT_CASE_PRICES:
        price = NFT_CASE_PRICES[case_type]
        drops = NFT_CASE_DROPS[case_type]
    else:
        raise HTTPException(status_code=400, detail="Ящик не найден")
    
    if user_data['balance'] < price:
        raise HTTPException(status_code=400, detail="Недостаточно UC")
    
    db_update_balance(tg_id, -price, "case_open")
    
    win_item = random.choice(drops)
    inv = json.loads(user_data['inventory'])
    inv.append(win_item)
    
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE users SET inventory = ?, total_spent = total_spent + ? WHERE tg_id = ?", 
              (json.dumps(inv), price, tg_id))
    conn.commit()
    conn.close()
    
    return {
        "reward_name": win_item['name'],
        "win_item": win_item,
        "balance": user_data['balance'] - price
    }

@app.post("/api/inventory/sell")
async def sell_item(req: Request, user: dict = Depends(verify_telegram_data)):
    data = await req.json()
    item_index = data.get('item_index')
    
    tg_id = user.get('id', 12345678)
    user_data = db_get_user(tg_id)
    
    inv = json.loads(user_data['inventory'])
    if item_index < 0 or item_index >= len(inv):
        raise HTTPException(status_code=400, detail="Предмет не найден")
    
    item = inv.pop(item_index)
    sell_price = int(item['price'] * 0.5)
    
    db_update_balance(tg_id, sell_price, "sell_item")
    db_update_inventory(tg_id, inv)
    
    return {"sold_for": sell_price, "new_balance": user_data['balance'] + sell_price}

@app.post("/api/color_dice/roll")
async def roll_dice_api(req: Request, user: dict = Depends(verify_telegram_data)):
    data = await req.json()
    bet_amount = data.get('bet_amount')
    color = data.get('color')
    
    tg_id = user.get('id', 12345678)
    user_data = db_get_user(tg_id)
    
    if bet_amount < 10:
        raise HTTPException(status_code=400, detail="Минимальная ставка 10 UC")
    if color not in ['red', 'black', 'green']:
        raise HTTPException(status_code=400, detail="Неверный цвет")
    if user_data['balance'] < bet_amount:
        raise HTTPException(status_code=400, detail="Недостаточно UC")
    
    db_update_balance(tg_id, -bet_amount, "dice_bet")
    
    dropped = roll_color_dice()
    win = (dropped == color)
    win_amount = 0
    
    if win:
        win_amount = int(bet_amount * COLOR_MULTIPLIERS[color])
        db_update_balance(tg_id, win_amount, "dice_win")
    
    new_balance = user_data['balance'] - bet_amount + win_amount
    
    return {
        "win": win,
        "dropped_color": dropped,
        "win_amount": win_amount,
        "new_balance": new_balance
    }

@app.post("/api/mines/start")
async def mines_start(req: Request, user: dict = Depends(verify_telegram_data)):
    data = await req.json()
    bet_amount = data.get('bet_amount')
    mines_count = data.get('mines_count')
    
    tg_id = user.get('id', 12345678)
    user_data = db_get_user(tg_id)
    
    if bet_amount < 5:
        raise HTTPException(status_code=400, detail="Минимальная ставка 5 UC")
    if mines_count < 1 or mines_count > 15:
        raise HTTPException(status_code=400, detail="Мины 1-15")
    if user_data['balance'] < bet_amount:
        raise HTTPException(status_code=400, detail="Недостаточно UC")
    
    db_update_balance(tg_id, -bet_amount, "mines_bet")
    
    grid = generate_mines_grid(mines_count)
    game_id = f"mines_{tg_id}_{int(time.time()*1000)}"
    
    active_mines_games[game_id] = {
        "user_id": tg_id,
        "bet": bet_amount,
        "mines_count": mines_count,
        "grid": grid,
        "opened": [],
        "step": 0
    }
    
    return {
        "game_id": game_id,
        "mines_count": mines_count,
        "bet": bet_amount,
        "balance": user_data['balance'] - bet_amount
    }

@app.post("/api/mines/open")
async def mines_open(req: Request, user: dict = Depends(verify_telegram_data)):
    data = await req.json()
    game_id = data.get('game_id')
    cell_index = data.get('cell_index')
    
    game = active_mines_games.get(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Игра не найдена")
    
    if cell_index in game["opened"]:
        raise HTTPException(status_code=400, detail="Ячейка уже открыта")
    
    if game["grid"][cell_index] == 1:
        del active_mines_games[game_id]
        return {
            "game_over": True,
            "hit_mine": True,
            "cell_index": cell_index,
            "opened": game["opened"]
        }
    
    game["opened"].append(cell_index)
    game["step"] += 1
    
    mults = MINES_MULTIPLIERS.get(game["mines_count"], [1.05])
    step_idx = min(game["step"] - 1, len(mults) - 1)
    current_mult = mults[step_idx]
    
    return {
        "game_over": False,
        "hit_mine": False,
        "cell_index": cell_index,
        "step": game["step"],
        "current_multiplier": current_mult
    }

@app.post("/api/mines/cashout")
async def mines_cashout(req: Request, user: dict = Depends(verify_telegram_data)):
    data = await req.json()
    game_id = data.get('game_id')
    
    game = active_mines_games.get(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Игра не найдена")
    
    if game["step"] == 0:
        raise HTTPException(status_code=400, detail="Откройте хотя бы одну клетку")
    
    mults = MINES_MULTIPLIERS.get(game["mines_count"], [1.05])
    step_idx = min(game["step"] - 1, len(mults) - 1)
    final_mult = mults[step_idx]
    win_amount = int(game["bet"] * final_mult)
    
    db_update_balance(game["user_id"], win_amount, "mines_win")
    del active_mines_games[game_id]
    
    return {
        "success": True,
        "win_amount": win_amount,
        "multiplier": final_mult,
        "profit": win_amount - game["bet"]
    }

@app.post("/api/free_case/claim")
async def claim_free_case(user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id', 12345678)
    
    # Проверяем, не использовал ли уже сегодня
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT created_at FROM transactions WHERE user_id = ? AND type = 'free_case' ORDER BY created_at DESC LIMIT 1", (tg_id,))
    row = c.fetchone()
    conn.close()
    
    if row:
        try:
            last_ts = time.mktime(time.strptime(row[0], "%Y-%m-%d %H:%M:%S"))
            if time.time() - last_ts < 86400:
                raise HTTPException(status_code=400, detail="Бесплатный ящик доступен раз в 24 часа")
        except:
            pass
    
    drops = STAR_CASE_DROPS["star_case_1"]
    win_item = random.choice(drops)
    
    user_data = db_get_user(tg_id)
    inv = json.loads(user_data['inventory'])
    inv.append(win_item)
    db_update_inventory(tg_id, inv)
    db_update_balance(tg_id, 0, "free_case")
    
    return {"success": True, "reward": win_item['name']}

@app.post("/api/withdraw/create")
async def create_withdraw(req: Request, user: dict = Depends(verify_telegram_data)):
    data = await req.json()
    amount = data.get('amount')
    requisites = data.get('requisites')
    
    tg_id = user.get('id', 12345678)
    user_data = db_get_user(tg_id)
    
    if amount < MIN_WITHDRAW:
        raise HTTPException(status_code=400, detail=f"Минимальный вывод {MIN_WITHDRAW} UC")
    if user_data['balance'] < amount:
        raise HTTPException(status_code=400, detail="Недостаточно средств")
    
    db_update_balance(tg_id, -amount, "withdraw_request")
    
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO withdraws (user_id, amount, requisites) VALUES (?, ?, ?)", (tg_id, amount, requisites))
    conn.commit()
    conn.close()
    
    return {"status": "success", "message": "Заявка отправлена"}

@app.post("/api/deposit")
async def deposit(req: Request, user: dict = Depends(verify_telegram_data)):
    data = await req.json()
    amount = data.get('amount')
    
    tg_id = user.get('id', 12345678)
    
    if amount < 10:
        raise HTTPException(status_code=400, detail="Минимальное пополнение 10 UC")
    
    db_update_balance(tg_id, amount, "deposit")
    return {"status": "success", "amount": amount}

# ==========================================
# 12. РАЗДАЧА HTML
# ==========================================

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    if not os.path.exists("index.html"):
        return HTMLResponse("<h2>❌ index.html не найден</h2>", status_code=404)
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

# ==========================================
# 13. ЗАПУСК
# ==========================================

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
