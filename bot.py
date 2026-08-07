import asyncio
import logging
import io
import random
import math
import gc
from concurrent.futures import ProcessPoolExecutor
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, ReplyKeyboardMarkup, KeyboardButton, LabeledPrice
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from PIL import Image, ImageDraw
import aiosqlite
from datetime import datetime, timedelta
from aiohttp import web

TOKEN = "8568288007:AAHlH9TAsyCupHXKavYrRTG87wJMYUFw-3Y"
DB_PATH = "user_scores.db"
ADMIN_IDS = [8239397075]
LOG_CHAT_ID = -1003923383682
REQUIRED_CHANNELS = ["@moggme1", "@looksmogg"]

bot = Bot(token=TOKEN)
dp = Dispatcher()

STARS_SHOP = {
    "premium_report": {"stars": 1, "title": "💎 Premium-отчёт", "description": "Расширенный анализ лица: перцентиль среди всех юзеров, потенциал улучшения, детальное сравнение с идеалом. Отправь фото после оплаты."},
    "vip_30": {"stars": 99, "title": "👑 VIP на 30 дней", "description": "VIP-значок 👑 в профиле и топе, +50% монет за все действия, ежедневный бонус 200 монет."},
    "coins_pack": {"stars": 25, "title": "🪙 Пак монет (1000)", "description": "1000 монет для магазина — купи себе ELO или другие плюшки."},
    "elo_boost": {"stars": 75, "title": "🚀 ELO Буст +300", "description": "Мгновенное добавление 300 очков рейтинга. Залети в топ!"},
    "platinum_nick": {"stars": 150, "title": "💠 Платиновый ник навсегда", "description": "Вечный платиновый значок 💠 перед ником в профиле и топе. Навсегда."},
    "eternal_premium": {"stars": 88, "title": "♾ Вечный Premium-отчёт", "description": "Каждый анализ лица — всегда в Premium-формате: перцентиль, потенциал, детальный разбор vs идеал. Навсегда, без доплат."},
}

possible_quests = [
    {"desc": "📸 Отправь фото для оценки", "reward": 10, "check": "photo", "need_value": 1},
    {"desc": "⚔️ Победи в батле", "reward": 20, "check": "battle_win", "need_value": 1},
    {"desc": "🎯 Достигни PSL 5.0 или выше", "reward": 30, "check": "psl_high", "need_value": 5.0},
    {"desc": "🔁 Отправь 2 фото", "reward": 15, "check": "photo_count", "need_value": 2},
    {"desc": "🏆 Выиграй 2 батла", "reward": 40, "check": "battle_win_count", "need_value": 2},
    {"desc": "⭐ Получи симметрию > 80%", "reward": 25, "check": "symmetry", "need_value": 80},
]

current_quests = []

async def update_quests():
    global current_quests
    while True:
        current_quests = random.sample(possible_quests, min(3, len(possible_quests)))
        for i, q in enumerate(current_quests):
            q['id'] = i
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM quest_claims WHERE cycle_expires < ?", (datetime.now(),))
            await db.commit()
        logging.info(f"Quests updated: {[q['desc'] for q in current_quests]}")
        await asyncio.sleep(300)

async def check_and_award_quest(user_id, check_type, value=None, increment=1):
    vip = await is_vip(user_id)
    plat = await is_platinum(user_id)
    multiplier = 1.5 if (vip or plat) else 1.0

    for quest in current_quests:
        if quest['check'] == check_type:
            if check_type == 'psl_high' and value >= quest['need_value']:
                pass
            elif check_type == 'symmetry' and value >= quest['need_value']:
                pass
            elif check_type in ('photo', 'battle_win') and quest['need_value'] == 1:
                pass
            elif check_type in ('photo_count', 'battle_win_count'):
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute("""
                        INSERT INTO quest_progress (user_id, quest_id, progress)
                        VALUES (?, ?, ?)
                        ON CONFLICT(user_id, quest_id) DO UPDATE SET progress = progress + ?
                    """, (user_id, quest['id'], increment, increment))
                    await db.commit()
                    cursor = await db.execute("SELECT progress FROM quest_progress WHERE user_id=? AND quest_id=?", (user_id, quest['id']))
                    row = await cursor.fetchone()
                    if row and row[0] >= quest['need_value']:
                        cursor2 = await db.execute("SELECT 1 FROM quest_claims WHERE user_id=? AND quest_id=? AND cycle_expires>?", (user_id, quest['id'], datetime.now()))
                        if not await cursor2.fetchone():
                            reward = int(quest['reward'] * multiplier)
                            await db.execute("INSERT INTO quest_claims (user_id, quest_id, cycle_expires) VALUES (?, ?, ?)", (user_id, quest['id'], datetime.now() + timedelta(minutes=5)))
                            await db.execute("UPDATE user_coins SET coins = coins + ? WHERE user_id=?", (reward, user_id))
                            await db.commit()
                            bonus_text = " (x1.5 VIP-буст!)" if multiplier > 1 else ""
                            await bot.send_message(user_id, f"✅ Вы выполнили квест **{quest['desc']}** и получили {reward} 🪙{bonus_text}!")
                            return True
                return False
            else:
                continue

            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("SELECT 1 FROM quest_claims WHERE user_id=? AND quest_id=? AND cycle_expires>?", (user_id, quest['id'], datetime.now()))
                if not await cursor.fetchone():
                    reward = int(quest['reward'] * multiplier)
                    await db.execute("INSERT INTO quest_claims (user_id, quest_id, cycle_expires) VALUES (?, ?, ?)", (user_id, quest['id'], datetime.now() + timedelta(minutes=5)))
                    await db.execute("UPDATE user_coins SET coins = coins + ? WHERE user_id=?", (reward, user_id))
                    await db.commit()
                    bonus_text = " (x1.5 VIP-буст!)" if multiplier > 1 else ""
                    await bot.send_message(user_id, f"✅ Вы выполнили квест **{quest['desc']}** и получили {reward} 🪙{bonus_text}!")
                    return True
    return False

class SubscriptionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: types.Message, data):
        user_id = event.from_user.id
        if user_id in ADMIN_IDS:
            return await handler(event, data)
        if event.text and event.text.startswith('/check_subscription'):
            return await handler(event, data)
        bot_obj: Bot = data['bot']
        missing = []
        for channel in REQUIRED_CHANNELS:
            try:
                chat_member = await bot_obj.get_chat_member(chat_id=channel, user_id=user_id)
                if chat_member.status not in ('member', 'administrator', 'creator'):
                    missing.append(channel)
            except Exception as e:
                logging.error(f"Channel check error {channel} for {user_id}: {e}")
                missing.append(channel)
        if not missing:
            return await handler(event, data)
        builder = InlineKeyboardBuilder()
        for ch in REQUIRED_CHANNELS:
            builder.add(types.InlineKeyboardButton(text=f"📢 Подписаться на {ch}", url=f"https://t.me/{ch.lstrip('@')}"))
        builder.adjust(1)
        await event.reply(
            f"❌ Для использования бота необходимо подписаться на каналы:\n" +
            "\n".join(f"• {ch}" for ch in REQUIRED_CHANNELS) +
            "\n\nПосле подписки нажмите /check_subscription.",
            reply_markup=builder.as_markup()
        )
        return

def get_main_keyboard():
    kb = [
        [KeyboardButton(text="🔍 Оценить лицо"), KeyboardButton(text="⚔️ Батл")],
        [KeyboardButton(text="👤 Мой профиль"), KeyboardButton(text="📊 Прогресс")],
        [KeyboardButton(text="🏆 Топ"), KeyboardButton(text="📖 Книга советов")],
        [KeyboardButton(text="🏅 Рейтинг"), KeyboardButton(text="❓ Помощь")],
        [KeyboardButton(text="🛒 Магазин"), KeyboardButton(text="📋 Квесты")],
        [KeyboardButton(text="🪙 Конинов"), KeyboardButton(text="⭐ Звёзды")],
        [KeyboardButton(text="🔗 Реферал"), KeyboardButton(text="/check_subscription")],
        [KeyboardButton(text="📢 Рассылка")],
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

class Registration(StatesGroup):
    waiting_for_nickname = State()

class Battle(StatesGroup):
    waiting_for_opponent = State()

class Broadcast(StatesGroup):
    waiting_for_text = State()

def calculate_distance(p1, p2):
    return math.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)

def generate_roast(psl, canthal_tilt, symmetry):
    roasts = []
    if canthal_tilt < -2:
        roasts.append("👁 Угол глаз как у печального бассет-хаунда.")
    elif canthal_tilt > 7:
        roasts.append("👁 Это не глаза, это лазерный прицел.")
    if symmetry < 65:
        roasts.append("🧩 Симметрия уровня 'Пикассо в плохой день'.")
    if psl < 3.5:
        roasts.append("💀 Твой PSL такой, что зеркала обижаются.")
    elif psl > 6:
        roasts.append("💎 Ты либо модель, либо бот.")
    return "\n".join(roasts) if roasts else "Сегодня без рофлов, всё слишком хорошо."

def get_psl_category(psl):
    if psl < 3.0:
        return "💀 Низкий"
    elif psl < 4.5:
        return "😐 Средний"
    elif psl < 6.0:
        return "😊 Хороший"
    elif psl < 7.5:
        return "🔥 Высокий"
    else:
        return "👑 Элитный"

def _analyze_in_worker(image_bytes):
    """Runs in a SEPARATE PROCESS. Loads mediapipe, analyzes, returns result, then DIES."""
    import cv2
    import numpy as np
    import mediapipe as mp
    from PIL import Image, ImageDraw
    import io
    import math

    def calc_dist(a, b):
        return math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2)

    face_mesh = mp.solutions.face_mesh.FaceMesh(static_image_mode=True, max_num_faces=1, refine_landmarks=True, min_detection_confidence=0.5)

    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return {"error": "Не удалось прочитать изображение"}
    h, w, _ = img.shape
    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    results = face_mesh.process(img_rgb)
    if not results.multi_face_landmarks:
        return {"error": "Лицо не обнаружено. Отправь чёткое анфас-фото."}
    landmarks = results.multi_face_landmarks[0]
    points = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks.landmark]

    nose_center = points[1]
    sellion = points[168]
    chin = points[152]
    bizygomatic_width = calc_dist(points[234], points[454])
    bigonial_width = calc_dist(points[58], points[288])
    ipd = calc_dist(points[468], points[473])
    nose_width = calc_dist(points[98], points[327])
    mouth_width = calc_dist(points[61], points[291])

    sym_pairs = [(33, 263), (133, 362), (234, 454), (58, 288), (98, 327), (61, 291), (105, 334)]
    deviations = []
    nose_x, _ = nose_center
    for li, ri in sym_pairs:
        lx, _ = points[li]
        rx, _ = points[ri]
        dl = abs(lx - nose_x)
        dr = abs(rx - nose_x)
        if max(dl, dr) > 0:
            deviations.append(abs(dl - dr) / max(dl, dr))
    symmetry_score = 1 - np.mean(deviations) if deviations else 0

    ipd_score = (1 - min(abs(ipd / bizygomatic_width - 0.46) / 0.1, 1.0)) if bizygomatic_width > 0 else 0.5
    nose_width_score = max(0.1, float(np.exp(-((nose_width / ipd - 1.0) ** 2) / (2 * 0.09)))) if ipd > 0 else 0.5
    mouth_score = (1 - min(abs(mouth_width / bizygomatic_width - 0.4) / 0.1, 1.0)) if bizygomatic_width > 0 else 0.5
    jaw_score = (1 - min(abs(bigonial_width / bizygomatic_width - 0.88) / 0.88, 1.0)) if bizygomatic_width > 0 else 0.5

    eye_center_y = (points[159][1] + points[386][1]) / 2
    upper = points[13][1] - eye_center_y
    lower = chin[1] - points[13][1]
    gold_vert_score = (1 - min(abs((upper / lower) - 1.618) / 1.618, 1.0)) if lower > 0 else 0.5

    eye_w = (calc_dist(points[33], points[133]) + calc_dist(points[362], points[263])) / 2
    eye_d = calc_dist(points[133], points[362])
    gold_horiz_score = (1 - min(abs(eye_w / eye_d - 1.0), 1.0)) if eye_d > 0 else 0.5

    left_outer = np.array(points[33]); left_inner = np.array(points[133])
    left_tilt = np.degrees(np.arctan2(*(left_outer - left_inner)[::-1]))
    right_inner = np.array(points[362]); right_outer = np.array(points[263])
    right_tilt = np.degrees(np.arctan2(*(right_outer - right_inner)[::-1]) * np.array([-1, 1]))
    tilt = round((left_tilt + right_tilt) / 2, 1)
    tilt_score = max(0, 1 - abs(tilt - 6.0) / 10)

    psl_raw = (symmetry_score*0.25 + ipd_score*0.15 + nose_width_score*0.1 + mouth_score*0.1 + jaw_score*0.15 + gold_vert_score*0.1 + gold_horiz_score*0.05 + tilt_score*0.1) * 7 + 1
    psl = round(min(max(psl_raw, 1.0), 8.0), 1)

    tips = []
    if symmetry_score < 0.75: tips.append("🔹 Асимметрия: сон на спине, упражнения для лица, ортодонт.")
    if ipd_score < 0.6: tips.append("🔹 Межглазное расстояние: подбери форму бровей, макияж глаз.")
    if nose_width_score < 0.6: tips.append("🔹 Ширина носа: контуринг, коррекция бровей.")
    if mouth_score < 0.6: tips.append("🔹 Ширина рта: макияж губ, форма усов/бороды.")
    if jaw_score < 0.7: tips.append("🔹 Челюсть: mewing, жёсткая пища, ортодонт.")
    if gold_vert_score < 0.6: tips.append("🔹 Вертикальные пропорции: причёска/борода.")
    if gold_horiz_score < 0.5: tips.append("🔹 Расстояние между глазами: форма бровей.")
    if not tips: tips.append("✅ Гармоничные черты, так держать!")

    img_cv2 = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    img_pil = Image.fromarray(cv2.cvtColor(img_cv2, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(img_pil)
    for color, pairs in [
        ((0,255,0), [(10,1), (1,152)]), ((255,255,0), [(234,454)]), ((0,255,255), [(58,288)]),
        ((255,0,255), [(468,473)]), ((128,0,128), [(98,327)]), ((255,0,0), [(61,291)]),
        ((0,128,128), [(168,1)]), ((255,128,0), [(33,133), (362,263)])
    ]:
        for a, b in pairs:
            draw.line([points[a], points[b]], fill=color, width=2)
    buf = io.BytesIO()
    img_pil.save(buf, format='JPEG', quality=90)
    buf.seek(0)
    annotated_bytes = buf.getvalue()

    return {
        "psl": psl, "symmetry": round(symmetry_score * 100),
        "ipd_score": round(ipd_score * 100), "nose_width_score": round(nose_width_score * 100),
        "mouth_score": round(mouth_score * 100), "jaw": round(jaw_score * 100),
        "gold_vert": round(gold_vert_score * 100), "gold_horiz": round(gold_horiz_score * 100),
        "canthal_tilt": tilt, "tilt_score": round(tilt_score * 100), "tips": tips,
        "annotated_image_bytes": annotated_bytes,
        "raw_scores": {"symmetry": symmetry_score, "ipd": ipd_score, "nose": nose_width_score,
                       "mouth": mouth_score, "jaw": jaw_score, "gold_vert": gold_vert_score,
                       "gold_horiz": gold_horiz_score, "tilt": tilt_score}
    }

_pool = ProcessPoolExecutor(max_workers=1)

async def analyze_face_async(image_bytes):
    """Runs analyze_face in a separate process. Process dies after, freeing all memory."""
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(loop.run_in_executor(_pool, _analyze_in_worker, image_bytes), timeout=60)
    except (asyncio.TimeoutError, Exception) as e:
        result = {"error": f"Ошибка анализа: {type(e).__name__}: {e}"}
    gc.collect()
    return result

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, nickname TEXT UNIQUE, elo INTEGER DEFAULT 1000, wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0)")
        cursor = await db.execute("PRAGMA table_info(users)")
        columns = [col[1] for col in await cursor.fetchall()]
        if 'wins' not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN wins INTEGER DEFAULT 0")
        if 'losses' not in columns:
            await db.execute("ALTER TABLE users ADD COLUMN losses INTEGER DEFAULT 0")
        await db.execute("CREATE TABLE IF NOT EXISTS scores (user_id INTEGER, date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, psl REAL, symmetry INTEGER, canthal_tilt REAL, jaw INTEGER)")
        await db.execute("CREATE TABLE IF NOT EXISTS bans (user_id INTEGER PRIMARY KEY, reason TEXT, until TIMESTAMP, banned_by INTEGER)")
        await db.execute("CREATE TABLE IF NOT EXISTS mutes (user_id INTEGER PRIMARY KEY, until TIMESTAMP, muted_by INTEGER)")
        await db.execute("CREATE TABLE IF NOT EXISTS warns (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, reason TEXT, warned_by INTEGER, date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("CREATE TABLE IF NOT EXISTS user_coins (user_id INTEGER PRIMARY KEY, coins INTEGER DEFAULT 0)")
        await db.execute("CREATE TABLE IF NOT EXISTS quest_claims (user_id INTEGER, quest_id INTEGER, cycle_expires TIMESTAMP, PRIMARY KEY (user_id, quest_id))")
        await db.execute("CREATE TABLE IF NOT EXISTS quest_progress (user_id INTEGER, quest_id INTEGER, progress INTEGER DEFAULT 0, PRIMARY KEY (user_id, quest_id))")
        await db.execute("CREATE TABLE IF NOT EXISTS vip_status (user_id INTEGER PRIMARY KEY, expires TIMESTAMP, last_daily_bonus DATE)")
        await db.execute("CREATE TABLE IF NOT EXISTS platinum_users (user_id INTEGER PRIMARY KEY, granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("CREATE TABLE IF NOT EXISTS pending_premium_report (user_id INTEGER PRIMARY KEY, granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("CREATE TABLE IF NOT EXISTS eternal_premium_users (user_id INTEGER PRIMARY KEY, granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        await db.execute("CREATE TABLE IF NOT EXISTS referrals (user_id INTEGER PRIMARY KEY, referred_by INTEGER, joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, rewarded INTEGER DEFAULT 0)")
        await db.commit()

async def get_ref_count(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM referrals WHERE referred_by=?", (user_id,)) as cur:
            total = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM referrals WHERE referred_by=? AND rewarded=1", (user_id,)) as cur:
            rewarded = (await cur.fetchone())[0]
    return total, rewarded

async def register_referral(user_id, referrer_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM referrals WHERE user_id=?", (user_id,)) as cur:
            if await cur.fetchone():
                return False
        await db.execute("INSERT OR IGNORE INTO referrals (user_id, referred_by) VALUES (?, ?)", (user_id, referrer_id))
        await db.commit()
    return True

async def try_reward_referrer(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT referred_by, rewarded FROM referrals WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
        if not row or row[1] == 1:
            return
        referrer_id = row[0]
        await db.execute("UPDATE referrals SET rewarded=1 WHERE user_id=?", (user_id,))
        await db.execute("UPDATE user_coins SET coins = coins + 150 WHERE user_id=?", (referrer_id,))
        await db.commit()
    nick = await get_nickname(user_id) or "Новичок"
    try:
        await bot.send_message(referrer_id, f"🎉 **Реферал активирован!**\n\nТвой приглашённый **{nick}** прошёл первый анализ лица!\nТы получаешь **+150 монет** за реферала 🪙\n\nПриглашай ещё — каждый активный реферал = +150 монет!")
    except:
        pass

async def is_vip(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT expires FROM vip_status WHERE user_id=?", (user_id,)) as cursor:
            row = await cursor.fetchone()
    if not row:
        return False
    return datetime.fromisoformat(row[0]) > datetime.now()

async def give_vip(user_id, days):
    expires = datetime.now() + timedelta(days=days)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO vip_status (user_id, expires, last_daily_bonus) VALUES (?, ?, NULL) ON CONFLICT(user_id) DO UPDATE SET expires = ?", (user_id, expires.isoformat(), expires.isoformat()))
        await db.commit()

async def claim_vip_daily_bonus(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT expires, last_daily_bonus FROM vip_status WHERE user_id=?", (user_id,)) as cursor:
            row = await cursor.fetchone()
        if not row:
            return False
        expires, last_bonus = row
        if datetime.fromisoformat(expires) < datetime.now():
            return False
        today = datetime.now().date().isoformat()
        if last_bonus == today:
            return False
        await db.execute("UPDATE vip_status SET last_daily_bonus=? WHERE user_id=?", (today, user_id))
        await db.execute("UPDATE user_coins SET coins = coins + 200 WHERE user_id=?", (user_id,))
        await db.commit()
        return True

async def is_platinum(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM platinum_users WHERE user_id=?", (user_id,)) as cursor:
            return (await cursor.fetchone()) is not None

async def give_platinum(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO platinum_users (user_id) VALUES (?)", (user_id,))
        await db.commit()

async def has_pending_premium(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM pending_premium_report WHERE user_id=?", (user_id,)) as cursor:
            return (await cursor.fetchone()) is not None

async def set_pending_premium(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO pending_premium_report (user_id, granted_at) VALUES (?, ?)", (user_id, datetime.now().isoformat()))
        await db.commit()

async def clear_pending_premium(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM pending_premium_report WHERE user_id=?", (user_id,))
        await db.commit()

async def is_eternal_premium(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM eternal_premium_users WHERE user_id=?", (user_id,)) as cursor:
            return (await cursor.fetchone()) is not None

async def give_eternal_premium(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO eternal_premium_users (user_id) VALUES (?)", (user_id,))
        await db.commit()

async def get_user_prefix(user_id):
    if await is_platinum(user_id):
        return "💠"
    if await is_vip(user_id):
        return "👑"
    return ""

async def get_nickname(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT nickname FROM users WHERE user_id=?", (user_id,)) as cursor:
            row = await cursor.fetchone()
    return row[0] if row else None

async def get_user_info(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT nickname, elo, wins, losses FROM users WHERE user_id=?", (user_id,)) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        info = {"nickname": row[0], "elo": row[1], "wins": row[2], "losses": row[3], "last_psl": None}
        async with db.execute("SELECT psl FROM scores WHERE user_id=? ORDER BY date DESC LIMIT 1", (user_id,)) as cur:
            psl_row = await cur.fetchone()
        if psl_row:
            info["last_psl"] = psl_row[0]
        return info

async def require_nickname(message: types.Message):
    nick = await get_nickname(message.from_user.id)
    if not nick:
        await message.answer("Сначала зарегистрируйся: команда /start и придумай игровой ник.")
    return nick

async def get_user_id_by_nick(nickname):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users WHERE nickname=?", (nickname,)) as cursor:
            row = await cursor.fetchone()
    return row[0] if row else None

async def is_banned(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT until FROM bans WHERE user_id=? AND (until IS NULL OR until > ?)", (user_id, datetime.now())) as cursor:
            return (await cursor.fetchone()) is not None

async def is_muted(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT until FROM mutes WHERE user_id=? AND until > ?", (user_id, datetime.now())) as cursor:
            return (await cursor.fetchone()) is not None

async def check_ban_and_mute(message: types.Message):
    user_id = message.from_user.id
    if await is_banned(user_id):
        await message.answer("⛔ Вы забанены и не можете использовать бота.")
        return False
    if await is_muted(user_id):
        await message.answer("🔇 Вы замучены и временно не можете отправлять фото.")
        return False
    return True

async def get_elo(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT elo FROM users WHERE user_id=?", (user_id,)) as cursor:
            row = await cursor.fetchone()
    return row[0] if row else 1000

async def get_coins(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT coins FROM user_coins WHERE user_id=?", (user_id,)) as cursor:
            row = await cursor.fetchone()
    return row[0] if row else 0

async def add_coins(user_id, amount):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO user_coins (user_id, coins) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET coins = coins + ?", (user_id, amount, amount))
        await db.commit()

async def spend_coins(user_id, amount):
    balance = await get_coins(user_id)
    if balance >= amount:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE user_coins SET coins = coins - ? WHERE user_id=?", (amount, user_id))
            await db.commit()
        return True
    return False

async def add_elo(user_id, delta):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET elo = elo + ? WHERE user_id=?", (delta, user_id))
        await db.commit()

def calculate_elo_change(winner_elo, loser_elo, k=32):
    expected_winner = 1 / (1 + 10 ** ((loser_elo - winner_elo) / 400))
    expected_loser = 1 - expected_winner
    new_winner = winner_elo + k * (1 - expected_winner)
    new_loser = loser_elo + k * (0 - expected_loser)
    return round(new_winner), round(new_loser)

async def update_elo_and_stats(winner_id, loser_id):
    winner_elo = await get_elo(winner_id)
    loser_elo = await get_elo(loser_id)
    new_winner, new_loser = calculate_elo_change(winner_elo, loser_elo)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET elo=?, wins=wins+1 WHERE user_id=?", (new_winner, winner_id))
        await db.execute("UPDATE users SET elo=?, losses=losses+1 WHERE user_id=?", (new_loser, loser_id))
        await db.commit()
    return new_winner, new_loser, winner_elo, loser_elo

async def save_score(user_id, data):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO scores (user_id, psl, symmetry, canthal_tilt, jaw) VALUES (?, ?, ?, ?, ?)",
                         (user_id, data['psl'], data['symmetry'], data['canthal_tilt'], data['jaw']))
        await db.commit()

async def get_last_score(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT psl, symmetry, canthal_tilt, jaw FROM scores WHERE user_id=? ORDER BY date DESC LIMIT 1", (user_id,)) as cursor:
            return await cursor.fetchone()

async def get_top_users_by_elo(limit=40):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT nickname, elo, user_id FROM users ORDER BY elo DESC LIMIT ?", (limit,)) as cursor:
            return await cursor.fetchall()

async def get_random_opponent(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT DISTINCT user_id FROM scores WHERE user_id != ? ORDER BY RANDOM() LIMIT 1", (user_id,)) as cursor:
            row = await cursor.fetchone()
    return row[0] if row else None

async def auto_battle(user_id, result):
    opponent_id = await get_random_opponent(user_id)
    if not opponent_id:
        return
    user_score = await get_last_score(user_id)
    opponent_score = await get_last_score(opponent_id)
    if not user_score or not opponent_score:
        return
    psl1, sym1, _, _ = user_score
    psl2, sym2, _, _ = opponent_score
    if psl1 > psl2:
        winner_id, loser_id = user_id, opponent_id
    elif psl2 > psl1:
        winner_id, loser_id = opponent_id, user_id
    else:
        if sym1 > sym2:
            winner_id, loser_id = user_id, opponent_id
        elif sym2 > sym1:
            winner_id, loser_id = opponent_id, user_id
        else:
            return
    new_winner_elo, new_loser_elo, old_winner_elo, old_loser_elo = await update_elo_and_stats(winner_id, loser_id)
    winner_nick = await get_nickname(winner_id)
    loser_nick = await get_nickname(loser_id)
    try:
        await bot.send_message(user_id, f"⚔️ **Автобатл!**\n{'🏆 Победа!' if winner_id == user_id else '💀 Поражение'}\nСоперник: {winner_nick if winner_id != user_id else loser_nick}\nРейтинг: {old_winner_elo if winner_id == user_id else old_loser_elo} → {new_winner_elo if winner_id == user_id else new_loser_elo}")
    except:
        pass
    if opponent_id != user_id:
        try:
            await bot.send_message(opponent_id, f"⚔️ **Автобатл!**\n{'🏆 Победа!' if winner_id == opponent_id else '💀 Поражение'}\nСоперник: {await get_nickname(user_id)}\nРейтинг: {old_winner_elo if winner_id == opponent_id else old_loser_elo} → {new_winner_elo if winner_id == opponent_id else new_loser_elo}")
        except:
            pass

GUIDE_TEXT = "📖 **КНИГА ЛУКСМАКСИНГА**\n\n1. Симметрия: сон на спине, осанка, facial yoga.\n2. Межглазное расстояние: форма бровей, макияж.\n3. Пропорции носа: контуринг, макияж.\n4. Рот: визуальная коррекция губами/бородой.\n5. Челюсть: mewing, жевание твёрдой пищи, дыхание носом.\n6. Вертикальные пропорции: причёска, борода.\n7. Наклон глаз: упражнения, массаж.\n\n_Не медицинская рекомендация._"

@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    if not await check_ban_and_mute(message):
        return
    user_id = message.from_user.id
    args = message.text.split(maxsplit=1)
    ref_arg = args[1].strip() if len(args) > 1 else None
    if ref_arg and ref_arg.startswith("ref_"):
        try:
            referrer_id = int(ref_arg[4:])
            if referrer_id != user_id:
                registered = await register_referral(user_id, referrer_id)
                if registered:
                    try:
                        await bot.send_message(referrer_id, "👥 По твоей реферальной ссылке зашёл новый пользователь!")
                    except:
                        pass
        except (ValueError, TypeError):
            pass
    nick = await get_nickname(user_id)
    if nick:
        await add_coins(user_id, 0)
        if await claim_vip_daily_bonus(user_id):
            await message.answer("👑 VIP-бонус: +200 монет за сегодня! 🪙")
        prefix = await get_user_prefix(user_id)
        await message.answer(f"👤 Твой ник: **{prefix}{nick}**\nИспользуй кнопки или введи /help.", reply_markup=get_main_keyboard())
        return
    await message.answer("🆕 Придумай **игровой никнейм** (2-20 символов):", reply_markup=get_main_keyboard())
    await state.set_state(Registration.waiting_for_nickname)

@dp.message(Registration.waiting_for_nickname)
async def process_nickname(message: types.Message, state: FSMContext):
    if not await check_ban_and_mute(message):
        return
    nickname = message.text.strip().split()[0]
    if len(nickname) < 2 or len(nickname) > 20:
        await message.answer("Никнейм должен быть от 2 до 20 символов.")
        return
    user_id = message.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users WHERE nickname=?", (nickname,)) as cursor:
            if await cursor.fetchone():
                await message.answer("Этот никнейм уже занят.")
                return
        await db.execute("INSERT INTO users (user_id, nickname, elo, wins, losses) VALUES (?, ?, 1000, 0, 0) ON CONFLICT(user_id) DO UPDATE SET nickname=?, elo=1000, wins=0, losses=0", (user_id, nickname, nickname))
        await db.commit()
    await add_coins(user_id, 0)
    await state.clear()
    await message.answer(f"✅ Готово! Твой никнейм: **{nickname}**", reply_markup=get_main_keyboard())

@dp.message(Command("setnick"))
async def set_nickname_cmd(message: types.Message, state: FSMContext):
    if not await check_ban_and_mute(message):
        return
    await message.answer("Введи новый игровой никнейм:")
    await state.set_state(Registration.waiting_for_nickname)

@dp.message(Command("myprofile"))
async def my_profile_cmd(message: types.Message):
    if not await check_ban_and_mute(message):
        return
    info = await get_user_info(message.from_user.id)
    if not info:
        await message.answer("Сначала зарегистрируйся.")
        return
    user_id = message.from_user.id
    prefix = await get_user_prefix(user_id)
    vip = await is_vip(user_id)
    plat = await is_platinum(user_id)
    status_line = ""
    if plat:
        status_line = "\n💠 **Платиновый статус** (навсегда)"
    elif vip:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT expires FROM vip_status WHERE user_id=?", (user_id,)) as cursor:
                row = await cursor.fetchone()
        exp_str = datetime.fromisoformat(row[0]).strftime("%d.%m.%Y") if row else "?"
        status_line = f"\n👑 **VIP** до {exp_str}"
    cat = f" | {get_psl_category(info['last_psl'])}" if info['last_psl'] else ""
    await message.answer(
        f"👤 Ник: **{prefix}{info['nickname']}**\n🏅 Рейтинг Elo: **{info['elo']}**\n🏆 Победы: {info['wins']} | 💀 Поражения: {info['losses']}\n" +
        (f"📊 Последний PSL: {info['last_psl']:.1f}/8.0{cat}" if info['last_psl'] else "Нет оценок") + status_line,
        reply_markup=get_main_keyboard()
    )

@dp.message(Command("help"))
async def help_cmd(message: types.Message):
    await message.answer(
        "ℹ️ **Команды:**\n/start — регистрация / профиль\n/setnick — сменить ник\n/myprofile — профиль\n/help — это сообщение\n/guide — книга луксмаксинга\n/progress — замеры\n/top — топ (40 чел)\n/rank — рейтинг\n/battle <ник> — дуэль\n/coins — монеты\n/shop — магазин\n/quests — квесты\n/stars — Telegram Stars ⭐\n/check_subscription — подписка\n\n⚙️ **Админы:**\n/ban, /unban, /mute, /unmute, /warn, /unwarn, /check, /banlist, /mutelist, /resetelo, /addelo, /broadcast",
        reply_markup=get_main_keyboard()
    )

@dp.message(Command("guide"))
async def guide_cmd(message: types.Message):
    await message.answer(GUIDE_TEXT, parse_mode="Markdown", reply_markup=get_main_keyboard())

@dp.message(Command("coins"))
async def coins_cmd(message: types.Message):
    if not await check_ban_and_mute(message):
        return
    nick = await require_nickname(message)
    if not nick:
        return
    user_id = message.from_user.id
    coins = await get_coins(user_id)
    vip_hint = ""
    if await is_vip(user_id) or await is_platinum(user_id):
        claimed = await claim_vip_daily_bonus(user_id)
        if claimed:
            coins += 200
            vip_hint = "\n👑 +200 монет VIP-бонус за сегодня!"
    await message.answer(f"🪙 У тебя **{coins}** монет.{vip_hint}", reply_markup=get_main_keyboard())

@dp.message(Command("shop"))
async def shop_cmd(message: types.Message):
    await message.answer("🛒 **Магазин монет**\n\n💰 100 Elo = 500 монет\n💰 200 Elo = 900 монет\n💰 500 Elo = 2000 монет\n\nДля покупки: /buy elo <кол-во>\n⭐ За Stars — /stars", reply_markup=get_main_keyboard())

@dp.message(Command("buy"))
async def buy_cmd(message: types.Message):
    args = message.text.split()
    if len(args) != 3 or args[1].lower() != 'elo':
        await message.answer("Используй: /buy elo <100/200/500>", reply_markup=get_main_keyboard())
        return
    try:
        amount = int(args[2])
    except:
        await message.answer("Количество должно быть числом.", reply_markup=get_main_keyboard())
        return
    prices = {100: 500, 200: 900, 500: 2000}
    if amount not in prices:
        await message.answer("Доступны только 100, 200 или 500 Elo.", reply_markup=get_main_keyboard())
        return
    price = prices[amount]
    if await spend_coins(message.from_user.id, price):
        await add_elo(message.from_user.id, amount)
        await message.answer(f"✅ Купил {amount} Elo за {price} монет!", reply_markup=get_main_keyboard())
    else:
        await message.answer(f"❌ Недостаточно монет. Нужно {price}.", reply_markup=get_main_keyboard())

@dp.message(Command("quests"))
async def quests_cmd(message: types.Message):
    if not current_quests:
        await message.answer("Квесты загружаются, попробуйте через минуту.", reply_markup=get_main_keyboard())
        return
    text = "📋 **Активные квесты**\n\n"
    user_id = message.from_user.id
    vip = await is_vip(user_id) or await is_platinum(user_id)
    for q in current_quests:
        reward = int(q['reward'] * 1.5) if vip else q['reward']
        bonus = " _(x1.5 VIP)_" if vip else ""
        text += f"• {q['desc']} — {reward} 🪙{bonus}\n"
    text += "\nНаграда зачисляется автоматически!"
    await message.answer(text, parse_mode="Markdown", reply_markup=get_main_keyboard())

@dp.message(Command("check_subscription"))
async def check_subscription_cmd(message: types.Message):
    user_id = message.from_user.id
    missing = []
    for channel in REQUIRED_CHANNELS:
        try:
            chat_member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if chat_member.status not in ('member', 'administrator', 'creator'):
                missing.append(channel)
        except Exception as e:
            logging.error(f"Channel check error {channel}: {e}")
            missing.append(channel)
    if not missing:
        await message.answer("✅ Подписка подтверждена!", reply_markup=get_main_keyboard())
    else:
        builder = InlineKeyboardBuilder()
        for ch in missing:
            builder.add(types.InlineKeyboardButton(text=f"📢 {ch}", url=f"https://t.me/{ch.lstrip('@')}"))
        builder.adjust(1)
        await message.answer("❌ Подпишись на каналы:\n" + "\n".join(f"• {ch}" for ch in missing), reply_markup=builder.as_markup())

@dp.message(Command("stars"))
async def stars_shop_cmd(message: types.Message):
    if not await check_ban_and_mute(message):
        return
    builder = InlineKeyboardBuilder()
    for key, item in STARS_SHOP.items():
        builder.add(types.InlineKeyboardButton(text=f"{item['title']} — {item['stars']} ⭐", callback_data=f"buy_stars:{key}"))
    builder.adjust(1)
    await message.answer("⭐ **Магазин Telegram Stars**\n\nВыбери товар:", parse_mode="Markdown", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("buy_stars:"))
async def buy_stars_callback(callback: types.CallbackQuery):
    item_key = callback.data.split(":")[1]
    item = STARS_SHOP.get(item_key)
    if not item:
        await callback.answer("Товар не найден", show_alert=True)
        return
    try:
        prices = [LabeledPrice(label=item["title"], amount=item["stars"])]
        await bot.send_invoice(chat_id=callback.from_user.id, title=item["title"], description=item["description"], payload=item_key, provider_token="", currency="XTR", prices=prices)
        await callback.answer()
    except Exception as e:
        logging.error(f"Invoice error: {e}")
        await callback.answer("Ошибка", show_alert=True)

@dp.pre_checkout_query()
async def pre_checkout_handler(query: types.PreCheckoutQuery):
    await query.answer(ok=True)

@dp.message(F.successful_payment)
async def successful_payment_handler(message: types.Message):
    payload = message.successful_payment.invoice_payload
    user_id = message.from_user.id

    if payload == "premium_report":
        await set_pending_premium(user_id)
        await message.answer("💎 Premium-отчёт активирован! Отправь фото.", reply_markup=get_main_keyboard())
    elif payload == "vip_30":
        await give_vip(user_id, 30)
        await message.answer("👑 VIP на 30 дней активирован!", reply_markup=get_main_keyboard())
    elif payload == "coins_pack":
        await add_coins(user_id, 1000)
        coins = await get_coins(user_id)
        await message.answer(f"🪙 +1000 монет! Баланс: **{coins}**", reply_markup=get_main_keyboard())
    elif payload == "elo_boost":
        old_elo = await get_elo(user_id)
        await add_elo(user_id, 300)
        await message.answer(f"🚀 +300 Elo! {old_elo} → **{old_elo+300}**", reply_markup=get_main_keyboard())
    elif payload == "platinum_nick":
        await give_platinum(user_id)
        if not await is_vip(user_id):
            await give_vip(user_id, 36500)
        await message.answer("💠 Платиновый статус навсегда!", reply_markup=get_main_keyboard())
    elif payload == "eternal_premium":
        await give_eternal_premium(user_id)
        await message.answer("♾ Вечный Premium активирован!", reply_markup=get_main_keyboard())

@dp.message(F.text.lower() == "🔍 оценить лицо")
async def button_evaluate(message: types.Message):
    if not await check_ban_and_mute(message):
        return
    nick = await require_nickname(message)
    if not nick:
        return
    if await has_pending_premium(message.from_user.id):
        await message.answer("📸 Premium-режим! Отправь фото анфас.", reply_markup=get_main_keyboard())
    else:
        await message.answer("📸 Отправь фото анфас (чёткое, без очков).", reply_markup=get_main_keyboard())

@dp.message(F.text.lower() == "⚔️ батл")
async def button_battle(message: types.Message, state: FSMContext):
    if not await check_ban_and_mute(message):
        return
    if not await require_nickname(message):
        return
    await message.answer("Введи ник противника:")
    await state.set_state(Battle.waiting_for_opponent)

@dp.message(Battle.waiting_for_opponent)
async def process_battle_opponent(message: types.Message, state: FSMContext):
    if not await check_ban_and_mute(message):
        return
    challenger_nick = await require_nickname(message)
    if not challenger_nick:
        return
    opponent_nick = message.text.strip()
    opponent_id = await get_user_id_by_nick(opponent_nick)
    if not opponent_id:
        await message.answer(f"❌ '{opponent_nick}' не найден.", reply_markup=get_main_keyboard())
        await state.clear()
        return
    challenger_id = message.from_user.id
    if challenger_id == opponent_id:
        await message.answer("Нельзя вызвать себя 😏", reply_markup=get_main_keyboard())
        await state.clear()
        return
    cs = await get_last_score(challenger_id)
    os_ = await get_last_score(opponent_id)
    if not cs:
        await message.answer("Сначала оцени лицо!", reply_markup=get_main_keyboard())
        await state.clear()
        return
    if not os_:
        await message.answer(f"{opponent_nick} ещё не делал замеров.", reply_markup=get_main_keyboard())
        await state.clear()
        return
    psl1, sym1, _, _ = cs
    psl2, sym2, _, _ = os_
    if psl1 > psl2:
        w, l = challenger_id, opponent_id
        wn, ln = challenger_nick, opponent_nick
        wp, lp = psl1, psl2
    elif psl2 > psl1:
        w, l = opponent_id, challenger_id
        wn, ln = opponent_nick, challenger_nick
        wp, lp = psl2, psl1
    else:
        if sym1 >= sym2:
            w, l = challenger_id, opponent_id
            wn, ln = challenger_nick, opponent_nick
            wp, lp = psl1, psl2
        else:
            w, l = opponent_id, challenger_id
            wn, ln = opponent_nick, challenger_nick
            wp, lp = psl2, psl1
    nw, nl, ow, ol = await update_elo_and_stats(w, l)
    await message.answer(f"⚔️ **Дуэль!**\n🏆 {wn} ({wp:.1f}) vs 💀 {ln} ({lp:.1f})\n\nРейтинг: {wn} {ow}→**{nw}** | {ln} {ol}→**{nl}**", reply_markup=get_main_keyboard())
    if w == challenger_id:
        await check_and_award_quest(challenger_id, "battle_win")
    else:
        await check_and_award_quest(opponent_id, "battle_win")
    await state.clear()

@dp.message(F.text.lower() == "👤 мой профиль")
async def button_profile(message: types.Message):
    await my_profile_cmd(message)

@dp.message(F.text.lower() == "📊 прогресс")
async def button_progress(message: types.Message):
    await progress_cmd(message)

@dp.message(F.text.lower() == "🏆 топ")
async def button_top(message: types.Message):
    await top_cmd(message)

@dp.message(F.text.lower() == "📖 книга советов")
async def button_guide(message: types.Message):
    await guide_cmd(message)

@dp.message(F.text.lower() == "🏅 рейтинг")
async def button_rank(message: types.Message):
    await rank_cmd(message)

@dp.message(F.text.lower() == "❓ помощь")
async def button_help(message: types.Message):
    await help_cmd(message)

@dp.message(F.text.lower() == "🛒 магазин")
async def button_shop(message: types.Message):
    await shop_cmd(message)

@dp.message(F.text.lower() == "📋 квесты")
async def button_quests(message: types.Message):
    await quests_cmd(message)

@dp.message(F.text.lower() == "🪙 конинов")
async def button_coins(message: types.Message):
    await coins_cmd(message)

@dp.message(F.text.lower() == "⭐ звёзды")
async def button_stars(message: types.Message):
    await stars_shop_cmd(message)

@dp.message(F.text.lower() == "🔗 реферал")
async def button_ref(message: types.Message):
    await ref_cmd(message)

@dp.message(Command("ref"))
async def ref_cmd(message: types.Message):
    if not await check_ban_and_mute(message):
        return
    nick = await require_nickname(message)
    if not nick:
        return
    user_id = message.from_user.id
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"
    total, rewarded = await get_ref_count(user_id)
    await message.answer(f"🔗 **Рефералы**\n\nСсылка: `{ref_link}`\n\n💰 +150 🪙 за активного реферала\n📊 Приглашено: **{total}** | Активных: **{rewarded}**", parse_mode="Markdown", reply_markup=get_main_keyboard())

@dp.message(F.text.lower() == "📢 рассылка")
async def button_broadcast(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Недостаточно прав.")
        return
    await message.answer("Введите текст для рассылки:")
    await state.set_state(Broadcast.waiting_for_text)

@dp.message(Command("broadcast"))
async def broadcast_cmd(message: types.Message, state: FSMContext):
    if not await admin_required(message):
        return
    await message.answer("Введите текст для рассылки:")
    await state.set_state(Broadcast.waiting_for_text)

@dp.message(Broadcast.waiting_for_text)
async def process_broadcast_text(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    text = message.text.strip()
    if not text:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            users = await cursor.fetchall()
    sent, failed = 0, 0
    for (uid,) in users:
        try:
            await bot.send_message(uid, text)
            sent += 1
        except:
            failed += 1
        await asyncio.sleep(0.05)
    await message.answer(f"✅ Рассылка: {sent} отправлено, {failed} ошибок", reply_markup=get_main_keyboard())
    await state.clear()

async def send_premium_report(message, nick, result):
    user_id = message.from_user.id
    psl = result['psl']
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM scores WHERE psl <= ?", (psl,)) as cur:
            below = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM scores") as cur:
            total = (await cur.fetchone())[0]
    percentile = round((below / total * 100)) if total > 0 else 50
    raw = result['raw_scores']
    boosted = {k: min(v + 0.2, 1.0) for k, v in raw.items()}
    potential_raw = (boosted['symmetry']*0.25 + boosted['ipd']*0.15 + boosted['nose']*0.1 + boosted['mouth']*0.1 + boosted['jaw']*0.15 + boosted['gold_vert']*0.1 + boosted['gold_horiz']*0.05 + boosted['tilt']*0.1) * 7 + 1
    potential_psl = round(min(max(potential_raw, 1.0), 8.0), 1)
    def bar(s):
        return "█" * round(s/10) + "░" * (10 - round(s/10))
    metrics = [("Симметрия", result['symmetry'], 92), ("Межзрачковое", result['ipd_score'], 90), ("Нос", result['nose_width_score'], 85), ("Рот", result['mouth_score'], 88), ("Челюсть", result['jaw'], 90), ("Золотое ↕", result['gold_vert'], 80), ("Глаза ↔", result['gold_horiz'], 80), ("Наклон", result['tilt_score'], 85)]
    mt = ""
    gaps = []
    for name, score, ideal in metrics:
        gap = ideal - score
        status = "✅" if gap <= 5 else ("⚠️" if gap <= 20 else "❌")
        mt += f"{status} {name}: {score}% {bar(score)} (~{ideal}%)\n"
        if gap > 15:
            gaps.append(name)
    pt = f"\n🎯 **Приоритет:**\n" + "\n".join(f"• {m}" for m in gaps[:3]) if gaps else ""
    report = f"💎 **PREMIUM: {nick}**\n\n🏆 PSL: **{psl}/8.0** ({get_psl_category(psl)})\n📈 Лучше **{percentile}%** юзеров\n🚀 Потенциал: до **{potential_psl}/8.0**\n\n📊 {mt}{pt}\n\n💡 " + "\n".join(result['tips']) + "\n\n_Premium-анализ 💎_"
    await message.answer(report, parse_mode="Markdown", reply_markup=get_main_keyboard())
    if not await is_eternal_premium(user_id):
        await clear_pending_premium(user_id)

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    if not await check_ban_and_mute(message):
        return
    nick = await require_nickname(message)
    if not nick:
        return
    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    file_bytes = await bot.download_file(file.file_path)
    img_bytes = file_bytes.read()

    is_premium = await has_pending_premium(message.from_user.id) or await is_eternal_premium(message.from_user.id)
    await message.answer("🔍 Анализирую лицо..." if not is_premium else "💎 Premium-анализ...", reply_markup=get_main_keyboard())

    result = await analyze_face_async(img_bytes)

    if "error" in result:
        await message.answer(f"❌ {result['error']}", reply_markup=get_main_keyboard())
        return

    annotated = result['annotated_image_bytes']
    await message.answer_photo(BufferedInputFile(annotated, filename="face.jpg"), caption="🔍 Разметка лица")

    if is_premium:
        await send_premium_report(message, nick, result)
    else:
        tips_text = "\n".join(result['tips'])
        report = f"🎯 **{nick}, PSL: {result['psl']}/8.0** ({get_psl_category(result['psl'])})\n\n📊 Симметрия: {result['symmetry']}%\n📊 Межглазное: {result['ipd_score']}%\n📊 Нос: {result['nose_width_score']}%\n📊 Рот: {result['mouth_score']}%\n📊 Челюсть: {result['jaw']}%\n📊 Золотое ↕: {result['gold_vert']}%\n📊 Глаза ↔: {result['gold_horiz']}%\n📊 Наклон: {result['canthal_tilt']}° ({result['tilt_score']}%)\n\n🛠 {tips_text}\n\n💎 _Детальный отчёт: /stars_"
        await message.answer(report, parse_mode="Markdown", reply_markup=get_main_keyboard())

    await save_score(message.from_user.id, result)
    await check_and_award_quest(message.from_user.id, "photo")
    await check_and_award_quest(message.from_user.id, "photo_count", increment=1)
    if result['psl'] >= 5.0:
        await check_and_award_quest(message.from_user.id, "psl_high", value=result['psl'])
    if result['symmetry'] >= 80:
        await check_and_award_quest(message.from_user.id, "symmetry", value=result['symmetry'])
    asyncio.create_task(auto_battle(message.from_user.id, result))
    asyncio.create_task(try_reward_referrer(message.from_user.id))
    if LOG_CHAT_ID:
        try:
            await bot.send_photo(LOG_CHAT_ID, photo=BufferedInputFile(img_bytes, filename="user.jpg"), caption=f"📷 {nick} | PSL: {result['psl']} | Sym: {result['symmetry']}%")
        except:
            pass

@dp.message(Command("progress"))
async def progress_cmd(message: types.Message):
    nick = await require_nickname(message)
    if not nick:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT psl, symmetry, date FROM scores WHERE user_id=? ORDER BY date DESC LIMIT 5", (message.from_user.id,)) as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await message.answer("📭 Нет замеров.", reply_markup=get_main_keyboard())
        return
    rows = list(reversed(rows))
    lines = [f"📅 {d}: PSL {p}, sym {s}%" for p, s, d in rows]
    await message.answer(f"📈 **{nick}:**\n" + "\n".join(lines), reply_markup=get_main_keyboard())

@dp.message(Command("rank"))
async def rank_cmd(message: types.Message):
    nick = await require_nickname(message)
    if not nick:
        return
    elo = await get_elo(message.from_user.id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users WHERE elo > ?", (elo,)) as cursor:
            rank = (await cursor.fetchone())[0] + 1
    prefix = await get_user_prefix(message.from_user.id)
    await message.answer(f"🏅 **{prefix}{nick}**: {elo} (место #{rank})", reply_markup=get_main_keyboard())

@dp.message(Command("top"))
async def top_cmd(message: types.Message):
    rows = await get_top_users_by_elo(40)
    if not rows:
        await message.answer("Пока пусто.", reply_markup=get_main_keyboard())
        return
    lines = []
    for i, (nick, elo, uid) in enumerate(rows, 1):
        prefix = await get_user_prefix(uid)
        lines.append(f"{i}. {prefix}{nick} — {elo}")
    await message.answer("🏆 **Топ:**\n" + "\n".join(lines), reply_markup=get_main_keyboard())

@dp.message(Command("battle"))
async def battle_cmd(message: types.Message):
    if not await check_ban_and_mute(message):
        return
    challenger_nick = await require_nickname(message)
    if not challenger_nick:
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Используй: /battle <ник>", reply_markup=get_main_keyboard())
        return
    opponent_nick = args[1].strip()
    opponent_id = await get_user_id_by_nick(opponent_nick)
    if not opponent_id:
        await message.answer(f"❌ '{opponent_nick}' не найден.", reply_markup=get_main_keyboard())
        return
    challenger_id = message.from_user.id
    if challenger_id ==opponent_id:
        await message.answer("Нельзя себя 😏", reply_markup=get_main_keyboard())
        return
    cs = await get_last_score(challenger_id)
    os_ = await get_last_score(opponent_id)
    if not cs:
        await message.answer("Оцени лицо!", reply_markup=get_main_keyboard())
        return
    if not os_:
        await message.answer(f"{opponent_nick} без замеров.", reply_markup=get_main_keyboard())
        return
    psl1, sym1, _, _ = cs
    psl2, sym2, _, _ = os_
    if psl1 > psl2:
        w, l = challenger_id, opponent_id
        wn, ln = challenger_nick, opponent_nick
    elif psl2 > psl1:
        w, l = opponent_id, challenger_id
        wn, ln = opponent_nick, challenger_nick
    elif sym1 >= sym2:
        w, l = challenger_id, opponent_id
        wn, ln = challenger_nick, opponent_nick
    else:
        w, l = opponent_id, challenger_id
        wn, ln = opponent_nick, challenger_nick
    nw, nl, ow, ol = await update_elo_and_stats(w, l)
    wp_ = psl1 if w == challenger_id else psl2
    lp_ = psl2 if w == challenger_id else psl1
    await message.answer(f"⚔️ **{wn}** ({wp_:.1f}) vs **{ln}** ({lp_:.1f})\n🏆 {wn}: {ow}→**{nw}** | 💀 {ln}: {ol}→**{nl}**", reply_markup=get_main_keyboard())
    if w == challenger_id:
        await check_and_award_quest(challenger_id, "battle_win")
    else:
        await check_and_award_quest(opponent_id, "battle_win")

def is_admin(user_id):
    return user_id in ADMIN_IDS

async def admin_required(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Недостаточно прав.")
        return False
    return True

@dp.message(Command("ban"))
async def ban_cmd(message: types.Message):
    if not await admin_required(message):
        return
    args = message.text.split(maxsplit=3)
    if len(args) < 3:
        await message.answer("/ban <ник/ID> <минуты> <причина>")
        return
    target = args[1]
    mins = int(args[2]) if args[2].isdigit() else 0
    reason = args[3] if len(args) > 3 else "Без причины"
    target_id = target.isdigit() and int(target) or await get_user_id_by_nick(target)
    if not target_id:
        await message.answer("Не найден.")
        return
    until = datetime.now() + timedelta(minutes=mins) if mins > 0 else None
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO bans (user_id, reason, until, banned_by) VALUES (?, ?, ?, ?)", (target_id, reason, until, message.from_user.id))
        await db.commit()
    await message.answer(f"✅ {target} забанен. Причина: {reason}")

@dp.message(Command("unban"))
async def unban_cmd(message: types.Message):
    if not await admin_required(message):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return
    target = args[1]
    target_id = target.isdigit() and int(target) or await get_user_id_by_nick(target)
    if not target_id:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM bans WHERE user_id=?", (target_id,))
        await db.commit()
    await message.answer(f"✅ {target} разбанен.")

@dp.message(Command("mute"))
async def mute_cmd(message: types.Message):
    if not await admin_required(message):
        return
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("/mute <ник/ID> <минуты>")
        return
    target = args[1]
    mins = int(args[2]) if args[2].isdigit() else 0
    if mins <= 0:
        return
    target_id = target.isdigit() and int(target) or await get_user_id_by_nick(target)
    if not target_id:
        return
    until = datetime.now() + timedelta(minutes=mins)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO mutes (user_id, until, muted_by) VALUES (?, ?, ?)", (target_id, until, message.from_user.id))
        await db.commit()
    await message.answer(f"🔇 {target} замучен на {mins} мин.")

@dp.message(Command("unmute"))
async def unmute_cmd(message: types.Message):
    if not await admin_required(message):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return
    target = args[1]
    target_id = target.isdigit() and int(target) or await get_user_id_by_nick(target)
    if not target_id:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM mutes WHERE user_id=?", (target_id,))
        await db.commit()
    await message.answer(f"🔈 {target} размучен.")

@dp.message(Command("warn"))
async def warn_cmd(message: types.Message):
    if not await admin_required(message):
        return
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        return
    target = args[1]
    reason = args[2]
    target_id = target.isdigit() and int(target) or await get_user_id_by_nick(target)
    if not target_id:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO warns (user_id, reason, warned_by) VALUES (?, ?, ?)", (target_id, reason, message.from_user.id))
        await db.commit()
        async with db.execute("SELECT COUNT(*) FROM warns WHERE user_id=? AND date > datetime('now', '-30 days')", (target_id,)) as cursor:
            count = (await cursor.fetchone())[0]
    if count >= 3:
        until = datetime.now() + timedelta(days=1)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR REPLACE INTO bans (user_id, reason, until, banned_by) VALUES (?, ?, ?, ?)", (target_id, "Автобан за 3 варна", until, message.from_user.id))
            await db.commit()
        await message.answer(f"⚠️ {target} забанен на 1 день (3 варна)")
    else:
        await message.answer(f"⚠️ {target}: варн {count}/3. Причина: {reason}")

@dp.message(Command("unwarn"))
async def unwarn_cmd(message: types.Message):
    if not await admin_required(message):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return
    target = args[1]
    target_id = target.isdigit() and int(target) or await get_user_id_by_nick(target)
    if not target_id:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM warns WHERE user_id=? ORDER BY date DESC LIMIT 1", (target_id,)) as cursor:
            row = await cursor.fetchone()
        if not row:
            return
        await db.execute("DELETE FROM warns WHERE id=?", (row[0],))
        await db.commit()
    await message.answer(f"✅ Варн снят с {target}")

@dp.message(Command("check"))
async def check_cmd(message: types.Message):
    if not await admin_required(message):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return
    target = args[1]
    target_id = target.isdigit() and int(target) or await get_user_id_by_nick(target)
    if not target_id:
        return
    nick = await get_nickname(target_id) or "?"
    elo = await get_elo(target_id)
    ib = await is_banned(target_id)
    im = await is_muted(target_id)
    vip = await is_vip(target_id)
    plat = await is_platinum(target_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM warns WHERE user_id=? AND date > datetime('now', '-30 days')", (target_id,)) as cursor:
            warns = (await cursor.fetchone())[0]
    await message.answer(f"👤 {nick} (ID:{target_id})\n🏅 Elo:{elo}\n⛔ Бан:{'да' if ib else 'нет'}\n🔇 Мут:{'да' if im else 'нет'}\n⚠️ Варны:{warns}\n👑 VIP:{'да' if vip else 'нет'}\n💠 Платина:{'да' if plat else 'нет'}")

@dp.message(Command("banlist"))
async def banlist_cmd(message: types.Message):
    if not await admin_required(message):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, reason, until FROM bans WHERE until IS NULL OR until > ?", (datetime.now(),)) as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await message.answer("Нет банов.")
        return
    lines = [f"{await get_nickname(uid) or uid}: {r} (до {u})" for uid, r, u in rows]
    await message.answer("⛔ **Баны:**\n" + "\n".join(lines))

@dp.message(Command("mutelist"))
async def mutelist_cmd(message: types.Message):
    if not await admin_required(message):
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, until FROM mutes WHERE until > ?", (datetime.now(),)) as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await message.answer("Нет мутов.")
        return
    lines = [f"{await get_nickname(uid) or uid}: до {u}" for uid, u in rows]
    await message.answer("🔇 **Муты:**\n" + "\n".join(lines))

@dp.message(Command("resetelo"))
async def resetelo_cmd(message: types.Message):
    if not await admin_required(message):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return
    target = args[1]
    target_id = target.isdigit() and int(target) or await get_user_id_by_nick(target)
    if not target_id:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET elo=1000 WHERE user_id=?", (target_id,))
        await db.commit()
    await message.answer(f"✅ {target}: elo → 1000")

@dp.message(Command("addelo"))
async def addelo_cmd(message: types.Message):
    if not await admin_required(message):
        return
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        return
    target = args[1]
    try:
        delta = int(args[2])
    except:
        return
    target_id = target.isdigit() and int(target) or await get_user_id_by_nick(target)
    if not target_id:
        return
    cur = await get_elo(target_id)
    new = cur + delta
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET elo=? WHERE user_id=?", (new, target_id))
        await db.commit()
    await message.answer(f"✅ {target}: {cur} → {new} ({'+' if delta > 0 else ''}{delta})")

@dp.message()
async def other_text(message: types.Message):
    await message.answer("Используй кнопки или /help", reply_markup=get_main_keyboard())

async def handle_health(request):
    return web.Response(text="ok", status=200)

async def self_ping():
    import aiohttp as aio
    while True:
        try:
            async with aio.ClientSession() as session:
                async with session.get("http://127.0.0.1:8080/") as resp:
                    pass
        except:
            pass
        await asyncio.sleep(600)

async def start_http_server():
    app = web.Application()
    app.router.add_get("/", handle_health)
    app.router.add_get("/health", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    logging.info("HTTP server on :8080")

async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.info("=== Starting moggme-tg-bot ===")
    logging.info("Step 1: init_db...")
    await init_db()
    logging.info("Step 2: middleware...")
    dp.message.middleware.register(SubscriptionMiddleware())
    logging.info("Step 3: tasks...")
    asyncio.create_task(update_quests())
    asyncio.create_task(self_ping())
    logging.info("Step 4: http server...")
    await start_http_server()
    logging.info("Step 5: polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
