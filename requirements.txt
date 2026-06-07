from fastapi import FastAPI

app = FastAPI()

balance = {}

@app.get("/balance/{user_id}")
def get_balance(user_id: str):
    if user_id not in balance:
        balance[user_id] = 1000
    return {"balance": balance[user_id]}
