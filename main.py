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

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*', ping_timeout=10, ping_interval=5)
socket_app = socketio.ASGIApp(sio, app)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_TG_ID_RAW = os.getenv("ADMIN_TG_ID")
ADMIN_TG_ID = int(ADMIN_TG_ID_RAW) if ADMIN_TG_ID_RAW else 0
DB_NAME = "database.db"

# ==================================================
# НАСТРОЙКИ ДЛЯ PUBG METRO ROYALE
# ==================================================

# ========== КОНТЕЙНЕРЫ (КЕЙСЫ) В СТИЛЕ PUBG METRO ==========
CONTAINER_PRICES = {
    "container_1": 50,   # Стандартный ящик
    "container_2": 150,  # Ящик повышенной сложности
    "container_3": 400,  # Суперконтейнер
    "container_4": 750,  # Режимный ящик
    "container_5": 1500, # Секретный контейнер
    "container_6": 2500, # Тайный склад
    "container_7": 4000,
    "container_8": 6000,
    "container_9": 9000,
    "container_10": 15000
}

CONTAINER_DROPS = {
    "container_1": {
        "c1_1": ("📦 Ящик с припасами", 12),
        "c1_2": ("🔫 M416", 29),
        "c1_3": ("🎒 Рюкзак Ур.2", 46),
        "c1_4": ("🛡️ Бронежилет Ур.2", 69),
        "c1_5": ("💊 Адреналин", 115),
        "c1_6": ("💰 Слиток золота", 230)
    },
    "container_2": {
        "c2_1": ("🔫 M762", 35),
        "c2_2": ("🎒 Рюкзак Ур.3", 86),
        "c2_3": ("🛡️ Бронежилет Ур.3", 138),
        "c2_4": ("📡 Портативная рация", 230),
        "c2_5": ("💰 Слиток золота", 403),
        "c2_6": ("💎 Легендарный артефакт", 690)
    },
    "container_3": {
        "c3_1": ("🔫 AUG", 92),
        "c3_2": ("🎒 Рюкзак Ур.4", 230),
        "c3_3": ("🛡️ Бронежилет Ур.4", 403),
        "c3_4": ("📡 Портативная рация", 575),
        "c3_5": ("💰 Слиток золота", 920),
        "c3_6": ("💎 Легендарный артефакт", 1725)
    },
    "container_4": {
        "c4_1": ("🔫 MK14", 173),
        "c4_2": ("🎒 Тактический рюкзак", 460),
        "c4_3": ("🛡️ Бронежилет Ур.5", 748),
        "c4_4": ("📡 Портативная рация", 1150),
        "c4_5": ("💰 Слиток золота", 2070),
        "c4_6": ("💎 Легендарный артефакт", 3450)
    },
    "container_5": {
        "c5_1": ("🔫 Groza", 345),
        "c5_2": ("🎒 Тактический рюкзак", 920),
        "c5_3": ("🛡️ Бронежилет Ур.5", 1495),
        "c5_4": ("📡 Портативная рация", 2300),
        "c5_5": ("💰 Слиток золота", 4025),
        "c5_6": ("💎 Легендарный артефакт", 6900)
    },
    "container_6": {
        "c6_1": ("🔫 AWM", 575),
        "c6_2": ("🎒 Тактический рюкзак", 1495),
        "c6_3": ("🛡️ Бронежилет Ур.5", 2530),
        "c6_4": ("📡 Портативная рация", 4025),
        "c6_5": ("💰 Слиток золота", 6325),
        "c6_6": ("💎 Легендарный артефакт", 11500)
    },
    "container_7": {
        "c7_1": ("🔫 M249", 920),
        "c7_2": ("🎒 Тактический рюкзак", 1725),
        "c7_3": ("🛡️ Бронежилет Ур.5", 2875),
        "c7_4": ("📡 Портативная рация", 4600),
        "c7_5": ("💰 Слиток золота", 8050),
        "c7_6": ("💎 Легендарный артефакт", 13800)
    },
    "container_8": {
        "c8_1": ("🔫 MG3", 1380),
        "c8_2": ("🎒 Тактический рюкзак", 2530),
        "c8_3": ("🛡️ Бронежилет Ур.5", 4370),
        "c8_4": ("📡 Портативная рация", 6900),
        "c8_5": ("💰 Слиток золота", 11500),
        "c8_6": ("💎 Легендарный артефакт", 20700)
    },
    "container_9": {
        "c9_1": ("🔫 P90", 2070),
        "c9_2": ("🎒 Тактический рюкзак", 4025),
        "c9_3": ("🛡️ Бронежилет Ур.5", 6900),
        "c9_4": ("📡 Портативная рация", 10350),
        "c9_5": ("💰 Слиток золота", 17250),
        "c9_6": ("💎 Легендарный артефакт", 28750)
    },
    "container_10": {
        "c10_1": ("🔫 FAMAS", 3450),
        "c10_2": ("🎒 Тактический рюкзак", 6325),
        "c10_3": ("🛡️ Бронежилет Ур.5", 10350),
        "c10_4": ("📡 Портативная рация", 17250),
        "c10_5": ("💰 Слиток золота", 28750),
        "c10_6": ("💎 Легендарный артефакт", 46000)
    }
}

# ========== ЛУТ ИЗ РЕЖИМА МЕТРО ==========
METRO_LOOT_POOL = [
    {"name": "💊 Аптечка", "price": 5},
    {"name": "💊 Адреналин", "price": 8},
    {"name": "🔫 M416", "price": 12},
    {"name": "🔫 M762", "price": 18},
    {"name": "🎒 Рюкзак Ур.2", "price": 25},
    {"name": "🎒 Рюкзак Ур.3", "price": 35},
    {"name": "🛡️ Бронежилет Ур.2", "price": 45},
    {"name": "🛡️ Бронежилет Ур.3", "price": 55},
    {"name": "💰 Слиток золота", "price": 70},
    {"name": "📡 Портативная рация", "price": 90},
    {"name": "🔫 AUG", "price": 110},
    {"name": "🎒 Тактический рюкзак", "price": 140},
    {"name": "🛡️ Бронежилет Ур.4", "price": 170},
    {"name": "💎 Артефакт", "price": 200},
    {"name": "🔫 MK14", "price": 240},
    {"name": "🛡️ Бронежилет Ур.5", "price": 280},
    {"name": "🔫 Groza", "price": 330},
    {"name": "💎 Легендарный артефакт", "price": 380},
    {"name": "🔫 AWM", "price": 440},
    {"name": "📡 Секретный документ", "price": 500},
    {"name": "🔫 M249", "price": 570},
    {"name": "🎒 Экзоскелет", "price": 650},
    {"name": "🛡️ Экзоскелет", "price": 740},
    {"name": "🔫 MG3", "price": 840},
    {"name": "💎 Артефакт Ур.2", "price": 950},
    {"name": "🔫 P90", "price": 1070},
    {"name": "🔫 FAMAS", "price": 1200},
    {"name": "📡 Рация Ур.2", "price": 1340},
    {"name": "💎 Артефакт Ур.3", "price": 1490},
    {"name": "💰 Слиток золота", "price": 1650},
    {"name": "🔫 Оружие будущего", "price": 1820},
    {"name": "🎒 Легендарный рюкзак", "price": 2000},
    {"name": "🛡️ Легендарный бронежилет", "price": 2200},
    {"name": "📡 Секретная рация", "price": 2420},
    {"name": "💎 Артефакт Ур.4", "price": 2660},
    {"name": "🔫 Прототип", "price": 2920},
    {"name": "🎒 Рюкзак Ур.6", "price": 3200},
    {"name": "🛡️ Бронежилет Ур.6", "price": 3500},
    {"name": "💰 Крупный слиток", "price": 3820},
    {"name": "📡 Секретная рация", "price": 4160},
    {"name": "💎 Артефакт Ур.5", "price": 4520},
    {"name": "🔫 Оружие будущего", "price": 4900},
    {"name": "🎒 Рюкзак Ур.7", "price": 5300},
    {"name": "🛡️ Бронежилет Ур.7", "price": 5720},
    {"name": "📡 Секретная рация", "price": 6160},
    {"name": "💎 Артефакт Ур.6", "price": 6620},
    {"name": "🔫 Прототип Ур.2", "price": 7100},
    {"name": "🎒 Рюкзак Ур.8", "price": 7600},
    {"name": "🛡️ Бронежилет Ур.8", "price": 8120},
    {"name": "📡 Секретная рация", "price": 8660},
    {"name": "💎 Артефакт Ур.7", "price": 9220},
    {"name": "🔫 Оружие будущего Ур.3", "price": 9800},
    {"name": "🎒 Рюкзак Ур.9", "price": 10400},
    {"name": "🛡️ Бронежилет Ур.9", "price": 11020},
    {"name": "📡 Секретная рация", "price": 11660},
    {"name": "💎 Артефакт Ур.8", "price": 12320},
    {"name": "🔫 Прототип Ур.3", "price": 13000},
    {"name": "🎒 Рюкзак Ур.10", "price": 13700},
    {"name": "🛡️ Бронежилет Ур.10", "price": 14420},
    {"name": "📡 Секретная рация", "price": 15160},
    {"name": "💎 Артефакт Ур.9", "price": 15920},
    {"name": "🔫 Оружие будущего Ур.4", "price": 16700},
    {"name": "🎒 Рюкзак Ур.11", "price": 17500},
    {"name": "🛡️ Бронежилет Ур.11", "price": 18320},
    {"name": "📡 Секретная рация", "price": 19160},
    {"name": "💎 Артефакт Ур.10", "price": 20020},
    {"name": "🔫 Прототип Ур.4", "price": 20900},
    {"name": "🎒 Рюкзак Ур.12", "price": 21800},
    {"name": "🛡️ Бронежилет Ур.12", "price": 22720},
    {"name": "📡 Секретная рация", "price": 23660},
    {"name": "💎 Артефакт Ур.11", "price": 24620},
    {"name": "🔫 Оружие будущего Ур.5", "price": 25600},
    {"name": "🎒 Рюкзак Ур.13", "price": 26600},
    {"name": "🛡️ Бронежилет Ур.13", "price": 27620},
    {"name": "📡 Секретная рация", "price": 28660},
    {"name": "💎 Артефакт Ур.12", "price": 29720},
    {"name": "🔫 Прототип Ур.5", "price": 30800},
    {"name": "🎒 Рюкзак Ур.14", "price": 31900},
    {"name": "🛡️ Бронежилет Ур.14", "price": 33020},
    {"name": "📡 Секретная рация", "price": 34160},
    {"name": "💎 Артефакт Ур.13", "price": 35320},
    {"name": "🔫 Оружие будущего Ур.6", "price": 36500},
    {"name": "🎒 Рюкзак Ур.15", "price": 37700},
    {"name": "🛡️ Бронежилет Ур.15", "price": 38920},
    {"name": "📡 Секретная рация", "price": 40160},
    {"name": "💎 Артефакт Ур.14", "price": 41420},
    {"name": "🔫 Прототип Ур.6", "price": 42700},
    {"name": "🎒 Рюкзак Ур.16", "price": 44000},
    {"name": "🛡️ Бронежилет Ур.16", "price": 45320},
    {"name": "📡 Секретная рация", "price": 46660},
    {"name": "💎 Артефакт Ур.15", "price": 48020},
    {"name": "🔫 Оружие будущего Ур.7", "price": 49400},
    {"name": "🎒 Рюкзак Ур.17", "price": 50800},
    {"name": "🛡️ Бронежилет Ур.17", "price": 52220},
    {"name": "📡 Секретная рация", "price": 53660},
    {"name": "💎 Артефакт Ур.16", "price": 55120},
    {"name": "🔫 Прототип Ур.7", "price": 56600},
    {"name": "🎒 Рюкзак Ур.18", "price": 58100},
    {"name": "🛡️ Бронежилет Ур.18", "price": 59620},
    {"name": "📡 Секретная рация", "price": 61160},
    {"name": "💎 Артефакт Ур.17", "price": 62720},
    {"name": "🔫 Оружие будущего Ур.8", "price": 64300},
    {"name": "🎒 Рюкзак Ур.19", "price": 65900},
    {"name": "🛡️ Бронежилет Ур.19", "price": 67520},
    {"name": "📡 Секретная рация", "price": 69160},
    {"name": "💎 Артефакт Ур.18", "price": 70820},
    {"name": "🔫 Прототип Ур.8", "price": 72500}
]

CONTAINER_DROP_WEIGHTS = [40.0, 28.0, 17.0, 10.0, 4.0, 1.0]

# ========== БЕСПЛАТНЫЙ КЕЙС (ДЖЕКПОТ) ==========
FREE_CASE_DROPS = [
    {"name": "🔫 M416", "price": 0.1, "weight": 35.0},
    {"name": "🔫 M762", "price": 0.5, "weight": 25.0},
    {"name": "🎒 Рюкзак Ур.2", "price": 1.0, "weight": 18.0},
    {"name": "🛡️ Бронежилет Ур.2", "price": 2.5, "weight": 12.0},
    {"name": "💊 Адреналин", "price": 5.0, "weight": 6.0},
    {"name": "💰 Слиток золота", "price": 15.0, "weight": 2.5},
    {"name": "📡 Портативная рация", "price": 35.0, "weight": 1.0},
    {"name": "💎 Артефакт", "price": 60.0, "weight": 0.4},
    {"name": "📡 Секретный документ", "price": 100.0, "weight": 0.1}
]

# Остальные настройки (CRASH, MINES, REFERRAL) остаются без изменений, 
# но можно подкорректировать названия под стиль PUBG

CRASH_MIN_BET = 25
CRASH_MAX_BET = 5000
CRASH_BETTING_TIME = 6
CRASH_COOLDOWN = 3
CRASH_HOUSE_EDGE = 0.05
CRASH_SPEED = 0.08

MINES_GRID_SIZE = 4
MINES_MIN_COUNT = 1
MINES_MAX_COUNT = 15
MINES_HOUSE_EDGE = 0.05
active_mines_games = {}

def generate_mines_grid(mines_count):
    total_cells = MINES_GRID_SIZE * MINES_GRID_SIZE
    grid = [0] * total_cells
    mine_positions = random.sample(range(total_cells), mines_count)
    for pos in mine_positions:
        grid[pos] = 1
    return grid

def calculate_mines_multiplier(mines_count, opened):
    total_cells = MINES_GRID_SIZE * MINES_GRID_SIZE
    safe_cells = total_cells - mines_count
    if opened >= safe_cells:
        return round((1 - MINES_HOUSE_EDGE) * 100, 2)
    probability = 1.0
    for i in range(opened):
        probability *= (safe_cells - i) / (total_cells - i)
    multiplier = ((1 - MINES_HOUSE_EDGE) / probability)
    max_multiplier = {1: 5.0, 2: 10.0, 3: 20.0, 5: 50.0, 7: 100.0, 10: 300.0, 12: 500.0, 14: 1000.0, 15: 2000.0}
    closest = min(max_multiplier.keys(), key=lambda k: abs(k - mines_count))
    return round(min(multiplier, max_multiplier.get(closest, 50.0)), 2)

REFERRAL_PERCENT = 7
MAX_WITHDRAW_AMOUNT = 50000
WITHDRAW_FEE = 0.05
WITHDRAW_COOLDOWN_HOURS = 24
MIN_DEPOSIT_FOR_REFERRAL = 50

# ========== АПГРЕЙД (СЛОЖНЫЙ) ==========
def calculate_upgrade_chance(current_price, target_price, chance_bonus=0):
    """Реалистичный шанс апгрейда - СЛОЖНЫЙ!"""
    diff = target_price - current_price
    ratio = current_price / target_price
    
    if ratio >= 0.98:
        base = 70
    elif ratio >= 0.95:
        base = 60
    elif ratio >= 0.90:
        base = 50
    elif ratio >= 0.80:
        base = 38
    elif ratio >= 0.70:
        base = 28
    elif ratio >= 0.55:
        base = 18
    elif ratio >= 0.40:
        base = 10
    elif ratio >= 0.25:
        base = 5
    else:
        base = 2
    
    if diff <= 10:
        base += 8
    elif diff <= 25:
        base += 5
    elif diff <= 50:
        base += 2
    elif diff <= 100:
        base += 0
    elif diff <= 300:
        base -= 3
    elif diff <= 500:
        base -= 6
    elif diff <= 1000:
        base -= 10
    elif diff <= 2000:
        base -= 15
    elif diff <= 5000:
        base -= 22
    elif diff <= 10000:
        base -= 30
    elif diff <= 20000:
        base -= 40
    elif diff <= 50000:
        base -= 50
    else:
        base -= 60
    
    if target_price >= 50000:
        base -= 15
    elif target_price >= 30000:
        base -= 10
    elif target_price >= 15000:
        base -= 6
    elif target_price >= 8000:
        base -= 3
    elif target_price >= 3000:
        base -= 1
    
    base += chance_bonus
    random_factor = random.randint(-2, 2)
    base += random_factor
    
    return min(max(round(base, 2), 0.01), 70)

UPGRADE_PRICES = [50, 100, 200, 500, 1000, 2000, 5000, 7500, 10000, 15000]

def get_upgrade_item_price(item_name):
    for item in METRO_LOOT_POOL:
        if item["name"] == item_name:
            return item["price"]
    return 0

TOP_PRIZES = {1: 1500, 2: 1000, 3: 500, 4: 100, 5: 50}
TOP_MIN_PLAYERS = 100
TOP_RESET_DAYS = 14

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
    return f"{tg_id}:{round_id}"

def generate_crash_point():
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

class AdminChanceRequest(BaseModel):
    target_tg_id: int
    chance_percent: int

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
    success: bool = False
    free_upgrade: bool = False

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
        return await sio.emit('error', {'message': f'Ставка {CRASH_MIN_BET}-{CRASH_MAX_BET}⭐️'}, to=sid)
    
    key = bet_key(tg_id, crash_state["round_id"])
    if key in crash_state["bets"]:
        return await sio.emit('error', {'message': 'Уже есть ставка'}, to=sid)
    
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "UPDATE users SET balance=balance-?, total_spent=total_spent+? WHERE tg_id=? AND balance>=?",
            (bet, bet, tg_id, bet)
        )
        await db.commit()
        if cursor.rowcount == 0:
            return await sio.emit('error', {'message': 'Недостаточно монет'}, to=sid)
        async with db.execute("SELECT balance FROM users WHERE tg_id=?", (tg_id,)) as cursor:
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
        return await sio.emit('error', {'message': 'Ракета упала!'}, to=sid)
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
        await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (win_amount, tg_id))
        await db.commit()
        async with db.execute("SELECT balance FROM users WHERE tg_id=?", (tg_id,)) as cursor:
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

# ========== STARTUP ==========
@app.on_event("startup")
async def startup():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS users (tg_id INTEGER PRIMARY KEY, username TEXT DEFAULT 'Игрок', balance INTEGER DEFAULT 0, total_spent INTEGER DEFAULT 0, inventory TEXT DEFAULT '[]')")
        await db.execute("CREATE TABLE IF NOT EXISTS withdraws (id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, amount INTEGER, requisites TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("CREATE TABLE IF NOT EXISTS referrals (user_id INTEGER PRIMARY KEY, referrer_id INTEGER NOT NULL, activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, total_earned INTEGER DEFAULT 0)")
        await db.execute("CREATE TABLE IF NOT EXISTS referral_earnings (id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id INTEGER NOT NULL, referral_id INTEGER NOT NULL, deposit_amount INTEGER NOT NULL, earned INTEGER NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("CREATE TABLE IF NOT EXISTS withdraw_cooldowns (user_id INTEGER PRIMARY KEY, last_withdraw_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("CREATE TABLE IF NOT EXISTS promo_uses (user_id INTEGER, promo_code TEXT, used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, promo_code))")
        await db.execute("CREATE TABLE IF NOT EXISTS promos (code TEXT PRIMARY KEY, reward_type TEXT, case_type TEXT, stars INTEGER DEFAULT 0, max_uses INTEGER DEFAULT 1, uses INTEGER DEFAULT 0, created_by INTEGER)")
        await db.execute("CREATE TABLE IF NOT EXISTS top_rewards (id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, amount INTEGER, place INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("CREATE TABLE IF NOT EXISTS free_case_uses (user_id INTEGER PRIMARY KEY, last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("CREATE TABLE IF NOT EXISTS user_chances (tg_id INTEGER PRIMARY KEY, chance_bonus INTEGER DEFAULT 0)")
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
        if hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest() != hash_value:
            raise HTTPException(status_code=401, detail="Data validation failed")
        return json.loads(init_data.get('user', ['{}'])[0])
    except Exception:
        raise HTTPException(status_code=401, detail="Parsing error")

# ========== ПОЛЬЗОВАТЕЛИ ==========
async def get_or_create_user(tg_id: int, username: str = "Игрок"):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT balance, total_spent, inventory FROM users WHERE tg_id=?", (tg_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                await db.execute("UPDATE users SET username=? WHERE tg_id=?", (username, tg_id))
                await db.commit()
                return {"balance": row[0], "total_spent": row[1], "inventory": json.loads(row[2])}
            else:
                start_balance = 10000 if (ADMIN_TG_ID and tg_id == ADMIN_TG_ID) else 0
                await db.execute(
                    "INSERT INTO users (tg_id, username, balance, total_spent, inventory) VALUES (?,?,?,0,'[]')",
                    (tg_id, username, start_balance)
                )
                await db.commit()
                return {"balance": start_balance, "total_spent": 0, "inventory": []}

# ========== PROFILE ==========
@app.get("/api/profile")
async def get_profile(user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    username = user.get('first_name', 'Игрок')
    user_info = await get_or_create_user(tg_id, username)
    user_info["is_admin"] = (ADMIN_TG_ID and tg_id == ADMIN_TG_ID)
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=?", (tg_id,)) as cursor:
            user_info["friends_count"] = (await cursor.fetchone())[0]
        async with db.execute("SELECT last_used FROM free_case_uses WHERE user_id=?", (tg_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                try:
                    last_ts = time.mktime(time.strptime(row[0], "%Y-%m-%d %H:%M:%S"))
                except:
                    last_ts = 0
                user_info["free_case_available"] = (time.time() - last_ts) >= 86400
            else:
                user_info["free_case_available"] = True
        async with db.execute("SELECT chance_bonus FROM user_chances WHERE tg_id=?", (tg_id,)) as cursor:
            row = await cursor.fetchone()
            user_info["chance_bonus"] = row[0] if row else 0
    return user_info

# ========== АДМИНКА ==========
@app.post("/api/admin/give_stars")
async def admin_give_stars(user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    if not ADMIN_TG_ID or tg_id != ADMIN_TG_ID:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance=balance+10000 WHERE tg_id=?", (tg_id,))
        await db.commit()
    return {"success": True, "message": "✅ +10,000 Stars"}

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
        raise HTTPException(status_code=400, detail="Макс 1M ⭐️")
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (tg_id, username, balance, total_spent, inventory) VALUES (?,?,0,0,'[]')",
            (req.target_tg_id, f"Player_{req.target_tg_id}")
        )
        await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (req.amount, req.target_tg_id))
        await db.commit()
    return {"success": True, "message": f"✅ Выдано {req.amount} ⭐️"}

@app.post("/api/admin/remove_stars")
async def admin_remove_stars(req: AdminGiveStarsRequest, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    if not ADMIN_TG_ID or tg_id != ADMIN_TG_ID:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    if req.target_tg_id <= 0:
        raise HTTPException(status_code=400, detail="Неверный ID")
    if req.amount < 1:
        raise HTTPException(status_code=400, detail="Сумма > 0")
    
    async with aiosqlite.connect(DB_NAME) as db:
        row = await (await db.execute("SELECT balance FROM users WHERE tg_id=?", (req.target_tg_id,))).fetchone()
        if not row:
            raise HTTPException(status_code=400, detail="Игрок не найден")
        if row[0] < req.amount:
            raise HTTPException(status_code=400, detail=f"У игрока только {row[0]} ⭐️")
        
        await db.execute("UPDATE users SET balance=balance-? WHERE tg_id=?", (req.amount, req.target_tg_id))
        await db.commit()
    
    return {"success": True, "message": f"✅ Забрано {req.amount} ⭐️ у игрока {req.target_tg_id}"}

@app.post("/api/admin/add_chance")
async def admin_add_chance(req: AdminChanceRequest, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    if not ADMIN_TG_ID or tg_id != ADMIN_TG_ID:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    if req.target_tg_id <= 0:
        raise HTTPException(status_code=400, detail="Неверный ID")
    if req.chance_percent < 1 or req.chance_percent > 100:
        raise HTTPException(status_code=400, detail="Процент от 1 до 100")
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO user_chances (tg_id, chance_bonus) VALUES (?, ?)",
            (req.target_tg_id, req.chance_percent)
        )
        await db.commit()
    return {"success": True, "message": f"✅ Игроку {req.target_tg_id} добавлен шанс +{req.chance_percent}%"}

# ========== PROMO ==========
@app.post("/api/admin/promo/create")
async def create_promo(req: PromoCreateRequest, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    if not ADMIN_TG_ID or tg_id != ADMIN_TG_ID:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    code = req.code.strip().upper()
    async with aiosqlite.connect(DB_NAME) as db:
        if await (await db.execute("SELECT code FROM promos WHERE code=?", (code,))).fetchone():
            raise HTTPException(status_code=400, detail="Такой код уже существует")
        await db.execute(
            "INSERT INTO promos (code, reward_type, case_type, stars, max_uses, created_by) VALUES (?,?,?,?,?,?)",
            (code, req.reward_type, req.case_type, req.stars, req.max_uses, tg_id)
        )
        await db.commit()
    return {"success": True, "message": f"✅ Промокод {code} создан!"}

@app.post("/api/promo/activate")
async def activate_promo(code: str, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    code = code.strip().upper()
    async with aiosqlite.connect(DB_NAME) as db:
        promo = await (await db.execute("SELECT * FROM promos WHERE code=?", (code,))).fetchone()
        if not promo:
            raise HTTPException(status_code=400, detail="Неверный промокод")
        if promo[5] >= promo[4]:
            raise HTTPException(status_code=400, detail="Промокод закончился")
        if await (await db.execute("SELECT used_at FROM promo_uses WHERE user_id=? AND promo_code=?", (tg_id, code))).fetchone():
            raise HTTPException(status_code=400, detail="Вы уже использовали этот промокод!")
        reward_name = None
        if promo[1] == "case":
            ct = promo[2]
            pool = CONTAINER_DROPS[ct] if ct in CONTAINER_PRICES else CONTAINER_DROPS[ct]
            weights = CONTAINER_DROP_WEIGHTS if ct in CONTAINER_PRICES else CONTAINER_DROP_WEIGHTS
            reward_id = random.choices(list(pool.keys()), weights=weights, k=1)[0]
            reward_name = pool[reward_id][0]
            async with db.execute("SELECT inventory FROM users WHERE tg_id=?", (tg_id,)) as cursor:
                inventory = json.loads((await cursor.fetchone())[0] or '[]')
            inventory.append({"id": reward_id, "name": reward_name, "case": ct})
            await db.execute("UPDATE users SET inventory=? WHERE tg_id=?", (json.dumps(inventory), tg_id))
        elif promo[1] == "stars":
            await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (promo[3], tg_id))
            reward_name = f"⭐ {promo[3]} Stars"
        await db.execute("INSERT INTO promo_uses (user_id, promo_code) VALUES (?,?)", (tg_id, code))
        await db.execute("UPDATE promos SET uses=uses+1 WHERE code=?", (code,))
        await db.commit()
    return {"success": True, "message": f"🎉 Промокод {code} активирован!", "reward": reward_name}

# ========== TOP REWARDS ==========
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
                await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (prize, tid))
                await db.execute("INSERT INTO top_rewards (tg_id, amount, place) VALUES (?,?,?)", (tid, prize, place))
        await db.commit()
    return {"success": True, "message": "✅ Призы выплачены топ-5 по потраченной валюте!", "top": [(t[1], t[2], TOP_PRIZES.get(i+1, 0)) for i, t in enumerate(top5)]}

@app.get("/api/top/rewards")
async def get_top_rewards(user: dict = Depends(verify_telegram_data)):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT tg_id, amount, place, created_at FROM top_rewards ORDER BY created_at DESC LIMIT 20") as cursor:
            rows = await cursor.fetchall()
        return [{"tg_id": r[0], "amount": r[1], "place": r[2], "date": r[3]} for r in rows]

# ========== FREE CASE ==========
@app.post("/api/free_case/claim")
async def claim_free_case(user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    async with aiosqlite.connect(DB_NAME) as db:
        row = await (await db.execute("SELECT last_used FROM free_case_uses WHERE user_id=?", (tg_id,))).fetchone()
        if row:
            try:
                last_ts = time.mktime(time.strptime(row[0], "%Y-%m-%d %H:%M:%S"))
            except:
                last_ts = 0
            if time.time() - last_ts < 86400:
                raise HTTPException(status_code=400, detail="Бесплатный кейс уже использован! Приходите через 24 часа.")
        
        await db.execute("INSERT OR REPLACE INTO free_case_uses (user_id, last_used) VALUES (?, CURRENT_TIMESTAMP)", (tg_id,))
        
        weights = [d["weight"] for d in FREE_CASE_DROPS]
        total_weight = sum(weights)
        norm_weights = [w / total_weight for w in weights]
        
        reward = random.choices(FREE_CASE_DROPS, weights=norm_weights, k=1)[0]
        reward_name = reward["name"]
        reward_price = reward["price"]
        
        await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (reward_price, tg_id))
        
        async with db.execute("SELECT inventory FROM users WHERE tg_id=?", (tg_id,)) as cursor:
            inventory = json.loads((await cursor.fetchone())[0] or '[]')
        inventory.append({"id": f"free_{int(time.time())}", "name": reward_name, "case": "free_case"})
        await db.execute("UPDATE users SET inventory=? WHERE tg_id=?", (json.dumps(inventory), tg_id))
        await db.commit()
    
    return {"success": True, "reward": reward_name, "price": reward_price}

# ========== CASES (КОНТЕЙНЕРЫ) ==========
@app.post("/api/case/open")
async def open_case(req: OpenCaseRequest, user: dict = Depends(verify_telegram_data)):
    if req.case_type in CONTAINER_PRICES:
        price = CONTAINER_PRICES[req.case_type]
        pool = CONTAINER_DROPS[req.case_type]
        weights = CONTAINER_DROP_WEIGHTS
    else:
        raise HTTPException(status_code=400, detail="Неизвестный контейнер")
    
    tg_id = user.get('id')
    user_info = await get_or_create_user(tg_id)
    if user_info["balance"] < price:
        raise HTTPException(status_code=400, detail="Недостаточно монет")
    
    reward_id = random.choices(list(pool.keys()), weights=weights, k=1)[0]
    reward_name = pool[reward_id][0]
    new_balance = user_info["balance"] - price
    user_info["inventory"].append({"id": reward_id, "name": reward_name, "case": req.case_type})
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET balance=?, total_spent=total_spent+?, inventory=? WHERE tg_id=?",
            (new_balance, price, json.dumps(user_info["inventory"]), tg_id)
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
    gain = 0
    
    gain = get_upgrade_item_price(item["name"])
    if gain == 0:
        if item["case"] in CONTAINER_DROPS and item["id"] in CONTAINER_DROPS[item["case"]]:
            gain = CONTAINER_DROPS[item["case"]][item["id"]][1]
    
    new_balance = user_info["balance"] + gain
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance=?, inventory=? WHERE tg_id=?", (new_balance, json.dumps(inventory), tg_id))
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
        gain = get_upgrade_item_price(item["name"])
        if gain == 0:
            if item["case"] in CONTAINER_DROPS and item["id"] in CONTAINER_DROPS[item["case"]]:
                gain = CONTAINER_DROPS[item["case"]][item["id"]][1]
        total_gain += gain
    new_balance = user_info["balance"] + total_gain
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance=?, inventory='[]' WHERE tg_id=?", (new_balance, tg_id))
        await db.commit()
    return {"gain": total_gain, "balance": new_balance}

# ========== АПГРЕЙД ==========
@app.post("/api/inventory/upgrade")
async def upgrade_item(req: UpgradeItemRequest, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    user_info = await get_or_create_user(tg_id)
    inventory = user_info["inventory"]
    
    if req.item_index < 0 or req.item_index >= len(inventory):
        raise HTTPException(status_code=400, detail="Предмет не найден")
    
    item = inventory[req.item_index]
    current_price = 0
    
    current_price = get_upgrade_item_price(item["name"])
    if current_price == 0:
        if item["case"] in CONTAINER_DROPS and item["id"] in CONTAINER_DROPS[item["case"]]:
            current_price = CONTAINER_DROPS[item["case"]][item["id"]][1]
    
    if current_price == 0:
        raise HTTPException(status_code=400, detail="Нельзя улучшить")
    
    if req.target_price <= current_price:
        raise HTTPException(status_code=400, detail="Цель должна быть дороже текущей")
    
    async with aiosqlite.connect(DB_NAME) as db:
        row = await (await db.execute("SELECT chance_bonus FROM user_chances WHERE tg_id=?", (tg_id,))).fetchone()
        chance_bonus = row[0] if row else 0
    
    upgrade_chance = calculate_upgrade_chance(current_price, req.target_price, chance_bonus) / 100
    
    is_success = req.success if hasattr(req, 'success') else random.random() < upgrade_chance
    
    if is_success:
        new_item = None
        
        for up_item in METRO_LOOT_POOL:
            if up_item["price"] == req.target_price:
                new_item = {"id": f"up_{up_item['name']}", "name": up_item["name"], "case": "upgrade"}
                break
        
        if not new_item:
            all_drops = {**CONTAINER_DROPS}
            for ct, drops in all_drops.items():
                for did, (dname, dprice) in drops.items():
                    if dprice == req.target_price:
                        new_item = {"id": did, "name": dname, "case": ct}
                        break
                if new_item:
                    break
        
        if new_item:
            inventory[req.item_index] = new_item
        else:
            all_prices = []
            for up_item in METRO_LOOT_POOL:
                all_prices.append(up_item["price"])
            for drops in all_drops.values():
                for _, (_, price) in drops.items():
                    all_prices.append(price)
            closest_price = min(all_prices, key=lambda x: abs(x - req.target_price))
            
            for up_item in METRO_LOOT_POOL:
                if up_item["price"] == closest_price:
                    inventory[req.item_index] = {"id": f"up_{up_item['name']}", "name": up_item["name"], "case": "upgrade"}
                    break
            if not inventory[req.item_index]:
                for ct, drops in all_drops.items():
                    for did, (dname, dprice) in drops.items():
                        if dprice == closest_price:
                            inventory[req.item_index] = {"id": did, "name": dname, "case": ct}
                            break
                    if inventory[req.item_index]:
                        break
        
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET inventory=? WHERE tg_id=?", (json.dumps(inventory), tg_id))
            await db.commit()
        
        return {
            "success": True,
            "message": f"✅ Успех! Улучшено до {req.target_price}⭐️",
            "new_balance": user_info["balance"],
            "upgrade_cost": 0,
            "new_item": inventory[req.item_index]["name"],
            "chance": int(upgrade_chance * 100)
        }
    else:
        inventory.pop(req.item_index)
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET inventory=? WHERE tg_id=?", (json.dumps(inventory), tg_id))
            await db.commit()
        
        return {
            "success": False,
            "message": f"💥 Предмет сгорел!",
            "new_balance": user_info["balance"],
            "upgrade_cost": 0,
            "chance": int(upgrade_chance * 100)
        }

# ========== ФУНКЦИЯ ДЛЯ ПОЛУЧЕНИЯ ЦЕН ПРЕДМЕТОВ ==========
@app.get("/api/upgrade/prices")
async def get_upgrade_prices(user: dict = Depends(verify_telegram_data)):
    prices = []
    for item in METRO_LOOT_POOL:
        prices.append(item["price"])
    all_drops = {**CONTAINER_DROPS}
    for drops in all_drops.values():
        for _, (_, price) in drops.items():
            prices.append(price)
    return {"prices": sorted(list(set(prices)))}

# ========== LEADERBOARD ==========
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

# ========== STARS SHOP ==========
@app.post("/api/stars/buy")
async def buy_stars(stars_amount: int, user: dict = Depends(verify_telegram_data)):
    if stars_amount < 50:
        raise HTTPException(status_code=400, detail="Минимум 50 Stars")
    tg_id = user.get('id')
    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/createInvoiceLink",
            json={
                "title": "Пополнение баланса",
                "description": f"Покупка {stars_amount} Stars",
                "payload": f"deposit_{tg_id}_{stars_amount}",
                "provider_token": "",
                "currency": "XTR",
                "prices": [{"label": "Stars", "amount": stars_amount}]
            }
        )
        res_data = res.json()
        if res_data.get("ok"):
            if stars_amount >= MIN_DEPOSIT_FOR_REFERRAL:
                async with aiosqlite.connect(DB_NAME) as db:
                    ref = await (await db.execute("SELECT referrer_id FROM referrals WHERE user_id=?", (tg_id,))).fetchone()
                    if ref:
                        earned = int(stars_amount * REFERRAL_PERCENT / 100)
                        await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (earned, ref[0]))
                        await db.execute("UPDATE referrals SET total_earned=total_earned+? WHERE user_id=?", (earned, tg_id))
                        await db.execute(
                            "INSERT INTO referral_earnings (referrer_id, referral_id, deposit_amount, earned) VALUES (?,?,?,?)",
                            (ref[0], tg_id, stars_amount, earned)
                        )
                        await db.commit()
            return {"invoice_url": res_data["result"]}
        else:
            raise HTTPException(status_code=500, detail="Ошибка Telegram Invoice")

# ========== WITHDRAW ==========
@app.post("/api/withdraw")
async def create_withdraw(amount: int, wallet: str, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    if amount < 100:
        raise HTTPException(status_code=400, detail="Минимум 100 Stars")
    if amount > MAX_WITHDRAW_AMOUNT:
        raise HTTPException(status_code=400, detail=f"Максимум {MAX_WITHDRAW_AMOUNT} Stars")
    user_info = await get_or_create_user(tg_id)
    if user_info["balance"] < amount:
        raise HTTPException(status_code=400, detail="Недостаточно монет")
    async with aiosqlite.connect(DB_NAME) as db:
        row = await (await db.execute("SELECT last_withdraw_at FROM withdraw_cooldowns WHERE user_id=?", (tg_id,))).fetchone()
        if row:
            try:
                last_ts = time.mktime(time.strptime(row[0], "%Y-%m-%d %H:%M:%S"))
            except:
                last_ts = 0
            if time.time() - last_ts < WITHDRAW_COOLDOWN_HOURS * 3600:
                raise HTTPException(status_code=400, detail=f"Следующий вывод через {int((WITHDRAW_COOLDOWN_HOURS*3600-(time.time()-last_ts))/3600)} ч.")
        fee = int(amount * WITHDRAW_FEE)
        payout = amount - fee
        new_balance = user_info["balance"] - amount
        await db.execute("UPDATE users SET balance=? WHERE tg_id=?", (new_balance, tg_id))
        await db.execute("INSERT INTO withdraws (tg_id, amount, requisites, status) VALUES (?,?,?,'pending')", (tg_id, amount, wallet))
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
            ticket = await (await db.execute("SELECT tg_id, amount FROM withdraws WHERE id=?", (req.ticket_id,))).fetchone()
            if ticket:
                await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (ticket[1], ticket[0]))
        await db.execute("UPDATE withdraws SET status=? WHERE id=?", (req.status, req.ticket_id))
        await db.commit()
    return {"success": True, "new_status": req.status}

# ========== REFERRAL ==========
@app.post("/api/referral/activate")
async def activate_referral(req: ReferralActivateRequest, user: dict = Depends(verify_telegram_data)):
    user_id = user.get('id')
    if user_id == req.referrer_id:
        raise HTTPException(status_code=400, detail="Нельзя себе")
    async with aiosqlite.connect(DB_NAME) as db:
        if not await (await db.execute("SELECT tg_id FROM users WHERE tg_id=?", (req.referrer_id,))).fetchone():
            raise HTTPException(status_code=400, detail="Не найден")
        if await (await db.execute("SELECT referrer_id FROM referrals WHERE user_id=?", (user_id,))).fetchone():
            raise HTTPException(status_code=400, detail="Уже есть")
        await db.execute("INSERT INTO referrals (user_id, referrer_id) VALUES (?,?)", (user_id, req.referrer_id))
        await db.commit()
    return {"success": True, "referrer_id": req.referrer_id}

@app.get("/api/referral/stats")
async def get_referral_stats(user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    async with aiosqlite.connect(DB_NAME) as db:
        earned = await (await db.execute("SELECT total_earned FROM referrals WHERE user_id=?", (tg_id,))).fetchone()
        count = await (await db.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=?", (tg_id,))).fetchone()
        history = await (await db.execute(
            "SELECT referral_id, deposit_amount, earned, created_at FROM referral_earnings WHERE referrer_id=? ORDER BY created_at DESC LIMIT 20",
            (tg_id,)
        )).fetchall()
    return {
        "total_earned": earned[0] if earned else 0,
        "referrals_count": count[0] if count else 0,
        "percent": REFERRAL_PERCENT,
        "history": [{"referral_id": r[0], "deposit": r[1], "earned": r[2], "date": r[3]} for r in history]
    }

# ========== MINES ==========
@app.post("/api/mines/start")
async def mines_start(req: MinesStartRequest, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    if req.bet_amount < 10:
        raise HTTPException(status_code=400, detail="Мин. 10 ⭐️")
    if req.bet_amount > 50000:
        raise HTTPException(status_code=400, detail="Макс. 50k ⭐️")
    if req.mines_count < MINES_MIN_COUNT or req.mines_count > MINES_MAX_COUNT:
        raise HTTPException(status_code=400, detail=f"Мины {MINES_MIN_COUNT}-{MINES_MAX_COUNT}")
    if tg_id in active_mines_games:
        raise HTTPException(status_code=400, detail="Завершите игру")
    user_info = await get_or_create_user(tg_id)
    if user_info["balance"] < req.bet_amount:
        raise HTTPException(status_code=400, detail="Недостаточно монет")
    new_balance = user_info["balance"] - req.bet_amount
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance=?, total_spent=total_spent+? WHERE tg_id=?", (new_balance, req.bet_amount, tg_id))
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
        await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (win_amount, tg_id))
        await db.commit()
        new_balance = (await (await db.execute("SELECT balance FROM users WHERE tg_id=?", (tg_id,))).fetchone())[0]
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

# ========== CRASH API ==========
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(socket_app, host="0.0.0.0", port=8000)
