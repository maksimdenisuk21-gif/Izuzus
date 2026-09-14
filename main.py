# main.py — GiftUpgrader
# FastAPI + Socket.IO backend for the Telegram Mini App HTML.
# Put this file + index.html on Render (or GitHub → Render).
#
# Env:
#   BOT_TOKEN          Telegram bot token (do NOT hardcode)
#   ADMIN_TG_ID        numeric Telegram user id of admin
#   DATABASE_URL       Neon/Postgres URL (optional; SQLite fallback)
#   SQLITE_PATH        sqlite file path (default database.db)
#   TON_TREASURY       TON wallet for deposits
#   TON_STARS_PER_TON  default 200
#   ALLOW_DEV_AUTH     1 = Authorization: dev works (default 1)
#   STARTING_BALANCE   default 1000
#   PORT               Render sets this
#
# Run:
#   pip install -r requirements.txt
#   uvicorn main:socket_app --host 0.0.0.0 --port ${PORT:-8080}
#
# HTML on GitHub Pages: open with ?api=https://YOUR-APP.onrender.com
# (saved to localStorage). If FastAPI serves index.html, same-origin is enough.

import os, hmac, hashlib, json, random, time, uuid, asyncio, math, secrets, re
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
import pathlib

from fastapi import FastAPI, Header, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pydantic import BaseModel, Field
import aiosqlite
import socketio

# -------------------- CONFIG --------------------
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
ADMIN_TG_ID = int(os.getenv("ADMIN_TG_ID") or "7092015279")
TON_TREASURY = (os.getenv("TON_TREASURY") or "UQBZ7Yf8GJ6Qzr2VOZGy-ZJBvhskRis0LVfzByZn1II3OwKE").strip()
TON_STARS_PER_TON = int(os.getenv("TON_STARS_PER_TON") or "200")
TON_DEPOSIT_MODE = os.getenv("TON_DEPOSIT_MODE", "verify")
ALLOW_DEV_AUTH = os.getenv("ALLOW_DEV_AUTH", "1") not in ("0", "false", "False")
STARTING_BALANCE = int(os.getenv("STARTING_BALANCE") or "1000")
HOUSE_EDGE = float(os.getenv("HOUSE_EDGE") or "0.07")
MIN_BET = 50
SHOP_MARKUP = 1.20

DATABASE_URL = (os.getenv("DATABASE_URL") or os.getenv("NEON_DATABASE_URL") or "").strip()
if DATABASE_URL and "channel_binding=" in DATABASE_URL:
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
    u = urlparse(DATABASE_URL)
    q = parse_qs(u.query)
    q.pop("channel_binding", None)
    if "sslmode" not in q:
        q["sslmode"] = ["require"]
    DATABASE_URL = urlunparse((u.scheme, u.netloc, u.path, u.params, urlencode({k: v[0] for k, v in q.items()}), u.fragment))
USE_POSTGRES = DATABASE_URL.startswith("postgres")
DB_NAME = os.getenv("SQLITE_PATH") or os.getenv("DB_PATH") or "database.db"
try:
    pathlib.Path(DB_NAME).parent.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
_pg_pool = None

HERE = pathlib.Path(__file__).resolve().parent
HTML_PATH = HERE / "index.html"

CDN = "https://cdn.jsdelivr.net/gh/ssamy2/TG_Photos@main/webp/by_name"


# -------------------- DB ADAPTER --------------------
def _sql_adapt(sql: str) -> str:
    if not USE_POSTGRES:
        return sql
    s = sql
    upper = s.upper()
    if "INSERT OR IGNORE INTO" in upper:
        s = s.replace("INSERT OR IGNORE INTO", "INSERT INTO").replace("insert or ignore into", "INSERT INTO")
        if "ON CONFLICT" not in s.upper():
            s = s.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    s = s.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    s = s.replace("integer primary key autoincrement", "SERIAL PRIMARY KEY")
    out = []; n = 0
    for ch in s:
        if ch == "?":
            n += 1; out.append(f"${n}")
        else:
            out.append(ch)
    return "".join(out)

class _ResultCursor:
    def __init__(self, rows):
        self._rows = rows or []
        self._i = 0
        self.lastrowid = None
        self.rowcount = len(self._rows)
    async def fetchone(self):
        if self._i >= len(self._rows):
            return None
        r = self._rows[self._i]; self._i += 1
        return r
    async def fetchall(self):
        return list(self._rows)

class _PGExecuteContext:
    def __init__(self, conn, sql, params=None):
        self._conn = conn; self._sql = sql; self._params = tuple(params or ())
        self._cur = None; self._entered = False
    async def _run(self):
        sql2 = _sql_adapt(self._sql)
        su = sql2.lstrip().upper()
        if su.startswith("SELECT") or su.startswith("WITH"):
            rows = await self._conn.fetch(sql2, *self._params)
            self._cur = _ResultCursor([tuple(r) for r in rows])
        else:
            await self._conn.execute(sql2, *self._params)
            self._cur = _ResultCursor([])
        self._entered = True
        return self._cur
    def __await__(self):
        async def _awaitable():
            await self._run(); return self
        return _awaitable().__await__()
    async def __aenter__(self):
        if not self._entered: await self._run()
        return self._cur
    async def __aexit__(self, *a): return False
    async def fetchone(self):
        if not self._entered: await self._run()
        return await self._cur.fetchone()
    async def fetchall(self):
        if not self._entered: await self._run()
        return await self._cur.fetchall()

class _PGConn:
    def __init__(self, conn): self._conn = conn
    def execute(self, sql, params=None):
        return _PGExecuteContext(self._conn, sql, params)
    async def commit(self): return None
    async def executescript(self, script):
        for part in script.split(";"):
            part = part.strip()
            if part: await self.execute(part)

class _SQLiteExecuteProxy:
    def __init__(self, db, sql, params):
        self._db = db; self._sql = sql; self._params = params
    def __await__(self):
        return self._db.execute(self._sql, self._params or ()).__await__()
    async def __aenter__(self):
        self._cm = self._db.execute(self._sql, self._params or ())
        self._cur = await self._cm.__aenter__()
        return self._cur
    async def __aexit__(self, *a):
        return await self._cm.__aexit__(*a)

class _SQLiteConn:
    def __init__(self, db): self._db = db
    def execute(self, sql, params=None):
        return _SQLiteExecuteProxy(self._db, sql, params)
    async def commit(self):
        return await self._db.commit()

async def _ensure_pg_pool():
    global _pg_pool
    if _pg_pool is None:
        import asyncpg
        last_err = None
        for attempt in range(1, 6):
            try:
                _pg_pool = await asyncpg.create_pool(
                    DATABASE_URL, min_size=1, max_size=5,
                    command_timeout=60, statement_cache_size=0, timeout=30,
                )
                print("[DB] Connected to Postgres")
                return _pg_pool
            except Exception as e:
                last_err = e
                print(f"[DB] connect try {attempt}/5:", e)
                await asyncio.sleep(2 * attempt)
        raise RuntimeError(f"Postgres connect failed: {last_err}")
    return _pg_pool

@asynccontextmanager
async def get_db():
    if USE_POSTGRES:
        pool = await _ensure_pg_pool()
        conn = await pool.acquire()
        try:
            yield _PGConn(conn)
        finally:
            await pool.release(conn)
    else:
        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = None
            yield _SQLiteConn(db)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  tg_id INTEGER PRIMARY KEY,
  username TEXT,
  balance INTEGER DEFAULT 0,
  inventory TEXT DEFAULT '[]',
  games INTEGER DEFAULT 0,
  wins INTEGER DEFAULT 0,
  deposited INTEGER DEFAULT 0,
  free_case_at INTEGER DEFAULT 0,
  friend_case_at INTEGER DEFAULT 0,
  chance_bonus REAL DEFAULT 0,
  ton_wallet TEXT DEFAULT '',
  cases_opened INTEGER DEFAULT 0,
  created_at INTEGER
);
CREATE TABLE IF NOT EXISTS history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tg_id INTEGER,
  game TEXT,
  result TEXT,
  detail TEXT,
  amount INTEGER DEFAULT 0,
  created_at INTEGER
);
CREATE TABLE IF NOT EXISTS withdrawals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tg_id INTEGER,
  amount INTEGER,
  method TEXT,
  dest TEXT,
  note TEXT,
  status TEXT DEFAULT 'pending',
  created_at INTEGER
);
CREATE TABLE IF NOT EXISTS promos (
  code TEXT PRIMARY KEY,
  reward_type TEXT,
  stars INTEGER,
  max_uses INTEGER,
  uses INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS promo_uses (
  code TEXT,
  tg_id INTEGER,
  PRIMARY KEY (code, tg_id)
);
CREATE TABLE IF NOT EXISTS quests (
  tg_id INTEGER,
  quest_id TEXT,
  progress INTEGER DEFAULT 0,
  claimed INTEGER DEFAULT 0,
  PRIMARY KEY (tg_id, quest_id)
);
CREATE TABLE IF NOT EXISTS live_drops (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT,
  emoji TEXT,
  img TEXT,
  user_name TEXT,
  value INTEGER,
  created_at INTEGER
);
CREATE TABLE IF NOT EXISTS deposits (
  payload TEXT PRIMARY KEY,
  tg_id INTEGER,
  amount INTEGER,
  paid INTEGER DEFAULT 0,
  created_at INTEGER
);
CREATE TABLE IF NOT EXISTS ton_deposits (
  id TEXT PRIMARY KEY,
  tg_id INTEGER,
  amount_ton REAL,
  boc TEXT,
  address TEXT,
  credited INTEGER DEFAULT 0,
  created_at INTEGER
);
CREATE TABLE IF NOT EXISTS share_claims (
  tg_id INTEGER,
  case_id TEXT,
  created_at INTEGER,
  PRIMARY KEY (tg_id, case_id)
);
"""

async def init_db():
    async with get_db() as db:
        for stmt in SCHEMA.split(";"):
            s = stmt.strip()
            if s:
                try:
                    await db.execute(s)
                except Exception as e:
                    print("[DB] schema", e)
        await db.commit()
        # seed welcome promo
        try:
            await db.execute(
                "INSERT OR IGNORE INTO promos(code,reward_type,stars,max_uses,uses) VALUES(?,?,?,?,?)",
                ("WELCOME", "stars", 150, 10000, 0),
            )
            await db.execute(
                "INSERT OR IGNORE INTO promos(code,reward_type,stars,max_uses,uses) VALUES(?,?,?,?,?)",
                ("LUCK", "stars", 50, 10000, 0),
            )
            await db.commit()
        except Exception:
            pass

print("[DB] mode:", "POSTGRES" if USE_POSTGRES else f"SQLite ({DB_NAME})")


# -------------------- CATALOG --------------------
GIFT_SN_CDN = {"мишка": "toy_bear", "сердце": "cookie_heart", "конфета": "lol_pop", "подарок": "joyful_bundle", "звезда": "hanging_star", "торт": "homemade_cake", "ракета": "stellar_rocket", "букет": "lush_bouquet", "ёлка": "winter_wreath", "елка": "winter_wreath", "шампанское": "spiced_wine", "цветы": "sakura_flower", "кольцо": "diamond_ring", "алмаз": "diamond_ring", "кубок": "mini_oscar", "teddy_bear": "toy_bear", "heart": "cookie_heart", "candy": "lol_pop"}

GIFTS_FLAT = [{"name": "Кольцо", "value": 100, "rarity": "Common", "sn": "ring"}, {"name": "Алмаз", "value": 100, "rarity": "Common", "sn": "diamond"}, {"name": "Кубок", "value": 100, "rarity": "Common", "sn": "trophy"}, {"name": "Мишка", "value": 15, "rarity": "Common", "sn": "teddy_bear"}, {"name": "Сердце", "value": 15, "rarity": "Common", "sn": "heart"}, {"name": "Конфета", "value": 15, "rarity": "Common", "sn": "candy"}, {"name": "Подарок", "value": 25, "rarity": "Common", "sn": "gift"}, {"name": "Звезда", "value": 25, "rarity": "Common", "sn": "star"}, {"name": "Торт", "value": 50, "rarity": "Common", "sn": "cake"}, {"name": "Ракета", "value": 50, "rarity": "Common", "sn": "rocket"}, {"name": "Букет", "value": 50, "rarity": "Common", "sn": "bouquet"}, {"name": "Ёлка", "value": 50, "rarity": "Common", "sn": "christmas_tree"}, {"name": "Шампанское", "value": 50, "rarity": "Common", "sn": "champagne"}, {"name": "Цветы", "value": 50, "rarity": "Common", "sn": "flowers"}, {"name": "Мишка тенор", "value": 50, "rarity": "Common", "sn": "teddy_bear"}, {"name": "Пасхальный мишка", "value": 50, "rarity": "Common", "sn": "easter_egg"}, {"name": "Triple Meow", "value": 300, "rarity": "Uncommon", "sn": "triple_meow"}, {"name": "Lush Bouquet", "value": 1031, "rarity": "Rare", "sn": "lush_bouquet"}, {"name": "Gift 5897607679345427347", "value": 1037, "rarity": "Rare", "sn": "gift_5897607679345427347"}, {"name": "Moon Pendant", "value": 1043, "rarity": "Rare", "sn": "moon_pendant"}, {"name": "Light Sword", "value": 1072, "rarity": "Rare", "sn": "light_sword"}, {"name": "Durov's Coat", "value": 11028, "rarity": "Rare", "sn": "durovs_coat"}, {"name": "Sleigh Bell", "value": 1154, "rarity": "Rare", "sn": "sleigh_bell"}, {"name": "Surge Board", "value": 1229, "rarity": "Rare", "sn": "surge_board"}, {"name": "Joyful Bundle", "value": 1261, "rarity": "Rare", "sn": "joyful_bundle"}, {"name": "Jolly Chimp", "value": 1265, "rarity": "Rare", "sn": "jolly_chimp"}, {"name": "Evil Eye", "value": 1283, "rarity": "Rare", "sn": "evil_eye"}, {"name": "Jingle Bells", "value": 1287, "rarity": "Rare", "sn": "jingle_bells"}, {"name": "Sand Castle", "value": 1318, "rarity": "Rare", "sn": "sand_castle"}, {"name": "Jelly Bunny", "value": 1341, "rarity": "Rare", "sn": "jelly_bunny"}, {"name": "Bunny Muffin", "value": 1345, "rarity": "Rare", "sn": "bunny_muffin"}, {"name": "Durov's Figurine", "value": 136489, "rarity": "Rare", "sn": "durovs_figurine"}, {"name": "Love Candle", "value": 1365, "rarity": "Rare", "sn": "love_candle"}, {"name": "Berry Box", "value": 1404, "rarity": "Rare", "sn": "berry_box"}, {"name": "Hanging Star", "value": 1482, "rarity": "Rare", "sn": "hanging_star"}, {"name": "Sakura Flower", "value": 1560, "rarity": "Rare", "sn": "sakura_flower"}, {"name": "Skull Flower", "value": 1753, "rarity": "Rare", "sn": "skull_flower"}, {"name": "Top Hat", "value": 1803, "rarity": "Rare", "sn": "top_hat"}, {"name": "Mad Pumpkin", "value": 1852, "rarity": "Rare", "sn": "mad_pumpkin"}, {"name": "Valentine Box", "value": 1883, "rarity": "Rare", "sn": "valentine_box"}, {"name": "Easter Cake", "value": 1928, "rarity": "Rare", "sn": "easter_cake"}, {"name": "Flying Broom", "value": 1940, "rarity": "Rare", "sn": "flying_broom"}, {"name": "REDO", "value": 27000, "rarity": "Rare", "sn": "redo"}, {"name": "Coffin", "value": 3256, "rarity": "Rare", "sn": "coffin"}, {"name": "Eight Roses", "value": 3300, "rarity": "Rare", "sn": "eight_roses"}, {"name": "1 May", "value": 4125, "rarity": "Rare", "sn": "may"}, {"name": "Red Star", "value": 4125, "rarity": "Rare", "sn": "red_star"}, {"name": "Telegram Pin", "value": 540225, "rarity": "Rare", "sn": "telegram_pin"}, {"name": "Lunar Snake", "value": 575, "rarity": "Rare", "sn": "lunar_snake"}, {"name": "Chill Flame", "value": 575, "rarity": "Rare", "sn": "chill_flame"}, {"name": "Case", "value": 5775, "rarity": "Rare", "sn": "case"}, {"name": "Pool Float", "value": 596, "rarity": "Rare", "sn": "pool_float"}, {"name": "Xmas Stocking", "value": 602, "rarity": "Rare", "sn": "xmas_stocking"}, {"name": "Candy Cane", "value": 604, "rarity": "Rare", "sn": "candy_cane"}, {"name": "Snake Box", "value": 606, "rarity": "Rare", "sn": "snake_box"}, {"name": "Vice Cream", "value": 608, "rarity": "Rare", "sn": "vice_cream"}, {"name": "Instant Ramen", "value": 610, "rarity": "Rare", "sn": "instant_ramen"}, {"name": "Big Year", "value": 614, "rarity": "Rare", "sn": "big_year"}, {"name": "Tama Gadget", "value": 614, "rarity": "Rare", "sn": "tama_gadget"}, {"name": "Lol Pop", "value": 618, "rarity": "Rare", "sn": "lol_pop"}, {"name": "Ice Cream", "value": 624, "rarity": "Rare", "sn": "ice_cream"}, {"name": "Easter Egg", "value": 625, "rarity": "Rare", "sn": "easter_egg"}, {"name": "Winter Wreath", "value": 625, "rarity": "Rare", "sn": "winter_wreath"}, {"name": "Holiday Drink", "value": 627, "rarity": "Rare", "sn": "holiday_drink"}, {"name": "Jester Hat", "value": 643, "rarity": "Rare", "sn": "jester_hat"}, {"name": "Pet Snake", "value": 643, "rarity": "Rare", "sn": "pet_snake"}, {"name": "Whip Cupcake", "value": 643, "rarity": "Rare", "sn": "whip_cupcake"}, {"name": "Hypno Lollipop", "value": 663, "rarity": "Rare", "sn": "hypno_lollipop"}, {"name": "Coconut Drink", "value": 678, "rarity": "Rare", "sn": "coconut_drink"}, {"name": "Ginger Cookie", "value": 682, "rarity": "Rare", "sn": "ginger_cookie"}, {"name": "Santa Hat", "value": 682, "rarity": "Rare", "sn": "santa_hat"}, {"name": "Liberty Figure", "value": 686, "rarity": "Rare", "sn": "liberty_figure"}, {"name": "Party Sparkler", "value": 702, "rarity": "Rare", "sn": "party_sparkler"}, {"name": "Timeless Book", "value": 702, "rarity": "Rare", "sn": "timeless_book"}, {"name": "Hex Pot", "value": 729, "rarity": "Rare", "sn": "hex_pot"}, {"name": "Fresh Socks", "value": 731, "rarity": "Rare", "sn": "fresh_socks"}, {"name": "Star Notepad", "value": 733, "rarity": "Rare", "sn": "star_notepad"}, {"name": "Mood Pack", "value": 741, "rarity": "Rare", "sn": "mood_pack"}, {"name": "Happy Brownie", "value": 744, "rarity": "Rare", "sn": "happy_brownie"}, {"name": "Trojan Horse", "value": 7449, "rarity": "Rare", "sn": "trojan_horse"}, {"name": "Victory Medal", "value": 756, "rarity": "Rare", "sn": "victory_medal"}, {"name": "Jack-in-the-Box", "value": 760, "rarity": "Rare", "sn": "jackinthebox"}, {"name": "Money Pot", "value": 780, "rarity": "Rare", "sn": "money_pot"}, {"name": "Snow Globe", "value": 785, "rarity": "Rare", "sn": "snow_globe"}, {"name": "Clover Pin", "value": 797, "rarity": "Rare", "sn": "clover_pin"}, {"name": "Snow Mittens", "value": 797, "rarity": "Rare", "sn": "snow_mittens"}, {"name": "Spy Agaric", "value": 815, "rarity": "Rare", "sn": "spy_agaric"}, {"name": "Spiced Wine", "value": 819, "rarity": "Rare", "sn": "spiced_wine"}, {"name": "Stellar Rocket", "value": 819, "rarity": "Rare", "sn": "stellar_rocket"}, {"name": "Gravestone", "value": 8246, "rarity": "Rare", "sn": "gravestone"}, {"name": "Desk Calendar", "value": 834, "rarity": "Rare", "sn": "desk_calendar"}, {"name": "Cookie Heart", "value": 838, "rarity": "Rare", "sn": "cookie_heart"}, {"name": "Bow Tie", "value": 848, "rarity": "Rare", "sn": "bow_tie"}, {"name": "Witch Hat", "value": 854, "rarity": "Rare", "sn": "witch_hat"}, {"name": "B-Day Candle", "value": 858, "rarity": "Rare", "sn": "bday_candle"}, {"name": "Restless Jar", "value": 858, "rarity": "Rare", "sn": "restless_jar"}, {"name": "Mousse Cake", "value": 859, "rarity": "Rare", "sn": "mousse_cake"}, {"name": "Homemade Cake", "value": 865, "rarity": "Rare", "sn": "homemade_cake"}, {"name": "Pretty Posy", "value": 875, "rarity": "Rare", "sn": "pretty_posy"}, {"name": "Faith Amulet", "value": 891, "rarity": "Rare", "sn": "faith_amulet"}, {"name": "Snoop Dogg", "value": 900, "rarity": "Rare", "sn": "snoop_dogg"}, {"name": "Heart Pendant", "value": 9075, "rarity": "Rare", "sn": "heart_pendant"}, {"name": "Durov's Boots", "value": 9426, "rarity": "Rare", "sn": "durovs_boots"}, {"name": "Spring Basket", "value": 955, "rarity": "Rare", "sn": "spring_basket"}, {"name": "Eternal Candle", "value": 963, "rarity": "Rare", "sn": "eternal_candle"}, {"name": "Input Key", "value": 967, "rarity": "Rare", "sn": "input_key"}, {"name": "Swag Bag", "value": 971, "rarity": "Rare", "sn": "swag_bag"}, {"name": "Crystal Ball", "value": 2125, "rarity": "Epic", "sn": "crystal_ball"}, {"name": "Record Player", "value": 2166, "rarity": "Epic", "sn": "record_player"}, {"name": "Snoop Cigar", "value": 2419, "rarity": "Epic", "sn": "snoop_cigar"}, {"name": "Trapped Heart", "value": 2509, "rarity": "Epic", "sn": "trapped_heart"}, {"name": "Love Potion", "value": 2533, "rarity": "Epic", "sn": "love_potion"}, {"name": "UFC Strike", "value": 2585, "rarity": "Epic", "sn": "ufc_strike"}, {"name": "Ionic Dryer", "value": 2726, "rarity": "Epic", "sn": "ionic_dryer"}, {"name": "Sky Stilettos", "value": 2788, "rarity": "Epic", "sn": "sky_stilettos"}, {"name": "Cupid Charm", "value": 3297, "rarity": "Epic", "sn": "cupid_charm"}, {"name": "Khabib's Papakha", "value": 3630, "rarity": "Epic", "sn": "khabibs_papakha"}, {"name": "Rare Bird", "value": 3676, "rarity": "Epic", "sn": "rare_bird"}, {"name": "Bling Binky", "value": 3742, "rarity": "Epic", "sn": "bling_binky"}, {"name": "Electric Skull", "value": 3763, "rarit
