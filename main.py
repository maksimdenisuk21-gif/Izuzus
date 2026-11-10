# main.py — GiftUpgrader
# NOTE: NFT на чёрном фоне (black model) = выше цена на Fragment; в дропах Дурова/Telegram бери реальные gifts, не выдуманные.
# BLACK_BG_NOTE
# FastAPI + Socket.IO backend for the Telegram Mini App HTML.
# Put this file + index.html on Render (or GitHub → Render).
#
# Env:
#   BOT_TOKEN          Telegram bot token (do NOT hardcode)
#   ADMIN_TG_ID        numeric Telegram user id of admin
#   DATABASE_URL       Neon/Postgres URL (optional; SQLite fallback)
#   SQLITE_PATH        sqlite file path (default database.db)
#   TON_TREASURY       TON wallet for deposits
#   TON_STARS_PER_TON  default 102.3 (Fragment Stars pack rate)
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
WEBAPP_URL = (os.getenv("WEBAPP_URL") or os.getenv("MINI_APP_URL") or "https://win-gifts.netlify.app").strip().rstrip("/")
FREE_CASE_COVER = (os.getenv("FREE_CASE_COVER") or "").strip()  # optional photo URL for free case notify
ADMIN_TG_ID = int(os.getenv("ADMIN_TG_ID") or "7092015279")
ADMIN_EXTRA: set = set()      # доп. админы (команда /admin в боте)
WBANNED: set = set()          # заблокирован вывод (/withdrawban)
BONUS_OFF: set = set()        # режимы, где бонусы к шансам выключены (/bonus)
BONUS_OFF_IDS: set = set()    # аккаунты админа, где бонусы выключены (только для себя)
# Анти-абуз: скрытый «слив» по tg_id. Игрок видит обычные шансы, сервер форсит проигрыш.
# modes: all | upgrade,mines,cases,crash,coin,mystery,plinko
RIGGED: Dict[int, set] = {}   # tg_id -> set of modes (anti-abuse)

def _bonus_off(uid, mode: str) -> bool:
    return mode in BONUS_OFF or int(uid or 0) in BONUS_OFF_IDS

def is_rigged(uid, mode: str = "all") -> bool:
    """Анти-абуз: скрытый слив. Игроку не показывается."""
    try:
        uid = int(uid or 0)
    except Exception:
        return False
    modes = RIGGED.get(uid)
    if not modes:
        return False
    if "all" in modes:
        return True
    return (mode or "").lower() in modes
SUPPORT_USERNAME = (os.getenv("SUPPORT_USERNAME") or "skumi1").lstrip("@").strip() or "skumi1"
GIFT_WITHDRAW_ONLY = (os.getenv("GIFT_WITHDRAW_ONLY") or "1") == "1"  # вывод только подарками
REF_L1_PCT = float(os.getenv("REF_L1_PCT") or 5)   # % рефереру с депозита (показывается в приложении)
REF_L2_PCT = float(os.getenv("REF_L2_PCT") or 1)
HOLD_STARS_SEC = int(os.getenv("HOLD_STARS_SEC") or 7 * 86400)   # вывод после пополнения звёздами — 7 дней
HOLD_TON_SEC = int(os.getenv("HOLD_TON_SEC") or 30 * 60)        # вывод после пополнения TON
GIFT_RELAYER = (os.getenv("GIFT_RELAYER") or "").strip().lstrip("@")  # аккаунт, куда игроки шлют подарки (Telegram Business + бот)
BOT_USERNAME = (os.getenv("BOT_USERNAME") or "").lstrip("@").strip()
BLOGGER_BOT = (os.getenv("BLOGGER_BOT_USERNAME") or "casefightbot").lstrip("@").strip()   # ссылки блогеров/промо
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
NFT_SHOP_EXTRA_TON = float(os.getenv("NFT_SHOP_EXTRA_TON") or "2")   # +2 TON к цене каждого NFT в магазине
MYSTERY_MIN_TON = float(os.getenv("MYSTERY_MIN_TON") or "10")        # минимальный вход в Mystery (10 или 15 TON)
SELL_FEE = float(os.getenv("SELL_FEE") or "0")  # без комиссии
MAX_CHANCE_BONUS = 12.0
_PROMO_LOCK = asyncio.Lock()
_PROMO_TRIES: Dict[int, list] = {}
INIT_MAX_AGE = int(os.getenv("INIT_MAX_AGE_SEC") or 7 * 86400)   # initData старше недели не принимаем (защита от украденных ссылок)
UPG_RTP = float(os.getenv("UPG_RTP") or 0.82)            # возврат в апгрейде: шанс = UPG_RTP / икс
UPG_MAX_CHANCE = float(os.getenv("UPG_MAX_CHANCE") or 80)  # потолок шанса, %
GAME_MAX_X = float(os.getenv("GAME_MAX_X") or 50)          # максимальный икс в любой игре
MAX_WIN = int(float(os.getenv("MAX_WIN_TON") or 300) * 110)  # максимальный выигрыш за одну игру (в ⭐, 1 TON≈110⭐)
UPG_MAX_X = float(os.getenv("UPG_MAX_X") or 100)           # апгрейд: цель не дороже ×100 от ставки

def cap_win(x) -> int:
    return min(MAX_WIN, int(round(float(x))))
# кейсы без нормальной обложки — скрыты и закрыты для открытия
DISABLED_CASES = {"chest_case", "witch_cauldron_case", "ghost_pumpkin_case", "dracula_crypt_case", "reaper_scythe_case"}
DISABLED_CASES_BASE = {x.replace("_case", "") for x in DISABLED_CASES}
UPGRADE_LUCKY_SHARE = float(os.getenv("UPGRADE_LUCKY_SHARE") or "0.08")  # доля Lucky-монеты в зоне выигрыша
UPGRADE_LUCKY_APPEAR = float(os.getenv("UPGRADE_LUCKY_APPEAR") or "0.03")  # как часто монета появляется на колесе
UP_ROUND: Dict[int, dict] = {}  # tg_id -> {"lucky": bool, "ts": int} — раунд выдаётся сервером, переролл невозможен  # потолок админ-бонуса шанса
DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
SQLITE_PATH = os.getenv("SQLITE_PATH") or "database.db"
DB_NAME = SQLITE_PATH  # alias for sqlite path
USE_POSTGRES = bool(DATABASE_URL)  # Neon/Postgres если задан DATABASE_URL
TON_TREASURY = (os.getenv("TON_TREASURY") or "").strip()
TON_STARS_PER_TON = float(os.getenv("TON_STARS_PER_TON") or os.getenv("STARS_PER_TON") or "110")
STARS_PER_TON = TON_STARS_PER_TON  # alias — иначе NameError в open_case / allin
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
    async def commit(self):
        # asyncpg: явно зафиксировать, если открыта транзакция
        try:
            if getattr(self._conn, "is_in_transaction", lambda: False)():
                await self._conn.execute("COMMIT")
        except Exception as e:
            print("[DB] commit:", e)
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
                    DATABASE_URL, min_size=2, max_size=int(os.getenv("PG_POOL_MAX") or 15),
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

_PK_AUTO = "SERIAL PRIMARY KEY" if USE_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
SCHEMA = f"""
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
  id {_PK_AUTO},
  tg_id BIGINT,
  game TEXT,
  result TEXT,
  detail TEXT,
  amount INTEGER DEFAULT 0,
  created_at INTEGER
);
CREATE TABLE IF NOT EXISTS withdrawals (
  id {_PK_AUTO},
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
  id {_PK_AUTO},
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
  id {_PK_AUTO},
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
  offer_a TEXT DEFAULT '{{}}',
  offer_b TEXT DEFAULT '{{}}',
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
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS photo_url TEXT",
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
                # battles.id в старых базах INTEGER — а комнаты создаются со строковым id (из-за этого был 500 и списание без батла)
                "ALTER TABLE battles ALTER COLUMN id DROP DEFAULT",
                "ALTER TABLE battles ALTER COLUMN id DROP IDENTITY IF EXISTS",
                "ALTER TABLE battles ALTER COLUMN id TYPE TEXT USING id::text",
                "ALTER TABLE battles ALTER COLUMN host_id TYPE BIGINT",
                "ALTER TABLE battles ALTER COLUMN winner_id TYPE BIGINT"]
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
        # Сидим базовые и хэллоуинские промокоды (если их ещё нет)
        for code, rtype, st, max_u in [
        ]:
            try:
                ex = await (await db.execute("SELECT 1 FROM promos WHERE UPPER(TRIM(code))=?", (code,))).fetchone()
                if not ex:
                    await db.execute(
                        "INSERT INTO promos(code,reward_type,stars,max_uses,uses) VALUES(?,?,?,?,0)",
                        (code, rtype, st, max_u),
                    )
            except Exception as _pe:
                print("[DB] promo seed:", _pe)
        try:
            await db.commit()
        except Exception:
            pass

print("[DB] mode:", "POSTGRES" if USE_POSTGRES else f"SQLite ({DB_NAME})")



# -------------------- CATALOG --------------------
# Цены в ⭐. Collectible floor ≈ TON_floor × 110 (1 TON ≈ 110⭐).
# Русские и EN-имена сведены к одним value, без дублей «15 vs 5773».
CDN = (os.getenv("GIFT_CDN") or "https://cdn.jsdelivr.net/gh/ssamy2/TG_Photos@main/webp/by_name").rstrip("/")
GIFT_SN_CDN = {
    "resistance": "resistance", "резистенс": "resistance",
    "мишка": "toy_bear", "сердце": "cookie_heart", "конфета": "lol_pop",
    "подарок": "joyful_bundle", "звезда": "hanging_star", "торт": "homemade_cake",
    "ракета": "stellar_rocket", "букет": "lush_bouquet", "ёлка": "winter_wreath",
    "елка": "winter_wreath", "шампанское": "spiced_wine", "цветы": "sakura_flower",
    "кольцо": "ring", "алмаз": "gem_stone", "кубок": "mini_oscar",
    "teddy_bear": "toy_bear", "heart": "cookie_heart", "candy": "lol_pop",
    # Fragment / English names → file sn
    "plush pepe": "plush_pepe", "spy agaric": "spy_agaric", "swiss watch": "swiss_watch",
    "heroic helmet": "heroic_helmet", "desk calendar": "desk_calendar", "bow tie": "bow_tie",
    "jelly bunny": "jelly_bunny", "crystal ball": "crystal_ball", "love potion": "love_potion",
    "diamond ring": "diamond_ring", "signet ring": "signet_ring", "mini oscar": "mini_oscar",
    "durov's glasses": "durov_glasses", "durov's cap": "durov_cap", "loot bag": "loot_bag",
    "precious peach": "precious_peach", "astral shard": "astral_shard", "scared cat": "scared_cat",
    "electric skull": "electric_skull", "cupid charm": "cupid_charm", "evil eye": "evil_eye",
    "snoop dogg": "snoop_dogg", "hanging star": "hanging_star", "top hat": "top_hat",
    "trapped heart": "trapped_heart", "kissed frog": "kissed_frog", "neko helmet": "neko_helmet",
    "ion gem": "ion_gem", "voodoo doll": "voodoo_doll", "vintage cigar": "vintage_cigar",
    "low rider": "low_rider", "sleigh bell": "sleigh_bell", "swag bag": "swag_bag",
}

# Fragment / new gifts sn aliases
GIFT_SN_EXTRA = {
    "mirage lamp": "magic_lamp", "timeless bottle": "timeless_bottle",
    "jack-in-the-box": "jack_in_the_box", "victory medal": "victory_medal",
    "star notepad": "star_notepad", "party sparkler": "party_sparkler",
    "hex pot": "hex_pot", "pretty posy": "pretty_posy", "faith amulet": "faith_amulet",
    "input key": "input_key", "spring basket": "spring_basket", "eternal candle": "eternal_candle",
    "fine pen": "fine_pen", "love candle": "love_candle", "record player": "record_player",
    "ionic dryer": "ionic_dryer", "ufc strike": "ufc_strike", "lucky locket": "lucky_locket",
    "sky stilettos": "sky_stilettos", "bling binky": "bling_binky", "rare bird": "rare_bird",
    "coffin": "coffin", "bonded ring": "bonded_ring", "sharp tongue": "sharp_tongue",
    "gem signet": "gem_signet", "artisan brick": "artisan_brick", "ufc box": "ufc_box",
    "perfume bottle": "perfume_bottle", "gravestone": "gravestone",
    "durov's boots": "durovs_boots", "durov's glasses": "durovs_glasses",
    "westside sign": "westside_sign", "nail bracelet": "nail_bracelet",
    "durov's coat": "durovs_coat", "mighty arm": "mighty_arm", "durov's cap": "durovs_cap",
    "heart locket": "heart_locket", "big year": "big_year", "happy brownie": "happy_brownie",
    "tama gadget": "tama_gadget", "mood pack": "mood_pack", "mousse cake": "mousse_cake",
    "restless jar": "restless_jar", "liberty figure": "liberty_figure", "santa hat": "santa_hat",
    "snow mittens": "snow_mittens", "mask": "mask", "airplane": "airplane",
    "plush pepe": "plush_pepe", "scared cat": "scared_cat", "swiss watch": "swiss_watch",
}
try:
    GIFT_SN_CDN.update(GIFT_SN_EXTRA)
except Exception:
    pass


GIFTS_FLAT = [
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
    {'name': 'Кольцо', 'value': 110, 'rarity': 'Common', 'sn': 'ring'},
    {'name': 'Vice Cream', 'value': 385, 'rarity': 'Rare', 'sn': 'vice_cream'},
    {'name': 'Xmas Stocking', 'value': 397, 'rarity': 'Rare', 'sn': 'xmas_stocking'},
    {'name': 'Candy Cane', 'value': 398, 'rarity': 'Rare', 'sn': 'candy_cane'},
    {'name': 'Snake Box', 'value': 398, 'rarity': 'Rare', 'sn': 'snake_box'},
    {'name': 'Ice Cream', 'value': 398, 'rarity': 'Rare', 'sn': 'ice_cream'},
    {'name': 'Instant Ramen', 'value': 398, 'rarity': 'Rare', 'sn': 'instant_ramen'},
    {'name': 'Pool Float', 'value': 398, 'rarity': 'Rare', 'sn': 'pool_float'},
    {'name': 'Chill Flame', 'value': 398, 'rarity': 'Rare', 'sn': 'chill_flame'},
    {'name': 'Big Year', 'value': 399, 'rarity': 'Rare', 'sn': 'big_year'},
    {'name': 'Whip Cupcake', 'value': 399, 'rarity': 'Rare', 'sn': 'whip_cupcake'},
    {'name': 'Lol Pop', 'value': 403, 'rarity': 'Rare', 'sn': 'lol_pop'},
    {'name': 'Holiday Drink', 'value': 404, 'rarity': 'Rare', 'sn': 'holiday_drink'},
    {'name': 'Lunar Snake', 'value': 408, 'rarity': 'Rare', 'sn': 'lunar_snake'},
    {'name': 'Pet Snake', 'value': 410, 'rarity': 'Rare', 'sn': 'pet_snake'},
    {'name': 'Jester Hat', 'value': 416, 'rarity': 'Rare', 'sn': 'jester_hat'},
    {'name': 'Fresh Socks', 'value': 416, 'rarity': 'Rare', 'sn': 'fresh_socks'},
    {'name': 'Hypno Lollipop', 'value': 421, 'rarity': 'Rare', 'sn': 'hypno_lollipop'},
    {'name': 'Easter Egg', 'value': 426, 'rarity': 'Rare', 'sn': 'easter_egg'},
    {'name': 'Happy Brownie', 'value': 427, 'rarity': 'Rare', 'sn': 'happy_brownie'},
    {'name': 'Winter Wreath', 'value': 429, 'rarity': 'Rare', 'sn': 'winter_wreath'},
    {'name': 'Tama Gadget', 'value': 430, 'rarity': 'Rare', 'sn': 'tama_gadget'},
    {'name': 'Ginger Cookie', 'value': 444, 'rarity': 'Rare', 'sn': 'ginger_cookie'},
    {'name': 'Spiced Wine', 'value': 449, 'rarity': 'Rare', 'sn': 'spiced_wine'},
    {'name': 'Mood Pack', 'value': 454, 'rarity': 'Rare', 'sn': 'mood_pack'},
    {'name': 'Party Sparkler', 'value': 459, 'rarity': 'Rare', 'sn': 'party_sparkler'},
    {'name': 'Victory Medal', 'value': 461, 'rarity': 'Rare', 'sn': 'victory_medal'},
    {'name': 'Timeless Bottle', 'value': 462, 'rarity': 'Rare', 'sn': 'timeless_bottle'},
    {'name': 'Star Notepad', 'value': 477, 'rarity': 'Rare', 'sn': 'star_notepad'},
    {'name': 'Mirage Lamp', 'value': 488, 'rarity': 'Rare', 'sn': 'mirage_lamp'},
    {'name': 'Jack-in-the-Box', 'value': 492, 'rarity': 'Rare', 'sn': 'jack_in_the_box'},
    {'name': 'B-Day Candle', 'value': 493, 'rarity': 'Rare', 'sn': 'bday_candle'},
    {'name': 'Cookie Heart', 'value': 494, 'rarity': 'Rare', 'sn': 'cookie_heart'},
    {'name': 'Hex Pot', 'value': 497, 'rarity': 'Rare', 'sn': 'hex_pot'},
    {'name': 'Money Pot', 'value': 504, 'rarity': 'Rare', 'sn': 'money_pot'},
    {'name': 'Mousse Cake', 'value': 507, 'rarity': 'Rare', 'sn': 'mousse_cake'},
    {'name': 'Clover Pin', 'value': 513, 'rarity': 'Rare', 'sn': 'clover_pin'},
    {'name': 'Stellar Rocket', 'value': 517, 'rarity': 'Rare', 'sn': 'stellar_rocket'},
    {'name': 'Bow Tie', 'value': 522, 'rarity': 'Rare', 'sn': 'bow_tie'},
    {'name': 'Swag Bag', 'value': 538, 'rarity': 'Rare', 'sn': 'swag_bag'},
    {'name': 'Homemade Cake', 'value': 539, 'rarity': 'Rare', 'sn': 'homemade_cake'},
    {'name': 'Restless Jar', 'value': 544, 'rarity': 'Rare', 'sn': 'restless_jar'},
    {'name': 'Liberty Figure', 'value': 552, 'rarity': 'Rare', 'sn': 'liberty_figure'},
    {'name': 'Santa Hat', 'value': 553, 'rarity': 'Rare', 'sn': 'santa_hat'},
    {'name': 'Snow Globe', 'value': 553, 'rarity': 'Rare', 'sn': 'snow_globe'},
    {'name': 'Pretty Posy', 'value': 560, 'rarity': 'Rare', 'sn': 'pretty_posy'},
    {'name': 'Snow Mittens', 'value': 566, 'rarity': 'Rare', 'sn': 'snow_mittens'},
    {'name': 'Desk Calendar', 'value': 574, 'rarity': 'Rare', 'sn': 'desk_calendar'},
    {'name': 'Spy Agaric', 'value': 582, 'rarity': 'Rare', 'sn': 'spy_agaric'},
    {'name': 'Faith Amulet', 'value': 585, 'rarity': 'Rare', 'sn': 'faith_amulet'},
    {'name': 'Snoop Dogg', 'value': 594, 'rarity': 'Rare', 'sn': 'snoop_dogg'},
    {'name': 'Input Key', 'value': 606, 'rarity': 'Rare', 'sn': 'input_key'},
    {'name': 'Spring Basket', 'value': 614, 'rarity': 'Rare', 'sn': 'spring_basket'},
    {'name': 'Eternal Candle', 'value': 630, 'rarity': 'Rare', 'sn': 'eternal_candle'},
    {'name': 'Moon Pendant', 'value': 631, 'rarity': 'Rare', 'sn': 'moon_pendant'},
    {'name': 'Witch Hat', 'value': 637, 'rarity': 'Rare', 'sn': 'witch_hat'},
    {'name': 'Lush Bouquet', 'value': 658, 'rarity': 'Rare', 'sn': 'lush_bouquet'},
    {'name': 'Light Sword', 'value': 685, 'rarity': 'Rare', 'sn': 'light_sword'},
    {'name': 'Surge Board', 'value': 694, 'rarity': 'Rare', 'sn': 'surge_board'},
    {'name': 'Jolly Chimp', 'value': 750, 'rarity': 'Rare', 'sn': 'jolly_chimp'},
    {'name': 'Evil Eye', 'value': 774, 'rarity': 'Rare', 'sn': 'evil_eye'},
    {'name': 'Jelly Bunny', 'value': 790, 'rarity': 'Rare', 'sn': 'jelly_bunny'},
    {'name': 'Joyful Bundle', 'value': 805, 'rarity': 'Rare', 'sn': 'joyful_bundle'},
    {'name': 'Sleigh Bell', 'value': 813, 'rarity': 'Rare', 'sn': 'sleigh_bell'},
    {'name': 'Bunny Muffin', 'value': 833, 'rarity': 'Rare', 'sn': 'bunny_muffin'},
    {'name': 'Fine Pen', 'value': 876, 'rarity': 'Rare', 'sn': 'fine_pen'},
    {'name': 'Berry Box', 'value': 928, 'rarity': 'Rare', 'sn': 'berry_box'},
    {'name': 'Hanging Star', 'value': 932, 'rarity': 'Rare', 'sn': 'hanging_star'},
    {'name': 'Top Hat', 'value': 1038, 'rarity': 'Epic', 'sn': 'top_hat'},
    {'name': 'Jingle Bells', 'value': 1072, 'rarity': 'Epic', 'sn': 'jingle_bells'},
    {'name': 'Sakura Flower', 'value': 1075, 'rarity': 'Epic', 'sn': 'sakura_flower'},
    {'name': 'Mask', 'value': 1154, 'rarity': 'Epic', 'sn': 'mask'},
    {'name': 'Valentine Box', 'value': 1164, 'rarity': 'Epic', 'sn': 'valentine_box'},
    {'name': 'Love Candle', 'value': 1199, 'rarity': 'Epic', 'sn': 'love_candle'},
    {'name': 'Record Player', 'value': 1234, 'rarity': 'Epic', 'sn': 'record_player'},
    {'name': 'Skull Flower', 'value': 1242, 'rarity': 'Epic', 'sn': 'skull_flower'},
    {'name': 'Flying Broom', 'value': 1263, 'rarity': 'Epic', 'sn': 'flying_broom'},
    {'name': 'Crystal Ball', 'value': 1326, 'rarity': 'Epic', 'sn': 'crystal_ball'},
    {'name': 'Mad Pumpkin', 'value': 1437, 'rarity': 'Epic', 'sn': 'mad_pumpkin'},
    {'name': 'Ionic Dryer', 'value': 1515, 'rarity': 'Epic', 'sn': 'ionic_dryer'},
    {'name': 'Love Potion', 'value': 1527, 'rarity': 'Epic', 'sn': 'love_potion'},
    {'name': 'UFC Strike', 'value': 1603, 'rarity': 'Epic', 'sn': 'ufc_strike'},
    {'name': 'Snoop Cigar', 'value': 1618, 'rarity': 'Epic', 'sn': 'snoop_cigar'},
    {'name': 'Trapped Heart', 'value': 1685, 'rarity': 'Epic', 'sn': 'trapped_heart'},
    {'name': 'Lucky Locket', 'value': 1795, 'rarity': 'Epic', 'sn': 'lucky_locket'},
    {'name': 'Sky Stilettos', 'value': 2011, 'rarity': 'Epic', 'sn': 'sky_stilettos'},
    {'name': 'Cupid Charm', 'value': 2300, 'rarity': 'Epic', 'sn': 'cupid_charm'},
    {'name': 'Bling Binky', 'value': 2524, 'rarity': 'Epic', 'sn': 'bling_binky'},
    {'name': 'Eternal Rose', 'value': 2626, 'rarity': 'Epic', 'sn': 'eternal_rose'},
    {'name': 'Electric Skull', 'value': 2692, 'rarity': 'Epic', 'sn': 'electric_skull'},
    {'name': 'Rare Bird', 'value': 2990, 'rarity': 'Epic', 'sn': 'rare_bird'},
    {'name': 'Diamond Ring', 'value': 3288, 'rarity': 'Epic', 'sn': 'diamond_ring'},
    {'name': 'Signet Ring', 'value': 3398, 'rarity': 'Epic', 'sn': 'signet_ring'},
    {'name': 'Coffin', 'value': 3460, 'rarity': 'Epic', 'sn': 'coffin'},
    {'name': 'Toy Bear', 'value': 3507, 'rarity': 'Epic', 'sn': 'toy_bear'},
    {'name': 'Voodoo Doll', 'value': 3891, 'rarity': 'Epic', 'sn': 'voodoo_doll'},
    {'name': 'Neko Helmet', 'value': 3894, 'rarity': 'Epic', 'sn': 'neko_helmet'},
    {'name': 'Genie Lamp', 'value': 3904, 'rarity': 'Epic', 'sn': 'genie_lamp'},
    {'name': 'Vintage Cigar', 'value': 3905, 'rarity': 'Epic', 'sn': 'vintage_cigar'},
    {'name': 'Kissed Frog', 'value': 4151, 'rarity': 'Legendary', 'sn': 'kissed_frog'},
    {'name': 'Bonded Ring', 'value': 4404, 'rarity': 'Legendary', 'sn': 'bonded_ring'},
    {'name': 'Sharp Tongue', 'value': 4798, 'rarity': 'Legendary', 'sn': 'sharp_tongue'},
    {'name': 'Magic Potion', 'value': 5684, 'rarity': 'Legendary', 'sn': 'magic_potion'},
    {'name': 'Swiss Watch', 'value': 5829, 'rarity': 'Legendary', 'sn': 'swiss_watch'},
    {'name': 'Low Rider', 'value': 6279, 'rarity': 'Legendary', 'sn': 'low_rider'},
    {'name': 'Gem Signet', 'value': 6450, 'rarity': 'Legendary', 'sn': 'gem_signet'},
    {'name': 'Artisan Brick', 'value': 6796, 'rarity': 'Legendary', 'sn': 'artisan_brick'},
    {'name': 'UFC Box', 'value': 6930, 'rarity': 'Legendary', 'sn': 'ufc_box'},
    {'name': 'Ion Gem', 'value': 7012, 'rarity': 'Legendary', 'sn': 'ion_gem'},
    {'name': 'Perfume Bottle', 'value': 7180, 'rarity': 'Legendary', 'sn': 'perfume_bottle'},
    {'name': 'Mini Oscar', 'value': 7327, 'rarity': 'Legendary', 'sn': 'mini_oscar'},
    {'name': 'Trojan Horse', 'value': 7854, 'rarity': 'Legendary', 'sn': 'trojan_horse'},
    {'name': 'Gravestone', 'value': 9806, 'rarity': 'Legendary', 'sn': 'gravestone'},
    {'name': 'Astral Shard', 'value': 11957, 'rarity': 'Legendary', 'sn': 'astral_shard'},
    {'name': 'Westside Sign', 'value': 12316, 'rarity': 'Legendary', 'sn': 'westside_sign'},
    {'name': 'Nail Bracelet', 'value': 12880, 'rarity': 'Legendary', 'sn': 'nail_bracelet'},
    {'name': 'Loot Bag', 'value': 13015, 'rarity': 'Legendary', 'sn': 'loot_bag'},
    {'name': 'Mighty Arm', 'value': 13452, 'rarity': 'Legendary', 'sn': 'mighty_arm'},
    {'name': 'Heroic Helmet', 'value': 19515, 'rarity': 'Legendary', 'sn': 'heroic_helmet'},
    {'name': 'Scared Cat', 'value': 23899, 'rarity': 'Legendary', 'sn': 'scared_cat'},
    {'name': 'Precious Peach', 'value': 26366, 'rarity': 'Legendary', 'sn': 'precious_peach'},
            {'name': 'Algorithm Cup', 'value': 178959, 'rarity': 'Mythic', 'sn': 'algorithm_cup'},
    {'name': 'Intelligence Cup', 'value': 183975, 'rarity': 'Mythic', 'sn': 'intelligence_cup'},
        {'name': 'Plush Pepe', 'value': 731544, 'rarity': 'Mythic', 'sn': 'plush_pepe'},
    {'name': "Durov's Cap", 'value': 42878, 'rarity': 'Legendary', 'sn': 'durovs_cap'},
    {'name': "Durov's Boots", 'value': 10372, 'rarity': 'Legendary', 'sn': 'durovs_boots'},
    {'name': "Durov's Glasses", 'value': 10990, 'rarity': 'Legendary', 'sn': 'durovs_glasses'},
    {'name': "Durov's Coat", 'value': 13165, 'rarity': 'Legendary', 'sn': 'durovs_coat'},
    {'name': 'Heart Locket', 'value': 134078, 'rarity': 'Mythic', 'sn': 'heart_locket'},
    {'name': 'Resistance', 'value': 51051, 'rarity': 'Legendary', 'sn': 'resistance'},
]



# Вымышленные подарки — никогда не отдаём в дроп/апгрейд/магазин
FAKE_GIFT_NAMES = {
    "white fox", "neon keychain", "star badge", "premium crown",
    "channel badge", "night goggles", "dm envelope", "big plans notebook",
    "socks tg", "triple meow", "energy drink", "chill flame x",
}
def is_real_gift(name: str) -> bool:
    n = (name or "").strip().lower()
    if not n: return False
    if n in FAKE_GIFT_NAMES: return False
    return True

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
    'random_case': {
        'name': 'Random', 'price': 1000, 'category': 'special',
        'icon': '❓', 'color': 'c-tg', 'rarities': ['Rare', 'Epic', 'Legendary'],
        'weights': [60, 30, 10], 'min_stars': 0, 'max_stars': 0, 'stars_chance': 0.0,
        'force_names': [], 'desc': 'Дроп меняется каждый день', 'cover': 'random',
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
        'name': 'Durov', 'price': 5490, 'category': 'only_nft',  # 49.9 TON × 110
        'icon': '🧢', 'color': 'c-tg', 'rarities': ['Epic', 'Legendary', 'Mythic'],
        'weights': [70, 24, 6], 'min_stars': 0, 'max_stars': 0, 'stars_chance': 0.0,
        # только реальные Telegram Gifts (Fragment); black model = выше цена
        'force_names': [
            'Crystal Ball', 'Electric Skull', 'Diamond Ring', 'Signet Ring',
            'Swiss Watch', 'Mini Oscar', "Durov's Glasses", 'Heroic Helmet', "Durov's Cap"],
        'desc': '49.9 TON · real NFT only', 'cover': 'durov', 'coming_soon': False,
    },
    'nft_candy': {
        'name': 'Candy', 'price': 400, 'category': 'nft',
        'icon': '🍬', 'color': 'c-pepe', 'rarities': ['Common', 'Uncommon', 'Rare'],
        'weights': [50, 35, 15], 'min_stars': 20, 'max_stars': 90, 'stars_chance': 0.25,
        'force_names': ['Конфета', 'Lol Pop', 'Cookie Heart', 'Candy Cane', 'Сердце', 'Berry Box', 'Jelly Bunny'],
        'desc': 'Сладкий кейс', 'cover': 'candy',
    },


    'witch_cauldron_case': {
        'name': 'Ведьмин Котел', 'price': 330, 'category': 'season',
        'icon': '🧪', 'color': 'c-pepe', 'rarities': ['Rare', 'Epic', 'Legendary'],
        'weights': [64, 26, 10], 'min_stars': 25, 'max_stars': 110, 'stars_chance': 0.12,
        'force_names': ['Hex Pot', 'Magic Potion', 'Flying Broom', 'Witch Hat', 'Crystal Ball', 'Spring Basket'],
        'desc': '🧪 Хэллоуин · зелья и артефакты', 'cover': 'witch_cauldron',
    },
    'dracula_crypt_case': {
        'name': 'Склеп Дракулы', 'price': 880, 'category': 'season',
        'icon': '🧛', 'color': 'c-tg', 'rarities': ['Rare', 'Epic', 'Legendary'],
        'weights': [58, 30, 12], 'min_stars': 40, 'max_stars': 180, 'stars_chance': 0.08,
        'force_names': ['Coffin', 'Gravestone', 'Trapped Heart', 'Eternal Rose', 'Voodoo Doll', 'Electric Skull', 'Sharp Tongue'],
        'desc': '🧛 Хэллоуин · реликвии вампиров', 'cover': 'dracula_crypt',
    },
    'reaper_scythe_case': {
        'name': 'Коса Жнеца', 'price': 2200, 'category': 'season',
        'icon': '☠️', 'color': 'c-durov', 'rarities': ['Epic', 'Legendary', 'Mythic'],
        'weights': [60, 30, 10], 'min_stars': 0, 'max_stars': 0, 'stars_chance': 0.0,
        'force_names': ['Electric Skull', 'Voodoo Doll', 'Gravestone', 'Astral Shard', 'Heroic Helmet', 'Scared Cat', "Durov's Cap"],
        'desc': '☠️ Хэллоуин VIP · только топ NFT', 'cover': 'reaper_scythe',
    },
    'ghost_pumpkin_case': {
        'name': 'Тыквенный Джек', 'price': 660, 'category': 'season',
        'icon': '🎃', 'color': 'c-starter', 'rarities': ['Rare', 'Epic', 'Legendary'],
        'weights': [60, 28, 12], 'min_stars': 30, 'max_stars': 150, 'stars_chance': 0.10,
        'force_names': ['Mad Pumpkin', 'Scared Cat', 'Skull Flower', 'Witch Hat', 'Jack-in-the-Box', 'Evil Eye'],
        'desc': '🎃 Хэллоуин · ночной охотник', 'cover': 'ghost_pumpkin',
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
    # 90 TON ≈ 9900⭐
    'telegram_case': {
        'name': 'Telegram', 'price': 3740, 'category': 'brands',  # 34 TON × 110
        'icon': '✈️', 'color': 'c-tg', 'rarities': ['Rare', 'Epic', 'Legendary', 'Mythic'],
        'weights': [55, 30, 12, 3], 'min_stars': 0, 'max_stars': 0, 'stars_chance': 0.0,
        'force_names': [
            'Swiss Watch', 'Mini Oscar', 'Diamond Ring', 'Signet Ring',
            'Heroic Helmet', 'Loot Bag', 'Precious Peach', 'Eternal Rose',
            "Durov's Glasses", 'Heroic Helmet'],
        'desc': 'Telegram Case · 90 TON', 'cover': 'telegram', 'coming_soon': False,
    },

    'allin_case': {
        'name': 'All In', 'price': 51, 'price_ton': 0.5, 'category': 'allin', 'allin': True,
        'icon': '💎', 'color': 'c-tg', 'rarities': ['Common'],
        'weights': [100], 'min_stars': 0, 'max_stars': 0, 'stars_chance': 0.0,
        'force_names': ['Plush Pepe','Swiss Watch','Ion Gem','Heroic Helmet','Diamond Ring','Mini Oscar','Durov\'s Glasses','Precious Peach'],
        'desc': 'All In · 0.5 TON', 'cover': 'allin',
    },
    'pythagoras_case': {
        'name': 'Pythagoras', 'price': 750, 'category': 'themed',
        'icon': '📐', 'color': 'c-starter', 'rarities': ['Rare', 'Epic', 'Legendary'],
        'weights': [68, 24, 8], 'min_stars': 40, 'max_stars': 160, 'stars_chance': 0.12,
        'force_names': ['Кольцо', 'Crystal Ball', 'Swiss Watch', 'Plush Pepe'],
        'desc': 'Pythagoras · math pack', 'cover': 'pythagoras',
    },
    'rocket_case': {
        'name': 'Rocket', 'price': 550, 'category': 'themed',
        'icon': '🚀', 'color': 'c-pepe', 'rarities': ['Uncommon', 'Rare', 'Epic'],
        'weights': [65, 27, 8], 'min_stars': 30, 'max_stars': 140, 'stars_chance': 0.15,
        'force_names': ['Ракета', 'Stellar Rocket', 'Desk Calendar', 'Lol Pop'],
        'desc': 'Rocket · to the moon', 'cover': 'rocket',
    },
    'vasya_case': {
        'name': 'Vasya', 'price': 400, 'category': 'themed',
        'icon': '👽', 'color': 'c-starter', 'rarities': ['Uncommon', 'Rare', 'Epic'],
        'weights': [70, 24, 6], 'min_stars': 25, 'max_stars': 100, 'stars_chance': 0.2,
        'force_names': ['Кольцо', 'Spy Agaric', 'Jelly Bunny', 'Berry Box'],
        'desc': 'Vasya · alien pack', 'cover': 'vasya',
    },
    'chicken_gun_case': {
        'name': 'Chicken Gun', 'price': 500, 'category': 'themed',
        'icon': '🐔', 'color': 'c-tg', 'rarities': ['Uncommon', 'Rare', 'Epic'],
        'weights': [68, 25, 7], 'min_stars': 30, 'max_stars': 120, 'stars_chance': 0.18,
        'force_names': ['Кубок', 'Алмаз', 'Evil Eye', 'Candy Cane'],
        'desc': 'Chicken Gun · battle pack', 'cover': 'chicken',
    },
    'gift_upg_case': {
        'name': 'Gift Upgrader', 'price': 900, 'category': 'nft',
        'icon': '⬆️', 'color': 'c-pepe', 'rarities': ['Rare', 'Epic', 'Legendary'],
        'weights': [65, 27, 8], 'min_stars': 50, 'max_stars': 200, 'stars_chance': 0.1,
        'force_names': ['Plush Pepe', 'Kissed Frog', 'Swiss Watch', 'Ion Gem'],
        'desc': 'Gift Upgrader · premium', 'cover': 'giftupg',
    },
    'cr7_case': {
        'name': 'CR7', 'price': 1600, 'category': 'brands',
        'icon': '⚽', 'color': 'c-tg', 'rarities': ['Epic', 'Legendary', 'Mythic'],
        'weights': [62, 28, 10], 'min_stars': 0, 'max_stars': 0, 'stars_chance': 0.05,
        'force_names': ['Swiss Watch', 'Diamond Ring', 'Heroic Helmet', 'Mini Oscar'],
        'desc': 'CR7 · GOAT pack', 'cover': 'cr7',
    },
    'redo_case': {
        'name': 'Redo', 'price': 700, 'category': 'brands',
        'icon': '🎭', 'color': 'c-tg', 'rarities': ['Rare', 'Epic', 'Legendary'],
        'weights': [64, 28, 8], 'min_stars': 20, 'max_stars': 140, 'stars_chance': 0.1,
        'force_names': ['Swiss Watch', 'Ion Gem', 'Mini Oscar', 'Heroic Helmet'],
        'desc': 'Redo — новый кейс', 'cover': 'redo',
    },
    'mask_case': {
        'name': 'Mask', 'price': 1200, 'category': 'brands',
        'icon': '🎭', 'color': 'c-tg', 'rarities': ['Epic', 'Legendary', 'Mythic'],
        'weights': [60, 30, 10], 'min_stars': 0, 'max_stars': 0, 'stars_chance': 0.05,
        'force_names': ['Diamond Ring', 'Plush Pepe', 'Swiss Watch', 'Ion Gem'],
        'desc': 'Mask — новый кейс', 'cover': 'mask',
    },

}

# Гарантия: новые кейсы всегда в CASES (если деплой частичный)
_ENSURE = {
    "allin_case", "pythagoras_case", "rocket_case", "vasya_case",
    "chicken_gun_case", "gift_upg_case", "cr7_case", "redo_case", "mask_case", "random_case",
}
# no-op if already present; helps debugging
for _k in list(_ENSURE):
    if _k not in CASES:
        print("[WARN] missing case in CASES:", _k)

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
    """CDN картинки TG Gifts. Несколько зеркал."""
    try:
        key = (name or "").lower().strip()
        if key in GIFT_SN_CDN:
            sn = GIFT_SN_CDN[key]
        elif not sn:
            sn = gift_short_name(name)
        sn = (sn or "toy_bear").lower().replace(" ", "_").replace("'", "").replace("’", "")
        if sn in GIFT_SN_CDN:
            sn = GIFT_SN_CDN[sn]
        # jsdelivr TG_Photos (основной)
        return f"{CDN}/{sn}.webp"
    except Exception:
        return f"{CDN}/toy_bear.webp"

def gift_img_fallbacks(name: str, sn: str = None) -> list:
    """Список URL картинок на случай если основной CDN не отдал."""
    key = (name or "").lower().strip()
    if key in GIFT_SN_CDN:
        sn = GIFT_SN_CDN[key]
    elif not sn:
        sn = gift_short_name(name)
    sn = (sn or "toy_bear").lower().replace(" ", "_").replace("'", "").replace("’", "")
    if sn in GIFT_SN_CDN:
        sn = GIFT_SN_CDN[sn]
    # plural variants often used on fragment-style CDNs
    alts = [sn, sn.rstrip("s") + "s" if not sn.endswith("s") else sn[:-1]]
    urls = []
    for s in alts:
        urls.append(f"{CDN}/{s}.webp")
        urls.append(f"https://cdn.jsdelivr.net/gh/ssamy2/TG_Photos@main/webp/{s}.webp")
        urls.append(f"https://nft.fragment.com/gift/{s}-1.large.jpg")
    # dedupe preserve order
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u); out.append(u)
    return out

def gift_public(g: dict) -> dict:
    name = g.get("name") or "Gift"
    sn = g.get("sn") or gift_short_name(name)
    rarity = g.get("rarity") or "Common"
    value = int(g.get("value") or 0)
    out = {
        "name": name,
        "sn": sn,
        "value": value,
        "rarity": rarity,
        "emoji": g.get("emoji") or EMOJI.get(name, "🎁"),
        "img": g.get("img") or gift_img_url(name, sn),
        "regular": bool(g.get("regular", rarity == "Common")),
    }
    if g.get("rental"):
        out["rental"] = True
        out["rental_hours"] = int(g.get("rental_hours") or 48)
        if "аренда" not in (name or "").lower():
            out["name"] = f"{name} · аренда 2д"
    return out

_BY_NAME: Dict[str, dict] = {}
_BY_RARITY: Dict[str, List[dict]] = {}
for _g in GIFTS_FLAT:
    if not is_real_gift(_g.get("name") or ""):
        continue
    gp = gift_public(_g)
    _BY_NAME[gp["name"].lower()] = gp
    _BY_RARITY.setdefault(gp["rarity"], []).append(gp)

def find_gift(name: str) -> Optional[dict]:
    if not name:
        return None
    if not is_real_gift(name):
        return None
    return _BY_NAME.get(name.lower().strip())

def gifts_grouped() -> dict:
    order = ["Common", "Uncommon", "Rare", "Epic", "Legendary", "Mythic"]
    out = {}
    for r in order:
        out[r] = [dict(x) for x in _BY_RARITY.get(r, []) if is_real_gift(x.get("name") or "")]
    return out

def nft_extra_stars() -> int:
    return int(round(NFT_SHOP_EXTRA_TON * float(TON_STARS_PER_TON)))

def is_nft_row(g: dict) -> bool:
    return str(g.get("rarity") or "Common").lower() != "common"

def upgrade_target_value(g: dict) -> int:
    """Цена цели в апгрейде = value из каталога (наценка NFT уже внутри value)."""
    return int(g.get("value") or 0)

def shop_price(g: dict) -> int:
    """Магазин: «сырая» цена × наценка + NFT_SHOP_EXTRA_TON (наценка уже сидит в value, поэтому вычитаем её)."""
    v = int(g.get("value") or 0)
    if is_nft_row(g):
        ex = nft_extra_stars()
        return max(1, int(round(max(0, v - ex) * SHOP_MARKUP)) + ex)
    return max(1, int(round(v * SHOP_MARKUP)))

def shop_items() -> List[dict]:
    items = []
    seen = set()
    for g in GIFTS_FLAT:
        n = (g.get("name") or "").strip()
        if not n or n.lower() in seen:
            continue
        if not is_real_gift(n):
            continue
        v = int(g.get("value") or 0)
        # почти весь каталог в магазине
        if v < 15 or v > 600000:
            continue
        seen.add(n.lower())
        pub = gift_public(g)
        pub["price"] = shop_price(g)
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
    """Шанс для UI и API. x2 ≈ 45%."""
    iv = float(iv or 0); tv = float(tv or 1)
    if tv <= 0 or iv <= 0:
        return 0.01
    # шанс строго от икса: chance = UPG_RTP / икс. ×2 → 42.5%, ×4 → 21.25%, ×10 → 8.5% (при RTP 85%)
    ch = (iv / tv) * 100.0 * UPG_RTP
    # бонус — относительный (+12% = шанс ×1.12), а не «+12 п.п.», чтобы ×20 не превращался в 15%
    bonus = min(MAX_CHANCE_BONUS, max(0.0, float(bonus or 0)))
    ch = ch * (1.0 + bonus / 100.0)
    return round(max(0.01, min(UPG_MAX_CHANCE, ch)), 2)

def roll_upgrade_success(chance: float) -> bool:
    """Показанный шанс не трогаем на клиенте, сервер режет фактический заход.
    x2 ~45% на экране → ~40.5% реальных побед (×0.90)."""
    ch = max(0.01, float(chance or 0))
    return random.random() * 100.0 < ch


CRASH_CFG = {"bet_sec": 10, "max_x": float(os.getenv("CRASH_MAX_X") or 30)}

def roll_crash_point() -> float:
    r = random.random()
    # жёстче: ~22% instant, кап 12x, RTP ~88–91%
    if r < 0.22:
        return 1.0
    if r < 0.40:
        return round(1.05 + random.random() * 0.45, 2)  # 1.05–1.50
    if r < 0.68:
        return round(1.50 + random.random() * 1.2, 2)   # 1.5–2.7
    p = 0.55 / max(1e-6, (1 - r))
    return min(float(CRASH_CFG["max_x"]), max(1.2, round(p * 100) / 100))  # кап ×30 (хвост: P(≥x)·x ≈ 0.55 — безопасно)


_PHOTO_CACHE = {}

async def fetch_tg_photo_url(tg_id: int) -> str:
    if not tg_id:
        return ""
    if tg_id in _PHOTO_CACHE:
        return _PHOTO_CACHE.get(tg_id) or ""
    token = (os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        return ""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                f"https://api.telegram.org/bot{token}/getUserProfilePhotos",
                params={"user_id": int(tg_id), "limit": 1},
            )
            data = r.json()
            photos = (((data or {}).get("result") or {}).get("photos") or [])
            if not photos:
                _PHOTO_CACHE[tg_id] = ""
                return ""
            sizes = photos[0]
            file_id = sizes[-1].get("file_id") if sizes else None
            if not file_id:
                _PHOTO_CACHE[tg_id] = ""
                return ""
            r2 = await client.get(
                f"https://api.telegram.org/bot{token}/getFile",
                params={"file_id": file_id},
            )
            fdata = r2.json()
            fpath = ((fdata or {}).get("result") or {}).get("file_path") or ""
            if not fpath:
                _PHOTO_CACHE[tg_id] = ""
                return ""
            out = f"https://api.telegram.org/file/bot{token}/{fpath}"
            _PHOTO_CACHE[tg_id] = out
            return out
    except Exception as e:
        print("[photo]", e)
        return ""

def mines_mult(mines: int, opened: int) -> float:
    total = 25
    mult = 1.0
    for i in range(opened):
        remaining = total - i
        safe = remaining - mines
        if safe <= 0:
            break
        mult *= remaining / safe
    # ниже иксы: edge ~14–16%
    edge = max(0.14, float(HOUSE_EDGE) * 1.6 if HOUSE_EDGE else 0.14)
    return min(GAME_MAX_X, max(1.0, round(mult * (1.0 - edge) * 100) / 100))

def now_ts() -> int:
    return int(time.time())

def ts_param(ts: int):
    """Postgres TIMESTAMP needs datetime; SQLite uses int."""
    try:
        ts = int(ts or 0)
    except Exception:
        ts = 0
    if USE_POSTGRES:
        try:
            return datetime.utcfromtimestamp(ts)
        except Exception:
            return datetime.utcnow()
    return ts


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
        try:
            auth_date = int(data.get("auth_date") or 0)
        except Exception:
            auth_date = 0
        if auth_date and time.time() - auth_date > INIT_MAX_AGE:
            raise HTTPException(401, "Сессия устарела — закрой и открой приложение заново")
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
    # Сначала X-Telegram-Init-Data (не Authorization — его режут прокси)
    raw = (x_telegram_init_data or "").strip() or (authorization or "").strip() or "dev"
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    info = _parse_init_data(raw)
    tg_id = int(info.get("id") or 0)
    if not tg_id:
        raise HTTPException(401, "no tg id")
    username = str(info.get("username") or info.get("first_name") or "Player")[:64]
    async with get_db() as db:
        row = await (await db.execute("SELECT tg_id, username, balance, inventory, games, wins, deposited, free_case_at, friend_case_at, chance_bonus, ton_wallet, cases_opened FROM users WHERE tg_id=?", (tg_id,))).fetchone()
        if not row:
            try:
                await db.execute(
                    "INSERT INTO users(tg_id,username,balance,inventory,games,wins,deposited,free_case_at,friend_case_at,chance_bonus,ton_wallet,cases_opened,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (tg_id, username, STARTING_BALANCE, "[]", 0, 0, 0, 0, 0, 0, "", 0, now_ts()),
                )
                await db.commit()
            except Exception as e:
                print("[user] insert", e)
                # retry minimal
                try:
                    await db.execute(
                        "INSERT INTO users(tg_id,username,balance,inventory,games,wins,cases_opened) VALUES(?,?,?,?,?,?,?)",
                        (tg_id, username, STARTING_BALANCE, "[]", 0, 0, 0),
                    )
                    await db.commit()
                except Exception as e2:
                    print("[user] insert2", e2)
            row = await (await db.execute("SELECT tg_id, username, balance, inventory, games, wins, deposited, free_case_at, friend_case_at, chance_bonus, ton_wallet, cases_opened FROM users WHERE tg_id=?", (tg_id,))).fetchone()
            if not row:
                row = (tg_id, username, STARTING_BALANCE, "[]", 0, 0, 0, 0, 0, 0, "", 0)
            # referral only for brand-new (balance==start and no games) — soft
            sp = str(info.get("start_param") or "").strip()
            if sp.startswith("ref_"):
                try:
                    ref_id = int(sp[4:].split("_")[0])
                    if ref_id and ref_id != tg_id:
                        exists = await (await db.execute("SELECT 1 FROM users WHERE tg_id=?", (ref_id,))).fetchone()
                        if exists:
                            try:
                                await db.execute(
                                    "INSERT INTO referrals(referred_id,referrer_id,earned,created_at) VALUES(?,?,0,?) ON CONFLICT DO NOTHING",
                                    (tg_id, ref_id, now_ts()),
                                )
                            except Exception:
                                # postgres: ON CONFLICT
                                try:
                                    await db.execute(
                                        "INSERT INTO referrals(referred_id,referrer_id,earned,created_at) VALUES(?,?,0,?) ON CONFLICT DO NOTHING",
                                        (tg_id, ref_id, now_ts()),
                                    )
                                except Exception as e2:
                                    print("[ref bind]", e2)
                            await db.commit()
                except Exception as e:
                    print("[ref bind]", e)
        else:
            # существующий игрок — обновить ник для топа
            try:
                if username and username != (row[1] or ""):
                    await db.execute("UPDATE users SET username=? WHERE tg_id=?", (username, tg_id))
                    await db.commit()
                    row = (row[0], username) + tuple(row[2:])
            except Exception:
                pass
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
            "is_admin": int(row[0]) == ADMIN_TG_ID or int(row[0]) in ADMIN_EXTRA,
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
    try:
        await hw_event(tg_id, game, result)
    except Exception as _e:
        print("[hw_event]", _e)

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
        if int(item.get("value") or 0) >= int(float(os.getenv("JACKPOT_TON") or 100) * float(STARS_PER_TON)):
            # джекпот-дождь у всех онлайн — с задержкой, чтобы у победителя успела доиграть анимация
            async def _jp(payload):
                await asyncio.sleep(float(os.getenv("JACKPOT_DELAY_SEC") or 14))
                try:
                    await sio.emit("jackpot", payload)
                except Exception:
                    pass
            asyncio.create_task(_jp({"user": user_name, "name": item.get("name"), "value": int(item.get("value") or 0),
                                     "img": item.get("img") or ""}))
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
    if inv[idx].get("rental") or inv[idx].get("playable") is False:
        raise HTTPException(400, "Аренду нельзя ставить в игры")
    item = inv.pop(idx)
    bet = int(item.get("value") or 0)
    # NFT-ставка: достаточно value >= 10⭐ (~0.1 TON), не полный MIN_BET
    if bet < 10:
        inv.insert(idx, item)
        raise HTTPException(400, "NFT слишком дешёвый (мин. ~0.1 TON)")
    return bet, item



async def pay_referral(referred_tg_id: int, deposit_stars: int):
    """L1: 5% рефереру · L2: 1% рефереру реферера. Мин. деп 50⭐."""
    if deposit_stars < 50:
        return
    bonus_l1 = max(1, int(deposit_stars * REF_L1_PCT / 100.0))
    bonus_l2 = max(1, int(deposit_stars * REF_L2_PCT / 100.0))
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
            await tg_notify(ref_id, f"👥 Реферал задепнул · тебе <b>+{bonus_l1}⭐</b> ({REF_L1_PCT:g}%)")
            if ref2_id:
                await tg_notify(ref2_id, f"👥 Реферал 2 ур. задепнул · тебе <b>+{bonus_l2}⭐</b> ({REF_L2_PCT:g}%)")
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


async def tg_send(tg_id: int, text: str, reply_markup: dict = None, photo_url: str = None):
    """sendMessage или sendPhoto в Telegram."""
    if not BOT_TOKEN or not tg_id:
        return False
    try:
        import urllib.request, urllib.parse, json as _json
        def _post(method, payload: dict):
            data = urllib.parse.urlencode({k: (v if isinstance(v, str) else _json.dumps(v, ensure_ascii=False)) for k, v in payload.items()}).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
                data=data, method="POST",
            )
            urllib.request.urlopen(req, timeout=12).read()
        payload = {
            "chat_id": int(tg_id),
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        if photo_url:
            payload["photo"] = photo_url
            payload["caption"] = (text or "")[:1000]
            await asyncio.get_event_loop().run_in_executor(None, lambda: _post("sendPhoto", payload))
        else:
            payload["text"] = (text or "")[:3500]
            await asyncio.get_event_loop().run_in_executor(None, lambda: _post("sendMessage", payload))
        return True
    except Exception as e:
        print("[tg_send]", e)
        return False

def _app_url() -> str:
    env = (os.getenv("WEBAPP_URL") or os.getenv("MINI_APP_URL") or "").strip().rstrip("/")
    if env:
        return env
    ext = (os.getenv("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
    return ext or WEBAPP_URL

def _webapp_keyboard(btn: str = "🎁 Открыть Win Gifts", extra: bool = True):
    rows = [[{"text": btn, "web_app": {"url": _app_url()}}]]
    if extra:
        row2 = []
        if os.getenv("CHAT_URL"):
            row2.append({"text": "💬 Чат", "url": os.getenv("CHAT_URL")})
        if os.getenv("CHANNEL_URL"):
            row2.append({"text": "📢 Канал", "url": os.getenv("CHANNEL_URL")})
        if row2:
            rows.append(row2)
    return {"inline_keyboard": rows}

async def tg_push(tg_id: int, text: str, btn: str = "🎁 Открыть", photo_url: str = None) -> bool:
    """Пуш в бота с кнопкой мини-аппа (как у топовых ботов)."""
    return await tg_send(int(tg_id), text, reply_markup=_webapp_keyboard(btn, extra=False), photo_url=photo_url)

async def handle_bot_start(tg_id: int, first_name: str = "", lang_code: str = ""):
    name = (first_name or "друг").strip() or "друг"
    # free case status
    free_ready = True
    try:
        last = 0
        async with get_db() as db:
            row = await (await db.execute(
                "SELECT free_case_at FROM users WHERE tg_id=?", (int(tg_id),)
            )).fetchone()
            if row:
                last = int(row[0] or 0)
        left = 86400 - (now_ts() - last) if last else 0
        free_ready = (last == 0) or (left <= 0)
        if free_ready:
            free_line = "✅ <b>Бесплатный кейс доступен прямо сейчас!</b>"
        else:
            hleft = max(1, int(left // 3600))
            free_line = f"⏳ Бесплатный кейс через ~{hleft} ч."
    except Exception as e:
        print("[start] user", e)
        free_line = "🎁 Каждый день — бесплатный кейс"
    text = start_text(lang_code, name, free_ready, free_line, tg_id)
    try:
        land = await start_landing_line(int(tg_id), lang_code)
        if land:
            text = land + "\n\n" + text
    except Exception as e:
        print("[landing]", e)
    await tg_send(tg_id, text, reply_markup=_webapp_keyboard())
    # если free готов — фото обложки
    if free_ready:
        cover = FREE_CASE_COVER if "FREE_CASE_COVER" in dir() and FREE_CASE_COVER else None
        if not cover:
            # попробуем из CASE_COVERS если есть base64 — skip, need URL
            cover = None
        if cover:
            await tg_send(
                tg_id,
                "🆓 <b>Free Case</b> снова доступен — заходи и забирай!",
                reply_markup=_webapp_keyboard(),
                photo_url=cover,
            )

async def free_case_notify_loop():
    """Раз в ~20 мин: кому снова доступен free case — пуш в бота."""
    await asyncio.sleep(15)
    while True:
        try:
            if not BOT_TOKEN:
                await asyncio.sleep(600)
                continue
            now = now_ts()
            async with get_db() as db:
                try:
                    rows = await (await db.execute(
                        "SELECT tg_id, username, free_case_at FROM users WHERE free_case_at > 0"
                    )).fetchall()
                except Exception as e:
                    print("[free_notify] select", e)
                    rows = []
            for row in rows or []:
                try:
                    tid = int(row[0])
                    last = int(row[2] or 0)
                    if last <= 0:
                        continue
                    # окно: кейс доступен (прошло 24ч) и ещё не уведомляли в этом цикле
                    if now - last < 86400:
                        continue
                    if now - last > 86400 + 3600:
                        # слишком давно — не спамим, только «свежие» в первый час после отката
                        continue
                    # флаг в share_claims
                    async with get_db() as db:
                        flag = f"free_notify_{last}"
                        exists = await (await db.execute(
                            "SELECT 1 FROM share_claims WHERE tg_id=? AND case_id=?",
                            (tid, flag),
                        )).fetchone()
                        if exists:
                            continue
                        await db.execute(
                            "INSERT INTO share_claims(tg_id, case_id, created_at) VALUES(?,?,?) ON CONFLICT DO NOTHING",
                            (tid, flag, now),
                        )
                        try:
                            await db.commit()
                        except Exception:
                            pass
                    uname = (row[1] or "друг")
                    text = (
                        f"🆓 <b>{uname}</b>, бесплатный кейс снова доступен!\n"
                        f"Заходи в Win Gifts и открой Free Case 🎁"
                    )
                    cover = FREE_CASE_COVER if FREE_CASE_COVER else None
                    await tg_send(tid, text, reply_markup=_webapp_keyboard(), photo_url=cover)
                    await asyncio.sleep(0.05)
                except Exception as e:
                    print("[free_notify] user", e)
        except Exception as e:
            print("[free_notify] loop", e)
        await asyncio.sleep(1200)  # 20 min


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
    """Слоты: 40%×25 + 65%×23 + 85%×24 + 115%×13 + 140%×7 + 180%×4.5 + 220%×2.5 + 270%×1
    → RTP ≈ 86% (чуть мягче базовой таблицы, касса в плюсе).
    """
    price = max(1, int(price or 1))
    slots = [
        (0.40, 25.0), (0.65, 23.0), (0.85, 24.0), (1.15, 13.0),
        (1.40, 7.0),  (1.80, 4.5),  (2.20, 2.5),  (2.70, 1.0)]
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

    best = None
    best_score = 1e18
    for g in pool:
        v = max(10, int(g.get("value") or 50))
        over = max(0, v - target) * 1.2
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
    'bednyy_shkolnik': [
        {'name': 'Подарок', 'value': 25, 'pct': 22.55},
        {'name': 'Торт', 'value': 50, 'pct': 20.55},
        {'name': 'Ракета', 'value': 50, 'pct': 22.00},
        {'name': 'Кольцо', 'value': 110, 'pct': 12.00},
        {'name': 'Алмаз', 'value': 100, 'pct': 7.00},
        {'name': 'Кубок', 'value': 100, 'pct': 5.00},
        {'name': 'Vice Cream', 'value': 385, 'pct': 2.50},
        {'name': 'Xmas Stocking', 'value': 397, 'pct': 8.40},
    ],
    'reliz': [
        {'name': 'Кольцо', 'value': 110, 'pct': 18.08},
        {'name': 'Алмаз', 'value': 100, 'pct': 17.59},
        {'name': 'Кубок', 'value': 100, 'pct': 21.67},
        {'name': 'Торт', 'value': 50, 'pct': 11.82},
        {'name': 'Vice Cream', 'value': 385, 'pct': 6.90},
        {'name': 'Timeless Bottle', 'value': 462, 'pct': 4.93},
        {'name': 'Input Key', 'value': 606, 'pct': 2.46},
        {'name': 'Evil Eye', 'value': 774, 'pct': 16.55},
    ],
    'fruits_case': [
        {'name': 'Кольцо', 'value': 110, 'pct': 17.50},
        {'name': 'Алмаз', 'value': 100, 'pct': 17.46},
        {'name': 'Кубок', 'value': 100, 'pct': 21.52},
        {'name': 'Торт', 'value': 50, 'pct': 11.74},
        {'name': 'Holiday Drink', 'value': 404, 'pct': 6.85},
        {'name': 'Mousse Cake', 'value': 507, 'pct': 4.89},
        {'name': 'Lush Bouquet', 'value': 658, 'pct': 2.44},
        {'name': 'Bunny Muffin', 'value': 833, 'pct': 17.60},
    ],
    'september_case': [
        {'name': 'Кольцо', 'value': 110, 'pct': 18.95},
        {'name': 'Алмаз', 'value': 100, 'pct': 17.72},
        {'name': 'Кубок', 'value': 100, 'pct': 21.84},
        {'name': 'Vice Cream', 'value': 385, 'pct': 11.91},
        {'name': 'Star Notepad', 'value': 477, 'pct': 6.95},
        {'name': 'Snoop Dogg', 'value': 594, 'pct': 4.96},
        {'name': 'Evil Eye', 'value': 774, 'pct': 2.48},
        {'name': 'Hanging Star', 'value': 932, 'pct': 15.19},
    ],
    'nft_candy': [
        {'name': 'Кольцо', 'value': 110, 'pct': 19.31},
        {'name': 'Алмаз', 'value': 100, 'pct': 17.77},
        {'name': 'Кубок', 'value': 100, 'pct': 21.90},
        {'name': 'Ginger Cookie', 'value': 444, 'pct': 11.95},
        {'name': 'Homemade Cake', 'value': 539, 'pct': 6.97},
        {'name': 'Light Sword', 'value': 685, 'pct': 4.98},
        {'name': 'Fine Pen', 'value': 876, 'pct': 2.49},
        {'name': 'Mask', 'value': 1154, 'pct': 14.63},
    ],
    'vasya_case': [
        {'name': 'Кольцо', 'value': 110, 'pct': 19.31},
        {'name': 'Алмаз', 'value': 100, 'pct': 17.77},
        {'name': 'Кубок', 'value': 100, 'pct': 21.90},
        {'name': 'Ginger Cookie', 'value': 444, 'pct': 11.95},
        {'name': 'Homemade Cake', 'value': 539, 'pct': 6.97},
        {'name': 'Light Sword', 'value': 685, 'pct': 4.98},
        {'name': 'Fine Pen', 'value': 876, 'pct': 2.49},
        {'name': 'Mask', 'value': 1154, 'pct': 14.63},
    ],
    'minecraft_case': [
        {'name': 'Кольцо', 'value': 110, 'pct': 21.50},
        {'name': 'Алмаз', 'value': 100, 'pct': 19.50},
        {'name': 'Vice Cream', 'value': 385, 'pct': 22.00},
        {'name': 'Cookie Heart', 'value': 494, 'pct': 12.00},
        {'name': 'Input Key', 'value': 606, 'pct': 7.00},
        {'name': 'Evil Eye', 'value': 774, 'pct': 5.00},
        {'name': 'Top Hat', 'value': 1038, 'pct': 2.50},
        {'name': 'Flying Broom', 'value': 1263, 'pct': 10.50},
    ],
    'chicken_gun_case': [
        {'name': 'Кольцо', 'value': 110, 'pct': 21.35},
        {'name': 'Алмаз', 'value': 100, 'pct': 19.35},
        {'name': 'Easter Egg', 'value': 426, 'pct': 22.00},
        {'name': 'Liberty Figure', 'value': 552, 'pct': 12.00},
        {'name': 'Light Sword', 'value': 685, 'pct': 7.00},
        {'name': 'Bunny Muffin', 'value': 833, 'pct': 5.00},
        {'name': 'Sakura Flower', 'value': 1075, 'pct': 2.50},
        {'name': 'Mad Pumpkin', 'value': 1437, 'pct': 10.80},
    ],

    'witch_cauldron_case': [
        {'name': 'Кольцо', 'value': 110, 'pct': 22.00},
        {'name': 'Hex Pot', 'value': 497, 'pct': 24.00},
        {'name': 'Witch Hat', 'value': 637, 'pct': 20.00},
        {'name': 'Evil Eye', 'value': 774, 'pct': 14.00},
        {'name': 'Flying Broom', 'value': 1263, 'pct': 10.00},
        {'name': 'Crystal Ball', 'value': 1326, 'pct': 6.00},
        {'name': 'Love Potion', 'value': 1527, 'pct': 3.00},
        {'name': 'Magic Potion', 'value': 5684, 'pct': 1.00},
    ],
    'ghost_pumpkin_case': [
        {'name': 'Кольцо', 'value': 110, 'pct': 21.00},
        {'name': 'Jack-in-the-Box', 'value': 492, 'pct': 22.00},
        {'name': 'Witch Hat', 'value': 637, 'pct': 20.00},
        {'name': 'Skull Flower', 'value': 1242, 'pct': 15.00},
        {'name': 'Mad Pumpkin', 'value': 1437, 'pct': 12.00},
        {'name': 'Trapped Heart', 'value': 1685, 'pct': 6.00},
        {'name': 'Electric Skull', 'value': 2692, 'pct': 3.00},
        {'name': 'Scared Cat', 'value': 23899, 'pct': 1.00},
    ],
    'dracula_crypt_case': [
        {'name': 'Eternal Candle', 'value': 630, 'pct': 24.00},
        {'name': 'Skull Flower', 'value': 1242, 'pct': 22.00},
        {'name': 'Trapped Heart', 'value': 1685, 'pct': 20.00},
        {'name': 'Eternal Rose', 'value': 2626, 'pct': 14.00},
        {'name': 'Electric Skull', 'value': 2692, 'pct': 9.00},
        {'name': 'Coffin', 'value': 3460, 'pct': 6.00},
        {'name': 'Voodoo Doll', 'value': 3891, 'pct': 3.50},
        {'name': 'Gravestone', 'value': 9806, 'pct': 1.50},
    ],
    'reaper_scythe_case': [
        {'name': 'Skull Flower', 'value': 1242, 'pct': 24.00},
        {'name': 'Mad Pumpkin', 'value': 1437, 'pct': 22.00},
        {'name': 'Electric Skull', 'value': 2692, 'pct': 20.00},
        {'name': 'Coffin', 'value': 3460, 'pct': 14.00},
        {'name': 'Voodoo Doll', 'value': 3891, 'pct': 9.00},
        {'name': 'Sharp Tongue', 'value': 4798, 'pct': 6.00},
        {'name': 'Gravestone', 'value': 9806, 'pct': 3.50},
        {'name': 'Scared Cat', 'value': 23899, 'pct': 1.50},
    ],
    'halloween_case': [
        {'name': 'Кольцо', 'value': 110, 'pct': 23.15},
        {'name': 'Vice Cream', 'value': 385, 'pct': 21.15},
        {'name': 'Timeless Bottle', 'value': 462, 'pct': 22.00},
        {'name': 'Input Key', 'value': 606, 'pct': 12.00},
        {'name': 'Jolly Chimp', 'value': 750, 'pct': 7.00},
        {'name': 'Hanging Star', 'value': 932, 'pct': 5.00},
        {'name': 'Love Candle', 'value': 1199, 'pct': 2.50},
        {'name': 'Love Potion', 'value': 1527, 'pct': 7.20},
    ],
    'rocket_case': [
        {'name': 'Кольцо', 'value': 110, 'pct': 23.15},
        {'name': 'Vice Cream', 'value': 385, 'pct': 21.15},
        {'name': 'Timeless Bottle', 'value': 462, 'pct': 22.00},
        {'name': 'Input Key', 'value': 606, 'pct': 12.00},
        {'name': 'Jolly Chimp', 'value': 750, 'pct': 7.00},
        {'name': 'Hanging Star', 'value': 932, 'pct': 5.00},
        {'name': 'Love Candle', 'value': 1199, 'pct': 2.50},
        {'name': 'Love Potion', 'value': 1527, 'pct': 7.20},
    ],
    'nft_pepe': [
        {'name': 'Кольцо', 'value': 110, 'pct': 22.55},
        {'name': 'Hypno Lollipop', 'value': 421, 'pct': 20.55},
        {'name': 'Liberty Figure', 'value': 552, 'pct': 22.00},
        {'name': 'Surge Board', 'value': 694, 'pct': 12.00},
        {'name': 'Fine Pen', 'value': 876, 'pct': 7.00},
        {'name': 'Sakura Flower', 'value': 1075, 'pct': 5.00},
        {'name': 'Mad Pumpkin', 'value': 1437, 'pct': 2.50},
        {'name': 'Lucky Locket', 'value': 1795, 'pct': 8.40},
    ],
    'pythagoras_case': [
        {'name': 'Кольцо', 'value': 110, 'pct': 22.55},
        {'name': 'Mirage Lamp', 'value': 488, 'pct': 20.55},
        {'name': 'Witch Hat', 'value': 637, 'pct': 22.00},
        {'name': 'Bunny Muffin', 'value': 833, 'pct': 12.00},
        {'name': 'Top Hat', 'value': 1038, 'pct': 7.00},
        {'name': 'Flying Broom', 'value': 1263, 'pct': 5.00},
        {'name': 'Snoop Cigar', 'value': 1618, 'pct': 2.50},
        {'name': 'Sky Stilettos', 'value': 2011, 'pct': 8.40},
    ],
    'brand_snoop': [
        {'name': 'Кольцо', 'value': 110, 'pct': 22.85},
        {'name': 'Bow Tie', 'value': 522, 'pct': 20.85},
        {'name': 'Light Sword', 'value': 685, 'pct': 22.00},
        {'name': 'Fine Pen', 'value': 876, 'pct': 12.00},
        {'name': 'Sakura Flower', 'value': 1075, 'pct': 7.00},
        {'name': 'Crystal Ball', 'value': 1326, 'pct': 5.00},
        {'name': 'Lucky Locket', 'value': 1795, 'pct': 2.50},
        {'name': 'Cupid Charm', 'value': 2300, 'pct': 7.80},
    ],
    'angel_case': [
        {'name': 'Vice Cream', 'value': 385, 'pct': 24.35},
        {'name': 'Faith Amulet', 'value': 585, 'pct': 22.35},
        {'name': 'Evil Eye', 'value': 774, 'pct': 22.00},
        {'name': 'Top Hat', 'value': 1038, 'pct': 12.00},
        {'name': 'Love Candle', 'value': 1199, 'pct': 7.00},
        {'name': 'Love Potion', 'value': 1527, 'pct': 5.00},
        {'name': 'Sky Stilettos', 'value': 2011, 'pct': 2.50},
        {'name': 'Bling Binky', 'value': 2524, 'pct': 4.80},
    ],
    'gift_upg_case': [
        {'name': 'Vice Cream', 'value': 385, 'pct': 24.35},
        {'name': 'Faith Amulet', 'value': 585, 'pct': 22.35},
        {'name': 'Evil Eye', 'value': 774, 'pct': 22.00},
        {'name': 'Top Hat', 'value': 1038, 'pct': 12.00},
        {'name': 'Love Candle', 'value': 1199, 'pct': 7.00},
        {'name': 'Love Potion', 'value': 1527, 'pct': 5.00},
        {'name': 'Sky Stilettos', 'value': 2011, 'pct': 2.50},
        {'name': 'Bling Binky', 'value': 2524, 'pct': 4.80},
    ],
    'nft_magic': [
        {'name': 'Big Year', 'value': 399, 'pct': 23.75},
        {'name': 'Lush Bouquet', 'value': 658, 'pct': 21.75},
        {'name': 'Bunny Muffin', 'value': 833, 'pct': 22.00},
        {'name': 'Sakura Flower', 'value': 1075, 'pct': 12.00},
        {'name': 'Crystal Ball', 'value': 1326, 'pct': 7.00},
        {'name': 'Trapped Heart', 'value': 1685, 'pct': 5.00},
        {'name': 'Cupid Charm', 'value': 2300, 'pct': 2.50},
        {'name': 'Electric Skull', 'value': 2692, 'pct': 6.00},
    ],
    'jeremy_case': [
        {'name': 'Star Notepad', 'value': 477, 'pct': 24.05},
        {'name': 'Evil Eye', 'value': 774, 'pct': 22.05},
        {'name': 'Top Hat', 'value': 1038, 'pct': 22.00},
        {'name': 'Crystal Ball', 'value': 1326, 'pct': 12.00},
        {'name': 'Snoop Cigar', 'value': 1618, 'pct': 7.00},
        {'name': 'Sky Stilettos', 'value': 2011, 'pct': 5.00},
        {'name': 'Eternal Rose', 'value': 2626, 'pct': 2.50},
        {'name': 'Signet Ring', 'value': 3398, 'pct': 5.40},
    ],
    'cr7_case': [
        {'name': 'Witch Hat', 'value': 637, 'pct': 23.90},
        {'name': 'Top Hat', 'value': 1038, 'pct': 21.90},
        {'name': 'Crystal Ball', 'value': 1326, 'pct': 22.00},
        {'name': 'Lucky Locket', 'value': 1795, 'pct': 12.00},
        {'name': 'Cupid Charm', 'value': 2300, 'pct': 7.00},
        {'name': 'Electric Skull', 'value': 2692, 'pct': 5.00},
        {'name': 'Toy Bear', 'value': 3507, 'pct': 2.50},
        {'name': 'Bonded Ring', 'value': 4404, 'pct': 5.70},
    ],
    'only_onyx': [
        {'name': 'Light Sword', 'value': 685, 'pct': 23.90},
        {'name': 'Sakura Flower', 'value': 1075, 'pct': 21.90},
        {'name': 'Mad Pumpkin', 'value': 1437, 'pct': 22.00},
        {'name': 'Lucky Locket', 'value': 1795, 'pct': 12.00},
        {'name': 'Cupid Charm', 'value': 2300, 'pct': 7.00},
        {'name': 'Rare Bird', 'value': 2990, 'pct': 5.00},
        {'name': 'Voodoo Doll', 'value': 3891, 'pct': 2.50},
        {'name': 'Sharp Tongue', 'value': 4798, 'pct': 5.70},
    ],
    'elite_case': [
        {'name': 'Surge Board', 'value': 694, 'pct': 23.75},
        {'name': 'Valentine Box', 'value': 1164, 'pct': 21.75},
        {'name': 'Love Potion', 'value': 1527, 'pct': 22.00},
        {'name': 'Sky Stilettos', 'value': 2011, 'pct': 12.00},
        {'name': 'Bling Binky', 'value': 2524, 'pct': 7.00},
        {'name': 'Rare Bird', 'value': 2990, 'pct': 5.00},
        {'name': 'Vintage Cigar', 'value': 3905, 'pct': 2.50},
        {'name': 'Sharp Tongue', 'value': 4798, 'pct': 6.00},
    ],
    'bogach': [
        {'name': 'Top Hat', 'value': 1038, 'pct': 23.75},
        {'name': 'Snoop Cigar', 'value': 1618, 'pct': 21.75},
        {'name': 'Sky Stilettos', 'value': 2011, 'pct': 22.00},
        {'name': 'Electric Skull', 'value': 2692, 'pct': 12.00},
        {'name': 'Signet Ring', 'value': 3398, 'pct': 7.00},
        {'name': 'Kissed Frog', 'value': 4151, 'pct': 5.00},
        {'name': 'Magic Potion', 'value': 5684, 'pct': 2.50},
        {'name': 'Ion Gem', 'value': 7012, 'pct': 6.00},
    ],
    'telegram_case': [
        {'name': 'Ionic Dryer', 'value': 1515, 'pct': 23.90},
        {'name': 'Bling Binky', 'value': 2524, 'pct': 21.90},
        {'name': 'Diamond Ring', 'value': 3288, 'pct': 22.00},
        {'name': 'Kissed Frog', 'value': 4151, 'pct': 12.00},
        {'name': 'Sharp Tongue', 'value': 4798, 'pct': 7.00},
        {'name': 'Low Rider', 'value': 6279, 'pct': 5.00},
        {'name': 'Trojan Horse', 'value': 7854, 'pct': 2.50},
        {'name': 'Gravestone', 'value': 9806, 'pct': 5.70},
    ],
    'only_durov': [
        {'name': 'Cupid Charm', 'value': 2300, 'pct': 23.60},
        {'name': 'Toy Bear', 'value': 3507, 'pct': 21.60},
        {'name': 'Sharp Tongue', 'value': 4798, 'pct': 22.00},
        {'name': 'Swiss Watch', 'value': 5829, 'pct': 12.00},
        {'name': 'Mini Oscar', 'value': 7327, 'pct': 7.00},
        {'name': 'Gravestone', 'value': 9806, 'pct': 5.00},
        {'name': 'Astral Shard', 'value': 11957, 'pct': 2.50},
        {'name': 'Mighty Arm', 'value': 13452, 'pct': 6.30},
    ],
    'free_daily': [
        {'stars': 1, 'pct': 40.0},
        {'stars': 2, 'pct': 30.0},
        {'stars': 3, 'pct': 18.0},
        {'stars': 4, 'pct': 8.0},
        {'stars': 5, 'pct': 4.0},
    ],
    'promo_case': [
        {'stars': 1, 'pct': 42.0},
        {'stars': 2, 'pct': 30.0},
        {'stars': 3, 'pct': 18.0},
        {'stars': 4, 'pct': 7.0},
        {'stars': 5, 'pct': 3.0},
    ],
}


def real_nft_pool(min_value: int = 500) -> list:
    """Только реальные коллекционные подарки (не shop-common и не фейки)."""
    ban = {
        "Spy Agaric", "Sticker Pack", "Freedom Pin", "Blue Heart", "Chat Bubble",
        
        "Surge Board",
        "Мишка", "Сердце", "Конфета", "Подарок", "Звезда", "Торт", "Ракета",
        "Букет", "Ёлка", "Шампанское", "Цветы", "Алмаз", "Кубок", "Кольцо",
    }
    out = []
    for g in GIFTS_FLAT:
        name = g.get("name") or ""
        if name in ban:
            continue
        v = int(g.get("value") or 0)
        if v < min_value:
            continue
        rar = (g.get("rarity") or "").lower()
        # Common только если value высокий (реальные коллекционки)
        if rar == "common" and v < 500:
            continue
        out.append(g)
    return out or [g for g in GIFTS_FLAT if int(g.get("value") or 0) >= min_value]

def roll_case_drop(case_id: str, c: dict, stake: int = 0) -> dict:
    """Ролл по DROP_TABLES. Free/promo — слабый дроп 1–5⭐.
    All In: ставка min(баланс, 100 TON); дроп от ставки, иногда слив, RTP ≈ 88%.
    """
    price = int((c or {}).get("price") or 0)
    cid = (case_id or "").strip()
    # ---- ALL IN ----
    if cid == "allin_case" or (c or {}).get("allin") or (c or {}).get("category") == "allin":
        S = max(10, int(stake or price or 0))
        # лимит на всякий
        try:
            S = min(S, int(round(100.0 * float(STARS_PER_TON))))  # All In cap 100 TON
        except Exception:
            S = min(S, 11000)
        # RTP ~87%: чаще слив, редко 2×+
        slots = [
            (0.25, 24.0),
            (0.40, 20.0),
            (0.60, 16.0),
            (0.85, 14.0),
            (1.00, 12.0),
            (1.30,  8.0),
            (1.80,  4.0),
            (2.80,  1.5),
            (3.50,  0.5)]  # EV ≈ 0.87
        tw = sum(w for _, w in slots)
        r = random.random() * tw
        acc = 0.0
        mult = slots[0][0]
        for mlt, w in slots:
            acc += w
            if r <= acc:
                mult = mlt
                break
        target = max(10, int(round(S * mult)))
        try:
            pool = real_nft_pool(min_value=max(20, int(S * 0.15)))
        except Exception:
            pool = [g for g in GIFTS_FLAT if int(g.get("value") or 0) >= max(20, int(S * 0.15))]
        if not pool:
            pool = list(GIFTS_FLAT or [])
        cand = [g for g in pool if target * 0.80 <= int(g.get("value") or 0) <= target * 1.15]
        if len(cand) < 2:
            cand = [g for g in pool if target * 0.70 <= int(g.get("value") or 0) <= target * 1.25]
        if not cand:
            return {"kind": "stars", "stars": int(target), "allin_lose": target < S, "allin_mult": mult, "allin_stake": S}
        if cand:
            weights = []
            for g in cand:
                d = abs(int(g.get("value") or 0) - target)
                weights.append(1.0 / (1.0 + d / max(30.0, float(target) * 0.1)))
            tw2 = sum(weights) or 1.0
            rr = random.random() * tw2
            a2 = 0.0
            pick = cand[0]
            for g, w in zip(cand, weights):
                a2 += w
                if rr <= a2:
                    pick = g
                    break
            try:
                out = gift_public(dict(pick))
            except Exception:
                out = {
                    "name": pick.get("name") or "Gift",
                    "value": int(pick.get("value") or target),
                    "rarity": pick.get("rarity") or "Rare",
                    "sn": pick.get("sn") or "",
                    "emoji": pick.get("emoji") or "🎁",
                    "img": pick.get("img") or "",
                }
            if not int(out.get("value") or 0):
                out["value"] = int(pick.get("value") or target)
            if not out.get("img"):
                try:
                    out["img"] = gift_img_url(out.get("name") or "Gift", out.get("sn"))
                except Exception:
                    pass
            return {
                "kind": "gift",
                "gift": out,
                "allin_lose": int(out.get("value") or 0) < S,
                "allin_mult": mult,
                "allin_stake": S,
            }
        return {
            "kind": "stars",
            "stars": target,
            "allin_lose": target < S,
            "allin_mult": mult,
            "allin_stake": S,
        }

    # free / promo
    if price <= 0 or (c or {}).get("category") in ("free", "promo") or cid in ("free_daily", "promo_case"):
        stars = random.randint(1, 5)
        return {"kind": "stars", "stars": stars}

    table = DROP_TABLES.get(cid) or DROP_TABLES.get((c or {}).get("id") or "")
    if not table:
        # fallback: слоты по структуре
        return _pick_gift_for_case(list(GIFTS_FLAT), price)

    # weighted by pct
    weights = [float(row.get("pct") or 0) for row in table]
    total = sum(weights) or 1.0
    r = random.random() * total
    acc = 0.0
    chosen = table[-1]
    for row, w in zip(table, weights):
        acc += w
        if r <= acc:
            chosen = row
            break

    if "name" in chosen and chosen.get("name"):
        name = chosen["name"]
        value = int(chosen.get("value") or 0)
        # try exact from catalog
        gift = None
        for g in GIFTS_FLAT:
            if g.get("name") == name:
                gift = dict(g)
                # цена всегда из каталога (Fragment), не из устаревшей таблицы
                break
        if not gift:
            gift = {"name": name, "value": value or max(1, int(price * 0.4)), "rarity": "Rare", "sn": "gift"}
        return {"kind": "gift", "gift": gift_public(gift)}
    stars = int(chosen.get("stars") or max(1, int(price * 0.4)))
    return {"kind": "stars", "stars": stars}



class CaseOpenRequest(BaseModel):
    case_id: str = ""
    promo: Optional[str] = None
    promo_code: Optional[str] = None

class UpgradeRequest(BaseModel):
    item_index: int
    target_value: int
    target_name: Optional[str] = None
    mode: Optional[str] = None
    stake: int = 0  # доплата звёздами к шансу (слайдер «Ставка»); item_index=-1 → апгрейд только за TON
    target_number: int = 0  # какую модель цели видел игрок — ту же и получит
    item_ids: List[str] = []  # несколько подарков в один апгрейд (до 6)

class SellItemRequest(BaseModel):
    item_index: int

class ShopBuyRequest(BaseModel):
    name: str

class MinesStartRequest(BaseModel):
    bet: int = 0
    mines: int = 3
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

# ----- FastAPI app + Socket.IO -----
HTML_PATH = pathlib.Path(__file__).resolve().parent / "index.html"


app = FastAPI(title="GiftUpgrader")
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1024)  # сжатие index.html и API — грузится быстрее

@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    # Всегда JSON + CORS — иначе WebView пишет Failed to fetch / «Нет ответа API»
    hdrs = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD",
    }
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=hdrs)
    import traceback
    print("[ERR]", request.url.path, traceback.format_exc())
    return JSONResponse(status_code=500, content={"detail": "server error: " + str(exc)[:240]}, headers=hdrs)


@app.middleware("http")
async def maintenance_middleware(request: Request, call_next):
    path = request.url.path or ""
    allow = (
        path in ("/api/health", "/api/maintenance", "/api/rates", "/tonconnect-manifest.json")
        or path.startswith("/admin")
        or path.startswith("/api/admin")
        or path == "/"
        or path.endswith((".html", ".js", ".css", ".webp", ".png", ".jpg", ".ico", ".json"))
    )
    if allow or not MAINTENANCE_MODE.get("on"):
        return await call_next(request)
    is_adm = False
    try:
        # раньше пропускало по заголовку X-Admin-Id — любой, кто знал ID админа, обходил тех.работы
        if not is_adm:
            raw = (
                request.headers.get("X-Telegram-Init-Data")
                or request.headers.get("authorization")
                or request.headers.get("Authorization")
                or ""
            )
            if raw and raw != "dev":
                try:
                    info = _parse_init_data(raw)
                except Exception:
                    info = None
                if info and int(info.get("id") or info.get("tg_id") or 0) == int(ADMIN_TG_ID):
                    is_adm = True
            elif raw == "dev" and int(ADMIN_TG_ID) > 0:
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
    allow_origins=["*"],
    allow_origin_regex=".*",
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=86400,
)


@app.middleware("http")
async def _cors_private_network(request: Request, call_next):
    # Chrome Private Network Access / Telegram WebView preflight
    if request.method == "OPTIONS":
        resp = JSONResponse(content={"ok": True})
        resp.headers["Access-Control-Allow-Origin"] = request.headers.get("origin") or "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD"
        resp.headers["Access-Control-Allow-Headers"] = request.headers.get("access-control-request-headers") or "*"
        resp.headers["Access-Control-Allow-Private-Network"] = "true"
        resp.headers["Access-Control-Max-Age"] = "86400"
        return resp
    response = await call_next(request)
    try:
        origin = request.headers.get("origin")
        if origin:
            response.headers.setdefault("Access-Control-Allow-Origin", origin)
        else:
            response.headers.setdefault("Access-Control-Allow-Origin", "*")
        response.headers.setdefault("Access-Control-Allow-Private-Network", "true")
    except Exception:
        pass
    return response


@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    """Webhook бота: /start и команды."""
    try:
        body = await request.json()
    except Exception:
        return {"ok": True}
    if not _hook_ok(request):
        raise HTTPException(403, "bad secret")
    asyncio.create_task(_tg_update_bg(body))   # Telegram получает ответ сразу — бот не «думает»
    return {"ok": True}


async def _tg_update_bg(body: dict):
    try:
        await _tg_update_process(body)
    except Exception as e:
        print("[tg update]", e)


async def _tg_update_process(body: dict):
    try:
        _from = (((body or {}).get("message") or {}).get("from") or {}).get("id")
        if _from and int(_from) in BANNED and int(_from) != int(ADMIN_TG_ID or 0):
            if str(((body or {}).get("message") or {}).get("text") or "").startswith("/"):
                await _tg_notify_sync(int(_from), "⛔ Твой аккаунт заблокирован. Если это ошибка — напиши @" + SUPPORT_USERNAME)
            return {"ok": True}
    except Exception:
        pass
    bm = (body or {}).get("business_message")
    if bm:
        await handle_business_gift(bm)
        return {"ok": True}
    if (body or {}).get("business_connection"):
        try:
            bc = body["business_connection"]
            print("[business] connection", bc.get("user", {}).get("username"), "enabled=", bc.get("is_enabled"))
        except Exception:
            pass
        return {"ok": True}
    if await _tg_payment_update(body):
        return {"ok": True}
    msg = (body or {}).get("message") or (body or {}).get("edited_message") or {}
    chat = msg.get("chat") or {}
    tg_id = chat.get("id") or (msg.get("from") or {}).get("id")
    text = (msg.get("text") or "").strip()
    from_user = msg.get("from") or {}
    name = from_user.get("first_name") or from_user.get("username") or ""
    if not tg_id:
        return {"ok": True}
    low = text.lower()
    lang_code = str(from_user.get("language_code") or "")
    if text.startswith("/") and from_user.get("id"):
        try:
            if await admin_bot_command(int(from_user["id"]), int(tg_id), text):
                return {"ok": True}
        except Exception as e:
            print("[admin cmd]", e)
    if low.startswith("/start"):
        parts = text.split(maxsplit=1)
        if len(parts) > 1 and parts[1].strip():
            try:
                await start_payload_save(int(tg_id), parts[1].strip())
            except Exception as e:
                print("[start payload]", e)
    if low.startswith("/start") or low in ("start", "старт", "/старт"):
        await handle_bot_start(int(tg_id), name, lang_code)
    elif low in ("/free", "free", "кейс"):
        await handle_bot_start(int(tg_id), name)
    elif low in ("/app", "app", "меню"):
        await tg_send(int(tg_id), "🎁 <b>Win Gifts</b> — открыть мини-апп:", reply_markup=_webapp_keyboard())
    return {"ok": True}


@app.on_event("startup")
async def _startup():
    try:
        await init_db()
    except Exception as e:
        print("[startup] init_db:", e)
    try:
        await _ensure_demo_top_user()
    except Exception as e:
        print("[startup] seed top:", e)
    try:
        asyncio.create_task(crash_loop())
    except Exception as e:
        print("[startup] crash_loop:", e)
    try:
        asyncio.create_task(free_case_notify_loop())
    except Exception as e:
        print("[startup] free_notify:", e)

_HTML_CACHE = {"mt": 0.0, "txt": "", "path": "", "remote_ts": 0}

def _find_index_html():
    """Ищем фронт где угодно: рядом с main.py, в рабочей папке, в static/public/frontend,
    и даже если файл называется 'index (19).html' / 'index__19_.html'."""
    base = pathlib.Path(__file__).resolve().parent
    dirs = [base, pathlib.Path.cwd(), base / "static", base / "public", base / "frontend", base.parent]
    for d in dirs:
        f = d / "index.html"
        if f.exists():
            return f
    for d in dirs:
        try:
            cands = sorted([x for x in d.glob("*.html") if x.is_file()], key=lambda x: -x.stat().st_size)
        except Exception:
            cands = []
        cands = [x for x in cands if "index" in x.name.lower()] or cands
        if cands and cands[0].stat().st_size > 50_000:
            return cands[0]
    return None

_ASSETS: Dict[str, tuple] = {}   # hash -> (mime, bytes)

def _prepare_html(txt: str) -> None:
    """Вынимаем большие картинки base64 из index.html в отдельные кешируемые файлы /a/<hash>.
    HTML худеет с ~4.5 МБ до ~1 МБ, а картинки браузер берёт из кеша при следующих заходах."""
    import base64, gzip, hashlib
    pat = re.compile(r"data:(image/[a-z0-9.+-]+|font/[a-z0-9.+-]+|audio/[a-z0-9.+-]+);base64,([A-Za-z0-9+/=]{6000,})")
    def sub(m):
        mime, b64 = m.group(1), m.group(2)
        h = hashlib.sha1(b64[:4096].encode() + str(len(b64)).encode()).hexdigest()[:16]
        if h not in _ASSETS:
            try:
                _ASSETS[h] = (mime, base64.b64decode(b64))
            except Exception:
                return m.group(0)
        return "/a/" + h
    slim = pat.sub(sub, txt)
    _HTML_CACHE["txt"] = slim
    _HTML_CACHE["gz"] = gzip.compress(slim.encode("utf-8"), 6)
    _HTML_CACHE["etag"] = '"' + hashlib.md5(_HTML_CACHE["gz"]).hexdigest()[:20] + '"'
    print(f"[frontend] html {round(len(txt)/1e6,2)}MB → {round(len(slim)/1e6,2)}MB, gzip {round(len(_HTML_CACHE['gz'])/1e6,2)}MB, assets {len(_ASSETS)}")

def _serve_html(request: Request):
    from fastapi.responses import Response
    et = _HTML_CACHE.get("etag") or ""
    if et and request.headers.get("if-none-match") == et:
        return Response(status_code=304, headers={"ETag": et, "Cache-Control": "no-cache"})
    if "gzip" in (request.headers.get("accept-encoding") or "") and _HTML_CACHE.get("gz"):
        return Response(_HTML_CACHE["gz"], media_type="text/html; charset=utf-8",
                        headers={"Content-Encoding": "gzip", "ETag": et, "Cache-Control": "no-cache", "Vary": "Accept-Encoding"})
    return HTMLResponse(_HTML_CACHE["txt"], headers={"ETag": et, "Cache-Control": "no-cache"})

@app.get("/a/{h}")
async def asset(h: str):
    from fastapi.responses import Response
    a = _ASSETS.get(str(h).split(".")[0])
    if not a:
        raise HTTPException(404, "asset")
    return Response(a[1], media_type=a[0], headers={"Cache-Control": "public, max-age=31536000, immutable"})

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    # фронт прямо на Render (без баннера Netlify)
    f = _find_index_html()
    if f:
        mt = f.stat().st_mtime
        if mt != _HTML_CACHE["mt"] or str(f) != _HTML_CACHE["path"]:
            _prepare_html(f.read_text(encoding="utf-8"))
            _HTML_CACHE["mt"] = mt
            _HTML_CACHE["path"] = str(f)
        return _serve_html(request)
    # запасной вариант: подтянуть фронт с FRONTEND_URL (например старый Netlify) и отдать с Render
    furl = (os.getenv("FRONTEND_URL") or "").strip()
    if furl:
        if not _HTML_CACHE["txt"] or now_ts() - _HTML_CACHE["remote_ts"] > 600:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as c:
                    r = await c.get(furl)
                if r.status_code == 200 and len(r.text) > 50_000:
                    _prepare_html(r.text)
                    _HTML_CACHE["remote_ts"] = now_ts()
            except Exception as e:
                print("[frontend fetch]", e)
        if _HTML_CACHE["txt"]:
            return _serve_html(request)
    base = pathlib.Path(__file__).resolve().parent
    try:
        files = ", ".join(sorted(x.name for x in base.iterdir())[:40])
    except Exception:
        files = "?"
    return HTMLResponse(
        "<meta name=viewport content='width=device-width'><body style='font-family:sans-serif;background:#120e24;color:#eee;padding:20px;line-height:1.5'>"
        "<h2>Фронт не найден</h2><p>Положи <b>index.html</b> в репозиторий <b>в ту же папку, что main.py</b>, и сделай деплой.</p>"
        f"<p style='opacity:.7;font-size:13px'>Папка сервера: {base}<br>Файлы в ней: {files}</p>"
        "<p style='opacity:.7;font-size:13px'>Или задай на Render переменную FRONTEND_URL = ссылка на сайт с фронтом.</p></body>")

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

@app.get("/api/maintenance")
async def maintenance_status():
    return {"on": bool(MAINTENANCE_MODE.get("on")), "message": MAINTENANCE_MODE.get("message") or ""}

@app.get("/api/rates")
async def rates():
    return {"stars_per_ton": TON_STARS_PER_TON, "treasury": TON_TREASURY}

@app.get("/api/ton/config")
async def ton_config():
    mode = (os.getenv("TON_DEPOSIT_MODE") or "prod").strip()
    return {"treasury": TON_TREASURY, "stars_per_ton": TON_STARS_PER_TON, "mode": mode}

@app.get("/tonconnect-manifest.json")
async def ton_manifest():
    return {
        "url": os.getenv("PUBLIC_URL") or "https://t.me",
        "name": "GiftUpgrader",
        "iconUrl": "https://ton.org/download/ton_symbol.png",
    }

@app.get("/api/gifts")
async def get_gifts():
    try:
        return {"gifts": gifts_grouped()}
    except Exception as e:
        print("[gifts]", e)
        return {"gifts": {"Common": [], "Rare": []}}

@app.get("/api/cases")
async def get_cases():
    out = {}
    for k, v in CASES.items():
        if k in DISABLED_CASES:
            continue
        out[k] = {
            "id": k,
            "name": v.get("name"),
            "price": v.get("price"),
            "price_ton": v.get("price_ton"),
            "category": v.get("category"),
            "cover": v.get("cover"),
            "desc": v.get("desc"),
            "coming_soon": bool(v.get("coming_soon")),
            "allin": bool(v.get("allin")),
        }
    return out

@app.get("/api/case/{case_id}/contents")
async def case_contents(case_id: str):
    cid = (case_id or "").strip()
    # soft alias
    ALIAS = {
        "vasya": "vasya_case", "vasya_case": "vasya_case",
        "rocket": "rocket_case", "rocket_case": "rocket_case",
        "pythagoras": "pythagoras_case", "pythagoras_case": "pythagoras_case",
        "chicken": "chicken_gun_case", "chicken_gun_case": "chicken_gun_case",
        "gift_upg": "gift_upg_case", "gift_upg_case": "gift_upg_case",
        "cr7": "cr7_case", "cr7_case": "cr7_case",
        "allin": "allin_case", "allin_case": "allin_case",
        "free": "free_daily", "free_daily": "free_daily",
        "promo": "promo_case", "promo_case": "promo_case",
    }
    key = cid.lower().replace("-", "_").replace(" ", "_")
    cid = ALIAS.get(cid) or ALIAS.get(key) or key
    c = CASES.get(cid)
    if not c:
        for k, v in CASES.items():
            if k == key or (v.get("cover") or "") == key.replace("_case", ""):
                cid, c = k, v
                break
    if not c:
        return {"items": [], "case_id": case_id}
    # All In — пример слотов от ставки (фронт тоже считает от баланса)
    if cid == "allin_case" or (c or {}).get("allin") or (c or {}).get("category") == "allin":
        tiers = [
            (0.30, 28.0), (0.45, 24.0), (0.70, 18.0), (1.00, 12.0),
            (1.50, 9.0), (2.20, 5.5), (2.80, 2.5), (3.50, 1.0)]
        # пример от price кейса (реальная ставка считается на клиенте / при open)
        base = max(int((c or {}).get("price") or 51), 51)
        try:
            base = min(base, int(round(500.0 * float(STARS_PER_TON))))
        except Exception:
            base = min(base, 11000)
        items = []
        for mlt, pct in tiers:
            items.append({
                "name": f"×{mlt:.2f} от ставки",
                "value": max(1, int(round(base * mlt))),
                "rarity": "Legendary" if mlt >= 2 else ("Rare" if mlt >= 1 else "Common"),
                "emoji": "💠",
                "img": "",
                "drop_chance": pct,
                "allin_mult": mlt,
                "is_ton": True,
            })
        return {"items": items, "case_id": cid, "allin": True}
    items = []
    table = DROP_TABLES.get(cid) or []
    for row in table:
        if row.get("name"):
            g = find_gift(row["name"]) or {"name": row["name"], "value": row.get("value") or 0, "rarity": "Rare", "sn": "gift"}
            if row.get("value"):
                g = dict(g)
                g["value"] = int(row["value"])
            try:
                gp = gift_public(g)
            except Exception:
                gp = g
            gp["pct"] = row.get("pct")
            items.append(gp)
        elif row.get("stars"):
            items.append({"name": f"{row['stars']}⭐", "value": int(row["stars"]), "stars": int(row["stars"]), "pct": row.get("pct"), "emoji": "⭐"})
        elif row.get("nothing"):
            items.append({"name": "Nothing", "value": 0, "pct": row.get("pct"), "sn": "ton"})
    # force_names bait for allin
    if c.get("force_names") and not items:
        for nm in c.get("force_names") or []:
            g = find_gift(nm)
            if g:
                try:
                    items.append(gift_public(g))
                except Exception:
                    items.append(g)
    return {"items": items, "case_id": cid, "name": c.get("name"), "price": c.get("price")}

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
    free_ok = (now_ts() - int(u.get("free_case_at") or 0)) >= 86400
    return {
        "tg_id": u["tg_id"],
        "username": u.get("username"),
        "balance": int(u.get("balance") or 0),
        "inventory": u.get("inventory") or [],
        "games_played": int(u.get("games") or 0),
        "wins": int(u.get("wins") or 0),
        "deposited": int(u.get("deposited") or 0),
        "free_case_available": free_ok,
        "is_admin": bool(u.get("is_admin")),
        "photo_url": u.get("photo_url") or await fetch_tg_photo_url(int(u.get("tg_id") or 0)),
        "chance_bonus": 0.0 if _bonus_off(u.get("tg_id") or 0, "upgrade") else float(u.get("chance_bonus") or 0),
        "cases_opened": int(u.get("cases_opened") or 0),
        "ton_wallet": u.get("ton_wallet") or "",
    }

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
        it = {**g, "price": shop_price(g)}
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
    stake = max(0, int(body.stake or 0))
    item = None
    multi_idx: List[int] = []
    if body.item_ids and len(body.item_ids) > 1:
        ids = [str(x) for x in body.item_ids][:6]
        for iid in ids:
            hit = next((k for k, x in enumerate(u["inventory"]) if str(x.get("id") or "") == iid and k not in multi_idx), None)
            if hit is None:
                raise HTTPException(400, "Один из подарков не найден — обнови инвентарь")
            x = u["inventory"][hit]
            if x.get("rental") or x.get("playable") is False:
                raise HTTPException(400, "Аренду нельзя апгрейдить")
            multi_idx.append(hit)
        iv = sum(int(u["inventory"][k].get("value") or 0) for k in multi_idx)
        item = {"name": f"{len(multi_idx)} подарка", "value": iv}
    elif body.item_index == -1 and stake > 0:
        iv = 0
    else:
        if body.item_index < 0 or body.item_index >= len(u["inventory"]):
            raise HTTPException(400, "Item not found")
        item = u["inventory"][body.item_index]
        if item.get("rental") or item.get("playable") is False:
            raise HTTPException(400, "Аренду нельзя апгрейдить")
        iv = int(item.get("value") or 0)
    if stake > int(u.get("balance") or 0):
        raise HTTPException(400, "Недостаточно средств для ставки")
    tv = int(body.target_value)
    _t0 = find_gift(body.target_name or "") if body.target_name else None
    if _t0:
        tv = upgrade_target_value(_t0)          # сервер сам считает цену цели (+2 TON для NFT), клиенту не верим
    eff = iv + stake
    if eff <= 0:
        raise HTTPException(400, "Выбери подарок или поставь TON")
    if eff >= tv:
        raise HTTPException(400, "Цель должна быть дороже")
    if tv > eff * UPG_MAX_X:
        raise HTTPException(400, f"Максимум ×{UPG_MAX_X:g}: выбери цель подешевле или добавь подарков/TON")
    u["balance"] = int(u["balance"]) - stake
    await hw_track(u["tg_id"], "wager", eff)
    target = _t0
    if not target:
        # closest by value (клиент присылает цену с наценкой — ищем по «сырой»)
        _rv = lambda g: int(g["value"])
        cands = [g for g in (gift_public(x) for x in GIFTS_FLAT) if _rv(g) == tv] or \
                [g for g in (gift_public(x) for x in GIFTS_FLAT) if abs(_rv(g) - tv) < max(10, tv * 0.05)]
        target = cands[0] if cands else gift_public({"name": body.target_name or "Target", "value": tv, "rarity": "Rare"})
    chance = calc_upgrade_chance(eff, tv, 0.0 if _bonus_off(u["tg_id"], "upgrade") else float(u.get("chance_bonus") or 0) + await temp_boost(u["tg_id"]))
    # Lucky-монета: маленький золотой кусок в начале зоны выигрыша (внизу колеса).
    # Попал в неё — цель выдаётся ×2 (два NFT). Размер = 5% от зоны выигрыша (+~4% к RTP).
    rnd = UP_ROUND.pop(int(u["tg_id"]), None)
    lucky_on = bool((body.mode or "") == "wheel" and not _bonus_off(u["tg_id"], "upgrade") and rnd and rnd.get("lucky") and now_ts() - int(rnd.get("ts") or 0) < 3600)
    lucky_chance = round(chance * UPGRADE_LUCKY_SHARE, 3) if lucky_on else 0.0
    roll = random.random() * 100.0
    success = roll < chance
    lucky = roll < lucky_chance
    if is_rigged(u["tg_id"], "upgrade"):  # anti-abuse: апгрейды не заходят
        success = False
        lucky = False
    if multi_idx:
        for k in sorted(multi_idx, reverse=True):
            u["inventory"].pop(k)
    elif item is not None:
        u["inventory"].pop(body.item_index)
    item = item or {"name": f"{round(stake / float(STARS_PER_TON), 2)} TON", "value": stake}
    u["games"] += 1
    if success:
        u["wins"] += 1
        new_item = {k: target[k] for k in ("name", "value", "rarity", "sn", "emoji", "img") if k in target}
        new_item["id"] = uuid.uuid4().hex[:10]
        if 0 < int(body.target_number or 0) < 100000:
            new_item["number"] = int(body.target_number)
        u["inventory"].append(new_item)
        if lucky:
            twin = dict(new_item); twin["id"] = uuid.uuid4().hex[:10]
            u["inventory"].append(twin)
        if eff >= QUEST_MIN_STARS:
            await bump_quest(u["tg_id"], "upg_5")
            await bump_quest(u["tg_id"], "upg_15")
        await add_history(u["tg_id"], "upgrade", "win", f"{item.get('name')} → {target.get('name')}{' · LUCKY ×2' if lucky else ''}", tv * (2 if lucky else 1) - eff)
        await push_live(new_item, u["username"])
    else:
        if eff >= QUEST_MIN_STARS:
            await bump_quest(u["tg_id"], "upg_5")
            await bump_quest(u["tg_id"], "upg_15")
        await add_history(u["tg_id"], "upgrade", "lose", f"{item.get('name')} сгорел", -eff)
    await save_user(u)

    return {"success": success, "chance": chance, "balance": u["balance"], "target": target if success else None, "inventory": u["inventory"], "lucky": bool(lucky and success), "lucky_chance": lucky_chance, "roll": round(roll, 3)}

@app.get("/api/upgrade/round")
async def upgrade_round(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    """Появится ли Lucky-монета на колесе в следующем апгрейде. Раунд живёт до апгрейда — перезапрос не меняет его."""
    u = await current_user(authorization, x_telegram_init_data)
    tid = int(u["tg_id"])
    r = UP_ROUND.get(tid)
    if not r or now_ts() - int(r.get("ts") or 0) > 3600:
        r = {"lucky": random.random() < UPGRADE_LUCKY_APPEAR, "ts": now_ts()}
        UP_ROUND[tid] = r
    return {"ok": True, "lucky": bool(r["lucky"]) and not _bonus_off(tid, "upgrade"), "share": UPGRADE_LUCKY_SHARE, "rtp": UPG_RTP, "max": UPG_MAX_CHANCE}

@app.post("/api/inventory/sell")
async def sell_item(body: SellItemRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    if body.item_index < 0 or body.item_index >= len(u["inventory"]):
        raise HTTPException(400, "Item not found")
    item = u["inventory"].pop(body.item_index)
    raw = int(item.get("value") or 0)
    # −5% комиссия: анти-абуз + небольшой edge
    price = max(0, int(raw))  # без комиссии
    u["balance"] += price
    await save_user(u)
    if price >= QUEST_MIN_STARS:
        await bump_quest(u["tg_id"], "sell_3")
    await add_history(u["tg_id"], "shop", "win", f"Продал {item.get('name')}", price)
    return {"success": True, "balance": u["balance"], "price": price, "fee": raw - price}

@app.post("/api/inventory/sell_all")
async def sell_all_items(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    inv = list(u.get("inventory") or [])
    if not inv:
        raise HTTPException(400, "Инвентарь пуст")
    total_raw = sum(int(item.get("value") or 0) for item in inv)
    price = max(0, int(total_raw))  # без комиссии
    u["inventory"] = []
    u["balance"] = int(u.get("balance") or 0) + price
    await save_user(u)
    return {"success": True, "ok": True, "sold": len(inv), "price": price, "balance": u["balance"]}


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
        (5.00, 1.2), (10.0, 0.3)]
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
    if cid.replace("_case", "") in DISABLED_CASES_BASE or cid in DISABLED_CASES:
        raise HTTPException(400, "Кейс недоступен")
    # normalize + aliases
    ALIAS = {
        "allin": "allin_case", "all_in": "allin_case", "allin_case": "allin_case",
        "pythagoras": "pythagoras_case", "pythagoras_case": "pythagoras_case",
        "rocket": "rocket_case", "rocket_case": "rocket_case", "rocket_nft": "rocket_case",
        "vasya": "vasya_case", "vasya_case": "vasya_case",
        "chicken": "chicken_gun_case", "chicken_gun": "chicken_gun_case", "chicken_gun_case": "chicken_gun_case",
        "gift_upg": "gift_upg_case", "gift_upgrader": "gift_upg_case", "gift_upg_case": "gift_upg_case",
        "cr7": "cr7_case", "cr7_case": "cr7_case", "ronaldo": "cr7_case",
        "promo": "promo_case", "promocase": "promo_case", "promo-case": "promo_case", "promo_case": "promo_case",
        "pepe": "nft_pepe", "pepe_case": "nft_pepe", "nft_pepe": "nft_pepe",
        "onyx": "only_onyx", "onyx_black": "only_onyx", "black": "only_onyx", "only_onyx": "only_onyx",
        "snoop": "brand_snoop", "snoop_dog": "brand_snoop", "snoopdog": "brand_snoop", "brand_snoop": "brand_snoop",
        "minecraft": "minecraft_case", "minecraft_case": "minecraft_case",
        "bomzh": "bednyy_shkolnik", "bednyy_shkolnik": "bednyy_shkolnik",
        "bogach": "bogach", "rich": "bogach",
        "elite": "elite_case", "elite_case": "elite_case",
        "durov": "only_durov", "only_durov": "only_durov", "durov_case": "only_durov",
        "free": "free_daily", "free_case": "free_daily", "free_daily": "free_daily",
        "candy": "nft_candy", "nft_candy": "nft_candy",
        "halloween": "halloween_case", "halloween_case": "halloween_case",
        "witch_cauldron": "witch_cauldron_case", "witch_cauldron_case": "witch_cauldron_case",
        "dracula_crypt": "dracula_crypt_case", "dracula_crypt_case": "dracula_crypt_case",
        "reaper_scythe": "reaper_scythe_case", "reaper_scythe_case": "reaper_scythe_case",
        "ghost_pumpkin": "ghost_pumpkin_case", "ghost_pumpkin_case": "ghost_pumpkin_case",
        "angel": "angel_case", "angel_case": "angel_case",
        "magic": "nft_magic", "nft_magic": "nft_magic",
        "jeremy": "jeremy_case", "jeremy_case": "jeremy_case",
        "reliz": "reliz", "release": "reliz",
        "september": "september_case", "september_case": "september_case", "1sept": "september_case",
        "fruits": "fruits_case", "fruits_case": "fruits_case",
        "telegram": "telegram_case", "telegram_case": "telegram_case", "tg_case": "telegram_case",
    }
    key = cid.lower().replace(" ", "_").replace("-", "_")
    cid = ALIAS.get(cid) or ALIAS.get(key) or key
    c = CASES.get(cid)
    if not c:
        # by name / cover
        low = key.replace("_case", "")
        for k, v in CASES.items():
            nm = (v.get("name") or "").lower().replace(" ", "_")
            cov = (v.get("cover") or "").lower()
            if k.lower() == key or nm == low or cov == low or k.replace("_case","") == low:
                cid, c = k, v
                break
    if not c:
        # last resort: create temporary case from price defaults so open never 404
        DEFAULTS = {
            "vasya_case": ("Vasya", 400, "vasya"),
            "rocket_case": ("Rocket", 550, "rocket"),
            "pythagoras_case": ("Pythagoras", 750, "pythagoras"),
            "chicken_gun_case": ("Chicken Gun", 500, "chicken"),
            "gift_upg_case": ("Gift Upgrader", 900, "giftupg"),
            "cr7_case": ("CR7", 1600, "cr7"),
            "allin_case": ("All In", 51, "allin"),
        }
        if cid in DEFAULTS or key in DEFAULTS:
            cid = cid if cid in DEFAULTS else key
            name, price, cover = DEFAULTS[cid]
            c = {
                "name": name, "price": price, "category": "themed",
                "icon": "🎁", "color": "c-tg", "rarities": ["Uncommon", "Rare", "Epic"],
                "weights": [60, 30, 10], "min_stars": 25, "max_stars": 150, "stars_chance": 0.15,
                "force_names": [], "desc": name, "cover": cover,
            }
            CASES[cid] = c
        else:
            raise HTTPException(404, f"case not found: {body.case_id} | available: {', '.join(sorted(CASES.keys()))}")
    body.case_id = cid
    if c.get("coming_soon"):
        raise HTTPException(400, "Кейс скоро будет доступен")
    # Promo case: нужен реальный промо от админа · 1 раз на аккаунт
    if c.get("require_promo"):
        promo_code = (getattr(body, "promo_code", None) or getattr(body, "promo", None) or "").strip().upper()
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
            # промо-кейс: 1 раз на аккаунт (share_claims).
            # НЕ пишем в promo_uses — иначе блокируется активация звёзд тем же кодом.
            # uses++ только считает ёмкость кода (общий лимит).
            try:
                await db.execute(
                    "INSERT INTO share_claims(tg_id, case_id, created_at) VALUES(?,?,?)",
                    (u["tg_id"], "promo_case_once", now_ts()),
                )
            except Exception:
                raise HTTPException(400, "Промо-кейс уже открыт на этом аккаунте")
            try:
                await db.execute("UPDATE promos SET uses=uses+1 WHERE code=?", (row[0],))
            except Exception as e:
                print("[promo_case] uses++", e)
            try:
                await db.commit()
            except Exception:
                pass

    # All In: ставка = весь баланс (звёзды)
    is_allin = bool(c.get("allin") or c.get("category") == "allin" or body.case_id == "allin_case")
    if is_allin:
        bal = max(0, int(u.get("balance") or 0))
        # лимит ставки — 100 TON (не весь баланс)
        max_stake = int(round(500.0 * float(STARS_PER_TON)))
        price = min(bal, max_stake)
        if price < 25:
            raise HTTPException(400, "All In: мало баланса (нужно мин. ~0.25 TON)")
        c = dict(c)
        c["allin"] = True
        c["price"] = price
        c["allin_max_ton"] = 100
    else:
        price = int(c.get("price") or 0)
    if price > 0:
        await hw_track(u["tg_id"], "wager", price)
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
            await db.execute("INSERT INTO share_claims(tg_id,case_id,created_at) VALUES(?,?,?) ON CONFLICT DO NOTHING", (u["tg_id"], body.case_id, now_ts()))
            await db.commit()
    if price > 0:
        if u["balance"] < price:
            raise HTTPException(400, "Недостаточно ⭐")
        u["balance"] -= price
    drop = roll_case_drop(body.case_id, c, stake=price if is_allin else 0)
    # anti-abuse: кейсы почти всегда в минус (не показываем игроку)
    if is_rigged(u["tg_id"], "cases") and price > 0:
        try:
            for _try in range(12):
                v = int((drop.get("gift") or {}).get("value") or drop.get("stars") or 0)
                if v <= int(price * 0.55):
                    break
                drop = roll_case_drop(body.case_id, c, stake=price if is_allin else 0)
            else:
                # крайний случай — звёзды ~30% цены
                drop = {"kind": "stars", "stars": max(1, int(price * 0.30))}
        except Exception as e:
            print("[rig cases]", e)
    secret = secrets.token_hex(16)
    fair = fair_commit(secret)
    u["games"] += 1
    u["cases_opened"] = int(u.get("cases_opened") or 0) + 1
    result = {"success": True, "balance": 0, "fair": fair, "allin_lose": bool(drop.get("allin_lose"))}
    if drop["kind"] == "gift":
        g = drop["gift"]
        inv_item = {k: g[k] for k in ("name", "value", "rarity", "sn", "emoji", "img", "rental", "rental_hours") if k in g}
        inv_item["id"] = uuid.uuid4().hex[:10]
        if g.get("rental"):
            inv_item["rental"] = True
            inv_item["rental_until"] = int(time.time()) + int(g.get("rental_hours") or 48) * 3600
            inv_item["playable"] = False
        u["inventory"].append(inv_item)
        u["wins"] += 1
        result["gift"] = inv_item
        result["rarity"] = g.get("rarity")
        try:
            await push_live(inv_item, u["username"])
        except Exception as _e:
            print("[open_case] push_live", _e)
        try:
            await add_history(u["tg_id"], "case", "gift", f"{c.get('name')}: {g.get('name')}", g.get("value") or 0)
        except Exception as _e:
            print("[open_case] history", _e)
    else:
        stars = int(drop.get("stars") or 0)
        u["balance"] += stars
        result["stars_earned"] = stars
        result["gift"] = None
        try:
            await add_history(u["tg_id"], "case", "win" if stars else "lose", f"{c.get('name')}: ⭐{stars}", stars)
        except Exception as _e:
            print("[open_case] history", _e)
    # содержимое кейса для ленты (чтобы на reel были только реальные дропы этого кейса)
    try:
        contents = []
        for row in (DROP_TABLES.get(cid) or []):
            if row.get("name"):
                g = find_gift(row["name"]) or {
                    "name": row["name"], "value": int(row.get("value") or 0),
                    "emoji": "🎁", "rarity": "Common",
                }
                pub = gift_public(g) if "gift_public" in dir() or True else g
                try:
                    pub = gift_public(g)
                except Exception:
                    pub = {"name": g.get("name"), "value": int(g.get("value") or row.get("value") or 0),
                           "emoji": g.get("emoji") or "🎁", "img": g.get("img") or "", "rarity": g.get("rarity") or "Common"}
                contents.append(pub)
            elif row.get("stars"):
                contents.append({"name": f"⭐ {int(row['stars'])}", "value": int(row["stars"]), "emoji": "⭐", "img": "", "rarity": "Common", "stars": int(row["stars"])})
        result["contents"] = contents
    except Exception:
        result["contents"] = []
    await save_user(u)
    if price >= QUEST_MIN_STARS:
        await bump_quest(u["tg_id"], "case_3")
        await bump_quest(u["tg_id"], "hw_case_5")
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
        await db.execute("INSERT INTO share_claims(tg_id,case_id,created_at) VALUES(?,?,?) ON CONFLICT DO NOTHING", (u["tg_id"], body.case_id or "free_friend", now_ts()))
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
    mines = int(body.mines or 3)
    if mines < 3 or mines > 20:
        raise HTTPException(400, "Мины 3–20")
    nft_item = None
    try:
        idx = int(getattr(body, "item_index", -1) if getattr(body, "item_index", None) is not None else -1)
    except Exception:
        idx = -1
    if idx >= 0:
        bet, nft_item = consume_nft_bet(u, idx)
    else:
        try:
            bet = int(round(float(body.bet or 0)))
        except Exception:
            bet = 0
        # если фронт случайно прислал TON (маленькое число), переведём в ⭐
        if 0 < bet < 30 and float(u.get("balance") or 0) >= bet * float(STARS_PER_TON):
            # похоже на TON: 0.1–29 TON
            bet = int(round(bet * float(STARS_PER_TON)))
        min_stars = max(11, int(round(0.1 * float(STARS_PER_TON))))  # 0.1 TON
        if bet < min_stars:
            raise HTTPException(400, f"Мин. ставка 0.1 TON (~{min_stars}⭐)")
        max_bet = int(round(500.0 * float(STARS_PER_TON)))
        if bet > max_bet:
            bet = max_bet
        if int(u.get("balance") or 0) < bet:
            raise HTTPException(400, "Недостаточно средств")
        u["balance"] = int(u.get("balance") or 0) - bet
    u["games"] = int(u.get("games") or 0) + 1
    bombs = random.sample(range(25), mines)
    await hw_track(u["tg_id"], "wager", bet)
    gid = uuid.uuid4().hex
    MINES_GAMES[gid] = {"tg_id": u["tg_id"], "bet": bet, "mines": mines, "bombs": bombs, "opened": [], "cashed": False, "nft": bool(nft_item), "rigged": is_rigged(u["tg_id"], "mines")}
    await save_user(u)
    try:
        if bet >= QUEST_MIN_STARS:
            await bump_quest(u["tg_id"], "mines_2")
    except Exception:
        pass
    return {"id": gid, "game_id": gid, "balance": u["balance"], "mult": 1.0, "multiplier": 1.0, "bet": bet, "inventory": u.get("inventory") or []}

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
    # anti-abuse: мины сразу сливают (игроку не видно)
    if g.get("rigged") or is_rigged(u["tg_id"], "mines"):
        if cell not in g["bombs"]:
            g["bombs"].append(cell)
        g["cashed"] = True
        await add_history(u["tg_id"], "mines", "lose", "Бомба", 0)
        return {
            "bomb": True, "status": "bomb", "mult": 0, "multiplier": 0,
            "bombs": list(g["bombs"]), "balance": u.get("balance"),
        }
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
    mult = mines_mult(g["mines"], len(g["opened"]))
    win = int(g["bet"] * mult)
    gift = None
    # Если выигрыш хватает на NFT из каталога — выдаём NFT вместо звёзд (ближайший <= win)
    try:
        pool = [x for x in (GIFTS_FLAT or []) if int(x.get("value") or 0) <= win and int(x.get("value") or 0) >= 50]
        if pool and win >= 102:  # от ~1 TON
            pool.sort(key=lambda x: int(x.get("value") or 0), reverse=True)
            chosen = pool[0]
            gift = {
                "name": chosen.get("name") or "Gift",
                "value": int(chosen.get("value") or 0),
                "rarity": chosen.get("rarity") or "Common",
                "sn": chosen.get("sn") or "",
                "emoji": chosen.get("emoji") or "🎁",
                "img": chosen.get("img") or "",
            }
            if not gift["img"] and gift.get("sn"):
                gift["img"] = f"{CDN}/{gift['sn']}.webp"
            inv = list(u.get("inventory") or [])
            inv.append(gift)
            u["inventory"] = inv
            # остаток звёздами если win > value
            rest = win - int(gift["value"])
            if rest > 0:
                u["balance"] = int(u.get("balance") or 0) + rest
            win_stars = rest  # для ответа
        else:
            u["balance"] = int(u.get("balance") or 0) + win
            win_stars = win
    except Exception:
        u["balance"] = int(u.get("balance") or 0) + win
        win_stars = win
        gift = None
    u["wins"] = int(u.get("wins") or 0) + 1
    await save_user(u)
    await add_history(u["tg_id"], "mines", "win", f"x{mult:.2f}", win)
    out = {
        "win": win,
        "balance": u["balance"],
        "mult": round(mult, 2),
        "multiplier": round(mult, 2),
        "inventory": u.get("inventory") or [],
    }
    if gift:
        out["gift"] = gift
        out["paid_as"] = "nft"
    else:
        out["paid_as"] = "stars"
    return out




@app.post("/api/upgrade/mystery")
async def upgrade_mystery(body: UpgradeRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    """Mystery: ставка только NFT, цель не нужна.
    Структура как у кейсов, RTP ≈ 97%:
      0.50×22% + 0.70×20% + 0.90×20% + 1.05×15% + 1.30×11% + 1.70×7% + 2.40×3.5% + 3.50×1.5%
    """
    u = await current_user(authorization, x_telegram_init_data)
    if body.item_index < 0 or body.item_index >= len(u["inventory"]):
        raise HTTPException(400, "Item not found")
    item = u["inventory"][body.item_index]
    if item.get("rental") or item.get("playable") is False:
        raise HTTPException(400, "Аренду нельзя")
    iv = int(item.get("value") or 0)
    min_in = int(round(MYSTERY_MIN_TON * float(STARS_PER_TON)))
    if iv < min_in:
        raise HTTPException(400, f"Mystery: минимум {MYSTERY_MIN_TON:g} TON (подарок от {min_in}⭐)")

    # RTP ≈ 92%: часто слив/около, редко жир (анимация может показывать крутые NFT)
    slots = [
        (0.40, 22.0),
        (0.60, 20.0),
        (0.80, 18.0),
        (1.00, 15.0),
        (1.25, 12.0),
        (1.60,  8.0),
        (2.20,  3.5),
        (3.50,  1.5)]  # EV ≈ 0.92
    total_w = sum(w for _, w in slots)
    r = random.random() * total_w
    acc = 0.0
    mult = slots[0][0]
    for mlt, w in slots:
        acc += w
        if r <= acc:
            mult = mlt
            break
    if is_rigged(u["tg_id"], "mystery") or is_rigged(u["tg_id"], "upgrade"):  # anti-abuse
        mult = random.choice([0.40, 0.50, 0.60, 0.70])
    target = max(15, int(round(iv * mult)))

    # проигрышные исходы (mult < 1.0): не NFT, а обычный гифт + остаток звёздами на баланс
    if mult < 1.0:
        reg = [g for g in GIFTS_FLAT
               if str(g.get("rarity") or "").lower() == "common"
               and is_real_gift(g.get("name") or "")
               and 15 <= int(g.get("value") or 0) <= target]
        if reg:
            reg.sort(key=lambda g: int(g.get("value") or 0), reverse=True)
            top = reg[:4]
            pick_r = dict(random.choice(top))
            pub_r = gift_public(pick_r)
            rest = max(0, target - int(pub_r.get("value") or 0))
            u["inventory"].pop(body.item_index)
            inv_item = {k: pub_r[k] for k in ("name", "value", "rarity", "sn", "emoji", "img") if k in pub_r}
            inv_item["id"] = uuid.uuid4().hex[:10]
            u["inventory"].append(inv_item)
            if rest > 0:
                u["balance"] = int(u.get("balance") or 0) + rest
            u["games"] = int(u.get("games") or 0) + 1
            await save_user(u)
            total_back = int(inv_item["value"]) + rest
            await add_history(
                u["tg_id"], "upgrade", "mystery",
                f"Mystery: {item.get('name')} → {inv_item.get('name')} +{rest}⭐ (x{mult:.2f})",
                total_back - iv,
            )
            try:
                pool_p = real_nft_pool(min_value=max(30, int(iv * 0.20)))
                random.shuffle(pool_p)
                preview_r = [gift_public(dict(g)) for g in pool_p[:14]]
            except Exception:
                preview_r = []
            preview_r.append(dict(inv_item))
            try:
                await ledger("mystery", -iv, u["tg_id"], "in")
                await ledger("mystery", total_back, u["tg_id"], "out")
            except Exception:
                pass
            return {
                "success": True, "gift": inv_item, "item": inv_item,
                "inventory": list(u["inventory"]), "balance": int(u.get("balance") or 0),
                "preview": preview_r, "mult": round(float(mult), 2), "won": False,
                "stars_back": rest, "paid_as": "regular+stars",
            }

    # пул реальных NFT, окно зависит от ставки (не фиксированные 500)
    try:
        pool = real_nft_pool(min_value=max(30, int(iv * 0.20)))
    except Exception:
        pool = [g for g in GIFTS_FLAT if int(g.get("value") or 0) >= max(30, int(iv * 0.20))]
    # убрать явный мусор / shop-common ещё раз
    ban_l = {"spy agaric", "sticker pack", "energy drink", "energy", "freedom pin", "blue heart",
             "chat bubble", "channel badge", "мишка", "сердце", "конфета", "подарок", "звезда",
             "торт", "ракета", "букет", "алмаз", "кубок", "кольцо",
             "white fox", "neon keychain", "star badge", "premium crown", "night goggles",
             "dm envelope", "big plans notebook", "socks tg", "vice cream"}
    pool = [g for g in pool if (g.get("name") or "").lower() not in ban_l and is_real_gift(g.get("name") or "")]
    if len(pool) < 5:
        pool = [g for g in GIFTS_FLAT if int(g.get("value") or 0) >= 50]
        pool = [g for g in pool if (g.get("name") or "").lower() not in ban_l]
    if not pool:
        raise HTTPException(400, "Нет NFT для Mystery")

    # кандидаты вокруг target: сначала узкая полоса, потом шире
    def near(lo_m, hi_m):
        lo, hi = target * lo_m, target * hi_m
        return [g for g in pool if lo <= int(g.get("value") or 0) <= hi]

    cand = near(0.90, 1.12)
    if len(cand) < 2:
        cand = near(0.80, 1.25)
    if len(cand) < 2:
        cand = near(0.70, 1.35)
    if not cand:
        # ближайшие, но не выше target*1.4 (анти-«всегда окуп»)
        cand = sorted(
            [g for g in pool if int(g.get("value") or 0) <= int(target * 1.40)],
            key=lambda g: abs(int(g.get("value") or 0) - target),
        )[:8]
    if not cand:
        cand = sorted(pool, key=lambda g: abs(int(g.get("value") or 0) - target))[:6]

    # среди кандидатов — вес обратно пропорционален |value - target|
    weights = []
    for g in cand:
        d = abs(int(g.get("value") or 0) - target)
        weights.append(1.0 / (1.0 + d / max(50.0, float(target) * 0.08)))
    tw = sum(weights) or 1.0
    rr = random.random() * tw
    acc = 0.0
    pick = cand[0]
    for g, w in zip(cand, weights):
        acc += w
        if rr <= acc:
            pick = g
            break
    pick = dict(pick)
    try:
        out = gift_public(pick)
    except Exception:
        out = {
            "name": pick.get("name") or "Gift",
            "value": int(pick.get("value") or target),
            "rarity": pick.get("rarity") or "Rare",
            "sn": pick.get("sn") or "",
            "emoji": pick.get("emoji") or "🎁",
            "img": pick.get("img") or "",
        }
    if not int(out.get("value") or 0):
        out["value"] = int(pick.get("value") or target)
    if not out.get("img"):
        try:
            out["img"] = gift_img_url(out.get("name") or "Gift", out.get("sn"))
        except Exception:
            pass

    # инвентарь: снять старый → положить новый
    u["inventory"].pop(body.item_index)
    inv_item = {k: out[k] for k in ("name", "value", "rarity", "sn", "emoji", "img") if k in out}
    inv_item["id"] = uuid.uuid4().hex[:10]
    inv_item["value"] = int(out.get("value") or target)
    u["inventory"].append(inv_item)
    u["games"] = int(u.get("games") or 0) + 1
    won = int(out.get("value") or 0) >= iv
    if won:
        u["wins"] = int(u.get("wins") or 0) + 1
    await save_user(u)
    await add_history(
        u["tg_id"], "upgrade", "mystery",
        f"Mystery: {item.get('name')} → {out.get('name')} (x{mult:.2f})",
        int(out.get("value") or 0) - iv,
    )

    # превью анимации: разные NFT от 0.2× до 5× ставки (байты + реальные)
    preview = []
    lo_p, hi_p = max(20, int(iv * 0.20)), max(40, int(iv * 5.0))
    band_prev = [g for g in pool if lo_p <= int(g.get("value") or 0) <= hi_p]
    if len(band_prev) < 12:
        band_prev = list(pool)
    random.shuffle(band_prev)
    for g in band_prev[:14]:
        try:
            gp = gift_public(dict(g))
            if not gp.get("img"):
                try:
                    gp["img"] = gift_img_url(gp.get("name") or "Gift", gp.get("sn"))
                except Exception:
                    pass
            preview.append(gp)
        except Exception:
            pass
    # финал в конец списка анимации
    preview.append(dict(out))

    try:
        await ledger("mystery", -iv, u["tg_id"], "in")
        await ledger("mystery", int(out.get("value") or 0), u["tg_id"], "out")
    except Exception:
        pass

    return {
        "success": True,
        "gift": inv_item,
        "item": inv_item,
        "inventory": list(u["inventory"]),
        "balance": int(u.get("balance") or 0),
        "preview": preview,
        "mult": round(float(mult), 2),
        "won": won,
    }



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
        if p["id"] and total >= QUEST_MIN_STARS * 2:
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
    rid = uuid.uuid4().hex[:8]
    try:
        async with get_db() as db:
            await db.execute(
                "INSERT INTO battles(id,case_id,host_id,host_name,price,status,created_at) VALUES(?,?,?,?,?,?,?)",
                (rid, body.case_id, u["tg_id"], u.get("username") or str(u["tg_id"]), price, "open", now_ts()),
            )
            await db.commit()
    except Exception as e:
        print("[battle create]", e)
        raise HTTPException(500, "Не удалось создать комнату. Баланс не списан.")
    try:
        u["balance"] -= price
        await save_user(u)
    except Exception as e:
        print("[battle create/charge]", e)
        async with get_db() as db:
            await db.execute("UPDATE battles SET status='cancelled' WHERE id=?", (rid,))
            await db.commit()
        raise HTTPException(500, "Ошибка списания, комната отменена")
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
    if not invoice_url and BOT_TOKEN:
        raise HTTPException(502, "Не удалось создать счёт Telegram Stars. Проверь BOT_TOKEN")
    if not invoice_url and not ALLOW_DEV_AUTH:
        raise HTTPException(503, "Оплата звёздами недоступна: не задан BOT_TOKEN")
    if not invoice_url:
        # demo credit (только dev)
        u["balance"] += amount
        u["deposited"] += amount
        await save_user(u)
        return {"success": True, "balance": u["balance"], "payload": payload, "message": "Демо-пополнение (нет BOT_TOKEN / invoice)"}
    return {"invoice_url": invoice_url, "payload": payload}

@app.post("/api/deposit/confirm")
async def deposit_confirm(body: DepositConfirmRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    """Только проверка статуса. Зачисление — ТОЛЬКО по successful_payment из вебхука Telegram.
    (Раньше этот эндпоинт сам начислял звёзды без проверки оплаты — бесплатные ⭐ для любого.)"""
    u = await current_user(authorization, x_telegram_init_data)
    async with get_db() as db:
        row = await (await db.execute("SELECT tg_id, amount, paid FROM deposits WHERE payload=?", (body.payload,))).fetchone()
        if not row or int(row[0]) != int(u["tg_id"]):
            raise HTTPException(404, "Платёж не найден")
        bal = await (await db.execute("SELECT balance FROM users WHERE tg_id=?", (u["tg_id"],))).fetchone()
    return {"paid": bool(int(row[2] or 0)), "balance": int(bal[0] or 0) if bal else u["balance"]}

@app.post("/api/withdraw")
async def withdraw(body: WithdrawRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    if int(u["tg_id"]) in WBANNED and not u.get("is_admin"):
        raise HTTPException(403, "Вывод отключён для аккаунта. Если это ошибка — напиши @" + SUPPORT_USERNAME)
    if GIFT_WITHDRAW_ONLY:
        raise HTTPException(400, "Вывод теперь только подарками: Профиль → Инвентарь → «Вывести» на подарке")
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
            (u["tg_id"], ts_param(day0)),
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
            (u["tg_id"], amount, body.method or "stars", dest, body.note or "", "pending", ts_param(now_ts())),
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
    """Активация промокода: +⭐ на баланс. 1 раз на аккаунт. Надёжно для Postgres."""
    u = await current_user(authorization, x_telegram_init_data)
    tg_id = int(u["tg_id"])

    # body / query
    if not code:
        try:
            body = await request.json()
            if isinstance(body, dict):
                code = body.get("code") or body.get("promo") or body.get("promo_code")
        except Exception:
            code = None
    code = (str(code or "")).strip().upper().replace(" ", "").replace("\xa0", "")
    if not code:
        raise HTTPException(400, "Введите промокод")
    try:
        bl = await blogger_find(code)
    except Exception:
        bl = None
    if bl:
        res = await blogger_attach(tg_id, code)
        bal = (await current_user(authorization, x_telegram_init_data))["balance"]
        return {"ok": True, "success": True, "blogger": True, "message": res.get("message"), "stars": res.get("bonus", 0), "balance": bal}

    # --- защита: одна активация за раз (раньше два одновременных запроса начисляли ×2) ---
    t_now = time.time()
    tries = [t for t in _PROMO_TRIES.get(tg_id, []) if t_now - t < 3600]
    if len(tries) >= 15:
        raise HTTPException(429, "Слишком много попыток — попробуй через час")
    tries.append(t_now); _PROMO_TRIES[tg_id] = tries
    async with _PROMO_LOCK:
      async with get_db() as db:
        await db.execute("CREATE TABLE IF NOT EXISTS promos (code TEXT PRIMARY KEY, reward_type TEXT, stars INTEGER, max_uses INTEGER, uses INTEGER DEFAULT 0)")
        await db.execute("CREATE TABLE IF NOT EXISTS promo_claims (code TEXT NOT NULL, tg_id BIGINT NOT NULL, ts BIGINT, PRIMARY KEY (code, tg_id))")
        row = await (await db.execute("SELECT code, stars, max_uses, uses FROM promos WHERE UPPER(TRIM(code))=?", (code,))).fetchone()
        if not row:
            raise HTTPException(400, "Промокод не найден")
        real_code = str(row[0]).strip().upper()
        stars = max(0, int(row[1] or 0))
        max_u = max(0, int(row[2] or 0))
        cnt = await (await db.execute("SELECT COUNT(*) FROM promo_claims WHERE code=?", (real_code,))).fetchone()
        uses = int(cnt[0] or 0) if cnt else 0
        if max_u > 0 and uses >= max_u:
            raise HTTPException(400, f"Лимит промо исчерпан ({uses}/{max_u})")
        mine = await (await db.execute("SELECT 1 FROM promo_claims WHERE code=? AND tg_id=?", (real_code, int(tg_id)))).fetchone()
        legacy = None
        if not mine:
            for prm in ((real_code, int(tg_id)), (real_code, str(tg_id))):
                try:
                    legacy = await (await db.execute("SELECT 1 FROM promo_uses WHERE UPPER(TRIM(code))=? AND tg_id=?", prm)).fetchone()
                    if legacy:
                        break
                except Exception:
                    pass
        if mine or legacy:
            br = await (await db.execute("SELECT balance FROM users WHERE tg_id=?", (tg_id,))).fetchone()
            return {"ok": False, "success": False, "already": True, "stars": 0, "balance": int(br[0] or 0) if br else 0,
                    "code": real_code, "message": "Промокод уже активирован на этом аккаунте"}
        # 1) СНАЧАЛА отметка (уникальный ключ) — если не вышло, ничего не начисляем
        try:
            await db.execute("INSERT INTO promo_claims(code, tg_id, ts) VALUES(?,?,?)", (real_code, int(tg_id), now_ts()))
            await db.commit()
        except Exception:
            raise HTTPException(400, "Промокод уже активирован на этом аккаунте")
        # 2) потом начисление
        if stars > 0:
            await db.execute("UPDATE users SET balance = COALESCE(balance, 0) + ? WHERE tg_id=?", (stars, tg_id))
        await db.execute("UPDATE promos SET uses=? WHERE UPPER(TRIM(code))=?", (uses + 1, real_code))
        try:
            await db.execute("INSERT INTO promo_uses(code, tg_id) VALUES(?, ?)", (real_code, tg_id))
        except Exception:
            pass
        try:
            await db.commit()
        except Exception:
            pass

        bal_new = 0
        try:
            br = await (await db.execute("SELECT balance FROM users WHERE tg_id=?", (tg_id,))).fetchone()
            if br is not None:
                bal_new = int(br[0] or 0)
        except Exception:
            pass

    try:
        u["balance"] = bal_new
    except Exception:
        pass

    return {
        "ok": True,
        "success": True,
        "stars": stars,
        "balance": bal_new,
        "code": real_code,
        "unlock_promo_case": True,
        "message": (f"+{stars}⭐ · промо {real_code}" if stars else f"Промо {real_code} активирован"),
    }




QUEST_MIN_STARS = int(round(0.5 * float(STARS_PER_TON)))   # действие засчитывается в квест, только если от 0.5 TON
QUESTS_DEF = [
    {"id": "upg_5", "title": "Сделай 15 апгрейдов (от 0.5 TON)", "need": 15, "reward": 20},
    {"id": "case_3", "title": "Открой 10 платных кейсов (от 0.5 TON)", "need": 10, "reward": 20},
    {"id": "mines_2", "title": "Сыграй в мины 10 раз (ставка от 0.5 TON)", "need": 10, "reward": 15},
    {"id": "pvp_1", "title": "Сыграй 5 PvP (банк от 1 TON)", "need": 5, "reward": 15},
    {"id": "sell_3", "title": "Продай 10 предметов (от 0.5 TON каждый)", "need": 10, "reward": 10},
    {"id": "upg_15", "title": "Сделай 60 апгрейдов (от 0.5 TON)", "need": 60, "reward": 60},
    {"id": "hw_case_5", "title": "🎃 Открой 20 платных кейсов в Хэллоуин", "need": 20, "reward": 40},
    {"id": "cauldron_1", "title": "🧪 Свари 5 зелий в Ведьмином Котле", "need": 5, "reward": 25},
]

DEFAULT_PROMOS = [
]

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


async def _ensure_demo_top_user():
    """Тестовый игрок «бебр» — чтобы топ не был пустым и было видно рост."""
    try:
        async with get_db() as db:
            row = await (await db.execute(
                "SELECT tg_id FROM users WHERE tg_id=?", (999000001,)
            )).fetchone()
            if row:
                try:
                    await db.execute(
                        "UPDATE users SET username=?, balance=?, wins=?, cases_opened=? WHERE tg_id=?",
                        ("бебр", 5000, 3, 5, 999000001),
                    )
                    await db.commit()
                except Exception as e:
                    print("[top] update бебр", e)
                return
            try:
                await db.execute(
                    "INSERT INTO users(tg_id, username, balance, inventory, games, wins, deposited, free_case_at, friend_case_at, chance_bonus, ton_wallet, cases_opened, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (999000001, "бебр", 5000, "[]", 12, 3, 0, 0, 0, 0.0, "", 5, int(__import__("time").time())),
                )
            except Exception:
                await db.execute(
                    "INSERT INTO users(tg_id, username, balance, inventory, games, wins, cases_opened) VALUES(?,?,?,?,?,?,?)",
                    (999000001, "бебр", 5000, "[]", 12, 3, 5),
                )
            await db.commit()
            print("[top] seeded бебр")
    except Exception as e:
        print("[top] seed бебр fail:", e)



class CoinFlipRequest(BaseModel):
    side: str = "heads"  # heads | tails
    bet: int = 0
    item_index: int = -1


@app.post("/api/coin/flip")
async def coin_flip(body: CoinFlipRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    """Орёл/решка ×1.90, P(win)=0.475. NFT-ставка через item_index. Макс 500 TON."""
    u = await current_user(authorization, x_telegram_init_data)
    side = (body.side or "heads").strip().lower()
    if side in ("orel", "орёл", "орел", "head", "o"):
        side = "heads"
    if side in ("reshka", "решка", "tail", "t"):
        side = "tails"
    if side not in ("heads", "tails"):
        raise HTTPException(400, "Выбери heads или tails")
    try:
        max_bet = int(round(500.0 * float(STARS_PER_TON)))
    except Exception:
        max_bet = 55000
    min_bet = max(1, int(round(0.1 * float(STARS_PER_TON))))
    inv = list(u.get("inventory") or [])
    idx = int(getattr(body, "item_index", -1) or -1)
    nft_item = None
    bet = 0
    if idx >= 0:
        if idx >= len(inv) or not inv[idx]:
            raise HTTPException(400, "NFT не найден")
        nft_item = inv[idx]
        if nft_item.get("rental") or nft_item.get("playable") is False:
            raise HTTPException(400, "Этот NFT нельзя ставить")
        bet = int(nft_item.get("value") or 0)
        if bet < 10:
            raise HTTPException(400, "Ставка слишком мала")
        if bet > max_bet:
            raise HTTPException(400, "Макс. ставка 500 TON")
        inv.pop(idx)
        u["inventory"] = inv
    else:
        bet = int(body.bet or 0)
        if bet < min_bet:
            raise HTTPException(400, "Ставка слишком мала")
        if bet > int(u.get("balance") or 0):
            raise HTTPException(400, "Недостаточно средств")
        if bet > max_bet:
            bet = max_bet
        u["balance"] = int(u.get("balance") or 0) - bet
    win = random.random() < 0.475
    if is_rigged(u["tg_id"], "coin"):  # anti-abuse
        win = False
    result = side if win else ("tails" if side == "heads" else "heads")
    payout = int(round(bet * 1.90)) if win else 0
    if win:
        u["balance"] = int(u.get("balance") or 0) + payout
        u["wins"] = int(u.get("wins") or 0) + 1
    u["games"] = int(u.get("games") or 0) + 1
    new_bal = max(0, int(u.get("balance") or 0))
    u["balance"] = new_bal
    try:
        await save_user(u)
    except Exception as e:
        print("[coin] save", e)
        async with get_db() as db:
            await db.execute(
                "UPDATE users SET balance=?, games=?, wins=?, inventory=? WHERE tg_id=?",
                (new_bal, int(u["games"]), int(u.get("wins") or 0),
                 json.dumps(u.get("inventory") or [], ensure_ascii=False), int(u["tg_id"])),
            )
            await db.commit()
    try:
        await add_history(
            u["tg_id"], "coin", "win" if win else "lose",
            f"{('NFT '+(nft_item.get('name') or '')) if nft_item else str(bet)+'⭐'} · {result}",
            payout if win else -bet,
        )
    except Exception:
        pass
    return {
        "success": True, "ok": True, "win": win, "result": result, "side": side,
        "bet": bet, "stake": bet, "payout": payout, "balance": new_bal,
        "inventory": u.get("inventory") or [],
    }



@app.get("/api/leaderboard")
async def leaderboard():
    await _ensure_demo_top_user()
    total_users = 0
    by_bal = by_wins = by_cases = []
    try:
        async with get_db() as db:
            try:
                total_row = await (await db.execute("SELECT COUNT(*) FROM users")).fetchone()
                total_users = int(total_row[0] or 0) if total_row else 0
            except Exception as e:
                print("[top] count", e)
            try:
                by_bal = await (await db.execute(
                    "SELECT tg_id, username, balance, wins, cases_opened, COALESCE(photo_url,'') FROM users ORDER BY balance DESC, wins DESC LIMIT 50"
                )).fetchall()
            except Exception as e:
                print("[top] bal", e)
            try:
                by_wins = await (await db.execute(
                    "SELECT tg_id, username, balance, wins, cases_opened, COALESCE(photo_url,'') FROM users ORDER BY wins DESC, balance DESC LIMIT 50"
                )).fetchall()
            except Exception as e:
                print("[top] wins", e)
            try:
                by_cases = await (await db.execute(
                    "SELECT tg_id, username, balance, wins, cases_opened, COALESCE(photo_url,'') FROM users ORDER BY cases_opened DESC, balance DESC LIMIT 50"
                )).fetchall()
            except Exception as e:
                print("[top] cases", e)
    except Exception as e:
        print("[top] fatal", e)
        return {"total_users": 0, "by_balance": [], "by_wins": [], "by_cases": [], "error": str(e)}

    async def pack(rows):
        out = []
        for r in (rows or []):
            try:
                tg = int(r[0] or 0)
                nm = (str(r[1]).strip() if r[1] is not None else "") or ("ID " + str(tg))
                photo = ""
                try:
                    if len(r) > 5 and r[5]:
                        photo = str(r[5])
                except Exception:
                    photo = ""
                if not photo and tg in _PHOTO_CACHE:
                    photo = _PHOTO_CACHE.get(tg) or "" 
                out.append({
                    "tg_id": tg,
                    "name": nm,
                    "username": nm,
                    "balance": int(r[2] or 0),
                    "wins": int(r[3] or 0),
                    "cases": int(r[4] or 0),
                    "photo_url": photo or "",
                    "avatar": photo or "",
                })
            except Exception:
                continue
        return out

    return {
        "total_users": total_users,
        "by_balance": await pack(by_bal),
        "by_wins": await pack(by_wins),
        "by_cases": await pack(by_cases),
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
    link = f"https://t.me/{(BOT_USERNAME or 'casefightbot')}?start=ref_{u['tg_id']}"
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
        "percent": REF_L1_PCT,
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
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "GiftUpgrader/1.0", **({"Authorization": "Bearer " + os.getenv("TONAPI_KEY")} if os.getenv("TONAPI_KEY") else {})})
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
        await mark_deposit(tg_id, "ton")
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


# duplicate leaderboard_weekly removed

# ----- admin -----


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
    if uid <= 0:
        raise HTTPException(400, "Некорректный tg_id")
    new_bal = 0
    async with get_db() as db:
        row = await (await db.execute("SELECT balance, username FROM users WHERE tg_id=?", (uid,))).fetchone()
        if not row:
            import time as _time
            new_bal = max(0, amt)
            try:
                await db.execute(
                    "INSERT INTO users(tg_id,username,balance,inventory,games,wins,deposited,free_case_at,friend_case_at,chance_bonus,ton_wallet,cases_opened,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (uid, f"user_{uid}", new_bal, "[]", 0, 0, 0, 0, 0, 0.0, "", 0, int(_time.time())),
                )
            except Exception as e:
                print("[admin/give] insert", e)
                await db.execute(
                    "INSERT INTO users(tg_id,username,balance,inventory,games,wins,cases_opened) VALUES(?,?,?,?,?,?,?)",
                    (uid, f"user_{uid}", new_bal, "[]", 0, 0, 0),
                )
        else:
            new_bal = int(row[0] or 0) + amt
            if new_bal < 0:
                new_bal = 0
            await db.execute("UPDATE users SET balance=? WHERE tg_id=?", (new_bal, uid))
        await db.commit()
        # verify
        v = await (await db.execute("SELECT balance FROM users WHERE tg_id=?", (uid,))).fetchone()
        if v is not None:
            new_bal = int(v[0] or 0)
    return {"ok": True, "success": True, "user_id": uid, "balance": new_bal, "amount": amt}


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
    code = (body.code or "").strip().upper().replace(" ", "").replace("\xa0", "")
    if not code or len(code) < 2:
        raise HTTPException(400, "Пустой код (мин. 2 символа)")
    stars = max(0, int(body.stars or 0))
    max_uses = max(1, int(body.max_uses or 1))
    reward_type = (body.reward_type or "stars").strip().lower()
    if reward_type not in ("stars", "balance", "case"):
        reward_type = "stars"
    async with get_db() as db:
        row = await (await db.execute("SELECT code, uses FROM promos WHERE UPPER(TRIM(code))=?", (code,))).fetchone()
        if row:
            await db.execute(
                "UPDATE promos SET reward_type=?, stars=?, max_uses=?, uses=0 WHERE UPPER(TRIM(code))=?",
                (reward_type, stars, max_uses, code),
            )
            # сброс активаций — можно выдать код заново
            try:
                await db.execute("DELETE FROM promo_claims WHERE code=?", (code,))
            except Exception:
                pass
            try:
                await db.execute("DELETE FROM promo_uses WHERE UPPER(TRIM(code))=?", (code,))
            except Exception:
                try:
                    await db.execute("DELETE FROM promo_uses WHERE code=?", (row[0],))
                except Exception as e:
                    print("[promo] reset uses", e)
            used = 0
        else:
            await db.execute(
                "INSERT INTO promos(code,reward_type,stars,max_uses,uses) VALUES(?,?,?,?,0)",
                (code, reward_type, stars, max_uses),
            )
            used = 0
        try:
            await db.commit()
        except Exception:
            pass
    left = max(0, max_uses - used)
    return {"ok": True, "code": code, "stars": stars, "max_uses": max_uses, "uses": used, "left": left, "message": f"Промо {code}: +{stars}⭐ · лимит {max_uses} (сброшен, можно снова)"}

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
        row = await (await db.execute("SELECT tg_id, amount, status, method, note FROM withdrawals WHERE id=?", (body.withdraw_id,))).fetchone()
        if not row:
            raise HTTPException(404, "wd")
        await db.execute("UPDATE withdrawals SET status=? WHERE id=?", (body.status, body.withdraw_id))
        if body.status in ("rejected", "cancel", "cancelled") and row[2] == "pending" and (row[3] or "") == "gift":
            # отказ по подарку — возвращаем сам подарок в инвентарь
            try:
                item = json.loads(row[4] or "{}").get("item") or {}
            except Exception:
                item = {}
            urow = await (await db.execute("SELECT inventory FROM users WHERE tg_id=?", (row[0],))).fetchone()
            if urow and item:
                inv = json.loads(urow[0] or "[]") if isinstance(urow[0], str) else (urow[0] or [])
                inv.append(item)
                await db.execute("UPDATE users SET inventory=? WHERE tg_id=?", (json.dumps(inv, ensure_ascii=False), row[0]))
        elif body.status in ("rejected", "cancel", "cancelled") and row[2] == "pending":
            bal = await (await db.execute("SELECT balance FROM users WHERE tg_id=?", (row[0],))).fetchone()
            if bal:
                await db.execute("UPDATE users SET balance=? WHERE tg_id=?", (int(bal[0] or 0) + int(row[1]), row[0]))
        await db.commit()
    try:
        uid, amt = int(row[0]), int(row[1])
        st = (body.status or "").lower()
        if (row[3] or "") == "gift":
            try:
                gname = (json.loads(row[4] or "{}").get("item") or {}).get("name") or "подарок"
            except Exception:
                gname = "подарок"
            if st in ("approved", "done", "paid", "ok"):
                await tg_notify(uid, f"✅ Вывод подарка <b>{gname}</b> одобрен — @{SUPPORT_USERNAME} отправит его тебе в Telegram.")
            elif st in ("rejected", "cancel", "cancelled"):
                await tg_notify(uid, f"❌ Вывод подарка <b>{gname}</b> отклонён, подарок вернулся в инвентарь.")
        elif st in ("approved", "done", "paid", "ok"):
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



@app.post("/telegram/webhook")
async def tg_webhook(request: Request):
    # тот же обработчик, что и /api/telegram/webhook: оплаты + /start, ответ Telegram сразу
    if not _hook_ok(request):
        raise HTTPException(403, "bad secret")
    try:
        data = await request.json()
    except Exception:
        return {"ok": True}
    asyncio.create_task(_tg_update_bg(data))
    return {"ok": True}

# -------------------- CRASH SOCKET --------------------
PVP = {}  # in-memory PvP lobbies (lobby_id -> state)
PVP_COLORS = ["#7c6cf0", "#34d399", "#f59e0b", "#f87171", "#38bdf8", "#e879f9"]
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

def _sio_auth_id(data) -> int:
    """tg_id для сокета: только из подписанного initData (иначе любой мог ставить за чужой баланс)."""
    d = data or {}
    raw = str(d.get("init_data") or "").strip()
    if raw:
        try:
            return int((_parse_init_data(raw) or {}).get("id") or 0)
        except Exception:
            return 0
    if not BOT_TOKEN or ALLOW_DEV_AUTH:
        try:
            return int(d.get("tg_id") or 0)
        except Exception:
            return 0
    return 0

@sio.on("place_bet")
async def on_place_bet(sid, data):
    try:
        tg_id = _sio_auth_id(data)
        if tg_id and int(tg_id) in BANNED:
            tg_id = 0
        if not tg_id:
            await sio.emit("error", {"message": "Нет авторизации Telegram — перезапусти мини-апп"}, to=sid)
            return
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
        await sio.emit("bet_placed", {"amount": amount, "balance": int(bal2[0]), "inventory": inv_out, "username": username}, to=sid)
        # всем — обновлённый список ставок раунда
        bets_view = [{"username": b.get("username") or "?", "amount": b.get("amount") or 0, "cashed": bool(b.get("cashed")), "is_nft": False} for b in CRASH["bets"].values()]
        await sio.emit("crash_bets", {"bets": bets_view})
    except Exception as e:
        await sio.emit("error", {"message": str(e)}, to=sid)

@sio.on("cashout")
async def on_cashout(sid, data):
    try:
        tg_id = _sio_auth_id(data)
        if not tg_id:
            return
        if CRASH["status"] != "flying":
            await sio.emit("error", {"message": "Нельзя сейчас"}, to=sid)
            return
        b = CRASH["bets"].get(tg_id)
        if not b or b["cashed"]:
            return
        # anti-abuse: кэшаут только до ×1.05
        if is_rigged(tg_id, "crash") and float(CRASH.get("multiplier") or 1) > 1.05:
            await sio.emit("error", {"message": "Уже разбилась"}, to=sid)
            return
        win = int(b["amount"] * CRASH["multiplier"])
        b["cashed"] = True
        b["win"] = win
        b["cash_mult"] = float(CRASH.get("multiplier") or 1)
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
            for t in range(int(CRASH_CFG["bet_sec"]), 0, -1):
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
                m = min(CRASH["crash_point"], m * 1.011 + 0.0012)
                CRASH["multiplier"] = round(m, 2)
                await sio.emit("crash_multiplier", {"multiplier": CRASH["multiplier"]})
                await asyncio.sleep(0.12)
            CRASH["status"] = "crashed"
            CRASH["history"] = ([CRASH["crash_point"]] + CRASH["history"])[:12]
            bets_view = []
            for tg_id, b in CRASH["bets"].items():
                if not b["cashed"]:
                    b["win"] = 0
                bets_view.append({
                    "username": b.get("username") or "Player",
                    "win": b.get("win") or 0,
                    "amount": b.get("amount") or 0,
                    "is_nft": bool(b.get("is_nft")),
                    "nft_name": b.get("nft_name") or "",
                    "img": b.get("img") or "",
                    "mult": b.get("cash_mult") or 0,
                    "cashed": bool(b.get("cashed")),
                })
            await sio.emit("crash_end", {"crash_point": CRASH["crash_point"], "bets": bets_view})
            await sio.emit("crash_state", {"status": "crashed", "history": CRASH["history"]})
            await asyncio.sleep(2.5)
        except Exception as e:
            print("[crash]", e)
            await asyncio.sleep(2)




class CauldronSpinRequest(BaseModel):
    bet: int = 55
    mode: str = "cauldron"

@app.post("/api/halloween/cauldron")
async def halloween_cauldron_spin(
    body: CauldronSpinRequest,
    authorization: Optional[str] = Header(None),
    x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data"),
):
    """Хэллоуинский режим «Ведьмин Котел / Колесо Кошмаров»:
    Ставка в ⭐ (конвертируется на фронте в TON), крутит зелье удачи:
    множители x0, x0.5, x1.2, x2.0, x3.5 или редкий Хэллоуин-NFT!"""
    u = await current_user(authorization, x_telegram_init_data)
    bet = max(11, min(int(body.bet or 55), int(round(200.0 * float(STARS_PER_TON)))))
    if int(u.get("balance") or 0) < bet:
        raise HTTPException(400, "Недостаточно средств для варки зелья")
    u["balance"] = int(u.get("balance") or 0) - bet
    u["games"] = int(u.get("games") or 0) + 1

    # Сектора: (id, label, mult, weight, give_nft)
    sectors = [
        (0, "💀 Скелет x0", 0.0, 26.0, False),
        (1, "🍬 Сладость x0.5", 0.5, 22.0, False),
        (2, "🕯️ Свеча x1.2", 1.2, 24.0, False),
        (3, "🦇 Вампир x2.0", 2.0, 15.0, False),
        (4, "🎃 Тыква x3.0", 3.0, 7.0, False),
        (5, "🧪 Зелье NFT!", 1.6, 4.5, True),
        (6, "👻 Призрак x0", 0.0, 1.0, False),
        (7, "👑 ДЖЕКПОТ x6.0", 6.0, 0.5, False),
    ]
    tw = sum(s[3] for s in sectors)
    r = random.random() * tw
    acc = 0.0
    chosen = sectors[0]
    for s in sectors:
        acc += s[3]
        if r <= acc:
            chosen = s
            break

    sec_idx, label, mult, _, give_nft = chosen
    payout = cap_win(bet * mult)
    gift_won = None

    if give_nft or (payout >= 400 and random.random() < 0.35):
        hw_names = ['Witch Hat', 'Hex Pot', 'Flying Broom', 'Skull Flower', 'Crystal Ball', 'Mad Pumpkin', 'Love Potion', 'Electric Skull', 'Coffin', 'Voodoo Doll']
        hw_pool = [find_gift(nm) for nm in hw_names if find_gift(nm)]
        target_v = max(110, payout)
        best = min(hw_pool, key=lambda g: abs(int(g.get("value") or 500) - target_v)) if hw_pool else None
        if best:
            gift_won = dict(best)
            gift_won["id"] = uuid.uuid4().hex[:10]
            u["inventory"].append(gift_won)
            payout = int(gift_won.get("value") or payout)
            u["wins"] = int(u.get("wins") or 0) + 1
            try:
                await push_live(gift_won, u.get("username") or "Hunter")
            except Exception:
                pass
    else:
        if payout > 0:
            u["balance"] += payout
        if mult >= 1.0:
            u["wins"] = int(u.get("wins") or 0) + 1

    await save_user(u)
    try:
        await bump_quest(u["tg_id"], "cauldron_1")
        await add_history(
            u["tg_id"], "cauldron",
            "win" if (mult >= 1.0 or gift_won) else "lose",
            f"Котел: {label}",
            payout if (mult >= 1.0 or gift_won) else -bet,
        )
    except Exception:
        pass

    return {
        "ok": True,
        "success": True,
        "sector_index": sec_idx,
        "label": label,
        "mult": mult,
        "payout": payout,
        "gift": gift_won,
        "win": bool(mult >= 1.0 or gift_won),
        "balance": u["balance"],
        "inventory": u["inventory"],
    }


# ----- TRICK OR TREAT (DOORS) GAME -----
DOORS_GAMES: Dict[str, dict] = {}

DOORS_DIFFICULTIES = {
    "easy":   {"fail_prob": 0.20, "step_mult": 1.19, "max_steps": 12, "title": "Легкий (20% риск)"},
    "medium": {"fail_prob": 0.35, "step_mult": 1.46, "max_steps": 10, "title": "Средний (35% риск)"},
    "hard":   {"fail_prob": 0.50, "step_mult": 1.90, "max_steps": 8,  "title": "Сложный (50% риск)"},
    "nightmare": {"fail_prob": 0.68, "step_mult": 2.95, "max_steps": 6, "title": "Кошмар (68% риск)"},
}

class DoorsStartRequest(BaseModel):
    bet: int = 55
    difficulty: str = "medium"
    item_index: int = -1

class DoorsActionRequest(BaseModel):
    game_id: str

@app.post("/api/doors/start")
async def doors_start(
    body: DoorsStartRequest,
    authorization: Optional[str] = Header(None),
    x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data"),
):
    u = await current_user(authorization, x_telegram_init_data)
    diff = (body.difficulty or "medium").strip().lower()
    if diff not in DOORS_DIFFICULTIES:
        diff = "medium"
    cfg = DOORS_DIFFICULTIES[diff]

    nft_item = None
    idx = int(getattr(body, "item_index", -1) if getattr(body, "item_index", None) is not None else -1)
    if idx >= 0:
        bet, nft_item = consume_nft_bet(u, idx)
    else:
        bet = int(round(float(body.bet or 0)))
        if 0 < bet < 30 and float(u.get("balance") or 0) >= bet * float(STARS_PER_TON):
            bet = int(round(bet * float(STARS_PER_TON)))
        min_stars = max(11, int(round(0.1 * float(STARS_PER_TON))))
        if bet < min_stars:
            raise HTTPException(400, f"Мин. ставка 0.1 TON (~{min_stars}⭐)")
        max_bet = int(round(500.0 * float(STARS_PER_TON)))
        if bet > max_bet:
            bet = max_bet
        if int(u.get("balance") or 0) < bet:
            raise HTTPException(400, "Недостаточно средств")
        u["balance"] = int(u.get("balance") or 0) - bet

    u["games"] = int(u.get("games") or 0) + 1
    await hw_track(u["tg_id"], "wager", bet)
    gid = uuid.uuid4().hex
    DOORS_GAMES[gid] = {
        "tg_id": u["tg_id"],
        "bet": bet,
        "difficulty": diff,
        "step": 0,
        "mult": 1.0,
        "active": True,
        "nft": bool(nft_item),
    }
    await save_user(u)
    return {
        "ok": True,
        "game_id": gid,
        "bet": bet,
        "difficulty": diff,
        "step": 0,
        "mult": 1.0,
        "next_mult": round(cfg["step_mult"], 2),
        "max_steps": cfg["max_steps"],
        "balance": u["balance"],
        "inventory": u.get("inventory") or [],
    }

@app.post("/api/doors/cashout")
async def doors_cashout(
    body: DoorsActionRequest,
    authorization: Optional[str] = Header(None),
    x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data"),
):
    u = await current_user(authorization, x_telegram_init_data)
    g = DOORS_GAMES.get(body.game_id)
    if not g or g["tg_id"] != u["tg_id"] or not g.get("active"):
        raise HTTPException(400, "Нет активной игры")
    if g["step"] < 1:
        raise HTTPException(400, "Постучи хотя бы в одну дверь!")

    g["active"] = False
    DOORS_GAMES.pop(body.game_id, None)
    win = cap_win(g["bet"] * g["mult"])
    u["balance"] = int(u.get("balance") or 0) + win
    u["wins"] = int(u.get("wins") or 0) + 1
    await save_user(u)
    await add_history(u["tg_id"], "doors", "win", f"Забрал на двери #{g['step']} (x{g['mult']})", win)
    try:
        await bump_quest(u["tg_id"], "cauldron_1")
    except Exception:
        pass
    return {
        "ok": True,
        "win": win,
        "mult": g["mult"],
        "step": g["step"],
        "balance": u["balance"],
    }

@app.get("/api/leaders")
async def leaders_alias():
    """Alias for /api/leaderboard (фронт иногда бьёт /api/leaders)."""
    return await leaderboard()

@app.get("/api/config/public")
async def public_config():
    """Публичные настройки для фронта: поддержка, курс."""
    return {
        "support_username": SUPPORT_USERNAME,
        "support_url": f"https://t.me/{SUPPORT_USERNAME}",
        "ton_stars_per_ton": TON_STARS_PER_TON,
        "webapp_url": WEBAPP_URL,
        "bot_username": BOT_USERNAME or None,
    }

# =====================================================================
#  HALLOWEEN EVENT PACK: босс-шкала, Хэллоуин-пасс, задания, Тыквенная грядка, шеринг рефералки с фото
#  Все числа настраиваются константами/ENV ниже.
# =====================================================================
from fastapi.responses import Response as _HwResponse
import base64 as _hw_b64

HW_PREMIUM_PRICE = int(os.getenv("HW_PREMIUM_PRICE") or 2500)     # цена Premium-пасса в ⭐
HW_XP_PER_LEVEL = int(os.getenv("HW_XP_PER_LEVEL") or 250)
HW_MAX_LEVEL = 30
HW_XP_PER_TON = float(os.getenv("HW_XP_PER_TON") or 2.0)           # XP за каждый TON оборота
HW_XP_PER_GAME = 3
HW_BOSSES = [
    {"id": "pumpkin_king",  "name": "Тыквенный Король",     "need_ton": 300.0,  "reward_ton": 3.0},
    {"id": "skeleton_lord", "name": "Повелитель Скелетов", "need_ton": 3000.0, "reward_ton": 30.0},
]
# key — счётчик; need — сколько надо; xp — награда в опыте пасса
HW_TASKS = [
    {"id": "t_case25",   "title": "Открой 60 кейсов",                 "key": "case",         "need": 60,  "xp": 200},
    {"id": "t_upg15",    "title": "Сделай 40 апгрейдов",              "key": "upgrade",      "need": 40,  "xp": 200},
    {"id": "t_upgwin5",  "title": "Выиграй 15 апгрейдов",             "key": "upgrade_win",  "need": 15,  "xp": 300},
    {"id": "t_door20",   "title": "Сыграй 40 раз в Двери",            "key": "door_game",    "need": 40,  "xp": 200},
    {"id": "t_pump15",   "title": "Сыграй 30 раз в Тыквенную грядку", "key": "pumpkin_game", "need": 30,  "xp": 200},
    {"id": "t_wins10",   "title": "Забери 30 выигрышей в мини-играх", "key": "any_win",      "need": 30,  "xp": 250},
    {"id": "t_wager20",  "title": "Отыграй 50 TON",                   "key": "wager_ton",    "need": 50,  "xp": 300},
    {"id": "t_wager100", "title": "Отыграй 300 TON",                  "key": "wager_ton",    "need": 300, "xp": 800},
    {"id": "t_boss1",    "title": "Победи Тыквенного Короля",         "key": "boss1",        "need": 1,   "xp": 500},
]
HW_DDL = ("CREATE TABLE IF NOT EXISTS hw_state (tg_id BIGINT PRIMARY KEY, wager DOUBLE PRECISION DEFAULT 0, xp INTEGER DEFAULT 0, "
          "premium INTEGER DEFAULT 0, claimed TEXT DEFAULT '[]', counters TEXT DEFAULT '{}')")

def hw_free_reward(l: int) -> int:
    v = 2 * l
    return v * 3 if l % 5 == 0 else v

def hw_prem_reward(l: int) -> int:
    v = 6 * l
    return int(v * 2.5) if l % 5 == 0 else v

async def _hw_load(tg_id: int) -> dict:
    async with get_db() as db:
        await db.execute(HW_DDL)
        row = await (await db.execute("SELECT wager,xp,premium,claimed,counters FROM hw_state WHERE tg_id=?", (int(tg_id),))).fetchone()
        if not row:
            await db.execute("INSERT INTO hw_state(tg_id,wager,xp,premium,claimed,counters) VALUES(?,0,0,0,'[]','{}') ON CONFLICT DO NOTHING", (int(tg_id),))
            await db.commit()
            return {"wager": 0.0, "xp": 0, "premium": 0, "claimed": [], "counters": {}}
        try: cl = json.loads(row[3] or "[]")
        except Exception: cl = []
        try: ct = json.loads(row[4] or "{}")
        except Exception: ct = {}
        return {"wager": float(row[0] or 0), "xp": int(row[1] or 0), "premium": int(row[2] or 0), "claimed": cl, "counters": ct}

async def _hw_save(tg_id: int, st: dict):
    async with get_db() as db:
        await db.execute("UPDATE hw_state SET wager=?, xp=?, premium=?, claimed=?, counters=? WHERE tg_id=?",
                         (float(st["wager"]), int(st["xp"]), int(st["premium"]), json.dumps(st["claimed"]), json.dumps(st["counters"]), int(tg_id)))
        await db.commit()

async def hw_track(tg_id: int, kind: str, amount: float = 0):
    """kind='wager' (amount=⭐) или имя счётчика (case/upgrade/…)."""
    try:
        st = await _hw_load(tg_id)
        if kind == "wager":
            ton = float(amount) / float(STARS_PER_TON)
            st["wager"] += ton
            st["xp"] += int(ton * HW_XP_PER_TON)
            st["counters"]["wager_ton"] = round(st["wager"], 4)
        else:
            st["counters"][kind] = int(st["counters"].get(kind, 0)) + 1
            if kind in ("case", "upgrade", "door_game", "pumpkin_game", "mines_game"):
                st["xp"] += HW_XP_PER_GAME
        await _hw_save(tg_id, st)
    except Exception as e:
        print("[hw_track]", e)

async def hw_event(tg_id: int, game: str, result: str):
    m = {"case": "case", "upgrade": "upgrade", "doors": "door_game", "pumpkin": "pumpkin_game", "mines": "mines_game"}
    if game in m:
        await hw_track(tg_id, m[game])
    if game == "upgrade" and result == "win":
        await hw_track(tg_id, "upgrade_win")
    if game in ("doors", "mines", "pumpkin", "upgrade") and result == "win":
        await hw_track(tg_id, "any_win")

def _hw_level(xp: int) -> int:
    return min(HW_MAX_LEVEL, int(xp) // HW_XP_PER_LEVEL)

def _hw_view(st: dict) -> dict:
    spt = float(STARS_PER_TON)
    lvl = _hw_level(st["xp"])
    cl = set(st["claimed"])
    bosses = []
    for b in HW_BOSSES:
        done = ("b:" + b["id"]) in cl
        bosses.append({**b, "progress_ton": round(min(st["wager"], b["need_ton"]), 2), "ready": st["wager"] >= b["need_ton"] and not done, "claimed": done})
    levels = [{"lvl": l, "free": hw_free_reward(l), "prem": hw_prem_reward(l),
               "free_claimed": f"f{l}" in cl, "prem_claimed": f"p{l}" in cl} for l in range(1, HW_MAX_LEVEL + 1)]
    tasks = []
    for t in HW_TASKS:
        if t["key"] == "boss1": prog = 1 if "b:pumpkin_king" in cl else 0
        elif t["key"] == "wager_ton": prog = st["wager"]
        else: prog = int(st["counters"].get(t["key"], 0))
        tasks.append({"id": t["id"], "title": t["title"], "need": t["need"], "xp": t["xp"], "progress": round(min(prog, t["need"]), 2),
                      "done": prog >= t["need"], "claimed": ("t:" + t["id"]) in cl})
    return {"ok": True, "stars_per_ton": spt, "wager_ton": round(st["wager"], 2), "bosses": bosses,
            "pass": {"premium": bool(st["premium"]), "price": HW_PREMIUM_PRICE, "xp": st["xp"], "level": lvl, "xp_per_level": HW_XP_PER_LEVEL,
                     "xp_in_level": (st["xp"] % HW_XP_PER_LEVEL) if lvl < HW_MAX_LEVEL else HW_XP_PER_LEVEL, "max_level": HW_MAX_LEVEL, "levels": levels},
            "tasks": tasks}

@app.get("/api/hw/state")
async def hw_state(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    st = await _hw_load(u["tg_id"])
    return {**_hw_view(st), "balance": u["balance"]}

class HwBossReq(BaseModel):
    boss_id: str

class HwPassClaimReq(BaseModel):
    level: int
    track: str = "free"

class HwTaskReq(BaseModel):
    task_id: str

@app.post("/api/hw/boss/claim")
async def hw_boss_claim(body: HwBossReq, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    st = await _hw_load(u["tg_id"])
    b = next((x for x in HW_BOSSES if x["id"] == body.boss_id), None)
    if not b: raise HTTPException(400, "Босс не найден")
    if ("b:" + b["id"]) in st["claimed"]: raise HTTPException(400, "Босс уже побеждён")
    if st["wager"] < b["need_ton"]: raise HTTPException(400, "Оборота пока не хватает")
    reward = int(round(b["reward_ton"] * float(STARS_PER_TON)))
    st["claimed"].append("b:" + b["id"]); await _hw_save(u["tg_id"], st)
    u["balance"] = int(u["balance"]) + reward
    await save_user(u)
    await add_history(u["tg_id"], "bonus", "win", f"Босс «{b['name']}» повержен", reward)
    return {**_hw_view(st), "reward": reward, "balance": u["balance"]}

@app.post("/api/hw/pass/buy")
async def hw_pass_buy(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    st = await _hw_load(u["tg_id"])
    if st["premium"]: raise HTTPException(400, "Premium уже куплен")
    if int(u["balance"]) < HW_PREMIUM_PRICE: raise HTTPException(400, "Недостаточно средств")
    u["balance"] = int(u["balance"]) - HW_PREMIUM_PRICE
    st["premium"] = 1; await _hw_save(u["tg_id"], st)
    await save_user(u)
    await add_history(u["tg_id"], "bonus", "buy", "Хэллоуин Premium Pass", -HW_PREMIUM_PRICE)
    return {**_hw_view(st), "balance": u["balance"]}

@app.post("/api/hw/pass/claim")
async def hw_pass_claim(body: HwPassClaimReq, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    st = await _hw_load(u["tg_id"])
    l = int(body.level)
    if l < 1 or l > HW_MAX_LEVEL or l > _hw_level(st["xp"]): raise HTTPException(400, "Уровень ещё не открыт")
    prem = body.track == "prem"
    if prem and not st["premium"]: raise HTTPException(400, "Нужен Premium")
    key = ("p" if prem else "f") + str(l)
    if key in st["claimed"]: raise HTTPException(400, "Уже получено")
    reward = hw_prem_reward(l) if prem else hw_free_reward(l)
    st["claimed"].append(key); await _hw_save(u["tg_id"], st)
    u["balance"] = int(u["balance"]) + reward
    await save_user(u)
    await add_history(u["tg_id"], "bonus", "win", f"Хэллоуин пасс · ур.{l}", reward)
    return {**_hw_view(st), "reward": reward, "balance": u["balance"]}

@app.post("/api/hw/task/claim")
async def hw_task_claim(body: HwTaskReq, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    st = await _hw_load(u["tg_id"])
    v = next((x for x in _hw_view(st)["tasks"] if x["id"] == body.task_id), None)
    if not v: raise HTTPException(400, "Задание не найдено")
    if v["claimed"]: raise HTTPException(400, "Уже получено")
    if not v["done"]: raise HTTPException(400, "Задание не выполнено")
    st["claimed"].append("t:" + v["id"]); st["xp"] += int(v["xp"]); await _hw_save(u["tg_id"], st)
    return {**_hw_view(st), "xp_gain": v["xp"], "balance": u["balance"]}

# ---------------- Тыквенная грядка ----------------
PUMPKIN_GAMES: Dict[str, dict] = {}
PUMPKIN_CELLS = 12
PUMPKIN_DIFF = {"easy": 2, "medium": 3, "hard": 5, "nightmare": 8}

PUMPKIN_RTP = float(os.getenv("PUMPKIN_RTP") or 0.92)

def pumpkin_mult(rot: int, n: int) -> float:
    m = 1.0
    for i in range(n):
        m *= (PUMPKIN_CELLS - i) / (PUMPKIN_CELLS - rot - i)
    return round(max(1.0, m * PUMPKIN_RTP), 2) if n > 0 else 1.0

class PumpkinStartReq(BaseModel):
    bet: int = 55
    difficulty: str = "medium"

class PumpkinPickReq(BaseModel):
    game_id: str
    cell: int

@app.post("/api/pumpkin/start")
async def pumpkin_start(body: PumpkinStartReq, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    diff = body.difficulty if body.difficulty in PUMPKIN_DIFF else "medium"
    bet = int(round(float(body.bet or 0)))
    if 0 < bet < 30 and float(u.get("balance") or 0) >= bet * float(STARS_PER_TON):
        bet = int(round(bet * float(STARS_PER_TON)))
    min_stars = max(11, int(round(0.1 * float(STARS_PER_TON))))
    if bet < min_stars: raise HTTPException(400, f"Мин. ставка 0.1 TON (~{min_stars}⭐)")
    bet = min(bet, int(round(500.0 * float(STARS_PER_TON))))
    if int(u.get("balance") or 0) < bet: raise HTTPException(400, "Недостаточно средств")
    u["balance"] = int(u["balance"]) - bet
    u["games"] = int(u.get("games") or 0) + 1
    rot = PUMPKIN_DIFF[diff]
    gid = uuid.uuid4().hex
    PUMPKIN_GAMES[gid] = {"tg_id": u["tg_id"], "bet": bet, "diff": diff, "rot": rot, "bad": random.sample(range(PUMPKIN_CELLS), rot), "opened": [], "active": True}
    await save_user(u)
    await hw_track(u["tg_id"], "wager", bet)
    return {"ok": True, "game_id": gid, "bet": bet, "rot": rot, "cells": PUMPKIN_CELLS, "next_mult": pumpkin_mult(rot, 1), "balance": u["balance"]}

@app.post("/api/pumpkin/pick")
async def pumpkin_pick(body: PumpkinPickReq, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    g = PUMPKIN_GAMES.get(body.game_id)
    if not g or g["tg_id"] != u["tg_id"] or not g["active"]: raise HTTPException(400, "Нет активной игры")
    c = int(body.cell)
    if c < 0 or c >= PUMPKIN_CELLS: raise HTTPException(400, "Нет такой тыквы")
    if c in g["opened"]: raise HTTPException(400, "Уже разбита")
    if c in g["bad"]:
        g["active"] = False; PUMPKIN_GAMES.pop(body.game_id, None)
        await add_history(u["tg_id"], "pumpkin", "lose", f"Гнилая тыква ({g['diff']})", -g["bet"])
        return {"ok": True, "boom": True, "cell": c, "bad": g["bad"], "mult": 0, "win": 0, "balance": u["balance"]}
    g["opened"].append(c)
    n = len(g["opened"]); mult = pumpkin_mult(g["rot"], n); win = int(round(g["bet"] * mult))
    if n >= PUMPKIN_CELLS - g["rot"]:
        g["active"] = False; PUMPKIN_GAMES.pop(body.game_id, None)
        u["balance"] = int(u["balance"]) + win; u["wins"] = int(u.get("wins") or 0) + 1
        await save_user(u)
        await add_history(u["tg_id"], "pumpkin", "win", f"Грядка зачищена x{mult}", win)
        return {"ok": True, "boom": False, "cell": c, "mult": mult, "win": win, "auto_cashed": True, "bad": g["bad"], "balance": u["balance"]}
    return {"ok": True, "boom": False, "cell": c, "mult": mult, "win": win, "next_mult": pumpkin_mult(g["rot"], n + 1), "balance": u["balance"]}

@app.post("/api/pumpkin/cashout")
async def pumpkin_cashout(body: DoorsActionRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    g = PUMPKIN_GAMES.get(body.game_id)
    if not g or g["tg_id"] != u["tg_id"] or not g["active"]: raise HTTPException(400, "Нет активной игры")
    if not g["opened"]: raise HTTPException(400, "Разбей хотя бы одну тыкву")
    n = len(g["opened"]); mult = pumpkin_mult(g["rot"], n); win = int(round(g["bet"] * mult))
    g["active"] = False; PUMPKIN_GAMES.pop(body.game_id, None)
    u["balance"] = int(u["balance"]) + win; u["wins"] = int(u.get("wins") or 0) + 1
    await save_user(u)
    await add_history(u["tg_id"], "pumpkin", "win", f"Забрал x{mult}", win)
    return {"ok": True, "win": win, "mult": mult, "bad": g["bad"], "balance": u["balance"]}

# ---------------- Реферальная карточка с фото ----------------
HW_SHARE_JPG = _hw_b64.b64decode("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAYEBAUEBAYFBQUGBgYHCQ4JCQgICRINDQoOFRIWFhUSFBQXGiEcFxgfGRQUHScdHyIjJSUlFhwpLCgkKyEkJST/2wBDAQYGBgkICREJCREkGBQYJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCT/wAARCALQBQADASIAAhEBAxEB/8QAHQAAAAcBAQEAAAAAAAAAAAAAAAECAwQFBgcICf/EAF4QAAIBAwIDBAYEBwgPBQYGAwECAwAEEQUhBhIxBxNBUQgUImFxkTKBsdEVI0JSkqHBFhczQ1NicrIkJTREY3OChJOUorPC0uEYNVSDo0VVZHXw8SYnNkZ0w+JlVv/EABsBAAEFAQEAAAAAAAAAAAAAAAQAAQIDBQYH/8QARBEAAQMCBAMECAUDAgYCAgMBAQACAwQRBRIhMRNBUSJhcZEGFDKBobHB0RUjUuHwQlPxM2IWJENykqIlgrLCNDVj0v/aAAwDAQACEQMRAD8A8wU1L7ThR1p6m1UGc5Hhn661pmlwDRzKkQnYY+7XwzS6FCimsDRYKwBCkTDMTfCl0dJzcwISso1rszA+VSajTHFyCOu1SSQPuqml0aWdCot6I6AoUYGaKAU0zfD8WnxNJhde6G/Qb1KZQ6EMARiqxSQaDqCYZc/VQfobpUr8znP1U7Bbl+V8gClWVstxMyucAIzfKpKIsYCqMAVGnpjIeLJt9Umsv2ij99EaBo60lYol5+R9dIP8GFHU+AqW8SyY5hnFQxtcAeAbArNqGljy79WiqcNVMiHIir5ClU086JnJyfIdaj+syZOCMe8UQ6pjis1TLgFIkmVJFXpuCTSZSbw91EBhd+YnanLVIJVY3QJbO3UbVLVLZf4H6WPf0q6OF8zSS4ZTy5+CkGl3PRQ4rR42UkrtUrfFLxQx7qLigbGLNVgYBsm2oqbFwkkhjXJI91SI0yPaBqLCJD2SmGuyapSjNPCNcjak3A7q3kdcKyjINXGItaXnkpZbaqnuP7ok/pGkUZYuxZjknc0Vco52Yk9UEd0M0KFCopkVClwqHkVT0NSjEpGCoq+KmdILgqQbdRkiLrkYp6KPk6mnQoAwBRkYoyOmDNeamG2SaFE5wpI8qjd9J+d+qnkmEZsUiQEUv8I3xpNGTzEk9TRVmO1JKqKAoUeKLFJJChQoUkkKFChSSQoUKFJJChQoUkkKHhQoUkkM0BQoUkkKFChTpI6KhihSSQo6IUdOEkKFCgKdJCjoYoU9kkKFCh9VJJCjBoqPNSCSOhQpWBUwLp0g0BS8DyoKu/SkGG6VkQoGnOUeVDl91W5CpWTdFTnL7qIqPKmLCmskYoYpRHuosVAtKVkWKFHihio2TWRUKBoUkkKFCipkkKGKPFDFPZJChijxQxTgJIUMUeKMKD1qYansk0dK5R5UfL7qlkKeyboU5yjypJFRLCE1kihSuWiIpi1KyGKOixR4pAJIqPFDFCnskhQo8GjA91OG3T2ScUoUtUBG4pQQZ6VY2IlPlTdKpwIPzae7lM/Rq9kDipBpTK9BSkqQsCFRhDTsVsmTlKOipHmytbGVGAPWlcpPSpqW0RYewfnT4tIfCP8AWaPjw95G6tEJKrOSjCmrMWcX8n9tD1SL+T+2rxhz+qlwCqzlNDlNWPqkX5lH6rF/J/rp/wAPf1TcEquKGi5KsfVovzPtoeqx/mU34e7qlwSq7lowhqw9Vi/MNGLaIfkUhh7uqfglVfIabdd/fVt6tFj6FMyW0fMfYqiTD3AbqBhKrCMUk7g1YSW0YH0DTRgTlPsHpQL6R4NlUYyoNJqUYV/Npto1DH2aBfTuG6rLSmDRU6VXPSiZQB0od0ZUbJrFFS8UWKqLVFJo6FDFNZJChQqwvdKntLKyuJIWQTqzZPjvt8NqtZC54c4bDX4gfVTbG5wJA0G/yVcaKlEb0WKpIVaTQo6GKjZJCioUdMkioqVihilZJJoUrFDFKySTQpYjdhkIxHmBTkUOSedfhmpNic42SAJSY4iwDeFPcppagKMDYUeMmtFkAaFaG2RCm5B7VPHCjoT8KZkyWzykDHlUpRpZO4Js7UDRnpRGg3BVoqFHilhR5UwbdIC6SBihSsDyoYFWZE9kjFHSuWhilkSsk0KPFDFNZJJoUdAimTJqMlH5T40tG/sjp12pU6EqCoyfdTETYkUk+PWqHXicGd6WxsptFQot+YjG1aKsR0dDFGBTgJ0xPESTJn6I6UcMveyY5cHHnTkqlo2AGTioJBVsHIIoGdxhkBGx3VZ0Kscb4pQXFOrEZIu/RSY/zsbUmtYMtqr8tk3K/dxs2M4qszvVjdEervvVd41kYk78wDuVMm6urLTDHpi6mZQRI7QCMLuPfmm8Yq2LQRcKWMCSoZzIZHQMCwznfHyqr5SfCt007Io42t5tBPiRqjp4mx5Q3oD7yNUjlo+X30vkPlQ5cVXkQ9k2+FUt5DNVjHmYsPE5qbfSGOMBTgk7+8UjTbOe5lMscLPHDhpGA2QeZrLqwZZmwtVbgXOyhLXS+YA971Gfo0v1WKzXnkIOTsSKnclQXtZrqVWmQooOM+YrRko2QgcJl3ct7e9WmMN2GqloIZI0YICCOvnQwqfRUCnpAieygAAGwFMNvWg9uQWNrq0iyIkDckAVHmv0icKqiTbOQ1HdSIkTKzAMynA86hW0IcksMj9tZVTUyB4ii3PwVD3kGwU2ziEXNITkyb/CpQbmGcVFBIGBtiia45Ni2KIjkZEwN5J2uDRZS+8VN2IA8zVdeXq3aLGkbDDZ3PWkXU4ePl5s79KkabZPnvZYvZKgoT9tCy1EtS/1aL2TumLi85QkR6VI6Zd+RvzcZpX4IP8ALD9GrHFJMqqcZ3owYbTNADh8VZwWBVF3Zm0KjnDcwPhimETnYLnGat7yFruH8UgkcEfECq+KB45faUjGQayKuj4c1mjsm38uh5I7O02TsaBFAwCR44pWaHSkNKqHBbFEXawW2CfZLpqWbu2xy52zTc05YkKfZx4UzkmhJqv+lig5/ROvOWGAMedNdaHWh40G55cbuVZN0BR0VHUUkKFFQpJIUKOipJIUKHhQpJIUKFKVacC6SIDagav7XRLqbhq4ultGYd6jq+BnkAbmI92cVQspFFT0j4mtc4e0L/FXSwOjDS4e0LpNChQoVUo6KgKOkkioChQpkkdFQo6dJFmjzRGjxThJCjAoUKkE6FChQpJI6FFmiBpXSR0BuaUqc+/lRhGB3U4qYad0rIAb70rFACl4Pvq9jVIBJ5ffSlX30pFOOlSrWynu2ZYYi5UZO4GPnREUDnuAaLlWMYXGwCihM+NHye+rB9LuYFMksPKiDmY8w2HzpmK6sO9TvT7GRzYBzii/VQzSUhp79FYYS02fp46KLye+iKe+rZ4dHaRjHqsKoSeUGN8gfKmnt9PLAR6rakeJZXH7Kk6laP62n/7D7qw0rhzHmPuqwrtSeWpKRC4mdLc97yhm2/NHj8qT3ZH5NBmG+o2Q5ZzUfloEU5ynypJU56VQWWULJBWixSyDRctVlqayQRvQpfKT4UXKfKmyprIgKMDehynPSjC+fWkGpWRYowKHKc04FOelWNbdSASQlLCb9aWsTEqMbsfZ9++KkLbrHNJFPNBE6Eqyu+CCD0opkG19FY2MnZReT30OWpvdW+P7stP0z91AQ25/vyzH+Wfuq807f1DzCs4JUArRFalyW7Kx5cMp3DL0I8xTbQsPyardTuG4VZYQmMYpJXPjTxRgOlJ5T5VQ6NQITRXBwaKnSp8RSCpB6VUWWTWQAoYztR491KVTSDUrIuWlBPfR8jeRp4QkgezRDISeSmGpEcWQd6cjhy30v1U7HCwXZafihYt9CtCClvbRWtjTIt/536qk+rY/LHyp1YWA3SpCwlj9GtiCiGuiIbEmI4PYHtD5U6tv/O/VWn0js54q1uwjv9O0W6ubWQkJKnLgkHB6nzq60Xsq4qi1exN/w9cC1E8Zm70py8nMObPtdMZotvAZoSLjvRDIeVlgFgKtnm6U8q5BORt769WJwPwZBOJk4f0pSpJU91nH1E1K/APCRH/c2kf6qv3UI3FGN9lhRQpTyXLrb0d2uLSC4HEiL3saScvqh25lBx9L31Ig9HKNlPe8Snmz+RZ7Y+tq636/agBVlRVUAAAYAHhSo5o5BzI4IoA4hVW9r4D7K71cW1C5IfRuh/J4mf8A1Mf81EPRvTP/AOpW/wBT/wD8q7GLiMbc1KFzGR1FV/iNV+r4D7KHBHRcb/7N0X//AEz/AOpj/moH0bo/DiZj/mf/APlXY+8XHX66HeqN+el+JVX6vgPslwR0XGv+zcDjHEpHxs//APKmb70cDa2Vxc/umDdzE8vL6n9LlUnGeb3V24TRHrIKXmKZGjfldHBVlI2IOxFN+J1Q3d8B9lExDovFK2xIyWFNvaEk4cfKvYS8BcJDYcOaUPjAKVccA8M+o3Ig4c0oymFwmLZc83KcY9+cVqOxynOmQqosbZeN5LPYZb9VNtaDkb2/Dyro0vZDxtsBw3e/NP8AmrP8Q8Iazwu8UWsadLZvcKWjWQjLAHBOxNa2SlkdaNwJPeoGEcljza/z/wBVR3tzzH2vHyq7Ns38nUSS1kLt+LPWs2pw/QWHzQr4LKpmgwR7X6qaaP2SM1Z3FuylRyeFR5ISEPs1iz0ZaTohnR2Kgd376Ty52qTyN5U0FPiKzHxWVJamiMGipbKc9KSVI60O5tlAhTNPurO2nD3Vgt0nLjkMhXJ89q3mua3o6aPYytp8d9BKMwRNzKAFGDv7ulc299PzXkk9tbwMTyQBgu/mc1qUeJvp4nx2Go00G9x9L7rTo8RdTxSRgDtDoOo89L7pNxLHLIWihEI39kHNM0KGKyXkuNysxxubojQofVQxUbKKLFDelAe6himskioAUeM0eKcNT2STS4Iu+k5M42zmix7qsdOtnMbME3zjNE0lMZpQ22inGzMbIJF3UfJzZx40wTmpksMi82UOwqLFC82yqTjrWpNGQRG0K9zbaBIHWnAKdNjIFLcp23qZplqrxs8kYO/sk1OCikfIGWtdOyJznWRW+mlsMZMAjypu6i7mQx5ztVv9EDHSqrUI3EpdVYpgZatirpI4YewETLGGM0VXLb90AebOfdTRp+fcD40/ZadLNJFI0RaEtkk9CK5j1d0smSIIDIXOs1QKnppxKhjINxnpVqNPtQ29vHj4VCvI2GBAPZOebFaQwrgNLpe14XV/q+QXdqodyiREIoOepOaZAyaXLG6sOcEHFEinNZzx27WsqTuhy0DH76fhBjKzMUVEdcs+4Bz4j6qVcQSQyFJF5W64+O4+2rRBdt7fz+XUshtdQyuD1omGKdZfaNIYHbNDPZZVkJGKIilYoiKpIUU4ahyqFkIFTKj3X5O1RrG3ZfoncNEq2dnyGJOMVINM2o/FE++nqtpgeGLqTdkM0KLxox8KvCdHVfJtK/xNWNR736C/GhqyPNHmvsmeNFZ2Fwv4HEOMEE7+e9MSsI0Z8ZwM1GtbhYLR87knYVFlmaZ+Y7DyFESYgGQMbucoCm+a7RfeyOWVpWbchSc8uaaxTkMLTvyIMnx91JdCjFT1BwaxXte4cQ7HmhjfcqTYD8f/AJBqxUVTRZ71MH8ofbVreSiCM5+k2QBWxh8oELidgiInWaUdxcJbIC4Jz0AqDJfuzkocL4DAqKxY9STUixsp72cW9vH3kpBblyBsBk9fdQsldNO8Mj0HQbqOd0hDWDfompZXmxzknHSrDT9YTTtNu7VLYtNckAy8+yqN8Y+PjUm00qGJHGpxurEjuzE4O3jnGaUbPTYT3sDzmVfohtxmjKegqmOE4cAT13tsdD3K+OKWPtggGx331059ygRet6nzKnsgbHHQ586snbkjVdiwGCaaWQjocfCi58nfpWjCzhNN3EuO5/bknb2R3oyaIih4UYqYSVdqf8JGPcaFmCYzj86kahvckZ6AfVUbmK+Jrm5phHVOeQhXOs8lSpboLkJ1z5Ux3rMcnerPToIwCGXmZgGyy4AoTWwkYkqi42GB1FFuo55GCTNvyUzG4i6hWlqb2YpzhMLnpmrCVbu07mOPLxqArNy++moYDA5YN4Y2FPGVyMcxx8aJpYGxxm9w88weSmxuVveltNj6NNMcnJ60XSi5sUU6QndSJupNq5UkedR7i1kQtLsVLE7eFLiflIajkmLgr4GpuyviyuO2yc2LbFRMZqHKD3h5jvVgRvTUNsEJLb586yKinMlmhDuaSkWttG8YkbJOTt4UV5DHGgZRgk491SwAowBge6j5Q3UZ+NXeqNMWQAX6qeQWsqmhVrKi90+FXOD4VVspXqKzKmlMNtbql7MqKhmhSlx0NDgXNlBJoUbjDEUVIixskhQzQofGmSQoUKFJJAVe6Vb8PHu3vr27UgAvGsPsk+IyN8VRUatiiKWcRPzFod43+4V0MojdmLQ7x/yF2u10+zh05bOKAC1KFQhzup3+PjXNtat+G4mJsLu7bYYQR5AOdwScGlWmvTRcK3VrzsHNwgV+YlgCMnB8Po/rrOs5Y710+L4vDNFG1kY28uVht0W9iuKRTRxtZGNvLlYbdEc3diRu6L8n5POBn68UiiJoVybjc3XNk31R0KFCopIqFHQxSskiFHQwaGKVimQFGd8YoqLNODZOlUVD66KldJHR0FpXhUwLi6cJB60MeNKjUu4wKk4HkKnFCXi6cNumoPomnDR4x0FDFGMZlGVTAsEg07gedBImkdUReZmIAA8TT63g06GYw3CesNhAFXPKM77kYq1ga0FzzYfzba6sY0HV2gTkVnILR7xonaBASWBwDvjGfOot5fW0tt3MFvIjFgSzyc2w8AMVGuLue6YvNK7seuTTBoeor7jJCLC1je1z9vC/vTPlFrMHnb+BHQoUKzEOhQoUKSSmaTfR2F00ksbSI8TxkKcEcwxmrGe15LW2ukVxFcqzJz9dm5eo28KoquOGb1YdYtFuJ+S3BYEOTyjKnG3xxWph9VZwgf7JPkSRqjaV4eRC/YnQ9Lka+SjsuPGm2G9TJImhcxyKA6HlYeRHWmmjJJIFHSQHkqy081HIoiNqmxpFDbzzzRtJ3ZQBQ3LnJPj9VLmijubdLy2iMcBxEQzZIkAyR7/PNVinvpfW17d211LhEi4Vfihy0+UI8BRxxtLKkagZdgo+uocA3sq8qj8tALVgsVpOkyRGYTwRl3LcpRiCAceON6jch8hTmDYg3UnRltk0FpxhyRs/XlxS0jLMFAySQAPfT2pSJbabJYswW6S69tANwAuOvxq3hZInSONrD49E7Y+ySUILuwaOD1mSeKSAk4jQMHGc9SRiq/UrlL2/uLlEaNZZC4UnJGajZz45oZrKmrXzNDHAW0+AsPgq3zlzQyyFDNA0VBqlWsOr2ywxJLZM7IgXmExXOPdiltfrfyRQ2tmYzuoVWLsxP1VT0pHeNg6MysNwVOCK0WYpNo15u3wA+NkR6w4jKdvAX87K9n026gtjPcW8sKBgoMiFck+WfhUTu/GmbXWby1cyCdnbGAJfbHybIq0HJqt2PVpLfvJ2ysQPKcnwxjzzWtDLBUjsaO6Hn4fayvtHJbh3v0P8+irSKQ1SXQ+AFNsvnVEkRCoLUzilIMEE05GvXaloPaXIHWoxwk2KQagBUhQMCjVOboBUhEOAMDNa0FMVexiTEuxx51KiQK/vxTlquAQVHWt1ovZNxNrWn2mqWlram1uAJI3e4QEjPiOvhWwyJkTA57gPFGRxE7LFxokmxdR9dd3X0e9HVFkGuaicqG2hTxGfOtu2m6cn0tNsAVAyfV08vhTcutyMhAwpO2c0HLWTS24PZ6rVioTz1T3C+mRcIaJDo9vJLcRwFyJJQAx5jncCn7yaS45eY9M+FZu4u2DtmVyfMGqvVtegskjWaaVS7HHLk9KhHQukfm3JWjHSW2Wr5WBxynHnQ7zDYwawq8R20zBEuZ+ZthnIp0TTOOZJpTnp7Roo4c4e0be5ECDqVuM+W9Pw3MsMZCx8wznNZDUeM49PtHa3gMksYA/GnAPn0rHapxNeazd+sFzD7IXkhdgu310oMKlm30CZtK5267ZBK0p3TlyKfXY9Olct7Pbu5e/u0eSZwYVOWckLhvfW7jlYSoOY/SHjWfWURhkLL3Qs1PkcRdW5fc0ZJOM+XlUfmp9SMA1nltkKRZNy8y8pGRvS4bhlY7c2aRLliB4VmOIeGtR1O/FzaXIjQIq8veMu4+FXwRMkOV7rK2NjXnK42W4Vz9Rp+OUgAbiqctyKoLb4HjT9veRqoRmJYmg3RaIV0OmiuOUN1FZPjjsw0zjy6s572/u7Q2sbRqsCKQ2Wzk5rSW8mSd87U8GJNVRySQvzxmxCFLSDovPXax2X6RwFpen3Vhf3tzLcztEwuAgUALnIx45rlEgGTv8AKvbU9vDdKFnghmVTkCRA4B89xXOe1vs3v+Lo9KTQrbT4/V2lMxbkhzzcuOg36Gujw/Gy7LDUd93E/wA8E1yRYrzBdJzFdz0qJNB+KJJNbvjTgbVOCJ7aDVRal7lGkTuZOcYBwc7bb1lJ0LRkcorVlp45mmRhuD0VEkW6pu6HnUYqD41bGFs45R0qD3RHgKwKmkLbaIF7LKG4w2KRJvipUi4Y7CmZeg2FZE0RF1Q5qjGhThGRjFIIoJzbKshFQxQpyIqrKzDIByR50mtubFMAm8UMip1y6XriO2hC752GM1MS2h0lLmKeRHlmgwAVzyn3GjW0Jc42d2R/VyV7YLk2Og5qnxtSTsadOPCix7hQro+iqskKM0eKVj4UMUwamsk1baROGLRBfNs1V4rQaJYz2lyzzIgUpgYIO+a18GhkdUDLtzRNKxxeLJUycwI8ximLW17hmIbmyKt5eUyN7Ipsx46AV1clC0yZ77LRdCM11DCgAjrmpNuqpGAoAGaVye4UoDHlVscWU3UmssbplwObJzTF1GJIih2DbZqSwyaQetVyMDgWnmoubfRVbaahwOdjvVlbx+rWyrnIQfOldT0FIkb2WXfcULDSxwEvYNVW1jWXITU10WGFGPfUct50icFSAu/nUdGZZN+maz5ql2azkO+Q31RXm8gOfDpTSDerAYPUD5U0sBQksBuaCkpSZM45qtzLm6ZuFJ0m4IHSWPPyapmqRsl44b6WFyPL2RUbXJo/VLGBGwREWkUbZJc4J8zinrNxLpNuze04kkDMdyfo43qMbmmofB0A18L/AP8A18FaQNYxyAP8/wDL4KGw9o024BxUp0JYnl2pqVcY2qiaK10O5qjkYpJG1OsNjSCKCeyyqISIZDISDjbypF3+T9dMBsUuWUSheuRQDpw6ItO6hmuLJ6yJIZfAHNSStQIZTE2x9k9RjrUyK4SVwg5gT0yKKpJmFgYTqpsItZGRQXrToQnpikYxRxjI1U7IxUGecyeyQMKfCn57ju8qM8+NjULcn31m1s9/y2nxVb3cgj6gDypyG3eeVI1VmLsFAUZNLtLb1i5SIuIw2SWxnAAzVtCVtUEcMrsMluYjlOTT0NDxu0/Ro/bRPHFm7R2SPVxaMYwvKykqSRv9dVl+gSfbPtDmOfOrZjznJ3NR7yKM27NISMdCBnetSupg6EhmgGoV0jARoqoHFOSTSXDgucnGKZqRZwm5uYrdWRGlYKGc4UZ8zXOxFzjkvoUK0EnKOaUls7A90jSMOoUE4q4tljsIEb1RRPLbkGRmbmHNkE46dKGnKLC2uG9Yfvp1CciqQBhs7nPupDyM7ZclifEnNdJS0bYQJD7R5aG2u/j8kfGwRNDhufDT902dvdRZpYFEUz0q8tPJQsmjKckDG1LDAmkmEnfIp5ECj31BjXk6pgCgo2pu9la3hDoBktjepMcfM6g+dQtYuYWLW6qweOTfI22qVW7h07nE2PLxUn6MJVXnLZPjVrbadbS6Ulw4kMjTOmzYGAAen11AtLV725it4yoeVgq8xwM1covqtgtmxBkjmdyV6bgDH6qx8Lps7nSSNu23Proq6dgIc5w0+uiQW2AAAAGAPIUgtQNERWw5xUyURO1JNKKkDrUea4WIlMHJHUUPK8MGZxsoONt0svjxFKHtUxDC4fmlPMcYG+afZljXJOBVUby4ZniyiDzKMnAoU0ki3G4U+z50vvF7xUOctvU2yAi99E4KVRUvA8KbkPN7AB38fKpO0F050Rg5pSnemoUMaBTufdTg67UmEmxO6YFK6gimJrWM4+l86fApQFTfEJBZwUi2+6qJFCyMo6A0uGIOOYk7GhckC4k/pUuGRY4zzHqawY2NEpDthdDAC+qE0AVWfOfKo9PTTCTZScUzmoTlmbsJnWvohQoUKpUUKFGil3VB1YgCgylGKnqDilY2ulbmioZ8qFCkknFuHWBoAfYZgxHvHT7abJoUMVLMTukSTuhQoUKZJCjAowKk2NlLfXKW8IBdzgFjgD4mroYXSODWi5KdrS42CYC52pQjrZaXwdFBIkl66zYIPINlx7/Or9tE0NDn1G1A8iorrqf0SqXMDpCG9x3+C2afA5pW3cQ3xXL+7NAxmurQ6dw4pAksrRh/QUftpGqcGaPrSLJpcsVk0aEciR5V2zsSc7eVKb0WlYLtIKIk9HJg28bw49BuuUslIIrWa1wRqej2rXUywyQKF5pI3yAScAb4Of2Vm3hIrnqnD5ITZ4sViT00kLssjSD3qN0o6UVxSOnnWc5tlQlJjO+ce6pPcp5moyNyncUvvR5Gr4XsaO0ptI5q40JbO3vxJKzAhW5c45Rtvn6qhSKiyMI2LICeUkYyKYhkDOFHjQadVJG9aPrLOCGAAAE/GyuMoLA2210tiBQXrSA4fcUoNg1UHgm4ULq3skuLe0h1DT4DNdrcFMFOdVHKCDj4nrUe5s9N9oRm5abO5ZgFB8cDHnUNZSOmR8DR8499GGaNzbObfx2HUjvPNEGYFuUD+dfepCWFjIBzrOpAwe7Yb+/ekahpdvDZxz2xnJMhRlfB8M52pUEgC9PGpcN0VzyMyn3HFXNpaeZhaWgE87bJwGOFiFnmBU4Ioq0GuWz6hC+rCaMlAkcsZ2YHYKR55/ViqACufqqYwSmPfoeo6oaaIxuty5d4QoUZBFFQxFt1SgaCsUYMOoOaFFTJBaGxuo9cu51e3CXMoMid255SRuRg+7NKuLRrWTu5UZGxnB8qzgOKs9N1GKG2mguWkwzKyFV5sEZyOvjmugo8Ta8COca69r46i3xujmTtk0fv1/nzUu7RRpVy2Tnni+1qZ0vUbdbC4s7oqqKDNCQDnvdhjI8MZoXWqW50+a2hLOZmTJZMYCknbfrSNP4Z1DU7CW/toka3iyHYuARgZO3WmnmeaprqTtkN1A15n7hWAvLwIRmNjcb8z/lE1zak7S/qNS7K+srOG4u47nF7EFNsOXILc25IxjYZqgxVpZ8Najf6ZNqUESm3hyWJcA4AySB41RDX1MjiImAmx2BuO/fkqad8jnXjbcgHrp3+7dPaEvenUmb2v7FZiQOmXWlmAE7ZqLo2pxafHepKHIuYO6HKM4PMDv8AKpB1i3it5xC0wlkjKKQMYyR4591GUU9O2lAkcLi57+fzVjXMLG3PI/Mp8RQaa8E1/wB4qSKzxhCOYkfRyOuM1SXt3JfXc11Iqq8zl2CjABPlSJZ5JyDLI8hAwC7E4HlvTdZVZXcbsMFmjX/P0Q8s+YZG6BCjoqOgAh0KLNHQNOUkVCn7Kzlv7qO2gAMkhwMnA8+tW9vosFtEj3E7LdB88iYdOXwyfOjKSgmqdWDs9eX+e5XR075BmA0VEAWOACT5Cpem3c2l3aXUcKtJGeZedTgVdQL6o0ksc5LMhQcq8pGSN809Fqt3CcieQ/0jmteHBQxweZbEdwP1RMUAYQ4usR7/AKpeqabHCYWtY27toUkLBuYFiMn5VUtGpPvqZpsdvp19JcrM7I0UqBTHg5ZSPh1NR+7J32rRkaZBmLbHpcHprorZsru0Ba99EiOFSTkmno4Iyw69aOOPJPhUiOMjA8atp6caaKDWJIgCZ5c1Ngtw/KAGZiOgpMcTKDnBrr/Z92d6joev6RxBLe2b28YE5RObnwyHA3GM71rBrYGlxHgEdBTueeyFF7M+zbRuKNDurzUxfJPFc92vdycgK8gPQjrkmup2M1twzoltpNg7Olsvdx94eY4yTuRjzpviLibkuI7dBIAY/aO3n4Vhda4vtzayxWqzrMTy8zAYHn40PHTS1jruGhN7cguhpaINaC8LZXWpSXXM7vHkAnArCvxfqRI5ltiOv0D99Z6LUUXYh6m8OaNNr9+0EEkaMkZkJkzgjOPD41uRYfFTsc6TYdyPaWM05KXLxlqTylVSDfGAIs1Dv7zUNV7szW8n4vOOSEjrW30Ts+vbDVbO/kurRkhlDlVLZIHltWv1jiC04btFur+SVY3fux3a8xJxnp9VByYjDHI1tNGHHu/wqZKhrXWYLrlPCGjHU+ILS1uo7iOFyxZgpUjCk9SK6tBwXpcMaor3RAGN3H3VW3vaHo1rYC8S5kuAeXESDD7+40xo/ajpOpXHqvc3kUkmQnNGCDt7jWfWyVtSeI1haByQ8r5n6t0VxrD2llYBYfVpGXCKrFWJ+OKrE4J0jX+71G79YikdAvJAwRRj3Ypvgjgu80PUpb66ktHjkhKoEJJBJz4jyqx4i470rhq+NhdQXLzd2sn4oLy4PTcn3UJ+Y2Tg0ji49QoZnNOSE3PVOaXwbpmizyyWLXbNIgUh3DYGc+VPSwNFdcic2xHWofDvHOm8R35tLSK6ilWMykyKAMDwyD7605uUK8uDk0LUOnjktODfvVb5JGnt6lQYlkDgOBipgwANhSSu+dsilKjHG4FCOddUuddJ2ZgPM1IFojDJY/VSFiKHOR5U8m3vqtzuirc7ooV7pmfxkZOw3yaOLTsohLYcHepNzJ30fdt9E9aELBECqNhTiR+WykJH5bJ4A265Vgx6b0/HMXXJwCPKo/OXyTimLyZLO0nupObu4Y2kblG+AM1VkzG3NUlt991Pac48KQZ2O+1UfDvElrxHHM9qkyCEjPeADOfKrfAIOM++nkhMTix4sQk+IsOVw1VDxX2eaJx1PbT6sbwPbI0adxKEGCc75B8a4f2v8D6TwbrFlZaT60Y5bXvpe+k5zzc5AwcDwFekEbkHQmll45WQSRowUg4ZQfH30bRYjLTPBNy0X0voqi1eKmhUHA5vnVY1sB+dXbuKuxjWYTrGuyahpncIZrsoC/Ny5LYA5cZrkU8ZcDGAfOurcYatmaM3IVEsQKo54VEh60zLEoA61ZzW7CRgxFQ7iBl5dxWBVUmXMbLPfHa6gvGoBO+ab5B76ltC3KdxTPdHzFY0sBvsh3NUfFHilFTmhjFC5bKFkcMrQOHTGffTt5fS303ey8vNgL7IxtTBG1DFT4jw3hg6dFIOdbLfRHQzQoVFMgBmtFpugWlxpEd9KZTIzEFQ2F64qitIHu7mK3jKh5WCgt0zW4hsnsNBjtpGQuh3K9N2Jro/R+hZO975G3aAd9r6fRaWHU4kL3vbcAHz0VGdHsEPSXI/n1YCYIrOSAACSfdTDjDYO+Ki6pbHUXVXnZIF3EaqM5x1Jrc0pmONPGL9NB5nuV+kYJYNUxf8RwqXFsC77crkez8utQv3S3mPow/o/wDWlXWhZJa1YBFTLd42+RVP1rkMQxDEopPzXZb7W29yzJp52u1NlqdG1YahzxSjFwMsoRcAqOv11Y1jtOuvUr2C46d24Y7Z28dq1l1eQWunpfHnaGTHKANzmt3BMT41O4zu1Zue7qUdST8SMlx1HyRkA0lhvSXuES0F2xIhKh8keBpwYcAjfIzWxmY42B7/AHHmr99E2RSXHsn4VGn4gs4HeDu2kKt/CIQQdvClWupW16snI3dsuMK5GWz5edBCtpXv4TJAXdFVxGE5QdURAFRzAgbO+T76lkcxzTfJk9KoliB3Cqc26bCDwp/u447W6ndC4hi5wuSATzAftoLCxBO21HqU0VkLrS5HZrieFFXC+yM8rAk/D3VXNliic52hNwPGxspsZYF52+tjZZ7UZxdd3MIVh9kLyqSRt471J0i6LwtauECoTIp6Ek4GP1VEugFYJkHlXBx0qPGAHUnoCDXHmd1PVcXfryv1+KyxKQ/MVp7y3SCdo0JK4U79dwD+2oU8SjHWrN5ItUSa9tyVSMICrjByRjb5VAlUnGCMV1VTGxzS5moO3hdHzMF7jY7eF1EdAFOM02UGKkvGQpORimihIzmseWLW1kK5qr5oCrAIGIxTRUqdwR8ashTUtuZujAfGsqah3LPJUlnRQelORuYnEigEjwNHNAYMZIOfKmxsaBs6N1joQq9QVZ2lyJkPNyqwPQUi4k7pGYYz4A1BBZGDoSCPKlTTmcKCAMVoGvdwi13tdVbxNNd0iSQytzHrS7eNpZo41UszMAFHiabUA+6rHRxJHI97EeV7cqVb81idjQlNGZZmh3M/LUqMbc7gCn9NsD388lyDD3MbMoJx7XQD3+NGdulBjzEk7k7kmiroo2tjZkYPeitAA0BLBpjUCTbYUE+0M4FPAULgYtpfD2TUpmF8ThfkUxF2lUlWvDSRPrdoJghTLfT6ZwcfrxVUamaWvNqFoOv45PtrnaA2nYbXsR8wqKZ2WVjrXsR81aMuKQQTT7oS7H+cftpPIc12EkdyjMqbCHFGFJ8Ke7v30AtIQ23SypoJ50vGPClBaYuryO0IDBixGQAKaTJE3O82CRs0XKE92LRGbK867hSdzVEzl2LHck5NOXM5uZ2lbO/QE5wPKmzsNq5SvrHVDv8AaNvug5ZM5tyVhocch1O1dY3ZElXmYKSBv41ayxL3jH+cftpywsLnSLOaK4kA9ZCuqxtnYefxzScZ8a6fD6TgU4a8anUg8uXy1R7YjGwMcNdz3KM0e5wpxSGXB6YqV7qi31wlty82SxGwFNUMZGwvcbBVuAAuU3LIkakM6qcHGTUS0RppC8ylvZGCwpi6uPWGBC8oAx1qQNRRUCiNthjrWF61FJLd7rNbt3/4QxeCdVLKjypEkKSABlzvmmRqUXjG/wAxTU2oOzfivZUdNtzREtZT5b3v3Jy9qdlmWNlVCMny3xSrW1Eftvu59/hTNtZsDzyZUg5AqVLKsKczdKjCC48aYWtsOiTde05OHlXGPGoxCRA9wS753HXFNNel5AqKCD0zTtvbtC7MTnPlUzMJnWjGg59PBIuzbJcXMy8zghj4U4BvQ60YFFNbawUwEZIUEkgDzpAmj/lE+dFcD8RJ/RNVRoSrqzA4ABQe/KbJc5DTuQcgt1FINFR1hOdck9UMTdChQoUySFGgBO5xRUKQNikrLStPW6u4h3qKOYHHNknHgBTeq2S2l3IqyxuCxIAOSPjUS3laCdJF6qwIwcUJpGlmdyd2Yk0cZoTT5AztX3uri9nDy21ukUKHvoUEqUdCiIo6SSFCiox1pwknYIpJ5FiiRnkc4VR1J8q6RomljSrFIVyWPtyEjfmI3+XSsZwxCfwnbTEfxgC/trfPOIVd2IVVBZiegAr0j0RoGsjdVv3Og7h19/yXQ4LC0NdMd9lXajqsy3Bt7OESN4y9VXPl51FNlJdsGvJ+8ZRgFF5aOyjEcI5WJDe0Pr3qVkqMjriuojiMozym9+XIffxKuc50xzSHTpyTR4djEAnMc4iY4EhHsk+WelPRy6nYDFjMjRk8zIyjJPurpnZzZxcRcAzWF9+NQXUoVvFGwpDL5HesZqWh3uj6pLp80TPLHggxqSGB6EVRTzQyudGBlI6dERFA1obJH2SeY0KKy1qHVrV7DVUROcfRkIVGx5g9D5EVWcQ8AI1ot1osTsUT8bAWLySEt1QY8BjarTUeGNRbTheXWn3NvblhGs7xlRzHw3pXDWtyWF0tlfTcrKR3LgYCgdN/eaqraGKoYdj3hGuDKkCGr15B+l/A/wA1XJJ4WjZgwIIOCD4VHYV0HtB4Qk0iVtRiTktpnA5G5uYMQSTk9cnNYKVMGvNMSoTA8tK42spH00pifuEzQoyKKsUhCpUb923MBuKSTk5oUKfMbWSTsRAByQN6XzL+cKjijKkdRVzJSBaykHKSCD0INHUdGKnpUtYy/Qe+ioXF+ym3VBXwOu9OJKQetMOMMRRod6IbKQbKQNlZrbG80m7VAZJlaNkRTuRk8xx442phNEihEZurpSWUMY4RzFQfAnoD7qahneJsoxUkEZB8D1pXeEeFGEU8jmySC5At3b3V5cxwFxqPvdSLvRIJLQz2DuzRk95HKw5iNscoHXrVHJG8UjRyIyOpwVYYIq3ExGCNiN80jX7c+um6MhY3JL4IwV6daFxKkY9nHhFrWuOXimmYxzS9osRZVNCrey0qBXBvmkxggpGQCD8elQLy0NlcyQMwLIcZB2PjWbNQzRRiV4sDp/lDuic1uYqPQpfI2M8px54pJoQiyqRVuODOMNN0HTPVroT94ZS5KJzDBx7/AHVh6Oi6Cuko5eLFa9raoyhrpKOTixWvtqnrmZZbqaVBhXkZht4E1to+NNKg4TGlok5n9VMOAmBzEb75rB0M1OkxGWlL3R2u4WKlS4hLTF5jtdwsfehmioUBQCBQoUZFSbTTLq+DNbxFwmAxyBj51OOJ8jssYJPQaqTWlxs0XUWjpc9vLbStFKpV16ipekaaupSyq8xiSOMyHC5J3AwPnU4qeSSUQtHava2ykyJz3ZANUwljdSbrbTH/ACDRS2dxCoaWCRFPiy4FXs1yZSBuAqhQM9AKbDq0csUgcpKoU4OD1z+yt1+DwgENeSUQadmwKGixQW1il6qc1wZXj5mOyjlHQee53pckm+AdqAMENqtvAsgAkMh5yD1AGP1Uw/WtKIcGBsQtpvbrzV7nWaGjkllttjSCx8TRLknAFEDzEjGMbVW55KrunFJI6GlAHypQFLRM4OfGiI4yTZTAQiRsnY1Ps7eSeWOKGJ5JXICoiksx8gKZiU5O9bDs20u81Di3Tms4Wla1lW4kKnHIinc/rrXgjDGFx5IqGO7gFpuyzgKy1vU7yPiTT7yGCGFZYmctCpbnwVOR7QI8PdXQ9c1O2s2a3trq2jVG5MqwAUDoB5UvWdTEk4tlG6H2jnO/lXLdSv5JbiaJlACyt88mp0tM+rk4jzp0XU0lIIdb7qx4g1e4F+ncXQkAjGTkNvk+NZ+4mZ1ZvpMTmlO/PjYD4Va8J6JBrutx2Vy0ixGN3JQ4OwroQGU8WY7AIuR/ZKjaXol5qElvKthdS2zyKGdIyQRzAHcV2qx4R0TSbtrmxsVt5WUoSrtjHwJprRNLj0CwjsraSUxISQXbJOTk0niTiq24atoJ7q3uJRO5QCPAwQM75rlK6umrJBHDe2osDv4rOkc5xs1Sr/WdL02OeF9Rs4rmJGYRSSAMDy5GR8q4zrvGWq8SW8MF+8HJExcCKPlySMb01xZrEOu67dX8ETxxy8uFkxzDCgb4+FU4rdwzCmU7RI/VxsdeRtqrI2ZdSnlffGal2r9wyTRyd3INwwOCKgIfaFPgYrWe2+iMicun8EcetJcTw6/qqLCIwIXkUKObPmB5Vn+0y4tNQ4lE9ndRXERtoxzxNzDO+1ZMHNKA22rLiw2OKo9YZppa2lkhA0ScQaLRcDalHpGtGd5ooV7llLS9N8bV1TTeI9K1GRIo9StHmI+isgyfhXCseFPWc/ql5BcAHMMiyYBwTgg9apr8KZVEyE2Nk01MJNea9Ggg0tCOYDB6edUHCXFsHFkN1NFZyWvcOqkO4bPMCeoFXw5UJySD8K4WaF8TzG8WIWI9haS126cYHl8Mg+e9J38KQZ15sZ5j51n+JuKJNCu4YRbpIJI+fLPgjfFKGB8rsjRqlFE6R2Vu6v3HuNORAcm9UelX9zqWkwSzv7b5JKDA6nFW7zpZabLeTczJBG0jY6kAZpSxFhyne9kpGFvZ5pdze2emhZb28hto39lTK4UE+6qvXeI9Ek0HUlg1ewklNrIEVZgSTynAA8a552gcaadxPYWUNpDPG8MrOwlAxgrjbBrCSPzbbV0FD6P8RjZJSWuvt4FHQ4aXNDnkg9F0Tst4kjt7m+g1K/t7eFolaPvMIGfODv8ADwrpNnrOk30qw22p2c0r7LHHMGZvgK834PjuK0fAlwLTivSpVRXY3CpgnH0sr+2jcUwVkhfUNdY2205BX1dEH5pLrvE/MoCg7e6mbe+tZnCLcws2OgYE1JmjxnG9YDV47rhC1k1kJHcLG4UxglchjjrXKUsAn7F9eXeVlQRtkBBOvJby9srTU7C4sbpS9vcxtFIFbBKkYOCOleZONuCbzRtb1b1LR9QTSrWZhFM0bMojGMEvjce+vRPDerDUdOtbx4+6FzGJOQnPLn31L4k0UcS8P32kes+retxd33mCwTcHpnfpRFFWPoJi12xNj3a6lDSR5HWK8aXUXNzEKc461BkhLY5lNbTjfhwcKcR3ui+tLderFV70Jyc2VB6Z99Zq4QALv511c0LJmcVp0OvmhJY76qnljIV9jiorKfI1ZzghXOdqhMOYg5rm6qEB1ggZG6qEVOehpJGOtST1pqQZash8dhdDlqaIosU5y5oclVcMlRskYpyOCSYERxu5HXlGcUOU52rcaNpiWukKVlZjOqynIHskjw91aeF4WayUsvYDdF0lIahxA2Cxdul3aXcc8Vu7SQsHwUJAPvrdPHLd6VC8nLDI6qzBtgD5UlbZIllk/KO5I6kCm7u+M9sYuUAZG/jXXYfhYoWvBeTm5fzuWxSwCmY4ON7jZVnduXIAzv1puQcpK7Z91Prt41GKFTvVcgyhCOFk5GQMZFUnEFtGt1G8SnnmBLAb5I8hVwM0fKnMrlQXX6JxuKBrqZtXBwTpqNen8CqlYJGZSshLFJE3LIjI2M4YYOKmWmoItrJa3qvNCFZolH5MmMA58vdV7qcUU1pJ+IaSYrhSoywx7/KsvPBJBIY5UKuOqmuOrqN+HS3YbgjppryPLvWfIx0DrtNx/N1NX1A6ZAWubhrozYkiweRY/Me+pmqa6rr3ViSqEEM7LuRjGAKogCSAPHapq6TdevepuFWTl58k5GMdcioU1bVcIxU7bZsoJAN+dhflfVITPIswW2Gn85qDmn7eOcussMbsUOQQuRmpVppDTXEsUriPuSA2BnNX0MMdsvJCoRM5wKvw7BZZjnkOVo87g/QhKGmc7U6JnT5/W4hEY5RdxhnlUrgco8R9XWn1UE9N6djkaNmZcBmQxlsb8p6jNKSEkqfCuyhheGhrjmI5/fvWk1ugCOONu7ZuXCDYsdgM+Zqt14JNxBNcIVeIIgWVWyuQgBweh8asdZuBb8O3Fu0ZkN3MkakHHKRvn3+VUN25ttNjsnI71Gwyg55d81n4pMM3CcNGAOv/ALtbN8jdKscGRiMdxPjqAPLVVsjF3LN1NMk42pwtSHG9cPOb9pYauuHZ40tNQjeVEZxHyhmA5sNvipjRMuOZSPjWYVckDzNbW/gePlzsMkCuowF7p6d0ZGkdviSVqU7jJHa3s6eZJVVLHlGGDimeTAxg1NlGIzUZuh+FEzwgG6g9tioVLXpTdLU7VkNOqHCU0av9JQfjVZKAJHA8CcVaqc9aYvLZWVpuYghemOtUVtOZGZmjUJpG3Fwq/J86KhRgZNYep0Q6WuMD31f3JW3ee3gRI4S4PKq4Jx0yaq9GjR9WsklXnQzoCucZ3qzvyXvbhj1Mr/1jXQ4Yy0Ln94HwN/O6OgbaIu6m3w/dRqFHijxRgamsjXwpUu8MmRn2T9lBVpq+mNvB7IB58qc+G1WPcGROc7ZSOguVTeFPQM0bxujFXVgQR1BzTNLR+TDYzg5xXJxGxugWmxutfqKhLqVVUABj0FVkuoW0UjRu5DL19mo8/EUtxK8jQRgsSTgmqyeUzzPKQFLHOB4V0+I43Ge1Sm5vzB2+CPnqWlxMfVTb7Uu8KerPIoAPN4ZqL65c/wAvJ86Y8cUoqPOuelq5pnF5dr3FBukcTe6d9euf5eT500SzsWZiT5mi5G8sihnFUukedHklRLidygdqCnBBwDjz6VMtNPW5hEhkKnJGAKdk0kLGzJIzsBsuOtENw+oeziNbpvuFMROtcJY1+4ldO/5WUYBwNwPIU9+FrfwEn6NVbWdwoz3L/KmuRz+Q3yoluJ1kejtfEKfHl5qRcalPLJlXKKOgXbNNKJ7xxnnkI2yfAVZxaFFJDHIZnBdQxHKNqbmxo8hjQd8rgNlhjFO+iqP9WrJDD338NE7o37v2S4dOiiyXIkz5jpSzZ25/ik+VLimWePnXpnApY36Vssp4C0ZGi3mrgxttAoT6emfZA+sUduts2FAjLAb7VMwKrLi0Akcox69KEnhEFnxsBHNVvbl1AU9gPAikMitsyg/EZqLaq0PMDvzU4s7GQqVAHnTtqGvALha/JLMCNU53KDfkXPwpX1UanIoGrmgDZSsiO9MXrMkSlWIPN4Gn2yEYjwGarZrpp0ClQN87ULWzBjC07lQkcALJBmkYYLsR8aRQNCsAkncoW6FFR0KikhQovGnIkVyQxx8Kk1pcbBJJKlfpAjIyKKp+pLAqQ92G5uQDrtioFW1EPBkLL3UntymyFChQqhRQoYo6FSSQoUWaOkkgaIbUdCldJaDS5jbm2lVQSnK2DsDW5KhwV2KuMeY3rn9qwltI+QeHKR7xW14evIbqyWNie9gAUjzHga9ZwGobkEd9CBbyXR4NIMxiPPZQWlWzt3LczCL2ThdzjbpW84Si4D1WK1Ely73rovPBdScq8/iF2AO/vrJ6pb4YzpzuHJ5/JemPqqlfTo2fnjLRN/N6fKj6uOZwDY3aD3K55kp5MuUEd/0XpHTtNtNGs2g021htoyS/IgwCx8T8qsLKTDh5AAx2OOo+uuadjusPNpOoafLM8rWs/MvN+aw+8VYar2paTouunTZQ0gVfxk0ZBCPn6OPdXNvgcXFnNaDJ2GMPOgKd7T+LGubkaHaykwREG6ymMyA5ABPUD3eNcx1aPv7dnTKyxgsjDr8K6RxHBY8aaadU0u8t3ubWMuy8wBdBuQR1BHhXPMBlIc7EYrp8KYwU/DaLEb+PVTexuQsPNXC3LcSaJEL7M/NEUXvj3nIQCARnxHWuPXsD280kTqQyEqQfAius8Ot/a9kJAVS4QAdMHxrA8eRLFr8nKuOeNHb3kjrXO+klM3gCTobIPHIRJRxVB30B77j9visuwpFLekV5zJuuPQNDwoUKrSQpZbmHLSKPpU2uskiO1PrcnlCkEe8GmSc0Wadry09lOCRspCnnGd6PpTKSlVxgUtHL5yMUSyUGw5qQKcBPnSuY++kUM79auDrKV0+rbDOalR3LcwZ8SYBA5xzYyPDNQO8IxtTiuRRsNRl0Csa+2ymCWl95btdLdSW6yuqBeVvokgYDEeJqGsh91LV6JE4cLHXmrBIrFb6eVSrysykYKnoR8Kg6pp8ciPdwBIuRQXjAwPAZWlI+OlDU7pILMQoxeWdfxgK7KuQRg+J2q2rfHJTuM+thoe/lZWOdmYc+qpMYoVJvLC6seT1mIp3meXcHOOvT41GxXIvY5ji14sehQDmFps4WKFChvQqKihVlpuliVTcXOViXHKvi5+731XhSoDlCUJxnwPmM1ozJBc2wubQSrEGEZSQbocbAHxFauFU8UkhMvLUDr+wRVPGDdx5clGure2uuVUhitmBJ5lzg/Gn4JPU7eOCJ1yB7bJ0Y/tphzjc03znyrca5schkaACdNFdnsbjdSnkW5dfWVWUBSgLDJXIPT66RZ276ZA/46Iyy43jbJC43B+vFM95jwomkJ8Ki57C8Sn2hfXxSElteaUzeHSk8xHiaRzbZFEGqkyaqu6dDHzNHu3jSV3ANORhTnLYNWtuVIao0yGyKcXDNuNzQRBz4zTyxAeJouOMkKwNTk0SBx3f0fGnoo15AMCkBcnepEEWQDk9a14Y7vuAiWtuUuKPBJK4rvvDekQaPwlbXC2EdnqBswJZFTlkOd9z8qwvAnZ7YcV6VLe3GoXNs8c5i5I0VgQADnf410PiTXjbWN4kdurCNOXLMd8YFSqDne2GPkdVuYfAQcxCxvEmoyxKsEZYNIeYyBsEY8KzHNkksST4k+NOz3BdmdhuWJxnzqI0hHQYzXTU8PDbZa8soBSpHw2cnpXVOy+1jj02a4lt4xMzju5SMtyFdxmuSsSetdH7NOIZpbxdJeGERd0X7zJ5hyj5UHjMbnUpDff4IVzrgrpBxnJziuA6lqVzezyLLd3M8SysyCVy2N/f02rpfFXaBPw/q5sbeztrhFiVyzuwOT4bVyqQ87s2MZJOKCwGkfGDI8aG1vioxNI1TbMTRDpQYbmgBXRpzujUnNOBjtuaQBvSgN6iVYy4T4alrk752poe6noo2cgICzE4AA61WUW1KzUr8H3Ld0IYpJzInMBGpOPdUQ1ruHtVtYri1YNymPAKk7geNCVEjo25mi6sJ00Vt2WW19ZahfJdRXNvG8KsokUqrENjO/jiumPkINydtzWRvNM0zjpobWPVJ4WtQ0mYBnmBwN8/CtCkK6Jp1rbGWSZIVWLvHHtN7yK4nEZBPJxDo87ttt71jzkPdc79FIxsev1U3JBFPhpoY5CNgXUMR86cgcyruMeOKd5FwBms7MWlUXylMxRRwxrHEixovRVGAKfLpLA0EqrJG4KsjjKkeWKZbpkCkliDSIvqkRdYftK4ct00u0fSdJjWY3GHNtD7RXlPXHhmubT2N3YOourea3ZwSolQrzDptmvRMDZbPMRjw86zvHvC9prtk+oz3M8cthbuURACH3zvn9lb2GYwYi2CUaddSdUZT1eS0b1xZOUYzXbOzXRNJm4X0++fTrR7oPIRO0YL5DnBz5iuNXlsluV5XJBGd6seHOLNW4auIpbWdpYUz/Y0rkxHP80fOtrFKWSrgywusb38dDojauB80dmGxXov1fnx0A8a4h2r6hex8U3th6xOLRViIh5j3Z9kHOOnWuk9n3GN7xbY3lxd2sEBglWNe5J3yuTnNVvaXwrDqGk6jqpmm76OJXWNVBHs/rrlMKPqVdkqB3ddTZYtIeBUZJfD36Ln3BfGkejQXKajd3LqeRYYxl+UDOceArsGh6oNTtEfoRsfh4V51NoEwDncA7jFdH4O4wuZNTtrJLWBY5AeZwxJ9lfKtzGsNbI0yxDXc+5alXSB7S4DVXfatwxpk/CGs6nFpNtJqRVHa5WLMuAwyc/0f1V5wuVUY9kGvYN1bw69oVzaSM0KXcLwsyblQRgkZrz72p9n2n8DjTfUr25uvWxJzd8ijl5cdMfGhPR+sbY00h7ROnl+y5/Sxad1y2ZAVcFRiohjGdlFWc8PsPuaid2o8TR1VTdpBSM1VYVGTlcHNJMeRkAVKkjUk7nrRd2PDNYjoNbIMsUVYd/oij7s/m1ZWViLiTeWKGMEB5ZnComfMmoF7qtpBMyWtv33dkgSu+VYjx5QOnxqqdkVO0Olda/mnMQa3M82Cf02BH1C2WRFZDIoYEbEVtJh3cPdooCjAAHQCsBa8RMroZrS2JEisJEXkKgHfYbGugW00N7brcwSCSF88rYIzvXQejdXTTNeyI9rfobLZwgxua9rDr8VXzu2wyRkfOo5H11YXkYZl6jaohi36mt6WM3VszCHEKPy+6kGIYzgGousavHpfKihZZid0JxgedQ7XiiGTm9ZieM/kmP2gfjWFUYrRRS8CR9nDyHv2QD5omuyOOqsHT2jtSGXFSgYpVWaF+8jcAq+MA0iRA2KvdECMzSncxRuY1Va1a86G6HMWGFIGMAedXDRgKTmmmVXHK6hh5Gs2upuPEYX89vFDSx5m5Soen6RFAglnAeRlH4tl+ganOQXVtuYDAONwPLNJLUWcmnghigjEUQsPn3pNa1oytSs5GAqg5ySBufifGgo9qjA8dvqpUa5cUSGkqYF0pF32GatdDiWbU7aN0VlL4KkZBqEkYOR0qLquqPoeoQQRIHE0AbnYkFSwIOMeVXS1EdJHxZNr280XC5sLhK/2QRfzVa9zLPf3dvcyPNDC0jRqx9lWBwCPqqpvJO+uHdW5gehp+XmsoO6jYsC2fa99DRbWK91W0tZywjmlVGKnBwfKuIqHvky07vaJ1v1OwvzssN7nTPDBuSkXGmXVraW91NHyxXILRnIPMB9lQ3G2a6LruiWiaQ0E00ypp8Ler5IHOff5/VXO3OSR0xVWL4d6m4M6j48/irsSoTSSBp5gefP43t3JvcVodEup7qC6aeWSUoU5S7ZxnOfsrO71N03VG09JkESyLLyk5JGMZ++gsJrG09S1z3WbrfyNvihqaQMfdx0/ZaGcABsAVFddjt4GrKeBQiNk/jER+nTIBqJIgAbr0Nd3URX1WjLGQSCqbrQoYoHauSss5ORnNHcAtbSY/NNFFjely7wyD+aavteM36FWDZU1KXqKTijX6QrmG7hBqZp1xHa6jazSkhElR2IGcAHerW7VmuJZAPYd2ZT5gnI/VVDJvjHka1eoWc8EEbun4tguGBBB9mulwkGSOVp2bY+f+FoU4c6NwA0GqrApJpfJ7qNVwaWAa0Gxpw1JVDnJqBq0q+zCPpA8xqxkIRGYsAQD1PjVBNM9w/PIQWxjYYrPxaYRxcIbu+SqndlblTfjRk+FGqk0YTJrnA0kaINJxTixH8rYUoDH0VzgeG9XWm6RE0XrN53csckeUiDEMrZ8aPo6B078jNT8B4q+GB0hsFTJCZVPdFBghfaOMk9MVc6Xo15Zajb3F3HGYI3BcFg2fq8aiavHbWzQG3gEQySSCTkjGOtWVvqT38SmXHLkMF94rWoKOBk7o5j222Iseyeetx4ImBjGSWduLW6KRdabp11qVxKY3SFyGTkbHhvtjbeqOXh+771+7jHJk8pLjceFXquG3pfenGBituow2lqRd7bak3FhuipYY5DdwtqTp3qpSI2qCFkCMBkgHO9KzUm+s/WwSshjfbcUXqYRQOck+Zqr1aRrsrR2RsqeGQbDZNxjLCnywTdjiq9TI9yueYKGwMVYGHmO7E1OnfmByhKM3GiT6zGZBEGy2M4x4UJEEqMjbgjFMS2JkuUkVioUYIpySVIpBGzAORzYPlT53doTaC9h3qVyb5lXw2txaPguO538R1oJqMQZ1fbBwCB1perSq9rgEH2h0NU+K5ysqPVHiKDbfXXfkgpH8M5Wq5S5jlBKkkDbpRPhgcYzjaoVk3suCQAN96lFgQQSMdOtXxVBljBdzUmvLhcqvmmMgUE+0uc4ooJOSQFyeWl3UUcZUJ4jfemQM1hPL2S3O4QxuCpyXkIP0mxjyojepzj2jy+O1QSMUKuNdLspcUqRc3Rd/wAW7BMYx0qPQo8bULJI6Q5nKBJJuioYoUKgmQoUKFJJGAPGjPLScUKcOtySunnlDkcxzgYG3Smjgtt0oqFSfIXbpyboztQoqFQKZChR0KSSKhQoUkkKFChikkp+mTKheItgsRj3+dWVrez2NyskD8rH2cY6jxFZ9WKMGU4IORVxaSidBJkcw2I99dNg1aS0Q3sW7eCIheRaxsQuiWU0GpW5eNw0cgKkdCPMVEvdKe3YNCDLGdwAcsvxrH2V/Pp1x30LFT+UPBhnofdWr0jiS31J1hIaGYrkhvok+OD99d7R4tHMQyTR3zXU09bBWAMm7L/5/LIcPcQ6hwwuqixgDTXqqsc3OB3JBO+CNzvUO3t+ZGa4xLJISzlt8mtJJZW8qsJYVDMB7YGGGKivpKF/xUzBcdCMmjIKSOJ5fvdWOwqVtmg5gNveqyCAW0okgklhI8EYgEeVSO8aR0jQZdzyqD4mp8eiMrq0hdk645CM1NgjisyxjjEYb6Xv+dF52tBDBZFU+FS/1dkJVhaerxBFKpyqXbmbAz1O9c4401C31DWnlt2LIsaITy43Gc1s+JtfisLKSKN8TMeRiMEAY3x765dczGWVnO2TXG+lFU1sTYb6k3Wd6R1rAxtJFsN/oPumGO9FQJoq86edVyCFPWyxMzGRuXlUsBjPMfKmaAPvpmOym9lJpsbp6V4GRe6jZX8fKmaFCk95cblIm6FCh1oZpkyPxpSMFJzSaWgUbtvtU2b6JBK71ceNIDHmzREb0KmXk7pyU71FKU4BpCnIpWaIaeamEsOAfGld4tMigDirBKQnzKUr48alhrUGKcr303d8nK6+zGR0I8z9lV3OfdTiOcDejIpxsRdWsksp9zMbrTpbYlS3Orx8wG3Xm39+1Vx0a7Fo92RH3SDJPOM9cbD66fR9uop5J+Yd0+HjYFSpOBg9atmiiqnZ5Sc1rDx5X0KmS1/t9LKlzU+DSm54XmaPu3CuQH35T+2paaVbScQTWvK4tVaTGCegUkb0p5SxBwo2AwowAAMUHR4eAS+cXANveN/cmFPkBL+tvJHq0Er2diIIibWMspIXC82fE+eAKeeSztrEwWpf25e8YN+TgYABqI0rEcvMcdcZ2ptmyOtabpWtkdK0akAeFhbRWGWxJHMW/wAI2kB6mk8wHWkHrRZFCGQ81RdO8wouYUjNDNLiFK6MHf40rGaQDSs++k0pBOqMCnApYZFNqdhvT0JzkUZEATZWt10T0QORkDpTyqSNqRGnOcZxT6KEGBWtDHfTkiWhOqGLACrPSdPm1K6gsrdVaaeQRoCcAsTgb1BVAHFaLgpWHEukPysUF5Fvg4+kPGtqNpYCeaMhbqus8CcOahwpotxaanEkVxJcGRFVw45eUDOR7xVXxLrOnrHqFk0zes+0nLyHHNnzrZ6neLczeww9kY69a4/xNzfugv8AfOZiaowuIzyl8mh30XRxtMbAFAlYM+RUckk0p2IOPdQVc11QVbzmKCrmp+nX9xps/fWkxhk5SvMMdD1qIFwMCn/VJY4FnOyscAeNQflIyu5q1jCE/eSyag6TyzvNcPs/MPLpUaWIwj29m8qvNFtY5bUyOUVgW9pvdVPft3rCQ/SPXFUxP7RYNgiHABt1DJyaNRQx76UtEoQbo1GTgU4EI2pCgg5HhUq0nSCYPJBHOuD7D5x+qoOJA0RDGhTdL0O61ebuLQIZAnOeduUYrofB/BFpa2ZfWbOGa5708pDkgLgeR+NZbgeZY9VmZ2VAYSBk4HUVb6/xhf6PfLb2UkDRGMPugbck+NYFe6omkNPEbc+nxTzB5dkYqybs619p5O6tYu7525D3yjIzt+qlW3AHEVvPHKbeEBWGSJ16Z3rX8M8bQcQTvbC3aGSOMOSWBDHocCtTEiSuisSAxAJFZs+K1cJ4cjR/PeqX1MjNHBO6Xp1lYQl7O2jg73BbkGM0xr5la0buZBHLH+NDMuRt4VbxQrEgjQHlXYZqFqVtHcM8UrMqMvKeU42IrnGSXkzO1Wax95MxWP0Hj+C8KRXpEbyEANy4X35PhWwtry2vATBOkgHUqciuZ8VcN2WjW8IsJZJVZ+Uo+CRt1yKY4d4pl4djlt2tpZFkYFQo2Fbs2HRzs41N5LSkpmyNzxLp95PFb8pJwM4ON96QJlYZB61CaZLmNWZgQQGpyKVcKnNWTwrDvQojsFZWg5mbHlSdatXutGvbaEKZZoGReY4GSKahmEBJfIU7Z8KltPEyn8Yu48DVBu14cOSocCHXC5VD2f64WJZLIg7by5x+qqbUOFdWsbi47y3DRxMfxikcpHmBXZEkIbGRy4qHqenQ3cTYIRn+kT0xW7DjUwf2wLLSZXPzdrZcnsNb1bhOaJ4pJIYpjztEH9mUdNwPt611fh7tH0TiG2t7WWdYNRlBDW/IxGR5MdjtXKe1KP8ABOmm8i7qUWkTMBzgd57Q6fOuL6b2j65pGuJqlq8eI2LLbyAtGARjHn4+dUYxXUDmtMt+Idez9f5dZ+J1lKxzRJe56fX+XXp3ibhGz4h16Wa11OOGWZRmMxZUFV8x8K5fFqFxYTNJazPFIuQHQ4IpHBvazrWsyJdSx2EE6TiMFFO4OPBj762HGPDdq8ou9MEXd5KyLGQfa861MLqm5GNe67HDT3b3+C0qOpa9jcpu0jT3LfcDcUwanoFs87skxZkYEZ3Bxn66qO17gjW+MBpI0i3jn9X73vOaVUxzcuOp36VUcD3MUdotguY7hXaQAKcYyN811W0nEnIS2dxmsSsvRVfGhHM796zq6AMdmavIWoWcljcT2kygTQu0bjOcMDg71VTQnm2ArS8WwyrxHqzmKQJ67N7RUgfTPjVE+ze+uxkAlY1x7lmSC6qjG3MQQOtKRAXHMcDO58hTsq7FhnrTDDHXNYbmZCgiLFFqVvPqc2Hlt7W0Q4iVcnnxsGYD8rHiaptS0uSwWKQyxyxylgrKT1GM5B6dRVyBvU22jmkZLSWKQQzMI3zH0ViM4JG3xrOnwuOqDjrnPPf9rfJRfCJib7nn/OSxnStDwvxMdOcWd07G1Y+yTv3R9w8jVfc6LcnV5tPtYpJSsrRoSMAgHqT06UzqWi3mk9360iqJMhSrBulc5TmsopPWImkZDYm2ngULB6xTOMzAeybHp4K903UtUe9v4Iu6vO6ZnBlmwqgE9D4j3UencRw22kNLdTd/dGRiIiTzEbYHuHWspnFAAtsASfIURFj9TFYscSRm3NwLn6d91JuIPbbLvrvrv9u+6eurqS9maaZy7t1JpuKJ5pVjiUs7kKoHiasbrh2+tLQXL92y7ZVCSwz7q0OgaNb21rFNNApuWAclgcrvkYB6Gno8Dq6yp4cwLT7RJ6X+ZUYqOWWTK/TmbqDpLa1pTIk9v3tqvWORgwRc5JXfY9auruAwsu6kOA64Pgdx+qn5o1YnIGKjSpsqrgAD5V3lNhwoozGHFzeV+XhoNFtNi4bMl7jldR5ThDt4VG99KZjzsM5FJxvkUHK4OOiEeblCnYozIyqoBLHAz50hVyam2MP9kwnDEc4PTrjerIYyXKcbMzgFAsWEnrQV1cpMc4/JzsPsNTY4yMYFVvD80UrakVjKc8iuAWyQMttVupwdhSwl/GpmyO53+ZClAAWBw/mpTkkttYwxy3kqwpIxUMQTnHXp8azd0brULuWe9JbuVKW4wAMZ2/V51J4jj1PUZYIIIWkgiXK8qjZj1yfqFUcl7fJnnyuDjdPGsXGMTbxzFMxwY06WGjtNSb6Gx1CHrajXh2OUfH/B2SrmK43lkjKqTgb5xTNvJLbzJOhKSIwZWB3BHQ0ma6uZFCu+R9LGBSOeT3VzEs8bpMzc3vtv7lklwvdqsr7W9R1CIR3N1LKoOQHOd/OqzlYncUos/mPlSSz+dVzzGQ5nkkp5JXSHM83PekspU7jrRUbMTjJzRZoF1r6KtX+jamssL29zPI8zMiQh8kAb7Z8PCp0qkBs+RrKwSPDKksQPMjBhtncVo7Cee7s3mnAB5mUYGPDy+uuwwTEeNH6tJq4Xse7vK0YJi9uV24VTI/dsgxnmOKNlJGM4NNS2cgTPOWxvg0m3l/F4bbl86xuK4PLJBa+yFvrYp8ZHvp+A7ikxKGzkU/HGoIYCtCnjJIIVrG81UTRyRIFkQqSxOT40zVnq6gJF8T+yqyufroRDMYxyt8kNI3K6ycQ4Tar3hgBtP1TPXEQH6Rqhi3zVnw7dJDevHNIVhlQ8y+DEfR+RozC5AypiJ7x5i31RNE8NlBPMEeYI+qtO4Ocg0m4uIrMK0zFQxwNs1Gu9atljljhaTvMEK3L41TXF7PdBRNIXC9M+Fbldi0EALYdXeY356q6WdrNG6lJu5VlupZE3VmJG1NZo6UqgDJrjzme8uPNZxNzdLUAAcx6U9axd7cxxbATMq5xnlztmml5VUuRnPT3VoNO7iDSbe4NtA9y8jsJHGWCggDG+Oua1aGmE7w24Fhf3XA+N0TTw8Q6mwGvxCiWtkdNuWkjmYnDJ0xt0p8yZbJJJ99GzFt6bfJO/hXQtjZC3JELDoiNhYbKHqweVY+RSwGc4pOmTs0scHJjYjOakSBxjlGahsVzyw4WXOxHWsmdpjqOODrpcdbW0CHdcPzK4eYwyxR8hbvDjI/Jp0So4PKwODjY+NUctzc20KAzyd4TnOfCrDTraS3VmZwQ+GAHvrTpq50svDDTbntpoiGSlzrAKWxcoeQ8reBqPZxXLzsWIIPXPU1awRxtGGZck+NOpEiklVAra9RMha/NsixBmIN1HW2IOSRSJUKtkHY05fymG0ndG5XVCVPkazsOu3SKRLyzb5BbYj5UPX10FI4RPB11uoTSsjIaVcyyCGF5CCQgzjzrP6jdi9mVwhTC8uCc0JtSuZucGVgj5yo6AeVRQM1yuI4n6yOHH7Pes6efPo3ZEBSsUaYBGRkeVPOIzjlUHb5VlMiuCbqgC6YCljgClBQds4IpPTpReNMLBRRuhXqaSrFTkdaMknrvRVBx1uEkZYnrRUKFRJJ3SQoHehQFJJChQoUySFChR0rJIqFDFHT2SReFChihilZMhQoYoU1k6OioUKeySOhQoU1kkVAUKFJMhSopWhcOhwRSaFO1xaQRunVml6twoU4Vz1FTLZBGM+JqhqQl9cR/xhYdMNvW/SYtlIMwueoVzJdbuV8mpXUMyd1cSYQ55SSVJ94qyg4n1CIEgwnO26n76zMepRqPahbmPUhupp/8ADNuAMWznAx9IV0FPjLACeLbz+yNhrXx6skI81qhxbqsoEavHk7D2c/tp65u5ZEMl1OAijLE7KPfWNOvPGea3hWNx0ZjzfqqDc39zdsTPPI/uJ2+VEH0phgacoL3eQ+/w96vdizyLPcXe/RStc1L167blOY4yVTHTHnVaTmhmk1wtXVPqJXSvOpWO95e4udzQPWhQoYoJQQoUeKLFPZOhQ99HigRTJIqFChSSQzR4oY91CnskhjpSsUmhmpAgJJzotGELUhQWYZrsvo69muh9oXFF7Z6/DNNaw2TTIsUxjPOHUZyPcTRcTcwLjsFJouuQGBgucGmxGc4r152s+jbw3o3A15fcI6PqE+rRPGwX1l5SI+b2zynr7Oa8rJZO0/KBn3DrREdO2cZottlLJfUKEIG8qIgocEV7N4B9Gngm94P0e61/R7r8KzWqSXIN46+2RnoDgbY2rzh21cL6Zwn2j65o+kW5t7G0kjWKMuXKgxIx3O53Jpm5HucyPcJ7dFgkYCnEcZxUbODRq5znJqLJrJg5WPrlwV5TNIVxjHMcYplnqOJDnqaMsTvmiHVJduVYZCU4xzSDRBiaBOR1qouvqo3R0eKIHakO7H6J391NewumvZLOFBPlTbPnopp6C3eT2myQa2f70nGAxnhu9OQCMKDsRkeNEspXyi+yk2N7/ZCwfMQelK7wn8n9db4dj/GLDI4av/0B99NP2R8YR9eHL4fFB99I4bINnBP6tLyB8li4pOY8uMbVIhOAfjS9Y0i+0S+msry3e1uoiA8bYyuRnw91V6yyxuAzkAnJqrOad1ni/eoglhs7dW0DDmGM53qZHnHvzVXbMxcMG28KsYCSu58a36GTOEZE5W0MfMw3runZLEn7hp8ncXEx/wBkVw2IYbavS+maNZaLo7W1hbLbRNGZGRSTlioyd62sSc1sLWcyfkt2jb2rrDa7qZ0mOJhH3vOSuM4xgViNRuvWruaYpy96xbGc4roOu6G+rLaokqRqjEvzDJ3A6VgNcgjtNWureEHu4pCq5OTgVr4a6Miw9rn5rcqHaKDjOw604oOw3oInj50ZJXoa1AhWi2pXQOAdLsrmxN3NEGmgnYRsfeo6+dX/ABPp1rqGiyrPI0McJ74uigkBQfD665XZ6xfWURS2u5YlJzyqcDPnW80TiuPXpG0+S2dfxWWZmBDgYByPfXPV1JM2X1gG4Bv4K0m+oXP5JUSd1iZniDeyzDBI8KdkQSDGcYNTOK7VbTXLhI4lijJDIqjAxj781E0+K4v5xbQo0sz/AEVHU1tseHRiTla6tjeDoVGdeU48qJB1qZe2U1jdSW1zF3c0Zwyk5xt7qY5QMHGKsDwRcKXD1uEkKTsKPuXB6inIkLN9HONzUhIHmz3agkeGQKg59lYGDmhC3eEqVxgZo3A3ro9vwvowRXFjCWKgEiQkZxv41Y2HBujXETsdLib2sBiT99YkmMQs1IPw+6Y1LGi5VVw3wuNUltCkkcJt1jnJ5N2xjauki15MPnHjjFRdPsLLTUb1a3jhYRhRy53x4ULq/aOKQ98AVU+PSuTq6l9TJpssmaV0ztNlT8XT6wJtPi05U7piwnc9UG2CN6VcagwthasC5CgFydzUI64l0oZ5+8UbZNQ9VvmNm7wyESEDlZeuKLjgNmsc3ZExxGwaQpsGkDWCyLMsbIOYcwzmnrHhC9FzKXeJEiwVYgkSbb48se+qHhbUb4z3RNy59lcE+G9b3RZrtreRrku2d1LeIxUax80BLQRZKodJFexVEqgADHQUfNg1NkSLvTiMAeQqlv5nS6dUYqoOwFRj7ZUmHOVeajcBLSK2UnmU8xPhVT3pIyfnQWSaeTmncvtinUhBB5kyMU7GBgsma0MFlK0qVfxuT5VU9p+vWGicBaxPeuVWa2e1jCjJMkilVHz8fConGt/c6BwVreo6fci1vILUyQy+ySrBl6A7E4zXmXiLtA4r40hjsNX1eW7gDhljZEReYeJ5VGfGsfEKkNfw2C7jt8ljYnWNjJjA1IWbLtJyqzMcbDmJOKnWujtcymMzqhwSDyk9KbFvHZjNzylmOVxk0LDUHhu0d5G5RnOB7qCpaeGKRrKsXJI0vt47LmmBocM6jXMEtncPBIBzod8b1rezzjyLg24vTdWs9zDdIo5YmA5WU5zg9diRUJoLPUCblo+8ZtixyDtVBd2stowEgADZwQc1ZV0EtA8VMJu25sd7DlfzVwL6d4liPgvXPZ7xBFxNwvbanbW7W0UkkihJCC3stjJIrcabuH3HhXi3Re0vi3h7TotN0vXLq1tIixSJAuFycnqPOul9lPbrJY3OoJxrq+oXUcojFqwiDiM5PNnGMbEfKpNxUSgNeNTuVssxZkoDH78zyuutdtPN+4ZhnY3kP/FXnmVDz16d46tLTVOBNSllRLiNbY3MD5yAwHsuCPia81TgK2K7r0ds+nc3ofsry24VYV3PxqPNG0kwRcZI2qVJG4JAQ7nb31faLo8awmS8s8TByAZBvijHwGQ5FCno3VL+G3RVHC0XPqEyuityxbZGcHmFapkPKxJOwzTMlrZ2B723gSKR/ZZk6kdah3F5JhvbOMdPOj6OnMUeUrep2iii4T9T3KQLxUGChP11XapBHqNnLbMREsuAX5QxUZ3x76jvcSMc8xX3CiWTnIDEk1ZK6ORpjcNDoVnS1PFGV2oWGvdGura/FoInZpCe66e2M7GtNougx6asVxIWN1g532XI6e/HnVo8EEk0c7xq0sWeRj1XNKkbCk1z2H+jVNRzOnPa17N+W3xv8Flw0LInF+/RFnNE2Sckk/GmQ7A9ahteTEkmUgfZW1LUtaNUQ6UN3TtxIxlYE7DpUbvBJ0qxtooZLNrmZuYnIznAGKqUb2jms2oJFnE+0qJQRYnmnGQlGIxmkKpPXrT6pkjalrEM7rVQhzFV5LpKIdt6c1q+Fpw2sImeOea45o+TIJC4B38OtWGnW9v6zAZ1UxA5k5unL1Oayuq3Prl7dc8sb2UVw/qxT6ABPh9QFU4rKaaHhN9p+ncBzJ93xV0pMEJcDq67flc+XxVVaajc6dcu9q4Rn9k5UEEfXW/aMncnJwM1zqcBZmZfo52IrTcN66080sV/dtI78qwq46nx6fVWJ6NYi2nndSzk2cdNdBv89Nt0Bhs4DjE477dOfzV+sYG9V3F8bS6UgDYCTj9amrlVUkjlzUDi+MJpEXKoHNMuffsa7LGIw6ikb1C2qiH/AJeQ9AsDICHAz4VL03TZdRn5I+UcuGYt5ZpqVQHHwrQ8O8Q/gy0ETaO96oZiHXPjj3GvOaCngNRapNmjxPu0XM0sUb5csrrD3n5KDDoyXcmpRRqFaGQchPgN9qqJoWgcxyKVdeoPhWj0vXja3+pzposlx6y/MIxn8T12O3v/AFVU6xcet3Qk9Ta0PJgo2cn39KJrRSPpw+EWeCeR1GY25W2VtRDEIg9jrm50septysqth7RpOKUfpEe+jOBXLubckoBaXRIf7VxPnxbb66mSAcrb+BpvRFH4EhIG5Lf1jTsiEI+2Nj9lenUceWjit+kfILcY20bfBUQbfeoM4DXuCcbjfy2p+a4U25eI+IXp0p2CzYRKz8pc75zmuOlaahwjZqBrdZhGbQJ23CyZIb5VIC4pqGJoyc438qfAIBY9AM1rwN7Oo1RDBoqvWHPeRx42A5s/H/7VX5qTqNxHczK8ZJAXG4xUWuSr3h9Q5wNwgpTdxSgcDNJo87UVCuN7BQQowCegoDrsM0s82OgFSawbpIuUDq1P29rJcKWVT3S/SbypCoqqXbBpYnmjXEYYRPnbGzedFRtY0gybdBv/AIU2gX7SkQQW13OsEbyBCmeYqM8w/ZVpyiG3hgUbRKV5vFsnO9N20cOmWgeQEk4yQN8nw+FRbqf1icPE8irgDHSulhhbTR53gcQ8hyHT4IsERt7ylm65c5UYHvoC451yFGPjUNoZG2JYiiEEg2BNC+szX2VPFcpyuW8KgQANfucYALGlCCT+d86AtmAOx3qqTiSFpLdjdMXE20RXcwFymUV1TB5T0Pxqf+E1x/BqPrqB6m5PnShZv5U8UlSxznNG6TXvaSQriw1cOxieMKiD6SnJNP3WrrAqmNOfJwc7YrNywPEM/ZSmlVrVIgW5g2TmjGY1UMYY3bjn9Fe2skDSDurC81ozRSRG3Qc6lc8x2zVOMAfRBp9Yiw3yTShAT4VmVT56pwe83Q0j3yG7lFx7qPFSvVz5UXqzeVD+qP6KGUqNiiyRUk258qaePGRVb4HN1TEEJqhilBSakx25ZQQKhHC6Q2CYC6ictFip3qj+AFNSW7IOlTfRvaLkJy0hRcUMUsqaUqZ8KGERJsopvlocpqUtuxGcUr1RyNhRIo3nYKWUqHijC5p9oSp3FOx2rN4UmUj3GwCYNJUXuzQ7s1bLpkmB7IpY0uQ/kitFuDTH+lWiB3RU3dmh3Zq7/BUn5oofgmbwSpfgs3RP6u7oqXkpJFWs2mSRqWZdhVfLEVoGpoXxe0FW5hbumcUMUtUzsKeW3Y+FDMgc7ZRAuo/LQ5Kli1Y+FL9Tf82iW0DzyUgwqDy0RWrEWMh6Cm5LN0ByKT8PkAvZIxlQSKFOOhBxSMb0A+Mg2VaLFGFzTiR83hUqKzZ+gomCjdJsFINJULko+WrP8Gvj6NMy2bxjJGKLdh0jBchSMZCgnIpOaedMGkBcnYUC6Mg2UCEkAmj5TUmK3aToDTwsH/NPyollG94uApCMlV5FEBUqW2aPqDRRQl+gqo0rs2WybKdkwFNAqanCzbypuS3KdRVrqF7RchPkKihKPkqRFFznAqStixGcr86eKhc8aBOGEqt5SKLFTprUp5H4VFZMVRNSuYdQoltk2Fo+SnEQmptvp8swPdxu+OvKpOPlU4aUv2TtbdQOSiKVfRcOahMfxdjdOcZ9mBz+yhccM6nDA0smnXiRoMs7QOFA8ySMCijhrwFLhlZ8rihy0/LCUJBG9IRCzYArPdTkOsq7I4kywrr/AGFce/uA1m61AWS3hltmhMZl5MZIOc4PlVP2X9i+v9qFtez6PPp8KWToknrUjKSWBIxgHPSum6b6KHGdjzP+F9CjZgBzc8jYGf6NbmHPpYXZKojKRYj/AAiqcsY78zbmvSPCPGuk8a6YL3S5+85Aqzpyn8U5GSuSN/qqZacLaBaXPrdtommwXG572K0jV/mFzXkfXdT1/sq1u84WHEdxbS2/JJILKZkidnQNkA79MD6qqJu1DiJjk8Vaof8AO2++i3eijZCZKacBh1F97d+yMdQsd2o3ixXrXtG7SNO4A0xnmZZNRljZ7S1YMO+IIBywGwGa8RdqXEz8ZcZarrctvHbyXboWijYsqlUVdif6NdU7OOGtW7aZLxrziO55NNVQj3IabPOTkLvsNq08nofwXdxJNNxa4MjFiEsh+1qrkpqDDouCZLync62sdRbS3RVzRwxRhoN3c/BeTJIyvhTQJzjFdg7cex237LL/AEu1tdUl1AXsMkrNJEI+QqwGBgnPWuRSryN8KxqiIBokYdCgHNtqEWaVzbY2pvmB2o+YUMHprpYbAow22KRnyoE4GakHp7pZfAqVZW5cgAFiegx191RYITM3hvXV+EOyDiC80bT+KoJ9PWzLrKA0xEihXxuuOuR51q4dCHvD37clZDE6V1gFpeGvRzvdRs4LubWLaFpI1kaB4HDRkjPK3vFdd1aV+EYbSK5ka+kkj3dAEA5QB0pux7Q9C03U3t7uS5Qsw5iIuYb7+BrI8U6rzXLSz3MrQ879wH3IUnOMVuQU88soZK2zdxpvfvXX0dAWyZMtm/P3qwm7VLaBnjOm3JKnH8KtRG7T7e5coumTjY9ZV+6ueXkwknlkUnlZicnyqIZwfzq6NmC01r5dfErYNFTsOytOKOzObjHUbniCHUoIFvMSCFo2Zk2AxkbHpXKOKeGLnh/VJLKckhd0l5OUSL5geVdZ4f1eLTr+CeRpjHH9NF/K2qNx7wzfcc3sOsaWbeO1SIQETycrBwSTsAfOsjEsKABBFx/NFg4rhUZYXwtu4nl3rjNrKUkEZxgZGanR3GNgB186hapYvaXU0LleeJyjcpyCQcHFHaurIBk5XGa5eCSSGQwn3Ll2Oc05StVZzM0gHKPCvVdxJiwfbH4n/hrydZsI5RkgeVd04r7ZuGdBuLzQby31M3cCLG7xxKUJKKdjzZxvXRYnM0MiLzzP0XQUk7GC8hspd1ctCFIUNmuba86y61dMF5C0hJGc71Mue2ThqVUCwantnOYV/wCasfqfHek3WpXFzHHdhJHLAFBnHzo/DsUo43EvkA0WtNiNKQLSBXh2A6Uw7HJqJpWs2+tJI9sJAIyFYOMHen2kUHzrpIaiOaMSROuDzUjK17Q5huCnFOKt9A1UaNfesmLvQUKEZxgHG/6qpkcEZFOI+TirHsbI3K7YqUbgt7xpY2c2kNqQjxcDuwr43Kk9D9VYnTr6XT7tLiIKXXIHMPPat3oER1jhrubzNxzMwHeHy+jv7qyGo6DdaLLEt0EJf2l5GyDg71m0L2tzU7zcgqxw1uFb22hHVohfS3RWSYkkcuah6tow0yONzP3hdiMcuMDFajRIzLawXCRrHAwJCZ3FPaxoF7riQpZJC3dMWfncL1G1DisLJcrjZvu0ReYDdYENgnPiMUGAYdRt7633DfAGoQa3bS6jbWctqvNzoXD59k42+NbJuGdIzj8E2Xw7oVCoxqGJ+Vva8FU+pY023WY7OuHr6GO9W9t7i2jfu2jZk2fY9PqNb2ztVsojErs+WLZO1KgkEcfd9ANgB0Aqp1bi/S9GvBa3bzCXkD+xHzDB9/1Vy9RNNWSkhu/IIB7nzOsArK+vYbGISTMiBm5VLsFBPlWY17VGtLG5vI0WQqOYDwJJ91YXjnXoNa1yWa1kla25UCq+Rggb7eFZ5Znb8WsrgE9OY4rcocFsxsjzqdSLfBExU+UAlbjhrU217UU00xJDzc0hdckjA8q3y8PWYgSKVe8IXBbpmq7gW40y7tVt7aN/WraFe+d4wC3gcEdd61PL0OMCsjEak8YtYC0D+XVNROc9hooNhw3plurmOIxk7ZVqt1RUiWNSeVV5R8qRGp6L8acKPjwrIkkc83cboF8jnHtFVTQeRIPniqHU7Yi8wvNuASa1zRNjpVXe2xe48gcZzRVPNlddFQTWcoltaBnKliQB1ApjiTWdM4U0W4vr6+tIZRFI1tFcycguJFUsEB8zt86sNW1bTuGNIudV1O5SC3gjLHccz43woJ9pvcK8+dt3ajw1x1oOn2WjNePcW12ZWM9v3YCFCDg5Pjig62uyA2OqCra4RtOU6rIdpHajcdoSWCTaZbWIs+cjuZXbn5gOufLFZuytEVI7jnYsBzcuPGk2Wku6RXUpXunyVAOSSD4jyoXdykZkiBZSMrsNqopIHRD1urG+1/MH7LmnOcTxJdyo17e+t8nsBeXPQ5qKpKnIosUqsWWd8r+K86oYkk3KtdOvmjhEfdA+11LedaW74aa4tXjjl5mI29msVDOIxvnOc7VsOHOIZpLww3cjSGUYjwo2P/2rtcArKeZnq1TrcADu3+ei06B0Tjwpueg7ll77TxYySRsWDpjKkY61ED8oO3XbNdOu9Gs9ZjlLwxCZlwJOjg+G9YbWeHL7SY4muRFiRiF5H5qAxvAJaZxfCLs7r6a80q3C5YBxALs69PFdRk9I+RuDE4Zi4Yto40sVshKLts7KBzY5fdnHvrDafxHLqtwYzbxxqq8xPOSayJQgkY6UqOZoTzRsVbGMigMNxmpongFxyXuRpr8FQyulboTour2+lRzRxSGWQZCtsB8atZ7nBO2c+dcn/dVqvKq+s5VQFC8oxtV3ovGcENoyah3pl5yQYoxjlwPfXcUvpTQzPyuu3vO3zXU0eN0wORgy3G5WuuJA6gFQMGoE+DzkKDt1qPp/ENlrUzQWqTF0QuedcDHT9tTSobIrpaephqI88Dg4dQr5JBOMzTcKsPXalIm/MTiphtT4BajyRkZzvv0FVmEjUoF0RbuktNynAwfOiMvOMECm5UbPsYpmcFYzk7+40PJK5t1U55F0k3fUcoqFKQMA/lHApwg5qLc3IgIWUEE7jG9Y1VN2bvKBkebXKe7xu7EXMeQHIXOwNKgjErEFsYFNQHv41kUYU+fWpkQC9KaBmchx2TMGbVPIm4GakpEDvnJ8qREvMygdSasbSxllLBApYDO5rdgiFrnZaMELnmzRcqBxFeDQrS1ZEEz3aOpDNjk2xkY+NZDUYlso1tEcuF9ssRj6qvOJr6HUNTgEaN/a1Gjn5wACc4GPOsxPOZ3MjuXPTJrhsareJI83uL2b3AWzeNz5WWbi8zTK5jNhYDyF/j8k1LugPlQsrprK7iuFUMYmDAHocUorkEDxpkrynBrlJs7XiQbj5rHaS03C3/DWvLrEkkckcUMi4KqGJLjx6+VS+NmVdIgA/lh9hrnVvdS2kqzQSNHKv0WU4IrS67xJa6vpltBEZTOhVpOZMDPLg4+uuzo/SET0L4ah35g2Onav9Qt+LEhJSPjkPat5qgmYFh8K33Z7HJNpypHAZiZG9nfbcb1z1jlq3fB/H1pwxo0doLYyT947OxTIIJyPGgcFqmx1bpHkDQ7+IVOBzRRVWeZ2UAH6Kz4Ngmn1viJUjcBbj2uX8ndvurKcfMG13CsWAhTc/XVrwzx9FoOp6xeG3ZxqEwlA/NGWOP11R8Y65ba/q/r1tEYlaJVZSMYYZousrGuoeFmF8x567lE1dVC/DhG1wz5ibd1ys7yfjR7zS515XA6Unm5ZAfI0JX7xgR5Vx78ga4De65wbLaaEijQrds+B2/yjTer3Xqlm0qqGOQuCfPxpWk+xoVuT0CFv1ms/rmoW1+0LQd57IIbmGK9Aq64UuGMsQHFjbeQFx4brbnlEcDetgqvmYry52Jzir+H2o1wQcADY5rPg4qfpd2LeQxlC3esoBB6VxeFVDIpLP2d8OiyoHgOsVbKvXINIu5RDbSlXUOE2BO9SQhzjes/qrrJeuU/JAU5HiK6DEp/VYcwGp0+G/uRcruG1Qz12oUKFcOs1CjUc1FTsUZO5G1TjYXGycaoKnu+ulFARguKchQzXEdqh5TIwQMfDJqWuirbwGa6kfPetEqRjry9Tk+FaEdM94IjbcDc7AK5kLnC4GiVp0StdC3vLJDEinIZShyBtv161YTTRqscdtEIIkBAQHm3JyTk1GuL9ri4kmYAFzkjNRpbzlcALnI8634ZIqdhAN++2tunWyK4rWNLWnTrbW3zTtyzXMsUQBJByRjzrtPZb6Odx2gcMx64daisA80kSwtbNIfZxvnI864zp0neXysRivc/o+RCPss0dhn8Y87n65D91VVc7oqc1LD2i4DblY9fBVn2DJzuuZH0PpOn7q4R/mJ/5qA9DyQ//ALsi/wBRP/NXpY9aFYn41V/qHkPsqeM9ebF9Dz87ixfqsf8A/KnU9D22/L4rk/ybIf8ANXo0tvSgaY4zV/q+A+ybjPXneP0PdMGO84quz/RtEH2mpC+iFoKjD8S6oT5iCMV6AosjxqBxerP9fwH2S4z+q8a9tvYVpfZxpWn3en6rd3st1K6NHcRooCquSQV8elcQitg7nlGfGvXHpXOBY6Cvji6b9SCvKWmIzTcq7lsKPia6KmhE0EUj9XOvf3GyIdGCxjuZv8126w9Efi65toZjquhIJEV8GSQkZGfzasE9D/iY/T1vRV+AlP7K9UWKGK2hQ/kxIvyUVKBFYH4tO02bbyQ/FI5LytF6HmsEfjeJdNT+jbyN91SU9Di6I9ri22H9GxY/8VeoCtIORTHGKk8x5BLiuXmM+hu4xzcXpufCwP8Az15u1nShYX91arJ3ogmeIPjHNysRnHhnFfS0N51y7th7F4e0fTtPtNLl0/SJbe4aWSX1UEyBlxj2ceO+9XU2I8R2SpOh59PIJg6+jl4ThtzJIF867/2QejradoPCKa5da5PZM9xJCsUduHACYGck+Oa45qGjy6Nr9zYSMJDbzSQ86jAblYjI+VezvRqgaLsq08kY7y4uHHvHPj9laNUx1HT8WPR17fy6syljb81jx6H2k4//AFRen/NU++uL9tvZTb9musw2NnqEt9HJbLOzyxhGUliMDHhtXu3oa8h+ldcyHjsxknkGnwYHxZjVOF1c1VI6OZ1wGk7D7KUTs5Id0K87Mm+KtuH9HXVdWsbFmZBc3EcJYDJXmYDP66ixRd7MPLNesewrsU4V1ng/ReJdT0+9/CfftKHM7oh5JDyEL0IwB8asMccDTNJteyqa0DU7JUXoe8PqeVuJtUIBwR6vGM1Lj9EHhVCC2uaywJx0iH7K70UAJI8aLfIHvrFGJ1I2d8B9lESOXzk4m4dXR9dvrGIs8UF1JEjN1Kq5Az79q6/2Cdiei9oGlapqGuSXcfq1wkEIt5Av5PM3MCD5jH11zLiq7M/El+znBN7N/vGr016K6/8A4Q1h/A6kB8olrrsRYKelfNCbO0RkjWtaXMU+P0XuCRjNxrB/89R/w1JT0ZuBEBDDVH95ugPsWur5HhR9a5b8arv7pVHrMvVcsT0beAUO9tqDfG7P7BUmP0duz1Rg6XcPn867euk7+R+VHgkdD8qicXrj/wBV3mU3rMv6iuNcd9gnAGmcF67f22jyx3NrYzTRSesuSrqhKnc46gV4rvoijEV9D+1Nnj7OOJyAf+7Ljw/mGvnrfPzORWpRyPqKdzpnEm/PXkkXFzLuOqiW0BkcDFelOAfRcsOJ+FNI1244iuoG1C2S4aFLVTyE+AJO9Zv0XuGdF4i4xvbTWtMtNQh/Bskix3CcwVg6bgeeCa9kWOn2mmWMFlZW8VtbW6COKKNcKijoAPKh6qc0lmR6O35bKN8trLhEPoh8Pfl8Rao3whjH31Li9EnhNAe81jV3+Hdr+yu4khfGi5gfGhPxar/X8B9kuK/quPQeitwIg/GTaxKfM3Kr9i1Ucb+jfwJofCGtanbRambi0spZoy93kBlXIyOXfeu9LWY7UTjs74k/+Wz/ANWnixGpkka1zzYkJcVxNiV87b63ETjHlUNUJPSrTVR+NG35IqHAuZB8a1KqnaJy0Jnt7VlacOaSNR1WwtpI3aKe5jifl64ZwDv8DXtOH0ZOzWNj/am7PhveyVh/Rc4O4d1rhS71HUdIsry8tdT/ABU0qczR4RWGPr3r0ezqDtQmIVRhcIoCQRv37J3HLo1c2j9HTs0jAB4fLY/OupT/AMVcg9I/st4T4K4c0q70DSRZTT3jRSMJnfmXuyQPaJ8RXqXnz0qu1rhrSOJYEttZ0y01CBH51juYg4VumRnocULT4jK2QOlcSOl0g831K+alwnLIw8M03GmTWi4ts4rfX9VihREjjvJkVU6KBIwAHuxVHGOU1ry01pLnZM5uq7l6NHZvoHGusaqvEenrfQQWiPFGZGUKxfGfZI8K9Cj0e+zNEyOF7Yn3zSn/AI65P6IJzqet+6yh/rmvTjD2aDxiR8FTw4nECw59wU5uy6wXhz0h+D9I4S49m0/Q7COysVtYXESMSOYg5O5J3qR6M/COkcVcezWet6Xb6jZpYSyd1OuVDhkAOPPc/Op/pQyE9p2oKeiWlsB/o8/tqy9EZs8fXA89Om/rx1qyRj1MSX7WS/vU8oIv3L0LH2MdnkY9ng7Rfrgz+2vPvpTcHaDw3faDFoekWOmrLbTNKttEEDkOuCfPG9euSBivL3pfEDVuHyT/AHnP/XFY+DvdLVNa9xIsdz3FVxau1XI+xTTra87SeHkuYYp4TfRq0cqhlcHOxB617qh4d0m3Ux2+lWESZ+ilsgH2V4g7CsN2j8NjP/tBP217yQ4zV2ODIYsvNv1TygANsuFelbpNpb9mts9vZ28b/hOHLRxKpxyP4gV42uFAY7V7a9Ksn97SH3ajD/VevFFyMyv8anSMzUYJ6lRt2AUdlF3r8qqWJIAAG5r1z6KXDGpaLp3EA1fRrqyM0sBiN1AULgK2ccwryzw1btJfxcqlj3seyjP5Qr6QwHlRc5zyjr8KjXPMNM1lvbv8CEtmpaxBPogL8Bisj2u2txfdmvEttbQ3FzNLYOiRQqWZycbADrV/f8RaTpl1Fa32p2VrcTY7uKaZUd8nAwCcnfap6uCcEGsRhdG5shHf42ULEalfNfiHR7/R7w2+o2F1ZTFQ4juIjGxU9Dg+FVdsAZlBFegPSt0y7m7Q3uktbhoF06EGYRsUG7flYxXAoRyzjbBrpQ3OY5etlNw1BXrH0PGA0viRQBkTW5x/ktXoh8sK84eh2/4jiYH/AOGP9evRzNscVk4w21W63d8glJ7S8Yek7bf/AJk6w7AH+5iD/wCStcl0TRptXv0tre2muGJGUhQs2M77AV1n0m5mftN1iLx/sbb3dytb/wBFTgFLOyn4yuHhdp+e1tgvNzxEEc5J6bjA8a6KaSOKnilcL2Y33m2yIka05T0AXY+z3sv4f7NormPRTect0qB/WJQ+OXOMYA8zWtI8jTHP76BkxvXISyPldnkNyh3XcbleafTBRvwvw2x/8LOP9ta8v3XVvjXqD0vpC1/w0fO3uB/tpXl+66t8a3H6UMfgfmU8nshRPfSs/Ok0dYQdqqEYOKUT7NN0Y61IP5JKbYfSHxr01wRevF2X6NC0ojgZXLEnAJ5zXmWxO4+Neh9DDHsl0Fh0LMP9tq6/CGhzWNPUfVb2ANDqgNKotXuO81a4ljkDLz+yw91LvtWm1ER97jMYIyPGqi8lC3UqkbhjSWlO1eitgFm9y7QTtBt0UiWT2TiouaIuTTbNV7W2Q80+Y3Su+ZGJBrScPXY9WmUygZdSEJ8fOsmz71Z6MzA5Az7Q38qrqYg6PVRopc0uQ7LmfEefwpf5/l5P6xqstWwWyQOlWnEhJ1S+/wAe/wDWqlRSzYryXEjlqrt7157UG0zrdSp15qEly0fq7MnJnfoQfjWi07hXijjr1nV4ra41STvAk07yrzFuUYByRnYCmuAdCtNc4nstOvVkNvMW5uQ8pOFJ6/EV3+y0XTOzfR5Y9OSSQXMwkCTyZJOMHBx0xR9NRvmfnebudyWhQ0L6t13HQn4rhzdkvFqgs2iSKB1zIn31GuOzLiWCGSWTSmVEUsx7xdgBknrXXNW421NSeSG2SNjgLyknHxqAvFM93DJb3KRd3KpjbkGDgjG3zrdHo/mZctC2j6PQNOV5N/d9lxCG5utPQrbSFQ5BYdc+VaXS9fimjiiuJ+e4chRhehPgancbcI6boFjbTWT3DvJIUbvGyMBc+ArBOWguFlHVWBrGdUz4S8ZTdvTkB3LCc6egl4Tzty5Lpkb4UjIFPREcw3BrNaBqEl/bSySuzFXwObHlVhJcNAOaMHPmPCu6pMQZNA2Zvslb0NW0tD7aLbaRxVe6VAlrF3Pchs+0mSMnfetzHfadcXAleeyZ12j5nUkZ8q5BaXDTWsbOvtkbk+NS4XEMqScoPKwbHng5pT0LJ+23Qn6rTZMCAQuxhMtkxggfk9K0WhWFvJaF+4lSXYOd9/Ks3omqfheKzvFhMXfsG5M5x7WOv1V0qxDMW5s+FcXiEjoxkOhVVVMWtUBY44oQCoGOrHrUdyhxgj34NHqF7gSx92oy2M599VnrB3x5eFBxxki5VDGE6lC41WxgjLNf2qgDJJlG1co4416wvteSWDU7OZDAi80cykZBO3WuccW8bNPLLZabJ+JYMskuMFsk5ABH66xOysCANjmtFtXHQy3h7fXl/lBT4wymltCM1tzy9y7pb6JPdxt/Y07sSGBVDnpRfucvY3yunXbEHb2Gqd2Ncfy8SzSaXqcsKXsaAxNz4a533AXzAFB+18cJ69rmkcRJd3JtrthavCAWKE5CnJAAAxjzrU/HHalo0WocXhyNkGx6/VbHs1sb61vL5p7aeBXhUAyIRk81WPaRx1BwFoDXMgMl3cBobdEkUOjFTiQg9VDYzist2cdoz8U8ba9dNdtFoVlYqYxIxVEAcZlYH6JIzmuPdrXGzcb8V3FwpjNna5trUx9GjDH2v8rOflWBWSmeUyELIrsRDmmRu50Hu0Vxo3b9xtp92ss+ox6hGFKmG4iXkJPj7IB2+Ndo4R7cuHOK797N0k0p0h73vb2WNY2xgFQc9d9vhXk22UZIrsXZz2E3PGOlHVNUvZdLtpQrWpEKyesIc5bdtgCMVVkjMeaTRZdNNKdjdekI3jkQOrBlccyspyCD0IPiKbmhDE9Ao3yfCsTwvo3EHAesWOgtcz67ot4/Kt7MD3lkVQ4TAJCpsMZra613tvpd/P3bFI7WZzgeAjY/soSQcPUG4Oq1w+wuuG+krqemahwlpCWOqWF466iSyW9wkhUd0dyAdhXneG1knkAVHILAEhdgM00xOF6D4ACtPpMzLoB3OyyDHzoDDqZuIVBEhsAL+Vlg39ZlLnaaKPqRWys7eC3n9lGI+kMkVQ3Dl5nbOcnrT11cd8iDlAx5VGp8XrRNJlj0aLWHgLIeWTOUVChQrHVSOrKznaG4SSJwHQ5B61WUpHMbhlOCKKpKkwPzDu+Ckx2U3XRtJ1ON7OOa5uYUkyc5YL47bVavb6TriAXjxTrH7ShZcYJ+Fc4tnM1urPgk58KstKv304y93Gr95jqcYxXpNLjTZmMjmbdpAudz7wujpcVsBHK0FvO+t/Fay54Q0aS3Z7OyQTYyjd4SD86o7vs+nuZAyz29uAMEYJz8qnaHxJeXOoQWBjgCuSoPKcrsT571OveIby1kMU1jErDoQxwfeKIfRYfVxEZOzfkLa+5abm4ZUMEzmWG2gsL78vFZcdnOpf8AirT5t91VWp8Lanp1z3At5Lj2QeeGNiu/hnFdDTXTsfVx+lVpYTNeQ94EK+0RgGgZ/RCjey0ZLT1vdMMFw+o7EDiD7/quOm01HS2WZ4bm1JOFdlK5PWp9pxJqEd3DJc3czxK4MijHtLncV0vX+H4NdghiuZp41icuO7xuSMeNZnUeA7CCyuZYJb2SZI2ZFJHtMBsMYrHkwCvo3E0bzkGu9r+4IGowKrp3k05u0a72+CbbjjTM/wADd/or99TE1KzuYVmWeNRIOYBnAI+IzXPbmwu7RVa5tpoQxwDIhXPwzTIGfKqo/TGujcROwHu2WecWnBtKPoukBo5t42V16ZU5H6qantsqTynOayuka/LpFs0CQRSAuXyxIPTHh8Ku9G4mm1TUFtWt4kDKxypOdhmukovSGiqwyOQ2e7S1jue9FRVcM1mk6nkpQthjdT8qgXtk1zHyBHHK2c4rRd27HoaeaM9DW1LhrJWlh2RJos4ss3bWpigQFWUAYxUuGHJPKCfhVnPb95GV5tyQfhRW9n3PMQ5bPupRUAjIaNkzaQggDZM29sQyOVYHNDXbqWw0m7NvOYbrCcoRsPjm3269KmtzRwyzcpIijaT48ozj9VYjUNYTV9WfV3iNuCqx8nPze0Fx1xQGM1rKeMUrdC++u1hzdfuVlVM2ki0PaOg8LEE37tFHvLjksyxbvJ7jDSszZbPXJqJaWU08byLFI0cY5pGVSQg8CfKmJ3aaVnPVjmrbSNZudOsr+1iSNo72IRSc+cgA5yPfXnzZGVFR2vZAsP53nfxXMNc2R/5hsP208yrDjPQLXQ7i0a1LhLqLve7PROmwNZiYYIIHXrV5xFxBca61q1xHEht4+6Xuwdx781RkkqVqOKGN0ruGNDt5K7EXwvqHOpxZp28h9U3TkAwxJ8qbjUscU8FYDGxFZdOwk50Eks2D8aNW8s0RVgdhRDmJ6YFX3IKZL5j50RYedDBpLKSehNO97raJJL9aKj5W8qDLy0I8Em9klLj1i+igECXLLEF5Qu2wqHRZoVKSeSQAPcTba5vbwUnPc62Y3shSkYg7ZBHQ0TKVYqQRg9KA61W0lrrKKnW2pvDaXMTNI0knKEYt9HzNQWYsSWJJO5J8aBOaKrZZ3yBrHG4bt53+qk55cADyQoHahRgbZPSqQLqKNBmQYqQyvIxSPwBJ+FIjUCBpAPaHSrXhq3tp3vLq7R5Ft4g3dq3Lz5YLufrrSpIOI9sN9Xa+5EQRGR4YDa6Vw2FW21Scxq0sVuDGzDJQk4yPI1XyXMsn05HcDpzMTVobhbe2nhtoIoRKvK5XJLDy3NU5U5rQmY6CGOEHa97ban7WV07rMYwcr380l5Gz9I0nnwRknNJYe0aLqRmshz3XQV1b6WS0ysa989iUHq/ZXw2pBBa17zf+czGvAulfSHwNfQjs3tzY9n3DluTummweHmgP7a06916Fg6u+n7og6xgLWcwrnfa12p3HZwul+radb3jXve572Rl5QnL0wP51bvvD515x9LfVJbOfh8QlOdIJnwwyMM6j9lBYPDC+qaKgXYASR4AlSp2sz3kFwpMnpQ62x9jQNNA98shpselDxAD/ANxaXj+nJ99eaTxdqAO3q4/8v/rS4eKb+Z+U9wPeI67Jv4ATlEXz+6P4tDyaf5719BuAeJbnizhHTdavLeG3mvEMhjiJKqOYgYzv0FaDY1i+yKOSLsy4Z7w+22nxOdsbtk/trXFj51wFW1nHeIxZtzbwvosqQDMcuy83+ltcFL/RYsnC2Vw/XzYD9led+Drf1zXdLtycCa7hjz8ZFH7a7x6W9yBxBp8ZP0dLY48syt91cW7LYPWuOOHIOUtz6nbjA8fxgP7K62ldkpoe5pPxKJcbNYO4/NfQ7ABPu2oucKRvQc4J38ai3shjtppM45I2b5KTXFgXQgC8wXfpB8dreTxx39oEWRlX+w0OwJxUjR+33ji61iwtpb2zkSa4jjZPVUHMpYAjI38a82arcXSXk7rPN7UjHHMfEmr7sztn1Pjjh9ZpZAW1K2UEsc47xa791bh+YwClbfXXTz2Wl61D7IiF/wCdy+ipGGOD40ROCCfOjfAz55NMXEnLDI2foox+QNcABdZtl4B4snEvEN1IMZku5m+bsa9gej2pTsm0DP5SzN85WrxTqEwk1QPnd3dv1mvbnYgDB2UcNKds2pb5uxrs/SJ2anFv1AfAouodmXQic14y9KORp+0XVE5jmKK2jG/T2Af217Ca4K5Jrx3206Ve8Z9umoaBpQRru8uIYE7xsKGES9T4CsfAy1kkhftkP0VUJy3vzCh9hHZPqvFevaXrVzo8N5w5BdtHePO68jYU5XkJy25XoK9pWkUNlaw2ttCkMEKCOOOMYVFAwAB4ACsp2YcDRdnvBtlokb80wHfXLc/OpmYDn5TgezkbbVqiSPGg6+p477D2Rt91Am6f5/fRlwoLeQJqMXPnTU90UikOeiMf1GgcijlXzv1+573XLhxn2rl2+bmvWPoqA/uD1Jz0bVGx9UaV5B1OUSaoX/Okz82Nex/Rdg7vszklHWTUZic+4IK7DFpL0sg/3BXOOjguxiuFekTxFrOia3pEWmapfWSPauzrBMUDHnxk4ruHfcleXPS6vZP3V6IscsiD8HM3ssR/Gt91ZXo6WsrmukbcWOnuUqR4jkDnC4WPftA4pfKniHVj/nb/AH1Hbi7iJzltc1Y587uT765PLqFyCf7Jm/TNN/hO4/l5v0zXYn0ko2G3BA8vstH8VjH9C6PrnFurz6TdxTapqMiOnKyvcyEMCRkEE1zyaTvHqNJeSuCDI5B82JoQNWLX4xHVyARtyhZ1TVcdwNrL0j6JkYXjHUZSg9jS2GcdMyJXq1Zw3jXnD0T9Otxp+uatyH1jmitFYnYJjnIx55xXf+djtmsbGQHVJA5AD4KMouVx30i+Oda4V1LRItL1250xbiCZ3WKQIHIcAE/Cub8D9p3FurcYaJZycWalcwzX8EckfrPMGUuMgjyIqZ6Xdx3vEuhW5dS0Onu5XxHNIevyrmnYjj98Xh/PQ6lAP9qugw2WNtI2J8TSS12tteZV8M4AEZaPFe+yxyfjWV7U5u77OOJWO+NOm/q1o5JySdx1rJ9qkwbs24mBI/7tm+wVx1K385niPmhGt1XgXVTmUfCi0eMSXXtAEBScEUnUmzLt5V0j0deENJ4y47On63atc2osppeRZGT2hjByu/jXVSSNbUmRw0bqfcp3/MuV607JNMs9J7PdBitoIYu9s45pTGgXvHYZLHHU+GT5VpteuDaaBqVzE7JJDaTSKy9VIQkEe+o+n2cGk2VvYWcaxW1tGsUUY35VAwBmsD6QPGN/wn2cz3GnzJFLdTC0dmQNmNkbmG9c5l9Yqrjmb69N/klbM+6rPR5401niyfWfwpq91qCwQwd2J2zyElskbeNdnablFeJfR+461nQdeNpaTxiPULq2t5ueIMSmTsPLqa9mNKSTvgZNHY5CHTCojADHjQDTbQ6eKuqRnIlAsHfTReSfSrtLW24/tktLW3tw+nRyOIY1TmYu+WOBuTjrXDCpDV7N9JPh3Srzs7v9bn0+3k1O2aCKG7K/jI1MoBAPlgnY+deNZxyykDyoymcJIGnpp/PNUP2BXpj0QP8AvTXP/wCFD/XNenXb2cV5g9EUldR15s4xZwD/AGzXpXvvM1n4629WfBvyCeYXddeNPSllP76GqgHGLe1H/pCrf0QH5uPLr3adP/XjrPelBNz9qutD/B2v+6Wrz0Pz/wDjq8/+Wzf1460DJeny/wD+f2Sadbdy9gl/fXlz0xWxq/D+/wDeU/8AvBXpoygHrXl30xZQda4dH/wU3+8FZGF9ioB7j8lAaG65v2DH/wDMzhvf/wBoR/Ya958+POvA3YVLydpfDhxnGox/tr3Ybk5O460VjAz8I/7fqVIi7QuT+lXKR2awf/Mof6r14vmzJMwHia9lelHIJezFckZGowfY9eN4xz3hA33NE0AJpms/3H6JjsAvQ/oes8et6/HyqyG0jY5AO4fb7TXqhJ/zq496OPC+i6DwDY8QWlu6alq0JF1K8pYMFkYABeg6V1C7vAltNKHUGON3HuIUn9lZuI2knIaNBp5aKRF7BecvSE4titO17TFKqZNOgt2QMNmJYyfLwr01bXfrVrDPgAyxrIQDsMgH9tfPTjfjHVeLuL/wzqUy3F1yxpziMKCEGBsNsYr2J2F8Z3/F/B095qd0txNBdm3QhFQLGI15VwK08Rgc6jaNPyTl0vre31B6clY45mZf0qz7a2duynikBj/cLeP85a8FSKFuj8TX0a1u1s9T0a+tL+CO4tJreRZom6OvKSQflXzlv/xdycDAJJA91UYY4NiNxsR8f8Kg6BenvREcLBxKf5tr/wAdeh/WCuc5rzd6JM5Wx4hc4yfVR/vK9Ceshs5YYqvFm56lzuob/wDiFa4XN15p7TeEL3tB9IDVNH0+W1hk9WhnZ7kkIFWFM9Ad969M28FvZwR29rHHFGgACRIFXp5Cs8vCmgwcUXPFUdp/bm5iEElwZGOUwBgLnlGyjwq3WdxsMHy2qFTOZmRxjZot7+fyT2JACmFm8CaSZipOSa4/wF2p6jxh2p6vbJdXUeiRWjtBZTKo5HUopY43znmPXxrpst67MQHBHnioz0L4XBj97A+fJSdEW6FcH9LmVXn4YOP4u5HN/lJtXmK5O5+NelfSrJeLhp2Ocesgf+nXmi4PtH40TNpSsb4/MqibQAKPQoUKwiqEKA60OtDOKQNjdJS7U4XPvrqvDHGUcXDVhphgdzGCMl9iSx6D665Nbk77/VVrpl9LbTRAzOI1OQAAcHrXZ+j9XHG9okGh+fVaOHVrqSXiN8F03VbdjJ3qb5JyAN6rckEgk7UvT+IYrq0FxOx7xmbIVPI7Ud2iqQYwfa3NelQPDmgLsZnsmHHiO+pCbL++m2f30WHPhQ7s+NE2QL3OclRLzuF65qe2qRaDG3eRd4uBIWDYPwqPJPZ6db99JIe9VQxXI8fd1rH8Q65JfSJ3h5UA2UDHicZ86ysSr44IyXJpqoUMeYH8zlz81VaxeLeXd1cKpVZZGcKeoyc4qHaHJNNSMGYnzPjTtoRvXlnGM1TmK45zy95cea7vw4xh4AtHQBXWzOGAAI3PjVU+qTzR8stxNIV+jzsTj4ZqDpWs3KcLWtovJ3fq4XpvjNRVmfm3Neo4bTARAn+aLvop+FHGOrR8lNkkMn0iT8TUZ5NyBRGVsYyB8KZaQ5IzWqGKmaoHJT9NkYzEMxIA2BOcb1zLXyDqV4T/ACz/ANauk6ZIonPOQBiuca+oOoXZHTvm+2uL9MWWgFv5osvGX5qWPxKsOFQTZzYP8b+ytFCmWHjtWS0S8ltbaRYyoBfO658K2NqQyox6lQan6OPY6kjZzA+pVeHOa9gaOSkIcY8Kkg00sYYA06sbM2ACT7hXWN03W3G0hWuk6jPBPApvJooUO+HIAH1VetxXdxyRvbaldsEYOy96wDAHODWY5e7TlPskeB6ikrIyggY360LJTRyHMQtAAAAFdx1Sc3GhPdoWQyxpIu+682D+2uX8WJxDrsqaHolxe29wALmS7MrRxcmCOTnG/MTjatLwzxLca5o93YXclss0SxpAPos48seOAOoq0SN4kCFhknw6VybqcxB0L+vw0Qroc8ZjJtf5Lz/b9mGtXly8Mc9k0g5iSZG3I9+Kx00MkUpjZSrKSrA9QRXoHRJTBrbBgOV3ZSfLc1F7QuArfip01CxZYL0FI3LHEbRjOTgD6W/Wqq/CmNIbFosavwJrW3pr3HxXELK9udPnW4tZ5beZPoyRsVZfgRUubiC+nvZb2aZp7mYASSTe2z4x1J+ArtWl9jHCd97LDUSyjf8AsjGff0rlXF3Z/qfCt/exNHJdWlpIkbXkcTCPmYZC5PjWWY5odAVlT0dTSi5+CpZNYv55LpluHhF2As6QnkWQDoGA2I91P6Tw7q2tSpHp+n3V0ZHEamONmHMT4nGBTei6Pda3qtrplmqtc3UgijDNyjJ8z4CvV3ZKmpW/BttYanp/4Nmsne17tV5BKq9JMeOcnfxxULaZ3aqumpzO4l5K4X2Z9nDcQcbyaRqbraHTczXEbJz95yOAY+uMHPXyr1baW1tZwR21tDHBBEvLHHEuFQDoAPAVWx6FpUWsNrKWkY1B4fV2nGxZM5wfA9OvWrWOMEnfwqmoka+2XQfVascDYm2CMbbjrXkXtw40vrntJ1uHTtXv0tE5LZoo55ETmVArry5AxnI6b1u+2ft21jh/iB+H+GWuNMutNmeO7nlSKRZzgcvKpBwB1+uuC3d/ecS65Nf3ztc3l7MZZnChS7MfaOAMCsaeTjERM3us2sqA/wDLZ1UrhZRJczF41ZRHjLAHBzTuvampD2UCGPkbDsMDPuAFStWjh0CFG03EbyPh+Y8+QBt1rNXNzJcTPLIQXc5YgYrerZXYZSDDye3rmI2seQO/TkqpnmFnB5800TvQoqMVyRQCFChQpJIUMUKFJJSbSVlZAWPLnGKsXJwMbVTK5XGPCrCyuJJgwcg8uMHFbeH1QtwTuVdE7knO8kR+dXZXHQqcEfXVnpeuSLJGt9I8ka53JLEVXsgPxpoqRuK1oZ5aeQPYfsfFERyvjdmaVaXXfGWSa1lmaBjzL7RBA+FR11S8VcJdzgeQkIpmPULmFAiMoA81pue4kuH53IzjwGKunrw4ZmEg8xy92qd0v9TSQVYWmtXUUrNJd3JBXH0yam22vMbuEvcXHIJF5sk9M1nS7UaTOhBB3G/Smp8Xljs0nT+d6kyslZaziumrqOk38iRSckzZ9kSRZwfdkU7qGjW95pdzFa2lt3skTCMiNRuRtvjaubLqt0pBEgH+SKvbLjjU7a2SItbycowC6b48tq3W4vSVALHtsTzsP3XQQ45DKHCpbvzA/dRl7OdakGXNpF7mlz9gqj1fSLnh3UDazyoZgivzQscYPv2roWj8WfhCKVr2S3jZWAXlIXIx7zSNR07QdfvRNMUluGUL7M/UAeQrIqfRuCWFrqJ3avzP7Kl+EUs0DX0j+0T/AFH9t1zVr+58Lif/AEjffWr0TjC3aNbe+5YBHGAsuWbnI2399XR4J0YIzCzY4U4/Gt1x8a5/d6PfafDHNdWssCSEhC4xk1nOixPA3iYEOB33I066C2/VAyU9XhpDybg+J8/NdItLqG+gS4t254nzhsYzj41Mt4yzbYrneh8TXOmyW8EsrNZRk80aoCcH3/GtvpPEFrq/e+qJKvdYzzgDOa7XCPSCCuYGF1pOnfa5tvputihr4qgAXs7p7uXcsvq/F017qxJLWtvGDC8MTkiQDIyR5moB1PTAvL6scZzjkGM1t73R7O/gkgaBIxLjmeNQrHBz1xWP4r4etNHitmt+9zKzA87Z6Ae6uexOlxGiY+bM17dySNdTaw+CzMQpapmad7g4b696rp72zKsYYF5tsZSmDdJ4Lj3AU2YOXA86UsDEHlUnAycDoPOubMtQ92th4BYLnlxRNOp8CfqohLGT9E/GlGFgqsUIVuhI2NIaMBSfGqX8Uan5KOqQ2Ax5cgeFESfM0WaXyDAoJt3E2UUA7nril58hQVcirfRtFN+ks8uRAgIyGwSwGflWrRUctS8Rs1JVkUbpHZW7qnDE+FJbnI8Ku9O0RLzSZblS3fKzBRzYXbFVTJy7HalPQSxxsfJs4XCd8TmAE80weY7Zon6DzpTLvSGGDisuQWBVSTR9BmhQocJKZqdoYH74sCJGOB4ioVXGubQw/wBI/ZVPWhisLYqpzWePmLq2Zoa6wQoUKFZyqRgZ2pap3rhAcUlQfAZp20QtLnyq+FmZwb1Umi5slSssKGADqOtXVpYPpmmtMZVcX0SgBQQU9oHf5VUtZ3F1MzW8EsyrgEopbHyrXeq82i6Yk6srrGQyHYjB8a6PB6MzTSEj2R2Ty3t79CtKjhLs7rbDTzt9VnWBII86THbAH2yGq5lsYFjZlTcAnrVeIZTsI2+VHzUBjcM+qg+EtOqrbi2wzsCOXJOKiNgkGp8quJZFceOMVDnjVHAXAGK5qsiAJLR/LoJ7eastIPtAeYNfRfQuWz0PTbYA4itIYxn3RqK+dvD0JmvLeIZzI6qPrYCvoiGWJRHzD2AF6+Qx+yrqrWnjae/5BWtF2hTTOD4GvLXpb3Zl4j02HfEenofraRz+yvTAuE686/OvJ3pVXgk47MYYEJZWy9emxP7aWGNyyPJ/S75WVjdAfArhEn0ql6fjLH3VCY5arDSIzNMsY6u6qPrOKjTPvMEGz2l9FeEeSy4T0S2RCqxafbqBnp+LWrUXQJAwar7dktrWCDnXMUaRnHuUD9lH3y9Qw+dAFmYkojKvMPpZXYl4yCDP4rTIV+bsf21z7sJhE3ajwspBIF+r7fzQT+ytL6UN0ZOP9QQEnktrVP8AYB/bVH6OUZn7VdBbOBC00pz/ADYmroJXhsUbP/8AP7p5Dq0dy9xi+5vA0UsqTxPDIpKSKUYA42IwftqFHMhA9tfnTgbm2Bya54xgJFoXPD6OHZfIS0mi3bsfFr6U/tq54S7JOBuBNRk1HQtF7u6dQgknlaYoM5yvPnlPvG9aiTnTOQ3ypprpEOJJFT+kwFWl0jrkuJv3pgzW6sVugTvmqfibizSdA02dtU1C1se9hlERnkC87BDsPM9KgcV8VWPC2gXWsTt30dsFJjhkTnbLAbZPvryX2l9pOp8Z3RkvrgC0gMptYGVVKqfPHVsYBPurRw7BzU3lebMbufsrmQAjM7Zc/aYzX0JB6Ka929leLfs24Yi3ONOiPzyf214GsJC8/MeoWvfPAuIOCuH4uYDl023H/pir8QfxYAerr/BVN7QutU0gIO1c70Xsqgte1bWeO9RlguTPIJNPjQuHtm5QrM3gTy7ePWtq1yAMc4+dNetjmz3gx5ZrKjzsBDeYsnyK2N2AMBTWL427UIeDte0PSH06W6fWJBGkiyhREecLuCDn6Watb7iLTNHiSfU9QtrOFnCCSeQIpbyyfGvLmlcT3/GHatoEN/q02orBqSCEyvzCNDLnA9xwK0MMw1spdJKOy0HrrodvBWRxA6levWusFl3JBIpi9uo0sbqRgcJBIxx7kNR5JFBYhx1J61W8QXBj0LU3Vx7NnOev+Das1sVyFDKvAl0c3iYPiDXtD0bHMHZTbbfwl7cNn6wP2V4qdibuP6q9oej/ADrD2UaShIBaW4bfx/GH7q26+z4XgfqHyKgztXXVDcDqT1ryt6WdwJeNdKQZ9jSx+uRzXpZ5g2faHzryx6UcpPHtovNkLpcWPrd6DwyMNmv3FSLbC64PMTmm6cl6mmqzJycyEKGal2Sd7IibAswAz7zUZVzVroOn3F/qdpaWkEk9xNMkccSDLOxOwA86IoWEyC+ydguV7D9Hbh+XQuAGuZJY5PwjdNOnICOVVATBz71JrqImZTzb7VjOze1vdB4F0jTdRtZLW6hjcSRSDDKS7Hf6iK0frhAxkHO1E1rc9Q9zdRc28OSPkb2jZeV/Se1lL7tKuIVUg2VnBbsSc8zcpbP+1isx2J+12h8NjH/tGI/I5qu7WtUGr8ea9erKsqS383K6nIZQ2Bg+WBVr2HJntD4b3/v1T+omtWFxB4Y/pYfkqWn8yy9wetA74NZftRk5uzjibIP/AHbN9gq4NyoH01+dZftRvkHZrxMOcEnTpR1+FYUMdpGkdR81LLZeIL4Zl+qu8ejLwvrmh8anU9S0bUbSzfTZe7uJoGRGLcuACeuR0rgc5aSdsAkY8K94cPXMrcP6WXcn+woOp/wa1rVMhs7KNHXCixuYkrUm8VjkA4rhnpHcDcZcd3+lRcN6bdX9lHbnvkjlVUWXnOCQzDfHjXWjKT0f5GjF06fln66zYmujOZm+3mrCzTReVeFOw7tP0XVreeTh24t4RPG7uLqIFcMN9m8BmvYjXsXMcbjPUVTG7L/SfNKMoA+mPnUpXSSsayTZt7e9IMIaGk7Kp7VtAvOOOBb7QdNa3S6uJIWQ3D8iYVwxycHwFeIOJ9JuOHuIL7Sbpo2ns5nt3MbcyllODg+Ve8JJ8EDnHXzrxL2s2V7a8dazJd2txALi9nliMsZUSIXOGUnqPeKJpnOjiLeV7quVtmrtHon3HdXXEJ/+Gtx/tmvRYuuY46V5v9FhkRtfbI3gth/tNXfe9DHAYbe+lirM1QSeg+QVpbdeR/SZcN2raz/i7b/crV56Jcxh40vWAznTpv68dUPpF2Vzc9qWrGKGaUGO2IKIzZ/FL5Cr/wBFuyu7Li68lurS4hT1CUBpImUHLx+JFPHsWn9H2VEftkL1UL3J3B+deafS8lEus8PMPCxm/wB4K9BSTKd1YfOvP/pK8P6xxBq+iNp2m318iWkqu1tA0gU95nBwNjQ9NGGvzePyU5G9lc17CyP3x+HWIP8A3hHt9Rr2690ASAD1rx72N8C8T6bx1od1d8P6tbwRXyO8stq6qi+ZJGwr1lcuEZgHGx86uqwHtj62+qdg7IXNfSbmLdmewP8A3hb+PuevMPZ9wxLxjxbZaJHdx2jXjlBNIhYJsTnA3PSvR3pHzs3ZpIM5Avrc/wBeuKej/bXFz2k6XNDBJIlvIZZnVciNOVhlj4DJAqdN2MoB2zH4KBH5gavV/AWjtwfwfpnD8t0t41kjKZkQorZctsDuOtXUzR3MMkMikxyo0bDOMqQQf1GoJcKNnX51HudTtrPl9Zu7eDm3XvZVTPwyaGMZe4u5lXho5LOw9hXZdEA78Nc7r+fdyt/xVp+HdF0LhCzksdA00WVtJJ3zork8z4AzuT4CqyfjHh6Bylxr2lxuADhrpPvorTi7h+/uEtbLXNOubiTIWKKcMzY32FWGnlsb3sfGyQiA5LVyamhhZJIudJFKspPUEYI/XXkP0iNA0Dh/iuG20HS4NOgNnC7RxEkc5LZO5Pur1AZ1PRh868/eknwnfzXX7qUltjYBbe05Oc97z+14Yxj66lTsEYd4HzUZGANK0HopkJo/ELkE4a1G3wkrubXQ8FNcB9FOfk0XiQO38bbAfKSu3vcrvhqeoHEkzdw//EJ49WgqW12fAVzTtk7S4dA0e60FEukvL22DpcRSBVjXn3yc5zsennW4v9X0/TLdZ9QvrezjclVeaQICcZwM9TXjTtA4u1DijVOe/vnvJIcxCQgD2ATgbURRxMZeeQaN27zy8uamSGNzldS9GeXv+LtRmlPM8lhI2T75Er0TI46CvM3ozs/7qbnfGdOk/rpXo4zcp3NSr+3IHdwSGoBXEfSlJMPDZ99yP1R15unPtH416P8ASgcSWfDjA5w9yP1R15vn+kaz6o2haPH5oOfdM0KFDFYipQoUKGKaySNSVNS4rlQcgbjzqOsLMOhFPJaseq1qUQmYeyNEtVKS/dI/ZmdACfZDVqRxhCFVXs2zyj+MHl8KkaH2Rahr2n2t5b30CrcRiTDRt7OfDNar94ebGZNcgBx4W5P7a7CinrIQbuttvr/hbdLDXwi8Yte3T6rHHjO2UbWRP/mf9KQON4VbmGnnb/Cf9K18vYeU660p+FsfvpCdiyZwdYP1W3/WjzX1ztnDyRl8Vvcf/qucaxri6ncySiJoeYAY5s9BVNNK0uMk4Hma6ZxD2RDSY0uPwv3qyPy47jBG3xrDa3oo0q8NushlAVW5sY6+6uexGmrZGmaTVt99Fj1tNUtJlnG57ufgqbrT9q4ViD4020ZBpIB8K5yPNDIHWWeDZbTQtbeV7fTyPYCFeg8BV3MRGRjfI8PCucwTFMEsVI6HOKt7HiEWUTJ3DTZOc9502r0XCPSNgYGVBsOvu6ALYpsSszJKff07lqu991KjAdgCcZ8azB4sOdrL/wBT/pRHipmVl9TxzAjPedM/VWt/xLQbZ/gfsr/X4ubvmrvXdQWxggFrKpZyQzRsDjFYW8uGmlcnOWYkk+O9LmuGb2VOw6nzphQGf2vCvP8AH8XfXODG6BZdVVGd1zoOimae/dxMGB3Of1V0fgFk13W4dPWMpzQOSXAYYC+Vc3g+ifjV/wAMXsllqayx3BgYRsOZW5T4bZozA55GZYmm19EZhs5ilb00XX5OzW8i53ivLcwqCw5wwbp0xT8WkyaLpcTzNGzysT7I+iMedQuFeORDYi3ur+3ctMd5nyxBx76t+L7+K5srY6dMs5VyrJF7QAxsTXWcWodIIpDcX3C7SCxs4bLFatIDezEHJJ+VQon5M5Gc07dc5nd5hyPnLZGMUxIQACrA/A1uMFmgKiR3aLlbaFqUenapb3cqM6RkkhcZ6EV0PSNZttat2nhDx92/KRIRmuSwEuQoBOamQtLCcozx7+BxQdXRMn7V7FTjkzBbXS9CvJdYdVmjVSXk3yQRn/rVzcaXPZueeZWUDOBmpXCVneLes11bzJG0WQ7pgE7VpbjTre5B7yLJO2c1zlVXFsuU6i3JXyT5X25Kk0AiKaQ46p+2ra8t7bVrVrG+hiubWf2XgkGVcZzgj6qVDpEUbE28YQ4wSTUhNPdSuSCQfCsueVj3ZkHK5jiSshwF2WpwhxDq2ptJZSwXZ/sWGOM81sOcnGW6bHG1dGCBVGPKosCsM8ykHNSQSAMZyKz5CSVniIRjK3ZOKh5cjrXO+0XtpsuzjWodKutJu72Wa3W4DxSqigEkY3B32rbahxBpmitGuqanY2JlBZPWZljLgdcZ615V7fOJU4l7Q7o29xaXFnZRx21tLbMHWROUNksOp5mYfVWdVzFjezuga6cxM7J1WU444nfjXivU+IJLdbZr2XvO6U5CgAADPicAZNXEFn+ANP8AbYTFF58ouDhsbVVaBw+7XBn1GAxwx9EmUjvCQcfKl8WXpe+WNJQY1jGQp8ffWzg8BoKeTEZ22cdG9e826X+SAjYYYnTPHaO3+FU6ndi4uJGCsCX5t6g5pTtzMTSa5SrqHTyukcdys1xLjcoUKFChlFHQoUKlZJChQoUrJIU/aXK2xbmUtzY6GmcE0VWRvdE4PbunBINwriKRZkVgCOYdKUUxUG0uRzqr4UAdSasEZZBlSGHmDXTU0zZ2A31RTHBwTDLk0Rjz41IKb9KSV+NSdCnLVHaMgedI5SfCpBBA6Gklc5ql0fRQLUzSSae5KaIIJodzSFEhGkgjzlc5qdpeqpp97HcmEuEzsCB1GKr6I7VOGqkhIcw7J2SOY4ObuFs04+hUACxk/wBIKvdZ0q11yOJLwSERnmXkbl3Irl3Ng9a1OgauVjm9b1AluYcveyeGK6fDMW9dcaess4HrYDmdfgt+hxYy3iqu0D4BU3FWkQaNqYitye6kjEiqTkr4YJ8elO8J61HptxJA8Tubl0QFSBy743+dS+M7G6vrm3u7eCWeBbYFpkUlRufGs5px5dQtienepn9IVytUXUOKF0AygO06WPTzWdOTTVpdELC+nv8A8rrQVg311n+ObMzWFnOXACzMnLjfdc5/VWkVg7gHOxqp43iYaRaHkIVrliDjY+xXpWMMa+kew87fNddiMQdRyk7fuFz6VAjLnetbZcLvFw5PqQdSZLZpPonIXHSstdRnnT4Gr4aBf/gY3Q1K47nuDJ3XtcvLjp1xiuPw+NzZJHNjzWHUaab6rkKFgzPvHmsOtrd6stK4bTWOEbaSSUKwBMexyDk7+8bVh5gOVwPDatPo3Dt7faXBPHqt1DEykiNM8q9dhvWXlBAYVRijnugiL48vZte41AA6fVTxFgEUTuHlu3e976DXuUUjFOqNhTZFSAmw+Fc1TsuSscC6sNB01dVv4rRpO6V+YlsZ6DNdIfh2O3sJO4ukYCFmCkHOy9M/VXONF08alfJa87xhlY8ydRgZrRScFslvK6ahcewjPjl8hnzrvMEM0VO58EdzzNwOW2q6LCQWwucIc/fmtbTorHgXSPXeHpJ/WYoeXvJArA5cjGwx4ms7xRoqaf3dyjbStylSDnOCSc05ofCt/qelLdw6hJbxsWwmGxt1OxqHrWiXOmRQtcXpnV2IUZO23Xeq5pZpMPa18OgAs64025b6pp2k0TCYToB2r/Gyo2+kaaf6VPv1NMP9KuIqRYLnyk0eCBnFGN9sfXUqWIxqAylcjbIxmoRQZwTfZIC6PVrpbmYKqkd3lST471CoE53O5oqqqJ3TyGV+5TucXG5R/GhihQqoKKehX2SffT2mDMsh8locoVOmNqdlTu9LSVDys0gU42J9mtaGLIRJ+kXV7G2N+ivOC2xZakAxBJTx+NTSfM03pqpBplr3Max95ArOVGOc5O5pxt67zDYuFRxR87X8zf6rdYC2JkfQfPX6pl5uViKjyXah+UgknpijupO7mSPunPP+UoyProuUA5IFVSyuLi0HZDOcSbBVNwhM7nzYmoN2vK65/NqzvIpVaSTlPLuQRVNI7MQXJJx41xuJERktI1Ky5tNFqOAbf1virRLfJAlvYEyPfIK93zy808h83P214h7HYTddo3DMW299E24z0Of2V7RZm529o9TUvaY3wV0I7Klc2xryN6SF4LjtK1VRnEQgiOfMRL99ereZztzGvHfbrOZu0jiIlublvOTP9FVH7Kdp4bXnu+yU2jVzonetBwZAbriHS4ACe+vYE297qKz3jW17JrY3XH/DcQOM6jCfk2f2UFRH8wlCR+0F7pmf8a/9I/bTDzMu9RTMxOcnek8+cgk0SI7LQsvKnpH3Jl7QtXyf/DoPgIlp30Yog/aTbMRnu7K5b4exj9tU/b7cCftE11gc4vOQf5Mair70YEYcZ3syn+D01/8AadBRUrszwzowBDHWUBermcK2RWW7UtXay7PtdlDMjC3ADIcEEuo2P11aCdyd2asR233Ih7LtaBLEy9zGMe+VT+yowR5JWOOtiPmiT2dei4O2r6jJ1vbsg+cz/fTT3E8xzJPKx82cn7TXO7iRllcB2wD+cajmZvzm+ddE/wBLGRnKYvj+ytOJt/R8f2XQNSultrKSSVyUXBO+T1rFX2oyXzjnxyoTygDHzqG0hYYyfnSQaw8W9IX1tmMGVvMXvf5IKpqjNsLBWOn/AEmPur3lw5IY+HNJQfk2NuP/AE1rwVYZ9oivdlhKYrG0iyfYt4l+SLVLe3C0eKlANFavIW60hmAUsSAFBJJ2AFR1kLkDmNc47SO1GwstEvdP0TVXXVlmEDBYT7K5IkGSMdNqnT0j5nBkY/bxRLWE6BYXts7SYtSYaRDcabdWEFwJ4rm0kLlvZICt4ZGT0rFdiQE3aPocre0Xv1bPuGTWD1G/Fx+LQHlB6mt92FRc/H2g745Z3f5IxrQFQ17zDF7LWnz5lUcXPIANgvYBmBAwfCqrim89X4X1qTwTT7g/+m1Ohyqg5qh46uGTgriJt8Lplwf9g1lCMKwjReLebmu4z8K9jdjBCdmOhDzjlb5ytXjdDm6SvZPZOqp2bcOjOM2pb5yPV2fPG7/u+ioptbrbLJj415f9JWUt2gYJ+jp8A/rH9telefHjXlz0ibjve0S6GSeSzt138PYz+2ow9gl3crJtGrkchyaQBk0GOTRxj2qw3dp6z1peCOB9W481ldI0dIGuTE8346URryr13PxrrHBfYPxhoHF2i6hfpp8drZXsU8zRXiswVWycDxO1QPRkiReM5ZzhWWxnAP6Ir0g0yAnckmtlsLogB1F/NGxw6AqwkuhcuSQBucUiZhHBJIoyyozD4hSf2VCSXP0Qa5F209pzafaT6Dpd3dQXkLn1uSLMZQAfQDeOc748qsgpHSuyM0+gRGVeb9UuTczvI30mZmPxJzW87EH5e0Hh3/8AkZ/2Grmsjl291dI7DxzdofDwP8sx/wDTalTT8SV56goKI3fdeuu/BUVlO1JgezviM5/vF/tFaTkAAwayvawqjs24jfm/vJv6y1FtgQUW/QFeWeC9BbiniW00ZLlbZr2YRd6ylgmxOcePSvaFkPUrC1tQxcQQpFzYxnlUDP6q8i9iy83aPohz0u8/7DV6xWc8oB61aSXxtvtr8yoQC7bp7U9VXS9KvdQMLSi1gecoDjm5RnGfCuXT+kPFgd1w54b8934/UtbHj+7mtOz/AIiuox/B2EmMjzwP2148l12+ViplUY/mCiqR1FG1xqmkm+lv8hTMsbPbXoFvSFvsnl4fs/dm5f7q33Z/xndcbaNLqM9pFatHcNByRsWBwoOd/jXj1NcvXYKbg4O2yivS/o5TyLwPdi4l52/CL4JPhyJV1TLRSRE00ZB6/wAJTtmY/RoXVAQSOc71599KmMLrfD4DE/2DL49Pxtd8mZQ/tSRoT05nAz+uvPHpRziTiHQwkkbhdPfdGDfxp8qxprBtzsqqj2FoPRjjPda6Qf4u2+1q7nzkGuCejdfw2dprTT3EMIdLYDvJAmfpdMmuztxBpCAGTV9OTP510g/bRtXGXSXaNLD5BWtByhXUWoSxH2So2x0GaE13LcbSOxHXBO1Z2Ti3h2L6ev6UP85U/ZUeXj7hSEHm4k03I8pC32CqBRvJuGHyKllPRaQkDaiEzx/wbsvwNZCbtP4QiGTxBbtvj2Edj9lR27W+DEBJ1d29y2zn9lXCgqD/ANM+RT5Ctw19cFeUyvj4mm++LNljkmsDN2zcHpkLc30n9G1O/wAzUVu27hdc8septgbYgAz/ALVWtwuoO0Z8ksh6I/SLx+9dcMAM+u232tXMvRm5l4g1kjOfUV/3q1Y9sHaxpfFPA0+k2lnfJK9zBIHlChQFLZ6EnxrG9jHHEfBep6ldS2D3nrFqsQVZAnKecHPQ+VDspZm1IgLe1bQeP+EMWkTgL1bGxOM1xb0jNQgsdS0ESqzc1rMRygHH4yrFu3kE5j4fHT8q63z9S1yfti4+n40vtNMthFaG0hdR3chbm5mzvnyxWiaarw+1UW2A8Dvp1REueJudZ2fXrYseWKUj4AVsOxvV1n7QtJWOJh+MZTzY6MpG1coLN5mtT2e67Pw/xDb6lbLG1xb/AIyMSLlSR5j66hTY1PWzcFxFnXHwP1Q0VQ97wCvZyurKMjG1c39IR1PZs/KOl/b/APFWLm7aOKHB5fwcmemLfp+uspx32ma5xBoT6PqN1A0UkiTciQKpypODn66sqMImhjMjyAB3omeMhhJW79GCT+0nEe3S4t/seu2r7Q2DfUDXkHgfiDWND0y5TS9SurNLmUd6IX5ecr0z8MmryXjLiiXCvxBqpA6D1lh9lWUuDSTQMkzDUD5BKCM8MLW+kLxhd2epxaBt3MMcd0itEPpsCCebrjA6VwGScySsxG7Nnb41b8U65daxfvNeXVxczKoiLzuXb2fefCqHPjWHiVUWuFO06M6deZQNRKXOy8gu6ejVKw4mvCFyFsJM+720r0K0yyDm72NR73H314t4XulQznvxHhQN35etW02pwRsEN2hyM/wmRXQ0+HMqoWzulA0t5adUdC0GMOJXU/SYULp3D5DowM1xjlYH8lPKvOk3U1faxdeuzIkbd4Ix9IHI3qomtyOorDxGkLSWMOYDmgagXcbKFR4pxYstipK2hIHs1jxUj37BUBpKhYNPQQ5wzAY8qfa35DhlFTdMsZNWvreztgnezuI05jyjJ8zRlPh/b7XkpNYSbJ3RdLk1jUrWwgMfe3EqxrztgZPnXfrbs04Ytcl9Gt3JxnJZgPhk1X8JcMw8PaBajULOza+tueRpERWYbkjDYznFQNW4tub255oZZYk5cYWQ7fLxrsqHDXvFmrr8OwwRx5pLXNtxt3K41fVrPRrI6ZpBNrJCwjVI1IEajqAaytzrN/dFTPeTuVGBlz0qLcXEk0rSGR2ZtyxOSajknzNdTTUbImgWuVqlzWjK0bKS15cHrPL+maIXlwOk8v6ZqPzHzpJJ86LEY6KBlstBpeuPFLELxPXIFB/FTe0PjvV/BYcN8Q/2U+j2Cup5OWSMZrAB2HRiPrqRb3TJjLtnOetA1OHtl1Gn86KeeOTR4We474GbQnutRE9uLeS5KxwopBUNkgeW1YZ8RnHnXofTtVsNbt4tOv7BLh0UsWmAdWI8d/GuW9o3DM+l6lcX6wW0djczcsIiIHL7PQr4dDXDYvhhju9ot/N1zGLYWIvzohp0+qzFno11dBZ2gkNvzYJGx6f/AFvWwHCekQkKbdycAnmkPlUDS+JWlUQLlCkYyw8+mKslvDP+MZmbPjXQYPhlAI8zbPvzIHw7lKijpWMuBmJ6gaIv3L6Mf71/9RqVHwtoxIBtTg7fwjU4GJ8aHe8pxzEH41tnCqTfht8gjx6uN4h5BUus8JMNQeLTbCUwhVwc5Gcb7msvdWj288sLjlkjYqw8iK6baagbcsWJkz0DHNJ1vTLfWNMlFlb2ou2ZW5sBSd98muexX0cje0uiFvD5BDVWExTtdLTmztTl+gsuZwScp7tgST41Lgm7uTmxnbFI1bTJtOu2t5iglQAnkbIGRnrUaCbm9kk82K4cOfTS8J+hB0XPDNG4tdoQr2wnHrEU3LnkcNj4Gui8N6/Je3ckCoYwEMjAHIbw/bXJ45HUjldgM52NaGyu5eZpbeWZCBgsCV+qurwmtLgWH/K28MrnQu025962WugzX1wjkgNjPn0FV0MIhTkBJHmasOFIp7u8t57gGaPJ5zIc52Pn1q64i00yrAbK0UAFufu1A8sV1rZmgtbblut8RmX8626hcMxWtzfw2s0ODK/KZgfaUYPQdK3Vx2a2CKrev3QDDmA5FNc+0WZdO1S3uZiBHE2W8T0x0rrNvcyTRKwZiOUEb5rNxSSaN4dE6wKubmDVoIUdEVfbIVQBn3Cn1wBkiuI6dxPqdtqkMlzqV61ukuZF7xmBXPl4103TtZGp2cd1bySGGTJXmGDscdKw6zC5ILFxuCquHfmtLb/TY+6nh9Ibb5qshvFZjyM2cb+FSopXkKEluvWsl7DdUuYb3U72aeiIJUAE+6ou/tdaxfbFrUui9nerXFvfyWV2RElvLHIUfnMi+ypG+SvN9QNCSnK0uPJCzOyMLjyWC9KzVLJ5uH9K5JPXYEluGzH7PdvhRv55Q7V5/toTNdwxA455FXPlk1J1TW9V125SbU7+7v5lHIj3ErSMBnoCfDPhXQeHNMtbHR7b8I2VsLhQWLsgLAk5G/nQeF4a7E6g5dANSfLTxKxKWlNfO43sBqoPEN+1rYiV1L4kC4z8awt9c+tXLy8vJz4OM58Km6nqc14zRvcSSRKxIDHrv1qrbc5FH+kmLGrfkjPYFtNN9dUNXVXHkuNkVChSkXJySAPfXLNBJsEGi8KOnXRHOUIApoqVz5CrnRlp6hKyGKAFGN/AUpRvSa26ayTij5dqWAPIUeBVwjT2SMUkjFPcufCiKe6nMRKfKmqstNYGAgfnGoRT3Uau0YIUlR7jV9K4wSZzspMOU3Vv1oiKTDIDGmcklRToGRXSNs4XCMGqbK7Uhk2NP4GKSRgdNqZ0aYtUfG1Nsp3qSQCOgphxuaDlZYKtwTBAFJbYU66k7gU2wyKBe2yqITRFOgCkEZosmqWWaVBdOTmj4LaPcAWZz+jXKVYq6t1wQa6fw1ctPoVsk7GXnVlYPvzDJGPlWP4y08WWrM0VssNtIq93yDCkgDP666H0mpjLTQ1jdAANPEXC6PF4TJTRVDdgAPMLZcMa0ddiNw9uIeSTkwrZB2605x3L/amzQMeUTscf5NYDh3WX0rUbdmuJo7USBpUU7EY8qu9a4qj4jhSKK2khEMhfLMDzAjH1UbSY7HU0QZK78zbx530RbMXimw58cjvzD8duipLs5KfA11FLBW7PpJ2Lc/4PJGDt9GuUXTFXTyxUz90WpG19UOoXPq/J3fdc55eXyx5UBDXMifIHc9Fk4fiDKUy5wTmFl13gew0luzKCZ2lOpknlGfYEe+dvOuI3B+n8T9tTI9c1C2hSG3vrmOJRgIkhAA+FV0r5U0HXVrZIWxi/ZH0G3kq8QxBlRDFG0HsC2vgNvJMk71LHQfCoZ3qTzgEDNYtI8AuWS1abgdM60D5ROf1V0m5uoINAvT6mqP6vKDPknm9k7eQ+qsZ2TSWcWuTTXoj7tID/AAmMbkeddQ1vUIZ+ynVg0kb/AIqZokJHs5YAED6672imENCLC9+/x+y7fCZDDhjngXvm+VlQ8ENov720CXVxcJqAWRoURQUbLnZvLpWG48ChrJVORhz9ldr7N9F0+Lshtr2a1ga4FpK6yMgLA8zYOa4v2lyltStCevckk4xneoCoDsPezXS302VdS6+E+AaPksO49o0w/wBKn2OSTTD/AEzXC1Wo06rjCjHSpkSy6jdW1tJNI4LBF5jnlB8qiLjNaTTIIl1yw5EUbsTgeS0ZQUpn0J0u0H3lX00RkeG30JA8ystQoUKw0OhSkHMwBohTqHuyCfCrYmgnXZOE/O3JFkfCpOqBYNPtIFOS34xv0QKiOpuIxyHx8aauJu+ZDuOVQu/urRlnyseP1AAedyr8+UHvWy05s6ZZ/wCJUfbUfUrx7SNDEELMT9LpgDNQbbiK2t9OigCSmaOPlzgcud/fQstIn7z1u65Csi8wUHoT7vCuubX8aCOCk7TrC5H9O2/xWm6bO0Nj1Nh7k7p+tPdwztJGqmNcjGfaore9a4HtRqMHw8aaujFaOIYtlYZI8qSbmIFQqsFFDipkYBHNJct37/8ACo4jhYOdsoV/qU5mkjAVUGV6darWYtjONtqmXThpZCOmSd6h43rj66R75DmdfdZ8jiTqV1D0fIu+7VuHgBnkaWT5RMa9btNuSRg14s7NOJNQ4V4ot9U0t4luoYpArSRhwAVwdj7q6bL2ycZSyNJ+E4l5jnlW2jwPhtXTYZg89VHxGEW21J+3ej6SFz2XC9EWzGW4jQDJZwNvjXiftSvPXuOuI5+UqH1O4IB6jDkfsrpEfa5xtHOssetujhsgiGPb/Zri+u301/qd5cXDtJNNPJI7n8pixJPzNU4xh0lDHmkIN9NL/ZQrIywC6r/GuldgcBuO1Hh1AvMRPI/TP0Y3Oa5pWj4J1O70zXba4sbqe1uEV+WWFyrDKkHBHurEwxhlnEQNi7TzQcLc0gHUr3KWZdiDn4Un8ZK4REYsxwABXl48W8ROcnXdUJ8/WX++kJxLrySLIus6mGByCLh8g/Ou5HovL+seRW4KNyyXa7dyXPH/ABCHULyalOvXxDcv7K6T6K9u0mrcQy8yAR2UQPMQOsn/AEriOt3Ul1qd1JKzvI0zszOclmJOST51d8ISlPWmDY9lB1x4muZoacVNcYc/de3QdNOiyYGl84b4r2yXiAJaaEAdcyL99cz7f9Rg/e0u44Lm3dpLq3Uqsik45iegPuriD3C49uaMZ83H31T8R3EQ07CPE7d4uysM+NdBVYIymidMZfZF9v3WhNBlYTdZOdiZGyfGm6N25mJpNcBK/M8lYqOjFFQzUAUlY6aw5gD0LD7a9hXHajwYsgEetIVCqPZgkxsoH5teN7KZIpULnC8wJ+Ga1rcSaZk/jZT8IzXZYPDSTRf8xJlI7wN/FaNGIyDndZemI+13g2N15tVk6jpbOfH4V5g444nOrazq72/L6vNeSvG4BVuXnJB6+VGeIrDmzzTHf8ysndSme4kcZ5WckfDNSxR9PRx/8nIXF1wdQdPcEqqRrRaN26aJJNdA7IdbtOHeLbDU74TtBAspIhUFiShA2JHiawHKattE1COwuQ8wcx8pGFGTmsfB+GKgCY2adCg6cgSDNsvUQ7beHlUf2Jqje7u1H/FVTxf2z6Df8Ja7Yx6dqXe3VjLBGzcgUFlxk79BXDm4psx9GGdvjgVD1PiKO6s5IIYHRnGCzEHbxrqqqHCmxuLH3NjbU7rVldT5TZ2qpEcG4UivRnCXa9p2g8KaRpf4Iu5HtLRImcTqAx3JIGPfXmsOUfmHWtJFxZDFBHGLWViqhc84HhWPg81D2xWm3Tf6IKidGCeIbLvr9vESkldAkPlm5HT9GuIdqfFg4u4uvdTFp6oHjij7rvOfHKgGc4HWoX7r4/Gyb/Sf9KoL67a9upJyAvOc4HhVuMT4dwR6ke1fv296srJYiwCM3UY0qMbii5aWoIO1crG05rlZoXUexziuPg7UJtQlsWvRJbyQqgk5OUkg5zg+VdLk7cPZPJoJ5vDmudv6teedI1mXTXJ5e9QgjkLYAPnVkeLpD9GzjHxc13lFPhb4WmovmAt/Vy8NFs081PwwHnX3rs9/22Xd1p13aJpEMDXELxCUXDEx8wxkbDeuDazrdzeSTLLIXZnPM5JJYe809qWvy31q0BhjjBIOVJJ2qjK5NZuLV0LBwqEWB3Ouvdqhqudh7MWyKtZwDrVzoWvWOo2Ri9ZtuZk7xeZd1I6ePWsnymnra9ns5VkgfkdcjIFY2H1DIJg+YXbzHchIXhjw52y7tJ2xcWsRy3Nom2PZtl/bVJxh2pcVanwxf6fd30T21zGIpFFugJUkeIGR0rl417UDv63J+r7qauNRu7qNkluJXU7lWbY10lTi2HSQubFDYkG2gWhJWROYQ1uvuVtwRqd5peu295YzNDcwsXSRQCV2Izv8a6LP2g8WOuDrl6B7iB9grjkLyQvzI7KcYypIp9rucjBmlI97mh8MxaCnp+HJFmI56fZU01U2JmUtuui8S8fcS3OgXlnda3fzW06iOSJpMqwJGxHltXL5ZC7FjS5ZWbYsxHvNMnesjF8RbVPvGzKOn12CoqJhK64FkpDgg1stCv19Q9q5WI85yveY8t8VixTisQKWD4o6ieXZb35JqebhOzWut5JqluT7d5E2POTNZjiO9jub1e6IZUQLzA5BPXaq7n2xjem3UtWjimPS1cPCDQBe6unqzK3Lay1Wg3tu1qwkeOMpyr7ZAzt4VYm9sh/fVv8ApCsMjFRg70rvfjRVL6TviibGWC4U464saG22W0OqWI63cPzps63YLt60v1KfurIhifOj3PnV59J6g+y0fH7qX4g/kAtS+vWH8u5+CGkfuisB4zn/ACP+tZnlPlQ5T5VS70irDsB5H7qPr0nQLRPxHZ+Ec5+oD9tIPE1uvS2lP+UKoOU02yt4VRJ6QVvX4KBrZf4FY6trh1CNYY42iTOWBIPN5VH07VZdOZ2jVG5wAecdN6hEYpJBrDlxKpdP6wXdrqh3TPL899Vdniq98Etx/kf9arr7UZ9Rm72YgtjACjAAqKFJpxYWPhUpcQq6luSR5I6JPmkeLON03jenra4ltZO8hco2MZpQgb3Ufq7eQqmOGRhDm3BCrAINwpH4av8Ap60/6qjXV1PdYM0rSEDAzR9w1D1dz4CiZX1Urcj3OI7yVY5z3CxJTUcskYwruB5AkUppnPV2+tjTnqzY6CkmBvdVIjla22qhYqO25zRU+0JA3psqelBvicDqo2Rxtjap1nbiY8z9ARtUARtVvpijkOfBt618Ii4koY8aKcYudV2nVLPhLTtRNvBwlYcsWA5Zn9s4Hv2rB8fWNmby3l0/TorOJ4cskCty83MfPO9aa21iy17VpAWmiaXLK0ijHQbHFPytJau0PMV5CQVztmvQPw6IxiO1jZeguw6mq6fLDYd4H+FyW002SaYqI5GPkqk10uz0rTrPT7JZdJtHcwIzmWM87EjfNTLe8ntZ+/t5Wil/PTY01dPI6y3Vw7vgF2YnJNNSYVFT30uoUGCspCXuIdpzH+VnOPIrFNMtpbXTra1Zp2UmNdyOXpmo3ZjYWd3qUt3crIZLN45IeV8ANk9R49KZ4v1yz1DTLW1hWUSRys5LAAYIx59af4FcaTaTXc2StwRyhNzgZ61mOp+LiWRg0A18vvZY7zBNioLbFgAPdt91v9U4qu0Z4oxFytzLkrvjpWW5ivTFMyaijsx5XyST0pBuFCY3rroIWRts1aUtU1xu0qQJssRSuao8bjGR0NLEgPQGiLKDJbjUpzmos0ksKLmpWTl6MuQegohNjqKSTTFxL3anH0vCkSALlUvlyi91caVqslneCZEQkKRg71N1eW24ptFtdQiBRJA47slSDjHX66zsT8yhhkZFWFjdRxAIwbOetC1FLHKLubdG007JOxLqD1XNBMbK5l7nA9orvvsDWh0+8ZrWKRsEsMkCqfiPRptHvQk0sUhmBkXkJ2GT1zTumyg2seCfZ9k/GuCwqqlp6p1O7Sw28lx8YkgldG/QjktAt03hymiMp3O2etQopVTOc70hpuUMTzdM11hrbC5KOM2ieu9cWwZVeMuzAkBaVa8Vzw8zCzQ8wxgudqq9HsxrutogYos8qICwzy5IGcfXXpr/ALI+nxkq/F0ox/8ABD/mrnpceeDnklytJNha+3PYoZlbK12drrdF5g12dtSvDddyI3dBzAEkEjaqUKYX5hv4b16r1r0VtOsNKvr5eKriVra3kmC+pqAxVS2M83ury9fxGIkED6q5vEOFU3qInXIOultfJCTkvcZCdSjhYsgY461bWuova8wVUIbqGFUcEwCBSDsamd8CehqWH1ZYMzTqpQyFuoOq3ei8W3VrZwBIbfCZI2Pn8a2/B+sniL1lLiNFMPLju/HOfP4VynRtQtYGthKrlVYcw5Qdq7Rwg1jbResRx8gnCMPZ3I612ccwkpw4G5sF2GGSyTtBz3DeXuVLrunaLZ2Fw9reGW6QgBC4/O32xTelceahYWyWyxwSLGOUM4OcfOi1zRLmzNxeOIjE0hOzZO5ONqzblVBYZ5+boBtitVkbJIwHnMEVMSDdSmmLMWHic1otI42v9LsIrOK3tWjhB5S4OTk533rKq464pxXyNqLlgjmbleLhRa4Luunu8io7AAvGrHHgSAatoGb8WMDAPzrF8OcV2mr3AtYEnVkizl1AGBgeBrXw3UFrZvdSBiIVaV+XckKCdh8BXBVsTonWeLJTC2pUnVtYsNA0+bUNVu4rOzjI55ZDhQScAfOvMXav2vXvFst/oMC2EujpdiS2njjYSOq55SST45PhWx7Uu23hPjHgS/0fTI9TW8uGhZBcW6qo5XDE8wY74FcT0Lh+74juZYLRoVZE5yZWIGM48jXLVEz6hwhg1v059y5eqqH1DxBT636c1e8G8OW95bR6pNJOksM+UUAcrAYPjVnxXrr2c8UUYiKFOdiRk5zir7VEjsOH5xIf4GDlPIPIAbVy7VbmK5ljaLmwq8p5hjxrtJ3MweiEUPtkC55k8zZH4lloKdtPF7RAJKgv479aR16Cj5Wc09HHyb+NefBjpXbaLlgE1GgYnINT7KBQVkIydx7qjO4TdulINy4f2HYL4DOKKhfFTuBdrZSaQ03KnmyjZy3tDO+B0qLPEEkZBnAp+LUo1iVZOcuBucdakRuJkDqNj59a0yynqG2iIvursrXbKtEajzoxGCRuanzWzSlSpAx50y1oY/aJXahnULmctFAxkJkRDzpz1dfM0YyvSnVwehFTjhadwnDQmhCB50fdDPjTvIaMRn3UQIB0U8qYaIY8abMQI8amGMigEqLqYHkmLFDhlkjmxzErjoas7eTvYyxAGDjaoktsW3XAanLWVYAIJM87HIxuKtpM0L8rzopR3abHZS8URG1HQrWIuiEw2xApt19o9akOnMwO21AoSNvhQzoibqstURhgU0UwPGpMiFdqYcbGs+Vlt1S4Jor5UgHwxTppqgn6FVFX+jcR3Ft6pZLFAUDheZs5wW+PvrW6zoNhriiS5eZTbo/KI2AB2zvt7q5rCwSaNznCsCcfGuicL6hBqqXcMfeLyqMlx5giutweqbVwOpao5tgAe4fSy6TBZxOTSzag7A9w/ZcvDBWBAB9xq1tOSK2VxktJuc9BTuv8J3fDsMMt1NbuJXKqImJIwM5OQKg2dzGid25cnPs+4Vx9EH0lQY5xlNufL+BYL4ZKeTJKLFSJwshBbbFRJOVTgHNSnljPmR8KjSSRk+znFF1TgTe4VT7JpnxjFJLZBG1GxDVLjhisbnFw6OTFlQg5hzEbZrNDHSHQ6aXPIXUWtzFR1jEKrK/U9Fx1HnR4WT2hnNB52ZR3h5jSVcCrAY29lu3xumuEO9aNuXwp4XLFeXmOOmM00SGG3WmypQ70uM+PncJrlWUOs39tGscN7cRouwVZWA+GM4pq71K4vRGJ5TIY1KqWJJxnNQlKnaj7s+dT9blc3KDopcV9st9EovTTH2iaM7GgRzdKDkcXCygiB3qXYajJp92l0gWRkzgOTjcYqH4UKhHO+MhzTqFJrywhzdwioUKFDqKUoywFLf6JpC/SFLf6Jq6P2CnGyXbOenNt5U3NEEC7EE5yDTtioMhbPQVI1tjPq8ijGcqgP1AUU6O9IHne9h/PcrQy8ebvT2iaU81wk8sZESjnBdMrJ7q0dzcqseGYDfaovt28K24P8EgTPwFQLjv2Ud4/NvXZUsbcOp+HGCSdz3/stJhEDMrd0xcqJZXlBxuTTANSjGTGd/CoMkixYLeNY1T2DndpdBP0NymZcF23HWo7DHjRs2XJHQmgRmudkdnJIQpN1J07UJtOnE0PJzBSo5hkb1PbifUSfpwj4RiqWhREGJ1MDOHG8gdxsrGTyMGVriArj90+pfy0Y+EYqqkkaRyzHJJJJpFAVXU189QAJXk26klM+V7/AGzdCnbe4ltpBJFI0bjoynBFN0KFY8scHNOoUAbahTTq18et9cf6Q0htQum+ldzn4yGolCizXzHdx8ypGR55lG7F2JJyT4mjVsDBpOKFCh5BzKCXzCgWBGwpNCpmUkWSQoUKFVJIUBQowN6k0XKSUop1VJG4o4o+Y1aW+ntIinHWtyioXzeyFNjC7ZVwjY9AaHcEn6Jq8j0psbDPwqRHosh/i3PwU1uMwCVw1CvFM48lm/Vz+aaMQMPyTWnGhTnpbTH/ACD91K/c/dHpZ3H+jb7qtHo84c1IUjuizHcuB9E0lom6cprXx8J6nMMx6XfOPNYHP7KcXgTXZDhNE1Eny9Xf7qi/BCNMyf1R/RYkwn82mmXFbyXs54k7t3/AOphVUsSbdsAAbmsbcwhDWTW4aYRe6olgcz2goRzQAoz1p2CPnYCsaOMudZUgXQSMkbCliE+RrYaJ2ccS65YR3+naDqd3ayEhZYLZnRiDg4IHnU2fsu4rswpuOHdUgDfR7y3Zc/OuggwkusCRfxCJZTPdoAsKsLeRpwQNj6JrbJ2d8QHcaPd/WoH7akRdnWvuu2kzj4lR+2tOPBQN3DzRLcOm/SfIrAtbt+aaQbZvzTXQ/wB7bX84Onqn9KZB+2nP3r9dx7VtbL8bmP76TsEYf6x5qX4ZOf6D5Fc2MB/NNMvGQdxXSYuzPWbiZbeOO1MjnCr6ym5+dYzVdNksbue2mXlkhkaNxnOCDg1nVmEGNtwboeoopYQC9pCqFSnkhz4UqOLLYrZ8F9netcZi7bSbeOZbNVaXnlCYDZxjPXoaFpKDP7Wg6qmKIvOULHi3b80/KiaAgfRPyros3ZnrVq3JKloh64NwtFZ9nGo6gxSGSyYgZIM46fKt0YG0suHBaH4TUWvkPkuZvGR1FNFa1PFnDVzw3qHqd33XOY1kHdvzDBz4/VWbdcGuZrqPhPIWbLE6Nxa4ahNAZp+OAv4Zoo05jW54D4Mh4lN81zqcOnRWcSyNJJGXDZbGNqnQUXFdY7KVPA6Z4YwalY31RvzTQNsw6qa6WOCNMM7RLrQZR0k9XIDfM0huDNKzg6nN16i3H31059HhbQ/Ba4wGqP8AT8R91zR4eXwxSUiyelbbjrg+DhlrIQXjXQuo2fJj5eXBA23361TaDpMd9qNtFOxjheZFkcdVUsAT8qyThTjKGNF1my0j4pTE8ahVkVmzdFzUkae35prtOucHcAaLqs9nbfhi4SJipYSqRnyzjeqifSuGRL/Y9jeNHj+Mn3z9QrcpMIjcwOLTr3LZhwCd4vsuYDT2/No/we35tdPTT9AB30liPfO1OLHoKKR+5+3Y+BaVqO/CIx/Sfh90R/w5L1H89y5WdPbH0aZlsiAfZ3rrYbR1+jw/Y/WWNOyvplzp93apoumwTSwskcqx/QY+O/21TJg7SNG/JJ3o5IBfMFw+WIr1FM8m9aHW9Jk066NvK0buAGzG2QQaqDDhulchW4c6J9lzEsLmOLXCxCKCAuQAKtotHlP8U/6JqRwzbq2pWpYZHfx/1hXoXj3jLUrTivU7WKGzijjk5UCwLsMDetrDcNHZBbe4J3ttbuPVamGYaaokXsvPH4En69zJ+gaeh4eu5s93aTvjryxsf2V1mXjDWJcj1lVBGPZjUfsqPJxHq8py19KPcuB9lb7cKb+gef7Ldb6NDm5c3j4S1NzhNMvGPugY/sp5eCdadQy6TelT/gWrfNreptkG/ud9j7ZFMevXR63M36Zq0Yaz9IVo9G4+bliP3B6+T/3PejPnGRQfgDXgQDpVwpIz7QA/bW0e5nf6U8rY83NNtNIx3kc/Emm/CmHonHo5DzcfgsFxJwPrnDVvbXGqabNaxXWe5Z8YfG/gffWcEOW6V0rjW8nudKtY5ppJFSQhQ7EhRy9BWLtrUGQsQCo8KwKvBvzQBzXM4jQinnMTTfZNpw/fkDNjc7jI/Et0+VTLbh7UkDYsLs5/wLfdXbuPNd1RNYhto7mSGK2s7ZFVDjP4pSSfn+qsxJq9/K2ZLudj585rRoMIbw2yjS4utiD0fa5oeXb9yxds+paGBeT2c0Kx4USzQsFyegORjwp5uLby7/HO8BZ9yeWtHxZPPdcB3EUsjshvYmBY53w1crM7Q+zscbb1dU4g+ldleAQOaErZpsPl4EbzltdbEcR3PXnh/RFRLrirUJO9gEsRjYFSAgzgjesv68+cBVzVlpcRlnRpfFsn4VUzFzVERxCxvug/xSpk7IefMqddcIa/KV59IvVPKCoaPGQd871Ih4T4kFpHCtleIB0AOB9tdU43uLltb7p5cJDBEkaqcAKUB+vrWceWQj+Ef9I0TTYcHjjE6uHzW/HgEBbnLnaju+ywWp8La7plm95eQXEcKEAs0nTPTxp7SL/1m1VZZVMqkjGfaIHia0PE0kj8P3almIyh3P8AOrC6Kp/CJ/oNQ4c6hrGRs1DtCseup20VS1kZJBA38T9lr4Wyi7+FOggVHttoVzTtdgz2QjWO0CczQyPOkUM09lPMjLdai3JyBvvT5O9RZjnJNVS+yh5nGyftTmFR5VMgA5xkgbiq6xJK71NWnh7UYV1O7QFZ7tEA/Cltv/Ef8Rqp0s/2Jt+easu0Ek6la/4j/iNUtm5is2YDJBJry+olEeLSnpf5BZOJO/56Qq39YjXZ5EU+ROKDzxMjHvYzsfyh5VO4F7PeIe0Se7TRbI3slpGry5kVMKTgfSIzvWvPo69oCjP7nj/rMX/NWgzEczbktHcTYodr3OGyxfBHsaxZHG4uIv64r31qF5z3LhTgAkV5C0HsQ480zWLeabQGWJZo2J9YiOAGBJ+lXqi9v4xPKVxu5+2sXEQx7ImtINs23uUms7IHS6d1WUS6BqsbYw1lP/u2r5/atGMnG9e7NU1AzaVewQrzSy20saL0yxRgB8yK8rr2E8fX+GTh9znzuIh/xU1FG0QSMeQL2396Z8ZykLkwDLnan1mVmx0+O1dbX0a+0VxkcOn67qH/AJq5txPw3e8M6td6VqUHcXlpIY5U5g3Kw8MjY0P6vw2kxvBtyBQuUt2UdHwgIP1itx2dcRJp0l819cu6FIwivJ03OcZrnNvPgrFy+J3q2sM4k+qtDC6sPkaR7x7kdQ1LopWyN5fZdv1iOz1LRJru2vWdWAZWMmY+vTasRIQrAEqceRzVLwrq10kh0lSXiuGwQSduXJGB0rSvpLZJZ1XPhXc4bMJIswPP7Lq4pzWRiRg12Ko2klt37znklGT7Gdqs7ZJJEDBG393Smksnd2BwvIep8a02haNfahav6pA03K5yV6Dai2kRAucdEqSndm7ZsFedm1i34WdnRgPVjuR7xW017WtH03TtQtLjVbC2uBbSjupblFfJQ49knO9R+ENBubK6wzlnaLl5MdDXm/ta4hseJeN77UNPSYQsEiPfIFYui8p2ydsiuD9IcRDZMzdbqnFqsUzbM1ushFHJO6RRI0kjbKqjJJ9wro/AfDdxpiPqFw4VriIKsOCGQc2faz47VmuBtHuL3V472NkWOzdWfJ3Oc7AVv9U1N9MtHnWB7hlIxGvU71X6L4W3J69Nfs3t9/mELgFCyOP1+a/Zvb79/MLK8Sa0bqC7h7+PADKEVxvv9tYgBnbcfdUy4jZ7iaSRORmdmKnqMmkcoHQUFi9TJXSh7xYN0t71zlXUPqJC9+6Ssar0FDmXH0h86blm5CAuCfGo4yze81jS1LWHKwIe9ksl5Bg5PwFBYnZgOUjPjipNvEYyTnJIqQNzilFRmQZnnVOGXUCWFo2wASPPFKW6nhUIGKgdBip3LTUtssrcxYjbHSrXUT47uhdYpywjZOafdmRX72QZyMZIFTTGsibHmB8jVJPCISuCTnzp221GW3dMHmRfyaJpcSEX5NSNue+6sbLbsuVibTyVqaaDlIKk/CrK2nNzCkuMcwzjyplrZkHNkEVtyUbHND4xcHVXGMEXCiCTBw1PLuNqTJBzEnP1UiJ2jYq3TwoduZjrO2URcHVPEe6i5d6cDAjIodaIygqdk3j3Uh4xkPy5ZdxT1A1F0dwmypMErSk868vx8afwD0IqNNsmR4b0q3fOfI1OKSxyO1Th1tCnjtQo+WhRBCmmZhuPhUaRMDapj+FR5fomgKhl7lVPCiMN6bqRimOlZErbIZySDvWq4IuoraW7MksceVTHOwGdzWUJ3pSycqtsOlX4dWClnbKRe32srqSpNPKJRuFve0e2e80+we1iecc7Me6UtgFRvtXOUwsik+ddj09ebT7fBI/ELt/k1x10JOfeaL9LKQRzsqQdX8ulgFtekUV5GVN9XjbwAT4ZSPpClQRxGTLn8Wo5mGcE/Co6j2aJgcZrCE1rOLbrnL6qebm3jXFtzICctzkNnyqIwiyWyCevWm1TKFiceXvo1iyuScVJ9Q+WwyjuTucTuj5UJznJ+NHhPOiEeOtH3YqDWkcgoJQ5cfSFE6hh1386AiyM42oFRjFTNyLEJ0yQVOKUr4U770ZGDvSeUZz0oUNLTomSt2GRSQcHelcx8xRHlO4PWpP11CSJvMeNJpRXAznNA7VS4dUkmhQoxVaSUmBmg58KSOtLdQQWq0XLCAnVjw4qvqMCuqspkGQRkGmVJl1lS/tE3ABz/Sp3Qn5LyFgBlST8hRajENM1ON42Mh9mb2hjfOcVrFlqKOTkHa/D90Y3/RaeQP2Wq1RUF2/KoX3AVWSjYA708bxr9I7llVGlTmKr0G5qJfXC2cSuylgW5cCuxqahjmGXZu/mtCoe1znPGybn2ikxsQp+ys+XZyOYk/GrO81CMohiVWLqQebqvhVYpwQfKuLxaoZJI1rDoP2Kyp3AnROmPfYUhjg4oxM3kKk21mLtHcsVIOAAPdQjGcZ2SHdVBuY2Ch9eg3ocpqf+DAq5MjZ+FMy26oxXmJp30UjRd4SLCN1GoUKUFzQgF1BJoYpfL76IrtmnLCkk4oUMUfhULJIulChQpkkQo6KhSSR0KKjpJIClxjekU7CuTmroBdySsdNi5pxkDFel9P1o8Ndj3CzWdlpzXF0JQ0ksCM4AdiDuN/rrzrpkKmTOSMV1u+uC3DnDVqUIEFhkNn6XM7HpXomGYcHMjzi4vf4H6roMEphJML7DVPjjXW+fn9YgB8hbRgfILTjcc68wIF3GmRj2IUH7KocUPCup9Ug/QPILthCwbBXDcX64/W/fb+aKYOv6s4wdQucf06rgKOpCniGzR5KwNAVlFxFrMMfdx6reInXAlIpP7odXXONUvct1/HNvVfmh1pcGP9I8k2QdFo+HOJLyG5uhc3V1Mj20gCtIWAblODvXDNRUE58zXYNHDK10yqCBbSc2fAcprkOoYrBxmFmR1h0XK+kjAMhHf9FSsPbNT9OiDyioT/TNWGmnDg++uKw6JrqixXJR+0vUXCnEtxw92B2kFlqQs7xpnaLupuWUjvjnA8sZrG6lxfr2rtG2oave3RiBCd5KTyg9apNMuWbQbGMquBEcHx+kaV1NeiUWHRQ5n2BLnE3sOa9Jwyijiha+1ydb26hSTf3Z/vqff+eaR6zPnPfy/HnNNAYo85rRytHJagCU0rscs7MfMmkF2P5R+dDY0RGPrqVgkQjileKVZY3ZHU5DKcEVRanwsLiK+vzfRc6q05jZG5m33Gemd6ut6MYOQyhlIwVI2I8jVU0LZG2KBrKOOpZlePDxXMjb8sg28a6JwHPJb6NrRikeMk24JRiMjmbbaqzjVbVV094LG3tfYfn7lcc+4wTR8J6qsMN5ZcikXRjIfPQrk4+vNZdLSCNxaNyR8CuUpKf1WvETzsfmFesxfcsWPvOaNJ5IiTG7IcYyDim/dQ61tWXarL8ad5eX0TOC7LAq8x32yaxk8PKSMV1u8SGXSrqJsrI0bYkUAnYZxXL7tMjPjXKY7RtIzgarhcfpDFPxL+1qo1qv4xc+ddL4RjNlo97I8ahb0KsRB39hsk48vurBaLp/r2o2ttzMvfTJHkDJGWAzXU54Law5NPtneSK0BiWRwAX3O+BSwCCw1Cu9HKYvlMp2b8yo2N6MDzoyRvTN3KYLKWdRzMmMKehya6smy7J7gxpcdgqTjeaSV7ESSM4VGChjnAyOla7SJ4YOzzShFY2PevPNzXDQK0pw2R7RrnvEOpNfz26uip3SFfZOc5Oa3+jJF+9/pWSOfv5j9WTWMbOqrEc/ouZpHxz4m97dRb7KO8skztJIxZ2JZifE0WKDEDpUa/1GHTrdZ5xIVZ+Qcgyc4zWuXNYLldO+RsbS55sApWcUKozxXZHpFcH/ACR99J/dbbg7W0x+sVXxmdUGcXox/wBQfFXnSgc0LCQajo41ONSid+YCrEE5ABzt8aPpVjXteLtRkcjJWh7DcFMXmm2+o2E9uyW6XEhTknkXdADk71hta0ltLvZbZ3jkaMgFk6HbO1dAB3G1Y7i4f25uvcw/qis7EKdhYXEarnfSGmjEQmA7VwPmUxoWFubbBwe9Tf8AyhXTuK5zc8RX0vrLXPNJ/Cn8rb/6Fcr0ebkubc8oOJEO/wARXRblxLcSuAF5nJwPDelQtDmtcOlvl9kvRsAtefD6poUoUQzTWr3I0/RTdxcpuBOqYYZHKQf15FaDnZQulmmbDGZH7BP4pBGKyj8WX4BwtuP8j/rTJ4s1I/lQj4RiqHVLG6FY7vSKkHXy/dbDm3obGsaeJtTb+NQfCMU23EeqA/3Tj4IKiaxg1VR9I6Xo7yH3V3xkhTTrY46ynHyrK2r4LUWq8RXt/AkE920qo2Qu2xxTFhI0nNmsZ9aySqAYuaxGtZU1JkjBtYb+C7HxHfC+vopQVP8AY0Ckqc7iNRVTgE5wKteIY4kmsliRFxZQc3KMZYoCSaq8VuUtuC221l38DRkbboFE4liJ4UuW5zhbmIcufPm3rld4pDE11Lia6C8MzW/drl7iNufxwM7VzS4XmcjrXO49FnFiuJ9JR/zXuH1Ue0hy4JFdn0awi4e4QihudN086jLcl2aaJZJFQqCvXp/1rB8J8IvrUneSieK0AcNOiZAZVzy5O2elbSaQzSlyD0A3OTgAD9lWYLhuRt3DvRPo/h5N55Bpy+qVd3tzqFw9xdTNNK+OZm6nAwP1U0Fz1o8DFAHFdMGgCwXXBoCa17lHCWpR92pOY25/Ee0Nq5jDdmwuDMqK5wVwT510rXZ2bhrULcBQrcjFsb7MNq5dd+xnOetctjkj4XiVuhGt1xXpLdtS1w/SPmVbxcSz8gAt4sf0jSxxJP8A+Hi+ZqhinjVACGozdR+TVmMx2osC6b5fZYgq5be0r7900/8A4eL5mh+6Wf8A8PD8zVB60n5rUfrSfmtT/wDEM/8Ad/nkpeuS/qV6eJJz/EQ/M022vTvnMUX66pTeIPyWovXF/MPzqDvSCU7y/wA8lE1Uh3cr2DX5rcYWKI+85pf7pronaGD5Gs/64PzD86P11R/Fn50m+kElrCU2/ncnFZINA5Sde1WfVbqOSZIwUj5RyAjxqJDcMqdxyryt186amumd8hQB0FIhYmUE+dc++tz1Jka65cdfBDySue8vcbkr0N6M91LaXmvGFymbaHOP6ZrvMepz5PPM+D9teYexrjrTeDZdVkv4Lqf1mGONBBy7EMSc5NdHl7ddC5T3enalnyPJ99dBNh0s7s7GXGmvuWlEAWBdSlucnJc5+NQ5Z2JJLMc++uWnt20oHfS78f8AmJXR1lLIG8GAPzGaofRPgtxBa6tAHJSO9LdTmn4LuSFgUdlx76rXlI3Armg7d9POf7T3eM4/h0+6pR0b5rhgunNhuu2W+uXZbkFy45tuteTu2kqeP+IQ3tMb1jk/AV0yHt602I834Euyf8en3VxXtE4jj4m4q1PVYYGt1u5zKImbmKbDYkfCp+qSUjXvkZYEW5dQh53BrdFkAAtxttvUuK4kQNys31Gq8k9/9dTIG5c7da5yjlOY5dNSs+MkFW+kXpsr6C65mHIScp16GtTBxNHcNh5Z0H50h2rFwjMqipajAIrscNrJIm2G11r0lbLA2zDovQVpp2h38Q9WtYHIReYmPBO3XetHoFjBYWrJbW8cQLknkGMmuN2XG93bRxgWkBwirksd8Cry/wC12+4a4Vs72DT7K4kuLyWFkldsIFVSDsc75ojFM0EBlceyummrYRGXZtvFNdsXaglhGmmcOaxf2Or2d4VuTAGiIUKRjn8d8VxPR1F7rdmLjE3e3C84ffny2+fjS+KNel4n16+1iaCK3kvJTK0cWeVTgdM7+Fb3R+CNO0yFL8CW5mVFmjMmwQ8udgOu9cdQ0s2J1WZoGVpF79L/ALLnKanmxKpMjbZW2vfp/ArmS30nh62knSGG1VjuIk3c+AwOtc61bV799Wu5Yry5igMhKJzFcDw28KteI+JLhZbVvV4WKZbDZxnastqF9Je3E11IFDyNzFR0FdNjdbHEBBES3Kb2Fxy7vHZF43ibJfyYDZrTsBbl/NEzNOzyNLIxJJySdyTTEkxLewxxiieYyKRgU2BXCz1LnHslcwTdKVGkJwMnrUuGBQg5lHMKCIIxgb++pUEQdAxJG/SjaOkF9dSrGMuhBES30cjFSFhywAjG5xUizgWR2GSMCrO20gSBZDIw9rpgV0UFES0WCNjgJCq/UpM/wI/VRGwcdYR9WK0y6bHneR/kKlrw/byKpNzKMjOMCjvUWc0T6qFiZrNNg8S58Miol1YjuG7qEc2RjlG9bLWNDt7VYiJ5G5ieoG1VFxZpHEzq7EjGxxQVThrHBwt79FTJS7rLrdXMA7sTSJy7YB6VdpqdqzcpdSMfGosmjRyOz9465OcYFQGT1F1dTz5yMEdKxYJavDyc/sm2+ug9/eg2l8W+y0AjjuU72LHKdgAMdKZltxgc8fwzUKz1t17uAQx8pfBOTnc1fPbpLsJM48q6KkdDXxkxakWvy196LZllbdu6qRGY1x1A8aUCDjfepMsSqzJze6ojw/jAATjHXyqqWExGwCgWlqXRHrg0Y9obU1K7RsMrkEbnyqLyGi6iTZB15gQehpqDMcoUt7OdqkZBQEdDUQMFkx4g0LKQ1zXKDtCCrLNJO9FGSyhiMZozWje4ur0hwaalUch2FOuenhTcgJQ43oWUaFVuUVjg0wR5VI69etMGsiZqGcE0etDFGw3NFQJ3VS1PDGrTSX1lbyXcz5bkKMxIIwdqlce6dp+n6XbNZ2NvbyNPyl41wSOU7VmNGumtdUtJUUEpKDg9K397psPFlmkVxI8Ihk5gYgDk4x4119MH4lhskYaC8aD4cyunw4+tUclPu/8Apv7uZ22XLlYlsUt1OOpxT2pWq6fqVzbIxdYZWQFupANIQ86E9D0rio4yC6J51F/guZewtcWncJEQJBHUeVOBdhRxry5pRHjRccdmhMBok4qZLpUsOmNfSqyBmURjbDAg71K4b0pNYvJI5GZVjUP7IznfofdWi42gFto0S8uOaYADGPA1u0mFtkpJKqTYA28f2WjBQl1O+odsBp8lO0vRrJ9KtGks7d37pObKDJOKy3GGkLpt6kkSxRxTjKxpsVx128q3unRr6rGAQuFAxjyArK9olusdxZSd8XMkRyuPo4b9ua6DHKaIUFgBcW1+C6HFaNraHPlGltfgsUetEQKUF5iat4eH+90G41VpyjQty90U+luPH664GGklnvwxewJPgFyEUL5SQwbAn3BUpUUQTenPHGaAFC8Jt1UmTnpmjwaKjzmhEkmhQoVFJGOtLc5Q4pvNOIQQVNWx/p6pwnLSUxMGVirKc5HhU/XR3k8U43jMaqD76qgeQsB16VZagO8so2zuoU4+qtGB+ekkiPKx8v2V7HXjc1TbbUrOGygR5fbWPBUA9cmqq/1GS9wCFVFOQo++oeaPFQnxSaoiEWzQLac/FM+oc4ZeSKhSsVbW+k28kEchaTLLk4IoemopKhxbHuFWyMv0CRp9o8aFpEUhwCud6mBeXYAAe6nCqrGqDOFAA+qk5JIABJrrIaZsDBGOSOawNFkhkON+lNNCCSQoJNWNxYyR2SXPK3IzcmffULBqVRBlNnBO+MtNnBVDW0nNggDHvpDIYzymrZ7XmBZcljvioUsI5zzZBGxFc9PQ8PUIN0VlGoGnHj5QMZNNZ5thQTgWmxVZFkqUxezyHqBkY6GkEiknyxRHbY1Q59zsmJR0VChVaZHRUKFJJChQoUkkdSIBnf31HFSrcbD40ZRtu9ON1f6ZE2Sdth4muo6jJH6lpNsjhnt7GOOQjoG64/XWX7P7Tv8A8ISepLdLFEpbmTmCb9fdV5d3BuLh5OVUztyqMAYGK9bw6IBje5dxgdGGRiovvcW96QKK8lFjp8l60ZkCMqcoOMlvfQVqjcRyGPhidxv+Pj2+daEz8jcxW1VSmOB8jTYgEqsfi5V6WX/qf9Kb/dgT/eS7f4Q/dWRuNTZGKiNdvEml2tw1ywAVRnbasT8XY6Thsdr4Lijj1WTYP+A+y6tfWfqUkcZlV2aJJDy59nmUHH1ZpgCp/EED2+pvG6spWOMYIxtyCoAY4rZhcXRhx6LvYzdoJU2wmMMN8QoINrIDk9PZNcg1A7D4V1bmItLsg4xbv9hrk1+dl+FYmOaRErlfSjQx+B+YVW/0zVjpv0h8arm+masdOH21xWFa1AXIR+0upaegXRtPw4YmDJA8MsadG1N2SldLsQdv7HQ04DXqMXsheq02kMY/2j5KXp4ja7iWQAqWGQfKsPqWu38eoXUcV04RZXVQANgCcVtLPHrEf9IVyvX2b1+4YEj8a/Q/zjWdiVSadnECxPSOeSGON0biLk7G3RWx13Uz1u5fqxWj4P1uW9uprTUJRNCtvI6c65IfbGCK5ks7k7s3zradn0Ya7nYA59Xcnx8RWZQYmaqTINPesHCq2eSrja55sTrqVrOtDFF0ow1dQu+VDxvnu7AfzH+0VR6BexWc4MpP0wQAM5FXnG7DlsfdE/21i1l5WyDuN6wqqo4FQ164XGJjFiLnt3FvkF1O1MV9ZNe27FokkETFhjDEZx8qIkVT8JXBudJnVCcmcZXPjy1aHIOCCD762IJBI3MDcLraOodNA2V3NKOTsMfXWH4l0yXTr9o5pYZXkAl5ochdydt63Ctg1U8W6fBNpXr55jcJKkIPNtyEHbHxobEIM8az8bpuNTl43br7uahdnujTX2rpfiOBrSwdJJzK/KAPd5nYn6q0sr88jMNwSTVfw9o1xZcNWt86YivZJHVv6J5QD8iRUwHFNh0QbHmvup4LBwqYHm7VKqPxBNFYaa0cr4muEDRqATkZ8T4dKfBJO1UPHsrLLZcxOVgIx5e0avq5eEzMFfilQ6Kle5vh56LJXlyDPkt06nzNa/SeKNOh0KytnS4aWLn5yo23Ynbeuf3Entdc70uO9ljQKjDl+FcZT4zknc5+y4WjxCSlkMke5Fuv82XSG4v0rKctncDH0txv+uqri7iiz1LToYLS1eFklDEsRg7EeFY9ryZ1ADYPmBU7StF1XXO89TtLm7EWC/dJzcuemcUbLjHHaY4wbnmj5scq6lhh5O6AKIL2Ufm/KlreTNt7OfhV2nBGukjOkXYJ2HMmM/Optp2fcRSE8mkzZG5yVH7aHihqiRmeQEA2jqD/AEHyKuOEo5Dw2ZGkYg3bez4Z5F8Ksifrp+PTI9A06LSkJkuFcTTyc4K87KPZUDwHTPjTLDB3FdbSNtGF6Dh8To6ZjHbgIKdwT51juMJlfWbwg/lf8IrXNIkSlmICjck+FYXie6jn1i6aM+yzDB89hQ2JyhkWqy/SJ4FMG31zD5FN6Pg3Vvncd4gPzFdO1UwtqVw0GBEXPLgVzHRji5gx/Kp9oro2oB47ydGGCHIx9dRwwDhtPcqvRrSOQ94+qIOMVWcUzD9z5QHDG4Q/Vg1OXJFSLa00a9Dwa2l21v8ATX1Zwrc46ZyOm5o6cdg2C3K5jpaZ8bdyFyiRbhmOXbHxpBim8Xb511N9E4QWU93pmovHjYveb5+paa/A3DWNtIl+u6asE4U55ub+f7rjh6PVJ3I8/wBly94pME8z/M1Fcv8AlE/Wa6idF0UOT+CkKZ6GVycfOs1xzaaXBc240uz9WjMWXHMTzNnruazsRwh8UfEvoqKzBZaaIyucNOl7/JZBGBYdattPIAaqjGJBirTTQSG+FZOEPPGAKyIt12ziy3S3n07kPtyadbu495QY/VVKMmtHxqnJe6acbHS7TH+jFZ0sK76hJMDD3L1OkcTC0noFW8TAHQ5DncSx/trH6Xod3rd+tpZRCSZ8lVLBdgMk5O3QVsuJlH7n5myP4WMfbVbwXpt29y2oiF/VVSWIyjoGKGhqyMSPsVzWLwesYgyPqBfwuVprGwGgcK2ljLLi/a5llnhWQMFUgBdxtuBUcsCaIEGnI4HndY4lLu5wqjqTWhDEImZbro6eFsEYjbsE3REjBzUi7s7ixZUuImidl5grdcVFdsrVzSDqFY46JGqW7S8PajIi8yogLb9BkVy3UAd/jXUbxsaJqPvhx+uuY34yD8a5j0jb+WVx3pILyMJ/T9SqznK7YoFxRN9I0mvM3SuBsuXTgcUOce+m6FNxnJJRbJos0VCqy8lJHzGhzUVClnKSFKjIU9aTQp2uLTmSV1pmpQ2ofvQ55sY5RmrAcQWnik3yFZbvDQ5yfGulpvSWeBgjZaw7lc2oe0WC1UN9a38wASQFemQK9XmZkCDoAi4/RFePNGJE4PuFeuptSsDygX1n9BP49PzR762TVOqo2yPGuv0WhSvL2kndPszSA4ONj9leS57mG0kZW7w5J3A99ep4tWsefu/X7Poes6+XxryNqkvNK39I/bVMtQ6lYXjdNWOLQCpo1q3XosvyFVt3epPcSSDIDHIBqGWNJ6mufq8cqKhoY46eCznSOcLFPJEzuJMbZqTEv0gBmm4sBARnen4Qd6jSxAWtzTsCkWrMrqXACjxzU0MG6HNRY03Ap9ARge+ujpSWtylFsuAnZb6KxKGZ3HNnGATVJq94l7d95GzFOUDcY3pF9fPdsqMigoSBy53rX9n/AAdDqqtqd/GJLZWaNIz+W3jkeQz86yp6mXFJfUoLZL3vryGt/wDCsgilrZRTxc/h3lSODeDLiyujeapaWssDwjugxD4YkHOPhVnxFxXZQpcWUV2yzBHjKIp9lugGatOL9Vm4f0uOe0jjLd6sYDjIAwfurlV9ctdzz3cgUSSsZCFGwJ8q3JJI8Kh9Xpfa3JPT4LoMQqmYZF6lS7nUk9/eLJmWcnBldifMkmocrcztgnBNCSRpMZxt5UmuGqaoym3JcaTdFin4IGdecDO+OtM1YWI/Ef5RpUMTZJbOTsFzZGntsFHWrG1tmaNRgYyfGoNogZixzt0q5skHcg9Tk10+HRZjdyMp2ZjqrnRdJnmkflhBHIDuQPGtLp2gX8s8TLbryBxklx50OBrX12QoxP8ABjPwzXQ4LWGIIka4VTW4+QRdlq1mgNFgqn8CSsf7liH6NK/czdvgrbpj+ktaTkwakxfRXFA+sOGybMskeGLrA7y0iYD84qaia5wtPNo86QWMAlPLjHKPEVvGGQKbltlnjKNnlPXFMKp17lNmXDbvha8tZAs1tGjEZA5xWQltWckGNDufKvRWocL6deHvJDPzgYHK+P2VyTWNBWFJJCgjkXJBB2b3UQ7LUBVyRB4XN9Ss5bebvCoVTgAg+OKf0bUktJJTcSPhlAHU75qw1K3Wc8j5wMHaqe+tI7dUKFiWJzmuaqKeWhnNTBsNde/T6rLc10T87eS1MLRXUSzRHmVhkEjFImsmfdQAfjVJpmrTwNBbYjMfNjdd8ZrV2pW5QsNsHHWuvw6phxGL/dz8ba2WjA5s4tzVGymNyhABBwRSWIxjwNTLy35pJHGeYE7VCwR1GKFmjMbi3kqHtLTZR5QyL7P0QMAYqLyMpDt0J61Y/God2vLIGJ9g+HlWRVQ2GdDSN0up0TAxJ8KJjUa2OYgfM1I8KMjkztBVgdcBJcgEZNA+0KRMdxQjbK4qov7RamvrZMyAK5xTBGRUicYbIFMVnzjWypcNU2wwTTbDFOMPapDigHtVRS7R1juYpGOFVgSa6DwffQ3Qukjk5mXlbGD03rnOcGtNwdcS25vWiAZiEGD8TXQejdW9lQKdo0df4D9lp4NU8GpaTtr8iofFOg6ha3l7qEkIFs8xKvzA5ydtqo4HXHLncmt/xjI8vDJYjBaSMkfOueRA96vxrJx6kZRV+SLZ2uveSmxenZDUfl3s4X177qYgzmlcp5TQjGQRSwPZNTjZcLOspWi6XqN+8p09uUpgMRJydelSNZ0rWrNLeO+leRZXxGpm5xzfs61f9nvq8K3ks5XHMoAJxnY09xfNDc32jRJIrEzHOD03Wuijw6P1ESEm579NXWW6zDY/UBPmOY8r6e1bZVSWfGSKsavPhTtiRf8A6NUOry30t1J+EZGe5T8W3MRkY8Nq7AqxtCJFPtFiMe7zrj+uyd9qN3IPypmI+dV43RtpoQWvcb8ibq3HKA0kTPzHHMdibjRHoVzp1veB9RQvEPAAnf31pbTiPRe+1JJ5FNtPMCiGMkMvKB0+Ipnh3QLO44Xu7+6tUkZY5WR2zkYG366l6Dwxp13o9nPNZh5JFyzb77mo4XBXNbGyIssRm1B2OljbxTYdTVTAwRhuoLtb7ezr5rH6q9vNqVxJaKq27Oe7Cryjl+HhUUDGak6ikcd9cJEvKiyMqjyANRicAn3VzFRpK6+9ztt7lz0pJe4nqdkx0owjEZA2osbVc6fYwy2YdgSzZ8elCUlIah+UdLpoozIbBUtCjoAZoK11WgBSl2INBOpp0ID4VfHHexCcBNyLvtUmJgUC5zgbimynuokymc+NFRgxvJ6qQ0KakXlc586KlTZJyaQelBOGVxsoc1IsVZ7uLClgHGdtsVomHl4Uu1torW2RIgQpAY5OckjrSZGAO+1dxQYeaSIh5uTqtSKHht1SUheU4Vc1aaXp0kx5Am+ck+AFZuS/mWRghAAOBirLSuI7yzUJ7DxlstzDJHwomir6Zstngq6mmhEg4l7LY3lhGdN9Ti8BkE+dYu8sp7ZmBilwOpKHatjBqcVzpxuUKlgQvuqn1XiZ7EGOHkM592ye/wCNbmJw074uM91gOa2sSbTPa15dbTS2uiySSOjhh1HnTM7kyO569adluGlleV2y7ksTjqTUOaQl2XwNedVMga3dcm92iRLIXxtjFN5pfLmjWMeNZLmue66o3TeD1oyC27HrS2IT6OPrpDNmmc0N0SQ8TRUKFVEpkVDNHRUySFHQoCkkgOtT7MbfXUNFyatNOgDjcHrW1hEJfKAFNguV0vgSRrfhPXHDFTLNbRbHHTmajZ/rq1gh03Ruz3SYLOKQ3eqMbu6ldsj2Cyqqjw/bVMXr1XDGnhEkW1+Wn0Xd4cDHTNaf5dOhjVXxe+NEhUeM+T8jVir4PSsxxVqUzyvZNyd1E4ZQF3zjz+upYi4MgJKWJ1LY6R4d/VoPn9FkbpiZDVjooPrEON8uu311VTNzSGrjh/JvbYf4Rftrz/DHiStv3rhYRmkA711fWL+fUtSnurjPeSNuM/RA2AqHmiuLp5ZnZsZJ8qa74/8A0K9JjjytDQF6iZWt0CksR+D78+Pq7/ZXKNQ/J+FdTe4K6TfqAPahb7K5XqB+j8K530gNoj/Oi5f0ndfhEdD81XH6ZqxsOlV35Rqxsfo1x2Ef665Nm661MndWtioP96Q/1BTGaElzLNFbpIV/FwxoMDGwUCm+civUoWkMAK9SY6zGg8gFLsw7XMQUZJYbVzzUdMuri6mItZ2BkbpG3mfdW6S4eN1dG5WU5B8jUubiDUpSC97LkHOxxQ1ZRmcZeSz8SpG1jWtc61r8r72+y5lHw3eHc2V19ULfdXRuzzhXUdM0nU9dkkt7WE20lskMr8s0hyuSEO+Pf4+FE+q3kjMWuZSW6+1TD3DyHLuzHzJzQkODNi1ZYICmweGCRsgeTbu/dPE++iDjNMd576IsxJwa2ci3OOqbjchRZ/4tj+usLI/LJmthxtL7VopYEiI9PjWKkf2zXAekU2Wa3RcNjjr1j7d3yCtbF8AkMRv4GunXi926ENzhkUg/UK5LZThMqOrHbNbbh/V57qWK0kZOUKxHs77DO1bGAVbXMDSUZgNayImJ27rW8dfurwsfCkSxLdwSW8r8iyKRzEZ5T54oFyd6LmrqC0EWXUOcHAg7FWN1fSXISMys8UShEHQAAY2HhUU0wHKnanY5CSAQN6iIw0WGysZILZUm6z6jdsGClIXYb4PTwrnGoXjTcvM7MQNyTmtNxPqs0XrFmjAL0O2+KxFy5JArkfSSt4f5bTyXI49WNlkDGf06HxumZG5iaRmiPWgK85dIS6651S7bBdQfGugcC6readBfx2kzQiZUV+XqQM+Nc9ttpFrb8HkmO7YLkDlz7utdv6M5XPAcP5Za2Df/AMltu/5FaY3M8+0s0jgbgMxODRh2AJDt86YBY7qPjtSsS4JCH5V3paBsu5a82S+9NLSRQCzE4AJPj0GahmVvdQEx3B8Rg/CpGM20UPWgFldd178KQoph7kIxbIbOdqzZkLyjyBrX69oloLEPY20xl7wLgMXOMeVZSW0mtrgJLG8bDqHXBFcFjjJxMM400XC4iycS3mNzpqNvkFb6KD6zD/jU+0V0S+kBvJySSS53rnejArcRH/Cr9orc3UjG5lz+ea6bB23hHgugwGTJC894+qeDjwo+baoqysBgYp31i3t4WnvJRFHkKDjq3lWq4ZRcrfbUtO5t8k4TSebNQH4g0xf76+SH7qaPEumDpLI3wjNVcaMbuCqdX04/6jfMKzYnYeFZbjUL6xb8v8kftqxfiewH0ROx/oY/bWd4m1WHUJYjEsi8iYPPjfeszFamP1Z1isfF66CSncxjwTp81nzjnq009vYaqjOXFWtjgI1cZhDr1Gi4+M6rquqTO80OWYgW8IGT09gVFDZpNzcvJ3TNy57mMbDyUUwZ28/1V6RDHaNo7l6Zx2t0Tl5o9zrVhNBbNEChWRzK4QBR7z7yKnXjwLHawwEBIbSGJuUYBdUAY+/fO/jVat1KgYK2AwwR50nvm91LgdvOUODGJTML3IspPeKPOous3q2Wmu7PyGVSse+5NKVy21ZniTUJbm/NmxTuoHyihdxlR41VWS8Flxz0VGIV3Bp3HmdB7/2Wl02Z5dC09ndmYo2SxyfpGllsjAqLprkaLYrgYCNj9I06ZDV8DTwxdTikAiYP9rfkFIYA6feBtx3LdfhXMr36NdILF7O6z0ELfZXN7zdflXNekotGsT0gdmbGe4/NVb9TSaW/0jSK8tlHaK5ZChQoVWnQofCjo+WpBt0kVFS8UOUVLIUkihTnIKSU32pzGQlZIo8UYXOaPl9kneoZDukpdpL3ZHUAjGa0kdzAFGZoRt+cKyCPybnNH3x8hXSYZj5pGWte6ujlyclrjd2wz+Ph6fnCsnOxJ+uk96T4CkgAnc4qrF8bNeGtAtb9k0kpfbRGlSMDPQU2saipAVc77VnU8ZAsVFoRoMgYHWpcQwMeIFNpGqgeNPoVUFm2AGTW5TRZdSiGCydj2O+BtneqW6vXuyvMqjlyPZ8akXepMXKwMOTGCcfSz9lXfA3DdrrXrT6hb3BiVR3TqxRSc77+J6ULUSvrpW0dMd736G2v0V0ML6uVsEW58k52daVqMmsRanbqq2sLGOV2weYFd1A8fD4Vu+IeIrPTLae1nkWOV4WIRRgnmBAO3vpjWOJV4ZtLVVt+8hP4oDIyOVRj41zniPX5tb1BruRI4zyBQqjoBnFb4MOD0xiDsz+fvHyXRzVEWE05pYnZpOdxpqOXw5qA8xRVMryN4YLE1BY5YnJwTS5JnlGGxtvsKbrh6qoEhs3YLjSboUKFChEyFT9PyY2AGSW/ZUAVd6XEi2yuAeYk5zWnhMJknsOishbdyk2lq0jLGAodz0HSt1wvw5dXMcXd90RGSWy2Nqo7K2t0VJQuHK9S3Suk9n02n3MDWaLIt2qlpGP0WGfD5128cQgZcLbgiDBdC04bvw+VEQK7jD1odL1CXvpILtESWNwuVGASavdO02AyMGBwF8GxRXuhRPKsqhgEIbIO+3nQr6hrjlcrS4bFK5s7kUYGTuKJcKcN49M0r4b0KoJxdgQKJ/omgHIBUeNR7m4USx2oBMkoJAA2wPM0wBJTJLK0h9k7edZ7QuDfX1F1qCh7Z8lU5sEkHxHlWntrKSSZFkVwgUk8pxv4Vc+rEAKNx4Y8KmZzGMrTunz20C51xhwI2o3U15aRwQ5Cqu4CnAxuMbVyjiTQZLS4eC6KxSQEgkYx0rvXGGu2mjaXcxNEWuIRzE8/0tsgY8K828b8Vz6xqLygRAF84Ue4CrTVCOnL5x2eSrme1sd3qmu17xGVDuRjPTxqrkEkLFCxyPI0p7mWRy5cgnyOBTbMztliSfM1yNZUsmOZoIP0WK9+Y3C22lahb3dtDbRSBphEMrg+A3o9QgbnGwzy7VktP1OfTJu+gKc2CvtLkYNafSdQfV4GluwveK/KvIOUYxXcYVjMdbGKaT2/hYe/da0FS2dmR3tfBQ51ZBuPGo7pzKRtkjxq5vLSNocrzA565qsMfISGG4qVXSFjtdlVLGWmxUa2IRe7YBSP104hAZkz7Wc491E6Kx9qkRhS3OQQwOOvgOlZou2zf5ZUC40RzbEUk/RpU5yRSAcjFVSEZzZMd0T7qajmpDDIqOd6Em3VTkhutNsaW3wxSHPSgJNlUUk9KCFlzgmgaIHFUXsVFdT4alD6RYoQGPcrkEZrnGv4/DV+en9kP0+NaThPiG6N3aWLGIQhSo9nc4G29HxvounWtm+oQJKLiaccxL5XJyTtXX40z17DmTQbR737gNl1FUTWYe18f9G9+4BZSybPPv5VKHUVWwSGMnA61YIT7OcVz2HzZow3mPuucYdLLfcA6ZPeWUohgMjSTHlwOuBvTWv6b/8AjPRbWXEZaPnOBnxbH2VK4H4h0/RdMHPqMVrcF3JycHBqDNrdpLx5DeetwPbxwELIX9nPKdsn3mu1fI3gRR5ha7fnddnnh9Sp4swuXNvqOtytXJpYt7J7vv29lGJTPuNcYlbI8/GuqazxDF+B7uS3ubaSRYW5VVwc526Z99cnkzj4Cs30lqPZaDfQoD0okjL42x9CfO32XRrR5bfs1kVIWJNsTkYOxYkn5VO4cFzDoNmGhJCw82QR03I/VUPWFNl2cIOheGGPb34NW9pILbhqNjty2QP+xWvRHJIB+mMfz4LepY8szRc9mJvzP2XI7hi87uTksxbPxNMvsppxmBbPnTch9k155MQcxC85Jvqms1NjYiMAEjaoVPLK2MbUPSyZCUmmyZpaDxpFOJ0oePdRCPGDmpEMeWAz1puNQc5q10ay9cv7aDu2bvHC4XqfhWxQ0pleGjmVfFGXuDRzQstCvdRkVbePKs6xmQ5CIT4sfAVEns2gmkiZkYoxUlTkHBxtXSLrg+5j0xba1knhV355Q/Nh9umPdWY13Q7XTLZZIrvvpi3KVBG3nsK6WqwQRx5xy31+S2KrB5YGZi3bUm48gFlJYsHFMOhUVOmBz0qNJGWG1chVwC5yrDc1aOC4WW2icZwUHX4Ypm4kU+x41UWd1JHJFG2SoYYBNWs0febjxrpaetNRDpuLXWg2XiN0VfLu7fGm2cqVwTtTsilGIINNNjrmsiUkEoRyvNN1MQ6PeBc8yujKPedqpXd5HLOxZicknxoK/LG4B64yPOmy1XVNa6WNjHH2R9SrJJi9rWnkPqkvIMbZpk5LZNK3O9Fg1jvcXm5QpN0AKNj7JoYom+icU2wSSMFjtSSD404ikUlgSehoZzdLprJNCgRg0KrsmQNChQpkkKMUQFLVC1WMYSdEyXCuTWn4V0ibVrn1aF4o2wXLSnCgAZNZ+3iLOBW64Fsp0uZZu5l7o20uJOQ8ucefSuy9H6U5w5aGHQ8WdrDsStZqT28Wg6LpyTpNcWUTpK0YPJuxIwfHrVQSKTI24ANIz769JghEbMoXYlwb2W7BSFAbpWI4juo59SuZYyeQttkeQxWwZlS3lMkiJlSBzHGdq55dkmMDxOKwfSGUthsFi43MeGyPrr5afVV5OTV7w0M6laDH8av21SiI+Rq/4ZGNUtPMSA1x2Bxu9ZBKwaMXnYO8fNbqSQd43XrSecU0z5Yknxog3vr1MN0XdumJJUqVv7VXxH8k32VzC/6rXTXP9qL8/wCCb7K5pfIWK4HhXKekYJjIH82WJ6RG4i8D81XflVY2X0agmPDe+rCzX2PrrkcIYRPquYaNV0t3xj3AfZTXeZpNw5EjLnpj7KaDe+vWGN7IXocs5zEBPd4M+NAuKKEKzMSdlUsR54FVbcT2nhaTfpCqpZmRmziqn1LI2gyOAvt7vBWfOKHMaqP3TQA/3G5HvcUR4pj8LH5yf9KpdWwjmh/xCm5yfA/ZXUS87YJAHnVdq+q29tbT2yl2lmjHKwxgb+NRTxUwjcLYw5IIyXJxtWSu7gmPIcE5Hj1rMxLF2QxktQVdirGsywG9730P1TdxMW5udixJyc1AZstRvIWO5pHWvLayrMrlypN09BLySAnO3lV5oGorZ6jDPKHaNebZeu4IrPqDUuB2U7A1oYVVPieDyCshlMTw9u4N/JdItdQiv4u+iR0XJXDddqd5xVNwnLdm2aaVYPVcsArfSZsdR7qtOb316zRTceIPXaQVDpIWyO3Pd8fBPBxS47pbRWuXQssYyRUdSWYKOpNQOJ7z1fToo4Z05ncq4RgdseNPVTNijLnKx1SYo3Tfp+azmv6kt7f3c+CO8ckVn5H5jUi6lLM2/jUQ9a8ixesM0hXDzymR5e7c6oYyaUFJ8KXHFzeFaKHg3WjEkv4Kuu7dQytybEEZBoejw6SfVoTxU8kxtG0nwF1n4yUYHHSrfTdZuLFJo4Ll4FkxzAHHNipa8Jaq3SxkHxIH7agaroN5pbRi7gMRkBKgkHIHwrdhpaqjHEaCr/VqqnHFyuaBzsR3bqZ+HrrBH4QlwfJ6S+szMpHr0xzt/CGqTu2U9KUsbnfFWNxipJsR81WaqU6Zj5lb3QrqfUbF5JmQmFhHkdW2zvU07EjrVTwlzjSbjAOO/G/+TVmWPia9Aw6R0kDXOXV07708bnbkfUp2NgrDJOM+FZ3ivB1aYn81Psq+TdhuKoOK/wDvWf3Kv2UNjDfybqjEnXpCP9w+RUTSnAniH+FX7RWzumHrMv8ATNYfSv7oiP8AhF+2tnOxM8mevMahgpLoRdVYS+0Lx3j5FDmqFr83Lo+CpOJ1O3wNSS2PGoWuCSXSuSNWdu+U4UZOMGtGsFoXEIyokJieB0Kycl+mT7D5HhTf4QXP8G3zqS2lXMjZ9Vn/ANGfupacP3j/AEbO4P8A5ZrhpBWud2fkuVEMx2afJQG1Bs7LgeFR55nnbmPlirxeG74/3hcH/INO/uV1Bumnz/o1RNQV0zcrzopiiqHf0HyKzEakODVlalghqXf6Be2EIlns5YkLcoZl2zUe3GFbahqOifTThrlU6F8TsrwQe9dAkmUrCcEfik+wU33o8qKYcscG+/dJ9lMlq9PiaMgXZSzODipAkHlTiAOwGepxUQNk4zTsTcjqzMFUHJJOABUnCwSjlJKi6/eQ2dpJHFNzyFgoaPpWOSRpLlnYksdySd6Vqd08lxPl+cB25d8jGfCo1mxMjE+VcNX4jx6ljG7BcvXVZqJbkWAW905/7S2OfzG/rGnebNRtNbOi2W/5L/1jTwIrs6XWJp7l0jXnIz/tb/8AiFJRx6pdKc5aF/sNc5vB7HyroUeDBc7/AMS/2Gue3p9j5VzPpPowLNxkksjv3/RVkn0jSKXJ9I0ivLJfaK5tHRUKMVABOjFHQo1GauA5J0aIWzjGwzQ5fOrzh3TjdpfyFCwS2cKOXPMx8j5iqh0IO4IPlR76NzImSn+q/wAFc6BzWNeed/gm6PBI2oHah0NDkKtCgRkUKOkmTRjydsAUXdkeIp7FArnpVRgB1SsmREx8RTiR8uc70sKfAGlqpJwQasjpwCCnDUlE5qfER5sZFJVOXoKdw3ka0YowN1Y1qXEhXGSCKa1PKwpg4y2+PhUiNW5R7JqPq6FYU2O7ePwoqraRSPIHJWOHZKXwtp1vqmt29rdKXik5uYBsHZSetdIfUtI4Ksbaz7u5MbFmQIOc5zkkkkedc54RnjttetpJZUiVebLOcAeya0fHUcsxsOVWfZ+gz5UVgFosPkqImjiA221scvwW5hs3q9DJURAZwbX7jlWVu7tZZpJCXIZ2IyckAmoMzh35hnGPGjl8sbg03XL1dQ97i0rmi4uNyhQoUKCUUKFChSSQxuK1ESpyEsW5tuXHSsvV7pcplthzyczBiNzvit7AZQ2RzCN/p/lE0x7Vkd1dxxRM3tZ6D40jSuJbnSp+8hmmjZfoyRnlb66q7rvOdg3PjPQ0zVdRik3GuzSyY1Dw64XfOBe1rTO/eK79eKmJckgEBs7nrmuy2ZgvreO4guIXhkTnRg2eYYyK8R29zPasXhlaNiMEjyrpHA3aFfW1xYWo1KSKNZUR0lccuM79fCtGlq2VYyvOV/wPh3o+GqEujtCvSM9ssqjmwCOhxnFRZbUx5b2eXNJ0PXV1tZUSa3mEWDmJlOM+eDVjIYGDJJNEp6bsMirjmYcpROoVO86JOsPKeZlLZ8NjT8CGV8KVGBnc1ntfv7dNT0u5imVokdlkKPkAZHXH7a0NhqFgW76G7tSu65aRfvq6RhawO6qRGl1d2aC3i9rBY75NY3iztG0vR9MkPLeLJIQgZEHsnz61I4r43t9FtEc39mzMeRV7xTgYO+xry7rmuOWa3huHkUnm5u85gNzVIEULDNOfAdVTI9sbczla8a8a/hbVrnumuBCyj6R3Y48d6xTyNK5dt2Y5NKlkaeQu5JY9TUm1tDKdlJrEnnmr5bcuQ7llSyumdqogQ0RFaObh+eOzSYxHlfptVRPaGP8AJINSqcIlgF3BNJTvj9oKFTsNxLCQFkdUyCQpxmkMpBoKjMDygn4VmMD2u7O6qBI2WxtdZtb9/V4Fl5sc2XUAYH10ue1Mgd15QeWshDLPZP3kbNGxHLnHhWp02+jm05DLcR96ynILAEn4V3+E4wK0GGp0cNegtt5rWhqBN2X7qBIpGxGDUSVHR+8Q9NyKtmtjKRlGzUN0KOysCCDjeqaumI1OypkjUSSdWXnwcDrSlORkeNCeNVH0fZ8QKCkMobwrMN89nFUa31RkbUwc1IplhvUZQmcEzJ501IdhUg4yRUaUMpOxI8KzZ9BdUuRdaI0StnrR0KDfUKCsNEuEg1S0kcMVSQE4rZarB+6vT2tbIiN4pFdjNsCMHpiuepI0bBlJDA5BHhWw4K1BAl0bq6RSWUL3jgeB866nAauGRjqCbZ99fd+y2cJnu71V/sOvfy6rPa3oc+gyxxXDxO0ilh3ZJxvimYJO8QAZ223rS8dWVxeXlm9tDLOvcnLRKWH0vdWSXvbebunBjYNhlYYINY9bEKCufEwEMvYeQ5oavgbBO5rB2f2UtlbzHzpo79aVzZH0v102+3R6lK++qCchkL4Ul2zuKIgn8qklf52aEfIbWCgU96xI0QjaRyn5pYkfKpia9qccTwrfXHdOndlC2Ry4xjBqsORnek8ze+o+uyM2JHvVjJns1a4j3pzON6SzhhtRc3gc0nbyoZ8t9Aq0DRhqLxocvvqoOI2SQpxOlI60YOKmzQpBSIvGr3h+buNSsnViGVwQQcGs/E25yam2ly9vIsiNyshyp8q38KqWxSNc7a/1RVPJkeHdCvQelam3EGiwxs8gktnCuW358DY1xbVLKVL64UoVPeMdx1HMasND4w1GzcRxXbJHJIpkycY866Fot9w3qndWmsaRbu5YkXJchRnfffb6q7Z0cdRGXxat3sNwutmkjxOFoabOb171xqW3csdvqqNJHyjYV6ZuuAuD7LT/AMKX+iW0dowHK5dwGz0xg1wzi7S9P067cWDCW2aQ93JgjK9cYPl0rAkw8SMdIzYdViVWGvhYZLghZPlw/MOuakw3UsbZZy48jRCJXnRGljQMdi3QfHFXsGgx3VnGtskdxch252Rj9HwGDQFHQzSOdweSBggfITw9/wCclWTqLiJXG2BnFV8iHPka0iaNcQv3UttImNtwcD66qr235ZBhfZx1omuoJMvEcNeanPA8C7hYqtxRHenHUDwpOBisFzLaIMhNkYowKPFKAqDWJrJBXNJIqSkfMpPLkDbNB4eQlWXBqw05IunyndRaWfawfKlFAD0pBXfFUFhbuo2skOvMc034U83WkOo8qHlj5hRKboUZFCh7JkpRnap1pbd4rHyqPbrlgMbV1Hsx1W30bSdYlfTbW7kkMao0yg93sdxkGukwXDzM8aXRdFSmolEYNt/gLrCQWnKwwCa3FjJNFodjGJXVSjezkgfSPhU641u4dDhYF3zlI1U/qFV9xdzXThpXJwMD3CvS6DDhT7LpKaljo8zmvJJFtrcwd79yTvRjOaRz++hz++taynnChcRW93cNHbxWk0gT2i6oSGJHh8Kpf3OanKc+o3H6Na4ahcqAomfHxoG/uCP4Z/nWRUYUKh2aQqmejpZpDI5ztfBZIcLannHqM3yq00ThzUbe+glltWREbJZmAxt8atTdTE5Mr/OiM8h6ux+uow4LHE7M0qEdFSRuDxmJBvuPsnGADHPWk5ApvmJ8aGffWzlRRkCsLaB7yxubaEBpZVKqpOMkiqI8AavJIIjHArnoDMKnpNJG2UcqfMGnDdTs4ZpZCw6EsdqAqqETG7lOVtPUtaJQbt00I6+CxWv6Dd6BqElleqqzABvZYMMEZG9R7IfRH84D9daTjKT1jUWeX237pMlup9ms/pyh5Yx5yL9orjvUhBVgN5rmKyBsNS6NmwJC292f7JcHwpsGlXmRdSZ86Z5zXoDR2Quhmf8AmO8Splt9Gc+ULn9VYNruBDhmII67VtoXIhuiDjED/ZXOLvZ2rk/SWqdTZXMCzsXkPDit/u+imnUbceLn/JofhKDyf5VTljQ5j51wrvSCe/Ly/dYfEKtn1SEqyqsmSMeFVTvknG1FmirOrMSlqbZzsoucXboUtFyaRUqzUF1yPGqKWLiyBqZWGmaBqGpQPNa2c00aNysyjYHGcVPh4W1SQqBaOoJAySMD39a0PDkxt+HZwjFee55Tjy5RSu8YdGOPjXqGG+jsJiDiV0DMOphExzibkXOo6+CemsodMCWdvK0scY2LHO/jTQak858TRoOYgV1scYYwMGwRj3hzuwLDkOilWXtTx/GsJqdyZmYnAPMdhWk1e/uLCSNbeTuvxfMxA3H11iZpi5JJOTvXFelOIhg4IWbidQC0RDcXv77JqVsnFIG5oE5NBeteaOfmfcrEU60GE+o11C/1C4EVoiTyKvqsYIBOPo1zC0PsH4Gui6g39zDyt0+yvTPRNgI16LocHcWRSlpt7PzKZ71vzjn40m9srTV0jN5dzxPFkKEUNkH40jNHkiuxmp2StyOGiM4gIIeLg8jf6FQxw1pOd7m9PwVRTqcP6IDgtfMP6Sj9lPUYoNuEUoPsKsCEbRt8vuVK723t7WO1tInjjT85s5qOz5OaRv50pFLMFA3NaDGNYMrdlN0rnkI0J5h8ao+LZP7bzr/NUfqq51G6bS7ZZO5VnZwAWOw2rI61qLX93JcOqqz4GF6DArm/SKrYyLJfVB4hM1sBhPtXB91j91I0neaH+mv21tNROLuQe+sRozZmh/pp9tbO+ctdynP5Rqz0fdmhBHRTw0/8s/xHyKYJp2C5ltmJicqSMEimceNHXQ2BFiimvc05mmxUr8JXWd5mNJOoXG3459vfUYmiOabI0clY6qmP9Z80+b6cjBmfHxoetTMCO9ffrvUfGaUtLK3ooCaQ7uKb153/AAEAWJBnB3P801k43yWFarX2Y6KFJOFmXA8tjWRiILv8K5DFzlq2hZWKuvK3/tC3d1/EnPWGP+qKZzTt0rf2OAp/gI+nj7IpsIx/JNdZAQYwteoB4hRL7Rqo17U57aea1VhyMgGCPMb1Yar39lpklwoeM86hX6fGsbf30ty5klkZ3Ixk9cVz+P4mIGcNpsVm185iZw9QTr7tVGmmBOPKnrNvaPwqAxyc1N08kvj+bXnVFOZKkXWG3dbvTv8AuSwbPUSf1jTmaTpxhOgWC+sQhl5+ZSwBGWNHI8Makm7gOPAOK9bo5WiBmY8l1uU5GH/a3/8AEKRFvDNn+Sf+qa59en2a3kNzZer3DyXkKERsFXm6nBrn125K9a5n0qmbkABWdipHDjsev0UJ/pGk0bbmiFeXPN3LCQpQFGAKGN6kGpI1xkZzjxxVjZx6UxX1ie7XfcJGpGPjmq4AUoHFGU7xG65aD4q2N+U3Iv4rrfDJ0b8HyCy5zGuyDP0X8ebNZvia14cjvpGnubuO5f2mSBAwyfPPSqjhTVZbS5lhMxEJikk5ScAsF2NU1zdS3UrTTyNLI/0mY5JrrazGI5KNgyC55chbp5roqnFo5KNjOGL39wt0se/5oTCDlHdtKW8Q4Ax8qZoutHXJPdmN7LmyboGioGhUEyUDTqjYVHJp5Wwoq2NwvqpNKWDjanYiufa6UiIBwTjxo2U5IUdKNZcAOVo6qSkasQ6k48qdAqLFM0ahQvj1NSmkCYDDAPjWlA5jhfbqrWkWS1OAKZ1iJpbdCoGFJJ391PpysoI3p9olkTlkQMPI0dJDx4HRX3CsLczSFl0X2hnzrqusQm5jtFU4Cg/YK5ZdYW5kCjlAcgAeG9dF4CmuNbtLiS/ma47mRVTn8BjpWf6JVLIal9M4au//AFujsELXl9Kd32+FyueTDE0g/nN9tMVOuo1W5mwMDvGx8zUJscxrmayMtdqsFwsUVCjoqDUUKFChSSQpcMhjmR1blIYb+VIoU7XFpBHJOFfaraCW1Nyj+y3tfGqPGDVxpdzLd2c1vMxkSNQEHl1qtuou6uGVVKrtt9VbmJNbM1lXGNHDXxuURUAOs9vNFHFz7YpZsnCs3KRjfpUyxjjZ/b6YrdWVhokmgzes5NwF/F4o/D8EFXGTe1kRRUBqLgOAsL6rH8M8aaxwrNNLp97cQNMoRjG2CQDnFdG0/tWe5s4hPb+sXCqDJIZMc5Pj061y/U4LeJ2EagY8j0qRwlayahqEkEcTS4jLcq+4imo5ZKeobSyEOB0/hTU8sjJBEdV13g63n1PS73vrdoYLmR3R/Bg2xx8Kq9W0/UuHrdy1l63bxYAmj8R7x1FdK4T0LOk2lrHbuzJCgKg9Djf9dTtS0aKC1kbkKSKQNydt63BVZXloK1Q4jQFeZ+J9XbUVjTk7mLJ5sb58qzOK7NxRwLGlzLNaacXjJLtgFgSdzjyrk95p8tqvMYm5Scc3UVz2NUMr3esE3B+Flk1sTw7OTe6jQpk71qOHhZxsvfl/pDGKyysyHBFWFpdMm4fYVDBqllPIHEXVVJKInhxF12TVdQ0UcOQxxw4nG5kB+l7q5NqcqtIQvnUiTWHa2ERlYgdB5VUXN0T1Nb+LYlE+MMYe9a+L4qKuxa21goch9o/GpWmozmTl8MUytrPNIqrExLVrdG0A2VmjXtmYp5BzYfIJQ/ROK53DaKWaozWs0X1/nisengdI7TZZm9jZlA6b1DiPcXCM2CEYMfnWi1u2SKYoiciAjl8ulUkkAJyV3psRoXMlzN3CaVhY5aC21sXTv3cTNyjOOlO3MQlR5CCG6isxbzXFkxZGKc2xIFaC0v8AvbWESks7jBOOu9b2G4v620xVPtWvtpvYd6Lin4gyv3USRSpwwxTDRgggEgeVWd3bhwCsZJwelQHUrsQQffVVVTFjiDsq5GWNio6OASG2JPSnCoNNzRn6afSH6xTuwOKAaDq1ypHQphxhjTUh6VKkjBBON6jSDBwdqHnYW3UXCyYdMEsNhSQcinetS4YLaaMHuwGXZtzv76DjpTI+zCAqwy50Vd40ewB+FLuIZIHPNGyA7rzDGR50yT76Dd2CWuGqgQQbFdj4ZkVdPiTnxiFOnwrl3E2+v6gev49/tq84S4gkWSZb68URJGqoHIUDf9e1W2saPpF1o19qcVtHJK8TSrMGO7Z69cV3OJMGL4eySnIGW5IO9gCOV11sw/EKJrYiAW3JB3sFz6NyRjHSl+G4FNoCmelO4BG9cXAS5tjuuTCabJO1EuVOQKcIApJPlVb2a3ukiwzEnFDmZSM0YZgOhpLNnqagezqDqklgq5xg5NJkTu8Z8aSDg5HWjZi/U5xSLgW6jVJEaKjIIoqpKZLo1GTiipaLtmiWi5ThBEIzvTyE7UlELZwKlRQFyAq5NaNNC52gVjW9EqFzHk4zmtPYa65eOHulw2F69KqG0K8ijVjCeYk+xkbDzzVxp2ht36FWMjgZC4xvXb4NTVkRIAsNL3/dalIydj8rQRsu+cMaimvdnk9pq6NcJ+NCsCByKgBAB8MY2rznrl0t0eVFdQHJCsc4Hxrq3Cep3GnWLWdxK6W8hbnjPTcY/XVDxDwra3EIayhQO0mfZX6K46fCjJaF15Y2Gwcb9y6OuwyaWAOZ4kLmttacziQ+BzirSG4aEgp7LDcEHpUqLRLyOUK1q4UNg1LOjy/+Ff5VCiw50Lbt0K5+GimHstPkVMtONWto1iltO/YD2neQ5b47UueLT+KIxOqRafJz92UUg94cZ91Z24064infMTDfAptrWZCMxsKsc6UkiRtwjPX5/YqBnaOR0+O6PV+GZ7Esclo+bCyY9k1TTWbxHfp51prPU5kSK0uVWS1Vtw4yQKTewWkwxaytlmxyOuAAffWdUYRBK0vi0PRBz00Mn5kOncd/d1CyhXBowm3XrVnc6TcRcxaE4XqwwRUb1d1H0TiuefQSRus5pWa6JzTZwRWvd8jxOSvMQQR4VfW3CjX2gSapDO0ro3L3XJucHffNUXdmNlJHvrQ8N62LK6jgnunjsTzF06qTjbb41p0MMX+nMO5G0IhL8k40Og5WJ5+5Zp4MEimGTHjWj1+1sWue902dWiYZKnIINUhhY4yvSgK2iMby0BDTwGN5ZvbpsorxeOaaZdutS3jbmIxTbxkAYFZcsHQIZzVFKbdaRjBqQUPlTZU+VAPisVWQpFmMyKK3vCq8mi3539qRR+qsHaKRIprd8PuE0GfJwWn2HngCu79Fm9oA/wA2Wxguk5d0a75FSGf2cUgtSS2RSfGvQgFovkJKXzUMmluIrW19aumMcJbkVgM5aop1jSgP7olJ90RqiSqijNnuATkAWzOAv1ICkUM1EOt6YD9KdvhHSW4g01RtDdN8h+2qTiNOP6lHiRDd481Mzmjyarf3S2qnazlb4uBSP3Spn2bIfXJ/0qBxWm/V81D1iAf1jyP2Vrkii5qq24nyf7jjUf0iauWUNbW0/KqmWMOQD51bT1sU5swqxkkcgJjde3cU2DTqnJpnxpUZy6gnGT1NFlSY43sqni5sajNg7BV/qiqLSiTcQ4/lV+0Va8YSgancqDnHKP1CqrRpEjuIZJPoLKpbA3wCK4SqkBrw26xq9161/wD3H5rZXMvPcyHH5RpAwaO61PS3neSMylWOQOTFR31Wx5gVSbHlgffXZiqjAFytiZ0ecniA69VPiwLW899u/wBlc4vT7bVt5OILWKyu1W3l55ImRWJHs5rC3ThnYjxrh/S6pY8NDSszFJGObG1jr2v81GoUKFebFY6FChQpkkKlWZ9tai0/auFkXNG0Dg2YEpBbrQgzaLIxzyi5/wCEVKHwqg0nXrm0s5baAR92z8x50yc4qQ2vX5IPPGCOmIxXrtBicTIGgaroI6uARMBJuBbb91cd2x6KT9VM3zS2unz3S8yGPGGHmTVWdf1HOfWCCdtlFQ9T1a7uLRo57iR4yR7Phmp1mLsELi3Q2Ki+tgDTlvextsPDmoWqarJesGkdiwGCc9fdVQ7ZNLlfJ91NV5PiVc+okLnG5WE95ecziiox1oqOssGyZTbVvYIro1rLFq8AdcxyQxqhUkb7bGuZ2sgRsHxq5t7pplKnPMo6+YrvvRrEGxC3NaWH1nAJDhcHceG2q2bWMqdTH+kKQ0HKcNLCP8sVk8seuaHKfzf1V134u7k1GnEI+Ufx/ZasxxqMm4tx/wCYKQz2y7G8tx/l1lzt4Y+qk5HmPnUXYw4bBQOIDkz4rTmezUjN9B9WTRrf6eqvzXg2U45Qck4rL94q9XUf5Qpie6UA4kQjH5wzQs2OOYwk2TfiRabhg+P3QudUmlg5JJZGUHI5mJxVS8nO2flRPIW6n303Xm9diT5yMxusV7y83JV3pFwkLpJJ9FXUkDqd611zrumSzvIILkBj09n76wNtMqY5mwMirM6hbZ/hh8jXXYPi7I4cuYD3o+krXwsLG2seoWkfW7IH2Lef62FJ/D1sM4tJD8ZKzZ1G2/lCfgpojqVuOjP+jWmceb/dHmFccSkve48gtJ+H4cbWI+uT/pSDxAAdrKLHvY1nfwpB5SfKknVYfzZD8qgcfj/uhROJS/qHkPstMOJGHSzt/wBdIk4inb6NvbD/ACTWaOrRjpG/zFJOrg9IT+lVDvSSIaGT4H7JjiU36/gPsrjXOJLy9sUtpVhWNWB9hMHYedUdpKGZ+Y4yKZubvv1C8nLg565qMrlTXLVuMZ6oSA3H7IOeofM/O83K2A1vUSiL62+FUAYA2GPhSPwtenrdS/Os+uqSgYyu382ibUpj/GD6lFbY9IoWt0J/nvVprZDu8+ZVtquqXctssctxLInN9FmJFUEr5Y++lz3Us6gO/Ng5G1MZrmcWxP1qS7dkNLI6Q3cboVLsZVjfc49k71EoZI8TWbS1BgeHhQG60NrkwqeucmnScddqzolIAHM3zoGXbqTXUx+kbWMDcu3f+yt4vcru7fktnYEHbpVLLKXO9IL5pNYuJYoapwIFtFBzsyHjSgM0QFKFZTRc3UQjoY99Ae+hV4To8UMUfhQzU06ct52t3LruSpX5ikZoqAqec2DeSe5tZCjA360VKAG2SKQF0yUsfODiklCKeV0HTxoMUI61fw25d9VPKLJkIM560Zox1p7EbBfAimZGDskAit1yp3IGelSUO/lmkAY6YFKAya0YmZAArmiyeUp+UA1KlhFw5XnwV36U0GwRUlCoPOSMnwrRiLXjK5WtsdChHD3SgFiceNSocNsTTIwwG/WnYl643rQpxlIAGivaLbKk1mxS0lWRZC/fMzYxjlrSdnGrNBJc2Aiz3g77n5umBjGPrqp120klhjlVcpEGLnI2zineB76y0zVpJr2dYIjAyhmBOTkbbVgBoo8ZaR2WkjfaxGu/fdSoH8CvYb5Rfc7W57pPEOifg1u+E5kEsjDBXBHjWekXlc1u+M1a5S3WFebmkZh4ZHLWLvLeWCfkkXlbAOKXpLQthmJiacunW1yOqFxGERTuawaKPQ60KFcqUAioUdFTJ0KFChSTK64cAEkzHceyCPPerHXNGM089xHhAiAhMfSAHXNQ9AEcVszu6Bmfx64FdD4d0+116CSK6lDRsuUUHdfDP1+VehYXSRTYe2GXx+f3WzSQCWLhuXKo5GiINTE1iRUKg9RjrW313s7vS6xaXpHrCqx/GxKN1x45NU8/ZzxKI2C6FPzY29lfvrONJU0pLYn38LqiSmmhcWt18LrI3E3enaugdlGhNDfpqMkhAmUw92UwRlgM5+qmdK7KtVe2Se/tfVMMQe8O48jgeBrfaBbLpz2lvI4IidQ0mMZ9oZNWYfhpDvWp97aDv71dR0jmu4snuVzx32sDsw4lk0O00NLxUhil76S5KH2hnoFpPCfafacf6ZfQyW0en38bKViM3N3yluq5wSR4j31z/wBIySKXtNuniYOhtLcgj+hXONC1u44e1e31K1IE1u3MuRnwxWX64Ypg5+3NDetOZNZ2y9KzwSRu8TtnwP1islc9mVlevLHb3YtoJU5e7aPvOU+YOfrqZwpxxbcWQQ888A1GRWaW3QEEYPXHljB61pHgbG+wNdIyQPbcHQrTu14uFw3iTgSDRNWmsTfvKY1U8/dhc5GemayogUHAevTEFraSXeJ7aCdtufnjDHHh1qf+5/hLmY3ul2ajGVIjwSfqFBT0UBIytsedtb/FDvoo3Wy6fzxXmGK0STAMpXPwq70/gUalaLdJf4Vs4Bj6YOPOvQ1jo3BMF9BMNPtAqOGPMhI+R61p4rjhMjlW301VzsFtgP2VLg0sf+pEXeY+qvp6OnY68wzC3h9VyLhTsXt7eSy1K7vPWoe7DmF4MKSR4nPhV/xjw7Z3Nx3CFGkSJAsyjddum3gK02p6zZmGSCC6AjY8ojAIULnpjFYDibiiy0jnjhnjNw0fMqHOKNgLr3Gg6Ik5WizBlb0WB4u0o6YircOoXOUIGef3YrEyOoJ2wvXfwq+4g1eTVnEk0vO/NnlznlGOgrOXU0YVlyObBGKzsVmAN7hY1W8F2ijzTc4ZVGR503DKYZUk68hBxmkjZSPOirj5JXOcH8/3QFze60una1EySGUiNuYYBbrT09l3rvIHwWOcEdKyoJByOo3rV2+p29yiRrMGmYDIweuN67DCcSbWMMFURcbcr3v8UfBLxBlfyUCWExuVO9MvkDmJ5cbmrd0Vjl1BqtmiOCrqcHY1bWUvDuQmkjskYyKamiDYPjQj5wQnXl60/wApPUUCGiVtiFTbMoBUr1pUcvdknrT00WVJAqP3beVZ8jHRO0VRBBVhLKvEN7Z22GgKx91zfSzjfOKTrPDT6TbpOJxIrMFIK4OagJzRuHVmVh0KnBFajRdV09dPEOpypK4kZgsyl9vCtGkhp6/O2qsJDqHk2AtYAW0CNgEc+ZsujjzJsOSx6rittour20/C02n3QaCKKMxtKp5icnOcVMa10biGCS3txCCmG5oU5CngD03qv/ctc2lpdW1uwue8XKdFJPlitKgwaaikdJGRJGWuFxz02tvuOSNp6SopncSGz2kEaa+7/CyF13Indbd3eIHCswwSPhQVwRj9VWv7kNdcgDT2Hh9JR+2qiWGS3meKReWSNirDyIrjJYp4HZpGFoPUEeV1kS08sXaewtB6ghOcoaksvIMjelRuOX2jvSjVwDXNuFTZM8/gaLKk77U4UG5xTTLg0NIHDfVMi2ztS0JFJKHGaNCQenWoMu12qSN8k5pFLc5G1IppT2tExSqdT6Ipqnk3UVfDupNT0G/NV3w/bie9EbHGYyQfEHaqW36tWj4YmME9zICAVtXIPluK6nBGAzMv1R9E1rpWh2y0hiXu+R8sR4nqat7XS1sjzYYlhsX8vKqmGUTRrIN+YA11rs6szfXs1n3UNw0kAkCyAEAjr1+Nd/iFVwIuJyXcUfCY105F8trKig4Nv54YLuDMlrK6IZAp9gsR4e6txa9k9tbTSC61GS5hMfKnJH3bK+evUgit9pNo1hYiB41jPOx5VxinpUAUuSAFBJJOABXC1OPVEhysNh8/53ICpxmV7srNAuap2J6dLMJG1a7K5yV7pcn681Em7E7wS8tvqKSJ5tCQfkDU7i/tX07TY9S0u1W4kuBE0aXULqY1YjqCDnbPhXLV491rAVeIL8D/APkNWvRR4rO3OX26XA+itp56kXcXgX62UvXuzjV9NnmNzp12I1cgSiM8rDPWs3c8P4Yc0jRtjYOuM1oLPtH4h0su8WuzlGbLLPJzqf0qtIu1uC+kH4dsNMvR0Z0Kq2PLG4rZM9XD2ZA13gbHyP3Vh4Un+q0HwNv55rltxpEkbNljnPgKjGxIGTIQR7q6nf3nD7MupaJcQKJNxbzFG5R47E1TXsljqUwMkdtFKw5QYQACfePOk0Qy9q1vfz8EO7BWP1jdv1WM06ZI+eC4AMUuxJ8KVeaLyATQnngYZBAzitJPwpdonPEqyp/N60JbHUNPij7+Fo0kTC8y7MKujhie0NJBVQw5wbw5htsf5yWHu7D2QyNnG2KgBOQ7VvlsXOe9jjI8CFxUO50piGCRKw8NhQNRg7JHZmGxQM2DyAZ2/JZ6CwlubT1iAc+GKso/JxULuiehrV6dbyWsxzGVTxUjAzSrvSWLNNBBzRDHMQPok1F+FBzW3OvNQOGudGHtBuNx9Vj5LfrvvTLRHl3rQ3Fq4lP4vHTwqLPA4Ufi/Gs2owjLc/RZskBF1QvFjJztTDDPSreW3kwx5NsVBaE8uOXFc7VULmHZCPZZJt/ZYVtdHX+0CH86dz9lYqNcOK2Wi67a2WipaTWbSuGZucMANzW96NuyP12H7I7C3xtkdxHWGU/TonwKUKabiW3AwNPX62/6UyeJFH0LGMD3tXaOr42rR4tMN5PgfsnddZl0BcnANxtn+iayXfRrs0igj31d8Q6+2o6bDbG3SJY5ObKtnO1Y+diZCa4b0hxXhTZmi6zMUmjfKOEbgABWhuoB/GrSfXIPz/1GqnJoZ99c0cflOzR/Pes3OVa+uQDxb9GiN/D4Bz9VVeSfGjyagcdm5AJZyrMXiuCQrV0BoXa1sgFP9zpjH9EVzKB8AitJHqd8YY0N3PyqoUDnOwx0rrfR3EjcufrotHD6tsQeHg6227lpe4lH8W3yp+2sppZUUIckjFZE3U56zyn/ACzRi5mztNJ+ma6n8U7kazEIgQcp8/2RcaEprV4viGAPyFUVpciNgCwA5hnNL1GdnmlLEkljkk5qAd68xxPEXMqzIzr9VjVM3FmdIBa5J8ytE19b9e/T50Q1C18Zh8jWeBxR8xq//imY/wBI+P3UOKVeXWoWzW0irISxXAHKapHbmOaIsTSayMRxWSscC4AW6KLnF26OhQoVlqKFChQpkkOtGp5WzRYoU4NjcJKdb6i0cZUoG36k4p38JuekSj6zVZR5rVjxmoY0NDtvD7J85U9tSl8FQUzNeySxlW5cHyFRaFVy4pPICC46pZigTmhQoVnE3TIUKFCmSQp1Z3AADEYpqhVscrmeyUk96xJ+e/zNAzsert8zTNCrvWpDuUtU4ZCepPzoc9N0eKbjOKZKL5pOaBFFioOkcUkKFHRVSU6AOKPJosYoVIPITJXNQ5qTvQqXEKSPNAtRUKbOUkeaImhQpiSkhQoUKinQoUKFLVJChQoUrJIUKFDFKySFA0KMU9imRUKPFHipBhSQ6UdEBR4NWgFOjoxSQKGDUtQldKoUQowKkLpXQo6AU0YQ1MNJToUKPkNJKGkWkckkYNGOtI5WoDIpg4jcJZk6KUDims58aQzE5Aq0yZdVLMpsTZU/GnUOT1qtAfGxPzpyAyLKPaPj41dFVm4BaptkVhjJp4dajRtnqfGpWCMHFbEGuqIbqnUyAPKpdrg5piJwUUHG1SYX2ZQBjatulADgboqMaprWB/am4OfAfaKyY6mt1CnNgEAg+BGaymvjl1e4UAABgMAY8BWJ6U0nZZV36Nt5m91TWx6CT3Ld67ApOmr0P/8AhWO4jQR6kVBz+LXethpimTh+xZtz3IOTv4mspxMo/Cf/AJa/trcx4cTDmyj+otPwR+LM7PF/VY/BUOaBpUgw5pNeYuFjZc8hRUdA0ySKhQoUklZ2EqtAIwfbBJx7q1PDV3OhCCVkVMkcvUE/srCBmQ5UkHzBq10y55I1CzES5O2d66fBsSs9rHDYdfBG00+VwK7tomvXD4iaFCVQe3k71ZXXE8VmrBkV5QMhFz1+NcOk1e6eBY3upAq9BzEVCOslThblubPi5ropaunabv09603VzeYXaZ+LTesiSW3drsMK+Rnzp2Tu8nlG+a5pw/rj3ZFvcH8ZnCk+NdG0e2D6fExO5z9tENdG5gezZXMeHjM1c+7eCx7QJ852tLf+pWEurBo4o7hSCrAZHkcV0rtE4Xu9a4v9diSV471URQXyQwAXHuFdAtOELN+FodDv7e1Mq24ikljjGeYeIOMmucOFF7pDLoDt91mmjc97y7TouAcOa9c8PaiLy1lMUgBUMAD167GvS7TF7WJjuSiknzJArkMHY3qh1aRf7DWAMzLzyZ9nOxxiu0QoIYkTbmVQuceQxV9DDLBHkkta+iuo43xtIeolvboC04wrsMZ88UV1ylA7sAqAlifAedSJFAPKDsBtVRrTMixD8kk5Hga0WDMUYNVi9T401H1yQafAhslO0pXcjzOelVcnaTfWj8sspU9diPuqXxKnq9vc8nIkbjmAXYDcfKuZ3+LuZpGYCOMldj199Sr5+AwcMXceR+KHqZ3RDs7rfntGlYZ7tmzvnm6/qqh1rW31e7W4MTAhAmCc9CfvrFmR1OA7YHTenIu9l35nI+Nc+30gdIcoYgHVr3CxUu9uxG7Km7EnI/NqEq8x52OSdzmpEcBLZbw8/GhdLyxEgY3FZ0zJJbzSbDYIRxLtSodChQrIVaFOQzPbyLIhwy9CKRRVJri0hzdwnBWj0q4lu7ZnlJYhiAfdinZIRMACcY8qzKuy7BmA9xq403UJbm6ZJCOQqSBjpXW4di7Jw2nmBvtfqjY5g4BrkuW3Ech5T7S/rpKuGJGwI6jPSrCRA/31Dubd2UGELzA7++j6imdHdzB7lJ7CNQkMOZSKYaPlOM09G4dc+PiPKleNBOjDwCqyLqA6lTvSd6lyIckgbUyy5PSs+SHKVSW2VjoGqSabJM0cSPzqFPMTtg1vLKRbyGGdMjnAOw6VzHdem1SLG6eG6gLTyLGrgn2jgD4V0GE40aZogeLj5XK1cOxN1NZjhdvlzXWY48bkVmLrs8tby8nuX1CVTNIX5FjG2T0zTmlcSwAtGszSxjc+ycr860EM8dx9BwTjOPGupqaSlrmjOA8DbXT4Lsm+p4kwCQA25X+y4vdxi3uZYlOe7crk+40UUjM2Ca7DqOlxT2VysdtA0zxuF9hc8xBxvXLNQ4c1XSoBcXdo0MfME5iwO5+BrzjFcEmoH52Xc03JsDYeO643E8GlozdvaG9wDp4qKd6QVzSY5OX2SCSTUg4HhWc3LK26x90ztjB8aQfZOKdYbnbFIY4xVT280xSW6UmlMcjNJoeTdRR07G22KaoA1Y1+U3TgqbC2G+NWum3jW0jKFUiVDE2fI/8A2qmiIIO++1TYDzSpjzrocNnLHNI6omF5abha/Tbsi2MYAzGf1V1Tsr146ZxBYNIYgty3cyFycIreXv6VxnTLlLd375+VSuxxmtto91HKEKuEdMFcHr7xXobmtq6QsPMEFdjg0jZWmGR24svVjgFyB4Vie1zUbnTeF1hgZAl9KbeUkZPJy823luKveAtUTWuGLS4Nybm4jXu7hiNw43x79sVl+2+KeThq0kjjLRxXgMjD8kFCB+uuBw+HJXsifyd8ln08eSqEbuRXn7VNS5e+SMRsoXANUf4QkXfCCpl/bSRR3E0ilY1yxJ8BWR1O+eeQw2788JXcAdcbmu1xbE/UmB3PkOZ/ZCYjVyB+Z/knNa12a95rRkVUjkzkH6WKOHhXVriGOeO2VkkUMpDr0NJ4a0621G/ZLn2o0Tn5M45t8Vtb6/SygjgtmEZUABQOi1zmHYZJiodW1r9DoLWvp7vJC01MydpnqCbd26xUnCusJ1sm+pl++mxw9q6sR6jPlfL/AO9a1tSvBCZmkHIGC5wOpoRavcdVddv5tFn0Xo3O0kd8PsrDR0fIuHksrbw6/ZOJbaLUYXAwGj5wcUJtX4izyy3uqbeDO+1a/wDDNyerL+jT8eqEqCzLnx2pj6Jg/wCnOR/PEKbKSHZsrh7v3WNsuKNb0/vPx7y94BtcAvjHlnpTx441g/lWw/8AKFbNdWjxhlVvqpPrthJtNaRP5ZQH9lXN9Ha2NuWOrNv5/uRbYZGtDWVRA9/3WOHGmrHq9uf/AChUmPtE1SFHRbezKuMNlD99akHRX3azg+uAUlrfhyQYbT7b/Q0z8HxUCzan5/urGU9U3VtUPNYubi69ncu0FuCfIH76ZbiS7cYMEPyNb2DSeFmAL2Nv/o2++pf4E4PZfZsbct7kYftod+FY0d57+f2VLsLqHG/Gaff+y5xFrkkjqk0USxscMwByB51Pe2RlDIcoRkHzFdFvOF+DbzTJ4NMsLY6g8Dd0MuCJOXbcnHWuY+qXvD12sWsLLbq8R7sFucbHwwTVDX1FI4R1/aDv6treJIGhQdRRTU9uLZwPMbDxTFxEFc4z186XDfRwxCN+bI6YGdqkzW5lUuoypHMDVdLbyc30fCq5eNSvL4Rus57S03CkNqcI8JPlRDVYB+RIflVe8bBelMkMPCgJMaqmHW3kqS9wU++1GOeNVRGGDnc1Wu3Mc0GJNFWDXVslU/M8qJJOpRUKOhQCZChQoUkkYYgbVMXUbgKAHUAD80VCoUVT1ksH+m4jwKQJGylnULg/xv6hRG/uP5Zqi9KFXOxOoP8AWfMpXPVKkkaRiWOST1pFCgKBe8uN3JIUdChiopIUKFCnSQzQoUKSSFChQpJIUKFClZJChQxQxSskhQoUKVikhQoUMUrFMhQoYoYp8pSQoUeKGDSylJFQxR4oAZ2qTWm9kkpUz4il91n3/Cum+j5c6Bp/aJaTcSGyGnm2nVjeRCSPnKjlyCDvmvQnaL6P+kdpWpadrGlatZaRaLZiMLaWKsswLFg45So6HFGcPJbONDzUwwkXXi4QsRkCm2BBr3Rqtrwl2M9lUGh6rfQu5tbm0t7l7Id5cSsrt4AkfSG5PlXh6dBtjwGKbhZmlwGiYtso9CjO3WiG9C5dbKKGKPlp6KAuRtUpbBvHHzo6DD5JRdoUgwlV/KaHJVn6iT0GfhR/g+T+TY/UaJ/CZf0qXDKq+Wj5Km+rEScpBBHgRVonDGpdDp12DjO8LD9lKHCpJDZoTshe82aLrP8AdmiKYrTLwrqjdNNufrTFKk4L1gIznT5Qqgkk46fOiTgU1rhqv9QqDsw+RWWwaPkzUp7flPuq10Hhy6124eCzWMyRxmVu8cIAo6nJ+NBxYa978gCHjhc9wY0XJVEIj5Gh3J8jW7i7OdTIBaSxX/zs/sp1+zu6j+neWQ+BY/srWb6NyuGy0W4LWO2jK58Y8UO7NXms6JLpV20EhDgYxIoIVts7Zqx4X4Tj16K6eS7Ft6uFIHJzFyc7dfdQbcFldLwgNUKyhlfNwA3tdPBZLuz4ijERNbPiDg+DSdM9ajvmml51QxGLlxnxzmqDSrNbu+ggkJVZJFRivUAmnlwaSKUROGpTz0MsEoikFifqq3uD76LujnGK6dHwJo7ZVri9LBsdVAx8qwktnyXTIu6hyBn40TU4BJAATzV9bhU1IGmUb999lBW1ZvA04LFj4Guq6dw9w4vD+lytYCe7ng552MzjDZPgNulBuH9HzlNNhA97Mf21qU/o3maHFaMHo5PKwPBAB8fsuVGyYeBpBgION662NJ0uID+1drt5qTn9dZTjGG0j1S3ENtFAhhyViXlBPMd6VT6OtjZmuq67An0kXFe4HwusktmSM03JAUrsUdlbTCMxW0CkovSMeQrB8YWwXXrlcAY5dgMfkioVvo+2GLM03KWI4IaSESl17m23cT9FnILN5voqx+AzT8mmyxpztFIEzjmKkD51vOAIlTT73LAAyRnH1GrDi2RH4beMvnFwhAJ+NWw+j7OBxHHVTiwRrqL1ov5E2t0PW65/ZcO393EssNlcyRtnDpGSDj31MHCOpHb1C5yfNcVseG9WS34dtrY3SRqGclC4G/MfCrKG7jusmOZJADg8rZxWjTYJTZATuVpUeAU0sbHOk1IBtp0XPk4O1SQ4WzcfFlH7aRf8G6nYWcl5PAqwx45j3ikjJx0rotxJbWih7iZIlJwC3iaqOJda02XQrq2ivI5JH5eVRnchgaeowSmawuT1mB0UETiZO0ASASOmmllj9N4Ov9UsxeQdwIi5Qc8mCSMZ2+uo2t8OXOjLEbhoj3ueURtnpjr863fBrr+AFGf4+T/hqs49iDR2jeXP+yhJ8Fg9VMjR2rISXCIG4b602+awPdqQudtlTgU5BGXalyQnPSn7RcN0rk6ekLpwx2y5YDVbY9mtvGeRtWJYAZ5bfzGfOsZrOnjTr+4tUcusMhQORgtjxxXYJ5lMvOjBlZVwR/RFct4qIOr3v+NauoxjDIIacPYLFdXjuHU9NAx8LbEnqeneVRx3DRYUAEZ8atQ+RnNUhHtfXVpEwbO+cVzOHzu7TSuahcdlMB5YiyDLkHGfCpNg0roTKoU7UxbYxlmxgVMhw2eTeuoo2XcHX9y0IxqCptu2MDFZvimBYb8SqWLTjnYHoPDatFBzBlJFVHFlu7pBdArygGMgnfOc1P0hj4mGuNvZIP0+qtqm5oD3I9F4mvZjY6V3cAh5lh5gp5sZ+2nOLrJINTHKzEmNev11TcPsF1uxJ/l0+2tVxkO8kVlUcygZPiBvWXhj5KzDJRM7NlIt3ABM1zp6FzpDcggDwssTcKElI3PjTdPXSkS58MUxXGztyyOHesU7o6BoUKpTIqFChSSQowSpBBIIoqFJJS31KV8ZVNqjO/eEHlA+FJoVdJUSS+2bqRcTur/h6dlYFQOaFlIJ8a7Rw5dd5odvPMVUnmz4D6VcI0u89W7z2lBOOoz0rfR8VNFwYITaFyHXmkY+wFJznHUeFdrhNQx9IATqN/C5WtRTNDLHkutafMCr4xkEYpa3LfhSC2BX8YpJz192Kxei8b2k9vBJAwl73AlXcGPz+NP8XcXJwvrlnKyRkiMSAyEgMMnYYrQLWgZiRYje+iPLmgZr6LoJi5OpqA1/CdV/BwmTv+673u/ysedVdlxFdcRvZXmn4j0+SPnduX6TZ3UE1keMNT1XhzipdYnk7m1H4qKcxgqVK/RPmapZGN3EDRRJAFyt/r1zJpekXl7EFMkMfMvMMjORWTveNtKvdNtZZpRHc+0HhUEnmA6D3Hwp3SbWLiqzn1LUXe5knTlQhiqqOXbAH1VzjSbN7LjWWBmcxrIOTmbmxVzQGFrbXJO/TRM4lpaBzUDW9Zv7vUJbS4iCw3QwCVIYL7qpbuFLa3kiQkjGd60vHhjh12CR25VjLAk+8VkdTulmkHdScy8uDisTFZGw8TObu2G17ED4XKy6rRzg43IULrVlY24a2L79TVaDir/T42TSyWXG5/ZWNg0IlmN+QKGgbmcVGdOQD3mol5IcBNsEZqdOBgfGq+9B5kPhjFFYhdkZDUpBZRqFChXPKhChQoUkkKdtrl7WTvEAJxjemqFSY9zHBzTYhODY3Cu9O1Ga7n7t1QKFJ2FTnHLjHjVXo9pPHJ6w8bLEyHDnoalahKuEw4PXpXa0M8opOJPe/f00R7HER3cmJYu6uowjNiRjzLnan35I1BY4zVaHBvY2zsCM0/dSLI2VbIxQcdS2zy0c9PJUteLEhStjTbImdzj3U3by+yQ56DakyyrzbnFWOmaWZinLgRdCRUHQ5psjG48KJpAQMGkFx50A94voqiQpVpfzWbM0fJ7QweYZq3seL7yCfnWK36Ebg4+2s4XXzpPeDzoiDF6inAbG8gdFbFVSREGN1rLqmh8QLqNqZbrkRg5X8WCRUjWrGy16yFtLI4QMHBiYA5H/AN65Xa6lcWqMkNxLEpOcKcAmt5w5qlneyRwrciSfucsOU5yMZrsMOxSnxCLgTb2sQSNfBdVh2M+tAU04vfQ35rGcUaPDpGqerWvemPu1fLnJyetViTeDHYCuu6oVGm3eAM9xJvj+aa45XGekGHDDqgOiOj7m1rW7li41QNpZQWHR1zbopOzbikuvSlRD8WMUHBGMis0jMy5WPyTRXakkYp3FJZcmqHs0UbJuh5UKUBtVA1TJ2E4JG+9TLZ+7lU++oKHB2qQp6EbVqUcmUg9FYw2Vwk3eDoAR5VptInKTQqSTgZHu2rGwyHlzneri0upMDDEMoGCDXfYPXi5DtytSjn4bw9d+7Nu0GDh7S3s5LeWYzXHN7JG2wFdT450VdT4YuhKPZhAuACOpXwPzry5oerYReV3Rw+QemD8a7/2WcfXPEqz6LrPPeTKjSd/KQeePYcjDx+NB45h5ieK6AbG5+Gy6Wqia5rK2Dcau+C819qYk0ueC1tpGWC4R3ZAfDOMHzFYy0tQLR3O8kqHBPgK796TfBqLeabqmm2UcNpFausvdIQobnzk42BxiuEwS95FjGCpwRQd/W5xUSa3GgPLkVzOIOMlQZSLB2yicN3DWurQ9OWU922fI1qtWAyJl3CjBrFXOba6bkymG5lI8K1ejTm+0pC7d5IAyMW3yf/rFXei9SA2TDnbi5HusP3VNI8lphPiory8wLeNCOcoelMMpQkHqNqNTvWjxnX6KOY3U9ZCdsU6ucVDiJJxmuocFdhfFHGugW+uafNpiWs7OqLPMyv7Jwdgp8aN9fiibnmdYd6va8blc9DHoKMHFdkT0X+MiN7nRv9Yb/lrI8f8AZZrPZ41omqPaSNdq7obZy+ApGc5Ax1qyDFKWZ4jilBceQKuaWk2DrlYoyEUoS0xICDvSQxB60XxiCmLiFOSZgKdW4YHINQ4eZ2CqCSegG5JrvVn6LV/d2sNwOJrVBLGsgVrR8jIBwfa99V1OL09IAah+W+2/0UxOGC5NlxWG+eJuZWZXHRh4VXcdWvrmlWmpAtmNu7Ix4N4/Mfrrpfaf2QTdmVpYXFxrEN/65I8YVIGj5OUA5ySc9axtoYru3lsrkLJE6nlVwCFPng/OqapkOLULuCbhw0Ou4/dGRS+sxOgJuHDTx5LMaXGZdHjdmJIBX5Got3GEfAPhRaLJJa6pJp80zCNOdQjnALZ2286manERMByD6PlWLAW1GHtcBZzbNPiN1kkZowemiopTlajEdamyoQn0fGoxHX4VyVTGb6oBwUYikHY06Rimz1rHkbZVIqBpaxlugo2iYDcYqHCda6ZN0KUEJOBS+4bGcUhC4p01QpTLy0QFQLCDYpIsZo+U1MsNOuNQl7q2t5Z5MZ5IkLnHwAq8tuAuJLn+B4d1iX+hZSn/AIaMioy4XKcNJWY5DQ5DW6g7I+OLgjuuD9dbIz/cbj7RU6LsH7R7hcpwbqwH8+NV+01c6ja0akJ8i5tijCE1ca7w5qPDmrT6Vqtq9pe2zcksLkEocA4ONuhFbLs97E+KO0ewuL/RIbM21vL3LyXFwI/axnAGN9jUG0m5cbBINXNu7PlQKEV3uP0SeOmJ7y70CLb8q6c/YlSD6IHFxhdzrOhGQIWEaNKSxA2GSoG/Sk6GID2gnyLz1igATUu8s3tZnikAEkbFGGc4IOCKlaDpX4X1aysAzL61cRwcwXmK8zAZx44zUPVSH5SoWN7Kt7o+VH3LV6qi9D/S43Ky8WXzYOMpZIPtarSD0RuEFQd9xBrjnx5ViXP6jUw2Abu+BVnDXkAxkUYiY+deje2bsA4W7P8Agl9b0u91We6W6hhxcSIU5XJBOAo32qy7C+w/gvjTgO31vW7G7uLx7qeMlLpo1KowA2FPw4rZ+Sjk1svL7RFfCiWMsdq9Bekb2Q6ZwdFpuocK8P3FvpccLi+uFd5FSQuAnMWJxkVgOxbhF+K+0DSLWTSX1KxhuElvoynMiwZwS/8ANyRSEDHWcNk2XWy5/wBw1GLc+4fXX0Dk7JuBLOyneHg7QQ6xOyn1NWIIUkdc1xH0R7Wznk4pe6tLa4KC2C97Er8uS+cZBxSa2NzHPA2t8VLJrZeaWhIp200+e8mWG3ilmlc4VI1LM3wA3NewPSot7KHsugMFrbQsdUhGY4lU45X8gK4J6PnMO2DhgqTtdsNv8W9Sjja4Zrdfgmy62WTHZ9xTJGZI+G9ZZACxYWUmAPPpWdKlTuDX0q1iaX8G3wLvj1WX8o/ybV84ZUDFR51WyPigkCyTm2VrwXwNrnHmqHS9AsfXLtYWnMfeKmEUgE5YgeIroUHou9pMw9rSLOAf4W+i/YTXVfRQ7OrO00occ+vSvdXKT2IteQBEUOuWz1JPKPnXcl4j0qfWZtEg1K0fU4E7yWzV8yxrgHJHh1Hzp3OLCWMF7b6FSDei+evGnBep8Da/daFqwhF7bchkEMnOo5lDDB8diKr9D0ptV1WzshIIvWZ44ecjPJzMFzjxxmvRnpV9n8MQ/dzFO/f3E0VpcQnlCgBCFYeJY8u/urgvBkmOJdKPlewf7xaviiY8tPVMW2K9BQehpNHMqzcaqo5sHu7A5/W1cr1jtI424Zv7jQIOKtVNvpkz2UXJOUHLGxUYA6bDpXu1nRrgEkZ5+n114Rfg6+457XNR0OwjZ3udWuS7KRmOISku+5A2G+KlQ1LzmznQeCmCW7LpHZX2car236Td3vG2u8Sep2cqGyzJlJSynnZS4OcYAyKY7bOwLhXs84FfWtKuNWmvBdwwf2VOrLytzZ2CjfYV6Z0fTrXh/S7XStOhS3s7SMRRRoMKqjyHvO/11y30ppebspkGP/aFt/x1V6w+WcfpvsncL6leLJV5TiiiGWFKn+lRQ7OKiWDiqjmui9mjWdquoS3Om2V+eSMItzHzBMk7itMDDjC2dmn9GBfurI8COQLwbYKpn5mrniK8ktdHnmidkdSuGU4PWvTcNiijpBIRy+S7/CHRQ4eJnNvYE9+hKuUcruqRDw2jX7qkwXzrkcwHu5RXKk4j1B25fWrnGf5SukbA/DH2UTTzw1IPD5I7DsUirc3DaRlt8b/ZYXihebii6OxJlU5+oV0PUL66u7pu/nkkK+yMnoB4VzjiOT/8SXJJx+MT7BW/mnHeNjrnrVNCxvFkNuaAwUgVNT/3fVybeWGMkPPEpHUM42o0uldcxuki9MggiuccSXMg1y9VQMd55e4VruDo2fQY3YYzLJ1GM71OCubNMYQNldR4wamqdTllgL636GypuMtLt4pYrmGPu3nZudV2XYDoPCtZoMMWn6JbLbRpELiFXmIG7tjffr9VZ7jhu5htM+Lt9lXOlzM2lWQ/wCfZUIYYm1T8o10VNHHEzE5coGgHxsnb3XrPTpBFPKVcrzABSdvqoWOsWuqu6WzuxQBjzKRtnFZDjhiuoQHJH4n/AIjT/AUx57xs59hOvxNRbXk1fq1km4zMcQ9UIGW58dr9Vo+JYA2gXbMASqgjIzj2h0qn4JkKx3oHmn7atOIpi2hX2+3IP6wqp4EHMt9n+Z+2rZdKto7vulVOH4tEf9p//ZT+LW/tI7Hr3iVjdA31W1J/l0+2tbxi2NFcD+VT7axugufwna/49PtrNxJ4FdG093zWRjjr4gzwb8yuog8s/X8quW3MpS7fb+Mb7TXTw2ZcH86uW3mBfSj/AAjfaavxsuaGZUb6Uu7Mdup+i6VogabSLE+UQH21WcRa5daTfrBFMiKYw2CoJzvU/h92TRbM5/ix9pqe9yr4LwxOQMBmjBOPjitQNeY25Tqtgskmo42RPymzdfd4hYWbjG9Of7JP+SoqoutSn1K6WWeRnKjlHN4Cui6pMJbC4HcxACFx7MYHgfdXLYlw+3TFYOKSTxvYx7rgrkMYingLWSSl4OvP7ldatJiLeF/Exqf1CsjxVpWpX2szTQWNzKjKmHSMkHbzrUWbc1pAAR/BJ9gp2S6aIcpuOUeXPityqpePHlJsuvraZlZA2N7rDQ/D91TcJade2Vpci6tpYSzpjnXGdjTvFiMuhSsdvxifbVit6GBCyq+OoDZxVVxXM0mhTAnOHT7ag+ERUxaDewKoliZBh74WG4DXfUrA+tNFlFAO/jW14HZ3srpicnvV6fA1gZD+MrccDylLW6AP5a/Ya53A5nSVOVx0C5XAX2rW37/kVN42lkXS4CG5T337DWBed5GwzsRnzrb8ZuX0pDnOJh9hrAqT3lRx6VzagMB00U/SF16wnuC6VwYC+hDB6Tv9i1ZX2l2mpKi3kTSCMkryuV6/D4VRcGyldJcc2AJm+wVJ4i4iuNJgt2hVG7xmViy56AYro4ixtI18ns2XS0tTCzDmmcXaAL8+fRSRwpoQPtWbEe+Vqx+uaBLo920iKGtXZjGy5PKM7Bj51o+G9duNa9ZWZY/xYVgVXB3ztQ4ucjQ5Rn8tPtoaelp5YuNGLW1uEFW09HU0JqIG5bAkWAG3XyV0D+LT+gv2Cub8Vf8AfF5/jD9ldDjkDwxknGY1P6hXPeLNtZuvewP6hQ3pG21GD3/RR9IZA6nb3H6FUH5X11MjJVsioJ+ltUtGwwzuM7ivPKV9iVx0anRzDlGTvVnZA+0c9cVE9WW5Ve5CAfKrOGIRKFHUAZNdph8Dw67thzWrCw3UiFcstUvGLFfVI8YGHb7Ku4HCsCdhvv8AVWHubqa5b8dNJLy5C87ZwM1H0mrGQ0XAtq/4WIKlWShsWTr+ykaAA2tWI/w6fbWu4uISXA35lA+2hwTo4TTGurmzVZO954pJE9rlx1B8qY4nOdS5ScgRrseg61DB6R9LhjnP/wCpr4aIkUzoMPu7+sgrIXjZlIHhUepOoYF2+BgbfZUauErL8d9+pXPO3KFChR0MooUKLFCkkhQoUKSSFChQpJIAldwcVvNAmtr/AEWe0l2549lbxx/9qwlSLS+uLWVDFJJheihiM1s4LiLKOUmQXa7QhE0s4idci4K6f2RvELK5WSNXKOuCRkjc1b9saJccNQzrCrNDOuZPFFOftNcs0HiC50KVxzzRhyGOCRg/Crrifjo69pUtrhgH5cDBwMHPjW2yqp5KAtzAODSLHfQI4VEbqfITqAuo9ml1HJwbYqFKmNeXJ8ap+2l5JOGYBygoLlST4g4IH7a5pofHusaDY+pWtwREDlRyg4+YprXeNtX4jgEF7cu8QIbk2xke4UPJidM+mLS7tFtrW52smdVxGHLre1l1Ps/4msLXhi0S7aVZOXoqZGBt+yud8U616txLJc6fMfYlZjjbO/Q1mk1C9jVYormdVGyqrkAfAVdaPwhd6rGbi5ma3XnwQ6Es3vp/xKWsjEFIw59Lm/Tp7+qi6pdOxscTTcW18FW6tq9xrV00kmfabIQHOPrpmOyKrzSruTsDVmmmpYcSJahi8fMQpbqRg9ad16KKB1jjwjFc4A2G/jQ7aCR4kqao3c02t3oYxOIdI86g2WfRCZuUDo36q0tupGmtzDGTkfCkWunp3cZdIjsCSB1p68ZlHdLjlIya0MNw91I1z3c1bDEYwXFQLlQEXHnVdfD2U+JqXc3KLhSR161AmmMxwB7I6e+svEpoyC0HVDyuCY8KGKOhXP2Q6KhR5oAZpkkXSn7K1a9uEgRlDNndulSrbQ72eOKZI0aN8EHmHStJNDa2eZhBHHg45lQZrocMwKSb82cZWCx1BFx3Hw+aLhpS7tO0CZEHqmmJA5DME5cjpmqO+JAB+NP317zzycsjcpPsjJ8qr5pWYY5ifia0MUro3N4UY0aLD3J55WnQck3GCW56ezk4HSkKpApaHGflmsSMZRZDBLViinpTZOTuaS0gH5QpkyFj1I91RlnA0TlydZsYoi1NEnzJouY+ZoYzqF04d6TSck+dL8KZrsyihWh4GJGudf4l/wBlZ9FJHQmuh6BpZsLGBpLeJLjkPM6gFiDv1+GK6P0coX1FU2QaBlj8dlqYRA59Q142bqrfUWA027z/ACEn9U1yDathxve3ME9vHFcSxq8Tc6qxAbfxrIBc03pdWCerELRbJp43sUVj1UJpgy3sqVDtGp91HJ0GaXAg7pdqOQAgDArObGeGPALHtomMYFERtTnLhegzSWGBVLmprKPilqNqICnFjJA2oWNhuoAIlXNPIvQZpyG1eQ4Vck7Vdw8J6msqCS3Eak7szDArao8NmlsWNJRUNNLJ7DSfAKnjXB65zU+KYqRt0q3HC90PyoPn/wBKk6pw4sphfTuVnKgSpkAKQBuPjvXTQYTUQtLgEe3DqgNLsu3n7kzYX4UInIMM2c56V0/s441/c3q0t0llHcs8BiKNIVxkg5yB7q5O1hc2n4pkAkXcgMDtV5o12YpC3MVcpg/GuijYKiAwVDeXh7lp4dM4P4E3sncL0/xxZRdoPZHe3FrIsEktuJyiHvOR1OTGa8aavZTaHetFLGCSNsHYjzFdr4E4wbSNWgkmvbmGzYlLkRDm7xCCMFehrOXel6BxbFdWCSRx61bySpDzc2CM7ZOMEYrnDhbqUPia7ndp8dx7rK2swhrmkRu1Orfq3xXI9RUOqzDbwNWPDOoCFJLbkyxPeA56+GKVq2i3WmzT2N9C0VxFsynwPUVUWcps7uORhjlb2gfLxrEZJJQ4gyp2B3+R+65a7oZAToVe3a8kzb55va+GaY5iPCpl/gqkq7g7cwOxHhVdLN3aE9K6CsIjkcb6bq2TskrpvYZ2c3fH/FtvNc6ctzw9ZS/2e7y8gIwcKMHmOSB099e1LWzgsraK2too4YIkCJHGMKigYAA8q8weiTqxhvr7TS2Bd2vfrnxZH3/Uxr0nqK3kun3UdhIUu3hdYG6cshU8pz8cVx2MOkfK0OPZIBHvF/29yg9pFrncXTV1xhw5ZTSwXHEGkQzQsUkjkvI1ZCOoIJyDUiKa01e3jmjaC7tZ0yjjDpIh8j0INfPbXtS1yLUrwalKDeieRZ3b2mMgYhiT4nOd69EejFxkZ76TQ5765kguYgbWJgSiSIMvjP0ds+40TNg0TYXyQPJcwXIItcc7eG/gpMY1wJadQs12ydkI4Bmtr20u3u7W+eZuQW5UWwBBC5BIxhsDOOlclY8hOa968b6Vc61wZrelWYEtzeWUsMKFgoZyuFGTsN68E6lE0UkkEgxIGMZHkwODXRYHiz6qnPF1c3n16fZWtlLmXO4W+7BuFI+Mu0O2D3bQJaD1naPmD92VOOoxnPWvbLDBLDp1rh/ou8GXXD/Clzq93GqHVDG9uQVPNEvNvtuMknY+Qrses6xaaHpF3ql9J3drZxNNK3KThQN9hXKY/UuqKsRN1y6afqOp+OnuQ8xJIaP4SvNnpN8eQarxBacMW5t5I9OBklkUnnWYjDIfDYY6edcXgmMMgkAzjwpniTX5+KOK9S1m4dnluZnfLNnGTsM+4YH1UzHLge0cV6FgzW09O2EDQfPn8bo2CTJ7PJQOKtP9TuYtXSQE3Eit3ZX6JAB6+PSpttIdatlvWAhY5Uqu42NP31qusaTJbj27iMh4d8YPTHw61E4VuoWtDYlvx6M7cuPyc+dBw04p8VdGTaOUXHe6/wA99B1V7mgzaey8X9/NQbiICLOfHyqvlT2jvV5f2rxxkkbZqmlQq7VlYvTcN9iFmzMIOqgMPCkhctinWGTsKQozJiuSezVCFejvR37KOCuM+C7zVOI9JkvruPUHgRhcPGAgRSBhSPEmoHpLdnXCXBGncPPwzpAsHu5LgTN3ruXChcfSJ6ZNb30VohH2b3bMRhtVlPyjSqD0up0e24WjQjZro/1BVjW/mWvp+ytLRlXHuyPs11LtC4nitbGG1lhs2jubtbiTlUw94oYe/I2xXsyDsj7PLaccvBmgj2hjmtQfH31nOxHgyPs74OgtpolOqXn9kXbkKSjMBiMMOqgAHr1JroKTGa4Q/wA5ftqFQSTZmgCkGWXz+7SbW3teOeIobWGOCCLUbhI441CqiiQgAAdAKyyrlsVq+0sg8dcRnz1K5/3jVlkOGpTsGcXQ53XefRFjaPtFvXU7/gqX/eR163l1UW6NJPOsMadXkk5VHxJOK8meiTIF471Jz+TpTj5yx12zt2mVuyfiJkI5liiI28e9SrJYWPla3loiABa63T8YaIn8LrenD+ldx/8ANVkv4xedMMCMg+Yr5zLf3LyEYixnGye+voPDeNDb26DYCFB/sinrKKKIDhEm99xbp3p7A+yvI3arpsOo9u+p2lxHzxXGsQxSLnHMrd2CM/A1660nTbLQbWLS9JsYbKzg/Fxwwryqoz+v4mvKPGlwk/pC3BJBB163HyaOvW63kXreS/8AGftq2vbZkdun0CfwWBufSG4EtLiW3ludSaSF2jbksyRzKcHfPmK2XDnEllxbolvrelGY2lwWCd8nI3ssVOR8RXgbi6aReKtXEcrhDfz4AY/yjV617CLx4+yTQFLnpOd/8c9SqqSBsYMQIdfmfHuTiztANVyf0uLC1tuJdCe3tLe2MljI0ncxqnOe9O5wBk1SeivLHH2pW4dQwNpcDcZx7IOf1Vc+lpcd9xFw/k5xYSZ/0pqm9FuzMnaNLOchYdPnfPkTyr+2pMbaP/6n6qv+uy9TcZ8W/uX4Z1TXI7YXPqMBm7oty8+CBjODjrWM7J+1+ftQ1HULZtJgsY7OFZedJ2csS3LjBAxTvbNfQWHZbxEHkA7y2EK58WZ1AFcr9Fu8WDVuIAOnqsX+8pqenjMDyW68vgrm75V0T0nTy9lNwc5Pr9r9rVyz0c+1h+GNSj4f1G5mk0y8cxRRZysEzNkMFAySx291dC9JS77/ALL5lB29ftv+OvJumX0mmanDdRytE8Th1deqkbgj66VKGt/LlF2nQ93f7lU45ZO0veXabw6vHXBWqaB37WzzqHWRIu8PMh5goXIzkjHXxrlHoq8LT2FrrfEMrNG8kn4O9WaPBQoQ5YnPmcYx4V0ns141teN+ErPVhPC14qiO7SIECKYb4392D49a0Fm9ppsfq9nbw28ZYtyRIFBYnc4HiT41QWOjDorKeTXRc39IvtIuOF+GIdKs7iWC+1B8s0MxjkSFevQdGO3UdKyPoh8pTipj4+q/a9c09IHjVOLePbz1aYyWVji1h6gZX6Rweh5s5+FSuwLtJteCtWn0+9jC22rSRJJc8x/EcvNj2QDzZLAe6r8gycBo139/80UCQXgLu3pK6JqGudmqw6XZXN9NHqEMrRW8TSMqBXBbCgnG4ri3YTwJxVo/adw9qF/w5q9rZw3JeSea0dEQd2wySRt1r1EurxWpIlvbeIjY5nQftok4g025lSGPVbKWWQ8qolyjMx8gAcmqGNc0Wt1+KmYtblX2q3sMmm3gx1tpR/6Zr5zMveSbHGBmvft/MDZXKhv4iQH9A14t7L+Cxx1xdDpcs7QWqI09zIuOcRLgHlyCObJHWrKWMMB6afVQezUAL132UcOngfgTS9KjnabMfrMkjry+1JhyMZ2Azj6q4twp2rIfSE1DUJLOKKPUpm09i0/sxD2U5+bG+eQfOvQEs9pNpL6aGdYmtzbZHULycmfjiuP6d6N/DOmX9tfpr2sySW8qTKpjiAJUg4O3uq2ncGl5ePa0Ph/LK2zhsF0ftW4QsuOODb+z1Fp1Fskl5D3LcpEqRty52ORv0rxXwonJxDpWP/GQH/1Fr3fcXUN9a3FtIzKk8bxsQNwGUg/bXj/ijg+04E7U7PQ7O7muraK4s5ElmUK5DMh3A22qNIyxsd+SqlZbVe20uo1uzzdBJ1+uuZdlPAUXC+qa/wAR6nbRjUtS1C4e3WWEd7bRd4+Cr5Ozgg+G1bq7kjEsh5/yj9tUvEPEGn8O6TdapeyqsFvGXI5gpc42VSduY9BQ8TDlLW/1WV2S5VknFtpccRXOgxCQ3Vvax3cjYHIFdiqjPnsa5z6TN0JeyySMjf8ACFuf69Y/sB4kn4l4w4u1q8dfWLyOJ2KjA+mwAA8NgKv/AEj3B7MnOc/2fb/8dEGnbHILcrKJsWErybP9Lam02YGjmbLUletCPd+Ygr6rc8DthLzx2T7TVnxS5Og3HxT7apeCpCsV9nyj+01Y8STg6HcD3r9tel0RvhJd3O+q7CmmAwzL/td8ysVE2JfrFdYMo5vqH2VyONvxv1iuolwxwD4D7KG9GyHiT+dUP6OS5BJ7vqsTxNIP3SXH+MT7BW+kkUuxz41zriU44kuP6afYK3cjYmbPTNFYOc9ROOh+pROFzZKioPV31cpi4bflXJ8cCinnaKMu74RAT16Dxrn2v6nJBqt0gL4D9Oc+QqrfV5GBGOvmxNVVPpBSwPLDuP50Tzekgjc5gZtcb/srjiXXxq80cKIBFEzFG3ywI6mtfpRxpVl/iFrlqSFpOYnJrpWms66ZZHf+AT7KG9H6w1dRJI5B4PVyS1EkzzckfVZ7jqQtqEG2PxP7TTvAJy94CeqL9pq21TRbTWJUmnlmRlTkwuMdaGnaVa6P3htmmZpAAS7DbBoxuFzjEfWT7P7K5tLKK/1o2y/tZSeI2C6DfAH8gf1hVLwRcFFvR58n7an6/MPwJdqzAMyDAJ3PtCqrgxcpe4/mftqypP8A8nE0dPup1ExOIxO6NP8A+yt+KW59EkPgJE+2sdojBdTttx/DJ9tb7mUoUkjSRDg8rjIoIYo2BitrdCPFYxV1ZhLp6hs4daylWUXrM7Zs9rW5dCrHnAcvjxrlV8x9elYdO8b7TXRxMy7/AF1T8YziXRRlFXE6dABnrUMdoy+DiXtluUsYj9ZizF1stzturHQ7sfgKyB2Pd/tNUfFN5dJfxCBpwpiBIjzjOT5VYaDltGstj/Bnw95q0CzBcKWAokQGppGBrspIBurnNdUUrIrkaN1HgFgpPwpMpAivnyPzXNQXgubWVfWIJYeYHlDqVyPrrpYaUHeQj4tWU42kzNZnnDfi36HPjWBimFcCP1h0lyLfNY1dhjYoTLnJI6+K1NmWa1t/D8Un2CsnxXcGPV5Byg5RDv8ACtDDqNvHaWvNdQqTCmQXGRsKyPFdxHPqpaKVJF7tASpyM71oY1VCOjDozrcfJGYvMDTBoPMfIq64MlEq3hIA+h+2rPiXA0C4PvX7az3CN3bW0d1386xc3Jy83jjNWGu6zZTaLcwJcB5CV5QFO+9NTVLfwwue4Xsfqo0tS0UGQuHsu5jvWKkf8ZWv4MlPcXY/nJ+2sYxBfNabhXUrWwiuRcGT2+Xl5Fz0zXLej04FaC42GvyWPhcgjqWucbDX5FXHFb50cf45fsNYVW/G1qOItatLrTTDD3vMZFPtLgbZ99ZMP7eat9JamM1gyG4sFPFpmyz5mm+gW94VY/gpwP5Y/wBUVL1LTrfVkiS5aZRESw7sgZz55FZ3QdfXT7R4Tb97l+bPPjwH3VMk4pYHK2sQ/pSGulpa2jfSNjlNxbXdasFbAKdsch5aix+yuNLsLXR+8NqJi0mATI+dh5bVF4sv4m0poWdRKzKQviRnrVW3F8/QRWy/M/tqg1PUJL65eWRt2Odun1e6hq/GKSCmMVON9FTVYnGIDDDsdNrDVdFR/wCxLYg/xKf1RWI4rfOsXGfEKf8AZFPDiu6EUcYnhURoEGIx0Aqo1K+k1C5aaRgzEAZAxkCs7HcXgqKRsUR7QI+RVOI18c8eRt9x8vFRAAWqettzk+1j6qr8YIq6SMI2xJ881y2HxiQuuOiyoW3UqzUQxL7huanpJzjpjFRYgDEFYZ91PoMZrtqUFjQ0bLVj7Isoup6qdNeNVhWTnUndiMeFZqBO+nSMnAdwpwOmTVpxGCZoP6B+2lcK6aupalht1gXvSM4zgiuSxPjVmIilvoCAB0uBdByNfNOIhquhXUcmhaCqAmf1dVQFvZ5hnFYvU703193joIyyhQAcjatTxjrNumk9w0hEkxHIOU74IzXP5Z+9l5lY9Nq6jGa1kNqdp2tp/O5beP1IEjYIz2QBp5/SyRqkIjkEvNnn8MdMVAq7wk6qDhmA3yKaa0HdsAicxBxXIVeGulkMsex1XOOjubhVNHUn1C4/MH6Qps2c4JBTce+sc0sw/oPkVVlPRM0dONbSopZlwB76RynyqDontNnAhMRZFQNLABpXKvlUhESErJrFClMuPhSaqcLGxTIUu3lEM8bkZCsCRTdCnY4tcHDkkDY3V7cWqapaSXIbuzbqTsM84NWXCvBtvr01kJruWKOb6aqoztnofqqp0q5jWIRCUAke2hGxFaHg5tTsbiJTH7MZLxFWGfh+uu1pYaepkbNIzMXDW2wOlr2200WlAyORwLxe/wA1A17hW10XixdJEsstu0YkDNsxyD5e8V0C37PeHLeB0Ni0veKMtJKSR8D4Vm+KNM1nW9asr+zsyzxR8re2o6Mdtz5Gtja6jfzLIkkKDk/LAwF9x3oijw+JkkodGNDoSORHL4oyngjDngt56Ll3E2kWuicRxLbALbmVcRnJ5cY8T1rZ2UwZLhc+AKj66Gv8EajrMkl2bRXbDOuZAMdN+vj4Cj0/hBI7S2kvJ7pbjczQiQcvuAI+qiqGA000mQdlxBHd1UomOie4NGh1WNvLSXUdcnMZaH1Vt5cZBOdqsJNOhnijjkXvHBLNMThifu91X97apErxwRqkRbLHxyPPzqrlCRsO7csMbkjG9XR0jIy4u1Ljc/4296bgNFydbqul5Ys4AVV2wPAVUahqQRxyoCCuxJwae1HVYR3sSPl8kHY9aoJZGlbmc5NYOLYsGDhwHX5LPqJ7dlpRSN3jlumfDyoDZaIY8aMtmuTB3cUAhjzojS4YnuJBHGuWPhnFWEXD980iF4RyEjm9sdKIgo55/wDRYSOoBU2RPf7IVYoJzgVpLfhm3xHI8srgqCVwBuR51Js4bfSAyP7AkPMPyulC/wBXtmt2EUhL5GBgjxrrKDBqWlYZKwhzv09Le/W+nJHxQRxtJkIv0UqOS20+BbfvAO7X2Q3WqO81mS8VQ8SLy7+yTUWS5LOWZySfOovOB1qnEMbdIwRRdlo0t3aWVU1U5wyt0COZyzFsYz4UlBncijGGOfCjZlTGTiufOpzuKE31KMtyqTimzOc7AYpDuW+FIoeSoJPZTEoE70KAGaPBoax3TIAUCN6LOKUDUm2OhSRhcUfjijXBNaXh7QryPULa6ktwIMFuYkHYrttWrQYfJVyCOIaXFza9gTur6eB0zw1o/ZDhjQY9UtZZWuGi5JOXATOds1ruINUGh6Us8cKzEOseGONsddvhTiBIEY4CqoLHAx0FYvijiuHVYDZW0ZaEMriVsgkjqMV3lW+HBqAsD7SEWHefDX7LqC2LD6YgGzyPM+Gqq9f1ttbmila3SExqV9kk53z41CtoCx5m+j4DzoooZLknu0LY648Kso4O5QI2CR41wMMUtbMaifW/PqVzBc+Z5e/UpoDlGB0p0WvPGrlwASRjHlRi3eQnlA+eKPUFa2t7eFm5JPaZx4gHGK1RHlY5z26D3cwrA3QkjRNSwCNC3Nn6qjOMjrTokT1YR82WHhTJxg0FMWm2UclU63JNKOlS4Y+ZQc1FXrU6DIjHwNV0TQXaqLAtNwxoyzpNNdW0nL7PdMcqD1zjz8K0uo3K29k80pI5Rtg4Jp22Z10yyHgI1+wVk+LpSb0DJx3QOPrNemNDKGku0XXaStbh9J2BckDXx1+CjXPEd/zfi5FVB5jJqlW4dZM8x3O+9MzMSRTRcnpXD1mJyyPu4nRclLUySG7ySraC8lgLFCDzdc71caNevzAsQWLFfqIrMQtyMCdvjVjbtiQe7cVr4VXvDgSdOisgncxwcOS6BpuWZsgZA2rJ8R3Uml8SzzRNjvVWQgHHUb/ZV7BfLIiSLuDvtVVx6Hn0m3mUArHL7Rxvgjaugx8FtKaiPduoXRVchlpDlOrNR/Pep+k8S2d9bXGn3ugW2qXd6pjt7hnYTRuRhcHO4B8DWA1qxudM1CW2uoXhlG5Vxg1J0N/W72zsjOLbvJVTv2O0eT9L6qsuNNG1HStWMN5cC6KrlJlfnV0zsQd/lXE10orqXit3BH7rAlc6opzK4Elp3059efvSIGa60yJgNwo/VtUG6X8W3uFOcPzKrTwk7kBgPhsaVOOV2U+BrSMjZ6OOUnW1j4jT47od3bYHLovYHrx0XinRLt2zHHd+ruPJJPZ/4q9otdNHJ9E5U/ZXzo0TW30i4uFVghl5Sr/mEePx99emex/t8geybSuML63gFrCotr5wxabGxVyM5bGCD44NZVZAKynjki1cwZXDnodFaS2SNttwLFY/tr7FNYseIRf6Lb3urx6xNPcmO2syfVWL55CRnP0tjt0q17AuyvjLTOKLfUtRsrjSLXTJVmJvImRpwwIKIPHbOT4V6Ut78PEk0MgaKRQ6MjbOpGQQfEEU8ty0zYBLe7rQf4tM2MssLkEE9x7ttlUCWm4SudsqTnYivDuucMPF2n3mgMxRYdTe3Ut1wZNj8iDXpzth7YbLs30t4rSS3l1xmQJbzKSsSkc3Mw2zt0A8fhXLvRg0SfV+N9S4kuoXmhgt2JlYgqZpGBAOck+zzH3bURhRfSwyVDx2bA26229x+KmzTUr0zw5w9acL6HZaLZNK1tZR91GZW5mIyTufrrk/pUcUTaBwDFp8Ax+FZ+7dskYRMNjbzOPqBrrlzfMG5VwD768P9vPFz8XdpV+yGT1a0YWyKx6cgwTjONznp7qzsLic+o47zqNfEnb46+5VMBBzn+FYW3LBgT1JyamBsCmI4y5GBinnQr0r0OmaWM02RDBYKbYSA3UajO+xqi9TNhxVFEo5UMwdN8+yasEb6jTfEa+saRDMuxhfB9+dqpxeIS0wn/qiOcd4Frj6+5WlwLL82m6tdQXEJPm1Zu6XErA1faOpk4ctgp3wwz/lGq+9iKytzDwqeJA1dPHPa2YA+Yup1TLgPHMAqidOU7U2n8KalTKVxUYH8aa4Odga5ZLhqvW3o1Fk7M/Z/L1Kf7EFbHiHg+14i4q4f1i/KPb6Ms0iQkBhLMxXl5gQQVABPxxWH9HfmXsygJ6NfXJ/WorpTOQpLMFUAkknYDzqbW31RjGgtF0jijjKy4VsYJ3WOae4uYbaKAyhWcu4UkeJABz08KukvuW7VUyfxgH668kcV8cnjTtc0V4XDWFrqEENqcblO8GSficmvT6XsIvBjJPe/wDFU5KZo7I1I3SbZxIHJeIOO5C/GOuknrqFx/vGqgXrVvxjJz8Vay3nfT/7xqpgd6ypH9tAFd59FYhOKNbk/N00DPxmX7q6323XZHZZryH8tIl/9Va456L6ltf1xvKxQf8AqiuqdtmF7L9XJ8TAP/UFakIDrO8EZGPy7ryjaSf2UFPQuB+uvoDLdKwRF2IRR+oV897Yn1xf8Yv9YV7seUIcEnIA+ynd+bYHlf6KNOL3Xm3iPmm9IWVMZJ4ghH+0leollkN17uf9teUdXuki9IN5pJFRF1+NmZ2wAAV3JPSvT0N2LhEuInSRG9pHRgwYZ6gjrUjd7fDRWQi4PivEHEErvr1+7dTdzH/bNeuuxG4SLsp4fUkZ7uU/OV6qG7FOAXuJJp9DkmkkcuzPdyHJJyfGtZpui2GkafbaXpNuLa0t15IoVJOMnPU7ncmnLS4Frut00UJB1XD/AEqbhZeJND5SNrB/96aX6L0LfhjW7pASYrNI8joOaTP7Kg9q2r6VxD2l8LR2NzDfRQyRQTgAlQ3rG6kEb7V32x9S0wyx2Vna2iMxLLbxLGGI8SAN6mGkXbbbRM2MmQnosP6Q93LF2cTxnpcXkEZ9wyW/4RWL9GEc1/xAfEW0P+8NRfSI7QYtWmi4a0+4Jhs5C95gAq8o+iA2+QAT08TS/RhLm74iIDNiCDOBn8s0mnL2Tvb6pX/OC3fpDXAXszlRhub63I/2q8rWtheancNFZWs9zIBzFYY2cgeeADXp/wBIP2uzWVuUgevW/h/Srnno1SiHi7UpO9EeNOfcuF/LXxod7czwy/eoyszShq1fo2aBxNpo1G9vmntNJYdyLOdWRpJtiJApHQDbPvxXS+1HiROFeBtS1JHCzlO4g5gxBkfYbqQQcZwfAgVOOrwow7y/tsnYc9wv315h7ae0duNNd7i0cHTbEtHAeXlZ845mbffcbe6riwt7bzoFa78tu65vdytJKzM7OWJJYnJJ86RbztDKrjGVOd6bZsmirGdO4SZ2lAd6t21yV+saE+8k1s+xe/luO0/QFKRgCdj7I32jaua9K2HZXr9nw3x3pGq35lFtbSMz92nM2CjDYeO5rThxOonORzjqrWyOLhcr2Xc37LZ3AYfxMn9Q1519GNFm4x1dzjKacQPrlStxf9u/CbwzqlvqzlonUHuVG5UgdWrj3Y5xza8CazqV7c2styLi0EKrG4XB51bJJ+FFmnka9rA2xKIfbO2y9NcccSfuO4YutbjijufV3jXu3cqDzMF6j41ySf0l7yMHl0rSwPIySMaqu0Ltqs+J+Dr/AESLSZIHuGjKytcK3LyuG6Ab9K4l3mWOfGpSTNpvy5GAu7z9ilLMWGw1Xt7SNdl1bR9Pviqo13bRzsq9AWUHA929cB7WXb9+ezJO5Nif1rQ0/t21TTtIsbCCLSUFrBHCGaJmYhVA33xmsPxZxrfcScVRa/M1utzGIeUwx8qgx9NjUp4xGA8Ee4pp3AsFl7YllaaWTLDHMftrzr6Q/Hcd7dDhmxnWSK0bmuWRjvNuOQjp7PXI8TWan7fOMZCSNcZCTk8lrGP2VzzXNZutc1O51C8mMs9xIZHcgAsT1OBVDyyAE5gT3JpZQB2V2n0X15r7iA75EEO/+W1bL0h3c9m0oIbAvrc9P6deceG+KtS4bNwdP1C8szOFVzbyFS4BOM4+NPa7xnq2uWbWt5qmo3MZcOUnnZlJHQ4J670vyzDnzC/TmotkaIsp3Wcc70EODvRHc0BtWICc10KtjwaQ0d7gjpH4+81N4jC/gS4wy5yu3MPOsVDN3YO2c0JbgupXlArs4MdZHh5pSNSCPP3LTZiAbBwcvIi9+t+7vSIye8HxFdFk1Czjk/uyAjbo/urm3Mc5qWb1sfRT5UHguLiiz9/7qmirTTh1he9vgpvEF1FPxBNLFIskZdcMu46Cthc63prOeW6U7+CmucvIWkLnGSc7U4bqY/xhqyh9IDTSSvtfOb/Pv71OLEHxue4Ado3+f3UjXbhLnVLmWJuZGfKnGM7Cq6lsSxJJ3NFiuZq5DNK6TqSfNAyPL3Fx5oI3Ka0+g6+bXnWbvJVKBQnP9HHiM1mMb0pWI8aNwvEZKN+ZinBO+F4ew6rbvxOh+jaH65P+lNnior/esf1yGsfzn30kt7q33+lNRuPp9kacVqD/AFfAfZWuu6q2ozhiFUKoUKpyBSdI1ufTFkETopfGeZc9Kqjk9KPBrEOKT+sGpB1QfrEnE4t9VpG4pvGH90RD4Rio7cT3v/i3HwUD9lUeT50eM0Y7H6t2zvifurDWTn+s+ZWs0XiPvDN69enAC8nefXnGBRcR6taXmmiGC4WRhKrYAPTesqMr02pRZm6miP8AiCd9MaeQA3vrrf5q78Rl4RiOt+et1Ng1J4YUjEswCjGAxAFLOqserSt8Xqv5SaHI3kaBZiVS1oa06BB8R9rXUttRz1U/W1RZ5zI4YDGBjFIZSKIKaGqK2eYZXlRLiVIW8cKAFTamJpjI3McZPlS1hLDND1cmovfPI3KSl2iihuXiBCnGfdQlupJFKs2QfdSWhZfClx27N4ZNQ4lQRwrmybXZR/Gno53jBCsVzUj1FsbjFJNrjxHzpR000ZzN0T5HKPJcPIMMxI99NVJe3I3GPqoQ2rzMFVSxJwABkmqZIpXv7WpTWKZD7UfOcVajh3UWTmTT7xgBnIgc/sqrkTlpSRSRjVIghNl6LJPjR0eKDOYpknegpwacQYFSbdQX+qrYoC8jVTa26FuiMikqCffVr3Z64qEBg1LWRht4V0dG1rAQUZEANFLjYsAcYqQvWoMZywwaeV+uDmt6CXRFtcqviXJmg/oH7al8EXAtb65dkZ/xGMLjP0hStRslu4WkPOZI0PIB4mqC4gntQveRtHzjIztmuYruLR4j66W3bv8AC2p5IfiOgqBMBstxxnY97p8N0rgiBjlQM7NjesWTUzSb95Z1t36vyonvPvqZqelStJI4I509koPMUfWZcTBrKYa8x4ftbRPVO9ZcZmC3VVK3DRbg+6p1u7SqvMdzVeUYblSPDehgg7bVkw1D43drUdEG1xB1V2IQPE0DYhzze1vvVIST4mptvcqkaKS2QMVqwYhFIcr22Hj+yvbK06EKbcaWJLdwhcsRsPOq78CzAfRkqfHqCodwxqdBdiQEpny3os0VFVuvzVvCikKyIPKaPvB5Vf3tk9xcGReUAgdaoblHjmZZF5WHUVytfQyUe+ovbZBSRlibJoqOirJKqQoUKFJJTdJTmujn8w1sNDmdhg59g4BrH6ZLHFcEyNygqQPjW+4TMck9oWwyEt9hrtfRtw4VgddfotOg7lpNMtJJIbeRZAPHBHvq5GgTd3MFulQyjc8mcH50zNKltaySrvyrnAqZZ69b3NsjsREw9kqTk7eNdC9z9wtjVTLS3a1txEzmUmPu2Y+PvqBfWaxKnIzHmzkmpU2r2qWzuJQSikjasnxHxh3FvCbaePJJy0gwBtVbc3tHQKJNhcqJrLiATA9WbAxWG1rXFCG3hQ5bB5yfDNMcQaq91clhIHZ/aZlPXI91UWK53F8YcwmCH3lZFVWEksYjdzI5durHNEKNVoEb1ypafaKzd0X0ugJp+ztvWLqKNw4V3CkgdBU3TrSSCZmkAwVxsffVgzcgLdABk1t0eD52iWU212sr44b6uT0HD8FtMJY5JCRkbkVJeZoEY7HlBxk1U/hBPDmNVkr87sQDuTtW9JidPSMtSsAv0P7Io1DIx+WFY6jfvO0ZIUYB6VXsx3NM7nrtQOB47VzdTWvmcXuQb5C83KUSW+NJ5QfpUnvMZAFN1nPmF+qrunmkCggdRTbMW60mhVL5XP0SJQoUpFJYCnu7byp2QlwumTHNihz1M9WkwDy07FA4TdfGjWUErja/wUg0qu60BV7ZaVdX5YQQGTkALYA2zVpDwjqN2vci3ERfbnfovxxWhD6OTzNLmn4bomOimkALGk37lkFJGa3HBl3LNeLFNMzRrBspOw6VHfs01FOt5Z5/yvuqo1bhTUdEtmuppIu75xGCjnLZ91XUDK/CnGd8RLRvrb7o6KirKNwmkjNhqVecX8Q3mn6i1nb913LQgnK5JznO9YbBzS8sepJ+NOQTm3k5wobbGDWTiOIPxCfiSuIbfQb2HcgaqqdUyFztuQ6Kzs7ZIFyrMS4BOaltAzD6Jpi3mWWJJCVUt4ZqSWPma6mlbEIgG7K1gFkTQHuWB2yp2qqELi1iDgqwLbHr4VImJEjmo+WzvQFZIx7gLbafEfZVyEHkmSOV8e6jK7UthnNIIwMVlluW6HTQNS7Z9sGoQOKeifl+uq6aXK65TNNiul8M6w+pW8kE3dkW6IF5RjzG/wAqZ4m0y3msri7aNjNHHhSDt18qznDOuR6QbjvIXl70KBysBjGfP41ubcQ65palueGO5T3Erv8A9K9KoallZSZHausf2Xb0MkdbR8FxvJY7+Q+i5c8JI9/vqKRg1p9e0WTTbhUMiOHyQQMbZrOyRFSQa4zEaF8LrELkKiB0Tyx4sQmwxA61YQSEgMCCSKryuKftZAjDb3UPRTFkgDlUw2K1egTvPHJC7AlMFRjwPWreWzh1O3NncqzxsRzKDg7Hbesrpt8tjc96wZl5SCF61rracIQcdR1r0rDpGVFKYX620IPRdHh0oczKdeR8FzW+gFhqc8IXlEUpUDOcDO1XVhxLPaafPp0qJcWc5y8TAAk+GG6io/GGnS22pvdkho7py6kA+yfEH31WQsHXpjFebsdLRVUtPtqbDu5d2yxHufSzuDDbfyP0spT3Fjb3yXFkzqp2aKQZK564NTr2MCYH84VRTRBH5ydmPQeFXtvKt9ZxMTyso5TnxIo/CpeJxadwAPtADbv8xZQY/PcWsqu4gDZKrlvPNHa6pcWJWMEFFP0f+tPuATTLwI2TgA+dCyRyRv4kDspVViDdq6JpvbfxJoulwaXp3Ed7BZ24KxRxqDygnOASM493hTGodt/FeoW7W8/EWrzI35HecgY/EYNYDuD5igtvhg3NnHuos107jcRsB65Rfx3VnHffYKy1HVNU4nvo59VvJ7lwoQCSQvyqOgyd9smvbPYxw+nA/Z3p9vKHFxeAXkqs2eUsBgDy9kL9deV+xbgRuPOMbewacQ26AyysVbPIhBbBHQnoD5mvatwylwqj2EAVcnOwoDEHjhtYdXO1PgNB5m+im0aXPNZvtJ4xm4X4K1nWrTu1ureHMXeKGHMSB08dia8tdkXZbc9qGvzlry3t7S1KzXZdiHZC+4QDxO4z0FdA9Kni60WzsuFIkl9b71L2R8jk5CrAL55zv5Yrfej/AKXo2kdntpcabHILm6dvXZZcF2lU45QR0QdQPf51GHNBAXsGunx2+ANksuunJam07KuBdNtYraDhnTJFiUIHnhEkje9mO5PvqQvAXCCkcvCuif6otUPa72qQ9mugRXUdnLdXt4WjtsrmFXXBPeHIPQnAHXFcl4c9KHXL3VYDqFjYCwWQesRwQESFPHlJbrUaanrKkdh5ub2BcbnwTtu45QdVV9r3Ys/AcNrqen3kt9aXc7xODEFMLndVABOQRnf3VzBY1mR7SdS0TbFenjXuqx1yDVNPtNRtQ5guY1nj51w3KRkZHga8U8d6bLwxxbqmmzsHeG6deYeIJ5gfkRXQ4RiLpGujqdxv3g6EEfBWNdb2lQcL6hcG9XS5GHqyBzycoyCPf1q41e1Qs3KhwFyKzfrP4K1uO/PMY3JLKgAz5jetXHdpq2mpcxqYwwY4JBI9xrQwKRropKCZ13sJsD+nS1u5F0zhJEYnG7h8lj54+gxUIoRKT4Zq3ukJCgEVWTKVdvjXL4lThrllSNXqfsEd4uy+wYlVU3VycswH5Y86r+37jabR+HbfSrRrd01QOszq4Z0CFSAuDtk+JrzjDqssECwgZRc4BY4+VRby8a4C5RV5c9Kq4sMMYeHXcOVvr3KfGAZYbq14KlReNNEluJljQahAzyOcKo5xkk+Ar1meMeGLe9QvxLpAUSZJ9ZU+PurxbHK0UiuuMqcjNSzq90fy1HwUUNSVULGu4hPuUIJhGDdOcR3Edzr+pTRurpJdzOrL0YFyQarV60bHJzRdKx3Pu66HK6j2J8eabwNfatcX8N3MLm3jiRbdVJyHyScmtR2n9s+mcTcH3Wj2NlqEUk8kR7ybk5QFbJGxz4VwlJWTPKzLnyOKNpWYYZ2PxNasdbE2LLl7XW6ubNZmVP29wEnVyMgMG6+RzXoS99I8XDho+HIU2Aw16d/9mvOIODSuempK1kd87c3vIUY5SxaDi3iE8QcU6jrHcLAbucy92rc4Xptnx6VuuCO2nVuFuHl0u1h05kWZ5A1zzFhnGw3AxtXJdyaUCfKroK/I43bdp5JMmLST1Xa7j0hOIXBxJo8f9GDOPmarLzt34kuLeaH8I2iCRGTMVsAwyMbHwPvrk5Y4pJc1e/FwPZiaPcp+su5BWVlq89pqdtfrIwmt5VlRupVlOQd/fvW0uu2fie5jkR9dviHDAgKozkb9B765vmjBNCQ4q+O4sDfqLqDJnN2TkszSHJNTtH1+90VpTaXE8PegK3dSsmcHxx1quCk0CpFUMqJWP4jTqqw4g3CtNS4kvtTtzbz3E7oWDEPMzAke4modlePZyFlAbIwQajUoITUvW5nSia/aCfO4m6s21uY9I4QfeM1WSuWYk9TRFTShCzdBUqiqqKjR5Jsk57nbpo0KcaMr1oghY4ArPMRvZRSKWjlGDKSCOhFK7hsdKQVI60+R7NU6cNzKQQZHI+NI5zSKFI1DzuUkotnwosZoAZqbp+m3Wo3C29pbTXMzZ5Y4Yy7HHXAG9SY0yFMNVGDMBRHOc1p4Oz7im4/g+G9Zf4Wcn3VYQ9knHNxgR8Ja02embVh9tH+rOtqVLhlYgk0WC1aXijgLiPhBLZte0a7031rm7n1hQO85cc2N/DI+dQuGeHrzibW7HRrERm6vplgi7xuVeZjtk+Aqk07ie5NlN7KqCNQ7tjXcYPRW44O0kuiR/G7J+xamReijxOzAS6zocY8cGVv+GiBDFbV4VnCK4F3LUXdkGvSMHokai4/HcWaYh/m2sh+0iuPcVcFvwzxlfcOPcpcvZ3ItzOqFVfpvg9OtSZSxyHLG65TcIrJLExo+4Y+FeoU9EexgYCbiu6bz5LFR9rVYQ+ilwuMd5xFrDe4QxCmaKcDV3wKkIl5P9Vbyomt2HhXrr/sucFxjfU9dkP8AjI1/4aw/bN2M8McDcIxapo7ak1015HAxuZw68pVidgo3yBVjY4JCGMJuUuD0Xnxbd28KcFk58DXXOx7sivuN9SttSktoJtEtLxI73nnCM42ZlUdTtXpIdj/Zpase74Q02Q5/L52H62pp4oIHBjrk91tE/CtuvCXqhHWkvByj/rXvaLs54EiHLFwdoK+82qsf11mu03sK0njjRbeHQrfTtG1G1cmIxwLHFIGxzB+RcnAG3vqnPATaxCRiXi1ICxqTHpdw/wBGCVvghP7K3/ZXwDHxV2iwcP3l2bdImlkeSNA3N3WTgA+fL1r2w4A2ijiQeAVFGP1VbM2OnIaRclM2NfPOHhrVZyBFpd9Jn8y3c/so7/hbV9Mt1uL3S7+1hZuVXnt3RSeuASOtfQhLl4m9qYr/AJeP21U8Z8LaPx/ojaLrizSQl+eJ0choZMEBx5kZOx2NVCdl7Fminw14G0nQ73WtQg07T7aW6u7hxHFDGMs7HwArXSdhnaHHC8zcIaoiRqXZnRVAAGSdz5Vf9m+mxaL23aZp0LtItnrDQLIwwWCMwBPxxXrm/mZtPvFKkg20o3H+DaiaxggLWtANxdLhAL52dyecDHWuicB9iHFHaBo8mraOlgbaOdrc9/dCNucAE7EdMMN6zWiaJc8Ra3ZaXZoGuLuVYY+Y4AYnGSfAe+vcPB/Bul8C6KmkaPC8MHN3snPIXLylQGYk/CmqGNp9tSmbHzXkLj/sQ4n7PdKt9U1c6eYJ7gWyi3uO8YMVJ3GBtgGr7gb0cda4z4asNfh1rS7W3vQzLHKsjOoDFd8DHhXYvShK/ve6efEapETn/FyVnuzTt14T4V7PtH0W9g1R7u0jdZDDApTJdjsSw86UUU00YfE25vyUgy5VXbeiHfO347iywUfzLOQ/aRVgvojWUf8AD8YTHbfurAftauqcDdpWmceW13c6VDeRx2sixP6ygUksMjGCab7Q+0Sy7PtKttSv7S5uormf1cLAygqeUtk83htVVqkScLY9NFLIuG9pPo86NwVwPqOvQa7qF3c2nd8sTwIiNzOF3wSfGsP2TdkOq9oOt23NZXo0NJgl7fQgARLg9C2xPTpnGa6B2mdvGlcX8Hahodpo13A12qDvpbhDycrhvogb9K6d6M9wkfZXEGzn8IXP1brVk8U0DC6Ua+76KLmAFVcXovcCQfwk2uy+43Sr9iVPt/Ru7ORyh9O1KXHUvfPv8gKm9tPalddm1tpc9lYWt2L1pVfvyw5OQKRjl881kuyzt+1XjzjC20S507TraCVJXZog/P7KFhgk+6oMinfHnDtLE79O73KVgVR9rno1NaxrqvAtmHtI4lWaw75nm5hktICxxy4xtnNY70cuFNH4o4+NhrmnQahaiwmlEUwPLzjlwdiPM/OvV9/ec1heKPG3l/qNXlz0ZLl4e0gFdydOuB+panA9zoXh24Gh9xUS3Vek17KeA7cYi4O0QY87bm+007F2f8KQnmi4U0RfhYoftFNdoGsXljwBxDe2Uk0N3BYySQyRHDq4xgj315o4H7Qe0HXeM9HtJNU4guIWvIjLEHlYGMOObm2xy4zn3VTT08krC50lrdbqQtsSu6dqPYzovHWhR2+nW9npWqWnMbR4YliiYsRlZOVckbbHqDXlvs7gbTe07QIGKs8OrwoSNwSJANq9vzyn1hMsqBpMLkgZ38K8PaJKLPtUs3YkrFrSE8h8pvCrKO+Wx12+N0zm2sV7e1K8muLe7HMwTuZRgHH5Jr5536IjEAbV9A767SKzuwcZ7qUbf0TXz81BgzZHjVcTQ2J2nT6qMosFX9TSwopON6WBtQLRcqgJcaBlOafhADbeVNwplfrqdZ2hmk5AwGFznFalJTue5oaNVfGwm1klU5qe5cVOtdIMkyJ3inmYDpUjVtM9QvHhDBk+kp9xromYdKGZ3BHNpn5DJbTZVPOQAAaeiY+YNMy8iSBQ65P5IO9KiIXORVEbi19iVW06qYknjTdzZQ3zIZVZuXpg4o0OelPoD4VpBjZm5JBcdFfYOFistJZXEIZ2glRAepU4+dKtdTubOMxxMoUnO653rU3duL61a3Z2QHByPdVPJwwzP+KuBy4/LBzn6q5qpwKqpZA+hJItvcA+G6EfTPYbxpdhqOmXaldWVxIDlZI9gR5ECivNOjcmfTyZbYjIxnK+fWqW6gNtcSQ84fkblJHQ0lZpVHKJHC+QY4oD8WOXgVMYJHMe1cdTzVZmBGWRvv5qU8Uqnpke4U2xce405HfYXDqWPnmmJJueRmGwJ6VTLJCGhzHHXl0VBtyS0mkBwT+qp9teyouzgb+VVne+6lo/MM1KlrHRu7Lk7Xlp0Kv4rsuDkgnwNV+ooks7Mw9rA6UiC6WNTlSc+RqWhW4i5sYO+K35JhWQ8InXdE5uILFUXUUVS7uza3UMXVgTjYdKi4rjpoXROyPGqDIINijC5oj1ox9E0FGRmmy7AJkmrnTOJ9Q04x9y8Q7oYUtGDVOaFWwVMlO7NG63gpskcw3abLUTcf61NG0bTW/KwwcQim4uONXiGFmgA/xIrN0Yo1uK1J/rPmrPWpf1FaWbj3WZUZGmh5WGDiIVT3+q3WphFndSqHKgLjeoYGan6Ro1zrN/b2lsjM8sqR+ypOOZgM4qbqupqG5C4kHldMZ5X9kuJUPuJD+Q3yoGCQfkN8q9P/8AZaiQ8q8b2PNnBBtTkH9Klr6KrOf/ANa2OPfZn/moj1ajtrIfIqfqzf1fBeWjzJsRj41Os7aKSPmkXJztvXWu1r0fb/gDRk1ybXbG6hadLdYkiZHbmBIbc4xtXIxILMGJgWOc5FQp4mMeZHdqPkT9lXlDHa7KbJKU+iRmo0l9IwZQRjGOlNySpJECPE9KZLDB2oioq3k9l2ik6Q8kXfMNs/qpBc7knrRFsUkkmsZ8p2uqLoy58KSTSxGfOjWIscZ+uqzHI5JNUvujmpcemMyhu8UA+6rA6dgbOPlR9NhM0gJc1WthcVWRW8bKOYEtU+y022kDmSMnGMbnapsASCNUZlyOppU17HABtz83kRtW/TYZTxASS207kSyFrdXJqPTbEOD3fT+caf8AULI9Iz8zUSbVYuQ8sTA+eRUUar/S+dXGpoIjlyt8lMyRN0sFdJBZgY5Dt8aS80EJ5ESPlG/tLk1THUSfzvnTbXvMd1PzqTsZhaLMA8kxqW8gtJZa1NYhzbNDHz4DYQb4qbb8W6ks8YM8bKWGRyDescLsfmH50a3iq4bkOxz1p2Y9a2quixSaMBrHkAd5XQ/3RzPuUh+dWN/aafq1jHDfJ3iHD8qsRvj3VzMaugH8C3zFaOPjyzjgjQ6fMSqhc848BW0zG6GZhZM4W8Cb/BbdHjbZMzap9weov9FPm4O0F/aitpAB5StvWd4m0SwsdOE1rbvG/ehcsWO2D51qND4hg1pmWK2eHlz9Jgc1J13S31nTnsxN3PMytzcuenhimqsLo6mie6kiaS4HKQAD9LIiahp6mndJTNF7HYW1+FlyQZQg+I3qwh1SRmYzMPdha0T9nhjikkfUchELbReQz51j+UgZrzyakrcMc3iDLm7wb28D3rk56WektxRa/wDOStDJ3g5s5Db02wBqJFcmPZssuNhnpUiKYTZwpGKJjqmTWBOqqDwUD03pDdKdbdcU2RjNRkCiQoopYNN0oVmMcqQpUMnhmtRw7r08FxZ273fLbBuVlYgKBv1rIIcVIhlwwOOhrdwzEX07gR/NUZS1L4Xh7CusTDTtXVedYbgIdiDnGa5/eaXdNLJy2U/IrE/wZwBmpGha82nmXMPeBgBjmxit9E8U0WO8HtpuAdxkV25ZDiUQINjz0XVBkOLMDicrxvYeWvuXJZrdlYoUYe7FNd0yeBFbPVOGHginuvW4ykac2OU5OKy7oWGDmuWrsMMD7H3LmqqikgdlkFk4hDhTkbjpWs0+YT2UTc2WChW33BFZCBCpA99aDh+RRJLCerAMPqroMAnLZAHc9FdQSZZLdVJ4ptPWtDISJ5pY3VkCAkjOx2HurCRP3cuDt4HO2K6rbZUgdMeNcv1a1ey1G5gfOVkbGfEZyDWX6Z0pinjrGc9D7tR5j5K/GYR2Zh4H+fzZFcjmjzg5Bp/RpwjSRM4AO65ON6QmHQHzFQ1zHIM/kt9hrmjMaedlQ3VYodlIIVxcIUkO2x6U0alXu6o3gc1FrdqmBshAVzhYoqJiQpwMnoMUZqy4W0q813iG00+yjeSaRwiogyWZtgMUOxud4YDa5t+/u3UQ3MQ0c16X9Gbh264f4TvtXuopIptQZYoVkiKMY1GeYZ/JJbw2OK6s10Y0Mk0ixxqOZpHOFUeJJPQCmLa0bSNOstLEjyeo28dvzN1PKoGaxna5xWOGuAtVuJEkZZ4zaDlUNytICoJB8POgJDxpS7kfkNPkirDy+i8xdrPErcV9oGp3veySQpcGCHnkD4jRsAAjYr1I9xr0F6OeqRy6Zq+kOWLQypcp5AMOUj5gV5KfLqG33rsvYjxjZ8J6tDqeqSTLZzWr28ndrzHmyMHGd8Fa0qJnrEM8LRqQCP8A6nZQpSX5xzOq7l248LniTs41GGO2nurm0KXVskClnMinHQdRys2RXjjT5pLTU0UjkVzyPzCvfmma3p+q2Ud7pt5FdQOAVkjYHBxnB8jv0O9ZePst4FjnMw4S0hpC5cu8XOSSc53JrOp6h8DhpYtN/uFEsOYOGhCf7NNTfUOAdCnliKOtsISpXGeQlQce8AGuD+k5osdpxpZ6jFEETUrMM5H5UkbFWPxxy16UaRYsk+yijbPRQP2AV5P7ce0mLjbiCKxt7aOODSHnhSZWJM3MRucgY+j+uroXEzOlAsDf48lOUaFy5zfkz2GFHM4YbDc1ZcKX8XqM9nIVjdGLDmOObO2PjVba577r1FRRD3Wtwr1DTI3zNaLqmSnqY65gv/QR4qMMpjeHjwWju7M8ikRkfVVLdQlHb2Dv7q2WoRMyjl3IJ2rOXq+1JjrXQYxQsIuP5ojKynDDZUckRXGFNMuDg7Vcx6fJdW008ZX8SQCvic+VV7xkAknPuriaqjc0XtoVmPiIAPVQSCPCk1JYDO1NMuBmseSGypITdDegaFCkJkKFChSSRinEjLHYE5pCDJrtvo89l2kcaz3msatcXKppFzAyW8QXlmY5b2yQdvZGw65ounYD2ina3MVhbfsn43n+hwjrpPvsnH2ip0XYj2gykBeEdWGfzowv2mvaV/q8dpE093dRW0OcGSaQIoJ6bk4quHFvDwZVbiHSeZiFC+uRkknYDrR7b20aiOCF404g7I+NOGdJl1bV+H7mysomVHmkZNixwNgxO5rFSIUODX0J1ezsdUtJdP1KygvLWbCyQzLzK2/iK8H8aW9vZ8U6xa2kQht4b2aOKMbhFDkAfUKpmaHMzHdVyRhouFR0ajJpOacj6igYxdypXQuyfsjvu1O/vrS01K1sPUoVmd50ZuYM3KAAtbDj70Z73gXhC94jn4jsrxLTkBgitnUtzMF6k++r/wBEXI1riIj/AMHAP/UNdL9IGSb96nXUJPLzwED/AM0Vp2LZBGNtEQGaXXi0WZaXkUFjnAAGSa9E8H+i7pOt8LaVrGoa7qdvcXtss8ltHbIO7J/Jy2/zFZb0f+B5df4xj126icafpMgl5iGAlmH0VVhtlThiPKvV/rLzMdySd6lM0M0YnZHpdeOe2bsp0zs51fS7PTb68uo7u2aZ2uQoKsHK4HKOldT7NuwDgniHgvRdY1Aam93eWommCXXInMSeg5dhtVB6VHMnEmh77iwc/wDqmo/YN2rXdlqMGh67q0507uFtLG37rmVJS45QMDIG53NEMpnPjDo97X8k7WjNZRu3XsXtOEWs7zhfSNTbTFtWkvbh2MyROHwOZseztWH7J+ArnjXjCxtUsGu7CCeOW/JOESDm9rmPhnptvXsLifRLbiXRb3RdS71rW6Tu5RG/K2xyMH4gVz3sZ7N7rs8tNSn1PuTqV5KFDQTF0EAwQDsBnmyarjIyEncJ+FqtVD2O8AwSAQ8H6URzbB0Z/H3sa8f9pthbadx3xBaWlvHbQQahNHHDGvKsahtgB4CvSnbl2iw6BwrNpVvdONSvSEAt5wskKggkuBuARt4ZryVf3Ml1O800jySOxZmYklifEk9aoljLYszzqeX1UJgALKJQoUKyOaoS0GTXoH0UOHbS94i1PXpZp0utKhVIUQgK3fB1bm8dgNsVwCEe1Xov0Vj3UXE0mcE+qr/vDWrTMvE4qyEXK9BXU0duQ090sYY4Bkl5c/DJpo3cbAPFKsqnbmRwwz8RWD7XLqM6TpXORkXb4z/izUns9uol4STlwP7Kl/ZWoMP/AOVFRfc2stQQflcS65r6Vdz3o4ZHkt1//XXNexEA9qHDBIzjUYsfOt96T8oc8OHG/Lc/alYLsRBPabwyB/7xi+2qo227PcVnu/1LL3WSjgkkVy269ITgCMlFl1SQgkHltMdPia3feukh9roa4SPRatJZXkm4yuk52LER2S7ZOfF6FpI6cXNRfusrALcrrQX3pKcIRKRBYazKc7exGn2tXnniniiHirtE1DWbeKSGG7vVlVJCCyjK9cbZ2rddq/YLpPAHB8mu2nEV7fzLcRQiKWBEU85OTkEnwrkWixBdQg3zmVP6wrQgMZeOA0jrcqDnkkC1l9DZ76J2wSMgDx91cX409Im14V4l1HQzoBnaxnaEytd8vPjG+OU4611C8KQTyZPj+yvG/bNOD2mcSMPG9Y/qFCUMMQJMjbi3f9FY45NV6Z7MO1Ido1tqVx+DksUsnjQcsxk5+YE75AxjFU/pGzxzdnEeCOYalB/VesZ6MUjDRuIceNxb/wBR60Xb+S3Z9uOmoQfY9EtiYKoFgsLj5BSGrcylejCQnBWr+1j+2X/9S1cdt/GGrcF8K2moaJdLb3Et6sLuY1f2ORjjB26gVlfRxuOTg7VFzj+2Wf8A0lpXpIz54EshnP8AbFP6jUz4x62XO2v9EzhYXWT7O+2XjHiTjvRNLvNbeS1uLtUliEEah13yCQM16Ytbxg6b9SK8Wdi2G7T+H9/78H9U17AhlKPHy77io17GvLS1oGnIW5pmdptyvGFnxbqfBvGN5rGl3L293HNcRq6qrEBmYEYYEdKv7/tu494wsZtAW6vb8Xi8hggt1LyY325F5vDO3lXP+IpSdZv/AP8Aky/1zW07A9QtNO7SNJu7q4ito4mlLSyuEVfxTAZJogzkvysAuL7gE6DkqWSOvlCPhfhXjnUtasV/c5r5xcRsxe2lCgBwSSWwOle07oJFM7syhFYsWJwAM5yT5Vk37Q+GudV/dJphdiFCi6Ukk9B1qB2iadecW8KX2jWGpTWFzKp5WVsLKcH8W568jeOKEnM05bxdLdyvDCN1wHg6eM+kHBLHIkkba7KyshyrAu+CD4jBr1jeXkRsLkDB/ESf1DXjbsy0q60Ptj0nTbwp6xa6h3MnI3MvMuc4PiK9YCQ8rIyghlIPvGN6nWsEhae5NGMwuuSejZwOtrHLxhfRcssnNDYhgysq4w7+TKwOAfca7lJcMPHHvqgiurPRtM7q2hWG2tICI4k2CIqnAGfcKyPZNxaOM5uKdaT1hLea/h7mKduYxIIthtsPqqE0bpS6Z2w+/wDCp5bbqu9Jy4L8B2QDZxqcf+7evKUskiycquwGegNepvSRnSTgW2OBn8Ix/wBR68rzt+N2pwcsI1sh5tCvUHoohG4a18O+4u4Tuf8ABmpvpPmN+z20GQTHqkZ+cbis76Md4Y9F16Mf+Ihb/YYVY+kY5m4BiY+Goxf1WpGP83P4fJWZfy7rzKjfjQPfXrP0eppB2aRKrYxf3H/DXkmP+H+uvVnYFOI+ziMDr69cf8NOLujN+qrg1KovSlmkfS+HQxz+OuP6q1gvRyITtT04t07m4/3RrZ+k5Oz6XoBOMief+qtYLsAuAnaVp52z3U+2f8GalGBbIeh+qc/6tl69uXjNtcAuN4JBj/INeW/RvmSLtKiLMADY3I/2RXoibUFaGbJx+Kk/qGvMPYM7HtDtlX8q1uBny9ioRMygtPP7FWPFntC9fjVETdJF299N/hcO4C3MSsdhhwCaxXFkDtwfroQM8h0+cKqjJJ5TgACvL2laFxfPfWyw8P6tIxdSoSzkB2IOc4pMpoL9t1vddScWtNivWPahpWucRcHXWn6BewW90ysXWRMtOnKfxaN+Qx8/q2rx3YW95o3FsEN9BJbXNrdIJIpV5WjZXGQR517OuZ3LeyCB4715B7UJZo+0zXnjyZPwg5UeZyMUmP4Ibm2uoTDLYr13q00nq144bK91Kc+7lNeF7ls9a7hbaX276hBJAkGrASIVKyXkS5BGCMFvKuGXUUsUrxyrysjFSPIg4NVVhaxlmOv5qFQ4G1k2uCN6WKbUHFOjpWdGqWp+33T66uNCjWa+EZkWMFD7TdPCqaI+ycedKS7aJ+eNirA7EVtUVUyBzHu5ImGQMcHHVdDtdPs4pUf1oM6nI9oAZpniPSbTVMc16lvOmx5mGCPeKqNNv47w7EBk395A6nFUuuam2oX80yZCO22eprscQxGnZS5suYO5Ldnr4RTluQEE7XKkppVrZXMhN9BOxjYLysD7VNhPKmLSIRoHYHvGG+fCpcYJNYMOVzRlYG+f1WS2xHZFk5GNxUlB4Uyi4Ip0HfFakQyhEN0TwxnrQ9Zt4TiW4iRh1DMAR9VUTcSEZUWwBGRnn/6VV3l097O0zgBmwNvdWbU+k0ETAYO073hUvrWt9nVO6reW13ctJb2/dAsSWJzz+/HhTAs7hlDiCXlIznkOMVZcO6daX8s63WcIoKnmxjer2YRXGkP6nc98kCsNs743xWPR4O+va6rmcBmuQBa5sddNPhdUtpnSgyuO9/gsrZ2feB5HP0MeyR1qPIB3rYG2dqclaTmZuZhnwBpMSNMeUeHU1jPY0hsTG6/NBkjYJvlwOlAEr0p+RAFNMYzVErCx1gobJyN9tz86kwSspUByFz0ztUIbU5G/hRFPUEEKTXWVvNFHcqFWQbHO29VE8Yik5AwOOpBzUm3m7lyfPaohw0xHm1FYjKyZrXBtnFWSODteaSKAOKmC2Q49/uovUB+f+qhPUphayhkKi4zuKLBJwASfKp0Nv3Lc3Nnbypwuo3GCatbQEi7zYpwzqq7u38Ub5UoIR9IEGphlA8KYT8ZdHJ23pjTtjIsb3Ki5oCXY6fd6jci2srWe5nIyI4Yy7Y88CvTnA/CWk9lE1xc3F3NatfxRqDfFV3Xc8px5mi7FOyzQ9P7PYu0V7q5/CL207FXYCKMK5XAxvvgdT41b6baR9p8z3GtIkcGnsFhSPfmLbnmz8BWrh8EYDnvOgNj3eH1WnRwsDS93+FaW3Euh311HFDq1jJK7YVVkBLGr+3a3JPLIjH3EGqqbgDQNPtJLu3toYnhXnDLGAR9dZfVZr/S4WvdJZHkgBcxP0mUdVB8DRwjjm/0ifei7Nd7JWu7e9IseIuC9JEzCWGzkEspjmCmIcnLzH3b15Z454Dms723fh/TtRvrFrQTSXEUbToGy2fbUYGAB8K7lrXEI4k7M9YvkjaMS2UmUY5IIxVf2AXWra7oOr8PQXXLDNbTpGjuQiOUI5vdkkZoaajDaR0J3brfuQ74WlnDO+915lQ4GKDH31ecZcIX3BGv3Wg6lJbtfWhVZRAxZBlQRhsDOxFUXduTgKT8KwHFwFljkEaFEBTicoA6ZrVaH2XcW8QafDqOm8PXt3aT57uaNRytgkHx8wRVsOxXie0WK51fSptOsTKkck7shMfMcA8vNk70XBQyOILba96ujp5HkBo3WEjRGcBzhfecVJQW6bBkx8a3Vz2Ha73xW1v8AT50zs3Myn7DVdxN2Ra7wroMmtX9xYtAkiRckbsWJY4HhRfDmpwXOj96Lkw+phaXPj0Cz6MoUcvTwpia7y38Lt4b1DNw6jk32GOtNAGRgDhfjTzYkS0NjCEM2lgpD3B5yQ4IpqWZ3xhvlTi2DeLAfVSks8EgMT9VDOZUPFiLXUcrion4xzyjmbPh50sWsuP4F/wBE1Z29osTq/NkjwxUonPjV8OEZm5pXaqxsFxqqExypt3bj6qNQ4HtKQauHi7zGTjFRJ4wshGc4quTDTF2g7RRdFZQmJHgaQWqWye+mWTqaDkicOarLU3ze+j5vfREUmhy4hRU/TtTutOyba4aEnqVxV/onFN818Fu9QJi5GOHIAz4Vkc0atg5FalDjU1M5tibA7XNkTBVywkZHGw5XNl1Qa9Y3cJikubYlwVK94N87YqMugaOo9nT4PrBP2mucpOVdWAwwIII860uncWzQRMt1E1w5Ynm5wNvLpXX0eP01Y61WwC2xtdbkGMRSm1S0ac7XVXrej3Q1a6FtYTdyHPJ3cR5ce6qua3ntX5Jo5ImIzysCDiup6bdpe2kNzyFBIvNy5ziqPX+F7jV9Q9ZinhjTkC4fOcj4VnYn6Kdg1NIS4uN7WA0OqoqsLzN4sBuSb27isdDOGAQgDA6k0ttxkU3qVm2nXs1qzq7RNyll6GkQyMfY8AK5dszmPMMo1GnksY5mksduE14UdCiJoHZVJaGlBiD1poHFLDb9KtZJZOFKimODvg+NW+mavLBfW8s88rxxtkqWOCMVQBsb0+kpyBgVsUVe+JwsURDM5jg5p2XS7DU7LWU7oHPeAgxuOopvV+G7c2fLY2Uffcw3XYgeNYa01SW05TGACpyDk1pdJ4ynml7q6iTlCE84JyTXcU2K01UBHL7R02XTQYpBUs4dWNTpe38soU/D97aRvNJalVQZZuYbD51Gt7hrWZZEJUg4JHiPEVto7uz1CPujLG4kHKUJwT7qgz8H20jkx3EsYJ2XlBAq99AWEOgTzYKTZ9Ecw8Rv8E4kmSCM4PSshxvZql5BdKoHfKQ582H/AErYWttLEojlRwYyUBYY5gOhqNxLpy32jyrkI0f40Hlz9EdPrq7H6P12gcwDtbjxH7XCVXA6Wnc22u/vC57aHKFSelNXacsuQNmGaVC/dvnGebA607ejmiz+aa8yLQ+ksd2/z5LkdwpWmTGeFo5WLsh25t9qEgw5HvqBp05hnC4GJCFJ8qtLheVgfOtWim49IL7t0P0VzTdqjlfE7V3D0X+EotU1+bX3UBtLQyA8xyZSSEOPIDJrhlzkxkA48a6h2KcdpwPenUXhnubeSGS3lgjcKWJOVO+2xq2njdM58UQ7WU2+F/hce9XU7S55A3svUxu3eRndiS25J8a86elBxELnV9O0WGZykMXeTRq/s85ORlfPlx9RrYXXpBQdwUi4c5T+fJd9Pktec+K+IZuLuJtQ1qZDGbmYuI+cuIx4KCfAYqipo5qQfmssXaDUd19u5TqWmNtjuVXsAVwKTBe3FnMrKzMq7chJ5SP2UY2omAYYNDFzwQ5hsQgtRqF0bgLtU1ThueztrO+mW3adZp7JQOSU/lAkjxAxmutXPpGwRRH1bh8h87G4uwFx9Qry13TK3MjlSOhHUUGhZ93kZvjvRhxFkgvPAHO6g5b+Ntyim1WnbbcruHFnpD6xqmlX1pb/AIKte+heHEKs0gztsxOx99cZaSe5ke4uZGkllbmZ2OWY+ZqPHCFYHJO9Sic1EyCUghgYByHzPeqnyGQ7WCEblGBBwRSr91MEdyi8sqyD2x1GKRuRT8C99E8BBAYHce+rm5pGOh6jTx5JDXRafg65bVtPn9ameWVJdmbcgEf/AHoazo7Rs7CAqW3RugYVRcEar+DdU9VdeZLkhDlscrDO/v8AKu0x6Bb8S8IzTWs/NfW59q3EfM2M7Ee/H2Vv4RWsqMPYJtSDlPceXwtqupo2x1lEAfaGnv7/AHc1xGTvIWHKzIfEA4qe9rJqGjvJFY88ysMyRoM4B3O1TuIdHFrND7bbg5yAPGqrTNUn0W8aeEK55SnKxODVEsXAe6OT2XC3Xlush0QglMU+2xty71X3emvaCNzh1dA+QDhSfyT76rXFaXU9fk1AzJJBGFmVMBScRsOpHxrPSLg5HnXN4nBE0/kG4QFUyMOtEbhRmApNLcb0k1zr22KDRUKFCqkyXH1r036KwMfDvEDg9byAfKNvvrzHH9KvSnozXXq3C2tHGS1/H+qKtOkbmbYK6AXdZa30jrgDsulPKCRf253/AMuvJ9tq09neQ3MbKrRSLIvsg4III+yvTvpD36z9mkqY3N7b/wDFXlJgS21FSzSwtDGlPPdrrLqtx6QnG00hc6+6ZOcRW0agf7Ncy1O/l1K+uLyeQySzyNK7kYLMxyT+uo5jcUhgR1oWrrHyNyloHgLKt0hduhS4vpCkdKUh3FARGzlFej/ROYrqHErA9LW3H/qNXUe2yC61Ps31OwtYZJ555bdESNSzE96OgFck9FufupuJWAzmG2HX+e9d4fUmydsVuMbd4kttZGxtzNVdwTw1YcDcN2+jWp5mT8ZcTbjvpiPafBJ5c4Gw22q2s9Zt7uBLq0uY54HB5JImDK2+NiPfWI7UOPY+EeF5poWVb+6zDbBs7ZGGcf0QR86PsqnjPZzoAIAJtiT8S7Vf6v2c7uZV1gNFzH0oLs3HEOisTn+wHG/+NNcQgupbedZYnZGUggqcEfCuw+ky2eING5enqLf7w1gezXgm6414kgslQerQ4nunYkKIgRkZG4J6D31HOQWtbyQb7mSzV6S7EOK9Y4i4PWTV4ZmWBzHDeyvzG6GTnrv7Oy5rcXtyyW08tvH30qRs6RZxzsASF+s7VV6fp9lpNnHY6ZZxWlnCCI4YlwAOvzPifOqng7je14u02e/tLaaBIp2g5ZWBLEAHO3TrVroi8mQBGhpG+68udoOqa1qvEt9c67FNb3zP+MglBDQj8lN98AEAe6sg7ZNejO3jgm31HS5uKrSKGC6tyGvXLNzXCkqiYHQEfVmvOsqYbrWdiIcdUBM0h2qboUKNdzWQ0XKqTsH0xXp/0cLBE4JvLoRKklxfuDIOrqqqAPgCT8zXE+z3s1v+P/XhY3llbepKjO1yWGeYkDGAfKvT/DlqeHeHtN0lWjY2dtHAzRjCswG5HxOTW/SwODLIqmjN8yy/bJPLFPo1rzfiyJpSvmfZUH7asez6cfuUQK399y/YtQ+JeENQ4o1d7ybVLSKJRyQx90xKJ5E53Oc1YaDobcOWHqDXYuB3rTcwTlxkAY6+6ulLoxRMhDu0Dc/FbBLRCG31XNPSUkLR8OknJAuR/UrC9i05i7S+HCDgi+jP213Tjvs4su0NdOW41aSxFoXAEcIkL85XzIxjFcY7P9Jj0fto0zTElaVLTVjCHYYLBSwyR4dKwXgtkvyWRMwiXNyK9aw6i8xAZ9zXHr30ldPjkkiTQZyyMyHmu1HQ4/NrpkkndMCo6GuTD0eeH57qSa51jVX7x2ciNY16nPkatjjY03Lb/BXvDh7AWM7TO2n923DzaMmlLbK08cveesc5HLnbGB51zXSJSNRtx/hU/rCvRI9H3gaJQZLvWn+M6D7Fri/GOiWHC3aFe6ZpplNpaXUax963M2MKdz49arN+KHAWCFlY8HO5ewbu7ae4k5mIOa8jdrxH74vEBznN4fsFeobzUGaeQqMbmvKXapK0nH+uk9fW2+wU8jeEy6uqhZoXX/RomWPR9f3xm4t/6j1oe3mbm7PZDnIF9bn+vWK9HR3Gk67joJ4CT/ktWo7bpgezafO/9nW/j/Sq3mJfD6KTf9G6Y9H2U/uT1PlOM6gN/wDyhS/SCjml4LskUPJ/bBdlUk/QbyrLdiPH2kaHYyaFfpLHJeXneR3A3TmKqoQjqM+fSu6ZniY8gkGNieWpyNcCHkbqbW5owF5b7JNOv4u0TQ5xZ3QiS6DNJ3TBVGDkk4wK9Y2twAUw25IqseV8EPKQD4FsVk+O+Ov3D6fb3qWy3jTTGNU74KAQObJxk4qsxZwGpMjDG2uuB8M8Fnj3j260X15LHna6m75ozIByFmxgEda2Grej3PpWj3d5p+tyajfRIDDaQWnKZTkDGS3kSfqrG9n3GUXDXG51u4tmljm76NkR8cne5GcnrjNejDxZw3buyya/pXs+PrKn7KjHEXHOPgh4YmOaSd1wbQ+zHj2LVLOWbQbtYUnjdzIyLhQwJ/K8q9TM8c0rMrbFiR86xc3aDwhEPa4h04/0XLfYKzHGva9pWnaG78PavFPqIkUKqwll5d8k8wAx8N84q4wyO9on3q9rWsG6xmjjufSGG+41mQ/1q9EG8yd2NeR+FeMSnaRZcSaxI7/2Z6xcPGg5jsckKMD6q7Q3bjwyGHLbaq4BHSJR/wAVMxnHGZgvbRRgIIPioPbzx0tpYpw7ZSkPcoHuWQqQYz0TzByAfhRejrdyxcM62VP0r2L/AHZrgms6g2oahPcZPK8rMoPXBJO9dE7Ku0y34K0W/tJdMkvGublZQyzBAuExjcHzqDTxJeDGLgfFVtkzSdy6H2+ztLwLFzMTjUIj/svXmmQ/ja6t2ndqkPFvD6abHpTWh9ZSYSGfn+iG2xgedckLEvmg8QJicGEaqqpIz6L0R6NE6JY8Qhj425A9+Hq47fZC3Z8QT0voD/WrjfZ12i3nA8V+LWCzm9bMfN6xnblzjGCPOpfHHatqvF2itpt1HpyQNKkmIEPMCucbknzoprbxcW426q0OHCsufB8TZ99ehOw3i3SoeH4NBkvOTUJruV44SjYbmxjDYx4GvOxbLZq34e4gvNA1CC+srh4JoH50kUZKHzFBUM7M5bJsVRBIGOuV7Ev9Js9VSNdQsrS8WIloxcRLJyE9SM9KOw0nTNOkWS202wt5VyBJFborDPXBAzXmd+2XiqT6XEWoAfzQo+wVCuO1DiG4JMmv6sxPlMR9laeWLnI1GcVi9G8dcb6VwZYxteRzTz3QZY4IhhmXBDNk7YGRt41wfsMye0S05TjFvck74/izWP1nie/1juzdXt3cFAQDPKXK58snaq2yvms5u9XJOCNjihpJoA9rA7xP7KiSUF4PRe1fwktphmuoo/ImVR+2j/djYgYm1yzAHUNdr/zV41fXnfrCp+LE00dal8IohRD5aLm+/uVpqGL1R2g9pencP8Mz3OmajZ3OoOwjgWKRJOVjvzMPzQAfnXmTUOIZNa4ok1jUDG8txcCaVlTlXORkgDp0qsuNUmuI+7YIq5z7IqHzHOazqmthaWiHW2tyEPLNmIsvYkPbBwTDciUcQKFD5wsEhyM/0a8j6rMst7cyIcq8zsp8wWJFNnUrr+Vao5ZpX3PWqK2pgkAEN799k0sudLjwQafRdxttSYogFzmnhsAKhDFoLpmhGqgDpim5kCoSq708eaUhix22p2NMMDRwhzjKFblvoo9pLLbyJKnXy8wfCpiwRhubkGaS1tk5Dke7FPquaOpoXMGR2qsY22hQAp6JcZNJVcYp1ehrSiZqiGhORkZHxrOS6ldiR+S5kADHAz03qTqmoyxvLbKoUbDnBOad4V4eXiK4uI3uGgEaBsqvMSScVhYjVSVU7KSkJzAnnbXpy2sq7PqJBDDv5JpOF9WlHetauFYBgTvzZ38KudK0GKKzZL+0TvectlxuFxWhudRg0uJI5nOEUKD4tjbpWd1PilTOy28AkRkxzMSpz8K6GLCsMwp3Emdd1rWNjv3WR0tLSU2ua56HVVWpSRWhJ09xFlsExnGRTOm6vdW0xzdOilW2ztzY2NRJn51A99MHY4rj6jEJG1Ami7IHIXA8li8dwdmabK3vrqW+jHezd8Fbrtsce6q7eEkqCPdT1hdJH+JlA7t3Us/Xl9+PGnrtEVpJFfnXOxHQjwNWyO9Zbx83a59QneS8ZydUzLiVO8AVebwHQVHYBaeBEkYwSADUeUkMV8qCqXi2fqq3dUijBI3FFSk8qAYLmygnY2DGkyjEox7qUke+QcYoTLuG8RRbg4x6qXJOhmH5RpfebbtUVZSOu9K7/wBwq9tUBzThyfaYDxJpDSgjbOab7/8AmiktKSNgBTOqOhSLkbuc9TQtt5wc702ATRo5icNjNDcTtBztrqA3XrLsk4gt4OzDRNM1CaKTT2jl722dAwbMrHcYz1wa6BpGscMyRyRWiW1sqYJVY+QHPwFeUODu0aeygsNJNpG0UYKBu8IPic/Ot/p/HktkXZLBH58Z5pDtj6q6mCijqIc8TvLTVbcLGPjBaV35tV0gqYxNblW2IIJBrmXFGsQLd6qIhHawpAxiVNtxkZ+NZ9e1KWPGdJiP/mt91YrjLjKXUZy6WyQ98pVk5icDOc5oqlw8wuLnK1rAy5Kq9Q4i1C24durGHUZ4rVomXuFfCtnqMeNXXYPr1/p15I9vcyRnllxy/wBEVy6/v21OQW8a8qo5YPk9MYO1aXhXUW0hoxbs0RLY5kYggePzqqOVtZVEs9gAjuJQzJRJNflstf2ySwwy6fxGtnBNqlxcMlxczoJO9AQYDK2VPxxnatnoMPZnxLZ2sx4d06KeVUWRY4MqshAyMrjxPlXI+0Piptbso9OMIHqciy97zZL8wxuKrtA4wvdAtRBHZqxeTvQzswxnH3ULIIY6l8T9B3ddvool8bZnB2x+a9Yy33D/AGY8MfjRFp2nWhIjij3JLNkhQTkkkk4rmXaF20cKcS8IX2kafPem8uigjElsUXIcH6WdqxVnLq3a1qrWmt67PbW8Kd+keC8aHZfZUnrg9a32leivpOrKH/djLtH3mFs1z/WqMsQprSO8U7nSNGZg0XEuDOI7zTtZt4hqF1FatKHaNZDys4yFOPPNd5jng404P1DS9QJvbvv4pI4JF9tlB3IA643riPaZ2c33ZdrFvZ3N7FcvNGbiNo1IwocgZ9+2a0vZH2gXFrq0AnggkKpJGZZGPMQRnrRGH1TJGmnf7e47+5X4dUta11PLvy+ybuuzi87yVIeG59yVQ9yR8NzWR4l7N+KeD7aC51vSpLKOclY2d1OSACehPmK9RS9q8hiUCxteXGBiRqr9V4dte2TSok1ae40j1OdwoiCvz5Ub7+GKsr4nytD5IwwC+37K2rpGytz5ctun+F5NgviHVJACM7sTvVjEYZVLR4IBwcVZ9qHCFlwRxlfaJYXMtzBbCPEspXmYlAT02HXpWZtb1rUMpBcHoCelZFLVuifkn26+5YrXljsr1auAqkqNx0pkySH8gimoNQ9YlSPuuXmOM56VL5fjWox7ZxmidorgQ7Vqj5mY+NJIzuwy1SqQyZOSaZ8JtvdMWKLIuw5RTDKN9qmNjwzTLxgZYnFZ80N1U5qilN9gabK1M5PfSPV+b8r9VBOpSdAFWWKHy0OU1KaDl8c1Ov8ARPUtPiu1mMnPy5XkxjIz1pmYZM9r3tGjRcpCJzgSOSqBsRTwY+dCGJJQxZ+Ujpt1ohUImOYL9VABT4dUvYYkjjvJ0RRgKr4AFdAictGuTnYfZXPobbupbJ5V7yKYg46eOCK6N3QiAAG1eh+jJlLZOITpl0PhddFgxf2sx6Lm/Ewxrt7/AIz9gqviyGPwqw4mH9vr3/GfsFV8fQmvOqwf87L/ANzvmViVP+s/xPzRdaKjoeNCFDoqAODQNFmobJJ0b0Y2OaaDYpyiGOununVcYpxJShyrEH3UVpCJmZSSPZyD76QylCQfA4rQYXtaH8lZqBdWlndlZInMjjlYEkHetlpnFlrFyrdtNIgO7BfaxXPIn6DOKkrOy9CK6PD8YdEzLvdaFFiEtMc0ZXYrTiDTLxQtuzAMvMFkTc1IueFb3UAJrYQvDMueViFxnqMVzDS7yVWtxzALtmt3pGv39ioQXrrGPohsMP1111O50sfEh353XWUuKtqm2mGvVYfingPV9Hu7qZ7RBbq3MvdMDhT02FUZt+aEcwI5x411LiHjySxu7VruFZklVlLxjBGMeHQ9arJToHFcwfmImRPooe7PL7x0rn3YVDxHNaRmO48ddO6xWTUYPTvkLKeQB/6T366Hw8fFcqccp2GN6vQfXLZXjOf1bjrV9rfABaF5dIi732gQpl9rGN+ux3qu0XSb0W1xHJbyIbdsvkYwD/1rKw7Cp6epdTyDsuG42uO/zWQ/Daink4cjd+fI+9U7LzDB8aRHPd2iNHDNIiE5PKcA++ptxb93IybggkYIxUYofEVTNA+N2hII5hBOaQVFle4nP4yV3/pMTS0QIMCne7FDkoQQEHMTcqOXmUjBowKVyU7HA0hwozVjIXONgpAJjFDkbyqYtkSRkgH506tgM+05+yjGYdK7kpCNxUKOMsNgMinO4fqMU8PVbfmY3C+8BgaabWLdDiOF3HmTinLKeIDjPAPn8lLK0e0U5HbSKSWVDnwJp2KLuOeRyAoXO1V0+sTyjliRY/IruaYNzeyqytJKykb+WKrOJ00ZtEHOtsf5YpuIwbaoXVxm9NzbsQQQytjBBHjXa+zHXpbHWtKnuLh0ikeN5+UbMOvQVx/RbJbmbvXx3cWDykfSNdI0RHkmthGrFiBgKN+nhWv6O0T3xTTSnSS+nnqtnAGudIbnQrb9rPCdvb6hHqmnRRnTr7mkjBIyrdWAHl5Vxe/sJEEjqF7vm233616S4n0Ke+7PtFumfkNnBzyI4PM3Nt9VcS1LThHI1sxwjbhseFatHG2spTE513NuPLa/wWjW0nFiDxqQSD7tljIIozJy3B5FPRsZ5TUCZMA4wd6tLuMLMyHI5SRVZL9E/GuRxCIMGXxXLyiwsojjemjTz9aabpXNStQhSaFChQpTJcf0q9E+jm6jhjVebxvl/wB2K86KcEV0Pst7SLng+8jsHMB0y7uUa4Ei7pnClww3GBvjoa1cOcM1iVdA4NeLru/ahwdfca8IvpWlS2yXBuI5v7IcopC5yM4O+9cs0b0d9dtNVs5tSvdGe1jnR5oxI7c6BgWXHLvkAj666vP2mcHwZxr9owzj2Q5+wVHbtX4NXrrPP/QgkP7K2HUJe4Pc0ox8bHHMVf23CPA8EuU4R0Vfb2/sVTtn315G43jgi4t1pLeNIolvpwiIMKq85wAPAV3Ljnth0ldEkXh6+ulv+8TklEJQKM7nLdfhivPOo3cl9eT3MzmSWZ2kdj1ZickmgcShETB1KHqctgAoxoDrQodCKwAdUKu+ejNlV4lcfm2o/W9drub+CytJbu7lEUEKF5JD0VR1NeXOzDtIk4Et9UjisLa6N6YsmaRk5eTm6Y69au+Ke27UNc0C+0o2NjBHdR92zRO5cDIO2/urqKWP8gPcdPFHRSBseqz3abxkeL+JZZ4w0dspCRx94WXC7cwz0J6nFd+7Mpcdn2gKM/3IP6zV5KM5MnOfPNdJ0Ttm1zRdEsdMtbq0ihtIhEg9VDNjJO5PU71OnnbUPcbgDvNtFXDKC8ucrv0iyTxBo3Nv/YTf7w103sl0XRtM4O0nULXT7a31C8sU9YuEB55cknc59wrzlxrxtqPGV7b3OoXKTvBEYkZYhHgFs4wKmaf2ncRadp1tYwa1fRQW8YijjjIAVR4DaoMycZwc8W8dE7XsEhJXY+3PjGHStEfQVNyt3eokyyIAECB9wTnOdq5d2QcYDh3idVmEskd8Ba8qDOGZhg+7fFZbiTirUeJZoptRvbm7eJO7VpmyQuc4HuzVTaXbW1xHKpYMjBlKnBBHkaTqxrJQ0Ou37qD5/wAwEL2+Io54mhngSWNvZZJEDK2/iDsa8f8AaQiRcb67HHFHEi30oVEUKqjPQAdKYfi2/fdri6b+lcN99Ut3cNczPK5JZ2LEk5PzqjEHw5LRvufCyU8jXDRR6UnWk0Y61gMNnXQy7r6OjII+I98MY7cD9Jq6ZxDxJpnC1pFdarcPFFLJ3aFIy5LYJ6D3CvO3Z72iScDR34isIrtrwRgmSUpycpPl1zmpXG/andcZadDZTWNvarDL3oaN2Yk8pGN/jXVwTxiO5KPjna2O19V1qXtm4Sjzyy30n9G2I+01Fk7a+GfyYdTY/wCKUftrzk07k7u3zo452Vw2TkHO9VDE472sq/XHdF7E9dW2aOQgsDhsCvO1txHDw92vPrU8LzRW2rSzNGhAZhzNsCdvGoE3arxbKADrEy4wByhRj9VZa7vJby6luZ3Mk0rF3c9WYnJNNWVkLsuTWxTz1DXWy8l6In9ICwYYj0Kc+97pR+yoT9vxX+D0KEf07s/sFef+9od7TjFIB/0/iU3rZ6LuNz2+3sikJpWnIT4mdzXLeJeIZNd4mutZlSJJJ5lkKx5KggAbZ3xtWf7z3URcmhqjE2vaA1tveq5Jy8WK63P28a9I5YSaamT+TaZ+01zfiDWrjX9Yu9TuihnuZDI5RQoJ9w8OlVfPREmqKnEzM0DKB4KMkrnixWq4U461fhS2uYNN1KezS5ZWkWIA85AIGcj3mnOIO0HXOIdP9QvtXu7m2MgkMUmApIzg7DwzWRDUOam/FH8PhWFvDVNxXZcvJTbO9e1uElVmUqQdjg//AHq+fjO9lBD3moPn864Y5/XWU5qHManTYxLA3KwpNlc3QLQS8QvJ175v6UhqDeakblApBUDzbOaruY0RapTY3PK0tcd/BJ0rjon4bgxSBh0z0qUdUc9FQfVVdQzQkWJTRjK11gohxGysPwpMPzflSJr9505XIwDnYVCzRZNO/E5nCxcU5e4p1ZSGyCR8KX6w/wCe/wA6j5xR5oZtU8aAqN0tpCaAkOPGm6FQ47r3TJbOTSKOhioueXHVJKDYFAsSPKk0KlndaySFDJ8DR42osEU2o1Ssj5jQ5zRCjxT53JIuYnrQpQWj5fjSs46lPZI3oUrFKVM7704aSlZJ7tvKldyxXIx8KcwacT6IohlOHHVTDUwLdz4D509FAFwW6g0sUtULH3URHTNadFMMCCjmOKUImznAx8aWECnNLYgY5STtvWg2IbuVob1RKufCnUU7bb0aoCKWib0fHGrWtRqreNOKuKAXFLAo1jLK0BFy1FutTht1lj5270AgAL0OKmMcRuehCk/qrLSM9xLzEs7v9ZJrMxaufStaIt3X/niq55CwADcqZY2dzr2oJbxsr3EucFzgHA8/qroXCumzaBYPb3SxCZpC2UIO22N6y3A2m3X4ZjvTHyQ2xJcv7OcgjA8zWh1ibUr3VBDYloo+XDSfkj/rRnovRBjDXStcXkkDvvbXzvqtXC4208QqnNJeSQO/b99VnNf1a1vpkMMjMULBsqR41SSHmJanjaNzuWYZBOfnUe45VfAz0rGxGpmncZZgBf8Ax3rBmkdI4vduUliMbU2RtsN6PrSlTIBzWMRnNlRumsletSY5e8EUec+BHnTUigDJ60cSsvK69c7bU0RfG/KNvpdONCpEi92xGAAPKochDOSOhqddHDP8KgGrcQsHZBsnfvZFRg4OaKjrPBsoJ6OQDpS+ZW2NRs4pQc7b0VHUaWKleykd2ngood0n5oprvmNJLvnPOav4ke9k+YJ5kUdAKbZ0A2G/wpsu3maAUsapdLfRgUbo2fPTak4pQiNFjFUuaTq5MjilkgkEkTFHXoR4Vdw8QKqrzyz5xvgnr86o/dVjp2mR3EF29wsiPGimPwyScePXajsNmqGScOnO/XbQXV8Dn3sxTn4gjb+MuD/9fGoN5qEt5IBCzhQMkk4OahEerzkFchTipIZWUMp2NFurqioBZI63W2nxUnTPcLEp+NookGF9sjcgdT8ak2113KGV9gufo74quaQICSdhQe6/FGNQDzjf3UQysbENCBYaJmyZdVZ6VpZ4j1WRxIqQJ7XK2csBjb3V6E4XvdNttGsbG4CtJFEsee5DDPuPlXEOz+1lkmm5Bg8h6j3iuocI2V1d6jNFdRzerx8gjPKVBJO+G8a1KKBvq/Ed7TtSfNadIwZM53K7lp2jWUdlE0VtAZOX8Y3Iuc/KpOsa/ZcNcMX13EgS8t4GcNybZFV2t6mvCqRoskfrUi5OTkKvvrIcYcQWvEfCV1Ywz99qEkbGVIj4eHTz2rOZTPmcHO1aT/PcnEZcbnZcq9IPV4uMbrTde09g9pFYhJOf2GBMhIwp69a5Ppl1FCjIz4Zm2AHurUcSXUs/DjxSqyGJI05TsRhhWLmt3tJUbIdSAwYfZVdew0NU2SEaWF78rkj6IKqbklzN6LqHDvE/dWsMM7PLAq8pIGWU/tFdS4f7UeGdMkaGe4uTBJgkrbk8pA+Neb7HiNrOIx9yrb53NOniUnJ9XP6dbJxShmjDXv8AgfsjmYhHww0ldd444n4d1nXbyWzt1lZuUq8lsAW9kYyTvVLJ2Laxx1Z2us6CbJVIaO6WecRhZA23KMdCpFY3TL03hNwCEZzylc5O1d47G7z1jTtUsHn7uEpG+VflcOSRkfUKLrYYqnDuzq0EW8NkZnbVw5X7BeZ9U0240TUrmxnZe/tJGicxtkcynBwaO01FUjInZy2dts7V1njrsR1bTr3VOImurS900SNcMgZlmYM3TAHv6g1i+IOFbGDh+LVLeBrX8eI2DltwR4ZrmWUk8eeaAizb6EnUdVlCglAkey1m3NjvZVKOJEV1+iwyKURiqa3u5oSVQll8Ad8U/wCv3A2ZVz71q+LFI3NBcDdDCZttVPZQTnFIeMY6Aim4LhpgeYAEeVOFvfRYeyRuYKVwQkqArBsAkVcWc+jrJm4VsEb4Txqmbc7GiNTgqjAbtaD4hTjk4ZvYHxWj1Dhl7y57yy7pYSowG2NS9N4c1Kzf8a0UkJGCnPnH1GqfTOIJ9Og7oIkvtc2XY5HurR6BxPLqN53M626R8hYYODkfGugpH0b5BK24eeX80W5R+oSyAm4cTsNv8Krt+Db63vbucLAIGVwnt779NsbVmL/SrjS3RLgKCwyOVs11xyroQMMGHgfCs5r3D9vfNHIyTHlBXCtsKVZgMUkNofaBJGump1/ZXYlgLWxZqfl39fcs7pM1rqNrBpxGZ48OCw2BBzsa1d3ex2lvJczlhFGMsVGTisReW03DurRT264Rtk5va9xBrZ6lG13pd1bovM8kTBR5nFW4XUTcKaJ7QJYxbbcAdnxQuHucI3tt226fDRc61y6ivtWuriAlo5HypIwcYFMW9vJLGzKBgHHWmm2PSp+nOPV5Bt1P2V5pB/zFS58m7rn3nVc7m4shc7nqoFCjoEYFCkKpJNDlPWlBc9aNhtTZErJGKGCBmh0oE5GKgLBMpVlMySE5OMZqdMyXaHJxjLVUK7L0NTFu2eERcoyp2ON8Vr0dW0RmJyvjfplKYGz+W9PKd6QRlsmjzUI+wVAaK0tGDxjPhtWqsbjvbGL+aOX5VibWdlYrnY71peH7hpUkt2P0faXbz613fo7XBzwzqLLToZLOt1S+KmMmhSZ35ZEI299Y60l5GIJO/SukJEGR42BIYFSB5HrXPNWtDpuoz2/LyiNvZGc+ydx+qs/0upHQVLK4HQ9n36n4j5J8VjcS2X3Kw/Dd5FYG2huJYlHQIxHjTug8R36agqNe3DiRSo5nJwfrqk7zI9xpmOZ7a4WSM8rocqcdKw34zNHNHLmNhby/wgY6uVrmkOOnetjqTzXUpnf2y27Njcmoq2ksrBEQlm6DzqHY8S8kbLdxl2H0WQAE/Gg3E7LJzw2qhh9FnY5HyrpHYvhsgErnnXlY3HzRLponnO52+/VP3NjcQsBJEUJ6A+NELWNBzSuFHmSAKEXFbzufwjAJo+XCiPCsp881Z3nDkGrW0Z0rUPWHJDNFPIoK7eGB1qtjoJmukpRnI5HQ/v7lYyBs13U3atyOh8ufuVa2oWNriPvVON/ZHN+uo13rkfIBa/TzuXTbFHqHDF5psnJcQNjA9tc8pz5GmItKQbyAn3A0DNPibyYmtDB77j3oWQzNJY5tiosmp3k2V58BhjCrimVhuJDuGP8ASNW/d28C4OFAHTO9B5YRgxRl/PO1BOwtzzeomJPS91SWk+0VAj013+kwHwqZDoaEAnLfXQWW4UHCKSem2cVLtYNYuv4CKUgeSYH66LpcPpgdYy4+F1ZFGwmwBJRpZJB7SxqmPHFRtReR+W3iIy/XlOc56CrqLhO+ugRe3IRSc8vNzk/sqzs+EbC37sFJJpVbIbJGT4DArbGHTTM4cbcjT32+AWkzC6mUWDMo7/tuqvTdPaOFLdNyBvk7Z8a6z2S8P3l/xHYywhO608rcTMWx7PTbzOagcI8C3erX6rDprvBFMi3JLcnKudwSd+ma9DaRp1lotlFYafCIreEFUzuxGSdz1PWrMUr46KD1aHUkW8B91p8EUTC3dxHkoPE8RudEvIMAl4XG/Toa85awf7BYjrzL9teheNeJrLhqCBLyynuRdq4UIwUDA8c/GvOmvXaJEYgyAs2468o61D0Za8RlxGhOn1WjQOyUUhfoDt8litaGbsf0RVFOMFhkdfCrPU7ozXLOCMDYfCqiU7HzNY+NzNdM4t6lcbUODnEhMS/SpoiluSTSDXIzG5KBKSaKjNFQpTIUpWKkfOk0KTXFpuElO/Clz/KD5CiOpXJ/jT8hUKhRv4lUfrPmVLO7qpMl5LKvK8jMPI1HJzRChQ0s75Ddxuokk7oUMUKFU2SRg4o+ak9KFTEh2SR5ocxFFQpw5yZGW86HNRYoYp8zkkec0WaAFHim7RSQzQoYoYp7EpIqOjxQ5abIUkQNHmhiixUxmSQoUeKGKjYpIqGaPFDFPYpIqKlY91DHuprFJFQo8UMU2UpIqFHj3UWKbKUkVClY91DFLIUkmhSsUWKWUp0VHQxQxmllKSKjo8UWMU+QpIYoYo8UKfKUyLFCjwKPHup8idJo8CjxQxThqSIUdGBRhakGJWScUMUvl91K5B5VMRkpwEgdKBWnBH4gUsRedWiIlSypjl3o6eMY8qSYx5U/AISyoh0FJO5p9YsgbUYh3q7gOIUspTSrgil4p0RDqaUEXPSrmQEBSDUbIWOfGmypBxUpxggdM0Xdr1xvRz4LlWFqZjQ752FLGUYFTv4U6iZztRiMZ3G9TbBYaKQamwpY70uOM9dqc5B5GnUQYwBREcGuqmGJCggYp5EOc0pIlI3BpwIPI1oRwkC6ua1EFonZUQs7BVG5JOwpFzdR2UYeXmwTgYGd8VQT6lc3ERikcFT1HKBQldicNL2Dq7p8rqMszWac0i/uFnu5JIySrHb5VYcMWVzc6rbzQQPIkEqPIw/JGeppjS9Gur6WBxbO9u8gVmGwxneuhRW2kcNB2gUW/f7ZZi3NisrB8ImrJ/W5uywG+ul+YtcWtdW4dQuldx3nK0G/2spOu6lDZWE0qFZGTHsZ99YrUuIbm5RViPcLk55G3P10NQ1ea5t5IZHVg/UBceNUrsF6da3caxhxPDgdZttet7qzEsUfO7smwshNMeq53qOdzRkFjtSkiJOa42QvlcsQ3KCRE0qX8WuMjmpx2WEZP1VDZ2dst1ppnNibkG6c2CIAnep9ptEPiaiKuSFHianRqFQACp0EWV2buTsGt1GvHzIV8qjU7dbzvTWaBqnF0rj3qLjqiowKKjG9UtFyooYoulLwDmiKkHpTlh5JJNKEbHfFAKc070FWxxX1KcIgoXrRlhRIjzkhBkClmymA/gzRDWuI7DdErHkE0W8qTg+GatdA4X1XiXU103TbcSXLqzqrMEBA67nauz8G9hsWnaJeTccaDIZklDxvFc9I+XcHlOOtNFSyzG1reKuip3yHRcp0Ls91/XbCPUrO3h9VctyySTKv0Tvt1qx4g16TV3SF4VQW7EA5yTtj9ldO1XXNE4W4ffQ9Ctp7aSKQtFzfjAEY5OSTnJrjN3MEcuNucknNdVS0gooNTYutf3dPeVpvjFPEGtO+/uVNOf7OcnpzUruI23B+RorhRJMZBurHJIpDK0e6HauYcMr3FwuLkrIO6U1vGBnmNT+HbGbUbloLdcsSNz0A8zVfa2819cR28eC8hwMnArsfBHC9pBYwrBD3lyWI5snLNjc0dhNNx5uM1tmt37z0RVLBxHZuQU/gfhdBdPDHKFcxgySMCcgHwH111e0tIrW1jtos8ka4GTVfo2hW2jx5RAbh1AkkznPuHuqZdXAtbeSUty4U4IHj4VuTy8R1m7LVPQLDdpF9LcSXFiAWclFYk9EGCKwtjqM2hSNc255WAww6Bh5GtRxfqVrZ6dcajeS/2UwyrNuXIxnb4VzHUOIWuraR44wvMC3MTk/Kj2TRxMs7cDZJ8rWb7ouN9bN/y4Eamckyb5OQcirPhvg671/Ql06FYVuGHf8A44lQBnOOnXGKpOFLQ6lqkF1dKsrFuVdsbAH9ddt4Wj07Tu6dlW3bl5Wckn51nxB1Q59Y/ZwsB3fuh42cUuldzFrLncXYZrzMNtNwT4zH7qlcT+jnxFwppv4Ruby2uoi+GW0jd2jUjPMcgbeFejdOsbGS0gd4SzsqsTzEZzVjxtxUnDejXtw211GhSJCucyY2yPEeNZbmRulY1sd9dtr+SqNPHmAsvDDN+A9TkQfjgo5SfonetvwZqwjkmuEjZ1kjUYJx41U9ow9ev21iZES7vpi0xiUIhPKOijYfVUThHUZohNDzqURV5QQPM0bh8z6eudSO9k3IG9tL7+CencYZ+Gdl6G4K41m1CJtKMDlFAxI5yVB8Pf0rMekfNK/DGjRtjBvXOw8o6qeE+IF07VLeaJ/xbuBLHtkqM7b10vUNI4b7RbG3j1K3a8ghfvEUStGyMRg55T5UXilJoeGN1qzxmWNzWbleWdAv7fTdQM9wDy8jLsM7mtA8OncTxvLEXWRMJz4xy+PTxrNcRWkVpxDqVnaIyww3UscSZLEKGIAz47UxYaveaarrbS8gc5I5QftrHw7G207fVahgdFc301v5hYlPVCMcGUXbqp2saJLpCxv6wJhISoABBGKrIySwYk4BrQ6TejW+9XVHEvdYMYxy4J69KmXWg6elnNLHAQwQspDnr50f+CCsb63Qm0e9iTfTfrzHVSdSCS8kOjeh3+qzaup8aVUe4HdMO75sY35hvmgs5xWKJw0ljtwgs3Ip4miHXOKTzFhkGk8zCpmQDVIlabSuMJtOtYbQW8ZRPZ5yTnGeuK2On6nBqcJmtixXmKnmXBBrlCuc71badxDf6XEYrWZUQnmIKA7/AF10eF4+6Pszm7QNNlu4bjUkByym7Pl4Lf6hp0OpRd1OCBnIK7EGoGm6U2lPIGlEnORjboKPRNei1OGGJ5ue8MfNIOTAyOvuq1MQbqN67SB0Utp2DVdG2KCqLamPfr9D3rjtwfx8g/nn7aQuxya2nEXClpYWF1eRxSZUcytzkjJPl9dYw7bV5BiWHTUM2SYi5108VwlZSSU0mSTdL5ffRgY60ZzRUPayHQoiM0qhSISTZWkHrTxFIdcYIFUvZzTEJB64pyFuVumdqbowcHrVbHZXXTAp8yDGCN6INSQebFHnejM5KldPRnlYNVzol4IL+FjsrnkbJ8DVKMADcdKfilwMg7jpWxhtUYJA4HbVXRPLCCF0JZSknSsjxtaqt1DeIuDMCr7/AJQ/6VpbWf1iCKYjBdAxA94qPxLZtf6M628TSyq6sqqMnrv+qvQPSKlbWYc/KLkDMPdr8Rp71v1EfGgcG68x7lgoBzDfwqSIUkGHFRJoprSdo5UaORDuD1FSYbhSvtkK1eX0csf+lKLEdVzYFjYpDWWGwrjHvFKWyHi5+oUU1yi/QwxNJjN5dZ7mKVwvXu1J+ynLqZr8rW3PQXKQaDspAt4x+SPrNSLTUYrSQSc+46cvWosOjapeScqWk5YDPtqVGPiatNJ4RvZb2IX9u8Vtk87BwCNq0aM1bpB6tCRc72NvkioKedzhw2Hfe2ivoO0VJbeO3u7N7mGNeUKzDcj8o++lDiLS7wZTh6VlJxlMHf6hUiPg3RISGKSyAb4eU4/VVxYrZ2MXcWyxwx5zypsM+ddpS0mIn/8AkOFu4X+i6uCnrXaTytt4An4hUsXC2lajCZRZ31i7DIMjht/h1q/0ngfg9NNddQ1C/a8JHK6DlVR4jlwc/HNHIjsOeOQMvxqBPexQHEs8anyLUacLp/atY9RYIh1FSRgF7Ae/b5aLY6LwbwraIwt+I4YmkIB7+ByT5b4xV5J2c6aXCnjLRgxGQM5/bXJTxEASFuVABx0ofugBYE3QGPdUDAdmT2H/ANfskMQp2DLEbDoA1da/ette5ab91WlMgOOYEdfnWr0zhvgmxghaU6dNPAil5jP9JlH0sc3mM1wBeI8ne5j/AEKP90oGf7Ki/RqiagklFn1OndYfJJ1XBIO1IfgPqvReo9ovD9iwaOdr13OSLcdPeScVQ33bNp8azxJpl2PZZRIZlXG3XpXChxNIdzdp+jVXqGvXErzAXWUcYxtig3YVQQNzPdm9/wBrIOWqoWMu0En3fQrY6jxnJdyQwXF7cXZUkKXcsEz5Z86xOsXjGacCN4yWwVY5INQre8xIrc3NhgT86teKYRPy39snPbyLlpV+jnOKIkqw+lcYtLdOiBqa2Sshc4/08gOR/mvis3LJnrUSRs0+/vqM5B2rhat5J1WA4pp+tJNKc70g1jSblUFF1oqVSaoKSFHQoU1kkVChQxTJIUKFDFOAkhR0AKPB8qkGpkWKABpQU+VLCHA2q1kRKcBICZo+7p9ISc+yacFufzTRjKQkXspBl1F7uj7qpQtz+YacFqfzDVzaFx5KQjKhCI0O7NThan8xqP1Vv5M1aMPd0T8MqD3dDuz51O9Ub+TND1Rv5Nqf8Pd0S4ZUHuqHdmpvqjfybUfqrfyZpeoO6JcMqD3R86HdEVO9Vb8w0PVW/MNP6g7olwyoPdGh3dTvVm/MNF6sfzDS9Qd0S4ZULujQ7r31N9XP5hoern8w03qDuiXDULuj50O6PnU31ZvzDQ9Wb8w03qDuiXDUHu6Hdmp3qrfmGh6q35hpvUHdEuGVC7s0Xd++p3qzfmGi9Vf8w0vUHdE3DKhd176HdVN9Wb8w0PVmH5BpeoO6J+GVC7qh3RqYbc/mGh6ufzTUfUT0TcNQ+694od3Uz1c/mGgLdvzDTeonolw1D7r30O699TfVj+YaL1c/mmn9Rd0S4ah90aHd1M9Xb800PVm/NNL1F3RPw1E7qh3VTPVm/MNH6ufzDUvUXdEuGoXd0fdmpfq7fmGh6u35hpeou6JZFFERPjRiI561LFs35hpS2xP5JqxtCTyUhGogiNLEVSvVT+aaX6sfI0QyhPRSEZUQRUfdHzqattt0NK9Xz+SavFAeimI1A7kk9RSxFyipq238009+D5mUOsDlT0IFXMw524Cm2EnYKu7s460BGanRafcXExhihkeQblFXJH1VIudIm09YxcROjOCcMMVYyhc7UDQKYgeQXW0CqxHinO4+FWOn6ZLf3HdwqMhSxycACpd5ojWYj5nRmcElRn2aKiwxzhmtorWUkjmcQDTqqju8+W1J7s82andxjYqRQ9Vz0QmrfUXdFHhlRVjOKUsR2qZHbZ6oaeW2GMclXR0DiFa2ElQO5Pup9LZsDcVKFnn8g0+tuABt4UbFQHmFayA81CSAgbkUi7mSxgM0ueUEDYb71ZLGqbEDzrMahqUmop3XcCOLOcZycjxzVGIPZSRae0b25696hO4RN7+Sa1XUIb+3SOISKQ/N7Q2xiq+GzluHCxozbjPKpOMnGTirGy0W7vTmC2klRWAbl8P/AKFdBt9JsNLd3tLdYGdeUkMdx1xvXPQYJPikplm7O2tt99lCjw6StcXvNgPj4KFpmkDRrSKCW5jbuSWZsYHX31U8S6tBemOKAFhEx/GZ2bbwqZq94zm4gCsBjlO+xrPyRNygchrp8QPChFNCOyBbyRlbUZGmnhFm/ZQJSApB6mopjJPWrJrR23MZNF6qyn+DIrkJqJ7zchYboyVDWDA670sLyA1LFvjqppq4QrsFO4pGjMTcyRZZVtw5k69M7CiRBjNPNHjqKQFwcAHc1iPiOfM7VUka6p21iy53GcU7M/cqWIzg42o7dGViSCNqRenELDOMkUdbhQFw31VgFmqBK/PIzgYyaTR0Vc+SXHMVQUeM0tdlzQVSBSug3oiNttU6QVKYNOKcgGhRbjoKmG5TokjNJiSa5mSCFGd5GCKijJYk4AFHk+VTuGpUi4i0uR2Cql5CxJ6ABxk1CQ3IaCnGpsuwdjPZDCb+a5460x4raWNo4LScvFIXBB7zIIwOowa7BcdkfZRZ2pubjSEWNSASNQk8Tj86qfjLXdJvJYFttQtXiVWdnSQEZz0OPhVH+CotdtFgtbtEE+CkyjmGBvt8q6iDDw2NtnloW22lY0WBUxoOz3grX2vuHuHr6eS2RgJ0v+dDldwFbOfLNV3Eva9Z8aafDHYWN3ZxxyHvBLIrFiBsNqzuo6NrWhzNHzd+hBK8uMnHjjwrm0utytOyDltplOWWP2efPjitJsEERa8m55EndWOyRWKseKdQaXVGZOdGCjfPUYrMTXa8roUJyCvWnbzU2ubmVJ/aJUe2Tudqq2lLMRjYdD51mV9drZh6hZtRNmcbFI5HCY5hj4U1lx0O1OBnmkEUS87scADxNabh7g7VbiTv5NNnIXHdhk2Ynx+ArCipnVMgjhv0vrYe9DRxOkdZqs+E+FfUxHfzSRyPLGGUBTlM713TgzQpdK0lWuY1SaR2cDHtKpxjPvqh4Q4Qfvo7m9BMdvy8iY2dgP1gVvuY9Scmuoc2OCMU8Ow38VthjY2hjUR2Hvqj4k1SKwBEsTOBEXODjb/6FWV1dCHm9pQ/KSFJ61wzinjd2WaCWRQ80Tg945OeowKaFrWgySGwCVw0ZnLM8Z8VtrEk0GHXJAX2gVVOuKyKvIqlVdgp6jNB3Mh5mJJxWi4Y4Uu9SvYZLq0lWyI52dvZDDG2POuZklmxGpGUdw7h32+Kx3OfUSabq57OLC7vbuyS3gkkJlwSFJC79SfAV2Lhezik19Le+tjKsJbnjPQEDbPuz86sOAuHItG0YukZia5Ibl6DlA2/bV1EkNlczTy4DSAMWA3IXoPea6dj+HFwBrYWuteMZG5FqYbiAgHnSM+CswHyrl/ahxtb6iXtlaaYxIS7FgRzY6A/CqviTiB9ZujLFzwqilEXm8KwTXySK0dw6ht1Ods1dSUIYRI7dO1mU3Wa4u1KPUbK1dFZAHbIbw2qi0mdYpXy2CcY3xmpuvx93LHaWziVWbCqDls+Ao7ng/VozHJBYXMqSKGwsZJU+RrBrnTeumaNt8tr+VllTF7pS8C5CtrfWIoXDNFIRg7Aiuq9h/E9g+s31nNHIjSxIyM7DGQ3T4nNcUfh7iNB7Wn3wHvjp/hrW/UZpJDderSYUB84OQc7VqxYnx3inmuwHmRZG01W4SNEmgRa2iDj7UVSTnX8Iy4YDGRzmnNR0JLuVZI3SIgYYcvWqpJWfiAzzPlmuCzMfEknetSjJIOZWDDzFSwCkhqaeaGZt+39lRCxsgcD1WJyEJBHQ1pNK4lt1t7WxaGQuAIy+Riomvaf/A+q27MSWLci5qntw0E8cjoyqrbkjArIilqsGrSxh00BNtLaEodjn07yAVsrvTbe+iPcpEkmQe8C5rL3Wly2l0Y5SAWyy4/Kq3sNUDORHIFI32PWrV2srtUluEgZ1G3N4V1NRR0mKsErCA4eR6+9GPjjqBduhWJEoU7bUovnfFWetaW0lz3lhAXh5csYxsD41TqcdelcRVRTUsphl25HkfBZ0jHRnKU7ze6jEo8jSPCi6VWHkbKu5VzoesJpV2tw0buAhXCnHWtromvjWe95YWi5CB7TZzn/AO1c1BGKt9E1GWz73u7nuM4PhvXWYFi0jJGwvPY16dFr4ZiclM4Nv2eY0XQdWsX1PTJ7IOIzKAOZhkDcGue8QcLzaGIm731gSZy0aEBceddA07Uorq0gBuEkmZATvuTTl2A8Dhk5xyk4Pwro8UwenxBmd2jraH47bLpq6ggro+KD2raHpzXJetEBvUjlB8BUeUMJCANq80lYWC64UiyWUKqG8CcCkVJiX8WOYUJVURscDNWmDs5k+XS6jURGdqV1oUPZRTbJvtTZ2p6TII8jTeN6FkZrYKJRpR+NJG1GG3pwbCxSTgOwp6Inl+uowOD8aeiJ5TRcD+0pArW8N3yvAbRyS6AlP6P/AEq9WfKBAu9c+trqW1kEsUjI2MZU1qNF1lJ4QkzsLjfDsfpf9a9JwLGGTMbTS6EbeHJblBWgAMO6m6hoNpqMEoeKNJ5BtNy+0D51SrwJk76gPqi/61fwyyvKAWJHjmpDFtsbVo1eBUNW/iSx3PvHysjjTQT9tzP57lT2nBOnQxv61I9y2cgglAB8KsrDT7LSFdLVe7Dnmb2ic0JrqOEASyJGD05jjNRLvUrWO3YrcIW2wEOTShw/D6LtRta0jnz8zqnBgp9WNAI81am6U7YLfGkNI2NlxWb/AHRtGfZi5wOhZsZqsbUbokkXMwyc/TNVT49TR2ydpUPxUctVsnnij2llVD19pgKiXOsQW2O6KztncKdgPPNZJ5jI/PK5dvNjk0Fmx0Yis2T0lLrhrbIR2JPPsiyvW1q9l9l5R3Z6qABkU8dSs5lxNaKWHVvP5Vm2ufa5QzE/HpQ75h4k/XQQxtwvrfx1VDa143N/HX5rTz6HmJZbdyzP7QQjGAaiSaNfxjnFs7qOpXeqhL6dSCJpBjphjtUmPWbpUKeuTgE9A5q38QpJBq0g+KtM9M86tI8D90l5So9/vpszH3VYQ6/axsC+npNtj2iPn0pzvbPXJUAihse7wMDH4zJ/ZUXBkpyxSAnpt8VDhNeOw8E9NfnsqXvmI22ptssSTWrfhW3cckV9bK/XP/0anWnB1tB3TzP3rrhiAvst8/CojBqh5yuIt1V8eD1UjrAe+4WS0y0e6l7uNSzEgYAzWu1rTxZcLNB3gcxhRzAYz7VTnOl6Kylo4rUyAgFEwW86yuvcSTXqT2iBBDz7MpOSoNGuiioaZzXm7iCNO9aBhhw+F7ZHXe4EacrhZ+UYaoTbZp+SQk9ajZNcDVPBOi5V5SWovCg25oVmuKrQoqOiqshJChQoUySKhR4oYpWSRUYoYowKk0apI1G9LFBBk1Kt4WkwirkkgAAbk+VH08BfoFJoumlj386tr3RZdPjtC7q4uoFnUgfRz4GjvdFvtLK+u2c1vzfR7xcZqz0mx7+8h/CZkjt1i5izeCAbYz4V0lFhouWvGvyR8FMS4xub2jbfS3iq6y0u4nDdzFJLg78q5xUm/tLexKIsrTyjaVEUjuj5EnqfhVtc6za6Jcoujd1PFIokdnP5WTtt7q3fZH2X2narcaxc6jfzac8PJKqwRBwS5OfpHoMVqSMp6eIm/s7m17a8vNFywwxMyNN3jfmB4HmuRiaIfxM36qV6xF/IzfMV6L1T0b9A0t1WXiO/JYZGLRPvqIPR/wCGiMjie9HxtF++h46qEjM1+n/afsqm08jhmG3guAC5jH8TL+qlC6h8Yph8jXfD2BcNg/8A6ovP9UX76RJ2DcNJ04lvG/zRfvq4VUX6/wD1P2UxSynb5LgvrUP5kvyFGLqDxSb9EV3Zewvhw9eIbz/VV++nP3ieGR/+4L8/5qn31L1mIf1/+p+yl6nN/AuEhkYZEU2PgKUOX+SkrujdivD67Lr9/gf/AAqffTR7GtDHTXL8/wCbJ99EtqKb9R/8T9laKGRcR9n+Sk/VRYX+Sk/VXbD2N6J/78vf9WT76Sex7Qx/7cvf9WX76lx6f9X/AKn7J/UZFxTC/wAlJ+qiIX+Sk/VXaP3odGz/AN83h/zdfvoHsg0bH/fN5/q6/fT8Sn/V/wCp+yX4fKuL4X+Sk/VRcq/yUn6q7R+9Fo3/AL5vP9WX76UvZFonjrF79Vun30xfT/q/9T9kvw+VcV5V/kpP1UMD+Tk/VXam7I9Cx/3zff6sn30kdkmhj/2zff6sn302eD9X/qfsl+HS9FxnlH8lJRFB/JvXaP3ptDH/ALYvv9WT76JuyfQ8baxfZ/8A46ffUs9P+r4H7J/w6XouMci/yb0OQfmPXZx2S6If/a17/oE++j/el0T/AN733+gT76bPT/q+B+yX4dKuL8g/MehyD8x67OeyXQ//AHxfD/N0++i/em0Pw1m+/wBWT76bNT/q+B+yX4dL0XGDED/FvSe7H8k9dqHZLoo/9tXg/wA2X76WeyXQ8f8AfN5n/wDjL99I+rfq/wDU/ZN+HSLiQhB/i3pXdf4N67QOyfRv/fN3/qy/fRjso0Mj2tbvAfdar99L/lh/V/6n7Jfh0i4v3I/k3od0P5N67UOybQT11y+H+ar99A9knD4/9u3/APqqffSzU3U/+J+yb8Pk6LihjA/IegEA/i3Ndq/em4c/K1+/H+aL99Adk/DWf/1Df/6ov302an6n/wAT9kvw+XouKlR/JPRcv+Deu2Hsn4a8OIb4/wCaL99A9knD3hr18f8ANU++lnp/1H/xP2S/Dpei4lgfyb0fsj+Keu2Dsj4ePXXb4f5qn30odj/DR68Q34/zVPvqJkg/Uf8AxP2THD5RyXEhy/yT/qoZUfxT12xux7hr8niG/P8AmqffSf3n+G//AH/qH+qJ99LiwfqP/ifsl6hN0XFu8X+Sf5ij76MD+Bk/VXZz2O8O4/7+vz/mq/fSD2PcPDrr18P81T76fjRcnf8Aqfsn9Rn6Li76hBH7JhmLD4Uj8MWkX8JbzknpgiuyT9ifDczlhxPdrt0Nmv8AzVAuuxHQ3XlXiK7ODnPqi/8ANQck02vCd4dn7hQ9SrL9lvyXLV1zTyrMba4AA/OGSafg4xtYIFiW0uGAzuXWugL2D6VI2F4gvGz0Hqqj/iqRF6POmSbnXb1T/iE++hH1+JtILSB7gpNhxCLUNA8ly2x4htLTWZ9Se3mYvzciBgOUnzPjtVjd8V2OrQ4mtp8oCqsXG2fcK2nFfo/jhe8jguNUusSxLLGxgUZBHTr1FUKdlcWeSPUrhix2AhBJPzp6F2JZSWAFjrn+ncqUMeImMtYAWnX+nVUGh8WWOjo8fqdy8jHLESAD6hjara44zsbuEF9Plz4AspxXV9C9EvTr3RoNX1Lia8tOdO8cerJyoPeSaoLrsH0v11kteI7t7bfldrZOY79cZ6YqGHYpVjPETcN00A+aanqMQDDBEBZumw+a5zfcb2V3knT58jAyWXwFRF4nts/3FN+mK7Dpnov2mss0FpxBdGQKXPNboAB7zmtIPRI0KO3Qy8W3azBRzKttGQD86pONVsbrSSj/AMQfoqKiesL7y2v7vovPo4nth/eU/wCkKWnFFtzACznz/SFegbX0SdGuCQvFN82Ov9iJ/wA1ST6Hukj/APdF8P8ANE/5quHpDI09qYf+P7Kk1NQ3QkeQXnhuJ4/Czl/TFKXim3HWzuPmK9Cr6IekKf8A9VXn+pp/zVIi9EDR3OP3VXn+qJ/zVZ/xIRrx/wD1/ZOK2Yal3w/ZeaLzWLS7mEvqlwGC8v0hUMXEH/hpvmK9T/8AY80gdOKr3/VE/wCalj0QdIHXim+/1RPvod+N08hu+XX/ALf2VDpy85nH4LzdpHEtppsMkZsLluc5yHXbaph4zt2ABsLjbzZa9D/9kLR/Dii+/wBUT76SfQ/0kn/9U33+qJ99ER+kcUYs2fT/ALf2RrMWmYwRtfoO4fZearnXoJ3d/U5hzfzhUUalbk/3LN+kK9Qf9kLSB/8Aum+/1RPvov8AshaP/wD9Tff6on307/SKF5uZv/X9kK6qc43LvgvMP4Qgx/c8w+sUfrcJ37iX5ivTv/ZD0cf/ALovv9UT76P/ALImksMDii+/1RPvpvx+m5y/+p+ybj9/wXnHTNNXWYnaB1jeNuVkfqNsg599Vl1YyLMY2UqwPKQR0Nb/ALQOHoeyjii50bTbsanEYo5GlmTkIbcEYU+FUNpqulaw0kmoNHbXvQBWIXl8G38a2GGGqjYHGxPu8PgtNkME0bWZrSc76D/KyVzacjFSwJU4yPGogTDge+tiSdMin/CFsPVpDy8zKDlt8Y8ay9rcJeTScsAjGcqM82B7zWNiFDHFK1gdq6+ljyWfUUwjIBOp5c0YqBqDqzBAclSc+6rdosbYFUd5g3MhHTmNZeLNdHFl6oafRqj0PGjHWgRgDzrmA3mhU6KBGaFH8aNAUkQo6FCnskgaPTh/Z9t/jU/rCixSMNG4kQkFTkEeFVu0c1/QpwbEFdN1O4mWJVUhULHmAHWutcB6YBoOl33fNzGHITGw3IrivZrJNrV9dJfSesJDHzAS77k11HUu0fR+FOE44baeKXUYkxDbAEK3t4PtDbYHNdtJWsqKcTM0B6+S3+M17OKNAtPrlhHckNIWVlGAVONjXnDXrKJkklPsvGW5XHXYmtBqfaTxrxGqNY2t2sKsSGt1dgx8s46VkrzUbofitRtGhD53wfroA1sDonRv262NvOyElqGPaWqpV+9k5mOT4nzpb8oU5IFR5OVJWETkrnY+dEWLdSTXLCoLQWu1Kyr2V1wnFHJqZ5lDFYmI9xr0hwhw/b6hpMU7TSxqAqKEA8vfXmnTWEMscgbkHLgkHHhXT+zfjSTh+aSY899A45HjMx9gdcgHxrq8KjcKThM9q9/Fa1C/sZBuuz21j+CLW6la5eaKMFkQqAc01ZatDf2ktxGjJ3bmMq3mKk6VrulcQoyWdwsrBA8kWDlQfP66znFN6tpev3EyRwJbkZTHKCc5Jx1NWMYXuLXDVFDU2KxvaFxKDbusUmAjjvJQxA8sbeFcQ1O8e+mDsAAuVUDyzV9xrqEsj28aSSCNlbmGcBjnxrOWUDX13DbBwhlcJzEZxmsfGqsuk9TjGgt7ys2slLn8JqXp9j65PyEkIBliPCvQ3Zxw2bi1sb25hRrKOFQqSDPenlx08hVT2f8AAY1C4guZLWAWNsQJCyD8awHTH6ya69HbxwRrFEqoijCqowAPcKOp6ZtEwxtN3Hc/RFQQ8EEc0puRUAACogAAA2AFVV2WcM7sAqgnPQAedTbr2V5icADfJ2rnvHvFIihfT4yixYV5JufH1fCiIIi46K9rVn+J+I9OgumkghcqzcvMDgO3mBXP9a1ZBEJBAQzOeVc9c1TcQa42r3neR86QoOVRzfSwT7RHnUKzk57qIyOSAcjmOaElxtrnmCEdwd9UBLWXJa3zWg0TSZLy9XUbmMplwyr4Aj7eldp4W0s65PLbmcwiOPn5lXOd8Yrmui31nDb24n5iFbLAITkc1dp4PuIJbqV7ZFACAnC8pxnatgQNp4TwtzqT1KOhYGM7O6cvezjurSaU6g8gWJ2I7sD8k15Z1PTG024AVzLCd1kxjPmPjXsjUtYTT7N5JygVlwI3/L91eVuOIu6S3TuxGRK+UAxy+6siuiM9K6aXdlre86qiraXxlzuSzULGW/Rh+VJmr61uGhlCcuQ7AdelZuJisqkeBFXEEjlc8xyD1zVGB1JYXEb3us+B9itJEpII6Zqi1uyW3tXAAzkHPuzVpY3a+q7szOm7Z+PnUi7jF1ayR4B51wM13NZTsraUhp1INvetV7Gyx6b2WItrr1SQsFDbYxmr2OQSIrdMgH4VWarZ+rRp+LVTzEZAxnamtPmma4RDI5XB2J91cFRTyUM/qkmoNvddZTHFjspV/Bdvao8TRgrL4k9ARioF5w+IrQPbtJNJzDYDqKkLv13+NR4bt4pjzu/LuMEnat6pEMjBHUC41AP6b7ohzmkBr/d3KoClDytkYOMeVAjBx199X8UVpcScxgjYvkFsb5PjVXd2E9gxt7mEpKDuD5Vz8+GOhZmBu3qL/HxQj4C0ZtwolKB22oMnLRoM0C0EGypC1fD12yXluuAQqED5Vr+97y2lYjHst9lcwsJHiuUZHZTvuD7q1mi3Uz2V2Xkd8DbmOcbGvR8DxATR8Jw11+S6bCcQLGmI87/JZPlxSRFzMWAJJ2xTzIQKkW1zDEgBGH8TiuRZTtc6zzYLEDQTql2enCYMJedMYxirNNCgbStRdFZ5FjUqWGSDzDpTdhLFKW/HRJ5d43LmtPpdg72F4qywFpUUJyyAgkHz8K6iiw+mkjta+hF/FbFDSNk0tfQ/IrmUsZjcqQdj4jFJxtW31Dg+e7m70zwRE7N7YOff1rHSQNFK8ZwSrFcg7bGuUxHC5KR+o7J2KyqmjkgPbGh2TQUN1zTWN6fZSDSHBxWVJGhSE1y0g9ac5aIrQrmKBCTnanI5CoIGKQRRUmuLTdNdSVctscU6kzKAAcYqIH5enWnBKux3+VHQ1Nueqsa5X1nxDeWygfi5MDAZwSaek4pvXUgLAh/OC9Kz3rCe/wCVDv1/nVtNxydrcokPmi21srRlDjZWN5qVxfFDcOH5M8vsgYzTBlOPAVEM2em1IL56mgZcQc5xcTc9VQ6YuNyblSWnxttSRcP+aKYDqB40DIT7qGNSd8yhmT5kJOTiiLc4wD08qZHt58SKBIWmMxOp2TXT3OU8B8qAnIO4U013nMcb0CyrnbrS436ToldOd+fdQ74+GKY5hQDYqv1hx3KWZSRM3kKcE3nioneDFDnFXNqSOacPU5Lnl6YqQ2rXTHJlc+H0jVVzihziiWYlIwWa6ymJSNirCfUZZQvOxbHTJJqJJMWz03pkvSS1DzVr5LlxUHSE7o2Od6QxzQJzREVnvddVlJowKAFHVVkyI0QFLHKAc5z4UnIpFqSAHMQANztQZSrFTsQcGrLR7GO5uojLPEq8w9gt7Te7FOa7YLBeXMiz25HPnuw3tDPhijhh0nq3rPK9vhdXcB3D4nJVNChQArPsqUBSgNqIDwpxEOKujZcpwE7DFlup6VZ6Wnd3kDE4CyoxPlgiotohMmAPCr3R4A17b8ygjvFJHuzXWYTQh5BCMp48zgtXe3VtxlOUu7adFtnPI0b7uG89vdVDqHEiyvd6RFp9v6uoNusxz3oUbZPgTtUTi3WbTVrmB9LeaOONGVxgpvn3da03B/YrxVxNYwatYpYPZ3K5V3u1Vh8V6g1qVFe0PyM0F9XaahbNZXuc8xw2J5uFu0LeGgGykdlnZ1Y8Y6lGl7cTx20cqxydwBzBSpPNk7bEfrrusuhWPYlw9dapw3cy3097NDA4vSrKqjmOwXG9WdxxBwl2bwQaXPpPq9zPaK0kljbrhiBy7nYncGuA6pqpmV4xLKRz5AYnzpUVLLiD72LYtNLaO/lkRR0TS3O/Ro/9lt9V7adcvpQ01lpmwwAI22/XVd++xq3/AIPTv0G++sE9wg+kSKAcHpmumZhFG0ZRGEYDGNGAWW6Palq7f3rp4/yD99JPafqx/vWwP+QfvrEd8i7HPyoesx+/5VP8NpR/QFZxWLbfvnaqP70sP0D99J/fO1Yf3tYfoN99Yo3UePyvlSPWE9/ypfh1L+gKJmb1W3Pahq3/AIax/Qb76Se0zVcf3PZfoH76xDTp/O+VI9YUedP6hS/oCrNQAtue0vVT/e9l+gfvoj2k6p/4ay/RP31ivWEx4/Kh36+Z+VS9Rpf0hR9Z71sv3yNU/kLL9A/fRHtH1U/xNl+gfvrFm5T3/Kh6ynv+VN6pSfpCj60eq2Y7RtU/kLP9A/fSv3x9Uxj1ey/RP31ivWU99D1pP53ypeqUn6Ql60eq2n74uqHrBZ/on76L98PVB/EWf6B++sZ60n875UDdJ/O+VL1Sk/SE4q+9bJ+0XU8/wFn+gfvov3w9U/kLM/5B++sW10n86gLyP+d8qb1akH9IUTWHqtr++Lqg/vez/RP30k9pOqZP9j2X6J++sZ62h/O+VNm7j/nfKmNPSfpCiax36ltz2kaof73sv0T99Ado+qjpb2X6B++sP65H/O+VD1yP+d8qbg0f6Qoeuu/Utv8Avj6p1NvZ/on76H75Gp/+Hs/0T99Yc3cZ8W+VF63H/O+VLg0f6Ql66/8AUtue0fUz/e9n+ifvpJ7RtT/kLP8ARP31iPWo/wCd8qL1pP53yqPDpP0hR9ef+pbj98fVB/E2f6B++i/fJ1T+Qs/0T99Yf1pP53yoetJ5t8qWSk/SFE1sn6ltj2iao38RZfoH76T++BqZ/ibP9A/fWK9ZQeLUPW082prUg/pCQr5P1LbDtB1MfxNn+gfvpX742qj+Jsv0D99Yc3SfzqL1lP53ypEUn6QnNfJ+pbn98jVj/E2X6B++i/fE1Xxhs/0D99Yf1lP51D1uP+d8qYNpP0hMK6T9S3I7RdUH8TZ/oH76P98bVfCGz/RP31hfW0/nfKh62n875UrUn6Qka9/6luf3x9V/kbP9A/fRHtE1Vv4qz/QP31h/W0/n/KgLuP8AnfKkBSfpCb15/wCpbX93+qHrFafon76Mcf6l4w2n6J++sV62n86gbtMflfKnvS/pCn+ISfqW5i7RtThdXEFmSpzup++rSw7U9QeU89pYBhuo5Wwf11zA3SfzvlQW6QHI5hVT2Ub92hJte7MC83C7pF2p6hxPZ3Wk6jaWDReqnlYRnmyu+QSdjjyrOadx/e8K30VxbWGmySFDytLCWwPn1rC2WrW5MaEyB8YzipOt6hbOtsqM5ZY2DMRt12qMVBStaWtaMh3HJaPEg9XdksNlteIe3HiDWJbaCW2sO4QMnJGjKvn0zg1ATtF1NuVxb2YB8QrffXPbadXkjUMx5MncddqlXN8q8qFmXG/s1ZDSUTIrtaAFnwVORptoOi6vZ9vvEFhGYILDRxyjkyICCQPMg709+/xxHMvMbHSAf8S3/NXI1vIcnHNv7qWL6IeL/KofhGHHXILp2mG5JAXWY+3XiWLJW10kZ6/iW/5qej9ILihGBFrpeR/gm/5q5Gl/Fv8AT+VK9di83+VI4LhzteG1W2pjuAuwf9obilv700n/AELf81J/7QfFYO1tpI/8lv8AmrkIvYv5/wAqUL6Lx5/lTfgWG/2mpxHSfpC7HF6RnFaLg2uknf8AkWH/ABUv/tH8VD+89I/0Tf8ANXGhfQ4/L+VD12InHtfKo/8AD+Gk/wCk1Lg0R/pC7IfSQ4rJ2s9IH/lN/wA1LHpH8Vf+D0n/AETf81cZF1H/ADvlShdR/wA75U//AA7h39kKQp6LmwLsR9I3iv8A8HpH+ib/AJqH/aM4q/8AB6R/om/5q4961H/O+VEbpBj6Xypf8OYf/ZCc09F+gLsY9I3igdbLSP8ARN/zUtPSP4nVuY2WkkeQjYf8VcXe6TGwOaWlwp6Z+VI+jmHH/ohIU9He2QLu6dkOh9rcMHEmqareWt7dqee3tihVdydubfxrzXxxw5ZaHxdqum2Ek01rZXLQxSTY52CnGTjbrmuncD8R2ug8R6bqVw1z3NtKHcRD2iMdBvXSOLeEdA7VeCb/AFThbRbSHUxcECaVBDIXG7kkdcg+Nc1WUz6Sa05JjNraABuu3kgMUoA2TMDdp7tB3LzVHfT8XR3NhNbiNoI+/j9XBLOQcYwfDek6NoC2UFyt9DLC4jLIGPKebw+PwqRxZwNrfAXq0+qpDB67zLEYbgOTjBOeXp1FPcP8TreW0umagiyyLCRbyd3ltgSeZvE+RoiklhlmDptX6gHx2VdM+OaYCqPbsdTsegP0VBcMIl5j4b1n51y7HzJrQ6jGxVKop4yHb41k49G7Nl5BY9SNbKIq+1vRuOlKA3oMK5XJ2UJZKAo6GCKFXAJ0BQoUKSSFJk+gaVSZd1NQk9kpKw0LXJtCgunhUMZ8RtvjA3NaLgPhkcU65C2rrdC2lLcpQ8uwUkYPlnFYkOO7KnzzXVOGddtNKsbW4DHvoYh7JXOcjFa+DMNTaN7uywXA7zdH0beL2XnRvJda4f0bT+HtLTTrMyGGMs4MrAtknJrj3FGhwzqZVHNEzEsB4HPWtlo/EEevAnn5bgknkwRlR4ihe6VJK/Nbqu/0kzgfVXTRRNYC12oPJafDBbbkuFataR2N13URdl5QSW8zUSFBJKqnODXQOK9Ixe3NsyLGSo6bgHGawzxGyu+7mxlDvy7jpXHYphvq04f/AEOPl3eSxaiDhu7lKVcLgdKlaZqM+lyO0YDhvz96jgggEeNCj2EsIcw2ISaS05hutba8a3loA8DxxMy4blcg/DrTd5x9drp7WoW2CZ5iBnmfJ6Zz0rJsijJNQrh1kkBTJGMdKerxeWNmlrq11ZJbdW1lay8T6m5nlaNQC/sjIAz9FfKuh8Mdn1jazQXoN0RJheYgEICdyDjY1iuECbW6YSfmMdt/KvS3CvEWialw3FZ286wqkYtmjlARyxGCQPHJPWiKKnYyFtQ9maQm5J3CKpWNycRwu66stMsINKso7S1TkhTON8knxJPnTt3OtnavcOSVQZIXcmoT6XcaPbQRaZaiaGJSrrJNh/djPU9ay2v8QXLpJ3qSW0MP04XGG5h51cyHiOuCiQ3MdEfFHGZRBBbIhhkjYSGZcEHP2VwrjHiZr6eeyj7uSFuUmQZzkb4qy4z4rjuorqzEj+sMVBwNlGc4BrByMZHLsSzHck+NZ+MVzYmeqwHU7nzBCCrKkNHDYfH7JOd60OicMS380KkP3rsOVEwdvfUbQtAuNSuYmCr3X0jk9fdXauGeG/wLGZJApupBhmG/KPIftqrB8MBBmqG+A+qqpKbN23hVUPBUENjZYkkZlOHHKBzHNXV/xNPwj3b2kcMks2VYOfogb9BVwJCux8KxXF0mj6a3sx81w+ebq2/vPhXVtcHdl+y1bgCyRq3aHqmr3q3E6QKEjEYjUHlG+c/E1iuPdUOo29mzwRrKrNmRc5bYdaJdQiXvXlJydyQNhWf1fU/wiUCryxoTyg9fiay8ZqIY6N0TTq7Ye8IGpnbwy2+6r1JDA+Rq0tZWMWcDrVVVhZZaH6zXLYXIWyEBZkZ1VxpUhkM6MQAVB3qw0udp4nR+X8XgDzNZ9WCHOOtWmlXcUbMh5uaQgLgV3GGVlnsY87X+Oy0aeWxAKlapp0WoJGrs68rZyuN6yJdrO7fk35GKjm8fCts7ePvrI6np1xA0lw/JyNIcYO+9BelNL7NTA3tDcju2Ua1mudo1UuwuXuYmZlUYOBijulGOfHtdKr9Pukt1KuWGWzsKuniDrg/GhKGQ1VNYm7huqWHO2yhQTtEQAPH5VcSTQah+Mu4TNKBgOr8m3vqneBkYjyp2G5CnlBO9E0sxivFLt0KnFKWdk7KTqmgSIGureCQWxjVxvzAbb71SBMdK2mma9HHB6tdD8WowGAzkeRFI1azTXe4OmRQiOIFX27vc4+dFVWDwTDi0x7X6f5yRU1FHK3iQHtfp537u5ZS29mZTVta6rNZQyxxrGRJ15h02xUF7GW0unjkxmNipwc0or7J+FA0bpqe4GhCAjL4jpoUMZ6imJkHMRVuYx1qvvIWDtJkY2qVXSljL7qx7CAmI/ZGOoq94fuGEN9GG2MIIXPjzCqACpmlZW65h15TiqsNmLJ2WT00hZICP5cWV2rNncePlWXYkzNsfpHw99bGykMznn3kHj7qopYsSNkEbnr8a28YpDIxjgev0RNVHma0g9VXd2GOcUiWIBQceNW0FnJO/LEvMQM9fCnJ9JvSoCwMxz0Ug1iuw1xYTb4IcUr3Nu0E+5Z5kAGaTy5qfdW0tvK8UyNG46qwwRUfuj5isWWmLXWshXRkGxUUjrSSKfZeo8abZSKCfGQqiE3uKLJ60oiixVBBUUOY0fNRAURFK5CSPmPnRjrRAUYBJp23KSVRDrR8pFJzvVhuN06XzlOlGxDAYOcU0TmjU+dLiX0T3SlJVgRTkqAEFTkHxpujqxp0sU/KyT0os0oj370CBnCmqy0qNkjmo+aiIxRCq7kJkvmo+tN04o2qbCSnCFDGaUBmjC1cGkp7JGKGKdEZpXdHyqYhJT2TGKGKeMRx0oilMYSErJnloY3pwqRSMVS5llGyEbtFIrqcMpBB8jS7uZri5llZixdiSfOkYoiKWdwYWX0vdPc2sk0KPFGKqsoo1G9PoNqZXrUiP6NG07blTarCwUd70/Jq/gvLSwmUQCWaZFKuQV5CSPDx2qjsFJl/yaat5TDc3AH8oa7KkmMEbbDclaEM5hAc0aq70vgHiS+7qSLQ9Tkt5vaWVLV2VlPiDjBr1dc3VtwV2b20ekXVv65p9rBEEcqXB2B5kznIydvCs5w12jXGicH8O2VrbrOY7CHnaRyADjoMVy7iPWfXNa1CfuwkstxI5H5pJzUqPCX1TwHtytab9b67EcltUOGCK0kujdD49ylcW8Walr97FcahMk0qx92pCBcLnOMCstNIVQt1o3YlvjUSV+8O3QV2rI2QRhjBbwRNRPc2aLDkOiS7GXqD9VTFDWsyJIwPP091HBKI7UrGcuPaPN0qtmmedsuxPl7qqfJk15lUEtiAde5KnXLjvdsYwKYdsLkHeouDgeVGDjNVmoJ5Kl0mYk2TwkJO5o+Y+dMZog1RE1lDMn2c+dJLE03mi69KiZSmJSyxA2od4c70kAkZxSSd8YqPEKYlK5qPOKbb2fA0Q3puIo3TmTRZNNO3LShkj41Hick1+SUWNJLGi5aSykDNQdIUxugWoi23Wm2aiU83SqDLqq7p3n99JLURBxSCDUS8piUrmNDmNJ5aIiqy8qJS+Yik8xpotvgURBqszFRzJ0saTzmmjtSeYA4qszKJcpHPTtjp95q+pWmn2YQzXcyQR8xwOdmCjJ8Bk1FxUrSNWOha3puqABvU7lJwD0JVgwB+VQllOUqLnaLa8ZdhnGXAuiTa1q50/1WB0jfubnnbLHA2wPGs5wjwhqvHOvQaHpHc+tzo7r30nImFUscnB8BXsHhPjzhLtm4VlbUbK07kyYnsb11ZcqdmGcZGelWei8Jdn3DN+uo6Rpeg2N2isqzwMisoIwd8+VcuMbnha6OZvb+HdzQ/Ec0WO68gdoHZTxL2aW9lc649iY712ji9Xn7z2lAJzsMbGsiXNdI9ITtks+0u80200q2ura009ptpip712IAcAdNgep8a5gCGwM5OK2cOqpnMtN7SnHI7UFPc5z1ouY01jJoZxRxmKtzJ3JocxpqgTgZqPGSuneb30fN76YByMik858qb1hLNZSSxouY0yDR9aXGKWZO8x86BkNRu8TOOagGBOAd6iKnolnUpZWVgwOCOhqRd3xuFjAABC+18ars0M1Y2re1paDoVISEAgc1Psp1im5mGdiMUu4k76XmwBt0FQYzjIx1pw1cypJjyKYkOXKpQkPnQ70+dRKAqYqnJ86liVvOlrK2RvVbLdRQtyu2/kBmhFdRTEhGJI3wRimGINzZb6+Kjxhe11bCY56ih3h/OqvHSjBA8KJFa5WiRWHeN50pJTzdRUFJMbeFL58jOKubVlSEisBIfMU5ze8VV8/uNL70USyu6qwSqy7z3iiMm2cioAIK5omlAQrsMkdatNbpqpcVTbOT1iQrIwUHNPSlrWQIh51IzuKqAxDBh4VLiuO82bYioQ1eYZTunZKC2x36q5trkjYnG1a3hvjrX9EsfUdP1N7e3aUyNGoXBY4BJyPdWDeN1jLRjJ64Jp7T3Zo+ZgAwaipoWT9iRtx3i604KoBwZI2/iu69vnDdpxdwpo66NEmpahBOpdLHEskaNH7RKrkgZxXnaThrW+D7+O81XR761t3DxLJPAyByV6DmG591dV7N+MbzhvW5ry2jg5mt2iIfOMEjy+FF268cXvFfD2lJeRwqILt2Uxk+KY3zXGnD5aOVsYAIab5r69bW6XWdV4e+L/AJiPUN1vz8lyC+ube6RGgV0YEhkYg48iKpblRzNt41O0pxJdXGB0UfbUfUB+Nb+kaoxAmeDjHqfhosiZxkbxCqvHWiIpZG9JO1ci4IKyPrRUKI1FJHRUOlDNK6ZHREAgg0fSipjrokmWj5T51LtdTngkQu7uijHJnamqQ6jBPjVTS+B2eI2ThxabhbjS78tbQyxOUbGRytuK6no2oxapb97GCGXCsp6g4rg/Dd1Fb3DpISDLyqpx45rrfZ7cCzl1K5fB7qAEAnr7XSu5oqv1ulEp9oaH+d+63KaXiRg81B44hiF3NMFIkJQE/VXJ9Z/7yn+I+wV1LjnWIbh7iZk5JCFcxc2SAB51yi+uBdXckwXlDnOM5rJ9IpG8BkZ3v9ChK9wsBzSI7l0bJJbbGCamQyiRAxwM+GagFcUVc3FVSQmx1Czg4hT7mRUjxgNzZHXpUW0jZplIGQpyT5UzUqymWMlWJHMRjaptmE87S/QJ75naqeJZIstGzIfMHG1bPgnihLU2ttIzPcCXnDMfEHIzWOx4GmyvKwZDjFdPFK+J2Yajoi4pXRuuF6l4X45OtXctvfC0twsfeKwkABOcY3PvrnXaPrEE2q6jy3SmOOYseRwchQPfv0rkbXsijmYqB7xVfe3rXDcjAAIT08ajNiUFKTIwakaBEOrWtuWtSboetXsrwI5WRyyg9cE1JsdGvpNQit/VWYuepUlcee1K0R8Xa5GzEfqIrs3D+hzSXMWpiWIRDmHKSeY7Y+FA4ZhcVSz1mRxve9uXW3vVFNTNmBe481X8F8PxwW8hmjbkGFBAwSfGtw8g6YpgkoQGxUTU9Vg063LsQ0rbInmfurpyMxAAWra2gUfXdZTToeRfamkBCgH6PvNct4ruj6kD3p7wSAsQfawancTa+IXmkkkHrUu45R0+qsBc3Et3cPPMeaR+prJxfEG0zDA3Vx+CBrKgNBYN0DM+W9okHzNIUbZowm2TRMfCuR7VrvWQUTdan2Oe4/yjUAdanWf0frojDv8AVupx7p9yNqctpCk8bDwbNRYoijOWGM9KcrZjlcCH2srgTutBbXPrSsSoUqcdahcQL/YA/wAYP201pcwSUxEE94Rg56U/rv8A3Y+/5a/bXQzz+sYdITvY3RhfnhJWXAww+NaOKbnYqCDt4Gs4anaLteH+ga4/BqkxTcID2iAgIH2dbqrSZTzHbqKghd8HbFWzDY48qhvGcbV0lZTm9wiZGa3S4XDDB6itHw0yiCcZGecYGfdWVhBMmegFWdmpB5h1BFaGEzEPBsiaKYxSB9rqVxBp/dXImiVz32WbbODVO0bBT7J6eVbpZPWrEMvss3hVZcxmJWUjHsnGK16nCWPcXtNr9yNrKFpcZWHQ67KB6qvv+dQ7q3UOykZX31ZqeZgvjjNKltu9Qg7ilLRtlZZoVTog4aLNJCrzzIFJVcYx4ZqTaw93OpVWG/jUu2tTG0hLA8xHQVKjtySDkVkU2GOBDiNb/VCxwc0q2laJ+cdem9S3tYNQnjaVGzjl2bG1MiAjqR8qnWo5ZU8cV0cUN25JNQtOBlyGu2WM1nU+5ubuytYlSIFoWYksWAPX3dKgaZqVzpc7S2zKGZSp5l5hij1qE2+r3cZbnIlb2sYzk5qIuxryOpqpnVRe5xDmkgcranQWWBPNIJiQbEHyW2tLi04ltNSu7mwWO7t7dX71JWw7dB7PQdKoJEVTsDVlwhJmx1tB42o3/wAqmxaEn6Q+VdNFG6qp2S7uN7nTqfBaNReWKKQjtEG5sNe0d1USRjJIBzmmiu9WMloRI3tDr5VHngKsPaHSsmekc25ss1zCFCdB1A3pPL7qldyfMUO5OeooE0xPJV5VFKYHQ0XL51L7k+YpBgP5wqDqZw5JsijlfGgGx18KeaPHjTbx+yapdGW7KNrJakN0ptlxQR+TY0sMGqQIe0X3T3umsUVKbZqIVQRrZRQBpXNRYo8VMEp0XWknY0sURUk7Uzh0TFJLE9aIdaPBzRharsSUyCjJpwCgFxTiiio2KQCNEq30bQZdSu4o3WaKBwSZgmQNtqn8IaIt466hK69zFIQE3yzAbHPTFbOaeC0hMsjCONRufAfVXZ4PgbZWCefRvIdR9l0OG4OJWCeY2bv4j6BZ8cC2wbl9ekP+Qv306OAbZjhb+Q/BF++lXHE3eNiysZLlQxBYnAI8x41Dj1TVObPqDfrrdFFQbBnwJR5OGMNhHm8Myj6xwdc2Mqi1SW5jKZLYAIOemKorjT5ocd5DKm+PaUitnDxVcWaCK609+YnI5jgYq2F7Ya9bi3lliaMhuTmGe6YjGQD0NDT4NBLfhaH+e9VOw6jqb+rvyu/Sf3/dcreLHhTJQCtvrPBU9pbGW2mW8xkuqLgqoHX31kprdkNcrXYW+E6hYNVRS07ssrbFQiKSRTrLSCPOsN8dkEQkYoCjIoAVTlTJSjepEf0aYXqKkR7rijabdTarjSgDPg/m1VNM34QnTIx3jbfXVpA62Ful9KGaNnMQVOucA0P3IambiaefubZMs+ZHycHfoK6eTivZFHC0ucDcgcgRpfxRroJJGARi5C61pd3MdI09crhbeMDb+aKy+oyPJf3DvgsZGzge+tBYEx6ZZLgnECDPntWbv2xdTk/yh+2u2omgX0Xb14y08fu+SjySuhGMVFZ8LkedLllwRkE0wWyN6eeXWwXPvfcpSTuhJB67U3QzQNCF5O6rJQ5j08BRcxoZos1DMmR5NDcUAaA3prpkRcjJPQV3fsM7J+F+P+EZ9U1eK9a4S9eBTFcGMcoVSNse81weXaNz7jXqf0Ugf3uro/8A+zk/qJXP+kNVLBTZonWNxqEJVyOYOyVdf9mzgLGO51P/AFw/dSR6NfAIOe41M/54furZ8eca2vAXDdxrl3bT3MUDxoY4SAxLMFHXbxrlo9K7QGOP3P6t/pIvvrlKaTGKpnEhc4jx/dBsdO/VpK0P/Zu4BP8Ae+pf6433Vzvtx7HeFuAuCl1nRorwXRvI4fx1yXXlYNnbHXYV3ngfi22444btNdtbae2iui4WOYgsOViu+NvCudelQzDsyTy/CMH9V6ehxCt9dZFNId7EEpMmkzgErhfY3whpPHPG9to+rCZrZ7aWUrFJyNlQCN69Ap6NXAJXPcap/rh+6uG+jHKf31bdD/4S5/qCvYyOAFXzovH8TqI6kCF5aLcvEqVRO7N2SuVn0auAc/wOqf64fuox6NXAJ/iNT/1xvuqiu/Ss4ftbqaA6DqrGKRoyRJHg4JHn7qjP6XGgINuHdWP/AJsdU8LG7Xu7z/dN+f1WB9IHs14e7PfwH+BY7pPXjOJe+mMn0eXGNtupqz7COyHhfj7hi+1LWEvmmhvTbp3E/djl5FPTHXJNY7tx7ZLHtMfRRp+nXdmbJpeYTurc/Py9OX4V2b0Tn5uAtR/+Zt/u0o2oqamHD+24iQHU89+vgnMrgyxOquP+zLwB/Jar/rp+6lD0Y+z/APktV/1w/dW6454vtuCOGb3X7q3muIbTk5o4iAzczBds7eNcmX0tuHObB0HVx4fTi++sinkxWoZnic4jbdVNdK4XBQ449GPQRwzeHhSG9bWvZ9XW4vfxZPMM5yMdM15bvIJtN1CbTr2JormCRoZFP5LKcEfMV9FbeUXdvHOoIWRFcA+AIz+2vLHpdQxW3E3D8kcUUbPaSu7KgBY94NzjrRmD4vOZDFKb87lPHO4GxV52O9hXB/GfAVjrerRX7XkzyhzFdFFwrkDYDyFbb/sw9np/iNV/11vuqX6N0ok7JdKPnJP/ALw1pO0ntEs+zbQY9YvLO4u43uFtwkDKGywJzv4bUHV1dW6sfDC8+0QBfvTPe4vIBWO/7L/Z4f4nVf8AXW+6iPot9nhIHc6t/rzfdVA3pc6BnH7nNWP/AJsdBfS54f6nh3Vvh3sVWeqYuf1eY+6WSVebtG0u21DjW10ibnFrLqi2hAb2u7MvL188eNesG9Fvs6cEGDVcZ/8AHN91eUOD7tb7tG0m6QFUm1mJ1B6gNNn9tfQrkJzg+JqeL1czMga4jdRc82C8HdsvC1jwF2kahw/oqzpYQxQuglkLsC0YY7n3msbYx3WqXK21rbzzyswAWNSxO++wrpnpNMydserbZ/EWv+5Wut+jB2XnQNNbjDURKl7qEJit4HjaNoI+Y8xYHrzYUg+XxrQ9fMFG17nEmw+SmHkN3V5H6K/Z0wDtbaqrYGR68236q4V6QXAOgdmvEun2Ghx3IhnsjO/fTd4S3ORsT0GBXtbGRjNeSvTAj5eNdJyc/wBrD/vGrGwurndPYuJ0KrY4g6LonCvo58Batw1pF/c2+pme6soJ5Ct4QOZkDHAxsMmrb/sudnbfxWrD/PT91ZDhz0odA0XQNL059C1WR7O0ht2ZZI8MUQKSM/Cupdl/axYdqFvqM1jp93ZrYSRxsLhlJYuCRjl+FTrG4nADJISG+Km8SN1Kzf8A2W+zz+S1b/XT91Ik9Frs8CFu61f/AF4/dXSuLeJIOEuGtQ125hlmhsYjK8cWOZhkDAzt41xub0teHeUj9z+sH4SRffVNP+I1LS6Ikgd6i0SO1C4hxTwdo2jdszcI2y3A078JW9qFaTmk5H5Ob2vPc16L/wCy52dnP4jVeuP7ub7q84T8W2/G/bxZ67a28sEN3rFs6xykFlwyLvjbwr3XkDJPmatxCsqGNY0OIOx+CTnuXgbti4b07gntJ1Ph7SklWxtu67vvpOd/ajVjk+O5NVnBXDs3FXFOl6XFa3dzbzXMS3QtkLMkJcBmJAPKMHrWn9JB1/fo11vze4/3K13z0auzCDhThmLia5ZzqmswBiBIeRLckMi8pA9rzrQdXmGlDnG5IHyUhIQ3VSP+yz2dAn8Tq/X/AMcfurzt2+cGaP2dcbppOix3C2hs4pj38pkYMxbO/lsK91nAXavGXpdMv75agdRpkH2vWZhtVK6Q5nXsCoNcVyFSGUNnqM0dIh/gU/oj7KXXVtNwCixslKcHal8xprpSwata6ykCnKauJe4QN5nFK5t6j6gwaFfcwNNPIWsLm7pPdZpIXfuwzsT4Q7SODZtZ106j64l69v8A2Pcd2vKqqRtg7+0ase1v0f8AhDgrgLU9d0NdWa/tjF3Xe3PeL7ThTlQu+xNV3o1drWjcO2EXCV/bzJJe37y+uGVFiiDKoHMDuPo9ffXqETwyRgoysrDIIwQRXJ1cs8U/EN8p1HehCDfMvnNbTu4KvgODvgeFOliK7l6QHYtqkeqajxhosrXi392udPtLRi8ClN2JXYjK77eNcGil7wEEMpU8p5vOujoq4TMAvqiI5L6FPc5o+9bzpHQ0M0dnPVXXTolNH3hx13pnNHzVMSkJZk737+BGPhRNIzgcx6U3ze6hmpmVx0unzJ6OU4KYG/jjelhipyDTCtginA6k+NWMk71IOViLyUgYYfKpEErSKS3nVYkoHhtUy2mUIdj1rdpakl2pRUclzqVquF5DJeurYwIvL3io3abiPR7Ujr35/q0OG7gQ3cjFScx/tFQ+0i7FxpVqApGJyd/6NUYrcNc/lZbU5JwuQ/zdYrh8Znuf6A+2kahtK/8ASNDh1ws1znxUfbRagfxrf0jXIhwOHNPefmVyA/0Aq/zpJo89aI1zjtQhkgmgKFCh7qKFCh1oUkkVDNHRYpikjzREZoUKjumSEkaCVXX6SEMPjWj0njC/tFkbv4lZ/ZIZBuOtZ5l5hik90fMVOlqp6Rx4R0PJWxyvj9kq31niK51CZyzIxdeV2CjBGPCqZVyd6V3R8TS+XwAqE8stTJnlKi97nm7kk+NN5p0jY00dqHmCgnViVoubB5qb3Q9MEb1Ktl5ohjbc03cJh858KvfT/lNeE5Gl0uO/fJ7zcY2wKW98hU8vNnw2qEFoFCKdtZUBtr3T5ylyXEkicjEY+FO28KFFcjeo1OwymJvEr4iqYZhxM0uqYHXVS1ZoZFkTAK9Dit7wh2g3FtcRW19Oq2q8xZORQDt4Hw3rAJOsgPhjzpEuFUnOR5VvU9bwBmYbtPJERzOjN27LvqcSaXeRrMLgqGGwKmsXq2rPPMJJ3DvjlRVGNqxlpxbLa28UCxezGoUdPCoN9rlxdSB4meLb2sHqa1HY5SMZmabnp/LI91bHlvzTes3M9xqE3fsGZG5AQMbDpURApyTSXdndnYkljkknJoq42WYvldIdbk7rIc67iU47+Aps0KFVveXKJKHUiptofZ+uoPjU+0BVQdtzmjMP/wBRTZupDgjG2KbJp65n7xUAB9kUxWxNYOsCrnb6J60YrcxkHGDUjWppDa8mRysFY7eOaiIMn4Um9flh5DvzH7Ku4xZSSMOx/wAKWezCFXEU5b3EltJzxkA4xuM02dzRVy4e5jszDYhCA21C09lI09tFI+OZhk4+NLuIwgGAcedRtGuVmt1iCsDEApJ8ck1azw8ycpIr0qjb6zSNeDc2Hnz+K1mDOy4VXsOgqVaMTzfVTEickhTIODin7ZSvN76hStLZFBgs5aexkUxRqCo9noPCnbm3jljYsM4U+NVNjLySIQDkCrbvRJA5AIIU5+Vdcx2di6SmlbJFlcs7qV0/qJOmuJLkMuBFhmA8dvKl6Veyz2ix3Lg3a83eIdmXfbI8KquGJjHrMKqP4QFNvhUrW2bQNelmQrMbpOblbblyf19K5OHEXEivc6zPZcOQ55rczsFixyEs9Y5XsRyHerQWgc+wuN98VKWzY4whp2BCA2RjpUTXpHi0e7ZHZGCbEHBG4rp5SyCJ8tr5QT5arS4TWMLyNkhNR0wMySX1srBsEF8Yp6LUbCCbL3ttgZGe8Fc3BJOTR9fKuCZ6aT78JvxWOMWc21mDRWPEUsU2tXbxOsiM+QynIOwquA3oBRSuXA6iuUleZZXSOG5J8zdZkj+I8v6m60XCE9vDHqkc9xDC00CrH3jcvMebpVgsUGd7+wHxnFY2gK2aLGn0sIhawG19fE3RrK+0bY3Nvl7+pv8AVbWTQ7px3kMPrEbDmEkJ5lI+NU9xBlhhPDypPBskja/bpzvyhJPZycfQPhVq9rzcpz4VvUrm4jAZWNtrb5fdFFjJYhKwWuSN77W7h1VNHavy+0hJpwWMrAFYWIPkKtYrGWQnuY3lK9Qik4+VK1O8ttDFrHc2ly8s0PfH2wnLliMYI91M+jgp2Z5zlA57/QqIpQG5nmwHNVf4MuP/AA7/ACqNNayxyFWjYe7FT/3UWHjZXX+mX7qdmUXKQ3MccqxzpzgH2iNyOo+FUZKKoGWmfcjXbl7wFB0MbheN1/54KlkgYdUNMvGwU7Gra4hKgHcfEVElTKHfes6pocpKEfHZV5jJP0TRBMHapGMGmM4aseSMMKoISXUHPgRRE+GKOT2nyOmKTjIqg7myiUARmjyD0pB2FEh8Krz2Nk105QPT30VCrE6NRSgu+aCdaVipsbcJwEMUpEaeRYUB3+kR4CiAqbpUSkvJj2ublz7qNpIONK2Pkd/BSaLmy33DVjGmiRd04UksxDHy2wKj6xHJc3MVo4XuFAlORuTuOtTuHpFGkwgKdi2fnTOrty6hbyAjEiFOXx28a9ViiZw2st2dF28rR6iwcrNv8EiGBVUKihQowAPCrng+x0/WOLLLRL65lgW6De3EAWDYyBvtviqdefmGASfdV/w/2fcV3mv6brFrpc0cUEqSGSVhHkBgds79KsxGfhQHK7KfcgpHOa0ZApfG/CVxwtqJhOZ7V94rgJsQc+yT05h4isbPpCTN3sH4mYbhh0+Vep9d4f07iDT5bC/QvGxLIQcFG8GHwrJWvY5oFuc3Oo314OXHKqrEAfPbNYFL6QQPhDaq+YdBv39yk58cjbSBcP07XZLaRI9SxAM4Dt0ceOKmcT8N2euwtqNjcmWdYgEEa+zPjAA92Bmp3aJomj2Wv3GnaVIZLWNVAYyiRo5Me0MjoQfCqPQdTmsbgaVctlCMRNjYk+/31oysbPG0u9lw06+9ERzCQCmqdWO2PMHksDd2slvM8ciFHRirKeoIqIw3rdcZ6HHEh1KMJGC3LKuDlmJ+lWJlXlNcHilCaeQtK5evo3Uspjd7vBMEUWMUthSaxS1AI16ipywIkUTiaNu8zlQd0+IqCNsU6jHwoule1p1F1NpAXTrDTtA0W2UX97p13bs4lhuHzyl8bgD3VRcU8TI08yafMk0LH+EjOQB/1rMajeySaZBatkrFKzqf6QGR+qoFnc9w7cyl43UhlBxmtyox8wycCMZWkDtcxp9/gtepxW7ODC0NGm2/+F3OyuUm0LSGXBcWiBwB0OKy+oHNzN/TP21pdItguiWMwb8W1tGyrjfoMDPSsxqB/smfwPO3213VARw73voupryPVYv5yUCfBYY8qbOMUpxuM0g9KqkN3Ernjuios0dFQ5TIUKGKKmumR0YoqOpBJJmP4l/hXqj0Uj/+XF1/8zl/qJXlaf8AgX+Br1H6LL8nZvdn/wD2cv8AUSua9Jxelt3hA1gur30lWH70WrHO/fW+Mf40V4uht52YN3pAPvNex/SRmB7IdU/x9t/vRXkG2OVRvAigfRyMOgc08ifoh4WA6Fe0PR2kLdkuiFjvzTj/ANVqqfSocfvYJv8A+0YP6r1Y+j1IF7IdDPm1wf8A1mql9KOQP2YL/wDMoP6slYsQ/wDlM3+8/NVgdu65F6MeG7VLR8dbO5/qCvYyqCFJOMV449GI8vadZknYWVz/AFBXr1J0kblBFS9IATUg/wC0fMp5hcrz9eeiMLq8nuP3YFe9kaTl9S6ZJOPpe+qnW/RJXTdKvb8cYM5tbeSfl9SxzcqlsZ5vdXSp/SL7PY3ZPwnfEqSpxYv1BxVLxH6Q/Ad3oOp2sOo3veT2k0SBrNwCzIQN/iRVokxSwzA2/wC39k2V/NeRra0wwkZ+Y46eWa9e+ieoTgDUD/8A7Rv92leRLB8p8h+qvW/orzqvZ/qI8fwm/wDu0rRxVrfULt5kfNTcPy9Fsu3ixvdT7LdctNNs57y6l7kRwwIXdvxq5wB12ryLY9j3Hl7dxxRcJayrzSBVaW3ZEBJ6ljsB7zXu1ZXc+zkn3UZnmjO6v8jWDRYk+lj4bWg631932VI00StOV7OwtoJcB44kRsHbIUA15h9MNx+6HhwedlN/vBXoPi7iqx4S0C513Ve+FpbcvP3SczEsQAAPia8Sdp/abqPaVxFFc3hhENp3kdskcfLyRl8gMfE9N6lhtNIX8flt7z0T5TbMvWHo2ry9kWjk7e3Of/UNXvat2dr2m8NR6N+EvweUukuO9EXefRBGMZHnVB6PDiPsg0M56mf/AHrVp+MuO9G4F02PUtcnlgtpZhArRwmQlyCcYHuBqqfi+uudF7WY280i05rhcXf0PFYe1xmw/wAy/wD8q5N20djjdlNzpMKa4dSGopK2TD3Xd8hUeZznm/VXo0ekn2dscHUr/wD1F64l6R/aNw9x9c8PvoF1POLRJ1l72Bo+XmZMYz16GtOF1e6QesA5fC30Tlp3K5zwFCI+M+HVB6anbf7xa+ieSM7jqa+dvBOV414dx/70t/8AeCvoEb1udhj8o/bQ+Nx9toA2v9Ezm62C888W9nrdoPpLXsNzEx0uzhtLi8cxF42AiUiJjkY59x9Rr0as1tawhU5I4o1wMnAVQPsAFVUVjZWt/e38VtHHdXzI1zKPpSlF5Vz8F2Fcg9JHtLk4X0SLh7TJZIb7VI2MkqhGUW5yjoQd1JyMHHTNCkOq3RxDQAAfDUp8t913cTc65Qgg7gg9a8mel4zNx1o4P/u3/wDtavUlnLHbWFsgIwsMY/2RXlb0t7gSccaKR/7t/wD7XpYW3LUX8VECy4SLWQv/AApx8TXqn0OYxHonEwLZ/su3yf8AIavLgJJFen/Q/fl0Hig//GQf1GrYxRjRA7L3fMKT2gDRdQ7dpP8A8o+Kgp6WDf1lrwGkM0g5hIQPeTXvTtwkD9kXFfn6g39da8KQH8Xt5mgsIiDgQ7r9lFrdbFaLs0hWLjrh0uQWOp23+8FfQ8gENnzNfO7gJj+7rhzH/vO2/wB4tfQB5WMj749o/bVeMMGZobyv9E7m8gvPN/2dzcaelDqdy4eOw0t7a8lkNuZI5GSOMiIk7AnPj4CvSjd3BHzMyRooyckAKKprKwtLC7vb2CFY7i/kWa5cE5ldVCgnJ8FAG3lXHfSN7R/UYtM4QsLmFpNRnQ3wBDNHGsicqHfKknf3ihcjquRkY0AAHluUsq73zb9RivGXpcEfvnY8TpsH2vXsJ5uVivlXjX0smz2ok5/9mW3/ABUsOGWQnuKjay5ZCPxSf0R9lKpMX8GvwFKrsm7BGDZCjoqFOnRiikiWReVhkUYpVOQCLFK11AVZbKYSpnK9D4N7jXo3sN7fbe3gtuG+JrgiMkJb3ckme4GMcjZ/I2HLjcZxXn2UDl3zv5Vq+yfsq1ftH1pRZ97babbSp63egjMAOSOUEjmbbw6VmVEbGNMb/YOvgeo/mqp1Yco2K91RzxBUkQ8wYBh1GQd68odtXYXfcPXkmt8LW99f6fMs93ekrGFs8Hmxtj2cE4+FelNLhGh6TZaa91cXr2sKwm5uDmSXA+kx8zWF7aO0fRuGuEtR0q+Ltd6rYzwQxxYLLlcczAnZcnGaxabixv8Ayv4OqWQ8l5AilEiZBz505nNQ9PfnVyPMfZUvFdVBKZIw4q2N12goUKFCrlNGDQohR090kdKBpNDFWNKcJ9TUq3I5OvjUQU/Dsh+NalM6zlcw2K0uhEG4bf8Ai/2iq/tDbGmW2Dj8cf6tTdA/hm/xf7agdom2l2v+OP8AVonG3WpHnuXQyn/4iQ/zcLH6AwEs/mVH20q+P4xviab0I/jpv6I+2l3p/GN8TXFQuvhrfE/MrjgfygoHjQNAneiasMlUJFDrQNCqEyB2o96GKUFpw26STR4NLCUsRGrWwk7J7JnloctSktJHXmWORh5hSaULGY/xM36B+6rRSP6KWQnkoePdRVNNhcHcQTf6M/dUZkxUH0zm7hMWkbpvFClEYoqpy2UUk9KZPWnnOAaZoSdMVMtD+J+s0VyN/qoWf8G3xoT/AE/qrSGtO1Wf0qOg3NLaJiD7JpMYy5qUw9k/Ch4Yw9puoNF1BoUKKs4qKFCjoCnASRYo6OgafLZJF40KFCkkgaKjoUySKp1t/Bj41Fjj71+XIHxqUkLcmAehrQoWOBLwFNl73ThYnPlRqKTHEy/SOfd5U8BtWsxpOrlaBdBRg7eNRL6RWKqGBIJyB4VNjHtioN3EqFiGBYuc+6q60O4Jy7Jn+yo3WhQ6UKwgEOrbQ54Ye8EkqpllxzHrWnZOY4xmsGOv11vVDc67Gu/9FqkywugI0bb4krWoHZmlvRQZ4AHY8m9FAjHOxqfcWxZS+aajj5M+Oa3jSZZb2RDorOTlqCJF+FWaPy27/A/ZUOCPlZWz4dKlgEwvjyP2Vswsys1R9MC0LnKzMjBkYqw6EHBFa/huKHUdKeS7jW4mDuqtKOYgY6AmsUj8xwRirbTuIrnSYDDDDCy5Y5fOdxivKcIr4oJC+c9ix5X18FiYfUMhlzSeyrLhDUgss8d3cu7yFFiV2JycnOKveI0A0O8P+D/aKznBWmpf3E11Izg2zIUUdCTnr8qk8Ua3KHn01I0ZGUAvk5866PDa4xYK50+zswbzJvff3/BaMExjoby7G4HfusicE7UW9bG24Ksri3ilF3OedAxA5dsirBuDdIeLlCTRsB9JZCT+vaufi9EsQe3NYD37+V0EzB6hwvp5rn4JNHjy2qdrdlFpuqzWkJZkjxgt13ANQc4rBkjMTnRv3aSD7lmyMLHFjtwjOQKSd/GrTQNOj1bVreznZ1jk5uYocHZSf2VpRwbpOB7d1kj88bfqrUocDqq9pfABYaamyKgw+WZnEZte3y+6zXDAca5acpYe0dx5YNbW4RYbV7uRVWGLCsQPE9BiittJ0fR4hdJFIssKHMhYtnbBOKo+IOJWMLafbLE9vJySs7KebmHh8K6+ih/A6JwncMxNwBryA+i14mto4C2ZwvqQB1tp8Rqo2ucUTz305sLm4hh7wGMISmAFx4VR3V7c38gkuZ5Z3A5Q0jljjyyabcmWRmOxY52pSrgVxFTVTVUhdIdD5eSw56mWZxL3bm/ckd23lU2z13UbHukiu5hHEQVjLHl65xjyqPRGMMcnNUtDojmiJB8VWx74zdhsVsX1644o06ee7itojZuoTuU5QQ+c5+Qqrmtz3RbkIGOtRNJ11tFhuIfU7a6juCpZZs+GcdCPOtNeCC+0i01Du1gkuYs9zF9BeVuXbO/QV1uHTtrIuG83kAJPn18lt5hVs4hdd4Hav42322ICzHd4O6iociEEkjxq3ljUMNzUCWMFgAScnes6tpcuiy5GKMMAdKTGhBORTkicj8oz9dSra2idiJWZQBtis+KndI/L0VbWFxsobx7MeWo+MGtBdW0HqTCMt7Ck586pO7Bpq6jMLgN7pSxFpSKLpSsbmj7vbrQIaTsqrIkIzTmabEZzjwpary1Yy+xThKxtU/RmC2js35/7Kg0jT5zHNyeDfqNGUdQKepY487jzVjHZXXW34U1HkvntTIVMwyoBO+3T5fZWk1C2E9qVjRWkUgqfEeeK5zZzPFcJcq3K0ZDAg438q3ul6smrWvegckinlkUDYH3e6vQ8IrOI3hu35d4XV4NVMliNLIddbeH7KHBr+scP6nBqGkOqPEDhuRXO+xyD4YrqPD3bvbXc4i1i1WI5A723Jbl8yynf5VzO7sGDc8KswYnKgfR+Huqtls4pGDFcOOjDY0VWYcypcXuFz/Nj91TLHLC83+P0XqTR+IdP4itPXdNuPWIAxj5+UrhhjIwfiKnSd68Lqj8rlSEYjocbH51zfsFuhdcI6jJP7EdtdFmPXC8mSf8AZrOaj2vaynElzfaYYZtMB7pLZslXVejb4IY5ycfCuUGGulmfHT6hvVRDw5oKpeJ+H9S4f1NotVZJZpszd8hysmTuc+eetZ/VIw9sZIwO+j9pWHUeeK3ep9qeh8VaM9premSQ3657lojsr42IzuM9CNxWF5+fr412lFI+aHJM2zhp3e5XucyRlgVb6a6Xel24nCzBoxzB/aDfGuY6onJfXCcvJyyMOXGMb10DhqKVIHE5YJlghIx7PhistxtAkerK6JymWIM58znGf1Vg+kEOamEnRLF2GWhimOhGizLCkmlsKQa8/eLFciUBTidKbBpxelSi3SCmxRJM6hxkDfFVskf4+UDAAZgBV1pkInuVRsgEeFVV0BHd3CDosjAZ+NaWIU4dAx7huT8lZK2zA5dv0yeX9z2nQEjkFvEQMb/RFZbUR/ZVx/TP21p9Nb+02nD/AOGj/qis7fpzXM++PbP216RhrRwrDovQK5v/ACsQH80VYyg9abI2xUiQcpFNMm21SljsVz7gmcUKURg0RG1CuaoEJOKLpSsUMVCyayTRijxRU9kkmf8AgJP6Jr0t6NEhTs3mwSM6lN/USvM1yT3Enwr0X6POvaNpfZ1JDf6vp9pMdQmcRT3Co3LypvgnptXO+kILoQAL7IOp1IV56RDs/ZJqgyf7otv94K8ow+zbKfJK9LdvvFWi6h2X6lbWer6dcztPblY4blHY4kGcAHNeY4WY2KsT+RnNA4ITGx7SNbH6IePRx8F6+7BLkjsl0FQSNp/981VPpKuz9my7kj8Iwf1ZKHYbxDoth2X6Hb3ms6bbzKsxaKW5RWXMrEZBORVf6RvE+i3fZusNlqlhdzDUIG7uC4R2xyvvgHpWVG3LWZyNM1/iobC5XP8A0ao89oaAHBWwuN/qFeplYxkHnO1eTfR41a007tAae8u7e0hFhN+MnkCLkhcDJ8a9MLxZw8y5biDSP9cj++rcXaXzAtGlh9VN+uy8xz+jhx/Ldyyd3pSh3Zhm/XxJPlSk9GTj6fZl0cA+Jv1+6vTB4q4a6niDSP8AXI/vpt+MuHE2XiDSPqvI/vqptVU2sB8CqwzReUON+y3XOzlbEa0tkovOcQ+rTiTPLjOdhjqK7h6MysnAupEEgfhRv90lYn0m9esdU/c0LHUrS75PWebuJlk5M8mM4O1aP0cOIdMseA71b/VdPs5H1J2WOe4VGK92gzgnpRtRUOmo8rx2r8h3qYfcZTutt256tdaZ2Ua5c2txNBOpg5ZInKMMyr0I3ryMe0DX2yH1XUmPn63J99eme3XiPRL/ALKdctrXWdNuZ3MBWKG5R2bEq9ADk15QijV4gfE56VThwlaC1mnPUeCrOYOs1Tr/AIv1jUrN7SbUL+SJ8ZWS5dlODnoTioNtAUOXG5pQiUe/40tpeRSfKtRrTfPKdk+U3u5eyOwKTHZNoZLHYzjH/mmi7c+ENY7Q+D7XSdDW3e5jvlnbv5hGAgRh1PjkjaqHsT4p0Ww7MNFtbrWdNt5174vFLcojLmQncE5FbxOL+HW68Q6QP88j++uemY8TukaOZ5J8twvMcnoz9oKHePSs/wDzBfuoQ+jV2gGQO1tpjY2A9fSvTcnF3DajJ4h0c/55H99NJxzw4siga/pGM/8AjI/vqxs8/IfApuGF5A4Pia3480GGUANHq0CNg53EoB+yvdElwvO2Cep+2vCPDWowL2kabcSzxJbrrCOZGYBAvfZ5iemMeNey34v4blZjHxFo5yT/AH5H5/GpV7uM4OGqZhzaqbxPxHacOcP32r3lxFFHaxM4Mrcqs+PZXPvbA+uvDfGvF13xvxRfa7dRrDJduHMMbErGAoAUE74GK6T6R3aFd6nrQ4ZsLy3m0uBI5+e2k5u+dl3DEHBA8B4VxhVMKAt9JmGc00EYY35/ZQkdyC+g9vPJ6vDzFv4JP6orzV6VDc3GWisT/wCzgP8A1Wr0DDxlw29tEBr2kZEaj+7I/wA0e+vN3pNarZajxdpD2F7bXca2HKzQSq4U962xIPWq4Glj8xFlZJoFysDcV6S9E+Urw9xJg/39D/u2rzWucivQvou6zpemcO8QLfajY2jyXsRRbidYywEZ3AJ361oYh2o7Afy4SO4XTe2id27KOKdz/cJH+2leKbcfi/rNewe1/ibQrnsw4lt4Na0yeaWzKpHHdIzMeddgAcmvHluxMe3maHw4ZXG4t/AmPtrR8BEDjzhv/wCZ23+8WvdMt7+McBj9I/bXg7gqVIeOeH5JpEijTULd2d2CqoEgJJJ6CvZv7r+GudyeINIO5P8Adkfn8ahWM4kl7bKTBclP8YcaW3B3D15rF4yutvGTHC0gQzv4IpPifd4Zrxbe8TX/ABjxzHq+py97c3V7E0j4AB9tQBgeQAH1VtvSE7RrzXOJrnhyG4tJdIsJVe3e2IbvCyDLMwJDEZIGOlc00VFh1XTizBFN1F7THG3OuSaUUbWs7O/P6D6lVyuB0C+hEkrJK4DE+0a8i+lS3P2mDPX8GW//ABV6bm4v4dWVyOItHI5j/fkfn8a8tektqVlqvaGtzY3dvdxHTrde8gkDrkc2RkeNCwRlpuRyTyjsrnsX8GvwFKxSYv4NfgPspeK6lo0CIGyTijxR4oxU7J7IgKTI4iUEgnJxTmKj355IVbGcMKjKcjC7omcbNJW07M+yrVu0XUonkjvLfQ+8eOe/iVWEbKvNygE9dwPrr1zoNjpnC+nJp2lWVtZwoqhhDGE5yBjmOOpPX66476OPFGjaVwJc2+oarYWkz6hI4SedUYjkQZwT02rXcd9oumcP8L3+rabqWk313AFMcHrKtzksAdlOTgHNYEzXSvsdlBjRa5Vn2ldpumdn2mRXFzELq7uSRDbc/ISMH2zt9EHHxrxpr+uapxZqbXOo3k11KfZ7yVsnHgPgPCl6/wAQ6nxbqtxfXcryNLI0nKWJVMnOFydgM7Cit4REg238c0TT07ZBw2bcz17vBRJ4hs3ZJtYPV4ghwT4kU/QxR4rZjjDGhrdgrg2wsEVFilYoctTyqVkQowM0fLSlAFSDE4CLHuoUeKWIxy5OatbGTsnslKu2cVIhUcp28aQilsY3p9E5RjOa1KePW6uYFeaDtO3+Lqu7RWzplr/jz/Vqw0Mfjm/xdVvaKcaXa/48/wBWp49pRSeC3pP/AOpk/nMLH6FvNN/RH20u8/hG/pGk6APx839EfbS73+Ef+ka4en1w1nifmuPH+kFAO5oiaM+NJNYrlQiIoAUYFKVc1FrblJGq58KejhLeFLggLsFAJJOAPM1vdA4Msba2F1rS3LzlZF9TRgnK2PYcsM7dSV9wrbosNkmPZF0dR0EtS60YvbyWS03h691NJHtYhIsZCtlgME/GtPp3BtrHbRNewO9xg86iQ8mc+73U/ccQ22lM0FskbOv0lAwo+OPGmWvdV1KUyCYW1s7ZCEnmVfhXX0eFU0Lre05bsFPRU5sRxH8xpb+ct1aW93Y6VCtpE0NvGCW5OfxPU706utW80gRbmJ2OwAfc1Ty6DbTYaSSVzjY5xUc6Faxe2neKw6HnO1bHCkGjGi3j+yOFfVM0awBo5X5fJajHfKVEjLzAg5PnWR4l4PjtYu90+OaaQuAyoeZcY3Iq102/ntJBDeyQi2A5Uk35s+ANXvec+E5Qepz4nah6yjZUsySj7hFuigxOIhwsfiFx24t3gkaORGR12KsMEUyRV5xZbvHq91cjLI0mHH5p8/hVGTncV5nX03AmdGeXy5FcBUR8ORzOhKbbpvTRG9OtTZFY0yHKkWfj8afmUd2xwM1Gs2IlC7YNTJAGQqds+ValJZ0FlczVqhx45ql4BU7ZqIo3NTLdeaPfzxT0gvdqZnRQDGSemBTfSp91EI1XBO9QGGGI8qzauDhOsoObY2QpUZHOM4+uk0dUN0N1FSeUY2AqOwxkeVLWUqAMA048GckEk+VGOAkbdqlvso9FSmUqcEYNEaELbaKKKhQowN6iBdJSLBQZWyM+zU4jHTaolmmHLZPSpZO1dBQjLCAURH7KIeJNK5T5UpE5utOOOSNmG5AzWgyK4uVYBzTmkYfUe7YA4XO9U13/AHVMP8I321Ntrx7SR7tUVnUYwelV0rGSR3IwWYsQPfQFdO0wMj53J93JVyvBjDedyk4osUoCjC1kBl0Mkha03DDyyXMwkkkfEeRzMT41nAuatdL1KTTZXkjRHLLykPnzrcwORsFS2R+gH7omkeGSBztltoxlRlRiki2zKcKvtEACntHL6na2zBB304GEXzJ6CndeubTSrD1iKG4WWFPxneEMGkz4AdF+NeouqGCMSna1/da667Izh8Q7WuoDssN5Jb+yzQuUYggrkdcEdRTGpafPqLIYpe5jVTkISM1H4Vkk166unlRYuaQyYi6DO+N616aRHFEQHfIB60BDUMrqUOfs73c1VTU5qYi7+kriqHlbenJBlMimaeQ8ycvkK8biNwWFceOit+GtZj0qG9R0ldpgoUKQFBGdz86i30wuL2WZfou2RkYqvRihIBp8vhc+6tSKue6lbTu2br8/urzO58YjOw2V9wxrVppTXHrUhj7wLy4UnOM56VtUYSIHU5VgGB8wa5PgzZOMYFdYhjZLWHY4Eaj9Qrs/ROvlnY6F1srbW66k7rocDmc9rozs363XPOLFxxBdfFT/ALIqpxuKueMBjXpj5qh/VVKDk1w+JgCtmb/ud8yufrRaokHefmrvhOZIeIbJnPKC5XPvKkD7a26hjgY3rA8OoZNe09Rvm4QbfGuiTR+r3DLucV3Hoe8mGRvf9Fv4O0mmJ5B3zA+yy/EGr2bwvapO6zxygOoUgHHUZrMXcqSyAocgLintcbOsXn+OaoXhXL4riktTM9r7aEjTuJ71hVk7pJDm5aeSVGpznG1OYpKHCilrWfG0WQ7QkHYgHqaBzQkGXSjIpiNSkkPjbNa2x1TT7nRtPsGujHdRBowhjYgktkDPSseW7zYeFT9IXOqWQ/w8f9YURhlbJBNeMDXTXxCKoqh0Ty0AEO0N/EH6K/urG4t52glQB0OCM5xVa8JznAzmtVr/ACjWbn+kPsFULxhm2O1drW0bbAhaNbTtjmdG3YEjyKq5F5ZDnGaftbWadvxa52z1pya0VpM5PwqTayerOSoH0cVkw0n5v5mgQbIxm7Wyg30FxbM0T4Hs7gHPWq0g1e3RNwHaT2m5cZqsNsOuTQeI0v5nY1CrmZ2tNlBIOTS40ZlJHnSpQisVXOQd6cjJVTWNHEC8goYDVNmNgMkbUXSnmYsuPCmivKasewNOichDFR5kKvzYwDUjNE4DDBFUTR5225qJF1KgnE0K46j6XxqTZajNp90txBylkyMN0IPWqSOV7aQ+I8RmpquroGQmtKkxJzwNbPb9FZHIWkOBsQul6Tq1tq8YMDYlA9qM7EHGTjPXHnSbrTlCZgDc+d1Y9a55ZzS28wuIpGjkX6LKcEVsNP46imeKHUoUTACmeMHYAdSPEk12uH4814An0PVdTS4rBUM4dXo7keX7fJO6VqfEGiQTaPGJILOdjJMUz7YIxjIOKUoVF5VAUe6rO21G3vV5rSdZUz+TsR8R1pbWkEsheRCWPgNq26RjI29g3B5ouPDRlHCfccr9PEKoMYcgkAkeJFPQ2ctyWWIZ5cc2+MA1aJYwMcLA3nuTUjvWUE4wPPpRBkFrBERYVreQ6dyTaoLUIu8iQjHt7ZA86wPGGpW2oanm2bmWJO7Y425snp51ouIeJILG1uI42c3AHICngT7659L9InxJya4/0irmhgp2G53P0WVj9ewtFNFYjn7tgm3xTZNKNIJrhZCuTKCmnkO1MilocLTwmxTBXuif3YufzTVPqCNDqN2kgAZZXB399WuiBnvUCjJ5TVXrL41W9z/Lv9tbWKSWoYj/ALj8gi5W3pwe/wCi7Fpd1CdJsQ0sYIt4+rD80VBvdPuR3tyYj3LMSGyMEE7Vzq01l4Y4wYwyBQCM9dq6/ZxLfaBaRksqyQxtnGcbZrtsNxGJ8beEb7XXZ4fXNxJnBtYsH7dFkZ4mOMDbFRiMVeaxYLYSxorl+ZebcY8aqpYOUcw8+lbLgHjO1BVEBY4joorJk5Aogp2BpzHvo8D66HMYQmVMlDSeQ08RvQ5arMSbKmeQ0XLT5UURSomJMWqPKgdGQ5wwwcVSzJf20rLEoeMnIzg1fFcGmpEDGgauk4o3II6IaaHPrexVE6X1yojkjUAnOcAVaQwotssJzy8vKad7kUfdD31RT0XCJcTcnTVVMgym51VMDf22Y0RGQHbODSZI7u7KrMiqgOcrjNW5jUk5FF3a+VBuw8nsl5y9Lqo0/K+irXimt5BLCA23KQaSbnUP5JP0RVk0a0koADTupHf0uIHcm4J5FV4m1A/xSfKgZb7+SQfUKngUZUGq/VXfrKbgn9RVWUuJpVecKAnTGKNWuYCViRWUnOSAasGjHlSTGB4VV6mWm4cb9eajwbc1WzG7nXkZFCk52AFSYou7QKM4FSCgocopmU2VxcSSe9OI7G6a5aJ4udSvnTpG1ERVhjFrKRaofeXUPsJCrAeJpPrF6f4lPlU3FFgUOYH8nlVcM9VD9ZvP5FPlQ7+7P8SnyqUVFDlFQ4Mn6ym4Z6qB6q6xhlH4wHOKWst6P4pPkKmcgo+UVWKTL7BI8EwitsVDSN5ZTJOuDtgDalzxCVCPHqKkcooctTFPoQdbpxGALKKLm+AwIk2/miiUTTzB50UADw2qZyihyilwHH2nEpcM8yk1FR7q2LLGilSc5IzUzlocoqT4nOIINiOikWXUOSe8mQxui8rdcACnYY+7QL8/jT/IKHKKi2BwdmcST3pgzW5KjTxM2HjGXXoKQZ70/wAUv6IqWVFFyimfCSbtJHgmMetwVBS3klmaWdRk+HSnLlXdFKDLKc1L5BRFBVQpLNLRzTcLSyiet3p/ik/RoJA91Izzjlz5eNSuQUpVwakKZziOI4kd6QjvuUoAKAB0o+u9EKWi5o5rb6BEAIgpo+U04EpXKKvbEpZUzymkTwrMnI+cZzsalcoNIZN6T4LixSLLiyqWiubfKoFK52JxSCl3N7LcuD4bCrYxKTuM0O5QbgVnuw4k2zG3S6H9X701FBHEuEXGeu9OchpXKBTgXaj2QgCwFleGgaJoKaUFNOBRR4Aq4RKYamu7JocjCnutGAPGpcEJ8qaCGlFCRsKcC+OaXEyI3tDmFWthGxUg1NxRgK3MCX8PKld0xzgfrpxF5nAGwJpxI+7LHOeaiY4LgdFMMSlQAbAA1It4iUOVHWijiDHfpVtp9j3yA9I8kE53rYiiDe0dkdS0zpX5WhP6ZavbuZJVCIUwCWFUPaKwOnWmCCO+PT+jVpxjOun6RG5RnAlVQAfca5vqOovdgcwAAPsjPSuZ9I8ThbA+Fx7RG3crsZrG0sTqBovcA38fd3J3QD+On/oj7aXenMr/ANI0rhqMSyXfmsPMP0hTd3tK39I1y0GmGs8T81zViImlQ2pHWlt40kCsd26GRgU/GoAyaRGuTWq4Q0QXk4vZlzBC3sjY87jwI8q08OonTyBrUVR0r6iURM3KuuGeH4NMge/1ARtLy+yjBXRV2IcH877Ka1jWbjVJJLPTgsqAqWJ2I95pviHUze3P4NtwwkSTDEnAO1ars24Ys9Y1pNOuu+EZid5JYgOckDbJIO2a76JkUEbgzRrdz9u9dM94ANJT6MHtHmT0WbstHijJlm/HXBxl2HQ+6tXwjwLqXFl2RbKEtIpFW4nJH4sHyB6naukr2PaBykm/1EHwxyfdW30u3g0nT7extUAht4xGpwAWx4nHiaAq8fiZHlpB2j1G3f3lNmZG20YXFO2W4XRp+H+GNMSIQ2VuW5SBzks25Y+8DNYiVlCnyrqvHfZlqPEvEs2uQX1qvOVCxOGBVQoG58a5vxFw5qnDc6R6nB3QkJETBgVkx1xWjglRDwgzPdx1t3nU/FMwuaHE8/581UtGs2FdeYAggHzrSQjmc7dBk1Q2SiW6iQ4xnJz5Crq8lFrYzzpGpaJGk+Q6Vq1LgFr4UA1j5Tt9lida7ubUbrdZI3c5wcg1krqD1S4eIkea48j0q7hueZyHI9ok/WaqNXbOoH4L9lee+kLo5IWzje9vNcLVyCQmTqVFPSkGlk7UiuOkQJTlpgXC/X9lT+QkVAtB/ZKfH9lWaitTC25oj4/ZXxC4UEoUYg9RUi3fAwOpO1My/wAI1Ltvpj4inhOWSwTN0KlyR86EEAnFVM0TK75HQ1e8vU1W3cYxI4znrRWJ0oczNzCsmZpdQBQpSDrQ5a51rLi6FSRUqOUEb7VGp3kAxvREJLT2U7SU4V5zkYINMPEydRUqMAKB1opxkCiZYQ5uY7qZbpdQwPGnIkMjcq7mggDnHSpVtEFJbxFD08Gdwtsotbco4IZEbcADG9SOQkZ8KUOlPRoCuK6GCnAGUIprOSKFMAk0i9nRImjY4ZlOBipHK2By1Wai5e55cY5Bj41bVvMMPZ56ealIcrUhJF9XdD9InanrHS5766it4lUySnC8xwM/GouwGa1CQ/uaaw1CYNL3id5GEGwPLsGP10LQwNqDeX2W2v4XTU0Aldmf7LbXPQEqv1bQJdKtYo50iW6LM7csgPsbAfrzVR3Z8qtNS1K71uWOe7ZGdF5RyLygDOaKy0qS9ivGQ4NtD3xXGS3tAY/XTz07JJPyQcvLyTztjklPABy8r76BQI1wMEU8luXJCDJAzRiEq2GBBz41NtY1DsfdV1JSZyGuVTGX0XX+BOz/AFL9zf4au4Whgi043Fm4lA55OozjfAGT8q5Lxfr8GperrZ3ErKA3eDBUHJGPjXYtG7R9TseJeH+ChFanSZdIh7zMf40lrcucNnpnwrz3IOeUgfnYA+uoYnjE7onUzSLXy879m38K0qytdwBA3bY+6y69wvw+ukmOSOAR88amQ8+cnl/609r/ABFpuiSCC8maN5ULKBGWyOnhWhWw7jSornJyAin5VwviHiC91y6WS9aNmiDRryIF2z4461v4piYoKdohAB2Gmmm/zW5iFUKCENiGp2005X6KnoZoUBXmC4hLBz4UuTPJgU0GwD76UJGOxxiiGyC1jzTgpcBwCK0Og3qQXneXU0xjRMgcxO+R4VmyxQ5GN6nJMYh7J3I8RWthNX6vIHD+lX08pjcHDkpPF1zFc63JJA4eMomGHTpVOtOXTtJMWbrgU2nWs6rmM1U+X9RJ+KjPKZZHPPM3TqM4Yd3zc/5PL1z7q2vEEuBp8fesJltkMi8xyCQOtY6zuHtbqGePAeNwy5GRke6rC+1O51C6NzOymQgL7K4GB7q3cIq208cl9S6w9w1RNPO2OF7ObiPIKsu97qUkknmPU0hOhopmLTOT1JpUQypzWJmDpTbqUCdSk8pcYFKjhcMCegPnS4h406KmyAEhxUmhNO784UADPQ026tzbndvKnyN6IqCQT4U8kZde5SITSRFc5qVo+TrdkP8A4mP+sKaNP6CObWrBvH1hD/tCpRRjixsb1HzU4R+Y0DqFvtaQ/hW52/K/ZVMVKt02rQ6mge+lZs8xO/yqse1TDHDZANeuTU2ZgIXU18RM7yOp+agRjvCQOXbqabuYj3WQBsc1nYtTvLK6Z2/FmQguCmMjzxWqj5LuDnjPMrYIAO+D5+Vc1h2IRYg1zGizhyPw/dZMUjZQQN1VBTnxpaoT4VYepqBnlaqzV7iO0iMWDzSKcYpqmn9WjMsp0Ci9mQZnIpbVlBcoMUyVUnJFM2l1eSKscijuOXAPL8t6mRxqwyQazYiyoGaMEeP82VAs7VqjSoVXoMZ60xy7GrCWJSmMHGfOoMy8jcozjFUVcWQ3Kg9tlDyR76UN6X3a48aHKFHjWPkdzVABTbxh13G/gaj8zwON+m+PA1NIFIeNXGCKolgJOZpsVEtTiXMcqbbEDoaXGNubxNQXtiD7G/u8qAuZkzls+G4zVza9zDaYeSfN1U4SOjc8bsjdMqSDVjY6rfWsZ5bqQAnOGOftqjF5ke0pz7qe/CKYA7tsfGjqXE443Zw+3mrI5iw3a6y0Q4l1DGPWf9mm57uaf2p53YhfaJbAqh/CYU5WHJHTJ8aQ91c3alZCFQnJwuM1oH0iBBbcvPLe3vurnVj3CznE+8py5uRc3JA3iTZfInzptjmiVQgwBQrBfK95LnnUoUm6JunSkHpSmO1IPShnlQKKnE+jTVOp0pozqmCsbIkPsSPZ8K1fF3DMmp6jbyoYk/FqgVvZ5seOw361m9E1H1C5aTuLeYNGU5Zk5gM+Pxq04n4ku01hzbPA8CsnIwUE9Btmuwp5KUUR9ZGZtxoFuUhpxSP42ty3Qe9ZZ42tbqWBiD3bFDg7bHwq+4a199G1GO5keSSNUZeRmJG422pi7tdP1C2v57W1uVuEw6/jefny2D7OKpBJJG5jcFSu2CMEGseOZ2HzC+oOo3Itcix26LPOalkbJGe8H399ui7tw5rltxFaSyiOHmRu7Hiemeh6VT32i39lEZ7i3KRc3LzEg7muf8M8UX2hORAIe7aUOxdMkEDGa6rw9qk3GNvc2moyRmKIJIhhUKc5I613GF4nxmcVottcdPBdbSYhHWxtY+/E18FlJl5mAA3FKFu46gfOtVqfDFrZygKZWBUNktUT8Fxf4T51utqI3DMEWzCJHdo2VGsJC9BmjMR8hV1+DIf5/wA6UNMtz4v+lU+O1W/hD1QmMgZwKbK+6tJ+B7Z1I5nB/pUhdAgP5Un6VMKhnNVvwmXlZZsxknoKSYSfAVom0i2Viv4zY4+lSfwTb/z/ANKlxIyq/wAFmPRZx4jjoKSIWPgK0h0e3I/jP0qJdFgY4USZ/pVAmMqBwSfu81ne6PkKQ0DMc4FaX8CQeIk/SpLaPbj+U+dRIjKicFm7lmjAwG4FJ7o+QrSHSLdv5T9KjOh22Nu8/SqBjZyUPwWbu81mu5PkKIpg9BWk/AsH+E/SpttFts/xv6VMYm8lWcIm6BZuSIk7AUnuW8hWkOiW3+E/SovwNbdPxn6VVmmBVZwefuWcEJ8hQ7o+QrRfgW285P0qB0S3Bwe9/SpeqhN+Dz9yzZgOegpJhP5orTfgS2/wv6VD8CW3+F/SqPqgUfwefuWZMLY6CiNu3kK034Ftf8L+lR/gS1/wv6VRNEEvwaY9Fl+4byFEYT5CtT+ArX/C/pUk6FaH+V/SqPqKb8Gn7vNZcxN5Cj7lvIVqPwFaY6y/pUk6Ha+cv6VN6imODzjosz3LeQou5byFaf8AAdr/AIX9Kj/AVqepl/SpvUUvwac9PNZfuW8hQ7k+QrUjQrT/AAv6VD8BWfnL+lS9RT/gs/d5rL90fIUXdHyFaj8A2nnL+lR/gG0/wv6VP6im/BZ+7zWW7lvdQ7lvIVqToNp/hf0qA0G0P8r+lS9RCQwWfu81lvV28hR9w3kK1X4AtPOX9KgdAtP8L+lTeohS/BKju81le5PkKLum8hWq/c/anxl/Sozw9aAb97+nS9RCX4JUd3msqIG8hSxbt5CtN+AbQfyv6VD8CWv+E/SqTaIBIYLP3eazfcnyFKEDHoBWjGiWviZf0qWNFtANu9/SqwUjVIYNP3LNJA+egpwQt+aK0S6Nbg9Jf0qV+Brf/CfOrW07ArG4NP3LO9y3kKHcN5CtGNFg8pfnSvwLB5S/Op8KNS/BZ+5ZruG/NFDuG8hWm/AlufCX50f4Dg8pf0qXDjS/BZ+7zWY7hvIUDA3kK034Dg8pvnRHQ7ceEvzpuHGl+Cz9yzIgfyFGLZz4CtKNFtx/KfpUf4Itx/KfpU4ijTjBJu7zWa9VYeAp3lx4D5VoPwRbHwk/SpJ0e3P8p+lU2tjbspDBZh0WeaFnOQBiglswzkCtCNJtx+f+lSvwVb/4T9KlkjvcpvwabuVAsJ8gKchjwG5gM5q6/BEPlJ86di0a3bmLd4AB+dVjXxt1U24NPfS3mmLDTJ4bhJJY17vBPgeoprWNft9JlaCTu1JTmxnB3z4UrWdfm0uYxqYxEiLgsuT0rnOuazcatfNPOVyAFUBcYXwrDxjFfVGB2hcdh91DEMTjoYjBT3z31vb3pi/vJZI/bkd/a6MxNFodpJqF93aBSSjHLnCjHiT4UjTYG1LUba3dXeN5VDhBuFJwTWst49HsLvUbGwt7hiIJBLLK+ccvgB8fGuFoo31tS2oe7sA213Jtew/mi5ijpOMRI91he3eeen80TGsQy6dZ2VujqAYyWMRGGOeuR1rPSt7RqVcX8ssEMLlSsIITA6Z3NQHcknNE4nVMkd2NBp8lCqla912bWHyCbJ3oCiNGN6wxqUGpmnWz3l1FboCWkYLt+s10jV7hdO0xu7yGf8WhG2Djr8hWd4E0yGcyXzc/fQvyJvtgrvkVZ8Txc8unqScNIUIztjY13+BU5ipDKd3fLb5rq8PhfT0D6gbu28L2+pTWkaeFRrmU80kvtZO5x8a1fCFhf3PENkmmhjMJVc4blHIDls+7FUMRkdkjRcliFUDxPgK65pV7w92WaJHPqEsVxrNyhaRIWV2Tb6IOcKB0J8TmtuvlFLDwo23cdAPmT3KyJrY2WC3rKY2IyceFNTXSW8cksrcqIpZiTjAAzXKtA7arvWOJUF7aQwaO+I2WNeZ0bGA3OffgkeVabtZ1a10jgjUDJL7d3EYYQBnnY4PXyxvmuLfRyROaJBv/AA+Sg2RpaX8ljOKO3GW7SW00GE24dColc5mz7gNlrnQN/ql6t9qFxI7ruqu5Y5PmTTOm2aRQBxu0ntkkb7+FXun6Zzr3swkTlYFR0zXYUVDHHG15bbnb5XPP5KqmglqnNB/ZTNOt1hhDtHiVj1I3Aqj461bnEGnxscIA7Dbp4dOvUn5Vda5rVlpFlHziR7qUsMKw2GNtuvWuayTTTSMZh7QA38MeFBYvXBreGNz8P8o7GqxlPD6lFvz/AJ3o2HMA4AG+CB9tVV1M092znHXAx5Cp09x6shYfSOwyM1Wr7TFj161wGLVGbLC3fc/RcbIeSUelJpdJIrLeLqkokOHGD41Z2pzH571V9DkVZ6d7cJLfnY2ovCnHiZFbDvZC5jwoPvpqE4lT+kKlXYxGvxqNEMOp94rQnZabTuVjx2lZpliaTPEXhdFAyRinYFBY5p5olC5x+ut9sOdhuiw24Wfu4WhYBgoJHhTAqz1KHmeM4PQ9PjUIRKCOtcrVUpZMQ3ZAyMs6wTYFLMYJzTvcp7/nTqRhjgj5U8dPfQpBijlzHy5A5SackQsARipQtImUFgfnQ7lAMAHA99GikeBZ2xU+GeaiQxt3ijA3NWESFQeYCkxQqHVsHY1IwKNo6Xhi5VsbLJvkywAHWpKRFVHSkwxczE4O3SnJOZI2IHQE1qRR2GchXtbzULVdo48HG5+yqk5LZNS5p5blVEmNtxgYoreFGuIg6sycw5gDjIrn6s+sTXZsbIKTtuuFHCZpbcxwCScdMkmtdBpeimwuL42N44R0j7qOfccwPtZx02qHxFcRaPLZC0sbdoZLWORjLHze0c5HMPhRNRhopozJM7QW2ud/JFmhLGF7nC3dfrboFZcIzXsOgSNYvbxSxahHOzzICCqp06dM+FVWrwSwR3et2moFZXueSQwkru+WIHuGKg6TxHeIRp4SAQXM4L4j9rfbY+Fbay4UtNU0XWrVY7j+xrKS+hSE5LzJgKD1yNzsKKjdDVUD3wg3YNzca20sjYy2WnAZ/SD131tZRbEpxfoOlwev2JvrGN1lWeTlmmZ3JG5HtYGANz1pF1He8GWMl7Jp9u5a4WECdQ6n2SdvlWFsrq70HV4LpYglzZyrIsc8fRlOcMp+yux6IbftK4YjfXu7DCdnKWjd2QRsCRv4E0PhuKPniMUYtKBoevLXvUqKYTghukoGh5HS2t+a4/rWs3GravNqLZieQjAQkBABgAeQ91Q7Uc9xEpYKC6gljgDfxqw4m06HSte1Cyt+buYJ3jTmbJ5QdsnxqrVsMDjoc1ykxfxy6Q6318b6rCkLuIc+99Vt+PNckGpXVkly3KBHgITgEDzrDnrvU3WdVk1rUp7+VFR5iCVXoMAAD9VQaLxbETWTl/8ASL28L6K+vqOPO94OlzbwuhQoUKyUGhRjY0VCnSSj7X1U/HKOQA5JFR84o1bFXxy5XXTg2SpW5nJpK9aDbnaiWoF133TJwEKQT4U/JKEAIB9rpUYnIpUzhlXBBwKKZKWNdZSB0TbEkknqadg+gfjTNPxDCHO1VwG77phulpsBTi9KajOVFOL0o+IiymEZ6iiwKOiqZTpLVL4eX+3VgP8A4hPtqKRUvQGSLW7GSRlSNZ1LMxwAM9SaaAWqIyeo+YVlP/rM8R81ruIOJINO1KWOa2mLDBypGOlWAgM0aSKMBlDfMZrG8Z3UN1rMrxSRSxMF5XjYMDt7qteDtfmupJYL67V+VUWFGIBPXOPPau5pcfvXupZXXadG7aWvue/kuibWiSskikNwXG23f81B4x0mZJV1BnQxkLFjJ5s7/qpvg2RPWZ7XDCSUcwPgAv8A961PEFhHrFg0ffd2EbvPZGScA7Vza3uZ7WXvYJXifGOZTg4NYeM//F4sysa3su18eTvms+tYKWrEluyf4V0i4tnSKRuZcBSf1VgNU1Bb+WJkRl5Vx7Xxrd6zcCPQrq4t7hCyIAHVgwzsCK51Baz3b93bwySvjPKikmrPS+tcTHTxahwued9dLeRU8Y0e2NnMXWg0W0eezysikKxXxqa9sYTysckjwq00/SI7C2WOKJkzhm5jnfAzTd81rDMqTzwxMRkB3xkV0VPhop6RnFsHAAHXT4qYpTHGM2hVTLHhOvjUZ48qfhSdLuJL+SdJXVypyijrjfJ+yrBrY+MZ6VkR5auMSxjQ/wA70IBnGYbKl9XYeIozavncipk8RVQY0Oc74pIVz1U5rNNG1pykKjhi9lDkgK43FI7sjxFTJoJcr7DH6qKSDljJCHNDvpNTpsomNQymPGiKZ64p4RsfySfqou7P5pocwnooZVFe3Rjkj5Gi9Xj8j86kmNs/RNF3R/NNDmlbe+VQypgRIu4XcUo04UIz7JpGD5VEx5dAE1kg0ltqcIoivmKrcCmsmm6UnGae5B5bUgoQemaqcw7lRITRFOIcCk4oxtVTdCohPxPyMG61Za3f2WpXKTWtl6qvIAyZGCw6kYqnzSg/vrRhqy2MxcirmykMLOR+ivuG9ci0TUPWpYnlHIVwhAOfrqx4g4SF3aprttcckd5iZopRllZt8AjrWR7wCuz8H3Gm3vD9lAZLa5EMCLKuQ3IcdCPA1t0GSuYaabUDUdbrdwhjaxj6SW1gLt8dPedFxbma3YxN186v+GuLtQ4ZE4slgYTcvN3qk4x5b1edpGk2NzqFiNHWwRRCVYJIqlm5j4eO1Ym6tptLmMFwgEhAbZgdjWY9k9DK4G4YNA63UXssyaCahmJYduY7wtfddqutzlQ8Vicf4M/fTP75msH+Jsf9GfvrGmUE5waLvB5VH8amvYSFIYxXDaUrZHtL1fGO4sd/8GfvpP742r/yNmP/ACz99Zi2iaZS64wDjep1po91etiJFPvLYFHQVVfMAYnuN1azFsRcbNkcrsdo+sYz3Vnj/Fn76B7SdYHSOz/0Z++sncssEpjDLJjqV6Z8qbWYZ+iaEfjMzTkMpuonGK7YynzWtbtG1YkHubRmPX8WfvqdZ8dalcA88NqCPJCP21igcPFhT7Z2qztL2KCCWFpo0YOQG6523xWnhuJSmW80pt37bX5q2LGK6+sxWpuON7y2i52W3GTgewdz86rZO0jVA/4qK0x/OjOftrM6jeJLMORi6AeHTNRO9HkapxL0he6QxxPsB05+9NLjlcTYSla798nWP5Kz/wBGfvom7R9XO5is/wDRn76yPe+40O89xrL/ABuf+6VT+M1390+a1B7RtWztFZ/6M/fVpaca3txZ98Ut+cA5HKcZH11gSwPhUrTE76/t4h1eVV36daIoMfqWz5XvLg7TXv5qyLGa7NbiE371rxx5qX8jafon76bbjzUyTmG0/QP31F4j01tMihkcxhXcr7O/hUC2tXurcyxcrgZyARkfV1rdmnq2TmnEhLhr3+SIlr8QY8xukNwrgcc6iT/A2n6J++lDjfUM/wADa/oH76ohbSZ2jNRZLqFB7LBz5ChpcSqYBeWQjxVH4rWDeQrUHja9X6SWg+Kn76J+N9SVGfu7XIGcch++qK20a8urRr3MfdIvOxLb8vXpVQZfxzuCSGJoepxyqha1z7gO28Oqd+J1zQC55F9lq37Q9SYDkhtRsM5Q9fHxpv8AfA1bP8Faf6M/fWYEo/NNH3o8jWZ+OVR1MxVH4vXf3StKePtWJ/g7T/Rn76Mcf6qP4q0/0Z++sx3o8jQ7weRphjdT/eKb8Wrf7pWp/fB1T+QtP0D99A9oGqfyFp+gfvrLd6PI0DIPI0/45U/3il+L1v8AdK1H74Gp/wAjafoH76L932qfyVp+gfvrL848jQ5x5Gm/HKr+8Uvxat/uFakdoGqD+JtP0D99D98HVP5G0/QP31luceRod4PKl+OVX94pfi9b/dK1H74Gq/yVp+gfvofvgap/JWn6B++svzjyNDvB5Gm/HKr+8Uvxet/ulaj98DVf5K0/QP30f74OqD+JtP0D99ZbnHlQ5x5Gl+O1X94pfi9b/dK1P74Oqn+JtP0D99D98HVf5K0/QP31luceRoc48jS/HKr+8fNP+L1v90rVfvg6r/I2n6B++h++Fqv8jafoH76y3ee40O8Hkaf8cqv7xS/GK7+6Vqh2h6oP4m0/QP30D2iaof4i0/QP31lOf3GhzjyNL8cqv7pS/GK7+6Vqv3wtUP8AEWn6B++h++Bqf8jafoH76yokHkaV3qjwNOMcqv7xS/F63+6VqRx/qn8jafoH76UvH+q4P4q0/QP31lO+HkaPvx5GrBjtR/eKcYvW/wB0rXx9oOpg+1FaY/oH76cHaBqH8lan/IP31jO/HkaHfjyNXs9IZmi3EupDGq3+6Vu17QrofxMH6P8A1pwdot2P4iD9H/rWC9ZH5po/Wh+a1FD0nfzcPJT/ABut/uFb798a8YfwMH6H/WjHaLdg/wADB+h/1rAi7A/Jaj9cH5rfOpj0nPUeScY5Wf3CugjtFvv5G2/Q/wCtJPaTekFWt7Yj+h/1rAeuD81vnReuD80/OnPpMORHkl+N1n9wrczdoV4xHLb2w+Kn76jtx9qH8jbfoH76x3rY/NPzoetr+YfnVR9JHnZ9vd+yY41Wn/qla8cf6gP4i2/RP30Y49v/AOQtv0T99Y/1tfzD86Hri/mH51H/AIjk/ufD9k34zW/3Stj+7q+O/dW/6J++jXju+GfxVv8Aon76x/ryn8g/OjF8n5jfOrR6Ru/ufD9kvxms/ulbNePL87d1b/oH76cPHN90Edt+ifvrFDUEH8W3zo/wkn8m3zq9vpELdqT4fspDGqz+6VP17W7jU7xppyoCgAKgwBt9tVKD1u4RFPL3jBQSOnhVlaaHNrfLNFeWMXOcCKWbD7bdMVqdHsbPh+1Fpqc9iLkuZBlh9E4x1+FAR0c+Izl0pyxnXMdj05jcKMVFNVvMspsDrcp7hXh6Th6W8vbidJQtsxKRgg4X2j1+FZy11e3m1PU7xIZFhuEbCEgsM++ugw9zLaX6s6gPZzAMSADlT41yKFe6yeY5YYOK0sVaMPkhhpxZgueup8fFaWKf8o2KKL2Rc+/+FOFwRTbbmjNJNctI8lc2UVGOtCjHWq2jVMt72e49Qu8/yo/q1ZcQWjzJBcoQEtWaRxvkjHh76z/AuqpBM2nGIlp3MgfmwBhemK2ssHrKyIFLIwIYDyPhXpWDPZJRNAO3zBv9l3mFsbU4eIgdtPfe/wBln0kLIrb4IB3qK1rJPK7XUplTmyiAYAHv86uZdGaBIo7SB+QZBBbOPLrSRpF2SAYSuTjJIwK3TlkAL/n/AC6GloZb5HNJt42/dQ1cKoQABcYwBtSeJeKtb4htbXh2WOI2VvhYpcHmGQBufH4VZronK/46aMr4hG3qZFbwWEALzJHEMsWY5I95oauhZUMDCbW5/MK5uFTSCzzlHNV9jobQ8nfDmCqOVVPQ++ndZ1xdChMjq0kjezyAgEA+O9Qdc41tdOkSOzVbpXU5dZQceH1GsE9zJK5aZ3kztliTisfEMZjiHDiNz8lTV4rDRNMFJq7men3Um91C41G4765fmbGF8gvkKZZe8QZYKVOeY02PZ6/EGoV1eGb2EPsePvrkKqtDGl8huT8Vyb5CSXONyUi4nNxINsAbAUFXAokTAyc599OAE9BWC0Oe4ySblD7pOKTTgXB3Bx40RUeVScwpWTXLk9attGj7yKRAccpz86reU56VZ6K4j77mYLnlxk4zRmEsAqW37/krYPbF1IvYSkI3G5qEkZDKcjrVvdQmRACpO9RPViDnkbat+rpCZbgIqSPtJ2A5k6EbVOWMsoOfCoEOz79KtYVBVAfKtagZmFiiIRdMtEQpHmKppdMlgTnLoQPLNaRocgYBPvqJdrA6mDvFaYnaMHfbrU8Qw9j23fuNtef1UpoARcqjjjKkEkbGpCQcjk5Bp82Dg7RPikiIr0U1jx0hjPaahRHbcJ72Dbd3j2t96jrbFyfaG1SreEvEHeNs0oxlNwhAO2cVoGnzgOcOSuLLgEplYioAyNqS8TOCFYZqVHEzMuVOCakx2SBubu6vZRGQWGykIi5Kt7Vu6XLL0FVWpKxuyobYKOhrfw9nfFd9YxzW3DuqSQTxh45EgYqykbEHyqh1zgTXdEcnUdHvYORFaRmjPKmfMjYVfWiN7BDEQT4q6eK7Q1qyqWpcn2gMVLstKkluoVDoOZwN81Nt7EDmJjP11stA4SgOp2F5dXFpbaSsAuLmea5RQmAcqd8gk4H10MzD44miWYaDVVwUmbtO2G6a4O0Sdm1a1Eqfj7FkU4OObmB32+NRJuBtWvNHu9Milh5pnjdSzkJ7GSSfLal3XaXdaLw1oFzw7qFvZaiZrqPUIUZZecgqEdlbOFKkgeGxNdUsdY4Vmsw0OtaXKsUaiZhOuxK4PMfec0K/Eqeq4lOwaEW87rSimppQY2jYED3g3XlizcW17DJJkrHIrNy9cA+FeoOzWy0u64TtuI7GG4jnu1khdpXzjlfBAA2HQVw/tbttDi4mgi4cSx9VFpHzepbqXyc5x+V0p/s216fh2S6nlu2gVLWfu45XIRnxlRyk4yTXOYTx4Zn0t+zufcsmhkdBMYSdOai9r2m3Nhx3qMtwyN663rUZV+Y8jbDPkdjVfwVxVFwxNdPLDLL36qo5CBjBz41E4l4l1Piy+jvtSCtOkQi5kTlBAJI2HxqsS1dlJ+iR0B8azmzSR1Znph1IuOo1QvrBjqDNB1uFJ1y/XVNXvL2MMqTytIobqAfOoUac5paxgA843oxyr0IH11QWF7+I/nqhXuL3FzuaTJEFAxTZGKDMSTvtRUPI5pOgUChSwmcHNIowxHiai0i+qSN15ffRU4mGX2tyKNlUqcAZq0xZu0E9kzQpXI3lRYPlVJaQmQoZoUVMNEkomk0dFTl10kpBlhTzEgZ8KYBIoFmIxk4q6OUMbZPdOwA9c7U7nwqPE/K252pwyL4GiIZQGWTg6J7NDNR+9kxjH104jHlGTvVzJw7QKWZLNFj2DRc24HnQb6J3xtUiRukm+pPupen3hsL2K5CBzG3Nyk4zTBLDfJoqA4zmva9mhGo+ii1xaQ4bromj37ajo8l0y8hPeDGc9BXO/KpVvqd5bwmCK6mSI59hWwN+tRDWri+LevxQ5h2mAgnqdNfgjKurE7GDmBqrNNYli0aTTRGhjlbmLeIOc1o+yq2W5125DMVItiRjx9oViuo61p+B9ROltf3BneGNYl52Xy5qfC53zV0Od2jRYX5AAlW4ZOfWo3SHRvyF1ouKL71K2u+R2EmWCFfDeueXl7cX0okuJXkYDlBY5wKlavrFzf3c5a4kkhaRioO2xNXPBOl2moi6N1bLKEK8rONgd8itLEKx+OVraWndlba2uxIub6X9ysmkdX1GRhsP4VP0jhyPT5VuO/LsUxjlx1xVlcQqkMj9QqknHwqbeGKyt3mkKqiKT1AzgdBmsTNr1xq2rQrbm4itXZEaLm2O++cedddU1VHhEbadjdXHQDv0vqtKcQ0jRGNzy+qfsJhqKyPErKiELlup2p/1cq+7ePlWmi0u0tEYRWsUSsckKMZqvuLdTM/JGOXPgKsjwt7Im8U5nc7Kt1E6Nozm5VRPnbemXQlCc1ZXEGOXKYG/UUzIg5CAooSekdmN0M+PUqu5fCk9376mdyCd1FKKIqlymQBnAGSaB9VJGqp4arzH76Sql3KKCWA5jgeFTp5IVtnmKGMBT9IYINZ6DUbu3kMkVxLG5XlLK2DjyrKxCaOlc0O1vrp0VEhawi6smiODv4VH7oedXFhdnU9LuXmt4O9iaICVEw7ZJySfEmozW+PyBVjqVsrGyR7H72+im+EWDmm4Iv8AG30VaV3NFKNgR4VNaLBPsimJVwRsBQElPlBQ5ZZRwfZxRU6U2zjanbYwLkSpk52qhkRc4NJsoht9FC+kDSGGDipDqMnA8aacZPShJY7KDgmqFLZcDpikkbUOWkKCArfdncy2+jatJ4ryn4+yawFbXgK3ebTdUczFIY8GRfBhyN19wra9H32rG6dfktbBL+ttt0PyKz2saj6/cRSxoYWiGBvvnOc1NubeDieQ3MbywSxRokisAwc7+0PKqNhg7nNX3DF5DYW2o3U0feIgjyvnliKupphVVLm1Xsv1d/8AUGx06dyopnmWUslOh1PuBUC+0Wz01EE95KZJFLKqxAjGcbnO1S14Ys7rS3v7PUXkVW7sq8PKQ3zqovryW+nLyyO4GQgc55VzsKtuELiQ6klkzFraUMzRHdSwXY486opRRS1nC4IyONhqbjkDv1TwGCSXh5NHaDe4PI79VOt9BttL4eOpXNwzczkKip1boB+rrVeeI1fTpbCK0EXeAgyF+Y79fCpHGF1ItxFZxyMtsIlfuQfYDZO+POs2Dj40TiNcaSU0tN2WtGU9/XU6qdXOIX8KEWsLHvPM6pRtl/OPyou4AP0j8qMufOk8x8zXOlkXJqzNFJEzrbdyuBtjmxvUb1fH5R+VDmPmaHOfM1ZI9sls+ttAnJuh3A/OPyou4H5x+VHzHzouc1Rkj6JtEfcD84/Kh6uPzj8qLnPnRc58zStF0SuEruB+cflS4VaCVJY3w6EMpx4imuc+Zoc586TTG03ASBtqFPvb+71GNY7mcuqtzAcoG9Hoiyx6rbrHKyiWQI2PFSdxVfznzqfoRY6xZBSc98uKOgn4tVHI8kuuNbm+6vieXytLjc3C0+u6Olro91JCZS/s9TnbmFYx7dUfl58464FdC4okuLfQpJlZ42MiDmBwcZrnjMckk9a2/StkIqWgD+kfMo/GYmRyhrRbT6lX2la2llpV5ZSqXMsZVGJxjbGP11Rm3X84/Km+b30fOfM1gz1nHYxkguGCwWc+dz2ta7ZuyUbcfnn5UXq4/PPyouc+Zoc58zQlouiquEfq4/P/AFUfq4/PPypPOfM0Oc+ZpWi/SlcJXqw/PPyoerD88/Kk94fM0Oc+ZpflfpSuEr1YfnH5UPVh+f8AqpHOfM0Oc+ZpvyuiVwl+rj88/Ki9XX88/Kk858zQ5z5ml+V0SuEv1cfnn5UPVx+cflSOc+Zoc58zT/ldErhL9XX84/Ki9XH5x+VJ5z5mj5z5mmtF0SuEr1cfnH5UPV1/PPypPOfM0Oc+Zp7RdErhK9XH5x+VD1Zfzj8qTznzo+c+Zp7RdEtEr1Yfnn5UPVV/PPypPOfM0Oc+Zp7RfpT3CV6qv55+VD1Zfzz8qT3h8zR94fOnDYTySuEfqq/nn5Ufqq/nn5UYbYUOY+dT4UX6U+iL1Rfzz8qMWa5+mflQ5j50fMfOpCOH9KVgh6kv8oflQ9SX+UPyo+cjxNDnPmalw4P0p7N6IhYr/KH5UfqS/wAoflQ5z5mjDnzNOI4P0pWb0RCwT+UPyoeor/KH5UvnPmacU7CrBTwHZvzUg1vRM+oJ/KH5Ua6crNgSH5U8W91O2hzIc+VWx0cDnhuX5qQY0myjjSlPWU/o1IGgoR/dDfo1NCLjoKi6jdNEFjRmV/pZHlWg6gpIGF8jLjxP3VpiY0XIUFtOjWRkM5HKcZ5aNdOhP98/7NN94zEnJJowx86yA2nvcM+J+6Hu3onDpsP/AIr/AGaFtpUU8oR7ruwejFf+tI5t+tDnPvqQZTXBMfxP3TgsveymzWEelzPGJjJMhGGAxyH76b1O9n1KcS3Dh35AmcY2FRjIT9dFv1q987cpjjFm9P5upPk0LW6A8l0GBnuuCZbiYgu9qwOBgbbD7K56auhdX0PDZT11jDJIqCIH6K75HwPlVKBvRmLVPG4QINw0bo7EanjNiFtQ0AoqMDNKwKGKycqzLJPLSlo8UdSDbJ7ICR4vbjLK46EHBBrbcKcT20mnRxX927XQZgxZSSBnbJrE4Jpsh4ZBLEcMPKjaHEJaGYSt1bsR9fFG0VdLRycSPyXWzqmmliUvYMeHM4zUa71zT7aF5muonCDPLGwLH4Cufrdx3kCsMBx9IeIpsLvggV1rseJbeEAg7Fbb/SOUjssHxWtfjWxAJjguWONsgD9tZq71vUbpGhuLp2Rhhk2wahMpRiponxycx2x1PurEqsUqZhZ7rW6aLHqcSqKgWe7Tu0Sc4JB6UC6xg85AFNPcIoHL7fwPSoxMkzYJLHyrAmrWs0ZqVnFyXcXTS+yNlB286KFPym+qlpCFHtAE06F91Ctie93ElNymsTuk0pB1pYj9kbZpfIB4CjWRO3VgamyM7Unu9utSliYkHlpXdfzRV/qxOqlkuoWN6WgPhvvTxi3+hS0jONlINNHTm6YMV/yllGaMxDlO/UVJVAIWYLzlY88vidqq9G1Nr6eaOdIY+RQV8CST0wa7yV8cUjInnV97e5bDsoIadymLlTbwySAhgik70/w3J61FK+MHvSSM58BU++sRLZXCxwgsY2wMe6s5w1eeqajHE7SNHMeTkU7cx2BNY9Q40GIwCT2HXHv2Q5bwpmh2xWsuT3FtLKBnu0LY88Cs3ogk1LVp9Q9iPl+koyfpDG3yq041keztYIY5Sjuzc6q2Cy48fdVjw7YwW+k20ncxo0kQZ2xjPvJo+oHr2KNp72bF2j3nl5XRjoTNU8MbN1KSYDj6R+VVwtm5jvt8Kmi9/DF+9nZq0MVucyyqw5iQdgMHBU1ZNbxE47hfqFabY4qvtxnQG1+p528FbwBLqzbqqqJOWMCnRbmUBSxwOgq3ubN7DSvwlcWU0dmMfjzCeQ5OBg4332rSdm/FPBmlLd3fEUFvJa3PLHbvcWpZedd2AONjgioVM8NMw3IcRyvrvZSdExmjnDwus/w3wtNrerWGnI5UXEgUsq5KrndseOK6jF6P0aSssuuSmPopFsAc58d6puPe1bgS14bv4eE0jttZmgAtbzT4e6aBucZHPsVJAPSuLt2pcbP14s1sjy9bf765Wv8ASKUPAhGQW7ig566OJwawfVXPFvHvEOh6/qOlWut6n6raXElvEi3Tqqoh5QAAdht0rU9nvaVrfG6LwPqRiktLmB4zckM04A9sMSThtxjcdK4vfXtxqNy9zdzyTzSMWeSQ5ZiepJ8TT2laxqOiz+sabez2c3TvIXKt8xXOjFn8fOfZ32H83WfHWubLnJNl6A1rssl02ya40+W6v5QQBCsIB9561zLiaeawkuNIeIx3MyCNkYYK82MZ99VEPaLxUpJl4l1fHh/ZT/fUvTuKBfXF7eaxcTX9zJByQzXA7xlcEYPMdxgeNdXS48aqI07nAZrjW2y0fX45m8NvZvz7lnNW0DUdDZRf20kPOSFLAgHHxrZHT7PhvhJonu3nuNYginEfdYEZG+M5369aqdR1x9SHJeXEl0UPMBKS4B92ajajrUl+rPeEzSkYWQ9V8vgKz4aKlopXyxvuLaX5cj+yFY6CFzyzW4sL8r6Hbu2UNmxIG8jmkz3AkTlx453qDLO/eKAxx40q4YhRg43rMdiF2vDVn5+iUsgZio6ikzzEEoB781HDEHOTmiLFjkkn41lPrCW2VebRLaQkcpApuhmhQbnlxuVFChQoVFJChQoUkkpW5aUJMkDam6FTbI4J7p/66IR++mafEi460QyRrvaTg3STEPM0l05RnNL7xfOlbMOmacxsd7O6VlHxmj91LZSGyB7NGGU7fsqkR8iU1k1QpZRsnA2ou7byqGR3RNZJoZxSihA3pNMbjdJKLZo1kwMY6UihTiRwNwU6dEuTuKBl64Gc01Q6VPjOsldHnaio6GKrvdMioUdCmSQ8KXHcSwpJHHIypIMOB0YZzvSMUCpqwOcNWpwSNkDuc1q+E9XtNN0y5SWULMXLoh2DYXzrKcpFDBx0ozDq6Sin47Bc6/FX0tS6nk4jN1dazxTLrNqLd7dIlDh8qxPT/wC9TOD+smOnepWZKkdRSoZGjdSGYLkE4NFUuMzNrW1dT2yBbkOVunerG1jzMJpNSui8Wak+l2UWIhIJ3KbsRjAzUnRolu9Ft76ZhCHTJydhuR1+quYySvIfadm3yMkmptnrd5ZRtHG4ZCvKEfcL8BW/T+lp9cdLKCGEWA0Nu/l/CtI4w105kc3S1gOi6Be20UnJyTKwHiu9QpLJeXZz8qznDmrW9q1yby4K8/Ly5BOcZ8qvV4j0hTk3RP8A5bfdXS0mN0dZCJZXNYTfQuF+nciGTwzDO4gX70oWIz9M/Ks4+vyKzKLZDykjPOa0q8UaMCT6z/6TfdWf4tjgjuLZ7eJEjlh58ouObJ6msvHahgp+NQzDs7gWO9gOtkNWtDY88Lwbb21Vlc6cLvT3DSFOZOfYZxtnFY87Cr3RtZUWs8F1PIzkERggttynas/vXL45VwVLYp4RqQQeunXzWfVOY8Ne1a6w/tJpUtxExlaaNJMMMBcb/tqBpt699I4KBAgB2OfGoV5fS3bwWttNI0ZiSLkHsgtjBHzrTQwafw5bI9zzwtcAcysOfDKN8H662aKX1mVohdkhiHaJ2ub7Hx70VGTKQGmzG/X91DktxuxJqNNAuRvVwYvWgJ4FzHIOZT0yKiXqeqL3ko5VUZJ61qVVG0ML7ab35eKnJDpfkqqWAgYB2poQ4OcmpU0kgsJL23zyA+y/146GpGlNLeWivIodyzDJA33rEZAyWYRDci46WQojDnZe66r4oVdsM5H1U1cQok2FYnAB6VIe3l7xvYxufGmpYHD7rQs0Jy2yc99VU5ulrKNMByjBOc01ipkkXsDA3poxN+bWfLA7NdVObqo/LVlp+t3On2F5YxCNorsYfmByPDbFQSjdcUkoR4VCJ74XZ2aFKN74zmYbH76ImzvvWivrC20nQ5BDcm4ku+57xTgd1+UOnyrPFTTkl1IyNCPZjYhmUeYGxq6nmZG1+ZtyRYHpcEFWQytjDri5IsD0uCmTUrStQfTL+O6jRHZMjD9DkYqLj3UXKRQkcj43iRu41VLHFjg5u4UzVdSk1O472VEUqvIAvkDUGjINAKc1GaV8zzI83JTPe57i525RUKUUI8KICq7FRsgNzQYYpQGKS+25pEWGqSTRZNK5SfCiKkbVWQUyKhQwaMgnwqNjZJJzQzR8pochxUbOSRUYYqcgkH3UVCmuUlb3nEV7e6Xb6dK/4qDyO7/m5+HhVSWoqFXTVMkpvIbnZWSzPlN5Dc7e4IZoZoUKpzKtHmhmiodKa5SQzR5oqFK6SOizQoUrpIUKFCmukhQzQoU90kdCioUrpI80M0XShSukjzQzRUKe5SR81Hmk5pQQ+VSBJ2SRc1GBnPuo+6ZhsKHIyjcVMNdzCeyCjJxTotyRkZ+VJiwGGaeLnoCcUTDG0i7lJoHNNleXbPTak5pTAk0XIfKk6+wSKAOaPpQC0rlqQBSRUVK5T5UAh8qexTohvRgb0fIfKlKh+upNaSU9kXSnwnsjfwpvkYeFSFViBt4UbBGSTcKbQm8DnAz1qfDarE3MGJ2x0puC2JJcr02FS442B3FatJTEdpw8ERGzmUk7CqW5uDdurlQpAxsaurs91A2+GYEL8ape5byofFi/SMbc/oo1F9GpoCjxTwgkx9Gj7lx1WsgQO6IcNKZANAinu6YDJFF3bEZxT8IhLKmqMDwzTndt5UfdnypCJyWVNMCDgk/ClKNxS1icnAXNPRw4Xdfaq2OAuKcNTXdUoRAeNP8Aq8h/I/XTyWk2BhP10fHSOcdGq0Rk8lBMe+BR91g75qZaTWs0/dGUB8HAwdz5VJNlK+/d5+VTioRK3NGb+GvyUmw3Fxqqnk99GYgR1qcbaTP0Kbe2kByU2+NRdRuA9n4JuGeirvVXhy0ErBunxpBvLmE8rgE+8VZGByNl/XQNu+N1oQ0Mg/0SW+dlDhHkqtr6VyMqvypvlmmPRjn5Va9w2fo0O6ceFDvw+V/+q8n3KJiJ3VclowHtnB91PxxBRy5qUIpG/JzTkdrIW+h+ur4cPDSMjU4i6KJ3IPjTotwPGpiWM8hASIknbG1LSEySiJVBctygDz8q0WUFvaCubCeiiLCOUbmnYbRZc5YjHuqdLaPYN/ZIEBU/lHpmmbsrbWZuFkVUkyiupyObHuox1IyIZpOQ1CtMOW+YbJa2KhGPM2AMjam+4UdSanaZrttJb2lnLdo55Qjh1O5+VSItIlvueSxaK5TJx3bj5YNGxxQzMa6Ag9wNz8Ci20wkA4PaPQbqClnbxnvZWZkVSzADc7U8mn217bpdW3fJG4xhl3yKjXWowwvLbQyst0MxKApGH6denWosU1zaalbvrF9JG0eD3Zy5KfVt51XJWUsUgZlDm7Ei1gb83X00v2earuwHKRcfLxP0V/pVx32oy2fIR3UYbnznO+OlZ3igrBxLK7DPK0bHz6CpnEWqWE1pEdNuGWYv7ZRShK48T8azE80s788sjSNgDmY5OKyvSHFmmP1RpzEODg4WtbWw911XW1DQzgg3IN7jbwXR7CZNYsTcQh0jk5k9rqPCsAxOk6qSn4z1WbbO3NymlQ6zeW9mlrBK8So7NlGIJzjr8qhySNK7SOxZmOST1JoDGMbZXRwkD8xm55XsL294VNVWtma2w7Q5q84s1EaheW0vIFLWykgHOM5OKtJuJoU4Ugi5YjcOhgMeScKNuY/dWOJzTkFvJdSCKIczHJxnFDRYzUceWSIdqUW/nf8AdRbXSB73N3cLKx0HiKTQWmMdvHN3oAPOSMY+FXCce3Mjezp0BJ8AzGqKLRbwSKWhGOYE+0OlbLUeNry31CRtJ1Ex2xA5BHEq8u3TpWhhr8RhiLHSmNo2BaNb3va/81RdJNMyIh0mUDYWBOt+ttvquja32qQcPaSOCdQ4Z03V7C1jjB9YldeckCTJA6EFvDyrmvGnGtvxLp+nabYaDY6LZafJLIkNq7sGZ8ZJ5vhWf1HVpdRvJbm8naeeQ5eR9yxxVfO6tjkPyp6qWJxc5u53Q1TOHucW7H7oOOdSpzvTYtwPyjRBgpDMcAUbM0pHdHYdSRWU8sdqRc9EHoUk26Z+kaIwKPyjTwjQbucnzO1R7gZkLKcj3VVNGyNubKmIskypy4wc5pUVy0aCMKCM0hH39omj5lOSevhQjHZXZ4zZRBtspKS8wz0pqS4JDLy/XmmPGhnNWvrHuFgUi4oFssDSpJTIAMYpFChM7rEX3UboUKFCoJIUKFCkkhQoUdJJFQoUKSSFChihSSR0VCjpJIqcjYk4puhU2PLTdOCn2+iaY3G4oZNCpSSZtUiU4HOBsN6WM+NM0OtWNlISunioYYNF3K+Zprf30BTGVpOoT3Tvcr5mh3K+ZoywHWjByMirwyO9rJ9E1IgTGM70ipINHsdsVB1OCbgpWUahUvlHkKYML5OAPnVb6dzdtUxam6FBlKnB60KoNxoVFHmgH86KhUg8hJKYhhtQBNJoU+fW6V0oe1saPux76TQBA61Nrgd06X3a0XIPfQ5xQ5xUuwn0RMAuMUnNL5x/9CjDAnH7KgWAnQpk3T93fz3qxLMwIhTkTAxgUWKaKHNSOdjS1p0O6cEgWB3RI7RtzKcHBFDNAqRQx41V2gMqinLef1e4im5Q3duHx54OaXd3cl7cyTSMxLuWwWJxk9BTHKetFU+K8M4d9L3spBxy5eS2Pr7WPDkUsRTvFiTlzg+PlVXPq02oaRcSzhOYMIxyjHXeqLrR4OK2Z8fnlAYLhuXLa+m1r7IuSsc7QbWtZXnDMpuJGsZFRoCC5BG+dqstaum0WOEWqRZcscEZAxWSAKnO9GXPjSp8bfDSerhvb5OvqB02+qZlWWR5ANeq1kcHeIHJOWAY49+9RLmJRKRk9BVI+pXLqFMmAAAMCn7TWZLWDuwiuck5bPjWkccpZewQWjr+ymahjtCFMeIY6mkmIYO5pEOvsWImiXk6+wN8/XUp9Wh74Q925LYAbbG9SZNRyjM1/dsUhwzrdQu6HmaaddyKtJ5orZA0nTONhmmuZZQHQZDbjaq5aNgOUOF+ii6MbXVdy0RQVPdc+ApDIceFCupLKsxqFyCiKVMCfCk92apdTKORQyBmiA3qYVxsaHLnwqv1dNkTJUFfhTNSyhAzSDTSRJFqjZ91Gy7U8RSSMVTw+RUbJtQ2RkbUbJk5zS6L30sgtYprJsqAM0gnAp+kt16VU9nRMQm0PhS6FFTAWCSHKPKksniKUM4oGmLQQkmiMdaKnqDDI2qoxdE1k1Q8KPkPuouhqsghMgKOhRUySGKFChTJIUKFCkkhvQoUN6SSFChQ+FJJCh8aFCkkhQoUKSSGKFORDY0sEc2MVc2LMAbp7JinOop3A8qMDHhVzIbc1LKij+gKNgGo+lAeNEgaWKkmgvt7eBpylgbjalYp2RWSDU2q5wKW6BDgHNLCnY0ur2xaKYbomOTK5zvR8m1PKuTtS+6PkKtbBdPlUblpxIeYZPSpZAAyRgVHu7gwopQA5ON6tfAyIFzzoFItDdSkMoXYb0Sj2hU5N/lS1Qlh7O3wq8UdzoVLh3UQKCOtSFi2FP8AdnyFSkAAGw6VpU9Fvcq5kSatUwh+NPBQD1p2Pp0FVmt6hCttJBFNy3COPZAIO3vo+eSOkg4jzsPC/cO9EOtGy5Q1Q5MaDqMtVXNc9wQvLzE7/VUq3u1uFBL8zqo5iap3bmkJzkc21cnitWNJYj7XwtZZ0z7nMOauoD3kKPjGRnFKMfN1yKamvYoByknn5cjAyPdQ0JudpyxyTg7/AF0ayVhlZTA3J37rBWNIJDUqSHCE5NI5AF3q3IGDgD5U07RrszRqfIkUbJQtGt1aYQq0KD40axAjrVusYPQD9VR7rVILCUQyxuWwGyFHjUX0ccLeJM+wSMIaLuNlGhiUsc56VIS1UkbnrTE+t2skWUWQYYZ9kCnLXiK0hiCskpO+/KKaGooQcrpBbqk0xA2LlNNoF3BOKganqM+myxCNEKMv5Q8c1T22oS2kwljOSM4Dbjf3U1dXL3czyyHdjnA6Cs+qx9jqciAFr79eX85Kp9UC3saFSJIfVtTVCx+mpJG3X/71qyh7rkIK7Y2O9YgsS3Nkk+eakT6jc3EMUTyHliBC46nPmfGhcMxiKkEt2e0bgfS/gowVDY81xurqLUO81OOzERAZuUsTuPqq0ktxkrnasfa3T2tzHcKAzxtzDm6GpkvEV/I5ZXSMH8lVGB860KH0ghbG41Ny4nQAbC3uVsVUwNOfe6vzaKN8mm3tfZPtVQ/h7UMf3R/siiOuX56z5/yRVrsfoT/Q74fdSNVD0KuO4A8WoCEeZqp/Dt35RfoUX4buvKL9Gqvxmi7/AC/dV8eNXSQDfGacCrEOduYgeVZq5v5rpgzNykDHsbUwZJDsXb5mqXekMTCRGy/Q3+lk3rTRsFoLnXmsbrkihV1XByxOc/VSrO4WctcFeUhw2M7Ek5rODmPifnTgjkA/60NFjtQZC54zN5DTT32VfrLybnZXXEesS395JD3aqAVOVz1AqDzs1gtufZDSd5k+4YqIIHJBJ+vO9LeAsRyn5mqJqyeeR80gPa0t3E7bJnzPkeXu3KjgMWwoJJ2Ap+0F3FMJIOdZIyGBBxgg04FxvRkgedCspQ1wcSdOmiqaLG6OeKa7uJJnxmVy7EeZO9JltApAZ2JxTJc5NDvMdTUnOhNyW6nndIuB1KW65ULk4FN92PM0ZlFJMgIPWqHujJumNkfdr76bIGTRU8trKyhgowRkb1QGmTRjVG19km3i7+4jiJwHYLnyya0lro1vZT98k7uVyuGAFU9ohiQhxg5zR3E34o4J8K3cOENKzjSszO3G4tZFRFrBci5Vg+pSq7KAmASOlVplOfCo3eDzNM5NUVeKvltfVVPmLlIaXmlIO1CV+QDHjTcYOcnypLb4xWeZnZSTuVWSlLzynl8KlRRiJeUEneozsApXxpvJ8zTslETrnUpwbJ2aZnyhAwDSM4j5aRQod0rnEkqN0KBGKOhVSZFQoUKSSFChQpJIUKFCkkhQoUKSSOioUKSSFChQpJIUKOizSSQoUKFJJChQoUkkKFChSSQowaKhT3SR9aFFQprpJRYnrRhyoxtSKFSzm97p0vvW91Gsrc3hTdCpCVwO6VypHeH3UrmqLml8499EMqOqfMnGQMcnNJMSgE70BIAPGlK4ban7DvFLRM0KkYHlRNGG3qs0xtoUsqYoURoUOoo80VChTJIUM0KFJJCjBwaKhTgpJfeN7qPvPdTdHUxI4J7lLLA0WBjFIoUjJfcJXSwNsUOUUkHai3py5vRJKwuaPPL0pFCo5rbJk4XOKACuMmm6FS4t906c7se+klPKgHx4UfMD5inOQpJPQ0pZWWRXB9pSCM+6kk0RqFy3ZNspFxfTXShZOXAOdhilR6hNGgVeTAGBlai0Kt9amzZ8xupZ3XvdS21KdsZ5Nv5tHHfSu4VuXB8hUOhUxWTXuXEp+I7qrB53UEgj5VMVAyKSOoBqjzR8x8z86LjxPKSXNv7/ANlNstt1bSrhulJHWqvmP5x+dGjEHOWp/wASaXeylxR0VpjIpPdioSTFXUlmwDvT/rsfk3yq9tXE8XdonDwUsoPfRFRmketxnwb5UayrJ0ztS4kbtGlK4KBRffQ5BjG9KodaYtCVk2VHhmi5AacoEVAsCayaKDwouQY8acIpNVlgTWSeUUkrS6FQLQmskYoUo0D0qJCayTgDek90vvpdCo5QlZNlBmiZQOlOmiNQMYTWTNCnT0pGQaqdHZNZJoUrIojUCEyKhQ60oL76QbdJJNCncClqNsYq1sN0+VNIgYnNL7pffSsUdXtiaBqpABI7pffR9ynvpdHUxEzonsEkRqOmaMRqGzvmlAUeMVaI29E9gi5RS+QHzoqMGrmgKQQ5RjFGsYPnSWmVTg5z8KL1pE3IY5pZ4xqSlcJ4Rr76MRr76Y9ej/Nf5UYv4x+S9SFRB1CkHNUnuwB40pYwfA1USSl5GYFsE560BIQuAzdc9apGJszez8VHijornu1XcZox1qkLt+c3zoudvzm+dS/GGg6M+P7J+MOiuLmd0nihGOV+u29QbyUmfuiByowqKWJIPMfnREknJJNC1OIumBHU/Dp9VB8pcri1uHknZH5cAZGKs5ZFitmkBX2UzjNZShRtPjzomFpZcnnf9lYyoLRayuo9aizl1PyqM+uXfM3IY+XO3seFV2RQoSTF6p4AD7eGigah55qeNbvRJziRemOXl2+VQ55nuJnlfHM5ycUihQctXNKMsjyRvqVB0jnCxKFChQodQS5ZWlYM2MgAbCnLW9ltObuivtYzkZpihVjZntfxGmx6qQcQbhT/AMNXn5yfo1CkdppWkfHMxycCk0M1OWplmFpXEjvKdz3O3KcMjk55iNsbGhLK8xUyMWKqFGfIUjNFUTKSLEqNyj6UVChVd0yFChQpkkKFChSukjoqFCnukhQoUBSCSPFKVQcUOceVH3i46GrWtbzKcIxEvvozGo3Gabdg3TNBWwOlTzR3tZPongeVcCh3h91M83xos71Pj2FgldSO9bpgUO9b3Uz3m3SiLZpzUnqlmTpmbGRikGdj5fKm6FUOneeaa5Suc0ROaKhVRcTumQpSDmYA+JpNSYP4M/GrIWZ3WTgXKP1dPf8AOlrMyAKMYGwzTbtkjFNOwIxvmjjI2LVmine2yelupFIAxv7qZaZ2XBxj4Ug/GioSSoe86lQLiUeaKhQqgm6ZKDEDAoqKhmkXEpIdaGaFCldJChQoUySOioZoeFJJChQoUkkKFChSSQoUKPNJJChQoqSSFChQpJL/2Q==")

@app.get("/ref-banner.jpg?v=3")
async def ref_banner():
    return _HwResponse(content=HW_SHARE_JPG, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})

def _public_base(request: Request) -> str:
    b = (os.getenv("PUBLIC_API_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
    return b or str(request.base_url).rstrip("/").replace("http://", "https://")

@app.post("/api/referral/share")
async def referral_share(request: Request, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    """Готовит сообщение «фото + ссылка» для WebApp.shareMessage (Bot API savePreparedInlineMessage)."""
    u = await current_user(authorization, x_telegram_init_data)
    link = f"https://t.me/{(BOT_USERNAME or 'casefightbot')}?start=ref_{u['tg_id']}"
    photo = _public_base(request) + "/ref-banner.jpg"
    out = {"ok": True, "link": link, "photo": photo, "prepared_id": None}
    if not BOT_TOKEN:
        return out
    try:
        import httpx
        result = {"type": "photo", "id": uuid.uuid4().hex[:16], "photo_url": photo, "thumbnail_url": photo,
                  "title": "Приглашай друзей", "caption": "🎁 <b>Win Gifts</b> — кейсы, апгрейды и бонусы!\nЗаходи по моей ссылке и забирай подарок 👇", "parse_mode": "HTML",
                  "reply_markup": {"inline_keyboard": [[{"text": "🎁 Открыть Win Gifts", "url": link}]]}}
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/savePreparedInlineMessage",
                                  json={"user_id": int(u["tg_id"]), "result": result, "allow_user_chats": True, "allow_group_chats": True, "allow_channel_chats": True, "allow_bot_chats": False})
            j = r.json()
            if j.get("ok"): out["prepared_id"] = (j.get("result") or {}).get("id")
            else: out["error"] = j.get("description")
    except Exception as e:
        out["error"] = str(e)[:120]
    return out

# ===== PAYMENTS (Stars webhook + TON autocredit) =====
async def _tg_payment_update(body: dict) -> bool:
    try:
        import httpx
        pcq = (body or {}).get("pre_checkout_query")
        if pcq and BOT_TOKEN:
            async with httpx.AsyncClient(timeout=10.0) as c:
                await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/answerPreCheckoutQuery", json={"pre_checkout_query_id": pcq["id"], "ok": True})
            return True
        sp = ((body or {}).get("message") or {}).get("successful_payment")
        if not sp:
            return False
        payload = sp.get("invoice_payload")
        got = None
        async with get_db() as db:
            row = await (await db.execute("SELECT tg_id, amount, paid FROM deposits WHERE payload=?", (payload,))).fetchone()
        # проверки подлинности: валюта, сумма, плательщик и реальная транзакция в Telegram
        if row and not int(row[2] or 0):
            payer = int(((body.get("message") or {}).get("from") or {}).get("id") or 0)
            ok_pay = (str(sp.get("currency") or "") == "XTR" and int(sp.get("total_amount") or 0) == int(row[1])
                      and payer == int(row[0]) and await _star_tx_exists(str(sp.get("telegram_payment_charge_id") or ""), int(row[1])))
            if not ok_pay:
                print("[payment] REJECTED fake/mismatched payment", payload, sp)
                try:
                    if ADMIN_TG_ID:
                        await _tg_notify_sync(int(ADMIN_TG_ID), f"🚨 Отклонён подозрительный платёж Stars: payload {payload}, от {payer}")
                except Exception:
                    pass
                row = None
        async with get_db() as db:
            if row and not int(row[2] or 0):
                await db.execute("UPDATE deposits SET paid=1 WHERE payload=?", (payload,))
                await db.execute("UPDATE users SET balance=COALESCE(balance,0)+?, deposited=COALESCE(deposited,0)+? WHERE tg_id=?", (int(row[1]), int(row[1]), int(row[0])))
                await db.commit()
                got = (int(row[0]), int(row[1]))
        if got:
            await mark_deposit(got[0], "stars")
            await pay_referral(got[0], got[1])
            await add_history(got[0], "deposit", "win", f"stars +{got[1]}", got[1])
            await tg_push(got[0], f"✅ <b>Пополнение +{got[1]}⭐</b> зачислено на баланс!\nУдачной игры 🍀", "🎰 Играть")
        return True
    except Exception as e:
        print("[payment]", e)
        return True

async def ton_autocredit_loop():
    await asyncio.sleep(20)
    while True:
        try:
            if TON_TREASURY:
                async with get_db() as db:
                    rows = await (await db.execute("SELECT id, tg_id FROM ton_deposits WHERE credited=0 AND created_at>?", (now_ts() - 7200,))).fetchall()
                for r in rows or []:
                    res = await try_credit_ton_deposit(r[0], int(r[1]))
                    if res.get("credited") and not res.get("already"):
                        await tg_notify(int(r[1]), f"✅ TON-пополнение зачислено: <b>+{res.get('stars')}⭐</b>")
        except Exception as e:
            print("[ton_auto]", e)
        await asyncio.sleep(20)

@app.on_event("startup")
async def _start_ton_auto():
    asyncio.create_task(ton_autocredit_loop())

# ===== DOORS: баффы и боссы =====
class DoorsBuffRequest(BaseModel):
    game_id: str
    buff: str = "skip"

@app.post("/api/doors/knock")
async def doors_knock(body: DoorsActionRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    g = DOORS_GAMES.get(body.game_id)
    if not g or g["tg_id"] != u["tg_id"] or not g.get("active"):
        raise HTTPException(400, "Активная игра не найдена")
    if g.get("offer"):
        raise HTTPException(400, "Сначала выбери бафф")
    cfg = DOORS_DIFFICULTIES.get(g["difficulty"], DOORS_DIFFICULTIES["medium"])
    for k, v in (("ins", False), ("focus", False), ("shield", False), ("boss", False), ("offer_at", random.randint(2, 4))):
        g.setdefault(k, v)
    if g.get("ins"):
        g["ins_used"] = True
    boss = bool(g["boss"])
    base_fail = 0.86 if boss else cfg["fail_prob"]
    shielded = bool(g["shield"])
    fail = base_fail * (0.5 if shielded else 1.0)
    g["shield"] = False
    tricks = ["🚪 БАХ! Перед твоим носом захлопнули дверь!", "👹 Из двери выскочил монстр!", "💀 Ловушка! Дверь заперлась на засов!"]
    treats = ["🎃 Хозяин вынес корзину сладостей!", "🍬 Ведьма угостила конфетами!", "🕯️ Дверь скрипнула — внутри монеты!"]
    if random.random() < fail:
        g["active"] = False
        DOORS_GAMES.pop(body.game_id, None)
        refund = int(g["bet"]) if g["ins"] else 0
        g["ins_used"] = False
        if refund:
            u["balance"] = int(u["balance"]) + refund
            await save_user(u)
        await add_history(u["tg_id"], "doors", "lose", f"Дверь #{g['step']+1} ({'босс' if boss else g['difficulty']})", -g["bet"] + refund)
        return {"ok": True, "slammed": True, "boss": boss, "refund": refund, "step": g["step"] + 1, "mult": 0.0, "win": 0,
                "phrase": "🛡️ Страховка вернула ставку!" if refund else random.choice(tricks), "balance": u["balance"]}
    gain = 6.5 if boss else cfg["step_mult"]
    # баффы без перекоса в минус кассе: шаг с чесноком/страховкой пересчитан до EV ≈ 0.95
    if shielded:
        gain = max(1.01, round(0.95 / (1.0 - fail), 3))
    if g.get("ins_used"):
        cur = max(1.0, float(g["mult"]))
        gain = max(1.01, (0.95 * cur - base_fail) / ((1.0 - base_fail) * cur)) if base_fail < 1 else gain
        g["ins_used"] = False
    fx = None
    if g["focus"]:
        fx = weighted_pick([0.5, 1.0, 1.5, 2.5], [45, 30, 18, 7])  # среднее ≈ 0.97
        gain *= fx
        g["focus"] = False
    g["step"] += 1
    g["ins"] = False
    g["mult"] = round(min(GAME_MAX_X, max(0.1, g["mult"] * gain)), 2)
    win = cap_win(g["bet"] * g["mult"])
    if g["step"] >= cfg["max_steps"]:
        g["active"] = False
        DOORS_GAMES.pop(body.game_id, None)
        u["balance"] = int(u["balance"]) + win
        u["wins"] = int(u.get("wins") or 0) + 1
        await save_user(u)
        await add_history(u["tg_id"], "doors", "win", f"Все {g['step']} дверей! x{g['mult']}", win)
        return {"ok": True, "slammed": False, "auto_cashed": True, "boss": boss, "step": g["step"], "mult": g["mult"], "next_mult": g["mult"], "win": win,
                "phrase": "👑 ТЫ ПРОШЁЛ ВСЮ УЛИЦУ!", "balance": u["balance"]}
    g["boss"] = random.random() < 0.12 and g["step"] < cfg["max_steps"] - 1
    out = {"ok": True, "slammed": False, "auto_cashed": False, "boss": boss, "next_boss": g["boss"], "focus_fx": fx, "step": g["step"], "mult": g["mult"],
           "next_mult": round(g["mult"] * (6.5 if g["boss"] else cfg["step_mult"]), 2), "win": win, "phrase": random.choice(treats), "balance": u["balance"]}
    if g["step"] >= g["offer_at"]:
        g["offer"] = random.sample(["insurance", "focus", "shield"], random.randint(2, 3))
        g["offer_at"] = g["step"] + random.randint(2, 4)
        out["buff_offer"] = g["offer"]
    return out

@app.post("/api/doors/buff")
async def doors_buff(body: DoorsBuffRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    g = DOORS_GAMES.get(body.game_id)
    if not g or g["tg_id"] != u["tg_id"] or not g.get("active"):
        raise HTTPException(400, "Нет активной игры")
    offer = g.get("offer") or []
    key = {"insurance": "ins", "focus": "focus", "shield": "shield"}.get(body.buff)
    if key and body.buff in offer:
        g["ins"] = g["focus"] = g["shield"] = False
        g[key] = True
    g["offer"] = None
    return {"ok": True, "buff": body.buff}

# ===== ЗЕЛЬЯ СКЕЛЕТА =====
POTION_GAMES: Dict[str, dict] = {}

class PotionStartReq(BaseModel):
    bet: int = 55
    poison: int = 1

class PotionPickReq(BaseModel):
    game_id: str
    flask: int = 0

def _potion_roll(g):
    g["bad"] = random.sample(range(g["n"]), g["k"])

@app.post("/api/potion/start")
async def potion_start(body: PotionStartReq, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    k = 2 if int(body.poison) == 2 else 1
    bet = int(body.bet or 0)
    min_stars = max(11, int(round(0.1 * float(STARS_PER_TON))))
    if bet < min_stars:
        raise HTTPException(400, f"Мин. ставка 0.1 TON (~{min_stars}⭐)")
    bet = min(bet, int(round(500.0 * float(STARS_PER_TON))))
    if int(u.get("balance") or 0) < bet:
        raise HTTPException(400, "Недостаточно средств")
    u["balance"] = int(u["balance"]) - bet
    u["games"] = int(u.get("games") or 0) + 1
    await save_user(u)
    await hw_track(u["tg_id"], "wager", bet)
    gid = uuid.uuid4().hex
    g = {"tg_id": u["tg_id"], "bet": bet, "round": 1, "n": 3, "k": k, "mult": 1.0, "state": "pick", "bad": []}
    _potion_roll(g)
    POTION_GAMES[gid] = g
    return {"ok": True, "game_id": gid, "n": 3, "k": k, "round": 1, "balance": u["balance"]}

@app.post("/api/potion/pick")
async def potion_pick(body: PotionPickReq, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    g = POTION_GAMES.get(body.game_id)
    if not g or g["tg_id"] != u["tg_id"] or g["state"] != "pick":
        raise HTTPException(400, "Нет активной игры")
    f = int(body.flask)
    if f < 0 or f >= g["n"]:
        raise HTTPException(400, "Нет такой колбы")
    if f in g["bad"]:
        POTION_GAMES.pop(body.game_id, None)
        await add_history(u["tg_id"], "potion", "lose", f"Яд, раунд {g['round']}", -g["bet"])
        return {"ok": True, "boom": True, "bad": g["bad"], "balance": u["balance"]}
    g["mult"] = round(min(GAME_MAX_X, g["mult"] * 0.95 * g["n"] / (g["n"] - g["k"])), 2)
    win = cap_win(g["bet"] * g["mult"])
    if g["round"] >= 3:
        POTION_GAMES.pop(body.game_id, None)
        u["balance"] = int(u["balance"]) + win
        u["wins"] = int(u.get("wins") or 0) + 1
        await save_user(u)
        await add_history(u["tg_id"], "potion", "win", f"3 раунда x{g['mult']}", win)
        return {"ok": True, "boom": False, "auto_cashed": True, "mult": g["mult"], "win": win, "bad": g["bad"], "balance": u["balance"]}
    g["state"] = "decide"
    return {"ok": True, "boom": False, "mult": g["mult"], "win": win, "next_n": g["n"] + 1, "next_k": g["k"] + 1, "balance": u["balance"]}

@app.post("/api/potion/next")
async def potion_next(body: DoorsActionRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    g = POTION_GAMES.get(body.game_id)
    if not g or g["tg_id"] != u["tg_id"] or g["state"] != "decide":
        raise HTTPException(400, "Нет активной игры")
    g["round"] += 1; g["n"] += 1; g["k"] += 1; g["state"] = "pick"
    _potion_roll(g)
    return {"ok": True, "round": g["round"], "n": g["n"], "k": g["k"]}

@app.post("/api/potion/cashout")
async def potion_cashout(body: DoorsActionRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    g = POTION_GAMES.get(body.game_id)
    if not g or g["tg_id"] != u["tg_id"] or g["state"] != "decide":
        raise HTTPException(400, "Нет активной игры")
    POTION_GAMES.pop(body.game_id, None)
    win = cap_win(g["bet"] * g["mult"])
    u["balance"] = int(u["balance"]) + win
    u["wins"] = int(u.get("wins") or 0) + 1
    await save_user(u)
    await add_history(u["tg_id"], "potion", "win", f"Забрал x{g['mult']}", win)
    return {"ok": True, "win": win, "mult": g["mult"], "bad": [], "balance": u["balance"]}


# ===== КОЛОДА СУДЬБЫ (Hi-Lo) =====
HILO_GAMES: Dict[str, dict] = {}

class HiloStartReq(BaseModel):
    bet: int = 55

class HiloGuessReq(BaseModel):
    game_id: str
    guess: str = "hi"

def _hilo_info(c: int) -> dict:
    ph, pl = (14 - c) / 13.0, (c - 2) / 13.0
    return {"hi": round(0.95 / ph, 2) if ph > 0 else 0, "lo": round(0.95 / pl, 2) if pl > 0 else 0}

@app.post("/api/hilo/start")
async def hilo_start(body: HiloStartReq, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    bet = int(body.bet or 0)
    if bet < max(11, int(round(0.1 * float(STARS_PER_TON)))):
        raise HTTPException(400, "Мин. ставка 0.1 TON")
    bet = min(bet, int(round(500.0 * float(STARS_PER_TON))))
    if int(u.get("balance") or 0) < bet:
        raise HTTPException(400, "Недостаточно средств")
    u["balance"] = int(u["balance"]) - bet
    u["games"] = int(u.get("games") or 0) + 1
    await save_user(u)
    await hw_track(u["tg_id"], "wager", bet)
    gid = uuid.uuid4().hex
    c = random.randint(2, 14)
    HILO_GAMES[gid] = {"tg_id": u["tg_id"], "bet": bet, "card": c, "mult": 1.0, "n": 0}
    return {"ok": True, "game_id": gid, "card": c, "balance": u["balance"], **_hilo_info(c)}

@app.post("/api/hilo/guess")
async def hilo_guess(body: HiloGuessReq, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    g = HILO_GAMES.get(body.game_id)
    if not g or g["tg_id"] != u["tg_id"]:
        raise HTTPException(400, "Нет активной игры")
    hi = body.guess == "hi"
    c = g["card"]
    p = ((14 - c) if hi else (c - 2)) / 13.0
    if p <= 0:
        raise HTTPException(400, "Невозможный выбор")
    nc = random.randint(2, 14)
    ok = nc > c if hi else nc < c
    if not ok:
        HILO_GAMES.pop(body.game_id, None)
        await add_history(u["tg_id"], "hilo", "lose", f"Карта {c} → {nc}", -g["bet"])
        return {"ok": True, "lose": True, "card": nc, "balance": u["balance"]}
    g["mult"] = round(min(GAME_MAX_X, g["mult"] * 0.95 / p), 2)
    g["card"] = nc
    g["n"] += 1
    return {"ok": True, "lose": False, "card": nc, "mult": g["mult"], "win": cap_win(g["bet"] * g["mult"]), "balance": u["balance"], **_hilo_info(nc)}

@app.post("/api/hilo/cashout")
async def hilo_cashout(body: DoorsActionRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    g = HILO_GAMES.get(body.game_id)
    if not g or g["tg_id"] != u["tg_id"] or g["n"] < 1:
        raise HTTPException(400, "Сначала угадай хотя бы одну карту")
    HILO_GAMES.pop(body.game_id, None)
    win = cap_win(g["bet"] * g["mult"])
    u["balance"] = int(u["balance"]) + win
    u["wins"] = int(u.get("wins") or 0) + 1
    await save_user(u)
    await add_history(u["tg_id"], "hilo", "win", f"x{g['mult']}", win)
    return {"ok": True, "win": win, "mult": g["mult"], "balance": u["balance"]}


# ===== ЕЖЕДНЕВНАЯ СЕРИЯ (Daily Streak) =====
STREAK_REWARDS = [11, 22, 33, 55, 88, 132, 330]

async def _streak_state(db, tg_id: int):
    await db.execute("CREATE TABLE IF NOT EXISTS streaks (tg_id BIGINT PRIMARY KEY, last_day INTEGER, streak INTEGER)")
    row = await (await db.execute("SELECT last_day, streak FROM streaks WHERE tg_id=?", (tg_id,))).fetchone()
    today = int(time.time() // 86400)
    last, st = (int(row[0] or 0), int(row[1] or 0)) if row else (0, 0)
    if last and last < today - 1:
        st = 0
    return today, last, st

@app.get("/api/streak")
async def streak_info(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    async with get_db() as db:
        today, last, st = await _streak_state(db, int(u["tg_id"]))
        await db.commit()
    can = last != today
    return {"ok": True, "streak": st % 7 if can else (st - 1) % 7 + 1 if st else 0, "day": st % 7, "can_claim": can, "rewards": STREAK_REWARDS, "total": st}

@app.post("/api/streak/claim")
async def streak_claim(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    tg = int(u["tg_id"])
    async with get_db() as db:
        today, last, st = await _streak_state(db, tg)
        if last == today:
            raise HTTPException(400, "Сегодня награда уже получена")
        reward = STREAK_REWARDS[st % 7]
        new_st = st + 1
        if last:
            await db.execute("UPDATE streaks SET last_day=?, streak=? WHERE tg_id=?", (today, new_st, tg))
        else:
            await db.execute("INSERT INTO streaks (tg_id, last_day, streak) VALUES (?,?,?)", (tg, today, new_st))
        await db.execute("UPDATE users SET balance=COALESCE(balance,0)+? WHERE tg_id=?", (reward, tg))
        await db.commit()
        br = await (await db.execute("SELECT balance FROM users WHERE tg_id=?", (tg,))).fetchone()
    await add_history(tg, "streak", "win", f"Серия день {new_st}", reward)
    return {"ok": True, "reward": reward, "day": (new_st - 1) % 7, "total": new_st, "balance": int(br[0] or 0) if br else 0}


# ===== DICE =====
class DiceReq(BaseModel):
    bet: int = 55
    chance: float = 50.0
    over: bool = False

@app.post("/api/dice/roll")
async def dice_roll(body: DiceReq, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    chance = round(max(95.0 / GAME_MAX_X, min(95.0, float(body.chance or 50.0))), 2)
    bet = int(body.bet or 0)
    if bet < max(11, int(round(0.1 * float(STARS_PER_TON)))):
        raise HTTPException(400, "Мин. ставка 0.1 TON")
    bet = min(bet, int(round(500.0 * float(STARS_PER_TON))))
    if int(u.get("balance") or 0) < bet:
        raise HTTPException(400, "Недостаточно средств")
    roll = random.randint(0, 9999) / 100.0
    win = (roll > 100.0 - chance) if body.over else (roll < chance)
    mult = round(95.0 / chance, 2)
    payout = cap_win(bet * mult) if win else 0
    u["balance"] = int(u["balance"]) - bet + payout
    u["games"] = int(u.get("games") or 0) + 1
    if win:
        u["wins"] = int(u.get("wins") or 0) + 1
    await save_user(u)
    await hw_track(u["tg_id"], "wager", bet)
    await add_history(u["tg_id"], "dice", "win" if win else "lose", f"{'>' if body.over else '<'} {chance}% -> {roll}", payout - bet if win else -bet)
    return {"ok": True, "roll": roll, "win": win, "mult": mult, "payout": payout, "balance": u["balance"]}


# ===== ПАРЫ (Склеп-пары) =====
PAIRS_GAMES: Dict[str, dict] = {}
PAIRS_MULT = {2: 3.85, 3: 1.5, 4: 0.85, 5: 0.5}
PAIRS_SYMS = ["pumpkin", "hbomb", "gift", "sbomb", "witch", "santa", "green", "red"]

class PairsStartReq(BaseModel):
    bet: int = 55

class PairsFlipReq(BaseModel):
    game_id: str
    idx: int = 0

@app.post("/api/pairs/start")
async def pairs_start(body: PairsStartReq, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    bet = int(body.bet or 0)
    if bet < max(11, int(round(0.1 * float(STARS_PER_TON)))):
        raise HTTPException(400, "Мин. ставка 0.1 TON")
    bet = min(bet, int(round(500.0 * float(STARS_PER_TON))))
    if int(u.get("balance") or 0) < bet:
        raise HTTPException(400, "Недостаточно средств")
    u["balance"] = int(u["balance"]) - bet
    u["games"] = int(u.get("games") or 0) + 1
    await save_user(u)
    await hw_track(u["tg_id"], "wager", bet)
    syms = random.sample(PAIRS_SYMS, 6)
    layout = syms * 2
    random.shuffle(layout)
    gid = uuid.uuid4().hex
    PAIRS_GAMES[gid] = {"tg_id": u["tg_id"], "bet": bet, "layout": layout, "flipped": []}
    return {"ok": True, "game_id": gid, "balance": u["balance"], "mult": PAIRS_MULT}

@app.post("/api/pairs/flip")
async def pairs_flip(body: PairsFlipReq, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    g = PAIRS_GAMES.get(body.game_id)
    if not g or g["tg_id"] != u["tg_id"]:
        raise HTTPException(400, "Нет активной игры")
    i = int(body.idx)
    if i < 0 or i >= 12 or i in g["flipped"]:
        raise HTTPException(400, "Эта карта уже открыта")
    g["flipped"].append(i)
    sym = g["layout"][i]
    seen = [g["layout"][j] for j in g["flipped"][:-1]]
    k = len(g["flipped"])
    if sym in seen:
        PAIRS_GAMES.pop(body.game_id, None)
        mult = PAIRS_MULT[k]
        payout = int(round(g["bet"] * mult))
        u["balance"] = int(u["balance"]) + payout
        if payout > g["bet"]:
            u["wins"] = int(u.get("wins") or 0) + 1
        await save_user(u)
        await add_history(u["tg_id"], "pairs", "win" if payout >= g["bet"] else "lose", f"Пара на {k}-м открытии x{mult}", payout - g["bet"])
        return {"ok": True, "symbol": sym, "done": True, "win": True, "mult": mult, "payout": payout, "layout": g["layout"], "balance": u["balance"]}
    if k >= 5:
        PAIRS_GAMES.pop(body.game_id, None)
        await add_history(u["tg_id"], "pairs", "lose", "Пары не найдены", -g["bet"])
        return {"ok": True, "symbol": sym, "done": True, "win": False, "mult": 0, "payout": 0, "layout": g["layout"], "balance": u["balance"]}
    return {"ok": True, "symbol": sym, "done": False, "left": 5 - k, "balance": u["balance"]}


# ===== МЕГА-АПГРЕЙД (до x950) + Lucky drop =====
class MegaReq(BaseModel):
    bet: int = 55
    mult: float = 10.0

@app.post("/api/mega/roll")
async def mega_roll(body: MegaReq, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    mult = round(max(1.5, min(GAME_MAX_X, float(body.mult or 10.0))), 2)
    bet = int(body.bet or 0)
    if bet < max(11, int(round(0.1 * float(STARS_PER_TON)))):
        raise HTTPException(400, "Мин. ставка 0.1 TON")
    bet = min(bet, int(round(100.0 * float(STARS_PER_TON))))
    if int(u.get("balance") or 0) < bet:
        raise HTTPException(400, "Недостаточно средств")
    chance = round(93.0 / mult, 4)
    roll = round(random.random() * 100.0, 4)
    win = roll < chance
    lucky = bool(win and not _bonus_off(u["tg_id"], "mega") and random.random() < 0.02)
    payout = cap_win(bet * mult * (2 if lucky else 1)) if win else 0
    u["balance"] = int(u["balance"]) - bet + payout
    u["games"] = int(u.get("games") or 0) + 1
    if win:
        u["wins"] = int(u.get("wins") or 0) + 1
    await save_user(u)
    await hw_track(u["tg_id"], "wager", bet)
    await add_history(u["tg_id"], "mega", "win" if win else "lose", f"x{mult} ({chance}%){' LUCKY x2' if lucky else ''}", payout - bet if win else -bet)
    return {"ok": True, "roll": roll, "chance": chance, "win": win, "lucky": lucky, "mult": mult, "payout": payout, "balance": u["balance"]}



# =====================================================================
#  WIN GIFTS v3 PACK — Plinko · Tower · Колесо Фортуны · Ранги · Lucky Drop
#  Всё в ⭐ (фронт показывает в TON). RTP каждого режима ≈ 96%.
# =====================================================================
V3_MIN_BET = max(11, int(round(0.1 * float(STARS_PER_TON))))      # 0.1 TON
V3_MAX_BET = int(round(200.0 * float(STARS_PER_TON)))             # 200 TON

V3_DDL = ("CREATE TABLE IF NOT EXISTS v3_state (tg_id BIGINT PRIMARY KEY, wager BIGINT DEFAULT 0, "
          "rank_claimed TEXT DEFAULT '[]', spin_at BIGINT DEFAULT 0, best_mult DOUBLE PRECISION DEFAULT 0)")
_V3_READY = {"ok": False}

async def _v3_ensure():
    if _V3_READY["ok"]:
        return
    async with get_db() as db:
        await db.execute(V3_DDL)
        await db.commit()
    _V3_READY["ok"] = True

async def _v3_load(tg_id: int) -> dict:
    await _v3_ensure()
    async with get_db() as db:
        row = await (await db.execute(
            "SELECT wager, rank_claimed, spin_at, best_mult FROM v3_state WHERE tg_id=?", (int(tg_id),)
        )).fetchone()
        if not row:
            await db.execute(
                "INSERT INTO v3_state(tg_id,wager,rank_claimed,spin_at,best_mult) VALUES(?,0,'[]',0,0) ON CONFLICT DO NOTHING",
                (int(tg_id),),
            )
            await db.commit()
            return {"wager": 0, "rank_claimed": [], "spin_at": 0, "best_mult": 0.0}
    try:
        rc = json.loads(row[1] or "[]")
    except Exception:
        rc = []
    return {"wager": int(row[0] or 0), "rank_claimed": rc, "spin_at": int(row[2] or 0), "best_mult": float(row[3] or 0)}

async def _v3_save(tg_id: int, st: dict):
    async with get_db() as db:
        await db.execute(
            "UPDATE v3_state SET wager=?, rank_claimed=?, spin_at=?, best_mult=? WHERE tg_id=?",
            (int(st["wager"]), json.dumps(st["rank_claimed"]), int(st["spin_at"]), float(st["best_mult"]), int(tg_id)),
        )
        await db.commit()

async def v3_add_wager(tg_id: int, stars: int, mult: float = 0.0):
    try:
        st = await _v3_load(tg_id)
        st["wager"] = int(st["wager"]) + max(0, int(stars or 0))
        if mult and mult > st["best_mult"]:
            st["best_mult"] = float(mult)
        await _v3_save(tg_id, st)
    except Exception as e:
        print("[v3 wager]", e)

# Оборот из старых режимов (кейсы, апгрейд, мины, двери…) тоже идёт в ранг
_hw_track_orig = hw_track
async def hw_track(tg_id: int, kind: str, amount: float = 0):  # noqa: F811
    await _hw_track_orig(tg_id, kind, amount)
    if kind == "wager":
        await v3_add_wager(tg_id, int(amount or 0))

# ----- ранги: (id, название, иконка, нужно TON оборота, награда ⭐, бонус к daily spin) -----
V3_RANKS = [
    ("rookie",   "Новичок",  "🥚", 0,    0,    1.0),
    ("bronze",   "Бронза",   "🥉", 5,    5,    1.1),
    ("silver",   "Серебро",  "🥈", 25,   15,   1.2),
    ("gold",     "Золото",   "🥇", 100,  50,   1.3),
    ("platinum", "Платина",  "💠", 400,  150,  1.4),
    ("diamond",  "Алмаз",    "💎", 1500, 500,  1.5),
    ("legend",   "Легенда",  "👑", 5000, 1500, 1.6),
]

def _v3_rank_view(st: dict) -> dict:
    spt = float(STARS_PER_TON)
    wager_ton = float(st["wager"]) / spt
    idx = 0
    for i, r in enumerate(V3_RANKS):
        if wager_ton >= r[3]:
            idx = i
    cur = V3_RANKS[idx]
    nxt = V3_RANKS[idx + 1] if idx + 1 < len(V3_RANKS) else None
    if nxt:
        span = max(0.0001, nxt[3] - cur[3])
        progress = max(0.0, min(1.0, (wager_ton - cur[3]) / span))
    else:
        progress = 1.0
    ranks = []
    for i, r in enumerate(V3_RANKS):
        ranks.append({
            "id": r[0], "name": r[1], "icon": r[2], "need_ton": r[3], "reward": r[4],
            "spin_mult": r[5], "reached": i <= idx,
            "claimed": (r[0] in st["rank_claimed"]) or r[4] == 0,
        })
    left = 0
    if st.get("spin_at"):
        left = max(0, 86400 - (now_ts() - int(st["spin_at"])))
    return {
        "wager_ton": round(wager_ton, 2), "rank_index": idx,
        "rank": {"id": cur[0], "name": cur[1], "icon": cur[2], "spin_mult": cur[5]},
        "next": ({"id": nxt[0], "name": nxt[1], "icon": nxt[2], "need_ton": nxt[3]} if nxt else None),
        "progress": round(progress, 4), "ranks": ranks,
        "spin_ready": left == 0, "spin_left": left,
        "best_mult": round(float(st.get("best_mult") or 0), 2),
    }

def _v3_take_bet(u: dict, bet) -> int:
    try:
        bet = int(round(float(bet or 0)))
    except Exception:
        bet = 0
    if bet < V3_MIN_BET:
        raise HTTPException(400, f"Мин. ставка 0.1 TON (~{V3_MIN_BET}⭐)")
    bet = min(bet, V3_MAX_BET)
    if int(u.get("balance") or 0) < bet:
        raise HTTPException(400, "Недостаточно средств")
    u["balance"] = int(u["balance"]) - bet
    u["games"] = int(u.get("games") or 0) + 1
    return bet

async def _v3_finish(u: dict, game: str, bet: int, payout: int, mult: float, detail: str):
    if payout > 0:
        u["balance"] = int(u["balance"]) + int(payout)
    if payout > bet:
        u["wins"] = int(u.get("wins") or 0) + 1
    await save_user(u)
    try:
        await add_history(u["tg_id"], game, "win" if payout >= bet else "lose", detail, int(payout) - int(bet))
    except Exception as e:
        print("[v3 history]", e)
    try:
        await _hw_track_orig(u["tg_id"], "wager", bet)
    except Exception:
        pass
    await v3_add_wager(u["tg_id"], bet, mult if payout > 0 else 0)
    try:
        await bump_weekly(u["tg_id"], u.get("username") or "", max(1, bet // 100))
    except Exception:
        pass

@app.get("/api/v3/state")
async def v3_state(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    st = await _v3_load(u["tg_id"])
    return {"ok": True, **_v3_rank_view(st), "balance": u["balance"],
            "plinko": PLINKO_TABLES, "wheel": WHEEL_SEGMENTS,
            "tower": {k: {"cols": v["cols"], "bad": v["bad"], "mults": _tower_mults(k)} for k, v in TOWER_DIFF.items()}}

class V3RankClaim(BaseModel):
    rank_id: str

@app.post("/api/v3/rank/claim")
async def v3_rank_claim(body: V3RankClaim, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    st = await _v3_load(u["tg_id"])
    view = _v3_rank_view(st)
    r = next((x for x in view["ranks"] if x["id"] == body.rank_id), None)
    if not r:
        raise HTTPException(400, "Ранг не найден")
    if not r["reached"]:
        raise HTTPException(400, "Ранг ещё не достигнут")
    if r["claimed"]:
        raise HTTPException(400, "Награда уже получена")
    st["rank_claimed"].append(r["id"])
    await _v3_save(u["tg_id"], st)
    u["balance"] = int(u["balance"]) + int(r["reward"])
    await save_user(u)
    await add_history(u["tg_id"], "bonus", "win", f"Ранг {r['icon']} {r['name']}", int(r["reward"]))
    return {"ok": True, "reward": r["reward"], "balance": u["balance"], **_v3_rank_view(st)}

# ----- PLINKO: 12 рядов, 13 лунок, RTP ≈ 96.4% -----
PLINKO_ROWS = 12
PLINKO_TABLES = {
    "low":  [9, 3, 1.6, 1.4, 1.0, 1.0, 0.5, 1.0, 1.0, 1.4, 1.6, 3, 9],
    "mid":  [26, 9, 4, 2.1, 1.0, 0.6, 0.3, 0.6, 1.0, 2.1, 4, 9, 26],        # RTP ≈96%, до ×26
    "high": [50, 16, 7, 2.6, 0.8, 0.25, 0.2, 0.25, 0.8, 2.6, 7, 16, 50],     # RTP ≈96%, до ×50
}

PLINKO_RTP = float(os.getenv("PLINKO_RTP") or 0.92)

def _plinko_rescale():
    """Подгоняем таблицы множителей под PLINKO_RTP. Клиент берёт таблицы с сервера, показанные иксы = реальные."""
    from math import comb
    for _risk, _tb in list(PLINKO_TABLES.items()):
        _rtp = sum(comb(PLINKO_ROWS, k) * float(_tb[k]) for k in range(PLINKO_ROWS + 1)) / (2 ** PLINKO_ROWS)
        _f = PLINKO_RTP / _rtp
        PLINKO_TABLES[_risk] = [round(float(x) * _f, 2) for x in _tb]
_plinko_rescale()

class PlinkoReq(BaseModel):
    bet: int = 55
    risk: str = "mid"

@app.post("/api/plinko/drop")
async def plinko_drop(body: PlinkoReq, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    risk = body.risk if body.risk in PLINKO_TABLES else "mid"
    bet = _v3_take_bet(u, body.bet)
    path = [1 if random.random() < 0.5 else 0 for _ in range(PLINKO_ROWS)]
    slot = sum(path)
    mult = float(PLINKO_TABLES[risk][slot])
    payout = cap_win(bet * mult)
    await _v3_finish(u, "plinko", bet, payout, mult, f"Plinko {risk} ×{mult}")
    return {"ok": True, "path": path, "slot": slot, "mult": mult, "payout": payout, "bet": bet,
            "risk": risk, "balance": u["balance"]}

# ----- TOWER: 8 этажей -----
TOWER_FLOORS = 8
TOWER_RTP = float(os.getenv("TOWER_RTP") or 0.92)
TOWER_DIFF = {
    # иксы ограничены: меньше этажей на сложных уровнях, чтобы не было ×245 и ×1000
    # плавный рост: на этаже больше плиток и меньше ловушек → икс растёт понемногу
    "easy":   {"cols": 5, "bad": 1, "floors": 10},  # ×1.2 → ×8.9
    "medium": {"cols": 4, "bad": 1, "floors": 9},   # ×1.28 → ×12.8
    "hard":   {"cols": 3, "bad": 1, "floors": 8},   # ×1.44 → ×24.6
    "insane": {"cols": 2, "bad": 1, "floors": 5},   # ×1.92 → ×30.7
}
TOWER_MAX_WIN = int(float(os.getenv("TOWER_MAX_WIN_TON") or 200) * float(STARS_PER_TON))   # потолок выигрыша за одну башню
TOWER_GAMES: Dict[str, dict] = {}

def _tower_mults(diff: str) -> list:
    d = TOWER_DIFF[diff]
    p = (d["cols"] - d["bad"]) / d["cols"]
    # кап ×1000 — защита кассы от жирного Insane-захода
    return [min(GAME_MAX_X, math.floor(TOWER_RTP / (p ** n) * 100) / 100) for n in range(1, int(d.get("floors") or TOWER_FLOORS) + 1)]

class TowerStartReq(BaseModel):
    bet: int = 55
    difficulty: str = "medium"

class TowerPickReq(BaseModel):
    game_id: str
    col: int

class TowerIdReq(BaseModel):
    game_id: str

@app.post("/api/tower/start")
async def tower_start(body: TowerStartReq, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    for gid, g in list(TOWER_GAMES.items()):
        if g["tg_id"] == u["tg_id"]:
            raise HTTPException(400, "Сначала закончи текущую башню")
    diff = body.difficulty if body.difficulty in TOWER_DIFF else "medium"
    bet = _v3_take_bet(u, body.bet)
    await save_user(u)
    d = TOWER_DIFF[diff]
    layout = [random.sample(range(d["cols"]), d["bad"]) for _ in range(int(d["floors"]))]
    gid = uuid.uuid4().hex
    TOWER_GAMES[gid] = {"tg_id": u["tg_id"], "bet": bet, "diff": diff, "layout": layout, "floor": 0, "picks": [], "ts": now_ts()}
    return {"ok": True, "game_id": gid, "bet": bet, "difficulty": diff, "cols": d["cols"], "floors": int(d["floors"]),
            "mults": _tower_mults(diff), "balance": u["balance"]}

@app.get("/api/tower/active")
async def tower_active(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    for gid, g in TOWER_GAMES.items():
        if g["tg_id"] == u["tg_id"]:
            d = TOWER_DIFF[g["diff"]]
            return {"active": True, "game_id": gid, "bet": g["bet"], "difficulty": g["diff"], "cols": d["cols"],
                    "floors": int(d["floors"]), "floor": g["floor"], "picks": g["picks"], "mults": _tower_mults(g["diff"])}
    return {"active": False}

@app.post("/api/tower/pick")
async def tower_pick(body: TowerPickReq, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    g = TOWER_GAMES.get(body.game_id)
    if not g or g["tg_id"] != u["tg_id"]:
        raise HTTPException(400, "Нет активной башни")
    d = TOWER_DIFF[g["diff"]]
    col = int(body.col)
    if col < 0 or col >= d["cols"]:
        raise HTTPException(400, "Нет такой плитки")
    fl = g["floor"]
    mults = _tower_mults(g["diff"])
    if col in g["layout"][fl]:
        TOWER_GAMES.pop(body.game_id, None)
        await _v3_finish(u, "tower", g["bet"], 0, 0, f"Tower {g['diff']} · упал на {fl + 1} этаже")
        return {"ok": True, "boom": True, "floor": fl, "col": col, "layout": g["layout"], "balance": u["balance"]}
    g["picks"].append(col)
    g["floor"] = fl + 1
    mult = mults[fl]
    if g["floor"] >= int(TOWER_DIFF[g["diff"]]["floors"]):
        TOWER_GAMES.pop(body.game_id, None)
        payout = min(TOWER_MAX_WIN, int(round(g["bet"] * mult)))
        await _v3_finish(u, "tower", g["bet"], payout, mult, f"Tower {g['diff']} · вершина ×{mult}")
        return {"ok": True, "boom": False, "top": True, "floor": g["floor"], "mult": mult, "win": payout,
                "layout": g["layout"], "balance": u["balance"]}
    return {"ok": True, "boom": False, "floor": g["floor"], "mult": mult, "win": min(TOWER_MAX_WIN, int(round(g["bet"] * mult))),
            "next_mult": mults[g["floor"]], "balance": u["balance"]}

@app.post("/api/tower/cashout")
async def tower_cashout(body: TowerIdReq, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    g = TOWER_GAMES.get(body.game_id)
    if not g or g["tg_id"] != u["tg_id"]:
        raise HTTPException(400, "Нет активной башни")
    if g["floor"] < 1:
        raise HTTPException(400, "Поднимись хотя бы на 1 этаж")
    TOWER_GAMES.pop(body.game_id, None)
    mult = _tower_mults(g["diff"])[g["floor"] - 1]
    payout = min(TOWER_MAX_WIN, int(round(g["bet"] * mult)))
    await _v3_finish(u, "tower", g["bet"], payout, mult, f"Tower {g['diff']} · забрал ×{mult}")
    return {"ok": True, "win": payout, "mult": mult, "layout": g["layout"], "balance": u["balance"]}

# ----- КОЛЕСО ФОРТУНЫ: 25 секторов, ставка на цвет; каждый цвет RTP 96% (12×2, 8×3, 4×6, 1×24 из 25) -----
# x2 — 12 секторов, x3 — 8, x6 — 4, x24 — 1
WHEEL_SEGMENTS = [2, 3, 2, 6, 2, 3, 2, 3, 2, 6, 2, 3, 24, 3, 2, 6, 2, 3, 2, 3, 2, 6, 2, 3, 2]

class WheelReq(BaseModel):
    bet: int = 55
    color: int = 2

@app.post("/api/wheel/spin")
async def wheel_spin(body: WheelReq, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    color = int(body.color or 2)
    if color not in (2, 3, 6, 24):
        raise HTTPException(400, "Выбери x2, x3, x6 или x24")
    bet = _v3_take_bet(u, body.bet)
    idx = random.randrange(len(WHEEL_SEGMENTS))
    landed = WHEEL_SEGMENTS[idx]
    win = landed == color
    payout = cap_win(bet * color) if win else 0
    await _v3_finish(u, "wheel", bet, payout, color if win else 0, f"Колесо: ставка x{color}, выпало x{landed}")
    return {"ok": True, "index": idx, "landed": landed, "color": color, "win": win, "payout": payout,
            "bet": bet, "balance": u["balance"]}



# ----- СОЦИАЛКА: лента с аватарками, карточка игрока, статистика истории -----
def _v3_rank_idx(wager_stars: int) -> int:
    w = float(wager_stars or 0) / float(STARS_PER_TON)
    idx = 0
    for i, r in enumerate(V3_RANKS):
        if w >= r[3]:
            idx = i
    return idx

@app.get("/api/v3/ranks")
async def v3_ranks(ids: str = ""):
    """Ранги для рамок аватарок (топ, лента). ids=1,2,3"""
    try:
        lst = [int(x) for x in str(ids).split(",") if x.strip().isdigit()][:60]
    except Exception:
        lst = []
    out = {}
    if lst:
        await _v3_ensure()
        async with get_db() as db:
            for tid in lst:
                row = await (await db.execute("SELECT wager FROM v3_state WHERE tg_id=?", (tid,))).fetchone()
                out[str(tid)] = _v3_rank_idx(int(row[0] or 0) if row else 0)
    return {"ranks": out}

async def _showcase_get(tg_id: int, inv: list = None) -> list:
    await _v3_ensure()
    try:
        async with get_db() as db:
            try:
                await db.execute("ALTER TABLE v3_state ADD COLUMN " + ("IF NOT EXISTS " if USE_POSTGRES else "") + "showcase TEXT DEFAULT '[]'")
                await db.commit()
            except Exception:
                pass
            row = await (await db.execute("SELECT showcase FROM v3_state WHERE tg_id=?", (int(tg_id),))).fetchone()
        sc = json.loads(row[0] or "[]") if row and row[0] else []
    except Exception:
        sc = []
    if not sc and inv:
        top = sorted([x for x in inv if is_real_gift(x.get("name") or "")], key=lambda x: -int(x.get("value") or 0))[:6]
        sc = [{"name": x.get("name"), "value": int(x.get("value") or 0), "img": x.get("img") or gift_img_url(x.get("name") or "")} for x in top]
    return sc[:6]

class ShowcaseReq(BaseModel):
    ids: List[str] = []
    names: List[str] = []

@app.post("/api/v3/showcase")
async def v3_showcase_set(body: ShowcaseReq, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    inv = list(u.get("inventory") or [])
    picked, used = [], set()
    for key in (body.ids or [])[:6]:
        for i, x in enumerate(inv):
            if i not in used and str(x.get("id") or "") == str(key):
                used.add(i); picked.append(x); break
    for nm in (body.names or []):
        if len(picked) >= 6:
            break
        for i, x in enumerate(inv):
            if i not in used and x.get("name") == nm:
                used.add(i); picked.append(x); break
    sc = [{"name": x.get("name"), "value": int(x.get("value") or 0), "img": x.get("img") or gift_img_url(x.get("name") or "")} for x in picked[:6]]
    await _showcase_get(u["tg_id"])
    await _v3_load(u["tg_id"])
    async with get_db() as db:
        await db.execute("UPDATE v3_state SET showcase=? WHERE tg_id=?", (json.dumps(sc, ensure_ascii=False), int(u["tg_id"])))
        await db.commit()
    return {"ok": True, "showcase": sc}

@app.get("/api/v3/showcase")
async def v3_showcase_me(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    return {"ok": True, "showcase": await _showcase_get(u["tg_id"], u.get("inventory") or [])}

@app.get("/api/v3/live")
async def v3_live(min_ton: float = 0):
    lim = int(max(0.0, float(min_ton or 0)) * float(STARS_PER_TON))
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT d.name, d.emoji, d.img, d.user_name, d.value, d.created_at, u.tg_id, COALESCE(u.photo_url,''), COALESCE(v.wager,0) "
            "FROM live_drops d LEFT JOIN users u ON u.username = d.user_name LEFT JOIN v3_state v ON v.tg_id = u.tg_id "
            "WHERE d.value > ? ORDER BY d.id DESC LIMIT 30", (lim,)
        )).fetchall()
    out = []
    for r in (rows or []):
        out.append({"name": r[0], "emoji": r[1] or "🎁", "img": r[2] or gift_img_url(r[0] or ""), "user": r[3] or "?",
                    "value": int(r[4] or 0), "ts": r[5], "uid": int(r[6] or 0), "photo": r[7] or "", "rk": _v3_rank_idx(int(r[8] or 0))})
    return {"items": out}

@app.get("/api/v3/player")
async def v3_player(name: str = "", uid: int = 0):
    async with get_db() as db:
        if uid:
            u = await (await db.execute("SELECT tg_id, username, wins, cases_opened, COALESCE(photo_url,''), games FROM users WHERE tg_id=?", (int(uid),))).fetchone()
        else:
            u = await (await db.execute("SELECT tg_id, username, wins, cases_opened, COALESCE(photo_url,''), games FROM users WHERE username=? LIMIT 1", (str(name)[:64],))).fetchone()
        if not u:
            raise HTTPException(404, "Игрок не найден")
        best = await (await db.execute(
            "SELECT name, emoji, img, value, created_at FROM live_drops WHERE user_name=? ORDER BY value DESC LIMIT 1", (u[1],)
        )).fetchone()
        last = await (await db.execute(
            "SELECT name, emoji, img, value FROM live_drops WHERE user_name=? ORDER BY id DESC LIMIT 6", (u[1],)
        )).fetchall()
    rank = None
    try:
        st = await _v3_load(int(u[0]))
        rv = _v3_rank_view(st)
        rank = {"name": rv["rank"]["name"], "icon": rv["rank"]["icon"], "idx": rv["rank_index"]}
    except Exception:
        pass
    showcase = []
    try:
        async with get_db() as db:
            irow = await (await db.execute("SELECT inventory FROM users WHERE tg_id=?", (int(u[0]),))).fetchone()
        inv = (json.loads(irow[0] or "[]") if isinstance(irow[0], str) else (irow[0] or [])) if irow else []
        showcase = await _showcase_get(int(u[0]), inv)
    except Exception as _e:
        print("[showcase]", _e)
    g = lambda r: {"name": r[0], "emoji": r[1] or "🎁", "img": r[2] or gift_img_url(r[0] or ""), "value": int(r[3] or 0)}
    return {"ok": True, "user": u[1], "photo": u[4], "wins": int(u[2] or 0), "cases": int(u[3] or 0), "games": int(u[5] or 0),
            "rank": rank, "best": g(best) if best else None, "last": [g(r) for r in (last or [])], "showcase": showcase}

@app.get("/api/v3/history")
async def v3_history(game: str = "", authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    since = now_ts() - 7 * 86400
    q = "SELECT game, result, detail, amount, created_at FROM history WHERE tg_id=?"
    args = [u["tg_id"]]
    if game:
        q += " AND game=?"; args.append(str(game)[:24])
    async with get_db() as db:
        rows = await (await db.execute(q + " ORDER BY id DESC LIMIT 200", tuple(args))).fetchall()
        week = await (await db.execute(
            "SELECT game, amount, created_at FROM history WHERE tg_id=? AND created_at>=?", (u["tg_id"], since)
        )).fetchall()
    days = [0] * 7
    games: Dict[str, int] = {}
    t0 = now_ts()
    for g_, amt, ts in (week or []):
        d = int((t0 - int(ts or 0)) // 86400)
        if 0 <= d < 7 and (not game or g_ == game):
            days[6 - d] += int(amt or 0)
        games[g_] = games.get(g_, 0) + 1
    return {"history": [{"game": r[0], "result": r[1], "detail": r[2], "amount": int(r[3] or 0), "ts": r[4]} for r in (rows or [])],
            "days": days, "games": games}


# ----- ВЫВОД ПОДАРКОВ -----
class GiftWithdrawRequest(BaseModel):
    item_index: int
    item_id: Optional[str] = None

@app.post("/api/withdraw/gift")
async def withdraw_gift(body: GiftWithdrawRequest, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    if int(u["tg_id"]) in WBANNED and not u.get("is_admin"):
        raise HTTPException(403, "Вывод отключён для аккаунта. Если это ошибка — напиши @" + SUPPORT_USERNAME)
    inv = u.get("inventory") or []
    idx = int(body.item_index)
    if body.item_id:
        hit = [i for i, x in enumerate(inv) if str(x.get("id") or "") == str(body.item_id)]
        if hit:
            idx = hit[0]
    if idx < 0 or idx >= len(inv):
        raise HTTPException(400, "Подарок не найден")
    item = inv[idx]
    if item.get("rental") or item.get("playable") is False:
        raise HTTPException(400, "Аренду вывести нельзя")
    if int(u.get("deposited") or 0) <= 0 and not u.get("is_admin"):
        raise HTTPException(400, "Вывод открывается после первого пополнения — бонусы и бесплатные дропы так не вывести")
    if item.get("is_ton") or not is_real_gift(item.get("name") or ""):
        raise HTTPException(400, "Этот предмет нельзя вывести как подарок — продай его")
    if not (u.get("username") or "").strip():
        raise HTTPException(400, "Поставь @username в Telegram — по нему админ отправит подарок")
    hold_until = 0
    try:
        async with get_db() as db:
            row = await (await db.execute("SELECT withdraw_hold_until FROM users WHERE tg_id=?", (u["tg_id"],))).fetchone()
            hold_until = int(row[0] or 0) if row else 0
    except Exception:
        hold_until = 0
    if hold_until > now_ts():
        raise HTTPException(400, f"Холд вывода после крупного выигрыша · ещё ~{max(1, (hold_until - now_ts()) // 3600)}ч")
    fb_left = await first_bonus_left(u["tg_id"])
    if fb_left > 0 and not u.get("is_admin"):
        raise HTTPException(400, f"Сначала отыграй бонус к первому пополнению: осталось {round(fb_left / float(STARS_PER_TON), 2)} TON оборота в любых играх")
    left = await deposit_hold_left(u["tg_id"])
    if left > 0:
        h, m = left // 3600, (left % 3600) // 60
        raise HTTPException(400, f"Вывод откроется через {h}ч {m}м после пополнения. Играть и продавать можно уже сейчас")
    inv.pop(idx)
    u["inventory"] = inv
    await save_user(u)
    note = json.dumps({"item": item}, ensure_ascii=False)
    async with get_db() as db:
        await db.execute(
            "INSERT INTO withdrawals(tg_id,amount,method,dest,note,status,created_at) VALUES(?,?,?,?,?,?,?)",
            (u["tg_id"], int(item.get("value") or 0), "gift", "@" + str(u.get("username") or ""), note, "pending", ts_param(now_ts())),
        )
        await db.commit()
    try:
        await ledger("withdraw_req", int(item.get("value") or 0), u["tg_id"], "gift")
        if ADMIN_TG_ID:
            await tg_notify(ADMIN_TG_ID, f"🎁 Вывод подарка\nuser <code>{u['tg_id']}</code> @{u.get('username') or ''}\n<b>{item.get('name')}</b> · {int(item.get('value') or 0)}⭐")
    except Exception:
        pass
    await add_history(u["tg_id"], "withdraw", "lose", f"Вывод {item.get('name')}", 0)
    return {"ok": True, "inventory": u["inventory"], "balance": u["balance"], "admin": SUPPORT_USERNAME,
            "message": f"Заявка создана. @{SUPPORT_USERNAME} отправит {item.get('name')} тебе в Telegram"}


# =====================================================================
#  ДЕПОЗИТЫ v2: TON по уникальному комментарию · холды вывода
# =====================================================================
_DEP_READY = {"ok": False}
async def _dep_ensure():
    if _DEP_READY["ok"]:
        return
    async with get_db() as db:
        await db.execute("CREATE TABLE IF NOT EXISTS dep_holds (tg_id BIGINT PRIMARY KEY, stars_at BIGINT DEFAULT 0, ton_at BIGINT DEFAULT 0)")
        await db.execute("CREATE TABLE IF NOT EXISTS ton_invoices (id TEXT PRIMARY KEY, tg_id BIGINT, amount_ton REAL, comment TEXT, credited INTEGER DEFAULT 0, "
                         "tx_hash TEXT DEFAULT '', got_ton REAL DEFAULT 0, created_at BIGINT)")
        await db.commit()
    _DEP_READY["ok"] = True

async def mark_deposit(tg_id: int, kind: str):
    try:
        await _dep_ensure()
        col = "stars_at" if kind == "stars" else "ton_at"
        async with get_db() as db:
            await db.execute("INSERT INTO dep_holds(tg_id,stars_at,ton_at) VALUES(?,0,0) ON CONFLICT DO NOTHING", (int(tg_id),))
            await db.execute(f"UPDATE dep_holds SET {col}=? WHERE tg_id=?", (now_ts(), int(tg_id)))
            await db.commit()
    except Exception as e:
        print("[mark_deposit]", e)

async def deposit_hold_left(tg_id: int) -> int:
    try:
        await _dep_ensure()
        async with get_db() as db:
            row = await (await db.execute("SELECT stars_at, ton_at FROM dep_holds WHERE tg_id=?", (int(tg_id),))).fetchone()
        if not row:
            return 0
        t = now_ts()
        a = int(row[0] or 0) + HOLD_STARS_SEC - t if row[0] else 0
        b = int(row[1] or 0) + HOLD_TON_SEC - t if row[1] else 0
        return max(0, a, b)
    except Exception:
        return 0

def _dep_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "WG-" + "".join(random.choice(alphabet) for _ in range(6))

class TonInvoiceReq(BaseModel):
    amount_ton: float

class TonInvoiceCheck(BaseModel):
    invoice_id: str

@app.post("/api/v3/ton/invoice")
async def v3_ton_invoice(body: TonInvoiceReq, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    if not TON_TREASURY:
        raise HTTPException(503, "Пополнение TON временно недоступно (не задан TON_TREASURY)")
    amt = round(float(body.amount_ton or 0), 2)
    if amt < 0.5:
        raise HTTPException(400, "Минимум 0.5 TON")
    if amt > 5000:
        raise HTTPException(400, "Максимум 5000 TON за раз")
    await _dep_ensure()
    async with get_db() as db:
        # один активный счёт на сумму — переиспользуем, чтобы не плодить
        row = await (await db.execute(
            "SELECT id, comment, amount_ton, created_at FROM ton_invoices WHERE tg_id=? AND credited=0 AND created_at>? ORDER BY created_at DESC LIMIT 1",
            (u["tg_id"], now_ts() - 3 * 3600))).fetchone()
        if row and abs(float(row[2]) - amt) < 1e-9:
            inv_id, code = row[0], row[1]
        else:
            inv_id, code = uuid.uuid4().hex, _dep_code()
            await db.execute("INSERT INTO ton_invoices(id,tg_id,amount_ton,comment,credited,created_at) VALUES(?,?,?,?,0,?)",
                             (inv_id, u["tg_id"], amt, code, now_ts()))
            await db.commit()
    nano = int(round(amt * 1e9))
    return {"ok": True, "invoice_id": inv_id, "address": TON_TREASURY, "amount_ton": amt, "comment": code,
            "stars": int(round(amt * TON_STARS_PER_TON)),
            "ton_link": f"ton://transfer/{TON_TREASURY}?amount={nano}&text={code}",
            "tonkeeper": f"https://app.tonkeeper.com/transfer/{TON_TREASURY}?amount={nano}&text={code}",
            "expires_in": 3 * 3600}

async def v3_try_credit_invoice(inv_id: str, tg_id: int) -> dict:
    await _dep_ensure()
    async with get_db() as db:
        row = await (await db.execute("SELECT amount_ton, comment, credited, created_at FROM ton_invoices WHERE id=? AND tg_id=?", (inv_id, int(tg_id)))).fetchone()
    if not row:
        return {"credited": False, "error": "Счёт не найден"}
    if int(row[2] or 0):
        return {"credited": True, "already": True}
    want, code, created = float(row[0]), str(row[1]).strip().upper(), int(row[3] or 0)
    txs = await _fetch_treasury_in_txs(60)
    for tx in txs:
        cmt = str(tx.get("comment") or "").strip().upper()
        if cmt != code:
            continue
        ut = int(tx.get("utime") or 0)
        if created and ut and ut + 300 < created:
            continue
        h = tx.get("hash") or ""
        got = int(tx.get("value_nano") or 0) / 1e9
        if got <= 0 or not h:
            continue
        async with get_db() as db:
            clash = await (await db.execute("SELECT id FROM ton_invoices WHERE tx_hash=? AND credited=1", (h,))).fetchone()
            if clash:
                continue
            again = await (await db.execute("SELECT credited FROM ton_invoices WHERE id=?", (inv_id,))).fetchone()
            if again and int(again[0] or 0):
                return {"credited": True, "already": True}
            stars = max(1, int(round(got * TON_STARS_PER_TON)))   # зачисляем фактически пришедшее
            await db.execute("UPDATE ton_invoices SET credited=1, tx_hash=?, got_ton=? WHERE id=?", (h, got, inv_id))
            bal = await (await db.execute("SELECT balance, deposited FROM users WHERE tg_id=?", (int(tg_id),))).fetchone()
            if bal:
                await db.execute("UPDATE users SET balance=?, deposited=? WHERE tg_id=?", (int(bal[0] or 0) + stars, int(bal[1] or 0) + stars, int(tg_id)))
            await db.commit()
        await mark_deposit(tg_id, "ton")
        try:
            await pay_referral(int(tg_id), stars)
            await add_history(int(tg_id), "deposit", "win", f"TON {got:g} ({code}) → +{stars}⭐", stars)
            await ledger("deposit_ton", stars, int(tg_id), code)
        except Exception:
            pass
        async with get_db() as db:
            b2 = await (await db.execute("SELECT balance FROM users WHERE tg_id=?", (int(tg_id),))).fetchone()
        return {"credited": True, "stars": stars, "got_ton": got, "short": got + 1e-9 < want * 0.99, "balance": int(b2[0] or 0) if b2 else 0}
    return {"credited": False, "pending": True, "message": "Перевод с комментарием " + code + " пока не найден. Обычно он приходит за 10–60 секунд"}

@app.post("/api/v3/ton/check")
async def v3_ton_check(body: TonInvoiceCheck, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    r = await v3_try_credit_invoice(body.invoice_id, u["tg_id"])
    if not r.get("credited"):
        return {"ok": True, "credited": False, "message": r.get("message") or r.get("error") or "Ожидаем перевод"}
    return {"ok": True, **r}

@app.get("/api/v3/deposit/info")
async def v3_dep_info(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    return {"ok": True, "hold_left": await deposit_hold_left(u["tg_id"]), "hold_stars_h": HOLD_STARS_SEC // 3600,
            "hold_ton_min": HOLD_TON_SEC // 60, "ton_enabled": bool(TON_TREASURY), "rate": TON_STARS_PER_TON}

async def _v3_invoice_loop():
    await asyncio.sleep(25)
    while True:
        try:
            if TON_TREASURY:
                await _dep_ensure()
                async with get_db() as db:
                    rows = await (await db.execute("SELECT id, tg_id FROM ton_invoices WHERE credited=0 AND created_at>?", (now_ts() - 3 * 3600,))).fetchall()
                for r in rows or []:
                    res = await v3_try_credit_invoice(r[0], int(r[1]))
                    if res.get("credited") and not res.get("already"):
                        await tg_push(int(r[1]), f"💎 <b>TON-пополнение зачислено</b>\n+{res.get('stars')}⭐ уже на балансе", "🎰 Играть")
        except Exception as e:
            print("[v3 invoice loop]", e)
        await asyncio.sleep(25)

@app.on_event("startup")
async def _v3_inv_start():
    asyncio.create_task(_v3_invoice_loop())

# =====================================================================
#  АДМИНКА v3
# =====================================================================
async def _adm(authorization, x_telegram_init_data):
    me = await current_user(authorization, x_telegram_init_data)
    require_admin(me)
    return me

@app.get("/api/v3/admin/overview")
async def v3_admin_overview(days: int = 1, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    days = max(1, min(30, int(days or 1)))
    since = now_ts() - days * 86400
    out = {"days": days}
    async with get_db() as db:
        q = lambda sql, a=(): db.execute(sql, a)
        out["users"] = int((await (await q("SELECT COUNT(*) FROM users")).fetchone())[0] or 0)
        out["balances_ton"] = round(int((await (await q("SELECT COALESCE(SUM(balance),0) FROM users")).fetchone())[0] or 0) / float(STARS_PER_TON), 2)
        try:
            out["active"] = int((await (await q("SELECT COUNT(DISTINCT tg_id) FROM history WHERE created_at>=?", (since,))).fetchone())[0] or 0)
        except Exception:
            out["active"] = 0
        try:
            r = await (await q("SELECT COALESCE(SUM(amount),0), COUNT(*) FROM deposits WHERE paid=1 AND created_at>=?", (since,))).fetchone()
            out["dep_stars"] = int(r[0] or 0); out["dep_stars_n"] = int(r[1] or 0)
        except Exception:
            out["dep_stars"] = out["dep_stars_n"] = 0
        try:
            await _dep_ensure()
            r = await (await q("SELECT COALESCE(SUM(got_ton),0), COUNT(*) FROM ton_invoices WHERE credited=1 AND created_at>=?", (since,))).fetchone()
            out["dep_ton"] = round(float(r[0] or 0), 2); out["dep_ton_n"] = int(r[1] or 0)
        except Exception:
            out["dep_ton"] = out["dep_ton_n"] = 0
        try:
            r = await (await q("SELECT COUNT(*), COALESCE(SUM(amount),0) FROM withdrawals WHERE status='pending'")).fetchone()
            out["wd_pending"] = int(r[0] or 0); out["wd_pending_ton"] = round(int(r[1] or 0) / float(STARS_PER_TON), 2)
        except Exception:
            out["wd_pending"] = 0; out["wd_pending_ton"] = 0
        games = {}
        try:
            rows = await (await q("SELECT game, COUNT(*), COALESCE(SUM(amount),0), SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) FROM history WHERE created_at>=? GROUP BY game", (since,))).fetchall()
            for g, n, amt, w in rows or []:
                games[str(g)] = {"n": int(n or 0), "player_net_ton": round(int(amt or 0) / float(STARS_PER_TON), 2), "wins": int(w or 0)}
        except Exception as e:
            print("[adm games]", e)
        out["games"] = games
    return out

@app.get("/api/v3/admin/users")
async def v3_admin_users(q: str = "", authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    qq = str(q or "").strip().lstrip("@")
    async with get_db() as db:
        if qq.isdigit():
            rows = await (await db.execute("SELECT tg_id, username, balance, deposited, games, wins FROM users WHERE tg_id=?", (int(qq),))).fetchall()
        elif qq:
            rows = await (await db.execute("SELECT tg_id, username, balance, deposited, games, wins FROM users WHERE LOWER(username) LIKE ? LIMIT 30", ("%" + qq.lower() + "%",))).fetchall()
        else:
            rows = await (await db.execute("SELECT tg_id, username, balance, deposited, games, wins FROM users ORDER BY balance DESC LIMIT 30")).fetchall()
    return {"items": [{"tg_id": int(r[0]), "username": r[1] or "", "balance_ton": round(int(r[2] or 0) / float(STARS_PER_TON), 2),
                       "deposited_ton": round(int(r[3] or 0) / float(STARS_PER_TON), 2), "games": int(r[4] or 0), "wins": int(r[5] or 0)} for r in rows or []]}

@app.get("/api/v3/admin/user/{tg_id}")
async def v3_admin_user(tg_id: int, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    async with get_db() as db:
        row = await (await db.execute("SELECT tg_id, username, balance, deposited, games, wins, inventory, COALESCE(chance_bonus,0) FROM users WHERE tg_id=?", (int(tg_id),))).fetchone()
        if not row:
            raise HTTPException(404, "Нет такого игрока")
        hist = await (await db.execute("SELECT game, result, detail, amount, created_at FROM history WHERE tg_id=? ORDER BY id DESC LIMIT 40", (int(tg_id),))).fetchall()
        ref = await (await db.execute("SELECT COUNT(*), COALESCE(SUM(earned),0) FROM referrals WHERE referrer_id=?", (int(tg_id),))).fetchone()
    inv = json.loads(row[6] or "[]") if isinstance(row[6], str) else (row[6] or [])
    return {"tg_id": int(row[0]), "username": row[1] or "", "balance": int(row[2] or 0), "balance_ton": round(int(row[2] or 0) / float(STARS_PER_TON), 2),
            "deposited_ton": round(int(row[3] or 0) / float(STARS_PER_TON), 2), "games": int(row[4] or 0), "wins": int(row[5] or 0),
            "chance_bonus": float(row[7] or 0), "inventory": inv, "inv_ton": round(sum(int(x.get("value") or 0) for x in inv) / float(STARS_PER_TON), 2),
            "hold_left": await deposit_hold_left(int(tg_id)), "ref_invited": int(ref[0] or 0) if ref else 0, "ref_earned": int(ref[1] or 0) if ref else 0,
            "history": [{"game": h[0], "result": h[1], "detail": h[2], "amount": int(h[3] or 0), "ts": h[4]} for h in hist or []]}

class AdmBalReq(BaseModel):
    tg_id: int = 0
    username: str = ""
    delta_ton: float = 0
    set_ton: Optional[float] = None

@app.post("/api/v3/admin/balance")
async def v3_admin_balance(body: AdmBalReq, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    me = await _adm(authorization, x_telegram_init_data)
    body.tg_id = await _resolve_uid(body.tg_id, body.username)
    async with get_db() as db:
        row = await (await db.execute("SELECT balance FROM users WHERE tg_id=?", (int(body.tg_id),))).fetchone()
        if not row:
            raise HTTPException(404, "Нет такого игрока")
        cur = int(row[0] or 0)
        new = int(round(body.set_ton * float(STARS_PER_TON))) if body.set_ton is not None else cur + int(round(float(body.delta_ton or 0) * float(STARS_PER_TON)))
        new = max(0, new)
        await db.execute("UPDATE users SET balance=? WHERE tg_id=?", (new, int(body.tg_id)))
        await db.commit()
    await ledger("admin_balance", new - cur, int(body.tg_id), f"by {me['tg_id']}")
    return {"ok": True, "balance": new, "balance_ton": round(new / float(STARS_PER_TON), 2)}

class AdmHoldReq(BaseModel):
    tg_id: int

@app.post("/api/v3/admin/hold_reset")
async def v3_admin_hold_reset(body: AdmHoldReq, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    await _dep_ensure()
    async with get_db() as db:
        await db.execute("UPDATE dep_holds SET stars_at=0, ton_at=0 WHERE tg_id=?", (int(body.tg_id),))
        try:
            await db.execute("UPDATE users SET withdraw_hold_until=0 WHERE tg_id=?", (int(body.tg_id),))
        except Exception:
            pass
        await db.commit()
    return {"ok": True}

@app.get("/api/v3/admin/withdrawals")
async def v3_admin_wd(status: str = "pending", authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    async with get_db() as db:
        if status == "all":
            rows = await (await db.execute("SELECT id, tg_id, amount, method, dest, note, status, created_at FROM withdrawals ORDER BY id DESC LIMIT 80")).fetchall()
        else:
            rows = await (await db.execute("SELECT id, tg_id, amount, method, dest, note, status, created_at FROM withdrawals WHERE status=? ORDER BY id DESC LIMIT 80", (status,))).fetchall()
    items = []
    for r in rows or []:
        item = None
        try:
            item = (json.loads(r[5] or "{}") or {}).get("item")
        except Exception:
            item = None
        items.append({"id": r[0], "tg_id": r[1], "amount": int(r[2] or 0), "amount_ton": round(int(r[2] or 0) / float(STARS_PER_TON), 2),
                      "method": r[3], "dest": r[4], "status": r[6], "ts": r[7], "item": item})
    return {"items": items}

@app.get("/api/v3/admin/promos")
async def v3_admin_promos(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    async with get_db() as db:
        try:
            rows = await (await db.execute("SELECT code, stars, max_uses, uses FROM promos ORDER BY code")).fetchall()
        except Exception:
            rows = []
    return {"items": [{"code": r[0], "stars": int(r[1] or 0), "ton": round(int(r[1] or 0) / float(STARS_PER_TON), 3), "max_uses": int(r[2] or 0), "uses": int(r[3] or 0)} for r in rows or []]}

class AdmPromoDel(BaseModel):
    code: str

@app.post("/api/v3/admin/promo/delete")
async def v3_admin_promo_del(body: AdmPromoDel, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    async with get_db() as db:
        await db.execute("DELETE FROM promos WHERE UPPER(TRIM(code))=?", (str(body.code).strip().upper(),))
        await db.commit()
    return {"ok": True}

class AdmBroadcast(BaseModel):
    text: str

@app.post("/api/v3/admin/broadcast")
async def v3_admin_broadcast(body: AdmBroadcast, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    text = str(body.text or "").strip()
    if len(text) < 3:
        raise HTTPException(400, "Пустое сообщение")
    async with get_db() as db:
        rows = await (await db.execute("SELECT tg_id FROM users")).fetchall()
    ids = [int(r[0]) for r in rows or [] if r and r[0]]
    async def _run():
        sent = 0
        for tid in ids:
            try:
                await tg_notify(tid, text)
                sent += 1
            except Exception:
                pass
            await asyncio.sleep(0.05)   # ~20 сообщений/сек — лимит Telegram
        print(f"[broadcast] sent {sent}/{len(ids)}")
    asyncio.create_task(_run())
    return {"ok": True, "queued": len(ids)}

@app.get("/api/v3/admin/deposits")
async def v3_admin_deposits(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    await _dep_ensure()
    out = []
    async with get_db() as db:
        try:
            for r in await (await db.execute("SELECT tg_id, amount, paid, created_at FROM deposits ORDER BY created_at DESC LIMIT 40")).fetchall() or []:
                out.append({"kind": "stars", "tg_id": r[0], "amount": f"{int(r[1] or 0)}⭐", "ok": bool(r[2]), "ts": r[3]})
        except Exception:
            pass
        for r in await (await db.execute("SELECT tg_id, amount_ton, got_ton, credited, comment, created_at FROM ton_invoices ORDER BY created_at DESC LIMIT 40")).fetchall() or []:
            out.append({"kind": "ton", "tg_id": r[0], "amount": f"{float(r[1]):g} TON" + (f" (пришло {float(r[2]):g})" if r[3] else ""), "ok": bool(r[3]), "comment": r[4], "ts": r[5]})
    out.sort(key=lambda x: -(int(x.get("ts") or 0)))
    return {"items": out[:60]}


# ----- держим Render бодрым: free-план засыпает через 15 мин без запросов -----
async def _keepalive_loop():
    url = (os.getenv("RENDER_EXTERNAL_URL") or os.getenv("PUBLIC_API_URL") or "").rstrip("/")
    if not url:
        return
    await asyncio.sleep(60)
    import httpx
    while True:
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                await c.get(url + "/api/health")
        except Exception as e:
            print("[keepalive]", e)
        await asyncio.sleep(9 * 60)

@app.on_event("startup")
async def _keepalive_start():
    asyncio.create_task(_keepalive_loop())
    try:
        f = _find_index_html()
        if f:
            _prepare_html(f.read_text(encoding="utf-8"))
            _HTML_CACHE["mt"] = f.stat().st_mtime
            _HTML_CACHE["path"] = str(f)
    except Exception as e:
        print("[frontend warmup]", e)

# ----- админка: выдача подарков, тех.работы, каталог -----
class AdmGiftReq(BaseModel):
    tg_id: int = 0
    username: str = ""
    name: str
    count: int = 1
    deposit: bool = False   # True = это пополнение подарком (рефка, холд, «депозит» в статистике)

async def _resolve_uid(tg_id: int, username: str) -> int:
    if tg_id:
        return int(tg_id)
    un = str(username or "").strip().lstrip("@").lower()
    if not un:
        raise HTTPException(400, "Укажи ID или @username")
    async with get_db() as db:
        row = await (await db.execute("SELECT tg_id FROM users WHERE LOWER(username)=? LIMIT 1", (un,))).fetchone()
    if not row:
        raise HTTPException(404, f"@{un} не найден — пусть откроет приложение хотя бы раз")
    return int(row[0])

@app.post("/api/v3/admin/give_gift")
async def v3_admin_give_gift(body: AdmGiftReq, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    me = await _adm(authorization, x_telegram_init_data)
    body.tg_id = await _resolve_uid(body.tg_id, body.username)
    g0 = find_gift(body.name)
    if not g0:
        raise HTTPException(400, "Подарок не найден в каталоге")
    n = max(1, min(20, int(body.count or 1)))
    async with get_db() as db:
        row = await (await db.execute("SELECT inventory FROM users WHERE tg_id=?", (int(body.tg_id),))).fetchone()
        if not row:
            raise HTTPException(404, "Игрок не найден — он должен хотя бы раз открыть приложение")
        inv = json.loads(row[0] or "[]") if isinstance(row[0], str) else (row[0] or [])
        for _ in range(n):
            g = dict(g0); g["id"] = uuid.uuid4().hex[:10]
            inv.append({k: g[k] for k in ("id", "name", "value", "rarity", "sn", "emoji", "img") if k in g})
        await db.execute("UPDATE users SET inventory=? WHERE tg_id=?", (json.dumps(inv, ensure_ascii=False), int(body.tg_id)))
        await db.commit()
    total = int(g0.get("value") or 0) * n
    if body.deposit:
        tid = int(body.tg_id)
        async with get_db() as db:
            await db.execute("UPDATE users SET deposited=COALESCE(deposited,0)+? WHERE tg_id=?", (total, tid))
            await db.commit()
        await _gdep_ensure()
        async with get_db() as db:
            await db.execute("INSERT INTO gift_deposits(gkey,tg_id,name,number,value,kind,status,ts) VALUES(?,?,?,?,?,?,?,?)",
                             (uuid.uuid4().hex, tid, g0.get("name"), 0, total, "manual", "credited", now_ts()))
            await db.commit()
        await mark_deposit(tid, "ton")
        try:
            await pay_referral(tid, total)
            await add_history(tid, "deposit", "win", f"Пополнение подарком: {g0.get('name')}" + (f" ×{n}" if n > 1 else ""), total)
        except Exception:
            pass
        await ledger("deposit_gift", total, tid, f"{g0.get('name')} x{n} by {me['tg_id']}")
        await tg_push(tid, f"🎁 Пополнение подарком: <b>{g0.get('name')}</b>" + (f" ×{n}" if n > 1 else "") +
                      f" зачислен в инвентарь · {round(total / float(STARS_PER_TON), 2)} TON", "🎰 Играть")
    else:
        await ledger("admin_gift", total, int(body.tg_id), f"{g0.get('name')} x{n} by {me['tg_id']}")
        try:
            await tg_notify(int(body.tg_id), f"🎁 Тебе выдан подарок: <b>{g0.get('name')}</b>" + (f" ×{n}" if n > 1 else ""))
        except Exception:
            pass
    return {"ok": True, "name": g0.get("name"), "count": n, "tg_id": int(body.tg_id)}

@app.get("/api/v3/admin/catalog")
async def v3_admin_catalog(q: str = "", authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    qq = str(q or "").lower().strip()
    items = [g for g in GIFTS_FLAT if not qq or qq in str(g.get("name") or "").lower()]
    items = sorted(items, key=lambda g: int(g.get("value") or 0))[:80]
    return {"items": [{"name": g.get("name"), "value": int(g.get("value") or 0), "ton": round(int(g.get("value") or 0) / float(STARS_PER_TON), 2),
                       "img": g.get("img") or gift_img_url(g.get("name") or "")} for g in items]}

class AdmMaint(BaseModel):
    on: bool
    message: str = ""

@app.post("/api/v3/admin/maintenance")
async def v3_admin_maint(body: AdmMaint, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    MAINTENANCE_MODE["on"] = bool(body.on)
    MAINTENANCE_MODE["message"] = (body.message or "").strip() or "Технические работы. Скоро вернёмся."
    return {"ok": True, **MAINTENANCE_MODE}

@app.get("/api/v3/admin/me")
async def v3_admin_me(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    me = await current_user(authorization, x_telegram_init_data)
    return {"is_admin": bool(me.get("is_admin")), "tg_id": int(me["tg_id"]), "maintenance": dict(MAINTENANCE_MODE)}


# =====================================================================
#  ПУШИ: возврат игроков с реальным бустом · ранг · бэкап · чат
# =====================================================================
BOOST_PP = float(os.getenv("COMEBACK_BOOST_PP") or 3)       # +п.п. к шансу апгрейда (до ×0.9)
BOOST_SEC = int(os.getenv("COMEBACK_BOOST_SEC") or 3600)
BIGWIN_PUSH_TON = float(os.getenv("BIGWIN_PUSH_TON") or 30)
_PUSH = {"last_campaign": 0}

async def _push_ensure():
    async with get_db() as db:
        await db.execute("CREATE TABLE IF NOT EXISTS boosts (tg_id BIGINT PRIMARY KEY, pp REAL DEFAULT 0, until_ts BIGINT DEFAULT 0)")
        await db.execute("CREATE TABLE IF NOT EXISTS push_log (tg_id BIGINT PRIMARY KEY, last_ts BIGINT DEFAULT 0)")
        await db.commit()

async def temp_boost(tg_id: int) -> float:
    try:
        async with get_db() as db:
            row = await (await db.execute("SELECT pp, until_ts FROM boosts WHERE tg_id=?", (int(tg_id),))).fetchone()
        if row and int(row[1] or 0) > now_ts():
            return float(row[0] or 0)
    except Exception:
        pass
    return 0.0

@app.get("/api/v3/boost")
async def v3_boost(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    pp, left = 0.0, 0
    try:
        async with get_db() as db:
            row = await (await db.execute("SELECT pp, until_ts FROM boosts WHERE tg_id=?", (int(u["tg_id"]),))).fetchone()
        if row and int(row[1] or 0) > now_ts():
            pp, left = float(row[0] or 0), int(row[1]) - now_ts()
    except Exception:
        pass
    if _bonus_off(u["tg_id"], "upgrade"):
        pp = 0.0
    return {"pp": pp, "left": left}

def _mask(name: str, uid: int) -> str:
    n = str(name or "").lstrip("@")
    if n:
        return "@" + (n[:max(2, len(n) // 2)] + "***")
    s = str(uid or "")
    return "id " + s[:max(2, len(s) - 4)] + "****"

async def bigwin_campaign(winner: str, gift_name: str, value_stars: int):
    """Крупный выигрыш → пуш тем, кто не заходил 24ч+: реальный буст +X п.п. к шансу апгрейда на час.
    Не чаще раза в 3 часа на весь бот и раза в 48 ч на игрока."""
    if not BOT_TOKEN or value_stars < BIGWIN_PUSH_TON * float(STARS_PER_TON):
        return
    if now_ts() - _PUSH["last_campaign"] < 3 * 3600:
        return
    _PUSH["last_campaign"] = now_ts()
    try:
        await _push_ensure()
        async with get_db() as db:
            rows = await (await db.execute(
                "SELECT u.tg_id FROM users u LEFT JOIN push_log p ON p.tg_id=u.tg_id "
                "WHERE COALESCE(p.last_ts,0) < ? AND u.tg_id NOT IN (SELECT DISTINCT tg_id FROM history WHERE created_at > ?) LIMIT 300",
                (now_ts() - 48 * 3600, now_ts() - 24 * 3600))).fetchall()
        ids = [int(r[0]) for r in rows or [] if r and r[0]]
        ton = round(value_stars / float(STARS_PER_TON), 1)
        mins = BOOST_SEC // 60
        for tid in ids:
            async with get_db() as db:
                await db.execute("INSERT INTO boosts(tg_id,pp,until_ts) VALUES(?,?,?) ON CONFLICT (tg_id) DO UPDATE SET pp=excluded.pp, until_ts=excluded.until_ts",
                                 (tid, BOOST_PP, now_ts() + BOOST_SEC))
                await db.execute("INSERT INTO push_log(tg_id,last_ts) VALUES(?,?) ON CONFLICT (tg_id) DO UPDATE SET last_ts=excluded.last_ts", (tid, now_ts()))
                await db.commit()
            await tg_push(tid, f"🔥 {winner} только что выбил <b>{gift_name}</b> за <b>{ton} TON</b>!\n\n"
                               f"🍀 Для тебя <b>+{BOOST_PP:g}% к шансу апгрейда</b> на {mins} мин — успей!", "🚀 Открыть")
            await asyncio.sleep(0.06)
        print(f"[bigwin push] {len(ids)}")
    except Exception as e:
        print("[bigwin push]", e)

_push_live_orig = push_live
async def push_live(item: dict, user_name: str):  # noqa: F811
    await _push_live_orig(item, user_name)
    try:
        asyncio.create_task(bigwin_campaign(_mask(user_name, 0), item.get("name") or "подарок", int(item.get("value") or 0)))
    except Exception:
        pass

# пуш о новом ранге
_v3_add_wager_orig = v3_add_wager
async def v3_add_wager(tg_id: int, stars: int, mult: float = 0.0):  # noqa: F811
    try:
        before = _v3_rank_view(await _v3_load(tg_id))["rank_index"]
    except Exception:
        before = None
    await _v3_add_wager_orig(tg_id, stars, mult)
    try:
        if before is not None:
            v = _v3_rank_view(await _v3_load(tg_id))
            if v["rank_index"] > before:
                r = v["rank"]; rw = V3_RANKS[v["rank_index"]][4]
                await tg_push(tg_id, f"🎉 Новый ранг: {r['icon']} <b>{r['name']}</b>!\nЗабери награду <b>+{round(rw / float(STARS_PER_TON), 2)} TON</b> в профиле.", "🏆 Забрать")
    except Exception as e:
        print("[rank push]", e)

# ---------------- БЭКАП БАЗЫ АДМИНУ ----------------
BACKUP_TABLES = ["users", "withdrawals", "deposits", "ton_deposits", "ton_invoices", "promos", "promo_uses", "referrals",
                 "v3_state", "dep_holds", "streaks", "boosts", "live_drops", "history"]

async def _table_cols(db, t: str) -> list:
    try:
        if USE_POSTGRES:
            rows = await (await db.execute("SELECT column_name FROM information_schema.columns WHERE table_name=? ORDER BY ordinal_position", (t,))).fetchall()
            return [r[0] for r in rows or []]
        rows = await (await db.execute(f"PRAGMA table_info({t})")).fetchall()
        return [r[1] for r in rows or []]
    except Exception:
        return []

def _jsonable(v):
    if isinstance(v, (int, float, str)) or v is None:
        return v
    try:
        return v.isoformat()
    except Exception:
        return str(v)

async def make_backup() -> bytes:
    import gzip
    out = {"ts": now_ts(), "tables": {}}
    async with get_db() as db:
        for t in BACKUP_TABLES:
            try:
                cols = await _table_cols(db, t)
                q = f"SELECT * FROM {t}"
                if t == "history":
                    q += f" WHERE created_at > {now_ts() - 30 * 86400}"
                elif t == "live_drops":
                    q += " ORDER BY id DESC LIMIT 500"
                rows = await (await db.execute(q)).fetchall()
                out["tables"][t] = {"cols": cols, "rows": [[_jsonable(x) for x in r] for r in rows or []]}
            except Exception as e:
                out["tables"][t] = {"error": str(e)}
    return gzip.compress(json.dumps(out, ensure_ascii=False).encode("utf-8"))

async def send_backup(to_id: int = 0) -> dict:
    to_id = int(to_id or ADMIN_TG_ID)
    if not BOT_TOKEN or not to_id:
        return {"ok": False, "error": "нет BOT_TOKEN или ADMIN_TG_ID"}
    data = await make_backup()
    import httpx
    fname = "wingifts-backup-" + datetime.utcnow().strftime("%Y-%m-%d_%H-%M") + ".json.gz"
    async with httpx.AsyncClient(timeout=60.0) as c:
        r = await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
                         data={"chat_id": str(to_id), "caption": f"🗄 Бэкап базы · {round(len(data) / 1024)} КБ"},
                         files={"document": (fname, data, "application/gzip")})
    ok = r.status_code == 200 and (r.json() or {}).get("ok")
    return {"ok": bool(ok), "size_kb": round(len(data) / 1024), "error": None if ok else r.text[:200]}

async def _backup_loop():
    await asyncio.sleep(120)
    while True:
        try:
            t = datetime.utcnow()
            if t.hour == 1 and t.minute < 10:   # ~04:00 МСК
                await send_backup()
                await asyncio.sleep(1200)
        except Exception as e:
            print("[backup]", e)
        await asyncio.sleep(300)

@app.on_event("startup")
async def _push_start():
    try:
        await _push_ensure()
        await _chat_ensure()
    except Exception as e:
        print("[push ensure]", e)
    asyncio.create_task(_backup_loop())

@app.post("/api/v3/admin/backup")
async def v3_admin_backup(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    me = await _adm(authorization, x_telegram_init_data)
    return await send_backup(int(me["tg_id"]))

# ---------------- ЧАТ ИГРОКОВ ----------------
_CHAT_RL: Dict[int, float] = {}
_LINK_RE = re.compile(r"(https?://|www\.|t\.me/|telegram\.me/|\.(com|ru|net|io|me|org|xyz|app)\b)", re.I)

async def _chat_ensure():
    async with get_db() as db:
        await db.execute("CREATE TABLE IF NOT EXISTS chat_msgs (id BIGINT PRIMARY KEY, tg_id BIGINT, uname TEXT, photo TEXT, rk INTEGER DEFAULT 0, "
                         "text TEXT, ts BIGINT, deleted INTEGER DEFAULT 0)")
        await db.execute("CREATE TABLE IF NOT EXISTS chat_mutes (tg_id BIGINT PRIMARY KEY, until_ts BIGINT DEFAULT 0)")
        await db.commit()

def _chat_row(r) -> dict:
    return {"id": int(r[0]), "uid": int(r[1] or 0), "user": r[2] or "", "photo": r[3] or "", "rk": int(r[4] or 0), "text": r[5] or "", "ts": int(r[6] or 0)}

@app.get("/api/v3/chat")
async def v3_chat_list(after: int = 0):
    async with get_db() as db:
        if after:
            rows = await (await db.execute("SELECT id,tg_id,uname,photo,rk,text,ts FROM chat_msgs WHERE deleted=0 AND id>? ORDER BY id ASC LIMIT 60", (int(after),))).fetchall()
        else:
            rows = await (await db.execute("SELECT id,tg_id,uname,photo,rk,text,ts FROM chat_msgs WHERE deleted=0 ORDER BY id DESC LIMIT 60")).fetchall()
            rows = list(reversed(rows or []))
    return {"items": [_chat_row(r) for r in rows or []]}

class ChatPost(BaseModel):
    text: str

@app.post("/api/v3/chat")
async def v3_chat_post(body: ChatPost, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    tid = int(u["tg_id"])
    text = re.sub(r"\s+", " ", str(body.text or "")).strip()[:240]
    if len(text) < 1:
        raise HTTPException(400, "Пустое сообщение")
    if not u.get("is_admin"):
        if int(u.get("games") or 0) < 3 and int(u.get("deposited") or 0) <= 0:
            raise HTTPException(400, "Писать в чат можно после 3 игр")
        if _LINK_RE.search(text):
            raise HTTPException(400, "Ссылки в чате запрещены")
        t = time.time()
        if t - _CHAT_RL.get(tid, 0) < 3:
            raise HTTPException(429, "Не так быстро")
        _CHAT_RL[tid] = t
        async with get_db() as db:
            m = await (await db.execute("SELECT until_ts FROM chat_mutes WHERE tg_id=?", (tid,))).fetchone()
        if m and int(m[0] or 0) > now_ts():
            raise HTTPException(403, f"Ты в муте ещё {max(1, (int(m[0]) - now_ts()) // 60)} мин")
    photo = ""
    try:
        async with get_db() as db:
            pr = await (await db.execute("SELECT COALESCE(photo_url,'') FROM users WHERE tg_id=?", (tid,))).fetchone()
            photo = pr[0] if pr else ""
    except Exception:
        pass
    try:
        rk = _v3_rank_view(await _v3_load(tid))["rank_index"]
    except Exception:
        rk = 0
    mid = int(time.time() * 1000) * 10 + random.randint(0, 9)
    uname = (u.get("username") or "") or f"id{str(tid)[-4:]}"
    async with get_db() as db:
        await db.execute("INSERT INTO chat_msgs(id,tg_id,uname,photo,rk,text,ts,deleted) VALUES(?,?,?,?,?,?,?,0)", (mid, tid, uname, photo, rk, text, now_ts()))
        await db.commit()
    msg = {"id": mid, "uid": tid, "user": uname, "photo": photo, "rk": rk, "text": text, "ts": now_ts(), "admin": bool(u.get("is_admin"))}
    try:
        await sio.emit("chat_msg", msg)
    except Exception:
        pass
    return {"ok": True, "msg": msg}

class ChatMod(BaseModel):
    id: int = 0
    tg_id: int = 0
    minutes: int = 60

@app.post("/api/v3/chat/delete")
async def v3_chat_delete(body: ChatMod, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    async with get_db() as db:
        await db.execute("UPDATE chat_msgs SET deleted=1 WHERE id=?", (int(body.id),))
        await db.commit()
    try:
        await sio.emit("chat_del", {"id": int(body.id)})
    except Exception:
        pass
    return {"ok": True}

@app.post("/api/v3/chat/mute")
async def v3_chat_mute(body: ChatMod, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    until = now_ts() + max(1, int(body.minutes or 60)) * 60
    async with get_db() as db:
        await db.execute("INSERT INTO chat_mutes(tg_id,until_ts) VALUES(?,?) ON CONFLICT (tg_id) DO UPDATE SET until_ts=excluded.until_ts", (int(body.tg_id), until))
        await db.commit()
    return {"ok": True, "until": until}


# =====================================================================
#  ПОПОЛНЕНИЕ ПОДАРКАМИ (Telegram Business)
#  Игрок отправляет подарок на аккаунт GIFT_RELAYER. К этому аккаунту
#  подключён наш бот (Настройки → Telegram Business → Чат-боты), бот
#  получает business_message с unique_gift / gift и зачисляет игроку.
# =====================================================================
async def _gdep_ensure():
    async with get_db() as db:
        await db.execute("CREATE TABLE IF NOT EXISTS gift_deposits (gkey TEXT PRIMARY KEY, tg_id BIGINT, name TEXT, number BIGINT DEFAULT 0, "
                         "value BIGINT DEFAULT 0, kind TEXT, status TEXT, ts BIGINT)")
        await db.commit()

async def handle_business_gift(bm: dict):
    ug = bm.get("unique_gift") or {}
    rg = bm.get("gift") or {}
    if not ug and not rg:
        return
    sender = bm.get("from") or {}
    tid = int(sender.get("id") or 0)
    if not tid or sender.get("is_bot"):
        return
    # подарок, который отправил сам владелец релеера (исходящий), не считаем
    try:
        if GIFT_RELAYER and str(sender.get("username") or "").lower() == GIFT_RELAYER.lower():
            return
    except Exception:
        pass
    await _gdep_ensure()
    if ug:
        g = ug.get("gift") or {}
        name = str(g.get("base_name") or g.get("name") or "").strip()
        number = int(g.get("number") or 0)
        gkey = str(ug.get("owned_gift_id") or f"u:{name}:{number}:{bm.get('message_id')}")
        kind = "unique"
    else:
        g = rg.get("gift") or {}
        name = "Gift"
        number = 0
        gkey = str(rg.get("owned_gift_id") or f"r:{tid}:{bm.get('message_id')}:{bm.get('date')}")
        kind = "regular"
    async with get_db() as db:
        dup = await (await db.execute("SELECT status FROM gift_deposits WHERE gkey=?", (gkey,))).fetchone()
    if dup:
        return
    # пользователь должен существовать
    async with get_db() as db:
        urow = await (await db.execute("SELECT inventory, balance FROM users WHERE tg_id=?", (tid,))).fetchone()
    if not urow:
        async with get_db() as db:
            await db.execute("INSERT INTO gift_deposits(gkey,tg_id,name,number,value,kind,status,ts) VALUES(?,?,?,?,?,?,?,?)",
                             (gkey, tid, name, number, 0, kind, "no_user", now_ts()))
            await db.commit()
        await tg_push(tid, f"🎁 Подарок <b>{name}</b> получен!\nОткрой приложение один раз, чтобы мы создали тебе аккаунт, и напиши @{SUPPORT_USERNAME} — зачислим вручную.", "🎁 Открыть")
        if ADMIN_TG_ID:
            await tg_notify(ADMIN_TG_ID, f"⚠️ Подарок от незарегистрированного <code>{tid}</code>: {name} #{number}")
        return
    if kind == "regular":
        stars = int(rg.get("convert_star_count") or (g.get("star_count") or 0))
        status = "credited" if stars > 0 else "review"
        async with get_db() as db:
            if stars > 0:
                await db.execute("UPDATE users SET balance=COALESCE(balance,0)+?, deposited=COALESCE(deposited,0)+? WHERE tg_id=?", (stars, stars, tid))
            await db.execute("INSERT INTO gift_deposits(gkey,tg_id,name,number,value,kind,status,ts) VALUES(?,?,?,?,?,?,?,?)",
                             (gkey, tid, name, 0, stars, kind, status, now_ts()))
            await db.commit()
        if stars > 0:
            await mark_deposit(tid, "ton")
            await add_history(tid, "deposit", "win", f"Обычный подарок → +{stars}⭐", stars)
            await tg_push(tid, f"🎁 Подарок получен · <b>+{stars}⭐</b> на баланс", "🎰 Играть")
        return
    base = find_gift(name)
    if not base:
        async with get_db() as db:
            await db.execute("INSERT INTO gift_deposits(gkey,tg_id,name,number,value,kind,status,ts) VALUES(?,?,?,?,?,?,?,?)",
                             (gkey, tid, name, number, 0, kind, "review", now_ts()))
            await db.commit()
        await tg_push(tid, f"🎁 Подарок <b>{name} #{number}</b> получен! Его нет в нашем каталоге — админ проверит и зачислит вручную.", "🎁 Открыть")
        if ADMIN_TG_ID:
            await tg_notify(ADMIN_TG_ID, f"🎁 На проверку: <b>{name} #{number}</b> от <code>{tid}</code> — нет в каталоге, выдай через админку")
        return
    item = dict(gift_public(dict(base)))
    item["id"] = uuid.uuid4().hex[:10]
    if number:
        item["number"] = number
    inv = json.loads(urow[0] or "[]") if isinstance(urow[0], str) else (urow[0] or [])
    inv.append({k: item[k] for k in ("id", "name", "value", "rarity", "sn", "emoji", "img", "number") if k in item})
    value = int(item.get("value") or 0)
    async with get_db() as db:
        await db.execute("UPDATE users SET inventory=?, deposited=COALESCE(deposited,0)+? WHERE tg_id=?", (json.dumps(inv, ensure_ascii=False), value, tid))
        await db.execute("INSERT INTO gift_deposits(gkey,tg_id,name,number,value,kind,status,ts) VALUES(?,?,?,?,?,?,?,?)",
                         (gkey, tid, name, number, value, kind, "credited", now_ts()))
        await db.commit()
    await mark_deposit(tid, "ton")
    try:
        await pay_referral(tid, value)
        await add_history(tid, "deposit", "win", f"Подарок {name} #{number} → инвентарь", value)
        await ledger("deposit_gift", value, tid, f"{name}#{number}")
    except Exception:
        pass
    await tg_push(tid, f"🎁 <b>{name} #{number}</b> зачислен в инвентарь!\nЦена: <b>{round(value / float(STARS_PER_TON), 2)} TON</b> — можно апгрейдить, играть или вывести.", "🎰 Играть")
    if ADMIN_TG_ID:
        try:
            await tg_notify(ADMIN_TG_ID, f"📥 Депозит подарком: {name} #{number} · {round(value / float(STARS_PER_TON), 2)} TON от <code>{tid}</code>")
        except Exception:
            pass

@app.get("/api/v3/gift_deposit/info")
async def v3_gdep_info(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    await _gdep_ensure()
    async with get_db() as db:
        rows = await (await db.execute("SELECT name, number, value, status, ts FROM gift_deposits WHERE tg_id=? ORDER BY ts DESC LIMIT 10", (int(u["tg_id"]),))).fetchall()
    return {"relayer": GIFT_RELAYER or SUPPORT_USERNAME, "enabled": True, "manual": not bool(GIFT_RELAYER),
            "my_id": int(u["tg_id"]), "my_username": u.get("username") or "",
            "items": [{"name": r[0], "number": int(r[1] or 0), "value": int(r[2] or 0), "status": r[3], "ts": r[4],
                       "img": gift_img_url(r[0] or "") if r[0] and r[0] != "Gift" else ""} for r in rows or []]}

@app.get("/api/v3/admin/gift_deposits")
async def v3_admin_gdeps(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    await _gdep_ensure()
    async with get_db() as db:
        rows = await (await db.execute("SELECT gkey, tg_id, name, number, value, status, ts FROM gift_deposits ORDER BY ts DESC LIMIT 60")).fetchall()
    return {"items": [{"key": r[0], "tg_id": r[1], "name": r[2], "number": int(r[3] or 0), "value_ton": round(int(r[4] or 0) / float(STARS_PER_TON), 2),
                       "status": r[5], "ts": r[6]} for r in rows or []]}


# =====================================================================
#  ЖИВЫЕ ЦЕНЫ NFT + АВТОБАЛАНС КЕЙСОВ
# =====================================================================
PRICE_FACTOR = float(os.getenv("PRICE_FACTOR") or 1.0)        # множитель к рыночному floor (1.05 = +5% на комиссии)
PRICE_MAX_JUMP = float(os.getenv("PRICE_MAX_JUMP") or 0.5)    # за одно обновление цена меняется не больше чем на ±50%
CASE_RTP = float(os.getenv("CASE_RTP") or 0.87)               # целевой возврат игрокам в кейсах (0.87 = касса +13%)
PRICE_FEED_URLS = [u.strip() for u in (os.getenv("PRICE_FEED_URL") or "").split(",") if u.strip()] or [
    "https://atlannn.github.io/telegram-gifts-floor-price/latest.json",
    "https://atlannn.github.io/telegram-gifts-floor-price/api/latest.json",
    "https://atlannn.github.io/telegram-gifts-floor-price/data/latest.json",
]
PRICE_STATE = {"ts": 0, "src": "", "matched": 0, "unmatched": [], "error": "", "changed": 0}
_BASE_PCT: Dict[str, list] = {}   # исходные веса таблиц (форма распределения), чтобы ребаланс не «уплывал»

def _norm(n: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(n or "").lower().replace("’", "").replace("'", ""))

def _set_gift_value(name: str, value: int):
    """value — «сырая» рыночная цена; к NFT (не Common) добавляется +NFT_SHOP_EXTRA_TON."""
    key = (name or "").lower().strip()
    ex = nft_extra_stars()
    for g in GIFTS_FLAT:
        if (g.get("name") or "").lower().strip() == key:
            g["_raw"] = int(value)
            g["value"] = int(value) + (ex if is_nft_row(g) else 0)
    gp = _BY_NAME.get(key)
    if gp:
        gp["value"] = int(value) + (ex if is_nft_row(gp) else 0)

def _nft_bump_init():
    """Один раз: +2 TON ко всем NFT каталога (то, что не обновится из БД/фида)."""
    ex = nft_extra_stars()
    for g in GIFTS_FLAT:
        if "_raw" in g:
            continue
        g["_raw"] = int(g.get("value") or 0)
        if ex and is_nft_row(g):
            g["value"] = g["_raw"] + ex
        gp = _BY_NAME.get((g.get("name") or "").lower().strip())
        if gp is not None:
            gp["value"] = int(g["value"])
_nft_bump_init()

def apply_case_price_bump():
    """Кейсы под подорожавшие NFT: цена кейса растёт на (доля NFT в дропе × наценка). Один раз на процесс."""
    if os.getenv("CASE_PRICE_BUMP", "1") != "1":
        return
    ex = nft_extra_stars()
    if ex <= 0:
        return
    for cid, c in CASES.items():
        if cid in DISABLED_CASES or "_price0" in c:
            continue
        price = int(c.get("price") or 0)
        if price <= 0 or c.get("allin") or c.get("category") in ("allin", "free", "promo") or cid in ("free_daily", "promo_case", "allin_case"):
            continue
        table = DROP_TABLES.get(cid)
        if not table:
            table = _auto_table(c)           # строим по старой цене, чтобы форма дропа не поплыла
            DROP_TABLES[cid] = table
        if not table:
            continue
        base = [float(r.get("pct") or 1.0) for r in table]
        tot = sum(base) or 1.0
        share = 0.0
        for b, r in zip(base, table):
            gp = _BY_NAME.get(str(r.get("name") or "").lower().strip())
            if gp and is_nft_row(gp):
                share += b
        share /= tot
        c["_price0"] = price
        c["price"] = price + int(round(ex * share))

def _extract_floors(data) -> Dict[str, float]:
    """Достаёт {нормализованное_имя: floor_TON} из JSON почти любой формы."""
    out: Dict[str, float] = {}
    NAME_KEYS = ("name", "collection", "title", "slug", "short_name", "gift", "id")
    PRICE_KEYS = ("floor_fixed_gram", "floor_gram", "floor_ton", "floor_price_ton", "floorPrice", "floor_price", "floor", "price_ton", "price")
    def num(v):
        try:
            f = float(v)
            return f if f > 0 else None
        except Exception:
            return None
    def walk(o, key_hint=""):
        if isinstance(o, dict):
            nm = None
            for k in NAME_KEYS:
                if isinstance(o.get(k), str) and o.get(k):
                    nm = o[k]; break
            pr = None
            for k in PRICE_KEYS:
                if k in o:
                    pr = num(o[k] if not isinstance(o[k], dict) else (o[k].get("ton") or o[k].get("gram") or o[k].get("value")))
                    if pr: break
            if nm and pr:
                out[_norm(nm)] = pr
            elif pr and key_hint and not nm:
                out[_norm(key_hint)] = pr
            for k, v in o.items():
                if isinstance(v, (dict, list)):
                    walk(v, k if isinstance(k, str) else "")
                elif key_hint == "" and isinstance(v, (int, float)) and isinstance(k, str) and len(k) > 2 and not nm:
                    pass
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(data)
    return out

async def _price_save(name: str, value: int, src: str, manual: int = 0):
    async with get_db() as db:
        await db.execute("CREATE TABLE IF NOT EXISTS gift_prices (name TEXT PRIMARY KEY, value BIGINT, src TEXT, manual INTEGER DEFAULT 0, ts BIGINT, prev BIGINT DEFAULT 0)")
        row = await (await db.execute("SELECT value FROM gift_prices WHERE name=?", (name,))).fetchone()
        prev = int(row[0] or 0) if row else 0
        await db.execute("INSERT INTO gift_prices(name,value,src,manual,ts,prev) VALUES(?,?,?,?,?,?) "
                         "ON CONFLICT (name) DO UPDATE SET value=excluded.value, src=excluded.src, manual=excluded.manual, ts=excluded.ts, prev=excluded.prev",
                         (name, int(value), src, int(manual), now_ts(), prev))
        await db.commit()

async def prices_load_saved():
    try:
        async with get_db() as db:
            await db.execute("CREATE TABLE IF NOT EXISTS gift_prices (name TEXT PRIMARY KEY, value BIGINT, src TEXT, manual INTEGER DEFAULT 0, ts BIGINT, prev BIGINT DEFAULT 0)")
            await db.commit()
            rows = await (await db.execute("SELECT name, value FROM gift_prices")).fetchall()
        for n, v in rows or []:
            if v and int(v) > 0:
                _set_gift_value(n, int(v))
    except Exception as e:
        print("[prices load]", e)

async def prices_refresh() -> dict:
    import httpx
    data, src, err = None, "", ""
    for url in PRICE_FEED_URLS:
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as c:
                r = await c.get(url)
            if r.status_code == 200:
                data = r.json(); src = url; break
            err = f"{url}: HTTP {r.status_code}"
        except Exception as e:
            err = f"{url}: {e}"
    if data is None:
        PRICE_STATE.update({"ts": now_ts(), "error": err or "нет источника"})
        return dict(PRICE_STATE)
    floors = _extract_floors(data)
    async with get_db() as db:
        await db.execute("CREATE TABLE IF NOT EXISTS gift_prices (name TEXT PRIMARY KEY, value BIGINT, src TEXT, manual INTEGER DEFAULT 0, ts BIGINT, prev BIGINT DEFAULT 0)")
        await db.commit()
        manual = {r[0] for r in (await (await db.execute("SELECT name FROM gift_prices WHERE manual=1")).fetchall() or [])}
    matched, changed, unmatched = 0, 0, []
    for g in GIFTS_FLAT:
        name = g.get("name") or ""
        if not is_real_gift(name) or name in manual:
            continue
        f = floors.get(_norm(name))
        if not f:
            unmatched.append(name); continue
        matched += 1
        new = int(round(f * float(STARS_PER_TON) * PRICE_FACTOR))
        old = int(g.get("_raw") or g.get("value") or 0)
        if old > 0:
            lo, hi = int(old * (1 - PRICE_MAX_JUMP)), int(old * (1 + PRICE_MAX_JUMP))
            new = max(lo, min(hi, new))     # защита от глюков фида
        new = max(1, new)
        if new != old:
            _set_gift_value(name, new)
            await _price_save(name, new, "feed")
            changed += 1
    PRICE_STATE.update({"ts": now_ts(), "src": src, "matched": matched, "unmatched": unmatched[:40], "error": "" if matched else "фид прочитан, но ни один подарок не совпал по названию", "changed": changed})
    rebalance_cases()
    print(f"[prices] {src} matched={matched} changed={changed}")
    return dict(PRICE_STATE)

def _tilt(vals: list, base: list, target: float) -> list:
    """Веса w_i = base_i · v_i^(-b): подбираем b, чтобы средний дроп = target. Форма кейса сохраняется."""
    vals = [max(1.0, float(v)) for v in vals]
    base = [max(1e-6, float(b)) for b in base]
    def ev(b):
        ws = [bi * (vi ** -b) for bi, vi in zip(base, vals)]
        t = sum(ws)
        return sum(w * v for w, v in zip(ws, vals)) / t, ws
    lo, hi = -4.0, 8.0
    if ev(hi)[0] > target:
        return ev(hi)[1]
    if ev(lo)[0] < target:
        return ev(lo)[1]
    for _ in range(60):
        mid = (lo + hi) / 2
        if ev(mid)[0] > target:
            lo = mid
        else:
            hi = mid
    return ev((lo + hi) / 2)[1]

def _row_value(row) -> int:
    if row.get("name"):
        g = _BY_NAME.get(str(row["name"]).lower().strip())
        if g:
            return int(g.get("value") or 0)
        for x in GIFTS_FLAT:
            if x.get("name") == row["name"]:
                return int(x.get("value") or 0)
        return int(row.get("value") or 0)
    return int(row.get("stars") or 0)

def _auto_table(c: dict) -> list:
    price = int(c.get("price") or 0)
    names = [n for n in (c.get("force_names") or []) if any(g.get("name") == n for g in GIFTS_FLAT)]
    cap = price * float(os.getenv("CASE_MAX_X") or 40)   # топ-приз не дороже ×40 цены кейса — касса не ляжет от одного дропа
    pool = [g for g in GIFTS_FLAT if g.get("name") in names and int(g.get("value") or 0) <= cap]
    near = sorted([g for g in GIFTS_FLAT if is_real_gift(g.get("name") or "") and 0.25 * price <= int(g.get("value") or 0) <= 8 * price],
                  key=lambda g: int(g.get("value") or 0))
    if len(near) > 10:
        step = (len(near) - 1) / 9.0
        near = [near[int(round(i * step))] for i in range(10)]
    seen = {g.get("name") for g in pool}
    for g in near:
        if g.get("name") not in seen and len(pool) < 12:
            pool.append(g); seen.add(g.get("name"))
    cheap = sorted([g for g in GIFTS_FLAT if int(g.get("value") or 0) < price * 0.6], key=lambda g: -int(g.get("value") or 0))[:2]
    for g in cheap:
        if g.get("name") not in seen:
            pool.append(g); seen.add(g.get("name"))
    return [{"name": g["name"], "value": int(g.get("value") or 0), "pct": 1.0} for g in pool]

def rebalance_cases() -> dict:
    """Пересчитывает шансы всех кейсов под текущие цены: RTP = CASE_RTP для каждого кейса."""
    report = {}
    for cid, c in CASES.items():
        if cid in DISABLED_CASES:
            continue
        price = int(c.get("price") or 0)
        if price <= 0 or c.get("allin") or c.get("category") in ("allin", "free", "promo") or cid in ("free_daily", "promo_case", "allin_case"):
            continue
        if cid not in DROP_TABLES:
            DROP_TABLES[cid] = _auto_table(c)
        table = DROP_TABLES[cid]
        if not table:
            continue
        if cid not in _BASE_PCT:
            _BASE_PCT[cid] = [float(r.get("pct") or 1.0) for r in table]
        vals = [_row_value(r) for r in table]
        ws = _tilt(vals, _BASE_PCT[cid], CASE_RTP * price)
        tot = sum(ws) or 1.0
        for r, w, v in zip(table, ws, vals):
            r["pct"] = round(w / tot * 100, 3)
            if r.get("name"):
                r["value"] = v
        ev = sum(r["pct"] / 100 * v for r, v in zip(table, vals))
        win = sum(r["pct"] for r, v in zip(table, vals) if v > price)
        report[cid] = {"name": c.get("name"), "price": price, "rtp": round(ev / price * 100, 1), "win_pct": round(win, 1),
                       "top": max(vals) if vals else 0, "items": len(table)}
    PRICE_STATE["cases"] = report
    return report

async def _prices_loop():
    await asyncio.sleep(40)
    while True:
        try:
            await prices_refresh()
        except Exception as e:
            print("[prices loop]", e)
        await asyncio.sleep(int(os.getenv("PRICE_REFRESH_SEC") or 6 * 3600))

@app.on_event("startup")
async def _prices_start():
    await prices_load_saved()
    try:
        apply_case_price_bump()
    except Exception as e:
        print("[case price bump]", e)
    rebalance_cases()
    asyncio.create_task(_prices_loop())

# пересчёт цен в инвентаре на текущие (иначе старые подарки продавались/апгрейдились по старой цене)
_current_user_orig = current_user
async def current_user(*args, **kwargs):  # noqa: F811
    u = await _current_user_orig(*args, **kwargs)
    try:
        for it in (u.get("inventory") or []):
            g = _BY_NAME.get(str(it.get("name") or "").lower().strip())
            if g and int(g.get("value") or 0) > 0 and not it.get("rental"):
                it["value"] = int(g["value"])
            if g and g.get("sg"):
                it["img"] = g["img"]   # официальная картинка обычного подарка
    except Exception:
        pass
    return u

@app.get("/api/v3/admin/prices")
async def v3_admin_prices(q: str = "", authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    saved = {}
    try:
        async with get_db() as db:
            for r in await (await db.execute("SELECT name, src, manual, ts, prev FROM gift_prices")).fetchall() or []:
                saved[r[0]] = {"src": r[1], "manual": bool(r[2]), "ts": r[3], "prev": int(r[4] or 0)}
    except Exception:
        pass
    qq = (q or "").lower().strip()
    items = []
    for g in sorted([g for g in GIFTS_FLAT if is_real_gift(g.get("name") or "")], key=lambda g: -int(g.get("value") or 0)):
        if qq and qq not in (g.get("name") or "").lower():
            continue
        sv = saved.get(g["name"], {})
        v = int(g.get("value") or 0)
        prev = sv.get("prev") or 0
        items.append({"name": g["name"], "ton": round(v / float(STARS_PER_TON), 3), "manual": sv.get("manual", False),
                      "src": sv.get("src", "каталог"), "ts": sv.get("ts"), "change": round((v - prev) / prev * 100, 1) if prev else None,
                      "img": g.get("img") or gift_img_url(g["name"])})
    return {"state": {k: v for k, v in PRICE_STATE.items() if k != "cases"}, "items": items[:200]}

@app.post("/api/v3/admin/prices/refresh")
async def v3_admin_prices_refresh(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    st = await prices_refresh()
    return {k: v for k, v in st.items() if k != "cases"}

class AdmPriceSet(BaseModel):
    name: str
    ton: float = 0
    auto: bool = False   # True = снять ручную цену, вернуть автообновление

@app.post("/api/v3/admin/price")
async def v3_admin_price_set(body: AdmPriceSet, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    g = _BY_NAME.get(body.name.lower().strip())
    if not g:
        raise HTTPException(404, "Нет такого подарка")
    if body.auto:
        async with get_db() as db:
            await db.execute("UPDATE gift_prices SET manual=0 WHERE name=?", (g["name"],))
            await db.commit()
        return {"ok": True}
    if body.ton <= 0:
        raise HTTPException(400, "Цена должна быть больше 0")
    v = int(round(body.ton * float(STARS_PER_TON)))
    _set_gift_value(g["name"], v)
    await _price_save(g["name"], v, "admin", 1)
    rebalance_cases()
    return {"ok": True, "name": g["name"], "ton": body.ton}

@app.get("/api/v3/admin/cases_balance")
async def v3_admin_cases_balance(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    rep = rebalance_cases()
    rows = sorted(rep.values(), key=lambda r: r["price"])
    return {"target_rtp": round(CASE_RTP * 100, 1), "items": [{**r, "price_ton": round(r["price"] / float(STARS_PER_TON), 2),
            "top_ton": round(r["top"] / float(STARS_PER_TON), 2)} for r in rows]}


# =====================================================================
#  ЖИВЫЕ NFT: атрибуты + Lottie с Fragment (кеш + прокси, без CORS)
#  · ТВОЯ НЕДЕЛЯ (wrapped) · картинки для сторис
# =====================================================================
_NFT_META: Dict[str, dict] = {}
_NFT_ANIM: Dict[str, bytes] = {}
_STORY_IMG: Dict[str, tuple] = {}

def _nft_slug(s: str) -> str:
    s = str(s or "").lower().strip()
    m = re.match(r"^([a-z0-9]+)-(\d{1,7})$", s)
    if not m:
        raise HTTPException(400, "bad slug")
    return f"{m.group(1)}-{int(m.group(2))}"

@app.get("/api/v3/nft/{slug}")
async def v3_nft_meta(slug: str):
    key = _nft_slug(slug)
    if key in _NFT_META:
        return _NFT_META[key]
    import httpx
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as c:
            r = await c.get(f"https://nft.fragment.com/gift/{key}.json")
        d = r.json() if r.status_code == 200 else {}
    except Exception:
        d = {}
    attrs = {}
    for a in (d.get("attributes") or []):
        t = str(a.get("trait_type") or "").lower()
        attrs[t] = {"value": a.get("value"), "rarity": a.get("rarity") or a.get("rarity_per_mille") or a.get("percent")}
    out = {"slug": key, "name": d.get("name") or key, "image": f"https://nft.fragment.com/gift/{key}.webp",
           "anim": f"/api/v3/nft_anim/{key}", "model": attrs.get("model"), "backdrop": attrs.get("backdrop"),
           "symbol": attrs.get("symbol") or attrs.get("pattern"), "ok": bool(d)}
    if len(_NFT_META) > 3000:
        _NFT_META.clear()
    _NFT_META[key] = out
    return out

@app.get("/api/v3/nft_anim/{slug}")
async def v3_nft_anim(slug: str):
    from fastapi.responses import Response
    key = _nft_slug(slug)
    data = _NFT_ANIM.get(key)
    if data is None:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as c:
                r = await c.get(f"https://nft.fragment.com/gift/{key}.lottie.json")
            if r.status_code != 200:
                raise HTTPException(404, "no anim")
            data = r.content
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(502, "fragment unavailable")
        if len(_NFT_ANIM) > 700:
            _NFT_ANIM.pop(next(iter(_NFT_ANIM)))
        _NFT_ANIM[key] = data
    return Response(data, media_type="application/json", headers={"Cache-Control": "public, max-age=604800"})

@app.get("/api/v3/wrapped")
async def v3_wrapped(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    tid = int(u["tg_id"]); since = now_ts() - 7 * 86400
    async with get_db() as db:
        rows = await (await db.execute("SELECT game, result, detail, amount, created_at FROM history WHERE tg_id=? AND created_at>=?", (tid, since))).fetchall()
    rows = rows or []
    games: Dict[str, int] = {}
    days = set(); net = 0; cases = 0; best = None; best_up = None; upg = 0; upg_win = 0
    for g, res, det, amt, ts in rows:
        if g in ("deposit", "withdraw", "bonus", "shop"):
            continue
        games[g] = games.get(g, 0) + 1
        net += int(amt or 0)
        try:
            days.add(int(ts or 0) // 86400)
        except Exception:
            pass
        if g == "case":
            cases += 1
        if g == "upgrade":
            upg += 1
            if res == "win":
                upg_win += 1
                if not best_up or int(amt or 0) > best_up["amount"]:
                    best_up = {"detail": det, "amount": int(amt or 0)}
        if res == "win" and (not best or int(amt or 0) > best["amount"]):
            best = {"game": g, "detail": det, "amount": int(amt or 0)}
    # место по обороту среди всех (по рангу/обороту за всё время — честно и дёшево)
    place, total_players = None, None
    try:
        st = await _v3_load(tid)
        async with get_db() as db:
            r1 = await (await db.execute("SELECT COUNT(*) FROM v3_state WHERE wager > ?", (int(st["wager"]),))).fetchone()
            r2 = await (await db.execute("SELECT COUNT(*) FROM v3_state WHERE wager > 0")).fetchone()
        place = int(r1[0] or 0) + 1; total_players = int(r2[0] or 0)
    except Exception:
        pass
    fav = max(games.items(), key=lambda x: x[1])[0] if games else None
    try:
        rv = _v3_rank_view(await _v3_load(tid)); rank = {"name": rv["rank"]["name"], "icon": rv["rank"]["icon"], "idx": rv["rank_index"]}
    except Exception:
        rank = None
    return {"games_total": sum(games.values()), "games": games, "fav": fav, "days": len(days), "net": net, "cases": cases,
            "best": best, "best_up": best_up, "upg": upg, "upg_win": upg_win, "place": place, "players": total_players,
            "rank": rank, "name": u.get("username") or ""}

class StoryImg(BaseModel):
    png: str

@app.post("/api/v3/story_img")
async def v3_story_img(body: StoryImg, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await current_user(authorization, x_telegram_init_data)
    import base64
    raw = str(body.png or "")
    if "," in raw:
        raw = raw.split(",", 1)[1]
    try:
        data = base64.b64decode(raw)
    except Exception:
        raise HTTPException(400, "bad image")
    if len(data) > 3_000_000:
        raise HTTPException(400, "too big")
    sid = uuid.uuid4().hex[:16]
    for k in [k for k, v in _STORY_IMG.items() if v[1] < now_ts() - 3600]:
        _STORY_IMG.pop(k, None)
    _STORY_IMG[sid] = (data, now_ts())
    base = (os.getenv("RENDER_EXTERNAL_URL") or os.getenv("PUBLIC_API_URL") or "").rstrip("/")
    return {"url": f"{base}/story/{sid}.png" if base else f"/story/{sid}.png"}

@app.get("/story/{sid}.png")
async def v3_story_get(sid: str):
    from fastapi.responses import Response
    v = _STORY_IMG.get(sid)
    if not v:
        raise HTTPException(404, "expired")
    return Response(v[0], media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


# =====================================================================
#  БЛОГЕРЫ · ПРИВЕТСТВИЕ ПО РЕФКЕ · АНТИФРОД · ЯЗЫКИ БОТА
# =====================================================================
WELCOME_BONUS_TON = float(os.getenv("WELCOME_BONUS_TON") or 0.1)
BLOGGER_BONUS_TON = float(os.getenv("BLOGGER_BONUS_TON") or 0.1)
_CHALLENGE: Dict[int, tuple] = {}

async def _gr_ensure():
    async with get_db() as db:
        await db.execute("CREATE TABLE IF NOT EXISTS bloggers (code TEXT PRIMARY KEY, name TEXT, contact TEXT, created_at BIGINT, active INTEGER DEFAULT 1)")
        await db.execute("CREATE TABLE IF NOT EXISTS blogger_users (tg_id BIGINT PRIMARY KEY, code TEXT, ts BIGINT)")
        await db.execute("CREATE TABLE IF NOT EXISTS start_payloads (tg_id BIGINT PRIMARY KEY, payload TEXT, ts BIGINT, done INTEGER DEFAULT 0)")
        await db.execute("CREATE TABLE IF NOT EXISTS welcome_claims (tg_id BIGINT PRIMARY KEY, amount BIGINT, src TEXT, ts BIGINT)")
        await db.execute("CREATE TABLE IF NOT EXISTS fps (tg_id BIGINT PRIMARY KEY, fp TEXT, ts BIGINT)")
        await db.commit()
    for col, typ in (("bonus_ton", "REAL"), ("rev_pct", "REAL DEFAULT 0"), ("rev_basis", "TEXT DEFAULT 'profit'"), ("paid_ton", "REAL DEFAULT 0")):
        try:
            async with get_db() as db:
                await db.execute(f"ALTER TABLE bloggers ADD COLUMN {'IF NOT EXISTS ' if USE_POSTGRES else ''}{col} {typ}")
                await db.commit()
        except Exception:
            pass

@app.on_event("startup")
async def _gr_start():
    try:
        await _gr_ensure()
    except Exception as e:
        print("[growth ensure]", e)

async def blogger_find(code: str):
    c = str(code or "").strip().upper()
    if not c:
        return None
    async with get_db() as db:
        row = await (await db.execute("SELECT code, name, bonus_ton FROM bloggers WHERE UPPER(code)=? AND active=1", (c,))).fetchone()
    if not row:
        return None
    return {"code": row[0], "name": row[1], "bonus_ton": float(row[2]) if row[2] is not None else BLOGGER_BONUS_TON}

async def blogger_attach(tg_id: int, code: str) -> dict:
    bl = await blogger_find(code)
    if not bl:
        return {"ok": False, "message": "Код не найден"}
    async with get_db() as db:
        had = await (await db.execute("SELECT code FROM blogger_users WHERE tg_id=?", (int(tg_id),))).fetchone()
        if had:
            return {"ok": True, "already": True, "message": "Код блогера уже активирован"}
        await db.execute("INSERT INTO blogger_users(tg_id,code,ts) VALUES(?,?,?) ON CONFLICT DO NOTHING", (int(tg_id), bl["code"], now_ts()))
        await db.commit()
    return {"ok": True, "message": f"Код {bl['code']} активирован — забери бонус на главной 🎁", "bonus": 0}

async def start_payload_save(tg_id: int, payload: str):
    p = str(payload or "").strip()[:64]
    async with get_db() as db:
        await db.execute("INSERT INTO start_payloads(tg_id,payload,ts,done) VALUES(?,?,?,0) ON CONFLICT (tg_id) DO UPDATE SET payload=excluded.payload, ts=excluded.ts, done=0",
                         (int(tg_id), p, now_ts()))
        await db.commit()

async def _apply_payload(tg_id: int, payload: str):
    p = str(payload or "").strip()
    if p.startswith("ref_"):
        try:
            rid = int(p[4:].split("_")[0])
        except Exception:
            return
        if rid and rid != tg_id:
            async with get_db() as db:
                ex = await (await db.execute("SELECT 1 FROM users WHERE tg_id=?", (rid,))).fetchone()
                had = await (await db.execute("SELECT 1 FROM referrals WHERE referred_id=?", (tg_id,))).fetchone()
                if ex and not had:
                    await db.execute("INSERT INTO referrals(referred_id,referrer_id,earned,created_at) VALUES(?,?,0,?) ON CONFLICT DO NOTHING", (tg_id, rid, now_ts()))
                    await db.commit()
    elif p.lower().startswith("bl_"):
        await blogger_attach(tg_id, p[3:])

async def start_landing_line(tg_id: int, lang_code: str = "") -> str:
    async with get_db() as db:
        row = await (await db.execute("SELECT payload FROM start_payloads WHERE tg_id=?", (int(tg_id),))).fetchone()
    p = str(row[0] if row else "")
    who = ""
    if p.startswith("ref_"):
        try:
            async with get_db() as db:
                r = await (await db.execute("SELECT username FROM users WHERE tg_id=?", (int(p[4:].split("_")[0]),))).fetchone()
            who = "@" + r[0] if r and r[0] else ""
        except Exception:
            who = ""
    bonus = WELCOME_BONUS_TON
    if p.lower().startswith("bl_"):
        bl = await blogger_find(p[3:])
        who = bl["name"] if bl else ""
        if bl:
            bonus = bl["bonus_ton"]
    if not who:
        return ""
    L = _bot_lang(lang_code)
    WELCOME_BONUS_TON_ = bonus
    T = {"ru": f"🎉 Тебя пригласил <b>{who}</b> — открой приложение и забери <b>+{WELCOME_BONUS_TON_:g} TON</b> на первый кейс!",
         "uk": f"🎉 Тебе запросив <b>{who}</b> — відкрий застосунок і забери <b>+{WELCOME_BONUS_TON_:g} TON</b> на перший кейс!",
         "uz": f"🎉 Seni <b>{who}</b> taklif qildi — ilovani och va birinchi keys uchun <b>+{WELCOME_BONUS_TON_:g} TON</b> ol!",
         "kk": f"🎉 Сені <b>{who}</b> шақырды — қосымшаны ашып, алғашқы кейске <b>+{WELCOME_BONUS_TON_:g} TON</b> ал!",
         "en": f"🎉 <b>{who}</b> invited you — open the app and grab <b>+{WELCOME_BONUS_TON_:g} TON</b> for your first case!"}
    return T.get(L, T["ru"])

def _bot_lang(code: str) -> str:
    c = str(code or "").lower()[:2]
    return {"uk": "uk", "uz": "uz", "kk": "kk", "en": "en", "ru": "ru", "be": "ru"}.get(c, "en" if c and c not in ("ru",) else "ru")

def start_text(lang_code: str, name: str, free_ready: bool, free_line_ru: str, tg_id: int) -> str:
    L = _bot_lang(lang_code)
    adm = GIFT_RELAYER or SUPPORT_USERNAME
    if L == "ru":
        return (f"👋 Привет, <b>{name}</b>!\n\n<b>Win Gifts</b> — кейсы с настоящими подарками Telegram.\n"
                f"🎯 Апгрейд, Plinko, Tower, ракетка, мины, PvP и не только.\n\n{free_line_ru}\n\n"
                f"💎 Пополнение: TON, ⭐ Stars или подарком (отправь @{adm} и напиши свой ID)\n🆔 Твой ID: <code>{tg_id}</code>\n\nЖми кнопку ниже 👇")
    F = {"uk": ("✅ <b>Безкоштовний кейс доступний просто зараз!</b>", "⏳ Безкоштовний кейс трохи згодом"),
         "uz": ("✅ <b>Bepul keys hozir mavjud!</b>", "⏳ Bepul keys birozdan so'ng"),
         "kk": ("✅ <b>Тегін кейс дәл қазір қолжетімді!</b>", "⏳ Тегін кейс сәл кейінірек"),
         "en": ("✅ <b>Your free case is ready right now!</b>", "⏳ Free case a bit later")}[L]
    fl = F[0] if free_ready else F[1]
    T = {"uk": f"👋 Привіт, <b>{name}</b>!\n\n<b>Win Gifts</b> — кейси зі справжніми подарунками Telegram.\n🎯 Апгрейд, Plinko, Tower, ракета, міни, PvP та інше.\n\n{fl}\n\n💎 Поповнення: TON, ⭐ Stars або подарунком (надішли @{adm} і напиши свій ID)\n🆔 Твій ID: <code>{tg_id}</code>\n\nТисни кнопку нижче 👇",
         "uz": f"👋 Salom, <b>{name}</b>!\n\n<b>Win Gifts</b> — haqiqiy Telegram sovg'alari bilan keyslar.\n🎯 Apgreyd, Plinko, Tower, raketa, minalar, PvP va boshqalar.\n\n{fl}\n\n💎 To'ldirish: TON, ⭐ Stars yoki sovg'a bilan (@{adm} ga yubor va ID'ingni yoz)\n🆔 Sening ID: <code>{tg_id}</code>\n\nPastdagi tugmani bos 👇",
         "kk": f"👋 Сәлем, <b>{name}</b>!\n\n<b>Win Gifts</b> — нағыз Telegram сыйлықтары бар кейстер.\n🎯 Апгрейд, Plinko, Tower, зымыран, миналар, PvP және т.б.\n\n{fl}\n\n💎 Толтыру: TON, ⭐ Stars немесе сыйлықпен (@{adm}-ға жібер де ID-іңді жаз)\n🆔 Сенің ID: <code>{tg_id}</code>\n\nТөмендегі батырманы бас 👇",
         "en": f"👋 Hi, <b>{name}</b>!\n\n<b>Win Gifts</b> — cases with real Telegram gifts.\n🎯 Upgrade, Plinko, Tower, rocket, mines, PvP and more.\n\n{fl}\n\n💎 Top up: TON, ⭐ Stars or a gift (send it to @{adm} with your ID)\n🆔 Your ID: <code>{tg_id}</code>\n\nTap the button below 👇"}
    return T[L]

# привязка рефки/блогера из /start при первом заходе в мини-апп
_cu_growth_orig = current_user
async def current_user(*args, **kwargs):  # noqa: F811
    u = await _cu_growth_orig(*args, **kwargs)
    try:
        async with get_db() as db:
            row = await (await db.execute("SELECT payload FROM start_payloads WHERE tg_id=? AND done=0", (int(u["tg_id"]),))).fetchone()
        if row:
            await _apply_payload(int(u["tg_id"]), row[0])
            async with get_db() as db:
                await db.execute("UPDATE start_payloads SET done=1 WHERE tg_id=?", (int(u["tg_id"]),))
                await db.commit()
    except Exception:
        pass
    return u

class BlAttach(BaseModel):
    code: str

@app.post("/api/v3/blogger/attach")
async def v3_bl_attach(body: BlAttach, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    c = str(body.code or "").strip()
    if c.lower().startswith("bl_"):
        c = c[3:]
    return await blogger_attach(int(u["tg_id"]), c)

# ---------- приветственный экран + бонус с проверкой «я не бот» ----------
EMO = ["🐸", "🎁", "💎", "🚀", "🍀", "🎃", "👑", "🔥", "⭐", "🍭"]

@app.get("/api/v3/welcome")
async def v3_welcome(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    tid = int(u["tg_id"])
    async with get_db() as db:
        cl = await (await db.execute("SELECT 1 FROM welcome_claims WHERE tg_id=?", (tid,))).fetchone()
        ref = await (await db.execute("SELECT r.referrer_id, u.username, COALESCE(u.photo_url,''), r.created_at FROM referrals r LEFT JOIN users u ON u.tg_id=r.referrer_id WHERE r.referred_id=?", (tid,))).fetchone()
        bl = await (await db.execute("SELECT b.code, b.name, bu.ts, b.bonus_ton FROM blogger_users bu JOIN bloggers b ON b.code=bu.code WHERE bu.tg_id=?", (tid,))).fetchone()
    if cl or (not ref and not bl):
        return {"show": False}
    joined = int((ref[3] if ref else 0) or (bl[2] if bl else 0) or 0)
    if joined and now_ts() - joined > 3 * 86400:
        return {"show": False}
    who = {"type": "blogger", "name": bl[1], "code": bl[0]} if bl else {"type": "ref", "name": "@" + (ref[1] or "друг"), "photo": ref[2]}
    opts = random.sample(EMO, 4)
    target = random.choice(opts)
    _CHALLENGE[tid] = (target, now_ts())
    bonus_ton = (float(bl[3]) if bl and bl[3] is not None else BLOGGER_BONUS_TON) if bl else WELCOME_BONUS_TON
    return {"show": True, "who": who, "bonus_ton": bonus_ton, "challenge": {"opts": opts, "target": target}}

class WelcomeClaim(BaseModel):
    pick: str
    fp: str = ""

@app.post("/api/v3/welcome/claim")
async def v3_welcome_claim(body: WelcomeClaim, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    tid = int(u["tg_id"])
    ch = _CHALLENGE.pop(tid, None)
    if not ch or now_ts() - ch[1] > 600:
        raise HTTPException(400, "Проверка устарела — открой приложение заново")
    if str(body.pick) != ch[0]:
        raise HTTPException(400, "Не тот значок — попробуй ещё раз")
    fp = str(body.fp or "")[:64]
    async with get_db() as db:
        if fp:
            await db.execute("INSERT INTO fps(tg_id,fp,ts) VALUES(?,?,?) ON CONFLICT (tg_id) DO UPDATE SET fp=excluded.fp, ts=excluded.ts", (tid, fp, now_ts()))
            same = await (await db.execute("SELECT COUNT(*) FROM welcome_claims w JOIN fps f ON f.tg_id=w.tg_id WHERE f.fp=?", (fp,))).fetchone()
            if same and int(same[0] or 0) >= 2:
                await db.commit()
                raise HTTPException(400, "С этого устройства бонус уже получали")
        had = await (await db.execute("SELECT 1 FROM welcome_claims WHERE tg_id=?", (tid,))).fetchone()
        if had:
            raise HTTPException(400, "Бонус уже получен")
        isbl = await (await db.execute("SELECT b.bonus_ton FROM blogger_users bu JOIN bloggers b ON b.code=bu.code WHERE bu.tg_id=?", (tid,))).fetchone()
        bt = (float(isbl[0]) if isbl[0] is not None else BLOGGER_BONUS_TON) if isbl else WELCOME_BONUS_TON
        stars = int(round(bt * float(STARS_PER_TON)))
        await db.execute("INSERT INTO welcome_claims(tg_id,amount,src,ts) VALUES(?,?,?,?)", (tid, stars, "blogger" if isbl else "ref", now_ts()))
        await db.execute("UPDATE users SET balance=COALESCE(balance,0)+? WHERE tg_id=?", (stars, tid))
        await db.commit()
        bal = await (await db.execute("SELECT balance FROM users WHERE tg_id=?", (tid,))).fetchone()
    await add_history(tid, "bonus", "win", "Приветственный бонус", stars)
    return {"ok": True, "stars": stars, "balance": int(bal[0] or 0) if bal else 0}

class FpReq(BaseModel):
    fp: str

@app.post("/api/v3/fp")
async def v3_fp(body: FpReq, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    fp = str(body.fp or "")[:64]
    if fp:
        async with get_db() as db:
            await db.execute("INSERT INTO fps(tg_id,fp,ts) VALUES(?,?,?) ON CONFLICT (tg_id) DO UPDATE SET fp=excluded.fp, ts=excluded.ts", (int(u["tg_id"]), fp, now_ts()))
            await db.commit()
    return {"ok": True}

# ---------- админка: блогеры ----------
class BlCreate(BaseModel):
    code: str
    name: str = ""
    contact: str = ""
    bonus_ton: Optional[float] = None
    rev_pct: float = 0
    rev_basis: str = "profit"   # profit = % от твоего дохода с его игроков, dep = % от их депозитов

@app.post("/api/v3/admin/blogger")
async def v3_adm_bl_create(body: BlCreate, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    code = re.sub(r"[^A-Z0-9_]", "", str(body.code or "").upper())[:24]
    if len(code) < 3:
        raise HTTPException(400, "Код минимум 3 символа (A-Z, 0-9)")
    async with get_db() as db:
        bt = None if body.bonus_ton is None else max(0.0, min(5.0, float(body.bonus_ton)))
        rp = max(0.0, min(80.0, float(body.rev_pct or 0)))
        rb = "dep" if body.rev_basis == "dep" else "profit"
        await db.execute("INSERT INTO bloggers(code,name,contact,created_at,active,bonus_ton,rev_pct,rev_basis) VALUES(?,?,?,?,1,?,?,?) "
                         "ON CONFLICT (code) DO UPDATE SET name=excluded.name, contact=excluded.contact, active=1, bonus_ton=excluded.bonus_ton, rev_pct=excluded.rev_pct, rev_basis=excluded.rev_basis",
                         (code, body.name or code, body.contact or "", now_ts(), bt, rp, rb))
        await db.commit()
    bot = BLOGGER_BOT
    return {"ok": True, "code": code, "link": f"https://t.me/{bot}?start=bl_{code}"}

@app.post("/api/v3/admin/blogger/delete")
async def v3_adm_bl_del(body: BlAttach, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    async with get_db() as db:
        await db.execute("UPDATE bloggers SET active=0 WHERE UPPER(code)=?", (str(body.code).upper(),))
        await db.commit()
    return {"ok": True}

@app.get("/api/v3/admin/bloggers")
async def v3_adm_bloggers(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    bot = BLOGGER_BOT
    out = []
    async with get_db() as db:
        bls = await (await db.execute("SELECT code, name, contact, created_at, bonus_ton, rev_pct, rev_basis, paid_ton FROM bloggers WHERE active=1 ORDER BY created_at DESC")).fetchall()
        for code, name, contact, ts, b_ton, r_pct, r_basis, paid in bls or []:
            r = await (await db.execute(
                "SELECT COUNT(*), SUM(CASE WHEN COALESCE(u.deposited,0)>0 THEN 1 ELSE 0 END), COALESCE(SUM(u.deposited),0) "
                "FROM blogger_users bu JOIN users u ON u.tg_id=bu.tg_id WHERE bu.code=?", (code,))).fetchone()
            w = await (await db.execute("SELECT COALESCE(SUM(v.wager),0) FROM blogger_users bu JOIN v3_state v ON v.tg_id=bu.tg_id WHERE bu.code=?", (code,))).fetchone()
            h = await (await db.execute(
                "SELECT COALESCE(SUM(h.amount),0) FROM blogger_users bu JOIN history h ON h.tg_id=bu.tg_id "
                "WHERE bu.code=? AND h.game NOT IN ('deposit','withdraw','bonus','shop') AND h.created_at>=bu.ts", (code,))).fetchone()
            d7 = await (await db.execute("SELECT COUNT(*) FROM blogger_users WHERE code=? AND ts>?", (code, now_ts() - 7 * 86400))).fetchone()
            users = int(r[0] or 0); dep_users = int(r[1] or 0)
            dep_ton = int(r[2] or 0) / float(STARS_PER_TON); prof_ton = -int(h[0] or 0) / float(STARS_PER_TON)
            r_pct = float(r_pct or 0); base = dep_ton if (r_basis or "profit") == "dep" else max(0.0, prof_ton)
            earned = round(base * r_pct / 100.0, 2); paid = float(paid or 0)
            out.append({"code": code, "name": name, "contact": contact, "link": f"https://t.me/{bot}?start=bl_{code}",
                        "bonus_ton": float(b_ton) if b_ton is not None else BLOGGER_BONUS_TON, "rev_pct": r_pct, "rev_basis": r_basis or "profit",
                        "earned_ton": earned, "paid_ton": round(paid, 2), "due_ton": round(max(0.0, earned - paid), 2),
                        "users": users, "users_7d": int(d7[0] or 0), "depositors": dep_users,
                        "conv": round(dep_users / users * 100, 1) if users else 0,
                        "deposited_ton": round(int(r[2] or 0) / float(STARS_PER_TON), 2),
                        "wager_ton": round(int(w[0] or 0) / float(STARS_PER_TON), 2),
                        "profit_ton": round(-int(h[0] or 0) / float(STARS_PER_TON), 2)})
    return {"items": out}

# ---------- админка: антифрод ----------
@app.get("/api/v3/admin/fraud")
async def v3_adm_fraud(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    out = {"burst": [], "devices": [], "empty_refs": []}
    async with get_db() as db:
        rows = await (await db.execute(
            "SELECT r.referrer_id, u.username, COUNT(*) FROM referrals r LEFT JOIN users u ON u.tg_id=r.referrer_id WHERE r.created_at>? GROUP BY r.referrer_id, u.username HAVING COUNT(*)>=?",
            (now_ts() - 3600, int(os.getenv("FRAUD_REFS_HOUR") or 10)))).fetchall()
        out["burst"] = [{"tg_id": r[0], "username": r[1] or "", "refs_hour": int(r[2])} for r in rows or []]
        rows = await (await db.execute("SELECT fp, COUNT(*) FROM fps GROUP BY fp HAVING COUNT(*)>=3 ORDER BY COUNT(*) DESC LIMIT 30")).fetchall()
        for fp, n in rows or []:
            ids = await (await db.execute("SELECT f.tg_id, u.username FROM fps f LEFT JOIN users u ON u.tg_id=f.tg_id WHERE f.fp=? LIMIT 12", (fp,))).fetchall()
            out["devices"].append({"fp": fp[:10], "count": int(n), "users": [{"tg_id": i[0], "username": i[1] or ""} for i in ids or []]})
        rows = await (await db.execute(
            "SELECT r.referrer_id, u2.username, COUNT(*), SUM(CASE WHEN COALESCE(u.deposited,0)>0 THEN 1 ELSE 0 END) "
            "FROM referrals r JOIN users u ON u.tg_id=r.referred_id LEFT JOIN users u2 ON u2.tg_id=r.referrer_id "
            "GROUP BY r.referrer_id, u2.username HAVING COUNT(*)>=15 ORDER BY COUNT(*) DESC LIMIT 30")).fetchall()
        out["empty_refs"] = [{"tg_id": r[0], "username": r[1] or "", "refs": int(r[2]), "depositors": int(r[3] or 0)} for r in rows or [] if int(r[3] or 0) / max(1, int(r[2])) < 0.05]
    return out


class BlPaid(BaseModel):
    code: str
    ton: float

@app.post("/api/v3/admin/blogger/paid")
async def v3_adm_bl_paid(body: BlPaid, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    async with get_db() as db:
        await db.execute("UPDATE bloggers SET paid_ton=COALESCE(paid_ton,0)+? WHERE UPPER(code)=?", (float(body.ton or 0), str(body.code).upper()))
        await db.commit()
    return {"ok": True}

# ---------- утренний отчёт админу ----------
async def daily_report_text() -> str:
    since = now_ts() - 86400
    spt = float(STARS_PER_TON)
    async with get_db() as db:
        q = lambda sql, a=(): db.execute(sql, a)
        try:
            newu = int((await (await q("SELECT COUNT(*) FROM (SELECT tg_id, MIN(created_at) m FROM history GROUP BY tg_id) t WHERE t.m>=?", (since,))).fetchone())[0] or 0)
        except Exception:
            newu = 0
        act = int((await (await q("SELECT COUNT(DISTINCT tg_id) FROM history WHERE created_at>=?", (since,))).fetchone())[0] or 0)
        try:
            ds = await (await q("SELECT COALESCE(SUM(amount),0), COUNT(*) FROM deposits WHERE paid=1 AND created_at>=?", (since,))).fetchone()
        except Exception:
            ds = (0, 0)
        try:
            dt = await (await q("SELECT COALESCE(SUM(got_ton),0), COUNT(*) FROM ton_invoices WHERE credited=1 AND created_at>=?", (since,))).fetchone()
        except Exception:
            dt = (0, 0)
        try:
            dg = await (await q("SELECT COALESCE(SUM(value),0), COUNT(*) FROM gift_deposits WHERE status='credited' AND ts>=?", (since,))).fetchone()
        except Exception:
            dg = (0, 0)
        g = await (await q("SELECT game, COALESCE(SUM(amount),0) FROM history WHERE created_at>=? AND game NOT IN ('deposit','withdraw','bonus','shop') GROUP BY game", (since,))).fetchall()
        try:
            wd = await (await q("SELECT COUNT(*) FROM withdrawals WHERE status='pending'")).fetchone()
        except Exception:
            wd = (0,)
        try:
            bl = await (await q("SELECT bu.code, COUNT(*) FROM blogger_users bu WHERE bu.ts>=? GROUP BY bu.code ORDER BY COUNT(*) DESC LIMIT 1", (since,))).fetchone()
        except Exception:
            bl = None
    house = -sum(int(x[1] or 0) for x in g or []) / spt
    games = sorted([(x[0], -int(x[1] or 0) / spt) for x in g or []], key=lambda x: -abs(x[1]))[:5]
    lines = [f"☀️ <b>Отчёт за сутки</b>",
             f"👤 Новых игроков: <b>{newu}</b> · активных: <b>{act}</b>",
             f"💎 Депозиты: <b>{round(float(dt[0] or 0), 2)} TON</b> ({int(dt[1] or 0)}) · <b>{int(ds[0] or 0)}⭐</b> ({int(ds[1] or 0)}) · подарками <b>{round(int(dg[0] or 0) / spt, 2)} TON</b> ({int(dg[1] or 0)})",
             f"🏦 Касса по играм: <b>{'+' if house >= 0 else ''}{round(house, 2)} TON</b>"]
    if games:
        lines.append("   " + " · ".join(f"{n}: {'+' if v >= 0 else ''}{round(v, 1)}" for n, v in games))
    lines.append(f"📤 Выводов ждут: <b>{int(wd[0] or 0)}</b>")
    if bl:
        lines.append(f"🎬 Блогер дня: <b>{bl[0]}</b> · +{int(bl[1])} игроков")
    return "\n".join(lines)

async def _report_loop():
    await asyncio.sleep(90)
    sent_day = 0
    while True:
        try:
            t = datetime.utcnow()
            hour = int(os.getenv("REPORT_HOUR_UTC") or 6)   # 06:00 UTC = 09:00 МСК
            if t.hour == hour and sent_day != t.toordinal() and ADMIN_TG_ID:
                await tg_send(int(ADMIN_TG_ID), await daily_report_text())
                sent_day = t.toordinal()
        except Exception as e:
            print("[report]", e)
        await asyncio.sleep(240)

@app.on_event("startup")
async def _report_start():
    asyncio.create_task(_report_loop())

@app.post("/api/v3/admin/report")
async def v3_admin_report(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    me = await _adm(authorization, x_telegram_init_data)
    txt = await daily_report_text()
    await tg_send(int(me["tg_id"]), txt)
    return {"ok": True}


# =====================================================================
#  СКОРОСТЬ: фоновые побочные действия, меньше запросов, пачка кейсов
# =====================================================================
def _bg(coro):
    async def run():
        try:
            await coro
        except Exception as e:
            print("[bg]", e)
    try:
        asyncio.create_task(run())
    except Exception:
        pass

# уведомления, журнал, квесты, недельный топ, история — не задерживают ответ игроку
_tg_notify_sync = tg_notify
async def tg_notify(tg_id: int, text: str):  # noqa: F811
    _bg(_tg_notify_sync(tg_id, text)); return True
_tg_push_sync = tg_push
async def tg_push(tg_id: int, text: str, btn: str = "🎁 Открыть", photo_url: str = None):  # noqa: F811
    _bg(_tg_push_sync(tg_id, text, btn, photo_url)); return True
_ledger_sync = ledger
async def ledger(kind: str, amount: int, tg_id: int = 0, detail: str = ""):  # noqa: F811
    _bg(_ledger_sync(kind, amount, tg_id, detail))
_bump_weekly_sync = bump_weekly
async def bump_weekly(tg_id: int, username: str, points: int):  # noqa: F811
    _bg(_bump_weekly_sync(tg_id, username, points))
_bump_quest_sync = bump_quest
async def bump_quest(tg_id: int, quest_id: str, n: int = 1):  # noqa: F811
    _bg(_bump_quest_sync(tg_id, quest_id, n))
_push_live_sync = push_live
async def push_live(item: dict, user_name: str):  # noqa: F811
    _bg(_push_live_sync(item, user_name))
_add_history_sync = add_history
async def add_history(tg_id: int, game: str, result: str, detail: str, amount: int = 0):  # noqa: F811
    _bg(_add_history_sync(tg_id, game, result, detail, amount))

# оборот/ранг: одно чтение вместо трёх, запись в фоне
async def v3_add_wager(tg_id: int, stars: int, mult: float = 0.0):  # noqa: F811
    async def run():
        try:
            st = await _v3_load(tg_id)
            before = _v3_rank_view(st)["rank_index"]
            st["wager"] = int(st["wager"]) + max(0, int(stars or 0))
            if mult and mult > st["best_mult"]:
                st["best_mult"] = float(mult)
            await _v3_save(tg_id, st)
            v = _v3_rank_view(st)
            if v["rank_index"] > before:
                r = v["rank"]; rw = V3_RANKS[v["rank_index"]][4]
                await _tg_push_sync(tg_id, f"🎉 Новый ранг: {r['icon']} <b>{r['name']}</b>!\nЗабери награду <b>+{round(rw / float(STARS_PER_TON), 2)} TON</b> в профиле.", "🏆 Забрать")
        except Exception as e:
            print("[wager]", e)
    _bg(run())

# привязка /start-payload: проверяем пользователя один раз за жизнь процесса, а не на каждый запрос
_PAYLOAD_CHECKED: set = set()
_cu_fast_inner = current_user
async def current_user(*args, **kwargs):  # noqa: F811
    return await _cu_fast_inner(*args, **kwargs)
async def _cu_growth_fast(*args, **kwargs):
    u = await _cu_growth_orig(*args, **kwargs)
    tid = int(u["tg_id"])
    if tid not in _PAYLOAD_CHECKED:
        _PAYLOAD_CHECKED.add(tid)
        try:
            async with get_db() as db:
                row = await (await db.execute("SELECT payload FROM start_payloads WHERE tg_id=? AND done=0", (tid,))).fetchone()
            if row:
                await _apply_payload(tid, row[0])
                async with get_db() as db:
                    await db.execute("UPDATE start_payloads SET done=1 WHERE tg_id=?", (tid,))
                    await db.commit()
        except Exception:
            pass
    return u
current_user = _cu_growth_fast  # noqa: F811

# после нового /start — проверить пользователя заново
_start_payload_save_orig = start_payload_save
async def start_payload_save(tg_id: int, payload: str):  # noqa: F811
    _PAYLOAD_CHECKED.discard(int(tg_id))
    await _start_payload_save_orig(tg_id, payload)

# ---------- открыть несколько кейсов одним запросом ----------
class CaseOpenMany(BaseModel):
    case_id: str
    count: int = 1
    promo_code: Optional[str] = None

@app.post("/api/v3/case/open_many")
async def v3_case_open_many(body: CaseOpenMany, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    n = max(1, min(5, int(body.count or 1)))
    out, err = [], None
    for _ in range(n):
        try:
            r = await open_case(CaseOpenRequest(case_id=body.case_id, promo_code=body.promo_code), authorization, x_telegram_init_data)
            out.append(r)
        except HTTPException as e:
            err = e.detail
            break
    if not out:
        raise HTTPException(400, err or "Не удалось открыть")
    return {"ok": True, "results": out, "error": err}


# =====================================================================
#  СКОРОСТЬ v2: индексы БД · кеш общих ответов · замер медленных запросов
# =====================================================================
_INDEXES = [
    ("ix_hist_tg_ts", "history", "tg_id, created_at"),
    ("ix_hist_ts", "history", "created_at"),
    ("ix_hist_game_ts", "history", "game, created_at"),
    ("ix_ref_referrer", "referrals", "referrer_id"),
    ("ix_ref_ts", "referrals", "created_at"),
    ("ix_live_user", "live_drops", "user_name"),
    ("ix_live_val", "live_drops", "value"),
    ("ix_wd_status", "withdrawals", "status"),
    ("ix_wd_tg", "withdrawals", "tg_id"),
    ("ix_dep_tg", "deposits", "tg_id"),
    ("ix_dep_paid_ts", "deposits", "paid, created_at"),
    ("ix_tinv_tg", "ton_invoices", "tg_id, credited"),
    ("ix_tinv_hash", "ton_invoices", "tx_hash"),
    ("ix_gdep_tg", "gift_deposits", "tg_id"),
    ("ix_blu_code", "blogger_users", "code"),
    ("ix_fps_fp", "fps", "fp"),
    ("ix_quests_tg", "quests", "tg_id, quest_id"),
    ("ix_users_uname", "users", "username"),
    ("ix_v3_wager", "v3_state", "wager"),
    ("ix_chat_del", "chat_msgs", "deleted, id"),
]

async def _make_indexes():
    made = 0
    for name, table, cols in _INDEXES:
        try:
            async with get_db() as db:
                await db.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({cols})")
                await db.commit()
            made += 1
        except Exception:
            pass   # таблицы может ещё не быть — создастся при следующем старте
    print(f"[indexes] ok {made}/{len(_INDEXES)}")

@app.on_event("startup")
async def _speed_start():
    async def later():
        await asyncio.sleep(20)   # после того как все таблицы созданы
        await _make_indexes()
    asyncio.create_task(later())

# ---------- кеш общих GET-ответов (одинаковых для всех игроков) ----------
_RESP_CACHE: Dict[str, tuple] = {}
_CACHE_TTL = {"/api/cases": 30, "/api/gifts": 20, "/api/rates": 30, "/api/live": 4, "/api/v3/live": 5,
              "/api/leaders": 20, "/api/maintenance": 5, "/api/ton/config": 60}
_PERF: Dict[str, list] = {}   # path -> [count, total_ms, max_ms]
_ALERT_ERR: List[float] = []

def _perf_key(path: str) -> str:
    p = re.sub(r"/\d+", "/:id", path)
    p = re.sub(r"/api/v3/(nft|nft_anim)/[^/]+", r"/api/v3/\1/:slug", p)
    return p

_USER_LOCKS: Dict[str, asyncio.Lock] = {}

def _user_lock_for(scope, path: str):
    if scope.get("method") != "POST" or not path.startswith("/api/") or "webhook" in path:
        return None
    hdrs = dict(scope.get("headers") or [])
    raw = (hdrs.get(b"x-telegram-init-data") or hdrs.get(b"authorization") or b"").decode("utf-8", "ignore")
    if not raw:
        return None
    m = re.search(r"%22id%22%3A(\d+)|\"id\":(\d+)", raw)
    key = (m.group(1) or m.group(2)) if m else raw[-40:]
    lk = _USER_LOCKS.get(key)
    if lk is None:
        if len(_USER_LOCKS) > 20000:
            for k in list(_USER_LOCKS)[:5000]:
                if not _USER_LOCKS[k].locked():
                    _USER_LOCKS.pop(k, None)
        lk = _USER_LOCKS[key] = asyncio.Lock()
    return lk

class SpeedMiddleware:
    def __init__(self, app_):
        self.app = app_
    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path") or ""
        t0 = time.perf_counter()
        ttl = _CACHE_TTL.get(path) if scope.get("method") == "GET" else None
        key = None
        if ttl and not MAINTENANCE_MODE.get("on"):
            qs = (scope.get("query_string") or b"").decode()
            hdrs = dict(scope.get("headers") or [])
            gz = b"gzip" in (hdrs.get(b"accept-encoding") or b"")
            key = f"{path}?{qs}|{gz}"
            hit = _RESP_CACHE.get(key)
            if hit and time.time() - hit[0] < ttl:
                await send({"type": "http.response.start", "status": 200, "headers": hit[1]})
                await send({"type": "http.response.body", "body": hit[2]})
                return
        status_holder = {"s": 200, "h": None, "b": []}
        async def send2(msg):
            if msg.get("type") == "http.response.start" and int(msg.get("status", 200)) >= 500 and path.startswith("/api/"):
                _ALERT_ERR.append(time.time())
            if key:
                if msg["type"] == "http.response.start":
                    status_holder["s"] = msg.get("status", 200); status_holder["h"] = msg.get("headers") or []
                elif msg["type"] == "http.response.body":
                    status_holder["b"].append(msg.get("body") or b"")
                    if not msg.get("more_body") and status_holder["s"] == 200:
                        _RESP_CACHE[key] = (time.time(), status_holder["h"], b"".join(status_holder["b"]))
                        if len(_RESP_CACHE) > 300:
                            _RESP_CACHE.pop(next(iter(_RESP_CACHE)))
            await send(msg)
        try:
            lk = _user_lock_for(scope, path)
            if lk is not None:
                async with lk:          # действия одного игрока выполняются строго по очереди — нет двойных начислений
                    await self.app(scope, receive, send2)
            else:
                await self.app(scope, receive, send2)
        finally:
            if path.startswith("/api/"):
                ms = (time.perf_counter() - t0) * 1000
                k = _perf_key(path)
                st = _PERF.setdefault(k, [0, 0.0, 0.0])
                st[0] += 1; st[1] += ms; st[2] = max(st[2], ms)

app.add_middleware(SpeedMiddleware)

@app.get("/api/v3/admin/perf")
async def v3_admin_perf(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    t = time.perf_counter()
    async with get_db() as db:
        await (await db.execute("SELECT 1")).fetchone()
    db_ms = round((time.perf_counter() - t) * 1000, 1)
    rows = sorted(([k, v[0], round(v[1] / max(1, v[0]), 1), round(v[2], 1)] for k, v in _PERF.items()), key=lambda r: -r[2])[:25]
    return {"db_ping_ms": db_ms, "db": "postgres" if USE_POSTGRES else "sqlite",
            "items": [{"path": r[0], "n": r[1], "avg_ms": r[2], "max_ms": r[3]} for r in rows]}

@app.post("/api/v3/admin/perf/reset")
async def v3_admin_perf_reset(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    _PERF.clear()
    return {"ok": True}


# ---------- прогрев анимаций: модели #1 и #2 каждого NFT грузятся заранее ----------
async def _nft_prewarm():
    await asyncio.sleep(60)
    import httpx
    names = [g.get("name") or "" for g in GIFTS_FLAT if is_real_gift(g.get("name") or "")]
    slugs = []
    for n in names:
        b = re.sub(r"[^a-z0-9]", "", n.lower().replace("’", "").replace("'", ""))
        if b and not re.search(r"[а-яё]", n.lower()):
            for k in (1, 2):
                slugs.append(f"{b}-{k}")
    ok = 0
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as c:
        for key in slugs:
            if key in _NFT_ANIM:
                continue
            try:
                r = await c.get(f"https://nft.fragment.com/gift/{key}.lottie.json")
                if r.status_code == 200:
                    _NFT_ANIM[key] = r.content; ok += 1
            except Exception:
                pass
            await asyncio.sleep(0.4)
    print(f"[nft prewarm] {ok}/{len(slugs)}")

@app.on_event("startup")
async def _nft_prewarm_start():
    asyncio.create_task(_nft_prewarm())


# =====================================================================
#  БОНУС К ПЕРВОМУ ПОПОЛНЕНИЮ (+20%, отыгрыш ×3 перед выводом подарков)
# =====================================================================
FIRST_DEP_PCT = float(os.getenv("FIRST_DEP_PCT") or 20)
FIRST_DEP_CAP_TON = float(os.getenv("FIRST_DEP_CAP_TON") or 50)
FIRST_DEP_WAGER_X = float(os.getenv("FIRST_DEP_WAGER_X") or 3)

async def _fb_ensure():
    async with get_db() as db:
        await db.execute("CREATE TABLE IF NOT EXISTS first_bonus (tg_id BIGINT PRIMARY KEY, bonus BIGINT DEFAULT 0, need_wager BIGINT DEFAULT 0, "
                         "wager_at BIGINT DEFAULT 0, ts BIGINT DEFAULT 0)")
        await db.commit()

@app.on_event("startup")
async def _fb_start():
    try:
        await _fb_ensure()
        # кто уже пополнял до запуска акции — бонус «первого» депозита не положен
        async with get_db() as db:
            await db.execute("INSERT INTO first_bonus(tg_id,bonus,need_wager,wager_at,ts) SELECT tg_id,0,0,0,0 FROM users "
                             "WHERE COALESCE(deposited,0)>0 ON CONFLICT DO NOTHING")
            await db.commit()
    except Exception as e:
        print("[first bonus init]", e)

async def first_bonus_grant(tg_id: int, deposit_stars: int):
    if FIRST_DEP_PCT <= 0 or deposit_stars <= 0:
        return
    await _fb_ensure()
    async with get_db() as db:
        had = await (await db.execute("SELECT 1 FROM first_bonus WHERE tg_id=?", (int(tg_id),))).fetchone()
        if had:
            return
        bonus = int(round(min(deposit_stars * FIRST_DEP_PCT / 100.0, FIRST_DEP_CAP_TON * float(STARS_PER_TON))))
        st = await _v3_load(int(tg_id))
        need = int(round(bonus * FIRST_DEP_WAGER_X))
        await db.execute("INSERT INTO first_bonus(tg_id,bonus,need_wager,wager_at,ts) VALUES(?,?,?,?,?) ON CONFLICT DO NOTHING",
                         (int(tg_id), bonus, need, int(st["wager"]), now_ts()))
        await db.execute("UPDATE users SET balance=COALESCE(balance,0)+? WHERE tg_id=?", (bonus, int(tg_id)))
        await db.commit()
    await add_history(int(tg_id), "bonus", "win", f"Бонус к первому пополнению +{FIRST_DEP_PCT:g}%", bonus)
    await tg_push(int(tg_id), f"🎁 <b>Бонус к первому пополнению: +{round(bonus / float(STARS_PER_TON), 2)} TON</b> (+{FIRST_DEP_PCT:g}%)!\n"
                              f"Чтобы вывести подарки, сделай оборот {round(need / float(STARS_PER_TON), 2)} TON в любых играх.", "🎰 Играть")

async def first_bonus_left(tg_id: int) -> int:
    """Сколько ⭐ оборота осталось отыграть (0 — можно выводить)."""
    try:
        async with get_db() as db:
            r = await (await db.execute("SELECT bonus, need_wager, wager_at FROM first_bonus WHERE tg_id=?", (int(tg_id),))).fetchone()
        if not r or int(r[0] or 0) <= 0:
            return 0
        st = await _v3_load(int(tg_id))
        return max(0, int(r[1] or 0) - (int(st["wager"]) - int(r[2] or 0)))
    except Exception:
        return 0

_pay_referral_orig = pay_referral
async def pay_referral(referred_tg_id: int, deposit_stars: int):  # noqa: F811
    await _pay_referral_orig(referred_tg_id, deposit_stars)
    try:
        await first_bonus_grant(int(referred_tg_id), int(deposit_stars or 0))
    except Exception as e:
        print("[first bonus]", e)

@app.get("/api/v3/first_bonus")
async def v3_first_bonus(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    u = await current_user(authorization, x_telegram_init_data)
    await _fb_ensure()
    async with get_db() as db:
        r = await (await db.execute("SELECT bonus, need_wager, wager_at FROM first_bonus WHERE tg_id=?", (int(u["tg_id"]),))).fetchone()
    out = {"pct": FIRST_DEP_PCT, "cap_ton": FIRST_DEP_CAP_TON, "x": FIRST_DEP_WAGER_X, "eligible": not r and FIRST_DEP_PCT > 0}
    if r and int(r[0] or 0) > 0:
        st = await _v3_load(int(u["tg_id"]))
        done = max(0, int(st["wager"]) - int(r[2] or 0))
        out["active"] = {"bonus": int(r[0]), "need": int(r[1]), "done": min(done, int(r[1])), "left": max(0, int(r[1]) - done)}
    return out

# =====================================================================
#  СИГНАЛЫ АДМИНУ: ошибки сервера, минус кассы, подозрительный игрок
# =====================================================================
ALERT_ERRORS = int(os.getenv("ALERT_ERRORS") or 20)            # ошибок 5xx за 10 минут
ALERT_LOSS_TON = float(os.getenv("ALERT_LOSS_TON") or 50)       # касса в минусе за час
ALERT_PLAYER_TON = float(os.getenv("ALERT_PLAYER_TON") or 30)   # один игрок в плюсе за час
_ALERT_LAST: Dict[str, float] = {}

async def _alert(key: str, text: str, every_sec: int):
    if time.time() - _ALERT_LAST.get(key, 0) < every_sec or not ADMIN_TG_ID:
        return
    _ALERT_LAST[key] = time.time()
    try:
        await tg_send(int(ADMIN_TG_ID), "🚨 " + text)
    except Exception as e:
        print("[alert]", e)

async def _alerts_loop():
    await asyncio.sleep(120)
    spt = float(STARS_PER_TON)
    while True:
        try:
            cut = time.time() - 600
            while _ALERT_ERR and _ALERT_ERR[0] < cut:
                _ALERT_ERR.pop(0)
            if len(_ALERT_ERR) >= ALERT_ERRORS:
                await _alert("errors", f"<b>Сервер сыпет ошибками</b>: {len(_ALERT_ERR)} ошибок за 10 минут. Проверь логи Render.", 1800)
            since = now_ts() - 3600
            async with get_db() as db:
                r = await (await db.execute("SELECT COALESCE(SUM(amount),0) FROM history WHERE created_at>=? AND game NOT IN ('deposit','withdraw','bonus','shop')", (since,))).fetchone()
                tops = await (await db.execute(
                    "SELECT h.tg_id, u.username, SUM(h.amount), COUNT(*) FROM history h LEFT JOIN users u ON u.tg_id=h.tg_id "
                    "WHERE h.created_at>=? AND h.game NOT IN ('deposit','withdraw','bonus','shop') GROUP BY h.tg_id, u.username "
                    "HAVING SUM(h.amount)>? ORDER BY SUM(h.amount) DESC LIMIT 3", (since, int(ALERT_PLAYER_TON * spt)))).fetchall()
            players_net = int(r[0] or 0) / spt
            if players_net >= ALERT_LOSS_TON:
                await _alert("loss", f"<b>Касса в минусе</b>: игроки за последний час выиграли <b>+{round(players_net, 2)} TON</b> сверх ставок.\n"
                                     f"Глянь «Админка → Обзор»; если похоже на баг — включи тех.работы.", 3600)
            for tid, un, amt, n in tops or []:
                await _alert(f"pl:{tid}", f"<b>Подозрительно везёт</b>: @{un or '—'} (<code>{tid}</code>) за час <b>+{round(int(amt or 0) / spt, 2)} TON</b> за {int(n)} игр.\n"
                                          f"Проверь в «Админка → Игроки» перед выдачей выводов.", 6 * 3600)
        except Exception as e:
            print("[alerts loop]", e)
        await asyncio.sleep(300)

@app.on_event("startup")
async def _alerts_start():
    asyncio.create_task(_alerts_loop())


# =====================================================================
#  ОБЫЧНЫЕ ПОДАРКИ TELEGRAM: официальные стикеры через Bot API
#  getAvailableGifts → картинка + анимация каждого обычного подарка,
#  новые обычные подарки сами добавляются в каталог
# =====================================================================
_SG: Dict[str, dict] = {}          # id -> {"stars","emoji","name","thumb":bytes,"anim":bytes,"tmime"}
_SG_STATE = {"ts": 0, "count": 0, "error": ""}
SG_NAMES = {"🧸": "Мишка", "❤": "Сердце", "🎁": "Подарок", "🌹": "Роза", "🎂": "Торт", "💐": "Букет", "🚀": "Ракета",
            "🍾": "Шампанское", "🏆": "Кубок", "💍": "Кольцо", "💎": "Алмаз", "🎄": "Ёлка", "🌷": "Цветы", "🍭": "Леденец",
            "⚽": "Футбольный мишка", "🐰": "Пасхальный мишка", "🍀": "Мишка-лепрекон", "🤡": "Мишка-клоун", "🏗": "Мишка-строитель",
            "💘": "Валентинка", "⭐": "Звезда", "🍬": "Конфета", "🎃": "Тыква", "🎅": "Новогодний мишка"}

def _sg_name(emoji: str, stars: int, taken: set) -> str:
    base = SG_NAMES.get((emoji or "").replace("\ufe0f", ""), "Подарок " + (emoji or "🎁"))
    name = base if base not in taken else f"{base} {stars}⭐"
    k = 2
    while name in taken:
        name = f"{base} #{k}"; k += 1
    return name

async def stargifts_refresh() -> dict:
    if not BOT_TOKEN:
        _SG_STATE["error"] = "нет BOT_TOKEN"
        return dict(_SG_STATE)
    import httpx, gzip
    api = f"https://api.telegram.org/bot{BOT_TOKEN}"
    try:
        async with httpx.AsyncClient(timeout=25.0) as c:
            r = await c.get(api + "/getAvailableGifts")
            gifts = ((r.json() or {}).get("result") or {}).get("gifts") or []
            taken: set = set()
            # имена: сначала самые дешёвые, чтобы «Мишка» достался классическому за 15⭐
            gifts = sorted(gifts, key=lambda g: (int(g.get("star_count") or 0), str(g.get("id"))))
            for g in gifts:
                gid = str(g.get("id"))
                st = g.get("sticker") or {}
                emoji = st.get("emoji") or "🎁"
                stars = int(g.get("star_count") or 0)
                if g.get("upgrade_star_count") and False:
                    pass
                name = _sg_name(emoji, stars, taken); taken.add(name)
                rec = _SG.get(gid) or {}
                rec.update({"stars": stars, "emoji": emoji, "name": name})
                async def dl(file_id):
                    fr = await c.get(api + "/getFile", params={"file_id": file_id})
                    fp = ((fr.json() or {}).get("result") or {}).get("file_path")
                    if not fp:
                        return None, ""
                    b = await c.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{fp}")
                    return (b.content if b.status_code == 200 else None), fp
                try:
                    if not rec.get("thumb") and (st.get("thumbnail") or {}).get("file_id"):
                        data, fp = await dl(st["thumbnail"]["file_id"])
                        if data:
                            rec["thumb"] = data
                            rec["tmime"] = "image/webp" if fp.endswith(".webp") else "image/jpeg"
                    if not rec.get("anim") and st.get("file_id") and st.get("is_animated"):
                        data, fp = await dl(st["file_id"])
                        if data:
                            try:
                                rec["anim"] = gzip.decompress(data)   # .tgs = gzip(lottie json)
                            except Exception:
                                rec["anim"] = data
                except Exception as e:
                    print("[stargift dl]", gid, e)
                _SG[gid] = rec
                await asyncio.sleep(0.05)
        _sg_apply_catalog()
        _SG_STATE.update({"ts": now_ts(), "count": len(_SG), "error": ""})
        print(f"[stargifts] {len(_SG)} обычных подарков")
    except Exception as e:
        _SG_STATE["error"] = str(e)[:200]
        print("[stargifts]", e)
    return dict(_SG_STATE)

def _sg_apply_catalog():
    """Привязываем официальные картинки к обычным подаркам каталога и добавляем новые."""
    by_name = {(g.get("name") or "").lower(): g for g in GIFTS_FLAT}
    for gid, rec in _SG.items():
        img = f"/api/v3/sg/{gid}.img"
        key = rec["name"].lower()
        g = by_name.get(key)
        if g:
            g["img"] = img; g["sg"] = gid
            if not int(g.get("value") or 0):
                g["value"] = rec["stars"]
        else:
            g = {"name": rec["name"], "value": int(rec["stars"]), "rarity": "Common", "emoji": rec["emoji"], "img": img, "sn": "", "sg": gid}
            GIFTS_FLAT.append(g)
            by_name[key] = g
        gp = gift_public(g); gp["img"] = img; gp["sg"] = gid
        _BY_NAME[key] = gp
        if gp not in _BY_RARITY.setdefault(gp.get("rarity") or "Common", []):
            _BY_RARITY[gp.get("rarity") or "Common"] = [x for x in _BY_RARITY.get(gp.get("rarity") or "Common", []) if (x.get("name") or "").lower() != key] + [gp]

@app.get("/api/v3/sg/{gid}.img")
async def v3_sg_img(gid: str):
    from fastapi.responses import Response
    rec = _SG.get(str(gid))
    if not rec or not rec.get("thumb"):
        raise HTTPException(404, "no image")
    return Response(rec["thumb"], media_type=rec.get("tmime") or "image/webp", headers={"Cache-Control": "public, max-age=604800"})

@app.get("/api/v3/sg/{gid}.json")
async def v3_sg_anim(gid: str):
    from fastapi.responses import Response
    rec = _SG.get(str(gid))
    if not rec or not rec.get("anim"):
        raise HTTPException(404, "no anim")
    return Response(rec["anim"], media_type="application/json", headers={"Cache-Control": "public, max-age=604800"})

@app.get("/api/v3/stargifts")
async def v3_stargifts():
    return {"state": _SG_STATE, "items": [{"id": k, "name": v["name"], "stars": v["stars"], "emoji": v["emoji"],
             "img": f"/api/v3/sg/{k}.img", "anim": f"/api/v3/sg/{k}.json" if v.get("anim") else None} for k, v in _SG.items()]}

@app.post("/api/v3/admin/stargifts/refresh")
async def v3_admin_sg_refresh(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    return await stargifts_refresh()

async def _sg_loop():
    await asyncio.sleep(15)
    while True:
        await stargifts_refresh()
        await asyncio.sleep(6 * 3600)

@app.on_event("startup")
async def _sg_start():
    asyncio.create_task(_sg_loop())


# =====================================================================
#  RANDOM-КЕЙС: 1000⭐, содержимое меняется каждый день (00:00 UTC).
#  Баланс тот же, что у всех: возврат CASE_RTP (90%), топ-приз ≤ ×40.
# =====================================================================
_RANDOM_DAY = {"day": None}

def _random_daily_table(price: int, day: int) -> list:
    rnd = random.Random(day * 7919 + 17)
    pool = [g for g in GIFTS_FLAT if is_real_gift(g.get("name") or "") and int(g.get("value") or 0) > 0]
    cheap = [g for g in pool if 0.15 * price <= int(g["value"]) < 0.8 * price]
    mid = [g for g in pool if 0.8 * price <= int(g["value"]) < 3 * price]
    high = [g for g in pool if 3 * price <= int(g["value"]) <= 40 * price]
    pick = []
    for grp, k in ((cheap, 5), (mid, 4), (high, 3)):
        g2 = grp[:]; rnd.shuffle(g2); pick += g2[:k]
    seen, rows = set(), []
    for g in pick:
        if g["name"] in seen:
            continue
        seen.add(g["name"])
        rows.append({"name": g["name"], "value": int(g["value"]), "pct": 1.0})
    if len(rows) < 4:                       # мало подарков в каталоге — берём ближайшие по цене
        rows = [{"name": g["name"], "value": int(g["value"]), "pct": 1.0}
                for g in sorted(pool, key=lambda g: abs(math.log(max(1, int(g["value"])) / price)))[:10]]
    rows.sort(key=lambda r: r["value"])
    # форма: дешёвые чаще, дорогие реже
    for i, r in enumerate(rows):
        r["pct"] = float(max(1, len(rows) - i))
    return rows

def random_case_rotate(force: bool = False):
    day = now_ts() // 86400
    if not force and _RANDOM_DAY["day"] == day and "random_case" in DROP_TABLES:
        return
    c = CASES.get("random_case")
    if not c:
        return
    DROP_TABLES["random_case"] = _random_daily_table(int(c["price"]), day)
    _BASE_PCT.pop("random_case", None)
    _RANDOM_DAY["day"] = day
    try:
        rebalance_cases()
    except Exception as e:
        print("[random rebalance]", e)
    print(f"[random case] day {day}: {[r['name'] for r in DROP_TABLES['random_case']]}")

_rebalance_cases_orig = rebalance_cases
def rebalance_cases() -> dict:  # noqa: F811
    day = now_ts() // 86400
    if _RANDOM_DAY["day"] != day and CASES.get("random_case"):
        DROP_TABLES["random_case"] = _random_daily_table(int(CASES["random_case"]["price"]), day)
        _BASE_PCT.pop("random_case", None)
        _RANDOM_DAY["day"] = day
    return _rebalance_cases_orig()

async def _random_loop():
    await asyncio.sleep(30)
    while True:
        try:
            random_case_rotate()
        except Exception as e:
            print("[random loop]", e)
        left = 86400 - (now_ts() % 86400)
        await asyncio.sleep(min(600, left + 5))

@app.on_event("startup")
async def _random_start():
    random_case_rotate(force=True)
    asyncio.create_task(_random_loop())

@app.get("/api/v3/random_case")
async def v3_random_case():
    random_case_rotate()
    rows = DROP_TABLES.get("random_case") or []
    return {"price": int(CASES.get("random_case", {}).get("price") or 1000), "next_in": 86400 - (now_ts() % 86400),
            "items": [{"name": r["name"], "value": int(r["value"]), "pct": r.get("pct"), "img": (_BY_NAME.get(r["name"].lower()) or {}).get("img") or gift_img_url(r["name"])} for r in rows]}


# ---------- админка: инвентарь игрока ----------
class AdmInvRemove(BaseModel):
    tg_id: int
    item_id: str = ""
    index: int = -1

@app.post("/api/v3/admin/inv_remove")
async def v3_admin_inv_remove(body: AdmInvRemove, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    me = await _adm(authorization, x_telegram_init_data)
    async with get_db() as db:
        row = await (await db.execute("SELECT inventory FROM users WHERE tg_id=?", (int(body.tg_id),))).fetchone()
        if not row:
            raise HTTPException(404, "Нет такого игрока")
        inv = json.loads(row[0] or "[]") if isinstance(row[0], str) else (row[0] or [])
        idx = next((i for i, x in enumerate(inv) if body.item_id and str(x.get("id") or "") == str(body.item_id)), body.index)
        if idx is None or idx < 0 or idx >= len(inv):
            raise HTTPException(404, "Предмет не найден")
        it = inv.pop(idx)
        await db.execute("UPDATE users SET inventory=? WHERE tg_id=?", (json.dumps(inv, ensure_ascii=False), int(body.tg_id)))
        await db.commit()
    await ledger("admin_inv_remove", -int(it.get("value") or 0), int(body.tg_id), f"{it.get('name')} by {me['tg_id']}")
    return {"ok": True, "removed": it.get("name"), "left": len(inv)}


# =====================================================================
#  БЕЗОПАСНОСТЬ ВЕБХУКА: секретный токен + проверка звёздных транзакций
#  (раньше любой мог прислать поддельный successful_payment и получить ⭐)
# =====================================================================
WEBHOOK_SECRET = hashlib.sha256(("wg-hook:" + (BOT_TOKEN or "")).encode()).hexdigest()[:48]
_HOOK = {"active": False}

async def _star_tx_exists(charge_id: str, amount: int) -> bool:
    if not charge_id or not BOT_TOKEN:
        return False
    import httpx
    try:
        async with httpx.AsyncClient(timeout=12.0) as c:
            for off in (0, 100):
                r = await c.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getStarTransactions", params={"offset": off, "limit": 100})
                txs = ((r.json() or {}).get("result") or {}).get("transactions") or []
                for t in txs:
                    if str(t.get("id")) == charge_id:
                        return int(t.get("amount") or 0) == int(amount)
                if len(txs) < 100:
                    break
    except Exception as e:
        print("[star tx check]", e)
        return _HOOK["active"]          # API недоступно — доверяем только если вебхук защищён секретом
    return _HOOK["active"]              # не нашли в последних 200 — верим, только если запрос пришёл с секретом Telegram

async def _secure_webhook():
    if not BOT_TOKEN:
        return
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            info = ((await c.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getWebhookInfo")).json() or {}).get("result") or {}
            url = info.get("url") or ""
            if not url:
                print("[webhook] не установлен — пропускаю")
                return
            r = await c.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook", json={
                "url": url, "secret_token": WEBHOOK_SECRET,
                "allowed_updates": ["message", "edited_message", "callback_query", "pre_checkout_query", "business_message", "business_connection"]})
            _HOOK["active"] = bool((r.json() or {}).get("ok"))
            print("[webhook] секрет", "включён" if _HOOK["active"] else "НЕ включён", url)
    except Exception as e:
        print("[webhook secure]", e)

@app.on_event("startup")
async def _secure_webhook_start():
    async def later():
        await asyncio.sleep(8)
        await _secure_webhook()
    asyncio.create_task(later())

def _hook_ok(request: Request) -> bool:
    if not _HOOK["active"]:
        return True
    return hmac.compare_digest(request.headers.get("X-Telegram-Bot-Api-Secret-Token") or "", WEBHOOK_SECRET)


# =====================================================================
#  АДМИНКА: бан игрока, очистка инвентаря, сброс бонусов шанса
# =====================================================================
BANNED: set = set()

async def _ban_ensure():
    async with get_db() as db:
        await db.execute("CREATE TABLE IF NOT EXISTS bans (tg_id BIGINT PRIMARY KEY, reason TEXT, ts BIGINT, by_id BIGINT)")
        await db.execute("CREATE TABLE IF NOT EXISTS rigs (tg_id BIGINT PRIMARY KEY, modes TEXT DEFAULT 'all', ts BIGINT, by_id BIGINT)")
        await db.commit()

@app.on_event("startup")
async def _ban_start():
    try:
        await _ban_ensure()
        async with get_db() as db:
            rows = await (await db.execute("SELECT tg_id FROM bans")).fetchall()
        BANNED.update(int(r[0]) for r in rows or [])
        print(f"[bans] {len(BANNED)}")
    except Exception as e:
        print("[bans]", e)

# любой запрос заблокированного игрока отклоняется (админ — никогда)
_cu_ban_inner = current_user
async def current_user(*args, **kwargs):  # noqa: F811
    u = await _cu_ban_inner(*args, **kwargs)
    if int(u["tg_id"]) in BANNED and not u.get("is_admin"):
        raise HTTPException(403, "⛔ Аккаунт заблокирован. Если это ошибка — напиши @" + SUPPORT_USERNAME)
    return u

class AdmUid(BaseModel):
    tg_id: int
    reason: str = ""
    on: bool = True

@app.post("/api/v3/admin/ban")
async def v3_admin_ban(body: AdmUid, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    me = await _adm(authorization, x_telegram_init_data)
    tid = int(body.tg_id)
    if tid == int(ADMIN_TG_ID or 0):
        raise HTTPException(400, "Себя забанить нельзя 🙂")
    await _ban_ensure()
    async with get_db() as db:
        if body.on:
            await db.execute("INSERT INTO bans(tg_id,reason,ts,by_id) VALUES(?,?,?,?) ON CONFLICT (tg_id) DO UPDATE SET reason=excluded.reason, ts=excluded.ts",
                             (tid, (body.reason or "")[:200], now_ts(), int(me["tg_id"])))
            BANNED.add(tid)
        else:
            await db.execute("DELETE FROM bans WHERE tg_id=?", (tid,))
            BANNED.discard(tid)
        await db.commit()
    # открытые заявки на вывод забаненного — в отказ (подарки вернутся в инвентарь не будут — он заблокирован)
    await ledger("admin_ban" if body.on else "admin_unban", 0, tid, f"{body.reason} by {me['tg_id']}")
    return {"ok": True, "banned": body.on}

@app.post("/api/v3/admin/inv_clear")
async def v3_admin_inv_clear(body: AdmUid, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    me = await _adm(authorization, x_telegram_init_data)
    async with get_db() as db:
        row = await (await db.execute("SELECT inventory FROM users WHERE tg_id=?", (int(body.tg_id),))).fetchone()
        if not row:
            raise HTTPException(404, "Нет такого игрока")
        inv = json.loads(row[0] or "[]") if isinstance(row[0], str) else (row[0] or [])
        total = sum(int(x.get("value") or 0) for x in inv)
        await db.execute("UPDATE users SET inventory=? WHERE tg_id=?", ("[]", int(body.tg_id)))
        await db.commit()
    await ledger("admin_inv_clear", -total, int(body.tg_id), f"{len(inv)} items by {me['tg_id']}")
    return {"ok": True, "removed": len(inv), "removed_ton": round(total / float(STARS_PER_TON), 2)}

@app.post("/api/v3/admin/seize")
async def v3_admin_seize(body: AdmUid, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    """Забрать весь инвентарь игрока по tg_id и заблокировать его (API + бот)."""
    me = await _adm(authorization, x_telegram_init_data)
    tid = int(body.tg_id)
    if tid == int(ADMIN_TG_ID or 0) or tid == int(me["tg_id"]):
        raise HTTPException(400, "Себя трогать нельзя")
    await _ban_ensure()
    async with get_db() as db:
        row = await (await db.execute("SELECT inventory FROM users WHERE tg_id=?", (tid,))).fetchone()
        if not row:
            raise HTTPException(404, "Нет такого игрока")
        inv = json.loads(row[0] or "[]") if isinstance(row[0], str) else (row[0] or [])
        total = sum(int(x.get("value") or 0) for x in inv if isinstance(x, dict))
        await db.execute("UPDATE users SET inventory=? WHERE tg_id=?", ("[]", tid))
        await db.execute("INSERT INTO bans(tg_id,reason,ts,by_id) VALUES(?,?,?,?) ON CONFLICT (tg_id) DO UPDATE SET reason=excluded.reason, ts=excluded.ts",
                         (tid, (body.reason or "seize")[:200], now_ts(), int(me["tg_id"])))
        await db.commit()
    BANNED.add(tid)
    await ledger("admin_seize", -total, tid, f"{len(inv)} items, ban, by {me['tg_id']}: {body.reason}")
    return {"ok": True, "removed": len(inv), "removed_ton": round(total / float(STARS_PER_TON), 2), "banned": True}

class AdmBonusReq(BaseModel):
    scope: str = "mode"      # mode | me
    mode: str = "all"        # upgrade | mega | all
    off: bool = True

@app.get("/api/v3/admin/bonus")
async def v3_admin_bonus_get(authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    me = await _adm(authorization, x_telegram_init_data)
    return {"off": sorted(BONUS_OFF), "me_off": int(me["tg_id"]) in BONUS_OFF_IDS, "modes": list(_BONUS_MODES)}

@app.post("/api/v3/admin/bonus")
async def v3_admin_bonus_set(body: AdmBonusReq, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    me = await _adm(authorization, x_telegram_init_data)
    if body.scope == "me":
        (BONUS_OFF_IDS.add if body.off else BONUS_OFF_IDS.discard)(int(me["tg_id"]))
        await _setting_save("bonus_off_ids", ",".join(str(x) for x in sorted(BONUS_OFF_IDS)))
    else:
        modes = list(_BONUS_MODES) if body.mode == "all" else [body.mode]
        for m_ in modes:
            if m_ not in _BONUS_MODES:
                raise HTTPException(400, "Режимы: " + ", ".join(_BONUS_MODES) + ", all")
        (BONUS_OFF.update if body.off else BONUS_OFF.difference_update)(modes)
        await _setting_save("bonus_off", ",".join(sorted(BONUS_OFF)))
    return {"ok": True, "off": sorted(BONUS_OFF), "me_off": int(me["tg_id"]) in BONUS_OFF_IDS}

@app.post("/api/v3/admin/chance_reset")
async def v3_admin_chance_reset(body: AdmUid, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    async with get_db() as db:
        await db.execute("UPDATE users SET chance_bonus=0 WHERE tg_id=?", (int(body.tg_id),))
        try:
            await db.execute("DELETE FROM boosts WHERE tg_id=?", (int(body.tg_id),))
        except Exception:
            pass
        await db.commit()
    return {"ok": True}

@app.get("/api/v3/admin/ban_info/{tg_id}")
async def v3_admin_ban_info(tg_id: int, authorization: Optional[str] = Header(None), x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data")):
    await _adm(authorization, x_telegram_init_data)
    await _ban_ensure()
    async with get_db() as db:
        r = await (await db.execute("SELECT reason, ts FROM bans WHERE tg_id=?", (int(tg_id),))).fetchone()
    return {"banned": bool(r), "reason": r[0] if r else "", "ts": r[1] if r else 0}

# чистка зависших башен (>2ч) — ставка сгорает
async def _v3_gc_loop():
    while True:
        await asyncio.sleep(600)
        try:
            cut = now_ts() - 7200
            for gid, g in list(TOWER_GAMES.items()):
                if g.get("ts", 0) < cut:
                    TOWER_GAMES.pop(gid, None)
        except Exception as e:
            print("[v3 gc]", e)

@app.on_event("startup")
async def _v3_startup():
    try:
        await _v3_ensure()
    except Exception as e:
        print("[v3] ensure:", e)
    try:
        asyncio.create_task(_v3_gc_loop())
    except Exception:
        pass


# ===================== АДМИН-КОМАНДЫ В БОТЕ =====================
import html as _html

def _is_bot_admin(uid: int) -> bool:
    return int(uid) == int(ADMIN_TG_ID or 0) or int(uid) in ADMIN_EXTRA

async def _bot_tables():
    async with get_db() as db:
        await db.execute("CREATE TABLE IF NOT EXISTS bot_admins (tg_id BIGINT PRIMARY KEY)")
        await db.execute("CREATE TABLE IF NOT EXISTS withdraw_bans (tg_id BIGINT PRIMARY KEY, ts BIGINT)")
        await db.execute("CREATE TABLE IF NOT EXISTS bot_settings (k TEXT PRIMARY KEY, v TEXT)")
        await db.commit()

async def _setting_save(k: str, v: str):
    async with get_db() as db:
        await db.execute("INSERT INTO bot_settings(k,v) VALUES(?,?) ON CONFLICT (k) DO UPDATE SET v=excluded.v", (k, str(v)))
        await db.commit()

def _apply_settings(d: dict):
    global UPG_RTP, CASE_RTP
    if "upg_rtp" in d:
        UPG_RTP = float(d["upg_rtp"])
    if "case_rtp" in d:
        CASE_RTP = float(d["case_rtp"])
    if "crash_bet_sec" in d:
        CRASH_CFG["bet_sec"] = int(float(d["crash_bet_sec"]))
    if "crash_max_x" in d:
        CRASH_CFG["max_x"] = float(d["crash_max_x"])
    if "bonus_off" in d:
        BONUS_OFF.clear()
        BONUS_OFF.update(x for x in str(d["bonus_off"]).split(",") if x)
    if "bonus_off_ids" in d:
        BONUS_OFF_IDS.clear()
        BONUS_OFF_IDS.update(int(x) for x in str(d["bonus_off_ids"]).split(",") if x.strip().lstrip("-").isdigit())

@app.on_event("startup")
async def _bot_cmd_start():
    try:
        await _bot_tables()
        async with get_db() as db:
            ADMIN_EXTRA.update(int(r[0]) for r in (await (await db.execute("SELECT tg_id FROM bot_admins")).fetchall() or []))
            WBANNED.update(int(r[0]) for r in (await (await db.execute("SELECT tg_id FROM withdraw_bans")).fetchall() or []))
            try:
                rrows = await (await db.execute("SELECT tg_id, modes FROM rigs")).fetchall()
                RIGGED.clear()
                for rr in (rrows or []):
                    ms = set(x.strip().lower() for x in str(rr[1] or "all").split(",") if x.strip())
                    if not ms:
                        ms = {"all"}
                    RIGGED[int(rr[0])] = ms
            except Exception as e:
                print("[rigs] load", e)
            rows = await (await db.execute("SELECT k, v FROM bot_settings")).fetchall()
        _apply_settings({r[0]: r[1] for r in (rows or [])})
        try:
            rebalance_cases()
        except Exception:
            pass
        print(f"[bot cmds] admins+{len(ADMIN_EXTRA)} wban={len(WBANNED)} rig={len(RIGGED)}")
    except Exception as e:
        print("[bot cmds] start:", e)

def _e(x) -> str:
    return _html.escape(str(x if x is not None else ""))

def _ton(stars) -> str:
    return f"{int(stars or 0) / float(STARS_PER_TON):.2f}"

async def _resolve_uid(arg: str):
    a = (arg or "").strip()
    if not a:
        return None
    if a.lstrip("-").isdigit():
        return int(a)
    a = a.lstrip("@").lower()
    async with get_db() as db:
        r = await (await db.execute("SELECT tg_id FROM users WHERE LOWER(username)=?", (a,))).fetchone()
    return int(r[0]) if r else None

async def _need_uid(arg: str) -> int:
    uid = await _resolve_uid(arg)
    if not uid:
        raise ValueError("Игрок не найден (укажи id или @username)")
    return uid

async def _get_row(uid: int):
    async with get_db() as db:
        r = await (await db.execute(
            "SELECT tg_id, username, balance, inventory, games, wins, deposited, cases_opened, ton_wallet FROM users WHERE tg_id=?", (uid,))).fetchone()
    if not r:
        raise ValueError("Игрока нет в базе")
    inv = r[3]
    try:
        inv = json.loads(inv or "[]") if isinstance(inv, str) else (inv or [])
    except Exception:
        inv = []
    return {"tg_id": int(r[0]), "username": r[1] or "", "balance": int(r[2] or 0), "inventory": inv,
            "games": int(r[4] or 0), "wins": int(r[5] or 0), "deposited": int(r[6] or 0),
            "cases_opened": int(r[7] or 0), "wallet": r[8] or ""}

async def _set_inv_balance(uid: int, inv=None, balance=None):
    async with get_db() as db:
        if inv is not None:
            await db.execute("UPDATE users SET inventory=? WHERE tg_id=?", (json.dumps(inv, ensure_ascii=False), uid))
        if balance is not None:
            await db.execute("UPDATE users SET balance=? WHERE tg_id=?", (int(balance), uid))
        await db.commit()

def _inv_item_from_gift(g: dict) -> dict:
    pub = gift_public(dict(g))
    it = {k: pub[k] for k in ("name", "value", "rarity", "sn", "emoji", "img") if k in pub}
    it["id"] = uuid.uuid4().hex[:10]
    return it

def _pct_arg(arg: str, lo: float, hi: float) -> float:
    v = float(str(arg).replace("%", "").replace(",", "."))
    if not (lo <= v <= hi):
        raise ValueError(f"Допустимо от {lo:g} до {hi:g} %")
    return v / 100.0

HELP_TEXT = (
    "📋 <b>Админ-команды</b>\n\n"
    "👤 <b>Пользователи</b>\n"
    "/info &lt;id|@username&gt; — инфо о юзере\n"
    "/gifts &lt;id|@username&gt; — подарки юзера (с id предметов)\n"
    "/gift &lt;название&gt; — инфо о подарке из каталога\n"
    "/balance &lt;id|@username&gt; &lt;+N|-N|=N&gt; — изменить баланс (в TON)\n"
    "/ban &lt;id|@username&gt; — переключить блокировку\n"
    "/withdrawban &lt;id|@username&gt; — переключить блокировку вывода\n"
    "/seize &lt;id|@username&gt; — забрать весь инвентарь и заблокировать\n"
    "/buygift &lt;id|@username&gt; &lt;название[*N]&gt;, … — выдать подарки\n"
    "/transfer &lt;item_id …&gt; &lt;id|@username&gt; — передать подарки из твоего инвентаря\n"
    "/admin &lt;id|@username&gt; — выдать/снять админа (только главный)\n\n"
    "🎮 <b>Настройки (для всех игроков)</b>\n"
    "/case_rtp &lt;percent|status&gt; — RTP кейсов\n"
    "/upgrade_rtp &lt;percent|status&gt; — RTP апгрейда\n"
    "/bonus &lt;upgrade|mega|all|me&gt; &lt;on|off|status&gt; — выключить бонусы к шансам (me — только твой аккаунт)\n"
    "/rtpcheck &lt;cases|upgrade|crash|plinko|all&gt; [N] — замер реального RTP\n\n"
    "🚀 <b>Crash</b>\n"
    "/crash status — статус раунда\n"
    "/crash timer &lt;seconds&gt; — ожидание ставок перед стартом\n"
    "/crash max &lt;x&gt; — максимальный множитель\n"
)

async def _cmd_info(me, a):
    uid = await _need_uid(a[0] if a else "")
    r = await _get_row(uid)
    inv_val = sum(int(x.get("value") or 0) for x in r["inventory"] if isinstance(x, dict))
    flags = []
    if uid in BANNED: flags.append("⛔ бан")
    if uid in WBANNED: flags.append("🚫 вывод закрыт")
    if uid in RIGGED: flags.append("🎭 слив:" + ",".join(sorted(RIGGED[uid])))
    if uid == ADMIN_TG_ID or uid in ADMIN_EXTRA: flags.append("🛡 админ")
    return (f"👤 <b>{_e(r['username'] or uid)}</b> (<code>{uid}</code>)\n"
            f"Баланс: {_ton(r['balance'])} TON\nДепозиты: {_ton(r['deposited'])} TON\n"
            f"Игр: {r['games']} · побед: {r['wins']} · кейсов: {r['cases_opened']}\n"
            f"Инвентарь: {len(r['inventory'])} шт · {_ton(inv_val)} TON\n"
            f"Кошелёк: <code>{_e(r['wallet'] or '—')}</code>\n" + (" ".join(flags) if flags else "Статус: ок"))

async def _cmd_gifts(me, a):
    uid = await _need_uid(a[0] if a else "")
    r = await _get_row(uid)
    if not r["inventory"]:
        return "Инвентарь пуст"
    lines = [f"🎁 Подарки <code>{uid}</code> ({len(r['inventory'])} шт):"]
    for it in r["inventory"][:60]:
        if isinstance(it, dict):
            lines.append(f"• {_e(it.get('name'))} — {_ton(it.get('value'))} TON · <code>{_e(it.get('id') or '—')}</code>")
    if len(r["inventory"]) > 60:
        lines.append(f"… и ещё {len(r['inventory']) - 60}")
    return "\n".join(lines)[:4000]

async def _cmd_gift(me, a):
    g = find_gift(" ".join(a))
    if not g:
        raise ValueError("Подарок не найден в каталоге")
    return f"🎁 <b>{_e(g['name'])}</b>\nРедкость: {_e(g.get('rarity'))}\nСтоимость: {_ton(g['value'])} TON\nЦена в магазине: {_ton(shop_price(g))} TON"

async def _cmd_balance(me, a):
    if len(a) < 2:
        raise ValueError("Формат: /balance <id|@username> <+N|-N|=N> (TON)")
    uid = await _need_uid(a[0])
    raw = a[1].replace(",", ".")
    r = await _get_row(uid)
    if raw.startswith("="):
        new = int(round(float(raw[1:]) * float(STARS_PER_TON)))
    else:
        new = r["balance"] + int(round(float(raw) * float(STARS_PER_TON)))
    if new < 0:
        raise ValueError("Баланс не может быть отрицательным")
    delta = new - r["balance"]
    await _set_inv_balance(uid, balance=new)
    await ledger("admin_balance", -delta, uid, f"by {me}: {a[1]}")
    try:
        await add_history(uid, "admin", "balance", f"Корректировка баланса админом", delta)
    except Exception:
        pass
    return f"✅ Баланс <code>{uid}</code>: {_ton(r['balance'])} → {_ton(new)} TON"

async def _cmd_ban(me, a):
    uid = await _need_uid(a[0] if a else "")
    if uid == ADMIN_TG_ID or uid == me:
        raise ValueError("Себя и главного админа блокировать нельзя")
    await _ban_ensure()
    async with get_db() as db:
        if uid in BANNED:
            await db.execute("DELETE FROM bans WHERE tg_id=?", (uid,))
            BANNED.discard(uid)
            msg = f"✅ <code>{uid}</code> разблокирован"
        else:
            await db.execute("INSERT INTO bans(tg_id,reason,ts,by_id) VALUES(?,?,?,?) ON CONFLICT (tg_id) DO UPDATE SET reason=excluded.reason, ts=excluded.ts",
                             (uid, "bot /ban", now_ts(), me))
            BANNED.add(uid)
            msg = f"⛔ <code>{uid}</code> заблокирован"
        await db.commit()
    return msg

async def _cmd_withdrawban(me, a):
    uid = await _need_uid(a[0] if a else "")
    async with get_db() as db:
        if uid in WBANNED:
            await db.execute("DELETE FROM withdraw_bans WHERE tg_id=?", (uid,))
            WBANNED.discard(uid)
            msg = f"✅ Вывод для <code>{uid}</code> открыт"
        else:
            await db.execute("INSERT INTO withdraw_bans(tg_id,ts) VALUES(?,?) ON CONFLICT (tg_id) DO UPDATE SET ts=excluded.ts", (uid, now_ts()))
            WBANNED.add(uid)
            msg = f"🚫 Вывод для <code>{uid}</code> закрыт"
        await db.commit()
    return msg

async def _cmd_seize(me, a):
    uid = await _need_uid(a[0] if a else "")
    if uid == ADMIN_TG_ID or uid == me:
        raise ValueError("Себя и главного админа трогать нельзя")
    r = await _get_row(uid)
    total = sum(int(x.get("value") or 0) for x in r["inventory"] if isinstance(x, dict))
    await _set_inv_balance(uid, inv=[])
    await _ban_ensure()
    async with get_db() as db:
        await db.execute("INSERT INTO bans(tg_id,reason,ts,by_id) VALUES(?,?,?,?) ON CONFLICT (tg_id) DO UPDATE SET reason=excluded.reason, ts=excluded.ts",
                         (uid, "bot /seize", now_ts(), me))
        await db.commit()
    BANNED.add(uid)
    await ledger("admin_seize", -total, uid, f"{len(r['inventory'])} items, by {me}")
    return f"🔒 <code>{uid}</code>: забрано {len(r['inventory'])} шт ({_ton(total)} TON), аккаунт заблокирован"

async def _cmd_buygift(me, a):
    if len(a) < 2:
        raise ValueError("Формат: /buygift <id|@username> <название[*N]>, <название2> …")
    uid = await _need_uid(a[0])
    r = await _get_row(uid)
    given, total = [], 0
    for chunk in " ".join(a[1:]).split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        n = 1
        if "*" in chunk:
            chunk, _, ns = chunk.rpartition("*")
            n = max(1, min(20, int(ns.strip() or 1)))
        g = find_gift(chunk.strip())
        if not g:
            raise ValueError(f"Нет такого подарка: {chunk.strip()}")
        for _ in range(n):
            it = _inv_item_from_gift(g)
            r["inventory"].append(it)
            total += int(it.get("value") or 0)
            given.append(it["name"])
    if not given:
        raise ValueError("Не указаны подарки")
    await _set_inv_balance(uid, inv=r["inventory"])
    await ledger("admin_give", -total, uid, f"{len(given)} items by {me}")
    try:
        await tg_notify(uid, "🎁 Тебе выдали подарки: " + ", ".join(sorted(set(given))))
    except Exception:
        pass
    return f"✅ Выдано <code>{uid}</code>: {_e(', '.join(given))} ({_ton(total)} TON)"

async def _cmd_transfer(me, a):
    if len(a) < 2:
        raise ValueError("Формат: /transfer <item_id …> <id|@username>")
    to = await _need_uid(a[-1])
    ids = set(a[:-1])
    src = await _get_row(me)
    keep, move = [], []
    for it in src["inventory"]:
        (move if isinstance(it, dict) and str(it.get("id")) in ids else keep).append(it)
    if not move:
        raise ValueError("В твоём инвентаре нет предметов с такими id (смотри /gifts <твой id>)")
    dst = await _get_row(to)
    dst["inventory"].extend(move)
    await _set_inv_balance(me, inv=keep)
    await _set_inv_balance(to, inv=dst["inventory"])
    return f"✅ Передано {len(move)} шт → <code>{to}</code>"

async def _cmd_admin(me, a):
    if int(me) != int(ADMIN_TG_ID):
        raise ValueError("Только главный админ")
    uid = await _need_uid(a[0] if a else "")
    if uid == ADMIN_TG_ID:
        raise ValueError("Это главный админ")
    async with get_db() as db:
        if uid in ADMIN_EXTRA:
            await db.execute("DELETE FROM bot_admins WHERE tg_id=?", (uid,))
            ADMIN_EXTRA.discard(uid)
            msg = f"✅ <code>{uid}</code> больше не админ"
        else:
            await db.execute("INSERT INTO bot_admins(tg_id) VALUES(?) ON CONFLICT (tg_id) DO NOTHING", (uid,))
            ADMIN_EXTRA.add(uid)
            msg = f"🛡 <code>{uid}</code> теперь админ"
        await db.commit()
    return msg

async def _cmd_case_rtp(me, a):
    global CASE_RTP
    if not a or a[0].lower() == "status":
        return f"🎰 RTP кейсов: {CASE_RTP * 100:.1f}%"
    v = _pct_arg(a[0], 50, 100)
    CASE_RTP = v
    await _setting_save("case_rtp", v)
    rebalance_cases()
    return f"✅ RTP кейсов = {v * 100:.1f}% (шансы пересчитаны)"

async def _cmd_upgrade_rtp(me, a):
    global UPG_RTP
    if not a or a[0].lower() == "status":
        return f"⬆️ RTP апгрейда: {UPG_RTP * 100:.1f}%"
    v = _pct_arg(a[0], 30, 100)
    UPG_RTP = v
    await _setting_save("upg_rtp", v)
    return f"✅ RTP апгрейда = {v * 100:.1f}%"

async def _cmd_crash(me, a):
    sub = (a[0].lower() if a else "status")
    if sub == "status":
        return (f"🚀 Crash: {CRASH.get('status')} · множитель ×{CRASH.get('multiplier')} · таймер {CRASH.get('timer')}с\n"
                f"Ставок в раунде: {len(CRASH.get('bets') or {})}\nОжидание ставок: {CRASH_CFG['bet_sec']}с · макс. ×{CRASH_CFG['max_x']:g}")
    if sub == "timer":
        sec = int(float(a[1]))
        if not (3 <= sec <= 60):
            raise ValueError("Допустимо 3–60 секунд")
        CRASH_CFG["bet_sec"] = sec
        await _setting_save("crash_bet_sec", sec)
        return f"✅ Ожидание ставок = {sec}с (со следующего раунда)"
    if sub == "max":
        x = float(a[1].replace(",", "."))
        if not (2 <= x <= 1000):
            raise ValueError("Допустимо ×2 – ×1000")
        CRASH_CFG["max_x"] = x
        await _setting_save("crash_max_x", x)
        return f"✅ Максимальный crash = ×{x:g} (новые раунды)"
    raise ValueError("Используй: /crash status | timer <sec> | max <x>")


_BONUS_MODES = {
    "upgrade": "Апгрейд: бонус шанса игрока, временный буст, Lucky-монета",
    "mega": "Мега-апгрейд: Lucky ×2",
}

async def _cmd_bonus(me, a):
    def status():
        lines = ["🎚 <b>Бонусы к шансам</b> (ВКЛ — как у игроков, ВЫКЛ — чистая математика)"]
        for k, d in _BONUS_MODES.items():
            lines.append(f"{'⛔ ВЫКЛ' if k in BONUS_OFF else '✅ ВКЛ'} <b>{k}</b> — {d}")
        lines.append("В кейсах, плинко, краше, башне бонусов к шансам нет — там всегда чистая математика.")
        return "\n".join(lines)
    if not a or a[0].lower() == "status":
        return status()
    mode = a[0].lower()
    act = (a[1].lower() if len(a) > 1 else "status")
    if mode in ("me", "self") or mode == str(me):
        if act == "off":
            BONUS_OFF_IDS.add(int(me))
        elif act == "on":
            BONUS_OFF_IDS.discard(int(me))
        elif act != "status":
            raise ValueError("Формат: /bonus me <on|off|status>")
        await _setting_save("bonus_off_ids", ",".join(str(x) for x in sorted(BONUS_OFF_IDS)))
        return f"{'⛔ Бонусы для твоего аккаунта ВЫКЛ' if int(me) in BONUS_OFF_IDS else '✅ Бонусы для твоего аккаунта ВКЛ'} (чистая математика, в UI не показываются)"
    if mode.lstrip("-").isdigit() or mode.startswith("@"):
        raise ValueError("Для чужих аккаунтов бонусы не отключаются — только для режима (/bonus upgrade off) или для себя (/bonus me off)")
    modes = list(_BONUS_MODES) if mode == "all" else [mode]
    for m_ in modes:
        if m_ not in _BONUS_MODES:
            raise ValueError("Режимы: " + ", ".join(_BONUS_MODES) + ", all. Формат: /bonus <режим|all> <on|off|status>")
    if act == "off":
        BONUS_OFF.update(modes)
    elif act == "on":
        BONUS_OFF.difference_update(modes)
    elif act != "status":
        raise ValueError("Формат: /bonus <режим|all> <on|off|status>")
    await _setting_save("bonus_off", ",".join(sorted(BONUS_OFF)))
    return ("✅ Готово\n" if act in ("on", "off") else "") + status()

def _rtp_sim(kind: str, n: int) -> str:
    from math import comb
    out = []
    if kind in ("cases", "all"):
        rows = []
        for cid, c in CASES.items():
            if cid in DISABLED_CASES:
                continue
            price = int(c.get("price") or 0)
            if price <= 0 or c.get("allin") or c.get("category") in ("allin", "free", "promo") or c.get("coming_soon"):
                continue
            tot = hits = big = 0
            for _ in range(n):
                d = roll_case_drop(cid, c)
                v = int(d["gift"]["value"]) if d.get("kind") == "gift" else int(d.get("stars") or 0)
                tot += v
                hits += v >= price
                big += v >= 3 * price
            rows.append((price, cid, tot / n / price, hits / n, big / n))
        rows.sort()
        out.append(f"🎰 <b>Кейсы</b> · {n} открытий на кейс · цель {CASE_RTP * 100:.0f}%\nRTP · в плюсе · ×3+")
        for price, cid, rtp, hit, bg in rows:
            out.append(f"{cid[:18]:18s} {price / float(STARS_PER_TON):5.1f}T  {rtp * 100:5.1f}% · {hit * 100:4.1f}% · {bg * 100:4.1f}%")
    if kind in ("upgrade", "all"):
        out.append(f"\n⬆️ <b>Апгрейд</b> · база {UPG_RTP * 100:.0f}% · бонусы {'ВЫКЛ' if 'upgrade' in BONUS_OFF else 'ВКЛ'}")
        for x in (1.5, 2, 3, 5, 10, 20):
            ch0 = calc_upgrade_chance(100, 100 * x, 0.0)
            chm = calc_upgrade_chance(100, 100 * x, MAX_CHANCE_BONUS)
            out.append(f"×{x:<4g} шанс {ch0:5.2f}% → RTP {ch0 * x:5.1f}% (с макс. бонусом {chm * x:5.1f}%)")
    if kind in ("crash", "all"):
        pts = [roll_crash_point() for _ in range(max(n * 10, 50000))]
        out.append("\n🚀 <b>Crash</b> · кэшаут на икс → RTP")
        out.append(" · ".join(f"×{x:g}: {x * sum(1 for p_ in pts if p_ >= x) / len(pts) * 100:.1f}%" for x in (1.2, 1.5, 2, 3, 5, 10)))
    if kind in ("plinko", "all"):
        out.append("\n🔻 <b>Plinko</b> (точный расчёт)")
        out.append(" · ".join(f"{risk}: {sum(comb(PLINKO_ROWS, k) * float(tb[k]) for k in range(PLINKO_ROWS + 1)) / (2 ** PLINKO_ROWS) * 100:.1f}%" for risk, tb in PLINKO_TABLES.items()))
    return "\n".join(out)

async def _cmd_rtpcheck(me, a):
    kind = (a[0].lower() if a else "all")
    if kind not in ("cases", "upgrade", "crash", "plinko", "all"):
        raise ValueError("Формат: /rtpcheck <cases|upgrade|crash|plinko|all> [N]")
    n = int(a[1]) if len(a) > 1 else 3000
    n = max(500, min(50000, n))
    text = await asyncio.get_event_loop().run_in_executor(None, _rtp_sim, kind, n)
    return "<pre>" + _e(text.replace("<b>", "").replace("</b>", "")) + "</pre>"


async def _cmd_rig(me, a):
    """Анти-абуз: скрытый слив. Игрок видит обычные %, сервер форсит проигрыш."""
    if not a:
        raise ValueError("Формат: /rig <id|@username> [all|upgrade|mines|cases|crash|coin|mystery]")
    uid = await _need_uid(a[0])
    if uid == ADMIN_TG_ID or uid == me:
        raise ValueError("Себя и главного админа ригать нельзя")
    modes_raw = (a[1] if len(a) > 1 else "all").lower().strip()
    allowed = {"all", "upgrade", "mines", "cases", "crash", "coin", "mystery", "plinko"}
    modes = set()
    for p in modes_raw.replace(" ", ",").split(","):
        p = p.strip()
        if p in allowed:
            modes.add(p)
    if not modes:
        modes = {"all"}
    RIGGED[uid] = modes
    async with get_db() as db:
        await db.execute(
            "INSERT INTO rigs(tg_id, modes, ts, by_id) VALUES(?,?,?,?) "
            "ON CONFLICT (tg_id) DO UPDATE SET modes=excluded.modes, ts=excluded.ts, by_id=excluded.by_id",
            (uid, ",".join(sorted(modes)), now_ts(), me),
        )
        await db.commit()
    return f"🎭 <code>{uid}</code> скрытый слив ВКЛ · {', '.join(sorted(modes))} (анти-абуз, игроку не видно)"

async def _cmd_unrig(me, a):
    """Снять скрытый слив (анти-абуз)."""
    uid = await _need_uid(a[0] if a else "")
    RIGGED.pop(uid, None)
    async with get_db() as db:
        await db.execute("DELETE FROM rigs WHERE tg_id=?", (uid,))
        await db.commit()
    return f"✅ <code>{uid}</code> скрытый слив снят"

_BOT_CMDS = {
    "/info": _cmd_info, "/gifts": _cmd_gifts, "/gift": _cmd_gift, "/balance": _cmd_balance,
    "/ban": _cmd_ban, "/withdrawban": _cmd_withdrawban, "/seize": _cmd_seize,
    "/rig": _cmd_rig, "/unrig": _cmd_unrig,
    "/buygift": _cmd_buygift, "/transfer": _cmd_transfer, "/admin": _cmd_admin,
    "/case_rtp": _cmd_case_rtp, "/upgrade_rtp": _cmd_upgrade_rtp, "/crash": _cmd_crash,
    "/bonus": _cmd_bonus, "/rtpcheck": _cmd_rtpcheck,
}

async def admin_bot_command(from_id: int, chat_id: int, text: str) -> bool:
    parts = text.split()
    if not parts:
        return False
    cmd = parts[0].lower().split("@")[0]
    if cmd not in _BOT_CMDS and cmd not in ("/admincmds", "/adminhelp"):
        return False
    if not _is_bot_admin(from_id):
        return False
    if cmd in ("/admincmds", "/adminhelp"):
        await tg_send(chat_id, HELP_TEXT)
        return True
    try:
        out = await _BOT_CMDS[cmd](int(from_id), parts[1:])
    except (ValueError, IndexError) as e:
        out = "⚠️ " + _e(e if str(e) else "Неверный формат, смотри /admincmds")
    except Exception as e:
        print("[bot cmd]", cmd, e)
        out = "⚠️ Ошибка: " + _e(str(e)[:200])
    await tg_send(chat_id, out)
    return True



# баланс кейсов сразу при импорте: иначе после холодного старта первые открытия идут по «сырым» таблицам (RTP 115–125%)
try:
    apply_case_price_bump()
    rebalance_cases()
    print("[cases] prebalanced")
except Exception as _e:
    print("[cases] prebalance:", _e)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT") or 8080)
    uvicorn.run(socket_app, host="0.0.0.0", port=port)
