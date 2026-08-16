import os
import random
import asyncio
import sqlite3
import time
import json
from typing import Optional, Dict, List, Any
from fastapi import FastAPI, HTTPException, Header, Request, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
import socketio
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# ==========================================
# 1. ГЛОБАЛЬНЫЕ НАСТРОЙКИ И ЭКОНОМИКА
# ==========================================

START_BALANCE: int = 5
CRASH_HOUSE_EDGE: float = 0.08
MINES_HOUSE_EDGE: float = 0.10
UPGRADE_HOUSE_EDGE: float = 0.10
COINFLIP_HOUSE_EDGE: float = 0.05
WITHDRAW_FEE: float = 0.05
MIN_WITHDRAW: int = 100
MAX_WITHDRAW: int = 50000

# Множители для Mines (5x5 поле)
MINES_MULTIPLIERS: Dict[int, List[float]] = {
    1: [1.03, 1.12, 1.23, 1.35, 1.50, 1.68, 1.90, 2.18, 2.54, 3.00, 3.50, 4.00, 4.50, 5.00],
    2: [1.08, 1.25, 1.45, 1.70, 2.00, 2.40, 2.90, 3.50, 4.20, 5.00, 6.00, 7.50, 9.00],
    3: [1.15, 1.40, 1.75, 2.25, 2.95, 4.00, 5.60, 8.00, 10.00, 13.00, 17.00, 22.00],
    4: [1.25, 1.60, 2.10, 2.85, 4.00, 5.80, 8.50, 12.50, 18.00, 25.00, 35.00],
    5: [1.30, 1.80, 2.60, 3.90, 6.00, 9.80, 16.00, 20.00, 28.00, 40.00],
    7: [1.50, 2.50, 4.50, 8.50, 17.00, 30.00, 50.00, 80.00],
    10: [2.00, 5.00, 15.00, 45.00, 100.00, 200.00]
}

# ==========================================
# 2. ДАННЫЕ ЯЩИКОВ (18 штук)
# ==========================================

CASE_PRICES: Dict[str, Dict[str, Any]] = {
    # UC Ящики (8 шт)
    "star_case_1": {
        "id": "star_case_1",
        "name": "Бронзовый Ящик",
        "price": 30,
        "items": [
            {"id": "s1_1", "name": "⭐ 12 UC", "price": 12, "rarity": "common"},
            {"id": "s1_2", "name": "⭐ 29 UC", "price": 29, "rarity": "common"},
            {"id": "s1_3", "name": "⭐ 46 UC", "price": 46, "rarity": "rare"},
            {"id": "s1_4", "name": "⭐ 69 UC", "price": 69, "rarity": "rare"},
            {"id": "s1_5", "name": "⭐ 115 UC", "price": 115, "rarity": "epic"},
            {"id": "s1_6", "name": "⭐ 230 UC", "price": 230, "rarity": "epic"}
        ]
    },
    "star_case_2": {
        "id": "star_case_2",
        "name": "Серебряный Ящик",
        "price": 100,
        "items": [
            {"id": "s2_1", "name": "⭐ 35 UC", "price": 35, "rarity": "common"},
            {"id": "s2_2", "name": "⭐ 86 UC", "price": 86, "rarity": "common"},
            {"id": "s2_3", "name": "⭐ 138 UC", "price": 138, "rarity": "rare"},
            {"id": "s2_4", "name": "⭐ 230 UC", "price": 230, "rarity": "rare"},
            {"id": "s2_5", "name": "⭐ 403 UC", "price": 403, "rarity": "epic"},
            {"id": "s2_6", "name": "⭐ 690 UC", "price": 690, "rarity": "epic"}
        ]
    },
    "star_case_3": {
        "id": "star_case_3",
        "name": "Золотой Ящик",
        "price": 250,
        "items": [
            {"id": "s3_1", "name": "⭐ 92 UC", "price": 92, "rarity": "common"},
            {"id": "s3_2", "name": "⭐ 230 UC", "price": 230, "rarity": "rare"},
            {"id": "s3_3", "name": "⭐ 403 UC", "price": 403, "rarity": "rare"},
            {"id": "s3_4", "name": "⭐ 575 UC", "price": 575, "rarity": "epic"},
            {"id": "s3_5", "name": "⭐ 920 UC", "price": 920, "rarity": "legendary"},
            {"id": "s3_6", "name": "⭐ 1725 UC", "price": 1725, "rarity": "legendary"}
        ]
    },
    "star_case_4": {
        "id": "star_case_4",
        "name": "Платиновый Ящик",
        "price": 500,
        "items": [
            {"id": "s4_1", "name": "⭐ 173 UC", "price": 173, "rarity": "common"},
            {"id": "s4_2", "name": "⭐ 460 UC", "price": 460, "rarity": "rare"},
            {"id": "s4_3", "name": "⭐ 748 UC", "price": 748, "rarity": "epic"},
            {"id": "s4_4", "name": "⭐ 1150 UC", "price": 1150, "rarity": "epic"},
            {"id": "s4_5", "name": "⭐ 2070 UC", "price": 2070, "rarity": "legendary"},
            {"id": "s4_6", "name": "⭐ 3450 UC", "price": 3450, "rarity": "mythic"}
        ]
    },
    "star_case_5": {
        "id": "star_case_5",
        "name": "Алмазный Ящик",
        "price": 1000,
        "items": [
            {"id": "s5_1", "name": "⭐ 345 UC", "price": 345, "rarity": "common"},
            {"id": "s5_2", "name": "⭐ 920 UC", "price": 920, "rarity": "rare"},
            {"id": "s5_3", "name": "⭐ 1495 UC", "price": 1495, "rarity": "epic"},
            {"id": "s5_4", "name": "⭐ 2300 UC", "price": 2300, "rarity": "legendary"},
            {"id": "s5_5", "name": "⭐ 4025 UC", "price": 4025, "rarity": "legendary"},
            {"id": "s5_6", "name": "⭐ 6900 UC", "price": 6900, "rarity": "mythic"}
        ]
    },
    "star_case_6": {
        "id": "star_case_6",
        "name": "Мифический Ящик",
        "price": 2000,
        "items": [
            {"id": "s6_1", "name": "⭐ 575 UC", "price": 575, "rarity": "rare"},
            {"id": "s6_2", "name": "⭐ 1495 UC", "price": 1495, "rarity": "epic"},
            {"id": "s6_3", "name": "⭐ 2530 UC", "price": 2530, "rarity": "epic"},
            {"id": "s6_4", "name": "⭐ 4025 UC", "price": 4025, "rarity": "legendary"},
            {"id": "s6_5", "name": "⭐ 6325 UC", "price": 6325, "rarity": "mythic"},
            {"id": "s6_6", "name": "⭐ 11500 UC", "price": 11500, "rarity": "mythic"}
        ]
    },
    "star_case_7": {
        "id": "star_case_7",
        "name": "Божественный Ящик",
        "price": 5000,
        "items": [
            {"id": "s7_1", "name": "⭐ 1000 UC", "price": 1000, "rarity": "rare"},
            {"id": "s7_2", "name": "⭐ 2500 UC", "price": 2500, "rarity": "epic"},
            {"id": "s7_3", "name": "⭐ 5000 UC", "price": 5000, "rarity": "legendary"},
            {"id": "s7_4", "name": "⭐ 7500 UC", "price": 7500, "rarity": "legendary"},
            {"id": "s7_5", "name": "⭐ 10000 UC", "price": 10000, "rarity": "mythic"},
            {"id": "s7_6", "name": "⭐ 25000 UC", "price": 25000, "rarity": "mythic"}
        ]
    },
    "star_case_8": {
        "id": "star_case_8",
        "name": "Космический Ящик",
        "price": 10000,
        "items": [
            {"id": "s8_1", "name": "⭐ 250 UC", "price": 250, "rarity": "common"},
            {"id": "s8_2", "name": "⭐ 750 UC", "price": 750, "rarity": "rare"},
            {"id": "s8_3", "name": "⭐ 1500 UC", "price": 1500, "rarity": "epic"},
            {"id": "s8_4", "name": "⭐ 3000 UC", "price": 3000, "rarity": "legendary"},
            {"id": "s8_5", "name": "⭐ 5000 UC", "price": 5000, "rarity": "legendary"},
            {"id": "s8_6", "name": "⭐ 15000 UC", "price": 15000, "rarity": "mythic"}
        ]
    },
    # NFT Скины (8 шт)
    "nft_case_1": {
        "id": "nft_case_1",
        "name": "Basic Skins",
        "price": 80,
        "items": [
            {"id": "n1_1", "name": "🎽 Brown Shirt", "price": 23, "rarity": "common"},
            {"id": "n1_2", "name": "🧢 Grey Cap", "price": 40, "rarity": "common"},
            {"id": "n1_3", "name": "👖 Cargo Pants", "price": 63, "rarity": "common"},
            {"id": "n1_4", "name": "🎒 Level 1 Backpack", "price": 92, "rarity": "rare"},
            {"id": "n1_5", "name": "🪖 Steel Helmet", "price": 150, "rarity": "rare"},
            {"id": "n1_6", "name": "🥾 Military Boots", "price": 288, "rarity": "epic"}
        ]
    },
    "nft_case_2": {
        "id": "nft_case_2",
        "name": "Tactical Skins",
        "price": 200,
        "items": [
            {"id": "n2_1", "name": "🧣 Red Scarf", "price": 52, "rarity": "common"},
            {"id": "n2_2", "name": "🧤 Tactical Gloves", "price": 86, "rarity": "rare"},
            {"id": "n2_3", "name": "🦺 Police Vest", "price": 138, "rarity": "rare"},
            {"id": "n2_4", "name": "🎽 Sports Top", "price": 219, "rarity": "epic"},
            {"id": "n2_5", "name": "👒 Straw Hat", "price": 345, "rarity": "epic"},
            {"id": "n2_6", "name": "🪖 Level 2 Helmet", "price": 575, "rarity": "legendary"}
        ]
    },
    "nft_case_3": {
        "id": "nft_case_3",
        "name": "Urban Skins",
        "price": 400,
        "items": [
            {"id": "n3_1", "name": "🧥 Leather Jacket", "price": 104, "rarity": "rare"},
            {"id": "n3_2", "name": "👖 Jeans", "price": 173, "rarity": "rare"},
            {"id": "n3_3", "name": "👟 Sneakers", "price": 276, "rarity": "epic"},
            {"id": "n3_4", "name": "🎒 Level 2 Backpack", "price": 437, "rarity": "epic"},
            {"id": "n3_5", "name": "🛡️ Riot Shield", "price": 690, "rarity": "legendary"},
            {"id": "n3_6", "name": "🪖 Level 3 Helmet", "price": 1150, "rarity": "legendary"}
        ]
    },
    "nft_case_4": {
        "id": "nft_case_4",
        "name": "Military Skins",
        "price": 800,
        "items": [
            {"id": "n4_1", "name": "🥋 Martial Arts", "price": 207, "rarity": "rare"},
            {"id": "n4_2", "name": "🦺 Ghillie Suit", "price": 345, "rarity": "epic"},
            {"id": "n4_3", "name": "🪖 Spetsnaz Helmet", "price": 552, "rarity": "epic"},
            {"id": "n4_4", "name": "🎒 Level 3 Backpack", "price": 863, "rarity": "legendary"},
            {"id": "n4_5", "name": "🔫 M416 Skin", "price": 1380, "rarity": "legendary"},
            {"id": "n4_6", "name": "🎯 AWM Skin", "price": 2300, "rarity": "mythic"}
        ]
    },
    "nft_case_5": {
        "id": "nft_case_5",
        "name": "Elite Skins",
        "price": 1500,
        "items": [
            {"id": "n5_1", "name": "👑 Crown", "price": 368, "rarity": "epic"},
            {"id": "n5_2", "name": "🦅 Eagle Mask", "price": 610, "rarity": "epic"},
            {"id": "n5_3", "name": "🐯 Tiger Suit", "price": 978, "rarity": "legendary"},
            {"id": "n5_4", "name": "🤖 Robot Suit", "price": 1553, "rarity": "legendary"},
            {"id": "n5_5", "name": "🔥 Flame Jacket", "price": 2415, "rarity": "mythic"},
            {"id": "n5_6", "name": "💎 Diamond Helmet", "price": 4025, "rarity": "mythic"}
        ]
    },
    "nft_case_6": {
        "id": "nft_case_6",
        "name": "Legendary Skins",
        "price": 2500,
        "items": [
            {"id": "n6_1", "name": "🦺 Golden Ghillie", "price": 690, "rarity": "epic"},
            {"id": "n6_2", "name": "🔫 Golden AKM", "price": 1150, "rarity": "legendary"},
            {"id": "n6_3", "name": "🎯 Golden AWM", "price": 1840, "rarity": "legendary"},
            {"id": "n6_4", "name": "👑 Royal Crown", "price": 2875, "rarity": "mythic"},
            {"id": "n6_5", "name": "🐲 Dragon Suit", "price": 4600, "rarity": "mythic"},
            {"id": "n6_6", "name": "⭐ Legendary Set", "price": 8050, "rarity": "mythic"}
        ]
    },
    "nft_case_7": {
        "id": "nft_case_7",
        "name": "Halloween Skins",
        "price": 500,
        "items": [
            {"id": "n7_1", "name": "🧛 Vampire Cape", "price": 150, "rarity": "rare"},
            {"id": "n7_2", "name": "🦇 Bat Mask", "price": 300, "rarity": "epic"},
            {"id": "n7_3", "name": "🎃 Pumpkin Head", "price": 500, "rarity": "epic"},
            {"id": "n7_4", "name": "👻 Ghost Suit", "price": 800, "rarity": "legendary"},
            {"id": "n7_5", "name": "🧟 Zombie Skin", "price": 1200, "rarity": "legendary"},
            {"id": "n7_6", "name": "🕷️ Spider Set", "price": 2000, "rarity": "mythic"}
        ]
    },
    "nft_case_8": {
        "id": "nft_case_8",
        "name": "Wild West Skins",
        "price": 1000,
        "items": [
            {"id": "n8_1", "name": "🤠 Cowboy Hat", "price": 200, "rarity": "rare"},
            {"id": "n8_2", "name": "🐴 Horse Mask", "price": 400, "rarity": "epic"},
            {"id": "n8_3", "name": "🔫 Sheriff Revolver", "price": 700, "rarity": "epic"},
            {"id": "n8_4", "name": "👢 Boots", "price": 1000, "rarity": "legendary"},
            {"id": "n8_5", "name": "⭐ Star Badge", "price": 1800, "rarity": "legendary"},
            {"id": "n8_6", "name": "🔥 Blazing Set", "price": 3500, "rarity": "mythic"}
        ]
    }
}

# ==========================================
# 3. ИНИЦИАЛИЗАЦИЯ FASTAPI
# ==========================================

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(
    title="PUBG Cases WebApp Engine",
    version="2.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc"
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Socket.IO сервер
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
socket_app = socketio.ASGIApp(sio, app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_NAME = "database.db"

# ==========================================
# 4. PYDANTIC МОДЕЛИ
# ==========================================

class DepositRequest(BaseModel):
    amount: int = Field(..., ge=10)

class WithdrawRequest(BaseModel):
    amount: int = Field(..., ge=100)
    wallet: str = Field(..., min_length=5)

class CaseOpenRequest(BaseModel):
    case_type: str

class UpgradeRequest(BaseModel):
    item_index: int = Field(..., ge=0)
    target_price: int = Field(..., ge=1)

class CoinFlipRequest(BaseModel):
    bet_amount: int = Field(..., ge=5)
    choice: str = Field(..., regex="^(heads|tails)$")

class MinesStartRequest(BaseModel):
    bet_amount: int = Field(..., ge=5)
    mines_count: int = Field(..., ge=1, le=10)

class MinesOpenRequest(BaseModel):
    game_id: str
    cell_index: int = Field(..., ge=0, le=24)

class MinesCashoutRequest(BaseModel):
    game_id: str

class CrashBetRequest(BaseModel):
    bet_amount: int = Field(..., ge=5)

class SellItemRequest(BaseModel):
    item_index: int = Field(..., ge=0)

# ==========================================
# 5. РАБОТА С БАЗОЙ ДАННЫХ
# ==========================================

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        tg_id INTEGER PRIMARY KEY,
        username TEXT NOT NULL,
        balance INTEGER DEFAULT 5,
        total_spent INTEGER DEFAULT 0,
        inventory TEXT DEFAULT '[]',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        type TEXT NOT NULL,
        amount INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(tg_id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS deposits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        status TEXT DEFAULT 'success',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(tg_id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS withdraws (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        wallet TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(tg_id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS daily_quests (
        user_id INTEGER NOT NULL,
        quest_type TEXT NOT NULL,
        progress INTEGER DEFAULT 0,
        target INTEGER NOT NULL,
        completed BOOLEAN DEFAULT FALSE,
        PRIMARY KEY (user_id, quest_type),
        FOREIGN KEY(user_id) REFERENCES users(tg_id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS withdraw_cooldowns (
        user_id INTEGER PRIMARY KEY,
        last_withdraw_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(tg_id)
    )''')
    
    conn.commit()
    conn.close()

init_db()

def db_get_user(tg_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def db_create_user_if_not_exists(tg_id: int, username: str) -> Dict[str, Any]:
    user = db_get_user(tg_id)
    if not user:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "INSERT INTO users (tg_id, username, balance, total_spent, inventory) VALUES (?, ?, ?, ?, ?)",
            (tg_id, username, START_BALANCE, 0, json.dumps([]))
        )
        c.execute("INSERT INTO daily_quests (user_id, quest_type, target) VALUES (?, 'open_cases', 3)", (tg_id,))
        c.execute("INSERT INTO daily_quests (user_id, quest_type, target) VALUES (?, 'play_mines', 5)", (tg_id,))
        conn.commit()
        conn.close()
        return db_get_user(tg_id)
    return user

def db_update_balance(tg_id: int, amount: int, tx_type: str):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (amount, tg_id))
    c.execute("INSERT INTO transactions (user_id, type, amount) VALUES (?, ?, ?)", (tg_id, tx_type, amount))
    conn.commit()
    conn.close()

def db_update_inventory(tg_id: int, inventory_list: List[Dict[str, Any]]):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE users SET inventory = ? WHERE tg_id = ?", (json.dumps(inventory_list), tg_id))
    conn.commit()
    conn.close()

# ==========================================
# 6. ХРАНИЛИЩА СОСТОЯНИЙ
# ==========================================

active_mines_games: Dict[str, Dict[str, Any]] = {}

crash_state: Dict[str, Any] = {
    "multiplier": 1.0,
    "status": "waiting",
    "crash_point": 1.0,
    "bets": {}
}

# ==========================================
# 7. CRASH ENGINE (WEBSOCKET LOOP)
# ==========================================

async def crash_loop():
    global crash_state
    while True:
        crash_state["status"] = "waiting"
        crash_state["multiplier"] = 1.0
        
        e = random.uniform(0.01, 1.0)
        crash_point = max(1.0, round((1.0 - CRASH_HOUSE_EDGE) / e, 2))
        if crash_point > 100.0:
            crash_point = 100.0
        crash_state["crash_point"] = crash_point

        for t in range(5, 0, -1):
            await sio.emit('crash_state', {'timer': t, 'status': 'waiting'})
            await asyncio.sleep(1)

        crash_state["status"] = "running"
        await sio.emit('crash_start', {})
        
        current = 1.0
        while current < crash_state["crash_point"]:
            await asyncio.sleep(0.1)
            current = round(current + 0.02 + (current * 0.015), 2)
            crash_state["multiplier"] = current
            await sio.emit('crash_multiplier', {'multiplier': current})

        crash_state["status"] = "crashed"
        await sio.emit('crash_end', {'crash_point': crash_state["crash_point"]})
        crash_state["bets"].clear()
        await asyncio.sleep(4)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(crash_loop())

# ==========================================
# 8. SOCKET.IO СОБЫТИЯ
# ==========================================

@sio.event
async def connect(sid, environ):
    await sio.emit('crash_state', {
        'status': crash_state["status"],
        'multiplier': crash_state["multiplier"]
    }, to=sid)

@sio.event
async def place_bet(sid, data):
    tg_id = data.get('tg_id')
    bet_amount = data.get('bet_amount', 0)
    
    if crash_state["status"] != "running":
        await sio.emit('error', {'message': 'Игра не запущена'}, to=sid)
        return
    
    if bet_amount < 5:
        await sio.emit('error', {'message': 'Минимальная ставка 5 UC'}, to=sid)
        return
    
    user = db_get_user(tg_id)
    if not user or user['balance'] < bet_amount:
        await sio.emit('error', {'message': 'Недостаточно средств'}, to=sid)
        return
    
    db_update_balance(tg_id, -bet_amount, "crash_bet")
    crash_state["bets"][tg_id] = bet_amount
    await sio.emit('bet_placed', {'tg_id': tg_id, 'amount': bet_amount})

@sio.event
async def cashout(sid, data):
    tg_id = data.get('tg_id')
    
    if crash_state["status"] != "running":
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
        'tg_id': tg_id,
        'amount': win_amount,
        'multiplier': crash_state["multiplier"]
    })

# ==========================================
# 9. API ENDPOINTS
# ==========================================

@app.get("/api/profile")
async def get_profile_api(authorization: Optional[str] = Header(None)):
    tg_id = 12345678
    user = db_create_user_if_not_exists(tg_id, "Survivor_Player")
    user_data = dict(user)
    user_data['inventory'] = json.loads(user_data['inventory'])
    return user_data

@app.post("/api/case/open")
@limiter.limit("15/minute")
async def open_case_api(req: Request, data: CaseOpenRequest, authorization: Optional[str] = Header(None)):
    tg_id = 12345678
    user = db_get_user(tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    case = CASE_PRICES.get(data.case_type)
    if not case:
        raise HTTPException(status_code=400, detail="Ящик не существует")
    
    if user['balance'] < case['price']:
        raise HTTPException(status_code=400, detail="Недостаточно UC")
    
    db_update_balance(tg_id, -case['price'], "case_open")
    
    win_item = random.choice(case['items'])
    inv = json.loads(user['inventory'])
    inv.append(win_item)
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE users SET inventory = ?, total_spent = total_spent + ? WHERE tg_id = ?", 
              (json.dumps(inv), case['price'], tg_id))
    c.execute("UPDATE daily_quests SET progress = progress + 1 WHERE user_id = ? AND quest_type = 'open_cases'", 
              (tg_id,))
    conn.commit()
    conn.close()
    
    return {
        "reward_name": win_item['name'],
        "balance": user['balance'] - case['price']
    }

@app.post("/api/inventory/sell_item")
async def sell_item_api(data: SellItemRequest, authorization: Optional[str] = Header(None)):
    tg_id = 12345678
    user = db_get_user(tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    inv = json.loads(user['inventory'])
    if data.item_index < 0 or data.item_index >= len(inv):
        raise HTTPException(status_code=400, detail="Предмет не найден")
    
    item = inv.pop(data.item_index)
    sell_price = int(item['price'] * 0.5)  # Продажа за 50% цены
    
    db_update_balance(tg_id, sell_price, "sell_item")
    db_update_inventory(tg_id, inv)
    
    return {"gain": sell_price, "balance": user['balance'] + sell_price}

@app.post("/api/inventory/sell_all")
async def sell_all_items_api(authorization: Optional[str] = Header(None)):
    tg_id = 12345678
    user = db_get_user(tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    inv = json.loads(user['inventory'])
    total_gain = sum(int(item['price'] * 0.5) for item in inv)
    
    db_update_balance(tg_id, total_gain, "sell_all")
    db_update_inventory(tg_id, [])
    
    return {"gain": total_gain, "balance": user['balance'] + total_gain}

@app.post("/api/inventory/upgrade")
async def upgrade_item_api(data: UpgradeRequest, authorization: Optional[str] = Header(None)):
    tg_id = 12345678
    user = db_get_user(tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    inv = json.loads(user['inventory'])
    if data.item_index < 0 or data.item_index >= len(inv):
        raise HTTPException(status_code=400, detail="Предмет не найден")
    
    item = inv.pop(data.item_index)
    current_price = item['price']
    target_price = data.target_price
    
    if target_price <= current_price:
        inv.append(item)
        raise HTTPException(status_code=400, detail="Цель должна быть дороже текущего предмета")
    
    # Шанс зависит от соотношения цен
    ratio = target_price / current_price
    if ratio >= 20:
        chance = 0.01
    elif ratio >= 10:
        chance = 0.03
    elif ratio >= 5:
        chance = 0.08
    elif ratio >= 3:
        chance = 0.15
    elif ratio >= 2:
        chance = 0.30
    elif ratio >= 1.5:
        chance = 0.50
    else:
        chance = 0.70
    
    chance = chance * (1 - UPGRADE_HOUSE_EDGE)
    is_success = random.random() < chance
    
    if is_success:
        # Успех — улучшаем предмет
        win_item = {
            "id": f"upgraded_{int(time.time())}",
            "name": f"★ {item['name']}",
            "price": target_price,
            "rarity": "mythic" if target_price >= 2000 else "legendary" if target_price >= 800 else "epic"
        }
        inv.append(win_item)
        message = f"✅ Успех! Улучшено до {target_price} UC (шанс был {int(chance*100)}%)"
    else:
        # Провал — предмет сгорает
        win_item = None
        message = f"💥 Провал! Предмет {item['name']} сгорел (шанс был {int(chance*100)}%)"
    
    db_update_inventory(tg_id, inv)
    
    return {
        "success": is_success,
        "chance": round(chance * 100, 2),
        "message": message,
        "win_item": win_item
    }

@app.post("/api/mines/start")
@limiter.limit("15/minute")
async def mines_start_api(req: Request, data: MinesStartRequest, authorization: Optional[str] = Header(None)):
    tg_id = 12345678
    user = db_get_user(tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    if user['balance'] < data.bet_amount:
        raise HTTPException(status_code=400, detail="Недостаточно UC")
    
    db_update_balance(tg_id, -data.bet_amount, "mines_bet")
    
    grid = [False] * 25
    mine_positions = random.sample(range(25), data.mines_count)
    for pos in mine_positions:
        grid[pos] = True
    
    game_id = f"mines_{tg_id}_{int(time.time() * 1000)}"
    active_mines_games[game_id] = {
        "user_id": tg_id,
        "bet": data.bet_amount,
        "mines_count": data.mines_count,
        "grid": grid,
        "opened_cells": [],
        "step": 0
    }
    
    return {
        "game_id": game_id,
        "mines_count": data.mines_count,
        "bet": data.bet_amount,
        "balance": user['balance'] - data.bet_amount
    }

@app.post("/api/mines/open")
@limiter.limit("60/minute")
async def mines_open_api(req: Request, data: MinesOpenRequest, authorization: Optional[str] = Header(None)):
    game = active_mines_games.get(data.game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Активная игра не найдена")
    
    if data.cell_index in game["opened_cells"]:
        raise HTTPException(status_code=400, detail="Ячейка уже открыта")
    
    if game["grid"][data.cell_index]:
        del active_mines_games[data.game_id]
        return {
            "status": "bomb",
            "cell_index": data.cell_index,
            "opened": game["opened_cells"],
            "mines": [i for i, v in enumerate(game["grid"]) if v]
        }
    
    game["opened_cells"].append(data.cell_index)
    game["step"] += 1
    
    mults = MINES_MULTIPLIERS.get(game["mines_count"], [1.05 * game["step"]])
    step_idx = min(game["step"] - 1, len(mults) - 1)
    current_mult = mults[step_idx]
    
    return {
        "status": "safe",
        "cell_index": data.cell_index,
        "opened": game["opened_cells"],
        "opened_count": game["step"],
        "current_multiplier": current_mult
    }

@app.post("/api/mines/cashout")
async def mines_cashout_api(data: MinesCashoutRequest, authorization: Optional[str] = Header(None)):
    game = active_mines_games.get(data.game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Активная игра не найдена")
    
    if game["step"] == 0:
        raise HTTPException(status_code=400, detail="Откройте хотя бы одну клетку")
    
    mults = MINES_MULTIPLIERS.get(game["mines_count"], [1.05 * game["step"]])
    step_idx = min(game["step"] - 1, len(mults) - 1)
    final_mult = mults[step_idx]
    win_amount = int(game["bet"] * final_mult)
    
    db_update_balance(game["user_id"], win_amount, "mines_win")
    
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "UPDATE daily_quests SET progress = progress + 1 WHERE user_id = ? AND quest_type = 'play_mines'",
        (game["user_id"],)
    )
    conn.commit()
    conn.close()
    
    del active_mines_games[data.game_id]
    
    return {
        "success": True,
        "win_amount": win_amount,
        "multiplier": final_mult,
        "profit": win_amount - game["bet"]
    }

@app.post("/api/coinflip")
@limiter.limit("30/minute")
async def play_coinflip_api(req: Request, data: CoinFlipRequest, authorization: Optional[str] = Header(None)):
    tg_id = 12345678
    user = db_get_user(tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    if user['balance'] < data.bet_amount:
        raise HTTPException(status_code=400, detail="Недостаточно UC")
    
    db_update_balance(tg_id, -data.bet_amount, "coinflip_bet")
    
    result = random.choice(['heads', 'tails'])
    win = (result == data.choice)
    win_amount = 0
    
    if win:
        win_amount = int(data.bet_amount * (2.0 - COINFLIP_HOUSE_EDGE))
        db_update_balance(tg_id, win_amount, "coinflip_win")
    
    return {
        "win": win,
        "result": result,
        "win_amount": win_amount,
        "balance": user['balance'] - data.bet_amount + win_amount
    }

@app.post("/api/free_case/claim")
async def claim_free_case_api(authorization: Optional[str] = Header(None)):
    tg_id = 12345678
    user = db_get_user(tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    # Проверяем, не использовал ли уже сегодня
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT created_at FROM transactions WHERE user_id = ? AND type = 'free_case' ORDER BY created_at DESC LIMIT 1", (tg_id,))
    row = c.fetchone()
    conn.close()
    
    if row:
        last_used = time.mktime(time.strptime(row[0], "%Y-%m-%d %H:%M:%S"))
        if time.time() - last_used < 86400:
            raise HTTPException(status_code=400, detail="Бесплатный ящик доступен раз в 24 часа")
    
    # Даём бесплатный предмет из бронзового ящика
    case = CASE_PRICES["star_case_1"]
    win_item = random.choice(case['items'])
    
    inv = json.loads(user['inventory'])
    inv.append(win_item)
    db_update_inventory(tg_id, inv)
    db_update_balance(tg_id, 0, "free_case")
    
    return {"success": True, "reward": win_item['name']}

@app.post("/api/stars/buy")
async def buy_stars_api(stars_amount: int, authorization: Optional[str] = Header(None)):
    tg_id = 12345678
    if stars_amount < 50:
        raise HTTPException(status_code=400, detail="Минимальная покупка 50 UC")
    
    # Имитация оплаты через Telegram Stars
    db_update_balance(tg_id, stars_amount, "deposit")
    
    return {"status": "success", "amount": stars_amount, "balance": db_get_user(tg_id)['balance']}

@app.post("/api/withdraw")
async def request_withdraw_api(data: WithdrawRequest, authorization: Optional[str] = Header(None)):
    tg_id = 12345678
    user = db_get_user(tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    if data.amount < MIN_WITHDRAW:
        raise HTTPException(status_code=400, detail=f"Минимальный вывод {MIN_WITHDRAW} UC")
    if data.amount > MAX_WITHDRAW:
        raise HTTPException(status_code=400, detail=f"Максимальный вывод {MAX_WITHDRAW} UC")
    if user['balance'] < data.amount:
        raise HTTPException(status_code=400, detail="Недостаточно средств")
    
    # Проверка кулдауна
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT last_withdraw_at FROM withdraw_cooldowns WHERE user_id = ?", (tg_id,))
    row = c.fetchone()
    if row:
        last_ts = time.mktime(time.strptime(row[0], "%Y-%m-%d %H:%M:%S"))
        if time.time() - last_ts < 86400:
            hours_left = int((86400 - (time.time() - last_ts)) / 3600)
            raise HTTPException(status_code=400, detail=f"Следующий вывод через {hours_left} ч.")
    
    fee = int(data.amount * WITHDRAW_FEE)
    payout = data.amount - fee
    new_balance = user['balance'] - data.amount
    
    db_update_balance(tg_id, -data.amount, "withdraw_request")
    
    c.execute("INSERT INTO withdraws (user_id, amount, wallet) VALUES (?, ?, ?)", (tg_id, data.amount, data.wallet))
    c.execute("INSERT OR REPLACE INTO withdraw_cooldowns (user_id, last_withdraw_at) VALUES (?, CURRENT_TIMESTAMP)", (tg_id,))
    conn.commit()
    conn.close()
    
    return {
        "status": "pending",
        "payout": payout,
        "fee": fee,
        "new_balance": new_balance
    }

@app.post("/api/promo/activate")
async def activate_promo_api(code: str, authorization: Optional[str] = Header(None)):
    tg_id = 12345678
    # Простая реализация промокодов (можно расширить)
    if code.upper() == "PUBG2024":
        db_update_balance(tg_id, 100, "promo")
        return {"success": True, "message": "Промокод активирован! +100 UC", "reward": "100 UC"}
    else:
        raise HTTPException(status_code=400, detail="Неверный промокод")

@app.get("/api/admin/stats")
async def get_admin_stats_api():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*), SUM(balance) FROM users")
    users_cnt, total_bal = c.fetchone()
    c.execute("SELECT SUM(amount) FROM deposits")
    deposits = c.fetchone()[0] or 0
    c.execute("SELECT SUM(amount) FROM withdraws WHERE status = 'pending'")
    pending_withdraws = c.fetchone()[0] or 0
    conn.close()
    
    return {
        "total_users": users_cnt,
        "total_balance": total_bal or 0,
        "total_deposits": deposits,
        "pending_withdraws": pending_withdraws
    }

@app.get("/api/leaderboard")
async def get_leaderboard_api():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT username, balance FROM users ORDER BY balance DESC LIMIT 10")
    rows = c.fetchall()
    conn.close()
    return {"players": [{"username": row[0], "balance": row[1]} for row in rows]}

@app.get("/api/crash/history")
async def get_crash_history_api():
    return {"history": []}

# ==========================================
# 10. РАЗДАЧА HTML
# ==========================================

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    if not os.path.exists("index.html"):
        return HTMLResponse("<h2>Ошибка: index.html не найден</h2>", status_code=404)
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

# ==========================================
# 11. ЗАПУСК
# ==========================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
