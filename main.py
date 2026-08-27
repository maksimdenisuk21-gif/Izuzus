# main.py - GiftUpgrader (полный код)

import os, hmac, hashlib, json, urllib.parse, random, time, uuid, asyncio, math
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from fastapi import FastAPI, Header, HTTPException, Depends
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
    "Astral Shard","B-Day Candle","Berry Box","Big Year","Bonded Ring","Bow Tie",
    "Bunny Muffin","Candy Cane","Cookie Heart","Crystal Ball","Cupid Charm",
    "Diamond Ring","Durov's Cap","Electric Skull","Eternal Rose","Flying Broom",
    "Genie Lamp","Ginger Cookie","Heart Locket","Heroic Helmet","Hex Pot",
    "Holiday Drink","Ion Gem","Jack-in-the-Box","Jester Hat","Khabib's Papakha",
    "Light Sword","Loot Bag","Love Potion","Lunar Snake","Magic Potion",
    "Mini Oscar","Moon Pendant","Nail Bracelet","Neko Helmet","Onyx Black",
    "Perfume Bottle","Plush Pepe","Precious Peach","Restless Jar","Rocket",
    "Santa Hat","Scared Cat","Signet Ring","Skull Flower","Snow Globe",
    "Spiced Wine","Star Notepad","Swiss Watch","Top Hat","Toy Bear",
    "Trapped Heart","Vintage Cigar","Voodoo Doll","Witch Hat","Xmas Stocking"
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
        "Vintage Cigar":"🚬","Voodoo Doll":"🪆","Witch Hat":"🧙","Xmas Stocking":"🧦"
    }
    return m.get(name, "🎁")

def build_nft_gifts():
    gifts = {}
    values = {
        "Common": (15,80), "Uncommon": (100,350), "Rare": (400,900),
        "Epic": (1000,2500), "Legendary": (3000,8000), "Mythic": (10000,60000)
    }
    idx = 0
    for r in ["Common","Uncommon","Rare","Epic","Legendary","Mythic"]:
        gifts[r] = []
        for i in range(8):
            if idx >= len(NFT_NAMES): idx = 0
            name = NFT_NAMES[idx]; idx += 1
            v = random.randint(values[r][0], values[r][1])
            v = round(v/10)*10 if r in ["Uncommon","Rare"] else round(v/50)*50 if r in ["Epic","Legendary"] else round(v/100)*100 if r=="Mythic" else round(v/5)*5
            gifts[r].append({"id": name.lower().replace(" ","_"), "name": name, "value": max(1,v), "emoji": get_emoji(name)})
    return gifts

NFT_GIFTS = build_nft_gifts()

CASES = {
    "free_daily": {"name":"🎁 FREE DAILY","price":0,"cooldown":86400,"rarities":["Common"],"weights":[100],"min_stars":0.5,"max_stars":15},
    "tg_starter": {"name":"🚀 TG STARTER","price":50,"rarities":["Common","Uncommon"],"weights":[50,50],"min_stars":10,"max_stars":80},
    "pepe_memes": {"name":"🐸 PEPE & MEMES","price":200,"rarities":["Uncommon","Rare"],"weights":[45,55],"min_stars":50,"max_stars":400},
    "telegram_gifts": {"name":"🎁 TELEGRAM GIFTS","price":500,"rarities":["Rare","Epic"],"weights":[40,60],"min_stars":100,"max_stars":1200},
    "fragment_nft": {"name":"💎 FRAGMENT NFT","price":1500,"rarities":["Epic","Legendary"],"weights":[45,55],"min_stars":300,"max_stars":4000},
    "durov_selection": {"name":"👑 DUROV'S SELECTION","price":5000,"rarities":["Legendary","Mythic"],"weights":[50,50],"min_stars":1000,"max_stars":30000}
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

# ===== DB =====
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS users (tg_id INTEGER PRIMARY KEY, username TEXT DEFAULT 'Player', balance INTEGER DEFAULT 50, total_spent INTEGER DEFAULT 0, inventory TEXT DEFAULT '[]', games_played INTEGER DEFAULT 0, wins INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
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
def verify_telegram(authorization: str = Header(None)):
    if not authorization or not BOT_TOKEN: raise HTTPException(401)
    try:
        data = urllib.parse.parse_qs(authorization)
        h = data.get('hash', [None])[0]
        if not h: raise HTTPException(401)
        sd = sorted([f"{k}={v[0]}" for k,v in data.items() if k!='hash'])
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        if hmac.new(secret, "\n".join(sd).encode(), hashlib.sha256).hexdigest() != h: raise HTTPException(401)
        return json.loads(data.get('user', ['{}'])[0])
    except: raise HTTPException(401)

def verify_admin(user=Depends(verify_telegram)):
    if user['id'] != ADMIN_TG_ID: raise HTTPException(403)
    return user

# ===== HELPERS =====
def calc_upgrade_chance(in_val, target):
    return max(1, min(60, (in_val/target)*100*(1-HOUSE_EDGE)))

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

# ===== HTML =====
HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>GiftUpgrader</title>
<script src="https://cdn.socket.io/4.5.0/socket.io.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
body{background:#0a0e17;color:#e8e8e8;min-height:100vh;padding-bottom:80px}
.app{max-width:480px;margin:0 auto;padding:12px}
.header{display:flex;justify-content:space-between;align-items:center;padding:8px 0 16px}
.logo{font-size:20px;font-weight:800;background:linear-gradient(135deg,#FFC107,#FF6B00);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.balance{background:rgba(255,193,7,0.12);border:1px solid rgba(255,193,7,0.2);padding:4px 14px;border-radius:16px;font-size:14px;font-weight:600;color:#FFC107;display:flex;align-items:center;gap:4px}
.tabs{display:flex;gap:4px;background:rgba(255,255,255,0.04);border-radius:14px;padding:4px;margin-bottom:16px;overflow-x:auto}
.tab{flex:1;min-width:52px;padding:8px 4px;border:none;background:transparent;color:#8899AA;font-size:11px;font-weight:600;border-radius:10px;cursor:pointer;transition:.3s;text-align:center;white-space:nowrap}
.tab.active{background:linear-gradient(135deg,#FFC107,#FF6B00);color:#0a0e17;box-shadow:0 4px 16px rgba(255,193,7,0.2)}
.tab:active{transform:scale(.95)}
.tab-content{display:none;animation:fade .3s}
.tab-content.active{display:block}
@keyframes fade{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
.glass{background:rgba(255,255,255,0.04);backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,0.06);border-radius:16px;padding:16px;margin-bottom:12px}
.btn{width:100%;padding:14px;border:none;border-radius:12px;font-size:16px;font-weight:700;cursor:pointer;transition:.3s;background:linear-gradient(135deg,#FFC107,#FF6B00);color:#0a0e17}
.btn:active{transform:scale(.97)}
.btn:disabled{opacity:.5;cursor:not-allowed}
.gift-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:12px 0}
.gift-card{background:rgba(255,255,255,0.04);border-radius:12px;padding:12px;text-align:center;border:1px solid rgba(255,255,255,0.06);cursor:pointer;transition:.3s}
.gift-card.selected{border-color:#FFC107;box-shadow:0 0 20px rgba(255,193,7,0.1)}
.gift-card .emoji{font-size:36px;display:block}
.gift-card .name{font-size:12px;font-weight:500;margin:4px 0}
.gift-card .value{font-size:11px;color:#8899AA}
.gift-card .rarity{font-size:9px;padding:2px 8px;border-radius:8px;display:inline-block;margin-top:4px}
.wheel-wrap{position:relative;width:100%;max-width:320px;margin:0 auto 12px;aspect-ratio:1/1}
.wheel{width:100%;height:100%;border-radius:50%;transition:transform 4s cubic-bezier(0.15,0.90,0.25,1.00);box-shadow:0 0 40px rgba(255,193,7,0.05)}
.wheel canvas{width:100%;height:100%;border-radius:50%;display:block}
.wheel-pointer{position:absolute;top:-10px;left:50%;transform:translateX(-50%);width:0;height:0;border-left:14px solid transparent;border-right:14px solid transparent;border-top:24px solid #FFC107;filter:drop-shadow(0 4px 12px rgba(255,193,7,0.4));z-index:10}
.wheel-center{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:48px;height:48px;border-radius:50%;background:radial-gradient(circle,#1a2a3f,#0a0e17);border:2px solid rgba(255,193,7,0.25);display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:#FFC107;z-index:5}
.wheel-glow{position:absolute;inset:-6px;border-radius:50%;pointer-events:none;transition:.6s;opacity:0}
.wheel-glow.success{opacity:1;box-shadow:0 0 50px rgba(76,175,80,0.3),inset 0 0 50px rgba(76,175,80,0.05)}
.wheel-glow.fail{opacity:1;box-shadow:0 0 50px rgba(244,67,54,0.3),inset 0 0 50px rgba(244,67,54,0.05)}
.upgrade-info{display:flex;justify-content:center;gap:20px;padding:8px 0}
.upgrade-info .stat{text-align:center}
.upgrade-info .stat .label{font-size:10px;color:#8899AA;text-transform:uppercase}
.upgrade-info .stat .value{font-size:20px;font-weight:700}
.upgrade-info .stat .value.gold{color:#FFC107}
.upgrade-info .stat .value.green{color:#4CAF50}
.cases-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.case-card{background:rgba(255,255,255,0.04);border-radius:12px;padding:14px;text-align:center;border:1px solid rgba(255,255,255,0.06);cursor:pointer;transition:.3s}
.case-card:active{transform:scale(.96)}
.case-card .icon{font-size:28px}
.case-card .name{font-size:12px;font-weight:600;margin:4px 0}
.case-card .price{font-size:13px;font-weight:600;color:#FFC107}
.case-card .rarities{font-size:10px;color:#8899AA}
.case-card .range{font-size:10px;color:#4CAF50}
.case-card.free{border-color:rgba(76,175,80,0.2)}
.case-card .cooldown{font-size:10px;color:#F44336}
.inv-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.inv-item{background:rgba(255,255,255,0.04);border-radius:10px;padding:10px;text-align:center;border:1px solid rgba(255,255,255,0.06);cursor:pointer;transition:.3s}
.inv-item:active{transform:scale(.95)}
.inv-item .emoji{font-size:28px}
.inv-item .name{font-size:10px;font-weight:500;margin:2px 0}
.inv-item .val{font-size:9px;color:#8899AA}
.inv-item .sell{font-size:9px;padding:2px 10px;border:none;border-radius:6px;background:rgba(244,67,54,0.15);color:#F44336;cursor:pointer;margin-top:4px}
.inv-item.selected{border-color:#FFC107}
.mines-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:5px;max-width:320px;margin:10px auto}
.mine-cell{aspect-ratio:1;background:rgba(255,255,255,0.06);border-radius:8px;border:1px solid rgba(255,255,255,0.05);display:flex;align-items:center;justify-content:center;font-size:18px;cursor:pointer;transition:.3s;color:#8899AA}
.mine-cell:active{transform:scale(.92)}
.mine-cell.opened{background:rgba(76,175,80,0.1);border-color:rgba(76,175,80,0.15)}
.mine-cell.bomb{background:rgba(244,67,54,0.15);border-color:rgba(244,67,54,0.2);color:#F44336}
.mine-cell .gem{color:#FFC107}
.mines-info{display:flex;justify-content:space-between;padding:6px 0;font-size:13px}
.mines-info .btn{padding:6px 16px;width:auto;font-size:12px;border-radius:8px}
.crash-graph{background:rgba(0,0,0,0.3);border-radius:12px;padding:12px;height:140px;margin-bottom:10px}
.crash-graph canvas{width:100%;height:100%}
.crash-mult{font-size:36px;font-weight:800;text-align:center;color:#FFC107;padding:4px 0}
.crash-status{text-align:center;font-size:13px;color:#8899AA;padding:4px 0}
.crash-row{display:flex;gap:8px;align-items:center}
.crash-row input{flex:1;padding:10px 14px;background:rgba(0,0,0,0.3);border:1px solid rgba(255,255,255,0.06);border-radius:10px;color:#e8e8e8;font-size:14px}
.crash-row input:focus{outline:none;border-color:#FFC107}
.crash-row .btn{padding:10px 18px;width:auto;font-size:13px;border-radius:10px}
.btn-crash{background:linear-gradient(135deg,#4CAF50,#2E7D32);color:#fff}
.btn-cashout{background:linear-gradient(135deg,#FFC107,#FF6B00);color:#0a0e17}
.crash-bets{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.crash-bets span{font-size:11px;padding:2px 10px;border-radius:8px;background:rgba(255,255,255,0.04)}
.promo-row{display:flex;gap:8px;margin-top:10px}
.promo-row input{flex:1;padding:10px 14px;background:rgba(0,0,0,0.3);border:1px solid rgba(255,255,255,0.06);border-radius:10px;color:#e8e8e8;font-size:14px;text-transform:uppercase}
.promo-row .btn{padding:10px 18px;width:auto;font-size:13px;border-radius:10px}
.toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);z-index:999;width:90%;max-width:400px;padding:12px 18px;border-radius:12px;background:rgba(22,31,46,.95);border:1px solid rgba(255,255,255,0.08);text-align:center;font-weight:500;font-size:14px;animation:toastIn .3s;display:none}
.toast.success{border-color:#4CAF50;color:#4CAF50}
.toast.error{border-color:#F44336;color:#F44336}
.toast.info{border-color:#FFC107;color:#FFC107}
@keyframes toastIn{from{opacity:0;transform:translateX(-50%) translateY(20px)}to{opacity:1;transform:translateX(-50%) translateY(0)}}
.empty{text-align:center;color:#8899AA;padding:20px 0;font-size:13px}
.ref-card{text-align:center;padding:12px 0}
.ref-card .big{font-size:40px;font-weight:800;color:#FFC107}
.ref-card .link{background:rgba(0,0,0,0.3);padding:8px 14px;border-radius:10px;margin:10px 0;font-size:13px;word-break:break-all}
.profile-stats{display:flex;justify-content:center;gap:20px;padding:10px 0}
.profile-stats div{text-align:center}
.profile-stats .num{font-size:18px;font-weight:700;color:#FFC107}
.profile-stats .lbl{font-size:10px;color:#8899AA}
</style>
</head>
<body>

<div class="app">
  <div class="header">
    <div class="logo">🎮 GiftUpgrader</div>
    <div class="balance" id="balance">⭐ 0</div>
  </div>

  <div class="tabs" id="tabs">
    <button class="tab active" data-tab="upgrade">Апгрейд</button>
    <button class="tab" data-tab="cases">Кейсы</button>
    <button class="tab" data-tab="mines">Мины</button>
    <button class="tab" data-tab="crash">Ракетка</button>
    <button class="tab" data-tab="inventory">Инвентарь</button>
    <button class="tab" data-tab="profile">Профиль</button>
  </div>

  <!-- UPGRADE -->
  <div class="tab-content active" id="tab-upgrade">
    <div class="glass">
      <div class="gift-grid">
        <div class="gift-card selected" id="inputCard">
          <span class="emoji" id="inEmoji">🧸</span>
          <div class="name" id="inName">Plush Bear</div>
          <div class="value" id="inValue">⭐ 15</div>
          <span class="rarity" id="inRarity" style="background:rgba(139,139,139,0.15);color:#8B8B8B">Common</span>
        </div>
        <div class="gift-card" id="targetCard">
          <span class="emoji" id="tEmoji">💎</span>
          <div class="name" id="tName">Diamond Ring</div>
          <div class="value" id="tValue">⭐ 100</div>
          <span class="rarity" id="tRarity" style="background:rgba(76,175,80,0.15);color:#4CAF50">Uncommon</span>
        </div>
      </div>

      <div class="wheel-wrap">
        <div class="wheel-glow" id="wheelGlow"></div>
        <div class="wheel" id="wheel"><canvas id="wheelCanvas" width="400" height="400"></canvas></div>
        <div class="wheel-pointer"></div>
        <div class="wheel-center" id="wheelCenter">?</div>
      </div>

      <div class="upgrade-info">
        <div class="stat"><div class="label">Шанс</div><div class="value gold" id="chanceDisplay">35%</div></div>
        <div class="stat"><div class="label">Множитель</div><div class="value green" id="multDisplay">2.5x</div></div>
      </div>

      <button class="btn" id="upgradeBtn">⬆️ АПГРЕЙД</button>
    </div>
  </div>

  <!-- CASES -->
  <div class="tab-content" id="tab-cases">
    <div class="glass"><div class="cases-grid" id="casesGrid"></div></div>
  </div>

  <!-- MINES -->
  <div class="tab-content" id="tab-mines">
    <div class="glass">
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
        <input type="number" id="minesBet" placeholder="Ставка" value="10" style="flex:1;padding:10px 12px;background:rgba(0,0,0,0.3);border:1px solid rgba(255,255,255,0.06);border-radius:10px;color:#e8e8e8;font-size:14px">
        <input type="number" id="minesCount" placeholder="Мин" value="3" min="1" max="24" style="flex:1;padding:10px 12px;background:rgba(0,0,0,0.3);border:1px solid rgba(255,255,255,0.06);border-radius:10px;color:#e8e8e8;font-size:14px">
        <button class="btn" id="minesStart" style="width:auto;padding:10px 18px;font-size:13px;background:linear-gradient(135deg,#4CAF50,#2E7D32);color:#fff">Старт</button>
      </div>
      <div class="mines-grid" id="minesGrid"></div>
      <div class="mines-info">
        <span>Множитель: <strong id="minesMult">1.00x</strong></span>
        <span>Открыто: <strong id="minesOpened">0</strong></span>
        <button class="btn" id="minesCashout" style="display:none;background:linear-gradient(135deg,#FFC107,#FF6B00);color:#0a0e17">Забрать</button>
      </div>
    </div>
  </div>

  <!-- CRASH -->
  <div class="tab-content" id="tab-crash">
    <div class="glass">
      <div class="crash-graph"><canvas id="crashCanvas"></canvas></div>
      <div class="crash-mult" id="crashMult">1.00x</div>
      <div class="crash-status" id="crashStatus">Ожидание ставок...</div>
      <div class="crash-row">
        <input type="number" id="crashBet" placeholder="Ставка" value="25" min="25" max="5000">
        <button class="btn btn-crash" id="crashBetBtn">Ставка</button>
        <button class="btn btn-cashout" id="crashCashBtn" style="display:none">Забрать</button>
      </div>
      <div class="crash-bets" id="crashBets"></div>
    </div>
  </div>

  <!-- INVENTORY -->
  <div class="tab-content" id="tab-inventory">
    <div class="glass">
      <div class="inv-grid" id="invGrid"></div>
      <div class="empty" id="invEmpty">Инвентарь пуст</div>
    </div>
  </div>

  <!-- PROFILE -->
  <div class="tab-content" id="tab-profile">
    <div class="glass">
      <div style="text-align:center;padding:8px 0">
        <div style="font-size:40px">👤</div>
        <div style="font-size:18px;font-weight:600" id="profileName">Player</div>
        <div style="font-size:12px;color:#8899AA" id="profileId">ID: 0</div>
        <div class="profile-stats">
          <div><div class="num" id="pBalance">0</div><div class="lbl">Баланс</div></div>
          <div><div class="num" id="pGames">0</div><div class="lbl">Игр</div></div>
          <div><div class="num" id="pWins" style="color:#4CAF50">0</div><div class="lbl">Побед</div></div>
        </div>
      </div>

      <div class="ref-card">
        <div class="big">🎯</div>
        <div style="font-weight:600;font-size:14px">Реферальная программа</div>
        <div style="font-size:12px;color:#8899AA">7% от депозитов друзей</div>
        <div style="display:flex;justify-content:center;gap:20px;margin:8px 0">
          <div><div style="font-size:16px;font-weight:700" id="refCount">0</div><div style="font-size:10px;color:#8899AA">Приглашено</div></div>
          <div><div style="font-size:16px;font-weight:700;color:#FFC107" id="refEarned">0</div><div style="font-size:10px;color:#8899AA">Заработано</div></div>
        </div>
        <div class="link" id="refLink">Загрузка...</div>
        <button class="btn" id="copyRef" style="width:auto;padding:8px 20px;font-size:12px;background:rgba(255,193,7,0.12);color:#FFC107;border:1px solid rgba(255,193,7,0.15)">📋 Копировать</button>
      </div>

      <div class="promo-row">
        <input type="text" id="promoInput" placeholder="Промокод">
        <button class="btn" id="promoBtn" style="width:auto;padding:10px 18px;font-size:13px">Активировать</button>
      </div>

      <button class="btn" id="withdrawBtn" style="margin-top:10px;background:rgba(244,67,54,0.12);color:#F44336;border:1px solid rgba(244,67,54,0.15)">💳 Вывести звёзды</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const STATE = {
  tgId:0, username:'Player', balance:50, inventory:[], gamesPlayed:0, wins:0,
  selectedItem:0, targetValue:80, isUpgrading:false,
  mines:{gameId:null,opened:[],bombs:[],multiplier:1,cashedOut:false,started:false},
  crash:{connected:false,socket:null,betPlaced:false},
  cases:{}, freeCase:true
};

function showToast(msg,type='info'){
  const t=document.getElementById('toast');
  t.textContent=msg; t.className='toast '+type; t.style.display='block';
  setTimeout(()=>t.style.display='none',3000);
}

async function api(method,url,body=null){
  const h={'Content-Type':'application/json'};
  if(window.Telegram?.WebApp?.initData) h.Authorization=window.Telegram.WebApp.initData;
  const res=await fetch(url,{method,headers:h,body:body?JSON.stringify(body):null});
  if(!res.ok){const e=await res.json().catch(()=>({}));throw new Error(e.detail||'API Error');}
  return res.json();
}

function initTelegram(){
  if(window.Telegram?.WebApp){
    const tg=window.Telegram.WebApp; tg.expand(); tg.enableClosingConfirmation();
    const u=tg.initDataUnsafe?.user;
    if(u){STATE.tgId=u.id; STATE.username=u.first_name||'Player'; document.getElementById('profileName').textContent=STATE.username; document.getElementById('profileId').textContent='ID: '+STATE.tgId;}
    window.tgHaptic={impact:s=>{try{tg.HapticFeedback.impactOccurred(s)}catch(e){}},notify:t=>{try{tg.HapticFeedback.notificationOccurred(t)}catch(e){}}};
  }else{window.tgHaptic={impact:()=>{},notify:()=>{}};}
}

function updateBalance(){document.getElementById('balance').textContent='⭐ '+STATE.balance; document.getElementById('pBalance').textContent=STATE.balance;}

async function loadProfile(){
  try{
    const d=await api('GET','/api/profile');
    STATE.balance=d.balance||0; STATE.inventory=d.inventory||[]; STATE.gamesPlayed=d.games_played||0; STATE.wins=d.wins||0;
    STATE.freeCase=d.free_case_available!==false;
    updateBalance(); renderInv(); loadCases(); loadGifts(); updateUpgrade();
    document.getElementById('pGames').textContent=STATE.gamesPlayed; document.getElementById('pWins').textContent=STATE.wins;
    loadRef();
  }catch(e){showToast('Ошибка загрузки','error')}
}

// CASES
async function loadCases(){
  try{const d=await api('GET','/api/cases'); STATE.cases=d; renderCases();}catch(e){}
}

function renderCases(){
  const g=document.getElementById('casesGrid'); g.innerHTML='';
  for(const [id,c] of Object.entries(STATE.cases)){
    const div=document.createElement('div');
    div.className='case-card'+(id==='free_daily'?' free':'');
    div.innerHTML=`
      <div class="icon">🎁</div>
      <div class="name">${c.name}</div>
      <div class="price">${c.price===0?'🎁 БЕСПЛАТНО':'⭐ '+c.price}</div>
      <div class="rarities">${c.rarities.join(' • ')}</div>
      <div class="range">⭐ ${c.min_stars||0} - ${c.max_stars||0}</div>
      ${id==='free_daily'?(STATE.freeCase?'<div style="color:#4CAF50;font-size:10px">✅ Доступен</div>':'<div class="cooldown">⏳ 24ч</div>'):''}
    `;
    div.onclick=()=>openCase(id);
    g.appendChild(div);
  }
}

async function openCase(id){
  try{
    const d=await api('POST','/api/case/open',{case_id:id});
    if(d.success){
      STATE.balance=d.balance||STATE.balance; updateBalance();
      const p=await api('GET','/api/profile'); STATE.inventory=p.inventory||[]; renderInv(); updateUpgrade();
      if(d.stars_earned) showToast('⭐ +'+d.stars_earned+' звёзд','success');
      else if(d.gift) showToast('🎁 '+d.gift.name+' ('+d.rarity+')','success');
      window.tgHaptic?.notify('success');
      if(id==='free_daily'){STATE.freeCase=false; renderCases();}
    }
  }catch(e){showToast('Ошибка: '+e.message,'error')}
}

// UPGRADE
let giftsData=null, wheelRotation=0;

async function loadGifts(){
  try{const d=await api('GET','/api/gifts'); giftsData=d; const u=d.gifts.Uncommon; if(u?.length) STATE.targetValue=u[0].value; updateUpgrade();}catch(e){}
}

function updateUpgrade(){
  const inv=STATE.inventory;
  if(!inv.length){
    document.getElementById('inEmoji').textContent='❌'; document.getElementById('inName').textContent='Нет предметов';
    document.getElementById('inValue').textContent='⭐ 0'; document.getElementById('inRarity').textContent='—';
    document.getElementById('chanceDisplay').textContent='0%'; document.getElementById('multDisplay').textContent='0x';
    return;
  }
  const idx=Math.min(STATE.selectedItem, inv.length-1);
  const item=inv[idx];
  document.getElementById('inEmoji').textContent=item.emoji||'🎁';
  document.getElementById('inName').textContent=item.name||'Item';
  document.getElementById('inValue').textContent='⭐ '+(item.value||0);
  const r=item.rarity||'Common';
  const cols={'Common':{bg:'rgba(139,139,139,0.15)',c:'#8B8B8B'},'Uncommon':{bg:'rgba(76,175,80,0.15)',c:'#4CAF50'},'Rare':{bg:'rgba(33,150,243,0.15)',c:'#2196F3'},'Epic':{bg:'rgba(156,39,176,0.15)',c:'#9C27B0'},'Legendary':{bg:'rgba(255,193,7,0.15)',c:'#FFC107'},'Mythic':{bg:'rgba(244,67,54,0.15)',c:'#F44336'}};
  const cl=cols[r]||cols.Common;
  document.getElementById('inRarity').textContent=r; document.getElementById('inRarity').style.background=cl.bg; document.getElementById('inRarity').style.color=cl.c;

  let target=null;
  if(giftsData){for(const [r2,list] of Object.entries(giftsData.gifts)){for(const g of list){if(g.value===STATE.targetValue){target={...g,rarity:r2};break}}if(target)break}}
  if(!target){const u=giftsData?.gifts?.Uncommon?.[0]; if(u){target={...u,rarity:'Uncommon'}; STATE.targetValue=u.value;}}
  if(target){
    document.getElementById('tEmoji').textContent=target.emoji||'🎁';
    document.getElementById('tName').textContent=target.name||'Target';
    document.getElementById('tValue').textContent='⭐ '+target.value;
    const tc=cols[target.rarity]||cols.Common;
    document.getElementById('tRarity').textContent=target.rarity; document.getElementById('tRarity').style.background=tc.bg; document.getElementById('tRarity').style.color=tc.c;
  }

  const iv=item.value||1, tv=target?.value||1;
  const ch=Math.min((iv/tv)*100*0.95,60);
  document.getElementById('chanceDisplay').textContent=ch.toFixed(1)+'%';
  document.getElementById('multDisplay').textContent=((tv/iv)*0.95).toFixed(2)+'x';
  drawWheel(ch);
}

function drawWheel(chance){
  const canvas=document.getElementById('wheelCanvas');
  const ctx=canvas.getContext('2d');
  const w=canvas.width,h=canvas.height,cx=w/2,cy=h/2,r=w/2-4;
  ctx.clearRect(0,0,w,h);
  const win=(chance/100)*2*Math.PI, lose=2*Math.PI-win;
  let start=-Math.PI/2;
  ctx.beginPath(); ctx.moveTo(cx,cy); ctx.arc(cx,cy,r,start,start+win); ctx.closePath();
  const gw=ctx.createRadialGradient(cx,cy,0,cx,cy,r); gw.addColorStop(0,'#4CAF50'); gw.addColorStop(1,'#1B5E20');
  ctx.fillStyle=gw; ctx.fill(); ctx.strokeStyle='rgba(255,255,255,0.05)'; ctx.lineWidth=1; ctx.stroke();
  start+=win;
  ctx.beginPath(); ctx.moveTo(cx,cy); ctx.arc(cx,cy,r,start,start+lose); ctx.closePath();
  const gl=ctx.createRadialGradient(cx,cy,0,cx,cy,r); gl.addColorStop(0,'#F44336'); gl.addColorStop(1,'#880E4F');
  ctx.fillStyle=gl; ctx.fill(); ctx.stroke();
  ctx.beginPath(); ctx.arc(cx,cy,r,0,2*Math.PI); ctx.strokeStyle='rgba(255,255,255,0.08)'; ctx.lineWidth=2; ctx.stroke();
  ctx.beginPath(); ctx.arc(cx,cy,r*0.15,0,2*Math.PI); ctx.fillStyle='rgba(13,18,29,0.6)'; ctx.fill();
  ctx.fillStyle='rgba(255,255,255,0.7)'; ctx.font='bold 14px sans-serif'; ctx.textAlign='center'; ctx.textBaseline='middle';
  const la=-Math.PI/2+win/2; ctx.fillText('WIN',cx+Math.cos(la)*r*0.6,cy+Math.sin(la)*r*0.6);
  const lla=-Math.PI/2+win+lose/2; ctx.fillText('LOSE',cx+Math.cos(lla)*r*0.6,cy+Math.sin(lla)*r*0.6);
  for(let i=0;i<24;i++){const a=(i/24)*2*Math.PI-Math.PI/2; ctx.beginPath(); ctx.moveTo(cx+Math.cos(a)*r*0.94,cy+Math.sin(a)*r*0.94); ctx.lineTo(cx+Math.cos(a)*r*0.98,cy+Math.sin(a)*r*0.98); ctx.strokeStyle='rgba(255,255,255,0.1)'; ctx.lineWidth=2; ctx.stroke();}
}

async function runUpgrade(){
  if(STATE.isUpgrading||!STATE.inventory.length) return;
  const idx=Math.min(STATE.selectedItem, STATE.inventory.length-1);
  const item=STATE.inventory[idx];
  if(!item||item.value>=STATE.targetValue){showToast('Цель должна быть дороже','error'); return;}
  STATE.isUpgrading=true; const btn=document.getElementById('upgradeBtn'); btn.classList.add('loading'); btn.disabled=true;
  document.getElementById('wheelGlow').className='wheel-glow';
  try{
    const d=await api('POST','/api/upgrade',{item_index:idx,target_value:STATE.targetValue});
    const angle=d.angle||0, win=d.success||false;
    wheelRotation+=360*5+(angle%360);
    document.getElementById('wheel').style.transform='rotate('+wheelRotation+'deg)';
    const glow=document.getElementById('wheelGlow');
    if(win){glow.className='wheel-glow success'; window.tgHaptic?.notify('success');}else{glow.className='wheel-glow fail'; window.tgHaptic?.notify('error');}
    if(d.balance!==undefined){STATE.balance=d.balance; updateBalance();}
    setTimeout(async()=>{
      try{const p=await api('GET','/api/profile'); STATE.inventory=p.inventory||[]; STATE.balance=p.balance||0; updateBalance(); renderInv(); updateUpgrade(); showToast(d.message||(win?'🎉 Успешно!':'💔 Неудача'),win?'success':'error');}catch(e){}
      STATE.isUpgrading=false; btn.classList.remove('loading'); btn.disabled=false; document.getElementById('wheelGlow').className='wheel-glow';
    },4500);
  }catch(e){showToast('Ошибка: '+e.message,'error'); STATE.isUpgrading=false; btn.classList.remove('loading'); btn.disabled=false; document.getElementById('wheelGlow').className='wheel-glow';}
}

// INVENTORY
function renderInv(){
  const g=document.getElementById('invGrid'), e=document.getElementById('invEmpty');
  g.innerHTML='';
  if(!STATE.inventory.length){e.style.display='block'; return;}
  e.style.display='none';
  STATE.inventory.forEach((item,idx)=>{
    const div=document.createElement('div');
    div.className='inv-item'+(idx===STATE.selectedItem?' selected':'');
    const cols={'Common':'rgba(139,139,139,0.15)','Uncommon':'rgba(76,175,80,0.15)','Rare':'rgba(33,150,243,0.15)','Epic':'rgba(156,39,176,0.15)','Legendary':'rgba(255,193,7,0.15)','Mythic':'rgba(244,67,54,0.15)'};
    div.style.borderColor=cols[item.rarity]||'rgba(255,255,255,0.05)';
    div.innerHTML=`<div class="emoji">${item.emoji||'🎁'}</div><div class="name">${item.name}</div><div class="val">⭐ ${item.value}</div><button class="sell" data-idx="${idx}">Продать</button>`;
    div.onclick=(e)=>{if(e.target.classList.contains('sell'))return; STATE.selectedItem=idx; renderInv(); updateUpgrade();};
    div.querySelector('.sell').onclick=async(e)=>{e.stopPropagation(); await sellItem(idx);};
    g.appendChild(div);
  });
}

async function sellItem(idx){
  try{
    const d=await api('POST','/api/inventory/sell',{item_index:idx});
    if(d.success){STATE.balance=d.balance||STATE.balance; updateBalance(); const p=await api('GET','/api/profile'); STATE.inventory=p.inventory||[]; renderInv(); updateUpgrade(); showToast('💰 +'+d.price+' ⭐','success'); window.tgHaptic?.impact('light');}
  }catch(e){showToast('Ошибка: '+e.message,'error')}
}

// MINES
const mines={grid:[],opened:[],bombs:[],gameId:null,bet:0,multiplier:1,cashedOut:false,started:false};

async function startMines(){
  const bet=parseInt(document.getElementById('minesBet').value)||10;
  const cnt=parseInt(document.getElementById('minesCount').value)||3;
  if(cnt<1||cnt>24){showToast('Мины 1-24','error'); return;}
  try{
    const d=await api('POST','/api/mines/start',{bet,mines:cnt});
    STATE.balance=d.balance||STATE.balance; updateBalance();
    mines.gameId=d.game_id; mines.bet=d.bet; mines.started=true; mines.opened=[]; mines.cashedOut=false; mines.multiplier=1; mines.bombs=[]; mines.grid=Array(25).fill(0);
    renderMines();
    document.getElementById('minesMult').textContent='1.00x'; document.getElementById('minesOpened').textContent='0'; document.getElementById('minesCashout').style.display='none';
    showToast('💣 Игра начата','info');
  }catch(e){showToast('Ошибка: '+e.message,'error')}
}

async function openMine(idx){
  if(!mines.started||mines.cashedOut||mines.opened.includes(idx)) return;
  try{
    const d=await api('POST','/api/mines/open',{game_id:mines.gameId,cell:idx});
    if(d.status==='bomb'){mines.cashedOut=true; mines.bombs=d.mines||[]; renderMines(); showToast('💥 Бомба!','error'); window.tgHaptic?.notify('error'); document.getElementById('minesCashout').style.display='none'; const p=await api('GET','/api/profile'); STATE.balance=p.balance||0; updateBalance(); return;}
    mines.opened=d.opened||[]; mines.multiplier=d.multiplier||1;
    document.getElementById('minesMult').textContent=mines.multiplier.toFixed(2)+'x'; document.getElementById('minesOpened').textContent=mines.opened.length;
    renderMines();
    if(mines.opened.length) document.getElementById('minesCashout').style.display='block';
    window.tgHaptic?.impact('light');
  }catch(e){showToast('Ошибка: '+e.message,'error')}
}

async function cashoutMines(){
  if(!mines.started||mines.cashedOut||!mines.opened.length){showToast('Откройте клетку','error'); return;}
  try{
    const d=await api('POST','/api/mines/cashout',{game_id:mines.gameId});
    STATE.balance=d.balance||STATE.balance; updateBalance(); mines.cashedOut=true;
    showToast('💰 Выигрыш: '+d.win+' ⭐ (x'+d.multiplier+')','success'); window.tgHaptic?.notify('success');
    document.getElementById('minesCashout').style.display='none'; renderMines();
  }catch(e){showToast('Ошибка: '+e.message,'error')}
}

function renderMines(){
  const g=document.getElementById('minesGrid'); g.innerHTML='';
  for(let i=0;i<25;i++){
    const cell=document.createElement('div'); cell.className='mine-cell';
    if(mines.opened.includes(i)){cell.classList.add('opened'); cell.textContent='💎';}
    if(mines.cashedOut&&mines.bombs&&mines.bombs.includes(i)){cell.classList.add('bomb'); cell.textContent='💣';}
    if(mines.cashedOut&&!mines.opened.includes(i)&&!(mines.bombs&&mines.bombs.includes(i))){cell.textContent='💎'; cell.style.opacity='0.5';}
    cell.onclick=()=>openMine(i);
    g.appendChild(cell);
  }
}

// CRASH
function initCrash(){
  const socket=io(); STATE.crash.socket=socket;
  socket.on('connect',()=>{STATE.crash.connected=true;});
  socket.on('crash_state',(d)=>{
    document.getElementById('crashStatus').textContent=d.status==='betting'?'⌛ Ставки: '+d.timer+'с':d.status==='flying'?'🚀 Взлёт!':d.status==='crashed'?'💥 Крах!':'⏳ Ожидание...';
    if(d.status==='betting') document.getElementById('crashMult').textContent='1.00x';
    if(d.history) drawCrashHistory(d.history);
  });
  socket.on('crash_multiplier',(d)=>{document.getElementById('crashMult').textContent=d.multiplier.toFixed(2)+'x';});
  socket.on('crash_start',()=>{document.getElementById('crashStatus').textContent='🚀 Взлёт!'; document.getElementById('crashBetBtn').disabled=true; document.getElementById('crashCashBtn').style.display='block'; STATE.crash.betPlaced=true;});
  socket.on('crash_end',(d)=>{document.getElementById('crashStatus').textContent='💥 Крах на '+d.crash_point.toFixed(2)+'x'; document.getElementById('crashBetBtn').disabled=false; document.getElementById('crashCashBtn').style.display='none'; STATE.crash.betPlaced=false; loadProfile(); if(d.bets){document.getElementById('crashBets').innerHTML=d.bets.map(b=>`<span>${b.username}: ${b.win>0?'✅+'+b.win:'❌0'}</span>`).join('');}});
  socket.on('cashout_success',(d)=>{showToast('💰 +'+d.win+' ⭐','success'); window.tgHaptic?.notify('success'); STATE.balance=d.balance||STATE.balance; updateBalance(); document.getElementById('crashCashBtn').style.display='none';});
  socket.on('bet_placed',(d)=>{showToast('✅ Ставка '+d.amount+' ⭐','success'); STATE.balance=d.balance||STATE.balance; updateBalance();});
  socket.on('error',(d)=>{showToast('❌ '+d.message,'error');});
}

function placeCrashBet(){
  if(!STATE.crash.connected){showToast('Подключение...','info'); return;}
  const amt=parseInt(document.getElementById('crashBet').value)||25;
  if(amt<25||amt>5000){showToast('Ставка 25-5000 ⭐','error'); return;}
  if(amt>STATE.balance){showToast('Недостаточно','error'); return;}
  STATE.crash.socket.emit('place_bet',{tg_id:STATE.tgId,amount:amt,username:STATE.username});
}

function crashCashout(){
  if(!STATE.crash.connected) return;
  STATE.crash.socket.emit('cashout',{tg_id:STATE.tgId});
}

function drawCrashHistory(history){
  const canvas=document.getElementById('crashCanvas');
  const ctx=canvas.getContext('2d');
  canvas.width=canvas.parentElement.clientWidth; canvas.height=canvas.parentElement.clientHeight;
  const w=canvas.width,h=canvas.height;
  ctx.clearRect(0,0,w,h);
  if(!history||history.length<2){ctx.fillStyle='#8899AA'; ctx.font='14px sans-serif'; ctx.textAlign='center'; ctx.fillText('История раундов',w/2,h/2); return;}
  const max=Math.max(2,...history), min=1, step=w/(history.length-1);
  ctx.beginPath(); ctx.strokeStyle='#FFC107'; ctx.lineWidth=2;
  for(let i=0;i<history.length;i++){const x=i*step, y=h-((history[i]-min)/(max-min))*(h-20)-10; if(i===0)ctx.moveTo(x,y); else ctx.lineTo(x,y);}
  ctx.stroke(); ctx.lineTo(w,h); ctx.lineTo(0,h); ctx.closePath();
  const grad=ctx.createLinearGradient(0,0,0,h); grad.addColorStop(0,'rgba(255,193,7,0.15)'); grad.addColorStop(1,'rgba(255,193,7,0)');
  ctx.fillStyle=grad; ctx.fill();
}

// REFERRALS
async function loadRef(){
  try{
    const d=await api('GET','/api/referral/stats');
    document.getElementById('refCount').textContent=d.referrals_count||0;
    document.getElementById('refEarned').textContent=d.total_earned||0;
    const link='https://t.me/'+(window.Telegram?.WebApp?.initDataUnsafe?.user?.username||'GiftUpgraderBot')+'?start=ref_'+STATE.tgId;
    document.getElementById('refLink').textContent=link; window._refLink=link;
  }catch(e){}
}

function copyRef(){
  const l=window._refLink||'';
  if(navigator.clipboard){navigator.clipboard.writeText(l).then(()=>showToast('📋 Скопировано!','success'));}
  else{const t=document.createElement('textarea'); t.value=l; document.body.appendChild(t); t.select(); document.execCommand('copy'); document.body.removeChild(t); showToast('📋 Скопировано!','success');}
}

// PROMO
async function activatePromo(){
  const code=document.getElementById('promoInput').value.trim().toUpperCase();
  if(!code){showToast('Введите промокод','error'); return;}
  try{
    const d=await api('POST','/api/promo/activate?code='+encodeURIComponent(code),{});
    showToast(d.message||'✅ Активирован!','success'); window.tgHaptic?.notify('success'); loadProfile(); document.getElementById('promoInput').value='';
  }catch(e){showToast('Ошибка: '+e.message,'error')}
}

// WITHDRAW
async function withdraw(){
  const amt=prompt('Сумма (мин 100 ⭐):','100');
  if(!amt)return; const v=parseInt(amt);
  if(isNaN(v)||v<100){showToast('Минимум 100 ⭐','error'); return;}
  const wallet=prompt('Адрес кошелька (TON/TRC20):','');
  if(!wallet||wallet.length<10){showToast('Некорректный адрес','error'); return;}
  try{const d=await api('POST','/api/withdraw',{amount:v,wallet}); showToast('✅ Заявка создана!','success'); STATE.balance=d.balance||STATE.balance; updateBalance();}catch(e){showToast('Ошибка: '+e.message,'error')}
}

// TABS
function initTabs(){
  document.querySelectorAll('.tab').forEach(b=>{
    b.onclick=function(){
      document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(t=>t.classList.remove('active'));
      this.classList.add('active'); document.getElementById('tab-'+this.dataset.tab).classList.add('active');
      if(this.dataset.tab==='inventory') renderInv();
      if(this.dataset.tab==='profile') loadProfile();
      if(this.dataset.tab==='crash'&&!STATE.crash.connected) initCrash();
    };
  });
}

// INIT
document.addEventListener('DOMContentLoaded',()=>{
  initTelegram(); loadProfile(); initTabs(); renderInv(); updateUpgrade();
  document.getElementById('upgradeBtn').onclick=runUpgrade;
  document.getElementById('minesStart').onclick=startMines;
  document.getElementById('minesCashout').onclick=cashoutMines;
  document.getElementById('crashBetBtn').onclick=placeCrashBet;
  document.getElementById('crashCashBtn').onclick=crashCashout;
  document.getElementById('promoBtn').onclick=activatePromo;
  document.getElementById('copyRef').onclick=copyRef;
  document.getElementById('withdrawBtn').onclick=withdraw;
  document.getElementById('promoInput').onkeydown=e=>{if(e.key==='Enter')activatePromo();};
  if(document.querySelector('.tab[data-tab="crash"]').classList.contains('active')) initCrash();
});
</script>
</body>
</html>
"""

# ===== ADMIN HTML =====
ADMIN_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Admin</title>
<style>*{margin:0;padding:0;box-sizing:border-box}body{background:#0D121D;color:#E8E8E8;font-family:sans-serif;padding:20px}.container{max-width:1200px;margin:0 auto}.header{display:flex;justify-content:space-between;align-items:center;padding:20px;background:#161F2E;border-radius:16px;margin-bottom:30px;border:1px solid #2A3A4F}.header h1{font-size:28px;background:linear-gradient(135deg,#FFC107,#F44336);-webkit-background-clip:text;-webkit-text-fill-color:transparent}.badge{background:#2A3A4F;padding:8px 16px;border-radius:20px;border:1px solid #FFC107;color:#FFC107}.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:16px;margin-bottom:30px}.stat{background:#161F2E;padding:20px;border-radius:12px;border:1px solid #2A3A4F;text-align:center}.stat .v{font-size:28px;font-weight:bold;background:linear-gradient(135deg,#FFC107,#FF6B00);-webkit-background-clip:text;-webkit-text-fill-color:transparent}.stat .l{font-size:13px;color:#8899AA}.panel{background:#161F2E;border-radius:16px;padding:20px;margin-bottom:20px;border:1px solid #2A3A4F}.panel h2{color:#FFC107;font-size:18px;margin-bottom:12px}.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}.form-group{margin-bottom:12px}.form-group label{display:block;font-size:13px;color:#8899AA;margin-bottom:4px}.form-group input,.form-group select{width:100%;padding:10px 14px;background:#0D121D;border:1px solid #2A3A4F;border-radius:8px;color:#E8E8E8;font-size:14px}.form-group input:focus,.form-group select:focus{outline:none;border-color:#FFC107}.btn{padding:10px 20px;border:none;border-radius:8px;font-weight:600;cursor:pointer;transition:.3s}.btn-primary{background:linear-gradient(135deg,#FFC107,#FF6B00);color:#0D121D}.btn-primary:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(255,193,7,0.3)}.btn-success{background:#4CAF50;color:#fff}.btn-danger{background:#F44336;color:#fff}.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:13px}th{text-align:left;padding:10px;color:#8899AA;border-bottom:1px solid #2A3A4F}td{padding:10px;border-bottom:1px solid #1A2A3F}.status{padding:4px 12px;border-radius:12px;font-size:11px;font-weight:600}.status-pending{background:#FFC107;color:#0D121D}.status-approved{background:#4CAF50;color:#fff}.status-rejected{background:#F44336;color:#fff}.tabs{display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap}.tab{padding:10px 20px;background:#0D121D;border:1px solid #2A3A4F;border-radius:8px;cursor:pointer;color:#8899AA;transition:.3s}.tab.active{border-color:#FFC107;color:#FFC107;background:#1A2A3F}.tab:hover{border-color:#FFC107}.tab-content{display:none}.tab-content.active{display:block}.empty{text-align:center;padding:30px;color:#8899AA}.actions{display:flex;gap:6px}.actions .btn{padding:4px 12px;font-size:11px}.toast{position:fixed;bottom:20px;right:20px;padding:14px 20px;border-radius:12px;background:#161F2E;border:1px solid #2A3A4F;display:none;z-index:1000}.toast.success{border-color:#4CAF50}.toast.error{border-color:#F44336}@media(max-width:768px){.row{grid-template-columns:1fr}.header{flex-direction:column;gap:12px;text-align:center}}
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

@app.post("/api/case/open")
async def open_case(req:CaseOpenRequest, user=Depends(verify_telegram)):
    tg_id=user['id']; c=CASES.get(req.case_id)
    if not c: raise HTTPException(400,"Invalid case")
    if c["price"]>0 and (await get_user(tg_id))["balance"]<c["price"]: raise HTTPException(400,"Insufficient")
    if req.case_id=="free_daily":
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT last_used FROM free_case_cooldowns WHERE user_id=?", (tg_id,)) as cur:
                r=await cur.fetchone()
                if r and (datetime.now()-datetime.fromisoformat(r[0])).total_seconds()<86400: raise HTTPException(400,"Cooldown")
            await db.execute("INSERT OR REPLACE INTO free_case_cooldowns (user_id,last_used) VALUES (?,?)", (tg_id,datetime.now().isoformat())); await db.commit()
    if random.random()<0.3:
        stars=round(random.uniform(c.get("min_stars",0), c.get("max_stars",10)),1)
        async with aiosqlite.connect(DB_NAME) as db:
            if c["price"]>0: await db.execute("UPDATE users SET balance=balance-?, total_spent=total_spent+? WHERE tg_id=?", (c["price"],c["price"],tg_id))
            await db.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (int(stars),tg_id)); await db.commit()
        return {"success":True,"stars_earned":stars,"balance":(await get_user(tg_id))["balance"]}
    rarity=random.choices(c["rarities"], weights=c["weights"])[0]; gift=random.choice(NFT_GIFTS[rarity])
    async with aiosqlite.connect(DB_NAME) as db:
        if c["price"]>0: await db.execute("UPDATE users SET balance=balance-?, total_spent=total_spent+? WHERE tg_id=?", (c["price"],c["price"],tg_id))
        u=await get_user(tg_id); inv=u["inventory"]; inv.append({"id":gift["id"],"name":gift["name"],"rarity":rarity,"value":gift["value"],"emoji":gift["emoji"]})
        await db.execute("UPDATE users SET inventory=? WHERE tg_id=?", (json.dumps(inv),tg_id)); await db.commit()
    return {"success":True,"gift":gift,"rarity":rarity,"balance":(await get_user(tg_id))["balance"]}

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
    final=random.uniform(3,win_deg-3) if win and win_deg>6 else (win_deg/2 if win else random.uniform(win_deg+3,357))
    if win:
        u["inventory"][req.item_index]={"id":target["id"],"name":target["name"],"rarity":target["rarity"],"value":target["value"],"emoji":target["emoji"]}
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET inventory=?, wins=wins+1 WHERE tg_id=?", (json.dumps(u["inventory"]),tg_id)); await db.commit()
    else:
        del u["inventory"][req.item_index]
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET inventory=? WHERE tg_id=?", (json.dumps(u["inventory"]),tg_id)); await db.commit()
    return {"success":win,"chance":chance*100,"target":target,"angle":final,"message":f"{'🎉' if win else '💔'} {item['name']} → {target['name']}","balance":(await get_user(tg_id))["balance"]}

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
            u=await get_user(tg_id); inv=u["inventory"]; inv.append({"id":gift["id"],"name":gift["name"],"rarity":rarity,"value":gift["value"],"emoji":gift["emoji"]})
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

if __name__=="__main__":
    import uvicorn
    uvicorn.run(socket_app, host="0.0.0.0", port=8000)
