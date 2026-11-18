from aiohttp import web
import asyncio
import re
import os
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import (Message, CallbackQuery, PollAnswer,
                           InlineKeyboardMarkup, InlineKeyboardButton, BotCommand)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ================= SOZLAMALAR =================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
QUIZ_TIME = 15
PM_BLOCK_SIZE = 30      # Shaxsiy chatda har necha savolda blok natijasi
GROUP_BLOCK_SIZE = 39   # Guruhda har necha savolda reyting

TOPICS = [
    ("01_Veb_asoslari_Oddiy_matn.txt", "🌐 Veb asoslari"),
    ("02_HTML5_Oddiy_matn.txt", "📄 HTML5"),
    ("03_CSS3_Oddiy_matn.txt", "🎨 CSS3"),
    ("04_JavaScript_Oddiy_matn.txt", "⚡ JavaScript"),
    ("05_jQuery_Oddiy_matn.txt", "💠 jQuery"),
]

# ================= MA'LUMOTLAR BAZASI =================
def init_db():
    conn = sqlite3.connect('quiz_results.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS results (
            user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT,
            score INTEGER, total_questions INTEGER, topic TEXT, chat_id INTEGER, date TEXT
        )
    ''')
    conn.commit()
    conn.close()

def save_result(user_id, username, full_name, score, total, topic, chat_id):
    conn = sqlite3.connect('quiz_results.db')
    cursor = conn.cursor()
    cursor.execute('''
        REPLACE INTO results (user_id, username, full_name, score, total_questions, topic, chat_id, date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, username, full_name, score, total, topic, chat_id,
          datetime.now().strftime("%Y-%m-%d %H:%M")))
    conn.commit()
    conn.close()

init_db()

# ================= SAVOLLARNI O'QISH =================
def parse_file(file_name):
    result = []
    if not os.path.exists(file_name):
        print(f"⚠️ {file_name} topilmadi!")
        return result
    with open(file_name, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    current_question, options, correct_idx = None, [], 0
    for line in lines:
        s = line.strip()
        if not s: continue
        if re.match(r'^\d+\.\s', s):
            if current_question and len(options) == 4:
                result.append({"text": current_question, "options": options, "correct": correct_idx})
            current_question = re.sub(r'^\d+\.\s', '', s)
            options, correct_idx = [], 0
        elif s.startswith("A)"): options.append(s[2:].strip())
        elif s.startswith("B)"): options.append(s[2:].strip())
        elif s.startswith("C)"): options.append(s[2:].strip())
        elif s.startswith("D)"): options.append(s[2:].strip())
        elif s.startswith("(Javob:"):
            m = re.search(r'\(Javob:\s*([A-D])\)', s)
            if m: correct_idx = ord(m.group(1)) - ord('A')
        else:
            if current_question and not options: current_question += " " + s
    if current_question and len(options) == 4:
        result.append({"text": current_question, "options": options, "correct": correct_idx})
    print(f"  ✅ {file_name} → {len(result)} ta savol")
    return result

questions_by_topic = {}
all_questions = []
for file_name, display_name in TOPICS:
    qs = parse_file(file_name)
    questions_by_topic[file_name] = qs
    all_questions.extend(qs)
print(f"\n🎯 Jami {len(all_questions)} ta savol yuklandi!")

# ================= BOT SOZLAMALARI =================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

class QuizStates(StatesGroup):
    idle = State()
    playing = State()

# SHAXSIY CHAT uchun holatlar
user_sessions = {}

# GURUH uchun global holat
active_game = {
    "is_running": False, "chat_id": None, "questions": [], "topic_name": "🎓 Barcha savollar",
    "current_q": 0, "poll_id": None, "message_id": None,
    "timer_task": None, "update_timer_task": None,
    "scores": {}, "participants": set(), "answers": {}
}

async def set_bot_commands():
    commands = [
        BotCommand(command="start",   description="🚀 Testni boshlash"),
        BotCommand(command="topics",  description="📚 Mavzu tanlash (shaxsiy)"),
        BotCommand(command="players", description="👥 Ishtirokchilar (guruh)"),
        BotCommand(command="progress",description="📊 Joriy natijam"),
        BotCommand(command="stop",    description="⏹ To'xtatish"),
        BotCommand(command="results", description="🏆 Barcha natijalar (admin)"),
        BotCommand(command="help",    description="❓ Yordam"),
    ]
    await bot.set_my_commands(commands)
    print("✅ Telegram menyu (☰) sozlandi!")

def get_stop_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏹ To'xtatish", callback_data="stop_quiz")]
    ])

def get_topics_keyboard():
    kb = []
    for file_name, display_name in TOPICS:
        count = len(questions_by_topic[file_name])
        kb.append([InlineKeyboardButton(text=f"{display_name} ({count})", callback_data=f"topic_{file_name}")])
    kb.append([InlineKeyboardButton(text=f"🎓 Barcha savollar ({len(all_questions)})", callback_data="topic_ALL")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

# ================================================================
#                  SHAXSIY CHAT (PM) MANTIGI
# ================================================================
async def update_timer_pm(chat_id, message_id, user_id):
    session = user_sessions.get(user_id)
    if not session: return
    current_q_index = session.get("timer_q_index", -1)
    time_left = QUIZ_TIME
    try:
        while time_left > 0:
            await asyncio.sleep(1)
            time_left -= 1
            if session.get("timer_q_index") != current_q_index: break
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"⏳ Qolgan vaqt: {time_left}s")
            except Exception: pass
    except asyncio.CancelledError: pass

async def ask_question_pm(message: Message, state: FSMContext, user_id: int):
    session = user_sessions[user_id]
    q_list = session["questions"]
    q_index = session["current_q"]

    if q_index >= len(q_list):
        save_result(user_id, session.get("username"), session.get("full_name"),
                    session["score"], len(q_list), session["topic_name"], session["chat_id"])
        await message.answer(
            f"🏆 <b>TEST TUGADI!</b>\n\n📚 Mavzu: <b>{session['topic_name']}</b>\n"
            f"📊 Natija: <b>{session['score']}</b> / <b>{len(q_list)}</b>\n"
            f"📈 Foiz: <b>{round(session['score'] / len(q_list) * 100)}%</b>\n\nQayta boshlash: /start",
            parse_mode="HTML")
        await state.set_state(QuizStates.idle)
        return

    q = q_list[q_index]
    block_num = q_index // PM_BLOCK_SIZE + 1
    total_blocks = (len(q_list) + PM_BLOCK_SIZE - 1) // PM_BLOCK_SIZE
    progress = f"📦 Blok {block_num}/{total_blocks} | Savol {q_index + 1}/{len(q_list)}"

    sent_poll = await message.answer_poll(
        question=f"{progress}\n\n{q['text']}", options=q["options"], type="quiz",
        correct_option_id=q["correct"], is_anonymous=False,
        explanation=f"To'g'ri javob: {q['options'][q['correct']]}")

    session["poll_id"] = sent_poll.poll.id
    session["chat_id"] = message.chat.id
    session["message_id"] = sent_poll.message_id
    timer_msg = await message.answer(f"⏳ Qolgan vaqt: {QUIZ_TIME}s")
    session["timer_q_index"] = q_index
    session["timer_task"] = asyncio.create_task(time_limit_pm(user_id, state))
    session["update_timer_task"] = asyncio.create_task(update_timer_pm(message.chat.id, timer_msg.message_id, user_id))
    await state.set_state(QuizStates.playing)

async def time_limit_pm(user_id: int, state: FSMContext):
    try:
        await asyncio.sleep(QUIZ_TIME)
        session = user_sessions[user_id]
        try: await bot.stop_poll(chat_id=session["chat_id"], message_id=session["message_id"])
        except Exception: pass
        if session.get("update_timer_task"): session["update_timer_task"].cancel()
        session["current_q"] += 1
        msg = await bot.send_message(session["chat_id"], "⏳ Vaqt tugadi! ➡️ Keyingi savol:", reply_markup=get_stop_keyboard())
        await ask_question_pm(msg, state, user_id)
    except asyncio.CancelledError: pass

# ================================================================
#                     GURUH MANTIGI
# ================================================================
async def update_timer_group(chat_id, message_id):
    time_left = QUIZ_TIME
    try:
        while time_left > 0:
            await asyncio.sleep(1)
            time_left -= 1
            if not active_game["is_running"]: break
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                    text=f"⏳ Qolgan vaqt: {time_left}s | Javob berganlar: {len(active_game['answers'])}")
            except Exception: pass
    except asyncio.CancelledError: pass

async def send_question_group():
    if not active_game["is_running"]: return
    q_index = active_game["current_q"]
    if q_index >= len(active_game["questions"]):
        await end_game_group()
        return
    q = active_game["questions"][q_index]
    active_game["answers"] = {}
    progress = f"📝 Savol {q_index + 1}/{len(active_game['questions'])} | {active_game['topic_name']}"
    sent_poll = await bot.send_poll(chat_id=active_game["chat_id"],
        question=f"{progress}\n\n{q['text']}", options=q["options"], type="quiz",
        correct_option_id=q["correct"], is_anonymous=False,
        explanation=f"To'g'ri javob: {q['options'][q['correct']]}")
    active_game["poll_id"] = sent_poll.poll.id
    active_game["message_id"] = sent_poll.message_id
    timer_msg = await bot.send_message(active_game["chat_id"],
        f"⏳ Qolgan vaqt: {QUIZ_TIME}s | Javob berganlar: 0", reply_markup=get_stop_keyboard())
    active_game["timer_task"] = asyncio.create_task(time_limit_group())
    active_game["update_timer_task"] = asyncio.create_task(update_timer_group(active_game["chat_id"], timer_msg.message_id))

async def time_limit_group():
    try:
        await asyncio.sleep(QUIZ_TIME)
        if not active_game["is_running"]: return
        try: await bot.stop_poll(chat_id=active_game["chat_id"], message_id=active_game["message_id"])
        except Exception: pass
        if active_game.get("update_timer_task"): active_game["update_timer_task"].cancel()
        await process_round_end_group()
    except asyncio.CancelledError: pass

async def process_round_end_group():
    if not active_game["is_running"]: return
    q = active_game["questions"][active_game["current_q"]]
    for user_id, option_id in active_game["answers"].items():
        if option_id == q["correct"]:
            active_game["scores"][user_id] = active_game["scores"].get(user_id, 0) + 1
    active_game["current_q"] += 1
    if active_game["current_q"] > 0 and active_game["current_q"] % GROUP_BLOCK_SIZE == 0:
        await show_leaderboard_group()
        await asyncio.sleep(4)
    if active_game["current_q"] >= len(active_game["questions"]):
        await end_game_group()
    else:
        await asyncio.sleep(2)
        await send_question_group()

async def show_leaderboard_group():
    sorted_scores = sorted(active_game["scores"].items(), key=lambda x: x[1], reverse=True)
    text = f"📊 <b>REYTING</b> (Savol {active_game['current_q']}/{len(active_game['questions'])})\n\n"
    if not sorted_scores:
        text += "Hali hech kim javob bermadi.\n"
    for i, (user_id, score) in enumerate(sorted_scores[:15]):
        try:
            user = await bot.get_chat(user_id)
            name = user.full_name or f"User {user_id}"
        except Exception:
            name = f"User {user_id}"
        emoji = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i+1}."
        text += f"{emoji} {name}: <b>{score}</b> ball\n"
    await bot.send_message(active_game["chat_id"], text, parse_mode="HTML")

async def end_game_group():
    active_game["is_running"] = False
    if active_game.get("timer_task"): active_game["timer_task"].cancel()
    if active_game.get("update_timer_task"): active_game["update_timer_task"].cancel()
    for user_id, score in active_game["scores"].items():
        try:
            user = await bot.get_chat(user_id)
            save_result(user_id, user.username, user.full_name, score, len(active_game["questions"]), active_game["topic_name"], active_game["chat_id"])
        except Exception: pass
    sorted_scores = sorted(active_game["scores"].items(), key=lambda x: x[1], reverse=True)
    text = f"🏆 <b>O'YIN TUGADI!</b>\n\n📚 Mavzu: <b>{active_game['topic_name']}</b>\n"
    text += f"📝 Jami savollar: <b>{len(active_game['questions'])}</b>\n👥 Ishtirokchilar: <b>{len(active_game['participants'])}</b>\n\n<b>YAKUNIY REYTING:</b>\n\n"
    for i, (user_id, score) in enumerate(sorted_scores[:20]):
        try:
            user = await bot.get_chat(user_id)
            name = user.full_name or f"User {user_id}"
        except Exception:
            name = f"User {user_id}"
        emoji = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i+1}."
        text += f"{emoji} {name}: <b>{score}</b> ball\n"
    await bot.send_message(active_game["chat_id"], text, parse_mode="HTML")
    active_game["scores"] = {}; active_game["participants"] = set(); active_game["answers"] = {}

# ================= HANDLERLAR =================
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    if message.from_user.is_bot: return
    user_id = message.from_user.id

    # ---------- SHAXSIY CHAT ----------
    if message.chat.type == 'private':
        # ✅ Agar eski sessiya bo'lsa va test HALI davom etayotgan bo'lsa
        if (user_id in user_sessions and 
            user_sessions[user_id].get("current_q", 0) > 0 and
            user_sessions[user_id].get("current_q", 0) < len(user_sessions[user_id].get("questions", []))):
            
            s = user_sessions[user_id]
            await message.answer(
                f"⚠️ Siz allaqachon test yechayapsiz!\n"
                f"📝 Savol: {s['current_q']+1}/{len(s['questions'])}\n"
                f"✅ To'g'ri: {s['score']}\n\n"
                f"Davom eting yoki /stop bosing.",
                parse_mode="HTML"
            )
            return
        
        # ✅ Eski sessiyani tozalash (test tugagan yoki to'xtatilgan)
        if user_id in user_sessions:
            if user_sessions[user_id].get("timer_task"): user_sessions[user_id]["timer_task"].cancel()
            if user_sessions[user_id].get("update_timer_task"): user_sessions[user_id]["update_timer_task"].cancel()
            del user_sessions[user_id]
        
        # Yangi sessiya yaratish...
        user_sessions[user_id] = {
            "current_q": 0, "score": 0, "timer_task": None, "update_timer_task": None,
            "poll_id": None, "chat_id": message.chat.id, "message_id": None, "timer_q_index": -1,
            "questions": all_questions, "topic_name": "🎓 Barcha savollar",
            "block_scores": [0] * ((len(all_questions) + PM_BLOCK_SIZE - 1) // PM_BLOCK_SIZE),
            "full_name": message.from_user.full_name, "username": message.from_user.username
        }
        
        await message.answer(
            f"👋 <b>Salom, {message.from_user.full_name}!</b>\n\n📚 <b>Oraliq Quiz Bot</b>\n"
            f"🎯 Jami savollar: <b>{len(all_questions)}</b> ta\n⏱ Har biriga: <b>{QUIZ_TIME}s</b>\n"
            f"📦 Har {PM_BLOCK_SIZE} savolda blok natijasi chiqadi\n\n🎯 Mavzu tanlang:",
            parse_mode="HTML", reply_markup=get_topics_keyboard())
        await state.set_state(QuizStates.idle)
@router.message(Command("topics"))
async def cmd_topics(message: Message):
    if message.from_user.is_bot: return
    if message.chat.type != 'private':
        await message.answer("📚 Mavzu tanlash faqat shaxsiy chatda ishlaydi!")
        return
    await message.answer("📚 <b>Mavzuni tanlang:</b>", parse_mode="HTML", reply_markup=get_topics_keyboard())

@router.callback_query(F.data.startswith("topic_"))
async def choose_topic_pm(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    topic_key = callback.data.replace("topic_", "")
    session = user_sessions.get(user_id)
    if not session:
        await callback.answer("Iltimos, /start bosing", show_alert=True)
        return
    if topic_key == "ALL":
        session["questions"] = all_questions
        session["topic_name"] = "🎓 Barcha savollar"
    else:
        session["questions"] = questions_by_topic[topic_key]
        session["topic_name"] = next(n for f, n in TOPICS if f == topic_key)
    session["current_q"] = 0
    session["score"] = 0
    session["chat_id"] = callback.message.chat.id
    session["block_scores"] = [0] * ((len(session["questions"]) + PM_BLOCK_SIZE - 1) // PM_BLOCK_SIZE)
    await callback.message.edit_text(f"🚀 <b>{session['topic_name']}</b> boshlandi!\nSavollar: {len(session['questions'])}", parse_mode="HTML")
    await callback.answer()
    msg = await bot.send_message(callback.message.chat.id, "➡️ Birinchi savol:", reply_markup=get_stop_keyboard())
    await ask_question_pm(msg, state, user_id)

@router.message(Command("players"))
async def cmd_players(message: Message):
    if message.from_user.is_bot: return
    if message.chat.type == 'private':
        await message.answer("👥 Bu buyruq faqat guruhda ishlaydi. Shaxsiy chatda /progress bosing.")
        return
    if not active_game["is_running"]:
        await message.answer("👥 Hozircha o'yin yo'q. /start bosing!")
        return
    sorted_scores = sorted(active_game["scores"].items(), key=lambda x: x[1], reverse=True)
    text = f"👥 <b>Ishtirokchilar</b> ({len(active_game['participants'])} ta)\n\n"
    for i, (user_id, score) in enumerate(sorted_scores[:15]):
        try:
            user = await bot.get_chat(user_id)
            name = user.full_name or f"User {user_id}"
        except Exception:
            name = f"User {user_id}"
        emoji = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i+1}."
        text += f"{emoji} {name}: <b>{score}</b> ball\n"
    await message.answer(text, parse_mode="HTML")

@router.message(Command("progress"))
async def cmd_progress(message: Message):
    if message.from_user.is_bot: return
    if message.chat.type != 'private':
        await message.answer("📊 Guruhda /players buyrug'ini bosing.")
        return
    user_id = message.from_user.id
    session = user_sessions.get(user_id)
    if not session or session.get("current_q", 0) == 0:
        await message.answer("📊 Test yechmayapsiz. /start bosing.")
        return
    total = len(session["questions"]); done = session["current_q"]; score = session["score"]
    percent = round(score / done * 100) if done > 0 else 0
    block_num = done // PM_BLOCK_SIZE + 1
    total_blocks = (total + PM_BLOCK_SIZE - 1) // PM_BLOCK_SIZE
    await message.answer(
        f"📊 <b>Joriy holatingiz</b>\n\n📚 Mavzu: <b>{session['topic_name']}</b>\n"
        f"📦 Blok: <b>{block_num}/{total_blocks}</b>\n📝 Savol: <b>{done}</b> / <b>{total}</b>\n"
        f"✅ To'g'ri: <b>{score}</b>\n📈 Aniqlik: <b>{percent}%</b>", parse_mode="HTML")

@router.message(Command("help"))
async def cmd_help(message: Message):
    if message.from_user.is_bot: return
    if message.chat.type == 'private':
        await message.answer(
            "❓ <b>Yordam (Shaxsiy chat)</b>\n\n🚀 <b>/start</b> — Mavzu tanlab test boshlash\n"
            f"📦 Har {PM_BLOCK_SIZE} savolda blok natijasi\n📊 <b>/progress</b> — Joriy natija\n"
            "⏹ <b>/stop</b> — To'xtatish\n🏆 <b>/results</b> — Barcha natijalar (admin)", parse_mode="HTML")
    else:
        await message.answer(
            "❓ <b>Yordam (Guruh)</b>\n\n🚀 <b>/start</b> — O'yinni boshlash/qo'shilish\n"
            f"📊 Har {GROUP_BLOCK_SIZE} savolda umumiy reyting\n👥 <b>/players</b> — Ishtirokchilar\n"
            "⏹ <b>/stop</b> — To'xtatish (admin)\n🏆 <b>/results</b> — Barcha natijalar (admin)", parse_mode="HTML")

@router.message(Command("results"))
async def cmd_results(message: Message):
    if message.from_user.is_bot: return
    if message.from_user.id != ADMIN_ID:
        await message.answer("🔒 Ushbu buyruqdan faqat administrator foydalanishi mumkin.")
        return
    conn = sqlite3.connect('quiz_results.db')
    cursor = conn.cursor()
    cursor.execute("SELECT full_name, username, score, total_questions, topic, date FROM results ORDER BY score DESC")
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        await message.answer("📊 Hali hech kim test ishlamadi.")
        return
    text = f"📊 <b>Jami ishtirokchilar: {len(rows)} ta</b>\n\n"
    chunks, current = [], text
    for i, r in enumerate(rows):
        name, username, score, total, topic, date = r
        uname = f"@{username}" if username else "Yo'q"
        percent = round(score / total * 100) if total else 0
        line = f"{i+1}. {name} ({uname})\n   📚 {topic}\n   ✅ {score}/{total} ({percent}%)\n   📅 {date}\n\n"
        if len(current) + len(line) > 4000: chunks.append(current); current = line
        else: current += line
    if current: chunks.append(current)
    for c in chunks: await message.answer(c, parse_mode="HTML")

@router.message(Command("stop"))
async def cmd_stop(message: Message, state: FSMContext):
    if message.from_user.is_bot: return
    user_id = message.from_user.id
    
    if message.chat.type == 'private':
        session = user_sessions.get(user_id)
        if session:
            if session.get("timer_task"): session["timer_task"].cancel()
            if session.get("update_timer_task"): session["update_timer_task"].cancel()
            
            total = len(session["questions"])
            score = session["score"]
            
            save_result(user_id, session.get("username"), session.get("full_name"),
                        score, total, session["topic_name"], session["chat_id"])
            
            # ✅ MUHIM: Sessiyani tozalash!
            del user_sessions[user_id]
            
            await message.answer(
                f"⏹ <b>Test to'xtatildi!</b>\n\n"
                f"✅ Natija: <b>{score}</b> / <b>{total}</b>\n"
                f"📈 Foiz: <b>{round(score / total * 100)}%</b>\n\n"
                f"Qayta boshlash uchun /start bosing.",
                parse_mode="HTML"
            )
            await state.set_state(QuizStates.idle)
        else:
            await message.answer("Siz hali test boshlamagansiz.")
    else:
        if user_id == ADMIN_ID and active_game["is_running"]:
            await end_game_group()
        else:
            await message.answer("🔒 O'yinni faqat admin to'xtata oladi!")

@router.callback_query(F.data == "stop_quiz")
async def stop_quiz_cb(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if callback.message.chat.type == 'private':
        session = user_sessions.get(user_id)
        if session:
            if session.get("timer_task"): session["timer_task"].cancel()
            if session.get("update_timer_task"): session["update_timer_task"].cancel()
            save_result(user_id, session.get("username"), session.get("full_name"),
                        session["score"], len(session["questions"]), session["topic_name"], session["chat_id"])
            await callback.message.answer(f"⏹ Test to'xtatildi!\n✅ Natija: <b>{session['score']}</b> / <b>{len(session['questions'])}</b>", parse_mode="HTML")
            await state.set_state(QuizStates.idle)
    else:
        if user_id == ADMIN_ID and active_game["is_running"]:
            await end_game_group()
    await callback.answer()

# ================= POLL ANSWER (IKKALA REJIM) =================
@router.poll_answer()
async def process_poll_answer(poll_answer: PollAnswer, state: FSMContext):
    user_id = poll_answer.user.id
    option_id = poll_answer.option_ids[0]

    # ---------- SHAXSIY CHAT ----------
    session = user_sessions.get(user_id)
    if session and poll_answer.poll_id == session.get("poll_id"):
        if session.get("timer_task"): session["timer_task"].cancel()
        if session.get("update_timer_task"): session["update_timer_task"].cancel()
        q = session["questions"][session["current_q"]]
        if option_id == q["correct"]:
            session["score"] += 1
            block_num = session["current_q"] // PM_BLOCK_SIZE
            session["block_scores"][block_num] += 1
        await asyncio.sleep(2)
        session["current_q"] += 1
        if session["current_q"] > 0 and session["current_q"] % PM_BLOCK_SIZE == 0:
            block_num = session["current_q"] // PM_BLOCK_SIZE
            block_score = session["block_scores"][block_num - 1]
            await bot.send_message(session["chat_id"],
                f"📊 <b>BLOK {block_num} NATIJASI</b> (Savollar {(block_num-1)*PM_BLOCK_SIZE+1}-{block_num*PM_BLOCK_SIZE})\n\n"
                f"✅ To'g'ri: <b>{block_score}</b> / <b>{PM_BLOCK_SIZE}</b>\n"
                f"📈 Foiz: <b>{round(block_score / PM_BLOCK_SIZE * 100)}%</b>\n\n⏳ Keyingi blok...",
                parse_mode="HTML", reply_markup=get_stop_keyboard())
            await asyncio.sleep(4)
        try:
            msg = await bot.send_message(session["chat_id"], "➡️ Keyingi savol:", reply_markup=get_stop_keyboard())
            await ask_question_pm(msg, state, user_id)
        except Exception as e: print(f"PM Xatolik: {e}")
        return

    # ---------- GURUH ----------
    if active_game["is_running"] and poll_answer.poll_id == active_game.get("poll_id"):
        active_game["participants"].add(user_id)
        if user_id not in active_game["scores"]: active_game["scores"][user_id] = 0
        if user_id not in active_game["answers"]: active_game["answers"][user_id] = option_id

async def start_http_server():
    """Render uchun kichik HTTP server"""
    async def handle(request):
        return web.Response(text="✅ Bot ishlayapti!")
    
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get('PORT', 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"🌐 HTTP server {port} portida ishga tushdi")

async def main():
    await set_bot_commands()
    
    # HTTP server ni ishga tushirish (Render uchun)
    asyncio.create_task(start_http_server())
    
    print("🚀 Bot ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
