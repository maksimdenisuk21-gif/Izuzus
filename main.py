import asyncio
import logging
import json
import os
import time
import random
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8922972247:AAGbc4tYV51F3zxAGA3SuLcBY7PCyGRbXoE"
ADMIN_ID = 7092015279
WEBSITE_URL = "https://jocular-blancmange-9267e3.netlify.app"

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

users = {}
pending_withdrawals = {}
crash_game = {"active": False, "multiplier": 1.0, "bets": {}, "task": None}
arena_rounds = {}
case_cooldowns = {}

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

STAR_CASES = [
    {
        "name": "⭐ Bronze Case",
        "price": 50,
        "items": [
            {"name": "10 Stars", "value": 10, "type": "stars", "chance": 40},
            {"name": "25 Stars", "value": 25, "type": "stars", "chance": 25},
            {"name": "50 Stars", "value": 50, "type": "stars", "chance": 15},
            {"name": "75 Stars", "value": 75, "type": "stars", "chance": 10},
            {"name": "100 Stars", "value": 100, "type": "stars", "chance": 5},
            {"name": "150 Stars", "value": 150, "type": "stars", "chance": 3},
            {"name": "200 Stars", "value": 200, "type": "stars", "chance": 1.5},
            {"name": "500 Stars", "value": 500, "type": "stars", "chance": 0.5},
        ]
    },
    {
        "name": "⭐ Silver Case",
        "price": 150,
        "items": [
            {"name": "50 Stars", "value": 50, "type": "stars", "chance": 35},
            {"name": "100 Stars", "value": 100, "type": "stars", "chance": 25},
            {"name": "175 Stars", "value": 175, "type": "stars", "chance": 15},
            {"name": "250 Stars", "value": 250, "type": "stars", "chance": 12},
            {"name": "350 Stars", "value": 350, "type": "stars", "chance": 7},
            {"name": "500 Stars", "value": 500, "type": "stars", "chance": 4},
            {"name": "750 Stars", "value": 750, "type": "stars", "chance": 1.5},
            {"name": "1500 Stars", "value": 1500, "type": "stars", "chance": 0.5},
        ]
    },
    {
        "name": "⭐ Gold Case",
        "price": 400,
        "items": [
            {"name": "150 Stars", "value": 150, "type": "stars", "chance": 30},
            {"name": "300 Stars", "value": 300, "type": "stars", "chance": 25},
            {"name": "500 Stars", "value": 500, "type": "stars", "chance": 18},
            {"name": "700 Stars", "value": 700, "type": "stars", "chance": 12},
            {"name": "900 Stars", "value": 900, "type": "stars", "chance": 8},
            {"name": "1200 Stars", "value": 1200, "type": "stars", "chance": 4},
            {"name": "2000 Stars", "value": 2000, "type": "stars", "chance": 2},
            {"name": "5000 Stars", "value": 5000, "type": "stars", "chance": 1},
        ]
    },
    {
        "name": "⭐ Diamond Case",
        "price": 1000,
        "items": [
            {"name": "500 Stars", "value": 500, "type": "stars", "chance": 30},
            {"name": "800 Stars", "value": 800, "type": "stars", "chance": 22},
            {"name": "1200 Stars", "value": 1200, "type": "stars", "chance": 18},
            {"name": "1800 Stars", "value": 1800, "type": "stars", "chance": 13},
            {"name": "2500 Stars", "value": 2500, "type": "stars", "chance": 8},
            {"name": "4000 Stars", "value": 4000, "type": "stars", "chance": 5},
            {"name": "7000 Stars", "value": 7000, "type": "stars", "chance": 3},
            {"name": "15000 Stars", "value": 15000, "type": "stars", "chance": 1},
        ]
    },
]

NFT_CASES = [
    {
        "name": "🎁 Starter NFT Case",
        "price": 100,
        "items": [
            {"name": "Plush Pepe", "value": 80, "type": "nft", "chance": 35, "rarity": "Common"},
            {"name": "Disco Ball", "value": 120, "type": "nft", "chance": 25, "rarity": "Common"},
            {"name": "Loot Bag", "value": 200, "type": "nft", "chance": 18, "rarity": "Uncommon"},
            {"name": "Spell Book", "value": 300, "type": "nft", "chance": 10, "rarity": "Uncommon"},
            {"name": "Astro Helmet", "value": 500, "type": "nft", "chance": 7, "rarity": "Rare"},
            {"name": "Neon Cat", "value": 800, "type": "nft", "chance": 3, "rarity": "Rare"},
            {"name": "Golden Doge", "value": 1500, "type": "nft", "chance": 1.5, "rarity": "Epic"},
            {"name": "Cosmic Skull", "value": 3000, "type": "nft", "chance": 0.5, "rarity": "Legendary"},
        ]
    },
    {
        "name": "🎁 Mystic NFT Case",
        "price": 300,
        "items": [
            {"name": "Flame Sword", "value": 250, "type": "nft", "chance": 32, "rarity": "Common"},
            {"name": "Ice Crown", "value": 380, "type": "nft", "chance": 24, "rarity": "Common"},
            {"name": "Thunder Shield", "value": 550, "type": "nft", "chance": 18, "rarity": "Uncommon"},
            {"name": "Shadow Cloak", "value": 800, "type": "nft", "chance": 12, "rarity": "Uncommon"},
            {"name": "Dragon Eye", "value": 1200, "type": "nft", "chance": 8, "rarity": "Rare"},
            {"name": "Phoenix Feather", "value": 2000, "type": "nft", "chance": 4, "rarity": "Epic"},
            {"name": "Void Fragment", "value": 4000, "type": "nft", "chance": 1.5, "rarity": "Epic"},
            {"name": "Galaxy Orb", "value": 9000, "type": "nft", "chance": 0.5, "rarity": "Legendary"},
        ]
    },
    {
        "name": "🎁 Legend NFT Case",
        "price": 700,
        "items": [
            {"name": "Crystal Wand", "value": 600, "type": "nft", "chance": 30, "rarity": "Uncommon"},
            {"name": "Titan Armor", "value": 900, "type": "nft", "chance": 22, "rarity": "Uncommon"},
            {"name": "Storm Hammer", "value": 1400, "type": "nft", "chance": 17, "rarity": "Rare"},
            {"name": "Soul Lantern", "value": 2000, "type": "nft", "chance": 12, "rarity": "Rare"},
            {"name": "Demon Wings", "value": 3000, "type": "nft", "chance": 9, "rarity": "Epic"},
            {"name": "Ancient Relic", "value": 5000, "type": "nft", "chance": 6, "rarity": "Epic"},
            {"name": "Celestial Harp", "value": 10000, "type": "nft", "chance": 3, "rarity": "Legendary"},
            {"name": "Infinity Gem", "value": 25000, "type": "nft", "chance": 1, "rarity": "Mythic"},
        ]
    },
    {
        "name": "🎁 Ultra NFT Case",
        "price": 2000,
        "items": [
            {"name": "Neon Samurai", "value": 1800, "type": "nft", "chance": 28, "rarity": "Rare"},
            {"name": "Crypto Punk #X", "value": 2500, "type": "nft", "chance": 22, "rarity": "Rare"},
            {"name": "Bored Ape Clone", "value": 3500, "type": "nft", "chance": 18, "rarity": "Epic"},
            {"name": "Laser Eyes Bot", "value": 5000, "type": "nft", "chance": 13, "rarity": "Epic"},
            {"name": "Quantum Fox", "value": 8000, "type": "nft", "chance": 9, "rarity": "Legendary"},
            {"name": "Astral Dragon", "value": 15000, "type": "nft", "chance": 6, "rarity": "Legendary"},
            {"name": "Divine Katana", "value": 30000, "type": "nft", "chance": 3, "rarity": "Mythic"},
            {"name": "Genesis NFT", "value": 75000, "type": "nft", "chance": 1, "rarity": "Mythic"},
        ]
    },
]

ALL_CASES = STAR_CASES + NFT_CASES

SHOP_ITEMS = [
    {"id": "no_cooldown", "name": "⚡ No Cooldown", "desc": "Убрать КД на кейсы навсегда", "price": 500},
    {"id": "odd_bets", "name": "🎲 Odd Bets", "desc": "Ставить нечётные суммы в Краш", "price": 250},
]

UPGRADE_CHAINS = [
    ["Plush Pepe", "Disco Ball", "Loot Bag", "Spell Book", "Astro Helmet", "Neon Cat", "Golden Doge", "Cosmic Skull"],
    ["Flame Sword", "Ice Crown", "Thunder Shield", "Shadow Cloak", "Dragon Eye", "Phoenix Feather", "Void Fragment", "Galaxy Orb"],
    ["Crystal Wand", "Titan Armor", "Storm Hammer", "Soul Lantern", "Demon Wings", "Ancient Relic", "Celestial Harp", "Infinity Gem"],
    ["Neon Samurai", "Crypto Punk #X", "Bored Ape Clone", "Laser Eyes Bot", "Quantum Fox", "Astral Dragon", "Divine Katana", "Genesis NFT"],
]

def get_item_value(item_name):
    for case in ALL_CASES:
        for item in case["items"]:
            if item["name"] == item_name:
                return item["value"]
    return 0

def get_upgrade_chance(current_name, next_name):
    current_val = get_item_value(current_name)
    next_val = get_item_value(next_name)
    if next_val <= 0:
        return 0
    ratio = current_val / next_val
    chance = max(5, min(85, int(ratio * 100)))
    return chance

def roll_item(case):
    items = case["items"]
    total = sum(i["chance"] for i in items)
    r = random.uniform(0, total)
    cumulative = 0
    for item in items:
        cumulative += item["chance"]
        if r <= cumulative:
            return item
    return items[-1]

class WithdrawState(StatesGroup):
    amount = State()
    address = State()

class DepositState(StatesGroup):
    amount = State()

class CrashBetState(StatesGroup):
    amount = State()

class ArenaBetState(StatesGroup):
    amount = State()

class UpgradeState(StatesGroup):
    item_index = State()

def main_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Кейсы", callback_data="menu_cases"),
         InlineKeyboardButton(text="💥 Краш", callback_data="menu_crash")],
        [InlineKeyboardButton(text="⚔️ Арена", callback_data="menu_arena"),
         InlineKeyboardButton(text="🏪 Магазин", callback_data="menu_shop")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="menu_profile"),
         InlineKeyboardButton(text="💰 Пополнить", callback_data="menu_deposit")],
        [InlineKeyboardButton(text="💸 Вывести", callback_data="menu_withdraw")],
        [InlineKeyboardButton(text="🌐 Сайт", url=WEBSITE_URL)],
    ])

def cases_kb():
    buttons = []
    row = []
    for i, case in enumerate(ALL_CASES):
        emoji = "⭐" if case in STAR_CASES else "🎁"
        row.append(InlineKeyboardButton(
            text=f"{emoji} {case['name'].split(' ', 1)[1]} ({case['price']}⭐)",
            callback_data=f"open_case_{i}"
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def profile_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎒 Инвентарь", callback_data="inventory")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")],
    ])

def inventory_kb(user_id):
    uid = str(user_id)
    user = users.get(uid)
    if not user or not user["inventory"]:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_profile")]
        ])
    buttons = []
    for i, item in enumerate(user["inventory"]):
        buttons.append([
            InlineKeyboardButton(text=f"🔼 Улучшить {item['name']}", callback_data=f"upgrade_{i}"),
            InlineKeyboardButton(text=f"💵 Продать ({item['value']}⭐)", callback_data=f"sell_{i}"),
        ])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="menu_profile")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def shop_kb():
    buttons = []
    for item in SHOP_ITEMS:
        buttons.append([InlineKeyboardButton(
            text=f"{item['name']} — {item['price']}⭐",
            callback_data=f"buy_shop_{item['id']}"
        )])
    buttons.append([InlineKeyboardButton(text="📦 Купить предметы из кейсов", callback_data="shop_items")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Заявки на вывод", callback_data="admin_withdrawals")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
    ])

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = get_user(message.from_user.id, message.from_user.username or "Unknown")
    text = (
        "╔══════════════════════╗\n"
        "║   🐸 CaseFight 🐸    ║\n"
        "╚══════════════════════╝\n\n"
        f"👋 Привет, {message.from_user.first_name}!\n"
        f"💰 Баланс: {user['balance']}⭐\n\n"
        "Выбери раздел:"
    )
    await message.answer(text, reply_markup=main_menu_kb())

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("🛡 Панель администратора", reply_markup=admin_kb())

@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(call: types.CallbackQuery):
    user = get_user(call.from_user.id, call.from_user.username or "Unknown")
    text = (
        "╔══════════════════════╗\n"
        "║   🐸 CaseFight 🐸    ║\n"
        "╚══════════════════════╝\n\n"
        f"💰 Баланс: {user['balance']}⭐\n\n"
        "Выбери раздел:"
    )
    await call.message.edit_text(text, reply_markup=main_menu_kb())

@dp.callback_query(F.data == "menu_profile")
async def cb_profile(call: types.CallbackQuery):
    user = get_user(call.from_user.id, call.from_user.username or "Unknown")
    inv_count = len(user["inventory"])
    inv_value = sum(i["value"] for i in user["inventory"])
    text = (
        "👤 Профиль\n"
        "━━━━━━━━━━━━━━━━\n"
        f"🆔 ID: {user['id']}\n"
        f"👤 Имя: {call.from_user.first_name}\n"
        f"💰 Баланс: {user['balance']}⭐\n"
        f"🎒 Предметов: {inv_count} (стоимость: {inv_value}⭐)\n"
        f"⚡ Без КД: {'Да' if user.get('no_cooldown') else 'Нет'}\n"
        f"🎲 Нечёт. ставки: {'Да' if user.get('odd_bets') else 'Нет'}\n"
    )
    await call.message.edit_text(text, reply_markup=profile_kb())

@dp.callback_query(F.data == "inventory")
async def cb_inventory(call: types.CallbackQuery):
    user = get_user(call.from_user.id)
    if not user["inventory"]:
        await call.answer("🎒 Инвентарь пуст!", show_alert=True)
        return
    text = "🎒 Инвентарь:\n━━━━━━━━━━━━━━━━\n"
    for i, item in enumerate(user["inventory"]):
        rarity = item.get("rarity", "")
        text += f"{i+1}. {item['name']} {rarity} — {item['value']}⭐\n"
    await call.message.edit_text(text, reply_markup=inventory_kb(call.from_user.id))

@dp.callback_query(F.data.startswith("sell_"))
async def cb_sell_item(call: types.CallbackQuery):
    idx = int(call.data.split("_")[1])
    uid = str(call.from_user.id)
    user = users[uid]
    if idx >= len(user["inventory"]):
        await call.answer("Предмет не найден!", show_alert=True)
        return
    item = user["inventory"].pop(idx)
    sell_price = int(item["value"] * 0.95)
    user["balance"] += sell_price
    save_data()
    await call.answer(f"✅ Продано: {item['name']} за {sell_price}⭐", show_alert=True)
    await cb_inventory(call)

@dp.callback_query(F.data.startswith("upgrade_"))
async def cb_upgrade_item(call: types.CallbackQuery):
    idx = int(call.data.split("_")[1])
    uid = str(call.from_user.id)
    user = users[uid]
    if idx >= len(user["inventory"]):
        await call.answer("Предмет не найден!", show_alert=True)
        return
    item = user["inventory"][idx]
    item_name = item["name"]
    next_item = None
    next_name = None
    for chain in UPGRADE_CHAINS:
        if item_name in chain:
            pos = chain.index(item_name)
            if pos + 1 < len(chain):
                next_name = chain[pos + 1]
                for case in ALL_CASES:
                    for ci in case["items"]:
                        if ci["name"] == next_name:
                            next_item = ci
                break
    if not next_item:
        await call.answer("❌ Это максимальный предмет в цепочке!", show_alert=True)
        return
    chance = get_upgrade_chance(item_name, next_name)
    text = (
        f"🔼 Улучшение предмета\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Текущий: {item_name} ({item['value']}⭐)\n"
        f"➡️ Следующий: {next_name} ({next_item['value']}⭐)\n"
        f"🎯 Шанс успеха: {chance}%\n\n"
        f"При провале предмет будет уничтожен!"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"✅ Улучшить ({chance}%)", callback_data=f"do_upgrade_{idx}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="inventory")],
    ])
    await call.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data.startswith("do_upgrade_"))
async def cb_do_upgrade(call: types.CallbackQuery):
    idx = int(call.data.split("_")[2])
    uid = str(call.from_user.id)
    user = users[uid]
    if idx >= len(user["inventory"]):
        await call.answer("Предмет не найден!", show_alert=True)
        return
    item = user["inventory"][idx]
    item_name = item["name"]
    next_item = None
    next_name = None
    for chain in UPGRADE_CHAINS:
        if item_name in chain:
            pos = chain.index(item_name)
            if pos + 1 < len(chain):
                next_name = chain[pos + 1]
                for case in ALL_CASES:
                    for ci in case["items"]:
                        if ci["name"] == next_name:
                            next_item = dict(ci)
                break
    if not next_item:
        await call.answer("Ошибка!", show_alert=True)
        return
    chance = get_upgrade_chance(item_name, next_name)
    roll = random.randint(1, 100)
    if roll <= chance:
        user["inventory"][idx] = next_item
        save_data()
        await call.answer(f"🎉 Успех! Получен {next_name} ({next_item['value']}⭐)!", show_alert=True)
    else:
        user["inventory"].pop(idx)
        save_data()
        await call.answer(f"💔 Неудача! {item_name} уничтожен.", show_alert=True)
    await cb_inventory(call)

@dp.callback_query(F.data == "menu_cases")
async def cb_menu_cases(call: types.CallbackQuery):
    text = (
        "📦 Кейсы\n"
        "━━━━━━━━━━━━━━━━\n"
        "⭐ Звёздные кейсы — выигрывай валюту\n"
        "🎁 NFT кейсы — выигрывай редкие предметы\n\n"
        "Выбери кейс для открытия:"
    )
    await call.message.edit_text(text, reply_markup=cases_kb())

@dp.callback_query(F.data.startswith("open_case_"))
async def cb_open_case(call: types.CallbackQuery):
    case_idx = int(call.data.split("_")[2])
    if case_idx >= len(ALL_CASES):
        await call.answer("Кейс не найден!", show_alert=True)
        return
    uid = str(call.from_user.id)
    user = get_user(call.from_user.id, call.from_user.username or "Unknown")
    case = ALL_CASES[case_idx]
    if not user.get("no_cooldown"):
        cd_key = f"{uid}_{case_idx}"
        last_open = case_cooldowns.get(cd_key, 0)
        if time.time() - last_open < 3:
            remaining = 3 - int(time.time() - last_open)
            await call.answer(f"⏳ КД: {remaining}с. Купи 'No Cooldown' в магазине!", show_alert=True)
            return
    if user["balance"] < case["price"]:
        await call.answer(f"❌ Недостаточно средств! Нужно {case['price']}⭐", show_alert=True)
        return
    user["balance"] -= case["price"]
    cd_key = f"{uid}_{case_idx}"
    case_cooldowns[cd_key] = time.time()
    won_item = roll_item(case)
    if won_item["type"] == "stars":
        user["balance"] += won_item["value"]
        result_text = f"💰 +{won_item['value']}⭐ на баланс!"
    else:
        item_copy = dict(won_item)
        user["inventory"].append(item_copy)
        result_text = f"🎁 {won_item['name']} добавлен в инвентарь!"
    save_data()
    rarity_emoji = {"Common": "⚪", "Uncommon": "🟢", "Rare": "🔵", "Epic": "🟣", "Legendary": "🟡", "Mythic": "🔴"}.get(won_item.get("rarity", ""), "⭐")
    text = (
        f"📦 {case['name']}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🎰 Результат:\n"
        f"{rarity_emoji} {won_item['name']}\n"
        f"💎 Стоимость: {won_item['value']}⭐\n\n"
        f"✅ {result_text}\n"
        f"💰 Баланс: {user['balance']}⭐"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Открыть ещё", callback_data=f"open_case_{case_idx}")],
        [InlineKeyboardButton(text="🔙 К кейсам", callback_data="menu_cases")],
    ])
    await call.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data == "menu_shop")
async def cb_menu_shop(call: types.CallbackQuery):
    user = get_user(call.from_user.id)
    text = (
        f"🏪 Магазин\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💰 Твой баланс: {user['balance']}⭐\n\n"
        "📌 Доступные улучшения:"
    )
    await call.message.edit_text(text, reply_markup=shop_kb())

@dp.callback_query(F.data.startswith("buy_shop_"))
async def cb_buy_shop(call: types.CallbackQuery):
    item_id = call.data.split("buy_shop_")[1]
    user = get_user(call.from_user.id)
    shop_item = next((i for i in SHOP_ITEMS if i["id"] == item_id), None)
    if not shop_item:
        await call.answer("Товар не найден!", show_alert=True)
        return
    if user.get(item_id):
        await call.answer("✅ У тебя уже есть это!", show_alert=True)
        return
    if user["balance"] < shop_item["price"]:
        await call.answer(f"❌ Нужно {shop_item['price']}⭐", show_alert=True)
        return
    user["balance"] -= shop_item["price"]
    user[item_id] = True
    save_data()
    await call.answer(f"✅ Куплено: {shop_item['name']}!", show_alert=True)
    await cb_menu_shop(call)

@dp.callback_query(F.data == "shop_items")
async def cb_shop_items(call: types.CallbackQuery):
    buttons = []
    for case_idx, case in enumerate(ALL_CASES):
        for item in case["items"]:
            buttons.append([InlineKeyboardButton(
                text=f"{item['name']} — {item['value']}⭐",
                callback_data=f"buy_item_{case_idx}_{item['name']}"
            )])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="menu_shop")])
    await call.message.edit_text("🛒 Покупка предметов:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("buy_item_"))
async def cb_buy_item(call: types.CallbackQuery):
    parts = call.data.split("_", 3)
    case_idx = int(parts[2])
    item_name = parts[3]
    user = get_user(call.from_user.id)
    case = ALL_CASES[case_idx]
    item = next((i for i in case["items"] if i["name"] == item_name), None)
    if not item:
        await call.answer("Предмет не найден!", show_alert=True)
        return
    if user["balance"] < item["value"]:
        await call.answer(f"❌ Нужно {item['value']}⭐", show_alert=True)
        return
    user["balance"] -= item["value"]
    user["inventory"].append(dict(item))
    save_data()
    await call.answer(f"✅ Куплено: {item['name']}!", show_alert=True)

@dp.callback_query(F.data == "menu_deposit")
async def cb_menu_deposit(call: types.CallbackQuery):
    text = (
        "💰 Пополнение баланса\n"
        "━━━━━━━━━━━━━━━━\n"
        "• Минимум: 50 Telegram Stars\n"
        "• Курс: 1 Star = 1 ⭐\n"
        "• Комиссия: 5%\n\n"
        "Выберите сумму пополнения:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="50 ⭐", callback_data="deposit_50"),
         InlineKeyboardButton(text="100 ⭐", callback_data="deposit_100")],
        [InlineKeyboardButton(text="250 ⭐", callback_data="deposit_250"),
         InlineKeyboardButton(text="500 ⭐", callback_data="deposit_500")],
        [InlineKeyboardButton(text="1000 ⭐", callback_data="deposit_1000"),
         InlineKeyboardButton(text="2000 ⭐", callback_data="deposit_2000")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")],
    ])
    await call.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data.startswith("deposit_"))
async def cb_deposit(call: types.CallbackQuery):
    amount = int(call.data.split("_")[1])
    prices = [LabeledPrice(label=f"Пополнение {amount}⭐", amount=amount)]
    await bot.send_invoice(
        chat_id=call.from_user.id,
        title="💰 Пополнение CaseFight",
        description=f"Пополнение баланса на {amount} Stars (получите {int(amount * 0.95)}⭐ после комиссии 5%)",
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
    await message.answer(
        f"✅ Оплата успешна!\n"
        f"💰 Зачислено: {credited}⭐\n"
        f"💼 Новый баланс: {user['balance']}⭐"
    )
    await bot.send_message(
        ADMIN_ID,
        f"💰 Новое пополнение!\n"
        f"👤 User ID: {user_id}\n"
        f"💎 Сумма: {amount} Stars → {credited}⭐"
    )

@dp.callback_query(F.data == "menu_withdraw")
async def cb_menu_withdraw(call: types.CallbackQuery, state: FSMContext):
    user = get_user(call.from_user.id)
    text = (
        f"💸 Вывод средств\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💰 Твой баланс: {user['balance']}⭐\n"
        f"• Минимум: 100⭐\n"
        f"• Комиссия: 5%\n"
        f"• Вывод обрабатывается вручную\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")]
    ])
    await call.message.edit_text(text, reply_markup=kb)
    await state.set_state(WithdrawState.amount)
    await call.message.answer("Введи сумму для вывода (минимум 100⭐):")

@dp.message(F.text)
async def handle_text(message: types.Message, state: FSMContext):
    current_state = await state.get_state()

    if current_state == WithdrawState.amount:
        try:
            amount = int(message.text)
        except ValueError:
            await message.answer("❌ Введи число!")
            return
        user = get_user(message.from_user.id)
        if amount < 100:
            await message.answer("❌ Минимум 100⭐!")
            return
        if user["balance"] < amount:
            await message.answer(f"❌ Недостаточно средств! Баланс: {user['balance']}⭐")
            return
        await state.update_data(amount=amount)
        await state.set_state(WithdrawState.address)
        await message.answer("Введи свой Telegram username для вывода Stars:")
        return

    if current_state == WithdrawState.address:
        data = await state.get_data()
        amount = data["amount"]
        address = message.text
        uid = str(message.from_user.id)
        user = users[uid]
        if user["balance"] < amount:
            await message.answer("❌ Недостаточно средств!")
            await state.clear()
            return
        net_amount = int(amount * 0.95)
        user["balance"] -= amount
        wd_id = f"wd_{message.from_user.id}_{int(time.time())}"
        pending_withdrawals[wd_id] = {
            "user_id": message.from_user.id,
            "username": message.from_user.username or "Unknown",
            "amount": amount,
            "net": net_amount,
            "address": address,
            "time": datetime.now().isoformat(),
            "status": "pending"
        }
        save_data()
        await state.clear()
        await message.answer(
            f"✅ Заявка создана!\n"
            f"💰 Сумма: {amount}⭐ → {net_amount}⭐\n"
            f"📬 Адрес: {address}\n"
            f"⏳ Ожидай обработки",
            reply_markup=main_menu_kb()
        )
        await bot.send_message(
            ADMIN_ID,
            f"📤 Новая заявка на вывод!\n"
            f"🆔 ID: {wd_id}\n"
            f"👤 @{message.from_user.username or 'Unknown'} ({message.from_user.id})\n"
            f"💰 {amount}⭐ → {net_amount} Stars\n"
            f"📬 {address}\n"
            f"/approve_{wd_id}\n"
            f"/reject_{wd_id}"
        )
        return

    if current_state == CrashBetState.amount:
        try:
            amount = int(message.text)
        except ValueError:
            await message.answer("❌ Введи число!")
            return
        uid = str(message.from_user.id)
        user = get_user(message.from_user.id)
        if not user.get("odd_bets") and amount % 2 != 0:
            await message.answer("❌ Только чётные суммы! Купи 'Odd Bets' в магазине.")
            return
        if amount <= 0:
            await message.answer("❌ Сумма должна быть больше 0!")
            return
        if user["balance"] < amount:
            await message.answer(f"❌ Недостаточно средств! Баланс: {user['balance']}⭐")
            return
        if not crash_game["active"]:
            await message.answer("❌ Краш не запущен. Жди следующего раунда!")
            await state.clear()
            return
        if uid in crash_game["bets"]:
            await message.answer("❌ Ты уже поставил в этом раунде!")
            await state.clear()
            return
        user["balance"] -= amount
        crash_game["bets"][uid] = {"amount": amount, "cashed_out": False, "multiplier": 0}
        save_data()
        await state.clear()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💰 Забрать выигрыш!", callback_data="crash_cashout")]
        ])
        await message.answer(
            f"✅ Ставка: {amount}⭐\n"
            f"🐸 Множитель: {crash_game['multiplier']:.2f}x\n"
            f"Нажми кнопку чтобы забрать!",
            reply_markup=kb
        )
        return

    if current_state == ArenaBetState.amount:
        try:
            amount = int(message.text)
        except ValueError:
            await message.answer("❌ Введи число!")
            return
        uid = str(message.from_user.id)
        user = get_user(message.from_user.id)
        if amount <= 0:
            await message.answer("❌ Сумма должна быть больше 0!")
            return
        if user["balance"] < amount:
            await message.answer(f"❌ Недостаточно средств! Баланс: {user['balance']}⭐")
            return
        active_round = None
        for round_id, round_data in arena_rounds.items():
            if round_data["status"] == "waiting" and len(round_data["players"]) < 5:
                active_round = round_id
                break
        if not active_round:
            active_round = f"arena_{int(time.time())}"
            arena_rounds[active_round] = {"players": {}, "status": "waiting", "start_time": time.time()}
        if uid in arena_rounds[active_round]["players"]:
            await message.answer("❌ Ты уже в этом раунде!")
            await state.clear()
            return
        user["balance"] -= amount
        arena_rounds[active_round]["players"][uid] = {"amount": amount, "username": message.from_user.username or "Unknown"}
        save_data()
        await state.clear()
        player_count = len(arena_rounds[active_round]["players"])
        total_pot = sum(p["amount"] for p in arena_rounds[active_round]["players"].values())
        await message.answer(
            f"⚔️ Ставка принята: {amount}⭐\n"
            f"👥 Игроков: {player_count}/5\n"
            f"💰 Банк: {total_pot}⭐\n"
            f"⏳ Старт через 30 сек или при 5 игроках"
        )
        if player_count >= 5:
            await resolve_arena(active_round)
        elif player_count >= 2:
            asyncio.create_task(arena_countdown(active_round, 30))

async def crash_loop():
    while True:
        await asyncio.sleep(10)
        crash_game["active"] = True
        crash_game["multiplier"] = 1.0
        crash_game["bets"] = {}
        crash_point = random.uniform(1.05, 15.0)
        if random.random() < 0.15:
            crash_point = 1.0
        start_time = time.time()
        while crash_game["multiplier"] < crash_point:
            elapsed = time.time() - start_time
            crash_game["multiplier"] = round(1.0 + elapsed * 0.3, 2)
            await asyncio.sleep(0.1)
        crash_game["active"] = False
        crash_game["bets"] = {}
        await asyncio.sleep(5)

@dp.callback_query(F.data == "menu_crash")
async def cb_menu_crash(call: types.CallbackQuery, state: FSMContext):
    user = get_user(call.from_user.id)
    status = "🟢 Активен" if crash_game["active"] else "🔴 Ожидание"
    multiplier = f"{crash_game['multiplier']:.2f}x" if crash_game["active"] else "—"
    text = (
        f"💥 Краш\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Статус: {status}\n"
        f"🐸 Множитель: {multiplier}\n"
        f"💰 Баланс: {user['balance']}⭐\n\n"
        f"Поставь сумму и забери до краша!\n"
        f"❗ Только чётные суммы (без 'Odd Bets')"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Сделать ставку", callback_data="crash_bet")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")],
    ])
    await call.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data == "crash_bet")
async def cb_crash_bet(call: types.CallbackQuery, state: FSMContext):
    if not crash_game["active"]:
        await call.answer("❌ Краш не активен! Подожди.", show_alert=True)
        return
    uid = str(call.from_user.id)
    if uid in crash_game["bets"]:
        await call.answer("❌ Ты уже поставил!", show_alert=True)
        return
    await state.set_state(CrashBetState.amount)
    await call.message.answer("Введи сумму ставки:")
    await call.answer()

@dp.callback_query(F.data == "crash_cashout")
async def cb_crash_cashout(call: types.CallbackQuery):
    uid = str(call.from_user.id)
    if not crash_game["active"]:
        await call.answer("❌ Раунд уже завершён!", show_alert=True)
        return
    if uid not in crash_game["bets"]:
        await call.answer("❌ У тебя нет ставки!", show_alert=True)
        return
    if crash_game["bets"][uid]["cashed_out"]:
        await call.answer("❌ Уже забрано!", show_alert=True)
        return
    bet = crash_game["bets"][uid]
    multiplier = crash_game["multiplier"]
    won = int(bet["amount"] * multiplier)
    crash_game["bets"][uid]["cashed_out"] = True
    user = get_user(call.from_user.id)
    user["balance"] += won
    save_data()
    await call.answer(f"💰 x{multiplier:.2f} = +{won}⭐", show_alert=True)
    await call.message.edit_text(
        f"✅ Выигрыш забран!\n"
        f"💰 {bet['amount']}⭐ × {multiplier:.2f} = {won}⭐\n"
        f"💼 Баланс: {user['balance']}⭐",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К Крашу", callback_data="menu_crash")]
        ])
    )

async def arena_countdown(round_id, seconds):
    await asyncio.sleep(seconds)
    if round_id in arena_rounds and arena_rounds[round_id]["status"] == "waiting":
        if len(arena_rounds[round_id]["players"]) >= 2:
            await resolve_arena(round_id)

async def resolve_arena(round_id):
    if round_id not in arena_rounds:
        return
    round_data = arena_rounds[round_id]
    round_data["status"] = "finished"
    players = round_data["players"]
    if len(players) < 2:
        for uid, p in players.items():
            user = get_user(int(uid))
            user["balance"] += p["amount"]
            save_data()
        del arena_rounds[round_id]
        return
    total = sum(p["amount"] for p in players.values())
    r = random.uniform(0, total)
    cumulative = 0
    winner_uid = None
    for uid, p in players.items():
        cumulative += p["amount"]
        if r <= cumulative:
            winner_uid = uid
            break
    if not winner_uid:
        winner_uid = list(players.keys())[-1]
    winner = players[winner_uid]
    prize = int(total * 0.95)
    user = get_user(int(winner_uid))
    user["balance"] += prize
    save_data()
    result_text = (
        f"⚔️ Арена — Результат!\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"👑 Победитель: @{winner['username']}\n"
        f"💰 Приз: {prize}⭐ (из {total}⭐)\n\n"
        f"Участники:\n"
    )
    for uid, p in players.items():
        chance = int(p["amount"] / total * 100)
        result_text += f"• @{p['username']}: {p['amount']}⭐ ({chance}%)\n"
    for uid in players:
        try:
            await bot.send_message(int(uid), result_text, reply_markup=main_menu_kb())
        except:
            pass
    del arena_rounds[round_id]

@dp.callback_query(F.data == "menu_arena")
async def cb_menu_arena(call: types.CallbackQuery, state: FSMContext):
    user = get_user(call.from_user.id)
    active_round = None
    for round_id, round_data in arena_rounds.items():
        if round_data["status"] == "waiting":
            active_round = round_id
            break
    if active_round:
        players = arena_rounds[active_round]["players"]
        total = sum(p["amount"] for p in players.values())
        text = f"⚔️ Арена\n━━━━━━━━━━━━━━━━\n👥 Игроков: {len(players)}/5\n💰 Банк: {total}⭐\n\n"
        for uid, p in players.items():
            chance = int(p["amount"] / total * 100) if total > 0 else 0
            text += f"• @{p['username']}: {p['amount']}⭐ ({chance}%)\n"
    else:
        text = (
            f"⚔️ Арена\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"• До 5 игроков в раунде\n"
            f"• Больше ставка — выше шанс\n"
            f"• Победитель забирает 95% банка\n\n"
            f"💰 Твой баланс: {user['balance']}⭐"
        )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚔️ Войти в арену", callback_data="arena_join")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")],
    ])
    await call.message.edit_text(text, reply_markup=kb)

@dp.callback_query(F.data == "arena_join")
async def cb_arena_join(call: types.CallbackQuery, state: FSMContext):
    uid = str(call.from_user.id)
    for round_id, round_data in arena_rounds.items():
        if round_data["status"] == "waiting" and uid in round_data["players"]:
            await call.answer("❌ Ты уже в арене!", show_alert=True)
            return
    await state.set_state(ArenaBetState.amount)
    await call.message.answer("Введи сумму ставки для арены:")
    await call.answer()

@dp.callback_query(F.data == "admin_withdrawals")
async def cb_admin_withdrawals(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    pending = {k: v for k, v in pending_withdrawals.items() if v["status"] == "pending"}
    if not pending:
        await call.answer("Нет ожидающих заявок!", show_alert=True)
        return
    text = "📋 Заявки на вывод:\n━━━━━━━━━━━━━━━━\n"
    buttons = []
    for wd_id, wd in pending.items():
        text += f"\n🆔 {wd_id}\n👤 @{wd['username']}\n💰 {wd['amount']}⭐ → {wd['net']} Stars\n📬 {wd['address']}\n"
        buttons.append([
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"wd_approve_{wd_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"wd_reject_{wd_id}"),
        ])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("wd_approve_"))
async def cb_wd_approve(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    wd_id = call.data.replace("wd_approve_", "")
    if wd_id not in pending_withdrawals:
        await call.answer("Заявка не найдена!", show_alert=True)
        return
    wd = pending_withdrawals[wd_id]
    pending_withdrawals[wd_id]["status"] = "approved"
    save_data()
    await call.answer("✅ Одобрено!", show_alert=True)
    try:
        await bot.send_message(wd["user_id"], f"✅ Вывод одобрен!\n💰 {wd['net']} Stars отправлены на {wd['address']}")
    except:
        pass

@dp.callback_query(F.data.startswith("wd_reject_"))
async def cb_wd_reject(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    wd_id = call.data.replace("wd_reject_", "")
    if wd_id not in pending_withdrawals:
        await call.answer("Заявка не найдена!", show_alert=True)
        return
    wd = pending_withdrawals[wd_id]
    pending_withdrawals[wd_id]["status"] = "rejected"
    user = get_user(wd["user_id"])
    user["balance"] += wd["amount"]
    save_data()
    await call.answer("❌ Отклонено, средства возвращены!", show_alert=True)
    try:
        await bot.send_message(wd["user_id"], f"❌ Вывод отклонён.\n💰 {wd['amount']}⭐ возвращены на баланс.")
    except:
        pass

@dp.callback_query(F.data == "admin_users")
async def cb_admin_users(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    text = f"👥 Пользователи: {len(users)}\n━━━━━━━━━━━━━━━━\n"
    total_balance = sum(u["balance"] for u in users.values())
    text += f"💰 Суммарный баланс: {total_balance}⭐\n"
    for uid, u in list(users.items())[:10]:
        text += f"\n• @{u['username']} ({u['id']}): {u['balance']}⭐"
    if len(users) > 10:
        text += f"\n... и ещё {len(users)-10}"
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
    ]))

@dp.callback_query(F.data == "admin_stats")
async def cb_admin_stats(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    total_users = len(users)
    total_balance = sum(u["balance"] for u in users.values())
    pending_count = sum(1 for w in pending_withdrawals.values() if w["status"] == "pending")
    total_items = sum(len(u["inventory"]) for u in users.values())
    text = (
        f"📊 Статистика\n━━━━━━━━━━━━━━━━\n"
        f"👥 Пользователей: {total_users}\n"
        f"💰 Суммарный баланс: {total_balance}⭐\n"
        f"📤 Заявок на вывод: {pending_count}\n"
        f"🎒 Предметов: {total_items}\n"
        f"💥 Краш активен: {'Да' if crash_game['active'] else 'Нет'}\n"
        f"⚔️ Арен активно: {len(arena_rounds)}\n"
    )
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
    ]))

@dp.callback_query(F.data == "admin_panel")
async def cb_admin_panel(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID:
        return
    await call.message.edit_text("🛡 Панель администратора", reply_markup=admin_kb())

@dp.message(Command("approve"))
async def cmd_approve(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split("_", 1)
    if len(parts) < 2:
        return
    wd_id = parts[1]
    if wd_id not in pending_withdrawals:
        await message.answer("Заявка не найдена!")
        return
    wd = pending_withdrawals[wd_id]
    pending_withdrawals[wd_id]["status"] = "approved"
    save_data()
    await message.answer(f"✅ Заявка {wd_id} одобрена!")
    try:
        await bot.send_message(wd["user_id"], f"✅ Вывод {wd['net']} Stars одобрен!")
    except:
        pass

@dp.message(Command("reject"))
async def cmd_reject(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split("_", 1)
    if len(parts) < 2:
        return
    wd_id = parts[1]
    if wd_id not in pending_withdrawals:
        await message.answer("Заявка не найдена!")
        return
    wd = pending_withdrawals[wd_id]
    pending_withdrawals[wd_id]["status"] = "rejected"
    user = get_user(wd["user_id"])
    user["balance"] += wd["amount"]
    save_data()
    await message.answer(f"❌ Заявка {wd_id} отклонена!")
    try:
        await bot.send_message(wd["user_id"], f"❌ Вывод отклонён. {wd['amount']}⭐ возвращены.")
    except:
        pass

@dp.message(Command("addbalance"))
async def cmd_addbalance(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Использование: /addbalance <user_id> <amount>")
        return
    try:
        target_id = str(parts[1])
        amount = int(parts[2])
    except ValueError:
        await message.answer("Неверный формат!")
        return
    if target_id not in users:
        await message.answer("Пользователь не найден!")
        return
    users[target_id]["balance"] += amount
    save_data()
    await message.answer(f"✅ Начислено {amount}⭐ пользователю {target_id}")

async def main():
    load_data()
    asyncio.create_task(crash_loop())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
