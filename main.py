import os
import hmac
import hashlib
import json
import urllib.parse
import random
from fastapi import FastAPI, Header, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import aiosqlite
import httpx

app = FastAPI()

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

# Цены покупки кейсов
CASE_PRICES = {
    "star_micro": 50, "star_common": 150, "star_rare": 500, "star_epic": 2000,
    "ton_frogs": 80, "digital_resistance": 200, "pudgy_penguins": 600, "bored_apes": 2500
}

# 48 предметов (6 на каждый кейс)
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
    }
}

DROP_WEIGHTS = [45.0, 28.0, 15.0, 8.0, 3.5, 0.5]

class OpenCaseRequest(BaseModel):
    case_type: str

class UpdateWithdrawStatusRequest(BaseModel):
    ticket_id: int
    status: str

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
        await db.commit()

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

@app.get("/api/profile")
async def get_profile(user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    username = user.get('first_name', 'Игрок')
    user_info = await get_or_create_user(tg_id, username)
    user_info["is_admin"] = (ADMIN_TG_ID and tg_id == ADMIN_TG_ID)
    return user_info

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

@app.get("/api/leaderboard")
async def get_leaderboard(user: dict = Depends(verify_telegram_data)):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT username, balance FROM users ORDER BY balance DESC LIMIT 10") as cursor:
            rows = await cursor.fetchall()
            return [{"username": r[0], "balance": r[1]} for r in rows]

# РЕАЛЬНОЕ СОЗДАНИЕ ИНВОЙСА ЧЕРЕЗ TELEGRAM BOT API
@app.post("/api/stars/buy")
async def buy_stars(stars_amount: int, user: dict = Depends(verify_telegram_data)):
    if stars_amount < 50:
        raise HTTPException(status_code=400, detail="Минимальное пополнение — 50 Stars")
    
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/createInvoiceLink"
    payload = {
        "title": "Пополнение баланса",
        "description": f"Покупка {stars_amount} монет Stars для открытия кейсов",
        "payload": f"deposit_{user.get('id')}_{stars_amount}",
        "provider_token": "", 
        "currency": "XTR",
        "prices": [{"label": "Stars", "amount": stars_amount}]
    }
    
    async with httpx.AsyncClient() as client:
        res = await client.post(url, json=payload)
        res_data = res.json()
        if res_data.get("ok"):
            return {"invoice_url": res_data["result"]}
        else:
            raise HTTPException(status_code=500, detail="Ошибка генерации Telegram Invoice")

@app.post("/api/withdraw")
async def create_withdraw(amount: int, wallet: str, user: dict = Depends(verify_telegram_data)):
    if amount < 100:
        raise HTTPException(status_code=400, detail="Минимальный вывод — 100 Stars")
    tg_id = user.get('id')
    user_info = await get_or_create_user(tg_id)
    if user_info["balance"] < amount:
        raise HTTPException(status_code=400, detail="Недостаточно монет")
        
    new_balance = user_info["balance"] - amount
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = ? WHERE tg_id = ?", (new_balance, tg_id))
        await db.execute("INSERT INTO withdraws (tg_id, amount, requisites, status) VALUES (?, ?, ?, 'pending')", (tg_id, amount, wallet))
        await db.commit()
    return {"status": "pending", "payout": amount, "new_balance": new_balance}

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
