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

# ========== ЗВЁЗДНЫЕ КЕЙСЫ (10 штук) ==========
STAR_CASE_PRICES = {
    "star_case_1": 50, "star_case_2": 150, "star_case_3": 400, 
    "star_case_4": 750, "star_case_5": 1500, "star_case_6": 2500,
    "star_case_7": 4000, "star_case_8": 6000, "star_case_9": 9000,
    "star_case_10": 15000
}

STAR_CASE_DROPS = {
    "star_case_1": {"s1_1": ("⭐ 12 Stars", 12), "s1_2": ("⭐ 29 Stars", 29), "s1_3": ("⭐ 46 Stars", 46), "s1_4": ("⭐ 69 Stars", 69), "s1_5": ("⭐ 115 Stars", 115), "s1_6": ("⭐ 230 Stars", 230)},
    "star_case_2": {"s2_1": ("⭐ 35 Stars", 35), "s2_2": ("⭐ 86 Stars", 86), "s2_3": ("⭐ 138 Stars", 138), "s2_4": ("⭐ 230 Stars", 230), "s2_5": ("⭐ 403 Stars", 403), "s2_6": ("⭐ 690 Stars", 690)},
    "star_case_3": {"s3_1": ("⭐ 92 Stars", 92), "s3_2": ("⭐ 230 Stars", 230), "s3_3": ("⭐ 403 Stars", 403), "s3_4": ("⭐ 575 Stars", 575), "s3_5": ("⭐ 920 Stars", 920), "s3_6": ("⭐ 1725 Stars", 1725)},
    "star_case_4": {"s4_1": ("⭐ 173 Stars", 173), "s4_2": ("⭐ 460 Stars", 460), "s4_3": ("⭐ 748 Stars", 748), "s4_4": ("⭐ 1150 Stars", 1150), "s4_5": ("⭐ 2070 Stars", 2070), "s4_6": ("⭐ 3450 Stars", 3450)},
    "star_case_5": {"s5_1": ("⭐ 345 Stars", 345), "s5_2": ("⭐ 920 Stars", 920), "s5_3": ("⭐ 1495 Stars", 1495), "s5_4": ("⭐ 2300 Stars", 2300), "s5_5": ("⭐ 4025 Stars", 4025), "s5_6": ("⭐ 6900 Stars", 6900)},
    "star_case_6": {"s6_1": ("⭐ 575 Stars", 575), "s6_2": ("⭐ 1495 Stars", 1495), "s6_3": ("⭐ 2530 Stars", 2530), "s6_4": ("⭐ 4025 Stars", 4025), "s6_5": ("⭐ 6325 Stars", 6325), "s6_6": ("⭐ 11500 Stars", 11500)},
    "star_case_7": {"s7_1": ("⭐ 920 Stars", 920), "s7_2": ("⭐ 1725 Stars", 1725), "s7_3": ("⭐ 2875 Stars", 2875), "s7_4": ("⭐ 4600 Stars", 4600), "s7_5": ("⭐ 8050 Stars", 8050), "s7_6": ("⭐ 13800 Stars", 13800)},
    "star_case_8": {"s8_1": ("⭐ 1380 Stars", 1380), "s8_2": ("⭐ 2530 Stars", 2530), "s8_3": ("⭐ 4370 Stars", 4370), "s8_4": ("⭐ 6900 Stars", 6900), "s8_5": ("⭐ 11500 Stars", 11500), "s8_6": ("⭐ 20700 Stars", 20700)},
    "star_case_9": {"s9_1": ("⭐ 2070 Stars", 2070), "s9_2": ("⭐ 4025 Stars", 4025), "s9_3": ("⭐ 6900 Stars", 6900), "s9_4": ("⭐ 10350 Stars", 10350), "s9_5": ("⭐ 17250 Stars", 17250), "s9_6": ("⭐ 28750 Stars", 28750)},
    "star_case_10": {"s10_1": ("⭐ 3450 Stars", 3450), "s10_2": ("⭐ 6325 Stars", 6325), "s10_3": ("⭐ 10350 Stars", 10350), "s10_4": ("⭐ 17250 Stars", 17250), "s10_5": ("⭐ 28750 Stars", 28750), "s10_6": ("⭐ 46000 Stars", 46000)}
}

# ========== NFT КЕЙСЫ (10 штук) ==========
NFT_CASE_PRICES = {
    "nft_case_1": 100, "nft_case_2": 250, "nft_case_3": 500, 
    "nft_case_4": 1000, "nft_case_5": 1750, "nft_case_6": 3000,
    "nft_case_7": 5000, "nft_case_8": 7500, "nft_case_9": 10000,
    "nft_case_10": 15000
}

NFT_CASE_DROPS = {
    "nft_case_1": {"n1_1": ("💎 Blood Gem", 23), "n1_2": ("💜 Amethyst", 40), "n1_3": ("💙 Sapphire", 63), "n1_4": ("💍 Princess Cut", 92), "n1_5": ("👑 King Midas", 150), "n1_6": ("💚 Kryptonite", 288)},
    "nft_case_2": {"n2_1": ("🐱 Scared Cat", 52), "n2_2": ("👻 Spooky Cat", 86), "n2_3": ("🐟 Fish Skeleton Cat", 138), "n2_4": ("🦇 Bat Cat", 219), "n2_5": ("🦠 Virus Cat", 345), "n2_6": ("👾 Glitch Cat", 575)},
    "nft_case_3": {"n3_1": ("🔵 Evil Eye Blue", 104), "n3_2": ("🟢 Evil Eye Green", 173), "n3_3": ("🔴 Evil Eye Red", 276), "n3_4": ("🟡 Evil Eye Gold", 437), "n3_5": ("⚫ Evil Eye Black", 690), "n3_6": ("💎 Evil Eye Crystal", 1150)},
    "nft_case_4": {"n4_1": ("🍑 Precious Peach", 207), "n4_2": ("🍑 Golden Peach", 345), "n4_3": ("🍑 Diamond Peach", 552), "n4_4": ("🍑 Royal Peach", 863), "n4_5": ("🍑 Mystic Peach", 1380), "n4_6": ("🍑 Legendary Peach", 2300)},
    "nft_case_5": {"n5_1": ("🧢 Durov's Cap", 368), "n5_2": ("⚪ Cap Silver", 610), "n5_3": ("⚫ Cap Black", 978), "n5_4": ("🟡 Cap Gold Trim", 1553), "n5_5": ("⭐ Founder Edition Cap", 2415), "n5_6": ("👑 Durov's Crown Cap", 4025)},
    "nft_case_6": {"n6_1": ("🐸 Plush Pepe", 690), "n6_2": ("😊 Pepe Smile", 1150), "n6_3": ("😎 Pepe Chill", 1840), "n6_4": ("🤨 Pepe Rare", 2875), "n6_5": ("✨ Golden Plush Pepe", 4600), "n6_6": ("👑 Mythic Plush Pepe", 8050)},
    "nft_case_7": {"n7_1": ("🦊 Shadow Fox", 1150), "n7_2": ("🦊 Golden Fox", 1725), "n7_3": ("🦊 Crystal Fox", 2875), "n7_4": ("🦊 Royal Fox", 4600), "n7_5": ("🦊 Mystic Fox", 8050), "n7_6": ("🦊 Legendary Fox", 13800)},
    "nft_case_8": {"n8_1": ("🐉 Dragon Scale", 1725), "n8_2": ("🐉 Dragon Eye", 2875), "n8_3": ("🐉 Dragon Wing", 4600), "n8_4": ("🐉 Dragon Crown", 7475), "n8_5": ("🐉 Dragon Soul", 13800), "n8_6": ("🐉 God Dragon", 23000)},
    "nft_case_9": {"n9_1": ("👾 Cyber Samurai", 2875), "n9_2": ("👾 Golden Samurai", 4600), "n9_3": ("👾 Dark Samurai", 7475), "n9_4": ("👾 Samurai Lord", 11500), "n9_5": ("👾 Samurai Emperor", 20700), "n9_6": ("👾 God Samurai", 34500)},
    "nft_case_10": {"n10_1": ("👑 Crown Prince", 4600), "n10_2": ("👑 Crown King", 8050), "n10_3": ("👑 Crown Emperor", 13800), "n10_4": ("👑 Crown God", 23000), "n10_5": ("👑 Crown Cosmic", 40250), "n10_6": ("👑 Crown Creator", 69000)}
}

# ========== БЕСПЛАТНЫЙ КЕЙС ==========
FREE_CASE_DROPS = [
    {"name": "⭐ 0.1 Stars", "price": 0.1, "weight": 35.0},
    {"name": "⭐ 0.5 Stars", "price": 0.5, "weight": 25.0},
    {"name": "⭐ 1 Star", "price": 1.0, "weight": 18.0},
    {"name": "⭐ 2.5 Stars", "price": 2.5, "weight": 12.0},
    {"name": "⭐ 5 Stars", "price": 5.0, "weight": 6.0},
    {"name": "⭐ 15 Stars", "price": 15.0, "weight": 2.5},
    {"name": "⭐ 35 Stars", "price": 35.0, "weight": 1.0},
    {"name": "⭐ 60 Stars", "price": 60.0, "weight": 0.4},
    {"name": "⭐ 100 Stars", "price": 100.0, "weight": 0.1}
]

# ========== 100+ НОВЫХ ПРЕДМЕТОВ ДЛЯ АПГРЕЙДА ==========
UPGRADE_ITEMS_POOL = [
    {"name": "⚡ Искра", "price": 5},
    {"name": "✨ Звездная пыль", "price": 8},
    {"name": "🌙 Лунный камень", "price": 12},
    {"name": "☀️ Солнечный кристалл", "price": 18},
    {"name": "💧 Капля росы", "price": 25},
    {"name": "🔥 Огненный цветок", "price": 35},
    {"name": "🌀 Ветряной ключ", "price": 45},
    {"name": "🌍 Земляной талисман", "price": 55},
    {"name": "💎 Алмазный осколок", "price": 70},
    {"name": "👑 Золотая корона", "price": 90},
    {"name": "🦄 Единорожий рог", "price": 110},
    {"name": "🐉 Драконий зуб", "price": 140},
    {"name": "🌺 Мистический цветок", "price": 170},
    {"name": "⭐ Падающая звезда", "price": 200},
    {"name": "🌌 Космический камень", "price": 240},
    {"name": "🌀 Изумрудный шар", "price": 280},
    {"name": "🔮 Хрустальный шар", "price": 330},
    {"name": "⚔️ Легендарный меч", "price": 380},
    {"name": "🛡️ Щит судьбы", "price": 440},
    {"name": "👁️ Око провидения", "price": 500},
    {"name": "🧙‍♂️ Магический посох", "price": 570},
    {"name": "🐲 Лунный дракон", "price": 650},
    {"name": "🌠 Метеоритный осколок", "price": 740},
    {"name": "💠 Небесный кристалл", "price": 840},
    {"name": "🌟 Сияющая звезда", "price": 950},
    {"name": "🎭 Карнавальная маска", "price": 1070},
    {"name": "🏆 Золотой трофей", "price": 1200},
    {"name": "🎪 Цирковой огонь", "price": 1340},
    {"name": "🌋 Вулканический камень", "price": 1490},
    {"name": "❄️ Ледяной жезл", "price": 1650},
    {"name": "🌈 Радужный амулет", "price": 1820},
    {"name": "☄️ Кометный хвост", "price": 2000},
    {"name": "🌊 Жемчужина океана", "price": 2200},
    {"name": "🌲 Древо жизни", "price": 2420},
    {"name": "🏔️ Сердце гор", "price": 2660},
    {"name": "🌅 Утренний свет", "price": 2920},
    {"name": "🌙 Полная луна", "price": 3200},
    {"name": "☀️ Божественное солнце", "price": 3500},
    {"name": "✨ Космический кристалл", "price": 3820},
    {"name": "🌀 Эфирный шар", "price": 4160},
    {"name": "⚡ Молниевый жезл", "price": 4520},
    {"name": "🌪️ Торнадо в бутылке", "price": 4900},
    {"name": "🌋 Сердце вулкана", "price": 5300},
    {"name": "❄️ Вечный лёд", "price": 5720},
    {"name": "🌈 Мост радуги", "price": 6160},
    {"name": "🌌 Галактический камень", "price": 6620},
    {"name": "🪐 Кольцо Сатурна", "price": 7100},
    {"name": "☀️ Фотонная сфера", "price": 7600},
    {"name": "🌙 Лунное кольцо", "price": 8120},
    {"name": "⭐ Звездный венец", "price": 8660},
    {"name": "💫 Падающая звезда", "price": 9220},
    {"name": "✨ Сияющий кристалл", "price": 9800},
    {"name": "🌟 Сверхновая", "price": 10400},
    {"name": "🌌 Туманность", "price": 11020},
    {"name": "🌠 Звездный поток", "price": 11660},
    {"name": "🪐 Астероидный камень", "price": 12320},
    {"name": "☄️ Хвост кометы", "price": 13000},
    {"name": "💠 Кристалл времени", "price": 13700},
    {"name": "🌀 Космический вихрь", "price": 14420},
    {"name": "⚡ Энергия хаоса", "price": 15160},
    {"name": "🔥 Пламя вечности", "price": 15920},
    {"name": "❄️ Ледяное сердце", "price": 16700},
    {"name": "🌿 Древний корень", "price": 17500},
    {"name": "🌊 Океанская душа", "price": 18320},
    {"name": "🏔️ Горный дух", "price": 19160},
    {"name": "🌋 Лавовое сердце", "price": 20020},
    {"name": "🌪️ Ветряной глаз", "price": 20900},
    {"name": "🌈 Радужный кристалл", "price": 21800},
    {"name": "🌙 Лунный камень", "price": 22720},
    {"name": "☀️ Солнечный кристалл", "price": 23660},
    {"name": "⭐ Звездный осколок", "price": 24620},
    {"name": "✨ Эфирный кристалл", "price": 25600},
    {"name": "🌀 Космический камень", "price": 26600},
    {"name": "💎 Алмазный кристалл", "price": 27620},
    {"name": "👑 Императорская корона", "price": 28660},
    {"name": "🦄 Кристалл единорога", "price": 29720},
    {"name": "🐉 Сердце дракона", "price": 30800},
    {"name": "🌺 Цветок жизни", "price": 31900},
    {"name": "⭐ Звезда судьбы", "price": 33020},
    {"name": "🌌 Космическое сердце", "price": 34160},
    {"name": "🌀 Изначальный шар", "price": 35320},
    {"name": "🔮 Хрустальный глаз", "price": 36500},
    {"name": "⚔️ Клинок света", "price": 37700},
    {"name": "🛡️ Щит вечности", "price": 38920},
    {"name": "👁️ Око судьбы", "price": 40160},
    {"name": "🧙‍♂️ Посох мудрости", "price": 41420},
    {"name": "🐲 Дракон судьбы", "price": 42700},
    {"name": "🌠 Метеоритный кристалл", "price": 44000},
    {"name": "💠 Небесный камень", "price": 45320},
    {"name": "🌟 Сияющий кристалл", "price": 46660},
    {"name": "🎭 Маска хаоса", "price": 48020},
    {"name": "🏆 Трофей судьбы", "price": 49400},
    {"name": "🎪 Огонь карнавала", "price": 50800},
    {"name": "🌋 Камень вулкана", "price": 52220},
    {"name": "❄️ Ледяной кристалл", "price": 53660},
    {"name": "🌈 Амулет радуги", "price": 55120},
    {"name": "☄️ Кометный кристалл", "price": 56600},
    {"name": "🌊 Жемчуг океана", "price": 58100},
    {"name": "🌲 Древо судьбы", "price": 59620},
    {"name": "🏔️ Сердце гор", "price": 61160},
    {"name": "🌅 Рассветный кристалл", "price": 62720},
    {"name": "🌙 Лунная корона", "price": 64300},
    {"name": "☀️ Солнечная корона", "price": 65900},
    {"name": "✨ Звездная корона", "price": 67520},
    {"name": "🌀 Космическая корона", "price": 69160},
    {"name": "💎 Алмазная корона", "price": 70820},
    {"name": "👑 Корона судьбы", "price": 72500}
]

STAR_DROP_WEIGHTS = [45.0, 28.0, 15.0, 8.0, 3.5, 0.5]
NFT_DROP_WEIGHTS = [40.0, 28.0, 17.0, 10.0, 4.0, 1.0]

# ========== CRASH ==========
CRASH_MIN_BET = 25
CRASH_MAX_BET = 5000
CRASH_BETTING_TIME = 6
CRASH_COOLDOWN = 3
CRASH_HOUSE_EDGE = 0.04
CRASH_SPEED = 0.08

# ========== MINES ==========
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

# ========== АПГРЕЙД ==========
def calculate_upgrade_chance(current_price, target_price, chance_bonus=0):
    """Реалистичный шанс апгрейда, макс. 70%, мин. 0.1% + бонус от админа"""
    diff = target_price - current_price
    ratio = current_price / target_price
    
    # Базовый шанс от соотношения цен
    if ratio >= 0.95:
        base = 70
    elif ratio >= 0.85:
        base = 60
    elif ratio >= 0.70:
        base = 50
    elif ratio >= 0.55:
        base = 40
    elif ratio >= 0.40:
        base = 30
    elif ratio >= 0.25:
        base = 20
    else:
        base = 10
    
    # Корректировка по разнице
    if diff <= 50:
        base += 5
    elif diff <= 100:
        base += 3
    elif diff <= 300:
        base += 1
    elif diff >= 5000:
        base -= 8
    elif diff >= 10000:
        base -= 15
    elif diff >= 20000:
        base -= 25
    
    # Добавляем бонус от админа
    base += chance_bonus
    
    # МИНИМАЛЬНЫЙ ШАНС 0.1%
    return min(max(round(base, 1), 0.1), 70)

# Доступные цены для улучшения
UPGRADE_PRICES = [50, 100, 200, 500, 1000, 2000, 5000, 7500, 10000, 15000]

# ========== ФУНКЦИЯ ДЛЯ ПОЛУЧЕНИЯ ЦЕНЫ ИЗ ПУЛА АПГРЕЙДА ==========
def get_upgrade_item_price(item_name):
    for item in UPGRADE_ITEMS_POOL:
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

# ========== ПОЛЬЗОВАТЕЛИ (СТАРТОВЫЙ БАЛАНС 0) ==========
async def get_or_create_user(tg_id: int, username: str = "Игрок"):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT balance, total_spent, inventory FROM users WHERE tg_id=?", (tg_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                await db.execute("UPDATE users SET username=? WHERE tg_id=?", (username, tg_id))
                await db.commit()
                return {"balance": row[0], "total_spent": row[1], "inventory": json.loads(row[2])}
            else:
                # СТАРТОВЫЙ БАЛАНС 0 ДЛЯ ВСЕХ (КРОМЕ АДМИНА)
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

# ========== ADMIN ==========
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

# ===== АДМИН-ФУНКЦИЯ: ЗАБРАТЬ ЗВЁЗДЫ =====
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

# ===== АДМИН-ФУНКЦИЯ: ДОБАВИТЬ ЗВЁЗДЫ (АЛЬТЕРНАТИВА) =====
@app.post("/api/admin/add_stars")
async def admin_add_stars(req: AdminGiveStarsRequest, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    if not ADMIN_TG_ID or tg_id != ADMIN_TG_ID:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    if req.target_tg_id <= 0:
        raise HTTPException(status_code=400, detail="Неверный ID")
    if req.amount < 1:
        raise HTTPException(status_code=400, detail="Сумма > 0")
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (tg_id, username, balance, total_spent, inventory) VALUES (?,?,0,0,'[]')",
            (req.target_tg_id, f"Player_{req.target_tg_id}")
        )
        await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (req.amount, req.target_tg_id))
        await db.commit()
    
    return {"success": True, "message": f"✅ Добавлено {req.amount} ⭐️ игроку {req.target_tg_id}"}

# ===== АДМИН-ФУНКЦИЯ: ПОЛУЧИТЬ ИНФО ОБ ИГРОКЕ =====
@app.get("/api/admin/user/{target_tg_id}")
async def admin_get_user(target_tg_id: int, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    if not ADMIN_TG_ID or tg_id != ADMIN_TG_ID:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    async with aiosqlite.connect(DB_NAME) as db:
        row = await (await db.execute(
            "SELECT tg_id, username, balance, total_spent, inventory FROM users WHERE tg_id=?",
            (target_tg_id,)
        )).fetchone()
        if not row:
            raise HTTPException(status_code=400, detail="Игрок не найден")
        
        chance_row = await (await db.execute("SELECT chance_bonus FROM user_chances WHERE tg_id=?", (target_tg_id,))).fetchone()
        chance_bonus = chance_row[0] if chance_row else 0
        
        return {
            "tg_id": row[0],
            "username": row[1],
            "balance": row[2],
            "total_spent": row[3],
            "inventory": json.loads(row[4]),
            "chance_bonus": chance_bonus
        }

# ===== АДМИН-ФУНКЦИЯ: УСТАНОВИТЬ ШАНС =====
@app.post("/api/admin/set_chance")
async def admin_set_chance(req: AdminChanceRequest, user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    if not ADMIN_TG_ID or tg_id != ADMIN_TG_ID:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    if req.target_tg_id <= 0:
        raise HTTPException(status_code=400, detail="Неверный ID")
    if req.chance_percent < 0 or req.chance_percent > 100:
        raise HTTPException(status_code=400, detail="Процент от 0 до 100")
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO user_chances (tg_id, chance_bonus) VALUES (?, ?)",
            (req.target_tg_id, req.chance_percent)
        )
        await db.commit()
    
    return {"success": True, "message": f"✅ Игроку {req.target_tg_id} установлен шанс +{req.chance_percent}%"}

# ===== АДМИН-ФУНКЦИЯ: ТОП ИГРОКОВ =====
@app.get("/api/admin/top")
async def admin_get_top(user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    if not ADMIN_TG_ID or tg_id != ADMIN_TG_ID:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    async with aiosqlite.connect(DB_NAME) as db:
        rows = await (await db.execute(
            "SELECT tg_id, username, balance, total_spent FROM users ORDER BY balance DESC LIMIT 50"
        )).fetchall()
        
        result = []
        for i, row in enumerate(rows):
            result.append({
                "place": i + 1,
                "tg_id": row[0],
                "username": row[1],
                "balance": row[2],
                "total_spent": row[3]
            })
        
        return {"players": result}

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
            pool = STAR_CASE_DROPS[ct] if ct in STAR_CASE_PRICES else NFT_CASE_DROPS[ct]
            weights = STAR_DROP_WEIGHTS if ct in STAR_CASE_PRICES else NFT_DROP_WEIGHTS
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

# ========== FREE CASE (ОБНОВЛЁННЫЙ) ==========
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

# ========== CASES ==========
@app.post("/api/case/open")
async def open_case(req: OpenCaseRequest, user: dict = Depends(verify_telegram_data)):
    if req.case_type in STAR_CASE_PRICES:
        price = STAR_CASE_PRICES[req.case_type]
        pool = STAR_CASE_DROPS[req.case_type]
        weights = STAR_DROP_WEIGHTS
    elif req.case_type in NFT_CASE_PRICES:
        price = NFT_CASE_PRICES[req.case_type]
        pool = NFT_CASE_DROPS[req.case_type]
        weights = NFT_DROP_WEIGHTS
    else:
        raise HTTPException(status_code=400, detail="Неизвестный кейс")
    
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
        if item["case"] in STAR_CASE_DROPS and item["id"] in STAR_CASE_DROPS[item["case"]]:
            gain = STAR_CASE_DROPS[item["case"]][item["id"]][1]
        elif item["case"] in NFT_CASE_DROPS and item["id"] in NFT_CASE_DROPS[item["case"]]:
            gain = NFT_CASE_DROPS[item["case"]][item["id"]][1]
    
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
            if item["case"] in STAR_CASE_DROPS and item["id"] in STAR_CASE_DROPS[item["case"]]:
                gain = STAR_CASE_DROPS[item["case"]][item["id"]][1]
            elif item["case"] in NFT_CASE_DROPS and item["id"] in NFT_CASE_DROPS[item["case"]]:
                gain = NFT_CASE_DROPS[item["case"]][item["id"]][1]
        total_gain += gain
    new_balance = user_info["balance"] + total_gain
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance=?, inventory='[]' WHERE tg_id=?", (new_balance, tg_id))
        await db.commit()
    return {"gain": total_gain, "balance": new_balance}

# ========== АПГРЕЙД (БЕСПЛАТНЫЙ, МИН. ШАНС 0.1%) ==========
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
        if item["case"] in STAR_CASE_DROPS and item["id"] in STAR_CASE_DROPS[item["case"]]:
            current_price = STAR_CASE_DROPS[item["case"]][item["id"]][1]
        elif item["case"] in NFT_CASE_DROPS and item["id"] in NFT_CASE_DROPS[item["case"]]:
            current_price = NFT_CASE_DROPS[item["case"]][item["id"]][1]
    
    if current_price == 0:
        raise HTTPException(status_code=400, detail="Нельзя улучшить")
    
    if req.target_price <= current_price:
        raise HTTPException(status_code=400, detail="Цель должна быть дороже текущей")
    
    async with aiosqlite.connect(DB_NAME) as db:
        row = await (await db.execute("SELECT chance_bonus FROM user_chances WHERE tg_id=?", (tg_id,))).fetchone()
        chance_bonus = row[0] if row else 0
    
    upgrade_chance = calculate_upgrade_chance(current_price, req.target_price, chance_bonus) / 100
    
    # ⚡ АПГРЕЙД БЕСПЛАТНЫЙ
    
    is_success = req.success if hasattr(req, 'success') else random.random() < upgrade_chance
    
    if is_success:
        new_item = None
        
        for up_item in UPGRADE_ITEMS_POOL:
            if up_item["price"] == req.target_price:
                new_item = {"id": f"up_{up_item['name']}", "name": up_item["name"], "case": "upgrade"}
                break
        
        if not new_item:
            all_drops = {**STAR_CASE_DROPS, **NFT_CASE_DROPS}
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
            for up_item in UPGRADE_ITEMS_POOL:
                all_prices.append(up_item["price"])
            for drops in all_drops.values():
                for _, (_, price) in drops.items():
                    all_prices.append(price)
            closest_price = min(all_prices, key=lambda x: abs(x - req.target_price))
            
            for up_item in UPGRADE_ITEMS_POOL:
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
            "message": f"💥 Сгорел!",
            "new_balance": user_info["balance"],
            "upgrade_cost": 0,
            "chance": int(upgrade_chance * 100)
        }

# ========== ФУНКЦИЯ ДЛЯ ПОЛУЧЕНИЯ ЦЕН ПРЕДМЕТОВ ==========
@app.get("/api/upgrade/prices")
async def get_upgrade_prices(user: dict = Depends(verify_telegram_data)):
    prices = []
    for item in UPGRADE_ITEMS_POOL:
        prices.append(item["price"])
    all_drops = {**STAR_CASE_DROPS, **NFT_CASE_DROPS}
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
