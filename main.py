<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>PUBG ELITE</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <script src="https://cdn.socket.io/4.7.4/socket.io.min.js"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Rajdhani:wght@600;700&family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
    
    <style>
        :root {
            --bg-main: #0a0b10;
            --card-bg: rgba(18, 22, 34, 0.85);
            --card-border: rgba(255, 255, 255, 0.06);
            --gold: #ffb703;
            --gold-glow: rgba(255, 183, 3, 0.25);
            --red: #ff4d4d;
            --green: #2ecc71;
            --text: #f1f2f6;
            --text2: #8c98a9;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; user-select: none; font-family: 'Inter', sans-serif; }
        body { background: var(--bg-main); color: var(--text); padding-bottom: 80px; }

        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 12px 16px;
            background: rgba(10, 11, 16, 0.9);
            backdrop-filter: blur(12px);
            border-bottom: 1px solid var(--card-border);
            position: sticky;
            top: 0;
            z-index: 100;
        }

        .logo {
            font-family: 'Rajdhani', sans-serif;
            font-weight: 700;
            font-size: 18px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .logo i { color: var(--gold); filter: drop-shadow(0 0 8px var(--gold-glow)); }

        .balance-box {
            background: rgba(255, 183, 3, 0.08);
            border: 1px solid rgba(255, 183, 3, 0.2);
            padding: 6px 14px;
            border-radius: 20px;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .balance-amount { font-family: 'Rajdhani', sans-serif; font-weight: 700; font-size: 16px; color: var(--gold); }
        .balance-currency { font-size: 11px; font-weight: 800; opacity: 0.7; }

        .container { padding: 14px; max-width: 460px; margin: 0 auto; }
        .page { display: none; animation: fadeIn 0.3s ease; }
        .page.active { display: block; }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 16px;
            margin-bottom: 12px;
            backdrop-filter: blur(10px);
        }

        .card-title {
            font-family: 'Rajdhani', sans-serif;
            font-size: 16px;
            font-weight: 700;
            text-transform: uppercase;
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 12px;
        }
        .card-title i { color: var(--gold); }

        .input-field {
            width: 100%;
            background: rgba(0, 0, 0, 0.4);
            border: 1px solid var(--card-border);
            border-radius: 10px;
            padding: 10px 14px;
            color: #fff;
            font-weight: 600;
            font-size: 14px;
            outline: none;
            margin-bottom: 8px;
        }
        .input-field:focus { border-color: var(--gold); }

        .btn {
            width: 100%;
            padding: 12px;
            border: none;
            border-radius: 10px;
            font-weight: 800;
            font-size: 13px;
            text-transform: uppercase;
            cursor: pointer;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }
        .btn:active { transform: scale(0.96); }

        .btn-gold { background: linear-gradient(135deg, #ffb703, #fb8500); color: #000; box-shadow: 0 4px 15px var(--gold-glow); }
        .btn-red { background: linear-gradient(135deg, #ff4d4d, #c0392b); color: #fff; }
        .btn-black { background: linear-gradient(135deg, #2c3e50, #1a252f); color: #fff; border: 1px solid rgba(255,255,255,0.05); }
        .btn-green { background: linear-gradient(135deg, #2ecc71, #27ae60); color: #fff; }
        .btn-outline { background: transparent; border: 2px solid var(--card-border); color: var(--text2); }

        .row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }

        /* ===== DICE ===== */
        .dice-display {
            background: rgba(0,0,0,0.3);
            border-radius: 12px;
            padding: 16px;
            text-align: center;
            margin-bottom: 12px;
            border: 1px solid var(--card-border);
        }
        .dice-icon { font-size: 44px; display: inline-block; transition: transform 0.3s; }
        .dice-status { font-size: 12px; color: var(--text2); font-weight: 600; margin-top: 4px; }

        /* ===== MINES ===== */
        .mines-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 8px;
            margin-bottom: 12px;
        }
        .mine-cell {
            aspect-ratio: 1;
            background: rgba(255,255,255,0.03);
            border: 1px solid var(--card-border);
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 20px;
            cursor: pointer;
            transition: 0.2s;
        }
        .mine-cell:active { transform: scale(0.92); }
        .mine-cell.open { background: rgba(46, 204, 113, 0.15); border-color: var(--green); }
        .mine-cell.bomb { background: rgba(231, 76, 60, 0.2); border-color: #e74c3c; animation: shake 0.3s; }

        @keyframes shake {
            0%, 100% { transform: translateX(0); }
            25% { transform: translateX(-4px); }
            75% { transform: translateX(4px); }
        }

        .mines-stats {
            display: flex;
            gap: 8px;
            margin-bottom: 10px;
        }
        .mines-stats .stat {
            flex: 1;
            background: rgba(0,0,0,0.3);
            padding: 6px;
            border-radius: 8px;
            text-align: center;
            border: 1px solid var(--card-border);
        }
        .mines-stats .stat .label { font-size: 7px; text-transform: uppercase; color: var(--text2); }
        .mines-stats .stat .value { font-family: 'Rajdhani', sans-serif; font-size: 16px; font-weight: 700; }

        /* ===== CRASH ===== */
        .crash-display {
            background: radial-gradient(circle, rgba(20,28,48,0.8), rgba(10,11,16,0.95));
            border-radius: 12px;
            padding: 20px;
            text-align: center;
            border: 1px solid var(--card-border);
            margin-bottom: 12px;
        }
        .crash-mult {
            font-family: 'Rajdhani', sans-serif;
            font-size: 44px;
            font-weight: 700;
            color: var(--gold);
            text-shadow: 0 0 30px var(--gold-glow);
        }
        .crash-status { font-size: 11px; color: var(--text2); font-weight: 600; }

        /* ===== CASES ===== */
        .cases-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 8px;
        }
        .case-card {
            background: rgba(255,255,255,0.02);
            border: 1px solid var(--card-border);
            border-radius: 12px;
            padding: 12px 8px;
            text-align: center;
            cursor: pointer;
            transition: 0.2s;
        }
        .case-card:active { transform: scale(0.95); }
        .case-card .icon { font-size: 24px; display: block; }
        .case-card .name { font-size: 11px; font-weight: 700; margin-top: 2px; }
        .case-card .price { font-size: 10px; color: var(--gold); font-weight: 700; }

        /* ===== INVENTORY ===== */
        .inv-grid {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 6px;
            margin-top: 8px;
        }
        .inv-item {
            background: rgba(255,255,255,0.02);
            border: 1px solid var(--card-border);
            border-radius: 8px;
            padding: 6px;
            text-align: center;
            font-size: 9px;
        }
        .inv-item .inv-icon { font-size: 18px; }
        .inv-item .inv-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .inv-item .inv-price { color: var(--gold); font-weight: 700; }
        .inv-item .inv-sell {
            background: var(--red);
            color: #fff;
            border: none;
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 7px;
            font-weight: 700;
            cursor: pointer;
            margin-top: 2px;
        }

        .rarity-common { border-color: rgba(140,152,169,0.3); }
        .rarity-rare { border-color: rgba(52,152,219,0.4); }
        .rarity-epic { border-color: rgba(168,85,247,0.4); }
        .rarity-legendary { border-color: var(--gold); }
        .rarity-mythic { border-color: #ff6b6b; animation: mythicGlow 1.5s infinite; }

        @keyframes mythicGlow {
            0%,100% { box-shadow: 0 0 10px rgba(255,107,107,0.1); }
            50% { box-shadow: 0 0 25px rgba(255,107,107,0.3); }
        }

        /* ===== TOAST ===== */
        .toast {
            position: fixed;
            bottom: 80px;
            left: 50%;
            transform: translateX(-50%);
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 10px;
            padding: 10px 18px;
            z-index: 3000;
            font-weight: 600;
            font-size: 13px;
            max-width: 90%;
            display: none;
            backdrop-filter: blur(10px);
        }
        .toast.show { display: block; animation: toastIn 0.3s; }
        @keyframes toastIn {
            from { opacity: 0; transform: translateX(-50%) translateY(16px); }
            to { opacity: 1; transform: translateX(-50%) translateY(0); }
        }
        .toast.success { border-color: var(--green); color: var(--green); }
        .toast.error { border-color: #ff4d4d; color: #ff4d4d; }
        .toast.info { border-color: var(--gold); color: var(--gold); }

        /* ===== NAV ===== */
        .nav-bar {
            position: fixed;
            bottom: 0; left: 0; right: 0;
            height: 66px;
            background: rgba(10, 11, 16, 0.95);
            backdrop-filter: blur(16px);
            border-top: 1px solid var(--card-border);
            display: flex;
            z-index: 100;
        }
        .nav-item {
            flex: 1;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 2px;
            color: var(--text2);
            font-size: 9px;
            font-weight: 600;
            cursor: pointer;
            transition: 0.2s;
        }
        .nav-item i { font-size: 18px; }
        .nav-item.active { color: var(--gold); }
        .nav-item:active { transform: scale(0.92); }

        @media (max-width: 400px) {
            .cases-grid { grid-template-columns: 1fr 1fr; }
            .inv-grid { grid-template-columns: repeat(2, 1fr); }
        }
    </style>
</head>
<body>

    <header>
        <div class="logo"><i class="fa-solid fa-crosshairs"></i> PUBG ELITE</div>
        <div class="balance-box">
            <i class="fa-solid fa-coins" style="color: var(--gold);"></i>
            <span class="balance-amount" id="balance">0</span>
            <span class="balance-currency">UC</span>
        </div>
    </header>

    <div class="container">

        <!-- TOAST -->
        <div class="toast" id="toast"></div>

        <!-- ===== PAGE 1: GAMES ===== -->
        <div id="page-games" class="page active">

            <!-- COLOR DICE -->
            <div class="card">
                <div class="card-title"><i class="fa-solid fa-dice-d6" style="color: #e74c3c;"></i> Color Dice</div>
                <div class="dice-display">
                    <div class="dice-icon" id="diceIcon">🎲</div>
                    <div class="dice-status" id="diceStatus">Сделайте ставку</div>
                </div>
                <input type="number" id="diceBet" class="input-field" placeholder="Сумма ставки UC" value="10">
                <div class="row-2">
                    <button class="btn btn-red" onclick="rollDice('red')">Красное <span style="font-size:10px;">1.9x</span></button>
                    <button class="btn btn-black" onclick="rollDice('black')">Черное <span style="font-size:10px;">1.9x</span></button>
                </div>
                <button class="btn btn-green" onclick="rollDice('green')" style="margin-top:6px;">Зеленое <span style="font-size:10px;">(23.75x)</span></button>
            </div>

            <!-- MINES -->
            <div class="card">
                <div class="card-title"><i class="fa-solid fa-bomb" style="color: var(--gold);"></i> Сапер</div>
                
                <div class="mines-stats">
                    <div class="stat"><div class="label">Множитель</div><div class="value" id="minesMult" style="color:var(--gold);">1.00x</div></div>
                    <div class="stat"><div class="label">Открыто</div><div class="value" id="minesOpened" style="color:var(--green);">0</div></div>
                    <div class="stat"><div class="label">Выигрыш</div><div class="value" id="minesWin" style="color:var(--gold);">0 UC</div></div>
                </div>

                <div class="mines-grid" id="minesGrid"></div>

                <div class="row-2">
                    <input type="number" id="minesBet" class="input-field" placeholder="Ставка UC" value="10" style="margin:0;">
                    <input type="number" id="minesCount" class="input-field" placeholder="Мины (1-15)" value="3" style="margin:0;">
                </div>

                <button class="btn btn-gold" id="minesStartBtn" onclick="startMines()"><i class="fa-solid fa-play"></i> Начать</button>
                <button class="btn btn-green" id="minesCashoutBtn" onclick="cashoutMines()" style="display:none;margin-top:6px;">
                    <i class="fa-solid fa-hand-holding-dollar"></i> Забрать
                </button>
            </div>

            <!-- CRASH -->
            <div class="card">
                <div class="card-title"><i class="fa-solid fa-rocket" style="color: var(--green);"></i> Crash</div>
                <div class="crash-display">
                    <div class="crash-mult" id="crashMult">1.00x</div>
                    <div class="crash-status" id="crashStatus">⏳ Ожидание...</div>
                </div>
                <input type="number" id="crashBet" class="input-field" placeholder="Ставка UC" value="10">
                <button class="btn btn-gold" onclick="placeCrashBet()"><i class="fa-solid fa-ticket"></i> Поставить</button>
                <button class="btn btn-green" id="crashCashoutBtn" onclick="cashoutCrash()" style="display:none;margin-top:6px;">
                    <i class="fa-solid fa-hand-holding-dollar"></i> Забрать
                </button>
            </div>

        </div>

        <!-- ===== PAGE 2: CASES ===== -->
        <div id="page-cases" class="page">
            <div class="card">
                <div class="card-title"><i class="fa-solid fa-box-open" style="color: var(--gold);"></i> UC Ящики</div>
                <div class="cases-grid" id="starCases"></div>
            </div>
            <div class="card">
                <div class="card-title"><i class="fa-solid fa-gun" style="color: var(--gold);"></i> Скины</div>
                <div class="cases-grid" id="nftCases"></div>
            </div>
            <button class="btn btn-gold" onclick="claimFreeCase()" style="margin-top:4px;">
                <i class="fa-solid fa-gift"></i> Бесплатный ящик
            </button>
        </div>

        <!-- ===== PAGE 3: PROFILE ===== -->
        <div id="page-profile" class="page">
            <div class="card">
                <div class="card-title"><i class="fa-solid fa-user"></i> Профиль</div>
                <div style="font-size:13px;">ID: <span id="userId" style="color:var(--gold);font-weight:700;">---</span></div>
                <div style="font-size:13px;">Потрачено: <span id="totalSpent" style="color:var(--gold);font-weight:700;">0</span> UC</div>
                <div style="display:flex;gap:8px;margin-top:12px;">
                    <button class="btn btn-green" onclick="openModal('depositModal')"><i class="fa-solid fa-wallet"></i> Пополнить</button>
                    <button class="btn btn-red" onclick="openModal('withdrawModal')"><i class="fa-solid fa-money-bill-transfer"></i> Вывести</button>
                </div>
                <button class="btn btn-outline" onclick="sellAllInventory()" style="margin-top:6px;">
                    <i class="fa-solid fa-dollar-sign"></i> Продать всё
                </button>
            </div>

            <div class="card">
                <div class="card-title"><i class="fa-solid fa-box"></i> Инвентарь <span id="invCount" style="font-size:11px;color:var(--text2);"></span></div>
                <div class="inv-grid" id="inventoryContainer"></div>
            </div>
        </div>

    </div>

    <!-- MODALS -->
    <div class="modal" id="depositModal" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.88);z-index:2000;align-items:center;justify-content:center;backdrop-filter:blur(6px);">
        <div class="modal-content" style="background:var(--card-bg);border:1px solid var(--card-border);border-radius:16px;padding:20px;width:90%;max-width:380px;">
            <div class="card-title"><i class="fa-solid fa-wallet"></i> Пополнение</div>
            <input type="number" id="depositAmount" class="input-field" placeholder="Сумма UC" value="100">
            <div style="display:flex;gap:8px;">
                <button class="btn btn-green" onclick="processDeposit()">Пополнить</button>
                <button class="btn btn-outline" onclick="closeModal('depositModal')">Отмена</button>
            </div>
        </div>
    </div>

    <div class="modal" id="withdrawModal" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.88);z-index:2000;align-items:center;justify-content:center;backdrop-filter:blur(6px);">
        <div class="modal-content" style="background:var(--card-bg);border:1px solid var(--card-border);border-radius:16px;padding:20px;width:90%;max-width:380px;">
            <div class="card-title"><i class="fa-solid fa-money-bill-transfer"></i> Вывод</div>
            <p style="font-size:11px;color:var(--text2);margin-bottom:8px;">Мин. 60 UC</p>
            <input type="number" id="withdrawAmount" class="input-field" placeholder="Сумма UC" value="100">
            <input type="text" id="withdrawWallet" class="input-field" placeholder="Реквизиты (ID/кошелёк)">
            <div style="display:flex;gap:8px;">
                <button class="btn btn-green" onclick="processWithdraw()">Вывести</button>
                <button class="btn btn-outline" onclick="closeModal('withdrawModal')">Отмена</button>
            </div>
        </div>
    </div>

    <!-- NAV -->
    <div class="nav-bar">
        <div class="nav-item active" onclick="switchTab('games', this)"><i class="fa-solid fa-gamepad"></i> Игры</div>
        <div class="nav-item" onclick="switchTab('cases', this)"><i class="fa-solid fa-cubes"></i> Кейсы</div>
        <div class="nav-item" onclick="switchTab('profile', this)"><i class="fa-solid fa-user"></i> Профиль</div>
    </div>

    <script>
        // ==========================================
        // 1. ИНИЦИАЛИЗАЦИЯ
        // ==========================================
        const tg = window.Telegram ? Telegram.WebApp : null;
        if (tg) { tg.expand(); tg.ready(); }

        let socket = null;
        let activeMinesId = null;
        let minesOpened = 0;
        let minesMult = 1.0;
        let minesBetAmount = 0;
        let isFlipping = false;

        // ==========================================
        // 2. ДАННЫЕ КЕЙСОВ
        // ==========================================
        const CASE_ICONS = {
            'star_case_1':'⭐','star_case_2':'⭐⭐','star_case_3':'💫',
            'star_case_4':'🌟','star_case_5':'✨','star_case_6':'👑',
            'star_case_7':'🔥','star_case_8':'💎','star_case_9':'🌈','star_case_10':'🌌',
            'nft_case_1':'🎽','nft_case_2':'🧢','nft_case_3':'👖',
            'nft_case_4':'🛡️','nft_case_5':'🐯','nft_case_6':'🐲',
            'nft_case_7':'⚡','nft_case_8':'❄️','nft_case_9':'🔥','nft_case_10':'🌌'
        };

        const CASE_NAMES = {
            'star_case_1':'Бронзовый','star_case_2':'Серебряный',
            'star_case_3':'Золотой','star_case_4':'Платиновый',
            'star_case_5':'Алмазный','star_case_6':'Мифический',
            'star_case_7':'Божественный','star_case_8':'Космический',
            'star_case_9':'Галактический','star_case_10':'Бесконечный',
            'nft_case_1':'Basic','nft_case_2':'Tactical',
            'nft_case_3':'Urban','nft_case_4':'Military',
            'nft_case_5':'Elite','nft_case_6':'Legendary',
            'nft_case_7':'Cyber','nft_case_8':'Frost',
            'nft_case_9':'Inferno','nft_case_10':'Cosmic'
        };

        const CASE_PRICES = {
            'star_case_1':25,'star_case_2':75,'star_case_3':200,
            'star_case_4':450,'star_case_5':900,'star_case_6':1800,
            'star_case_7':3500,'star_case_8':6000,'star_case_9':10000,'star_case_10':25000,
            'nft_case_1':50,'nft_case_2':150,'nft_case_3':350,
            'nft_case_4':800,'nft_case_5':1500,'nft_case_6':3000,
            'nft_case_7':5500,'nft_case_8':9000,'nft_case_9':15000,'nft_case_10':30000
        };

        // ==========================================
        // 3. TOAST
        // ==========================================
        let toastTimer = null;

        function showToast(msg, type = 'info') {
            const el = document.getElementById('toast');
            el.textContent = msg;
            el.className = 'toast ' + type + ' show';
            clearTimeout(toastTimer);
            toastTimer = setTimeout(() => el.classList.remove('show'), 3500);
        }

        // ==========================================
        // 4. NAVIGATION
        // ==========================================
        function switchTab(tab, el) {
            document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
            document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
            document.getElementById('page-' + tab).classList.add('active');
            if (el) el.classList.add('active');
            if (tab === 'profile') loadProfile();
            if (tab === 'cases') loadCases();
        }

        // ==========================================
        // 5. MODALS
        // ==========================================
        function openModal(id) { 
            document.getElementById(id).style.display = 'flex'; 
        }
        function closeModal(id) { 
            document.getElementById(id).style.display = 'none'; 
        }

        // ==========================================
        // 6. SOCKET (CRASH)
        // ==========================================
        function initSocket() {
            socket = io();

            socket.on('connect', () => showToast('✅ Соединение установлено', 'success'));
            socket.on('disconnect', () => showToast('❌ Соединение потеряно', 'error'));
            socket.on('error', (d) => showToast('❌ ' + (d.message || 'Ошибка'), 'error'));

            socket.on('crash_state', (d) => {
                const s = document.getElementById('crashStatus');
                if (d.status === 'betting') {
                    s.textContent = `⏳ До старта: ${d.timer} сек...`;
                    document.getElementById('crashCashoutBtn').style.display = 'none';
                } else if (d.status === 'flying') {
                    s.textContent = '🚀 Взлёт!';
                } else {
                    s.textContent = '⏳ Ожидание...';
                }
            });

            socket.on('crash_multiplier', (d) => {
                document.getElementById('crashMult').textContent = d.multiplier.toFixed(2) + 'x';
            });

            socket.on('crash_end', (d) => {
                document.getElementById('crashStatus').textContent = `💥 КРАШ на ${d.crash_point.toFixed(2)}x!`;
                document.getElementById('crashCashoutBtn').style.display = 'none';
                loadProfile();
            });

            socket.on('bet_placed', () => {
                document.getElementById('crashCashoutBtn').style.display = 'block';
                loadProfile();
                showToast('✅ Ставка принята!', 'success');
            });

            socket.on('cashout_success', (d) => {
                document.getElementById('crashCashoutBtn').style.display = 'none';
                loadProfile();
                showToast(`💰 Забрал ${d.win_amount} UC (x${d.multiplier.toFixed(2)})`, 'success');
            });
        }

        // ==========================================
        // 7. CRASH
        // ==========================================
        function placeCrashBet() {
            if (!socket) return showToast('❌ Нет соединения', 'error');
            const bet = parseInt(document.getElementById('crashBet').value);
            if (!bet || bet < 5) return showToast('❌ Мин. 5 UC', 'error');
            if (bet > 5000) return showToast('❌ Макс. 5000 UC', 'error');
            socket.emit('place_bet', { bet_amount: bet });
        }

        function cashoutCrash() {
            if (!socket) return showToast('❌ Нет соединения', 'error');
            socket.emit('cashout', {});
        }

        // ==========================================
        // 8. COLOR DICE
        // ==========================================
        async function rollDice(color) {
            if (isFlipping) return;
            const bet = parseInt(document.getElementById('diceBet').value);
            if (!bet || bet < 10) return showToast('❌ Мин. 10 UC', 'error');

            const icon = document.getElementById('diceIcon');
            const status = document.getElementById('diceStatus');
            isFlipping = true;
            icon.style.transform = 'scale(1.3) rotate(360deg)';
            status.textContent = '🎰 ...';

            try {
                const r = await fetch('/api/color_dice/roll', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ bet_amount: bet, color: color })
                });
                const d = await r.json();
                
                setTimeout(() => {
                    icon.style.transform = 'scale(1) rotate(0deg)';
                    if (r.ok) {
                        status.textContent = `Выпало: ${d.dropped_color.toUpperCase()}`;
                        if (d.win) {
                            showToast(`🎉 Win: +${d.win_amount} UC`, 'success');
                        } else {
                            showToast(`❌ Проигрыш`, 'error');
                        }
                        document.getElementById('balance').textContent = d.new_balance;
                    } else {
                        status.textContent = '❌ Ошибка';
                        showToast('❌ ' + (d.detail || 'Ошибка'), 'error');
                    }
                    isFlipping = false;
                    loadProfile();
                }, 400);

            } catch (e) {
                showToast('❌ Ошибка сервера', 'error');
                isFlipping = false;
            }
        }

        // ==========================================
        // 9. MINES
        // ==========================================
        function initMinesGrid() {
            const g = document.getElementById('minesGrid');
            g.innerHTML = '';
            for (let i = 0; i < 16; i++) {
                const cell = document.createElement('div');
                cell.className = 'mine-cell';
                cell.dataset.index = i;
                cell.dataset.opened = 'false';
                cell.textContent = '?';
                cell.onclick = () => openMineCell(i);
                g.appendChild(cell);
            }
            document.getElementById('minesMult').textContent = '1.00x';
            document.getElementById('minesOpened').textContent = '0';
            document.getElementById('minesWin').textContent = '0 UC';
            minesOpened = 0;
            minesMult = 1.0;
        }

        async function startMines() {
            const bet = parseInt(document.getElementById('minesBet').value);
            const cnt = parseInt(document.getElementById('minesCount').value);
            if (!bet || bet < 5) return showToast('❌ Мин. 5 UC', 'error');
            if (!cnt || cnt < 1 || cnt > 15) return showToast('❌ Мины 1-15', 'error');

            try {
                const r = await fetch('/api/mines/start', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ bet_amount: bet, mines_count: cnt })
                });
                const d = await r.json();
                if (!r.ok) return showToast('❌ ' + (d.detail || 'Ошибка'), 'error');

                activeMinesId = d.game_id;
                minesBetAmount = bet;
                initMinesGrid();
                document.getElementById('minesStartBtn').style.display = 'none';
                document.getElementById('minesCashoutBtn').style.display = 'block';
                document.getElementById('minesCashoutBtn').innerHTML = '<i class="fa-solid fa-hand-holding-dollar"></i> Забрать';
                showToast('💣 Игра началась!', 'info');
                loadProfile();
            } catch (e) { showToast('❌ Ошибка', 'error'); }
        }

        async function openMineCell(idx) {
            const cell = document.querySelector(`.mine-cell[data-index="${idx}"]`);
            if (!cell || cell.dataset.opened === 'true' || !activeMinesId) return;

            try {
                const r = await fetch('/api/mines/open', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ game_id: activeMinesId, cell_index: idx })
                });
                const d = await r.json();

                if (d.game_over || d.hit_mine) {
                    cell.classList.add('bomb');
                    cell.textContent = '💣';
                    showToast('💥 Взрыв!', 'error');
                    resetMines();
                    loadProfile();
                    return;
                }

                cell.classList.add('open');
                cell.dataset.opened = 'true';
                cell.textContent = '💎';
                minesOpened++;
                minesMult = d.current_multiplier || 1.0;

                document.getElementById('minesMult').textContent = minesMult.toFixed(2) + 'x';
                document.getElementById('minesOpened').textContent = minesOpened;
                document.getElementById('minesWin').textContent = Math.floor(minesBetAmount * minesMult) + ' UC';
                document.getElementById('minesCashoutBtn').innerHTML = 
                    `<i class="fa-solid fa-hand-holding-dollar"></i> Забрать ${Math.floor(minesBetAmount * minesMult)} UC (${minesMult.toFixed(2)}x)`;

            } catch (e) { showToast('❌ Ошибка', 'error'); }
        }

        async function cashoutMines() {
            if (!activeMinesId) return;
            try {
                const r = await fetch('/api/mines/cashout', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ game_id: activeMinesId })
                });
                const d = await r.json();
                if (d.success) {
                    showToast(`💰 +${d.win_amount} UC`, 'success');
                    resetMines();
                    loadProfile();
                }
            } catch (e) { showToast('❌ Ошибка', 'error'); }
        }

        function resetMines() {
            activeMinesId = null;
            document.getElementById('minesStartBtn').style.display = 'block';
            document.getElementById('minesCashoutBtn').style.display = 'none';
            initMinesGrid();
        }

        // ==========================================
        // 10. CASES
        // ==========================================
        function loadCases() {
            const starContainer = document.getElementById('starCases');
            const nftContainer = document.getElementById('nftCases');
            
            starContainer.innerHTML = '';
            nftContainer.innerHTML = '';

            const starTypes = Object.keys(CASE_PRICES).filter(k => k.startsWith('star'));
            const nftTypes = Object.keys(CASE_PRICES).filter(k => k.startsWith('nft'));

            starTypes.forEach(id => {
                const div = document.createElement('div');
                div.className = 'case-card';
                div.onclick = () => openCase(id);
                div.innerHTML = `
                    <span class="icon">${CASE_ICONS[id] || '📦'}</span>
                    <div class="name">${CASE_NAMES[id] || id}</div>
                    <div class="price">${CASE_PRICES[id]} UC</div>
                `;
                starContainer.appendChild(div);
            });

            nftTypes.forEach(id => {
                const div = document.createElement('div');
                div.className = 'case-card';
                div.onclick = () => openCase(id);
                div.innerHTML = `
                    <span class="icon">${CASE_ICONS[id] || '🎁'}</span>
                    <div class="name">${CASE_NAMES[id] || id}</div>
                    <div class="price">${CASE_PRICES[id]} UC</div>
                `;
                nftContainer.appendChild(div);
            });
        }

        async function openCase(caseType) {
            try {
                const r = await fetch('/api/case/open', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ case_type: caseType })
                });
                const d = await r.json();
                if (!r.ok) return showToast('❌ ' + (d.detail || 'Ошибка'), 'error');
                
                showToast(`🎉 Выиграл: ${d.reward_name}!`, 'success');
                loadProfile();
            } catch (e) {
                showToast('❌ Ошибка соединения', 'error');
            }
        }

        async function claimFreeCase() {
            try {
                const r = await fetch('/api/free_case/claim', { method: 'POST' });
                const d = await r.json();
                if (!r.ok) return showToast('❌ ' + (d.detail || 'Ошибка'), 'error');
                showToast(`🎁 Бесплатно: ${d.reward}!`, 'success');
                loadProfile();
            } catch (e) {
                showToast('❌ Ошибка', 'error');
            }
        }

        // ==========================================
        // 11. PROFILE & INVENTORY
        // ==========================================
        async function loadProfile() {
            try {
                const r = await fetch('/api/profile');
                const d = await r.json();
                document.getElementById('balance').textContent = d.balance || 0;
                document.getElementById('userId').textContent = d.tg_id || '---';
                document.getElementById('totalSpent').textContent = d.total_spent || 0;
                renderInventory(d.inventory || []);
            } catch (e) {
                console.error('Profile error:', e);
            }
        }

        function renderInventory(items) {
            const c = document.getElementById('inventoryContainer');
            const cnt = document.getElementById('invCount');
            c.innerHTML = '';
            if (!items || !items.length) {
                cnt.textContent = '(0)';
                c.innerHTML = '<div style="grid-column:1/-1;color:var(--text2);font-size:12px;text-align:center;padding:8px;">Инвентарь пуст</div>';
                return;
            }
            cnt.textContent = '(' + items.length + ')';
            items.forEach((item, idx) => {
                const div = document.createElement('div');
                div.className = 'inv-item';
                let rarity = 'common';
                if (item.price >= 2000) rarity = 'mythic';
                else if (item.price >= 800) rarity = 'legendary';
                else if (item.price >= 300) rarity = 'epic';
                else if (item.price >= 100) rarity = 'rare';
                div.classList.add('rarity-' + rarity);

                let icon = '🎁';
                if (item.name.includes('⭐')) icon = '⭐';
                else if (item.name.includes('🎽')) icon = '🎽';
                else if (item.name.includes('🧢')) icon = '🧢';
                else if (item.name.includes('👑')) icon = '👑';
                else if (item.name.includes('🔥')) icon = '🔥';
                else if (item.name.includes('💎')) icon = '💎';

                div.innerHTML = `
                    <div class="inv-icon">${icon}</div>
                    <div class="inv-name">${item.name}</div>
                    <div class="inv-price">${item.price} UC</div>
                    <button class="inv-sell" onclick="sellItem(${idx})">Продать</button>
                `;
                c.appendChild(div);
            });
        }

        async function sellItem(idx) {
            try {
                const r = await fetch('/api/inventory/sell', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ item_index: idx })
                });
                const d = await r.json();
                if (r.ok) {
                    showToast(`💰 +${d.sold_for} UC`, 'success');
                    loadProfile();
                } else {
                    showToast('❌ ' + (d.detail || 'Ошибка'), 'error');
                }
            } catch (e) { showToast('❌ Ошибка', 'error'); }
        }

        async function sellAllInventory() {
            if (!confirm('Продать все предметы?')) return;
            const inv = document.querySelectorAll('.inv-item');
            for (let i = inv.length - 1; i >= 0; i--) {
                await sellItem(i);
            }
        }

        // ==========================================
        // 12. DEPOSIT & WITHDRAW
        // ==========================================
        async function processDeposit() {
            const amt = parseInt(document.getElementById('depositAmount').value);
            if (!amt || amt < 10) return showToast('❌ Мин. 10 UC', 'error');
            try {
                const r = await fetch('/api/deposit', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ amount: amt })
                });
                if (r.ok) {
                    showToast(`💰 +${amt} UC`, 'success');
                    closeModal('depositModal');
                    loadProfile();
                }
            } catch (e) { showToast('❌ Ошибка', 'error'); }
        }

        async function processWithdraw() {
            const amt = parseInt(document.getElementById('withdrawAmount').value);
            const wallet = document.getElementById('withdrawWallet').value.trim();
            if (!amt || amt < 60) return showToast('❌ Мин. 60 UC', 'error');
            if (!wallet) return showToast('❌ Введите реквизиты', 'error');
            try {
                const r = await fetch('/api/withdraw/create', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ amount: amt, requisites: wallet })
                });
                const d = await r.json();
                if (r.ok) {
                    showToast('✅ Заявка отправлена!', 'success');
                    closeModal('withdrawModal');
                    loadProfile();
                } else {
                    showToast('❌ ' + (d.detail || 'Ошибка'), 'error');
                }
            } catch (e) { showToast('❌ Ошибка', 'error'); }
        }

        // ==========================================
        // 13. START
        // ==========================================
        initMinesGrid();
        loadProfile();
        loadCases();
        initSocket();
        setInterval(loadProfile, 10000);
    </script>
</body>
</html>
