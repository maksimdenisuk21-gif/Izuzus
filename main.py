from fastapi import FastAPI, HTTPException
import sqlite3
import json
import random

app = FastAPI()

conn = sqlite3.connect("game.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    balance INTEGER,
    inventory TEXT
)
""")
conn.commit()


def get_user(user_id):
    cursor.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    return cursor.fetchone()


def save(user_id, balance, inv):
    cursor.execute("""
    UPDATE users SET balance=?, inventory=? WHERE user_id=?
    """, (balance, json.dumps(inv), user_id))
    conn.commit()


@app.post("/auth")
def auth(user_id: str):
    if not get_user(user_id):
        cursor.execute(
            "INSERT INTO users VALUES (?, ?, ?)",
            (user_id, 1000, "[]")
        )
        conn.commit()
    return {"ok": True}


@app.get("/balance")
def balance(user_id: str):
    user = get_user(user_id)
    if not user:
        raise HTTPException(403)
    return {"balance": user[1]}


@app.post("/case")
def case(user_id: str):
    user = get_user(user_id)
    if not user:
        raise HTTPException(403)

    balance = user[1]
    inv = json.loads(user[2])

    if balance < 10:
        return {"error": "no money"}

    balance -= 10

    items = ["common", "rare", "epic", "legendary"]
    reward = random.choice(items)

    values = {
        "common": 15,
        "rare": 40,
        "epic": 100,
        "legendary": 250
    }

    balance += values[reward]
    inv.append(reward)

    save(user_id, balance, inv)

    return {"reward": reward, "balance": balance}
