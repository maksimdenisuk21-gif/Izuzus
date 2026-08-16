import os
import random
import asyncio
import sqlite3
import time
import json
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Header, Request, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import socketio
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# ==========================================
# 1. ГЛОБАЛЬНЫЕ НАСТРОЙКИ И ЭКОНОМИКА (PUBG)
# ==========================================

START_BALANCE: int = 5
CRASH_HOUSE_EDGE: float = 0.08
MINES_HOUSE_EDGE: float = 0.10
UPGRADE_HOUSE_EDGE: float = 0.10
COINFLIP_HOUSE_EDGE: float = 0.05

MINES_MULTIPLIERS: Dict[int, List[float]] = {
    1: [1.03, 1.12, 1.23, 1.35, 1.50, 1.68, 1.90, 2.18, 2.54, 3.00],
    3: [1.15, 1.40, 1.75, 2.25, 2.95, 4.00, 5.60, 8.00, 10.00],
    5: [1.30, 1.80, 2.60, 3.90, 6.00, 9.80, 16.00, 20.00],
    7: [1.50, 2.50, 4.50, 8.50, 17.00, 40.00],
    10: [2.00, 5.00, 15.00, 45.00, 100.00]
}

CASE_PRICES: Dict[str, Dict[str, Any]] = {
    "star_case_1": {
        "id": "star_case_1",
        "name": "Бронзовый Ящик",
        "price": 30,
        "items": [
            {"id": "item_1", "name": "Кепка PUBG Camo", "price": 10, "rarity": "common"},
            {"id": "item_2", "name": "Футболка Survivor Red", "price": 25, "rarity": "common"},
            {"id": "item_3", "name": "Очки Combat Tactical", "price": 35, "rarity": "rare"},
            {"id": "item_4", "name": "M416 Desert Camo", "price": 80, "rarity": "epic"}
        ]
    },
    "star_case_2": {
        "id": "star_case_2",
        "name": "Серебряный Ящик",
        "price": 100,
        "items": [
            {"id": "item_5", "name": "Шлем Level 2 Military", "price": 50, "rarity": "common"},
            {"id": "item_6", "name": "Куртка Biker Black", "price": 90, "rarity": "rare"},
            {"id": "item_7", "name": "AKM Gold Skin", "price": 190, "rarity": "epic"},
            {"id": "item_8", "name": "AWM Dragon Lore", "price": 400, "rarity": "legendary"}
        ]
    },
    "star_case_3": {
        "id": "star_case_3",
        "name": "Золотой Ящик",
        "price": 250,
        "items": [
            {"id": "item_9", "name": "Бронежилет L3 Heavy", "price": 150, "rarity": "rare"},
            {"id": "item_10", "name": "M416 Glacier Level 1", "price": 450, "rarity": "epic"},
            {"id": "item_11", "name": "Рюкзак L3 Gold Plated", "price": 600, "rarity": "epic"},
            {"id": "item_12", "name": "Костюм Мумии White", "price": 1300, "rarity": "legendary"}
        ]
    },
    "star_case_4": {
        "id": "star_case_4",
        "name": "Легендарный X-Suit Ящик",
        "price": 500,
        "items": [
            {"id": "item_13", "name": "Kar98k Kukulkan", "price": 300, "rarity": "rare"},
            {"id": "item_14", "name": "M24 Pharaoh Gold", "price": 750, "rarity": "epic"},
            {"id": "item_15", "name": "X-Suit Poseidon Full", "price": 2600, "rarity": "mythic"},
            {"id": "item_16", "name": "X-Suit Blood Raven Ultimate", "price": 5500, "rarity": "mythic"}
        ]
    }
}

# ==========================================
# 2. ИНИЦИАЛИЗАЦИЯ И МИДДЛВЕЙРЫ FASTAPI
# ==========================================

limiter = Limiter(key_func=get_remote_address)
app = FastAPI(
    title="PUBG Cases WebApp Engine",
    version="2.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc"
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
socket_app = socketio.ASGIApp(sio, app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_NAME = "database.db"

# ==========================================
# 3. Pydantic МОДЕЛИ ЗАПРОСОВ (DTO)
# ==========================================

class DepositRequest(BaseModel):
    amount: int = Field(..., ge=10, description="Минимальное пополнение 10 UC")

class WithdrawRequest(BaseModel):
    amount: int = Field(..., ge=100, description="Минимальный вывод 100 UC")
    wallet: str = Field(..., min_length=5, description="Реквизиты для вывода")

class CaseOpenRequest(BaseModel):
    case_type: str = Field(..., description="ID кейса для открытия")

class UpgradeRequest(BaseModel):
    item_index: int = Field(..., ge=0, description="Индекс предмета в инвентаре")
    target_multiplier: float = Field(..., ge=1.1, le=100.0, description="Множитель для апгрейда")

class CoinFlipRequest(BaseModel):
    bet_amount: int = Field(..., ge=5, description="Минимальная ставка 5 UC")
    choice: str = Field(..., regex="^(heads|tails)$", description="Выбор: heads или tails")

class MinesStartRequest(BaseModel):
    bet_amount: int = Field(..., ge=5, description="Минимальная ставка 5 UC")
    mines_count: int = Field(..., ge=1, le=10, description="Количество мин от 1 до 10")

class MinesOpenRequest(BaseModel):
    game_id: str = Field(..., description="Идентификатор активной игры")
    cell_index: int = Field(..., ge=0, le=24, description="Индекс ячейки от 0 до 24")

class MinesCashoutRequest(BaseModel):
    game_id: str = Field(..., description="Идентификатор активной игры")

class CrashBetRequest(BaseModel):
    bet_amount: int = Field(..., ge=5, description="Минимальная ставка 5 UC")

# ==========================================
# 4. РАБОТА С БАЗОЙ ДАННЫХ SQLITE
# ==========================================

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        tg_id INTEGER PRIMARY KEY,
        username TEXT NOT NULL,
        balance INTEGER DEFAULT 5,
        total_spent INTEGER DEFAULT 0,
        inventory TEXT DEFAULT '[]',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        type TEXT NOT NULL,
        amount INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(tg_id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS deposits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        status TEXT DEFAULT 'success',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(tg_id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS withdraws (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        wallet TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(tg_id)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS daily_quests (
        user_id INTEGER NOT NULL,
        quest_type TEXT NOT NULL,
        progress INTEGER DEFAULT 0,
        target INTEGER NOT NULL,
        completed BOOLEAN DEFAULT FALSE,
        PRIMARY KEY (user_id, quest_type),
        FOREIGN KEY(user_id) REFERENCES users(tg_id)
    )''')
    
    conn.commit()
    conn.close()

init_db()

def db_get_user(tg_id: int) -> Optional[Dict[str, Any]]:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

def db_create_user_if_not_exists(tg_id: int, username: str) -> Dict[str, Any]:
    user = db_get_user(tg_id)
    if not user:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "INSERT INTO users (tg_id, username, balance, total_spent, inventory) VALUES (?, ?, ?, ?, ?)",
            (tg_id, username, START_BALANCE, 0, json.dumps([]))
        )
        c.execute("INSERT INTO daily_quests (user_id, quest_type, target) VALUES (?, 'open_cases', 3)", (tg_id,))
        c.execute("INSERT INTO daily_quests (user_id, quest_type, target) VALUES (?, 'play_mines', 5)", (tg_id,))
        conn.commit()
        conn.close()
        return db_get_user(tg_id)
    return user

def db_update_balance(tg_id: int, amount: int, tx_type: str):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (amount, tg_id))
    c.execute("INSERT INTO transactions (user_id, type, amount) VALUES (?, ?, ?)", (tg_id, tx_type, amount))
    conn.commit()
    conn.close()

def db_update_inventory(tg_id: int, inventory_list: List[Dict[str, Any]]):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE users SET inventory = ? WHERE tg_id = ?", (json.dumps(inventory_list), tg_id))
    conn.commit()
    conn.close()
# ==========================================
# 5. СЕССИИ И СОСТОЯНИЯ ИГР (IN-MEMORY)
# ==========================================

# Хранилище активных игр в Mines: {game_id: {...}}
active_mines_games: Dict[str, Dict[str, Any]] = {}

# Глобальное состояние игры Crash
crash_state: Dict[str, Any] = {
    "multiplier": 1.0,
    "status": "waiting",  # waiting -> running -> crashed
    "crash_point": 1.0,
    "bets": {}            # {user_id: bet_amount}
}

# ==========================================
# 6. CRASH ENGINE (WEBSOCKET LOOP)
# ==========================================

async def crash_loop():
    """Бесконечный фоновый цикл игры Crash с передачей параметров по WebSocket."""
    global crash_state
    while True:
        # Фаза ожидания
        crash_state["status"] = "waiting"
        crash_state["multiplier"] = 1.0
        
        # Расчет точки краша с учетом House Edge
        e = random.uniform(0.01, 1.0)
        crash_point = max(1.0, round((1.0 - CRASH_HOUSE_EDGE) / e, 2))
        if crash_point > 100.0:
            crash_point = 100.0
        crash_state["crash_point"] = crash_point

        # Обратный отсчет до старта
        for t in range(5, 0, -1):
            await sio.emit('crash_state', {'timer': t, 'status': 'waiting'})
            await asyncio.sleep(1)

        # Старт раунда
        crash_state["status"] = "running"
        await sio.emit('crash_start', {})
        
        current = 1.0
        while current < crash_state["crash_point"]:
            await asyncio.sleep(0.1)
            current = round(current + 0.02 + (current * 0.015), 2)
            crash_state["multiplier"] = current
            await sio.emit('crash_multiplier', {'multiplier': current})

        # Завершение раунда
        crash_state["status"] = "crashed"
        await sio.emit('crash_end', {'crash_point': crash_state["crash_point"]})
        crash_state["bets"].clear()
        await asyncio.sleep(4)

@app.on_event("startup")
async def startup_event():
    # Запуск фонового процесса Crash при старте FastAPI
    asyncio.create_task(crash_loop())

# ==========================================
# 7. API ENDPOINTS: ИГРОВЫЕ РЕЖИМЫ
# ==========================================

@app.post("/api/upgrade")
@limiter.limit("20/minute")
async def upgrade_item_api(
    req: Request,
    data: UpgradeRequest,
    authorization: Optional[str] = Header(None)
):
    """Режим Upgrade (Улучшение предметов из инвентаря)."""
    tg_id = 12345678  # Тестовый ID
    user = db_get_user(tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    inv = json.loads(user['inventory'])
    if data.item_index < 0 or data.item_index >= len(inv):
        raise HTTPException(status_code=400, detail="Указанный предмет не найден в инвентаре")

    # Извлекаем предмет из инвентаря
    item = inv.pop(data.item_index)
    
    # Шанс победы рассчитывается строго от целевого коэффициента с учетом комиссий
    chance = (1.0 / data.target_multiplier) * (1.0 - UPGRADE_HOUSE_EDGE)
    is_success = random.random() < chance
    win_item = None

    if is_success:
        win_item = {
            "id": f"upgraded_{int(time.time())}",
            "name": f"★ {item['name']}",
            "price": int(item['price'] * data.target_multiplier),
            "rarity": "mythic"
        }
        inv.append(win_item)

    db_update_inventory(tg_id, inv)

    return {
        "success": bool(is_success),
        "chance": round(chance * 100, 2),
        "win_item": win_item
    }

@app.post("/api/coinflip")
@limiter.limit("30/minute")
async def play_coinflip_api(
    req: Request,
    data: CoinFlipRequest,
    authorization: Optional[str] = Header(None)
):
    """Режим CoinFlip (Орел или Решка)."""
    tg_id = 12345678
    user = db_get_user(tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    if user['balance'] < data.bet_amount:
        raise HTTPException(status_code=400, detail="Недостаточно UC на балансе")

    # Списываем ставку
    db_update_balance(tg_id, -data.bet_amount, "coinflip_bet")

    result = random.choice(['heads', 'tails'])
    win = (result == data.choice)
    win_amount = 0

    if win:
        # Выигрыш х1.9 (5% house edge)
        win_amount = int(data.bet_amount * (2.0 - COINFLIP_HOUSE_EDGE))
        db_update_balance(tg_id, win_amount, "coinflip_win")

    return {
        "win": win,
        "result": result,
        "win_amount": win_amount
    }

@app.post("/api/mines/start")
@limiter.limit("15/minute")
async def mines_start_api(
    req: Request,
    data: MinesStartRequest,
    authorization: Optional[str] = Header(None)
):
    """Старт игры в Mines (Минное поле 5x5)."""
    tg_id = 12345678
    user = db_get_user(tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    if user['balance'] < data.bet_amount:
        raise HTTPException(status_code=400, detail="Недостаточно UC на балансе")

    db_update_balance(tg_id, -data.bet_amount, "mines_bet")

    # Генерируем 25 ячеек с минами
    grid = [False] * 25
    mine_positions = random.sample(range(25), data.mines_count)
    for pos in mine_positions:
        grid[pos] = True

    game_id = f"mines_{tg_id}_{int(time.time() * 1000)}"
    active_mines_games[game_id] = {
        "user_id": tg_id,
        "bet": data.bet_amount,
        "mines_count": data.mines_count,
        "grid": grid,
        "opened_cells": [],
        "step": 0
    }

    return {
        "game_id": game_id,
        "mines_count": data.mines_count,
        "bet": data.bet_amount
    }@app.post("/api/mines/open")
@limiter.limit("60/minute")
async def mines_open_api(
    req: Request,
    data: MinesOpenRequest,
    authorization: Optional[str] = Header(None)
):
    """Открытие ячейки в режиме Mines."""
    game = active_mines_games.get(data.game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Активная игра не найдена")

    if data.cell_index in game["opened_cells"]:
        raise HTTPException(status_code=400, detail="Ячейка уже открыта")

    # Проверка на мину
    if game["grid"][data.cell_index]:
        # Взрыв - игра окончена
        del active_mines_games[data.game_id]
        return {
            "game_over": True,
            "hit_mine": True,
            "cell_index": data.cell_index,
            "opened_cells": game["opened_cells"]
        }

    game["opened_cells"].append(data.cell_index)
    game["step"] += 1
    
    # Получение текущего множителя
    mults = MINES_MULTIPLIERS.get(game["mines_count"], [1.05 * game["step"]])
    step_idx = min(game["step"] - 1, len(mults) - 1)
    current_mult = mults[step_idx]

    return {
        "game_over": False,
        "hit_mine": False,
        "cell_index": data.cell_index,
        "step": game["step"],
        "current_multiplier": current_mult,
        "current_win": int(game["bet"] * current_mult)
    }

@app.post("/api/mines/cashout")
async def mines_cashout_api(data: MinesCashoutRequest, authorization: Optional[str] = Header(None)):
    """Забрать выигрыш в режиме Mines."""
    game = active_mines_games.get(data.game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Активная игра не найдена")

    if game["step"] == 0:
        raise HTTPException(status_code=400, detail="Необходимо открыть хотя бы одну ячейку")

    mults = MINES_MULTIPLIERS.get(game["mines_count"], [1.05 * game["step"]])
    step_idx = min(game["step"] - 1, len(mults) - 1)
    final_mult = mults[step_idx]
    win_amount = int(game["bet"] * final_mult)

    # Начисление выигрыша
    db_update_balance(game["user_id"], win_amount, "mines_win")

    # Обновление квестов
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "UPDATE daily_quests SET progress = progress + 1 WHERE user_id = ? AND quest_type = 'play_mines'",
        (game["user_id"],)
    )
    conn.commit()
    conn.close()

    del active_mines_games[data.game_id]

    return {
        "success": True,
        "win_amount": win_amount,
        "multiplier": final_mult
    }

# ==========================================
# 8. API ENDPOINTS: КЕЙСЫ И ИНВЕНТАРЬ
# ==========================================

@app.post("/api/case/open")
@limiter.limit("15/minute")
async def open_case_api(
    req: Request,
    data: CaseOpenRequest,
    authorization: Optional[str] = Header(None)
):
    """Открытие ящика с выпадением предметов."""
    tg_id = 12345678
    user = db_get_user(tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    case = CASE_PRICES.get(data.case_type)
    if not case:
        raise HTTPException(status_code=400, detail="Ящик не существует")

    if user['balance'] < case['price']:
        raise HTTPException(status_code=400, detail="Недостаточно UC")

    # Списываем стоимость кейса
    db_update_balance(tg_id, -case['price'], "case_open")

    # Выбор случайного предмета из списка
    win_item = random.choice(case['items'])
    inv = json.loads(user['inventory'])
    inv.append(win_item)

    # Обновление инвентаря, расходов и квестов
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE users SET inventory = ?, total_spent = total_spent + ? WHERE tg_id = ?", 
              (json.dumps(inv), case['price'], tg_id))
    c.execute("UPDATE daily_quests SET progress = progress + 1 WHERE user_id = ? AND quest_type = 'open_cases'", 
              (tg_id,))
    conn.commit()
    conn.close()

    return {
        "win_item": win_item,
        "win_index": case['items'].index(win_item)
    }

# ==========================================
# 9. API ENDPOINTS: ПРОФИЛЬ И ФИНАНСЫ
# ==========================================

@app.get("/api/profile")
async def get_profile_api(authorization: Optional[str] = Header(None)):
    """Получение данных профиля и инвентаря."""
    tg_id = 12345678
    user = db_create_user_if_not_exists(tg_id, "Survivor_Player")
    user_data = dict(user)
    user_data['inventory'] = json.loads(user_data['inventory'])
    return user_data

@app.post("/api/deposit/create")
@limiter.limit("5/minute")
async def create_deposit_api(
    req: Request,
    data: DepositRequest,
    authorization: Optional[str] = Header(None)
):
    """Пополнение баланса UC."""
    tg_id = 12345678
    db_update_balance(tg_id, data.amount, "deposit")

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT INTO deposits (user_id, amount) VALUES (?, ?)", (tg_id, data.amount))
    conn.commit()
    conn.close()

    return {"status": "success", "amount": data.amount}

@app.post("/api/withdraw")
async def request_withdraw_api(data: WithdrawRequest, authorization: Optional[str] = Header(None)):
    """Создание заявки на вывод средств."""
    tg_id = 12345678
    user = db_get_user(tg_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    if user['balance'] < data.amount:
        raise HTTPException(status_code=400, detail="Недостаточно средств для вывода")

    db_update_balance(tg_id, -data.amount, "withdraw_request")

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT INTO withdraws (user_id, amount, wallet) VALUES (?, ?, ?)", 
              (tg_id, data.amount, data.wallet))
    conn.commit()
    conn.close()

    return {"status": "success", "amount": data.amount, "wallet": data.wallet}

@app.get("/api/admin/stats")
async def get_admin_stats_api():
    """Статистика для админ-панели."""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*), SUM(balance) FROM users")
    users_cnt, total_bal = c.fetchone()
    c.execute("SELECT SUM(amount) FROM deposits")
    deposits = c.fetchone()[0] or 0
    c.execute("SELECT SUM(amount) FROM withdraws")
    withdraws = c.fetchone()[0] or 0
    conn.close()
    
    return {
        "total_users": users_cnt,
        "total_balance": total_bal or 0,
        "total_deposits": deposits,
        "total_withdraws": withdraws
    }

# ==========================================
# 10. РАЗДАЧА HTML И ЗАПУСК
# ==========================================

from fastapi.responses import HTMLResponse

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """Отдача фронтенда index.html клиенту."""
    if not os.path.exists("index.html"):
        return HTMLResponse("<h2>Ошибка: Файл index.html не найден в папке с main.py</h2>", status_code=404)
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

