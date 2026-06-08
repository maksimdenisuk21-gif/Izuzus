from fastapi import FastAPI, Request, HTTPException
import sqlite3
import json
import os
import random
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)

# ======================
# APP
# ======================
app = FastAPI()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = os.getenv("ADMIN_IDS", "")

tg_app = Application.builder().token(BOT_TOKEN).build()

def is_admin(uid: int):
    return str(uid) in ADMIN_IDS.split(",")

# ======================
# DB
# ======================
conn = sqlite3.connect("game.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    password TEXT,
    token TEXT,
    balance INTEGER,
    inventory TEXT,
    withdraw_time INTEGER
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS withdraws (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    amount INTEGER,
    payout INTEGER,
    status TEXT,
    time INTEGER
)
""")

conn.commit()

# ======================
# HELPERS
# ======================
def get_user(user_id):
    cursor.execute("SELECT * FROM users WHERE user_id=?", (str(user_id),))
    return cursor.fetchone()

def create_user(user_id):
    if not get_user(user_id):
        cursor.execute("""
        INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)
        """, (str(user_id), "", "", 1000, json.dumps([]), 0))
        conn.commit()

# ======================
# CASE SYSTEM
# ======================
@app.post("/case/open")
def open_case(token: str):

    cursor.execute("SELECT * FROM users WHERE token=?", (token,))
    user = cursor.fetchone()

    if not user:
        raise HTTPException(status_code=403)

    user_id = user[0]
    balance = user[3]
    inv = json.loads(user[4])

    if balance < 100:
        return {"error": "no balance"}

    balance -= 100

    items = ["trash", "common", "skin", "rare", "epic", "legendary", "knife"]
    values = {
        "trash": 10,
        "common": 20,
        "skin": 50,
        "rare": 150,
        "epic": 300,
        "legendary": 800,
        "knife": 500
    }

    reward = random.choice(items)

    inv.append(reward)
    balance += values[reward]

    cursor.execute("""
        UPDATE users SET balance=?, inventory=? WHERE user_id=?
    """, (balance, json.dumps(inv), user_id))

    conn.commit()

    return {"reward": reward, "balance": balance}

# ======================
# WITHDRAW (USER)
# ======================
@app.post("/withdraw")
def withdraw(user_id: str, amount: int):

    cursor.execute("SELECT balance, withdraw_time FROM users WHERE user_id=?", (str(user_id),))
    row = cursor.fetchone()

    if not row:
        return {"error": "no user"}

    balance, last = row

    if amount < 100 or amount > 5000:
        return {"error": "100-5000"}

    if balance < amount:
        return {"error": "no balance"}

    if last and time.time() - last < 86400:
        return {"error": "cooldown"}

    payout = int(amount * 0.9)

    balance -= amount

    cursor.execute("""
        UPDATE users SET balance=?, withdraw_time=? WHERE user_id=?
    """, (balance, int(time.time()), str(user_id)))

    cursor.execute("""
        INSERT INTO withdraws (user_id, amount, payout, status, time)
        VALUES (?, ?, ?, ?, ?)
    """, (str(user_id), amount, payout, "pending", int(time.time())))

    conn.commit()

    return {"status": "pending", "payout": payout}

# ======================
# TELEGRAM BOT
# ======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    create_user(update.effective_user.id)
    await update.message.reply_text("CaseFight online")

# ======================
# ADMIN PANEL
# ======================
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("no access")
        return

    cursor.execute("SELECT * FROM withdraws WHERE status='pending'")
    rows = cursor.fetchall()

    if not rows:
        await update.message.reply_text("no withdraws")
        return

    for w in rows:
        wid, uid, amount, payout, status, t = w

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✔", callback_data=f"ap_{wid}"),
                InlineKeyboardButton("✖", callback_data=f"re_{wid}")
            ]
        ])

        await update.message.reply_text(
            f"ID {wid}\nUser {uid}\nAmount {amount}\nPayout {payout}",
            reply_markup=kb
        )

# ======================
# CALLBACKS
# ======================
async def cb(update: Update, context: ContextTypes.DEFAULT_TYPE):

    q = update.callback_query
    await q.answer()

    if not is_admin(q.from_user.id):
        await q.edit_message_text("no access")
        return

    action, wid = q.data.split("_")

    cursor.execute("SELECT user_id, amount FROM withdraws WHERE id=?", (wid,))
    row = cursor.fetchone()

    if not row:
        await q.edit_message_text("not found")
        return

    uid, amount = row

    if action == "ap":
        cursor.execute("UPDATE withdraws SET status='paid' WHERE id=?", (wid,))
        conn.commit()
        await q.edit_message_text(f"PAID #{wid}")

    else:
        cursor.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
        bal = cursor.fetchone()[0]

        cursor.execute("UPDATE users SET balance=? WHERE user_id=?", (bal + amount, uid))
        cursor.execute("UPDATE withdraws SET status='rejected' WHERE id=?", (wid,))
        conn.commit()

        await q.edit_message_text(f"REJECTED #{wid}")

# ======================
# REGISTER
# ======================
tg_app.add_handler(CommandHandler("start", start))
tg_app.add_handler(CommandHandler("admin", admin))
tg_app.add_handler(CallbackQueryHandler(cb))

# ======================
# WEBHOOK
# ======================
@app.post("/telegram")
async def telegram(req: Request):
    data = await req.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return {"ok": True}

# ======================
# STARTUP
# ======================
@app.on_event("startup")
async def startup():
    await tg_app.initialize()
    print("BOT STARTED")
