from fastapi import FastAPI, Request, HTTPException, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import APIKeyHeader
import json
import os
import random
import time
import hmac
import hashlib
from urllib.parse import parse_qs
from contextlib import asynccontextmanager
import aiosqlite

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

# ======================
# CONFIG & AUTH
# ======================
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN")
ADMIN_IDS = os.getenv("ADMIN_IDS", "").split(",")

API_KEY_HEADER = APIKeyHeader(name="Authorization", auto_error=True)

def verify_telegram_data(init_data: str) -> dict:
    try:
        parsed_data = parse_qs(init_data)
        received_hash = parsed_data.pop("hash")[0]
        data_check_string = "\n".join(f"{k}={v[0]}" for k, v in sorted(parsed_data.items()))
        
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if calculated_hash != received_hash:
            raise HTTPException(status_code=401, detail="Data integrity compromised")
            
        return json.loads(parsed_data["user"][0])
    except Exception:
        raise HTTPException(status_code=401, detail="Unauthorized")

# ======================
# DB MANAGEMENT
# ======================
DB_PATH = "game.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            balance INTEGER DEFAULT 1000,
            inventory TEXT DEFAULT '[]',
            last_bonus INTEGER DEFAULT 0
        )""")
        await db.commit()

# ======================
# LIFESPAN & APP INIT
# ======================
tg_app = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global tg_app
    await init_db()
    tg_app = Application.builder().token(BOT_TOKEN).build()
    await setup_bot_handlers(tg_app)
    await tg_app.initialize()
    await tg_app.start()
    print("🚀 API SERVER STARTED (FREE-TO-PLAY MODE)")
    yield
    await tg_app.stop()
    await tg_app.shutdown()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ======================
# API ENDPOINTS
# ======================

@app.get("/api/profile")
async def get_profile(auth_header: str = Security(API_KEY_HEADER)):
    tg_user = verify_telegram_data(auth_header)
    uid = str(tg_user["id"])
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance, inventory FROM users WHERE user_id=?", (uid,)) as cursor:
            row = await cursor.fetchone()
            
        if not row:
            await db.execute("INSERT INTO users (user_id, balance) VALUES (?, ?)", (uid, 1000))
            await db.commit()
            return {"user_id": uid, "balance": 1000, "inventory": []}
            
        return {"user_id": uid, "balance": row[0], "inventory": json.loads(row[1])}

@app.post("/api/case/open")
async def case_open(auth_header: str = Security(API_KEY_HEADER)):
    tg_user = verify_telegram_data(auth_header)
    uid = str(tg_user["id"])
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance, inventory FROM users WHERE user_id=?", (uid,)) as cursor:
            row = await cursor.fetchone()
            
        if not row or row[0] < 100:
            raise HTTPException(status_code=400, detail="Недостаточно монет для симуляции")
            
        balance, inv_raw = row[0], row[1]
        inv = json.loads(inv_raw)
        
        items = ["trash", "common", "skin", "rare", "epic", "legendary", "knife"]
        values = {"trash": 10, "common": 20, "skin": 50, "rare": 150, "epic": 300, "legendary": 800, "knife": 1500}
        
        reward = random.choice(items)
        new_balance = balance - 100 + values[reward]
        inv.append(reward)
        
        await db.execute("UPDATE users SET balance=?, inventory=? WHERE user_id=?", (new_balance, json.dumps(inv), uid))
        await db.commit()
        
        return {"reward": reward, "balance": new_balance}

@app.post("/api/bonus")
async def claim_bonus(auth_header: str = Security(API_KEY_HEADER)):
    tg_user = verify_telegram_data(auth_header)
    uid = str(tg_user["id"])
    current_time = int(time.time())
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT last_bonus FROM users WHERE user_id=?", (uid,)) as cursor:
            row = await cursor.fetchone()
            
        if row and (current_time - row[0] < 3600):
            raise HTTPException(status_code=400, detail="Бонус доступен раз в час!")
            
        bonus_amount = random.randint(50, 200)
        await db.execute("UPDATE users SET balance = balance + ?, last_bonus = ? WHERE user_id = ?", 
                         (bonus_amount, current_time, uid))
        await db.commit()
        
        async with db.execute("SELECT balance FROM users WHERE user_id=?", (uid,)) as cursor:
            new_balance = (await cursor.fetchone())[0]
            
        return {"bonus": bonus_amount, "balance": new_balance}

# ======================
# BOT HANDLERS & WEBHOOKS
# ======================
async def setup_bot_handlers(application: Application):
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("👋 Привет! Запускай игровой симулятор через меню бота, выполняй задания и собирай коллекцию NFT!")

    application.add_handler(CommandHandler("start", start))

@app.post("/telegram")
async def telegram_webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return {"ok": True}
