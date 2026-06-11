import asyncio
import json
import os
import time
import random
import hashlib
import hmac
from datetime import datetime
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7092015279"))
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://your-miniapp-domain.netlify.app")  # URL вашего фронтенда

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# --- Данные ---
users = {}
pending_withdrawals = {}
crash_game = {"active": False, "multiplier": 1.0, "bets": {}, "task": None}
arena_rounds = {}
case_cooldowns = {}

# --- Загрузка данных ---
def load_data():
    global users, pending_withdrawals
    if os.path.exists("users.json"):
        with open("users.json", "r") as f:
            users = json.load(f)
    if os.path.exists("withdrawals.json"):
        with open("withdrawals.json", "r") as f:
            pending_withdrawals = json.load(f)

def save_data():
    with open("users.json", "w") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)
    with open("withdrawals.json", "w") as f:
        json.dump(pending_withdrawals, f, ensure_ascii=False, indent=2)

def get_user(user_id, username="Unknown"):
    uid = str(user_id)
    if uid not in users:
        users[uid] = {
            "id": user_id,
            "username": username,
            "balance": 0,
            "inventory": [],
            "no_cooldown": False,
            "odd_bets": False,
            "registered_at": datetime.now().isoformat()
        }
        save_data()
    return users[uid]

# --- Валидация initData (безопасность) ---
def check_init_data(init_data: str, bot_token: str) -> dict:
    """Проверяет подпись Telegram WebApp initData, возвращает dict с параметрами или None"""
    try:
        params = {}
        for pair in init_data.split('&'):
            key, val = pair.split('=')
            params[key] = val
        if 'hash' not in params:
            return None
        hash_str = params.pop('hash')
        data_check_string = '\n'.join([f"{k}={v}" for k, v in sorted(params.items())])
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if computed_hash == hash_str:
            return params
    except:
        return None
    return None

# --- Данные кейсов, предметов, апгрейдов (те же самые, что в исходнике) ---
STAR_CASES = [
    {"name": "⭐ Bronze Case", "price": 50, "items": [
        {"name": "10 Stars", "value": 10, "type": "stars", "chance": 40},
        {"name": "25 Stars", "value": 25, "type": "stars", "chance": 25},
        {"name": "50 Stars", "value": 50, "type": "stars", "chance": 15},
        {"name": "75 Stars", "value": 75, "type": "stars", "chance": 10},
        {"name": "100 Stars", "value": 100, "type": "stars", "chance": 5},
        {"name": "150 Stars", "value": 150, "type": "stars", "chance": 3},
        {"name": "200 Stars", "value": 200, "type": "stars", "chance": 1.5},
        {"name": "500 Stars", "value": 500, "type": "stars", "chance": 0.5}
    ]},
    {"name": "⭐ Silver Case", "price": 150, "items": [
        {"name": "50 Stars", "value": 50, "type": "stars", "chance": 35},
        {"name": "100 Stars", "value": 100, "type": "stars", "chance": 25},
        {"name": "175 Stars", "value": 175, "type": "stars", "chance": 15},
        {"name": "250 Stars", "value": 250, "type": "stars", "chance": 12},
        {"name": "350 Stars", "value": 350, "type": "stars", "chance": 7},
        {"name": "500 Stars", "value": 500, "type": "stars", "chance": 4},
        {"name": "750 Stars", "value": 750, "type": "stars", "chance": 1.5},
        {"name": "1500 Stars", "value": 1500, "type": "stars", "chance": 0.5}
    ]},
    {"name": "⭐ Gold Case", "price": 400, "items": [
        {"name": "150 Stars", "value": 150, "type": "stars", "chance": 30},
        {"name": "300 Stars", "value": 300, "type": "stars", "chance": 25},
        {"name": "500 Stars", "value": 500, "type": "stars", "chance": 18},
        {"name": "700 Stars", "value": 700, "type": "stars", "chance": 12},
        {"name": "900 Stars", "value": 900, "type": "stars", "chance": 8},
        {"name": "1200 Stars", "value": 1200, "type": "stars", "chance": 4},
        {"name": "2000 Stars", "value": 2000, "type": "stars", "chance": 2},
        {"name": "5000 Stars", "value": 5000, "type": "stars", "chance": 1}
    ]},
    {"name": "⭐ Diamond Case", "price": 1000, "items": [
        {"name": "500 Stars", "value": 500, "type": "stars", "chance": 30},
        {"name": "800 Stars", "value": 800, "type": "stars", "chance": 22},
        {"name": "1200 Stars", "value": 1200, "type": "stars", "chance": 18},
        {"name": "1800 Stars", "value": 1800, "type": "stars", "chance": 13},
        {"name": "2500 Stars", "value": 2500, "type": "stars", "chance": 8},
        {"name": "4000 Stars", "value": 4000, "type": "stars", "chance": 5},
        {"name": "7000 Stars", "value": 7000, "type": "stars", "chance": 3},
        {"name": "15000 Stars", "value": 15000, "type": "stars", "chance": 1}
    ]}
]
NFT_CASES = [
    {"name": "🎁 Starter NFT Case", "price": 100, "items": [
        {"name": "Plush Pepe", "value": 80, "type": "nft", "chance": 35, "rarity": "Common"},
        {"name": "Disco Ball", "value": 120, "type": "nft", "chance": 25, "rarity": "Common"},
        {"name": "Loot Bag", "value": 200, "type": "nft", "chance": 18, "rarity": "Uncommon"},
        {"name": "Spell Book", "value": 300, "type": "nft", "chance": 10, "rarity": "Uncommon"},
        {"name": "Astro Helmet", "value": 500, "type": "nft", "chance": 7, "rarity": "Rare"},
        {"name": "Neon Cat", "value": 800, "type": "nft", "chance": 3, "rarity": "Rare"},
        {"name": "Golden Doge", "value": 1500, "type": "nft", "chance": 1.5, "rarity": "Epic"},
        {"name": "Cosmic Skull", "value": 3000, "type": "nft", "chance": 0.5, "rarity": "Legendary"}
    ]},
    {"name": "🎁 Mystic NFT Case", "price": 300, "items": [
        {"name": "Flame Sword", "value": 250, "type": "nft", "chance": 32, "rarity": "Common"},
        {"name": "Ice Crown", "value": 380, "type": "nft", "chance": 24, "rarity": "Common"},
        {"name": "Thunder Shield", "value": 550, "type": "nft", "chance": 18, "rarity": "Uncommon"},
        {"name": "Shadow Cloak", "value": 800, "type": "nft", "chance": 12, "rarity": "Uncommon"},
        {"name": "Dragon Eye", "value": 1200, "type": "nft", "chance": 8, "rarity": "Rare"},
        {"name": "Phoenix Feather", "value": 2000, "type": "nft", "chance": 4, "rarity": "Epic"},
        {"name": "Void Fragment", "value": 4000, "type": "nft", "chance": 1.5, "rarity": "Epic"},
        {"name": "Galaxy Orb", "value": 9000, "type": "nft", "chance": 0.5, "rarity": "Legendary"}
    ]},
    {"name": "🎁 Legend NFT Case", "price": 700, "items": [
        {"name": "Crystal Wand", "value": 600, "type": "nft", "chance": 30, "rarity": "Uncommon"},
        {"name": "Titan Armor", "value": 900, "type": "nft", "chance": 22, "rarity": "Uncommon"},
        {"name": "Storm Hammer", "value": 1400, "type": "nft", "chance": 17, "rarity": "Rare"},
        {"name": "Soul Lantern", "value": 2000, "type": "nft", "chance": 12, "rarity": "Rare"},
        {"name": "Demon Wings", "value": 3000, "type": "nft", "chance": 9, "rarity": "Epic"},
        {"name": "Ancient Relic", "value": 5000, "type": "nft", "chance": 6, "rarity": "Epic"},
        {"name": "Celestial Harp", "value": 10000, "type": "nft", "chance": 3, "rarity": "Legendary"},
        {"name": "Infinity Gem", "value": 25000, "type": "nft", "chance": 1, "rarity": "Mythic"}
    ]},
    {"name": "🎁 Ultra NFT Case", "price": 2000, "items": [
        {"name": "Neon Samurai", "value": 1800, "type": "nft", "chance": 28, "rarity": "Rare"},
        {"name": "Crypto Punk #X", "value": 2500, "type": "nft", "chance": 22, "rarity": "Rare"},
        {"name": "Bored Ape Clone", "value": 3500, "type": "nft", "chance": 18, "rarity": "Epic"},
        {"name": "Laser Eyes Bot", "value": 5000, "type": "nft", "chance": 13, "rarity": "Epic"},
        {"name": "Quantum Fox", "value": 8000, "type": "nft", "chance": 9, "rarity": "Legendary"},
        {"name": "Astral Dragon", "value": 15000, "type": "nft", "chance": 6, "rarity": "Legendary"},
        {"name": "Divine Katana", "value": 30000, "type": "nft", "chance": 3, "rarity": "Mythic"},
        {"name": "Genesis NFT", "value": 75000, "type": "nft", "chance": 1, "rarity": "Mythic"}
    ]}
]
ALL_CASES = STAR_CASES + NFT_CASES
SHOP_ITEMS = [
    {"id": "no_cooldown", "name": "⚡ No Cooldown", "desc": "Убрать КД на кейсы навсегда", "price": 500},
    {"id": "odd_bets", "name": "🎲 Odd Bets", "desc": "Ставить нечётные суммы в Краш", "price": 250}
]
UPGRADE_CHAINS = [
    ["Plush Pepe", "Disco Ball", "Loot Bag", "Spell Book", "Astro Helmet", "Neon Cat", "Golden Doge", "Cosmic Skull"],
    ["Flame Sword", "Ice Crown", "Thunder Shield", "Shadow Cloak", "Dragon Eye", "Phoenix Feather", "Void Fragment", "Galaxy Orb"],
    ["Crystal Wand", "Titan Armor", "Storm Hammer", "Soul Lantern", "Demon Wings", "Ancient Relic", "Celestial Harp", "Infinity Gem"],
    ["Neon Samurai", "Crypto Punk #X", "Bored Ape Clone", "Laser Eyes Bot", "Quantum Fox", "Astral Dragon", "Divine Katana", "Genesis NFT"]
]

def get_item_value(item_name):
    for case in ALL_CASES:
        for item in case["items"]:
            if item["name"] == item_name:
                return item["value"]
    return 0

def get_upgrade_chance(current_name, next_name):
    cur = get_item_value(current_name)
    nxt = get_item_value(next_name)
    if nxt <= 0:
        return 0
    return max(5, min(85, int((cur / nxt) * 100)))

def roll_item(case):
    items = case["items"]
    total = sum(i["chance"] for i in items)
    r = random.uniform(0, total)
    cum = 0
    for item in items:
        cum += item["chance"]
        if r <= cum:
            return item
    return items[-1]

# --- API хендлеры ---
async def api_handle(request):
    data = await request.json()
    init_data = data.get("initData")
    params = check_init_data(init_data, BOT_TOKEN)
    if not params:
        return web.json_response({"error": "Unauthorized"}, status=401)
    user_id = int(params.get("user", {}).get("id", 0)) if isinstance(params.get("user"), dict) else 0
    if not user_id:
        return web.json_response({"error": "No user"}, status=400)

    action = data.get("action")
    user = get_user(user_id)

    if action == "getUser":
        inv_value = sum(i["value"] for i in user["inventory"])
        return web.json_response({
            "balance": user["balance"],
            "inventory": user["inventory"],
            "no_cooldown": user.get("no_cooldown", False),
            "odd_bets": user.get("odd_bets", False),
            "inventory_value": inv_value
        })
    elif action == "openCase":
        case_idx = data.get("caseIdx")
        if case_idx is None or case_idx >= len(ALL_CASES):
            return web.json_response({"error": "Invalid case"})
        case = ALL_CASES[case_idx]
        if not user.get("no_cooldown"):
            cd_key = f"{user_id}_{case_idx}"
            last = case_cooldowns.get(cd_key, 0)
            if time.time() - last < 3:
                return web.json_response({"error": f"Cooldown {3 - int(time.time()-last)}s"})
        if user["balance"] < case["price"]:
            return web.json_response({"error": "Not enough stars"})
        user["balance"] -= case["price"]
        if not user.get("no_cooldown"):
            case_cooldowns[f"{user_id}_{case_idx}"] = time.time()
        won = roll_item(case)
        if won["type"] == "stars":
            user["balance"] += won["value"]
            result = {"type": "stars", "amount": won["value"], "item": None}
        else:
            user["inventory"].append(dict(won))
            result = {"type": "nft", "item": won, "amount": 0}
        save_data()
        return web.json_response({"newBalance": user["balance"], "result": result})
    elif action == "sellItem":
        idx = data.get("itemIdx")
        if idx is None or idx >= len(user["inventory"]):
            return web.json_response({"error": "Invalid item"})
        item = user["inventory"].pop(idx)
        sell_price = int(item["value"] * 0.95)
        user["balance"] += sell_price
        save_data()
        return web.json_response({"newBalance": user["balance"], "soldItem": item, "sellPrice": sell_price})
    elif action == "upgradeItem":
        idx = data.get("itemIdx")
        if idx is None or idx >= len(user["inventory"]):
            return web.json_response({"error": "Invalid item"})
        item = user["inventory"][idx]
        item_name = item["name"]
        next_item = None
        next_name = None
        for chain in UPGRADE_CHAINS:
            if item_name in chain:
                pos = chain.index(item_name)
                if pos + 1 < len(chain):
                    next_name = chain[pos+1]
                    for case in ALL_CASES:
                        for ci in case["items"]:
                            if ci["name"] == next_name:
                                next_item = dict(ci)
                                break
                break
        if not next_item:
            return web.json_response({"error": "Max level"})
        chance = get_upgrade_chance(item_name, next_name)
        success = random.randint(1,100) <= chance
        if success:
            user["inventory"][idx] = next_item
            result = {"success": True, "newItem": next_item}
        else:
            user["inventory"].pop(idx)
            result = {"success": False}
        save_data()
        return web.json_response({"result": result, "chance": chance})
    elif action == "buyShopItem":
        item_id = data.get("itemId")
        shop_item = next((i for i in SHOP_ITEMS if i["id"] == item_id), None)
        if not shop_item:
            return web.json_response({"error": "Not found"})
        if user.get(item_id):
            return web.json_response({"error": "Already bought"})
        if user["balance"] < shop_item["price"]:
            return web.json_response({"error": "Not enough"})
        user["balance"] -= shop_item["price"]
        user[item_id] = True
        save_data()
        return web.json_response({"success": True, "newBalance": user["balance"]})
    elif action == "crashInfo":
        return web.json_response({
            "active": crash_game["active"],
            "multiplier": crash_game["multiplier"],
            "userBet": crash_game["bets"].get(str(user_id))
        })
    elif action == "crashBet":
        amount = data.get("amount")
        if not crash_game["active"]:
            return web.json_response({"error": "Game not active"})
        if not user.get("odd_bets") and amount % 2 != 0:
            return web.json_response({"error": "Only even bets"})
        if user["balance"] < amount:
            return web.json_response({"error": "Not enough"})
        uid = str(user_id)
        if uid in crash_game["bets"]:
            return web.json_response({"error": "Already bet"})
        user["balance"] -= amount
        crash_game["bets"][uid] = {"amount": amount, "cashed_out": False}
        save_data()
        return web.json_response({"success": True, "newBalance": user["balance"]})
    elif action == "crashCashout":
        uid = str(user_id)
        if not crash_game["active"] or uid not in crash_game["bets"] or crash_game["bets"][uid]["cashed_out"]:
            return web.json_response({"error": "Cannot cashout"})
        bet = crash_game["bets"][uid]
        win = int(bet["amount"] * crash_game["multiplier"])
        crash_game["bets"][uid]["cashed_out"] = True
        user = get_user(user_id)
        user["balance"] += win
        save_data()
        return web.json_response({"win": win, "newBalance": user["balance"]})
    elif action == "arenaInfo":
        active_round = None
        for rid, rd in arena_rounds.items():
            if rd["status"] == "waiting":
                active_round = {"id": rid, "players": rd["players"], "total": sum(p["amount"] for p in rd["players"].values())}
                break
        return web.json_response({"activeRound": active_round})
    elif action == "arenaJoin":
        amount = data.get("amount")
        if amount <= 0 or user["balance"] < amount:
            return web.json_response({"error": "Invalid amount"})
        active_round = None
        for rid, rd in arena_rounds.items():
            if rd["status"] == "waiting" and len(rd["players"]) < 5:
                active_round = rid
                break
        if not active_round:
            active_round = f"arena_{int(time.time())}"
            arena_rounds[active_round] = {"players": {}, "status": "waiting", "start_time": time.time()}
        if str(user_id) in arena_rounds[active_round]["players"]:
            return web.json_response({"error": "Already in round"})
        user["balance"] -= amount
        arena_rounds[active_round]["players"][str(user_id)] = {"amount": amount, "username": user["username"]}
        save_data()
        # Запуск таймера или мгновенный старт при 5 игроках
        if len(arena_rounds[active_round]["players"]) >= 5:
            asyncio.create_task(resolve_arena(active_round))
        else:
            asyncio.create_task(arena_countdown(active_round, 30))
        return web.json_response({"success": True, "newBalance": user["balance"]})
    else:
        return web.json_response({"error": "Unknown action"})

async def resolve_arena(round_id):
    await asyncio.sleep(0.1)  # небольшая задержка
    if round_id not in arena_rounds:
        return
    rd = arena_rounds[round_id]
    rd["status"] = "finished"
    players = rd["players"]
    if len(players) < 2:
        for uid in players:
            u = get_user(int(uid))
            u["balance"] += players[uid]["amount"]
            save_data()
    else:
        total = sum(p["amount"] for p in players.values())
        r = random.uniform(0, total)
        cum = 0
        winner_uid = None
        for uid, p in players.items():
            cum += p["amount"]
            if r <= cum:
                winner_uid = uid
                break
        if not winner_uid:
            winner_uid = list(players.keys())[-1]
        prize = int(total * 0.95)
        winner_user = get_user(int(winner_uid))
        winner_user["balance"] += prize
        save_data()
    del arena_rounds[round_id]

async def arena_countdown(round_id, seconds):
    await asyncio.sleep(seconds)
    if round_id in arena_rounds and arena_rounds[round_id]["status"] == "waiting":
        if len(arena_rounds[round_id]["players"]) >= 2:
            await resolve_arena(round_id)
        else:
            # недостаточно игроков – возврат ставок
            rd = arena_rounds[round_id]
            for uid, p in rd["players"].items():
                u = get_user(int(uid))
                u["balance"] += p["amount"]
            save_data()
            del arena_rounds[round_id]

# --- Краш-игра (фоновый цикл) ---
async def crash_loop():
    while True:
        await asyncio.sleep(10)
        crash_game["active"] = True
        crash_game["multiplier"] = 1.0
        crash_game["bets"] = {}
        crash_point = random.uniform(1.05, 15.0)
        if random.random() < 0.15:
            crash_point = 1.0
        start = time.time()
        while crash_game["multiplier"] < crash_point:
            elapsed = time.time() - start
            crash_game["multiplier"] = round(1.0 + elapsed * 0.3, 2)
            await asyncio.sleep(0.1)
        crash_game["active"] = False
        # сброс невыплаченных ставок
        crash_game["bets"] = {}
        await asyncio.sleep(5)

# --- Бот команды для старта Mini App ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = get_user(message.from_user.id, message.from_user.username or "Unknown")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Открыть игру", web_app=types.WebAppInfo(url=WEBAPP_URL))]
    ])
    await message.answer(
        f"🐸 Добро пожаловать в CaseFight!\nБаланс: {user['balance']}⭐\nНажми кнопку, чтобы открыть Mini App.",
        reply_markup=kb
    )

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    # простой админ-панель для заявок (можно расширить)
    await message.answer("Админ-панель через Mini App. Используйте API.")

# --- Обработка платежей (Telegram Stars) ---
@dp.callback_query(F.data.startswith("deposit_"))
async def cb_deposit(call: types.CallbackQuery):
    amount = int(call.data.split("_")[1])
    prices = [LabeledPrice(label="Пополнение", amount=amount)]
    await bot.send_invoice(
        chat_id=call.from_user.id,
        title="💰 Пополнение CaseFight",
        description=f"Пополнение на {amount} Stars (получите {int(amount*0.95)}⭐)",
        payload=f"deposit_{call.from_user.id}_{amount}",
        currency="XTR",
        prices=prices
    )
    await call.answer()

@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    parts = payload.split("_")
    user_id = int(parts[1])
    amount = int(parts[2])
    credited = int(amount * 0.95)
    user = get_user(user_id)
    user["balance"] += credited
    save_data()
    await message.answer(f"✅ +{credited}⭐ на баланс! Новый баланс: {user['balance']}⭐")

# --- Запуск aiohttp сервера (API) + polling бота ---
async def start_api():
    app = web.Application()
    app.router.add_post("/api", api_handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"API server started on port {port}")

async def main():
    load_data()
    asyncio.create_task(crash_loop())
    asyncio.create_task(start_api())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
