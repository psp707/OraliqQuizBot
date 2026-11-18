from aiohttp import web
import asyncio
import re
import os
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import (Message, CallbackQuery, PollAnswer,
                           InlineKeyboardMarkup, InlineKeyboardButton, BotCommand,
                           BotCommandScopePrivate, BotCommandScopeAllGroupChats)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ================= SOZLAMALAR =================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
QUIZ_TIME = 30
PM_BLOCK_SIZE = 30
GROUP_BLOCK_SIZE = 30

# ================= FANLAR VA MAVZULAR =================
SUBJECTS = {
    "WEB": {
        "title": "💻 WEB DASTURLASH",
        "topics": [
            ("01_Veb_asoslari_Oddiy_matn.txt", "🌐 Veb asoslari"),
            ("02_HTML5_Oddiy_matn.txt", "📄 HTML5"),
            ("03_CSS3_Oddiy_matn.txt", "🎨 CSS3"),
            ("04_JavaScript_Oddiy_matn.txt", "⚡ JavaScript"),
            ("05_jQuery_Oddiy_matn.txt", "💠 jQuery"),
        ]
    },
    "OYD": {
        "title": "📘 OYD (Obyektga yo'naltirilgan dasturlash)",
        "topics": [
            ("01_OYD_asoslari_sinf_obyekt.txt", "01. Sinf va obyekt asoslari"),
            ("02_Konstruktor_destruktor.txt", "02. Konstruktor va destruktor"),
            ("03_Inkapsulyatsiya_modifikatorlar.txt", "03. Inkapsulyatsiya"),
            ("04_Vorislik_turlari.txt", "04. Vorislik turlari"),
        ]
    }
}

# ================= MA'LUMOTLAR BAZASI =================
def init_db():
    conn = sqlite3.connect('quiz_results.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS results (
            user_id INTEGER, username TEXT, full_name TEXT,
            score INTEGER, total_questions INTEGER, topic TEXT, chat_id INTEGER, chat_type TEXT, date TEXT,
            UNIQUE(user_id, topic, chat_id, chat_type)
        )
    ''')
    conn.commit()
    conn.close()

def save_result(user_id, username, full_name, score, total, topic, chat_id, chat_type):
    conn = sqlite3.connect('quiz_results.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO results (user_id, username, full_name, score, total_questions, topic, chat_id, chat_type, date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, username, full_name, score, total, topic, chat_id, chat_type,
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
    
    is_new_format = any("ANSWER:" in line for line in lines[:30])
    
    if is_new_format:
        current_q = {"text": "", "options": [], "correct": 0}
        in_options = False
        for line in lines:
            s = line.strip()
            if not s: continue
            q_match = re.match(r'^(\d{2,3})\.\s*\[([^\]]+)\]\s*(.*)$', s)
            if q_match:
                if current_q["text"] and len(current_q["options"]) == 4:
                    result.append(current_q)
                current_q = {"text": q_match.group(3).strip(), "options": [], "correct": 0}
                in_options = False
                continue
            opt_match = re.match(r'^([A-D])\.\s*(.*)$', s, re.IGNORECASE)
            if opt_match:
                in_options = True
                current_q["options"].append(opt_match.group(2).strip())
                continue
            ans_match = re.match(r'^ANSWER:\s*([A-D])$', s, re.IGNORECASE)
            if ans_match:
                current_q["correct"] = ord(ans_match.group(1).upper()) - ord('A')
                continue
            if current_q and not in_options and len(current_q["options"]) == 0:
                current_q["text"] += " " + s
        if current_q["text"] and len(current_q["options"]) == 4:
            result.append(current_q)
        print(f"  ✅ {file_name} (Yangi) → {len(result)} ta")
        return result
    
    current_question, options, correct_idx = None, [], 0
    for line in lines:
        s = line.strip()
        if not s: continue
        if re.match(r'^\d+\.\s', s):
            if current_question and len(options) == 4:
                result.append({"text": current_question, "options": options, "correct": correct_idx})
            current_question = re.sub(r'^\d+\.\s', '', s)
            options, correct_idx = [], 0
        elif s.upper().startswith("A)"): options.append(s[2:].strip())
        elif s.upper().startswith("B)"): options.append(s[2:].strip())
        elif s.upper().startswith("C)"): options.append(s[2:].strip())
        elif s.upper().startswith("D)"): options.append(s[2:].strip())
        elif s.startswith("(Javob:") or s.startswith("(javob:"):
            m = re.search(r'\(Javob:\s*([A-D])\)', s, re.IGNORECASE)
            if m: correct_idx = ord(m.group(1).upper()) - ord('A')
        else:
            if current_question and not options:
                current_question += " " + s
    if current_question and len(options) == 4:
        result.append({"text": current_question, "options": options, "correct": correct_idx})
    print(f"  ✅ {file_name} (Eski) → {len(result)} ta")
    return result

# Barcha savollarni yuklash
questions_db = {}
total_all_questions = 0
for subj_id, subj_data in SUBJECTS.items():
    questions_db[subj_id] = {"title": subj_data["title"], "topics": {}}
    for file_name, display_name in subj_data["topics"]:
        qs = parse_file(file_name)
        questions_db[subj_id]["topics"][file_name] = {"title": display_name, "questions": qs}
        total_all_questions += len(qs)
print(f"\n🎯 Jami {total_all_questions} ta savol yuklandi!")

# ================= BOT =================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

class QuizStates(StatesGroup):
    idle = State()
    playing = State()

# Shaxsiy chat sessiyalari
user_sessions = {}

# Guruh o'yini
active_games = {}  # chat_id -> game data

async def set_bot_commands():
    # SHAXSIY CHAT uchun
    private_commands = [
        BotCommand(command="start",    description="🚀 Testni boshlash"),
        BotCommand(command="topics",   description="📚 Mavzu tanlash"),
        BotCommand(command="progress", description="📊 Joriy natijam"),
        BotCommand(command="stats",    description="📈 Shaxsiy statistika"),
        BotCommand(command="stop",     description="⏹ To'xtatish"),
        BotCommand(command="help",     description="❓ Yordam"),
    ]
    
    # BARCHA GURUHLAR uchun (ID kerak emas!)
    group_commands = [
        BotCommand(command="start",    description="🚀 Guruh testini boshlash"),
        BotCommand(command="stop",     description="⏹ Testni to'xtatish (admin)"),
        BotCommand(command="results",  description="📊 Natijalar (admin)"),
        BotCommand(command="help",     description="❓ Yordam"),
    ]
    
    try:
        # Shaxsiy chatga o'rnatish
        await bot.set_my_commands(private_commands, scope=BotCommandScopePrivate())
        print("✅ Shaxsiy chat komandalari sozlandi!")
        
        # Barcha guruhlarga avtomatik o'rnatish
        await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())
        print("✅ Barcha guruhlar uchun komandalar sozlandi!")
        
    except Exception as e:
        print(f"⚠️ Komanda sozlashda xato: {e}")
def get_stop_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏹ To'xtatish", callback_data="stop_quiz")]
    ])

def get_fan_keyboard():
    kb = []
    for subj_id, subj_data in questions_db.items():
        total_q = sum(len(t["questions"]) for t in subj_data["topics"].values())
        kb.append([InlineKeyboardButton(text=f"{subj_data['title']} ({total_q})", callback_data=f"subj_{subj_id}")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_topic_keyboard(subj_id):
    kb = []
    for file_name, topic_data in questions_db[subj_id]["topics"].items():
        count = len(topic_data["questions"])
        kb.append([InlineKeyboardButton(text=f"{topic_data['title']} ({count})", callback_data=f"topic_{subj_id}_{file_name}")])
    kb.append([InlineKeyboardButton(text="⬅️ Fanlarga", callback_data="show_fans")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

# ================================================================
#                  SHAXSIY CHAT (PM)
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
                    session["score"], len(q_list), session["topic_name"], session["chat_id"], "private")
        await message.answer(
            f"🏆 <b>TEST TUGADI!</b>\n\n📚 {session['topic_name']}\n"
            f"📊 Natija: <b>{session['score']}</b> / <b>{len(q_list)}</b>\n"
            f"📈 Foiz: <b>{round(session['score'] / len(q_list) * 100)}%</b>\n\nQayta: /start",
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
        session = user_sessions.get(user_id)
        if not session: return
        try: await bot.stop_poll(chat_id=session["chat_id"], message_id=session["message_id"])
        except Exception: pass
        if session.get("update_timer_task"): session["update_timer_task"].cancel()
        session["current_q"] += 1
        msg = await bot.send_message(session["chat_id"], "⏳ Vaqt tugadi! ➡️", reply_markup=get_stop_keyboard())
        await ask_question_pm(msg, state, user_id)
    except asyncio.CancelledError: pass

# ================================================================
#                  GURUH O'YINI
# ================================================================
async def update_timer_group(chat_id, message_id):
    game = active_games.get(chat_id)
    if not game: return
    current_q_index = game.get("timer_q_index", -1)
    time_left = QUIZ_TIME
    try:
        while time_left > 0:
            await asyncio.sleep(1)
            time_left -= 1
            if game.get("timer_q_index") != current_q_index: break
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                    text=f"⏳ Qolgan vaqt: {time_left}s | Javob berganlar: {len(game.get('answers', {}))}")
            except Exception: pass
    except asyncio.CancelledError: pass

async def ask_question_group(chat_id: int):
    game = active_games.get(chat_id)
    if not game or not game["is_running"]: return
    
    q_index = game["current_q"]
    q_list = game["questions"]
    
    if q_index >= len(q_list):
        await end_game_group(chat_id)
        return
    
    q = q_list[q_index]
    block_num = q_index // GROUP_BLOCK_SIZE + 1
    total_blocks = (len(q_list) + GROUP_BLOCK_SIZE - 1) // GROUP_BLOCK_SIZE
    progress = f"📦 Blok {block_num}/{total_blocks} | Savol {q_index + 1}/{len(q_list)}"
    
    game["answers"] = {}
    game["timer_q_index"] = q_index
    
    sent_poll = await bot.send_poll(
        chat_id=chat_id,
        question=f"{progress}\n\n{q['text']}", 
        options=q["options"], 
        type="quiz",
        correct_option_id=q["correct"], 
        is_anonymous=False,
        explanation=f"To'g'ri javob: {q['options'][q['correct']]}")
    
    game["poll_id"] = sent_poll.poll.id
    game["message_id"] = sent_poll.message_id
    
    timer_msg = await bot.send_message(chat_id, f"⏳ Qolgan vaqt: {QUIZ_TIME}s | Javob berganlar: 0", reply_markup=get_stop_keyboard())
    game["timer_task"] = asyncio.create_task(time_limit_group(chat_id))
    game["update_timer_task"] = asyncio.create_task(update_timer_group(chat_id, timer_msg.message_id))

async def time_limit_group(chat_id: int):
    try:
        await asyncio.sleep(QUIZ_TIME)
        game = active_games.get(chat_id)
        if not game or not game["is_running"]: return
        try: await bot.stop_poll(chat_id=chat_id, message_id=game["message_id"])
        except Exception: pass
        if game.get("update_timer_task"): game["update_timer_task"].cancel()
        
        # Javob bermaganlarga 0 ball
        game["current_q"] += 1
        
        # Blok natijasi
        if game["current_q"] > 0 and game["current_q"] % GROUP_BLOCK_SIZE == 0:
            await show_block_result_group(chat_id)
            await asyncio.sleep(4)
        
        await ask_question_group(chat_id)
    except asyncio.CancelledError: pass

async def show_block_result_group(chat_id: int):
    game = active_games.get(chat_id)
    if not game: return
    block_num = game["current_q"] // GROUP_BLOCK_SIZE
    block_score = game["block_scores"][block_num - 1] if block_num <= len(game["block_scores"]) else 0
    
    sorted_scores = sorted(game["scores"].items(), key=lambda x: x[1], reverse=True)[:10]
    text = f"📊 <b>BLOK {block_num} NATIJASI</b> (Savollar {(block_num-1)*GROUP_BLOCK_SIZE+1}-{block_num*GROUP_BLOCK_SIZE})\n\n"
    text += f"✅ Eng yaxshi natija: <b>{block_score}</b> / <b>{GROUP_BLOCK_SIZE}</b>\n\n"
    text += "<b>🏆 Top 10:</b>\n"
    for i, (user_id, score) in enumerate(sorted_scores):
        try:
            user = await bot.get_chat(user_id)
            name = user.full_name or f"User {user_id}"
        except Exception:
            name = f"User {user_id}"
        emoji = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i+1}."
        text += f"{emoji} {name}: <b>{score}</b>\n"
    
    await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=get_stop_keyboard())

async def end_game_group(chat_id: int):
    game = active_games.get(chat_id)
    if not game: return
    game["is_running"] = False
    if game.get("timer_task"): game["timer_task"].cancel()
    if game.get("update_timer_task"): game["update_timer_task"].cancel()
    
    # Natijalarni saqlash
    for user_id, score in game["scores"].items():
        user_info = game.get("user_info", {}).get(user_id, {})
        save_result(user_id, user_info.get("username"), user_info.get("full_name"),
                    score, len(game["questions"]), game["topic_name"], chat_id, "group")
    
    sorted_scores = sorted(game["scores"].items(), key=lambda x: x[1], reverse=True)
    text = f"🏆 <b>O'YIN TUGADI!</b>\n\n📚 {game['topic_name']}\n"
    text += f"📝 Jami savollar: <b>{len(game['questions'])}</b>\n"
    text += f"👥 Ishtirokchilar: <b>{len(game['participants'])}</b>\n\n<b>YAKUNIY REYTING:</b>\n\n"
    
    for i, (user_id, score) in enumerate(sorted_scores[:20]):
        user_info = game.get("user_info", {}).get(user_id, {})
        name = user_info.get("full_name", f"User {user_id}")
        emoji = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i+1}."
        total = len(game["questions"])
        percent = round(score / total * 100) if total else 0
        text += f"{emoji} {name}: <b>{score}/{total}</b> ({percent}%)\n"
    
    await bot.send_message(chat_id, text, parse_mode="HTML")
    del active_games[chat_id]

# ================= HANDLERLAR =================
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    if message.from_user.is_bot: return
    user_id = message.from_user.id

    if message.chat.type == 'private':
        # Shaxsiy chat - mavjud sessiyani tekshirish
        if (user_id in user_sessions and 
            user_sessions[user_id].get("current_q", 0) > 0 and
            user_sessions[user_id].get("current_q", 0) < len(user_sessions[user_id].get("questions", []))):
            s = user_sessions[user_id]
            await message.answer(
                f"⚠️ Test davom etmoqda!\n📝 Savol: {s['current_q']+1}/{len(s['questions'])}\n"
                f"✅ To'g'ri: {s['score']}\n\nDavom eting yoki /stop",
                parse_mode="HTML"
            )
            return
        
        if user_id in user_sessions:
            if user_sessions[user_id].get("timer_task"): user_sessions[user_id]["timer_task"].cancel()
            if user_sessions[user_id].get("update_timer_task"): user_sessions[user_id]["update_timer_task"].cancel()
            del user_sessions[user_id]
        
        user_sessions[user_id] = {
            "current_q": 0, "score": 0, "timer_task": None, "update_timer_task": None,
            "poll_id": None, "chat_id": message.chat.id, "message_id": None, "timer_q_index": -1,
            "full_name": message.from_user.full_name, "username": message.from_user.username
        }
        
        await message.answer(
            f"👋 <b>Salom, {message.from_user.full_name}!</b>\n\n📚 <b>Oraliq Quiz Bot</b>\n"
            f"⏱ Har bir savolga: <b>{QUIZ_TIME}s</b>\n"
            f"📦 Har {PM_BLOCK_SIZE} tadan blok\n\n🎯 <b>Fanni tanlang:</b>",
            parse_mode="HTML", reply_markup=get_fan_keyboard())
        await state.set_state(QuizStates.idle)
    
    else:
        # GURUH - o'yinni boshlash
        chat_id = message.chat.id
        if chat_id in active_games and active_games[chat_id]["is_running"]:
            await message.answer("⚠️ Guruhda test allaqachon boshlangan! /stop bilan to'xtating.")
            return
        
        active_games[chat_id] = {
            "is_running": True, "chat_id": chat_id, "questions": [], "topic_name": "",
            "current_q": 0, "poll_id": None, "message_id": None,
            "timer_task": None, "update_timer_task": None, "timer_q_index": -1,
            "scores": {}, "participants": set(), "answers": {},
            "block_scores": [], "user_info": {}
        }
        
        await message.answer(
            f"🎯 <b>Guruh testi</b>\n\n👥 Guruh: {message.chat.title}\n"
            f"⏱ Har bir savolga: <b>{QUIZ_TIME}s</b>\n"
            f"📦 Har {GROUP_BLOCK_SIZE} tadan blok natijasi\n\n🎯 <b>Fanni tanlang:</b>",
            parse_mode="HTML", reply_markup=get_fan_keyboard())

@router.message(Command("quiz"))
async def cmd_quiz(message: Message):
    """Guruhda yangi test boshlash"""
    if message.from_user.is_bot: return
    if message.chat.type in ['group', 'supergroup']:
        chat_id = message.chat.id
        if chat_id in active_games and active_games[chat_id]["is_running"]:
            await message.answer("⚠️ Test davom etmoqda! /stop bilan to'xtating.")
            return
        await cmd_start(message, None)
    else:
        await message.answer("Bu command faqat guruhda ishlaydi!")

@router.message(Command("topics"))
async def cmd_topics(message: Message):
    if message.from_user.is_bot: return
    if message.chat.type != 'private':
        await message.answer("📚 Mavzu tanlash faqat shaxsiy chatda!")
        return
    await message.answer("📚 <b>Fanni tanlang:</b>", parse_mode="HTML", reply_markup=get_fan_keyboard())

@router.callback_query(F.data == "show_fans")
async def show_fans(callback: CallbackQuery):
    await callback.message.edit_text("🎯 <b>Fanni tanlang:</b>", parse_mode="HTML", reply_markup=get_fan_keyboard())
    await callback.answer()

@router.callback_query(F.data.startswith("subj_"))
async def show_topics(callback: CallbackQuery):
    subj_id = callback.data.replace("subj_", "")
    if subj_id not in questions_db:
        await callback.answer("Xatolik", show_alert=True)
        return
    
    chat_id = callback.message.chat.id
    chat_type = callback.message.chat.type
    
    # Guruhda o'yin borligini tekshirish
    if chat_type in ['group', 'supergroup']:
        if chat_id not in active_games or not active_games[chat_id]["is_running"]:
            await callback.answer("Avval /start bosing!", show_alert=True)
            return
    
    await callback.message.edit_text(
        f"📖 <b>{questions_db[subj_id]['title']}</b>\n\nMavzuni tanlang:",
        parse_mode="HTML", reply_markup=get_topic_keyboard(subj_id))
    await callback.answer()

@router.callback_query(F.data.startswith("topic_"))
async def choose_topic(callback: CallbackQuery, state: FSMContext = None):
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    chat_type = callback.message.chat.type
    
    parts = callback.data.replace("topic_", "").split("_", 1)
    if len(parts) != 2:
        await callback.answer("Xatolik", show_alert=True)
        return
    
    subj_id, file_name = parts
    topic_data = questions_db.get(subj_id, {}).get("topics", {}).get(file_name)
    
    if not topic_data:
        await callback.answer("Mavzu topilmadi", show_alert=True)
        return
    
    if chat_type == 'private':
        # SHAXSIY CHAT
        session = user_sessions.get(user_id)
        if not session:
            session = {
                "current_q": 0, "score": 0, "timer_task": None, "update_timer_task": None,
                "poll_id": None, "chat_id": chat_id, "message_id": None, "timer_q_index": -1,
                "full_name": callback.from_user.full_name, "username": callback.from_user.username
            }
            user_sessions[user_id] = session
        
        session["questions"] = topic_data["questions"]
        session["topic_name"] = topic_data["title"]
        session["current_q"] = 0
        session["score"] = 0
        session["chat_id"] = chat_id
        session["block_scores"] = [0] * ((len(session["questions"]) + PM_BLOCK_SIZE - 1) // PM_BLOCK_SIZE)
        
        await callback.message.edit_text(
            f"🚀 <b>{session['topic_name']}</b> boshlandi!\nJami: {len(session['questions'])} ta",
            parse_mode="HTML")
        await callback.answer()
        
        msg = await bot.send_message(chat_id, "➡️ Birinchi savol:", reply_markup=get_stop_keyboard())
        await ask_question_pm(msg, state, user_id)
    
    else:
        # GURUH
        if chat_id not in active_games or not active_games[chat_id]["is_running"]:
            await callback.answer("Avval /start bosing!", show_alert=True)
            return
        
        game = active_games[chat_id]
        game["questions"] = topic_data["questions"]
        game["topic_name"] = topic_data["title"]
        game["current_q"] = 0
        game["scores"] = {}
        game["participants"] = set()
        game["answers"] = {}
        game["user_info"] = {}
        game["block_scores"] = [0] * ((len(game["questions"]) + GROUP_BLOCK_SIZE - 1) // GROUP_BLOCK_SIZE)
        
        await callback.message.edit_text(
            f"🚀 <b>{game['topic_name']}</b> boshlandi!\nJami: {len(game['questions'])} ta\n"
            f"👥 Ishtirokchilar soni: test davomida aniqlanadi",
            parse_mode="HTML")
        await callback.answer()
        
        await asyncio.sleep(2)
        await ask_question_group(chat_id)

@router.message(Command("players"))
async def cmd_players(message: Message):
    if message.from_user.is_bot: return
    if message.chat.type not in ['group', 'supergroup']:
        await message.answer("👥 Bu command faqat guruhda!")
        return
    
    chat_id = message.chat.id
    game = active_games.get(chat_id)
    if not game or not game["is_running"]:
        await message.answer("👥 Hozircha o'yin yo'q. /start bosing!")
        return
    
    sorted_scores = sorted(game["scores"].items(), key=lambda x: x[1], reverse=True)
    text = f"👥 <b>Ishtirokchilar</b> ({len(game['participants'])} ta)\n\n"
    for i, (uid, score) in enumerate(sorted_scores[:15]):
        info = game.get("user_info", {}).get(uid, {})
        name = info.get("full_name", f"User {uid}")
        emoji = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i+1}."
        text += f"{emoji} {name}: <b>{score}</b>\n"
    await message.answer(text, parse_mode="HTML")

@router.message(Command("progress"))
async def cmd_progress(message: Message):
    if message.from_user.is_bot: return
    if message.chat.type != 'private':
        await message.answer("📊 Guruhda /players bosing.")
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
        f"📊 <b>Joriy holat</b>\n\n📚 {session['topic_name']}\n"
        f"📦 Blok: <b>{block_num}/{total_blocks}</b>\n📝 Savol: <b>{done}</b> / <b>{total}</b>\n"
        f"✅ To'g'ri: <b>{score}</b>\n📈 Aniqlik: <b>{percent}%</b>", parse_mode="HTML")

@router.message(Command("leaderboard"))
async def cmd_leaderboard(message: Message):
    if message.from_user.is_bot: return
    if message.chat.type not in ['group', 'supergroup']:
        await message.answer("🏆 Bu command faqat guruhda!")
        return
    
    chat_id = message.chat.id
    conn = sqlite3.connect('quiz_results.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT full_name, username, score, total_questions, topic, date 
        FROM results WHERE chat_id = ? AND chat_type = 'group' ORDER BY score DESC LIMIT 10
    """, (chat_id,))
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        await message.answer("📊 Bu guruhda hali natija yo'q.")
        return
    
    text = f"🏆 <b>Guruh Reytingi</b> (Top 10)\n\n"
    for i, (name, username, score, total, topic, date) in enumerate(rows):
        uname = f"@{username}" if username else ""
        percent = round(score / total * 100) if total else 0
        emoji = "🥇" if i == 0 else "🥈" if i == 1 else "🥉" if i == 2 else f"{i+1}."
        text += f"{emoji} {name} {uname}\n   📚 {topic}\n   ✅ {score}/{total} ({percent}%)\n   📅 {date}\n\n"
    await message.answer(text, parse_mode="HTML")

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.is_bot: return
    if message.chat.type != 'private':
        await message.answer("📊 Faqat shaxsiy chatda!")
        return
    
    user_id = message.from_user.id
    conn = sqlite3.connect('quiz_results.db')
    cursor = conn.cursor()
    cursor.execute("SELECT topic, score, total_questions, date FROM results WHERE user_id = ? AND chat_type = 'private' ORDER BY date DESC", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        await message.answer("📊 Hali test ishlamagansiz. /start bosing!")
        return
    
    text = f"📊 <b>Statistikangiz</b>\n\n"
    for topic, score, total, date in rows:
        percent = round(score / total * 100) if total else 0
        emoji = "🏆" if percent >= 80 else "👍" if percent >= 60 else "📚"
        text += f"{emoji} {topic}\n   ✅ {score}/{total} ({percent}%)\n   📅 {date}\n\n"
    await message.answer(text, parse_mode="HTML")

@router.message(Command("help"))
async def cmd_help(message: Message):
    if message.from_user.is_bot: return
    if message.chat.type == 'private':
        await message.answer(
            "❓ <b>Yordam (Shaxsiy chat)</b>\n\n🚀 <b>/start</b> — Fan va mavzu tanlash\n"
            f"📦 Har {PM_BLOCK_SIZE} savolda blok natijasi\n⏱ Savolga vaqt: <b>{QUIZ_TIME}s</b>\n"
            "📊 <b>/progress</b> — Joriy natija\n📈 <b>/stats</b> — Statistikangiz\n"
            "⏹ <b>/stop</b> — To'xtatish", parse_mode="HTML")
    else:
        await message.answer(
            "❓ <b>Yordam (Guruh)</b>\n\n🚀 <b>/start</b> — Test boshlash\n"
            f"📦 Har {GROUP_BLOCK_SIZE} savolda blok reytingi\n⏱ Savolga vaqt: <b>{QUIZ_TIME}s</b>\n"
            "👥 <b>/players</b> — Ishtirokchilar\n🏆 <b>/leaderboard</b> — Reyting\n"
            "⏹ <b>/stop</b> — To'xtatish (admin)", parse_mode="HTML")

@router.message(Command("stop"))
async def cmd_stop(message: Message, state: FSMContext = None):
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
                        score, total, session["topic_name"], session["chat_id"], "private")
            del user_sessions[user_id]
            await message.answer(
                f"⏹ <b>To'xtatildi!</b>\n✅ {score}/{total} ({round(score/total*100) if total else 0}%)\n\n/start",
                parse_mode="HTML"
            )
            if state: await state.set_state(QuizStates.idle)
        else:
            await message.answer("Test boshlanmagan.")
    else:
        if user_id == ADMIN_ID:
            chat_id = message.chat.id
            if chat_id in active_games and active_games[chat_id]["is_running"]:
                await end_game_group(chat_id)
            else:
                await message.answer("O'yin yo'q.")
        else:
            await message.answer("🔒 Faqat admin to'xtata oladi!")

@router.callback_query(F.data == "stop_quiz")
async def stop_quiz_cb(callback: CallbackQuery, state: FSMContext = None):
    user_id = callback.from_user.id
    chat_type = callback.message.chat.type
    chat_id = callback.message.chat.id
    
    if chat_type == 'private':
        session = user_sessions.get(user_id)
        if session:
            if session.get("timer_task"): session["timer_task"].cancel()
            if session.get("update_timer_task"): session["update_timer_task"].cancel()
            save_result(user_id, session.get("username"), session.get("full_name"),
                        session["score"], len(session["questions"]), session["topic_name"], session["chat_id"], "private")
            await callback.message.answer(f"⏹ To'xtatildi!\n✅ {session['score']}/{len(session['questions'])}", parse_mode="HTML")
            del user_sessions[user_id]
            if state: await state.set_state(QuizStates.idle)
    else:
        if user_id == ADMIN_ID and chat_id in active_games and active_games[chat_id]["is_running"]:
            await end_game_group(chat_id)
    await callback.answer()

# ================= POLL ANSWER =================
@router.poll_answer()
async def process_poll_answer(poll_answer: PollAnswer, state: FSMContext = None):
    user_id = poll_answer.user.id
    option_id = poll_answer.option_ids[0] if poll_answer.option_ids else None
    
    # SHAXSIY CHAT
    session = user_sessions.get(user_id)
    if session and poll_answer.poll_id == session.get("poll_id"):
        if session.get("timer_task"): session["timer_task"].cancel()
        if session.get("update_timer_task"): session["update_timer_task"].cancel()
        
        q = session["questions"][session["current_q"]]
        if option_id == q["correct"]:
            session["score"] += 1
            block_num = session["current_q"] // PM_BLOCK_SIZE
            if block_num < len(session["block_scores"]):
                session["block_scores"][block_num] += 1
        
        await asyncio.sleep(2)
        session["current_q"] += 1
        
        if session["current_q"] > 0 and session["current_q"] % PM_BLOCK_SIZE == 0:
            block_num = session["current_q"] // PM_BLOCK_SIZE
            block_score = session["block_scores"][block_num - 1]
            await bot.send_message(session["chat_id"],
                f"📊 <b>BLOK {block_num}</b> ({(block_num-1)*PM_BLOCK_SIZE+1}-{block_num*PM_BLOCK_SIZE})\n"
                f"✅ To'g'ri: <b>{block_score}</b> / <b>{PM_BLOCK_SIZE}</b>\n"
                f"📈 Foiz: <b>{round(block_score / PM_BLOCK_SIZE * 100)}%</b>\n\n⏳ Keyingi blok...",
                parse_mode="HTML", reply_markup=get_stop_keyboard())
            await asyncio.sleep(4)
        
        try:
            msg = await bot.send_message(session["chat_id"], "➡️ Keyingi savol:", reply_markup=get_stop_keyboard())
            await ask_question_pm(msg, state, user_id)
        except Exception as e: print(f"PM Xato: {e}")
        return

    # GURUH
    for chat_id, game in active_games.items():
        if game.get("is_running") and poll_answer.poll_id == game.get("poll_id"):
            game["participants"].add(user_id)
            
            # User info saqlash
            if user_id not in game["user_info"]:
                game["user_info"][user_id] = {
                    "full_name": poll_answer.user.full_name or f"User {user_id}",
                    "username": poll_answer.user.username
                }
            
            if user_id not in game["scores"]:
                game["scores"][user_id] = 0
            
            if user_id not in game["answers"] and option_id is not None:
                game["answers"][user_id] = option_id
                q = game["questions"][game["current_q"]]
                if option_id == q["correct"]:
                    game["scores"][user_id] += 1
                    block_num = game["current_q"] // GROUP_BLOCK_SIZE
                    if block_num < len(game["block_scores"]):
                        game["block_scores"][block_num] += 1
            break

# ================= HTTP SERVER =================
async def start_http_server():
    async def handle(request):
        return web.Response(text="✅ Bot ishlayapti!")
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"🌐 HTTP server {port} portida")

async def main():
    await set_bot_commands()
    asyncio.create_task(start_http_server())
    print("🚀 Bot ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
