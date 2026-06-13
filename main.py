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
from fastapi import FastAPI, Header, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import aiosqlite
import httpx
import socketio

app = FastAPI()

# Socket.IO сервер
sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins='*',
    ping_timeout=10,
    ping_interval=5
)

# Монтируем socket.io
socket_app = socketio.ASGIApp(sio, app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_TG_ID_RAW = os.getenv("ADMIN_TG_ID")
ADMIN_TG_ID = int(ADMIN_TG_ID_RAW) if ADMIN_TG_ID_RAW else 0

DB_NAME = "database.db"

# Цены для 12 кейсов
CASE_PRICES = {
    "star_micro": 50, "star_common": 150, "star_rare": 500, "star_epic": 2000,
    "ton_frogs": 80, "digital_resistance": 200, "pudgy_penguins": 600, "bored_apes": 2500,
    "ton_gems": 100, "memecoins": 300, "crypto_punks": 1200, "bitcoin_whales": 5000
}

# 12 кейсов по 6 предметов = 72 уникальных предмета
CASE_DROPS = {
    "star_micro": {
        "m1": ("🥉 Бронзовая Звезда", 10), "m2": ("🌀 Telegram Спиннер", 25), "m3": ("👍 Пиксельный Лайк", 45),
        "m4": ("🎭 Кастомный Эмодзи", 90), "m5": ("📂 Набор Стикеров", 150), "m6": ("🥈 Серебряный Токен", 400)
    },
    "star_common": {
        "c1": ("🥇 Золотая Звезда", 30), "c2": ("🖼️ Анимированный Фон", 70), "c3": ("✨ Звёздный Статус", 130),
        "c4": ("🔥 Редкий Смайл", 260), "c5": ("💎 VIP Значок", 500), "c6": ("🔮 Кристалл Stars", 1200)
    },
    "star_rare": {
        "r1": ("💿 Платиновая Звезда", 100), "r2": ("📱 Премиум Номер (+888)", 220), "r3": ("🏷️ Красивый Юзернейм", 450),
        "r4": ("💎 Алмазный Статус", 900), "r5": ("🗺️ Секретный Квест", 1800), "r6": ("👑 Корона Stars", 4000)
    },
    "star_epic": {
        "e1": ("🔻 Рубиновая Звезда", 400), "e2": ("📞 Элитный Анонимный Номер", 900), "e3": ("🔱 Золотой Юзернейм", 1900),
        "e4": ("🎨 Эксклюзивный Аватар", 3800), "e5": ("📜 Сертификат Дурова", 7500), "e6": ("⭐ Главная Звезда Экосистемы", 16000)
    },
    "ton_frogs": {
        "f1": ("🐸 Green Frog NFT", 15), "f2": ("🕶️ Cool Glasses Frog NFT", 40), "f3": ("🧥 Hoodie Frog NFT", 75),
        "f4": ("💼 Suit Business Frog NFT", 150), "f5": ("🤖 Cyber Cyborg Frog NFT", 320), "f6": ("👑 King TON Frog NFT", 700)
    },
    "digital_resistance": {
        "d1": ("🐶 Classic Spotty NFT", 40), "d2": ("🥷 Resistance Dog NFT", 90), "d3": ("👾 Pixel Spotty NFT", 180),
        "d4": ("🦾 Cybernetic Dog NFT", 380), "d5": ("🎨 Street Art Dog NFT", 750), "d6": ("🔱 Gold Resistance Dog NFT", 1700)
    },
    "pudgy_penguins": {
        "p1": ("👒 Hat Pudgy Penguin NFT", 120), "p2": ("👓 Glass Pudgy Penguin NFT", 280), "p3": ("🎣 Fisher Pudgy Penguin NFT", 550),
        "p4": ("🥷 Ninja Pudgy Penguin NFT", 1100), "p5": ("🚀 Astro Pudgy Penguin NFT", 2300), "p6": ("📀 Solid Gold Pudgy Penguin", 5000)
    },
    "bored_apes": {
        "a1": ("🎽 Striped Bored Ape NFT", 500), "a2": ("🧟 Zombie Bored Ape NFT", 1100), "a3": ("🎧 Music Bored Ape NFT", 2200),
        "a4": ("👁️ Laser Eyes Bored Ape NFT", 4500), "a5": ("👑 Crown King Bored Ape NFT", 9500), "a6": ("🔱 Solid Gold Bored Ape NFT", 22000)
    },
    "ton_gems": {
        "g1": ("💎 Кварцевый Самоцвет", 20), "g2": ("🔮 Аметистовый Кристалл", 50), "g3": ("🟢 Изумруд TON", 95),
        "g4": ("🔷 Сапфировый Токен", 190), "g5": ("❤️ Рубин Валидатора", 400), "g6": ("👑 Королевский Алмаз", 900)
    },
    "memecoins": {
        "mem1": ("🐕 Пиксельный Doge", 60), "mem2": ("🐸 Грустный Pepe токен", 140), "mem3": ("🐱 Коин Наруто-Кэт", 280),
        "mem4": ("🐹 Монета Хомяка", 550), "mem5": ("🐐 Золотой GOAT коин", 1200), "mem6": ("🚀 Мем-Ракета на Марс", 2600)
    },
    "crypto_punks": {
        "punk1": ("🧢 Punk с банданой NFT", 240), "punk2": ("🕶️ Punk в очках NFT", 500), "punk3": ("🚬 Punk с сигаретой NFT", 1000),
        "punk4": ("🧟 Зомби-Панк NFT", 2100), "punk5": ("👽 Инопланетный Панк NFT", 4600), "punk6": ("👑 Элитный Король Панков", 11000)
    },
    "bitcoin_whales": {
        "wh1": ("🐋 Железный Кит", 1000), "wh2": ("🐋 Бронзовый Кит", 2200), "wh3": ("🐋 Серебряный Кит", 4500),
        "wh4": ("🐋 Золотой Мега-Кит", 9000), "wh5": ("🐋 Платиновый Альфа-Кит", 19000), "wh6": ("👑 Сатоши Накамото Кит", 45000)
    }
}

DROP_WEIGHTS = [45.0, 28.0, 15.0, 8.0, 3.5, 0.5]

# ========== PROVABLY FAIR CRASH (ХАУС ЭЙДЖ 5%) ==========
CRASH_MIN_BET = 25
CRASH_MAX_BET = 5000
CRASH_BETTING_TIME = 6
CRASH_COOLDOWN = 3
CRASH_HOUSE_EDGE = 0.05
CRASH_SPEED = 0.08

# ========== REFERRAL SYSTEM ==========
REFERRAL_PERCENT = 7
MAX_WITHDRAW_AMOUNT = 50000
WITHDRAW_FEE = 0.05
WITHDRAW_COOLDOWN_HOURS = 24
MIN_DEPOSIT_FOR_REFERRAL = 50

SERVER_SEED = os.getenv("CRASH_SERVER_SEED", str(uuid.uuid4()))
SERVER_SEED_HASH = hashlib.sha256(SERVER_SEED.encode()).hexdigest()
crash_nonce = 0
ROUNDS_BEFORE_SEED_CHANGE = 100
rounds_since_seed_change = 0

crash_state = {
    "status": "waiting",
    "round_id": "",
    "crash_point": 1.0,
    "hash": "",
    "start_time": 0,
    "bets": {},
    "history": [],
    "timer_ends": 0,
    "connected_users": set(),
    "current_multiplier": 1.0,
    "crashed": False
}

def bet_key(tg_id: int, round_id: str) -> str:
    """Единый ключ ставки: tg_id:round_id"""
    return f"{tg_id}:{round_id}"

def generate_crash_point() -> tuple:
    """Provably Fair генерация краша — ХАУС ЭЙДЖ 5%"""
    global crash_nonce, rounds_since_seed_change, SERVER_SEED, SERVER_SEED_HASH
    
    crash_nonce += 1
    rounds_since_seed_change += 1
    
    if rounds_since_seed_change >= ROUNDS_BEFORE_SEED_CHANGE:
        SERVER_SEED = str(uuid.uuid4())
        SERVER_SEED_HASH = hashlib.sha256(SERVER_SEED.encode()).hexdigest()
        crash_nonce = 0
        rounds_since_seed_change = 0
    
    message = f"{SERVER_SEED}:{crash_nonce}"
    hash_hex = hashlib.sha256(message.encode()).hexdigest()
    
    h = int(hash_hex[:16], 16)
    r = h / (2**64)
    
    if r < 0.30:
        crash_point = round(1.01 + (r / 0.30) * 0.09, 2)
    elif r < 0.60:
        crash_point = round(1.10 + ((r - 0.30) / 0.30) * 0.20, 2)
    elif r < 0.82:
        crash_point = round(1.30 + ((r - 0.60) / 0.22) * 0.50, 2)
    elif r < 0.94:
        crash_point = round(1.80 + ((r - 0.82) / 0.12) * 1.20, 2)
    elif r < 0.98:
        crash_point = round(3.00 + ((r - 0.94) / 0.04) * 5.00, 2)
    elif r < 0.995:
        crash_point = round(8.00 + ((r - 0.98) / 0.015) * 12.00, 2)
    else:
        crash_point = round(20.00 + ((r - 0.995) / 0.005) * 30.00, 2)
    
    return min(crash_point, 50.0), hash_hex

async def crash_game_loop():
    """Главный цикл краш-игры"""
    global crash_state
    
    while True:
        crash_state["status"] = "betting"
        crash_state["round_id"] = str(uuid.uuid4())[:8]
        crash_state["bets"] = {}
        crash_state["crash_point"], crash_state["hash"] = generate_crash_point()
        crash_state["start_time"] = 0
        crash_state["current_multiplier"] = 1.0
        crash_state["crashed"] = False
        crash_state["timer_ends"] = time.time() + CRASH_BETTING_TIME
        
        await sio.emit('crash_state', {
            'status': 'betting',
            'round_id': crash_state["round_id"],
            'hash': crash_state["hash"],
            'timer': CRASH_BETTING_TIME,
            'bets_count': 0,
            'total_amount': 0
        })
        
        await asyncio.sleep(CRASH_BETTING_TIME)
        
        if len(crash_state["bets"]) == 0:
            crash_state["status"] = "cooldown"
            await sio.emit('crash_state', {
                'status': 'cooldown',
                'timer': CRASH_COOLDOWN,
                'history': crash_state["history"][:10]
            })
            await asyncio.sleep(CRASH_COOLDOWN)
            continue
        
        crash_state["status"] = "flying"
        crash_state["start_time"] = time.time()
        
        total_amount = sum(b["bet"] for b in crash_state["bets"].values())
        
        await sio.emit('crash_start', {
            'round_id': crash_state["round_id"],
            'hash': crash_state["hash"],
            'total_bets': len(crash_state["bets"]),
            'total_amount': total_amount
        })
        
        last_sent_mult = 1.0
        
        while True:
            elapsed = time.time() - crash_state["start_time"]
            current_mult = 1.00 * math.exp(CRASH_SPEED * elapsed)
            crash_state["current_multiplier"] = current_mult
            
            if current_mult >= crash_state["crash_point"]:
                crash_state["crashed"] = True
                crash_state["status"] = "crashed"
                final_point = crash_state["crash_point"]
                
                crash_state["history"].insert(0, final_point)
                if len(crash_state["history"]) > 20:
                    crash_state["history"] = crash_state["history"][:20]
                
                results = []
                for key, b in crash_state["bets"].items():
                    cashed = b.get("cashed_out", False)
                    results.append({
                        'username': b['username'],
                        'amount': b['bet'],
                        'cashed_out': cashed,
                        'cashed_at': b.get('cashed_at', 0),
                        'win': int(b['bet'] * b.get('cashed_at', 0)) if cashed else 0
                    })
                
                await sio.emit('crash_end', {
                    'crash_point': final_point,
                    'hash': crash_state["hash"],
                    'server_seed': SERVER_SEED,
                    'nonce': crash_nonce,
                    'bets': results
                })
                break
            
            if abs(current_mult - last_sent_mult) >= 0.01:
                await sio.emit('crash_multiplier', {
                    'multiplier': round(current_mult, 2),
                    'elapsed': elapsed
                })
                last_sent_mult = current_mult
            
            await asyncio.sleep(0.1)
        
        crash_state["status"] = "cooldown"
        await sio.emit('crash_state', {
            'status': 'cooldown',
            'timer': CRASH_COOLDOWN,
            'history': crash_state["history"][:10]
        })
        await asyncio.sleep(CRASH_COOLDOWN)

# ========== MODELS ==========
class OpenCaseRequest(BaseModel):
    case_type: str

class SellItemRequest(BaseModel):
    item_index: int

class UpdateWithdrawStatusRequest(BaseModel):
    ticket_id: int
    status: str

class ReferralActivateRequest(BaseModel):
    referrer_id: int

class AdminGiveStarsRequest(BaseModel):
    target_tg_id: int
    amount: int

# ========== SOCKET.IO EVENTS ==========
@sio.event
async def connect(sid, environ):
    crash_state["connected_users"].add(sid)
    await sio.emit('crash_state', {
        'status': crash_state["status"],
        'round_id': crash_state["round_id"],
        'timer': max(0, int(crash_state["timer_ends"] - time.time())),
        'history': crash_state["history"][:10],
        'bets_count': len(crash_state["bets"])
    }, to=sid)

@sio.event
async def disconnect(sid):
    crash_state["connected_users"].discard(sid)

@sio.event
async def place_bet(sid, data):
    try:
        tg_id = int(data.get('tg_id', 0))
    except (TypeError, ValueError):
        await sio.emit('error', {'message': 'Неверный ID пользователя'}, to=sid)
        return
    
    if tg_id <= 0:
        await sio.emit('error', {'message': 'Неверный ID пользователя'}, to=sid)
        return
    
    try:
        bet = int(data.get('bet_amount', 0))
    except (TypeError, ValueError):
        await sio.emit('error', {'message': 'Неверная сумма ставки'}, to=sid)
        return
    
    username = data.get('username', 'Игрок')
    round_id = crash_state["round_id"]
    
    if crash_state["status"] != "betting":
        await sio.emit('error', {'message': 'Ставки закрыты!'}, to=sid)
        return
    
    if bet < CRASH_MIN_BET or bet > CRASH_MAX_BET:
        await sio.emit('error', {'message': f'Ставка от {CRASH_MIN_BET} до {CRASH_MAX_BET} ⭐️'}, to=sid)
        return
    
    key = bet_key(tg_id, round_id)
    if key in crash_state["bets"]:
        await sio.emit('error', {'message': 'Уже есть ставка в этом раунде'}, to=sid)
        return
    
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("""
            UPDATE users 
            SET balance = balance - ? 
            WHERE tg_id = ? AND balance >= ?
        """, (bet, tg_id, bet))
        await db.commit()
        
        if cursor.rowcount == 0:
            await sio.emit('error', {'message': 'Недостаточно монет'}, to=sid)
            return
        
        async with db.execute("SELECT balance FROM users WHERE tg_id = ?", (tg_id,)) as cursor:
            row = await cursor.fetchone()
            new_balance = row[0] if row else 0
    
    crash_state["bets"][key] = {
        "tg_id": tg_id,
        "bet": bet,
        "username": username,
        "round_id": round_id,
        "cashed_out": False,
        "cashed_at": 0,
        "sid": sid
    }
    
    await sio.emit('bet_placed', {
        'tg_id': tg_id,
        'username': username,
        'amount': bet,
        'balance': new_balance,
        'round_id': round_id
    }, to=sid)
    
    await sio.emit('bets_update', {
        'count': len(crash_state["bets"]),
        'total': sum(b["bet"] for b in crash_state["bets"].values())
    })

@sio.event
async def cashout(sid, data):
    try:
        tg_id = int(data.get('tg_id', 0))
    except (TypeError, ValueError):
        await sio.emit('error', {'message': 'Неверный ID пользователя'}, to=sid)
        return
    
    if tg_id <= 0:
        await sio.emit('error', {'message': 'Неверный ID пользователя'}, to=sid)
        return
    
    round_id = crash_state["round_id"]
    
    if crash_state["crashed"]:
        await sio.emit('error', {'message': 'Ракета уже упала! Слишком поздно.'}, to=sid)
        return
    
    if crash_state["status"] != "flying":
        await sio.emit('error', {'message': f'Не время для кешаута. Статус: {crash_state["status"]}'}, to=sid)
        return
    
    key = bet_key(tg_id, round_id)
    bet_data = crash_state["bets"].get(key)
    
    if not bet_data:
        await sio.emit('error', {'message': 'Нет активной ставки в этом раунде'}, to=sid)
        return
    
    if bet_data.get("round_id") != round_id:
        await sio.emit('error', {'message': 'Ставка из прошлого раунда'}, to=sid)
        return
    
    if bet_data["cashed_out"]:
        await sio.emit('error', {'message': 'Вы уже забрали выигрыш'}, to=sid)
        return
    
    current_mult = crash_state["current_multiplier"]
    crash_point = crash_state["crash_point"]
    
    if current_mult >= crash_point:
        await sio.emit('error', {'message': 'Слишком поздно! Ракета уже упала.'}, to=sid)
        return
    
    win_amount = int(bet_data["bet"] * current_mult)
    
    bet_data["cashed_out"] = True
    bet_data["cashed_at"] = current_mult
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (win_amount, tg_id))
        await db.commit()
        async with db.execute("SELECT balance FROM users WHERE tg_id = ?", (tg_id,)) as cursor:
            row = await cursor.fetchone()
            new_balance = row[0] if row else 0
    
    await sio.emit('cashout_success', {
        'multiplier': round(current_mult, 2),
        'win_amount': win_amount,
        'profit': win_amount - bet_data["bet"],
        'balance': new_balance,
        'round_id': round_id
    }, to=sid)
    
    await sio.emit('player_cashout', {
        'username': bet_data["username"],
        'amount': bet_data["bet"],
        'multiplier': round(current_mult, 2),
        'win': win_amount
    })

# ========== STARTUP ==========
@app.on_event("startup")
async def startup():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                username TEXT DEFAULT 'Игрок',
                balance INTEGER DEFAULT 20,
                inventory TEXT DEFAULT '[]'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS withdraws (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER,
                amount INTEGER,
                requisites TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                user_id INTEGER PRIMARY KEY,
                referrer_id INTEGER NOT NULL,
                activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_earned INTEGER DEFAULT 0
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
            CREATE TABLE IF NOT EXISTS withdraw_cooldowns (
                user_id INTEGER PRIMARY KEY,
                last_withdraw_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()
    
    asyncio.create_task(crash_game_loop())

# ========== AUTH ==========
def verify_telegram_data(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization Header")
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN missing")
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
            raise HTTPException(status_code=401, detail="Data validation failed")
        return json.loads(init_data.get('user', ['{}'])[0])
    except Exception:
        raise HTTPException(status_code=401, detail="Parsing error")

async def get_or_create_user(tg_id: int, username: str = "Игрок"):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT balance, inventory FROM users WHERE tg_id = ?", (tg_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                await db.execute("UPDATE users SET username = ? WHERE tg_id = ?", (username, tg_id))
                await db.commit()
                return {"balance": row[0], "inventory": json.loads(row[1])}
            else:
                start_balance = 10000 if (ADMIN_TG_ID and tg_id == ADMIN_TG_ID) else 20
                await db.execute(
                    "INSERT INTO users (tg_id, username, balance, inventory) VALUES (?, ?, ?, ?)", 
                    (tg_id, username, start_balance, '[]')
                )
                await db.commit()
                return {"balance": start_balance, "inventory": []}

# ========== PROFILE ==========
@app.get("/api/profile")
async def get_profile(user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    username = user.get('first_name', 'Игрок')
    user_info = await get_or_create_user(tg_id, username)
    user_info["is_admin"] = (ADMIN_TG_ID and tg_id == ADMIN_TG_ID)
    return user_info

# ========== ADMIN ==========
@app.post("/api/admin/give_stars")
async def admin_give_stars(user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    if not ADMIN_TG_ID or tg_id != ADMIN_TG_ID:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance + 10000 WHERE tg_id = ?", (tg_id,))
        await db.commit()
    return {"success": True, "message": "✅ +10,000 Stars на ваш баланс"}

@app.post("/api/admin/give_stars_to_user")
async def admin_give_stars_to_user(req: AdminGiveStarsRequest, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    
    if not ADMIN_TG_ID or tg_id != ADMIN_TG_ID:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    target_id = req.target_tg_id
    amount = req.amount
    
    if target_id <= 0:
        raise HTTPException(status_code=400, detail="Неверный ID пользователя")
    if amount < 1:
        raise HTTPException(status_code=400, detail="Сумма должна быть больше 0")
    if amount > 1000000:
        raise HTTPException(status_code=400, detail="Максимальная сумма: 1,000,000 ⭐️")
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (tg_id, username, balance, inventory) VALUES (?, ?, 20, '[]')",
            (target_id, f"Player_{target_id}")
        )
        
        await db.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (amount, target_id))
        await db.commit()
        
        async with db.execute("SELECT username, balance FROM users WHERE tg_id = ?", (target_id,)) as cursor:
            row = await cursor.fetchone()
            target_username = row[0] if row else f"Player_{target_id}"
            target_balance = row[1] if row else amount
    
    return {
        "success": True,
        "message": f"✅ Выдано {amount} ⭐️ игроку {target_username} (ID: {target_id})",
        "target_tg_id": target_id,
        "target_username": target_username,
        "amount": amount,
        "new_balance": target_balance
    }

# ========== CASES ==========
@app.post("/api/case/open")
async def open_case(req: OpenCaseRequest, user: dict = Depends(verify_telegram_data)):
    case_type = req.case_type
    if case_type not in CASE_PRICES:
        raise HTTPException(status_code=400, detail="Неизвестный тип кейса")
        
    price = CASE_PRICES[case_type]
    tg_id = user.get('id')
    user_info = await get_or_create_user(tg_id)
    
    if user_info["balance"] < price:
        raise HTTPException(status_code=400, detail="Недостаточно монет")
    
    case_pool = CASE_DROPS[case_type]
    item_ids = list(case_pool.keys())
    
    reward_id = random.choices(item_ids, weights=DROP_WEIGHTS, k=1)[0]
    reward_name = case_pool[reward_id][0]
    
    new_balance = user_info["balance"] - price
    user_info["inventory"].append({"id": reward_id, "name": reward_name, "case": case_type})
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET balance = ?, inventory = ? WHERE tg_id = ?",
            (new_balance, json.dumps(user_info["inventory"]), tg_id)
        )
        await db.commit()
        
    return {"reward_id": reward_id, "reward_name": reward_name, "balance": new_balance}

# ========== INVENTORY ==========
@app.post("/api/inventory/sell_item")
async def sell_single_item(req: SellItemRequest, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    user_info = await get_or_create_user(tg_id)
    inventory = user_info["inventory"]
    
    if req.item_index < 0 or req.item_index >= len(inventory):
        raise HTTPException(status_code=400, detail="Предмет не найден")
        
    item = inventory.pop(req.item_index)
    c_type = item.get("case")
    i_id = item.get("id")
    
    gain = 0
    if c_type in CASE_DROPS and i_id in CASE_DROPS[c_type]:
        gain = CASE_DROPS[c_type][i_id][1]
        
    new_balance = user_info["balance"] + gain
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = ?, inventory = ? WHERE tg_id = ?", (new_balance, json.dumps(inventory), tg_id))
        await db.commit()
        
    return {"gain": gain, "balance": new_balance}

@app.post("/api/inventory/sell_all")
async def sell_all_items(user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    user_info = await get_or_create_user(tg_id)
    inventory = user_info["inventory"]
    if not inventory:
        raise HTTPException(status_code=400, detail="Инвентарь пуст")
        
    total_gain = 0
    for item in inventory:
        c_type = item.get("case")
        i_id = item.get("id")
        if c_type in CASE_DROPS and i_id in CASE_DROPS[c_type]:
            total_gain += CASE_DROPS[c_type][i_id][1]
            
    new_balance = user_info["balance"] + total_gain
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = ?, inventory = '[]' WHERE tg_id = ?", (new_balance, tg_id))
        await db.commit()
        
    return {"gain": total_gain, "balance": new_balance}

# ========== LEADERBOARD ==========
@app.get("/api/leaderboard")
async def get_leaderboard(user: dict = Depends(verify_telegram_data)):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT username, balance FROM users ORDER BY balance DESC LIMIT 10") as cursor:
            rows = await cursor.fetchall()
            return [{"username": r[0], "balance": r[1]} for r in rows]

# ========== STARS SHOP ==========
@app.post("/api/stars/buy")
async def buy_stars(stars_amount: int, user: dict = Depends(verify_telegram_data)):
    if stars_amount < 50:
        raise HTTPException(status_code=400, detail="Минимальное пополнение — 50 Stars")
    
    tg_id = user.get('id')
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/createInvoiceLink"
    payload = {
        "title": "Пополнение баланса",
        "description": f"Покупка {stars_amount} монет Stars для открытия кейсов",
        "payload": f"deposit_{tg_id}_{stars_amount}",
        "provider_token": "", 
        "currency": "XTR",
        "prices": [{"label": "Stars", "amount": stars_amount}]
    }
    async with httpx.AsyncClient() as client:
        res = await client.post(url, json=payload)
        res_data = res.json()
        if res_data.get("ok"):
            if stars_amount >= MIN_DEPOSIT_FOR_REFERRAL:
                async with aiosqlite.connect(DB_NAME) as db:
                    async with db.execute("SELECT referrer_id FROM referrals WHERE user_id = ?", (tg_id,)) as cursor:
                        ref = await cursor.fetchone()
                        if ref:
                            referrer_id = ref[0]
                            earned = int(stars_amount * REFERRAL_PERCENT / 100)
                            await db.execute(
                                "UPDATE users SET balance = balance + ? WHERE tg_id = ?",
                                (earned, referrer_id)
                            )
                            await db.execute(
                                "UPDATE referrals SET total_earned = total_earned + ? WHERE user_id = ?",
                                (earned, tg_id)
                            )
                            await db.execute(
                                "INSERT INTO referral_earnings (referrer_id, referral_id, deposit_amount, earned) VALUES (?, ?, ?, ?)",
                                (referrer_id, tg_id, stars_amount, earned)
                            )
                            await db.commit()
            
            return {"invoice_url": res_data["result"]}
        else:
            raise HTTPException(status_code=500, detail="Ошибка генерации Telegram Invoice")

# ========== WITHDRAW ==========
@app.post("/api/withdraw")
async def create_withdraw(amount: int, wallet: str, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    
    if amount < 100:
        raise HTTPException(status_code=400, detail="Минимальный вывод — 100 Stars")
    if amount > MAX_WITHDRAW_AMOUNT:
        raise HTTPException(status_code=400, detail=f"Максимальный вывод — {MAX_WITHDRAW_AMOUNT} Stars")
    
    user_info = await get_or_create_user(tg_id)
    if user_info["balance"] < amount:
        raise HTTPException(status_code=400, detail="Недостаточно монет")
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT last_withdraw_at FROM withdraw_cooldowns WHERE user_id = ?", (tg_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                last_withdraw = row[0]
                try:
                    last_ts = time.mktime(time.strptime(last_withdraw, "%Y-%m-%d %H:%M:%S"))
                except:
                    last_ts = 0
                
                if time.time() - last_ts < WITHDRAW_COOLDOWN_HOURS * 3600:
                    hours_left = int((WITHDRAW_COOLDOWN_HOURS * 3600 - (time.time() - last_ts)) / 3600)
                    raise HTTPException(
                        status_code=400, 
                        detail=f"Следующий вывод через {hours_left} ч."
                    )
        
        fee = int(amount * WITHDRAW_FEE)
        payout = amount - fee
        
        new_balance = user_info["balance"] - amount
        
        await db.execute("UPDATE users SET balance = ? WHERE tg_id = ?", (new_balance, tg_id))
        await db.execute(
            "INSERT INTO withdraws (tg_id, amount, requisites, status) VALUES (?, ?, ?, 'pending')",
            (tg_id, amount, wallet)
        )
        
        await db.execute(
            "INSERT OR REPLACE INTO withdraw_cooldowns (user_id, last_withdraw_at) VALUES (?, CURRENT_TIMESTAMP)",
            (tg_id,)
        )
        
        await db.commit()
    
    return {
        "status": "pending", 
        "payout": payout, 
        "fee": fee,
        "new_balance": new_balance
    }

@app.get("/api/admin/withdraws")
async def get_admin_withdraws(user: dict = Depends(verify_telegram_data)):
    if not ADMIN_TG_ID or user.get('id') != ADMIN_TG_ID:
        raise HTTPException(status_code=403, detail="Доступ запрещен.")
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, tg_id, amount, requisites, status, created_at FROM withdraws ORDER BY id DESC") as cursor:
            rows = await cursor.fetchall()
            return [{"id": r[0], "tg_id": r[1], "amount": r[2], "requisites": r[3], "status": r[4], "date": r[5]} for r in rows]

@app.post("/api/admin/withdraw/status")
async def update_withdraw_status(req: UpdateWithdrawStatusRequest, user: dict = Depends(verify_telegram_data)):
    if not ADMIN_TG_ID or user.get('id') != ADMIN_TG_ID:
        raise HTTPException(status_code=403, detail="Доступ запрещен.")
    async with aiosqlite.connect(DB_NAME) as db:
        if req.status == "rejected":
            async with db.execute("SELECT tg_id, amount FROM withdraws WHERE id = ?", (req.ticket_id,)) as cursor:
                ticket = await cursor.fetchone()
                if ticket:
                    await db.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (ticket[1], ticket[0]))
        await db.execute("UPDATE withdraws SET status = ? WHERE id = ?", (req.status, req.ticket_id))
        await db.commit()
    return {"success": True, "new_status": req.status}

# ========== REFERRAL ENDPOINTS ==========
@app.post("/api/referral/activate")
async def activate_referral(req: ReferralActivateRequest, user: dict = Depends(verify_telegram_data)):
    user_id = user.get('id')
    referrer_id = req.referrer_id
    
    if user_id == referrer_id:
        raise HTTPException(status_code=400, detail="Нельзя быть своим реферером")
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT tg_id FROM users WHERE tg_id = ?", (referrer_id,)) as cursor:
            if not await cursor.fetchone():
                raise HTTPException(status_code=400, detail="Реферер не найден")
        
        async with db.execute("SELECT referrer_id FROM referrals WHERE user_id = ?", (user_id,)) as cursor:
            if await cursor.fetchone():
                raise HTTPException(status_code=400, detail="Реферал уже активирован")
        
        await db.execute(
            "INSERT INTO referrals (user_id, referrer_id) VALUES (?, ?)",
            (user_id, referrer_id)
        )
        await db.commit()
    
    return {"success": True, "referrer_id": referrer_id}

@app.get("/api/referral/stats")
async def get_referral_stats(user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT total_earned FROM referrals WHERE user_id = ?", (tg_id,)) as cursor:
            earned = await cursor.fetchone()
            total_earned = earned[0] if earned else 0
        
        async with db.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (tg_id,)) as cursor:
            count = await cursor.fetchone()
            referrals_count = count[0] if count else 0
        
        async with db.execute(
            "SELECT referral_id, deposit_amount, earned, created_at FROM referral_earnings WHERE referrer_id = ? ORDER BY created_at DESC LIMIT 20",
            (tg_id,)
        ) as cursor:
            history = await cursor.fetchall()
            earnings_history = [
                {
                    "referral_id": r[0],
                    "deposit": r[1],
                    "earned": r[2],
                    "date": r[3]
                }
                for r in history
            ]
    
    return {
        "total_earned": total_earned,
        "referrals_count": referrals_count,
        "percent": REFERRAL_PERCENT,
        "history": earnings_history
    }

# ========== CRASH HTTP ENDPOINTS ==========
@app.get("/api/crash/history")
async def crash_history():
    return {
        "history": crash_state["history"][:15],
        "server_seed_hash": SERVER_SEED_HASH
    }

@app.get("/api/crash/verify")
async def verify_crash(server_seed: str, nonce: int):
    message = f"{server_seed}:{nonce}"
    hash_hex = hashlib.sha256(message.encode()).hexdigest()
    h = int(hash_hex[:16], 16)
    r = h / (2**64)
    
    if r < 0.30:
        crash_point = round(1.01 + (r / 0.30) * 0.09, 2)
    elif r < 0.60:
        crash_point = round(1.10 + ((r - 0.30) / 0.30) * 0.20, 2)
    elif r < 0.82:
        crash_point = round(1.30 + ((r - 0.60) / 0.22) * 0.50, 2)
    elif r < 0.94:
        crash_point = round(1.80 + ((r - 0.82) / 0.12) * 1.20, 2)
    elif r < 0.98:
        crash_point = round(3.00 + ((r - 0.94) / 0.04) * 5.00, 2)
    elif r < 0.995:
        crash_point = round(8.00 + ((r - 0.98) / 0.015) * 12.00, 2)
    else:
        crash_point = round(20.00 + ((r - 0.995) / 0.005) * 30.00, 2)
    
    crash_point = min(crash_point, 50.0)
    
    return {
        "verified": True,
        "crash_point": crash_point,
        "hash": hash_hex
    }
