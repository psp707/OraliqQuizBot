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
PM_BLOCK_SIZE = 30      # Shaxsiy chatda har 30 ta savolda blok natijasi
GROUP_BLOCK_SIZE = 39   # Guruhda har 39 ta savolda reyting

# YANGI TUZILMA: Fanlar va ularning mavzulari
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
            ("03_Inkapsulyatsiya_modifikatorlar.txt", "03. Inkapsulyatsiya va modifikatorlar"),
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

# ================= SAVOLLARNI O'QISH (IKKI FORMATNI QO'LLAB-QUVVATLAYDI) =================
def parse_file(file_name):
    result = []
    if not os.path.exists(file_name):
        print(f"⚠️ {file_name} topilmadi!")
        return result
    
    with open(file_name, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # Yangi formatni tekshirish (ANSWER: A)
    is_new_format = any("ANSWER:" in line for line in lines[:30])
    
    if is_new_format:
        current_q = {"text": "", "options": [], "correct": 0}
        in_options = False
        
        for line in lines:
            s = line.strip()
            if not s: continue
            
            # Savol boshi: 001. [Nazariy] Savol matni...
            q_match = re.match(r'^(\d{2,3})\.\s*\[([^\]]+)\]\s*(.*)$', s)
            if q_match:
                if current_q["text"] and len(current_q["options"]) == 4:
                    result.append(current_q)
                current_q = {"text": q_match.group(3).strip(), "options": [], "correct": 0}
                in_options = False
                continue
            
            # Variantlar: A. ...
            opt_match = re.match(r'^([A-D])\.\s*(.*)$', s, re.IGNORECASE)
            if opt_match:
                in_options = True
                current_q["options"].append(opt_match.group(2).strip())
                continue
            
            # Javob: ANSWER: A
            ans_match = re.match(r'^ANSWER:\s*([A-D])$', s, re.IGNORECASE)
            if ans_match:
                current_q["correct"] = ord(ans_match.group(1).upper()) - ord('A')
                continue
            
            # Agar savol matni bir nechta qatorga cho'zilgan bo'lsa (masalan, kod bloklari)
            if current_q and not in_options and len(current_q["options"]) == 0:
                current_q["text"] += " " + s
        
        # Oxirgi savolni qo'shish
        if current_q["text"] and len(current_q["options"]) == 4:
            result.append(current_q)
            
        print(f"  ✅ {file_name} (Yangi format) → {len(result)} ta savol")
        return result

    # ESKI FORMAT uchun zaxira (A), B), C), D), (Javob: A))
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
    
    print(f"  ✅ {file_name} (Eski format) → {len(result)} ta savol")
    return result

# Barcha savollarni yuklash va xotirada saqlash
questions_db = {}
total_all_questions = 0

for subj_id, subj_data in SUBJECTS.items():
    questions_db[subj_id] = {"title": subj_data["title"], "topics": {}}
    for file_name, display_name in subj_data["topics"]:
        qs = parse_file(file_name)
        questions_db[subj_id]["topics"][file_name] = {"title": display_name, "questions": qs}
        total_all_questions += len(qs)

print(f"\n🎯 Jami {total_all_questions} ta savol muvaffaqiyatli yuklandi!")

# ================= BOT SOZLAMALARI =================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

class QuizStates(StatesGroup):
    idle = State()
    playing = State()

user_sessions = {}

active_game = {
    "is_running": False, "chat_id": None, "questions": [], "topic_name": "🎓 Barcha savollar",
    "current_q": 0, "poll_id": None, "message_id": None,
    "timer_task": None, "update_timer_task": None,
    "scores": {}, "participants": set(), "answers": {}
}

async def set_bot_commands():
    private_commands = [
        BotCommand(command="start", description="🚀 Testni boshlash"),
        BotCommand(command="topics", description="📚 Fanni va mavzuni tanlash"),
        BotCommand(command="progress", description="📊 Joriy natijam"),
        BotCommand(command="stats", description="📈 Shaxsiy statistika"),
        BotCommand(command="stop", description="⏹ Testni to'xtatish"),
    ]
    try:
        await bot.set_my_commands(private_commands, scope={"type": "private"})
        print("✅ Command'lar sozlandi!")
    except Exception as e:
        print(f"⚠️ Command sozlashda xato: {e}")

def get_stop_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏹ To'xtatish", callback_data="stop_quiz")]
    ])

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
        session = user_sessions.get(user_id)
        if not session: return
        try: await bot.stop_poll(chat_id=session["chat_id"], message_id=session["message_id"])
        except Exception: pass
        if session.get("update_timer_task"): session["update_timer_task"].cancel()
        session["current_q"] += 1
        msg = await bot.send_message(session["chat_id"], "⏳ Vaqt tugadi! ➡️ Keyingi savol:", reply_markup=get_stop_keyboard())
        await ask_question_pm(msg, state, user_id)
    except asyncio.CancelledError: pass

# ================= HANDLERLAR =================
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    if message.from_user.is_bot: return
    user_id = message.from_user.id

    if message.chat.type == 'private':
        if (user_id in user_sessions and 
            user_sessions[user_id].get("current_q", 0) > 0 and
            user_sessions[user_id].get("current_q", 0) < len(user_sessions[user_id].get("questions", []))):
            s = user_sessions[user_id]
            await message.answer(
                f"⚠️ Siz allaqachon test yechayapsiz!\n"
                f"📝 Savol: {s['current_q']+1}/{len(s['questions'])}\n"
                f"✅ To'g'ri: {s['score']}\n\nDavom eting yoki /stop bosing.",
                parse_mode="HTML"
            )
            return
        
        if user_id in user_sessions:
            if user_sessions[user_id].get("timer_task"): user_sessions[user_id]["timer_task"].cancel()
            if user_sessions[user_id].get("update_timer_task"): user_sessions[user_id]["update_timer_task"].cancel()
            del user_sessions[user_id]
        
        # FANLARNI TANLASH MENYUSI
        kb = []
        for subj_id, subj_data in questions_db.items():
            total_q = sum(len(t["questions"]) for t in subj_data["topics"].values())
            kb.append([InlineKeyboardButton(text=f"{subj_data['title']} ({total_q} ta)", callback_data=f"subj_{subj_id}")])
        
        await message.answer(
            f"👋 <b>Salom, {message.from_user.full_name}!</b>\n\n📚 <b>Oraliq Quiz Bot</b>\n"
            f"⏱ Har bir savolga: <b>{QUIZ_TIME}s</b>\n"
            f"📦 Savollar {PM_BLOCK_SIZE} tadan bloklarga bo'lingan\n\n🎯 <b>Fanni tanlang:</b>",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
        await state.set_state(QuizStates.idle)

@router.message(Command("topics"))
async def cmd_topics(message: Message):
    if message.from_user.is_bot: return
    if message.chat.type != 'private':
        await message.answer("📚 Mavzu tanlash faqat shaxsiy chatda ishlaydi!")
        return
    
    kb = []
    for subj_id, subj_data in questions_db.items():
        total_q = sum(len(t["questions"]) for t in subj_data["topics"].values())
        kb.append([InlineKeyboardButton(text=f"{subj_data['title']} ({total_q} ta)", callback_data=f"subj_{subj_id}")])
    
    await message.answer("📚 <b>Fanni tanlang:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data.startswith("subj_"))
async def show_topics(callback: CallbackQuery):
    subj_id = callback.data.replace("subj_", "")
    subj_data = questions_db.get(subj_id)
    if not subj_data: return
    
    kb = []
    for file_name, topic_data in subj_data["topics"].items():
        count = len(topic_data["questions"])
        kb.append([InlineKeyboardButton(text=f"{topic_data['title']} ({count} ta)", callback_data=f"topic_{subj_id}_{file_name}")])
    kb.append([InlineKeyboardButton(text="⬅️ Orqaga (Fanlar)", callback_data="show_main_menu")])
    
    await callback.message.edit_text(f"📖 <b>{subj_data['title']}</b>\n\nMavzuni tanlang:", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()

@router.callback_query(F.data == "show_main_menu")
async def show_main_menu(callback: CallbackQuery):
    kb = []
    for subj_id, subj_data in questions_db.items():
        total_q = sum(len(t["questions"]) for t in subj_data["topics"].values())
        kb.append([InlineKeyboardButton(text=f"{subj_data['title']} ({total_q} ta)", callback_data=f"subj_{subj_id}")])
    
    await callback.message.edit_text("🎯 <b>Fanni tanlang:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    await callback.answer()

@router.callback_query(F.data.startswith("topic_"))
async def choose_topic_pm(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    parts = callback.data.replace("topic_", "").split("_", 1)
    if len(parts) != 2: return
    
    subj_id, file_name = parts
    topic_data = questions_db.get(subj_id, {}).get("topics", {}).get(file_name)
    
    if not topic_data:
        await callback.answer("Xatolik yuz berdi", show_alert=True)
        return

    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "current_q": 0, "score": 0, "timer_task": None, "update_timer_task": None,
            "poll_id": None, "chat_id": callback.message.chat.id, "message_id": None, "timer_q_index": -1,
            "full_name": callback.from_user.full_name, "username": callback.from_user.username
        }
    
    session = user_sessions[user_id]
    session["questions"] = topic_data["questions"]
    session["topic_name"] = topic_data["title"]
    session["current_q"] = 0
    session["score"] = 0
    session["chat_id"] = callback.message.chat.id
    session["block_scores"] = [0] * ((len(session["questions"]) + PM_BLOCK_SIZE - 1) // PM_BLOCK_SIZE)
    
    await callback.message.edit_text(f"🚀 <b>{session['topic_name']}</b> boshlandi!\nJami savollar: {len(session['questions'])} ta\nHar {PM_BLOCK_SIZE} tadan blok bo'lib chiqadi.", parse_mode="HTML")
    await callback.answer()
    
    msg = await bot.send_message(callback.message.chat.id, "➡️ Birinchi savol:", reply_markup=get_stop_keyboard())
    await ask_question_pm(msg, state, user_id)

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

@router.message(Command("stop"))
@router.callback_query(F.data == "stop_quiz")
async def cmd_stop(message_or_callback, state: FSMContext = None):
    # Handler moslashuvi: Message yoki CallbackQuery bo'lishi mumkin
    if isinstance(message_or_callback, CallbackQuery):
        user_id = message_or_callback.from_user.id
        chat_type = message_or_callback.message.chat.type
        respond = message_or_callback.message.answer
        ack = message_or_callback.answer
    else:
        user_id = message_or_callback.from_user.id
        chat_type = message_or_callback.chat.type
        respond = message_or_callback.answer
        ack = lambda: None

    if chat_type == 'private':
        session = user_sessions.get(user_id)
        if session:
            if session.get("timer_task"): session["timer_task"].cancel()
            if session.get("update_timer_task"): session["update_timer_task"].cancel()
            
            total = len(session["questions"])
            score = session["score"]
            save_result(user_id, session.get("username"), session.get("full_name"), score, total, session["topic_name"], session["chat_id"])
            del user_sessions[user_id]
            
            await respond(
                f"⏹ <b>Test to'xtatildi!</b>\n\n"
                f"✅ Natija: <b>{score}</b> / <b>{total}</b>\n"
                f"📈 Foiz: <b>{round(score / total * 100) if total > 0 else 0}%</b>\n\n"
                f"Qayta boshlash uchun /start bosing.",
                parse_mode="HTML"
            )
            if state: await state.set_state(QuizStates.idle)
        else:
            await respond("Siz hali test boshlamagansiz.")
    else:
        if user_id == ADMIN_ID and active_game["is_running"]:
            await end_game_group()
    await ack()

@router.poll_answer()
async def process_poll_answer(poll_answer: PollAnswer, state: FSMContext):
    user_id = poll_answer.user.id
    option_id = poll_answer.option_ids[0]

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
        
        # BLOK NATIJASINI KO'RSATISH (Har 30 ta savolda)
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
        except Exception as e: 
            print(f"PM Xatolik: {e}")
        return

# ================= STATISTIKA =================
@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.is_bot: return
    if message.chat.type != 'private':
        await message.answer("📊 Bu command faqat shaxsiy chatda ishlaydi!")
        return
    
    user_id = message.from_user.id
    conn = sqlite3.connect('quiz_results.db')
    cursor = conn.cursor()
    cursor.execute("SELECT topic, score, total_questions, date FROM results WHERE user_id = ? ORDER BY date DESC", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        await message.answer("📊 Siz hali test ishlamagansiz. /start bosing!")
        return
    
    text = f"📊 <b>Sizning Statistikangiz</b>\n\n"
    for topic, score, total, date in rows:
        percent = round(score / total * 100) if total else 0
        emoji = "🏆" if percent >= 80 else "👍" if percent >= 60 else "📚"
        text += f"{emoji} {topic}\n   ✅ {score}/{total} ({percent}%)\n   📅 {date}\n\n"
    await message.answer(text, parse_mode="HTML")

# ================= HTTP SERVER (Render uchun) =================
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
    print(f"🌐 HTTP server {port} portida ishga tushdi")

async def main():
    await set_bot_commands()
    asyncio.create_task(start_http_server())
    print("🚀 Bot ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
