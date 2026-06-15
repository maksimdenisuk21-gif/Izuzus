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

# ========== ЗВЁЗДНЫЕ КЕЙСЫ (6 шт) ==========
STAR_CASE_PRICES = {
    "star_case_1": 50, "star_case_2": 150, "star_case_3": 400,
    "star_case_4": 750, "star_case_5": 1500, "star_case_6": 2500
}

STAR_CASE_DROPS = {
    "star_case_1": {
        "s1_1": ("⭐ 10 Stars", 10), "s1_2": ("⭐ 25 Stars", 25), "s1_3": ("⭐ 40 Stars", 40),
        "s1_4": ("⭐ 60 Stars", 60), "s1_5": ("⭐ 100 Stars", 100), "s1_6": ("⭐ 200 Stars", 200)
    },
    "star_case_2": {
        "s2_1": ("⭐ 30 Stars", 30), "s2_2": ("⭐ 75 Stars", 75), "s2_3": ("⭐ 120 Stars", 120),
        "s2_4": ("⭐ 200 Stars", 200), "s2_5": ("⭐ 350 Stars", 350), "s2_6": ("⭐ 600 Stars", 600)
    },
    "star_case_3": {
        "s3_1": ("⭐ 80 Stars", 80), "s3_2": ("⭐ 200 Stars", 200), "s3_3": ("⭐ 350 Stars", 350),
        "s3_4": ("⭐ 500 Stars", 500), "s3_5": ("⭐ 800 Stars", 800), "s3_6": ("⭐ 1500 Stars", 1500)
    },
    "star_case_4": {
        "s4_1": ("⭐ 150 Stars", 150), "s4_2": ("⭐ 400 Stars", 400), "s4_3": ("⭐ 650 Stars", 650),
        "s4_4": ("⭐ 1000 Stars", 1000), "s4_5": ("⭐ 1800 Stars", 1800), "s4_6": ("⭐ 3000 Stars", 3000)
    },
    "star_case_5": {
        "s5_1": ("⭐ 300 Stars", 300), "s5_2": ("⭐ 800 Stars", 800), "s5_3": ("⭐ 1300 Stars", 1300),
        "s5_4": ("⭐ 2000 Stars", 2000), "s5_5": ("⭐ 3500 Stars", 3500), "s5_6": ("⭐ 6000 Stars", 6000)
    },
    "star_case_6": {
        "s6_1": ("⭐ 500 Stars", 500), "s6_2": ("⭐ 1300 Stars", 1300), "s6_3": ("⭐ 2200 Stars", 2200),
        "s6_4": ("⭐ 3500 Stars", 3500), "s6_5": ("⭐ 5500 Stars", 5500), "s6_6": ("⭐ 10000 Stars", 10000)
    }
}

# ========== NFT КЕЙСЫ (6 шт) ==========
NFT_CASE_PRICES = {
    "nft_case_1": 100, "nft_case_2": 250, "nft_case_3": 500,
    "nft_case_4": 1000, "nft_case_5": 1750, "nft_case_6": 3000
}

NFT_CASE_DROPS = {
    "nft_case_1": {
        "n1_1": ("💎 Blood Gem", 20), "n1_2": ("💜 Amethyst", 35), "n1_3": ("💙 Sapphire", 55),
        "n1_4": ("💍 Princess Cut", 80), "n1_5": ("👑 King Midas", 130), "n1_6": ("💚 Kryptonite", 250)
    },
    "nft_case_2": {
        "n2_1": ("🐱 Scared Cat", 45), "n2_2": ("👻 Spooky Cat", 75), "n2_3": ("🐟 Fish Skeleton Cat", 120),
        "n2_4": ("🦇 Bat Cat", 190), "n2_5": ("🦠 Virus Cat", 300), "n2_6": ("👾 Glitch Cat", 500)
    },
    "nft_case_3": {
        "n3_1": ("🔵 Evil Eye Blue", 90), "n3_2": ("🟢 Evil Eye Green", 150), "n3_3": ("🔴 Evil Eye Red", 240),
        "n3_4": ("🟡 Evil Eye Gold", 380), "n3_5": ("⚫ Evil Eye Black", 600), "n3_6": ("💎 Evil Eye Crystal", 1000)
    },
    "nft_case_4": {
        "n4_1": ("🐸 Kissed Frog", 180), "n4_2": ("🟤 Swamp Frog", 300), "n4_3": ("🌸 Lily Frog", 480),
        "n4_4": ("✨ Golden Frog", 750), "n4_5": ("☠️ Poison Frog", 1200), "n4_6": ("🤴 Frog Prince", 2000)
    },
    "nft_case_5": {
        "n5_1": ("🧢 Durov's Cap", 320), "n5_2": ("⚪ Cap Silver", 530), "n5_3": ("⚫ Cap Black", 850),
        "n5_4": ("🟡 Cap Gold Trim", 1350), "n5_5": ("⭐ Founder Edition Cap", 2100), "n5_6": ("👑 Durov's Crown Cap", 3500)
    },
    "nft_case_6": {
        "n6_1": ("🐸 Plush Pepe", 600), "n6_2": ("😊 Pepe Smile", 1000), "n6_3": ("😎 Pepe Chill", 1600),
        "n6_4": ("🤨 Pepe Rare", 2500), "n6_5": ("✨ Golden Plush Pepe", 4000), "n6_6": ("👑 Mythic Plush Pepe", 7000)
    }
}

# ========== ШАНСЫ ДРОПА ==========
STAR_DROP_WEIGHTS = [45.0, 28.0, 15.0, 8.0, 3.5, 0.5]
NFT_DROP_WEIGHTS = [40.0, 28.0, 17.0, 10.0, 4.0, 1.0]

# ========== PROVABLY FAIR CRASH (ХАУС ЭЙДЖ 4%) ==========
CRASH_MIN_BET = 25
CRASH_MAX_BET = 5000
CRASH_BETTING_TIME = 6
CRASH_COOLDOWN = 3
CRASH_HOUSE_EDGE = 0.04  # 4% хаус эйдж
CRASH_SPEED = 0.08

# ========== MINES GAME ==========
MINES_GRID_SIZE = 4
MINES_MIN_COUNT = 1
MINES_MAX_COUNT = 15
MINES_HOUSE_EDGE = 0.05

active_mines_games: dict = {}

def generate_mines_grid(mines_count: int) -> list:
    total_cells = MINES_GRID_SIZE * MINES_GRID_SIZE
    grid = [0] * total_cells
    mine_positions = random.sample(range(total_cells), mines_count)
    for pos in mine_positions:
        grid[pos] = 1
    return grid

def calculate_mines_multiplier(mines_count: int, opened: int) -> float:
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
    max_for_mines = max_multiplier.get(closest, 50.0)
    
    return round(min(multiplier, max_for_mines), 2)

# ========== REFERRAL SYSTEM ==========
REFERRAL_PERCENT = 7
MAX_WITHDRAW_AMOUNT = 50000
WITHDRAW_FEE = 0.05
WITHDRAW_COOLDOWN_HOURS = 24
MIN_DEPOSIT_FOR_REFERRAL = 50

# ========== ITEM UPGRADE SYSTEM ==========
UPGRADE_CHANCES = {
    50: 0.60, 100: 0.50, 200: 0.40, 500: 0.30,
    1000: 0.20, 2000: 0.10, 5000: 0.05
}

SERVER_SEED = os.getenv("CRASH_SERVER_SEED", str(uuid.uuid4()))
SERVER_SEED_HASH = hashlib.sha256(SERVER_SEED.encode()).hexdigest()
crash_nonce = 0
ROUNDS_BEFORE_SEED_CHANGE = 100
rounds_since_seed_change = 0

crash_state = {
    "status": "waiting", "round_id": "", "crash_point": 1.0,
    "hash": "", "start_time": 0, "bets": {}, "history": [],
    "timer_ends": 0, "connected_users": set(),
    "current_multiplier": 1.0, "crashed": False
}

def bet_key(tg_id: int, round_id: str) -> str:
    return f"{tg_id}:{round_id}"

def generate_crash_point() -> tuple:
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
    
    if r < 0.30: crash_point = round(1.01 + (r / 0.30) * 0.09, 2)
    elif r < 0.60: crash_point = round(1.10 + ((r - 0.30) / 0.30) * 0.20, 2)
    elif r < 0.82: crash_point = round(1.30 + ((r - 0.60) / 0.22) * 0.50, 2)
    elif r < 0.94: crash_point = round(1.80 + ((r - 0.82) / 0.12) * 1.20, 2)
    elif r < 0.98: crash_point = round(3.00 + ((r - 0.94) / 0.04) * 5.00, 2)
    elif r < 0.995: crash_point = round(8.00 + ((r - 0.98) / 0.015) * 12.00, 2)
    else: crash_point = round(20.00 + ((r - 0.995) / 0.005) * 30.00, 2)
    
    return min(crash_point, 50.0), hash_hex

async def crash_game_loop():
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
            'status': 'betting', 'round_id': crash_state["round_id"],
            'hash': crash_state["hash"], 'timer': CRASH_BETTING_TIME,
            'bets_count': 0, 'total_amount': 0
        })
        
        await asyncio.sleep(CRASH_BETTING_TIME)
        
        if len(crash_state["bets"]) == 0:
            crash_state["status"] = "cooldown"
            await sio.emit('crash_state', {
                'status': 'cooldown', 'timer': CRASH_COOLDOWN,
                'history': crash_state["history"][:10]
            })
            await asyncio.sleep(CRASH_COOLDOWN)
            continue
        
        crash_state["status"] = "flying"
        crash_state["start_time"] = time.time()
        
        total_amount = sum(b["bet"] for b in crash_state["bets"].values())
        
        await sio.emit('crash_start', {
            'round_id': crash_state["round_id"], 'hash': crash_state["hash"],
            'total_bets': len(crash_state["bets"]), 'total_amount': total_amount
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
                        'username': b['username'], 'amount': b['bet'],
                        'cashed_out': cashed, 'cashed_at': b.get('cashed_at', 0),
                        'win': int(b['bet'] * b.get('cashed_at', 0)) if cashed else 0
                    })
                
                await sio.emit('crash_end', {
                    'crash_point': final_point, 'hash': crash_state["hash"],
                    'server_seed': SERVER_SEED, 'nonce': crash_nonce, 'bets': results
                })
                break
            
            if abs(current_mult - last_sent_mult) >= 0.01:
                await sio.emit('crash_multiplier', {
                    'multiplier': round(current_mult, 2), 'elapsed': elapsed
                })
                last_sent_mult = current_mult
            
            await asyncio.sleep(0.1)
        
        crash_state["status"] = "cooldown"
        await sio.emit('crash_state', {
            'status': 'cooldown', 'timer': CRASH_COOLDOWN,
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

# ========== SOCKET.IO EVENTS ==========
@sio.event
async def connect(sid, environ):
    crash_state["connected_users"].add(sid)
    await sio.emit('crash_state', {
        'status': crash_state["status"], 'round_id': crash_state["round_id"],
        'timer': max(0, int(crash_state["timer_ends"] - time.time())),
        'history': crash_state["history"][:10], 'bets_count': len(crash_state["bets"])
    }, to=sid)

@sio.event
async def disconnect(sid):
    crash_state["connected_users"].discard(sid)

@sio.event
async def place_bet(sid, data):
    try:
        tg_id = int(data.get('tg_id', 0))
    except (TypeError, ValueError):
        await sio.emit('error', {'message': 'Неверный ID'}, to=sid)
        return
    
    if tg_id <= 0:
        await sio.emit('error', {'message': 'Неверный ID'}, to=sid)
        return
    
    try:
        bet = int(data.get('bet_amount', 0))
    except (TypeError, ValueError):
        await sio.emit('error', {'message': 'Неверная сумма'}, to=sid)
        return
    
    username = data.get('username', 'Игрок')
    round_id = crash_state["round_id"]
    
    if crash_state["status"] != "betting":
        await sio.emit('error', {'message': 'Ставки закрыты!'}, to=sid)
        return
    
    if bet < CRASH_MIN_BET or bet > CRASH_MAX_BET:
        await sio.emit('error', {'message': f'Ставка {CRASH_MIN_BET}-{CRASH_MAX_BET} ⭐️'}, to=sid)
        return
    
    key = bet_key(tg_id, round_id)
    if key in crash_state["bets"]:
        await sio.emit('error', {'message': 'Уже есть ставка'}, to=sid)
        return
    
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("UPDATE users SET balance = balance - ? WHERE tg_id = ? AND balance >= ?", (bet, tg_id, bet))
        await db.commit()
        
        if cursor.rowcount == 0:
            await sio.emit('error', {'message': 'Недостаточно монет'}, to=sid)
            return
        
        async with db.execute("SELECT balance FROM users WHERE tg_id = ?", (tg_id,)) as cursor:
            row = await cursor.fetchone()
            new_balance = row[0] if row else 0
    
    crash_state["bets"][key] = {
        "tg_id": tg_id, "bet": bet, "username": username,
        "round_id": round_id, "cashed_out": False, "cashed_at": 0, "sid": sid
    }
    
    await sio.emit('bet_placed', {
        'tg_id': tg_id, 'username': username, 'amount': bet,
        'balance': new_balance, 'round_id': round_id
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
        await sio.emit('error', {'message': 'Неверный ID'}, to=sid)
        return
    
    if tg_id <= 0:
        await sio.emit('error', {'message': 'Неверный ID'}, to=sid)
        return
    
    round_id = crash_state["round_id"]
    
    if crash_state["crashed"]:
        await sio.emit('error', {'message': 'Ракета упала!'}, to=sid)
        return
    
    if crash_state["status"] != "flying":
        await sio.emit('error', {'message': f'Не время. Статус: {crash_state["status"]}'}, to=sid)
        return
    
    key = bet_key(tg_id, round_id)
    bet_data = crash_state["bets"].get(key)
    
    if not bet_data:
        await sio.emit('error', {'message': 'Нет активной ставки'}, to=sid)
        return
    
    if bet_data["cashed_out"]:
        await sio.emit('error', {'message': 'Уже забрали'}, to=sid)
        return
    
    current_mult = crash_state["current_multiplier"]
    crash_point = crash_state["crash_point"]
    
    if current_mult >= crash_point:
        await sio.emit('error', {'message': 'Слишком поздно!'}, to=sid)
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
        'multiplier': round(current_mult, 2), 'win_amount': win_amount,
        'profit': win_amount - bet_data["bet"], 'balance': new_balance, 'round_id': round_id
    }, to=sid)
    
    await sio.emit('player_cashout', {
        'username': bet_data["username"], 'amount': bet_data["bet"],
        'multiplier': round(current_mult, 2), 'win': win_amount
    })

# ========== STARTUP ==========
@app.on_event("startup")
async def startup():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS users (tg_id INTEGER PRIMARY KEY, username TEXT DEFAULT 'Игрок', balance INTEGER DEFAULT 20, inventory TEXT DEFAULT '[]')")
        await db.execute("CREATE TABLE IF NOT EXISTS withdraws (id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, amount INTEGER, requisites TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("CREATE TABLE IF NOT EXISTS referrals (user_id INTEGER PRIMARY KEY, referrer_id INTEGER NOT NULL, activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, total_earned INTEGER DEFAULT 0)")
        await db.execute("CREATE TABLE IF NOT EXISTS referral_earnings (id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id INTEGER NOT NULL, referral_id INTEGER NOT NULL, deposit_amount INTEGER NOT NULL, earned INTEGER NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("CREATE TABLE IF NOT EXISTS withdraw_cooldowns (user_id INTEGER PRIMARY KEY, last_withdraw_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
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
                await db.execute("INSERT INTO users (tg_id, username, balance, inventory) VALUES (?, ?, ?, ?)", (tg_id, username, start_balance, '[]'))
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
    return {"success": True, "message": "✅ +10,000 Stars"}

@app.post("/api/admin/give_stars_to_user")
async def admin_give_stars_to_user(req: AdminGiveStarsRequest, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    if not ADMIN_TG_ID or tg_id != ADMIN_TG_ID:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    target_id = req.target_tg_id
    amount = req.amount
    
    if target_id <= 0: raise HTTPException(status_code=400, detail="Неверный ID")
    if amount < 1: raise HTTPException(status_code=400, detail="Сумма > 0")
    if amount > 1000000: raise HTTPException(status_code=400, detail="Макс 1M ⭐️")
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (tg_id, username, balance, inventory) VALUES (?, ?, 20, '[]')", (target_id, f"Player_{target_id}"))
        await db.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (amount, target_id))
        await db.commit()
        async with db.execute("SELECT username, balance FROM users WHERE tg_id = ?", (target_id,)) as cursor:
            row = await cursor.fetchone()
    
    return {"success": True, "message": f"✅ Выдано {amount} ⭐️", "target_tg_id": target_id}

# ========== CASES ==========
@app.post("/api/case/open")
async def open_case(req: OpenCaseRequest, user: dict = Depends(verify_telegram_data)):
    case_type = req.case_type
    
    if case_type in STAR_CASE_PRICES:
        price = STAR_CASE_PRICES[case_type]
        case_pool = STAR_CASE_DROPS[case_type]
        weights = STAR_DROP_WEIGHTS
    elif case_type in NFT_CASE_PRICES:
        price = NFT_CASE_PRICES[case_type]
        case_pool = NFT_CASE_DROPS[case_type]
        weights = NFT_DROP_WEIGHTS
    else:
        raise HTTPException(status_code=400, detail="Неизвестный кейс")
    
    tg_id = user.get('id')
    user_info = await get_or_create_user(tg_id)
    
    if user_info["balance"] < price:
        raise HTTPException(status_code=400, detail="Недостаточно монет")
    
    item_ids = list(case_pool.keys())
    reward_id = random.choices(item_ids, weights=weights, k=1)[0]
    reward_name = case_pool[reward_id][0]
    
    new_balance = user_info["balance"] - price
    user_info["inventory"].append({"id": reward_id, "name": reward_name, "case": case_type})
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = ?, inventory = ? WHERE tg_id = ?", (new_balance, json.dumps(user_info["inventory"]), tg_id))
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
    if c_type in STAR_CASE_DROPS and i_id in STAR_CASE_DROPS[c_type]:
        gain = STAR_CASE_DROPS[c_type][i_id][1]
    elif c_type in NFT_CASE_DROPS and i_id in NFT_CASE_DROPS[c_type]:
        gain = NFT_CASE_DROPS[c_type][i_id][1]
    
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
    if not inventory: raise HTTPException(status_code=400, detail="Инвентарь пуст")
    
    total_gain = 0
    for item in inventory:
        c_type = item.get("case")
        i_id = item.get("id")
        if c_type in STAR_CASE_DROPS and i_id in STAR_CASE_DROPS[c_type]:
            total_gain += STAR_CASE_DROPS[c_type][i_id][1]
        elif c_type in NFT_CASE_DROPS and i_id in NFT_CASE_DROPS[c_type]:
            total_gain += NFT_CASE_DROPS[c_type][i_id][1]
    
    new_balance = user_info["balance"] + total_gain
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = ?, inventory = '[]' WHERE tg_id = ?", (new_balance, tg_id))
        await db.commit()
    
    return {"gain": total_gain, "balance": new_balance}

# ========== ITEM UPGRADE ==========
@app.post("/api/inventory/upgrade")
async def upgrade_item(req: UpgradeItemRequest, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    user_info = await get_or_create_user(tg_id)
    inventory = user_info["inventory"]
    
    if req.item_index < 0 or req.item_index >= len(inventory):
        raise HTTPException(status_code=400, detail="Предмет не найден")
    
    item = inventory[req.item_index]
    c_type = item.get("case")
    i_id = item.get("id")
    
    current_price = 0
    if c_type in STAR_CASE_DROPS and i_id in STAR_CASE_DROPS[c_type]:
        current_price = STAR_CASE_DROPS[c_type][i_id][1]
    elif c_type in NFT_CASE_DROPS and i_id in NFT_CASE_DROPS[c_type]:
        current_price = NFT_CASE_DROPS[c_type][i_id][1]
    
    if current_price == 0:
        raise HTTPException(status_code=400, detail="Нельзя улучшить")
    
    target_price = req.target_price
    if target_price <= current_price:
        raise HTTPException(status_code=400, detail="Цель дороже")
    
    upgrade_chance = UPGRADE_CHANCES.get(target_price, 0.10)
    upgrade_cost = int(target_price * 0.1)
    
    if user_info["balance"] < upgrade_cost:
        raise HTTPException(status_code=400, detail=f"Нужно {upgrade_cost} ⭐️")
    
    new_balance = user_info["balance"] - upgrade_cost
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = ? WHERE tg_id = ?", (new_balance, tg_id))
        await db.commit()
    
    if random.random() < upgrade_chance:
        new_item = None
        for ct, drops in {**STAR_CASE_DROPS, **NFT_CASE_DROPS}.items():
            for did, (dname, dprice) in drops.items():
                if dprice == target_price:
                    new_item = {"id": did, "name": dname, "case": ct}
                    break
            if new_item: break
        
        if new_item:
            inventory[req.item_index] = new_item
        
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET inventory = ? WHERE tg_id = ?", (json.dumps(inventory), tg_id))
            await db.commit()
        
        return {"success": True, "message": f"✅ Успех! Предмет улучшен до {target_price}⭐️", "new_balance": new_balance, "upgrade_cost": upgrade_cost}
    else:
        inventory.pop(req.item_index)
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET inventory = ? WHERE tg_id = ?", (json.dumps(inventory), tg_id))
            await db.commit()
        
        return {"success": False, "message": f"💥 Сгорел! Потеряно: {upgrade_cost}⭐️", "new_balance": new_balance, "upgrade_cost": upgrade_cost}

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
        raise HTTPException(status_code=400, detail="Минимум 50 Stars")
    
    tg_id = user.get('id')
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/createInvoiceLink"
    payload = {
        "title": "Пополнение баланса",
        "description": f"Покупка {stars_amount} Stars",
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
                            await db.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (earned, referrer_id))
                            await db.execute("UPDATE referrals SET total_earned = total_earned + ? WHERE user_id = ?", (earned, tg_id))
                            await db.execute("INSERT INTO referral_earnings (referrer_id, referral_id, deposit_amount, earned) VALUES (?, ?, ?, ?)", (referrer_id, tg_id, stars_amount, earned))
                            await db.commit()
            return {"invoice_url": res_data["result"]}
        else:
            raise HTTPException(status_code=500, detail="Ошибка Telegram Invoice")

# ========== WITHDRAW ==========
@app.post("/api/withdraw")
async def create_withdraw(amount: int, wallet: str, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    
    if amount < 100: raise HTTPException(status_code=400, detail="Минимум 100 Stars")
    if amount > MAX_WITHDRAW_AMOUNT: raise HTTPException(status_code=400, detail=f"Максимум {MAX_WITHDRAW_AMOUNT} Stars")
    
    user_info = await get_or_create_user(tg_id)
    if user_info["balance"] < amount: raise HTTPException(status_code=400, detail="Недостаточно монет")
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT last_withdraw_at FROM withdraw_cooldowns WHERE user_id = ?", (tg_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                try: last_ts = time.mktime(time.strptime(row[0], "%Y-%m-%d %H:%M:%S"))
                except: last_ts = 0
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
    if not ADMIN_TG_ID or user.get('id') != ADMIN_TG_ID: raise HTTPException(status_code=403, detail="Доступ запрещен")
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, tg_id, amount, requisites, status, created_at FROM withdraws ORDER BY id DESC") as cursor:
            rows = await cursor.fetchall()
            return [{"id": r[0], "tg_id": r[1], "amount": r[2], "requisites": r[3], "status": r[4], "date": r[5]} for r in rows]

@app.post("/api/admin/withdraw/status")
async def update_withdraw_status(req: UpdateWithdrawStatusRequest, user: dict = Depends(verify_telegram_data)):
    if not ADMIN_TG_ID or user.get('id') != ADMIN_TG_ID: raise HTTPException(status_code=403, detail="Доступ запрещен")
    async with aiosqlite.connect(DB_NAME) as db:
        if req.status == "rejected":
            async with db.execute("SELECT tg_id, amount FROM withdraws WHERE id = ?", (req.ticket_id,)) as cursor:
                ticket = await cursor.fetchone()
                if ticket: await db.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (ticket[1], ticket[0]))
        await db.execute("UPDATE withdraws SET status = ? WHERE id = ?", (req.status, req.ticket_id))
        await db.commit()
    return {"success": True, "new_status": req.status}

# ========== REFERRAL ==========
@app.post("/api/referral/activate")
async def activate_referral(req: ReferralActivateRequest, user: dict = Depends(verify_telegram_data)):
    user_id = user.get('id')
    referrer_id = req.referrer_id
    if user_id == referrer_id: raise HTTPException(status_code=400, detail="Нельзя себе")
    async with aiosqlite.connect(DB_NAME) as db:
        if not await (await db.execute("SELECT tg_id FROM users WHERE tg_id = ?", (referrer_id,))).fetchone(): raise HTTPException(status_code=400, detail="Не найден")
        if await (await db.execute("SELECT referrer_id FROM referrals WHERE user_id = ?", (user_id,))).fetchone(): raise HTTPException(status_code=400, detail="Уже есть")
        await db.execute("INSERT INTO referrals (user_id, referrer_id) VALUES (?, ?)", (user_id, referrer_id))
        await db.commit()
    return {"success": True, "referrer_id": referrer_id}

@app.get("/api/referral/stats")
async def get_referral_stats(user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT total_earned FROM referrals WHERE user_id = ?", (tg_id,)) as cursor: earned = await cursor.fetchone()
        async with db.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (tg_id,)) as cursor: count = await cursor.fetchone()
        async with db.execute("SELECT referral_id, deposit_amount, earned, created_at FROM referral_earnings WHERE referrer_id = ? ORDER BY created_at DESC LIMIT 20", (tg_id,)) as cursor: history = await cursor.fetchall()
    return {"total_earned": earned[0] if earned else 0, "referrals_count": count[0] if count else 0, "percent": REFERRAL_PERCENT, "history": [{"referral_id": r[0], "deposit": r[1], "earned": r[2], "date": r[3]} for r in history]}

# ========== MINES ==========
@app.post("/api/mines/start")
async def mines_start(req: MinesStartRequest, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    bet = req.bet_amount
    mines_count = req.mines_count
    
    if bet < 10: raise HTTPException(status_code=400, detail="Мин. 10 ⭐️")
    if bet > 50000: raise HTTPException(status_code=400, detail="Макс. 50k ⭐️")
    if mines_count < MINES_MIN_COUNT or mines_count > MINES_MAX_COUNT: raise HTTPException(status_code=400, detail=f"Мины {MINES_MIN_COUNT}-{MINES_MAX_COUNT}")
    if tg_id in active_mines_games: raise HTTPException(status_code=400, detail="Завершите игру")
    
    user_info = await get_or_create_user(tg_id)
    if user_info["balance"] < bet: raise HTTPException(status_code=400, detail="Недостаточно монет")
    
    new_balance = user_info["balance"] - bet
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = ? WHERE tg_id = ?", (new_balance, tg_id))
        await db.commit()
    
    grid = generate_mines_grid(mines_count)
    game_id = str(uuid.uuid4())[:8]
    
    active_mines_games[tg_id] = {"game_id": game_id, "bet": bet, "mines_count": mines_count, "grid": grid, "opened": [], "cashed_out": False, "current_multiplier": 1.0}
    
    return {"game_id": game_id, "bet": bet, "mines_count": mines_count, "balance": new_balance, "total_cells": MINES_GRID_SIZE * MINES_GRID_SIZE, "safe_cells": (MINES_GRID_SIZE * MINES_GRID_SIZE) - mines_count}

@app.post("/api/mines/open")
async def mines_open_cell(req: MinesOpenRequest, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    if tg_id not in active_mines_games: raise HTTPException(status_code=400, detail="Нет игры")
    game = active_mines_games[tg_id]
    if game["game_id"] != req.game_id: raise HTTPException(status_code=400, detail="Неверный ID")
    if game["cashed_out"]: raise HTTPException(status_code=400, detail="Завершена")
    
    cell_index = req.cell_index
    total_cells = MINES_GRID_SIZE * MINES_GRID_SIZE
    if cell_index < 0 or cell_index >= total_cells: raise HTTPException(status_code=400, detail="Неверная клетка")
    if cell_index in game["opened"]: raise HTTPException(status_code=400, detail="Открыта")
    
    if game["grid"][cell_index] == 1:
        game["cashed_out"] = True
        mines_positions = [i for i, v in enumerate(game["grid"]) if v == 1]
        del active_mines_games[tg_id]
        return {"status": "bomb", "cell_index": cell_index, "opened": game["opened"], "mines": mines_positions, "win_amount": 0, "balance": (await get_or_create_user(tg_id))["balance"]}
    
    game["opened"].append(cell_index)
    opened_count = len(game["opened"])
    multiplier = calculate_mines_multiplier(game["mines_count"], opened_count)
    game["current_multiplier"] = multiplier
    
    return {"status": "safe", "cell_index": cell_index, "opened": game["opened"], "opened_count": opened_count, "current_multiplier": multiplier}

@app.post("/api/mines/cashout")
async def mines_cashout(req: MinesCashoutRequest, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    if tg_id not in active_mines_games: raise HTTPException(status_code=400, detail="Нет игры")
    game = active_mines_games[tg_id]
    if game["game_id"] != req.game_id: raise HTTPException(status_code=400, detail="Неверный ID")
    if game["cashed_out"]: raise HTTPException(status_code=400, detail="Завершена")
    if len(game["opened"]) == 0: raise HTTPException(status_code=400, detail="Откройте клетку")
    
    multiplier = game["current_multiplier"]
    win_amount = int(game["bet"] * multiplier)
    game["cashed_out"] = True
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (win_amount, tg_id))
        await db.commit()
        async with db.execute("SELECT balance FROM users WHERE tg_id = ?", (tg_id,)) as cursor: new_balance = (await cursor.fetchone())[0]
    
    mines_positions = [i for i, v in enumerate(game["grid"]) if v == 1]
    del active_mines_games[tg_id]
    
    return {"status": "cashed_out", "multiplier": multiplier, "win_amount": win_amount, "profit": win_amount - game["bet"], "balance": new_balance, "opened": game["opened"], "mines": mines_positions}

@app.get("/api/mines/state")
async def mines_get_state(user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    if tg_id not in active_mines_games: return {"active": False}
    game = active_mines_games[tg_id]
    return {"active": True, "game_id": game["game_id"], "bet": game["bet"], "mines_count": game["mines_count"], "opened": game["opened"], "current_multiplier": game["current_multiplier"], "cashed_out": game["cashed_out"], "total_cells": MINES_GRID_SIZE * MINES_GRID_SIZE}

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
    if r < 0.30: crash_point = round(1.01 + (r / 0.30) * 0.09, 2)
    elif r < 0.60: crash_point = round(1.10 + ((r - 0.30) / 0.30) * 0.20, 2)
    elif r < 0.82: crash_point = round(1.30 + ((r - 0.60) / 0.22) * 0.50, 2)
    elif r < 0.94: crash_point = round(1.80 + ((r - 0.82) / 0.12) * 1.20, 2)
    elif r < 0.98: crash_point = round(3.00 + ((r - 0.94) / 0.04) * 5.00, 2)
    elif r < 0.995: crash_point = round(8.00 + ((r - 0.98) / 0.015) * 12.00, 2)
    else: crash_point = round(20.00 + ((r - 0.995) / 0.005) * 30.00, 2)
    return {"verified": True, "crash_point": min(crash_point, 50.0), "hash": hash_hex}
