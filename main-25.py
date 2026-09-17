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
#   TON_STARS_PER_TON  default 110
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
TON_TREASURY = (os.getenv("TON_TREASURY") or "").strip()
TON_STARS_PER_TON = int(os.getenv("TON_STARS_PER_TON") or "110")
TON_DEPOSIT_MODE = os.getenv("TON_DEPOSIT_MODE", "verify")
ALLOW_DEV_AUTH = os.getenv("ALLOW_DEV_AUTH", "0") not in ("0", "false", "False")  # prod default OFF
STARTING_BALANCE = int(os.getenv("STARTING_BALANCE") or "1000")
HOUSE_EDGE = float(os.getenv("HOUSE_EDGE") or "0.08")  # RTP ≈ 92%, касса в плюсе мягко
MIN_BET = 50
MAX_BET = int(os.getenv("MAX_BET") or "25000")
MAX_WIN = int(os.getenv("MAX_WIN") or "150000")
MAX_WITHDRAW_DAY = int(os.getenv("MAX_WITHDRAW_DAY") or "50000")
MINES_TTL_SEC = 1800
SHOP_MARKUP = 1.35
_USER_LOCKS = {}
_RATE_CASE = {}  # tg_id -> last open ts


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

def clamp_win(amount: int) -> int:
    return max(0, min(int(amount or 0), MAX_WIN))

def check_bet(amount: int) -> int:
    amount = int(amount)
    if amount < MIN_BET:
        raise HTTPException(400, f"Мин. ставка {MIN_BET}⭐")
    if amount > MAX_BET:
        raise HTTPException(400, f"Макс. ставка {MAX_BET}⭐")
    return amount

async def user_lock(tg_id: int):
    if tg_id not in _USER_LOCKS:
        _USER_LOCKS[tg_id] = asyncio.Lock()
    return _USER_LOCKS[tg_id]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  tg_id BIGINT PRIMARY KEY,
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
  tg_id BIGINT,
  game TEXT,
  result TEXT,
  detail TEXT,
  amount INTEGER DEFAULT 0,
  created_at INTEGER
);
CREATE TABLE IF NOT EXISTS withdrawals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tg_id BIGINT,
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
  tg_id BIGINT,
  PRIMARY KEY (code, tg_id)
);
CREATE TABLE IF NOT EXISTS quests (
  tg_id BIGINT,
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
  tg_id BIGINT,
  amount INTEGER,
  paid INTEGER DEFAULT 0,
  created_at INTEGER
);
CREATE TABLE IF NOT EXISTS ton_deposits (
  id TEXT PRIMARY KEY,
  tg_id BIGINT,
  amount_ton REAL,
  boc TEXT,
  address TEXT,
  credited INTEGER DEFAULT 0,
  created_at INTEGER
);
CREATE TABLE IF NOT EXISTS share_claims (
  tg_id BIGINT,
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
        # PostgreSQL compatibility: add columns to databases created by older versions
        if USE_POSTGRES:
            migrations = [
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS games INTEGER DEFAULT 0",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS wins INTEGER DEFAULT 0",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS deposited INTEGER DEFAULT 0",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS free_case_at BIGINT DEFAULT 0",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS friend_case_at BIGINT DEFAULT 0",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS chance_bonus DOUBLE PRECISION DEFAULT 0",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS ton_wallet TEXT DEFAULT ''",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS cases_opened INTEGER DEFAULT 0",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS inventory TEXT DEFAULT '[]'",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS balance INTEGER DEFAULT 0",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT",
                # Telegram IDs > 2^31 — нужен BIGINT
                "ALTER TABLE users ALTER COLUMN tg_id TYPE BIGINT",
                "ALTER TABLE history ALTER COLUMN tg_id TYPE BIGINT",
                "ALTER TABLE withdrawals ALTER COLUMN tg_id TYPE BIGINT",
                "ALTER TABLE promo_uses ALTER COLUMN tg_id TYPE BIGINT",
                "ALTER TABLE quests ALTER COLUMN tg_id TYPE BIGINT",
                "ALTER TABLE deposits ALTER COLUMN tg_id TYPE BIGINT",
                "ALTER TABLE ton_deposits ALTER COLUMN tg_id TYPE BIGINT",
                "ALTER TABLE share_claims ALTER COLUMN tg_id TYPE BIGINT",
            ]
            for migration in migrations:
                try:
                    await db.execute(migration)
                except Exception as e:
                    print("[DB] migration", migration, e)
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

GIFTS_FLAT = [
    {'name': 'Кольцо', 'value': 100, 'rarity': 'Common', 'sn': 'ring'},
    {'name': 'Алмаз', 'value': 100, 'rarity': 'Common', 'sn': 'diamond'},
    {'name': 'Кубок', 'value': 100, 'rarity': 'Common', 'sn': 'trophy'},
    {'name': 'Мишка', 'value': 15, 'rarity': 'Common', 'sn': 'teddy_bear'},
    {'name': 'Сердце', 'value': 15, 'rarity': 'Common', 'sn': 'heart'},
    {'name': 'Конфета', 'value': 15, 'rarity': 'Common', 'sn': 'candy'},
    {'name': 'Подарок', 'value': 25, 'rarity': 'Common', 'sn': 'gift'},
    {'name': 'Звезда', 'value': 25, 'rarity': 'Common', 'sn': 'star'},
    {'name': 'Торт', 'value': 50, 'rarity': 'Common', 'sn': 'cake'},
    {'name': 'Ракета', 'value': 50, 'rarity': 'Common', 'sn': 'rocket'},
    {'name': 'Букет', 'value': 50, 'rarity': 'Common', 'sn': 'bouquet'},
    {'name': 'Ёлка', 'value': 50, 'rarity': 'Common', 'sn': 'christmas_tree'},
    {'name': 'Шампанское', 'value': 50, 'rarity': 'Common', 'sn': 'champagne'},
    {'name': 'Цветы', 'value': 50, 'rarity': 'Common', 'sn': 'flowers'},
    {'name': 'Мишка тенор', 'value': 50, 'rarity': 'Common', 'sn': 'teddy_bear'},
    {'name': 'Пасхальный мишка', 'value': 50, 'rarity': 'Common', 'sn': 'easter_egg'},
    {'name': 'Triple Meow', 'value': 300, 'rarity': 'Uncommon', 'sn': 'triple_meow'},
    {'name': 'Lush Bouquet', 'value': 1031, 'rarity': 'Rare', 'sn': 'lush_bouquet'},
    {'name': 'Gift 5897607679345427347', 'value': 1037, 'rarity': 'Rare', 'sn': 'gift_5897607679345427347'},
    {'name': 'Moon Pendant', 'value': 1043, 'rarity': 'Rare', 'sn': 'moon_pendant'},
    {'name': 'Light Sword', 'value': 1072, 'rarity': 'Rare', 'sn': 'light_sword'},
    {'name': "Durov's Coat", 'value': 11028, 'rarity': 'Rare', 'sn': 'durovs_coat'},
    {'name': 'Sleigh Bell', 'value': 1154, 'rarity': 'Rare', 'sn': 'sleigh_bell'},
    {'name': 'Surge Board', 'value': 1229, 'rarity': 'Rare', 'sn': 'surge_board'},
    {'name': 'Joyful Bundle', 'value': 1261, 'rarity': 'Rare', 'sn': 'joyful_bundle'},
    {'name': 'Jolly Chimp', 'value': 1265, 'rarity': 'Rare', 'sn': 'jolly_chimp'},
    {'name': 'Evil Eye', 'value': 1283, 'rarity': 'Rare', 'sn': 'evil_eye'},
    {'name': 'Jingle Bells', 'value': 1287, 'rarity': 'Rare', 'sn': 'jingle_bells'},
    {'name': 'Sand Castle', 'value': 1318, 'rarity': 'Rare', 'sn': 'sand_castle'},
    {'name': 'Jelly Bunny', 'value': 1341, 'rarity': 'Rare', 'sn': 'jelly_bunny'},
    {'name': 'Bunny Muffin', 'value': 1345, 'rarity': 'Rare', 'sn': 'bunny_muffin'},
    {'name': "Durov's Figurine", 'value': 136489, 'rarity': 'Rare', 'sn': 'durovs_figurine'},
    {'name': 'Love Candle', 'value': 1365, 'rarity': 'Rare', 'sn': 'love_candle'},
    {'name': 'Berry Box', 'value': 1404, 'rarity': 'Rare', 'sn': 'berry_box'},
    {'name': 'Hanging Star', 'value': 1482, 'rarity': 'Rare', 'sn': 'hanging_star'},
    {'name': 'Sakura Flower', 'value': 1560, 'rarity': 'Rare', 'sn': 'sakura_flower'},
    {'name': 'Skull Flower', 'value': 1753, 'rarity': 'Rare', 'sn': 'skull_flower'},
    {'name': 'Top Hat', 'value': 1803, 'rarity': 'Rare', 'sn': 'top_hat'},
    {'name': 'Mad Pumpkin', 'value': 1852, 'rarity': 'Rare', 'sn': 'mad_pumpkin'},
    {'name': 'Valentine Box', 'value': 1883, 'rarity': 'Rare', 'sn': 'valentine_box'},
    {'name': 'Easter Cake', 'value': 1928, 'rarity': 'Rare', 'sn': 'easter_cake'},
    {'name': 'Flying Broom', 'value': 1940, 'rarity': 'Rare', 'sn': 'flying_broom'},
    {'name': 'REDO', 'value': 27000, 'rarity': 'Rare', 'sn': 'redo'},
    {'name': 'Coffin', 'value': 3256, 'rarity': 'Rare', 'sn': 'coffin'},
    {'name': 'Eight Roses', 'value': 3300, 'rarity': 'Rare', 'sn': 'eight_roses'},
    {'name': '1 May', 'value': 4125, 'rarity': 'Rare', 'sn': 'may'},
    {'name': 'Red Star', 'value': 4125, 'rarity': 'Rare', 'sn': 'red_star'},
    {'name': 'Telegram Pin', 'value': 540225, 'rarity': 'Rare', 'sn': 'telegram_pin'},
    {'name': 'Lunar Snake', 'value': 575, 'rarity': 'Rare', 'sn': 'lunar_snake'},
    {'name': 'Chill Flame', 'value': 575, 'rarity': 'Rare', 'sn': 'chill_flame'},
    {'name': 'Case', 'value': 5775, 'rarity': 'Rare', 'sn': 'case'},
    {'name': 'Pool Float', 'value': 596, 'rarity': 'Rare', 'sn': 'pool_float'},
    {'name': 'Xmas Stocking', 'value': 602, 'rarity': 'Rare', 'sn': 'xmas_stocking'},
    {'name': 'Candy Cane', 'value': 604, 'rarity': 'Rare', 'sn': 'candy_cane'},
    {'name': 'Snake Box', 'value': 606, 'rarity': 'Rare', 'sn': 'snake_box'},
    {'name': 'Vice Cream', 'value': 608, 'rarity': 'Rare', 'sn': 'vice_cream'},
    {'name': 'Instant Ramen', 'value': 610, 'rarity': 'Rare', 'sn': 'instant_ramen'},
    {'name': 'Big Year', 'value': 614, 'rarity': 'Rare', 'sn': 'big_year'},
    {'name': 'Tama Gadget', 'value': 614, 'rarity': 'Rare', 'sn': 'tama_gadget'},
    {'name': 'Lol Pop', 'value': 618, 'rarity': 'Rare', 'sn': 'lol_pop'},
    {'name': 'Ice Cream', 'value': 624, 'rarity': 'Rare', 'sn': 'ice_cream'},
    {'name': 'Easter Egg', 'value': 625, 'rarity': 'Rare', 'sn': 'easter_egg'},
    {'name': 'Winter Wreath', 'value': 625, 'rarity': 'Rare', 'sn': 'winter_wreath'},
    {'name': 'Holiday Drink', 'value': 627, 'rarity': 'Rare', 'sn': 'holiday_drink'},
    {'name': 'Jester Hat', 'value': 643, 'rarity': 'Rare', 'sn': 'jester_hat'},
    {'name': 'Pet Snake', 'value': 643, 'rarity': 'Rare', 'sn': 'pet_snake'},
    {'name': 'Whip Cupcake', 'value': 643, 'rarity': 'Rare', 'sn': 'whip_cupcake'},
    {'name': 'Hypno Lollipop', 'value': 663, 'rarity': 'Rare', 'sn': 'hypno_lollipop'},
    {'name': 'Coconut Drink', 'value': 678, 'rarity': 'Rare', 'sn': 'coconut_drink'},
    {'name': 'Ginger Cookie', 'value': 682, 'rarity': 'Rare', 'sn': 'ginger_cookie'},
    {'name': 'Santa Hat', 'value': 682, 'rarity': 'Rare', 'sn': 'santa_hat'},
    {'name': 'Liberty Figure', 'value': 686, 'rarity': 'Rare', 'sn': 'liberty_figure'},
    {'name': 'Party Sparkler', 'value': 702, 'rarity': 'Rare', 'sn': 'party_sparkler'},
    {'name': 'Timeless Book', 'value': 702, 'rarity': 'Rare', 'sn': 'timeless_book'},
    {'name': 'Hex Pot', 'value': 729, 'rarity': 'Rare', 'sn': 'hex_pot'},
    {'name': 'Fresh Socks', 'value': 731, 'rarity': 'Rare', 'sn': 'fresh_socks'},
    {'name': 'Star Notepad', 'value': 733, 'rarity': 'Rare', 'sn': 'star_notepad'},
    {'name': 'Mood Pack', 'value': 741, 'rarity': 'Rare', 'sn': 'mood_pack'},
    {'name': 'Happy Brownie', 'value': 744, 'rarity': 'Rare', 'sn': 'happy_brownie'},
    {'name': 'Trojan Horse', 'value': 7449, 'rarity': 'Rare', 'sn': 'trojan_horse'},
    {'name': 'Victory Medal', 'value': 756, 'rarity': 'Rare', 'sn': 'victory_medal'},
    {'name': 'Jack-in-the-Box', 'value': 760, 'rarity': 'Rare', 'sn': 'jackinthebox'},
    {'name': 'Money Pot', 'value': 780, 'rarity': 'Rare', 'sn': 'money_pot'},
    {'name': 'Snow Globe', 'value': 785, 'rarity': 'Rare', 'sn': 'snow_globe'},
    {'name': 'Clover Pin', 'value': 797, 'rarity': 'Rare', 'sn': 'clover_pin'},
    {'name': 'Snow Mittens', 'value': 797, 'rarity': 'Rare', 'sn': 'snow_mittens'},
    {'name': 'Spy Agaric', 'value': 815, 'rarity': 'Rare', 'sn': 'spy_agaric'},
    {'name': 'Spiced Wine', 'value': 819, 'rarity': 'Rare', 'sn': 'spiced_wine'},
    {'name': 'Stellar Rocket', 'value': 819, 'rarity': 'Rare', 'sn': 'stellar_rocket'},
    {'name': 'Gravestone', 'value': 8246, 'rarity': 'Rare', 'sn': 'gravestone'},
    {'name': 'Desk Calendar', 'value': 834, 'rarity': 'Rare', 'sn': 'desk_calendar'},
    {'name': 'Cookie Heart', 'value': 838, 'rarity': 'Rare', 'sn': 'cookie_heart'},
    {'name': 'Bow Tie', 'value': 848, 'rarity': 'Rare', 'sn': 'bow_tie'},
    {'name': 'Witch Hat', 'value': 854, 'rarity': 'Rare', 'sn': 'witch_hat'},
    {'name': 'B-Day Candle', 'value': 858, 'rarity': 'Rare', 'sn': 'bday_candle'},
    {'name': 'Restless Jar', 'value': 858, 'rarity': 'Rare', 'sn': 'restless_jar'},
    {'name': 'Mousse Cake', 'value': 859, 'rarity': 'Rare', 'sn': 'mousse_cake'},
    {'name': 'Homemade Cake', 'value': 865, 'rarity': 'Rare', 'sn': 'homemade_cake'},
    {'name': 'Pretty Posy', 'value': 875, 'rarity': 'Rare', 'sn': 'pretty_posy'},
    {'name': 'Faith Amulet', 'value': 891, 'rarity': 'Rare', 'sn': 'faith_amulet'},
    {'name': 'Snoop Dogg', 'value': 900, 'rarity': 'Rare', 'sn': 'snoop_dogg'},
    {'name': 'Heart Pendant', 'value': 9075, 'rarity': 'Rare', 'sn': 'heart_pendant'},
    {'name': "Durov's Boots", 'value': 9426, 'rarity': 'Rare', 'sn': 'durovs_boots'},
    {'name': 'Spring Basket', 'value': 955, 'rarity': 'Rare', 'sn': 'spring_basket'},
    {'name': 'Eternal Candle', 'value': 963, 'rarity': 'Rare', 'sn': 'eternal_candle'},
    {'name': 'Input Key', 'value': 967, 'rarity': 'Rare', 'sn': 'input_key'},
    {'name': 'Swag Bag', 'value': 971, 'rarity': 'Rare', 'sn': 'swag_bag'},
    {'name': 'Crystal Ball', 'value': 2125, 'rarity': 'Epic', 'sn': 'crystal_ball'},
    {'name': 'Record Player', 'value': 2166, 'rarity': 'Epic', 'sn': 'record_player'},
    {'name': 'Snoop Cigar', 'value': 2419, 'rarity': 'Epic', 'sn': 'snoop_cigar'},
    {'name': 'Trapped Heart', 'value': 2509, 'rarity': 'Epic', 'sn': 'trapped_heart'},
    {'name': 'Love Potion', 'value': 2533, 'rarity': 'Epic', 'sn': 'love_potion'},
    {'name': 'UFC Strike', 'value': 2585, 'rarity': 'Epic', 'sn': 'ufc_strike'},
    {'name': 'Ionic Dryer', 'value': 2726, 'rarity': 'Epic', 'sn': 'ionic_dryer'},
    {'name': 'Sky Stilettos', 'value': 2788, 'rarity': 'Epic', 'sn': 'sky_stilettos'},
    {'name': 'Cupid Charm', 'value': 3297, 'rarity': 'Epic', 'sn': 'cupid_charm'},
    {'name': "Khabib's Papakha", 'value': 3630, 'rarity': 'Epic', 'sn': 'khabibs_papakha'},
    {'name': 'Rare Bird', 'value': 3676, 'rarity': 'Epic', 'sn': 'rare_bird'},
    {'name': 'Bling Binky', 'value': 3742, 'rarity': 'Epic', 'sn': 'bling_binky'},
    {'name': 'Electric Skull', 'value': 3763, 'rarity': 'Epic', 'sn': 'electric_skull'},
    {'name': 'Eternal Rose', 'value': 3945, 'rarity': 'Epic', 'sn': 'eternal_rose'},
    {'name': 'Diamond Ring', 'value': 4524, 'rarity': 'Epic', 'sn': 'diamond_ring'},
    {'name': 'Genie Lamp', 'value': 5192, 'rarity': 'Epic', 'sn': 'genie_lamp'},
    {'name': 'Signet Ring', 'value': 5194, 'rarity': 'Epic', 'sn': 'signet_ring'},
    {'name': 'Neko Helmet', 'value': 5478, 'rarity': 'Epic', 'sn': 'neko_helmet'},
    {'name': 'Perfume Bottle', 'value': 10286, 'rarity': 'Legendary', 'sn': 'perfume_bottle'},
    {'name': 'Ion Gem', 'value': 10561, 'rarity': 'Legendary', 'sn': 'ion_gem'},
    {'name': 'Westside Sign', 'value': 11475, 'rarity': 'Legendary', 'sn': 'westside_sign'},
    {'name': 'Nail Bracelet', 'value': 13500, 'rarity': 'Legendary', 'sn': 'nail_bracelet'},
    {'name': 'Loot Bag', 'value': 15319, 'rarity': 'Legendary', 'sn': 'loot_bag'},
    {'name': 'Mighty Arm', 'value': 15338, 'rarity': 'Legendary', 'sn': 'mighty_arm'},
    {'name': 'Astral Shard', 'value': 16200, 'rarity': 'Legendary', 'sn': 'astral_shard'},
    {'name': "Durov's Glasses", 'value': 18000, 'rarity': 'Legendary', 'sn': 'durovs_glasses'},
    {'name': 'Voodoo Doll', 'value': 5591, 'rarity': 'Legendary', 'sn': 'voodoo_doll'},
    {'name': 'Toy Bear', 'value': 5773, 'rarity': 'Legendary', 'sn': 'toy_bear'},
    {'name': 'Vintage Cigar', 'value': 5829, 'rarity': 'Legendary', 'sn': 'vintage_cigar'},
    {'name': 'Bonded Ring', 'value': 6085, 'rarity': 'Legendary', 'sn': 'bonded_ring'},
    {'name': 'Sharp Tongue', 'value': 6430, 'rarity': 'Legendary', 'sn': 'sharp_tongue'},
    {'name': 'Kissed Frog', 'value': 6529, 'rarity': 'Legendary', 'sn': 'kissed_frog'},
    {'name': 'Swiss Watch', 'value': 7095, 'rarity': 'Legendary', 'sn': 'swiss_watch'},
    {'name': 'Low Rider', 'value': 7924, 'rarity': 'Legendary', 'sn': 'low_rider'},
    {'name': 'Magic Potion', 'value': 8085, 'rarity': 'Legendary', 'sn': 'magic_potion'},
    {'name': 'Artisan Brick', 'value': 9012, 'rarity': 'Legendary', 'sn': 'artisan_brick'},
    {'name': 'Mini Oscar', 'value': 9158, 'rarity': 'Legendary', 'sn': 'mini_oscar'},
    {'name': 'Gem Signet', 'value': 9541, 'rarity': 'Legendary', 'sn': 'gem_signet'},
    {'name': 'Heart Locket', 'value': 105707, 'rarity': 'Mythic', 'sn': 'heart_locket'},
    {'name': 'Scared Cat', 'value': 23773, 'rarity': 'Mythic', 'sn': 'scared_cat'},
    {'name': 'Heroic Helmet', 'value': 24503, 'rarity': 'Mythic', 'sn': 'heroic_helmet'},
    {'name': 'Precious Peach', 'value': 36433, 'rarity': 'Mythic', 'sn': 'precious_peach'},
    {'name': "Durov's Cap", 'value': 44100, 'rarity': 'Mythic', 'sn': 'durovs_cap'},
    {'name': 'Plush Pepe', 'value': 556290, 'rarity': 'Mythic', 'sn': 'plush_pepe'},
]
CASES = {
    'free_daily': {'name': '🎁 FREE DAILY', 'price': 0, 'cooldown': 86400, 'category': 'free', 'icon': '🎁', 'color': 'free', 'star_drops': [1, 2, 3, 5, 7, 10], 'star_weights': [45, 28, 15, 7, 3.5, 1.5], 'bait_names': ['Swiss Watch', 'Diamond Ring', "Durov's Cap", 'Plush Pepe', 'Mini Oscar'], 'desc': 'Раз в 24ч · обычно 1–3⭐, редко до 10'},
    'star_case_1': {'name': '⭐ STAR CASE I', 'price': 55, 'category': 'stars', 'icon': '⭐', 'color': 'c-starter', 'star_drops': [15, 25, 35, 45, 55, 85, 125, 210], 'star_weights': [35, 28, 16, 9, 6, 3.5, 1.5, 1], 'desc': 'RTP ~72% · редко выше цены'},
    'star_case_2': {'name': '⭐ STAR CASE II', 'price': 110, 'category': 'stars', 'icon': '✨', 'color': 'c-starter', 'star_drops': [25, 45, 65, 90, 115, 175, 285, 460], 'star_weights': [35, 28, 16, 9, 6, 3.5, 1.5, 1], 'desc': 'RTP ~72%'},
    'star_case_3': {'name': '⭐ STAR CASE III', 'price': 280, 'category': 'stars', 'icon': '🌟', 'color': 'c-pepe', 'star_drops': [60, 100, 150, 220, 285, 430, 720, 1150], 'star_weights': [35, 28, 16, 9, 6, 3.5, 1.5, 1], 'desc': 'RTP ~72%'},
    'star_case_4': {'name': '⭐ STAR CASE IV', 'price': 550, 'category': 'stars', 'icon': '💫', 'color': 'c-tg', 'star_drops': [120, 200, 300, 430, 560, 860, 1420, 2550], 'star_weights': [35, 28, 16, 9, 6, 3.5, 1.5, 1], 'desc': 'RTP ~72%'},
    'nft_starter': {'name': '🌱 NFT STARTER', 'price': 220, 'category': 'nft', 'icon': '🌱', 'color': 'c-starter', 'rarities': ['Common', 'Uncommon', 'Rare'], 'weights': [75, 20, 5], 'min_stars': 15, 'max_stars': 60, 'stars_chance': 0.45, 'force_names': ['Мишка', 'Сердце', 'Конфета', 'Подарок', 'Ракета', 'Букет', 'Кольцо', 'Triple Meow'], 'desc': 'Чаще 15–100⭐, редко Meow'},
    'nft_candy': {'name': '🍭 CANDY NFT', 'price': 400, 'category': 'nft', 'icon': '🍭', 'color': 'c-pepe', 'rarities': ['Uncommon', 'Rare', 'Epic'], 'weights': [75, 20, 5], 'min_stars': 30, 'max_stars': 100, 'stars_chance': 0.22, 'force_names': ['Конфета', 'Candy Cane', 'Lol Pop', 'Berry Box', 'Cookie Heart', 'Whip Cupcake', 'Love Potion', 'Jelly Bunny', 'Spy Agaric'], 'desc': 'Сладкое · EV < цена, иногда 500+'},
    'nft_pepe': {'name': '🐸 PEPE BOX', 'price': 650, 'category': 'nft', 'icon': '🐸', 'color': 'c-pepe', 'rarities': ['Rare', 'Epic', 'Legendary'], 'weights': [75, 20, 5], 'min_stars': 40, 'max_stars': 150, 'stars_chance': 0.20, 'force_names': ['Мишка', 'Ракета', 'Букет', 'Кольцо', 'Алмаз', 'Jelly Bunny', 'Spy Agaric', 'Kissed Frog', 'Toy Bear'], 'desc': 'Чаще 15–100, редко топ'},
    'nft_magic': {'name': '🔮 MAGIC VAULT', 'price': 1000, 'category': 'nft', 'icon': '🔮', 'color': 'c-frag', 'rarities': ['Rare', 'Epic', 'Legendary'], 'weights': [70, 22, 8], 'min_stars': 80, 'max_stars': 280, 'stars_chance': 0.22, 'force_names': ['Мишка', 'Ракета', 'Кольцо', 'Hex Pot', 'Spy Agaric', 'Flying Broom', 'Crystal Ball', 'Love Potion', 'Eternal Rose', 'Genie Lamp', 'Magic Potion'], 'desc': 'Чаще 15–100, редко Magic Potion'},
    'brand_gucci': {'name': '👜 GUCCI DROP', 'price': 900, 'category': 'brands', 'icon': '👜', 'color': 'c-tg', 'rarities': ['Epic', 'Legendary'], 'weights': [75, 25], 'min_stars': 100, 'max_stars': 280, 'stars_chance': 0.22, 'force_names': ['Candy Cane', 'Bow Tie', 'Snoop Dogg', 'Top Hat', 'Swag Bag', 'Diamond Ring', 'Swiss Watch'], 'desc': 'От 300⭐ · редко Swiss'},
    'brand_rolex': {'name': '⌚ ROLEX CASE', 'price': 1350, 'category': 'brands', 'icon': '⌚', 'color': 'c-frag', 'rarities': ['Epic', 'Legendary', 'Mythic'], 'weights': [70, 22, 8], 'min_stars': 120, 'max_stars': 400, 'stars_chance': 0.20, 'force_names': ['Top Hat', 'Bow Tie', 'Diamond Ring', 'Signet Ring', 'Vintage Cigar', 'Swiss Watch', 'Gem Signet'], 'desc': 'Чаще среднее, редко Swiss Watch'},
    'brand_snoop': {'name': '🐕 SNOOP DROP', 'price': 800, 'category': 'brands', 'icon': '🐕', 'color': 'c-pepe', 'rarities': ['Rare', 'Epic', 'Legendary'], 'weights': [75, 20, 5], 'min_stars': 60, 'max_stars': 250, 'stars_chance': 0.22, 'force_names': ['Candy Cane', 'Snoop Dogg', 'Swag Bag', 'Snoop Cigar', 'Top Hat', 'Vintage Cigar', 'Low Rider'], 'desc': 'От 300⭐ · редко Low Rider'},
    'only_onyx': {'name': '🖤 ONYX BLACK', 'price': 1700, 'category': 'only_nft', 'icon': '🖤', 'color': 'c-durov', 'rarities': ['Epic', 'Legendary'], 'weights': [80, 20], 'stars_chance': 0.22, 'force_names': ['Candy Cane', 'Lol Pop', 'Evil Eye', 'Skull Flower', 'Top Hat', 'Electric Skull', 'Neko Helmet', 'Voodoo Doll'], 'desc': 'От 300⭐ · редко Neko/Voodoo'},
    'only_crystal': {'name': '💎 CRYSTAL VAULT', 'price': 2300, 'category': 'only_nft', 'icon': '💎', 'color': 'c-frag', 'rarities': ['Epic', 'Legendary', 'Mythic'], 'weights': [75, 20, 5], 'stars_chance': 0.22, 'force_names': ['Spy Agaric', 'Cookie Heart', 'Crystal Ball', 'Flying Broom', 'Diamond Ring', 'Ion Gem', 'Mini Oscar'], 'desc': 'От 300⭐ · редко Oscar'},
    'only_durov': {'name': '🧢 DUROV ONLY', 'price': 4000, 'category': 'only_nft', 'icon': '🧢', 'color': 'c-durov', 'rarities': ['Legendary', 'Mythic'], 'weights': [80, 20], 'stars_chance': 0.22, 'force_names': ['Top Hat', 'Crystal Ball', "Khabib's Papakha", 'Diamond Ring', 'Mini Oscar', 'Heroic Helmet', 'Precious Peach'], 'desc': 'Чаще среднее, микро Pepe/Cap'},
    'allin_pepe': {'name': '🐸 ALL-IN PEPE', 'price': 45, 'category': 'allin', 'icon': '🐸', 'color': 'c-pepe', 'allin': True, 'lose_stars': [0, 1, 2, 3, 5], 'lose_weights': [50, 25, 15, 7, 3], 'jackpot_name': 'Plush Pepe', 'jackpot_value': 1000000, 'jackpot_chance': 0.0, 'desc': '40⭐ · 99.99% ничего · Pepe не падает'},
    'allin_rolex': {'name': '⌚ ALL-IN ROLEX', 'price': 25, 'category': 'allin', 'icon': '⌚', 'color': 'c-frag', 'allin': True, 'lose_stars': [0, 1, 2], 'lose_weights': [70, 20, 10], 'jackpot_name': 'Swiss Watch', 'jackpot_value': 50000, 'jackpot_chance': 1e-05, 'desc': '25⭐ · микрошанс на Rolex'},
    'allin_cap': {'name': '🧢 ALL-IN CAP', 'price': 65, 'category': 'allin', 'icon': '🧢', 'color': 'c-durov', 'allin': True, 'lose_stars': [0, 1, 2, 5], 'lose_weights': [55, 25, 12, 8], 'jackpot_name': "Durov's Cap", 'jackpot_value': 200000, 'jackpot_chance': 5e-06, 'desc': '60⭐ · микрошанс на Cap'},
    'allin_helmet': {'name': '⛑️ ALL-IN HELMET', 'price': 90, 'category': 'allin', 'icon': '⛑️', 'color': 'c-durov', 'allin': True, 'lose_stars': [0, 1, 3], 'lose_weights': [60, 25, 15], 'jackpot_name': 'Heroic Helmet', 'jackpot_value': 150000, 'jackpot_chance': 8e-06, 'desc': '80⭐ · микрошанс на Helmet'},
    'rich_gold': {'name': '👑 GOLD RICH', 'price': 1350, 'category': 'rich', 'icon': '👑', 'color': 'c-durov', 'rarities': ['Epic', 'Legendary', 'Mythic'], 'weights': [75, 20, 5], 'min_stars': 200, 'max_stars': 800, 'stars_chance': 0.20, 'force_names': ['Candy Cane', 'Snoop Dogg', 'Evil Eye', 'Top Hat', 'Crystal Ball', 'Diamond Ring', 'Swiss Watch'], 'desc': 'От 300⭐ · редко Swiss'},
    'rich_diamond': {'name': '💎 DIAMOND RICH', 'price': 2850, 'category': 'rich', 'icon': '💎', 'color': 'c-frag', 'rarities': ['Legendary', 'Mythic'], 'weights': [80, 20], 'min_stars': 400, 'max_stars': 1500, 'stars_chance': 0.18, 'force_names': ['Evil Eye', 'Top Hat', 'Crystal Ball', 'Diamond Ring', 'Swiss Watch', 'Mini Oscar', 'Ion Gem'], 'desc': 'От 300⭐ · редко Ion'},
    'rich_mythic': {'name': '☄️ MYTHIC RICH', 'price': 5750, 'category': 'rich', 'icon': '☄️', 'color': 'c-durov', 'rarities': ['Legendary', 'Mythic'], 'weights': [80, 20], 'min_stars': 800, 'max_stars': 3000, 'stars_chance': 0.18, 'force_names': ['Top Hat', 'Crystal Ball', 'Flying Broom', 'Diamond Ring', 'Swiss Watch', 'Mini Oscar', 'Ion Gem', 'Heroic Helmet'], 'desc': 'От 300⭐ · редко Helmet'},
    'rich_durov': {'name': '🔥 DUROV RICH', 'price': 9200, 'category': 'rich', 'icon': '🔥', 'color': 'c-durov', 'rarities': ['Mythic'], 'weights': [100], 'min_stars': 1500, 'max_stars': 8000, 'stars_chance': 0.16, 'force_names': ['Crystal Ball', 'Diamond Ring', 'Swiss Watch', 'Mini Oscar', 'Ion Gem', 'Heroic Helmet', 'Precious Peach', "Durov's Cap"], 'desc': 'От 300⭐ · микро Cap'},
    'snoop_pack': {'name': '🐕 SNOOP PACK', 'price': 800, 'category': 'nft', 'icon': '🐕', 'color': 'c-pepe', 'force_names': ['Candy Cane', 'Lol Pop', 'Snoop Dogg', 'Swag Bag', 'Snoop Cigar', 'Top Hat'], 'desc': 'От 300⭐ · редко Cigar'},
    'snake_2025': {'name': '🐍 SNAKE 2025', 'price': 500, 'category': 'nft', 'icon': '🐍', 'color': 'c-starter', 'force_names': ['Конфета', 'Ёлка', 'Candy Cane', 'Lunar Snake', 'Pet Snake', 'Snake Box'], 'desc': 'Чаще мало, редко Snake'},
    'meow_case': {'name': '🐱 MEOW CASE', 'price': 400, 'category': 'nft', 'icon': '🐱', 'color': 'c-starter', 'force_names': ['Мишка', 'Сердце', 'Triple Meow', 'Jelly Bunny', 'Bunny Muffin', 'Toy Bear', 'Kissed Frog'], 'desc': 'Много мишек, иногда Meow/Frog'},
    'ramen_drop': {'name': '🍜 RAMEN DROP', 'price': 450, 'category': 'nft', 'icon': '🍜', 'color': 'c-pepe', 'force_names': ['Торт', 'Подарок', 'Instant Ramen', 'Vice Cream', 'Berry Box', 'Whip Cupcake', 'Cookie Heart'], 'desc': 'Еда · дом в плюсе, редко 500+'},
    'xmas_case': {'name': '🎄 XMAS CASE', 'price': 400, 'category': 'nft', 'icon': '🎄', 'color': 'c-tg', 'force_names': ['Ёлка', 'Конфета', 'Xmas Stocking', 'Candy Cane', 'Santa Hat', 'Jolly Chimp', 'Holiday Drink'], 'desc': 'НГ · чаще дешёвое, иногда выше цены'},
    'float_party': {'name': '🏊 FLOAT PARTY', 'price': 400, 'category': 'nft', 'icon': '🏊', 'color': 'c-starter', 'force_names': ['Шампанское', 'Торт', 'Lol Pop', 'B-Day Candle', 'Pool Float', 'Party Sparkler', 'Ice Cream'], 'desc': 'Праздник · иногда выше 400'},
    'starter_plus': {'name': '🌱 STARTER+', 'price': 150, 'category': 'nft', 'icon': '🌱', 'color': 'c-starter', 'force_names': ['Мишка', 'Сердце', 'Конфета', 'Подарок', 'Ракета', 'Букет', 'Кольцо', 'Алмаз', 'Кубок', 'Triple Meow'], 'desc': 'Чаще 15–50⭐, иногда 100, редко Meow'},
    'flame_case': {'name': '🔥 FLAME CASE', 'price': 900, 'category': 'nft', 'icon': '🔥', 'color': 'c-durov', 'force_names': ['Chill Flame', 'Candy Cane', 'Spy Agaric', 'Crystal Ball', 'Flying Broom', 'Top Hat'], 'desc': 'От 300⭐ · редко Broom'},
    'bednyy_shkolnik': {'name': '🎒 БЕДНЫЙ ШКОЛЬНИК', 'price': 80, 'category': 'nft', 'icon': '🎒', 'color': 'c-starter', 'force_names': ['Мишка', 'Сердце', 'Конфета', 'Подарок', 'Ракета', 'Букет'], 'desc': 'Маленький бюджет · чаще 15–50⭐'},
    'bogach': {'name': '💼 БОГАЧ', 'price': 2500, 'category': 'rich', 'icon': '💼', 'color': 'c-durov', 'force_names': ['Top Hat', 'Diamond Ring', 'Swiss Watch', 'Mini Oscar', 'Ion Gem', 'Crystal Ball', 'Spy Agaric'], 'desc': 'Дорого · EV < цена, иногда топ'},
    'master': {'name': '🎯 МАСТЕР', 'price': 1200, 'category': 'nft', 'icon': '🎯', 'color': 'c-frag', 'force_names': ['Кольцо', 'Алмаз', 'Spy Agaric', 'Jelly Bunny', 'Crystal Ball', 'Flying Broom', 'Top Hat', 'Love Potion'], 'desc': 'Средний риск · баланс EV'},
    'dep_500': {'name': '🎁 ДЕПО 500', 'price': 0, 'category': 'deposit', 'icon': '🎁', 'color': 'free', 'require_deposit': 500, 'force_names': ['Мишка', 'Сердце', 'Конфета', 'Подарок', 'Ракета', 'Букет', 'Кольцо', 'Triple Meow'], 'desc': 'За депозит 500⭐ · разово'},
    'dep_1000': {'name': '🎁 ДЕПО 1000', 'price': 0, 'category': 'deposit', 'icon': '🎁', 'color': 'free', 'require_deposit': 1000, 'force_names': ['Мишка', 'Конфета', 'Кольцо', 'Алмаз', 'Triple Meow', 'Candy Cane', 'Lol Pop', 'Jelly Bunny'], 'desc': 'За депозит 1000⭐ · разово'},
    'dep_5000': {'name': '🎁 ДЕПО 5000', 'price': 0, 'category': 'deposit', 'icon': '💎', 'color': 'free', 'require_deposit': 5000, 'force_names': ['Candy Cane', 'Lol Pop', 'Spy Agaric', 'Jelly Bunny', 'Crystal Ball', 'Top Hat', 'Love Potion'], 'desc': 'За депозит 5000⭐ · разово'},
    'dep_15000': {'name': '🎁 ДЕПО 15000', 'price': 0, 'category': 'deposit', 'icon': '👑', 'color': 'free', 'require_deposit': 15000, 'force_names': ['Top Hat', 'Crystal Ball', 'Diamond Ring', 'Swiss Watch', 'Mini Oscar', 'Ion Gem', 'Spy Agaric'], 'desc': 'За депозит 15000⭐ · разово'},
    'free_friend': {'name': '👥 ЗА ДРУГА', 'price': 0, 'category': 'free', 'icon': '👥', 'color': 'free', 'require_share': True, 'min_stars': 1, 'max_stars': 25, 'stars_bias_low': True, 'stars_chance': 1.0, 'desc': 'Поделись ссылкой с другом · 1 раз / 12ч'},
    'spider_case': {'name': '🕷️ SPIDER-MAN', 'price': 350, 'category': 'themed', 'icon': '🕷️', 'color': 'c-durov', 'theme': 'spider', 'force_names': ['Мишка', 'Сердце', 'Конфета', 'Ракета', 'Кольцо', 'Алмаз', 'Spy Agaric', 'Jelly Bunny', 'Lol Pop', 'Candy Cane', 'Evil Eye', 'Top Hat'], 'desc': 'Паучок · 70% не окуп'},
    'batman_case': {'name': '🦇 BATMAN', 'price': 420, 'category': 'themed', 'icon': '🦇', 'color': 'c-tg', 'theme': 'batman', 'force_names': ['Мишка', 'Подарок', 'Букет', 'Кольцо', 'Алмаз', 'Кубок', 'Spy Agaric', 'Crystal Ball', 'Top Hat', 'Electric Skull', 'Skull Flower', 'Voodoo Doll'], 'desc': 'Тёмный рыцарь · EV < цена'},
    'bobik_case': {'name': '🐶 БОБИК', 'price': 180, 'category': 'themed', 'icon': '🐶', 'color': 'c-starter', 'theme': 'bobik', 'force_names': ['Мишка', 'Сердце', 'Конфета', 'Подарок', 'Ракета', 'Букет', 'Торт', 'Кольцо', 'Triple Meow', 'Jelly Bunny'], 'desc': 'Собачий кейс · дешёвый'},
    'spongebob_case': {'name': '🧽 ГУБКА БОБ', 'price': 260, 'category': 'themed', 'icon': '🧽', 'color': 'c-frag', 'theme': 'spongebob', 'force_names': ['Конфета', 'Торт', 'Шампанское', 'Lol Pop', 'Candy Cane', 'Berry Box', 'Whip Cupcake', 'Cookie Heart', 'Instant Ramen', 'Pool Float'], 'desc': 'Бикини Боттом · весело'},
    'ironman_case': {'name': '🤖 IRON MAN', 'price': 900, 'category': 'themed', 'icon': '🤖', 'color': 'c-durov', 'theme': 'ironman', 'force_names': ['Кольцо', 'Алмаз', 'Spy Agaric', 'Crystal Ball', 'Top Hat', 'Diamond Ring', 'Flying Broom', 'Ion Gem', 'Swiss Watch', 'Mini Oscar'], 'desc': 'Броня · редко топ'},
    'joker_case': {'name': '🃏 JOKER', 'price': 550, 'category': 'themed', 'icon': '🃏', 'color': 'c-pepe', 'theme': 'joker', 'force_names': ['Мишка', 'Конфета', 'Evil Eye', 'Skull Flower', 'Electric Skull', 'Voodoo Doll', 'Spy Agaric', 'Top Hat', 'Crystal Ball', 'Love Potion'], 'desc': 'Хаос · 70% слив'},
    'mario_case': {'name': '🍄 MARIO', 'price': 300, 'category': 'themed', 'icon': '🍄', 'color': 'c-durov', 'theme': 'mario', 'force_names': ['Мишка', 'Конфета', 'Торт', 'Ракета', 'Кольцо', 'Spy Agaric', 'Lol Pop', 'Candy Cane', 'Cookie Heart', 'Berry Box'], 'desc': 'Грибное королевство'},
    'pokemon_case': {'name': '⚡ POKÉMON', 'price': 480, 'category': 'themed', 'icon': '⚡', 'color': 'c-frag', 'theme': 'pokemon', 'force_names': ['Мишка', 'Сердце', 'Ракета', 'Кольцо', 'Алмаз', 'Jelly Bunny', 'Spy Agaric', 'Crystal Ball', 'Top Hat', 'Ion Gem', 'Electric Skull'], 'desc': 'Поймал? EV < цена'},
    'naruto_case': {'name': '🍥 NARUTO', 'price': 380, 'category': 'themed', 'icon': '🍥', 'color': 'c-tg', 'theme': 'naruto', 'force_names': ['Мишка', 'Конфета', 'Подарок', 'Кольцо', 'Кубок', 'Spy Agaric', 'Flying Broom', 'Crystal Ball', 'Top Hat', 'Evil Eye'], 'desc': 'Ниндзя · баланс'},
    'freeze_case': {'name': '❄️ FREEZE', 'price': 700, 'category': 'themed', 'icon': '❄️', 'color': 'c-tg', 'theme': 'freeze', 'force_names': ['Кольцо', 'Алмаз', 'Spy Agaric', 'Crystal Ball', 'Ice Cream', 'Top Hat', 'Diamond Ring', 'Swiss Watch', 'Ion Gem', 'Love Potion'], 'desc': 'Лёд · редко дорогой'},
}
EMOJI = {
    "Мишка": "🧸", "Сердце": "❤️", "Конфета": "🍭", "Подарок": "🎁",
    "Звезда": "⭐", "Торт": "🎂", "Ракета": "🚀", "Букет": "💐",
    "Plush Pepe": "🐸", "Durov's Cap": "🧢", "Swiss Watch": "⌚",
}

def gift_short_name(name: str) -> str:
    key = (name or "").lower().strip()
    if key in GIFT_SN_CDN:
        return GIFT_SN_CDN[key]
    s = key
    for ch in ["'", "’", "-", "."]:
        s = s.replace(ch, "")
    s = "".join(c if c.isalnum() or c == " " else "" for c in s)
    return "_".join(s.split()) or "toy_bear"

def gift_img_url(name: str, sn: str = None) -> str:
    try:
        key = (name or "").lower().strip()
        if key in GIFT_SN_CDN:
            sn = GIFT_SN_CDN[key]
        elif not sn:
            sn = gift_short_name(name)
        sn = (sn or "toy_bear").lower().replace(" ", "_").replace("'", "")
        if sn in GIFT_SN_CDN:
            sn = GIFT_SN_CDN[sn]
        return f"{CDN}/{sn}.webp"
    except Exception:
        return f"{CDN}/toy_bear.webp"

def gift_public(g: dict) -> dict:
    name = g.get("name") or "Gift"
    sn = g.get("sn") or gift_short_name(name)
    rarity = g.get("rarity") or "Common"
    value = int(g.get("value") or 0)
    return {
        "name": name,
        "sn": sn,
        "value": value,
        "rarity": rarity,
        "emoji": g.get("emoji") or EMOJI.get(name, "🎁"),
        "img": g.get("img") or gift_img_url(name, sn),
        "regular": bool(g.get("regular", rarity == "Common")),
    }

_BY_NAME: Dict[str, dict] = {}
_BY_RARITY: Dict[str, List[dict]] = {}
for _g in GIFTS_FLAT:
    gp = gift_public(_g)
    _BY_NAME[gp["name"].lower()] = gp
    _BY_RARITY.setdefault(gp["rarity"], []).append(gp)

def find_gift(name: str) -> Optional[dict]:
    if not name:
        return None
    return _BY_NAME.get(name.lower().strip())

def gifts_grouped() -> dict:
    order = ["Common", "Uncommon", "Rare", "Epic", "Legendary", "Mythic"]
    out = {}
    for r in order:
        out[r] = [dict(x) for x in _BY_RARITY.get(r, [])]
    return out

def shop_items() -> List[dict]:
    items = []
    seen = set()
    for g in GIFTS_FLAT:
        n = (g.get("name") or "").strip()
        if not n or n.lower() in seen:
            continue
        v = int(g.get("value") or 0)
        # почти весь каталог в магазине
        if v < 15 or v > 600000:
            continue
        seen.add(n.lower())
        pub = gift_public(g)
        pub["price"] = max(1, int(round(v * SHOP_MARKUP)))
        items.append(pub)
    items.sort(key=lambda x: x["price"])
    return items

def weighted_pick(items, weights):
    if not items:
        return None
    w = [max(0.0, float(x)) for x in (weights or [1] * len(items))]
    if len(w) < len(items):
        w += [1] * (len(items) - len(w))
    s = sum(w) or 1
    r = random.random() * s
    acc = 0.0
    for it, ww in zip(items, w):
        acc += ww
        if r <= acc:
            return it
    return items[-1]

def calc_upgrade_chance(iv: float, tv: float, bonus: float = 0.0) -> float:
    """Реальный шанс (ролл). На колесе показываем display — чуть выше."""
    iv = float(iv or 0); tv = float(tv or 1)
    if tv <= 0 or iv <= 0:
        return 0.01
    raw = (iv / tv) * 100.0
    # x2 ≈ 43% — реже заходит
    ch = max(0.01, min(72.0, raw * 0.86))
    ch = min(80.0, ch + min(2.0, float(bonus or 0)))
    return round(ch, 2)

def calc_upgrade_display(iv: float, tv: float, bonus: float = 0.0) -> float:
    """Что видит игрок на колесе (x2 ≈ 47%)."""
    iv = float(iv or 0); tv = float(tv or 1)
    if tv <= 0 or iv <= 0:
        return 0.01
    raw = (iv / tv) * 100.0
    ch = max(0.01, min(80.0, raw * 0.94))
    ch = min(90.0, ch + min(2.0, float(bonus or 0)))
    return round(ch, 2)

def roll_crash_point() -> float:
    r = random.SystemRandom().random()
    # ~4% краш 1.00x, фактор 0.95 → RTP ~95–96%, оборот важнее edge
    if r < 0.04:
        return 1.0
    p = max(1.01, 0.95 / (1 - r))
    return min(100.0, round(p * 100) / 100)

def mines_mult(mines: int, opened: int) -> float:
    total = 25
    mult = 1.0
    for i in range(opened):
        remaining = total - i
        safe = remaining - mines
        if safe <= 0:
            break
        mult *= remaining / safe
    return max(1.0, mult * (1 - max(HOUSE_EDGE, 0.05)))  # мины: RTP ~95%, edge ~5%

def now_ts() -> int:
    return int(time.time())

def fair_commit(secret: str) -> dict:
    h = hashlib.sha256(secret.encode()).hexdigest()
    return {"hash": h, "secret": secret}

# -------------------- AUTH --------------------
def _parse_init_data(raw: str) -> dict:
    """Validate Telegram WebApp initData according to Telegram's HMAC scheme."""
    if not raw:
        raise HTTPException(401, "Telegram initData missing")
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    if raw in ("dev", "Bearer dev"):
        if not ALLOW_DEV_AUTH:
            raise HTTPException(401, "dev auth disabled")
        return {"id": 100001, "username": "Demo", "first_name": "Demo"}
    try:
        from urllib.parse import parse_qsl, unquote
        pairs = parse_qsl(raw, keep_blank_values=True)
        data = dict(pairs)
        received_hash = data.pop("hash", "")
        if not received_hash:
            raise HTTPException(401, "bad initData: hash missing")
        if not BOT_TOKEN:
            raise HTTPException(500, "BOT_TOKEN is not configured on Render")
        check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()
        calculated = hmac.new(secret_key, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated, received_hash):
            raise HTTPException(401, "bad initData")
        user_raw = data.get("user", "")
        if not user_raw:
            raise HTTPException(401, "Telegram user missing")
        user = json.loads(user_raw)
        tg_id = int(user.get("id") or 0)
        if not tg_id:
            raise HTTPException(401, "Telegram user id missing")
        return {"id": tg_id, "username": user.get("username") or user.get("first_name") or "Player", "first_name": user.get("first_name") or "Player"}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(401, "bad initData") from exc

async def current_user(
    authorization: Optional[str] = Header(None),
    x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data"),
) -> dict:
    raw = x_telegram_init_data or authorization or "dev"
    info = _parse_init_data(raw)
    tg_id = int(info.get("id") or 0)
    if not tg_id:
        raise HTTPException(401, "no tg id")
    username = str(info.get("username") or info.get("first_name") or "Player")[:64]
    async with get_db() as db:
        row = await (await db.execute("SELECT tg_id, username, balance, inventory, games, wins, deposited, free_case_at, friend_case_at, chance_bonus, ton_wallet, cases_opened FROM users WHERE tg_id=?", (tg_id,))).fetchone()
        if not row:
            await db.execute(
                "INSERT INTO users(tg_id,username,balance,inventory,games,wins,deposited,free_case_at,friend_case_at,chance_bonus,ton_wallet,cases_opened,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tg_id, username, STARTING_BALANCE, "[]", 0, 0, 0, 0, 0, 0, "", 0, now_ts()),
            )
            await db.commit()
            row = (tg_id, username, STARTING_BALANCE, "[]", 0, 0, 0, 0, 0, 0, "", 0)
        else:
            if username and username != (row[1] or ""):
                await db.execute("UPDATE users SET username=? WHERE tg_id=?", (username, tg_id))
                await db.commit()
        inv = []
        try:
            inv = json.loads(row[3] or "[]")
            if not isinstance(inv, list):
                inv = []
        except Exception:
            inv = []
        return {
            "tg_id": int(row[0]),
            "username": row[1] or username,
            "balance": int(row[2] or 0),
            "inventory": inv,
            "games": int(row[4] or 0),
            "wins": int(row[5] or 0),
            "deposited": int(row[6] or 0),
            "free_case_at": int(row[7] or 0),
            "friend_case_at": int(row[8] or 0),
            "chance_bonus": float(row[9] or 0),
            "ton_wallet": row[10] or "",
            "cases_opened": int(row[11] or 0),
            "is_admin": int(row[0]) == ADMIN_TG_ID,
        }

async def save_user(u: dict, extra_sql=None):
    async with get_db() as db:
        await db.execute(
            "UPDATE users SET username=?, balance=?, inventory=?, games=?, wins=?, deposited=?, free_case_at=?, friend_case_at=?, chance_bonus=?, ton_wallet=?, cases_opened=? WHERE tg_id=?",
            (
                u["username"], int(u["balance"]), json.dumps(u["inventory"], ensure_ascii=False),
                int(u["games"]), int(u["wins"]), int(u["deposited"]),
                int(u["free_case_at"]), int(u["friend_case_at"]),
                float(u.get("chance_bonus") or 0), u.get("ton_wallet") or "",
                int(u.get("cases_opened") or 0), int(u["tg_id"]),
            ),
        )
        await db.commit()

async def add_history(tg_id: int, game: str, result: str, detail: str, amount: int = 0):
    async with get_db() as db:
        await db.execute(
            "INSERT INTO history(tg_id,game,result,detail,amount,created_at) VALUES(?,?,?,?,?,?)",
            (tg_id, game, result, detail, int(amount), now_ts()),
        )
        await db.commit()

async def bump_quest(tg_id: int, quest_id: str, n: int = 1):
    async with get_db() as db:
        row = await (await db.execute("SELECT progress, claimed FROM quests WHERE tg_id=? AND quest_id=?", (tg_id, quest_id))).fetchone()
        if not row:
            await db.execute("INSERT INTO quests(tg_id,quest_id,progress,claimed) VALUES(?,?,?,0)", (tg_id, quest_id, n))
        else:
            await db.execute("UPDATE quests SET progress=? WHERE tg_id=? AND quest_id=?", (int(row[0] or 0) + n, tg_id, quest_id))
        await db.commit()

async def push_live(item: dict, user_name: str):
    async with get_db() as db:
        await db.execute(
            "INSERT INTO live_drops(name,emoji,img,user_name,value,created_at) VALUES(?,?,?,?,?,?)",
            (item.get("name"), item.get("emoji") or "🎁", item.get("img") or "", user_name, int(item.get("value") or 0), now_ts()),
        )
        await db.commit()
    try:
        await sio.emit("live_win", {"name": item.get("name"), "user": user_name})
    except Exception:
        pass

def require_admin(u: dict):
    if not u.get("is_admin"):
        raise HTTPException(403, "admin only")

# -------------------- CASE ROLLS --------------------
def _slot_targets(price: int) -> list:
    """8 слотов относительно цены кейса (таблица экономики)."""
    p = max(1, int(price or 1))
    bands = [
        (0.24, 0.30),  # ~27% (+2% к жёсткому)
        (0.34, 0.40),  # ~37%
        (0.45, 0.55),  # ~50%
        (0.60, 0.72),  # ~66%
        (0.95, 1.08),  # ~окуп
        (1.35, 1.65),  # плюс
        (2.10, 2.80),  # большой
        (4.00, 5.20),  # джекпот
    ]
    return [max(1, int(round(p * (lo + hi) / 2))) for lo, hi in bands]


SLOT_WEIGHTS = [35, 28, 16, 9, 6, 3.5, 1.5, 1]  # чуть мягче жёсткой: +1–3% к средним/окупу


def _gift_in_band(target: int, names_pool: list | None = None):
    """Только предмет с value ≈ target (±30%). Без 'левых' Meow мимо слота."""
    target = max(1, int(target))
    lo = max(1, int(target * 0.68))
    hi = int(target * 1.35)
    # 1) force_names в полосе
    pool = []
    if names_pool:
        for g in names_pool:
            v = int(g.get("value") or 0)
            if lo <= v <= hi:
                pool.append(g)
    # 2) весь каталог в полосе
    if not pool:
        pool = [g for g in GIFTS_FLAT if lo <= int(g.get("value") or 0) <= hi]
    # 3) ближайшие 8 из каталога
    if not pool:
        pool = sorted(GIFTS_FLAT, key=lambda g: abs(int(g.get("value") or 0) - target))[:8]
    ww = []
    for g in pool:
        v = int(g.get("value") or 0)
        dist = abs(v - target) + 10
        # чуть чаще чуть дешевле target (edge)
        ww.append((1.25 if v <= target else 1.0) / dist)
    g = weighted_pick(pool, ww) or pool[0]
    return gift_public(g)


def roll_case_drop(case_id: str, c: dict) -> dict:
    """Шансы строго как таблица: 38/30/15/8/5/2.5/1/0.5 от цены кейса."""
    price = int(c.get("price") or 0)

    # FREE — только 1–10⭐
    if case_id == "free_daily" or (price <= 0 and c.get("category") == "free"):
        drops = c.get("star_drops") or [1, 2, 3, 5, 7, 10]
        weights = c.get("star_weights") or [45, 28, 15, 7, 3.5, 1.5]
        stars = max(1, min(10, int(weighted_pick(drops, weights) or 1)))
        return {"kind": "stars", "stars": stars}

    # ALL-IN
    if c.get("allin") or c.get("category") == "allin":
        jp_name = c.get("jackpot_name")
        jp_chance = float(c.get("jackpot_chance") or 0)
        if jp_name and random.random() * 100 < jp_chance:
            g = find_gift(jp_name) or {
                "name": jp_name,
                "value": int(c.get("jackpot_value") or 0),
                "rarity": "Mythic",
                "sn": gift_short_name(jp_name),
            }
            return {"kind": "gift", "gift": gift_public(g)}
        lose = c.get("lose_stars") or [0, 1, 2, 3, 5]
        lw = c.get("lose_weights") or [50, 25, 15, 7, 3]
        return {"kind": "stars", "stars": int(weighted_pick(lose, lw) or 0), "allin_lose": True}

    # STAR — только ⭐ по star_drops / star_weights
    star_drops = c.get("star_drops") or []
    is_star = c.get("category") == "stars" or (
        star_drops and not c.get("force_names") and not c.get("rarities") and price > 0
    )
    if is_star and star_drops:
        sw = c.get("star_weights") or SLOT_WEIGHTS
        stars = int(weighted_pick(star_drops, sw) or star_drops[0])
        return {"kind": "stars", "stars": stars}

    # NFT / brands / themed — слот → value → NFT только в полосе value
    if price > 0:
        targets = list(c.get("value_drops") or _slot_targets(price))[:8]
        while len(targets) < 8:
            targets.append(targets[-1] if targets else price)
        targets = [int(x) for x in targets]
        slot = int(weighted_pick(list(range(8)), SLOT_WEIGHTS) or 0)
        target = targets[slot]

        names_pool = []
        for n in (c.get("force_names") or []):
            g = find_gift(n)
            if g:
                names_pool.append(g)

        gift = _gift_in_band(target, names_pool)
        # жёсткий clamp: если всё равно вылезло мимо полосы — stars ≈ target (редко) или перекат
        gv = int(gift.get("value") or 0)
        lo, hi = int(target * 0.62), int(target * 1.42)
        if gv < lo or gv > hi:
            # 85% — звёзды на сумму target (не NFT мимо таблицы)
            if random.random() < 0.85:
                return {"kind": "stars", "stars": max(1, target)}
            gift = _gift_in_band(target, None)
        return {"kind": "gift", "gift": gift}

    return {"kind": "stars", "stars": random.randint(1, 5)}


def case_contents(case_id: str, c: dict) -> List[dict]:
    items = []
    if c.get("star_drops"):
        for s, w in zip(c["star_drops"], c.get("star_weights") or [1]*len(c["star_drops"])):
            items.append({"name": f"⭐ {s}", "value": int(s), "rarity": "Common", "emoji": "⭐", "img": "", "drop_chance": None})
    # превью 8 слотов по цене — чтобы в кейсе было видно «как в таблице»
    price = int(c.get("price") or 0)
    if price > 0 and c.get("category") not in ("stars", "allin", "free") and not c.get("star_drops"):
        for i, t in enumerate(_slot_targets(price)):
            g = _gift_in_band(t, None)
            items.append({**g, "drop_chance": SLOT_WEIGHTS[i] if i < len(SLOT_WEIGHTS) else None})
    for n in (c.get("force_names") or []):
        g = find_gift(n) or gift_public({"name": n, "value": 50, "rarity": "Common"})
        items.append({**gift_public(g), "drop_chance": None})
    if c.get("jackpot_name"):
        g = find_gift(c["jackpot_name"]) or gift_public({"name": c["jackpot_name"], "value": int(c.get("jackpot_value") or 0), "rarity": "Mythic"})
        items.append({**gift_public(g), "drop_chance": c.get("jackpot_chance")})
    for r in (c.get("rarities") or []):
        for g in _BY_RARITY.get(r, [])[:12]:
            items.append({**gift_public(g), "drop_chance": None})
    # unique by name
    seen=set(); out=[]
    for it in items:
        k=(it.get("name") or "").lower()
        if k in seen: continue
        seen.add(k); out.append(it)
    return out[:60]

QUESTS_DEF = [
    {"id": "upg_5", "title": "Прокрути апгрейд 5 раз", "need": 5, "reward": 30},
    {"id": "case_3", "title": "Открой 3 кейса", "need": 3, "reward": 25},
    {"id": "mines_2", "title": "Сыграй в мины 2 раза", "need": 2, "reward": 20},
    {"id": "pvp_1", "title": "Сыграй 1 PvP", "need": 1, "reward": 20},
    {"id": "sell_3", "title": "Продай 3 предмета", "need": 3, "reward": 15},
    {"id": "upg_15", "title": "Апгрейд 15 раз", "need": 15, "reward": 80},
]


# -------------------- APP --------------------
app = FastAPI(title="GiftUpgrader")
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
socket_app = socketio.ASGIApp(sio, app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

@app.on_event("startup")
async def _startup():
    await init_db()
    asyncio.create_task(crash_loop())

@app.get("/", response_class=HTMLResponse)
async def root():
    if HTML_PATH.exists():
        return HTMLResponse(HTML_PATH.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>GiftUpgrader API</h1><p>Put index.html next to main.py</p>")

@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    return HTMLResponse("""<!doctype html><meta charset=utf-8><title>Admin</title>
    <body style="font-family:sans-serif;background:#0b0f1a;color:#e8ecf4;padding:24px">
    <h2>GiftUpgrader admin</h2>
    <p>Use the Mini App profile panel (your Telegram id must match ADMIN_TG_ID).</p>
    </body>""")

@app.get("/api/health")
async def health():
    # also expose safe flags for ops
    ok = True; err = None
    try:
        async with get_db() as db:
            await db.execute("SELECT 1")
    except Exception as e:
        ok = False; err = str(e)
    return {"ok": ok, "db": "postgres" if USE_POSTGRES else "sqlite", "error": err,
        "dev_auth": ALLOW_DEV_AUTH,
        "ton_mode": TON_DEPOSIT_MODE,
        "max_bet": MAX_BET,
        "max_win": MAX_WIN}

@app.get("/api/rates")
async def rates():
    return {"stars_per_ton": TON_STARS_PER_TON, "treasury": TON_TREASURY}

@app.get("/tonconnect-manifest.json")
async def ton_manifest():
    return {
        "url": os.getenv("PUBLIC_URL") or "https://t.me",
        "name": "GiftUpgrader",
        "iconUrl": f"{CDN}/toy_bear.webp",
    }

@app.get("/api/ton/config")
async def ton_config():
    return {"treasury": TON_TREASURY, "stars_per_ton": TON_STARS_PER_TON, "mode": TON_DEPOSIT_MODE}

@app.get("/api/gifts")
async def get_gifts():
    try:
        return {"gifts": gifts_grouped()}
    except Exception as e:
        print("[gifts]", e)
        return {"gifts": {"Common": [], "Rare": []}}

@app.get("/api/cases")
async def get_cases():
    return CASES

@app.get("/api/case/{case_id}/contents")
async def case_contents_api(case_id: str):
    c = CASES.get(case_id)
    if not c:
        raise HTTPException(404, "case not found")
    return {"items": case_contents(case_id, c)}

@app.get("/api/shop")
async def shop_list():
    try:
        return {"items": shop_items()}
    except Exception as e:
        print("[shop]", e)
        return {"items": []}

@app.get("/api/profile")
async def profile(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    free_ok = (now_ts() - int(u["free_case_at"] or 0)) >= 86400
    return {
        "tg_id": u["tg_id"],
        "username": u["username"],
        "balance": u["balance"],
        "inventory": u["inventory"],
        "games_played": u["games"],
        "wins": u["wins"],
        "deposited": u["deposited"],
        "free_case_available": free_ok,
        "is_admin": u["is_admin"],
        "chance_bonus": u["chance_bonus"],
        "cases_opened": u["cases_opened"],
    }

@app.get("/api/inventory")
async def inventory_get(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    return {"inventory": u["inventory"], "balance": u["balance"]}

# ----- models -----
class CaseOpenRequest(BaseModel):
    case_id: str

class UpgradeRequest(BaseModel):
    item_index: int
    target_value: int
    target_name: Optional[str] = None

class SellItemRequest(BaseModel):
    item_index: int

class ShopBuyRequest(BaseModel):
    name: str

class MinesStartRequest(BaseModel):
    bet: int
    mines: int

class MinesOpenRequest(BaseModel):
    game_id: str
    cell: int

class MinesCashoutRequest(BaseModel):
    game_id: str

class DepositRequest(BaseModel):
    amount: int

class DepositConfirmRequest(BaseModel):
    payload: str

class WithdrawRequest(BaseModel):
    amount: int
    username: str = ""
    wallet: Optional[str] = None
    method: str = "stars"
    dest: str = ""
    note: str = ""

class PvpCreateRequest(BaseModel):
    bet: int

class PvpJoinRequest(BaseModel):
    lobby_id: str
    bet: int = 0

class PvpStartRequest(BaseModel):
    lobby_id: str

class PromoActivate: pass

class AdminGiveRequest(BaseModel):
    user_id: int
    amount: int

class AdminTakeRequest(BaseModel):
    user_id: int
    amount: int = 0
    item_index: int = -1

class AdminChanceRequest(BaseModel):
    user_id: int
    chance_bonus: float = 0

class AdminWithdrawStatusRequest(BaseModel):
    withdraw_id: int
    status: str

class PromoCreateRequest(BaseModel):
    code: str
    reward_type: str = "stars"
    stars: int = 50
    max_uses: int = 100

class TonWalletRequest(BaseModel):
    address: str

class TonDepositRequest(BaseModel):
    amount_ton: float
    boc: str = ""
    address: str = ""

class CaseBuyTonRequest(BaseModel):
    case_id: str
    boc: str = ""

class GivePrizeRequest(BaseModel):
    user_id: int
    prize_type: str = "gift"
    name: str = ""
    value: int = 0
    rarity: str = "Epic"

class QuestClaimRequest(BaseModel):
    quest_id: str

class ShareClaimRequest(BaseModel):
    case_id: str = "free_friend"

class TonCheckRequest(BaseModel):
    deposit_id: str

# ----- shop / upgrade / cases -----
@app.post("/api/shop/buy")
async def shop_buy(body: ShopBuyRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    items = shop_items()
    it = next((x for x in items if x["name"].lower() == body.name.lower()), None)
    if not it:
        # allow buying any catalog gift
        g = find_gift(body.name)
        if not g:
            raise HTTPException(404, "Нет такого подарка")
        it = {**g, "price": max(1, int(round(g["value"] * SHOP_MARKUP)))}
    price = int(it["price"])
    if u["balance"] < price:
        raise HTTPException(400, "Недостаточно ⭐")
    u["balance"] -= price
    inv_item = {k: it[k] for k in ("name", "value", "rarity", "sn", "emoji", "img") if k in it}
    inv_item["id"] = uuid.uuid4().hex[:10]
    u["inventory"].append(inv_item)
    await save_user(u)
    await add_history(u["tg_id"], "shop", "gift", f"Купил {it['name']}", -price)
    return {"success": True, "balance": u["balance"], "price": price, "item": inv_item}

@app.post("/api/upgrade")
async def upgrade(body: UpgradeRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    lock = await user_lock(u["tg_id"])
    async with lock:
        return await _do_upgrade(body, authorization, x_telegram_init_data)

async def _do_upgrade(body: UpgradeRequest, authorization, x_telegram_init_data):
    u = await current_user(authorization, x_telegram_init_data)
    if body.item_index < 0 or body.item_index >= len(u["inventory"]):
        raise HTTPException(400, "Item not found")
    item = u["inventory"][body.item_index]
    iv = int(item.get("value") or 0)
    tv = int(body.target_value)
    if iv >= tv:
        raise HTTPException(400, "Цель должна быть дороже")
    target = find_gift(body.target_name or "") if body.target_name else None
    if not target:
        # closest by value
        cands = [g for g in (gift_public(x) for x in GIFTS_FLAT) if g["value"] == tv] or \
                [g for g in (gift_public(x) for x in GIFTS_FLAT) if abs(g["value"] - tv) < max(10, tv * 0.05)]
        target = cands[0] if cands else gift_public({"name": body.target_name or "Target", "value": tv, "rarity": "Rare"})
    # ролл по реальному (ниже), на UI — display повыше
    real = calc_upgrade_chance(iv, tv, u.get("chance_bonus") or 0)
    chance = calc_upgrade_display(iv, tv, u.get("chance_bonus") or 0)  # колесо
    success = (random.SystemRandom().random() * 100.0) < real
    u["inventory"].pop(body.item_index)
    u["games"] += 1
    if success:
        u["wins"] += 1
        new_item = {k: target[k] for k in ("name", "value", "rarity", "sn", "emoji", "img") if k in target}
        new_item["id"] = uuid.uuid4().hex[:10]
        u["inventory"].append(new_item)
        await bump_quest(u["tg_id"], "upg_5")
        await bump_quest(u["tg_id"], "upg_15")
        await add_history(u["tg_id"], "upgrade", "win", f"{item.get('name')} → {target.get('name')}", tv)
        await push_live(new_item, u["username"])
    else:
        await bump_quest(u["tg_id"], "upg_5")
        await bump_quest(u["tg_id"], "upg_15")
        await add_history(u["tg_id"], "upgrade", "lose", f"{item.get('name')} сгорел", 0)
    await save_user(u)
    return {"success": success, "chance": chance, "balance": u["balance"], "target": target if success else None, "inventory": u["inventory"]}

@app.post("/api/inventory/sell")
async def sell_item(body: SellItemRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    lock = await user_lock(u["tg_id"])
    async with lock:
        u = await current_user(authorization, x_telegram_init_data)
        if body.item_index < 0 or body.item_index >= len(u["inventory"]):
            raise HTTPException(400, "Item not found")
        item = u["inventory"].pop(body.item_index)
        price = min(int(item.get("value") or 0), MAX_WIN)
        u["balance"] += price
        await save_user(u)
    await bump_quest(u["tg_id"], "sell_3")
    await add_history(u["tg_id"], "shop", "win", f"Продал {item.get('name')}", price)
    return {"success": True, "balance": u["balance"], "price": price}

@app.post("/api/case/open")
async def open_case(body: CaseOpenRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    lock = await user_lock(u["tg_id"])
    async with lock:
        return await _open_case_inner(body, authorization, x_telegram_init_data)

async def _open_case_inner(body: CaseOpenRequest, authorization, x_telegram_init_data):
    u = await current_user(authorization, x_telegram_init_data)
    # анти-спам открытий
    last = _RATE_CASE.get(u["tg_id"]) or 0
    if now_ts() - last < 1:
        raise HTTPException(429, "Слишком быстро")
    _RATE_CASE[u["tg_id"]] = now_ts()  # rate case
    c = CASES.get(body.case_id)
    if not c:
        raise HTTPException(404, "case not found")
    price = int(c.get("price") or 0)
    # free daily cooldown
    if body.case_id == "free_daily" or (price == 0 and not c.get("require_deposit") and not c.get("require_share")):
        if now_ts() - int(u["free_case_at"] or 0) < 86400:
            raise HTTPException(400, "Бесплатный кейс раз в 24ч")
        u["free_case_at"] = now_ts()
    if c.get("require_share") or body.case_id == "free_friend":
        if now_ts() - int(u["friend_case_at"] or 0) < 12 * 3600:
            raise HTTPException(400, "Кейс за друга раз в 12ч")
        u["friend_case_at"] = now_ts()
    if c.get("require_deposit"):
        need = int(c["require_deposit"])
        async with get_db() as db:
            used = await (await db.execute("SELECT 1 FROM share_claims WHERE tg_id=? AND case_id=?", (u["tg_id"], body.case_id))).fetchone()
        if used:
            raise HTTPException(400, "Уже открыт")
        if int(u["deposited"] or 0) < need:
            raise HTTPException(400, f"Нужен депозит {need}⭐")
        async with get_db() as db:
            await db.execute("INSERT OR IGNORE INTO share_claims(tg_id,case_id,created_at) VALUES(?,?,?)", (u["tg_id"], body.case_id, now_ts()))
            await db.commit()
    if price > 0:
        if u["balance"] < price:
            raise HTTPException(400, "Недостаточно ⭐")
        u["balance"] -= price
    drop = roll_case_drop(body.case_id, c)
    secret = secrets.token_hex(16)
    fair = fair_commit(secret)
    u["games"] += 1
    u["cases_opened"] = int(u.get("cases_opened") or 0) + 1
    result = {"success": True, "balance": 0, "fair": fair, "allin_lose": bool(drop.get("allin_lose"))}
    if drop["kind"] == "gift":
        g = drop["gift"]
        inv_item = {k: g[k] for k in ("name", "value", "rarity", "sn", "emoji", "img") if k in g}
        inv_item["id"] = uuid.uuid4().hex[:10]
        u["inventory"].append(inv_item)
        u["wins"] += 1
        result["gift"] = inv_item
        result["rarity"] = g.get("rarity")
        await push_live(inv_item, u["username"])
        await add_history(u["tg_id"], "case", "gift", f"{c.get('name')}: {g.get('name')}", g.get("value") or 0)
    else:
        stars = int(drop.get("stars") or 0)
        u["balance"] += stars
        result["stars_earned"] = stars
        result["gift"] = None
        await add_history(u["tg_id"], "case", "win" if stars else "lose", f"{c.get('name')}: ⭐{stars}", stars)
    await save_user(u)
    await bump_quest(u["tg_id"], "case_3")
    result["balance"] = u["balance"]
    return result

@app.post("/api/share/claim")
async def share_claim(body: ShareClaimRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    # mark share; actual open still checks cooldown
    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO share_claims(tg_id,case_id,created_at) VALUES(?,?,?)", (u["tg_id"], body.case_id or "free_friend", now_ts()))
        await db.commit()
    return {"ok": True}

@app.post("/api/case/buy_ton")
async def case_buy_ton(body: CaseBuyTonRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    # credit as stars then open — TON verify is best-effort
    u = await current_user(authorization, x_telegram_init_data)
    c = CASES.get(body.case_id)
    if not c:
        raise HTTPException(404, "case not found")
    stars = int(c.get("price") or 0)
    u["balance"] += stars  # credited from TON
    u["deposited"] += stars
    await save_user(u)
    return await open_case(CaseOpenRequest(case_id=body.case_id), authorization, x_telegram_init_data)

# ----- mines -----
MINES_GAMES: Dict[str, dict] = {}

@app.post("/api/mines/start")
async def mines_start(body: MinesStartRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    bet = check_bet(body.bet); mines = int(body.mines)
    if mines < 5 or mines > 20:
        raise HTTPException(400, "Мины 5–20")
    now = now_ts()
    for k, g0 in list(MINES_GAMES.items()):
        if now - int(g0.get("ts") or 0) > MINES_TTL_SEC:
            MINES_GAMES.pop(k, None)
    lock = await user_lock(u["tg_id"])
    async with lock:
        u = await current_user(authorization, x_telegram_init_data)
        if u["balance"] < bet:
            raise HTTPException(400, "Недостаточно ⭐")
        u["balance"] -= bet
        u["games"] += 1
        bombs = random.sample(range(25), mines)
        gid = uuid.uuid4().hex
        MINES_GAMES[gid] = {"tg_id": u["tg_id"], "bet": bet, "mines": mines, "bombs": bombs, "opened": [], "cashed": False, "ts": now}
        await save_user(u)
    await bump_quest(u["tg_id"], "mines_2")
    return {"id": gid, "game_id": gid, "balance": u["balance"], "mult": 1.0, "multiplier": 1.0}

@app.post("/api/mines/open")
async def mines_open(body: MinesOpenRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    g = MINES_GAMES.get(body.game_id)
    if not g or g["tg_id"] != u["tg_id"]:
        raise HTTPException(400, "Нет игры")
    if g["cashed"]:
        raise HTTPException(400, "Уже кэшаут")
    cell = int(body.cell)
    if cell in g["opened"]:
        return {"bomb": False, "mult": mines_mult(g["mines"], len(g["opened"]))}
    if cell in g["bombs"]:
        g["cashed"] = True
        await add_history(u["tg_id"], "mines", "lose", "Бомба", 0)
        return {
            "bomb": True, "status": "bomb", "mult": 0, "multiplier": 0,
            "mines": list(g["bombs"]), "opened": list(g["opened"]),
            "balance": u["balance"],
        }
    g["opened"].append(cell)
    m = mines_mult(g["mines"], len(g["opened"]))
    return {
        "bomb": False, "status": "ok", "mult": round(m, 2), "multiplier": round(m, 2),
        "opened": list(g["opened"]),
    }

@app.post("/api/mines/cashout")
async def mines_cashout(body: MinesCashoutRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    g = MINES_GAMES.get(body.game_id)
    if not g or g["tg_id"] != u["tg_id"] or g["cashed"]:
        raise HTTPException(400, "Нет игры")
    if not g["opened"]:
        raise HTTPException(400, "Открой клетку")
    g["cashed"] = True
    m = mines_mult(g["mines"], len(g["opened"]))
    win = clamp_win(int(g["bet"] * m))
    u["balance"] += win
    u["wins"] += 1
    await save_user(u)
    await add_history(u["tg_id"], "mines", "win", f"x{m:.2f}", win)
    return {"win": win, "balance": u["balance"], "mult": round(m, 2), "multiplier": round(m, 2)}

# ----- pvp -----
PVP: Dict[str, dict] = {}
PVP_COLORS = ["#3b82f6", "#22c55e", "#f59e0b", "#ef4444", "#a855f7", "#06b6d4"]

@app.get("/api/pvp/list")
async def pvp_list(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    lobbies = []
    mine = None
    for lid, L in list(PVP.items()):
        if L.get("started"):
            continue
        players = L["players"]
        total = sum(p["bet"] for p in players) or 1
        pl = [{**p, "chance": round(100 * p["bet"] / total, 1)} for p in players]
        view = {
            "id": lid,
            "players": pl,
            "player_list": pl,
            "pot": total,
        }
        lobbies.append(view)
        if any(p["id"] == u["tg_id"] for p in players):
            mine = view
    return {"lobbies": lobbies, "lobby": mine, "list": lobbies}

@app.post("/api/pvp/create")
async def pvp_create(body: PvpCreateRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    bet = check_bet(body.bet)
    if u["balance"] < bet:
        raise HTTPException(400, "Недостаточно ⭐")
    # already in lobby?
    for L in PVP.values():
        if not L.get("started") and any(p["id"] == u["tg_id"] for p in L["players"]):
            raise HTTPException(400, "Уже в лобби")
    u["balance"] -= bet
    lid = uuid.uuid4().hex[:8]
    PVP[lid] = {
        "players": [{
            "id": u["tg_id"], "name": u["username"], "avatar": (u["username"] or "P")[0].upper(),
            "bet": bet, "color": PVP_COLORS[0],
        }],
        "started": False,
    }
    await save_user(u)
    return {"lobby_id": lid, "id": lid, "balance": u["balance"]}

@app.post("/api/pvp/join")
async def pvp_join(body: PvpJoinRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    L = PVP.get(body.lobby_id)
    if not L or L.get("started"):
        raise HTTPException(400, "Лобби не найдено")
    if any(p["id"] == u["tg_id"] for p in L["players"]):
        return {"ok": True, "balance": u["balance"]}
    bet = int(body.bet or L["players"][0]["bet"])
    if bet < MIN_BET:
        raise HTTPException(400, f"Мин. {MIN_BET}⭐")
    if u["balance"] < bet:
        raise HTTPException(400, "Недостаточно ⭐")
    u["balance"] -= bet
    L["players"].append({
        "id": u["tg_id"], "name": u["username"], "avatar": (u["username"] or "P")[0].upper(),
        "bet": bet, "color": PVP_COLORS[len(L["players"]) % len(PVP_COLORS)],
    })
    await save_user(u)
    return {"ok": True, "balance": u["balance"]}

@app.post("/api/pvp/cancel")
async def pvp_cancel(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    refund = 0
    for lid, L in list(PVP.items()):
        if L.get("started"):
            continue
        stay = []
        for p in L["players"]:
            if p["id"] == u["tg_id"]:
                refund += p["bet"]
            else:
                stay.append(p)
        if refund:
            L["players"] = stay
            if not stay:
                PVP.pop(lid, None)
            break
    if refund:
        u["balance"] += refund
        await save_user(u)
    return {"balance": u["balance"], "refund": refund}

@app.post("/api/pvp/start")
async def pvp_start(body: PvpStartRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    L = PVP.get(body.lobby_id)
    if not L or L.get("started"):
        raise HTTPException(400, "Лобби не найдено")
    players = L["players"]
    # fill bots if solo
    if len(players) < 2:
        bot_bet = players[0]["bet"]
        players.append({
            "id": 0, "name": random.choice(["DurovFan", "PepeKing", "Mila", "Ivan"]),
            "avatar": "B", "bet": bot_bet, "color": PVP_COLORS[1], "bot": True,
        })
    L["started"] = True
    total = sum(p["bet"] for p in players)
    r = random.random() * total
    acc = 0.0
    winner = players[-1]
    win_deg = 0.0
    for p in players:
        acc += p["bet"]
        if r <= acc:
            winner = p
            break
    # degrees: end of winner slice
    deg_acc = 0.0
    for p in players:
        slice_deg = 360.0 * p["bet"] / total
        if p is winner:
            win_deg = deg_acc + slice_deg / 2
            break
        deg_acc += slice_deg
    payout = clamp_win(int(total * (1 - max(HOUSE_EDGE, 0.07))))  # PvP ~7%, cap MAX_WIN
    your_win = winner["id"] == u["tg_id"]
    # pay winner
    if winner["id"] and winner["id"] != 0:
        async with get_db() as db:
            row = await (await db.execute("SELECT balance, wins FROM users WHERE tg_id=?", (winner["id"],))).fetchone()
            if row:
                await db.execute("UPDATE users SET balance=?, wins=? WHERE tg_id=?", (int(row[0]) + payout, int(row[1] or 0) + 1, winner["id"]))
                await db.commit()
    for p in players:
        await add_history(p["id"], "pvp", "win" if p is winner else "lose", f"pot {total}", payout if p is winner else 0)
        if p["id"]:
            await bump_quest(p["id"], "pvp_1")
    u2 = await current_user(authorization, x_telegram_init_data)
    view_players = []
    for p in players:
        view_players.append({**p, "chance": round(100 * p["bet"] / total, 1)})
    PVP.pop(body.lobby_id, None)
    return {
        "players": view_players,
        "winner_id": winner["id"],
        "winner_name": winner["name"],
        "win_deg": win_deg,
        "payout": payout,
        "your_win": your_win,
        "balance": u2["balance"],
    }

# ----- deposit / withdraw / promo / quests / leaders / history / live -----
@app.post("/api/deposit")
async def deposit(body: DepositRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    amount = int(body.amount)
    if amount < 10:
        raise HTTPException(400, "Минимум 10⭐")
    payload = f"dep_{u['tg_id']}_{int(time.time())}_{secrets.token_hex(4)}"
    async with get_db() as db:
        await db.execute("INSERT INTO deposits(payload,tg_id,amount,paid,created_at) VALUES(?,?,?,?,?)", (payload, u["tg_id"], amount, 0, now_ts()))
        await db.commit()
    invoice_url = None
    if BOT_TOKEN:
        try:
            import urllib.request
            data = json.dumps({
                "title": "GiftUpgrader",
                "description": f"{amount}⭐",
                "payload": payload,
                "provider_token": "",  # Telegram Stars
                "currency": "XTR",
                "prices": [{"label": f"{amount} stars", "amount": amount}],
            }).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{BOT_TOKEN}/createInvoiceLink",
                data=data, headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                js = json.loads(resp.read().decode())
                if js.get("ok"):
                    invoice_url = js["result"]
        except Exception as e:
            print("[invoice]", e)
    if not invoice_url:
        # demo credit
        u["balance"] += amount
        u["deposited"] += amount
        await save_user(u)
        return {"success": True, "balance": u["balance"], "payload": payload, "message": "Демо-пополнение (нет BOT_TOKEN / invoice)"}
    return {"invoice_url": invoice_url, "payload": payload}

@app.post("/api/deposit/confirm")
async def deposit_confirm(body: DepositConfirmRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    async with get_db() as db:
        row = await (await db.execute("SELECT tg_id, amount, paid FROM deposits WHERE payload=?", (body.payload,))).fetchone()
        if not row:
            raise HTTPException(404, "Платёж не найден")
        if int(row[2]):
            return {"balance": u["balance"]}
        await db.execute("UPDATE deposits SET paid=1 WHERE payload=?", (body.payload,))
        await db.commit()
    amount = int(row[1])
    u["balance"] += amount
    u["deposited"] += amount
    await save_user(u)
    return {"balance": u["balance"]}

@app.post("/api/withdraw")
async def withdraw(body: WithdrawRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    amount = int(body.amount)
    if amount < 50:
        raise HTTPException(400, "Минимум 50⭐")
    if amount > MAX_BET * 2:
        raise HTTPException(400, f"Макс. за раз {MAX_BET * 2}⭐")
    dest = body.dest or getattr(body, "username", None) or getattr(body, "wallet", None) or ""
    lock = await user_lock(u["tg_id"])
    async with lock:
        u = await current_user(authorization, x_telegram_init_data)
        if u["balance"] < amount:
            raise HTTPException(400, "Недостаточно ⭐")
        day_ago = now_ts() - 86400
        async with get_db() as db:
            row = await (await db.execute(
                "SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE tg_id=? AND created_at>=? AND status IN ('pending','approved','paid')",
                (u["tg_id"], day_ago),
            )).fetchone()
            used = int((row[0] if row else 0) or 0)
            if used + amount > MAX_WITHDRAW_DAY:
                raise HTTPException(400, f"Лимит вывода в сутки {MAX_WITHDRAW_DAY}⭐")
            u["balance"] -= amount
            await save_user(u)
            await db.execute(
                "INSERT INTO withdrawals(tg_id,amount,method,dest,note,status,created_at) VALUES(?,?,?,?,?,?,?)",
                (u["tg_id"], amount, body.method or "stars", dest, body.note or "", "pending", now_ts()),
            )
            await db.commit()
        return {"ok": True, "balance": u["balance"], "message": "Заявка отправлена админу"}

@app.post("/api/promo/activate")
async def promo_activate(code: str = Query(...), authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    code = (code or "").strip().upper()
    async with get_db() as db:
        row = await (await db.execute("SELECT code, stars, max_uses, uses FROM promos WHERE code=?", (code,))).fetchone()
        if not row:
            raise HTTPException(400, "Неверный промокод")
        used = await (await db.execute("SELECT 1 FROM promo_uses WHERE code=? AND tg_id=?", (code, u["tg_id"]))).fetchone()
        if used:
            raise HTTPException(400, "Уже использован")
        if int(row[3] or 0) >= int(row[2] or 0):
            raise HTTPException(400, "Лимит промокода")
        stars = int(row[1] or 0)
        await db.execute("INSERT INTO promo_uses(code,tg_id) VALUES(?,?)", (code, u["tg_id"]))
        await db.execute("UPDATE promos SET uses=uses+1 WHERE code=?", (code,))
        await db.commit()
    u["balance"] += stars
    await save_user(u)
    return {"ok": True, "stars": stars, "balance": u["balance"]}

@app.get("/api/quests")
async def quests(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    async with get_db() as db:
        rows = await (await db.execute("SELECT quest_id, progress, claimed FROM quests WHERE tg_id=?", (u["tg_id"],))).fetchall()
    prog = {r[0]: (int(r[1] or 0), int(r[2] or 0)) for r in (rows or [])}
    out = []
    for q in QUESTS_DEF:
        p, cl = prog.get(q["id"], (0, 0))
        out.append({**q, "progress": p, "claimed": bool(cl), "done": p >= q["need"]})
    return {"quests": out}

@app.post("/api/quests/claim")
async def quests_claim(body: QuestClaimRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    q = next((x for x in QUESTS_DEF if x["id"] == body.quest_id), None)
    if not q:
        raise HTTPException(404, "quest")
    async with get_db() as db:
        row = await (await db.execute("SELECT progress, claimed FROM quests WHERE tg_id=? AND quest_id=?", (u["tg_id"], q["id"]))).fetchone()
        if not row or int(row[0] or 0) < q["need"]:
            raise HTTPException(400, "Ещё не выполнено")
        if int(row[1] or 0):
            raise HTTPException(400, "Уже получено")
        await db.execute("UPDATE quests SET claimed=1 WHERE tg_id=? AND quest_id=?", (u["tg_id"], q["id"]))
        await db.commit()
    u["balance"] += q["reward"]
    await save_user(u)
    return {"ok": True, "balance": u["balance"], "reward": q["reward"]}

@app.get("/api/leaderboard")
async def leaderboard():
    async with get_db() as db:
        by_bal = await (await db.execute("SELECT username, balance, wins, cases_opened FROM users ORDER BY balance DESC LIMIT 20")).fetchall()
        by_wins = await (await db.execute("SELECT username, balance, wins, cases_opened FROM users ORDER BY wins DESC LIMIT 20")).fetchall()
        by_cases = await (await db.execute("SELECT username, balance, wins, cases_opened FROM users ORDER BY cases_opened DESC LIMIT 20")).fetchall()
    def pack(rows):
        return [{"name": r[0] or "Player", "balance": int(r[1] or 0), "wins": int(r[2] or 0), "cases": int(r[3] or 0)} for r in (rows or [])]
    return {"by_balance": pack(by_bal), "by_wins": pack(by_wins), "by_cases": pack(by_cases)}

@app.get("/api/history")
async def history(limit: int = 30, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT game, result, detail, amount, created_at FROM history WHERE tg_id=? ORDER BY id DESC LIMIT ?",
            (u["tg_id"], max(1, min(100, limit))),
        )).fetchall()
    return {"history": [{"game": r[0], "result": r[1], "detail": r[2], "amount": r[3], "ts": r[4]} for r in (rows or [])]}

@app.get("/api/live")
async def live():
    async with get_db() as db:
        rows = await (await db.execute("SELECT name, emoji, img, user_name, value FROM live_drops ORDER BY id DESC LIMIT 18")).fetchall()
    items = [{"name": r[0], "emoji": r[1] or "🎁", "img": r[2] or gift_img_url(r[0] or ""), "user": r[3], "value": r[4]} for r in (rows or [])]
    return {"items": items}

@app.get("/api/referral/stats")
async def ref_stats(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    link = f"https://t.me/GiftUpgraderBot?start=ref_{u['tg_id']}"
    return {"invited": 0, "earned": 0, "link": link, "code": f"ref_{u['tg_id']}"}

@app.post("/api/referral/activate")
async def ref_activate():
    return {"ok": True}

@app.post("/api/ton/wallet")
async def ton_wallet(body: TonWalletRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    u["ton_wallet"] = body.address
    await save_user(u)
    return {"ok": True}


# ----- TON chain verify (TonAPI public) -----
async def _http_json(url: str, timeout: float = 12.0):
    import urllib.request
    def _do():
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "GiftUpgrader/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore") or "{}")
    return await asyncio.to_thread(_do)


async def verify_ton_payment(treasury: str, amount_ton: float, created_at: int, tg_id: int) -> dict:
    """Ищем входящий TON на казну ≈ amount, после created_at. Возврат {ok, tx_hash, amount}.
    Не начисляет баланс — только проверка сети.
    """
    if not treasury:
        return {"ok": False, "reason": "no_treasury"}
    need_nano = int(round(float(amount_ton) * 1e9))
    # допуск ±3% и минимум ±0.01 TON
    tol = max(int(0.01 * 1e9), int(need_nano * 0.03))
    since = max(0, int(created_at) - 120)
    # 1) TonAPI events
    try:
        url = f"https://tonapi.io/v2/accounts/{treasury}/events?limit=40"
        data = await _http_json(url)
        events = data.get("events") or data.get("event_ids") or []
        if isinstance(data.get("events"), list):
            for ev in data["events"]:
                ts = int(ev.get("timestamp") or ev.get("utime") or 0)
                if ts and ts < since:
                    continue
                tx_hash = str(ev.get("event_id") or ev.get("tx_hash") or ev.get("hash") or "")
                for act in (ev.get("actions") or []):
                    typ = (act.get("type") or act.get("Type") or "").lower()
                    if "tontransfer" not in typ and typ != "ton":
                        # structure: action.TonTransfer
                        pass
                    tt = act.get("TonTransfer") or act.get("ton_transfer") or act
                    if not isinstance(tt, dict):
                        continue
                    # incoming to treasury
                    recipient = str((tt.get("recipient") or {}).get("address") or tt.get("recipient") or "")
                    sender = str((tt.get("sender") or {}).get("address") or tt.get("sender") or "")
                    amt = tt.get("amount")
                    try:
                        amt = int(amt)
                    except Exception:
                        continue
                    # если recipient пуст — TonAPI иногда кладёт amount на входящие в account endpoint
                    if abs(amt - need_nano) <= tol and amt > 0:
                        # comment / payload may include tg id
                        comment = str(tt.get("comment") or act.get("status") or "")
                        return {"ok": True, "tx_hash": tx_hash or f"ev-{ts}-{amt}", "amount_nano": amt, "comment": comment}
    except Exception as e:
        print("[TON] tonapi events", e)
    # 2) fallback toncenter getTransactions
    try:
        from urllib.parse import quote
        url = f"https://toncenter.com/api/v2/getTransactions?address={quote(treasury)}&limit=25"
        data = await _http_json(url)
        for tx in (data.get("result") or []):
            utime = int(tx.get("utime") or 0)
            if utime and utime < since:
                continue
            in_msg = tx.get("in_msg") or {}
            val = int(in_msg.get("value") or 0)
            if val <= 0:
                continue
            if abs(val - need_nano) <= tol:
                txid = str(tx.get("transaction_id", {}).get("hash") or tx.get("transaction_id") or utime)
                body_msg = str(in_msg.get("message") or in_msg.get("msg_data", {}).get("text") or "")
                return {"ok": True, "tx_hash": txid, "amount_nano": val, "comment": body_msg}
    except Exception as e:
        print("[TON] toncenter", e)
    return {"ok": False, "reason": "not_found"}


async def credit_ton_deposit(dep_id: str, tg_id: int, stars: int, tx_hash: str = "") -> dict:
    """Идемпотентное начисление по deposit id."""
    lock = await user_lock(tg_id)
    async with lock:
        async with get_db() as db:
            row = await (await db.execute(
                "SELECT credited, amount_ton FROM ton_deposits WHERE id=? AND tg_id=?",
                (dep_id, tg_id),
            )).fetchone()
            if not row:
                return {"credited": False, "error": "not_found"}
            if int(row[0] or 0):
                # already
                bal = await (await db.execute("SELECT balance FROM users WHERE tg_id=?", (tg_id,))).fetchone()
                return {"credited": True, "balance": int(bal[0] if bal else 0), "stars": 0, "dup": True}
            # mark credited first (anti double)
            await db.execute(
                "UPDATE ton_deposits SET credited=1, boc=COALESCE(NULLIF(boc,''), ?) WHERE id=? AND credited=0",
                (tx_hash or "onchain", dep_id),
            )
            await db.commit()
            bal = await (await db.execute("SELECT balance, deposited FROM users WHERE tg_id=?", (tg_id,))).fetchone()
            if not bal:
                return {"credited": False, "error": "user"}
            new_bal = int(bal[0] or 0) + int(stars)
            new_dep = int(bal[1] or 0) + int(stars)
            await db.execute("UPDATE users SET balance=?, deposited=? WHERE tg_id=?", (new_bal, new_dep, tg_id))
            await db.commit()
            return {"credited": True, "balance": new_bal, "stars": stars}


@app.post("/api/ton/deposit")
async def ton_deposit(body: TonDepositRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    amount_ton = float(body.amount_ton or 0)
    if amount_ton < 0.05 or amount_ton > 500:
        raise HTTPException(400, "TON 0.05–500")
    boc = (body.boc or "").strip()
    stars = min(int(round(amount_ton * TON_STARS_PER_TON)), MAX_WIN)
    if boc:
        async with get_db() as db:
            prev = await (await db.execute(
                "SELECT id, credited FROM ton_deposits WHERE boc=? AND length(boc)>0 LIMIT 1", (boc,)
            )).fetchone()
            if prev and int(prev[1] or 0):
                return {"ok": True, "deposit_id": prev[0], "credited": True, "balance": u["balance"], "stars": 0, "dup": True}
    dep_id = uuid.uuid4().hex
    async with get_db() as db:
        await db.execute(
            "INSERT INTO ton_deposits(id,tg_id,amount_ton,boc,address,credited,created_at) VALUES(?,?,?,?,?,?,?)",
            (dep_id, u["tg_id"], amount_ton, boc, body.address or "", 0, now_ts()),
        )
        await db.commit()
    # Только явный credit-mode (тесты). ALLOW_DEV_AUTH больше НЕ даёт бесплатные ⭐
    if TON_DEPOSIT_MODE == "credit":
        lock = await user_lock(u["tg_id"])
        async with lock:
            u2 = await current_user(authorization, x_telegram_init_data)
            u2["balance"] += stars
            u2["deposited"] += stars
            await save_user(u2)
            async with get_db() as db:
                await db.execute("UPDATE ton_deposits SET credited=1 WHERE id=? AND credited=0", (dep_id,))
                await db.commit()
            return {"ok": True, "deposit_id": dep_id, "credited": True, "balance": u2["balance"], "stars": stars}
    # сразу пробуем найти tx в сети (часто уже есть через 2–10с)
    if TON_TREASURY and TON_DEPOSIT_MODE != "credit":
        ver = await verify_ton_payment(TON_TREASURY, amount_ton, now_ts(), u["tg_id"])
        if ver.get("ok"):
            res = await credit_ton_deposit(dep_id, u["tg_id"], stars, str(ver.get("tx_hash") or ""))
            if res.get("credited"):
                return {"ok": True, "deposit_id": dep_id, "credited": True, "balance": res.get("balance"), "stars": res.get("stars", stars), "tx": ver.get("tx_hash")}
    return {"ok": True, "deposit_id": dep_id, "credited": False, "message": "Ожидаем сеть — нажми проверить через 20с"}

@app.post("/api/ton/check")
async def ton_check(body: TonCheckRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    """Проверка оплаты в сети → начисление ⭐ один раз."""
    u = await current_user(authorization, x_telegram_init_data)
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT amount_ton, credited, created_at FROM ton_deposits WHERE id=? AND tg_id=?",
            (body.deposit_id, u["tg_id"]),
        )).fetchone()
    if not row:
        raise HTTPException(404, "deposit")
    amount_ton = float(row[0] or 0)
    credited = int(row[1] or 0)
    created_at = int(row[2] or 0)
    stars = min(int(round(amount_ton * TON_STARS_PER_TON)), MAX_WIN)
    if credited:
        return {"success": True, "credited": True, "balance": u["balance"], "stars": 0}
    if not TON_TREASURY:
        return {"success": False, "credited": False, "balance": u["balance"], "message": "Казна не настроена"}
    ver = await verify_ton_payment(TON_TREASURY, amount_ton, created_at, u["tg_id"])
    if not ver.get("ok"):
        return {"success": False, "credited": False, "balance": u["balance"], "message": "Пока не видно в сети, подожди 15–60с"}
    res = await credit_ton_deposit(body.deposit_id, u["tg_id"], stars, str(ver.get("tx_hash") or ""))
    return {
        "success": bool(res.get("credited")),
        "credited": bool(res.get("credited")),
        "balance": res.get("balance", u["balance"]),
        "stars": res.get("stars", 0),
        "tx": ver.get("tx_hash"),
    }

# ----- admin -----
@app.post("/api/admin/give")
async def admin_give(body: AdminGiveRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    me = await current_user(authorization, x_telegram_init_data)
    require_admin(me)
    async with get_db() as db:
        row = await (await db.execute("SELECT balance FROM users WHERE tg_id=?", (body.user_id,))).fetchone()
        if not row:
            raise HTTPException(404, "user")
        await db.execute("UPDATE users SET balance=? WHERE tg_id=?", (int(row[0] or 0) + int(body.amount), body.user_id))
        await db.commit()
    return {"ok": True}

@app.post("/api/admin/take")
async def admin_take(body: AdminTakeRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    me = await current_user(authorization, x_telegram_init_data)
    require_admin(me)
    async with get_db() as db:
        row = await (await db.execute("SELECT balance, inventory FROM users WHERE tg_id=?", (body.user_id,))).fetchone()
        if not row:
            raise HTTPException(404, "user")
        bal = int(row[0] or 0)
        inv = json.loads(row[1] or "[]")
        if body.amount:
            bal = max(0, bal - int(body.amount))
        if body.item_index >= 0 and body.item_index < len(inv):
            inv.pop(body.item_index)
        await db.execute("UPDATE users SET balance=?, inventory=? WHERE tg_id=?", (bal, json.dumps(inv, ensure_ascii=False), body.user_id))
        await db.commit()
    return {"ok": True}

@app.post("/api/admin/chance")
async def admin_chance(body: AdminChanceRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    me = await current_user(authorization, x_telegram_init_data)
    require_admin(me)
    async with get_db() as db:
        await db.execute("UPDATE users SET chance_bonus=? WHERE tg_id=?", (float(body.chance_bonus), body.user_id))
        await db.commit()
    return {"ok": True}

@app.post("/api/admin/give_prize")
async def admin_give_prize(body: GivePrizeRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    me = await current_user(authorization, x_telegram_init_data)
    require_admin(me)
    g = find_gift(body.name) or gift_public({"name": body.name, "value": body.value, "rarity": body.rarity})
    g["id"] = uuid.uuid4().hex[:10]
    async with get_db() as db:
        row = await (await db.execute("SELECT inventory FROM users WHERE tg_id=?", (body.user_id,))).fetchone()
        if not row:
            raise HTTPException(404, "user")
        inv = json.loads(row[0] or "[]")
        inv.append(g)
        await db.execute("UPDATE users SET inventory=? WHERE tg_id=?", (json.dumps(inv, ensure_ascii=False), body.user_id))
        await db.commit()
    return {"ok": True, "item": g}

@app.post("/api/admin/promo")
async def admin_promo(body: PromoCreateRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    me = await current_user(authorization, x_telegram_init_data)
    require_admin(me)
    async with get_db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO promos(code,reward_type,stars,max_uses,uses) VALUES(?,?,?,?,0)",
            (body.code.strip().upper(), body.reward_type, int(body.stars), int(body.max_uses)),
        )
        await db.commit()
    return {"ok": True}

@app.get("/api/admin/withdrawals")
async def admin_withdrawals(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    me = await current_user(authorization, x_telegram_init_data)
    require_admin(me)
    async with get_db() as db:
        rows = await (await db.execute("SELECT id, tg_id, amount, method, dest, note, status, created_at FROM withdrawals ORDER BY id DESC LIMIT 50")).fetchall()
    items = [
        {"id": r[0], "tg_id": r[1], "amount": r[2], "method": r[3], "dest": r[4], "note": r[5], "status": r[6], "ts": r[7]}
        for r in (rows or [])
    ]
    return {"items": items, "withdrawals": items, "list": items}

@app.post("/api/admin/withdraw/status")
async def admin_wd_status(body: AdminWithdrawStatusRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    me = await current_user(authorization, x_telegram_init_data)
    require_admin(me)
    st = (body.status or "").lower().strip()
    if st not in ("approved", "rejected", "cancelled", "paid"):
        raise HTTPException(400, "status: approved|rejected|cancelled|paid")
    async with get_db() as db:
        row = await (await db.execute("SELECT tg_id, amount, status FROM withdrawals WHERE id=?", (body.withdraw_id,))).fetchone()
        if not row:
            raise HTTPException(404, "wd")
        if (row[2] or "").lower() != "pending":
            raise HTTPException(400, f"Уже обработан: {row[2]}")
        await db.execute("UPDATE withdrawals SET status=? WHERE id=? AND status='pending'", (st, body.withdraw_id))
        if st in ("rejected", "cancelled"):
            bal = await (await db.execute("SELECT balance FROM users WHERE tg_id=?", (row[0],))).fetchone()
            if bal:
                await db.execute("UPDATE users SET balance=? WHERE tg_id=?", (int(bal[0] or 0) + int(row[1]), row[0]))
        await db.commit()
    return {"ok": True}

@app.get("/api/admin/stats")
async def admin_stats(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    me = await current_user(authorization, x_telegram_init_data)
    require_admin(me)
    async with get_db() as db:
        n = await (await db.execute("SELECT COUNT(*) FROM users")).fetchone()
        s = await (await db.execute("SELECT SUM(balance) FROM users")).fetchone()
    return {"users": int(n[0] or 0), "stars": int(s[0] or 0)}

@app.post("/telegram/webhook")
async def tg_webhook(request: Request):
    # optional: handle successful_payment
    try:
        data = await request.json()
        msg = data.get("message") or {}
        sp = msg.get("successful_payment")
        if sp:
            payload = sp.get("invoice_payload")
            async with get_db() as db:
                row = await (await db.execute("SELECT tg_id, amount, paid FROM deposits WHERE payload=?", (payload,))).fetchone()
                if row and not int(row[2]):
                    await db.execute("UPDATE deposits SET paid=1 WHERE payload=?", (payload,))
                    bal = await (await db.execute("SELECT balance, deposited FROM users WHERE tg_id=?", (row[0],))).fetchone()
                    if bal:
                        await db.execute("UPDATE users SET balance=?, deposited=? WHERE tg_id=?", (int(bal[0])+int(row[1]), int(bal[1])+int(row[1]), row[0]))
                    await db.commit()
    except Exception as e:
        print("[webhook]", e)
    return {"ok": True}

# -------------------- CRASH SOCKET --------------------
CRASH = {
    "status": "betting",  # betting | flying | crashed
    "timer": 8,
    "multiplier": 1.0,
    "crash_point": 2.0,
    "history": [1.24, 3.81, 1.02, 12.4, 2.15],
    "bets": {},  # tg_id -> {amount, username, cashed, win}
}

@sio.event
async def connect(sid, environ, auth):
    await sio.emit("crash_state", {"status": CRASH["status"], "timer": CRASH["timer"], "history": CRASH["history"]}, to=sid)

@sio.event
async def disconnect(sid):
    return

@sio.on("place_bet")
async def on_place_bet(sid, data):
    try:
        tg_id = int((data or {}).get("tg_id") or 0)
        amount = int((data or {}).get("amount") or 0)
        username = str((data or {}).get("username") or "Player")
        if CRASH["status"] != "betting":
            await sio.emit("error", {"message": "Ставки закрыты"}, to=sid)
            return
        if amount < MIN_BET or amount > MAX_BET:
            await sio.emit("error", {"message": f"Ставка {MIN_BET}–{MAX_BET}⭐"}, to=sid)
            return
        async with get_db() as db:
            row = await (await db.execute("SELECT balance FROM users WHERE tg_id=?", (tg_id,))).fetchone()
            if not row or int(row[0]) < amount:
                await sio.emit("error", {"message": "Недостаточно ⭐"}, to=sid)
                return
            await db.execute("UPDATE users SET balance=balance-?, games=games+1 WHERE tg_id=?", (amount, tg_id))
            await db.commit()
            bal = await (await db.execute("SELECT balance FROM users WHERE tg_id=?", (tg_id,))).fetchone()
        CRASH["bets"][tg_id] = {"amount": amount, "username": username, "cashed": False, "win": 0, "sid": sid}
        await sio.emit("bet_placed", {"amount": amount, "balance": int(bal[0])}, to=sid)
    except Exception as e:
        await sio.emit("error", {"message": str(e)}, to=sid)

@sio.on("cashout")
async def on_cashout(sid, data):
    try:
        tg_id = int((data or {}).get("tg_id") or 0)
        if CRASH["status"] != "flying":
            await sio.emit("error", {"message": "Нельзя сейчас"}, to=sid)
            return
        b = CRASH["bets"].get(tg_id)
        if not b or b["cashed"]:
            return
        win = clamp_win(int(b["amount"] * CRASH["multiplier"]))
        b["cashed"] = True
        b["win"] = win
        async with get_db() as db:
            await db.execute("UPDATE users SET balance=balance+?, wins=wins+1 WHERE tg_id=?", (win, tg_id))
            await db.commit()
            bal = await (await db.execute("SELECT balance FROM users WHERE tg_id=?", (tg_id,))).fetchone()
        await sio.emit("cashout_success", {"win": win, "balance": int(bal[0] if bal else 0)}, to=sid)
    except Exception as e:
        await sio.emit("error", {"message": str(e)}, to=sid)

async def crash_loop():
    await asyncio.sleep(1.5)
    while True:
        try:
            # betting
            CRASH["status"] = "betting"
            CRASH["multiplier"] = 1.0
            CRASH["bets"] = {}
            CRASH["crash_point"] = roll_crash_point()
            for t in range(8, 0, -1):
                CRASH["timer"] = t
                await sio.emit("crash_state", {"status": "betting", "timer": t, "history": CRASH["history"]})
                await asyncio.sleep(1)
            # flying
            CRASH["status"] = "flying"
            await sio.emit("crash_start", {})
            await sio.emit("crash_state", {"status": "flying", "history": CRASH["history"]})
            m = 1.0
            while m < CRASH["crash_point"]:
                # ~x2 за 8–10 сек
                m = min(CRASH["crash_point"], m * 1.0065 + 0.0008)
                CRASH["multiplier"] = round(m, 2)
                await sio.emit("crash_multiplier", {"multiplier": CRASH["multiplier"]})
                await asyncio.sleep(0.12)
            CRASH["status"] = "crashed"
            CRASH["history"] = ([CRASH["crash_point"]] + CRASH["history"])[:12]
            bets_view = []
            for tg_id, b in CRASH["bets"].items():
                if not b["cashed"]:
                    b["win"] = 0
                bets_view.append({"username": b["username"], "win": b["win"]})
            await sio.emit("crash_end", {"crash_point": CRASH["crash_point"], "bets": bets_view})
            await sio.emit("crash_state", {"status": "crashed", "history": CRASH["history"]})
            await asyncio.sleep(2.5)
        except Exception as e:
            print("[crash]", e)
            await asyncio.sleep(2)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT") or 8080)
    uvicorn.run(socket_app, host="0.0.0.0", port=port)
