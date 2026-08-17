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

# ========== UC ЯЩИКИ (6 шт) ==========
STAR_CASE_PRICES = {
    "star_case_1": 50,
    "star_case_2": 150,
    "star_case_3": 400,
    "star_case_4": 750,
    "star_case_5": 1500,
    "star_case_6": 2500
}

STAR_CASE_DROPS = {
    "star_case_1": {
        "s1_1": ("⭐ 12 UC", 12),
        "s1_2": ("⭐ 29 UC", 29),
        "s1_3": ("⭐ 46 UC", 46),
        "s1_4": ("⭐ 69 UC", 69),
        "s1_5": ("⭐ 115 UC", 115),
        "s1_6": ("⭐ 230 UC", 230)
    },
    "star_case_2": {
        "s2_1": ("⭐ 35 UC", 35),
        "s2_2": ("⭐ 86 UC", 86),
        "s2_3": ("⭐ 138 UC", 138),
        "s2_4": ("⭐ 230 UC", 230),
        "s2_5": ("⭐ 403 UC", 403),
        "s2_6": ("⭐ 690 UC", 690)
    },
    "star_case_3": {
        "s3_1": ("⭐ 92 UC", 92),
        "s3_2": ("⭐ 230 UC", 230),
        "s3_3": ("⭐ 403 UC", 403),
        "s3_4": ("⭐ 575 UC", 575),
        "s3_5": ("⭐ 920 UC", 920),
        "s3_6": ("⭐ 1725 UC", 1725)
    },
    "star_case_4": {
        "s4_1": ("⭐ 173 UC", 173),
        "s4_2": ("⭐ 460 UC", 460),
        "s4_3": ("⭐ 748 UC", 748),
        "s4_4": ("⭐ 1150 UC", 1150),
        "s4_5": ("⭐ 2070 UC", 2070),
        "s4_6": ("⭐ 3450 UC", 3450)
    },
    "star_case_5": {
        "s5_1": ("⭐ 345 UC", 345),
        "s5_2": ("⭐ 920 UC", 920),
        "s5_3": ("⭐ 1495 UC", 1495),
        "s5_4": ("⭐ 2300 UC", 2300),
        "s5_5": ("⭐ 4025 UC", 4025),
        "s5_6": ("⭐ 6900 UC", 6900)
    },
    "star_case_6": {
        "s6_1": ("⭐ 575 UC", 575),
        "s6_2": ("⭐ 1495 UC", 1495),
        "s6_3": ("⭐ 2530 UC", 2530),
        "s6_4": ("⭐ 4025 UC", 4025),
        "s6_5": ("⭐ 6325 UC", 6325),
        "s6_6": ("⭐ 11500 UC", 11500)
    }
}

# ========== PUBG СКИНЫ (6 шт) ==========
NFT_CASE_PRICES = {
    "nft_case_1": 100,
    "nft_case_2": 250,
    "nft_case_3": 500,
    "nft_case_4": 1000,
    "nft_case_5": 1750,
    "nft_case_6": 3000
}

NFT_CASE_DROPS = {
    "nft_case_1": {
        "n1_1": ("🎽 Brown Shirt", 23),
        "n1_2": ("🧢 Grey Cap", 40),
        "n1_3": ("👖 Cargo Pants", 63),
        "n1_4": ("🎒 Level 1 Backpack", 92),
        "n1_5": ("🪖 Steel Helmet", 150),
        "n1_6": ("🥾 Military Boots", 288)
    },
    "nft_case_2": {
        "n2_1": ("🧣 Red Scarf", 52),
        "n2_2": ("🧤 Tactical Gloves", 86),
        "n2_3": ("🦺 Police Vest", 138),
        "n2_4": ("🎽 Sports Top", 219),
        "n2_5": ("👒 Straw Hat", 345),
        "n2_6": ("🪖 Level 2 Helmet", 575)
    },
    "nft_case_3": {
        "n3_1": ("🧥 Leather Jacket", 104),
        "n3_2": ("👖 Jeans", 173),
        "n3_3": ("👟 Sneakers", 276),
        "n3_4": ("🎒 Level 2 Backpack", 437),
        "n3_5": ("🛡️ Riot Shield", 690),
        "n3_6": ("🪖 Level 3 Helmet", 1150)
    },
    "nft_case_4": {
        "n4_1": ("🥋 Martial Arts", 207),
        "n4_2": ("🦺 Ghillie Suit", 345),
        "n4_3": ("🪖 Spetsnaz Helmet", 552),
        "n4_4": ("🎒 Level 3 Backpack", 863),
        "n4_5": ("🔫 M416 Skin", 1380),
        "n4_6": ("🎯 AWM Skin", 2300)
    },
    "nft_case_5": {
        "n5_1": ("👑 Crown", 368),
        "n5_2": ("🦅 Eagle Mask", 610),
        "n5_3": ("🐯 Tiger Suit", 978),
        "n5_4": ("🤖 Robot Suit", 1553),
        "n5_5": ("🔥 Flame Jacket", 2415),
        "n5_6": ("💎 Diamond Helmet", 4025)
    },
    "nft_case_6": {
        "n6_1": ("🦺 Golden Ghillie", 690),
        "n6_2": ("🔫 Golden AKM", 1150),
        "n6_3": ("🎯 Golden AWM", 1840),
        "n6_4": ("👑 Royal Crown", 2875),
        "n6_5": ("🐲 Dragon Suit", 4600),
        "n6_6": ("⭐ Legendary Set", 8050)
    }
}

# ========== НОВЫЕ ШАНСЫ ДЛЯ КЕЙСОВ (более щедрые) ==========
# Для UC ящиков — смещение в сторону редких
STAR_DROP_WEIGHTS = [25.0, 22.0, 18.0, 15.0, 12.0, 8.0]
# Для скинов — тоже чуть щедрее
NFT_DROP_WEIGHTS = [20.0, 18.0, 16.0, 14.0, 12.0, 10.0]

# ========== CRASH (Парашют) ==========
CRASH_MIN_BET = 25
CRASH_MAX_BET = 5000
CRASH_BETTING_TIME = 6
CRASH_COOLDOWN = 3
CRASH_HOUSE_EDGE = 0.04
CRASH_SPEED = 0.08

# ========== MINES (Минное поле) ==========
MINES_GRID_SIZE = 4
MINES_MIN_COUNT = 1
MINES_MAX_COUNT = 15
MINES_HOUSE_EDGE = 0.05

active_mines_games = {}

def generate_mines_grid(mines_count):
    """Генерация минного поля"""
    total_cells = MINES_GRID_SIZE * MINES_GRID_SIZE
    grid = [0] * total_cells
    mine_positions = random.sample(range(total_cells), mines_count)
    for pos in mine_positions:
        grid[pos] = 1
    return grid

def calculate_mines_multiplier(mines_count, opened):
    """Расчёт множителя для мин"""
    total_cells = MINES_GRID_SIZE * MINES_GRID_SIZE
    safe_cells = total_cells - mines_count
    if opened >= safe_cells:
        return round((1 - MINES_HOUSE_EDGE) * 100, 2)
    probability = 1.0
    for i in range(opened):
        probability *= (safe_cells - i) / (total_cells - i)
    multiplier = ((1 - MINES_HOUSE_EDGE) / probability)
    max_multiplier = {
        1: 5.0, 2: 10.0, 3: 20.0, 5: 50.0,
        7: 100.0, 10: 300.0, 12: 500.0, 14: 1000.0, 15: 2000.0
    }
    closest = min(max_multiplier.keys(), key=lambda k: abs(k - mines_count))
    return round(min(multiplier, max_multiplier.get(closest, 50.0)), 2)

# ========== НАСТРОЙКИ ==========
REFERRAL_PERCENT = 7
MAX_WITHDRAW_AMOUNT = 50000
WITHDRAW_FEE = 0.05
WITHDRAW_COOLDOWN_HOURS = 24
MIN_DEPOSIT_FOR_REFERRAL = 50
TOP_PRIZES = {1: 1500, 2: 1000, 3: 500, 4: 100, 5: 50}
TOP_MIN_PLAYERS = 100
TOP_RESET_DAYS = 14

# ========== АПГРЕЙД-РУЛЕТКА (НОВАЯ ЛОГИКА) ==========
def get_upgrade_chance(current_price, target_price):
    """Шанс апгрейда с жестким урезанием (House Edge ~15-20%)"""
    ratio = target_price / current_price
    
    # Базовый шанс (было щедрее, теперь режем)
    if ratio >= 20:
        base_chance = 0.5   # было 1%
    elif ratio >= 10:
        base_chance = 1.5   # было 3%
    elif ratio >= 5:
        base_chance = 4.0   # было 8%
    elif ratio >= 3:
        base_chance = 8.0   # было 15%
    elif ratio >= 2:
        base_chance = 16.0  # было 30%
    elif ratio >= 1.5:
        base_chance = 30.0  # было 50%
    else:
        base_chance = 50.0  # было 70%
    
    # Жесткое урезание — дом всегда в плюсе
    # Чем дороже цель, тем сильнее режем
    cut_factor = 1.0 - (ratio / 30)  # максимальный рез ~30%
    final_chance = max(base_chance * max(cut_factor, 0.7), 0.5)
    
    # Округляем до 2 знаков
    return round(final_chance, 2)

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

def bet_key(tg_id, round_id):
    """Ключ ставки"""
    return f"{tg_id}:{round_id}"

def generate_crash_point():
    """Provably Fair генерация точки краша"""
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
        cp = round(1.01 + (r / 0.30) * 0.09, 2)
    elif r < 0.60:
        cp = round(1.10 + ((r - 0.30) / 0.30) * 0.20, 2)
    elif r < 0.82:
        cp = round(1.30 + ((r - 0.60) / 0.22) * 0.50, 2)
    elif r < 0.94:
        cp = round(1.80 + ((r - 0.82) / 0.12) * 1.20, 2)
    elif r < 0.98:
        cp = round(3.00 + ((r - 0.94) / 0.04) * 5.00, 2)
    elif r < 0.995:
        cp = round(8.00 + ((r - 0.98) / 0.015) * 12.00, 2)
    else:
        cp = round(20.00 + ((r - 0.995) / 0.005) * 30.00, 2)
    return min(cp, 50.0), hash_hex

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

# ========== МОДЕЛИ ==========
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

class MinesStartRequest(BaseModel):
    bet_amount: int
    mines_count: int

class MinesOpenRequest(BaseModel):
    game_id: str
    cell_index: int

class MinesCashoutRequest(BaseModel):
    game_id: str

class UpgradeItemRequest(BaseModel):
    item_index: int
    target_price: int

class PromoCreateRequest(BaseModel):
    code: str
    reward_type: str
    case_type: str = None
    stars: int = 0
    max_uses: int = 1

# ========== SOCKET.IO ==========
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
    except:
        return await sio.emit('error', {'message': 'Неверный ID'}, to=sid)
    if tg_id <= 0:
        return await sio.emit('error', {'message': 'Неверный ID'}, to=sid)
    try:
        bet = int(data.get('bet_amount', 0))
    except:
        return await sio.emit('error', {'message': 'Неверная сумма'}, to=sid)
    
    if crash_state["status"] != "betting":
        return await sio.emit('error', {'message': 'Ставки закрыты!'}, to=sid)
    if bet < CRASH_MIN_BET or bet > CRASH_MAX_BET:
        return await sio.emit('error', {'message': f'Ставка {CRASH_MIN_BET}-{CRASH_MAX_BET} UC'}, to=sid)
    
    key = bet_key(tg_id, crash_state["round_id"])
    if key in crash_state["bets"]:
        return await sio.emit('error', {'message': 'Уже есть ставка'}, to=sid)
    
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "UPDATE users SET balance = balance - ?, total_spent = total_spent + ? WHERE tg_id = ? AND balance >= ?",
            (bet, bet, tg_id, bet)
        )
        await db.commit()
        if cursor.rowcount == 0:
            return await sio.emit('error', {'message': 'Недостаточно UC'}, to=sid)
        async with db.execute("SELECT balance FROM users WHERE tg_id = ?", (tg_id,)) as cursor:
            new_balance = (await cursor.fetchone())[0]
    
    crash_state["bets"][key] = {
        "tg_id": tg_id,
        "bet": bet,
        "username": data.get('username', 'Игрок'),
        "round_id": crash_state["round_id"],
        "cashed_out": False,
        "cashed_at": 0,
        "sid": sid
    }
    
    await sio.emit('bet_placed', {
        'tg_id': tg_id,
        'username': data.get('username', 'Игрок'),
        'amount': bet,
        'balance': new_balance,
        'round_id': crash_state["round_id"]
    }, to=sid)
    await sio.emit('bets_update', {
        'count': len(crash_state["bets"]),
        'total': sum(b["bet"] for b in crash_state["bets"].values())
    })

@sio.event
async def cashout(sid, data):
    try:
        tg_id = int(data.get('tg_id', 0))
    except:
        return await sio.emit('error', {'message': 'Неверный ID'}, to=sid)
    if tg_id <= 0:
        return await sio.emit('error', {'message': 'Неверный ID'}, to=sid)
    if crash_state["crashed"]:
        return await sio.emit('error', {'message': 'Парашют не раскрылся!'}, to=sid)
    if crash_state["status"] != "flying":
        return await sio.emit('error', {'message': 'Не время'}, to=sid)
    
    key = bet_key(tg_id, crash_state["round_id"])
    bet_data = crash_state["bets"].get(key)
    if not bet_data:
        return await sio.emit('error', {'message': 'Нет ставки'}, to=sid)
    if bet_data["cashed_out"]:
        return await sio.emit('error', {'message': 'Уже забрали'}, to=sid)
    
    current_mult = crash_state["current_multiplier"]
    if current_mult >= crash_state["crash_point"]:
        return await sio.emit('error', {'message': 'Поздно!'}, to=sid)
    
    win_amount = int(bet_data["bet"] * current_mult)
    bet_data["cashed_out"] = True
    bet_data["cashed_at"] = current_mult
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (win_amount, tg_id))
        await db.commit()
        async with db.execute("SELECT balance FROM users WHERE tg_id = ?", (tg_id,)) as cursor:
            new_balance = (await cursor.fetchone())[0]
    
    await sio.emit('cashout_success', {
        'multiplier': round(current_mult, 2),
        'win_amount': win_amount,
        'profit': win_amount - bet_data["bet"],
        'balance': new_balance,
        'round_id': crash_state["round_id"]
    }, to=sid)
    await sio.emit('player_cashout', {
        'username': bet_data["username"],
        'amount': bet_data["bet"],
        'multiplier': round(current_mult, 2),
        'win': win_amount
    })

# ========== СТАРТ ==========
@app.on_event("startup")
async def startup():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS users (tg_id INTEGER PRIMARY KEY, username TEXT DEFAULT 'Игрок', balance INTEGER DEFAULT 20, total_spent INTEGER DEFAULT 0, inventory TEXT DEFAULT '[]')")
        await db.execute("CREATE TABLE IF NOT EXISTS withdraws (id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, amount INTEGER, requisites TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("CREATE TABLE IF NOT EXISTS referrals (user_id INTEGER PRIMARY KEY, referrer_id INTEGER NOT NULL, activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, total_earned INTEGER DEFAULT 0)")
        await db.execute("CREATE TABLE IF NOT EXISTS referral_earnings (id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id INTEGER NOT NULL, referral_id INTEGER NOT NULL, deposit_amount INTEGER NOT NULL, earned INTEGER NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("CREATE TABLE IF NOT EXISTS withdraw_cooldowns (user_id INTEGER PRIMARY KEY, last_withdraw_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("CREATE TABLE IF NOT EXISTS promo_uses (user_id INTEGER, promo_code TEXT, used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, promo_code))")
        await db.execute("CREATE TABLE IF NOT EXISTS promos (code TEXT PRIMARY KEY, reward_type TEXT, case_type TEXT, stars INTEGER DEFAULT 0, max_uses INTEGER DEFAULT 1, uses INTEGER DEFAULT 0, created_by INTEGER)")
        await db.execute("CREATE TABLE IF NOT EXISTS top_rewards (id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, amount INTEGER, place INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("CREATE TABLE IF NOT EXISTS free_case_uses (user_id INTEGER PRIMARY KEY, last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await db.commit()
    asyncio.create_task(crash_game_loop())

# ========== АВТОРИЗАЦИЯ ==========
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
        async with db.execute("SELECT balance, total_spent, inventory FROM users WHERE tg_id = ?", (tg_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                await db.execute("UPDATE users SET username = ? WHERE tg_id = ?", (username, tg_id))
                await db.commit()
                return {"balance": row[0], "total_spent": row[1], "inventory": json.loads(row[2])}
            else:
                start_balance = 10000 if (ADMIN_TG_ID and tg_id == ADMIN_TG_ID) else 20
                await db.execute(
                    "INSERT INTO users (tg_id, username, balance, total_spent, inventory) VALUES (?, ?, ?, 0, ?)",
                    (tg_id, username, start_balance, '[]')
                )
                await db.commit()
                return {"balance": start_balance, "total_spent": 0, "inventory": []}

# ========== ПРОФИЛЬ ==========
@app.get("/api/profile")
async def get_profile(user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    username = user.get('first_name', 'Игрок')
    user_info = await get_or_create_user(tg_id, username)
    user_info["is_admin"] = (ADMIN_TG_ID and tg_id == ADMIN_TG_ID)
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (tg_id,)) as cursor:
            user_info["friends_count"] = (await cursor.fetchone())[0]
        
        async with db.execute("SELECT last_used FROM free_case_uses WHERE user_id = ?", (tg_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                try:
                    last_ts = time.mktime(time.strptime(row[0], "%Y-%m-%d %H:%M:%S"))
                except:
                    last_ts = 0
                user_info["free_case_available"] = (time.time() - last_ts) >= 86400
            else:
                user_info["free_case_available"] = True
    
    return user_info

# ========== АДМИН ==========
@app.post("/api/admin/give_stars")
async def admin_give_stars(user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    if not ADMIN_TG_ID or tg_id != ADMIN_TG_ID:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance + 10000 WHERE tg_id = ?", (tg_id,))
        await db.commit()
    return {"success": True, "message": "✅ +10,000 UC"}

@app.post("/api/admin/give_stars_to_user")
async def admin_give_stars_to_user(req: AdminGiveStarsRequest, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    if not ADMIN_TG_ID or tg_id != ADMIN_TG_ID:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    if req.target_tg_id <= 0:
        raise HTTPException(status_code=400, detail="Неверный ID")
    if req.amount < 1:
        raise HTTPException(status_code=400, detail="Сумма > 0")
    if req.amount > 1000000:
        raise HTTPException(status_code=400, detail="Макс 1M UC")
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (tg_id, username, balance, total_spent, inventory) VALUES (?, ?, 20, 0, '[]')",
            (req.target_tg_id, f"Player_{req.target_tg_id}")
        )
        await db.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (req.amount, req.target_tg_id))
        await db.commit()
    
    return {"success": True, "message": f"✅ Выдано {req.amount} UC"}

# ========== ПРОМОКОДЫ ==========
@app.post("/api/admin/promo/create")
async def create_promo(req: PromoCreateRequest, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    if not ADMIN_TG_ID or tg_id != ADMIN_TG_ID:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    code = req.code.strip().upper()
    
    async with aiosqlite.connect(DB_NAME) as db:
        if await (await db.execute("SELECT code FROM promos WHERE code = ?", (code,))).fetchone():
            raise HTTPException(status_code=400, detail="Такой код уже существует")
        await db.execute(
            "INSERT INTO promos (code, reward_type, case_type, stars, max_uses, created_by) VALUES (?, ?, ?, ?, ?, ?)",
            (code, req.reward_type, req.case_type, req.stars, req.max_uses, tg_id)
        )
        await db.commit()
    
    return {"success": True, "message": f"✅ Промокод {code} создан!"}

@app.post("/api/promo/activate")
async def activate_promo(code: str, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    code = code.strip().upper()
    
    async with aiosqlite.connect(DB_NAME) as db:
        promo = await (await db.execute("SELECT * FROM promos WHERE code = ?", (code,))).fetchone()
        if not promo:
            raise HTTPException(status_code=400, detail="Неверный промокод")
        if promo[5] >= promo[4]:
            raise HTTPException(status_code=400, detail="Промокод закончился")
        if await (await db.execute("SELECT used_at FROM promo_uses WHERE user_id = ? AND promo_code = ?", (tg_id, code))).fetchone():
            raise HTTPException(status_code=400, detail="Вы уже использовали этот промокод!")
        
        reward_name = None
        if promo[1] == "case":
            ct = promo[2]
            pool = STAR_CASE_DROPS[ct] if ct in STAR_CASE_PRICES else NFT_CASE_DROPS[ct]
            weights = STAR_DROP_WEIGHTS if ct in STAR_CASE_PRICES else NFT_DROP_WEIGHTS
            reward_id = random.choices(list(pool.keys()), weights=weights, k=1)[0]
            reward_name = pool[reward_id][0]
            
            async with db.execute("SELECT inventory FROM users WHERE tg_id = ?", (tg_id,)) as cursor:
                inventory = json.loads((await cursor.fetchone())[0] or '[]')
            inventory.append({"id": reward_id, "name": reward_name, "case": ct})
            await db.execute("UPDATE users SET inventory = ? WHERE tg_id = ?", (json.dumps(inventory), tg_id))
        elif promo[1] == "stars":
            await db.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (promo[3], tg_id))
            reward_name = f"⭐ {promo[3]} UC"
        
        await db.execute("INSERT INTO promo_uses (user_id, promo_code) VALUES (?, ?)", (tg_id, code))
        await db.execute("UPDATE promos SET uses = uses + 1 WHERE code = ?", (code,))
        await db.commit()
    
    return {"success": True, "message": f"🎉 Промокод {code} активирован!", "reward": reward_name}

# ========== ПРИЗЫ ТОПА ==========
@app.post("/api/admin/top/reward")
async def reward_top_players(user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    if not ADMIN_TG_ID or tg_id != ADMIN_TG_ID:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            total_players = (await cursor.fetchone())[0]
        if total_players < TOP_MIN_PLAYERS:
            raise HTTPException(status_code=400, detail=f"Нужно минимум {TOP_MIN_PLAYERS} игроков. Сейчас: {total_players}")
        
        async with db.execute("SELECT tg_id, username, total_spent FROM users ORDER BY total_spent DESC LIMIT 5") as cursor:
            top5 = await cursor.fetchall()
        
        for i, (tid, tname, tspent) in enumerate(top5):
            place = i + 1
            prize = TOP_PRIZES.get(place, 0)
            if prize > 0:
                await db.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (prize, tid))
                await db.execute("INSERT INTO top_rewards (tg_id, amount, place) VALUES (?, ?, ?)", (tid, prize, place))
        
        await db.commit()
    
    return {"success": True, "message": "✅ Призы выплачены топ-5!", "top": [(t[1], t[2], TOP_PRIZES.get(i+1, 0)) for i, t in enumerate(top5)]}

@app.get("/api/top/rewards")
async def get_top_rewards(user: dict = Depends(verify_telegram_data)):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT tg_id, amount, place, created_at FROM top_rewards ORDER BY created_at DESC LIMIT 20") as cursor:
            rows = await cursor.fetchall()
        return [{"tg_id": r[0], "amount": r[1], "place": r[2], "date": r[3]} for r in rows]

# ========== БЕСПЛАТНЫЙ ЯЩИК ==========
@app.post("/api/free_case/claim")
async def claim_free_case(user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    
    async with aiosqlite.connect(DB_NAME) as db:
        row = await (await db.execute("SELECT last_used FROM free_case_uses WHERE user_id = ?", (tg_id,))).fetchone()
        if row:
            try:
                last_ts = time.mktime(time.strptime(row[0], "%Y-%m-%d %H:%M:%S"))
            except:
                last_ts = 0
            if time.time() - last_ts < 86400:
                raise HTTPException(status_code=400, detail="Бесплатный ящик уже использован! Приходите через 24 часа.")
        
        await db.execute("INSERT OR REPLACE INTO free_case_uses (user_id, last_used) VALUES (?, CURRENT_TIMESTAMP)", (tg_id,))
        
        pool = STAR_CASE_DROPS["star_case_1"]
        weights = STAR_DROP_WEIGHTS
        reward_id = random.choices(list(pool.keys()), weights=weights, k=1)[0]
        reward_name = pool[reward_id][0]
        
        async with db.execute("SELECT inventory FROM users WHERE tg_id = ?", (tg_id,)) as cursor:
            inventory = json.loads((await cursor.fetchone())[0] or '[]')
        inventory.append({"id": reward_id, "name": reward_name, "case": "star_case_1"})
        await db.execute("UPDATE users SET inventory = ? WHERE tg_id = ?", (json.dumps(inventory), tg_id))
        await db.commit()
    
    return {"success": True, "reward": reward_name}

# ========== ОТКРЫТИЕ ЯЩИКА (НОВАЯ ЛОГИКА) ==========
@app.post("/api/case/open")
async def open_case(req: OpenCaseRequest, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    
    # Определяем кейс
    if req.case_type in STAR_CASE_PRICES:
        price = STAR_CASE_PRICES[req.case_type]
        pool = STAR_CASE_DROPS[req.case_type]
        weights = STAR_DROP_WEIGHTS  # уже обновлены (более щедрые)
        is_star = True
    elif req.case_type in NFT_CASE_PRICES:
        price = NFT_CASE_PRICES[req.case_type]
        pool = NFT_CASE_DROPS[req.case_type]
        weights = NFT_DROP_WEIGHTS  # уже обновлены (более щедрые)
        is_star = False
    else:
        raise HTTPException(status_code=400, detail="Неизвестный ящик")
    
    # Проверка баланса
    user_info = await get_or_create_user(tg_id)
    if user_info["balance"] < price:
        raise HTTPException(status_code=400, detail="Недостаточно UC")
    
    # Выбор предмета с обновлёнными шансами
    reward_id = random.choices(list(pool.keys()), weights=weights, k=1)[0]
    reward_name = pool[reward_id][0]
    
    # Обновляем баланс и инвентарь
    new_balance = user_info["balance"] - price
    user_info["inventory"].append({"id": reward_id, "name": reward_name, "case": req.case_type})
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET balance = ?, total_spent = total_spent + ?, inventory = ? WHERE tg_id = ?",
            (new_balance, price, json.dumps(user_info["inventory"]), tg_id)
        )
        await db.commit()
    
    return {
        "reward_id": reward_id,
        "reward_name": reward_name,
        "balance": new_balance,
        "case_type": req.case_type,
        "price": price
    }

# ========== ИНВЕНТАРЬ ==========
@app.post("/api/inventory/sell_item")
async def sell_single_item(req: SellItemRequest, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    user_info = await get_or_create_user(tg_id)
    inventory = user_info["inventory"]
    
    if req.item_index < 0 or req.item_index >= len(inventory):
        raise HTTPException(status_code=400, detail="Предмет не найден")
    
    item = inventory.pop(req.item_index)
    gain = 0
    if item["case"] in STAR_CASE_DROPS and item["id"] in STAR_CASE_DROPS[item["case"]]:
        gain = STAR_CASE_DROPS[item["case"]][item["id"]][1]
    elif item["case"] in NFT_CASE_DROPS and item["id"] in NFT_CASE_DROPS[item["case"]]:
        gain = NFT_CASE_DROPS[item["case"]][item["id"]][1]
    
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
        if item["case"] in STAR_CASE_DROPS and item["id"] in STAR_CASE_DROPS[item["case"]]:
            total_gain += STAR_CASE_DROPS[item["case"]][item["id"]][1]
        elif item["case"] in NFT_CASE_DROPS and item["id"] in NFT_CASE_DROPS[item["case"]]:
            total_gain += NFT_CASE_DROPS[item["case"]][item["id"]][1]
    
    new_balance = user_info["balance"] + total_gain
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = ?, inventory = '[]' WHERE tg_id = ?", (new_balance, tg_id))
        await db.commit()
    
    return {"gain": total_gain, "balance": new_balance}

# ========== АПГРЕЙД-РУЛЕТКА (НОВАЯ ЛОГИКА) ==========
@app.post("/api/inventory/upgrade")
async def upgrade_item(req: UpgradeItemRequest, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    user_info = await get_or_create_user(tg_id)
    inventory = user_info["inventory"]
    
    if req.item_index < 0 or req.item_index >= len(inventory):
        raise HTTPException(status_code=400, detail="Предмет не найден")
    
    item = inventory[req.item_index]
    
    # Находим текущую цену
    current_price = 0
    for ct, drops in {**STAR_CASE_DROPS, **NFT_CASE_DROPS}.items():
        for did, (dname, dprice) in drops.items():
            if dname == item["name"] and ct == item["case"]:
                current_price = dprice
                break
        if current_price > 0:
            break
    
    if current_price == 0:
        raise HTTPException(status_code=400, detail="Нельзя улучшить")
    
    if req.target_price <= current_price:
        raise HTTPException(status_code=400, detail="Цель должна быть дороже")
    
    # ========== НОВАЯ ФОРМУЛА ШАНСА (урезанная, сложнее) ==========
    ratio = req.target_price / current_price
    
    # Базовая формула с сильным урезанием
    if ratio >= 20:
        base_chance = 0.5   # 0.5%
    elif ratio >= 15:
        base_chance = 0.8   # 0.8%
    elif ratio >= 10:
        base_chance = 1.5   # 1.5%
    elif ratio >= 7:
        base_chance = 2.5   # 2.5%
    elif ratio >= 5:
        base_chance = 4.0   # 4%
    elif ratio >= 3:
        base_chance = 8.0   # 8%
    elif ratio >= 2:
        base_chance = 15.0  # 15%
    elif ratio >= 1.5:
        base_chance = 25.0  # 25%
    else:
        base_chance = 40.0  # 40%
    
    # Дополнительное урезание для дорогих предметов (house edge 20%)
    house_edge = 0.20
    final_chance = base_chance * (1 - house_edge)
    
    # Дополнительное урезание если цель > 5000 UC
    if req.target_price > 5000:
        final_chance *= 0.7
    elif req.target_price > 2000:
        final_chance *= 0.85
    
    # Округляем до процентов для отображения
    chance_percent = int(round(final_chance))
    if chance_percent < 0.5:
        chance_percent = 0.5
    
    # Проверяем существование цели
    target_item = None
    for ct, drops in {**STAR_CASE_DROPS, **NFT_CASE_DROPS}.items():
        for did, (dname, dprice) in drops.items():
            if dprice == req.target_price:
                target_item = {"id": did, "name": dname, "case": ct}
                break
        if target_item:
            break
    
    if not target_item:
        raise HTTPException(status_code=400, detail="Предмет с такой ценой не найден")
    
    # Вычисляем углы для анимации
    import random
    import math
    
    # Генерируем случайное смещение для WIN сектора
    sector_offset = random.randint(0, 360)
    
    # WIN сектор занимает chance_percent % от круга
    win_sector_size = (chance_percent / 100) * 360
    win_start_angle = sector_offset
    win_end_angle = win_start_angle + win_sector_size
    
    # Решаем: победа или провал
    is_win = random.random() < (final_chance / 100)
    
    # Вычисляем угол, на который должна указать стрелка
    if is_win:
        # Попадаем внутрь WIN сектора
        target_angle = win_start_angle + random.random() * win_sector_size
    else:
        # Попадаем в LOSE сектор (за пределами WIN)
        lose_sector_size = 360 - win_sector_size
        if lose_sector_size <= 0:
            target_angle = win_start_angle
        else:
            lose_start = win_end_angle
            target_angle = lose_start + random.random() * lose_sector_size
            if target_angle >= 360:
                target_angle -= 360
    
    # Добавляем несколько полных оборотов для красоты
    rotations = 3 + random.randint(0, 3)
    final_angle = rotations * 360 + target_angle
    
    if is_win:
        # УСПЕХ — заменяем предмет
        inventory[req.item_index] = target_item
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET inventory = ? WHERE tg_id = ?", (json.dumps(inventory), tg_id))
            await db.commit()
        return {
            "success": True,
            "chance": chance_percent,
            "angle": final_angle,
            "win_angle": target_angle,
            "win_sector_start": win_start_angle,
            "win_sector_end": win_end_angle,
            "message": f"Успех! {item['name']} → {target_item['name']}"
        }
    else:
        # ПРОВАЛ — предмет сгорает
        inventory.pop(req.item_index)
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET inventory = ? WHERE tg_id = ?", (json.dumps(inventory), tg_id))
            await db.commit()
        return {
            "success": False,
            "chance": chance_percent,
            "angle": final_angle,
            "win_angle": target_angle,
            "win_sector_start": win_start_angle,
            "win_sector_end": win_end_angle,
            "message": f"Провал! {item['name']} сгорел"
        }

# ========== ЛИДЕРБОРД ==========
@app.get("/api/leaderboard")
async def get_leaderboard(user: dict = Depends(verify_telegram_data)):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT username, balance FROM users ORDER BY balance DESC LIMIT 10") as cursor:
            rows = await cursor.fetchall()
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            total = (await cursor.fetchone())[0]
    return {"players": [{"username": r[0], "balance": r[1]} for r in rows], "total_players": total, "min_for_prizes": TOP_MIN_PLAYERS, "prizes": TOP_PRIZES}

@app.get("/api/leaderboard/spent")
async def get_spent_leaderboard(user: dict = Depends(verify_telegram_data)):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT username, total_spent FROM users ORDER BY total_spent DESC LIMIT 10") as cursor:
            rows = await cursor.fetchall()
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            total = (await cursor.fetchone())[0]
    return {"players": [{"username": r[0], "total_spent": r[1]} for r in rows], "total_players": total, "min_for_prizes": TOP_MIN_PLAYERS, "prizes": TOP_PRIZES, "reset_days": TOP_RESET_DAYS}

# ========== ПОПОЛНЕНИЕ UC ==========
@app.post("/api/stars/buy")
async def buy_stars(stars_amount: int, user: dict = Depends(verify_telegram_data)):
    if stars_amount < 50:
        raise HTTPException(status_code=400, detail="Минимум 50 UC")
    
    tg_id = user.get('id')
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/createInvoiceLink"
    payload = {
        "title": "Пополнение UC",
        "description": f"Покупка {stars_amount} UC",
        "payload": f"deposit_{tg_id}_{stars_amount}",
        "provider_token": "",
        "currency": "XTR",
        "prices": [{"label": "UC", "amount": stars_amount}]
    }
    async with httpx.AsyncClient() as client:
        res = await client.post(url, json=payload)
        res_data = res.json()
        if res_data.get("ok"):
            if stars_amount >= MIN_DEPOSIT_FOR_REFERRAL:
                async with aiosqlite.connect(DB_NAME) as db:
                    ref = await (await db.execute("SELECT referrer_id FROM referrals WHERE user_id = ?", (tg_id,))).fetchone()
                    if ref:
                        earned = int(stars_amount * REFERRAL_PERCENT / 100)
                        await db.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (earned, ref[0]))
                        await db.execute("UPDATE referrals SET total_earned = total_earned + ? WHERE user_id = ?", (earned, tg_id))
                        await db.execute("INSERT INTO referral_earnings (referrer_id, referral_id, deposit_amount, earned) VALUES (?, ?, ?, ?)", (ref[0], tg_id, stars_amount, earned))
                        await db.commit()
            return {"invoice_url": res_data["result"]}
        else:
            raise HTTPException(status_code=500, detail="Ошибка Telegram Invoice")

# ========== ВЫВОД ==========
@app.post("/api/withdraw")
async def create_withdraw(amount: int, wallet: str, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    
    if amount < 100:
        raise HTTPException(status_code=400, detail="Минимум 100 UC")
    if amount > MAX_WITHDRAW_AMOUNT:
        raise HTTPException(status_code=400, detail=f"Максимум {MAX_WITHDRAW_AMOUNT} UC")
    
    user_info = await get_or_create_user(tg_id)
    if user_info["balance"] < amount:
        raise HTTPException(status_code=400, detail="Недостаточно UC")
    
    async with aiosqlite.connect(DB_NAME) as db:
        row = await (await db.execute("SELECT last_withdraw_at FROM withdraw_cooldowns WHERE user_id = ?", (tg_id,))).fetchone()
        if row:
            try:
                last_ts = time.mktime(time.strptime(row[0], "%Y-%m-%d %H:%M:%S"))
            except:
                last_ts = 0
            if time.time() - last_ts < WITHDRAW_COOLDOWN_HOURS * 3600:
                hours_left = int((WITHDRAW_COOLDOWN_HOURS * 3600 - (time.time() - last_ts)) / 3600)
                raise HTTPException(status_code=400, detail=f"Следующий вывод через {hours_left} ч.")
        
        fee = int(amount * WITHDRAW_FEE)
        payout = amount - fee
        new_balance = user_info["balance"] - amount
        
        await db.execute("UPDATE users SET balance = ? WHERE tg_id = ?", (new_balance, tg_id))
        await db.execute("INSERT INTO withdraws (tg_id, amount, requisites, status) VALUES (?, ?, ?, 'pending')", (tg_id, amount, wallet))
        await db.execute("INSERT OR REPLACE INTO withdraw_cooldowns (user_id, last_withdraw_at) VALUES (?, CURRENT_TIMESTAMP)", (tg_id,))
        await db.commit()
    
    return {"status": "pending", "payout": payout, "fee": fee, "new_balance": new_balance}

@app.get("/api/admin/withdraws")
async def get_admin_withdraws(user: dict = Depends(verify_telegram_data)):
    if not ADMIN_TG_ID or user.get('id') != ADMIN_TG_ID:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, tg_id, amount, requisites, status, created_at FROM withdraws ORDER BY id DESC") as cursor:
            rows = await cursor.fetchall()
            return [{"id": r[0], "tg_id": r[1], "amount": r[2], "requisites": r[3], "status": r[4], "date": r[5]} for r in rows]

@app.post("/api/admin/withdraw/status")
async def update_withdraw_status(req: UpdateWithdrawStatusRequest, user: dict = Depends(verify_telegram_data)):
    if not ADMIN_TG_ID or user.get('id') != ADMIN_TG_ID:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    async with aiosqlite.connect(DB_NAME) as db:
        if req.status == "rejected":
            ticket = await (await db.execute("SELECT tg_id, amount FROM withdraws WHERE id = ?", (req.ticket_id,))).fetchone()
            if ticket:
                await db.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (ticket[1], ticket[0]))
        await db.execute("UPDATE withdraws SET status = ? WHERE id = ?", (req.status, req.ticket_id))
        await db.commit()
    return {"success": True, "new_status": req.status}

# ========== РЕФЕРАЛЫ ==========
@app.post("/api/referral/activate")
async def activate_referral(req: ReferralActivateRequest, user: dict = Depends(verify_telegram_data)):
    user_id = user.get('id')
    referrer_id = req.referrer_id
    
    if user_id == referrer_id:
        raise HTTPException(status_code=400, detail="Нельзя быть своим реферером")
    
    async with aiosqlite.connect(DB_NAME) as db:
        if not await (await db.execute("SELECT tg_id FROM users WHERE tg_id = ?", (referrer_id,))).fetchone():
            raise HTTPException(status_code=400, detail="Реферер не найден")
        if await (await db.execute("SELECT referrer_id FROM referrals WHERE user_id = ?", (user_id,))).fetchone():
            raise HTTPException(status_code=400, detail="Реферал уже активирован")
        await db.execute("INSERT INTO referrals (user_id, referrer_id) VALUES (?, ?)", (user_id, referrer_id))
        await db.commit()
    
    return {"success": True, "referrer_id": referrer_id}

@app.get("/api/referral/stats")
async def get_referral_stats(user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    
    async with aiosqlite.connect(DB_NAME) as db:
        earned = await (await db.execute("SELECT total_earned FROM referrals WHERE user_id = ?", (tg_id,))).fetchone()
        count = await (await db.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (tg_id,))).fetchone()
        history = await (await db.execute("SELECT referral_id, deposit_amount, earned, created_at FROM referral_earnings WHERE referrer_id = ? ORDER BY created_at DESC LIMIT 20", (tg_id,))).fetchall()
    
    return {
        "total_earned": earned[0] if earned else 0,
        "referrals_count": count[0] if count else 0,
        "percent": REFERRAL_PERCENT,
        "history": [{"referral_id": r[0], "deposit": r[1], "earned": r[2], "date": r[3]} for r in history]
    }

# ========== МИНЫ ==========
@app.post("/api/mines/start")
async def mines_start(req: MinesStartRequest, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    
    if req.bet_amount < 10:
        raise HTTPException(status_code=400, detail="Мин. 10 UC")
    if req.bet_amount > 50000:
        raise HTTPException(status_code=400, detail="Макс. 50k UC")
    if req.mines_count < MINES_MIN_COUNT or req.mines_count > MINES_MAX_COUNT:
        raise HTTPException(status_code=400, detail=f"Мины {MINES_MIN_COUNT}-{MINES_MAX_COUNT}")
    if tg_id in active_mines_games:
        raise HTTPException(status_code=400, detail="Завершите игру")
    
    user_info = await get_or_create_user(tg_id)
    if user_info["balance"] < req.bet_amount:
        raise HTTPException(status_code=400, detail="Недостаточно UC")
    
    new_balance = user_info["balance"] - req.bet_amount
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = ?, total_spent = total_spent + ? WHERE tg_id = ?", (new_balance, req.bet_amount, tg_id))
        await db.commit()
    
    grid = generate_mines_grid(req.mines_count)
    game_id = str(uuid.uuid4())[:8]
    active_mines_games[tg_id] = {
        "game_id": game_id,
        "bet": req.bet_amount,
        "mines_count": req.mines_count,
        "grid": grid,
        "opened": [],
        "cashed_out": False,
        "current_multiplier": 1.0
    }
    
    return {
        "game_id": game_id,
        "bet": req.bet_amount,
        "mines_count": req.mines_count,
        "balance": new_balance,
        "total_cells": 16,
        "safe_cells": 16 - req.mines_count
    }

@app.post("/api/mines/open")
async def mines_open_cell(req: MinesOpenRequest, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    
    if tg_id not in active_mines_games:
        raise HTTPException(status_code=400, detail="Нет игры")
    game = active_mines_games[tg_id]
    
    if game["game_id"] != req.game_id:
        raise HTTPException(status_code=400, detail="Неверный ID")
    if game["cashed_out"]:
        raise HTTPException(status_code=400, detail="Завершена")
    if req.cell_index < 0 or req.cell_index >= 16:
        raise HTTPException(status_code=400, detail="Неверная клетка")
    if req.cell_index in game["opened"]:
        raise HTTPException(status_code=400, detail="Открыта")
    
    if game["grid"][req.cell_index] == 1:
        game["cashed_out"] = True
        mines = [i for i, v in enumerate(game["grid"]) if v == 1]
        del active_mines_games[tg_id]
        return {
            "status": "bomb",
            "cell_index": req.cell_index,
            "opened": game["opened"],
            "mines": mines,
            "win_amount": 0,
            "balance": (await get_or_create_user(tg_id))["balance"]
        }
    
    game["opened"].append(req.cell_index)
    multiplier = calculate_mines_multiplier(game["mines_count"], len(game["opened"]))
    game["current_multiplier"] = multiplier
    
    return {
        "status": "safe",
        "cell_index": req.cell_index,
        "opened": game["opened"],
        "opened_count": len(game["opened"]),
        "current_multiplier": multiplier
    }

@app.post("/api/mines/cashout")
async def mines_cashout(req: MinesCashoutRequest, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    
    if tg_id not in active_mines_games:
        raise HTTPException(status_code=400, detail="Нет игры")
    game = active_mines_games[tg_id]
    
    if game["game_id"] != req.game_id:
        raise HTTPException(status_code=400, detail="Неверный ID")
    if game["cashed_out"]:
        raise HTTPException(status_code=400, detail="Завершена")
    if len(game["opened"]) == 0:
        raise HTTPException(status_code=400, detail="Откройте клетку")
    
    win_amount = int(game["bet"] * game["current_multiplier"])
    game["cashed_out"] = True
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (win_amount, tg_id))
        await db.commit()
        new_balance = (await (await db.execute("SELECT balance FROM users WHERE tg_id = ?", (tg_id,))).fetchone())[0]
    
    mines = [i for i, v in enumerate(game["grid"]) if v == 1]
    del active_mines_games[tg_id]
    
    return {
        "status": "cashed_out",
        "multiplier": game["current_multiplier"],
        "win_amount": win_amount,
        "profit": win_amount - game["bet"],
        "balance": new_balance,
        "opened": game["opened"],
        "mines": mines
    }

@app.get("/api/mines/state")
async def mines_get_state(user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    
    if tg_id not in active_mines_games:
        return {"active": False}
    game = active_mines_games[tg_id]
    
    return {
        "active": True,
        "game_id": game["game_id"],
        "bet": game["bet"],
        "mines_count": game["mines_count"],
        "opened": game["opened"],
        "current_multiplier": game["current_multiplier"],
        "cashed_out": game["cashed_out"],
        "total_cells": 16
    }

# ========== CRASH HTTP ==========
@app.get("/api/crash/history")
async def crash_history():
    return {"history": crash_state["history"][:15], "server_seed_hash": SERVER_SEED_HASH}

@app.get("/api/crash/verify")
async def verify_crash(server_seed: str, nonce: int):
    message = f"{server_seed}:{nonce}"
    hash_hex = hashlib.sha256(message.encode()).hexdigest()
    h = int(hash_hex[:16], 16)
    r = h / (2**64)
    if r < 0.30:
        cp = round(1.01 + (r / 0.30) * 0.09, 2)
    elif r < 0.60:
        cp = round(1.10 + ((r - 0.30) / 0.30) * 0.20, 2)
    elif r < 0.82:
        cp = round(1.30 + ((r - 0.60) / 0.22) * 0.50, 2)
    elif r < 0.94:
        cp = round(1.80 + ((r - 0.82) / 0.12) * 1.20, 2)
    elif r < 0.98:
        cp = round(3.00 + ((r - 0.94) / 0.04) * 5.00, 2)
    elif r < 0.995:
        cp = round(8.00 + ((r - 0.98) / 0.015) * 12.00, 2)
    else:
        cp = round(20.00 + ((r - 0.995) / 0.005) * 30.00, 2)
    return {"verified": True, "crash_point": min(cp, 50.0), "hash": hash_hex}

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(socket_app, host="0.0.0.0", port=8000)
