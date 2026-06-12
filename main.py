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

app = FastAPI()

# Настройка CORS для работы с Netlify
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ЧИТАЕМ НАСТРОЙКИ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ РЕНДЕРА
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_TG_ID_RAW = os.getenv("ADMIN_TG_ID")

# Превращаем ID админа в число, если он задан на Рендере
ADMIN_TG_ID = int(ADMIN_TG_ID_RAW) if ADMIN_TG_ID_RAW else 0

DB_NAME = "database.db"

ITEM_VALUES = {
    "trash": 10, "common": 20, "skin": 50, "rare": 150, 
    "epic": 300, "legendary": 800, "knife": 1500
}

class UpdateWithdrawStatusRequest(BaseModel):
    ticket_id: int
    status: str

@app.on_event("startup")
async def startup():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
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

# Проверка валидности данных от Telegram
def verify_telegram_data(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization Header")
    
    # Если BOT_TOKEN не задан в Render, ругаемся в консоль сервера
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="Server configuration error: BOT_TOKEN missing")

    try:
        init_data = urllib.parse.parse_qs(authorization)
        hash_value = init_data.get('hash', [None])[0]
        if not hash_value:
            raise HTTPException(status_code=401, detail="Invalid Telegram InitData")

        sorted_data = sorted([f"{k}={v[0]}" for k, v in init_data.items() if k != 'hash'])
        data_check_string = "\n".join(sorted_data)

        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if calculated_hash != hash_value:
            raise HTTPException(status_code=401, detail="Data integrity compromised")

        user_data = json.loads(init_data.get('user', ['{}'])[0])
        return user_data
    except Exception:
        raise HTTPException(status_code=401, detail="Error parsing initData")

async def get_or_create_user(tg_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT balance, inventory FROM users WHERE tg_id = ?", (tg_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return {"balance": row[0], "inventory": json.loads(row[1])}
            else:
                # Если зашел админ — баланс 10к, если обычный игрок — 20
                start_balance = 10000 if (ADMIN_TG_ID and tg_id == ADMIN_TG_ID) else 20
                await db.execute(
                    "INSERT INTO users (tg_id, balance, inventory) VALUES (?, ?, ?)",
                    (tg_id, start_balance, '[]')
                )
                await db.commit()
                return {"balance": start_balance, "inventory": []}

@app.get("/api/profile")
async def get_profile(user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    user_info = await get_or_create_user(tg_id)
    user_info["is_admin"] = (ADMIN_TG_ID and tg_id == ADMIN_TG_ID)
    return user_info

@app.post("/api/case/open")
async def open_case(user: dict = Depends(verify_telegram_data)):
    tg_id = user.get('id')
    user_info = await get_or_create_user(tg_id)
    
    if user_info["balance"] < 100:
        raise HTTPException(status_code=400, detail="Недостаточно монет для открытия кейса")
    
    rewards = list(ITEM_VALUES.keys())
    weights = [40, 25, 15, 12, 5, 2.5, 0.5]
    reward = random.choices(rewards, weights=weights, k=1)[0]
    
    new_balance = user_info["balance"] - 100
    user_info["inventory"].append(reward)
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET balance = ?, inventory = ? WHERE tg_id = ?",
            (new_balance, json.dumps(user_info["inventory"]), tg_id)
        )
        await db.commit()
        
    return {"reward": reward, "balance": new_balance}

@app.post("/api/stars/buy")
async def buy_stars(stars_amount: int, user: dict = Depends(verify_telegram_data)):
    if stars_amount < 50:
        raise HTTPException(status_code=400, detail="Минимальная сумма пополнения — 50 Stars")
    
    # Имитация инвойса для Telegram Mini App
    dummy_invoice_url = f"https://t.me/invoice/example_stars_{stars_amount}"
    
    # Начисляем монеты на баланс (1 Star = 1 монета)
    tg_id = user.get('id')
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (stars_amount, tg_id))
        await db.commit()

    return {"invoice_url": dummy_invoice_url}

@app.post("/api/withdraw")
async def create_withdraw(amount: int, wallet: str, user: dict = Depends(verify_telegram_data)):
    if amount < 100 or amount > 5000:
        raise HTTPException(status_code=400, detail="Сумма вывода должна быть от 100 до 5000 монет")
    
    tg_id = user.get('id')
    user_info = await get_or_create_user(tg_id)
    
    if user_info["balance"] < amount:
        raise HTTPException(status_code=400, detail="Недостаточно монет")
    
    new_balance = user_info["balance"] - amount
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance = ? WHERE tg_id = ?", (new_balance, tg_id))
        await db.execute(
            "INSERT INTO withdraws (tg_id, amount, requisites, status) VALUES (?, ?, ?, 'pending')",
            (tg_id, amount, wallet)
        )
        await db.commit()
        
    return {"status": "pending", "payout": amount, "new_balance": new_balance}

# --- АДМИН-ЭНДПОИНТЫ ---

@app.get("/api/admin/withdraws")
async def get_admin_withdraws(user: dict = Depends(verify_telegram_data)):
    if not ADMIN_TG_ID or user.get('id') != ADMIN_TG_ID:
        raise HTTPException(status_code=403, detail="Доступ запрещен.")
        
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id, tg_id, amount, requisites, status, created_at FROM withdraws ORDER BY id DESC") as cursor:
            rows = await cursor.fetchall()
            withdraws_list = []
            for r in rows:
                withdraws_list.append({
                    "id": r[0], "tg_id": r[1], "amount": r[2], 
                    "requisites": r[3], "status": r[4], "date": r[5]
                })
            return withdraws_list

@app.post("/api/admin/withdraw/status")
async def update_withdraw_status(req: UpdateWithdrawStatusRequest, user: dict = Depends(verify_telegram_data)):
    if not ADMIN_TG_ID or user.get('id') != ADMIN_TG_ID:
        raise HTTPException(status_code=403, detail="Доступ запрещен.")
        
    async with aiosqlite.connect(DB_NAME) as db:
        if req.status == "rejected":
            async with db.execute("SELECT tg_id, amount FROM withdraws WHERE id = ?", (req.ticket_id,)) as cursor:
                ticket = await cursor.fetchone()
                if ticket:
                    client_id, amount = ticket[0], ticket[1]
                    await db.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (amount, client_id))
        
        await db.execute("UPDATE withdraws SET status = ? WHERE id = ?", (req.status, req.ticket_id))
        await db.commit()
        
    return {"success": True, "new_status": req.status}
