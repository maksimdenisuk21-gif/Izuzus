from fastapi import FastAPI, HTTPException
import os
import random
import sqlite3
import json

app = FastAPI()

# ======================
# ADMIN SECURITY
# ======================
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")

def check_admin(token: str):
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="forbidden")

# ======================
# DATABASE
# ======================
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

# ======================
# USER FUNCTIONS
# ======================
def get_user(user_id: str):
    cursor.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()

    if row is None:
        cursor.execute(
            "INSERT INTO users VALUES (?, ?, ?)",
            (user_id, 1000, json.dumps([]))
        )
        conn.commit()

        return {
            "balance": 1000,
            "inventory": []
        }

    return {
        "balance": row[1],
        "inventory": json.loads(row[2])
    }


def save_user(user_id: str, user: dict):
    cursor.execute(
        "UPDATE users SET balance=?, inventory=? WHERE user_id=?",
        (user["balance"], json.dumps(user["inventory"]), user_id)
    )
    conn.commit()

# ======================
# USER ENDPOINTS
# ======================
@app.get("/balance/{user_id}")
def balance(user_id: str):
    user = get_user(user_id)
    return {"balance": user["balance"]}


@app.post("/case/open/{user_id}")
def open_case(user_id: str):
    user = get_user(user_id)

    if user["balance"] < 100:
        return {"error": "not enough money"}

    user["balance"] -= 100

    items = ["trash", "common", "skin", "rare skin", "epic", "legendary", "knife"]
    reward = random.choice(items)

    values = {
        "trash": 10,
        "common": 20,
        "skin": 50,
        "rare skin": 150,
        "epic": 300,
        "legendary": 800,
        "knife": 500
    }

    user["inventory"].append(reward)
    user["balance"] += values[reward]

    save_user(user_id, user)

    return {
        "reward": reward,
        "balance": user["balance"]
    }

# ======================
# ADMIN ENDPOINTS
# ======================
@app.post("/admin/give/{user_id}/{amount}")
def admin_give(user_id: str, amount: int, token: str):
    check_admin(token)

    user = get_user(user_id)
    user["balance"] += amount

    save_user(user_id, user)

    return {
        "user_id": user_id,
        "balance": user["balance"]
    }


@app.post("/admin/set/{user_id}/{amount}")
def admin_set(user_id: str, amount: int, token: str):
    check_admin(token)

    user = get_user(user_id)
    user["balance"] = amount

    save_user(user_id, user)

    return {
        "user_id": user_id,
        "balance": user["balance"]
    }


@app.get("/admin/user/{user_id}")
def admin_user(user_id: str, token: str):
    check_admin(token)

    return get_user(user_id)
