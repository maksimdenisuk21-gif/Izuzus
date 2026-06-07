from fastapi import FastAPI, HTTPException
import sqlite3
import json
import os
import random
import secrets
import time

app = FastAPI()

# ======================
# ADMIN
# ======================
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")

def check_admin(token: str):
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="forbidden")

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
    inventory TEXT
)
""")
conn.commit()

# ======================
# COOLDOWN (ANTI SPAM)
# ======================
cooldowns = {}
COOLDOWN_SECONDS = 5

def check_cooldown(user_id: str):
    now = time.time()

    if user_id in cooldowns:
        last = cooldowns[user_id]
        if now - last < COOLDOWN_SECONDS:
            return False

    cooldowns[user_id] = now
    return True

# ======================
# HELPERS
# ======================
def create_user(user_id, password):
    cursor.execute("""
    INSERT INTO users VALUES (?, ?, ?, ?, ?)
    """, (user_id, password, "", 1000, json.dumps([])))
    conn.commit()

def get_user_by_token(token: str):
    cursor.execute("SELECT * FROM users WHERE token=?", (token,))
    return cursor.fetchone()

def save_user(user_id, balance, inventory):
    cursor.execute("""
    UPDATE users SET balance=?, inventory=? WHERE user_id=?
    """, (balance, json.dumps(inventory), user_id))
    conn.commit()

# ======================
# AUTH
# ======================
@app.post("/register")
def register(user_id: str, password: str):
    cursor.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    if cursor.fetchone():
        return {"error": "user exists"}

    create_user(user_id, password)
    return {"status": "registered"}

@app.post("/login")
def login(user_id: str, password: str):
    cursor.execute("SELECT password FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()

    if not row or row[0] != password:
        return {"error": "invalid login"}

    token = secrets.token_hex(16)

    cursor.execute("""
    UPDATE users SET token=? WHERE user_id=?
    """, (token, user_id))
    conn.commit()

    return {"token": token}

# ======================
# USER
# ======================
@app.get("/balance")
def balance(token: str):
    user = get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=403, detail="invalid token")

    return {"balance": user[3]}

@app.post("/case/open")
def open_case(token: str):
    user = get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=403, detail="invalid token")

    user_id = user[0]
    balance = user[3]
    inventory = json.loads(user[4])

    # анти-спам
    if not check_cooldown(user_id):
        return {"error": "too fast, wait 5 sec"}

    if balance < 100:
        return {"error": "not enough money"}

    balance -= 100

    items = ["trash", "common", "skin", "rare", "epic", "legendary", "knife"]
    reward = random.choice(items)

    values = {
        "trash": 10,
        "common": 20,
        "skin": 50,
        "rare": 150,
        "epic": 300,
        "legendary": 800,
        "knife": 500
    }

    inventory.append(reward)
    balance += values[reward]

    save_user(user_id, balance, inventory)

    return {
        "reward": reward,
        "balance": balance
    }

# ======================
# ADMIN
# ======================
@app.post("/admin/give")
def admin_give(user_id: str, amount: int, token: str):
    check_admin(token)

    cursor.execute("SELECT balance, inventory FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()

    if not row:
        return {"error": "no user"}

    balance = row[0] + amount
    inventory = json.loads(row[1])

    save_user(user_id, balance, inventory)

    return {"user_id": user_id, "balance": balance}
