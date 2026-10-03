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
#   STARTING_BALANCE   default 0 (без халявы)
#   TON_TREASURY       адрес казны — для авто-проверки депозитов через tonapi.io
#   TON_DEPOSIT_MODE   prod | credit (credit только для тестов)
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

# --- config ---
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
ADMIN_TG_ID = int(os.getenv("ADMIN_TG_ID") or "7092015279")
MAINTENANCE_MODE = {"on": False, "message": "Технические работы. Скоро вернёмся."}
# DEV-авторизация ТОЛЬКО если явно включена. В проде с BOT_TOKEN — выкл.
_ALLOW_DEV_RAW = (os.getenv("ALLOW_DEV_AUTH") or "").strip().lower()
if _ALLOW_DEV_RAW in ("1", "true", "yes", "on"):
    ALLOW_DEV_AUTH = True
elif _ALLOW_DEV_RAW in ("0", "false", "no", "off"):
    ALLOW_DEV_AUTH = False
else:
    # по умолчанию: dev только без BOT_TOKEN (локалка)
    ALLOW_DEV_AUTH = not bool((os.getenv("BOT_TOKEN") or "").strip())
STARTING_BALANCE = int(os.getenv("STARTING_BALANCE") or "0")  # без бесплатных ⭐
HOUSE_EDGE = float(os.getenv("HOUSE_EDGE") or "0.05")  # RTP ~95% смешанный
MIN_BET = int(os.getenv("MIN_BET") or "50")
SHOP_MARKUP = float(os.getenv("SHOP_MARKUP") or "1.40")
SELL_FEE = float(os.getenv("SELL_FEE") or "0.05")  # продажа инвентаря −5%
MAX_CHANCE_BONUS = 12.0  # потолок админ-бонуса шанса
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
SQLITE_PATH = os.getenv("SQLITE_PATH") or "database.db"
DB_NAME = SQLITE_PATH  # alias for sqlite path
USE_POSTGRES = bool(DATABASE_URL)  # Neon/Postgres если задан DATABASE_URL
TON_TREASURY = (os.getenv("TON_TREASURY") or "").strip()
TON_STARS_PER_TON = float(os.getenv("TON_STARS_PER_TON") or "110")
# credit = мгновенно зачислять (только для тестов). prod = ждать проверки.
TON_DEPOSIT_MODE = (os.getenv("TON_DEPOSIT_MODE") or "prod").strip().lower()


def _sql_adapt(sql: str) -> str:
    """SQLite ? → Postgres $1,$2 for Neon."""
    if not DATABASE_URL or not sql:
        return sql
    out = []
    n = 0
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "?":
            n += 1
            out.append(f"${n}")
        else:
            out.append(ch)
        i += 1
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

_pg_pool = None  # asyncpg pool, создаётся при первом get_db()

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
  created_at INTEGER,
  tx_hash TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS share_claims (
  tg_id BIGINT,
  case_id TEXT,
  created_at INTEGER,
  PRIMARY KEY (tg_id, case_id)
);
CREATE TABLE IF NOT EXISTS battles (
  id TEXT PRIMARY KEY,
  case_id TEXT,
  host_id BIGINT,
  host_name TEXT,
  price INTEGER DEFAULT 0,
  status TEXT DEFAULT 'open',
  winner_id BIGINT,
  drop_host TEXT,
  drop_join TEXT,
  created_at INTEGER
);
CREATE TABLE IF NOT EXISTS trade_plaza (
  tg_id BIGINT PRIMARY KEY,
  username TEXT,
  balance INTEGER DEFAULT 0,
  updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS case_opens (
  case_id TEXT PRIMARY KEY,
  opens INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS house_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT,
  amount INTEGER DEFAULT 0,
  tg_id BIGINT,
  detail TEXT,
  created_at INTEGER
);
CREATE TABLE IF NOT EXISTS referrals (
  referred_id BIGINT PRIMARY KEY,
  referrer_id BIGINT,
  earned INTEGER DEFAULT 0,
  created_at INTEGER
);
CREATE TABLE IF NOT EXISTS streaks (
  tg_id BIGINT PRIMARY KEY,
  count INTEGER DEFAULT 0,
  last_claim_day INTEGER DEFAULT 0,
  total_earned INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS weekly_scores (
  tg_id BIGINT PRIMARY KEY,
  username TEXT,
  score INTEGER DEFAULT 0,
  week_key TEXT
);

CREATE TABLE IF NOT EXISTS trades (
  id TEXT PRIMARY KEY,
  from_id BIGINT,
  from_name TEXT,
  to_id BIGINT,
  offer_a TEXT DEFAULT '{}',
  offer_b TEXT DEFAULT '{}',
  status TEXT DEFAULT 'open',
  accepted_a INTEGER DEFAULT 0,
  accepted_b INTEGER DEFAULT 0,
  created_at INTEGER
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
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS withdraw_hold_until INTEGER DEFAULT 0",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS allin_day INTEGER DEFAULT 0",
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS allin_count INTEGER DEFAULT 0",
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
                "ALTER TABLE ton_deposits ADD COLUMN IF NOT EXISTS tx_hash TEXT DEFAULT ''",
                "ALTER TABLE share_claims ALTER COLUMN tg_id TYPE BIGINT",
            ]
            for migration in migrations:
                try:
                    await db.execute(migration)
                except Exception as e:
                    print("[DB] migration", migration, e)
            await db.commit()
        # SQLite: add tx_hash if missing
        try:
            await db.execute("ALTER TABLE ton_deposits ADD COLUMN tx_hash TEXT DEFAULT ''")
            await db.commit()
        except Exception:
            pass
        # Промокоды только создаёт админ — ничего не сидим
        pass

print("[DB] mode:", "POSTGRES" if USE_POSTGRES else f"SQLite ({DB_NAME})")



# -------------------- CATALOG --------------------
# Цены в ⭐. Collectible floor ≈ TON_floor × 110 (1 TON ≈ 110⭐).
# Русские и EN-имена сведены к одним value, без дублей «15 vs 5773».
CDN = (os.getenv("GIFT_CDN") or "https://cdn.jsdelivr.net/gh/ssamy2/TG_Photos@main/webp/by_name").rstrip("/")
GIFT_SN_CDN = {
    "мишка": "toy_bear", "сердце": "cookie_heart", "конфета": "lol_pop",
    "подарок": "joyful_bundle", "звезда": "hanging_star", "торт": "homemade_cake",
    "ракета": "stellar_rocket", "букет": "lush_bouquet", "ёлка": "winter_wreath",
    "елка": "winter_wreath", "шампанское": "spiced_wine", "цветы": "sakura_flower",
    "кольцо": "diamond_ring", "алмаз": "diamond_ring", "кубок": "mini_oscar",
    "teddy_bear": "toy_bear", "heart": "cookie_heart", "candy": "lol_pop",
}

GIFTS_FLAT = [
    # --- Common: базовые TG-подарки (магазин) ---
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
    {'name': 'Алмаз', 'value': 100, 'rarity': 'Common', 'sn': 'diamond'},
    {'name': 'Кубок', 'value': 100, 'rarity': 'Common', 'sn': 'trophy'},
    {'name': 'Кольцо', 'value': 110, 'rarity': 'Common', 'sn': 'ring'},  # ~1 TON
    # --- Uncommon: дешёвые limited (~1.5–4 TON) ---
    {'name': 'Desk Calendar', 'value': 550, 'rarity': 'Uncommon', 'sn': 'desk_calendar'},
    {'name': 'Lol Pop', 'value': 550, 'rarity': 'Uncommon', 'sn': 'lol_pop'},
    {'name': 'Triple Meow', 'value': 550, 'rarity': 'Uncommon', 'sn': 'triple_meow'},
    {'name': 'Candy Cane', 'value': 550, 'rarity': 'Uncommon', 'sn': 'candy_cane'},
    {'name': 'Ice Cream', 'value': 550, 'rarity': 'Uncommon', 'sn': 'ice_cream'},
    {'name': 'Easter Egg', 'value': 550, 'rarity': 'Uncommon', 'sn': 'easter_egg'},
    {'name': 'Cookie Heart', 'value': 550, 'rarity': 'Uncommon', 'sn': 'cookie_heart'},
    {'name': 'Lunar Snake', 'value': 550, 'rarity': 'Uncommon', 'sn': 'lunar_snake'},
    {'name': 'Pool Float', 'value': 550, 'rarity': 'Uncommon', 'sn': 'pool_float'},
    {'name': 'Xmas Stocking', 'value': 550, 'rarity': 'Uncommon', 'sn': 'xmas_stocking'},
    {'name': 'Chill Flame', 'value': 550, 'rarity': 'Uncommon', 'sn': 'chill_flame'},
    {'name': 'Snake Box', 'value': 550, 'rarity': 'Uncommon', 'sn': 'snake_box'},
    {'name': 'Vice Cream', 'value': 550, 'rarity': 'Uncommon', 'sn': 'vice_cream'},
    {'name': 'Instant Ramen', 'value': 550, 'rarity': 'Uncommon', 'sn': 'instant_ramen'},
    {'name': 'Winter Wreath', 'value': 550, 'rarity': 'Uncommon', 'sn': 'winter_wreath'},
    {'name': 'Holiday Drink', 'value': 550, 'rarity': 'Uncommon', 'sn': 'holiday_drink'},
    {'name': 'Jester Hat', 'value': 550, 'rarity': 'Uncommon', 'sn': 'jester_hat'},
    {'name': 'Pet Snake', 'value': 550, 'rarity': 'Uncommon', 'sn': 'pet_snake'},
    {'name': 'Whip Cupcake', 'value': 550, 'rarity': 'Uncommon', 'sn': 'whip_cupcake'},
    {'name': 'Snoop Dogg', 'value': 620, 'rarity': 'Uncommon', 'sn': 'snoop_dogg'},
    {'name': 'Spy Agaric', 'value': 600, 'rarity': 'Uncommon', 'sn': 'spy_agaric'},
    {'name': 'Spiced Wine', 'value': 600, 'rarity': 'Uncommon', 'sn': 'spiced_wine'},
    {'name': 'Stellar Rocket', 'value': 600, 'rarity': 'Uncommon', 'sn': 'stellar_rocket'},
    {'name': 'Bow Tie', 'value': 700, 'rarity': 'Uncommon', 'sn': 'bow_tie'},
    {'name': 'B-Day Candle', 'value': 550, 'rarity': 'Uncommon', 'sn': 'bday_candle'},
    {'name': 'Homemade Cake', 'value': 800, 'rarity': 'Uncommon', 'sn': 'homemade_cake'},
    {'name': 'Party Sparkler', 'value': 550, 'rarity': 'Uncommon', 'sn': 'party_sparkler'},
    {'name': 'Hex Pot', 'value': 550, 'rarity': 'Uncommon', 'sn': 'hex_pot'},
    {'name': 'Swag Bag', 'value': 900, 'rarity': 'Uncommon', 'sn': 'swag_bag'},
    {'name': 'Money Pot', 'value': 1000, 'rarity': 'Uncommon', 'sn': 'money_pot'},
    {'name': 'Snow Globe', 'value': 1100, 'rarity': 'Uncommon', 'sn': 'snow_globe'},
    {'name': 'Clover Pin', 'value': 550, 'rarity': 'Uncommon', 'sn': 'clover_pin'},
    # --- Rare (~12–25 TON) ---
    {'name': 'Berry Box', 'value': 910, 'rarity': 'Rare', 'sn': 'berry_box'},
    {'name': 'Lush Bouquet', 'value': 1000, 'rarity': 'Rare', 'sn': 'lush_bouquet'},
    {'name': 'Moon Pendant', 'value': 1000, 'rarity': 'Rare', 'sn': 'moon_pendant'},
    {'name': 'Evil Eye', 'value': 810, 'rarity': 'Rare', 'sn': 'evil_eye'},
    {'name': 'Jingle Bells', 'value': 930, 'rarity': 'Rare', 'sn': 'jingle_bells'},
    {'name': 'Jelly Bunny', 'value': 850, 'rarity': 'Rare', 'sn': 'jelly_bunny'},
    {'name': 'Bunny Muffin', 'value': 830, 'rarity': 'Rare', 'sn': 'bunny_muffin'},
    {'name': 'Joyful Bundle', 'value': 840, 'rarity': 'Rare', 'sn': 'joyful_bundle'},
    {'name': 'Jolly Chimp', 'value': 900, 'rarity': 'Rare', 'sn': 'jolly_chimp'},
    {'name': 'Hanging Star', 'value': 1040, 'rarity': 'Rare', 'sn': 'hanging_star'},
    {'name': 'Sakura Flower', 'value': 1100, 'rarity': 'Rare', 'sn': 'sakura_flower'},
    {'name': 'Top Hat', 'value': 1210, 'rarity': 'Rare', 'sn': 'top_hat'},
    {'name': 'Mad Pumpkin', 'value': 1460, 'rarity': 'Rare', 'sn': 'mad_pumpkin'},
    {'name': 'Flying Broom', 'value': 1430, 'rarity': 'Rare', 'sn': 'flying_broom'},
    {'name': 'Skull Flower', 'value': 1280, 'rarity': 'Rare', 'sn': 'skull_flower'},
    {'name': 'Valentine Box', 'value': 1210, 'rarity': 'Rare', 'sn': 'valentine_box'},
    {'name': 'Sleigh Bell', 'value': 1200, 'rarity': 'Rare', 'sn': 'sleigh_bell'},
    {'name': 'Surge Board', 'value': 1500, 'rarity': 'Rare', 'sn': 'surge_board'},
    {'name': 'Light Sword', 'value': 1500, 'rarity': 'Rare', 'sn': 'light_sword'},
    # --- Epic (~45–80 TON) ---
    {'name': 'Crystal Ball', 'value': 1320, 'rarity': 'Epic', 'sn': 'crystal_ball'},
    {'name': 'Snoop Cigar', 'value': 1610, 'rarity': 'Epic', 'sn': 'snoop_cigar'},
    {'name': 'Trapped Heart', 'value': 1640, 'rarity': 'Epic', 'sn': 'trapped_heart'},
    {'name': 'Love Potion', 'value': 1600, 'rarity': 'Epic', 'sn': 'love_potion'},
    {'name': 'Electric Skull', 'value': 2760, 'rarity': 'Epic', 'sn': 'electric_skull'},
    {'name': 'Eternal Rose', 'value': 2860, 'rarity': 'Epic', 'sn': 'eternal_rose'},
    {'name': 'Cupid Charm', 'value': 2390, 'rarity': 'Epic', 'sn': 'cupid_charm'},
    {'name': "Khabib's Papakha", 'value': 2970, 'rarity': 'Epic', 'sn': 'khabibs_papakha'},
    {'name': 'Diamond Ring', 'value': 3300, 'rarity': 'Epic', 'sn': 'diamond_ring'},
    {'name': 'Toy Bear', 'value': 3950, 'rarity': 'Epic', 'sn': 'toy_bear'},
    {'name': 'Neko Helmet', 'value': 4140, 'rarity': 'Epic', 'sn': 'neko_helmet'},
    {'name': 'Voodoo Doll', 'value': 3850, 'rarity': 'Epic', 'sn': 'voodoo_doll'},
    {'name': 'Signet Ring', 'value': 3850, 'rarity': 'Epic', 'sn': 'signet_ring'},
    {'name': 'Genie Lamp', 'value': 4290, 'rarity': 'Epic', 'sn': 'genie_lamp'},
    # --- Legendary (~60–180 TON) ---
    {'name': 'Swiss Watch', 'value': 6050, 'rarity': 'Legendary', 'sn': 'swiss_watch'},
    {'name': 'Vintage Cigar', 'value': 4070, 'rarity': 'Legendary', 'sn': 'vintage_cigar'},
    {'name': 'Kissed Frog', 'value': 4140, 'rarity': 'Legendary', 'sn': 'kissed_frog'},
    {'name': 'Magic Potion', 'value': 5775, 'rarity': 'Legendary', 'sn': 'magic_potion'},
    {'name': 'Mini Oscar', 'value': 8580, 'rarity': 'Legendary', 'sn': 'mini_oscar'},
    {'name': "Durov's Glasses", 'value': 10230, 'rarity': 'Legendary', 'sn': 'durovs_glasses'},
    {'name': 'Low Rider', 'value': 5960, 'rarity': 'Legendary', 'sn': 'low_rider'},
    {'name': 'Ion Gem', 'value': 7755, 'rarity': 'Legendary', 'sn': 'ion_gem'},
    {'name': 'Astral Shard', 'value': 12540, 'rarity': 'Legendary', 'sn': 'astral_shard'},
    {'name': 'Loot Bag', 'value': 15400, 'rarity': 'Legendary', 'sn': 'loot_bag'},
    # --- Mythic (топ, редко) ---
    {'name': 'Heroic Helmet', 'value': 19800, 'rarity': 'Mythic', 'sn': 'heroic_helmet'},
    {'name': 'Scared Cat', 'value': 25800, 'rarity': 'Mythic', 'sn': 'scared_cat'},
    {'name': 'Precious Peach', 'value': 28050, 'rarity': 'Mythic', 'sn': 'precious_peach'},
    {'name': "Durov's Cap", 'value': 46200, 'rarity': 'Mythic', 'sn': 'durovs_cap'},
    {'name': 'Plush Pepe', 'value': 556290, 'rarity': 'Mythic', 'sn': 'plush_pepe'},  # ~5057 TON × 110

]

# 1 TON ≈ 110 Stars
CASES = {
    'free_daily': {
        'name': 'Free case', 'price': 0, 'cooldown': 86400, 'category': 'free',
        'icon': '🎁', 'color': 'free', 'rarities': ['Common'],
        'weights': [100], 'min_stars': 1, 'max_stars': 5, 'stars_bias_low': True,
        'stars_chance': 1.0, 'force_names': [], 'force_max_value': 5,
        'desc': 'Раз в 24ч · 1–5⭐', 'cover': 'free',
    },
    'promo_case': {
        'name': 'Promo case', 'price': 0, 'category': 'promo', 'require_promo': True,
        'icon': '👑', 'color': 'c-pepe', 'rarities': ['Common'],
        'weights': [100], 'min_stars': 1, 'max_stars': 5, 'stars_chance': 1.0,
        'force_names': [], 'force_max_value': 5,
        'desc': 'По промокоду · 1 раз · 1–5⭐', 'cover': 'promo', 'once_per_code': True,
    },
    'nft_pepe': {
        'name': 'Pepe case', 'price': 650, 'category': 'nft',
        'icon': '🐸', 'color': 'c-pepe', 'rarities': ['Rare', 'Epic', 'Legendary'],
        'weights': [72, 22, 6], 'min_stars': 40, 'max_stars': 150, 'stars_chance': 0.18,
        'force_names': ['Plush Pepe', 'Kissed Frog', 'Scared Cat', 'Кольцо', 'Swiss Watch', 'Jelly Bunny'],
        'desc': 'Pepe · премиум NFT', 'cover': 'pepe',
    },
    'only_onyx': {
        'name': 'Onyx black', 'price': 1700, 'category': 'only_nft',
        'icon': '🖤', 'color': 'c-tg', 'rarities': ['Epic', 'Legendary', 'Mythic'],
        'weights': [70, 25, 5], 'min_stars': 0, 'max_stars': 0, 'stars_chance': 0.0,
        'force_names': ['Electric Skull', 'Ion Gem', 'Astral Shard', 'Dark Ring', 'Neko Helmet'],
        'desc': 'Только NFT · тёмный люкс', 'cover': 'black',
    },
    'brand_snoop': {
        'name': 'Snoop dog', 'price': 800, 'category': 'brands',
        'icon': '🐕', 'color': 'c-tg', 'rarities': ['Rare', 'Epic', 'Legendary'],
        'weights': [70, 24, 6], 'min_stars': 50, 'max_stars': 180, 'stars_chance': 0.15,
        'force_names': ['Snoop Dogg', 'Top Hat', 'Vintage Cigar', 'Low Rider', 'Westside Sign'],
        'desc': 'Snoop pack', 'cover': 'snoop',
    },
    'minecraft_case': {
        'name': 'Minecraft', 'price': 450, 'category': 'themed',
        'icon': '🟩', 'color': 'c-starter', 'rarities': ['Uncommon', 'Rare', 'Epic'],
        'weights': [68, 25, 7], 'min_stars': 30, 'max_stars': 120, 'stars_chance': 0.28,
        'force_names': ['Ракета', 'Алмаз', 'Кубок', 'Кольцо', 'Crystal Ball', 'Ion Gem'],
        'desc': 'Блочный вайб · ⭐ + NFT', 'cover': 'minecraft',
    },
    'bednyy_shkolnik': {
        'name': 'Бомж', 'price': 100, 'category': 'nft',
        'icon': '🧳', 'color': 'c-starter', 'rarities': ['Common', 'Uncommon', 'Rare'],
        'weights': [78, 18, 4], 'min_stars': 5, 'max_stars': 40, 'stars_chance': 0.55,
        'force_names': ['Мишка', 'Сердце', 'Конфета', 'Подарок', 'Звезда', 'Торт'],
        'desc': 'Дешёвый · чаще ⭐, редко NFT', 'cover': 'bomzh',
    },
    'bogach': {
        'name': 'Богач', 'price': 2500, 'category': 'rich',
        'icon': '💼', 'color': 'c-tg', 'rarities': ['Epic', 'Legendary', 'Mythic'],
        'weights': [68, 26, 6], 'min_stars': 0, 'max_stars': 0, 'stars_chance': 0.05,
        'force_names': ['Swiss Watch', 'Diamond Ring', 'Plush Pepe', 'Mini Oscar', 'Durovs Cap', 'Precious Peach'],
        'desc': 'Топ · жирные NFT', 'cover': 'bogach',
    },
    'september_case': {
        'name': '1 сентября', 'price': 350, 'category': 'season',
        'icon': '📚', 'color': 'c-pepe', 'rarities': ['Uncommon', 'Rare', 'Epic'],
        'weights': [70, 24, 6], 'min_stars': 25, 'max_stars': 100, 'stars_chance': 0.30,
        'force_names': ['Букет', 'Ракета', 'Торт', 'Подарок', 'Кольцо', 'Lol Pop'],
        'desc': 'Сезон · школа', 'cover': 'september',
    },
    'elite_case': {
        'name': 'Elite', 'price': 1800, 'category': 'brands',
        'icon': '👑', 'color': 'c-tg', 'rarities': ['Epic', 'Legendary', 'Mythic'],
        'weights': [70, 24, 6], 'min_stars': 0, 'max_stars': 0, 'stars_chance': 0.08,
        'force_names': ['Swiss Watch', 'Diamond Ring', 'Signet Ring', 'Mini Oscar', 'Heroic Helmet'],
        'desc': 'Elite статус', 'cover': 'elite',
    },
    # --- скоро будет (арт ещё нет) ---
    'nft_magic': {
        'name': 'Magic', 'price': 1000, 'category': 'nft',
        'icon': '🔮', 'color': 'c-pepe', 'rarities': ['Rare', 'Epic', 'Legendary'],
        'weights': [70, 24, 6], 'min_stars': 50, 'max_stars': 200, 'stars_chance': 0.16,
        'force_names': ['Crystal Ball', 'Genie Lamp', 'Magic Potion', 'Ion Gem', 'Astral Shard'],
        'desc': 'Скоро · магия', 'cover': 'magic',
    },
    'reliz': {
        'name': 'Reliz', 'price': 275, 'category': 'season',
        'icon': '🚀', 'color': 'c-starter', 'rarities': ['Uncommon', 'Rare', 'Epic'],
        'weights': [70, 24, 6], 'min_stars': 20, 'max_stars': 90, 'stars_chance': 0.32,
        'force_names': ['Hanging Star', 'Sakura Flower', 'Evil Eye', 'Звезда', 'Ракета'],
        'desc': '🚀 Релиз · лимит 50 открытий', 'cover': 'reliz',
        'max_opens_global': 50,
    },
    'only_durov': {
        'name': 'Durov', 'price': 4000, 'category': 'only_nft',
        'icon': '🧢', 'color': 'c-tg', 'rarities': ['Epic', 'Legendary', 'Mythic'],
        'weights': [70, 24, 6], 'min_stars': 0, 'max_stars': 0, 'stars_chance': 0.0,
        # Cap очень редко (через rarity/Mythic + bias), не в каждом втором открытии
        'force_names': [
            "Durov's Glasses", 'Heroic Helmet', 'Mini Oscar', 'Swiss Watch',
            'Diamond Ring', 'Signet Ring', 'Plush Pepe', "Durov's Cap",
        ],
        'desc': 'Only Durov · редкий Cap', 'cover': 'durov', 'coming_soon': False,
    },
    'nft_candy': {
        'name': 'Candy', 'price': 400, 'category': 'nft',
        'icon': '🍬', 'color': 'c-pepe', 'rarities': ['Common', 'Uncommon', 'Rare'],
        'weights': [50, 35, 15], 'min_stars': 20, 'max_stars': 90, 'stars_chance': 0.25,
        'force_names': ['Конфета', 'Lol Pop', 'Cookie Heart', 'Candy Cane', 'Сердце', 'Berry Box', 'Jelly Bunny'],
        'desc': 'Сладкий кейс', 'cover': 'candy',
    },

    'halloween_case': {
        'name': 'Halloween', 'price': 550, 'category': 'season',
        'icon': '🎃', 'color': 'c-tg', 'rarities': ['Uncommon', 'Rare', 'Epic'],
        'weights': [55, 32, 13], 'min_stars': 30, 'max_stars': 120, 'stars_chance': 0.18,
        'force_names': ['Mad Pumpkin', 'Flying Broom', 'Skull Flower', 'Electric Skull', 'Voodoo Doll', 'Magic Potion'],
        'desc': '🎃 Сезон · лимит 50 открытий', 'cover': 'halloween',
        'max_opens_global': 50,
    },
    'angel_case': {
        'name': 'Angel', 'price': 900, 'category': 'nft',
        'icon': '😇', 'color': 'c-starter', 'rarities': ['Rare', 'Epic', 'Legendary'],
        'weights': [58, 30, 12], 'min_stars': 40, 'max_stars': 160, 'stars_chance': 0.16,
        'force_names': ['Eternal Rose', 'Cupid Charm', 'Crystal Ball', 'Swiss Watch', 'Mini Oscar', "Durov's Glasses"],
        'desc': 'Angel case', 'cover': 'angel',
    },
    'jeremy_case': {
        'name': 'Jeremy Scott', 'price': 1200, 'category': 'brands',
        'icon': '👟', 'color': 'c-tg', 'rarities': ['Rare', 'Epic', 'Legendary'],
        'weights': [55, 32, 13], 'min_stars': 50, 'max_stars': 180, 'stars_chance': 0.14,
        'force_names': ['Top Hat', 'Bow Tie', 'Swag Bag', 'Low Rider', 'Swiss Watch', 'Signet Ring'],
        'desc': 'Jeremy Scott', 'cover': 'jeremy',
    },
    'fruits_case': {
        'name': 'Фрукты', 'price': 300, 'category': 'nft',
        'icon': '🍎', 'color': 'c-starter', 'rarities': ['Common', 'Uncommon', 'Rare'],
        'weights': [72, 22, 6], 'min_stars': 15, 'max_stars': 70, 'stars_chance': 0.42,
        'force_names': ['Berry Box', 'Precious Peach', 'Lol Pop', 'Сердце', 'Букет'],
        'desc': 'Скоро · фрукты', 'cover': 'fruits',
    },
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
    iv = float(iv or 0); tv = float(tv or 1)
    if tv <= 0 or iv <= 0:
        return 0.01
    raw = (iv / tv) * 100.0
    # x2 ≈ 43% (raw=50 * 0.86) — реже заходит, но не жёстко
    edge_f = 0.86
    ch = max(0.01, min(65.0, raw * edge_f))
    bonus = min(MAX_CHANCE_BONUS, max(0.0, float(bonus or 0)))
    ch = min(68.0, ch + bonus)
    return round(ch, 2)

def roll_crash_point() -> float:
    r = random.random()
    edge_f = max(0.92, 1.0 - max(HOUSE_EDGE, 0.04))
    # ~4% instant 1.00x
    if r < 0.04:
        return 1.0
    p = max(1.01, edge_f / (1 - r))
    return min(80.0, round(p * 100) / 100)  # кап 80x — анти-джекпот-абуз

def mines_mult(mines: int, opened: int) -> float:
    total = 25
    mult = 1.0
    for i in range(opened):
        remaining = total - i
        safe = remaining - mines
        if safe <= 0:
            break
        mult *= remaining / safe
    # дом забирает HOUSE_EDGE с выплаты
    return max(1.0, mult * (1.0 - max(HOUSE_EDGE, 0.04)))

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
        start_param = (data.get("start_param") or "").strip()
        return {"id": tg_id, "username": user.get("username") or user.get("first_name") or "Player", "first_name": user.get("first_name") or "Player", "start_param": start_param}
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
            # referral from start_param ref_123456
            sp = str(info.get("start_param") or "").strip()
            if sp.startswith("ref_"):
                try:
                    ref_id = int(sp[4:].split("_")[0])
                    if ref_id and ref_id != tg_id:
                        exists = await (await db.execute("SELECT 1 FROM users WHERE tg_id=?", (ref_id,))).fetchone()
                        if exists:
                            await db.execute(
                                "INSERT OR IGNORE INTO referrals(referred_id,referrer_id,earned,created_at) VALUES(?,?,0,?)",
                                (tg_id, ref_id, now_ts()),
                            )
                            await db.commit()
                except Exception as e:
                    print("[ref bind]", e)
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

def consume_nft_bet(u: dict, item_index: int):
    """Списать NFT из инвентаря → вернуть (bet_stars, item)."""
    idx = int(item_index)
    inv = u.get("inventory") or []
    if idx < 0 or idx >= len(inv):
        raise HTTPException(400, "Нет такого предмета в инвентаре")
    item = inv.pop(idx)
    bet = int(item.get("value") or 0)
    if bet < MIN_BET:
        inv.insert(idx, item)
        raise HTTPException(400, f"NFT дешевле мин. ставки {MIN_BET}⭐")
    return bet, item



async def pay_referral(referred_tg_id: int, deposit_stars: int):
    """L1: 5% рефереру · L2: 1% рефереру реферера. Мин. деп 50⭐."""
    if deposit_stars < 50:
        return
    bonus_l1 = max(1, int(deposit_stars * 0.05))
    bonus_l2 = max(1, int(deposit_stars * 0.01))
    try:
        async with get_db() as db:
            row = await (await db.execute(
                "SELECT referrer_id FROM referrals WHERE referred_id=?", (int(referred_tg_id),)
            )).fetchone()
            if not row:
                return
            ref_id = int(row[0])
            bal = await (await db.execute("SELECT balance FROM users WHERE tg_id=?", (ref_id,))).fetchone()
            if bal:
                await db.execute("UPDATE users SET balance=? WHERE tg_id=?", (int(bal[0] or 0) + bonus_l1, ref_id))
                await db.execute(
                    "UPDATE referrals SET earned=COALESCE(earned,0)+? WHERE referred_id=?",
                    (bonus_l1, int(referred_tg_id)),
                )
            # L2
            row2 = await (await db.execute(
                "SELECT referrer_id FROM referrals WHERE referred_id=?", (ref_id,)
            )).fetchone()
            ref2_id = int(row2[0]) if row2 else 0
            if ref2_id and ref2_id != ref_id and ref2_id != int(referred_tg_id):
                bal2 = await (await db.execute("SELECT balance FROM users WHERE tg_id=?", (ref2_id,))).fetchone()
                if bal2:
                    await db.execute("UPDATE users SET balance=? WHERE tg_id=?", (int(bal2[0] or 0) + bonus_l2, ref2_id))
            await db.commit()
        try:
            await tg_notify(ref_id, f"👥 Реферал задепнул · тебе <b>+{bonus_l1}⭐</b> (5%)")
            if ref2_id:
                await tg_notify(ref2_id, f"👥 Реферал 2 ур. задепнул · тебе <b>+{bonus_l2}⭐</b> (1%)")
        except Exception:
            pass
    except Exception as e:
        print("[pay_referral]", e)



async def tg_notify(tg_id: int, text: str):
    """Тихое уведомление игроку в бота (если BOT_TOKEN задан)."""
    if not BOT_TOKEN or not tg_id:
        return
    try:
        import urllib.request
        import urllib.parse
        data = urllib.parse.urlencode({
            "chat_id": int(tg_id),
            "text": text[:3500],
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data=data, method="POST",
        )
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: urllib.request.urlopen(req, timeout=8).read()
        )
    except Exception as e:
        print("[tg_notify]", e)

async def ledger(kind: str, amount: int, tg_id: int = 0, detail: str = ""):
    try:
        async with get_db() as db:
            await db.execute(
                "INSERT INTO house_ledger(kind,amount,tg_id,detail,created_at) VALUES(?,?,?,?,?)",
                (kind, int(amount), int(tg_id or 0), detail or "", now_ts()),
            )
            await db.commit()
    except Exception as e:
        print("[ledger]", e)

async def set_withdraw_hold(tg_id: int, seconds: int = 6 * 3600, reason: str = ""):
    until = now_ts() + int(seconds)
    try:
        async with get_db() as db:
            # sqlite may lack column — try update
            try:
                await db.execute(
                    "UPDATE users SET withdraw_hold_until=? WHERE tg_id=?",
                    (until, int(tg_id)),
                )
                await db.commit()
            except Exception:
                pass
        if reason:
            print(f"[hold] user {tg_id} until {until}: {reason}")
    except Exception as e:
        print("[hold]", e)

async def bump_weekly(tg_id: int, username: str, points: int):
    import time as _t
    week_key = time.strftime("%Y-W%W", _t.gmtime(now_ts()))
    try:
        async with get_db() as db:
            row = await (await db.execute(
                "SELECT score, week_key FROM weekly_scores WHERE tg_id=?", (int(tg_id),)
            )).fetchone()
            if not row:
                await db.execute(
                    "INSERT INTO weekly_scores(tg_id,username,score,week_key) VALUES(?,?,?,?)",
                    (int(tg_id), username or "", int(points), week_key),
                )
            else:
                sc = int(row[0] or 0)
                wk = row[1] or ""
                if wk != week_key:
                    sc = 0
                await db.execute(
                    "UPDATE weekly_scores SET username=?, score=?, week_key=? WHERE tg_id=?",
                    (username or "", sc + int(points), week_key, int(tg_id)),
                )
            await db.commit()
    except Exception as e:
        print("[weekly]", e)



# -------------------- CASE ROLLS --------------------
def _cheap_fallback_stars(price: int) -> dict:
    """Вместо звёзд — NFT ≈ 35–70% цены кейса; звёзды очень редко."""
    price = max(0, int(price or 0))
    if price <= 0:
        target = random.randint(15, 40)
    else:
        r = random.random()
        if r < 0.55:
            target = random.randint(max(15, int(price * 0.30)), max(25, int(price * 0.55)))
        elif r < 0.90:
            target = random.randint(max(20, int(price * 0.50)), max(30, int(price * 0.75)))
        else:
            target = random.randint(max(25, int(price * 0.70)), max(40, int(price * 0.95)))
    # 12% — чистые звёзды, иначе NFT
    if random.random() < 0.12:
        return {"kind": "stars", "stars": target}
    band = [g for g in GIFTS_FLAT if abs(int(g.get("value") or 0) - target) <= max(50, int(target * 0.3))]
    if not band:
        band = [g for g in GIFTS_FLAT if int(g.get("value") or 0) >= 15]
    ww = []
    for g in band:
        v = int(g.get("value") or 0)
        dist = abs(v - target)
        ww.append((1.3 if v <= target else 1.0) / (dist + 20))
    g = weighted_pick(band, ww) or band[0]
    return {"kind": "gift", "gift": gift_public(g)}


def _pick_gift_for_case(pool: list, price: int) -> dict:
    """Слоты → теор. EV ~0.97–0.99, + bias к более дешёвому NFT → RTP ~94–96%.
    Не «нищета» на дешёвых и не раздача на дорогих.
    """
    price = max(1, int(price or 1))
    # (mult, weight) sum weights = 100
    # EV ≈ 0.98 × price до bias
    slots = [
        (0.40, 20), (0.55, 19), (0.75, 17), (0.95, 15),
        (1.12, 12), (1.50, 9),  (2.40, 5),  (4.50, 3),
    ]
    # дешёвые: чуть жёстче, но не 86% как раньше (было слишком жестоко)
    if price < 400:
        slots = [
            (0.40, 21), (0.55, 19), (0.72, 17), (0.92, 15),
            (1.10, 12), (1.45, 8),  (2.20, 5),  (3.80, 3),
        ]
    # очень дорогие (Durov и т.п.): реже «жир», чаще недокуп
    if price >= 2500:
        slots = [
            (0.28, 24), (0.42, 22), (0.58, 18), (0.78, 14),
            (0.95, 10), (1.25, 7),  (1.90, 3.5), (3.20, 1.5),
        ]
    total_w = sum(w for _, w in slots)
    r = random.random() * total_w
    acc = 0.0
    mult = slots[0][0]
    for m, w in slots:
        acc += w
        if r <= acc:
            mult = m
            break
    target = max(10, int(price * mult))

    if not pool:
        return _cheap_fallback_stars(price)

    # ближайший подарок к target (с лёгким bias вниз для edge)
    best = None
    best_score = 1e18
    for g in pool:
        v = max(10, int(g.get("value") or 50))
        # штраф за слишком жирный дроп
        over = max(0, v - target) * (1.4 if price < 400 else 1.1)
        under = max(0, target - v) * 1.0
        score = over + under + abs(v - target) * 0.15
        if score < best_score:
            best_score = score
            best = g
    if not best:
        return _cheap_fallback_stars(price)
    return {"kind": "gift", "gift": gift_public(best)}



# ----- DROP TABLES (RTP ~92–95%, явные % на каждый предмет) -----
DROP_TABLES = {
    "nft_pepe": [
        {"stars": 162, "pct": 11.74},
        {"stars": 260, "pct": 26.16},
        {"name": "Spy Agaric", "value": 600, "pct": 29.5},
        {"name": "Jelly Bunny", "value": 850, "pct": 19.04},
        {"name": "Sleigh Bell", "value": 1200, "pct": 9.3},
        {"name": "Trapped Heart", "value": 1640, "pct": 3.81},
        {"name": "Khabib's Papakha", "value": 2970, "pct": 0.37},
        {"name": "Kissed Frog", "value": 4140, "pct": 0.07},
        {"name": "Swiss Watch", "value": 6050, "pct": 0.01},
    ],
    "only_onyx": [
        {"name": "Desk Calendar", "value": 550, "pct": 12.83},
        {"name": "Bow Tie", "value": 700, "pct": 17.37},
        {"name": "Crystal Ball", "value": 1320, "pct": 21.9},
        {"name": "Love Potion", "value": 1600, "pct": 19.97},
        {"name": "Cupid Charm", "value": 2390, "pct": 12.93},
        {"name": "Electric Skull", "value": 2760, "pct": 10.21},
        {"name": "Neko Helmet", "value": 4140, "pct": 4.18},
        {"name": "Ion Gem", "value": 7755, "pct": 0.54},
        {"name": "Astral Shard", "value": 12540, "pct": 0.07},
    ],
    "brand_snoop": [
        {"stars": 200, "pct": 25.47},
        {"stars": 320, "pct": 21.83},
        {"name": "Snoop Dogg", "value": 620, "pct": 15.02},
        {"name": "Evil Eye", "value": 810, "pct": 12.27},
        {"name": "Hanging Star", "value": 1040, "pct": 9.88},
        {"name": "Top Hat", "value": 1210, "pct": 8.56},
        {"name": "Cupid Charm", "value": 2390, "pct": 3.99},
        {"name": "Vintage Cigar", "value": 4070, "pct": 1.92},
        {"name": "Low Rider", "value": 5960, "pct": 1.06},
    ],
    "minecraft_case": [
        {"name": "Алмаз", "value": 100, "pct": 20.68},
        {"name": "Кубок", "value": 100, "pct": 20.68},
        {"name": "Кольцо", "value": 110, "pct": 21.79},
        {"name": "Spy Agaric", "value": 600, "pct": 15.95},
        {"name": "Evil Eye", "value": 810, "pct": 11.78},
        {"name": "Crystal Ball", "value": 1320, "pct": 6.14},
        {"name": "Cupid Charm", "value": 2390, "pct": 2.13},
        {"name": "Voodoo Doll", "value": 3850, "pct": 0.74},
        {"name": "Ion Gem", "value": 7755, "pct": 0.11},
    ],
    "bednyy_shkolnik": [
        {"name": "Подарок", "value": 25, "pct": 4.71},
        {"name": "Звезда", "value": 25, "pct": 4.71},
        {"name": "Торт", "value": 50, "pct": 29.28},
        {"name": "Алмаз", "value": 100, "pct": 58.39},
        {"name": "Desk Calendar", "value": 550, "pct": 2.53},
        {"name": "Jelly Bunny", "value": 850, "pct": 0.38},
    ],
    "bogach": [
        {"name": "Bow Tie", "value": 700, "pct": 6.56},
        {"name": "Crystal Ball", "value": 1320, "pct": 20.5},
        {"name": "Trapped Heart", "value": 1640, "pct": 23.43},
        {"name": "Cupid Charm", "value": 2390, "pct": 21.64},
        {"name": "Diamond Ring", "value": 3300, "pct": 14.78},
        {"name": "Genie Lamp", "value": 4290, "pct": 8.76},
        {"name": "Swiss Watch", "value": 6050, "pct": 3.31},
        {"name": "Mini Oscar", "value": 8580, "pct": 0.88},
        {"name": "Astral Shard", "value": 12540, "pct": 0.14},
        {"name": "Precious Peach", "value": 28050, "pct": 0.0},
        {"name": "Durov's Cap", "value": 46200, "pct": 0.0},
    ],
    "september_case": [
        {"name": "Кольцо", "value": 110, "pct": 63.58},
        {"name": "Lol Pop", "value": 550, "pct": 27.43},
        {"name": "Money Pot", "value": 1000, "pct": 7.16},
        {"name": "Love Potion", "value": 1600, "pct": 1.68},
        {"name": "Khabib's Papakha", "value": 2970, "pct": 0.15},
    ],
    "elite_case": [
        {"name": "Desk Calendar", "value": 550, "pct": 12.14},
        {"name": "Homemade Cake", "value": 800, "pct": 18.84},
        {"name": "Crystal Ball", "value": 1320, "pct": 21.68},
        {"name": "Trapped Heart", "value": 1640, "pct": 19.64},
        {"name": "Cupid Charm", "value": 2390, "pct": 13.18},
        {"name": "Diamond Ring", "value": 3300, "pct": 7.44},
        {"name": "Signet Ring", "value": 3850, "pct": 5.25},
        {"name": "Swiss Watch", "value": 6050, "pct": 1.43},
        {"name": "Mini Oscar", "value": 8580, "pct": 0.39},
        {"name": "Heroic Helmet", "value": 19800, "pct": 0.01},
    ],
    "nft_magic": [
        {"stars": 250, "pct": 5.25},
        {"stars": 400, "pct": 15.68},
        {"name": "Bow Tie", "value": 700, "pct": 25.92},
        {"name": "Jingle Bells", "value": 930, "pct": 23.98},
        {"name": "Crystal Ball", "value": 1320, "pct": 16.01},
        {"name": "Trapped Heart", "value": 1640, "pct": 10.5},
        {"name": "Electric Skull", "value": 2760, "pct": 2.24},
        {"name": "Genie Lamp", "value": 4290, "pct": 0.34},
        {"name": "Magic Potion", "value": 5775, "pct": 0.07},
        {"name": "Ion Gem", "value": 7755, "pct": 0.01},
        {"name": "Astral Shard", "value": 12540, "pct": 0.0},
    ],
    "reliz": [
        {"name": "Кольцо", "value": 110, "pct": 68.85},
        {"name": "Desk Calendar", "value": 550, "pct": 28.71},
        {"name": "Evil Eye", "value": 810, "pct": 2.08},
        {"name": "Hanging Star", "value": 1040, "pct": 0.23},
        {"name": "Sakura Flower", "value": 1100, "pct": 0.13},
        {"name": "Cupid Charm", "value": 2390, "pct": 0.0},
    ],
    "only_durov": [
        {"name": "Crystal Ball", "value": 1320, "pct": 12.84},
        {"name": "Trapped Heart", "value": 1640, "pct": 15.11},
        {"name": "Electric Skull", "value": 2760, "pct": 16.55},
        {"name": "Diamond Ring", "value": 3300, "pct": 15.48},
        {"name": "Signet Ring", "value": 3850, "pct": 14.04},
        {"name": "Magic Potion", "value": 5775, "pct": 9.1},
        {"name": "Swiss Watch", "value": 6050, "pct": 8.52},
        {"name": "Mini Oscar", "value": 8580, "pct": 4.65},
        {"name": "Durov's Glasses", "value": 10230, "pct": 3.19},
        {"name": "Heroic Helmet", "value": 19800, "pct": 0.5},
        {"name": "Durov's Cap", "value": 46200, "pct": 0.02},
    ],
    "nft_candy": [
        {"name": "Кольцо", "value": 110, "pct": 55.84},
        {"name": "Lol Pop", "value": 550, "pct": 10.18},
        {"name": "Cookie Heart", "value": 550, "pct": 10.18},
        {"name": "Candy Cane", "value": 550, "pct": 10.18},
        {"name": "Jelly Bunny", "value": 850, "pct": 5.6},
        {"name": "Berry Box", "value": 910, "pct": 5.07},
        {"name": "Surge Board", "value": 1500, "pct": 2.35},
        {"name": "Diamond Ring", "value": 3300, "pct": 0.6},
    ],
    "halloween_case": [
        {"name": "Кольцо", "value": 110, "pct": 56.33},
        {"name": "Desk Calendar", "value": 550, "pct": 13.14},
        {"name": "Bow Tie", "value": 700, "pct": 9.86},
        {"name": "Money Pot", "value": 1000, "pct": 6.24},
        {"name": "Skull Flower", "value": 1280, "pct": 4.44},
        {"name": "Flying Broom", "value": 1430, "pct": 3.79},
        {"name": "Mad Pumpkin", "value": 1460, "pct": 3.68},
        {"name": "Electric Skull", "value": 2760, "pct": 1.37},
        {"name": "Voodoo Doll", "value": 3850, "pct": 0.78},
        {"name": "Magic Potion", "value": 5775, "pct": 0.37},
    ],
    "angel_case": [
        {"stars": 225, "pct": 9.97},
        {"stars": 360, "pct": 19.53},
        {"name": "Spy Agaric", "value": 600, "pct": 24.3},
        {"name": "Joyful Bundle", "value": 840, "pct": 20.99},
        {"name": "Crystal Ball", "value": 1320, "pct": 11.98},
        {"name": "Trapped Heart", "value": 1640, "pct": 7.89},
        {"name": "Cupid Charm", "value": 2390, "pct": 3.04},
        {"name": "Eternal Rose", "value": 2860, "pct": 1.75},
        {"name": "Neko Helmet", "value": 4140, "pct": 0.45},
        {"name": "Swiss Watch", "value": 6050, "pct": 0.08},
        {"name": "Mini Oscar", "value": 8580, "pct": 0.01},
        {"name": "Durov's Glasses", "value": 10230, "pct": 0.01},
    ],
    "jeremy_case": [
        {"name": "Desk Calendar", "value": 550, "pct": 23.02},
        {"name": "Bow Tie", "value": 700, "pct": 21.68},
        {"name": "Swag Bag", "value": 900, "pct": 19.0},
        {"name": "Top Hat", "value": 1210, "pct": 14.87},
        {"name": "Surge Board", "value": 1500, "pct": 11.71},
        {"name": "Cupid Charm", "value": 2390, "pct": 5.86},
        {"name": "Signet Ring", "value": 3850, "pct": 2.25},
        {"name": "Low Rider", "value": 5960, "pct": 0.75},
        {"name": "Swiss Watch", "value": 6050, "pct": 0.72},
        {"name": "Durov's Glasses", "value": 10230, "pct": 0.14},
    ],
    "fruits_case": [
        {"name": "Алмаз", "value": 100, "pct": 25.54},
        {"name": "Кольцо", "value": 110, "pct": 37.91},
        {"name": "Lol Pop", "value": 550, "pct": 34.09},
        {"name": "Berry Box", "value": 910, "pct": 2.38},
        {"name": "Flying Broom", "value": 1430, "pct": 0.08},
        {"name": "Cupid Charm", "value": 2390, "pct": 0.0},
    ],
}


def roll_from_table(case_id: str, price_hint: int = 0) -> dict:
    """Явная таблица % → gift/stars. value из таблицы — источник истины для EV."""
    table = DROP_TABLES.get(case_id) or []
    if not table:
        return {"kind": "stars", "stars": 1}

    def _pick_one():
        total = sum(float(x.get("pct") or 0) for x in table) or 100.0
        r = random.random() * total
        acc = 0.0
        pick = table[-1]
        for x in table:
            acc += float(x.get("pct") or 0)
            if r <= acc:
                pick = x
                break
        return pick

    def _val(p):
        if "stars" in p and p.get("stars") is not None:
            return int(p["stars"])
        return int(p.get("value") or 0)

    pick = _pick_one()
    # anti-trash: если <22% цены кейса — 30% шанс перекрутить 1 раз
    if price_hint > 0 and _val(pick) < price_hint * 0.22 and random.random() < 0.30:
        pick = _pick_one()

    if "stars" in pick and pick.get("stars") is not None:
        return {"kind": "stars", "stars": int(pick["stars"])}
    name = pick.get("name") or "Подарок"
    val = int(pick.get("value") or 50)
    g = find_gift(name)
    if g:
        out = dict(g)
        out["value"] = val
        return {"kind": "gift", "gift": gift_public(out)}
    return {
        "kind": "gift",
        "gift": gift_public({"name": name, "value": val, "rarity": "Rare", "sn": gift_short_name(name)}),
    }


def roll_case_drop(case_id: str, c: dict) -> dict:
    """Return {kind: gift|stars, ...}. Платные — DROP_TABLES; free/promo — слабые ⭐."""
    price = int(c.get("price") or 0)

    # Free — чаще 1⭐
    if case_id == "free_daily" or c.get("category") == "free":
        r = random.random()
        if r < 0.70:
            stars = 1
        elif r < 0.90:
            stars = 2
        elif r < 0.97:
            stars = 3
        elif r < 0.995:
            stars = 4
        else:
            stars = 5
        return {"kind": "stars", "stars": stars}

    if case_id == "promo_case" or c.get("category") == "promo" or c.get("require_promo"):
        r = random.random()
        if r < 0.70:
            stars = 1
        elif r < 0.90:
            stars = 2
        elif r < 0.97:
            stars = 3
        else:
            stars = 4
        return {"kind": "stars", "stars": stars}

    # Платные кейсы — жёсткая таблица
    if case_id in DROP_TABLES:
        return roll_from_table(case_id, price_hint=price)

    if c.get("allin") or (c.get("category") == "allin"):
        jp_name = c.get("jackpot_name")
        jp_chance = float(c.get("jackpot_chance") or 0)
        if jp_name and random.random() * 100 < jp_chance:
            g = find_gift(jp_name) or {"name": jp_name, "value": int(c.get("jackpot_value") or 0), "rarity": "Mythic", "sn": gift_short_name(jp_name)}
            return {"kind": "gift", "gift": gift_public(g)}
        lose = c.get("lose_stars") or [0, 1, 2, 3, 5]
        lw = c.get("lose_weights") or [50, 25, 15, 7, 3]
        stars = int(weighted_pick(lose, lw) or 0)
        return {"kind": "stars", "stars": stars, "allin_lose": True}

    def _nft_near(target_val: int, price_hint: int = 0) -> dict:
        """NFT ≈ target_val (чуть ниже чаще — дом в плюсе)."""
        target_val = max(10, int(target_val or 10))
        tol = max(40, int(target_val * 0.25))
        band = [g for g in GIFTS_FLAT if abs(int(g.get("value") or 0) - target_val) <= tol]
        if not band and price_hint > 0:
            band = [g for g in GIFTS_FLAT if price_hint * 0.25 <= int(g.get("value") or 0) <= price_hint * 0.95]
        if not band:
            band = [g for g in GIFTS_FLAT if int(g.get("value") or 0) >= 15]
        # вес: ближе к target и чуть дешевле — выше шанс
        ww = []
        for g in band:
            v = int(g.get("value") or 0)
            dist = abs(v - target_val)
            cheap_bonus = 1.25 if v <= target_val else 1.0
            ww.append(cheap_bonus / (dist + 25))
        g = weighted_pick(band, ww) or band[0]
        return {"kind": "gift", "gift": gift_public(g)}

    # STAR-кейсы — ТОЛЬКО звёзды
    star_drops = c.get("star_drops") or []
    if star_drops and not (c.get("force_names") or c.get("rarities")):
        sw = c.get("star_weights") or [1] * len(star_drops)
        stars = int(weighted_pick(star_drops, sw) or star_drops[0])
        return {"kind": "stars", "stars": stars}

    # Обычные кейсы: звёзды очень редко; вместо них NFT ≈ ожидаемой сумме
    stars_chance = float(c.get("stars_chance") or 0) * 0.25  # в 4 раза реже
    if price >= 500:
        stars_chance = min(stars_chance, 0.08)
    if stars_chance > 0 and random.random() < stars_chance:
        lo = int(c.get("min_stars") or 1)
        hi = int(c.get("max_stars") or 20)
        if price > 0:
            lo = max(lo, int(price * 0.25))
            hi = min(max(hi, lo + 5), max(lo + 10, int(price * 0.75)))
        stars = random.randint(lo, hi)
        # 90% → NFT за ~ту же цену, 10% → звёзды
        if random.random() < 0.90:
            return _nft_near(stars, price)
        return {"kind": "stars", "stars": stars}

    names = list(c.get("force_names") or [])
    pool = []
    for n in names:
        g = find_gift(n)
        if g:
            pool.append(g)
        else:
            pool.append(gift_public({"name": n, "value": 50, "rarity": "Common", "sn": gift_short_name(n)}))

    rarities = list(c.get("rarities") or [])
    weights = list(c.get("weights") or [])
    if rarities:
        r = weighted_pick(rarities, weights) or rarities[0]
        rar_pool = list(_BY_RARITY.get(r, []))
        if rar_pool:
            if pool:
                same = [p for p in pool if p.get("rarity") == r]
                pick_from = (same or pool) + rar_pool[:8]
            else:
                pick_from = rar_pool
            return _pick_gift_for_case(pick_from, price)

    if pool:
        return _pick_gift_for_case(pool, price)

    return _cheap_fallback_stars(price)


def case_contents(case_id: str, c: dict) -> List[dict]:
    items = []
    if case_id in ("free_daily", "promo_case") or c.get("category") in ("free", "promo") or c.get("require_promo"):
        for s in (1, 2, 3, 4, 5):
            items.append({"name": f"⭐ {s}", "value": s, "rarity": "Common", "emoji": "⭐", "img": "", "drop_chance": None})
        return items
    # Показываем ровно таблицу дропа
    if case_id in DROP_TABLES:
        for x in DROP_TABLES[case_id]:
            if x.get("stars") is not None and "name" not in x:
                items.append({
                    "name": f"⭐ {int(x['stars'])}", "value": int(x["stars"]),
                    "rarity": "Common", "emoji": "⭐", "img": "", "drop_chance": x.get("pct"),
                })
            else:
                name = x.get("name") or "Gift"
                val = int(x.get("value") or 50)
                g = find_gift(name) or {"name": name, "value": val, "rarity": "Rare"}
                gp = gift_public(dict(g, value=val))
                gp["drop_chance"] = x.get("pct")
                items.append(gp)
        return items
    if c.get("star_drops"):
        for s, w in zip(c["star_drops"], c.get("star_weights") or [1]*len(c["star_drops"])):
            items.append({"name": f"⭐ {s}", "value": int(s), "rarity": "Common", "emoji": "⭐", "img": "", "drop_chance": None})
    for n in (c.get("force_names") or []):
        g = find_gift(n) or gift_public({"name": n, "value": 50, "rarity": "Common"})
        items.append({**gift_public(g), "drop_chance": None})
    seen=set(); out=[]
    for it in items:
        k=(it.get("name") or "").lower()
        if k in seen: continue
        seen.add(k); out.append(it)
    return out[:60]

QUESTS_DEF = [
    {"id": "upg_5", "title": "Прокрути апгрейд 5 раз", "need": 5, "reward": 2},
    {"id": "case_3", "title": "Открой 3 кейса", "need": 3, "reward": 2},
    {"id": "mines_2", "title": "Сыграй в мины 2 раза", "need": 2, "reward": 1},
    {"id": "pvp_1", "title": "Сыграй 1 PvP", "need": 1, "reward": 1},
    {"id": "sell_3", "title": "Продай 3 предмета", "need": 3, "reward": 1},
    {"id": "upg_15", "title": "Апгрейд 15 раз", "need": 15, "reward": 5},
]


# -------------------- APP --------------------
app = FastAPI(title="GiftUpgrader")

@app.middleware("http")
async def maintenance_middleware(request: Request, call_next):
    path = request.url.path or ""
    allow = (
        path in ("/api/health", "/api/maintenance", "/api/rates", "/tonconnect-manifest.json")
        or path.startswith("/admin")
        or path.startswith("/api/admin")
        or path == "/"
        or path.endswith(".html")
        or path.endswith(".js")
        or path.endswith(".css")
        or path.endswith(".webp")
        or path.endswith(".png")
        or path.endswith(".ico")
    )
    if allow or not MAINTENANCE_MODE.get("on"):
        return await call_next(request)
    # admin bypass
    is_adm = False
    try:
        aid = request.headers.get("X-Admin-Id") or request.query_params.get("admin_id") or ""
        if str(aid) == str(ADMIN_TG_ID):
            is_adm = True
        if not is_adm:
            raw = (
                request.headers.get("X-Telegram-Init-Data")
                or request.headers.get("authorization")
                or request.headers.get("Authorization")
                or ""
            )
            if raw and raw != "dev":
                info = _parse_init_data(raw) if "_parse_init_data" in dir() else None
                # try function
                try:
                    info = _parse_init_data(raw)
                except Exception:
                    info = None
                if info and int(info.get("id") or info.get("tg_id") or 0) == int(ADMIN_TG_ID):
                    is_adm = True
            elif raw == "dev" and int(ADMIN_TG_ID) > 0:
                # local dev: only if ADMIN allows
                is_adm = True
    except Exception:
        is_adm = False
    if is_adm:
        return await call_next(request)
    return JSONResponse(
        {"ok": False, "maintenance": True, "error": MAINTENANCE_MODE.get("message") or "Техработы"},
        status_code=503,
    )


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
    ok = True; err = None
    try:
        async with get_db() as db:
            await db.execute("SELECT 1")
    except Exception as e:
        ok = False; err = str(e)
    return {"ok": ok, "db": "postgres" if USE_POSTGRES else "sqlite", "error": err}

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

def resolve_case_id(case_id: str):
    """Нормализация id кейса + aliases."""
    cid = (case_id or "").strip()
    ALIAS = {
        "promo": "promo_case", "promocase": "promo_case", "promo-case": "promo_case",
        "pepe": "nft_pepe", "pepe_case": "nft_pepe",
        "onyx": "only_onyx", "onyx_black": "only_onyx", "black": "only_onyx",
        "snoop": "brand_snoop", "snoop_dog": "brand_snoop", "snoopdog": "brand_snoop",
        "minecraft": "minecraft_case",
        "bomzh": "bednyy_shkolnik", "бомж": "bednyy_shkolnik",
        "bogach": "bogach", "богач": "bogach", "rich": "bogach",
        "elite": "elite_case", "durov": "only_durov", "durov_case": "only_durov",
        "free": "free_daily", "free_case": "free_daily",
        "candy": "nft_candy", "halloween": "halloween_case", "angel": "angel_case",
        "jeremy": "jeremy_case", "jeremy_scott": "jeremy_case", "magic": "nft_magic",
        "fruits": "fruits_case", "фрукты": "fruits_case",
        "reliz": "reliz", "release": "reliz",
        "september": "september_case", "1_sentyabrya": "september_case", "1 сентября": "september_case",
    }
    key = cid.lower().replace(" ", "_").replace("-", "_")
    cid = ALIAS.get(cid) or ALIAS.get(key) or cid
    if cid in CASES:
        return cid, CASES[cid]
    low = cid.lower()
    for k, v in CASES.items():
        if k.lower() == low or (v.get("name") or "").lower() == low:
            return k, v
    return cid, None

@app.get("/api/case/{case_id}/contents")
async def case_contents_api(case_id: str):
    case_id, c = resolve_case_id(case_id)
    if not c:
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
    promo_code: Optional[str] = None
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
    bet: int = 0
    mines: int = 5
    item_index: int = -1

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
    bet: int = 0
    item_index: int = -1

class PvpJoinRequest(BaseModel):
    lobby_id: str
    bet: int = 0
    item_index: int = -1

class PvpStartRequest(BaseModel):
    lobby_id: str

class BattleCreateRequest(BaseModel):
    case_id: str

class BattleJoinRequest(BaseModel):
    room_id: str

class TradeOfferRequest(BaseModel):
    to_id: int

class TradeAddRequest(BaseModel):
    trade_id: str
    stars: int = 0
    item_index: Optional[int] = None

class TradeIdRequest(BaseModel):
    trade_id: str

class PromoActivate: pass

class AdminGiveRequest(BaseModel):
    user_id: int
    amount: int

class CraftRequest(BaseModel):
    item_ids: list = []
    item_indices: list = []


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
    chance = calc_upgrade_chance(iv, tv, u.get("chance_bonus") or 0)
    success = random.random() * 100 < chance
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
    if body.item_index < 0 or body.item_index >= len(u["inventory"]):
        raise HTTPException(400, "Item not found")
    item = u["inventory"].pop(body.item_index)
    raw = int(item.get("value") or 0)
    # −5% комиссия: анти-абуз + небольшой edge
    price = max(0, int(raw * (1.0 - max(0.0, min(0.15, SELL_FEE)))))
    u["balance"] += price
    await save_user(u)
    await bump_quest(u["tg_id"], "sell_3")
    await add_history(u["tg_id"], "shop", "win", f"Продал {item.get('name')}", price)
    return {"success": True, "balance": u["balance"], "price": price, "fee": raw - price}

@app.post("/api/craft")
async def craft_items(body: CraftRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    """3–10 предметов → один новый. RTP ~88% (EV ≈ 0.88 × сумма)."""
    u = await current_user(authorization, x_telegram_init_data)
    inv = list(u.get("inventory") or [])
    indices = []
    # 1) по индексам (предпочтительно)
    for i in (body.item_indices or []):
        try:
            i = int(i)
        except Exception:
            continue
        if 0 <= i < len(inv) and i not in indices:
            indices.append(i)
    # 2) по id / uid
    if len(indices) < 3:
        for iid in (body.item_ids or []):
            sid = str(iid or "")
            if not sid:
                continue
            for i, it in enumerate(inv):
                if i in indices:
                    continue
                if str(it.get("id") or "") == sid or str(it.get("uid") or "") == sid:
                    indices.append(i)
                    break
    if len(indices) < 3:
        raise HTTPException(400, "Минимум 3 предмета")
    if len(indices) > 10:
        raise HTTPException(400, "Максимум 10 предметов")
    # списать (с конца, чтобы индексы не сдвигались)
    taken = []
    total = 0
    for i in sorted(indices, reverse=True):
        if 0 <= i < len(inv):
            it = inv.pop(i)
            taken.append(it)
            total += int(it.get("value") or 0)
    if len(taken) < 3 or total <= 0:
        raise HTTPException(400, "Не удалось списать предметы")
    # слоты результата: 10%..1000% от total, EV ~88%
    slots = [
        (0.10, 12), (0.25, 18), (0.45, 20), (0.70, 18),
        (0.90, 12), (1.10, 10), (1.50, 6),  (2.50, 2.5),
        (5.00, 1.2), (10.0, 0.3),
    ]
    total_w = sum(w for _, w in slots)
    r = random.random() * total_w
    acc = 0.0
    mult = 0.70
    for m, w in slots:
        acc += w
        if r <= acc:
            mult = m
            break
    target_val = max(10, int(total * mult))
    # ближайший NFT к target
    pool = list(GIFTS_FLAT)
    best = None
    best_score = 1e18
    for g in pool:
        v = int(g.get("value") or 0)
        if v < 10:
            continue
        score = abs(v - target_val)
        if v > target_val * 1.5:
            score *= 1.3
        if score < best_score:
            best_score = score
            best = g
    if not best:
        best = {"name": "Craft Gift", "value": target_val, "rarity": "Rare", "sn": "gift", "emoji": "✨"}
    new_item = gift_public(best)
    new_item["id"] = uuid.uuid4().hex[:10]
    inv.append(new_item)
    u["inventory"] = inv
    u["games"] = int(u.get("games") or 0) + 1
    if int(new_item.get("value") or 0) >= total:
        u["wins"] = int(u.get("wins") or 0) + 1
    await save_user(u)
    await add_history(u["tg_id"], "craft", "win" if int(new_item.get("value") or 0) >= int(total * 0.88) else "lose",
                      f"craft {len(taken)} → {new_item.get('name')}", int(new_item.get("value") or 0))
    try:
        await push_live(new_item, u.get("username") or "Player")
    except Exception:
        pass
    return {
        "success": True,
        "ok": True,
        "gift": new_item,
        "inventory": u["inventory"],
        "balance": u["balance"],
        "total_in": total,
        "result_value": int(new_item.get("value") or 0),
    }

@app.post("/api/case/open")
async def open_case(body: CaseOpenRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    cid = (body.case_id or "").strip()
    # aliases if frontend sends old/wrong ids
    ALIAS = {
        "promo": "promo_case", "promocase": "promo_case", "promo-case": "promo_case", "promo_case": "promo_case",
        "pepe": "nft_pepe", "pepe_case": "nft_pepe", "nft_pepe": "nft_pepe",
        "onyx": "only_onyx", "onyx_black": "only_onyx", "black": "only_onyx", "only_onyx": "only_onyx",
        "snoop": "brand_snoop", "snoop_dog": "brand_snoop", "snoopdog": "brand_snoop", "brand_snoop": "brand_snoop",
        "minecraft": "minecraft_case", "minecraft_case": "minecraft_case",
        "bomzh": "bednyy_shkolnik", "бомж": "bednyy_shkolnik", "bednyy_shkolnik": "bednyy_shkolnik",
        "bogach": "bogach", "богач": "bogach", "rich": "bogach",
        "elite": "elite_case", "elite_case": "elite_case",
        "durov": "only_durov", "only_durov": "only_durov", "durov_case": "only_durov",
        "free": "free_daily", "free_case": "free_daily", "free_daily": "free_daily",
        "candy": "nft_candy", "nft_candy": "nft_candy",
        "halloween": "halloween_case", "halloween_case": "halloween_case",
        "angel": "angel_case", "angel_case": "angel_case",
        "jeremy": "jeremy_case", "jeremy_scott": "jeremy_case", "jeremy_case": "jeremy_case",
        "magic": "nft_magic", "nft_magic": "nft_magic",
        "fruits": "fruits_case", "fruits_case": "fruits_case", "фрукты": "fruits_case",
        "reliz": "reliz", "release": "reliz",
        "september": "september_case", "september_case": "september_case", "1_sentyabrya": "september_case",
        "1 сентября": "september_case",
    }
    key = cid.lower().replace(" ", "_").replace("-", "_")
    cid = ALIAS.get(cid) or ALIAS.get(key) or cid
    c = CASES.get(cid)
    if not c:
        # fallback: найти по имени кейса
        low = cid.lower()
        for k, v in CASES.items():
            if k.lower() == low or (v.get("name") or "").lower() == low:
                cid, c = k, v
                break
    if not c:
        raise HTTPException(404, f"case not found: {body.case_id}")
    body.case_id = cid  # normalize
    if c.get("coming_soon"):
        raise HTTPException(400, "Кейс скоро будет доступен")
    # Promo case: нужен реальный промо от админа · 1 раз на аккаунт
    if c.get("require_promo"):
        promo_code = (getattr(body, "promo_code", None) or "").strip().upper()
        if not promo_code:
            raise HTTPException(400, "Введи промокод")
        async with get_db() as db:
            # 1 открытие промо-кейса на аккаунт (любой код)
            once = await (await db.execute(
                "SELECT 1 FROM share_claims WHERE tg_id=? AND case_id=?",
                (u["tg_id"], "promo_case_once"),
            )).fetchone()
            if once:
                raise HTTPException(400, "Промо-кейс уже открыт на этом аккаунте")
            row = await (await db.execute(
                "SELECT code, stars, max_uses, uses FROM promos WHERE UPPER(TRIM(code))=?",
                (promo_code,),
            )).fetchone()
            if not row:
                row = await (await db.execute(
                    "SELECT code, stars, max_uses, uses FROM promos WHERE code=?", (promo_code,)
                )).fetchone()
            if not row:
                raise HTTPException(400, "Промокод не найден. Создай код в админке.")
            max_u = int(row[2] or 0)
            uses = int(row[3] or 0)
            if max_u > 0 and uses >= max_u:
                raise HTTPException(400, "Промокод исчерпан")
            used_promo = await (await db.execute(
                "SELECT 1 FROM promo_uses WHERE UPPER(code)=? AND tg_id=?",
                (promo_code, u["tg_id"]),
            )).fetchone()
            if used_promo:
                raise HTTPException(400, "Этот промокод уже использован")
            try:
                await db.execute(
                    "INSERT INTO share_claims(tg_id, case_id, created_at) VALUES(?,?,?)",
                    (u["tg_id"], "promo_case_once", now_ts()),
                )
            except Exception:
                raise HTTPException(400, "Промо-кейс уже открыт на этом аккаунте")
            await db.execute("INSERT INTO promo_uses(code,tg_id) VALUES(?,?)", (row[0], u["tg_id"]))
            await db.execute("UPDATE promos SET uses=uses+1 WHERE code=?", (row[0],))
            await db.commit()

    price = int(c.get("price") or 0)
    # глобальный лимит открытий (сезонный Reliz и т.п.)
    max_opens = int(c.get("max_opens_global") or 0)
    if max_opens > 0:
        async with get_db() as db:
            row = await (await db.execute("SELECT opens FROM case_opens WHERE case_id=?", (body.case_id,))).fetchone()
            opened = int(row[0] or 0) if row else 0
            if opened >= max_opens:
                raise HTTPException(400, f"Лимит открытий кейса исчерпан ({max_opens})")
    # дневной лимит all-in
    if c.get("allin") or c.get("category") == "allin":
        day = now_ts() // 86400
        async with get_db() as db:
            try:
                row = await (await db.execute(
                    "SELECT allin_day, allin_count FROM users WHERE tg_id=?", (u["tg_id"],)
                )).fetchone()
            except Exception:
                row = None
            ad = int(row[0] or 0) if row else 0
            ac = int(row[1] or 0) if row else 0
            if ad != day:
                ac = 0
            if ac >= 15:
                raise HTTPException(400, "Дневной лимит all-in (15) — завтра снова")
            try:
                await db.execute(
                    "UPDATE users SET allin_day=?, allin_count=? WHERE tg_id=?",
                    (day, ac + 1, u["tg_id"]),
                )
                await db.commit()
            except Exception:
                pass
    # free daily cooldown (не promo)
    if body.case_id == "free_daily" or (price == 0 and not c.get("require_deposit") and not c.get("require_share") and not c.get("require_promo") and c.get("category") != "promo"):
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
    # сезонный счётчик
    max_opens = int(c.get("max_opens_global") or 0)
    if max_opens > 0:
        try:
            async with get_db() as db:
                row = await (await db.execute("SELECT opens FROM case_opens WHERE case_id=?", (body.case_id,))).fetchone()
                if row:
                    await db.execute("UPDATE case_opens SET opens=opens+1 WHERE case_id=?", (body.case_id,))
                else:
                    await db.execute("INSERT INTO case_opens(case_id,opens) VALUES(?,1)", (body.case_id,))
                await db.commit()
                row2 = await (await db.execute("SELECT opens FROM case_opens WHERE case_id=?", (body.case_id,))).fetchone()
                result["opens_left"] = max(0, max_opens - int((row2[0] if row2 else 0) or 0))
        except Exception as e:
            print("[case_opens]", e)
    payout = 0
    if drop.get("kind") == "gift":
        payout = int((drop.get("gift") or {}).get("value") or 0)
    else:
        payout = int(drop.get("stars") or 0)
    try:
        await ledger("case_in", price, u["tg_id"], body.case_id)
        await ledger("case_out", payout, u["tg_id"], body.case_id)
        await bump_weekly(u["tg_id"], u.get("username") or "", max(1, payout // 50))
    except Exception:
        pass
    if payout >= 3000 and price > 0 and payout >= price * 3:
        await set_withdraw_hold(u["tg_id"], 6 * 3600, f"big case win {payout}")
        try:
            await tg_notify(u["tg_id"], f"🎉 Крупный выигрыш <b>{payout}⭐</b>!\nВывод на холде 6ч (анти-абуз).")
        except Exception:
            pass
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
    """Больше НЕ кредитует ⭐ бесплатно (дыра). Сначала пополни баланс TON→⭐, потом открой."""
    u = await current_user(authorization, x_telegram_init_data)
    c = CASES.get(body.case_id)
    if not c:
        raise HTTPException(404, "case not found")
    price = int(c.get("price") or 0)
    if price <= 0:
        return await open_case(CaseOpenRequest(case_id=body.case_id), authorization, x_telegram_init_data)
    if int(u.get("balance") or 0) < price:
        raise HTTPException(400, "Недостаточно ⭐. Сначала пополни баланс (TON → ⭐), потом открой кейс.")
    # открытие спишет цену внутри open_case
    return await open_case(CaseOpenRequest(case_id=body.case_id), authorization, x_telegram_init_data)

# ----- mines -----
MINES_GAMES: Dict[str, dict] = {}

@app.post("/api/mines/start")
async def mines_start(body: MinesStartRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    mines = int(body.mines)
    if mines < 5 or mines > 20:
        raise HTTPException(400, "Мины 5–20")
    nft_item = None
    if int(getattr(body, "item_index", -1) or -1) >= 0:
        bet, nft_item = consume_nft_bet(u, int(body.item_index))
    else:
        bet = int(body.bet)
        if bet < MIN_BET:
            raise HTTPException(400, f"Мин. ставка {MIN_BET}⭐")
        if u["balance"] < bet:
            raise HTTPException(400, "Недостаточно ⭐")
        u["balance"] -= bet
    u["games"] += 1
    bombs = random.sample(range(25), mines)
    gid = uuid.uuid4().hex
    MINES_GAMES[gid] = {"tg_id": u["tg_id"], "bet": bet, "mines": mines, "bombs": bombs, "opened": [], "cashed": False, "nft": bool(nft_item)}
    await save_user(u)
    await bump_quest(u["tg_id"], "mines_2")
    return {"id": gid, "game_id": gid, "balance": u["balance"], "mult": 1.0, "multiplier": 1.0, "bet": bet, "inventory": u["inventory"]}

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
    win = int(g["bet"] * m)
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
    for L in PVP.values():
        if not L.get("started") and any(p["id"] == u["tg_id"] for p in L["players"]):
            raise HTTPException(400, "Уже в лобби")
    nft_item = None
    if int(getattr(body, "item_index", -1) or -1) >= 0:
        bet, nft_item = consume_nft_bet(u, int(body.item_index))
    else:
        bet = int(body.bet)
        if bet < MIN_BET:
            raise HTTPException(400, f"Мин. {MIN_BET}⭐")
        if u["balance"] < bet:
            raise HTTPException(400, "Недостаточно ⭐")
        u["balance"] -= bet
    lid = uuid.uuid4().hex[:8]
    PVP[lid] = {
        "players": [{
            "id": u["tg_id"], "name": u["username"], "avatar": (u["username"] or "P")[0].upper(),
            "bet": bet, "color": PVP_COLORS[0], "nft": bool(nft_item),
        }],
        "started": False,
    }
    await save_user(u)
    return {"lobby_id": lid, "id": lid, "balance": u["balance"], "bet": bet, "inventory": u["inventory"]}

@app.post("/api/pvp/join")
async def pvp_join(body: PvpJoinRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    L = PVP.get(body.lobby_id)
    if not L or L.get("started"):
        raise HTTPException(400, "Лобби не найдено")
    if any(p["id"] == u["tg_id"] for p in L["players"]):
        return {"ok": True, "balance": u["balance"]}
    nft_item = None
    if int(getattr(body, "item_index", -1) or -1) >= 0:
        bet, nft_item = consume_nft_bet(u, int(body.item_index))
    else:
        bet = int(body.bet or L["players"][0]["bet"])
        if bet < MIN_BET:
            raise HTTPException(400, f"Мин. {MIN_BET}⭐")
        if u["balance"] < bet:
            raise HTTPException(400, "Недостаточно ⭐")
        u["balance"] -= bet
    L["players"].append({
        "id": u["tg_id"], "name": u["username"], "avatar": (u["username"] or "P")[0].upper(),
        "bet": bet, "color": PVP_COLORS[len(L["players"]) % len(PVP_COLORS)], "nft": bool(nft_item),
    })
    await save_user(u)
    return {"ok": True, "balance": u["balance"], "bet": bet, "inventory": u["inventory"]}

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
    payout = int(total * (1 - max(HOUSE_EDGE, 0.10)))  # дом ~10% банка PvP
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

# ----- BATTLE (case vs case) — Neon/Postgres -----
def _drop_val(d: dict) -> int:
    if d.get("kind") == "stars":
        return int(d.get("stars") or 0)
    g = d.get("gift") or {}
    return int(g.get("value") or 0)

def _drop_view(d: dict) -> dict:
    if d.get("kind") == "stars":
        s = int(d.get("stars") or 0)
        return {"name": f"⭐ {s}", "value": s, "emoji": "⭐", "img": ""}
    g = d.get("gift") or {}
    return {
        "name": g.get("name") or "?",
        "value": int(g.get("value") or 0),
        "emoji": g.get("emoji") or "🎁",
        "img": g.get("img") or gift_img_url(g.get("name") or ""),
    }

async def _give_drop_to_user(tg_id: int, d: dict):
    async with get_db() as db:
        row = await (await db.execute("SELECT balance, inventory FROM users WHERE tg_id=?", (tg_id,))).fetchone()
        if not row:
            return
        inv = json.loads(row[1] or "[]") if isinstance(row[1], str) else (row[1] or [])
        bal = int(row[0] or 0)
        if d.get("kind") == "stars":
            bal += int(d.get("stars") or 0)
        else:
            g = dict(d.get("gift") or {})
            if g:
                inv.append(g)
                try:
                    await push_live(g, str(tg_id))
                except Exception:
                    pass
        await db.execute(
            "UPDATE users SET balance=?, inventory=? WHERE tg_id=?",
            (bal, json.dumps(inv, ensure_ascii=False), tg_id),
        )
        await db.commit()

@app.get("/api/battle/list")
async def battle_list(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await current_user(authorization, x_telegram_init_data)
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT id, case_id, host_id, host_name, price FROM battles WHERE status='open' ORDER BY created_at DESC LIMIT 50"
        )).fetchall()
    rooms = []
    for r in (rows or []):
        c = CASES.get(r[1]) or {}
        rooms.append({
            "id": r[0],
            "case_id": r[1],
            "case_name": c.get("name") or r[1],
            "price": int(r[4] or c.get("price") or 0),
            "host_name": r[3] or "Игрок",
            "host_id": int(r[2] or 0),
        })
    return {"rooms": rooms}

@app.post("/api/battle/create")
async def battle_create(body: BattleCreateRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    c = CASES.get(body.case_id)
    if not c:
        raise HTTPException(400, "Кейс не найден")
    price = int(c.get("price") or 0)
    if price <= 0:
        raise HTTPException(400, "Только платные кейсы")
    if u["balance"] < price:
        raise HTTPException(400, "Недостаточно ⭐")
    async with get_db() as db:
        exist = await (await db.execute(
            "SELECT id FROM battles WHERE host_id=? AND status='open'", (u["tg_id"],)
        )).fetchone()
        if exist:
            raise HTTPException(400, "Уже есть открытая комната")
    u["balance"] -= price
    await save_user(u)
    rid = uuid.uuid4().hex[:8]
    async with get_db() as db:
        await db.execute(
            "INSERT INTO battles(id,case_id,host_id,host_name,price,status,created_at) VALUES(?,?,?,?,?,?,?)",
            (rid, body.case_id, u["tg_id"], u.get("username") or str(u["tg_id"]), price, "open", now_ts()),
        )
        await db.commit()
    return {"room": {"id": rid, "case_id": body.case_id, "price": price}, "balance": u["balance"]}

@app.post("/api/battle/cancel")
async def battle_cancel(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT id, price FROM battles WHERE host_id=? AND status='open'", (u["tg_id"],)
        )).fetchone()
        if not row:
            raise HTTPException(400, "Нет комнаты")
        refund = int(row[1] or 0)
        await db.execute("UPDATE battles SET status='cancelled' WHERE id=?", (row[0],))
        await db.commit()
    u["balance"] += refund
    await save_user(u)
    return {"ok": True, "balance": u["balance"]}

@app.post("/api/battle/join")
async def battle_join(body: BattleJoinRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT id, case_id, host_id, host_name, price, status FROM battles WHERE id=?",
            (body.room_id,),
        )).fetchone()
    if not row or row[5] != "open":
        raise HTTPException(400, "Комната недоступна")
    host_id = int(row[2])
    if host_id == u["tg_id"]:
        raise HTTPException(400, "Нельзя войти в свою")
    c = CASES.get(row[1])
    if not c:
        raise HTTPException(400, "Кейс не найден")
    price = int(row[4] or c.get("price") or 0)
    if u["balance"] < price:
        raise HTTPException(400, "Недостаточно ⭐")
    u["balance"] -= price
    await save_user(u)
    drop_h = roll_case_drop(row[1], c)
    drop_j = roll_case_drop(row[1], c)
    vh, vj = _drop_val(drop_h), _drop_val(drop_j)
    host_wins = vh >= vj
    winner_id = host_id if host_wins else u["tg_id"]
    await _give_drop_to_user(winner_id, drop_h)
    await _give_drop_to_user(winner_id, drop_j)
    async with get_db() as db:
        await db.execute(
            "UPDATE battles SET status='done', winner_id=?, drop_host=?, drop_join=? WHERE id=?",
            (winner_id, json.dumps(_drop_view(drop_h), ensure_ascii=False), json.dumps(_drop_view(drop_j), ensure_ascii=False), body.room_id),
        )
        await db.commit()
    await add_history(u["tg_id"], "battle", "win" if winner_id == u["tg_id"] else "lose",
                      f"{c.get('name')} vs host", _drop_val(drop_j))
    await add_history(host_id, "battle", "win" if winner_id == host_id else "lose",
                      f"{c.get('name')} vs join", _drop_val(drop_h))
    try:
        await tg_notify(host_id, f"⚔️ Батл: соперник найден!\nКейс {c.get('name')}\n{'🏆 Ты победил' if winner_id==host_id else '😔 Поражение'}")
        await tg_notify(u["tg_id"], f"⚔️ Батл завершён!\n{'🏆 Победа — оба дропа твои' if winner_id==u['tg_id'] else '😔 Поражение'}")
    except Exception:
        pass
    u2 = await current_user(authorization, x_telegram_init_data)
    return {
        "winner_id": winner_id,
        "you_win": winner_id == u["tg_id"],
        "your_drop": _drop_view(drop_j),
        "opp_drop": _drop_view(drop_h),
        "balance": u2["balance"],
    }

# ----- TRADE PLAZA — Neon -----
def _trade_view_db(T: dict, me: int) -> dict:
    oa = T["offer_a"] if isinstance(T["offer_a"], dict) else json.loads(T["offer_a"] or "{}")
    ob = T["offer_b"] if isinstance(T["offer_b"], dict) else json.loads(T["offer_b"] or "{}")
    mine = oa if int(T["from_id"]) == me else ob
    their = ob if int(T["from_id"]) == me else oa
    st = T.get("status") or "open"
    if T.get("accepted_a") and not T.get("accepted_b"):
        st = "partial"
    elif T.get("accepted_b") and not T.get("accepted_a"):
        st = "partial"
    return {"id": T["id"], "my": mine, "their": their, "status": st}

async def _load_trade(trade_id: str) -> Optional[dict]:
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT id, from_id, from_name, to_id, offer_a, offer_b, status, accepted_a, accepted_b FROM trades WHERE id=?",
            (trade_id,),
        )).fetchone()
    if not row:
        return None
    return {
        "id": row[0], "from_id": int(row[1]), "from_name": row[2], "to_id": int(row[3]),
        "offer_a": json.loads(row[4] or "{}"), "offer_b": json.loads(row[5] or "{}"),
        "status": row[6], "accepted_a": bool(row[7]), "accepted_b": bool(row[8]),
    }

@app.post("/api/trade/join")
async def trade_join(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    args = (u["tg_id"], u.get("username") or str(u["tg_id"]), u["balance"], now_ts())
    async with get_db() as db:
        if USE_POSTGRES:
            await db.execute(
                "INSERT INTO trade_plaza(tg_id,username,balance,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(tg_id) DO UPDATE SET username=EXCLUDED.username, balance=EXCLUDED.balance, updated_at=EXCLUDED.updated_at",
                args,
            )
        else:
            await db.execute(
                "INSERT OR REPLACE INTO trade_plaza(tg_id,username,balance,updated_at) VALUES(?,?,?,?)",
                args,
            )
        await db.commit()
    return {"ok": True}

@app.get("/api/trade/plaza")
async def trade_plaza(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    cutoff = now_ts() - 600
    async with get_db() as db:
        await db.execute("DELETE FROM trade_plaza WHERE updated_at < ?", (cutoff,))
        await db.commit()
        rows = await (await db.execute(
            "SELECT tg_id, username, balance FROM trade_plaza ORDER BY updated_at DESC LIMIT 40"
        )).fetchall()
        treqs = await (await db.execute(
            "SELECT id, from_name FROM trades WHERE to_id=? AND status='open'", (u["tg_id"],)
        )).fetchall()
        tactive = await (await db.execute(
            "SELECT id FROM trades WHERE (from_id=? OR to_id=?) AND status IN ('open','partial') ORDER BY created_at DESC LIMIT 1",
            (u["tg_id"], u["tg_id"]),
        )).fetchone()
    users = [{"tg_id": int(r[0]), "username": r[1], "balance": int(r[2] or 0)} for r in (rows or [])]
    requests = [{"id": r[0], "from_name": r[1] or "Игрок"} for r in (treqs or [])]
    active = None
    if tactive:
        T = await _load_trade(tactive[0])
        if T:
            active = _trade_view_db(T, u["tg_id"])
    return {"users": users, "requests": requests, "active": active}

@app.post("/api/trade/offer")
async def trade_offer(body: TradeOfferRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    if body.to_id == u["tg_id"]:
        raise HTTPException(400, "Нельзя себе")
    async with get_db() as db:
        in_plaza = await (await db.execute("SELECT 1 FROM trade_plaza WHERE tg_id=?", (body.to_id,))).fetchone()
        if not in_plaza:
            raise HTTPException(400, "Игрок не в плазе")
        tid = uuid.uuid4().hex[:10]
        empty = json.dumps({"stars": 0, "items": [], "item_indices": []})
        await db.execute(
            "INSERT INTO trades(id,from_id,from_name,to_id,offer_a,offer_b,status,accepted_a,accepted_b,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (tid, u["tg_id"], u.get("username") or str(u["tg_id"]), body.to_id, empty, empty, "open", 0, 0, now_ts()),
        )
        await db.commit()
    T = await _load_trade(tid)
    return {"trade_id": tid, "id": tid, "trade": _trade_view_db(T, u["tg_id"])}

@app.get("/api/trade/{trade_id}")
async def trade_get(trade_id: str, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    T = await _load_trade(trade_id)
    if not T:
        raise HTTPException(404, "Трейд не найден")
    return {"trade": _trade_view_db(T, u["tg_id"])}

@app.post("/api/trade/add")
async def trade_add(body: TradeAddRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    T = await _load_trade(body.trade_id)
    if not T or T["status"] in ("done", "declined"):
        raise HTTPException(400, "Трейд закрыт")
    if u["tg_id"] not in (T["from_id"], T["to_id"]):
        raise HTTPException(403, "Не твой трейд")
    offer = T["offer_a"] if u["tg_id"] == T["from_id"] else T["offer_b"]
    offer = dict(offer or {})
    offer.setdefault("items", [])
    offer.setdefault("item_indices", [])
    offer.setdefault("stars", 0)
    if body.stars and body.stars > 0:
        if u["balance"] < int(body.stars):
            raise HTTPException(400, "Недостаточно ⭐")
        offer["stars"] = int(body.stars)
    if body.item_index is not None:
        inv = list(u.get("inventory") or [])
        idx = int(body.item_index)
        if idx < 0 or idx >= len(inv):
            raise HTTPException(400, "Нет предмета")
        if idx in (offer.get("item_indices") or []):
            raise HTTPException(400, "Уже добавлен")
        offer["items"].append(inv[idx])
        offer["item_indices"].append(idx)
    col = "offer_a" if u["tg_id"] == T["from_id"] else "offer_b"
    async with get_db() as db:
        await db.execute(
            f"UPDATE trades SET {col}=?, accepted_a=0, accepted_b=0, status='open' WHERE id=?",
            (json.dumps(offer, ensure_ascii=False), body.trade_id),
        )
        await db.commit()
    T2 = await _load_trade(body.trade_id)
    return {"trade": _trade_view_db(T2, u["tg_id"])}

@app.post("/api/trade/accept")
async def trade_accept(body: TradeIdRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    T = await _load_trade(body.trade_id)
    if not T or T["status"] in ("done", "declined"):
        raise HTTPException(400, "Трейд закрыт")
    if u["tg_id"] == T["from_id"]:
        T["accepted_a"] = True
    elif u["tg_id"] == T["to_id"]:
        T["accepted_b"] = True
    else:
        raise HTTPException(403, "Не твой трейд")
    async with get_db() as db:
        await db.execute(
            "UPDATE trades SET accepted_a=?, accepted_b=?, status=? WHERE id=?",
            (1 if T["accepted_a"] else 0, 1 if T["accepted_b"] else 0,
             "partial" if not (T["accepted_a"] and T["accepted_b"]) else "open", body.trade_id),
        )
        await db.commit()
    if T["accepted_a"] and T["accepted_b"]:
        async def load_u(tg_id):
            async with get_db() as db:
                row = await (await db.execute("SELECT tg_id, username, balance, inventory FROM users WHERE tg_id=?", (tg_id,))).fetchone()
            if not row:
                return None
            return {"tg_id": row[0], "username": row[1], "balance": int(row[2] or 0), "inventory": json.loads(row[3] or "[]")}
        async def save_u(usr):
            async with get_db() as db:
                await db.execute("UPDATE users SET balance=?, inventory=? WHERE tg_id=?",
                                 (usr["balance"], json.dumps(usr["inventory"], ensure_ascii=False), usr["tg_id"]))
                await db.commit()
        a = await load_u(T["from_id"])
        b = await load_u(T["to_id"])
        if not a or not b:
            raise HTTPException(400, "Игрок не найден")
        oa, ob = T["offer_a"], T["offer_b"]
        if a["balance"] < int(oa.get("stars") or 0) or b["balance"] < int(ob.get("stars") or 0):
            raise HTTPException(400, "Не хватает ⭐ у одной из сторон")
        a["balance"] = a["balance"] - int(oa.get("stars") or 0) + int(ob.get("stars") or 0)
        b["balance"] = b["balance"] - int(ob.get("stars") or 0) + int(oa.get("stars") or 0)
        def take_items(usr, indices):
            taken = []
            for i in sorted(indices or [], reverse=True):
                if 0 <= i < len(usr["inventory"]):
                    taken.append(usr["inventory"].pop(i))
            return taken
        items_a = take_items(a, oa.get("item_indices"))
        items_b = take_items(b, ob.get("item_indices"))
        a["inventory"].extend(items_b)
        b["inventory"].extend(items_a)
        await save_u(a)
        await save_u(b)
        async with get_db() as db:
            await db.execute("UPDATE trades SET status='done' WHERE id=?", (body.trade_id,))
            await db.commit()
        await add_history(T["from_id"], "trade", "done", f"trade {body.trade_id}", 0)
        await add_history(T["to_id"], "trade", "done", f"trade {body.trade_id}", 0)
        try:
            await tg_notify(T["from_id"], "✅ Трейд принят обеими сторонами — обмен выполнен.")
            await tg_notify(T["to_id"], "✅ Трейд принят обеими сторонами — обмен выполнен.")
        except Exception:
            pass
        return {"done": True, "trade": _trade_view_db(await _load_trade(body.trade_id) or T, u["tg_id"])}
    return {"done": False, "trade": _trade_view_db(await _load_trade(body.trade_id) or T, u["tg_id"])}

@app.post("/api/trade/decline")
async def trade_decline(body: TradeIdRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    T = await _load_trade(body.trade_id)
    if T and u["tg_id"] in (T.get("from_id"), T.get("to_id")):
        async with get_db() as db:
            await db.execute("UPDATE trades SET status='declined' WHERE id=?", (body.trade_id,))
            await db.commit()
    return {"ok": True}

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
    try:
        await pay_referral(u["tg_id"], amount)
        await add_history(u["tg_id"], "deposit", "win", f"stars +{amount}", amount)
    except Exception:
        pass
    return {"balance": u["balance"]}

@app.post("/api/withdraw")
async def withdraw(body: WithdrawRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    amount = int(body.amount)
    if amount < 50:
        raise HTTPException(400, "Минимум 50⭐")
    if amount > 25000:
        raise HTTPException(400, "Максимум 25000⭐ за заявку")
    if u["balance"] < amount:
        raise HTTPException(400, "Недостаточно ⭐")
    # холд после крупного выигрыша
    hold_until = 0
    try:
        async with get_db() as db:
            row = await (await db.execute(
                "SELECT withdraw_hold_until FROM users WHERE tg_id=?", (u["tg_id"],)
            )).fetchone()
            hold_until = int(row[0] or 0) if row else 0
    except Exception:
        hold_until = 0
    if hold_until > now_ts():
        left = hold_until - now_ts()
        hrs = max(1, left // 3600)
        raise HTTPException(400, f"Холд вывода после крупного выигрыша · ещё ~{hrs}ч")
    # дневной лимит суммы заявок
    day0 = now_ts() - (now_ts() % 86400)
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE tg_id=? AND created_at>=? AND status!='rejected' AND status!='cancelled'",
            (u["tg_id"], day0),
        )).fetchone()
        day_sum = int(row[0] or 0) if row else 0
    if day_sum + amount > 15000:
        raise HTTPException(400, f"Дневной лимит вывода 15000⭐ (уже {day_sum})")
    dest = body.dest or body.username or body.wallet or ""
    u["balance"] -= amount
    await save_user(u)
    async with get_db() as db:
        await db.execute(
            "INSERT INTO withdrawals(tg_id,amount,method,dest,note,status,created_at) VALUES(?,?,?,?,?,?,?)",
            (u["tg_id"], amount, body.method or "stars", dest, body.note or "", "pending", now_ts()),
        )
        await db.commit()
    try:
        await ledger("withdraw_req", amount, u["tg_id"], body.method or "stars")
        if ADMIN_TG_ID:
            await tg_notify(ADMIN_TG_ID, f"📤 Заявка на вывод\nuser <code>{u['tg_id']}</code> @{u.get('username') or ''}\n<b>{amount}⭐</b> · {body.method or 'stars'} → {dest}")
    except Exception:
        pass
    return {"ok": True, "balance": u["balance"], "message": "Заявка отправлена админу"}

@app.post("/api/promo/activate")
async def promo_activate(
    request: Request,
    code: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
    x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data"),
):
    u = await current_user(authorization, x_telegram_init_data)
    if not code:
        try:
            body = await request.json()
            code = (body or {}).get("code")
        except Exception:
            code = None
    code = (code or "").strip().upper().replace(" ", "").replace("\u00a0", "")
    if not code:
        raise HTTPException(400, "Введите промокод")
    async with get_db() as db:
        # case-insensitive lookup (Postgres + SQLite)
        row = await (await db.execute(
            "SELECT code, stars, max_uses, uses FROM promos WHERE UPPER(TRIM(code))=?",
            (code,),
        )).fetchone()
        if not row:
            # fallback exact
            row = await (await db.execute("SELECT code, stars, max_uses, uses FROM promos WHERE code=?", (code,))).fetchone()
        if not row:
            raise HTTPException(400, "Неверный промокод")
        real_code = row[0]
        used = await (await db.execute(
            "SELECT 1 FROM promo_uses WHERE UPPER(code)=? AND tg_id=?",
            (code, u["tg_id"]),
        )).fetchone()
        if used:
            raise HTTPException(400, "Уже использован")
        max_u = int(row[2] or 0)
        uses = int(row[3] or 0)
        if max_u > 0 and uses >= max_u:
            raise HTTPException(400, "Лимит промокода")
        stars = int(row[1] or 0)
        await db.execute("INSERT INTO promo_uses(code,tg_id) VALUES(?,?)", (real_code, u["tg_id"]))
        await db.execute("UPDATE promos SET uses=uses+1 WHERE code=?", (real_code,))
        await db.commit()
    u["balance"] += stars
    await save_user(u)
    return {"ok": True, "stars": stars, "balance": u["balance"], "message": f"+{stars} ⭐"}

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
        total_row = await (await db.execute("SELECT COUNT(*) FROM users")).fetchone()
        total_users = int(total_row[0] or 0) if total_row else 0
        by_bal = await (await db.execute(
            "SELECT tg_id, username, balance, wins, cases_opened FROM users ORDER BY balance DESC LIMIT 50"
        )).fetchall()
        by_wins = await (await db.execute(
            "SELECT tg_id, username, balance, wins, cases_opened FROM users ORDER BY wins DESC LIMIT 50"
        )).fetchall()
        by_cases = await (await db.execute(
            "SELECT tg_id, username, balance, wins, cases_opened FROM users ORDER BY cases_opened DESC LIMIT 50"
        )).fetchall()
    def pack(rows):
        out = []
        for r in (rows or []):
            out.append({
                "tg_id": int(r[0] or 0),
                "name": (r[1] or "Player"),
                "balance": int(r[2] or 0),
                "wins": int(r[3] or 0),
                "cases": int(r[4] or 0),
            })
        return out
    return {
        "total_users": total_users,
        "by_balance": pack(by_bal),
        "by_wins": pack(by_wins),
        "by_cases": pack(by_cases),
    }

@app.get("/api/leaderboard/weekly")
async def leaderboard_weekly():
    """Топ недели по weekly_scores + приз 1 месту."""
    import time as _t
    week_key = time.strftime("%Y-W%W", _t.gmtime(now_ts()))
    items = []
    try:
        async with get_db() as db:
            rows = await (await db.execute(
                "SELECT tg_id, username, score FROM weekly_scores WHERE week_key=? ORDER BY score DESC LIMIT 30",
                (week_key,),
            )).fetchall()
            for r in (rows or []):
                items.append({
                    "tg_id": int(r[0] or 0),
                    "username": r[1] or "Player",
                    "score": int(r[2] or 0),
                })
            # если пусто — fallback по cases_opened за всё время (чтобы не «пусто»)
            if not items:
                rows = await (await db.execute(
                    "SELECT tg_id, username, COALESCE(cases_opened,0) FROM users ORDER BY cases_opened DESC LIMIT 20"
                )).fetchall()
                for r in (rows or []):
                    items.append({
                        "tg_id": int(r[0] or 0),
                        "username": r[1] or "Player",
                        "score": int(r[2] or 0),
                    })
    except Exception as e:
        print("[weekly lb]", e)
    prize = "🏆 1 место: 500⭐ в конце недели (ручная выдача)"
    return {"items": items, "week": week_key, "prize": prize}

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
    """Только реальные дропы из live_drops + лучший дроп за сегодня."""
    day_start = now_ts() - (now_ts() % 86400)
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT name, emoji, img, user_name, value, created_at FROM live_drops WHERE value > 0 ORDER BY id DESC LIMIT 24"
        )).fetchall()
        best = await (await db.execute(
            "SELECT name, emoji, img, user_name, value FROM live_drops WHERE created_at >= ? AND value > 0 ORDER BY value DESC LIMIT 1",
            (day_start,),
        )).fetchone()
    items = []
    for r in (rows or []):
        items.append({
            "name": r[0],
            "emoji": r[1] or "🎁",
            "img": r[2] or gift_img_url(r[0] or ""),
            "user": r[3] or "?",
            "value": int(r[4] or 0),
            "ts": r[5],
        })
    best_drop = None
    if best:
        best_drop = {
            "name": best[0],
            "emoji": best[1] or "🎁",
            "img": best[2] or gift_img_url(best[0] or ""),
            "user": best[3] or "?",
            "value": int(best[4] or 0),
        }
    return {"items": items, "best_today": best_drop}

@app.get("/api/referral/stats")
async def ref_stats(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    link = f"https://t.me/GiftUpgraderBot?start=ref_{u['tg_id']}"
    invited = 0
    earned = 0
    try:
        async with get_db() as db:
            row = await (await db.execute(
                "SELECT COUNT(*), COALESCE(SUM(earned),0) FROM referrals WHERE referrer_id=?",
                (u["tg_id"],),
            )).fetchone()
            if row:
                invited = int(row[0] or 0)
                earned = int(row[1] or 0)
    except Exception as e:
        print("[ref_stats]", e)
    return {
        "invited": invited,
        "earned": earned,
        "referrals_count": invited,
        "total_earned": earned,
        "link": link,
        "code": f"ref_{u['tg_id']}",
        "percent": 7,
    }

@app.post("/api/referral/activate")
async def ref_activate(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data"),
):
    u = await current_user(authorization, x_telegram_init_data)
    code = ""
    try:
        body = await request.json()
        code = str((body or {}).get("code") or (body or {}).get("ref") or "").strip()
    except Exception:
        code = ""
    if not code:
        # also accept start_param from init later
        pass
    if code.startswith("ref_"):
        code = code[4:]
    try:
        ref_id = int(code)
    except Exception:
        raise HTTPException(400, "Неверный реф-код")
    if ref_id == u["tg_id"]:
        raise HTTPException(400, "Нельзя пригласить себя")
    async with get_db() as db:
        exists = await (await db.execute("SELECT 1 FROM users WHERE tg_id=?", (ref_id,))).fetchone()
        if not exists:
            raise HTTPException(400, "Реферер не найден")
        already = await (await db.execute("SELECT 1 FROM referrals WHERE referred_id=?", (u["tg_id"],))).fetchone()
        if already:
            return {"ok": True, "already": True}
        await db.execute(
            "INSERT INTO referrals(referred_id,referrer_id,earned,created_at) VALUES(?,?,0,?)",
            (u["tg_id"], ref_id, now_ts()),
        )
        await db.commit()
    return {"ok": True, "referrer_id": ref_id}

@app.get("/api/streak")
async def streak_get(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    day = now_ts() // 86400
    # в 5 раз меньше: 1⭐ максимум на 7-й день
    rewards = {1: 0, 2: 0, 3: 1, 4: 1, 5: 1, 6: 1, 7: 1}
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT count, last_claim_day, total_earned FROM streaks WHERE tg_id=?", (u["tg_id"],)
        )).fetchone()
    count = int(row[0] or 0) if row else 0
    last = int(row[1] or 0) if row else 0
    total = int(row[2] or 0) if row else 0
    claimed_today = last == day
    can_claim = not claimed_today
    next_day = (count % 7) + 1 if not claimed_today else min(7, (count % 7) + 1)
    if last and last < day - 1:
        next_day = 1
    return {
        "count": count,
        "claimed_today": claimed_today,
        "can_claim": can_claim,
        "next_reward": rewards.get(next_day, 1),
        "next_day": next_day,
        "total_earned": total,
        "rewards": rewards,
    }

@app.post("/api/streak/claim")
async def streak_claim(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    day = now_ts() // 86400
    rewards = {1: 0, 2: 0, 3: 1, 4: 1, 5: 1, 6: 1, 7: 1}
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT count, last_claim_day, total_earned FROM streaks WHERE tg_id=?", (u["tg_id"],)
        )).fetchone()
        count = int(row[0] or 0) if row else 0
        last = int(row[1] or 0) if row else 0
        total = int(row[2] or 0) if row else 0
        if last == day:
            raise HTTPException(400, "Уже забрано сегодня")
        if last and last < day - 1:
            count = 0  # streak broken
        new_count = count + 1
        day_num = ((new_count - 1) % 7) + 1
        reward = int(rewards.get(day_num, 1))
        if row:
            await db.execute(
                "UPDATE streaks SET count=?, last_claim_day=?, total_earned=? WHERE tg_id=?",
                (new_count, day, total + reward, u["tg_id"]),
            )
        else:
            await db.execute(
                "INSERT INTO streaks(tg_id,count,last_claim_day,total_earned) VALUES(?,?,?,?)",
                (u["tg_id"], new_count, day, reward),
            )
        await db.commit()
    if reward > 0:
        u["balance"] = int(u["balance"]) + reward
        await save_user(u)
        await add_history(u["tg_id"], "streak", "win", f"day {day_num}", reward)
    return {"ok": True, "reward": reward, "count": new_count, "day": day_num, "balance": u["balance"]}


@app.post("/api/ton/wallet")
async def ton_wallet(body: TonWalletRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    u["ton_wallet"] = body.address
    await save_user(u)
    return {"ok": True}

@app.post("/api/ton/deposit")
async def ton_deposit(body: TonDepositRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    dep_id = uuid.uuid4().hex
    stars = int(round(float(body.amount_ton) * TON_STARS_PER_TON))
    async with get_db() as db:
        await db.execute(
            "INSERT INTO ton_deposits(id,tg_id,amount_ton,boc,address,credited,created_at) VALUES(?,?,?,?,?,?,?)",
            (dep_id, u["tg_id"], float(body.amount_ton), body.boc or "", body.address or "", 0, now_ts()),
        )
        await db.commit()
    # Автокредит ТОЛЬКО в явном test-режиме (не через ALLOW_DEV_AUTH — дыра)
    if TON_DEPOSIT_MODE in ("credit", "test", "dev"):
        u["balance"] += stars
        u["deposited"] += stars
        await save_user(u)
        async with get_db() as db:
            await db.execute("UPDATE ton_deposits SET credited=1 WHERE id=?", (dep_id,))
            await db.commit()
        try:
            await pay_referral(u["tg_id"], stars)
        except Exception:
            pass
        return {"ok": True, "deposit_id": dep_id, "credited": True, "balance": u["balance"], "stars": stars}
    return {"ok": True, "deposit_id": dep_id, "credited": False, "message": "Ожидает подтверждения оплаты"}

def _ton_to_nano(amount: float) -> int:
    return int(round(float(amount) * 1e9))

async def _fetch_treasury_in_txs(limit: int = 30) -> list:
    """Публичный TonAPI — входящие на TON_TREASURY. Без ключа, без своих нод."""
    if not TON_TREASURY:
        return []
    import urllib.request
    addr = TON_TREASURY.strip()
    url = f"https://tonapi.io/v2/blockchain/accounts/{addr}/transactions?limit={limit}"
    def _get():
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "GiftUpgrader/1.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read().decode())
    try:
        data = await asyncio.to_thread(_get)
    except Exception as e:
        print("[tonapi]", e)
        return []
    txs = data.get("transactions") or data.get("events") or []
    if isinstance(data, list):
        txs = data
    out = []
    for tx in txs:
        # tonapi v2 shape
        h = (tx.get("hash") or tx.get("transaction_id", {}) or {})
        if isinstance(h, dict):
            h = h.get("hash") or ""
        h = str(h or "")
        utime = int(tx.get("utime") or tx.get("now") or tx.get("timestamp") or 0)
        # in_msg value
        in_msg = tx.get("in_msg") or {}
        val = 0
        if isinstance(in_msg, dict):
            val = int(in_msg.get("value") or 0)
        if not val:
            # account value diff
            for a in (tx.get("actions") or []):
                if a.get("type") == "TonTransfer":
                    st = a.get("status") or ""
                    if st and st != "ok":
                        continue
                    val = int((a.get("TonTransfer") or a).get("amount") or 0)
                    if val:
                        break
        comment = ""
        try:
            body = (in_msg.get("decoded_body") or in_msg.get("msg_data") or {})
            if isinstance(body, dict):
                comment = str(body.get("text") or body.get("comment") or "")
            decoded = tx.get("in_msg", {}).get("decoded_op_name") or ""
            if not comment and in_msg.get("message"):
                comment = str(in_msg.get("message") or "")
        except Exception:
            pass
        if val > 0:
            out.append({"hash": h, "utime": utime, "value_nano": val, "comment": comment})
    return out

async def try_credit_ton_deposit(deposit_id: str, tg_id: int) -> dict:
    """Проверяет казну через TonAPI и зачисляет 1 раз, если нашлась подходящая tx."""
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT amount_ton, credited, created_at, tx_hash FROM ton_deposits WHERE id=? AND tg_id=?",
            (deposit_id, tg_id),
        )).fetchone()
    if not row:
        return {"credited": False, "error": "deposit not found"}
    amount_ton = float(row[0] or 0)
    credited = int(row[1] or 0)
    created_at = int(row[2] or 0)
    if credited:
        return {"credited": True, "already": True}
    if not TON_TREASURY:
        return {"credited": False, "error": "TON_TREASURY not set"}
    need_nano = _ton_to_nano(amount_ton)
    # допуск 1% (комиссии/округление)
    lo = int(need_nano * 0.99)
    hi = int(need_nano * 1.02) + 10
    txs = await _fetch_treasury_in_txs(40)
    # уже использованные хэши
    used = set()
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT tx_hash FROM ton_deposits WHERE credited=1 AND tx_hash IS NOT NULL AND tx_hash!=''"
        )).fetchall()
        for r in (rows or []):
            if r and r[0]:
                used.add(str(r[0]))
    matched = None
    for tx in txs:
        h = tx.get("hash") or ""
        if not h or h in used:
            continue
        ut = int(tx.get("utime") or 0)
        # tx не раньше чем за 2 мин до создания заявки и не старше 2ч
        if created_at and ut and ut + 120 < created_at:
            continue
        if created_at and ut and ut > created_at + 7200:
            continue
        val = int(tx.get("value_nano") or 0)
        if lo <= val <= hi:
            matched = tx
            break
        # comment содержит id депозита — тоже ок даже если сумма чуть гуляет
        cmt = (tx.get("comment") or "").lower()
        if deposit_id[:8].lower() in cmt and val >= lo:
            matched = tx
            break
    if not matched:
        return {"credited": False, "pending": True, "message": "Платёж ещё не найден в сети. Подожди 15–60с и нажми «Проверить»."}
    stars = max(1, int(round(amount_ton * TON_STARS_PER_TON)))
    async with get_db() as db:
        # повторная проверка
        row2 = await (await db.execute(
            "SELECT credited FROM ton_deposits WHERE id=?", (deposit_id,)
        )).fetchone()
        if row2 and int(row2[0] or 0):
            return {"credited": True, "already": True}
        # хэш не должен быть у другого депозита
        clash = await (await db.execute(
            "SELECT id FROM ton_deposits WHERE tx_hash=? AND credited=1", (matched["hash"],)
        )).fetchone()
        if clash:
            return {"credited": False, "error": "tx already used"}
        await db.execute(
            "UPDATE ton_deposits SET credited=1, tx_hash=? WHERE id=?",
            (matched["hash"], deposit_id),
        )
        bal = await (await db.execute("SELECT balance, deposited FROM users WHERE tg_id=?", (tg_id,))).fetchone()
        if bal:
            await db.execute(
                "UPDATE users SET balance=?, deposited=? WHERE tg_id=?",
                (int(bal[0] or 0) + stars, int(bal[1] or 0) + stars, tg_id),
            )
        await db.commit()
    try:
        await pay_referral(tg_id, stars)
        await add_history(tg_id, "deposit", "win", f"TON {amount_ton} → +{stars}⭐", stars)
    except Exception:
        pass
    async with get_db() as db:
        bal = await (await db.execute("SELECT balance FROM users WHERE tg_id=?", (tg_id,))).fetchone()
    return {
        "credited": True,
        "stars": stars,
        "tx_hash": matched["hash"],
        "balance": int(bal[0] or 0) if bal else 0,
    }

@app.post("/api/ton/check")
async def ton_check(body: TonCheckRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    result = await try_credit_ton_deposit(body.deposit_id, u["tg_id"])
    if result.get("credited"):
        # обновить u.balance
        async with get_db() as db:
            row = await (await db.execute("SELECT balance FROM users WHERE tg_id=?", (u["tg_id"],))).fetchone()
        bal = int(row[0] or 0) if row else u["balance"]
        return {"credited": True, "balance": bal, "stars": result.get("stars"), "tx_hash": result.get("tx_hash"), "already": result.get("already")}
    return {
        "credited": False,
        "balance": u["balance"],
        "pending": result.get("pending", True),
        "message": result.get("message") or result.get("error") or "Ожидание оплаты",
    }


@app.get("/api/leaderboard/weekly")
async def leaderboard_weekly():
    import time as _t
    week_key = time.strftime("%Y-W%W", _t.gmtime(now_ts()))
    async with get_db() as db:
        try:
            rows = await (await db.execute(
                "SELECT tg_id, username, score FROM weekly_scores WHERE week_key=? ORDER BY score DESC LIMIT 30",
                (week_key,),
            )).fetchall()
        except Exception:
            rows = []
    return {
        "week": week_key,
        "prize": "1 место — 500⭐ (выдаёт админ)",
        "items": [
            {"tg_id": int(r[0]), "username": r[1] or "Player", "score": int(r[2] or 0)}
            for r in (rows or [])
        ],
    }

# ----- admin -----

@app.get("/api/maintenance")
async def get_maintenance():
    return {"ok": True, "on": bool(MAINTENANCE_MODE.get("on")), "message": MAINTENANCE_MODE.get("message")}

@app.post("/api/admin/maintenance")
async def admin_maintenance(body: dict, authorization: Optional[str] = Header(None)):
    u = await current_user(authorization)
    require_admin(u)
    on = bool(body.get("on"))
    msg = (body.get("message") or "").strip() or "Технические работы. Скоро вернёмся."
    MAINTENANCE_MODE["on"] = on
    MAINTENANCE_MODE["message"] = msg
    return {"ok": True, "on": on, "message": msg}


@app.post("/api/admin/give")
async def admin_give(body: AdminGiveRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    me = await current_user(authorization, x_telegram_init_data)
    require_admin(me)
    uid = int(body.user_id)
    amt = int(body.amount)
    async with get_db() as db:
        row = await (await db.execute("SELECT balance FROM users WHERE tg_id=?", (uid,))).fetchone()
        if not row:
            # create user on the fly so admin can give to new players
            import time as _time
            await db.execute(
                "INSERT INTO users (tg_id, username, balance, inventory, games, wins, cases_opened, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (uid, f"user_{uid}", max(0, amt), "[]", 0, 0, 0, int(_time.time())),
            )
            new_bal = max(0, amt)
        else:
            new_bal = int(row[0] or 0) + amt
            await db.execute("UPDATE users SET balance=? WHERE tg_id=?", (new_bal, uid))
        await db.commit()
    return {"ok": True, "success": True, "balance": new_bal, "user_id": uid}

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
    bonus = min(MAX_CHANCE_BONUS, max(0.0, float(body.chance_bonus or 0)))
    async with get_db() as db:
        await db.execute("UPDATE users SET chance_bonus=? WHERE tg_id=?", (bonus, body.user_id))
        await db.commit()
    return {"ok": True, "chance_bonus": bonus}

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
    code = body.code.strip().upper().replace(" ", "")
    if not code:
        raise HTTPException(400, "Пустой код")
    async with get_db() as db:
        row = await (await db.execute("SELECT code FROM promos WHERE UPPER(TRIM(code))=?", (code,))).fetchone()
        if row:
            await db.execute(
                "UPDATE promos SET reward_type=?, stars=?, max_uses=? WHERE code=?",
                (body.reward_type, int(body.stars), int(body.max_uses), row[0]),
            )
        else:
            await db.execute(
                "INSERT INTO promos(code,reward_type,stars,max_uses,uses) VALUES(?,?,?,?,0)",
                (code, body.reward_type, int(body.stars), int(body.max_uses)),
            )
        await db.commit()
    return {"ok": True, "code": code, "stars": int(body.stars), "max_uses": int(body.max_uses)}

@app.get("/api/admin/withdrawals")
async def admin_withdrawals(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    me = await current_user(authorization, x_telegram_init_data)
    require_admin(me)
    async with get_db() as db:
        rows = await (await db.execute("SELECT id, tg_id, amount, method, dest, note, status, created_at FROM withdrawals ORDER BY id DESC LIMIT 50")).fetchall()
    return {"items": [
        {"id": r[0], "tg_id": r[1], "amount": r[2], "method": r[3], "dest": r[4], "note": r[5], "status": r[6], "ts": r[7]}
        for r in (rows or [])
    ]}

@app.post("/api/admin/withdraw/status")
async def admin_wd_status(body: AdminWithdrawStatusRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    me = await current_user(authorization, x_telegram_init_data)
    require_admin(me)
    async with get_db() as db:
        row = await (await db.execute("SELECT tg_id, amount, status FROM withdrawals WHERE id=?", (body.withdraw_id,))).fetchone()
        if not row:
            raise HTTPException(404, "wd")
        await db.execute("UPDATE withdrawals SET status=? WHERE id=?", (body.status, body.withdraw_id))
        if body.status in ("rejected", "cancel", "cancelled") and row[2] == "pending":
            bal = await (await db.execute("SELECT balance FROM users WHERE tg_id=?", (row[0],))).fetchone()
            if bal:
                await db.execute("UPDATE users SET balance=? WHERE tg_id=?", (int(bal[0] or 0) + int(row[1]), row[0]))
        await db.commit()
    try:
        uid, amt = int(row[0]), int(row[1])
        st = (body.status or "").lower()
        if st in ("approved", "done", "paid", "ok"):
            await tg_notify(uid, f"✅ Вывод <b>{amt}⭐</b> одобрен.")
            await ledger("withdraw_paid", amt, uid, "approved")
        elif st in ("rejected", "cancel", "cancelled"):
            await tg_notify(uid, f"❌ Вывод <b>{amt}⭐</b> отклонён, средства возвращены.")
    except Exception as e:
        print("[wd notify]", e)
    return {"ok": True}

@app.get("/api/admin/stats")
async def admin_stats(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    me = await current_user(authorization, x_telegram_init_data)
    require_admin(me)
    day0 = now_ts() - (now_ts() % 86400)
    async with get_db() as db:
        n = await (await db.execute("SELECT COUNT(*) FROM users")).fetchone()
        s = await (await db.execute("SELECT COALESCE(SUM(balance),0) FROM users")).fetchone()
        # ledger RTP за сегодня
        try:
            cin = await (await db.execute(
                "SELECT COALESCE(SUM(amount),0) FROM house_ledger WHERE kind='case_in' AND created_at>=?", (day0,)
            )).fetchone()
            cout = await (await db.execute(
                "SELECT COALESCE(SUM(amount),0) FROM house_ledger WHERE kind='case_out' AND created_at>=?", (day0,)
            )).fetchone()
            wpay = await (await db.execute(
                "SELECT COALESCE(SUM(amount),0) FROM house_ledger WHERE kind='withdraw_paid' AND created_at>=?", (day0,)
            )).fetchone()
            tin = int(cin[0] or 0); tout = int(cout[0] or 0); wpaid = int(wpay[0] or 0)
        except Exception:
            tin = tout = wpaid = 0
        # fallback from history if ledger empty
        if tin == 0 and tout == 0:
            try:
                h = await (await db.execute(
                    "SELECT COALESCE(SUM(CASE WHEN result IN ('gift','win') THEN amount ELSE 0 END),0), COUNT(*) "
                    "FROM history WHERE created_at>=? AND game='case'", (day0,)
                )).fetchone()
                tout = int(h[0] or 0) if h else 0
            except Exception:
                pass
        wd_pend = await (await db.execute(
            "SELECT COALESCE(SUM(amount),0) FROM withdrawals WHERE status='pending'"
        )).fetchone()
        # reliz opens
        try:
            ro = await (await db.execute("SELECT opens FROM case_opens WHERE case_id='reliz'")).fetchone()
            reliz_opens = int(ro[0] or 0) if ro else 0
        except Exception:
            reliz_opens = 0
    edge = 0.0
    if tin > 0:
        edge = round(100.0 * (tin - tout) / tin, 2)
    rtp = round(100.0 - edge, 2) if tin > 0 else 0.0
    return {
        "users": int(n[0] or 0),
        "stars": int(s[0] or 0),
        "day": {
            "turnover_in": tin,
            "payouts_out": tout,
            "edge_pct": edge,
            "rtp_pct": rtp,
            "withdraw_paid": wpaid,
            "withdraw_pending": int(wd_pend[0] or 0) if wd_pend else 0,
        },
        "reliz_opens": reliz_opens,
        "reliz_left": max(0, 50 - reliz_opens),
    }



@app.get("/api/admin/users")
async def admin_users(
    q: str = "",
    limit: int = 20,
    authorization: Optional[str] = Header(None),
    x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data"),
):
    me = await current_user(authorization, x_telegram_init_data)
    require_admin(me)
    q = (q or "").strip().lstrip("@")
    async with get_db() as db:
        if q.isdigit():
            rows = await (await db.execute(
                "SELECT tg_id, username, balance, wins, cases_opened FROM users WHERE tg_id=? LIMIT ?",
                (int(q), limit),
            )).fetchall()
        elif q:
            rows = await (await db.execute(
                "SELECT tg_id, username, balance, wins, cases_opened FROM users WHERE username LIKE ? ORDER BY balance DESC LIMIT ?",
                (f"%{q}%", limit),
            )).fetchall()
        else:
            rows = await (await db.execute(
                "SELECT tg_id, username, balance, wins, cases_opened FROM users ORDER BY balance DESC LIMIT ?",
                (limit,),
            )).fetchall()
    users = [{"tg_id": int(r[0]), "username": r[1] or "Player", "balance": int(r[2] or 0), "wins": int(r[3] or 0), "cases": int(r[4] or 0)} for r in (rows or [])]
    return {"users": users}

@app.get("/api/admin/user")
async def admin_user_detail(
    user_id: int = Query(...),
    limit: int = 40,
    authorization: Optional[str] = Header(None),
    x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data"),
):
    me = await current_user(authorization, x_telegram_init_data)
    require_admin(me)
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT tg_id, username, balance, inventory, games, wins, deposited, chance_bonus, cases_opened FROM users WHERE tg_id=?",
            (user_id,),
        )).fetchone()
        if not row:
            raise HTTPException(404, "user not found")
        hist = await (await db.execute(
            "SELECT game, result, detail, amount, created_at FROM history WHERE tg_id=? ORDER BY id DESC LIMIT ?",
            (user_id, max(1, min(100, limit))),
        )).fetchall()
        wds = await (await db.execute(
            "SELECT id, amount, method, dest, note, status, created_at FROM withdrawals WHERE tg_id=? ORDER BY id DESC LIMIT 20",
            (user_id,),
        )).fetchall()
        by_mode_rows = await (await db.execute(
            "SELECT game, COUNT(*), SUM(CASE WHEN result IN ('win','gift') THEN 1 ELSE 0 END), SUM(amount) "
            "FROM history WHERE tg_id=? GROUP BY game",
            (user_id,),
        )).fetchall()
    inv = []
    try:
        inv = json.loads(row[3] or "[]")
    except Exception:
        inv = []
    inv_val = sum(int(it.get("value") or 0) for it in inv if isinstance(it, dict))
    bal = int(row[2] or 0)
    dep = int(row[6] or 0)
    user = {
        "tg_id": int(row[0]),
        "username": row[1] or "Player",
        "balance": bal,
        "inventory_count": len(inv),
        "inventory_value": inv_val,
        "games": int(row[4] or 0),
        "wins": int(row[5] or 0),
        "deposited": dep,
        "chance_bonus": float(row[7] or 0),
        "cases_opened": int(row[8] or 0),
        "net_hint": bal + inv_val - dep,
    }
    history = [{"game": r[0], "result": r[1], "detail": r[2], "amount": r[3], "ts": r[4]} for r in (hist or [])]
    withdrawals = [
        {"id": r[0], "amount": r[1], "method": r[2], "dest": r[3], "note": r[4], "status": r[5], "ts": r[6]}
        for r in (wds or [])
    ]
    by_mode = [
        {"game": r[0] or "?", "spins": int(r[1] or 0), "wins": int(r[2] or 0), "sum_amount": int(r[3] or 0)}
        for r in (by_mode_rows or [])
    ]
    # депозиты (stars + ton)
    deposits = []
    try:
        async with get_db() as db:
            drows = await (await db.execute(
                "SELECT payload, amount, paid, created_at FROM deposits WHERE tg_id=? ORDER BY created_at DESC LIMIT 20",
                (user_id,),
            )).fetchall()
            for r in (drows or []):
                deposits.append({
                    "kind": "stars", "payload": r[0], "amount": int(r[1] or 0),
                    "paid": bool(int(r[2] or 0)), "ts": r[3],
                })
            try:
                trows = await (await db.execute(
                    "SELECT id, amount_ton, credited, created_at FROM ton_deposits WHERE tg_id=? ORDER BY created_at DESC LIMIT 10",
                    (user_id,),
                )).fetchall()
                for r in (trows or []):
                    deposits.append({
                        "kind": "ton", "payload": r[0], "amount_ton": float(r[1] or 0),
                        "paid": bool(int(r[2] or 0)), "ts": r[3],
                    })
            except Exception:
                pass
    except Exception:
        deposits = []
    return {
        "user": user, "history": history, "withdrawals": withdrawals,
        "by_mode": by_mode, "deposits": deposits,
    }

@app.post("/api/craft")
async def craft_items(
    body: CraftRequest,
    authorization: Optional[str] = Header(None),
    x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data"),
):
    """Craft 3+ inventory gifts into one. RTP ~88%, range ~0.12x..10x of sum."""
    u = await current_user(authorization, x_telegram_init_data)
    raw_ids = body.item_ids or []
    ids = []
    for x in raw_ids:
        try:
            ids.append(int(x) if str(x).isdigit() else x)
        except Exception:
            ids.append(x)
    indices = []
    for x in (body.item_indices or []):
        try:
            indices.append(int(x))
        except Exception:
            pass

    async with get_db() as db:
        row = await (await db.execute("SELECT inventory FROM users WHERE tg_id=?", (u["tg_id"],))).fetchone()
        inv = []
        if row and row[0]:
            try:
                inv = json.loads(row[0]) if isinstance(row[0], str) else (row[0] or [])
            except Exception:
                inv = []
        if not isinstance(inv, list):
            inv = []

        selected = []
        used_idx = set()
        for idx in indices:
            if 0 <= idx < len(inv) and idx not in used_idx:
                selected.append(inv[idx])
                used_idx.add(idx)
        if len(selected) < 3 and ids:
            idset = set(str(x) for x in ids)
            for i, it in enumerate(inv):
                if i in used_idx:
                    continue
                iid = str(it.get("id") or it.get("uid") or "")
                if iid and iid in idset:
                    selected.append(it)
                    used_idx.add(i)
                    idset.discard(iid)
        if len(selected) < 3:
            raise HTTPException(400, "Минимум 3 подарка для крафта")
        if len(selected) > 10:
            raise HTTPException(400, "Максимум 10 подарков")

        remaining = [it for i, it in enumerate(inv) if i not in used_idx]
        total = sum(max(1, int(it.get("value") or 0)) for it in selected)

        # bands: (weight, min_mult, max_mult) — EV ~0.88
        bands = [
            (0.38, 0.12, 0.32),
            (0.28, 0.32, 0.55),
            (0.16, 0.55, 0.85),
            (0.09, 0.85, 1.15),
            (0.055, 1.15, 2.0),
            (0.025, 2.0, 4.0),
            (0.008, 4.0, 7.0),
            (0.002, 7.0, 10.0),
        ]
        r = random.random()
        acc = 0.0
        lo, hi = 0.12, 0.32
        for w, a, b in bands:
            acc += w
            if r <= acc:
                lo, hi = a, b
                break
        mult = lo + random.random() * (hi - lo)
        target = max(1, int(total * mult))

        pool = list(GIFTS_FLAT) if GIFTS_FLAT else [{"name": "Подарок", "value": target, "rarity": "Common", "emoji": "🎁", "sn": "gift"}]
        scored = sorted(pool, key=lambda g: abs(int(g.get("value") or 0) - target))
        top = scored[:15] or scored
        weights = []
        for g in top:
            dist = abs(int(g.get("value") or 0) - target) + 1
            weights.append(1.0 / (dist ** 1.2))
        s = sum(weights) or 1
        rr = random.random() * s
        accw = 0.0
        chosen = top[0]
        for g, w in zip(top, weights):
            accw += w
            if rr <= accw:
                chosen = g
                break

        import time as _time
        result = {
            "id": f"craft_{u['tg_id']}_{int(_time.time())}_{random.randint(1000,9999)}",
            "uid": f"craft_{u['tg_id']}_{int(_time.time())}",
            "name": chosen.get("name") or "Крафт",
            "value": int(chosen.get("value") or target),
            "rarity": chosen.get("rarity") or "Rare",
            "emoji": chosen.get("emoji") or "🎁",
            "img": chosen.get("img") or "",
            "sn": chosen.get("sn") or "",
        }
        if not result["img"] and result.get("sn"):
            result["img"] = f"https://cdn.jsdelivr.net/gh/ssamy2/TG_Photos@main/webp/by_name/{result['sn']}.webp"

        remaining.append(result)
        await db.execute(
            "UPDATE users SET inventory=? WHERE tg_id=?",
            (json.dumps(remaining, ensure_ascii=False), u["tg_id"]),
        )
        await db.commit()

    return {"success": True, "gift": result, "inventory": remaining, "cost": total, "mult": round(mult, 3)}


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
        item_index = int((data or {}).get("item_index") if (data or {}).get("item_index") is not None else -1)
        username = str((data or {}).get("username") or "Player")
        if CRASH["status"] != "betting":
            await sio.emit("error", {"message": "Ставки закрыты"}, to=sid)
            return
        async with get_db() as db:
            row = await (await db.execute(
                "SELECT balance, inventory FROM users WHERE tg_id=?", (tg_id,)
            )).fetchone()
            if not row:
                await sio.emit("error", {"message": "Нет пользователя"}, to=sid)
                return
            bal = int(row[0] or 0)
            try:
                inv = json.loads(row[1] or "[]")
            except Exception:
                inv = []
            if item_index >= 0:
                if item_index >= len(inv):
                    await sio.emit("error", {"message": "Нет NFT"}, to=sid)
                    return
                item = inv.pop(item_index)
                amount = int(item.get("value") or 0)
                if amount < MIN_BET:
                    await sio.emit("error", {"message": f"NFT < {MIN_BET}⭐"}, to=sid)
                    return
                await db.execute(
                    "UPDATE users SET balance=?, inventory=?, games=games+1 WHERE tg_id=?",
                    (bal, json.dumps(inv, ensure_ascii=False), tg_id),
                )
            else:
                if amount < MIN_BET:
                    await sio.emit("error", {"message": f"Мин. {MIN_BET}⭐"}, to=sid)
                    return
                if bal < amount:
                    await sio.emit("error", {"message": "Недостаточно ⭐"}, to=sid)
                    return
                await db.execute(
                    "UPDATE users SET balance=balance-?, games=games+1 WHERE tg_id=?",
                    (amount, tg_id),
                )
            await db.commit()
            bal2 = await (await db.execute("SELECT balance, inventory FROM users WHERE tg_id=?", (tg_id,))).fetchone()
        CRASH["bets"][tg_id] = {"amount": amount, "username": username, "cashed": False, "win": 0, "sid": sid}
        inv_out = []
        try:
            inv_out = json.loads(bal2[1] or "[]")
        except Exception:
            pass
        await sio.emit("bet_placed", {"amount": amount, "balance": int(bal2[0]), "inventory": inv_out}, to=sid)
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
        win = int(b["amount"] * CRASH["multiplier"])
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
