from fastapi import FastAPI, HTTPException
import os
import random

app = FastAPI()

# ======================
# SECURITY (ADMIN)
# ======================
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")

def check_admin(token: str):
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="forbidden")

# ======================
# DATA STORAGE (TEMP)
# ======================
users = {}

def get_user(user_id: str):
    if user_id not in users:
        users[user_id] = {
            "balance": 1000,
            "inventory": []
        }
    return users[user_id]

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

    return {
        "user_id": user_id,
        "balance": user["balance"]
    }

@app.post("/admin/set/{user_id}/{amount}")
def admin_set(user_id: str, amount: int, token: str):
    check_admin(token)

    user = get_user(user_id)
    user["balance"] = amount

    return {
        "user_id": user_id,
        "balance": user["balance"]
    }

@app.get("/admin/logs")
def admin_logs(token: str):
    check_admin(token)
    return {"status": "logs not implemented yet"}
