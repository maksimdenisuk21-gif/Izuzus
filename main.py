# main.py - GiftUpgrader (UI в стиле референса + полный backend)
import os, hmac, hashlib, json, urllib.parse, random, time, uuid, asyncio, math
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from fastapi import FastAPI, Header, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import aiosqlite
import socketio

app = FastAPI(title="GiftUpgrader")
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
socket_app = socketio.ASGIApp(sio, app)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# ===== CONFIG =====
BOT_TOKEN = "8922972247:AAGbc4tYV51F3zxAGA3SuLcBY7PCyGRbXoE"
ADMIN_TG_ID = 7092015279
DB_NAME = "database.db"
HOUSE_EDGE = 0.05
TON_TO_STARS = 110

# ===== NFT NAMES =====
NFT_NAMES = [
    # Common / Uncommon tier
    "Astral Shard","B-Day Candle","Berry Box","Big Year","Bonded Ring","Bow Tie",
    "Bunny Muffin","Candy Cane","Cookie Heart","Crystal Ball","Cupid Charm",
    "Desk Calendar","Easter Egg","Eternal Candle","Flying Broom","Ginger Cookie",
    "Happy Brownie","Holiday Drink","Homemade Cake","Jack-in-the-Box","Jester Hat",
    "Jingle Bells","Jolly Chimp","Lol Pop","Love Candle","Lush Bouquet",
    "Mad Pumpkin","Moon Pendant","Party Sparkler","Pet Snake","Pool Float",
    "Record Player","Restless Jar","Rocket","Sakura Flower","Santa Hat",
    "Sleigh Bell","Snow Globe","Spiced Wine","Star Notepad","Tama Gadget",
    "Toy Bear","Whip Cupcake","Winter Wreath","Witch Hat","Xmas Stocking",
    # Rare / Epic
    "Artisan Brick","Diamond Ring","Dove of Peace","Electric Skull","Eternal Rose",
    "Evil Eye","Gem Signet","Genie Lamp","Hanging Star","Heart Locket",
    "Hex Pot","Input Key","Ion Gem","Jelly Bunny","Joyful Bundle",
    "Kissed Frog","Light Sword","Loot Bag","Love Potion","Low Rider",
    "Lunar Snake","Magic Potion","Mighty Arm","Nail Bracelet","Neko Helmet",
    "Onyx Black","Perfume Bottle","Precious Peach","Scared Cat","Sharp Tongue",
    "Signet Ring","Skull Flower","Snake Box","Snoop Cigar","Snoop Dogg",
    "Spy Agaric","Stellar Rocket","Swag Bag","Swiss Watch","Top Hat",
    "Trapped Heart","Trojan Horse","Vintage Cigar","Voodoo Doll","Westside Sign",
    # Legendary / Mythic
    "Durov's Cap","Heroic Helmet","Khabib's Papakha","Mini Oscar","Plush Pepe",
]

RARITY_COLORS = {
    "Common": {"color":"#8B8B8B","bg":"rgba(139,139,139,0.15)"},
    "Uncommon": {"color":"#4CAF50","bg":"rgba(76,175,80,0.15)"},
    "Rare": {"color":"#2196F3","bg":"rgba(33,150,243,0.15)"},
    "Epic": {"color":"#9C27B0","bg":"rgba(156,39,176,0.15)"},
    "Legendary": {"color":"#FFC107","bg":"rgba(255,193,7,0.15)"},
    "Mythic": {"color":"#F44336","bg":"rgba(244,67,54,0.15)"}
}

def get_emoji(name):
    m = {
        "Astral Shard":"💫","B-Day Candle":"🎂","Berry Box":"🫐","Big Year":"📅",
        "Bonded Ring":"💍","Bow Tie":"🎀","Bunny Muffin":"🐰","Candy Cane":"🍭",
        "Cookie Heart":"🍪","Crystal Ball":"🔮","Cupid Charm":"💘","Diamond Ring":"💎",
        "Durov's Cap":"🧢","Electric Skull":"⚡","Eternal Rose":"🌹","Flying Broom":"🧹",
        "Genie Lamp":"🪔","Ginger Cookie":"🍪","Heart Locket":"❤️","Heroic Helmet":"⛑️",
        "Hex Pot":"🧪","Holiday Drink":"🥂","Ion Gem":"💠","Jack-in-the-Box":"📦",
        "Jester Hat":"🎭","Khabib's Papakha":"🧢","Light Sword":"⚔️","Loot Bag":"💰",
        "Love Potion":"💗","Lunar Snake":"🐍","Magic Potion":"🧙","Mini Oscar":"🏆",
        "Moon Pendant":"🌙","Nail Bracelet":"📿","Neko Helmet":"🐱","Onyx Black":"🖤",
        "Perfume Bottle":"🧴","Plush Pepe":"🐸","Precious Peach":"🍑","Restless Jar":"🏺",
        "Rocket":"🚀","Santa Hat":"🎅","Scared Cat":"😱","Signet Ring":"💍",
        "Skull Flower":"💀","Snow Globe":"❄️","Spiced Wine":"🍷","Star Notepad":"📒",
        "Swiss Watch":"⌚","Top Hat":"🎩","Toy Bear":"🧸","Trapped Heart":"💔",
        "Vintage Cigar":"🚬","Voodoo Doll":"🪆","Witch Hat":"🧙","Xmas Stocking":"🧦",
        "Jelly Bunny":"🐰","Spy Agaric":"🍄","Kissed Frog":"🐸","Sharp Tongue":"👅",
        "Evil Eye":"👁️","Homemade Cake":"🍰","Jolly Chimp":"🐵","Desk Calendar":"📆",
        "Eternal Candle":"🕯️","Mighty Arm":"💪","Snoop Dogg":"🐕","Low Rider":"🚗",
        "Lol Pop":"🍭","Artisan Brick":"🧱","Westside Sign":"🪧","Gem Signet":"💠",
        "Easter Egg":"🥚","Pool Float":"🛟","Pet Snake":"🐍","Jingle Bells":"🔔",
        "Happy Brownie":"🍫","Winter Wreath":"🎄","Whip Cupcake":"🧁","Mad Pumpkin":"🎃",
        "Record Player":"🎙️","Hanging Star":"⭐","Tama Gadget":"📱","Snake Box":"📦",
        "Sakura Flower":"🌸","Party Sparkler":"✨","Lush Bouquet":"💐","Stellar Rocket":"🚀",
        "Sleigh Bell":"🔔","Love Candle":"🕯️","Joyful Bundle":"🎁","Input Key":"🔑",
        "Dove of Peace":"🕊️","Trojan Horse":"🐴","Swag Bag":"👜","Snoop Cigar":"🚬",
    }
    return m.get(name, "🎁")

def gift_short_name(name: str) -> str:
    """short_name для CDN картинок Telegram-подарков"""
    s = name.lower()
    for ch in ["'", "’", "-", "."]:
        s = s.replace(ch, "")
    s = "".join(c if c.isalnum() or c == " " else "" for c in s)
    return "_".join(s.split())

def gift_img_url(name: str) -> str:
    sn = gift_short_name(name)
    # публичный CDN с webp-картинками подарков
    return f"https://cdn.jsdelivr.net/gh/ssamy2/TG_Photos@main/webp/by_name/{sn}.webp"

def build_nft_gifts():
    """Все NFT_NAMES по редкостям. Мин. цена NFT ~80⭐ (дешёвых за 10 нет)."""
    gifts = {r: [] for r in ["Common","Uncommon","Rare","Epic","Legendary","Mythic"]}
    base = {
        "Common": 80, "Uncommon": 180, "Rare": 400,
        "Epic": 900, "Legendary": 2500, "Mythic": 12000
    }
    step = {
        "Common": 12, "Uncommon": 25, "Rare": 55,
        "Epic": 120, "Legendary": 350, "Mythic": 2500
    }
    order = ["Common","Uncommon","Rare","Epic","Legendary","Mythic"]
    buckets = {r: [] for r in order}
    for i, name in enumerate(NFT_NAMES):
        r = order[i % 6]
        if i < len(NFT_NAMES) * 0.55 and r in ("Legendary","Mythic"):
            r = order[i % 4]
        buckets[r].append(name)
    for r in order:
        for n, name in enumerate(buckets[r]):
            v = base[r] + n * step[r]
            gifts[r].append({
                "id": name.lower().replace(" ","_").replace("'",""),
                "name": name,
                "value": v,
                "emoji": get_emoji(name),
                "img": gift_img_url(name),
                "rarity": r
            })
    return gifts

NFT_GIFTS = build_nft_gifts()

# category: free | stars | nft | brands | only_nft | allin | rich
CASES = {
    # ===== FREE =====
    "free_daily": {
        "name": "🎁 FREE DAILY", "price": 0, "cooldown": 86400,
        "category": "free", "icon": "🎁", "color": "free",
        "rarities": ["Common"], "weights": [100],
        "min_stars": 1, "max_stars": 20, "stars_bias_low": True, "stars_chance": 1.0,
        "desc": "Раз в 24ч · чаще мало, иногда до 50⭐"
    },
    # ===== STARS =====
    "star_case_1": {
        "name": "⭐ STAR CASE I", "price": 50, "category": "stars",
        "icon": "⭐", "color": "c-starter",
        "star_drops": [5, 15, 25, 35, 40, 55, 70, 100],
        "star_weights": [28, 22, 16, 12, 9, 7, 4, 2],
        "desc": "8 дропов 5–100⭐"
    },
    "star_case_2": {
        "name": "⭐ STAR CASE II", "price": 100, "category": "stars",
        "icon": "✨", "color": "c-starter",
        "star_drops": [10, 25, 40, 60, 80, 120, 180, 250],
        "star_weights": [26, 22, 16, 12, 10, 7, 5, 2],
        "desc": "10–250⭐"
    },
    "star_case_3": {
        "name": "⭐ STAR CASE III", "price": 250, "category": "stars",
        "icon": "🌟", "color": "c-pepe",
        "star_drops": [25, 50, 100, 150, 200, 350, 500, 800],
        "star_weights": [24, 20, 16, 14, 10, 8, 5, 3],
        "desc": "25–800⭐"
    },
    "star_case_4": {
        "name": "⭐ STAR CASE IV", "price": 500, "category": "stars",
        "icon": "💫", "color": "c-tg",
        "star_drops": [50, 100, 200, 300, 450, 700, 1000, 1500],
        "star_weights": [22, 20, 16, 14, 12, 8, 5, 3],
        "desc": "50–1500⭐"
    },
    # ===== NFT =====
    "nft_starter": {
        "name": "🌱 NFT STARTER", "price": 200, "category": "nft",
        "icon": "🌱", "color": "c-starter",
        "rarities": ["Common", "Uncommon", "Rare"], "weights": [50, 35, 15],
        "min_stars": 15, "max_stars": 80, "stars_chance": 0.25,
        "desc": "Common–Rare + иногда ⭐"
    },
    "nft_candy": {
        "name": "🍭 CANDY NFT", "price": 350, "category": "nft",
        "icon": "🍭", "color": "c-pepe",
        "rarities": ["Uncommon", "Rare", "Epic"], "weights": [45, 40, 15],
        "min_stars": 30, "max_stars": 120, "stars_chance": 0.2,
        "desc": "Uncommon–Epic"
    },
    "nft_pepe": {
        "name": "🐸 PEPE BOX", "price": 600, "category": "nft",
        "icon": "🐸", "color": "c-pepe",
        "rarities": ["Rare", "Epic", "Legendary"], "weights": [50, 35, 15],
        "min_stars": 50, "max_stars": 200, "stars_chance": 0.18,
        "desc": "Шанс на Legendary"
    },
    "nft_magic": {
        "name": "🔮 MAGIC VAULT", "price": 900, "category": "nft",
        "icon": "🔮", "color": "c-frag",
        "rarities": ["Rare", "Epic", "Legendary"], "weights": [40, 40, 20],
        "min_stars": 80, "max_stars": 300, "stars_chance": 0.15,
        "desc": "Magic & potions"
    },
    # ===== BRANDS =====
    "brand_gucci": {
        "name": "👜 GUCCI DROP", "price": 800, "category": "brands",
        "icon": "👜", "color": "c-tg",
        "rarities": ["Epic", "Legendary"], "weights": [70, 30],
        "min_stars": 100, "max_stars": 400, "stars_chance": 0.22,
        "force_names": ["Swag Bag", "Perfume Bottle", "Top Hat", "Swiss Watch", "Diamond Ring", "Nail Bracelet"],
        "desc": "Бренд-стиль · сумки, часы, духи"
    },
    "brand_rolex": {
        "name": "⌚ ROLEX CASE", "price": 1200, "category": "brands",
        "icon": "⌚", "color": "c-frag",
        "rarities": ["Epic", "Legendary", "Mythic"], "weights": [55, 35, 10],
        "min_stars": 150, "max_stars": 500, "stars_chance": 0.2,
        "force_names": ["Swiss Watch", "Signet Ring", "Diamond Ring", "Gem Signet", "Top Hat", "Vintage Cigar"],
        "desc": "Часы и люкс"
    },
    "brand_snoop": {
        "name": "🐕 SNOOP DROP", "price": 700, "category": "brands",
        "icon": "🐕", "color": "c-pepe",
        "rarities": ["Rare", "Epic", "Legendary"], "weights": [45, 40, 15],
        "min_stars": 80, "max_stars": 350, "stars_chance": 0.2,
        "force_names": ["Snoop Dogg", "Snoop Cigar", "Low Rider", "Vintage Cigar", "Swag Bag", "Westside Sign"],
        "desc": "Snoop collab vibes"
    },
    # ===== ONLY NFT =====
    "only_onyx": {
        "name": "🖤 ONYX BLACK", "price": 1500, "category": "only_nft",
        "icon": "🖤", "color": "c-durov",
        "rarities": ["Epic", "Legendary"], "weights": [55, 45],
        "stars_chance": 0.0,
        "force_names": ["Onyx Black", "Electric Skull", "Skull Flower", "Voodoo Doll", "Evil Eye", "Neko Helmet", "Heroic Helmet"],
        "desc": "Только NFT · тёмный стиль"
    },
    "only_crystal": {
        "name": "💎 CRYSTAL VAULT", "price": 2000, "category": "only_nft",
        "icon": "💎", "color": "c-frag",
        "rarities": ["Epic", "Legendary", "Mythic"], "weights": [50, 35, 15],
        "stars_chance": 0.0,
        "force_names": ["Astral Shard", "Ion Gem", "Crystal Ball", "Gem Signet", "Diamond Ring", "Perfume Bottle", "Mini Oscar"],
        "desc": "Только NFT · кристаллы и гемы"
    },
    "only_durov": {
        "name": "🧢 DUROV ONLY", "price": 3500, "category": "only_nft",
        "icon": "🧢", "color": "c-durov",
        "rarities": ["Legendary", "Mythic"], "weights": [65, 35],
        "stars_chance": 0.0,
        "force_names": ["Durov's Cap", "Heroic Helmet", "Plush Pepe", "Khabib's Papakha", "Mini Oscar", "Precious Peach"],
        "desc": "Топ-коллекции"
    },
    # ===== ALL-IN =====
    "allin_pepe": {
        "name": "🐸 ALL-IN PEPE", "price": 40, "category": "allin",
        "icon": "🐸", "color": "c-pepe",
        "allin": True,
        "lose_stars": [0, 1, 2, 3, 5],
        "lose_weights": [50, 25, 15, 7, 3],
        "jackpot_name": "Plush Pepe", "jackpot_value": 1000000, "jackpot_chance": 0.0,
        "desc": "40⭐ · 99.99% ничего · Pepe не падает"
    },
    "allin_rolex": {
        "name": "⌚ ALL-IN ROLEX", "price": 25, "category": "allin",
        "icon": "⌚", "color": "c-frag",
        "allin": True,
        "lose_stars": [0, 1, 2],
        "lose_weights": [70, 20, 10],
        "jackpot_name": "Swiss Watch", "jackpot_value": 50000, "jackpot_chance": 0.00001,
        "desc": "25⭐ · микрошанс на Rolex"
    },
    "allin_cap": {
        "name": "🧢 ALL-IN CAP", "price": 60, "category": "allin",
        "icon": "🧢", "color": "c-durov",
        "allin": True,
        "lose_stars": [0, 1, 2, 5],
        "lose_weights": [55, 25, 12, 8],
        "jackpot_name": "Durov's Cap", "jackpot_value": 200000, "jackpot_chance": 0.000005,
        "desc": "60⭐ · микрошанс на Cap"
    },
    "allin_helmet": {
        "name": "⛑️ ALL-IN HELMET", "price": 80, "category": "allin",
        "icon": "⛑️", "color": "c-durov",
        "allin": True,
        "lose_stars": [0, 1, 3],
        "lose_weights": [60, 25, 15],
        "jackpot_name": "Heroic Helmet", "jackpot_value": 150000, "jackpot_chance": 0.000008,
        "desc": "80⭐ · микрошанс на Helmet"
    },
    # ===== RICH =====
    "rich_gold": {
        "name": "👑 GOLD RICH", "price": 1200, "category": "rich",
        "icon": "👑", "color": "c-durov",
        "rarities": ["Epic", "Legendary", "Mythic"], "weights": [45, 40, 15],
        "min_stars": 200, "max_stars": 800, "stars_chance": 0.12,
        "desc": "От 1200⭐ · жирные дропы"
    },
    "rich_diamond": {
        "name": "💎 DIAMOND RICH", "price": 2500, "category": "rich",
        "icon": "💎", "color": "c-frag",
        "rarities": ["Legendary", "Mythic"], "weights": [60, 40],
        "min_stars": 400, "max_stars": 1500, "stars_chance": 0.1,
        "desc": "Legendary + Mythic"
    },
    "rich_mythic": {
        "name": "☄️ MYTHIC RICH", "price": 5000, "category": "rich",
        "icon": "☄️", "color": "c-durov",
        "rarities": ["Legendary", "Mythic"], "weights": [40, 60],
        "min_stars": 800, "max_stars": 3000, "stars_chance": 0.08,
        "desc": "Топ-тир"
    },
    "rich_durov": {
        "name": "🔥 DUROV RICH", "price": 8000, "category": "rich",
        "icon": "🔥", "color": "c-durov",
        "rarities": ["Mythic"], "weights": [100],
        "min_stars": 1500, "max_stars": 8000, "stars_chance": 0.05,
        "force_names": ["Plush Pepe", "Durov's Cap", "Heroic Helmet", "Khabib's Papakha", "Mini Oscar", "Precious Peach"],
        "desc": "Только Mythic коллекция"
    },
}

# ===== MODELS =====
class UpgradeRequest(BaseModel): item_index:int; target_value:int
class CaseOpenRequest(BaseModel): case_id:str
class SellItemRequest(BaseModel): item_index:int
class MinesStartRequest(BaseModel): bet:int; mines:int
class MinesOpenRequest(BaseModel): game_id:str; cell:int
class MinesCashoutRequest(BaseModel): game_id:str
class AdminGiveRequest(BaseModel): user_id:int; amount:int
class WithdrawRequest(BaseModel): amount:int; wallet:str
class PromoCreateRequest(BaseModel): code:str; reward_type:str; case_id:str=None; stars:int=0; max_uses:int=1
class AdminWithdrawStatusRequest(BaseModel): withdraw_id:int; status:str
class DepositRequest(BaseModel): amount:int
class DepositConfirmRequest(BaseModel): payload:str

# LIVE wins feed (in-memory, last 40)
LIVE_WINS: List[dict] = []

def push_live(item: dict, username: str = "Player"):
    LIVE_WINS.insert(0, {
        "name": item.get("name", "Gift"),
        "emoji": item.get("emoji", "🎁"),
        "img": item.get("img", ""),
        "value": item.get("value", 0),
        "rarity": item.get("rarity", ""),
        "user": username[:16],
        "ts": time.time()
    })
    if len(LIVE_WINS) > 40:
        del LIVE_WINS[40:]

# ===== DB =====
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS users (tg_id INTEGER PRIMARY KEY, username TEXT DEFAULT 'Player', balance INTEGER DEFAULT 50, total_spent INTEGER DEFAULT 0, total_deposited INTEGER DEFAULT 0, inventory TEXT DEFAULT '[]', games_played INTEGER DEFAULT 0, wins INTEGER DEFAULT 0, cases_opened INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        # migrations for older DBs
        for col, typ in [("total_deposited","INTEGER DEFAULT 0"),("cases_opened","INTEGER DEFAULT 0")]:
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} {typ}")
            except Exception:
                pass
        await db.execute("CREATE TABLE IF NOT EXISTS withdrawals (id INTEGER PRIMARY KEY AUTOINCREMENT, tg_id INTEGER, amount INTEGER, wallet TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("CREATE TABLE IF NOT EXISTS referrals (user_id INTEGER PRIMARY KEY, referrer_id INTEGER NOT NULL, total_earned INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("CREATE TABLE IF NOT EXISTS referral_earnings (id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id INTEGER NOT NULL, referral_id INTEGER NOT NULL, deposit_amount INTEGER NOT NULL, earned INTEGER NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("CREATE TABLE IF NOT EXISTS promocodes (code TEXT PRIMARY KEY, reward_type TEXT, case_id TEXT, stars INTEGER DEFAULT 0, max_uses INTEGER DEFAULT 1, uses INTEGER DEFAULT 0, created_by INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("CREATE TABLE IF NOT EXISTS promo_uses (user_id INTEGER, promo_code TEXT, used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (user_id, promo_code))")
        await db.execute("CREATE TABLE IF NOT EXISTS free_case_cooldowns (user_id INTEGER PRIMARY KEY, last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("CREATE TABLE IF NOT EXISTS admin_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER, action TEXT, details TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await db.commit()

async def get_user(tg_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT balance,total_spent,inventory,games_played,wins FROM users WHERE tg_id=?", (tg_id,)) as c:
            r = await c.fetchone()
            if r: return {"balance": r[0], "total_spent": r[1], "inventory": json.loads(r[2]), "games_played": r[3], "wins": r[4]}
            await db.execute("INSERT INTO users (tg_id, balance, inventory) VALUES (?, 50, '[]')", (tg_id,)); await db.commit()
            return {"balance": 50, "total_spent": 0, "inventory": [], "games_played": 0, "wins": 0}

async def log_admin_action(admin_id, action, details=""):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO admin_logs (admin_id, action, details) VALUES (?, ?, ?)", (admin_id, action, details))
        await db.commit()

# ===== AUTH =====
# DEV_MODE=1 — мягкий auth (удобно с Netlify); 0 = строгий hash Telegram
DEV_MODE = os.getenv("DEV_MODE", "1") == "1"
PUBLIC_URL = os.getenv("PUBLIC_URL", "https://izuzus-2.onrender.com").rstrip("/")
PENDING_DEPOSITS: Dict[str, dict] = {}  # invoice payload -> {tg_id, amount}

def tg_api(method: str, data: dict) -> dict:
    """Вызов Telegram Bot API"""
    import urllib.request
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8"))

def _parse_init_data(authorization: str):
    data = urllib.parse.parse_qs(authorization)
    h = data.get("hash", [None])[0]
    if not h:
        return None
    sd = sorted([f"{k}={v[0]}" for k, v in data.items() if k != "hash"])
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    if hmac.new(secret, "\n".join(sd).encode(), hashlib.sha256).hexdigest() != h:
        return None
    return json.loads(data.get("user", ["{}"])[0])

def verify_telegram(
    authorization: str = Header(None),
    x_telegram_init_data: str = Header(None, alias="X-Telegram-Init-Data"),
):
    """Auth из Mini App. Поддерживает Authorization и X-Telegram-Init-Data (Netlify/прокси)."""
    raw = authorization or x_telegram_init_data
    if not raw:
        # без Telegram — demo только если DEV_MODE
        if DEV_MODE:
            return {"id": 100001, "first_name": "Demo", "username": "demo"}
        raise HTTPException(401, "Open inside Telegram Mini App")
    if raw.strip().lower() in ("dev", "demo"):
        if DEV_MODE:
            return {"id": 100001, "first_name": "Demo", "username": "demo"}
        raise HTTPException(401, "DEV disabled")
    if not BOT_TOKEN:
        raise HTTPException(401)
    try:
        user = _parse_init_data(raw)
        if user and user.get("id"):
            return user
        # если hash не сошёлся — возможно старый токен; в DEV пускаем
        if DEV_MODE:
            try:
                data = urllib.parse.parse_qs(raw)
                u = json.loads(data.get("user", ["{}"])[0])
                if u.get("id"):
                    return u
            except Exception:
                pass
            return {"id": 100001, "first_name": "Demo", "username": "demo"}
        raise HTTPException(401, "Invalid Telegram auth")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "Auth error")

def verify_admin(user=Depends(verify_telegram)):
    if user['id'] != ADMIN_TG_ID: raise HTTPException(403)
    return user

# ===== HELPERS =====
def calc_upgrade_chance(in_val, target):
    return max(1, min(85, (in_val/target)*100*(1-HOUSE_EDGE)))

def calc_mines_multiplier(mines, opened):
    total, safe = 25, 25-mines
    if opened >= safe: return round((1-HOUSE_EDGE)*100, 2)
    p = 1.0
    for i in range(opened): p *= (safe-i)/(total-i)
    caps = {1:5, 3:15, 5:40, 10:150, 15:500, 20:1500, 24:3000}
    return round(min((1-HOUSE_EDGE)/p, caps[min(caps.keys(), key=lambda k: abs(k-mines))]), 2)

# ===== CRASH =====
CRASH_MIN_BET, CRASH_MAX_BET = 25, 5000
CRASH_BETTING_TIME, CRASH_COOLDOWN, CRASH_SPEED = 6, 3, 0.08
SERVER_SEED = os.getenv("CRASH_SERVER_SEED", str(uuid.uuid4()))
crash_nonce = 0
crash_state = {"status":"waiting","round_id":"","crash_point":1.0,"hash":"","bets":{},"history":[],"timer_ends":0,"multiplier":1.0}

def gen_crash_point():
    global crash_nonce
    crash_nonce += 1
    h = int(hashlib.sha256(f"{SERVER_SEED}:{crash_nonce}".encode()).hexdigest()[:16], 16) / (2**64)
    if h < 0.30: cp = 1.01 + (h/0.30)*0.09
    elif h < 0.60: cp = 1.10 + ((h-0.30)/0.30)*0.20
    elif h < 0.82: cp = 1.30 + ((h-0.60)/0.22)*0.50
    elif h < 0.94: cp = 1.80 + ((h-0.82)/0.12)*1.20
    elif h < 0.98: cp = 3.00 + ((h-0.94)/0.04)*5.00
    elif h < 0.995: cp = 8.00 + ((h-0.98)/0.015)*12.00
    else: cp = 20.00 + ((h-0.995)/0.005)*30.00
    return round(min(cp, 50), 2)

async def crash_loop():
    while True:
        crash_state.update({"status":"betting","round_id":str(uuid.uuid4())[:8],"bets":{},"crash_point":gen_crash_point(),"multiplier":1.0,"timer_ends":time.time()+CRASH_BETTING_TIME})
        await sio.emit("crash_state", {"status":"betting","round_id":crash_state["round_id"],"timer":CRASH_BETTING_TIME})
        await asyncio.sleep(CRASH_BETTING_TIME)
        if not crash_state["bets"]:
            await sio.emit("crash_state", {"status":"cooldown","timer":CRASH_COOLDOWN})
            await asyncio.sleep(CRASH_COOLDOWN)
            continue
        crash_state["status"]="flying"
        st = time.time()
        await sio.emit("crash_start", {"round_id":crash_state["round_id"]})
        while True:
            cur = 1.0 * math.exp(CRASH_SPEED * (time.time()-st))
            crash_state["multiplier"] = cur
            if cur >= crash_state["crash_point"]:
                crash_state["status"]="crashed"
                crash_state["history"].insert(0, crash_state["crash_point"])
                if len(crash_state["history"]) > 20: crash_state["history"] = crash_state["history"][:20]
                results = [{"username":b["username"],"amount":b["amount"],"cashed":b.get("cashed",False),"win":int(b["amount"]*b.get("cashed_at",1)) if b.get("cashed") else 0} for b in crash_state["bets"].values()]
                await sio.emit("crash_end", {"crash_point":crash_state["crash_point"],"bets":results})
                break
            await sio.emit("crash_multiplier", {"multiplier":round(cur,2)})
            await asyncio.sleep(0.05)
        crash_state["status"]="cooldown"
        await sio.emit("crash_state", {"status":"cooldown","timer":CRASH_COOLDOWN,"history":crash_state["history"][:10]})
        await asyncio.sleep(CRASH_COOLDOWN)

@sio.event
async def connect(sid, env):
    await sio.emit("crash_state", {"status":crash_state["status"],"round_id":crash_state["round_id"],"history":crash_state["history"][:10]}, to=sid)

@sio.event
async def place_bet(sid, data):
    if crash_state["status"] != "betting":
        return await sio.emit("error", {"message":"Bets closed"}, to=sid)
    tg_id = data.get("tg_id", 0)
    amount = data.get("amount", 0)
    if not tg_id or amount < CRASH_MIN_BET or amount > CRASH_MAX_BET:
        return await sio.emit("error", {"message":"Invalid bet"}, to=sid)
    u = await get_user(tg_id)
    if u["balance"] < amount:
        return await sio.emit("error", {"message":"Insufficient balance"}, to=sid)
    key = f"{tg_id}:{crash_state['round_id']}"
    if key in crash_state["bets"]:
        return await sio.emit("error", {"message":"Already placed"}, to=sid)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance=balance-?, games_played=games_played+1 WHERE tg_id=?", (amount, tg_id))
        await db.commit()
    crash_state["bets"][key] = {"tg_id":tg_id, "amount":amount, "username":data.get("username","Player"), "cashed":False, "cashed_at":0}
    await sio.emit("bet_placed", {"username":data.get("username","Player"), "amount":amount, "balance":(await get_user(tg_id))["balance"]})

@sio.event
async def cashout(sid, data):
    if crash_state["status"] != "flying":
        return await sio.emit("error", {"message":"Not flying"}, to=sid)
    tg_id = data.get("tg_id", 0)
    key = f"{tg_id}:{crash_state['round_id']}"
    bet = crash_state["bets"].get(key)
    if not bet or bet["cashed"]:
        return await sio.emit("error", {"message":"No bet"}, to=sid)
    win = int(bet["amount"] * crash_state["multiplier"])
    bet["cashed"] = True
    bet["cashed_at"] = crash_state["multiplier"]
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance=balance+?, wins=wins+1 WHERE tg_id=?", (win, tg_id))
        await db.commit()
    await sio.emit("cashout_success", {"username":bet["username"], "win":win, "balance":(await get_user(tg_id))["balance"]})

# ===== HTML (UI как на референсах) =====
HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>GiftUpgrader</title>
<script src="https://cdn.socket.io/4.5.0/socket.io.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
:root{
  --bg:#0b0f1a;
  --card:#12182a;
  --card2:#161d32;
  --border:rgba(255,255,255,0.06);
  --text:#e8ecf4;
  --muted:#7a8699;
  --accent:#3b82f6;
  --gold:#f5c542;
  --green:#22c55e;
  --red:#ef4444;
  --purple:#a855f7;
}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;min-height:100vh;overflow-x:hidden}
.app{max-width:480px;margin:0 auto;min-height:100vh;position:relative;padding-bottom:20px}

/* TOP BAR */
.topbar{display:flex;align-items:center;justify-content:space-between;padding:10px 14px 6px;position:sticky;top:0;z-index:50;background:rgba(11,15,26,0.92);backdrop-filter:blur(12px)}
.topbar-left{display:flex;align-items:center;gap:8px}
.close-btn{width:32px;height:32px;border-radius:10px;border:1px solid var(--border);background:var(--card);color:var(--muted);font-size:16px;display:flex;align-items:center;justify-content:center;cursor:pointer}
.logo-icon{width:36px;height:36px;border-radius:12px;background:linear-gradient(135deg,#3b82f6,#8b5cf6);display:flex;align-items:center;justify-content:center;font-size:18px}
.balance-pill{display:flex;align-items:center;gap:6px;background:var(--card);border:1px solid var(--border);border-radius:20px;padding:5px 10px 5px 8px;font-size:13px;font-weight:600}
.bal-item{display:flex;align-items:center;gap:3px}
.bal-item .ic{font-size:14px}
.add-bal{width:22px;height:22px;border-radius:50%;background:linear-gradient(135deg,#3b82f6,#6366f1);border:none;color:#fff;font-size:14px;display:flex;align-items:center;justify-content:center;cursor:pointer;margin-left:2px}
.avatar{width:32px;height:32px;border-radius:50%;background:linear-gradient(135deg,#a855f7,#ec4899);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:13px;color:#fff;cursor:pointer}

/* NAV TABS */
.nav{display:flex;gap:2px;padding:4px 10px 10px;overflow-x:auto;scrollbar-width:none;-ms-overflow-style:none}
.nav::-webkit-scrollbar{display:none}
.nav-item{flex-shrink:0;padding:8px 12px;border-radius:12px;border:none;background:transparent;color:var(--muted);font-size:12px;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:5px;transition:.2s;white-space:nowrap}
.nav-item.active{background:linear-gradient(135deg,#3b82f6,#6366f1);color:#fff;box-shadow:0 4px 16px rgba(59,130,246,0.35)}
.nav-item .ico{font-size:14px}

/* CONTENT */
.page{display:none;padding:0 12px 20px;animation:fade .25s ease}
.page.active{display:block}
@keyframes fade{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}

/* LIVE BAR */
.live-bar{display:flex;align-items:center;gap:8px;background:var(--card);border:1px solid var(--border);border-radius:14px;padding:8px 10px;margin-bottom:12px;overflow:hidden}
.live-dot{width:8px;height:8px;border-radius:50%;background:#22c55e;box-shadow:0 0 8px #22c55e;flex-shrink:0;animation:pulse 1.5s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
.live-label{font-size:11px;color:var(--muted);font-weight:600;flex-shrink:0}
.live-scroll{display:flex;gap:6px;overflow-x:auto;scrollbar-width:none;flex:1}
.live-scroll::-webkit-scrollbar{display:none}
.live-item{width:40px;height:40px;border-radius:10px;background:var(--card2);border:1px solid var(--border);display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0;overflow:hidden}
.live-item img{width:32px;height:32px;object-fit:contain}

/* SECTION TITLE */
.sec-title{font-size:15px;font-weight:700;margin:14px 0 10px;display:flex;align-items:center;justify-content:space-between}
.sec-title span{color:var(--muted);font-size:12px;font-weight:500}

/* CASE CARDS */
.cases-row{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:8px}
.case-card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:0 0 12px;text-align:center;cursor:pointer;transition:.2s;position:relative;overflow:hidden}
.case-card:active{transform:scale(.97)}
.case-card .case-visual{height:110px;display:flex;align-items:center;justify-content:center;position:relative;background:radial-gradient(ellipse at 50% 30%,rgba(59,130,246,0.18),transparent 70%)}
.case-card .case-visual .case-emoji{font-size:48px;filter:drop-shadow(0 6px 16px rgba(0,0,0,0.45));z-index:1}
.case-card .case-visual .case-box{position:absolute;inset:12px 18px 8px;border-radius:12px;border:1px solid rgba(255,255,255,0.08);background:linear-gradient(160deg,rgba(255,255,255,0.06),rgba(0,0,0,0.15));pointer-events:none}
.case-card .case-name{font-size:12px;font-weight:700;margin:6px 8px 4px;letter-spacing:.3px}
.case-card .case-price{display:inline-flex;align-items:center;gap:3px;background:rgba(59,130,246,0.15);color:#60a5fa;font-size:12px;font-weight:700;padding:3px 10px;border-radius:8px}
.case-card .case-badge{position:absolute;top:8px;right:8px;background:rgba(0,0,0,0.55);border-radius:8px;padding:2px 6px;font-size:10px;color:var(--muted);z-index:2}
.case-card.free .case-price{background:rgba(34,197,94,0.15);color:#4ade80}
.case-card.free .case-visual{background:radial-gradient(ellipse at 50% 30%,rgba(34,197,94,0.2),transparent 70%)}
.case-card.promo{border-color:rgba(168,85,247,0.25)}
.case-card.c-starter .case-visual{background:radial-gradient(ellipse at 50% 30%,rgba(59,130,246,0.25),transparent 70%)}
.case-card.c-pepe .case-visual{background:radial-gradient(ellipse at 50% 30%,rgba(34,197,94,0.25),transparent 70%)}
.case-card.c-tg .case-visual{background:radial-gradient(ellipse at 50% 30%,rgba(168,85,247,0.25),transparent 70%)}
.case-card.c-frag .case-visual{background:radial-gradient(ellipse at 50% 30%,rgba(245,197,66,0.22),transparent 70%)}
.case-card.c-durov .case-visual{background:radial-gradient(ellipse at 50% 30%,rgba(239,68,68,0.22),transparent 70%)}

/* UPGRADE + WHEEL */
.upg-layout{display:grid;grid-template-columns:56px 1fr;gap:10px;align-items:start;margin-bottom:12px}
.upg-side{display:flex;flex-direction:column;gap:8px;padding-top:18px}
.chance-preset{width:56px;padding:10px 0;border-radius:12px;border:1px solid var(--border);background:var(--card);color:var(--muted);font-size:11px;font-weight:700;cursor:pointer;text-align:center;transition:.15s}
.chance-preset:active{transform:scale(.95)}
.chance-preset.active{background:rgba(59,130,246,0.2);border-color:#3b82f6;color:#60a5fa}
.upg-main{display:flex;flex-direction:column;align-items:center}
.wheel-wrap{position:relative;width:200px;height:200px;margin:4px auto 12px}
.wheel{width:200px;height:200px;border-radius:50%;position:relative;transition:transform 0s;box-shadow:0 0 0 4px rgba(255,255,255,0.06),0 0 32px rgba(0,0,0,0.4);overflow:hidden}
.wheel.spinning{transition:transform 4.2s cubic-bezier(0.12,0.75,0.08,1)}
.wheel-seg-label{position:absolute;inset:0;pointer-events:none;display:flex;align-items:center;justify-content:center}
.wheel-center{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);width:72px;height:72px;border-radius:50%;background:var(--card);border:2px solid rgba(255,255,255,0.1);display:flex;flex-direction:column;align-items:center;justify-content:center;z-index:3;box-shadow:0 4px 16px rgba(0,0,0,0.35)}
.wheel-center .pct{font-size:18px;font-weight:800;line-height:1}
.wheel-center .lbl{font-size:8px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-top:2px}
.wheel-pointer{position:absolute;top:-6px;left:50%;transform:translateX(-50%);z-index:5;width:0;height:0;border-left:10px solid transparent;border-right:10px solid transparent;border-top:16px solid var(--gold);filter:drop-shadow(0 2px 4px rgba(0,0,0,.5))}
.upg-slots-row{display:grid;grid-template-columns:1fr 1fr;gap:8px;width:100%;margin-bottom:10px}
.upg-slot{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:12px 8px;text-align:center;min-height:100px;display:flex;flex-direction:column;align-items:center;justify-content:center;cursor:pointer;transition:.2s}
.upg-slot:active{transform:scale(.98)}
.upg-slot .plus{width:36px;height:36px;border-radius:50%;background:rgba(255,255,255,0.04);border:1px dashed rgba(255,255,255,0.12);display:flex;align-items:center;justify-content:center;font-size:20px;color:var(--muted);margin-bottom:6px}
.upg-slot .hint{font-size:10px;color:var(--muted);line-height:1.3;max-width:100px}
.upg-slot.filled .emoji{font-size:28px;margin-bottom:2px}
.upg-slot.filled .name{font-size:10px;font-weight:600;margin-bottom:2px}
.upg-slot.filled .val{font-size:10px;color:var(--gold)}
.upg-slot.filled .gift-thumb{width:48px;height:48px;margin-bottom:4px;object-fit:contain}
.mult-row{display:flex;gap:8px;width:100%;margin-bottom:10px}
.mult-btn{flex:1;padding:12px 0;border-radius:12px;border:1px solid var(--border);background:var(--card);color:var(--text);font-size:14px;font-weight:800;cursor:pointer;transition:.15s}
.mult-btn:active{transform:scale(.96)}
.mult-btn.active{background:linear-gradient(135deg,#3b82f6,#6366f1);border-color:transparent;color:#fff;box-shadow:0 4px 14px rgba(59,130,246,0.35)}
.upg-btn{width:100%;padding:14px;border:none;border-radius:14px;font-size:15px;font-weight:700;background:linear-gradient(135deg,#3b82f6,#6366f1);color:#fff;cursor:pointer;box-shadow:0 6px 20px rgba(59,130,246,0.35);transition:.2s;margin-top:2px}
.upg-btn:active{transform:scale(.97)}
.upg-btn:disabled{opacity:.45;cursor:not-allowed;box-shadow:none}
.upg-stats{display:flex;justify-content:center;gap:28px;margin:6px 0 8px}
.upg-stats .st{text-align:center}
.upg-stats .st .l{font-size:10px;color:var(--muted);text-transform:uppercase}
.upg-stats .st .v{font-size:16px;font-weight:700}
.upg-stats .st .v.gold{color:var(--gold)}
.upg-stats .st .v.blue{color:#60a5fa}
.upg-stats .st .v.green{color:#4ade80}
.upg-stats .st .v.red{color:#f87171}

/* INV / TARGET LISTS under upgrade */
/* MINES */
.mines-controls{display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap}
.mines-controls input{flex:1;min-width:70px;padding:10px 12px;background:var(--card);border:1px solid var(--border);border-radius:12px;color:var(--text);font-size:14px;font-weight:600}
.mines-controls input:focus{outline:none;border-color:#3b82f6}
.mines-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:6px;max-width:320px;margin:0 auto 12px}
.mine-cell{aspect-ratio:1;background:var(--card);border:1px solid var(--border);border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:20px;cursor:pointer;transition:.15s;color:var(--muted)}
.mine-cell:active{transform:scale(.92)}
.mine-cell.opened{background:rgba(34,197,94,0.12);border-color:rgba(34,197,94,0.25);color:#4ade80}
.mine-cell.bomb{background:rgba(239,68,68,0.15);border-color:rgba(239,68,68,0.3);color:#f87171}
.mines-bar{display:flex;align-items:center;justify-content:space-between;background:var(--card);border:1px solid var(--border);border-radius:14px;padding:10px 14px;font-size:13px}
.mines-bar strong{color:var(--gold)}
.btn-sm{padding:8px 16px;border:none;border-radius:10px;font-size:12px;font-weight:700;cursor:pointer;background:linear-gradient(135deg,#3b82f6,#6366f1);color:#fff}
.btn-sm.green{background:linear-gradient(135deg,#22c55e,#16a34a)}
.btn-sm.gold{background:linear-gradient(135deg,#f5c542,#f59e0b);color:#0b0f1a}
.btn-sm:disabled{opacity:.4}

/* CRASH / GAMES */
.crash-box{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:14px;margin-bottom:10px}
.crash-mult{font-size:42px;font-weight:800;text-align:center;color:var(--gold);letter-spacing:-1px}
.crash-status{text-align:center;font-size:12px;color:var(--muted);margin:4px 0 12px}
.crash-row{display:flex;gap:8px}
.crash-row input{flex:1;padding:12px;background:var(--card2);border:1px solid var(--border);border-radius:12px;color:var(--text);font-size:14px;font-weight:600}
.crash-row input:focus{outline:none;border-color:#3b82f6}
.crash-bets{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.crash-bets span{font-size:11px;padding:3px 8px;border-radius:8px;background:var(--card2);color:var(--muted)}
.mult-pills{display:flex;gap:6px;overflow-x:auto;padding:8px 0;scrollbar-width:none}
.mult-pills::-webkit-scrollbar{display:none}
.mult-pill{flex-shrink:0;padding:6px 12px;border-radius:10px;background:var(--card);border:1px solid var(--border);font-size:12px;font-weight:700;color:var(--gold)}
.mult-pill.active{background:rgba(245,197,66,0.15);border-color:rgba(245,197,66,0.4)}

/* PROFILE */
.profile-card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:16px;margin-bottom:12px}
.profile-head{display:flex;align-items:center;gap:12px;margin-bottom:14px}
.profile-av{width:52px;height:52px;border-radius:50%;background:linear-gradient(135deg,#a855f7,#ec4899);display:flex;align-items:center;justify-content:center;font-size:22px;font-weight:800;color:#fff}
.profile-name{font-size:16px;font-weight:700}
.profile-id{font-size:12px;color:var(--muted)}
.stats-row{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px}
.stat-box{background:var(--card2);border-radius:12px;padding:10px;text-align:center}
.stat-box .n{font-size:16px;font-weight:700;color:var(--gold)}
.stat-box .l{font-size:10px;color:var(--muted);margin-top:2px}
.promo-row{display:flex;gap:8px;margin-top:10px}
.promo-row input{flex:1;padding:11px 12px;background:var(--card2);border:1px solid var(--border);border-radius:12px;color:var(--text);font-size:13px;text-transform:uppercase}
.promo-row input:focus{outline:none;border-color:#3b82f6}
.promo-row button,.btn-full{padding:11px 16px;border:none;border-radius:12px;font-size:13px;font-weight:700;background:linear-gradient(135deg,#3b82f6,#6366f1);color:#fff;cursor:pointer}
.btn-full{width:100%;margin-top:10px}
.btn-danger{background:rgba(239,68,68,0.12);color:#f87171;border:1px solid rgba(239,68,68,0.2)}
.ref-box{background:var(--card2);border-radius:12px;padding:12px;margin-top:10px;text-align:center}
.ref-box .ref-link{font-size:11px;color:var(--muted);word-break:break-all;margin:6px 0;padding:8px;background:rgba(0,0,0,0.25);border-radius:8px}

/* TOAST */
.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);z-index:999;width:90%;max-width:360px;padding:12px 16px;border-radius:14px;background:rgba(18,24,42,0.96);border:1px solid var(--border);text-align:center;font-size:13px;font-weight:600;display:none;animation:toastIn .3s}
.toast.ok{border-color:rgba(34,197,94,0.4);color:#4ade80}
.toast.err{border-color:rgba(239,68,68,0.4);color:#f87171}
.toast.info{border-color:rgba(59,130,246,0.4);color:#60a5fa}
@keyframes toastIn{from{opacity:0;transform:translateX(-50%) translateY(12px)}to{opacity:1;transform:translateX(-50%) translateY(0)}}

/* CRAFT placeholder */
.craft-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-bottom:14px}
.craft-slot{aspect-ratio:1;background:var(--card);border:1px solid var(--border);border-radius:14px;display:flex;align-items:center;justify-content:center;font-size:18px;color:var(--muted);font-weight:600}
.craft-footer{display:flex;gap:8px;align-items:center;justify-content:space-between;background:var(--card);border:1px solid var(--border);border-radius:14px;padding:12px}
.craft-footer .info{font-size:12px;color:var(--muted)}
.craft-footer .info strong{color:var(--text);display:block;font-size:14px}
</style>
</head>
<body>
<div class="app">
  <!-- TOP BAR -->
  <div class="topbar">
    <div class="topbar-left">
      <div class="logo-icon">🎁</div>
    </div>
    <div class="balance-pill">
      <div class="bal-item"><span class="ic">⭐</span> <span id="balStars">0</span></div>
      <button class="add-bal" id="addBalBtn" title="Пополнить">+</button>
    </div>
    <div class="avatar" id="avatarBtn">C</div>
  </div>

  <!-- NAV -->
  <div class="nav" id="nav">
    <button class="nav-item active" data-page="cases"><span class="ico">📦</span> Кейсы</button>
    <button class="nav-item" data-page="upgrade"><span class="ico">⬆️</span> Апгрейд</button>
    <button class="nav-item" data-page="mines"><span class="ico">💣</span> Мины</button>
    <button class="nav-item" data-page="crash"><span class="ico">🚀</span> Игры</button>
    <button class="nav-item" data-page="inventory"><span class="ico">🎒</span> Инвентарь</button>
    <button class="nav-item" data-page="profile"><span class="ico">👤</span> Профиль</button>
  </div>

  <!-- CASES -->
  <div class="page active" id="page-cases">
    <div class="live-bar">
      <div class="live-dot"></div>
      <div class="live-label">LIVE</div>
      <div class="live-scroll" id="liveScroll">
        <div class="live-item">🎀</div><div class="live-item">🍄</div><div class="live-item">🕯️</div>
        <div class="live-item">🌙</div><div class="live-item">🐸</div><div class="live-item">🎩</div>
        <div class="live-item">🔮</div><div class="live-item">🧦</div><div class="live-item">💍</div>
      </div>
    </div>
    <div class="sec-title">Промо <span>бесплатно</span></div>
    <div class="cases-row" id="promoCases"></div>
    <div class="sec-title">Все кейсы</div>
    <div class="cases-row" id="allCases"></div>
  </div>

  <!-- UPGRADE -->
  <div class="page" id="page-upgrade">
    <div class="upg-layout">
      <div class="upg-side">
        <button class="chance-preset" data-ch="35">35%</button>
        <button class="chance-preset" data-ch="55">55%</button>
        <button class="chance-preset" data-ch="75">75%</button>
      </div>
      <div class="upg-main">
        <div class="wheel-wrap">
          <div class="wheel-pointer"></div>
          <div class="wheel" id="upgWheel" style="background:conic-gradient(#22c55e 0deg, #22c55e 0deg, #ef4444 0deg, #ef4444 360deg)"></div>
          <div class="wheel-center">
            <div class="pct" id="chancePct">0%</div>
            <div class="lbl">шанс</div>
          </div>
        </div>
        <div class="upg-slots-row">
          <div class="upg-slot" id="inputSlot">
            <div class="plus">+</div>
            <div class="hint">Ваш подарок</div>
          </div>
          <div class="upg-slot" id="targetSlot">
            <div class="plus">+</div>
            <div class="hint">Цель</div>
          </div>
        </div>
        <div class="mult-row">
          <button class="mult-btn" data-m="2">x2</button>
          <button class="mult-btn" data-m="3">x3</button>
          <button class="mult-btn" data-m="4">x4</button>
        </div>
        <div class="upg-stats">
          <div class="st"><div class="l">Шанс</div><div class="v gold" id="chanceTxt">0%</div></div>
          <div class="st"><div class="l">Множитель</div><div class="v blue" id="multTxt">0x</div></div>
        </div>
        <button class="upg-btn" id="upgradeBtn" disabled>Начать апгрейд</button>
      </div>
    </div>

    <div class="list-panel">
      <div class="lp-title">Ваш инвентарь <span id="invCount">0</span></div>
      <div class="inv-grid" id="upgInvGrid"></div>
      <div class="empty-hint" id="upgInvEmpty">У вас пока нет подарков. Пополните баланс или выиграйте в кейсах.</div>
    </div>
    <div class="list-panel">
      <div class="lp-title">Желаемый подарок <span>сервис</span></div>
      <div class="inv-grid" id="targetGrid"></div>
      <div class="empty-hint" id="targetEmpty" style="display:none">Нет целей</div>
    </div>
  </div>

  <!-- MINES -->
  <div class="page" id="page-mines">
    <div class="mines-controls">
      <input type="number" id="minesBet" value="10" min="10" placeholder="Ставка">
      <input type="number" id="minesCount" value="3" min="1" max="24" placeholder="Мины">
      <button class="btn-sm green" id="minesStart">Старт</button>
    </div>
    <div class="mines-grid" id="minesGrid"></div>
    <div class="mines-bar">
      <div>Множитель: <strong id="minesMult">1.00x</strong></div>
      <div>Открыто: <strong id="minesOpened">0</strong></div>
      <button class="btn-sm gold" id="minesCashout" style="display:none">Забрать</button>
    </div>
  </div>

  <!-- CRASH / GAMES -->
  <div class="page" id="page-crash">
    <div class="mult-pills" id="crashHistory"></div>
    <div class="crash-box">
      <div class="crash-mult" id="crashMult">1.00x</div>
      <div class="crash-status" id="crashStatus">Ожидание ставок...</div>
      <div class="crash-row">
        <input type="number" id="crashBet" value="25" min="25" max="5000">
        <button class="btn-sm green" id="crashBetBtn">Ставка</button>
        <button class="btn-sm gold" id="crashCashBtn" style="display:none">Забрать</button>
      </div>
      <div class="crash-bets" id="crashBets"></div>
    </div>
  </div>

  <!-- INVENTORY -->
  <div class="page" id="page-inventory">
    <div class="sec-title">Инвентарь <span id="invTotal">0 предметов</span></div>
    <div class="inv-grid" id="mainInvGrid" style="grid-template-columns:repeat(3,1fr);gap:10px"></div>
    <div class="empty-hint" id="mainInvEmpty">Инвентарь пуст. Откройте кейсы!</div>
  </div>

  <!-- PROFILE -->
  <div class="page" id="page-profile">
    <div class="profile-card">
      <div class="profile-head">
        <div class="profile-av" id="profileAv">C</div>
        <div>
          <div class="profile-name" id="profileName">Player</div>
          <div class="profile-id" id="profileId">ID: 0</div>
        </div>
      </div>
      <div class="stats-row">
        <div class="stat-box"><div class="n" id="pBalance">0</div><div class="l">Баланс</div></div>
        <div class="stat-box"><div class="n" id="pGames">0</div><div class="l">Игр</div></div>
        <div class="stat-box"><div class="n" id="pWins">0</div><div class="l">Побед</div></div>
      </div>
      <div class="promo-row">
        <input type="text" id="promoInput" placeholder="Промокод">
        <button id="promoBtn">Применить</button>
      </div>
      <div class="ref-box">
        <div style="font-size:13px;font-weight:600">Реферальная программа · 7%</div>
        <div style="font-size:11px;color:var(--muted);margin-top:2px">Приглашено: <strong id="refCount">0</strong> · Заработано: <strong id="refEarned">0</strong></div>
        <div class="ref-link" id="refLink">Загрузка...</div>
        <button class="btn-sm" id="copyRef" style="margin-top:6px">📋 Копировать ссылку</button>
      </div>
      <button class="btn-full btn-danger" id="withdrawBtn">💳 Вывести звёзды</button>
    </div>
  </div>
</div>

<!-- CASE PREVIEW MODAL -->
<div id="caseOverlay" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:200;align-items:flex-end;justify-content:center">
  <div style="width:100%;max-width:480px;max-height:85vh;overflow:auto;background:#12182a;border-radius:20px 20px 0 0;padding:18px 14px 24px;border:1px solid rgba(255,255,255,.08)">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <div>
        <div id="caseModalTitle" style="font-size:16px;font-weight:700">Кейс</div>
        <div id="caseModalPrice" style="font-size:13px;color:#60a5fa;margin-top:2px"></div>
      </div>
      <button onclick="document.getElementById('caseOverlay').style.display='none'" style="width:32px;height:32px;border-radius:10px;border:1px solid rgba(255,255,255,.08);background:#161d32;color:#7a8699;font-size:16px;cursor:pointer">✕</button>
    </div>
    <div style="font-size:12px;color:#7a8699;margin-bottom:10px">Что может выпасть:</div>
    <div id="caseModalGrid" class="inv-grid" style="grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:14px"></div>
    <button id="caseModalOpen" class="btn-full">Открыть</button>
  </div>
</div>

<!-- DEPOSIT MODAL -->
<div id="depOverlay" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:200;align-items:flex-end;justify-content:center">
  <div style="width:100%;max-width:480px;background:#12182a;border-radius:20px 20px 0 0;padding:20px 16px 28px;border:1px solid rgba(255,255,255,.08)">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
      <div style="font-size:16px;font-weight:700">⭐ Пополнить баланс</div>
      <button id="depClose" style="width:32px;height:32px;border-radius:10px;border:1px solid rgba(255,255,255,.08);background:#161d32;color:#7a8699;font-size:16px;cursor:pointer">✕</button>
    </div>
    <div style="font-size:12px;color:#7a8699;margin-bottom:12px">Оплата звёздами Telegram (Stars). Выбери сумму:</div>
    <div id="depAmounts" style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px"></div>
    <button id="depCustom" class="btn-full" style="margin-top:4px">Другая сумма</button>
    <div style="font-size:11px;color:#7a8699;margin-top:10px;text-align:center">1 ⭐ = 1 к балансу · мгновенно</div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const S = {
  tgId:0, username:'Player', balance:50, inventory:[], games:0, wins:0,
  selItem:-1, targetVal:0, targetGift:null, isUpgrading:false,
  mines:{id:null,opened:[],bombs:[],mult:1,cashed:false,started:false},
  crash:{connected:false,socket:null},
  cases:{}, freeCase:true, gifts:null
};

/* ===== API URL: на Netlify укажи бэкенд Render =====
   Вариант 1: ?api=https://your-app.onrender.com
   Вариант 2: localStorage.setItem('API_BASE','https://your-app.onrender.com')
   Вариант 3: если HTML отдаёт тот же Render — оставь пустым
*/
// ВСЕГДА бьём в Render (Netlify = только оболочка для BotFather)
const API_BASE = (function(){
  const DEFAULT='https://izuzus-2.onrender.com';
  try{
    const q=new URLSearchParams(location.search).get('api');
    if(q){localStorage.setItem('API_BASE',q.replace(/\/$/,'')); return q.replace(/\/$/,'');}
    const ls=localStorage.getItem('API_BASE');
    if(ls) return ls.replace(/\/$/,'');
  }catch(e){}
  if(/izuzus-2\.onrender\.com/i.test(location.hostname)) return ''; // same origin
  return DEFAULT;
})();

function toast(msg,type='info'){
  const t=document.getElementById('toast');
  t.textContent=msg; t.className='toast '+type; t.style.display='block';
  setTimeout(()=>t.style.display='none',3500);
}

async function api(method,url,body=null){
  const full=(API_BASE||'')+url;
  const h={'Content-Type':'application/json'};
  const initData=window.Telegram?.WebApp?.initData||'';
  if(initData){
    h['Authorization']=initData;
    h['X-Telegram-Init-Data']=initData;
  }else{
    h['Authorization']='dev';
  }
  let r;
  try{
    r=await fetch(full,{method,headers:h,body:body?JSON.stringify(body):null,mode:'cors'});
  }catch(netErr){
    throw new Error('Нет связи с сервером ('+(API_BASE||'same')+'). Подожди 30с — Render просыпается.');
  }
  if(!r.ok){
    const e=await r.json().catch(()=>({}));
    const detail=typeof e.detail==='string'?e.detail:(e.detail?JSON.stringify(e.detail):('HTTP '+r.status));
    throw new Error(detail);
  }
  return r.json();
}

function initTG(){
  if(window.Telegram?.WebApp){
    const tg=window.Telegram.WebApp; tg.expand(); tg.enableClosingConfirmation();
    const u=tg.initDataUnsafe?.user;
    if(u){
      S.tgId=u.id; S.username=u.first_name||'Player';
      document.getElementById('profileName').textContent=S.username;
      document.getElementById('profileId').textContent='ID: '+S.tgId;
      document.getElementById('avatarBtn').textContent=(S.username[0]||'C').toUpperCase();
      document.getElementById('profileAv').textContent=(S.username[0]||'C').toUpperCase();
    }
    window.haptic={impact:s=>{try{tg.HapticFeedback.impactOccurred(s)}catch(e){}},notify:t=>{try{tg.HapticFeedback.notificationOccurred(t)}catch(e){}}};
  }else{
    // Вне Telegram — demo-пользователь (совпадает с DEV_MODE на бэке)
    S.tgId=100001; S.username='Demo';
    document.getElementById('profileName').textContent='Demo';
    document.getElementById('profileId').textContent='ID: 100001';
    document.getElementById('avatarBtn').textContent='D';
    document.getElementById('profileAv').textContent='D';
    window.haptic={impact:()=>{},notify:()=>{}};
  }
}

function updBal(){
  document.getElementById('balStars').textContent=S.balance;
  document.getElementById('pBalance').textContent=S.balance;
}

async function loadProfile(){
  try{
    const d=await api('GET','/api/profile');
    if(d.tg_id) S.tgId=d.tg_id;
    if(d.username) S.username=d.username;
    S.balance=d.balance||0; S.inventory=d.inventory||[]; S.games=d.games_played||0; S.wins=d.wins||0;
    S.freeCase=d.free_case_available!==false;
    updBal();
    document.getElementById('pGames').textContent=S.games;
    document.getElementById('pWins').textContent=S.wins;
    renderAllInv(); renderUpgInv(); renderCases(); loadRef();
  }catch(e){toast('Ошибка загрузки','err')}
}

async function loadCases(){
  try{
    S.cases=await api('GET','/api/cases');
    if(!S.gifts){try{const d=await api('GET','/api/gifts'); S.gifts=d.gifts;}catch(e){}}
    renderCases();
  }catch(e){toast('Кейсы: '+e.message,'err')}
}

async function loadGifts(){
  try{
    const d=await api('GET','/api/gifts');
    S.gifts=d.gifts;
    renderTargets();
    if(S.cases && Object.keys(S.cases).length) renderCases();
  }catch(e){}
}

function casePreviewImgs(c){
  // берём картинки NFT из редкостей кейса
  if(!S.gifts) return '';
  const imgs=[];
  for(const r of (c.rarities||[])){
    const pool=S.gifts[r]||[];
    for(const g of pool.slice(0,2)){
      if(g.img) imgs.push(g.img);
      if(imgs.length>=3) break;
    }
    if(imgs.length>=3) break;
  }
  if(!imgs.length) return `<div class="case-emoji">${c.icon||'📦'}</div>`;
  return `<div style="display:flex;gap:4px;align-items:center;justify-content:center;z-index:1;position:relative">${imgs.map(src=>`<img src="${src}" style="width:36px;height:36px;object-fit:contain;filter:drop-shadow(0 2px 6px rgba(0,0,0,.4))" onerror="this.style.display='none'">`).join('')}</div>`;
}

function renderCases(){
  const promo=document.getElementById('promoCases');
  const all=document.getElementById('allCases');
  promo.innerHTML=''; all.innerHTML='';
  for(const [id,c] of Object.entries(S.cases||{})){
    const isFree=c.price===0;
    const card=document.createElement('div');
    card.className='case-card '+(c.color||'')+(isFree?' free':'');
    card.innerHTML=`
      <div class="case-badge">${(c.rarities||[]).join(' · ')}</div>
      <div class="case-visual"><div class="case-box"></div>${casePreviewImgs(c)}</div>
      <div class="case-name">${c.name.replace(/^[^\s]+\s/,'')}</div>
      <div class="case-price">${isFree?(S.freeCase?'Бесплатно':'⏳ 24ч'):'⭐ '+c.price}</div>
    `;
    card.onclick=()=>showCaseModal(id);
    if(isFree) promo.appendChild(card); else all.appendChild(card);
  }
}

async function showCaseModal(id){
  const c=S.cases[id];
  if(!c) return;
  let contents={items:[]};
  try{contents=await api('GET','/api/case/'+id+'/contents');}catch(e){}
  const ov=document.getElementById('caseOverlay');
  if(!ov){openCase(id); return;}
  document.getElementById('caseModalTitle').textContent=c.name;
  document.getElementById('caseModalPrice').textContent=c.price===0?'Бесплатно':'⭐ '+c.price;
  const grid=document.getElementById('caseModalGrid');
  grid.innerHTML='';
  const items=contents.items||[];
  if(!items.length && S.gifts){
    (c.rarities||[]).forEach(r=>{(S.gifts[r]||[]).forEach(g=>items.push({...g,rarity:r}));});
  }
  items.slice(0,24).forEach(it=>{
    const d=document.createElement('div');
    d.className='inv-item r-'+(it.rarity||'Common');
    d.innerHTML=giftThumb(it)+`<div class="nm">${it.name}</div><div class="vl">⭐ ${it.value}</div>`;
    grid.appendChild(d);
  });
  const btn=document.getElementById('caseModalOpen');
  btn.textContent=c.price===0?'Открыть бесплатно':'Открыть за ⭐ '+c.price;
  btn.onclick=()=>{ov.style.display='none'; openCase(id);};
  ov.style.display='flex';
}

async function openCase(id){
  try{
    const d=await api('POST','/api/case/open',{case_id:id});
    if(d.success){
      S.balance=d.balance||S.balance; updBal();
      const p=await api('GET','/api/profile'); S.inventory=p.inventory||[]; renderAllInv(); renderUpgInv();
      if(d.stars_earned) toast('⭐ +'+d.stars_earned,'ok');
      else if(d.gift) toast((d.gift.emoji||'🎁')+' '+d.gift.name+' ('+d.rarity+')','ok');
      haptic.notify('success');
      if(id==='free_daily'){S.freeCase=false; renderCases();}
    }
  }catch(e){toast(e.message,'err')}
}

function giftThumb(item, size){
  const em=item.emoji||'🎁';
  const img=item.img||'';
  if(img){
    return `<div class="img-wrap"><img class="gift-thumb" src="${img}" alt="" onerror="this.style.display='none';this.nextElementSibling.style.display='block'"><span class="em" style="display:none">${em}</span></div>`;
  }
  return `<div class="img-wrap"><span class="em">${em}</span></div>`;
}

function renderUpgInv(){
  const g=document.getElementById('upgInvGrid');
  const empty=document.getElementById('upgInvEmpty');
  g.innerHTML='';
  document.getElementById('invCount').textContent=S.inventory.length;
  if(!S.inventory.length){empty.style.display='block'; return;}
  empty.style.display='none';
  S.inventory.forEach((item,idx)=>{
    const div=document.createElement('div');
    const rar=item.rarity||'Common';
    div.className='inv-item r-'+rar+(S.selItem===idx?' selected':'');
    div.innerHTML=giftThumb(item)+`<div class="nm">${item.name}</div><div class="vl">⭐ ${item.value}</div>`;
    div.onclick=()=>{S.selItem=idx; fillInputSlot(item); renderUpgInv(); updateChance();};
    g.appendChild(div);
  });
}

function fillInputSlot(item){
  const slot=document.getElementById('inputSlot');
  slot.className='upg-slot filled';
  const img=item.img?`<img class="gift-thumb" src="${item.img}" onerror="this.outerHTML='<div class=emoji>${item.emoji||'🎁'}</div>'">`:`<div class="emoji">${item.emoji||'🎁'}</div>`;
  slot.innerHTML=`${img}<div class="name">${item.name}</div><div class="val">⭐ ${item.value}</div>`;
}

function renderTargets(){
  const g=document.getElementById('targetGrid');
  g.innerHTML='';
  if(!S.gifts) return;
  const list=[];
  for(const [r,arr] of Object.entries(S.gifts)){
    arr.forEach(x=>list.push({...x,rarity:r}));
  }
  list.sort((a,b)=>a.value-b.value);
  list.slice(0,48).forEach(item=>{
    const div=document.createElement('div');
    div.className='inv-item r-'+(item.rarity||'Common')+(S.targetVal===item.value?' selected':'');
    div.innerHTML=giftThumb(item)+`<div class="nm">${item.name}</div><div class="vl">⭐ ${item.value}</div>`;
    div.onclick=()=>{
      selectTarget(item);
      document.querySelectorAll('.mult-btn,.chance-preset').forEach(b=>b.classList.remove('active'));
    };
    g.appendChild(div);
  });
}

function setWheelChance(ch){
  // ch = 0..100, green from 0 to winDeg (clockwise from top via conic from top)
  const winDeg = Math.max(0, Math.min(360, ch * 3.6));
  const w = document.getElementById('upgWheel');
  if(!w) return;
  // conic-gradient starts at top (12 o'clock) with from 0deg in modern browsers; use from -90deg for top
  w.style.background = `conic-gradient(from -90deg, #22c55e 0deg, #22c55e ${winDeg}deg, #ef4444 ${winDeg}deg, #ef4444 360deg)`;
}

function updateChance(){
  const btn=document.getElementById('upgradeBtn');
  if(S.selItem<0||!S.inventory[S.selItem]||!S.targetGift){
    document.getElementById('chancePct').textContent='0%';
    document.getElementById('chanceTxt').textContent='0%';
    document.getElementById('multTxt').textContent='0x';
    setWheelChance(0);
    btn.disabled=true; return;
  }
  const iv=S.inventory[S.selItem].value||1;
  const tv=S.targetGift.value||1;
  if(iv>=tv){btn.disabled=true; document.getElementById('chancePct').textContent='—'; setWheelChance(0); return;}
  const ch=Math.min((iv/tv)*100*0.95,60);
  document.getElementById('chancePct').textContent=ch.toFixed(0)+'%';
  document.getElementById('chanceTxt').textContent=ch.toFixed(1)+'%';
  document.getElementById('multTxt').textContent=((tv/iv)).toFixed(2)+'x';
  setWheelChance(ch);
  btn.disabled=false;
}

function flatGifts(){
  const list=[];
  if(!S.gifts) return list;
  for(const [r,arr] of Object.entries(S.gifts)){
    arr.forEach(x=>list.push({...x,rarity:r}));
  }
  list.sort((a,b)=>a.value-b.value);
  return list;
}

function pickTargetByMult(m){
  if(S.selItem<0||!S.inventory[S.selItem]){toast('Сначала выберите свой подарок','err'); return;}
  const iv=S.inventory[S.selItem].value||1;
  const want=Math.round(iv*m);
  const list=flatGifts().filter(g=>g.value>iv);
  if(!list.length){toast('Нет целей дороже','err'); return;}
  let best=list[0], bd=Math.abs(list[0].value-want);
  for(const g of list){const d=Math.abs(g.value-want); if(d<bd){bd=d; best=g;}}
  selectTarget(best);
  document.querySelectorAll('.mult-btn').forEach(b=>b.classList.toggle('active', +b.dataset.m===m));
  document.querySelectorAll('.chance-preset').forEach(b=>b.classList.remove('active'));
}

function pickTargetByChance(pct){
  if(S.selItem<0||!S.inventory[S.selItem]){toast('Сначала выберите свой подарок','err'); return;}
  const iv=S.inventory[S.selItem].value||1;
  // chance ≈ (iv/tv)*95 => tv ≈ iv*95/pct
  const want=Math.round(iv*95/Math.max(1,pct));
  const list=flatGifts().filter(g=>g.value>iv);
  if(!list.length){toast('Нет целей','err'); return;}
  let best=list[0], bd=1e9;
  for(const g of list){
    const ch=Math.min((iv/g.value)*100*0.95,60);
    const d=Math.abs(ch-pct);
    if(d<bd){bd=d; best=g;}
  }
  selectTarget(best);
  document.querySelectorAll('.chance-preset').forEach(b=>b.classList.toggle('active', +b.dataset.ch===pct));
  document.querySelectorAll('.mult-btn').forEach(b=>b.classList.remove('active'));
}

function selectTarget(item){
  S.targetVal=item.value; S.targetGift=item;
  const slot=document.getElementById('targetSlot');
  slot.className='upg-slot filled';
  const img=item.img?`<img class="gift-thumb" src="${item.img}" onerror="this.outerHTML='<div class=emoji>${item.emoji||'🎁'}</div>'">`:`<div class="emoji">${item.emoji||'🎁'}</div>`;
  slot.innerHTML=`${img}<div class="name">${item.name}</div><div class="val">⭐ ${item.value}</div>`;
  renderTargets(); updateChance();
}

async function runUpgrade(){
  if(S.isUpgrading||S.selItem<0||!S.targetGift) return;
  const item=S.inventory[S.selItem];
  if(item.value>=S.targetVal){toast('Цель должна быть дороже','err'); return;}
  S.isUpgrading=true;
  const btn=document.getElementById('upgradeBtn');
  btn.disabled=true; btn.textContent='⏳ Крутим...';
  const wheel=document.getElementById('upgWheel');
  try{
    const d=await api('POST','/api/upgrade',{item_index:S.selItem,target_value:S.targetVal});
    // анимация: много оборотов + финальный угол (маркер сверху)
    const spins=5+Math.floor(Math.random()*3);
    // angle от бэка: 0..win_deg = win (зелёный от -90deg), дальше red
    // чтобы маркер сверху указывал на final: rotate = 360*spins + (360 - angle)
    const finalAngle = d.angle || 0;
    const rotate = spins*360 + (360 - finalAngle);
    wheel.classList.remove('spinning');
    wheel.style.transition='none';
    wheel.style.transform='rotate(0deg)';
    void wheel.offsetWidth;
    wheel.classList.add('spinning');
    wheel.style.transform=`rotate(${rotate}deg)`;
    await new Promise(r=>setTimeout(r,4300));
    if(d.success){
      toast('🎉 Успех! '+item.name+' → '+d.target.name,'ok');
      haptic.notify('success');
    }else{
      toast('💔 Неудача — предмет сгорел','err');
      haptic.notify('error');
    }
    if(d.balance!==undefined){S.balance=d.balance; updBal();}
    const p=await api('GET','/api/profile');
    S.inventory=p.inventory||[]; S.selItem=-1; S.targetVal=0; S.targetGift=null;
    document.getElementById('inputSlot').className='upg-slot';
    document.getElementById('inputSlot').innerHTML='<div class="plus">+</div><div class="hint">Ваш подарок</div>';
    document.getElementById('targetSlot').className='upg-slot';
    document.getElementById('targetSlot').innerHTML='<div class="plus">+</div><div class="hint">Цель</div>';
    document.querySelectorAll('.mult-btn,.chance-preset').forEach(b=>b.classList.remove('active'));
    renderUpgInv(); renderAllInv(); updateChance();
    setTimeout(()=>{wheel.classList.remove('spinning'); wheel.style.transition='none'; wheel.style.transform='rotate(0deg)';},600);
  }catch(e){toast(e.message,'err')}
  S.isUpgrading=false; btn.textContent='Начать апгрейд'; updateChance();
}

function renderAllInv(){
  const g=document.getElementById('mainInvGrid');
  const empty=document.getElementById('mainInvEmpty');
  g.innerHTML='';
  document.getElementById('invTotal').textContent=S.inventory.length+' предметов';
  if(!S.inventory.length){empty.style.display='block'; return;}
  empty.style.display='none';
  S.inventory.forEach((item,idx)=>{
    const div=document.createElement('div');
    const rar=item.rarity||'Common';
    div.className='inv-item r-'+rar;
    div.style.padding='12px 6px';
    div.innerHTML=giftThumb(item)+`<div class="nm" style="font-size:11px">${item.name}</div><div class="vl">⭐ ${item.value}</div>
      <button style="margin-top:6px;font-size:10px;padding:3px 10px;border:none;border-radius:6px;background:rgba(239,68,68,0.15);color:#f87171;cursor:pointer" data-i="${idx}">Продать</button>`;
    div.querySelector('button').onclick=async(e)=>{e.stopPropagation(); await sellItem(idx);};
    g.appendChild(div);
  });
}

async function sellItem(idx){
  try{
    const d=await api('POST','/api/inventory/sell',{item_index:idx});
    if(d.success){
      S.balance=d.balance||S.balance; updBal();
      const p=await api('GET','/api/profile'); S.inventory=p.inventory||[];
      renderAllInv(); renderUpgInv();
      toast('💰 +'+d.price+' ⭐','ok'); haptic.impact('light');
    }
  }catch(e){toast(e.message,'err')}
}

/* MINES */
async function startMines(){
  const bet=parseInt(document.getElementById('minesBet').value)||10;
  const cnt=parseInt(document.getElementById('minesCount').value)||3;
  if(cnt<1||cnt>24){toast('Мины 1–24','err'); return;}
  try{
    const d=await api('POST','/api/mines/start',{bet,mines:cnt});
    S.balance=d.balance||S.balance; updBal();
    S.mines={id:d.game_id,opened:[],bombs:[],mult:1,cashed:false,started:true};
    document.getElementById('minesMult').textContent='1.00x';
    document.getElementById('minesOpened').textContent='0';
    document.getElementById('minesCashout').style.display='none';
    renderMines(); toast('💣 Игра начата','info');
  }catch(e){toast(e.message,'err')}
}

async function openMine(idx){
  if(!S.mines.started||S.mines.cashed||S.mines.opened.includes(idx)) return;
  try{
    const d=await api('POST','/api/mines/open',{game_id:S.mines.id,cell:idx});
    if(d.status==='bomb'){
      S.mines.cashed=true; S.mines.bombs=d.mines||[]; renderMines();
      toast('💥 Бомба!','err'); haptic.notify('error');
      document.getElementById('minesCashout').style.display='none';
      const p=await api('GET','/api/profile'); S.balance=p.balance||0; updBal();
      return;
    }
    S.mines.opened=d.opened||[]; S.mines.mult=d.multiplier||1;
    document.getElementById('minesMult').textContent=S.mines.mult.toFixed(2)+'x';
    document.getElementById('minesOpened').textContent=S.mines.opened.length;
    renderMines();
    if(S.mines.opened.length) document.getElementById('minesCashout').style.display='block';
    haptic.impact('light');
  }catch(e){toast(e.message,'err')}
}

async function cashoutMines(){
  if(!S.mines.started||S.mines.cashed||!S.mines.opened.length) return;
  try{
    const d=await api('POST','/api/mines/cashout',{game_id:S.mines.id});
    S.balance=d.balance||S.balance; updBal(); S.mines.cashed=true;
    toast('💰 +'+d.win+' ⭐ (x'+d.multiplier+')','ok'); haptic.notify('success');
    document.getElementById('minesCashout').style.display='none'; renderMines();
  }catch(e){toast(e.message,'err')}
}

function renderMines(){
  const g=document.getElementById('minesGrid'); g.innerHTML='';
  for(let i=0;i<25;i++){
    const c=document.createElement('div'); c.className='mine-cell';
    if(S.mines.opened.includes(i)){c.classList.add('opened'); c.textContent='💎';}
    if(S.mines.cashed&&S.mines.bombs.includes(i)){c.classList.add('bomb'); c.textContent='💣';}
    if(S.mines.cashed&&!S.mines.opened.includes(i)&&!S.mines.bombs.includes(i)){c.textContent='💎'; c.style.opacity='.4';}
    c.onclick=()=>openMine(i); g.appendChild(c);
  }
}

/* CRASH */
function initCrash(){
  if(S.crash.socket) return;
  const socket=io(API_BASE||undefined,{transports:['websocket','polling']}); S.crash.socket=socket; S.crash.connected=false;
  socket.on('live_win',()=>{loadLive();});
  socket.on('connect',()=>{S.crash.connected=true;});
  socket.on('crash_state',d=>{
    const st=document.getElementById('crashStatus');
    if(d.status==='betting') st.textContent='⌛ Ставки: '+(d.timer||'?')+'с';
    else if(d.status==='flying') st.textContent='🚀 Взлёт!';
    else if(d.status==='crashed') st.textContent='💥 Крах!';
    else st.textContent='⏳ Ожидание...';
    if(d.status==='betting') document.getElementById('crashMult').textContent='1.00x';
    if(d.history){
      const h=document.getElementById('crashHistory');
      h.innerHTML=d.history.map(x=>`<div class="mult-pill">${x.toFixed(2)}x</div>`).join('');
    }
  });
  socket.on('crash_multiplier',d=>{document.getElementById('crashMult').textContent=d.multiplier.toFixed(2)+'x';});
  socket.on('crash_start',()=>{
    document.getElementById('crashStatus').textContent='🚀 Взлёт!';
    document.getElementById('crashBetBtn').disabled=true;
    document.getElementById('crashCashBtn').style.display='block';
  });
  socket.on('crash_end',d=>{
    document.getElementById('crashStatus').textContent='💥 Крах на '+d.crash_point.toFixed(2)+'x';
    document.getElementById('crashBetBtn').disabled=false;
    document.getElementById('crashCashBtn').style.display='none';
    loadProfile();
    if(d.bets) document.getElementById('crashBets').innerHTML=d.bets.map(b=>`<span>${b.username}: ${b.win>0?'✅+'+b.win:'❌'}</span>`).join('');
  });
  socket.on('cashout_success',d=>{toast('💰 +'+d.win+' ⭐','ok'); haptic.notify('success'); S.balance=d.balance||S.balance; updBal(); document.getElementById('crashCashBtn').style.display='none';});
  socket.on('bet_placed',d=>{toast('✅ Ставка '+d.amount,'ok'); S.balance=d.balance||S.balance; updBal();});
  socket.on('error',d=>toast(d.message,'err'));
}

function placeCrash(){
  if(!S.crash.connected){toast('Подключение...','info'); initCrash(); return;}
  const amt=parseInt(document.getElementById('crashBet').value)||25;
  if(amt<25||amt>5000){toast('Ставка 25–5000','err'); return;}
  if(amt>S.balance){toast('Недостаточно','err'); return;}
  S.crash.socket.emit('place_bet',{tg_id:S.tgId,amount:amt,username:S.username});
}
function cashCrash(){if(S.crash.socket) S.crash.socket.emit('cashout',{tg_id:S.tgId});}

async function loadRef(){
  try{
    const d=await api('GET','/api/referral/stats');
    document.getElementById('refCount').textContent=d.referrals_count||0;
    document.getElementById('refEarned').textContent=d.total_earned||0;
    const link='https://t.me/GiftUpgraderBot?start=ref_'+S.tgId;
    document.getElementById('refLink').textContent=link; window._ref=link;
  }catch(e){}
}

function copyRef(){
  const l=window._ref||'';
  if(navigator.clipboard) navigator.clipboard.writeText(l).then(()=>toast('Скопировано','ok'));
  else{const t=document.createElement('textarea');t.value=l;document.body.appendChild(t);t.select();document.execCommand('copy');document.body.removeChild(t);toast('Скопировано','ok');}
}

async function activatePromo(){
  const code=document.getElementById('promoInput').value.trim().toUpperCase();
  if(!code){toast('Введите промокод','err'); return;}
  try{
    const d=await api('POST','/api/promo/activate?code='+encodeURIComponent(code),{});
    toast(d.message||'Активирован!','ok'); haptic.notify('success'); loadProfile();
    document.getElementById('promoInput').value='';
  }catch(e){toast(e.message,'err')}
}

async function withdraw(){
  const amt=prompt('Сумма (мин 100 ⭐):','100');
  if(!amt) return; const v=parseInt(amt);
  if(isNaN(v)||v<100){toast('Минимум 100','err'); return;}
  const wallet=prompt('Адрес кошелька (TON):','');
  if(!wallet||wallet.length<10){toast('Некорректный адрес','err'); return;}
  try{
    const d=await api('POST','/api/withdraw',{amount:v,wallet});
    toast('Заявка создана','ok'); S.balance=d.balance||S.balance; updBal();
  }catch(e){toast(e.message,'err')}
}

/* NAV */
document.querySelectorAll('.nav-item').forEach(b=>{
  b.onclick=function(){
    document.querySelectorAll('.nav-item').forEach(x=>x.classList.remove('active'));
    document.querySelectorAll('.page').forEach(x=>x.classList.remove('active'));
    this.classList.add('active');
    document.getElementById('page-'+this.dataset.page).classList.add('active');
    if(this.dataset.page==='inventory') renderAllInv();
    if(this.dataset.page==='upgrade'){renderUpgInv(); renderTargets();}
    if(this.dataset.page==='crash') initCrash();
    if(this.dataset.page==='profile') loadProfile();
  };
});

document.getElementById('avatarBtn').onclick=()=>{
  document.querySelectorAll('.nav-item').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.page').forEach(x=>x.classList.remove('active'));
  document.querySelector('.nav-item[data-page="profile"]').classList.add('active');
  document.getElementById('page-profile').classList.add('active');
  loadProfile();
};

document.addEventListener('DOMContentLoaded',()=>{
  initTG(); loadProfile(); loadCases(); loadGifts();
  document.getElementById('upgradeBtn').onclick=runUpgrade;
  document.querySelectorAll('.mult-btn').forEach(b=>b.onclick=()=>pickTargetByMult(+b.dataset.m));
  document.querySelectorAll('.chance-preset').forEach(b=>b.onclick=()=>pickTargetByChance(+b.dataset.ch));
  document.getElementById('minesStart').onclick=startMines;
  document.getElementById('minesCashout').onclick=cashoutMines;
  document.getElementById('crashBetBtn').onclick=placeCrash;
  document.getElementById('crashCashBtn').onclick=cashCrash;
  document.getElementById('promoBtn').onclick=activatePromo;
  document.getElementById('copyRef').onclick=copyRef;
  document.getElementById('withdrawBtn').onclick=withdraw;
  document.getElementById('addBalBtn').onclick=openDeposit;
  document.getElementById('depClose').onclick=()=>{document.getElementById('depOverlay').style.display='none';};
  document.getElementById('depOverlay').onclick=e=>{if(e.target.id==='depOverlay')e.target.style.display='none';};
  document.getElementById('depCustom').onclick=()=>{
    const v=prompt('Сумма в ⭐ (мин 10):','50');
    if(v) doDeposit(parseInt(v));
  };
  const amounts=[50,100,250,500,1000,2500];
  const box=document.getElementById('depAmounts');
  amounts.forEach(a=>{
    const b=document.createElement('button');
    b.className='btn-sm';
    b.style.cssText='padding:14px 8px;font-size:14px;width:100%';
    b.innerHTML='⭐ '+a;
    b.onclick=()=>doDeposit(a);
    box.appendChild(b);
  });
  renderMines();
  loadLive();
  setInterval(loadLive, 12000);
});

function openDeposit(){
  document.getElementById('depOverlay').style.display='flex';
}

async function doDeposit(amount){
  if(!amount||amount<10){toast('Минимум 10 ⭐','err'); return;}
  document.getElementById('depOverlay').style.display='none';
  try{
    const d=await api('POST','/api/deposit',{amount:amount});
    if(d.invoice_url && window.Telegram?.WebApp?.openInvoice){
      window.Telegram.WebApp.openInvoice(d.invoice_url, async status=>{
        if(status==='paid'){
          try{
            if(d.payload){
              const c=await api('POST','/api/deposit/confirm',{payload:d.payload});
              S.balance=c.balance||S.balance; updBal();
            }
            toast('⭐ Баланс пополнен!','ok');
            loadProfile();
          }catch(e){toast('Оплачено, обновляю...','info'); loadProfile();}
        }else if(status==='cancelled') toast('Оплата отменена','info');
        else toast('Статус: '+status,'info');
      });
    }else if(d.success && d.balance!==undefined){
      S.balance=d.balance; updBal();
      toast('⭐ +'+amount+' на баланс','ok');
    }else{
      toast(d.message||'Не удалось создать счёт','err');
    }
  }catch(e){toast(e.message,'err')}
}

async function loadLive(){
  try{
    const d=await api('GET','/api/live');
    const sc=document.getElementById('liveScroll');
    if(!sc||!d.items||!d.items.length) return;
    sc.innerHTML=d.items.map(it=>{
      const em=it.emoji||'🎁';
      const img=it.img?`<img src="${it.img}" style="width:28px;height:28px;object-fit:contain" onerror="this.outerHTML='<span style=font-size:18px>${em}</span>'">`:`<span style="font-size:18px">${em}</span>`;
      return `<div class="live-item" title="${(it.name||'')+' · '+(it.user||'')}">${img}</div>`;
    }).join('');
  }catch(e){}
}
</script>
</body>
</html>
"""

# ===== ADMIN HTML (unchanged core) =====
ADMIN_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Admin</title>
<style>*{margin:0;padding:0;box-sizing:border-box}body{background:#0D121D;color:#E8E8E8;font-family:sans-serif;padding:20px}.container{max-width:1200px;margin:0 auto}.header{display:flex;justify-content:space-between;align-items:center;padding:20px;background:#161F2E;border-radius:16px;margin-bottom:30px;border:1px solid #2A3A4F}.header h1{font-size:28px;background:linear-gradient(135deg,#FFC107,#F44336);-webkit-background-clip:text;-webkit-text-fill-color:transparent}.badge{background:#2A3A4F;padding:8px 16px;border-radius:20px;border:1px solid #FFC107;color:#FFC107}.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:16px;margin-bottom:30px}.stat{background:#161F2E;padding:20px;border-radius:12px;border:1px solid #2A3A4F;text-align:center}.stat .v{font-size:28px;font-weight:bold;background:linear-gradient(135deg,#FFC107,#FF6B00);-webkit-background-clip:text;-webkit-text-fill-color:transparent}.stat .l{font-size:13px;color:#8899AA}.panel{background:#161F2E;border-radius:16px;padding:20px;margin-bottom:20px;border:1px solid #2A3A4F}.panel h2{color:#FFC107;font-size:18px;margin-bottom:12px}.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}.form-group{margin-bottom:12px}.form-group label{display:block;font-size:13px;color:#8899AA;margin-bottom:4px}.form-group input,.form-group select{width:100%;padding:10px 14px;background:#0D121D;border:1px solid #2A3A4F;border-radius:8px;color:#E8E8E8;font-size:14px}.form-group input:focus,.form-group select:focus{outline:none;border-color:#FFC107}.btn{padding:10px 20px;border:none;border-radius:8px;font-weight:600;cursor:pointer;transition:.3s}.btn-primary{background:linear-gradient(135deg,#FFC107,#FF6B00);color:#0D121D}.btn-success{background:#4CAF50;color:#fff}.btn-danger{background:#F44336;color:#fff}.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:13px}th{text-align:left;padding:10px;color:#8899AA;border-bottom:1px solid #2A3A4F}td{padding:10px;border-bottom:1px solid #1A2A3F}.status{padding:4px 12px;border-radius:12px;font-size:11px;font-weight:600}.status-pending{background:#FFC107;color:#0D121D}.status-approved{background:#4CAF50;color:#fff}.status-rejected{background:#F44336;color:#fff}.tabs{display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap}.tab{padding:10px 20px;background:#0D121D;border:1px solid #2A3A4F;border-radius:8px;cursor:pointer;color:#8899AA}.tab.active{border-color:#FFC107;color:#FFC107;background:#1A2A3F}.tab-content{display:none}.tab-content.active{display:block}.empty{text-align:center;padding:30px;color:#8899AA}.actions{display:flex;gap:6px}.actions .btn{padding:4px 12px;font-size:11px}.toast{position:fixed;bottom:20px;right:20px;padding:14px 20px;border-radius:12px;background:#161F2E;border:1px solid #2A3A4F;display:none;z-index:1000}.toast.success{border-color:#4CAF50}.toast.error{border-color:#F44336}@media(max-width:768px){.row{grid-template-columns:1fr}}
</style></head>
<body>
<div class="container">
<div class="header"><h1>🎮 GiftUpgrader Admin</h1><div class="badge">👑 Admin Panel</div></div>
<div class="stats" id="stats">
<div class="stat"><div class="v" id="totalUsers">0</div><div class="l">Users</div></div>
<div class="stat"><div class="v" id="totalWithdraws">0</div><div class="l">Pending</div></div>
<div class="stat"><div class="v" id="totalPromos">0</div><div class="l">Promos</div></div>
<div class="stat"><div class="v" id="totalReferrals">0</div><div class="l">Referrals</div></div>
</div>
<div class="tabs"><div class="tab active" data-tab="promos">🎫 Promos</div><div class="tab" data-tab="withdrawals">💳 Withdrawals</div><div class="tab" data-tab="users">👤 Users</div><div class="tab" data-tab="logs">📋 Logs</div></div>
<div class="tab-content active" id="tab-promos">
<div class="panel"><h2>Create Promocode</h2><form id="promoForm">
<div class="row"><div class="form-group"><label>Code</label><input type="text" id="promoCode" placeholder="GIFT2024"></div><div class="form-group"><label>Type</label><select id="promoType"><option value="stars">⭐ Stars</option><option value="gift">🎁 Gift</option></select></div></div>
<div class="row" id="starsField"><div class="form-group"><label>Stars</label><input type="number" id="promoStars" value="100"></div></div>
<div class="row" id="giftField" style="display:none"><div class="form-group"><label>Case</label><select id="promoCase"><option value="tg_starter">TG STARTER</option><option value="pepe_memes">PEPE & MEMES</option><option value="telegram_gifts">TELEGRAM GIFTS</option><option value="fragment_nft">FRAGMENT NFT</option><option value="durov_selection">DUROV'S SELECTION</option></select></div></div>
<div class="form-group"><label>Max Uses</label><input type="number" id="promoMaxUses" value="1"></div>
<button type="submit" class="btn btn-primary">Create</button></form></div>
<div class="panel"><h2>Active Promocodes</h2><div class="table-wrap"><table><thead><tr><th>Code</th><th>Type</th><th>Reward</th><th>Uses</th><th>Max</th><th>Created</th></tr></thead><tbody id="promoList"><tr><td colspan="6" class="empty">No promocodes</td></tr></tbody></table></div></div>
</div>
<div class="tab-content" id="tab-withdrawals">
<div class="panel"><h2>Withdrawals</h2><div class="table-wrap"><table><thead><tr><th>ID</th><th>User</th><th>Amount</th><th>Wallet</th><th>Status</th><th>Date</th><th>Actions</th></tr></thead><tbody id="withdrawList"><tr><td colspan="7" class="empty">No withdrawals</td></tr></tbody></table></div></div>
</div>
<div class="tab-content" id="tab-users">
<div class="panel"><h2>Give Stars</h2><form id="giveForm"><div class="row"><div class="form-group"><label>User ID</label><input type="number" id="giveUserId" placeholder="123456789"></div><div class="form-group"><label>Amount</label><input type="number" id="giveAmount" value="100"></div></div><button type="submit" class="btn btn-primary">Give</button></form></div>
<div class="panel"><h2>Top Users</h2><div class="table-wrap"><table><thead><tr><th>#</th><th>Username</th><th>Balance</th><th>Spent</th><th>Games</th><th>Wins</th></tr></thead><tbody id="userList"><tr><td colspan="6" class="empty">Loading...</td></tr></tbody></table></div></div>
</div>
<div class="tab-content" id="tab-logs">
<div class="panel"><h2>Admin Logs</h2><div class="table-wrap"><table><thead><tr><th>ID</th><th>Admin</th><th>Action</th><th>Details</th><th>Date</th></tr></thead><tbody id="logList"><tr><td colspan="5" class="empty">No logs</td></tr></tbody></table></div></div>
</div>
</div>
<div class="toast" id="toast"></div>
<script>
let currentTab='promos';
function showToast(m,t){const toast=document.getElementById('toast');toast.textContent=m;toast.className='toast '+t;toast.style.display='block';setTimeout(()=>toast.style.display='none',3000);}
async function fetchData(url){try{const r=await fetch(url);if(!r.ok)throw new Error();return await r.json();}catch(e){return null;}}
async function loadStats(){const d=await fetchData('/api/admin/stats');if(d){document.getElementById('totalUsers').textContent=d.total_users||0;document.getElementById('totalWithdraws').textContent=d.pending_withdrawals||0;document.getElementById('totalPromos').textContent=d.total_promos||0;document.getElementById('totalReferrals').textContent=d.total_referrals||0;}}
async function loadPromos(){const d=await fetchData('/api/admin/promos');const tbody=document.getElementById('promoList');if(d&&d.length){tbody.innerHTML=d.map(p=>`<tr><td><strong>${p.code}</strong></td><td>${p.reward_type}</td><td>${p.reward_type==='stars'?'⭐ '+p.stars:'🎁 '+p.case_id}</td><td>${p.uses}/${p.max_uses}</td><td>${p.max_uses}</td><td>${new Date(p.created_at).toLocaleDateString()}</td></tr>`).join('');}else{tbody.innerHTML='<tr><td colspan="6" class="empty">No promocodes</td></tr>';}}
async function loadWithdrawals(){const d=await fetchData('/api/admin/withdrawals');const tbody=document.getElementById('withdrawList');if(d&&d.length){tbody.innerHTML=d.map(w=>`<tr><td>#${w.id}</td><td>${w.tg_id}</td><td>⭐ ${w.amount}</td><td>${w.wallet||'N/A'}</td><td><span class="status status-${w.status}">${w.status}</span></td><td>${new Date(w.created_at).toLocaleDateString()}</td><td>${w.status==='pending'?`<div class="actions"><button class="btn btn-success" onclick="updateWithdraw(${w.id},'approved')">✅</button><button class="btn btn-danger" onclick="updateWithdraw(${w.id},'rejected')">❌</button></div>`:'-'}</td></tr>`).join('');}else{tbody.innerHTML='<tr><td colspan="7" class="empty">No withdrawals</td></tr>';}}
async function loadUsers(){const d=await fetchData('/api/admin/users');const tbody=document.getElementById('userList');if(d&&d.length){tbody.innerHTML=d.map((u,i)=>`<tr><td>#${i+1}</td><td>${u.username}</td><td>⭐ ${u.balance}</td><td>⭐ ${u.total_spent}</td><td>${u.games_played||0}</td><td>${u.wins||0}</td></tr>`).join('');}else{tbody.innerHTML='<tr><td colspan="6" class="empty">No users</td></tr>';}}
async function loadLogs(){const d=await fetchData('/api/admin/logs');const tbody=document.getElementById('logList');if(d&&d.length){tbody.innerHTML=d.map(l=>`<tr><td>#${l.id}</td><td>${l.admin_id}</td><td>${l.action}</td><td>${l.details||'-'}</td><td>${new Date(l.created_at).toLocaleString()}</td></tr>`).join('');}else{tbody.innerHTML='<tr><td colspan="5" class="empty">No logs</td></tr>';}}
async function updateWithdraw(id,status){if(!confirm('Set #'+id+' to '+status+'?'))return;const r=await fetch('/api/admin/withdraw/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({withdraw_id:id,status})});const d=await r.json();if(d.success){showToast('✅ #'+id+' '+status,'success');loadWithdrawals();loadStats();}else{showToast('❌ Error','error');}}
document.querySelectorAll('.tab').forEach(t=>{t.onclick=function(){document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));document.querySelectorAll('.tab-content').forEach(x=>x.classList.remove('active'));this.classList.add('active');document.getElementById('tab-'+this.dataset.tab).classList.add('active');currentTab=this.dataset.tab;if(currentTab==='withdrawals')loadWithdrawals();if(currentTab==='users')loadUsers();if(currentTab==='logs')loadLogs();}});
document.getElementById('promoType').onchange=function(){if(this.value==='stars'){document.getElementById('starsField').style.display='block';document.getElementById('giftField').style.display='none';}else{document.getElementById('starsField').style.display='none';document.getElementById('giftField').style.display='block';}};
document.getElementById('promoForm').onsubmit=async function(e){e.preventDefault();const data={code:document.getElementById('promoCode').value.toUpperCase(),reward_type:document.getElementById('promoType').value,case_id:document.getElementById('promoCase').value,stars:parseInt(document.getElementById('promoStars').value)||0,max_uses:parseInt(document.getElementById('promoMaxUses').value)||1};const r=await fetch('/api/admin/promo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const d=await r.json();if(d.success){showToast('✅ '+d.message,'success');loadPromos();loadStats();document.getElementById('promoForm').reset();}else{showToast('❌ '+(d.detail||'Error'),'error');}};
document.getElementById('giveForm').onsubmit=async function(e){e.preventDefault();const data={user_id:parseInt(document.getElementById('giveUserId').value),amount:parseInt(document.getElementById('giveAmount').value)};const r=await fetch('/api/admin/give',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const d=await r.json();if(d.success){showToast('✅ '+d.message,'success');loadUsers();loadStats();document.getElementById('giveForm').reset();}else{showToast('❌ '+(d.detail||'Error'),'error');}};
loadStats();loadPromos();loadWithdrawals();loadUsers();loadLogs();
setInterval(()=>{loadStats();if(currentTab==='promos')loadPromos();if(currentTab==='withdrawals')loadWithdrawals();if(currentTab==='users')loadUsers();if(currentTab==='logs')loadLogs();},30000);
</script>
</body></html>"""

# ===== API ROUTES =====
@app.get("/", response_class=HTMLResponse)
async def root(): return HTML

@app.get("/admin", response_class=HTMLResponse)
async def admin(user=Depends(verify_admin)): return ADMIN_HTML

@app.get("/api/profile")
async def profile(user=Depends(verify_telegram)):
    tg_id=user['id']; username=user.get('first_name','Player')
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET username=? WHERE tg_id=?", (username, tg_id)); await db.commit()
    u=await get_user(tg_id)
    u.update({"tg_id":tg_id,"username":username,"is_admin":tg_id==ADMIN_TG_ID})
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT last_used FROM free_case_cooldowns WHERE user_id=?", (tg_id,)) as c:
            r=await c.fetchone()
            u["free_case_available"]=True if not r else (datetime.now()-datetime.fromisoformat(r[0])).total_seconds()>=86400
    return u

@app.get("/api/gifts")
async def get_gifts(): return {"rarities":RARITY_COLORS,"gifts":NFT_GIFTS}

@app.get("/api/cases")
async def get_cases(): return CASES

@app.get("/api/case/{case_id}/contents")
async def case_contents(case_id: str):
    c = CASES.get(case_id)
    if not c:
        raise HTTPException(404, "Case not found")
    items = []
    if c.get("star_drops"):
        total = sum(c.get("star_weights") or [1]*len(c["star_drops"])) or 1
        for sv, w in zip(c["star_drops"], c.get("star_weights") or [1]*len(c["star_drops"])):
            items.append({"name": f"⭐ {sv}", "value": sv, "emoji": "⭐", "rarity": "Common", "drop_chance": round(100*w/total,1), "img": ""})
        return {"case": c, "items": items, "preview": items[:6]}
    # free case — только мелкие ⭐ + байт-превью (не дропаются)
    if c.get("stars_bias_low") or case_id == "free_daily":
        for sv, ch in [(1, 40), (3, 25), (5, 15), (8, 10), (12, 6), (20, 4)]:
            items.append({"name": f"⭐ {sv}", "value": sv, "emoji": "⭐", "rarity": "Common", "drop_chance": ch, "img": ""})
        bait = []
        for rname in ("Legendary", "Mythic"):
            for g in NFT_GIFTS.get(rname, [])[:2]:
                bait.append({**g, "drop_chance": 0, "name": g["name"] + " (байт)"})
        return {"case": c, "items": items + bait, "preview": items[:6]}
    if c.get("allin"):
        items.append({"name": "Пусто / 0–5⭐", "value": 0, "emoji": "💀", "rarity": "Common", "drop_chance": 99.99, "img": ""})
        jn = c.get("jackpot_name", "Jackpot")
        items.append({"name": jn, "value": c.get("jackpot_value", 0), "emoji": get_emoji(jn), "rarity": "Mythic", "drop_chance": float(c.get("jackpot_chance") or 0)*100, "img": gift_img_url(jn)})
        return {"case": c, "items": items, "preview": items}
    rarities = c.get("rarities") or []
    weights = c.get("weights") or []
    total_w = sum(weights) or 1
    force = c.get("force_names") or []
    if force:
        for name in force:
            found = None
            for r, arr in NFT_GIFTS.items():
                for g in arr:
                    if g["name"] == name:
                        found = {**g, "rarity": r, "drop_chance": round(100/len(force),1)}
                        break
                if found: break
            if found:
                items.append(found)
            else:
                items.append({"name": name, "value": 500, "emoji": get_emoji(name), "rarity": "Epic", "drop_chance": round(100/len(force),1), "img": gift_img_url(name)})
    else:
        for r, w in zip(rarities, weights):
            pool = NFT_GIFTS.get(r, [])
            chance = round(100 * w / total_w, 1)
            for g in pool:
                items.append({**g, "drop_chance": chance, "rarity": r})
    preview = items[:6]
    return {"case": c, "items": items, "preview": preview}

@app.get("/api/live")
async def get_live():
    if LIVE_WINS:
        return {"items": LIVE_WINS[:24]}
    # демо-лента пока никто не открывал кейсы
    demo = []
    for r in ["Common", "Uncommon", "Rare"]:
        for g in NFT_GIFTS.get(r, [])[:3]:
            demo.append({"name": g["name"], "emoji": g["emoji"], "img": g.get("img", ""), "user": "Player", "value": g["value"]})
    return {"items": demo[:16]}

@app.post("/api/deposit")
async def deposit(req: DepositRequest, user=Depends(verify_telegram)):
    """Пополнение через Telegram Stars (XTR) — createInvoiceLink."""
    tg_id = user["id"]
    amount = int(req.amount)
    if amount < 10 or amount > 100000:
        raise HTTPException(400, "Сумма 10–100000 ⭐")

    # Demo-юзер без Telegram — только DEV
    if tg_id == 100001 and DEV_MODE:
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (amount, tg_id))
            await db.commit()
        return {"success": True, "balance": (await get_user(tg_id))["balance"], "message": f"+{amount} ⭐ (demo)"}

    payload = f"dep:{tg_id}:{amount}:{uuid.uuid4().hex[:10]}"
    PENDING_DEPOSITS[payload] = {"tg_id": tg_id, "amount": amount, "ts": time.time()}

    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: tg_api("createInvoiceLink", {
                "title": f"Пополнение {amount} ⭐",
                "description": f"Зачисление {amount} звёзд на баланс GiftUpgrader",
                "payload": payload,
                "currency": "XTR",
                "prices": [{"label": f"{amount} Stars", "amount": amount}],
            })
        )
        if not result.get("ok"):
            raise HTTPException(502, result.get("description", "Telegram API error"))
        invoice_url = result["result"]
        return {"success": True, "invoice_url": invoice_url, "payload": payload, "amount": amount}
    except HTTPException:
        raise
    except Exception as e:
        # fallback DEV если Stars недоступны
        if DEV_MODE:
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (amount, tg_id))
                await db.commit()
            return {"success": True, "balance": (await get_user(tg_id))["balance"], "message": f"+{amount} ⭐ (dev fallback)"}
        raise HTTPException(502, f"Stars invoice failed: {e}")

@app.post("/api/deposit/confirm")
async def deposit_confirm(req: DepositConfirmRequest, user=Depends(verify_telegram)):
    """Клиент: openInvoice → paid. Начисляем по payload."""
    tg_id = user["id"]
    info = PENDING_DEPOSITS.pop(req.payload, None)
    if not info or info["tg_id"] != tg_id:
        raise HTTPException(400, "Неизвестный платёж")
    amount = int(info["amount"])
    async with aiosqlite.connect(DB_NAME) as db:
        try:
            await db.execute("UPDATE users SET balance=balance+?, total_deposited=COALESCE(total_deposited,0)+? WHERE tg_id=?", (amount, amount, tg_id))
        except Exception:
            await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (amount, tg_id))
        await db.commit()
    return {"success": True, "balance": (await get_user(tg_id))["balance"], "amount": amount}

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """Webhook бота: pre_checkout + successful_payment (зачисление Stars)."""
    data = await request.json()
    pcq = data.get("pre_checkout_query")
    if pcq:
        try:
            qid = pcq["id"]
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: tg_api("answerPreCheckoutQuery", {"pre_checkout_query_id": qid, "ok": True})
            )
        except Exception:
            pass
        return {"ok": True}

    msg = data.get("message") or data.get("edited_message") or {}
    sp = msg.get("successful_payment")
    if sp and sp.get("currency") == "XTR":
        payload = sp.get("invoice_payload") or ""
        info = PENDING_DEPOSITS.pop(payload, None)
        amount = int(sp.get("total_amount") or (info or {}).get("amount") or 0)
        tg_id = (info or {}).get("tg_id") or (msg.get("from") or {}).get("id")
        if tg_id and amount > 0:
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute("INSERT OR IGNORE INTO users (tg_id, balance) VALUES (?, 50)", (tg_id,))
                await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (amount, tg_id))
                await db.commit()
        return {"ok": True}
    return {"ok": True}

@app.post("/api/case/open")
async def open_case(req:CaseOpenRequest, user=Depends(verify_telegram)):
    tg_id = user["id"]
    c = CASES.get(req.case_id)
    if not c:
        raise HTTPException(400, "Invalid case")
    u = await get_user(tg_id)
    if c["price"] > 0 and u["balance"] < c["price"]:
        raise HTTPException(400, "Insufficient")

    # free daily cooldown
    if req.case_id == "free_daily":
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT last_used FROM free_case_cooldowns WHERE user_id=?", (tg_id,)) as cur:
                r = await cur.fetchone()
                if r and (datetime.now() - datetime.fromisoformat(r[0])).total_seconds() < 86400:
                    raise HTTPException(400, "Cooldown")
            await db.execute(
                "INSERT OR REPLACE INTO free_case_cooldowns (user_id,last_used) VALUES (?,?)",
                (tg_id, datetime.now().isoformat()),
            )
            await db.commit()

    # charge + track
    async def charge_and_inc():
        async with aiosqlite.connect(DB_NAME) as db:
            if c["price"] > 0:
                await db.execute(
                    "UPDATE users SET balance=balance-?, total_spent=total_spent+?, games_played=games_played+1 WHERE tg_id=?",
                    (c["price"], c["price"], tg_id),
                )
            else:
                await db.execute("UPDATE users SET games_played=games_played+1 WHERE tg_id=?", (tg_id,))
            # cases_opened column (safe if missing)
            try:
                await db.execute("UPDATE users SET cases_opened=COALESCE(cases_opened,0)+1 WHERE tg_id=?", (tg_id,))
            except Exception:
                pass
            await db.commit()

    uname = user.get("first_name") or user.get("username") or "Player"

    # ---------- ALL-IN ----------
    if c.get("allin"):
        await charge_and_inc()
        jp_chance = float(c.get("jackpot_chance") or 0)
        # allin_pepe: jackpot_chance forced 0 so Pepe never drops
        if random.random() < jp_chance and jp_chance > 0:
            # extremely rare real jackpot
            name = c.get("jackpot_name", "Plush Pepe")
            val = int(c.get("jackpot_value", 100000))
            gift = {
                "id": name.lower().replace(" ", "_").replace("'", ""),
                "name": name,
                "value": val,
                "emoji": get_emoji(name),
                "img": gift_img_url(name),
                "rarity": "Mythic",
            }
            async with aiosqlite.connect(DB_NAME) as db:
                u2 = await get_user(tg_id)
                inv = u2["inventory"]
                inv.append({k: gift[k] for k in ("id", "name", "rarity", "value", "emoji", "img")})
                await db.execute("UPDATE users SET inventory=?, wins=wins+1 WHERE tg_id=?", (json.dumps(inv), tg_id))
                await db.commit()
            push_live(gift, uname)
            try:
                await sio.emit("live_win", {"name": gift["name"], "emoji": gift["emoji"], "img": gift.get("img", ""), "user": uname})
            except Exception:
                pass
            return {"success": True, "gift": gift, "rarity": "Mythic", "balance": (await get_user(tg_id))["balance"], "allin_win": True}
        # lose path: tiny stars or 0
        drops = c.get("lose_stars") or [0]
        weights = c.get("lose_weights") or [100]
        stars = int(random.choices(drops, weights=weights)[0])
        if stars > 0:
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (stars, tg_id))
                await db.commit()
        return {"success": True, "stars_earned": stars, "balance": (await get_user(tg_id))["balance"], "allin_lose": True}

    # ---------- PURE STARS CASES ----------
    if c.get("star_drops"):
        await charge_and_inc()
        drops = c["star_drops"]
        weights = c.get("star_weights") or [1] * len(drops)
        stars = int(random.choices(drops, weights=weights)[0])
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (stars, tg_id))
            await db.commit()
        return {"success": True, "stars_earned": stars, "balance": (await get_user(tg_id))["balance"]}

    # ---------- FREE: только 0.1–20⭐, NFT никогда ----------
    if c.get("stars_bias_low") or req.case_id == "free_daily":
        r = random.random()
        if r < 0.70:
            stars = round(random.uniform(0.1, 3), 1)
        elif r < 0.90:
            stars = round(random.uniform(3, 8), 1)
        elif r < 0.98:
            stars = round(random.uniform(8, 15), 1)
        else:
            stars = round(random.uniform(15, 20), 1)
        await charge_and_inc()
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (max(1, int(stars)), tg_id))
            await db.commit()
        # bait_items для рулетки на фронте (дорогие NFT — только визуал)
        bait = []
        for rname in ("Legendary", "Mythic", "Epic"):
            bait.extend(NFT_GIFTS.get(rname, [])[:3])
        return {
            "success": True,
            "stars_earned": stars,
            "balance": (await get_user(tg_id))["balance"],
            "bait": [{"name": b["name"], "emoji": b["emoji"], "value": b["value"], "img": b.get("img", "")} for b in bait[:8]],
        }

    # ---------- FREE / mixed: stars or gift ----------
    stars_chance = float(c.get("stars_chance", 0.3 if c.get("min_stars") is not None else 0))
    if False:  # disabled old free path
        pass

    if stars_chance > 0 and random.random() < stars_chance and c.get("min_stars") is not None:
        lo = c.get("min_stars", 1)
        hi = c.get("max_stars", 20)
        stars = int(round(random.uniform(lo, hi)))
        await charge_and_inc()
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (stars, tg_id))
            await db.commit()
        return {"success": True, "stars_earned": stars, "balance": (await get_user(tg_id))["balance"]}

    # ---------- NFT drop ----------
    force = c.get("force_names") or []
    if force:
        # pick from forced list, map to existing gift by name
        name = random.choice(force)
        gift = None
        rarity = "Epic"
        for r, arr in NFT_GIFTS.items():
            for g in arr:
                if g["name"] == name:
                    gift = {**g, "rarity": r}
                    rarity = r
                    break
            if gift:
                break
        if not gift:
            # synthesize
            gift = {
                "id": name.lower().replace(" ", "_").replace("'", ""),
                "name": name,
                "value": 500,
                "emoji": get_emoji(name),
                "img": gift_img_url(name),
                "rarity": "Epic",
            }
            rarity = "Epic"
    else:
        rarities = c.get("rarities") or ["Common"]
        weights = c.get("weights") or [100]
        rarity = random.choices(rarities, weights=weights)[0]
        pool = NFT_GIFTS.get(rarity) or NFT_GIFTS["Common"]
        gift = random.choice(pool)
        gift = {**gift, "rarity": rarity}

    await charge_and_inc()
    async with aiosqlite.connect(DB_NAME) as db:
        u2 = await get_user(tg_id)
        inv = u2["inventory"]
        inv.append({
            "id": gift["id"], "name": gift["name"], "rarity": gift.get("rarity", rarity),
            "value": gift["value"], "emoji": gift["emoji"], "img": gift.get("img", ""),
        })
        await db.execute("UPDATE users SET inventory=? WHERE tg_id=?", (json.dumps(inv), tg_id))
        await db.commit()
    push_live(gift, uname)
    try:
        await sio.emit("live_win", {"name": gift["name"], "emoji": gift["emoji"], "img": gift.get("img", ""), "user": uname})
    except Exception:
        pass
    return {
        "success": True,
        "gift": gift,
        "rarity": gift.get("rarity", rarity),
        "balance": (await get_user(tg_id))["balance"],
    }

@app.post("/api/upgrade")
async def upgrade(req:UpgradeRequest, user=Depends(verify_telegram)):
    tg_id=user['id']; u=await get_user(tg_id)
    if req.item_index<0 or req.item_index>=len(u["inventory"]): raise HTTPException(400,"Item not found")
    item=u["inventory"][req.item_index]
    if item["value"]>=req.target_value: raise HTTPException(400,"Target must be higher")
    target=None
    for r,g in NFT_GIFTS.items():
        for x in g:
            if x["value"]==req.target_value: target={**x,"rarity":r}; break
        if target: break
    if not target: raise HTTPException(400,"Target not found")
    chance=calc_upgrade_chance(item["value"], target["value"])/100
    win=random.random()<chance
    win_deg=chance*360
    # угол относительно маркера сверху: 0 = центр зелёного, зелёный = [0, win_deg)
    if win and win_deg > 6:
        final = random.uniform(3, max(4, win_deg - 3))
    elif win:
        final = win_deg / 2
    else:
        final = random.uniform(min(win_deg + 3, 350), 357) if win_deg < 354 else 180
    if win:
        u["inventory"][req.item_index]={
            "id":target["id"],"name":target["name"],"rarity":target["rarity"],
            "value":target["value"],"emoji":target["emoji"],"img":target.get("img","")
        }
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET inventory=?, wins=wins+1 WHERE tg_id=?", (json.dumps(u["inventory"]),tg_id)); await db.commit()
    else:
        del u["inventory"][req.item_index]
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET inventory=? WHERE tg_id=?", (json.dumps(u["inventory"]),tg_id)); await db.commit()
    return {
        "success": win,
        "chance": round(chance * 100, 2),
        "win_deg": round(win_deg, 2),
        "target": target,
        "angle": round(final, 2),
        "message": f"{'🎉' if win else '💔'} {item['name']} → {target['name']}",
        "balance": (await get_user(tg_id))["balance"],
    }

@app.get("/api/inventory")
async def get_inventory(user=Depends(verify_telegram)): return {"inventory":(await get_user(user['id']))["inventory"]}

@app.post("/api/inventory/sell")
async def sell(req:SellItemRequest, user=Depends(verify_telegram)):
    tg_id=user['id']; u=await get_user(tg_id)
    if req.item_index<0 or req.item_index>=len(u["inventory"]): raise HTTPException(400,"Item not found")
    item=u["inventory"].pop(req.item_index); price=int(item["value"]*0.7)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance=balance+?, inventory=? WHERE tg_id=?", (price,json.dumps(u["inventory"]),tg_id)); await db.commit()
    return {"success":True,"sold":item["name"],"price":price,"balance":(await get_user(tg_id))["balance"]}

# ===== MINES =====
active_mines={}
@app.post("/api/mines/start")
async def mines_start(req:MinesStartRequest, user=Depends(verify_telegram)):
    tg_id=user['id']
    if req.bet<10 or req.bet>50000 or req.mines<1 or req.mines>24: raise HTTPException(400,"Invalid")
    u=await get_user(tg_id)
    if u["balance"]<req.bet: raise HTTPException(400,"Insufficient")
    grid=[0]*25; mp=random.sample(range(25), req.mines)
    for p in mp: grid[p]=1
    gid=str(uuid.uuid4())[:8]
    active_mines[tg_id]={"game_id":gid,"bet":req.bet,"mines":req.mines,"grid":grid,"opened":[],"cashed_out":False,"multiplier":1.0}
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance=balance-?, games_played=games_played+1 WHERE tg_id=?", (req.bet,tg_id)); await db.commit()
    return {"game_id":gid,"bet":req.bet,"mines":req.mines,"balance":(await get_user(tg_id))["balance"]}

@app.post("/api/mines/open")
async def mines_open(req:MinesOpenRequest, user=Depends(verify_telegram)):
    tg_id=user['id']
    if tg_id not in active_mines: raise HTTPException(400,"No game")
    g=active_mines[tg_id]
    if g["game_id"]!=req.game_id or g["cashed_out"] or req.cell in g["opened"] or req.cell<0 or req.cell>=25: raise HTTPException(400,"Invalid")
    if g["grid"][req.cell]==1:
        mp=[i for i,v in enumerate(g["grid"]) if v==1]; del active_mines[tg_id]
        return {"status":"bomb","cell":req.cell,"opened":g["opened"],"mines":mp,"balance":(await get_user(tg_id))["balance"]}
    g["opened"].append(req.cell); g["multiplier"]=calc_mines_multiplier(g["mines"], len(g["opened"]))
    return {"status":"safe","cell":req.cell,"opened":g["opened"],"opened_count":len(g["opened"]),"multiplier":g["multiplier"]}

@app.post("/api/mines/cashout")
async def mines_cashout(req:MinesCashoutRequest, user=Depends(verify_telegram)):
    tg_id=user['id']
    if tg_id not in active_mines: raise HTTPException(400,"No game")
    g=active_mines[tg_id]
    if g["game_id"]!=req.game_id or g["cashed_out"] or len(g["opened"])==0: raise HTTPException(400,"Invalid")
    win=int(g["bet"]*g["multiplier"]); g["cashed_out"]=True
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance=balance+?, wins=wins+1 WHERE tg_id=?", (win,tg_id)); await db.commit()
    del active_mines[tg_id]
    return {"status":"cashed_out","multiplier":g["multiplier"],"win":win,"balance":(await get_user(tg_id))["balance"]}

# ===== WITHDRAW =====
@app.post("/api/withdraw")
async def withdraw(req:WithdrawRequest, user=Depends(verify_telegram)):
    tg_id=user['id']
    if req.amount<100 or req.amount>50000: raise HTTPException(400,"Invalid")
    u=await get_user(tg_id)
    if u["balance"]<req.amount: raise HTTPException(400,"Insufficient")
    fee=int(req.amount*0.05)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET balance=balance-? WHERE tg_id=?", (req.amount,tg_id))
        await db.execute("INSERT INTO withdrawals (tg_id,amount,wallet) VALUES (?,?,?)", (tg_id,req.amount,req.wallet)); await db.commit()
    return {"success":True,"requested":req.amount,"fee":fee,"payout":req.amount-fee,"balance":(await get_user(tg_id))["balance"]}

@app.post("/api/admin/withdraw/status")
async def update_withdraw(req:AdminWithdrawStatusRequest, user=Depends(verify_admin)):
    if req.status not in ["approved","rejected"]: raise HTTPException(400,"Invalid")
    async with aiosqlite.connect(DB_NAME) as db:
        if req.status=="rejected":
            async with db.execute("SELECT tg_id,amount FROM withdrawals WHERE id=?", (req.withdraw_id,)) as c:
                r=await c.fetchone()
                if r: await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (r[1],r[0]))
        await db.execute("UPDATE withdrawals SET status=? WHERE id=?", (req.status,req.withdraw_id)); await db.commit()
        await log_admin_action(user['id'], f"withdraw_{req.status}", f"#{req.withdraw_id} -> {req.status}")
    return {"success":True}

@app.get("/api/admin/withdrawals")
async def admin_withdrawals(user=Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id,tg_id,amount,wallet,status,created_at FROM withdrawals ORDER BY created_at DESC LIMIT 100") as c:
            return [{"id":r[0],"tg_id":r[1],"amount":r[2],"wallet":r[3],"status":r[4],"created_at":r[5]} for r in await c.fetchall()]

@app.get("/api/admin/stats")
async def admin_stats(user=Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        tu=(await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        pw=(await (await db.execute("SELECT COUNT(*) FROM withdrawals WHERE status='pending'")).fetchone())[0]
        tp=(await (await db.execute("SELECT COUNT(*) FROM promocodes")).fetchone())[0]
        tr=(await (await db.execute("SELECT COUNT(*) FROM referrals")).fetchone())[0]
        return {"total_users":tu,"pending_withdrawals":pw,"total_promos":tp,"total_referrals":tr}

@app.get("/api/admin/promos")
async def admin_promos(user=Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT code,reward_type,case_id,stars,max_uses,uses,created_at FROM promocodes ORDER BY created_at DESC") as c:
            return [{"code":r[0],"reward_type":r[1],"case_id":r[2],"stars":r[3],"max_uses":r[4],"uses":r[5],"created_at":r[6]} for r in await c.fetchall()]

@app.get("/api/admin/users")
async def admin_users(user=Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT username,balance,total_spent,games_played,wins FROM users ORDER BY balance DESC LIMIT 50") as c:
            return [{"username":r[0],"balance":r[1],"total_spent":r[2],"games_played":r[3],"wins":r[4]} for r in await c.fetchall()]

@app.get("/api/admin/logs")
async def admin_logs(user=Depends(verify_admin)):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id,admin_id,action,details,created_at FROM admin_logs ORDER BY created_at DESC LIMIT 50") as c:
            return [{"id":r[0],"admin_id":r[1],"action":r[2],"details":r[3],"created_at":r[4]} for r in await c.fetchall()]

@app.post("/api/admin/give")
async def admin_give(req:AdminGiveRequest, user=Depends(verify_admin)):
    if req.user_id<=0 or req.amount<1 or req.amount>1000000: raise HTTPException(400,"Invalid")
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (tg_id,balance) VALUES (?,50)", (req.user_id,))
        await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (req.amount,req.user_id)); await db.commit()
        await log_admin_action(user['id'], "give_stars", f"Gave {req.amount} to {req.user_id}")
    return {"success":True,"message":f"Added {req.amount} to {req.user_id}"}


@app.get("/api/admin/tops")
async def admin_tops(user=Depends(verify_admin)):
    """Топы: депы / слив / открытые кейсы. Сбрасываются каждые 14 дней вручную/по флагу."""
    async with aiosqlite.connect(DB_NAME) as db:
        # ensure columns
        for col in ("total_deposited", "cases_opened"):
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER DEFAULT 0")
            except Exception:
                pass
        await db.commit()
        dep = await (await db.execute(
            "SELECT tg_id, username, COALESCE(total_deposited,0) as v FROM users ORDER BY v DESC LIMIT 20"
        )).fetchall()
        spent = await (await db.execute(
            "SELECT tg_id, username, total_spent as v FROM users ORDER BY total_spent DESC LIMIT 20"
        )).fetchall()
        cases = await (await db.execute(
            "SELECT tg_id, username, COALESCE(cases_opened,0) as v FROM users ORDER BY v DESC LIMIT 20"
        )).fetchall()
    def pack(rows):
        return [{"tg_id": r[0], "username": r[1] or "Player", "value": r[2]} for r in rows]
    return {
        "by_deposit": pack(dep),
        "by_spent": pack(spent),
        "by_cases": pack(cases),
        "period_days": 14,
        "prizes": {
            "1": "NFT 2000–3000⭐ (выдаёшь сам)",
            "2": "NFT 1000–1500⭐ (выдаёшь сам)",
            "3": "750⭐ (выдаёшь сам)",
        },
    }

@app.post("/api/admin/give_prize")
async def admin_give_prize(req: dict, user=Depends(verify_admin)):
    """Выдать приз по tg_id: stars или конкретный gift по name/value/rarity."""
    tg_id = int(req.get("user_id") or 0)
    if tg_id <= 0:
        raise HTTPException(400, "Invalid user_id")
    prize_type = (req.get("prize_type") or "stars").lower()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (tg_id, balance) VALUES (?, 50)", (tg_id,))
        if prize_type == "stars":
            amount = int(req.get("amount") or 0)
            if amount < 1:
                raise HTTPException(400, "amount")
            await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (amount, tg_id))
            await db.commit()
            await log_admin_action(user["id"], "give_prize_stars", f"{amount} to {tg_id}")
            return {"success": True, "message": f"+{amount}⭐ to {tg_id}"}
        else:
            name = req.get("name") or "Gift"
            value = int(req.get("value") or 100)
            rarity = req.get("rarity") or "Epic"
            emoji = get_emoji(name)
            img = gift_img_url(name)
            gid = name.lower().replace(" ", "_").replace("'", "")
            # load inv
            async with db.execute("SELECT inventory FROM users WHERE tg_id=?", (tg_id,)) as cur:
                row = await cur.fetchone()
            inv = json.loads(row[0] if row and row[0] else "[]")
            inv.append({"id": gid, "name": name, "rarity": rarity, "value": value, "emoji": emoji, "img": img})
            await db.execute("UPDATE users SET inventory=? WHERE tg_id=?", (json.dumps(inv), tg_id))
            await db.commit()
            await log_admin_action(user["id"], "give_prize_gift", f"{name} to {tg_id}")
            return {"success": True, "message": f"Gift {name} to {tg_id}"}

@app.post("/api/admin/promo")
async def create_promo(req:PromoCreateRequest, user=Depends(verify_admin)):
    code=req.code.strip().upper()
    async with aiosqlite.connect(DB_NAME) as db:
        if await (await db.execute("SELECT code FROM promocodes WHERE code=?", (code,))).fetchone():
            raise HTTPException(400,"Exists")
        await db.execute("INSERT INTO promocodes (code,reward_type,case_id,stars,max_uses,created_by) VALUES (?,?,?,?,?,?)",
                         (code,req.reward_type,req.case_id,req.stars,req.max_uses,user['id'])); await db.commit()
        await log_admin_action(user['id'], "create_promo", f"{code} ({req.reward_type})")
    return {"success":True,"message":f"✅ {code} created!"}

@app.post("/api/promo/activate")
async def activate_promo(code:str, user=Depends(verify_telegram)):
    tg_id=user['id']; code=code.upper()
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT reward_type,case_id,stars,max_uses,uses FROM promocodes WHERE code=?", (code,)) as c:
            p=await c.fetchone()
            if not p or p[3]>=p[4] or await (await db.execute("SELECT 1 FROM promo_uses WHERE user_id=? AND promo_code=?", (tg_id,code))).fetchone():
                raise HTTPException(400,"Invalid or used")
        if p[0]=="stars":
            await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (p[2],tg_id))
            reward=f"⭐ {p[2]}"
        else:
            case=CASES.get(p[1])
            if not case: raise HTTPException(400,"Invalid case")
            rarity=random.choices(case["rarities"], weights=case["weights"])[0]; gift=random.choice(NFT_GIFTS[rarity])
            u=await get_user(tg_id); inv=u["inventory"]
            inv.append({"id":gift["id"],"name":gift["name"],"rarity":rarity,"value":gift["value"],"emoji":gift["emoji"],"img":gift.get("img","")})
            await db.execute("UPDATE users SET inventory=? WHERE tg_id=?", (json.dumps(inv),tg_id))
            reward=gift["name"]
        await db.execute("INSERT INTO promo_uses (user_id,promo_code) VALUES (?,?)", (tg_id,code))
        await db.execute("UPDATE promocodes SET uses=uses+1 WHERE code=?", (code,)); await db.commit()
    return {"success":True,"message":"🎉 Activated!","reward":reward}

@app.post("/api/referral/activate")
async def activate_referral(referrer_id:int, user=Depends(verify_telegram)):
    tg_id=user['id']
    if tg_id==referrer_id: raise HTTPException(400,"Self refer")
    async with aiosqlite.connect(DB_NAME) as db:
        if not await (await db.execute("SELECT 1 FROM users WHERE tg_id=?", (referrer_id,))).fetchone():
            raise HTTPException(400,"Referrer not found")
        if await (await db.execute("SELECT 1 FROM referrals WHERE user_id=?", (tg_id,))).fetchone():
            raise HTTPException(400,"Already referred")
        await db.execute("INSERT INTO referrals (user_id,referrer_id) VALUES (?,?)", (tg_id,referrer_id)); await db.commit()
    return {"success":True,"referrer":referrer_id}

@app.get("/api/referral/stats")
async def referral_stats(user=Depends(verify_telegram)):
    tg_id=user['id']
    async with aiosqlite.connect(DB_NAME) as db:
        earned=(await (await db.execute("SELECT total_earned FROM referrals WHERE user_id=?", (tg_id,))).fetchone() or [0])[0]
        count=(await (await db.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id=?", (tg_id,))).fetchone())[0]
    return {"total_earned":earned,"referrals_count":count,"percent":7}

# ===== STARTUP =====
@app.on_event("startup")
async def startup():
    await init_db()
    asyncio.create_task(crash_loop())
    # Webhook для Stars successful_payment
    try:
        wh = f"{PUBLIC_URL}/telegram/webhook"
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: tg_api("setWebhook", {"url": wh, "allowed_updates": ["message", "pre_checkout_query"]})
        )
    except Exception as e:
        print("setWebhook skip:", e)

if __name__=="__main__":
    import uvicorn
    uvicorn.run(socket_app, host="0.0.0.0", port=8000
