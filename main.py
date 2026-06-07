from fastapi import FastAPI
import random

app = FastAPI()

users = {}

def get_user(user_id: str):
    if user_id not in users:
        users[user_id] = {
            "balance": 1000,
            "inventory": []
        }
    return users[user_id]

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

    items = ["knife", "gun", "skin", "rare skin", "trash", "legendary", "epic", "common"]
    reward = random.choice(items)

    user["inventory"].append(reward)

    reward_value = {
        "trash": 10,
        "common": 20,
        "skin": 50,
        "rare skin": 150,
        "epic": 300,
        "legendary": 800,
        "knife": 500,
        "gun": 200
    }[reward]

    user["balance"] += reward_value

    return {
        "reward": reward,
        "balance": user["balance"]
    }
