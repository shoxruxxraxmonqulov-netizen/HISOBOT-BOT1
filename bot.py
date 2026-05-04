import asyncio
import logging
import sqlite3
import datetime
import re
import os
import calendar
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import WebhookInfo
from aiohttp import web
import pdfplumber

# ================= CONFIG =================
API_TOKEN = os.environ.get("API_TOKEN", "8739518073:AAFWoIH25mVRf10YWHHHwpCg5w_YEe383bk")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")   # Render, Railway da URL ni qo‘ying
WEBHOOK_PATH = "/webhook"
DAILY_TARGET = 3_000_000

logging.basicConfig(level=logging.INFO)

# Proksi YO‘Q – to‘g‘ridan-to‘g‘ri ulanish
session = AiohttpSession()
bot = Bot(token=API_TOKEN, session=session)
dp = Dispatcher()

# ================= DATABASE =================
conn = sqlite3.connect("bot_db.sqlite3", check_same_thread=False)
cursor = conn.cursor()

def init_db():
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        declaration_number TEXT UNIQUE,
        bojxona_sum INTEGER,
        deklarant_fish TEXT,
        avto TEXT,
        tirkama TEXT,
        kirish_posti TEXT,
        timestamp TEXT,
        date_only DATE,
        user_id INTEGER,
        user_full_name TEXT
    )""")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_props (
        user_id INTEGER,
        key TEXT,
        value TEXT,
        PRIMARY KEY (user_id, key)
    )""")
    conn.commit()
    print("? Database tayyor")

init_db()

# ================= KEYBOARDS =================
def group_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="?? Mening statistika")],
            [KeyboardButton(text="?? Mening hujjatlarim")],
            [KeyboardButton(text="?? Sana bo'yicha hisobot")]
        ],
        resize_keyboard=True
    )

# ================= TOOLS =================
def format_currency(amount):
    return f"{amount or 0:,}".replace(",", " ") + " so'm"

def transliterate_uzbek(text):
    mapping = {
        'à': 'a', 'á': 'b', 'â': 'v', 'ã': 'g', 'ä': 'd', 'å': 'e', '¸': 'yo',
        'æ': 'j', 'ç': 'z', 'è': 'i', 'é': 'y', 'ê': 'k', 'ë': 'l', 'ì': 'm',
        'í': 'n', 'î': 'o', 'ï': 'p', 'ð': 'r', 'ñ': 's', 'ò': 't', 'ó': 'u',
        'ô': 'f', 'õ': 'x', 'ö': 'c', '÷': 'ch', 'ø': 'sh', 'ú': "'", 'û': 'i',
        'ü': "'", 'ý': 'e', 'þ': 'yu', 'ÿ': 'ya', '?': "g'", '?': 'h', '?': 'q',
        '¢': "o'", 'À': 'A', 'Á': 'B', 'Â': 'V', 'Ã': 'G', 'Ä': 'D', 'Å': 'E',
        '¨': 'Yo', 'Æ': 'J', 'Ç': 'Z', 'È': 'I', 'É': 'Y', 'Ê': 'K', 'Ë': 'L',
        'Ì': 'M', 'Í': 'N', 'Î': 'O', 'Ï': 'P', 'Ð': 'R', 'Ñ': 'S', 'Ò': 'T',
        'Ó': 'U', 'Ô': 'F', 'Õ': 'X', 'Ö': 'C', '×': 'Ch', 'Ø': 'Sh', 'Ú': "'",
        'Û': 'I', 'Ü': "'", 'Ý': 'E', 'Þ': 'Yu', 'ß': 'Ya', '?': "G'", '?': 'H',
        '?': 'Q', '¡': "O'",
    }
    return ''.join(mapping.get(c, c) for c in text)

def extract_bojxona_service_sum(full_text):
    full_text = transliterate_uzbek(full_text)
    full_text = re.sub(r'[\n\r\t]+', ' ', full_text)
    full_text = re.sub(r'\s{2,}', ' ', full_text.strip())
    full_text = full_text.replace('`', "'").replace('’', "'").replace(',', '.')
    lower_text = full_text.lower()

    total = 0
    total_match = re.findall(r"(\d{1,3}(?:\s*\d{3})*)[.,]\d{2}", full_text)
    if total_match:
        jami_str = total_match[-1].replace(" ", "")
        total = int(jami_str)

    if total == 0:
        all_sums = re.findall(r'(\d{5,})\s*so\'?m?', lower_text)
        if all_sums:
            total = max(int(s.replace(' ', '').replace('.', '')) for s in all_sums)

    if total == 0:
        return 0

    is_mb = bool(re.search(r'(mb|MB)\d{5,}', lower_text))
    if is_mb:
        mb_patterns = [
            r'bojxona\s*servis\s*xizmatlari\s*1\s*(\d{3,6})\.?\d*',
            r'bojxona\s*servis\s*.*?(\d{5,})\.?\d*',
            r'servis\s*yig\'imi\s*(\d{5,})',
        ]
        for pat in mb_patterns:
            m = re.search(pat, lower_text)
            if m:
                val = m.group(1).replace('.', '').replace(' ', '')
                if val.isdigit() and 10000 < int(val) < 100000:
                    return int(val)

    other_services_sum = 0
    service_lines = re.findall(r"([A-Za-zÀ-ßà-ÿ¨¸¡???¢???\s]+?)\s+(\d{1,3}(?:\s*\d{3})*)[.,]\d{2}", full_text, re.IGNORECASE)
    bojxona_keywords = ["bojxona servis", "bojxona xizmat", "bojxona", "servis xizmatlari"]
    for name, amount_str in service_lines:
        amount = int(amount_str.replace(" ", ""))
        if not any(keyword in name.lower() for keyword in bojxona_keywords):
            other_services_sum += amount

    bojxona_sum = total - other_services_sum
    return bojxona_sum if bojxona_sum > 5000 else total

def parse_pdf_data(full_text, chat_title=None):
    full_text = transliterate_uzbek(full_text)
    full_text = re.sub(r'[\n\r\t]+', ' ', full_text)
    full_text = re.sub(r'\s{2,}', ' ', full_text.strip())
    full_text = full_text.replace('`', "'").replace('’', "'").replace(',', '.')

    at_number = "Topilmadi"
    m = re.search(r"((AT|MB)\d{5,})", full_text, re.IGNORECASE)
    if m:
        at_number = m.group(1).upper()

    deklarant = "Noma'lum"
    m = re.search(r"Deklarant:\s*([A-ZÀ-ß¨¡???]{2,})\s+([A-ZÀ-ß¨¡???]{2,})", full_text)
    if m:
        deklarant = f"{m.group(1)} {m.group(2)}"
    if deklarant == "Noma'lum":
        m = re.search(r"Deklarant[:\s]*([A-ZÀ-ß¨¡???]{2,}(?:\s+[A-ZÀ-ß¨¡???]{2,})?)", full_text, re.IGNORECASE)
        if m:
            deklarant = m.group(1).strip()

    avto, tirkama = "Yo'q", "Yo'q"
    if re.search(r'MB\d{5,}', full_text, re.IGNORECASE):
        m = re.search(r"Avtotransport raqami:\s*(\S+)", full_text, re.IGNORECASE)
        if m:
            avto = m.group(1).strip()
        if avto == "Yo'q":
            m = re.search(r"AVTOTRANSPORT RAQAMI[:\s]*(\S+)", full_text, re.IGNORECASE)
            if m:
                avto = m.group(1).strip()
        if avto == "Yo'q":
            m = re.search(r'\b(\d{5}[A-Z]{3})\b', full_text)
            if m:
                avto = m.group(1)

    if avto == "Yo'q":
        patterns = [
            r"(?i)GOS\.?¹\s*AVTO/TIRKAMA:\s*([A-Z0-9]{5,})\s*/\s*([A-Z0-9]{5,})?",
            r"(?i)AVTOTRANSPORT\s*RAQAMI\s*[:=]\s*([A-Z0-9]{7,10})",
            r'(?i)AVTOTRANSPORT\s*RAQAMI.*?([A-Z0-9]{7,10})',
            r'\b(\d{5}[A-Z]{3})\b',
            r'\b(\d{2}[A-Z]{2}\d{3})\b',
        ]
        for pat in patterns:
            m = re.search(pat, full_text, re.IGNORECASE)
            if m:
                if len(m.groups()) == 2 and m.group(1) and m.group(2):
                    avto = m.group(1).upper()
                    tirkama = m.group(2).upper()
                elif m.group(1):
                    avto = m.group(1).upper()
                break

    kirish_posti = "Noma'lum"
    m = re.search(r"Kirish bojxona posti:\s*(.+?)(?:\s+Tashuvchi nomi:|$)", full_text, re.IGNORECASE)
    if m:
        kirish_posti = m.group(1).strip()
    else:
        m = re.search(r"Yo'nalish\s+Kirish", full_text, re.IGNORECASE)
        if m:
            kirish_posti = "Kirish"

    if kirish_posti == "Noma'lum" and chat_title:
        kirish_posti = chat_title

    return at_number, deklarant, avto, tirkama, kirish_posti

def check_declaration_exists(declaration_number):
    cursor.execute("SELECT declaration_number, user_full_name, timestamp FROM files WHERE declaration_number = ?", (declaration_number,))
    return cursor.fetchone()

def get_user_stats(user_id):
    today = datetime.date.today().isoformat()
    week_ago = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
    month_ago = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()

    cursor.execute("SELECT COUNT(*), SUM(bojxona_sum) FROM files WHERE user_id = ? AND date_only = ?", (user_id, today))
    today_count, today_sum = cursor.fetchone()
    today_sum = today_sum or 0

    cursor.execute("SELECT COUNT(*), SUM(bojxona_sum) FROM files WHERE user_id = ? AND date_only BETWEEN ? AND ?", (user_id, week_ago, today))
    week_count, week_sum = cursor.fetchone()
    week_sum = week_sum or 0

    cursor.execute("SELECT COUNT(*), SUM(bojxona_sum) FROM files WHERE user_id = ? AND date_only BETWEEN ? AND ?", (user_id, month_ago, today))
    month_count, month_sum = cursor.fetchone()
    month_sum = month_sum or 0

    return {
        'today': (today_count, today_sum),
        'week': (week_count, week_sum),
        'month': (month_count, month_sum)
    }

def get_declarant_stats(user_id):
    cursor.execute("""
        SELECT deklarant_fish, COUNT(*), SUM(bojxona_sum)
        FROM files
        WHERE user_id = ? AND deklarant_fish IS NOT NULL AND deklarant_fish != 'Noma''lum'
        GROUP BY deklarant_fish
        ORDER BY SUM(bojxona_sum) DESC
    """, (user_id,))
    return cursor.fetchall()

def get_total_amount(user_id):
    cursor.execute("SELECT SUM(bojxona_sum) FROM files WHERE user_id = ?", (user_id,))
    total = cursor.fetchone()[0]
    return total or 0

def get_today_total(user_id):
    today = datetime.date.today().isoformat()
    cursor.execute("SELECT SUM(bojxona_sum) FROM files WHERE user_id = ? AND date_only = ?", (user_id, today))
    total = cursor.fetchone()[0]
    return total or 0

async def send_congratulation(user_id, total_amount):
    milestones = {
        1_000_000: "?? **1 000 000 so‘m** – birinchi million! Tabriklaymiz! ??",
        10_000_000: "?? **10 000 000 so‘m** – o‘n million! Ajoyib natija! ??",
        20_000_000: "?? **20 000 000 so‘m** – yigirma million! Juda zo‘r! ??",
        30_000_000: "?? **30 000 000 so‘m** – o‘ttiz million! Siz haqiqiy rekordchisiz! ??",
        40_000_000: "?? **40 000 000 so‘m** – qirq million! Eng yaxshi deklarantlardan birisiz! ??",
        50_000_000: "?? **50 000 000 so‘m** – ellik million! Bu katta yutuq! ??",
        60_000_000: "?? **60 000 000 so‘m** – oltmish million! Barakalla! ??",
        70_000_000: "?? **70 000 000 so‘m** – yetmish million! Super natija! ??",
        80_000_000: "?? **80 000 000 so‘m** – sakson million! Siz chempionsiz! ??",
        90_000_000: "?? **90 000 000 so‘m** – to‘qson million! Juda kuchli! ??",
        100_000_000: "?????? **100 000 000 so‘m** – YUZ MILLION! Tarixiy yutuq! Tabriklaymiz! ??????"
    }
    if total_amount >= 10_000_000 and total_amount % 10_000_000 == 0:
        if total_amount in milestones:
            try:
                await bot.send_message(user_id, milestones[total_amount], parse_mode="Markdown")
            except:
                pass
    if total_amount >= 1_000_000 and total_amount < 10_000_000 and total_amount == 1_000_000:
        try:
            await bot.send_message(user_id, milestones[1_000_000], parse_mode="Markdown")
        except:
            pass

async def check_daily_target(user_id, today_total):
    if today_total >= DAILY_TARGET:
        msg = f"?? **Bugungi reja 100% bajarildi!**\n?? Bugun: {format_currency(today_total)}\n?? Tabriklaymiz, ajoyib ish!"
        try:
            await bot.send_message(user_id, msg, parse_mode="Markdown")
        except:
            pass

# ================= SINGLE PDF PROCESSING (webhook versiya) =================
async def process_single_pdf(message: types.Message, document: types.Document):
    path = f"tmp_{document.file_unique_id}.pdf"
    try:
        file = await bot.get_file(document.file_id)
        await bot.download_file(file.file_path, path)

        with pdfplumber.open(path) as pdf:
            full_text = ""
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    full_text += text + "\n"

        if not full_text.strip():
            await message.reply("? Matn o'qib bo'lmadi (skaner bo'lishi mumkin).", parse_mode="Markdown")
            return

        at, dekl, avto, tirk, post = parse_pdf_data(full_text, message.chat.title)
        summa = extract_bojxona_service_sum(full_text)

        if at == "Topilmadi":
            await message.reply("? PDF da AT yoki MB raqami topilmadi!", parse_mode="Markdown")
            return

        existing = check_declaration_exists(at)
        if existing:
            await message.reply(
                f"?? **Bu deklaratsiya avval yuklangan!**\n?? `{at}`\n?? {existing[1]}\n? {existing[2]}",
                parse_mode="Markdown"
            )
            return

        cursor.execute("""
            INSERT INTO files (declaration_number, bojxona_sum, deklarant_fish, avto, tirkama, kirish_posti, timestamp, date_only, user_id, user_full_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            at, summa, dekl, avto, tirk, post,
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            datetime.date.today().isoformat(),
            message.from_user.id,
            message.from_user.full_name or message.from_user.username or "Noma'lum"
        ))
        conn.commit()

        total_amount = get_total_amount(message.from_user.id)
        today_total = get_today_total(message.from_user.id)
        await send_congratulation(message.from_user.id, total_amount)
        await check_daily_target(message.from_user.id, today_total)

        stats = get_user_stats(message.from_user.id)
        decl_stats = get_declarant_stats(message.from_user.id)

        text = (
            f"? **PDF qabul qilindi!**\n\n"
            f"?? `{at}`\n"
            f"?? {post}\n"
            f"?? {avto} / {tirk}\n"
            f"?? {dekl}\n"
            f"?? {format_currency(summa)}\n\n"
            f"?? **Sizning statistikangiz:**\n"
            f"?? Bugun: {stats['today'][0]} ta | {format_currency(stats['today'][1])}\n"
            f"?? Haftalik: {stats['week'][0]} ta | {format_currency(stats['week'][1])}\n"
            f"?? Oylik: {stats['month'][0]} ta | {format_currency(stats['month'][1])}\n"
        )
        if decl_stats:
            text += "\n?? **Deklarantlar bo'yicha:**\n"
            for dekl_name, cnt, s in decl_stats:
                text += f"?? {dekl_name}: {cnt} ta | {format_currency(s or 0)}\n"
        else:
            text += "\n?? Deklarantlar bo'yicha ma'lumot yo'q."

        await message.reply(text, parse_mode="Markdown")

    except sqlite3.IntegrityError:
        await message.reply("?? Deklaratsiya allaqachon mavjud!", parse_mode="Markdown")
    except Exception as e:
        await message.reply(f"? **Xatolik:** {str(e)}", parse_mode="Markdown")
    finally:
        if os.path.exists(path):
            os.remove(path)

# ================= HANDLERS =================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.chat.type == "private":
        await message.answer("?? Bu bot faqat guruhlarda ishlaydi. Iltimos, botni guruingizga qo'shing.")
    else:
        await message.answer(
            "? **Bot guruhda ishga tushdi!**\n\n"
            "?? PDF fayl yuboring – avtomatik tahlil qilinadi.\n"
            "?? Webhook asosida ishlaydi – hech qachon uxlamaydi.\n\n"
            "Quyidagi tugmalar orqali statistikangizni ko‘rishingiz mumkin:",
            reply_markup=group_keyboard(),
            parse_mode="Markdown"
        )

@dp.message(Command("menu"))
async def show_menu(message: types.Message):
    if message.chat.type in ["group", "supergroup"]:
        await message.answer("?? **Asosiy menyu:**", reply_markup=group_keyboard(), parse_mode="Markdown")
    else:
        await message.answer("? Bu buyruq faqat guruhda ishlaydi.")

@dp.message(F.new_chat_members)
async def on_bot_added_to_group(message: types.Message):
    for member in message.new_chat_members:
        if member.id == (await bot.get_me()).id:
            await message.answer(
                "? **Bot muvaffaqiyatli qo'shildi!**\n\n"
                "?? PDF fayl yuboring – avtomatik tahlil qilinadi.\n"
                "Quyidagi tugmalar orqali statistikangizni ko‘rishingiz mumkin:",
                reply_markup=group_keyboard(),
                parse_mode="Markdown"
            )
            break

@dp.message(F.document)
async def handle_pdf_document(message: types.Message):
    if message.chat.type not in ["group", "supergroup"]:
        await message.answer("? Bot faqat guruhlarda ishlaydi!")
        return
    if not message.document.file_name.lower().endswith(".pdf"):
        await message.reply("? Faqat PDF fayl yuboring!", parse_mode="Markdown")
        return
    # Webhook versiyada loading_msg kerak emas, to‘g‘ridan-to‘g‘ri ishlov beriladi
    await process_single_pdf(message, message.document)

# ================= STATISTIKA VA HUJJATLAR =================
@dp.message(F.text == "?? Mening statistika")
async def my_stats(message: types.Message):
    if message.chat.type not in ["group", "supergroup"]:
        return
    stats = get_user_stats(message.from_user.id)
    decl_stats = get_declarant_stats(message.from_user.id)
    text = (
        f"?? **SIZNING STATISTIKANGIZ**\n\n"
        f"?? **Bugun:** {stats['today'][0]} ta | {format_currency(stats['today'][1])}\n"
        f"?? **Haftalik:** {stats['week'][0]} ta | {format_currency(stats['week'][1])}\n"
        f"?? **Oylik:** {stats['month'][0]} ta | {format_currency(stats['month'][1])}\n"
    )
    if decl_stats:
        text += "\n?? **Deklarantlar bo'yicha:**\n"
        for dekl_name, cnt, s in decl_stats:
            text += f"?? {dekl_name}: {cnt} ta | {format_currency(s or 0)}\n"
    else:
        text += "\n?? Deklarantlar bo'yicha ma'lumot yo'q."
    await message.reply(text, parse_mode="Markdown")

# ================= HUJJATLAR RO'YXATI (ORQAGA TUGMASI BILAN) =================
def get_files_list_keyboard(user_id):
    cursor.execute("SELECT id, declaration_number, avto, timestamp FROM files WHERE user_id=? ORDER BY id DESC LIMIT 30", (user_id,))
    files = cursor.fetchall()
    if not files:
        return None
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for fid, at, avto, ts in files:
        kb.inline_keyboard.append([
            InlineKeyboardButton(text=f"?? {at} | {avto}", callback_data=f"view_file:{fid}"),
            InlineKeyboardButton(text="??", callback_data=f"edit_file:{fid}"),
            InlineKeyboardButton(text="???", callback_data=f"del_file:{fid}")
        ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(text="?? Asosiy menyu", callback_data="back_to_main_keyboard")
    ])
    return kb

@dp.message(F.text == "?? Mening hujjatlarim")
async def my_files(message: types.Message):
    if message.chat.type not in ["group", "supergroup"]:
        return
    kb = get_files_list_keyboard(message.from_user.id)
    if kb is None:
        await message.reply("? Siz hali hech qanday PDF yuklamagansiz.")
        return
    await message.reply("?? **Sizning hujjatlaringiz (oxirgi 30 ta):**", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "back_to_main_keyboard")
async def back_to_main_keyboard(cb: types.CallbackQuery):
    if cb.message.chat.type in ["group", "supergroup"]:
        await cb.message.answer("?? **Asosiy menyu**", reply_markup=group_keyboard(), parse_mode="Markdown")
        await cb.message.delete()
    await cb.answer()

# ================= CRUD OPERATIONLAR =================
@dp.callback_query(F.data.startswith("view_file:"))
async def view_file(cb: types.CallbackQuery):
    fid = int(cb.data.split(":")[1])
    cursor.execute("SELECT declaration_number, bojxona_sum, deklarant_fish, avto, tirkama, kirish_posti, timestamp FROM files WHERE id=? AND user_id=?", (fid, cb.from_user.id))
    row = cursor.fetchone()
    if not row:
        await cb.answer("? Hujjat topilmadi yoki sizga tegishli emas!", show_alert=True)
        return
    at, s, dekl, avto, tirk, post, ts = row
    text = (
        f"?? **Deklaratsiya:** `{at}`\n"
        f"?? Summa: {format_currency(s)}\n"
        f"?? Deklarant: {dekl}\n"
        f"?? Avto: {avto} / {tirk}\n"
        f"?? Post: {post}\n"
        f"? Vaqt: {ts}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="?? Tahrirlash", callback_data=f"edit_file:{fid}"),
         InlineKeyboardButton(text="??? O'chirish", callback_data=f"del_file:{fid}")],
        [InlineKeyboardButton(text="?? Orqaga", callback_data="back_to_files")]
    ])
    await cb.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("edit_file:"))
async def edit_file_start(cb: types.CallbackQuery, state: FSMContext):
    fid = int(cb.data.split(":")[1])
    cursor.execute("SELECT id FROM files WHERE id=? AND user_id=?", (fid, cb.from_user.id))
    if not cursor.fetchone():
        await cb.answer("? Ruxsat yo'q!", show_alert=True)
        return
    await state.update_data(file_id=fid)
    await state.set_state(EditStates.waiting_field)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Deklarant", callback_data="edit_field:deklarant_fish"),
         InlineKeyboardButton(text="Avto", callback_data="edit_field:avto")],
        [InlineKeyboardButton(text="Tirkama", callback_data="edit_field:tirkama"),
         InlineKeyboardButton(text="Post", callback_data="edit_field:kirish_posti")],
        [InlineKeyboardButton(text="Summa", callback_data="edit_field:bojxona_sum")],
        [InlineKeyboardButton(text="? Bekor", callback_data="back_to_files")]
    ])
    await cb.message.edit_text("?? Qaysi maydonni o'zgartirmoqchisiz?", reply_markup=kb, parse_mode="Markdown")

class EditStates(StatesGroup):
    waiting_field = State()
    waiting_value = State()

@dp.callback_query(F.data.startswith("edit_field:"))
async def edit_field(cb: types.CallbackQuery, state: FSMContext):
    field = cb.data.split(":")[1]
    await state.update_data(field=field)
    await state.set_state(EditStates.waiting_value)
    field_names = {
        "deklarant_fish": "Deklarant ismi",
        "avto": "Avtomobil raqami",
        "tirkama": "Tirkama raqami",
        "kirish_posti": "Bojxona posti",
        "bojxona_sum": "Summa (faqat raqam)"
    }
    await cb.message.edit_text(f"?? Yangi {field_names.get(field, field)} ni kiriting:", parse_mode="Markdown")

@dp.message(EditStates.waiting_value)
async def edit_value(message: types.Message, state: FSMContext):
    data = await state.get_data()
    fid = data['file_id']
    field = data['field']
    new_value = message.text.strip()
    if field == "bojxona_sum":
        try:
            new_value = int(new_value.replace(" ", ""))
        except:
            await message.reply("? Summa faqat raqam bo'lishi kerak!", parse_mode="Markdown")
            return
    cursor.execute(f"UPDATE files SET {field}=? WHERE id=? AND user_id=?", (new_value, fid, message.from_user.id))
    conn.commit()
    await state.clear()
    await message.answer("? Muvaffaqiyatli o'zgartirildi!")
    await view_file_callback(message.from_user.id, fid, message)

async def view_file_callback(user_id, fid, message):
    cursor.execute("SELECT declaration_number, bojxona_sum, deklarant_fish, avto, tirkama, kirish_posti, timestamp FROM files WHERE id=? AND user_id=?", (fid, user_id))
    row = cursor.fetchone()
    if row:
        at, s, dekl, avto, tirk, post, ts = row
        text = f"?? `{at}`\n?? {format_currency(s)}\n?? {dekl}\n?? {avto} / {tirk}\n?? {post}\n? {ts}"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="?? Tahrirlash", callback_data=f"edit_file:{fid}"),
             InlineKeyboardButton(text="??? O'chirish", callback_data=f"del_file:{fid}")],
            [InlineKeyboardButton(text="?? Orqaga", callback_data="back_to_files")]
        ])
        await message.answer(text, reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("del_file:"))
async def confirm_delete(cb: types.CallbackQuery):
    fid = int(cb.data.split(":")[1])
    cursor.execute("SELECT declaration_number FROM files WHERE id=? AND user_id=?", (fid, cb.from_user.id))
    row = cursor.fetchone()
    if not row:
        await cb.answer("? Hujjat topilmadi!", show_alert=True)
        return
    at = row[0]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="? Ha, o'chir", callback_data=f"do_del_file:{fid}"),
         InlineKeyboardButton(text="? Yo'q", callback_data="back_to_files")]
    ])
    await cb.message.edit_text(f"?? `{at}` hujjatini o'chirishni tasdiqlaysizmi?", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data.startswith("do_del_file:"))
async def do_delete(cb: types.CallbackQuery):
    fid = int(cb.data.split(":")[1])
    cursor.execute("DELETE FROM files WHERE id=? AND user_id=?", (fid, cb.from_user.id))
    conn.commit()
    await cb.answer("? Hujjat o'chirildi!", show_alert=True)
    await cb.message.edit_text("??? Hujjat o'chirildi.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="?? Orqaga", callback_data="back_to_files")]]))

@dp.callback_query(F.data == "back_to_files")
async def back_to_files(cb: types.CallbackQuery):
    kb = get_files_list_keyboard(cb.from_user.id)
    if kb is None:
        await cb.message.edit_text("? Sizda hujjatlar mavjud emas.")
        return
    await cb.message.edit_text("?? **Sizning hujjatlaringiz (oxirgi 30 ta):**", reply_markup=kb, parse_mode="Markdown")

# ================= SANA BO'YICHA HISOBOT =================
@dp.message(F.text == "?? Sana bo'yicha hisobot")
async def date_report_button(message: types.Message):
    if message.chat.type not in ["group", "supergroup"]:
        return
    now = datetime.datetime.now()
    await message.answer(
        "?? **Hisobot uchun BOSHLANISH sanasini tanlang:**",
        reply_markup=get_calendar_markup(now.year, now.month, message.from_user.id, "start"),
        parse_mode="Markdown"
    )

def get_calendar_markup(year, month, user_id, side):
    markup = []
    month_names = ["Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun", "Iyul", "Avgust", "Sentyabr", "Oktyabr", "Noyabr", "Dekabr"]
    markup.append([InlineKeyboardButton(text=f"?? {month_names[month-1]} {year}", callback_data="ignore")])
    markup.append([InlineKeyboardButton(text=d, callback_data="ignore") for d in ["Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya"]])
    month_calendar = calendar.monthcalendar(year, month)
    for week in month_calendar:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="ignore"))
            else:
                row.append(InlineKeyboardButton(text=str(day), callback_data=f"cal_date:{user_id}:{side}:{year}:{month}:{day}"))
        markup.append(row)
    markup.append([
        InlineKeyboardButton(text="??", callback_data=f"nav_cal:{user_id}:{side}:{year}:{month}:prev"),
        InlineKeyboardButton(text="? Bekor", callback_data="cancel_report"),
        InlineKeyboardButton(text="??", callback_data=f"nav_cal:{user_id}:{side}:{year}:{month}:next")
    ])
    return InlineKeyboardMarkup(inline_keyboard=markup)

@dp.callback_query(F.data.startswith("nav_cal:"))
async def nav_calendar(cb: types.CallbackQuery):
    _, user_id, side, year_str, month_str, action = cb.data.split(":")
    year, month = int(year_str), int(month_str)
    if action == "prev":
        month -= 1
        if month < 1:
            month = 12
            year -= 1
    else:
        month += 1
        if month > 12:
            month = 1
            year += 1
    await cb.message.edit_reply_markup(reply_markup=get_calendar_markup(year, month, int(user_id), side))

@dp.callback_query(F.data.startswith("cal_date:"))
async def process_date(cb: types.CallbackQuery):
    parts = cb.data.split(":")
    user_id = int(parts[1])
    side = parts[2]
    year, month, day = int(parts[3]), int(parts[4]), int(parts[5])
    selected = f"{year}-{month:02d}-{day:02d}"

    if cb.from_user.id != user_id:
        await cb.answer("? Ruxsatsiz!", show_alert=True)
        return

    if side == "start":
        cursor.execute("INSERT OR REPLACE INTO user_props (user_id, key, value) VALUES (?, ?, ?)", (user_id, "report_start", selected))
        conn.commit()
        await cb.message.edit_text(f"?? Boshlanish: {selected}\n? **Yakuniy sanani tanlang:**", reply_markup=get_calendar_markup(year, month, user_id, "end"), parse_mode="Markdown")
    else:
        cursor.execute("SELECT value FROM user_props WHERE user_id=? AND key=?", (user_id, "report_start"))
        row = cursor.fetchone()
        if not row:
            await cb.answer("Boshlanish sanasi topilmadi!", show_alert=True)
            return
        start_date = row[0]
        end_date = selected
        cursor.execute("""
            SELECT declaration_number, bojxona_sum, deklarant_fish, avto, tirkama, kirish_posti, timestamp
            FROM files WHERE user_id=? AND date_only BETWEEN ? AND ?
            ORDER BY timestamp DESC
        """, (user_id, start_date, end_date))
        rows = cursor.fetchall()

        if not rows:
            text = f"? {start_date} dan {end_date} gacha hech qanday hujjat topilmadi."
        else:
            total_sum = sum(r[1] for r in rows if r[1])
            text = f"?? **{start_date} – {end_date}**\n?? Jami: {len(rows)} ta\n?? Summa: {format_currency(total_sum)}\n\n"
            for at, s, dekl, avto, tirk, post, ts in rows[:20]:
                text += f"?? `{at}` | {avto} | {format_currency(s)}\n"
            if len(rows) > 20:
                text += f"\n... va {len(rows)-20} ta ko'proq"

        await cb.message.answer(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="?? Orqaga", callback_data="back_to_stats")]]))
        await cb.message.delete()
        await cb.answer()

@dp.callback_query(F.data == "cancel_report")
async def cancel_report(cb: types.CallbackQuery):
    await cb.message.delete()
    await cb.answer("Bekor qilindi")

@dp.callback_query(F.data == "back_to_stats")
async def back_to_stats(cb: types.CallbackQuery):
    stats = get_user_stats(cb.from_user.id)
    decl_stats = get_declarant_stats(cb.from_user.id)
    text = (
        f"?? **SIZNING STATISTIKANGIZ**\n\n"
        f"?? **Bugun:** {stats['today'][0]} ta | {format_currency(stats['today'][1])}\n"
        f"?? **Haftalik:** {stats['week'][0]} ta | {format_currency(stats['week'][1])}\n"
        f"?? **Oylik:** {stats['month'][0]} ta | {format_currency(stats['month'][1])}\n"
    )
    if decl_stats:
        text += "\n?? **Deklarantlar bo'yicha:**\n"
        for dekl_name, cnt, s in decl_stats:
            text += f"?? {dekl_name}: {cnt} ta | {format_currency(s or 0)}\n"
    await cb.message.answer(text, parse_mode="Markdown", reply_markup=group_keyboard())
    await cb.message.delete()

# ================= WEBHOQQ SOZLASH =================
async def on_startup():
    if not WEBHOOK_URL:
        logging.error("WEBHOOK_URL environment variable not set!")
        return
    await bot.set_webhook(f"{WEBHOOK_URL}{WEBHOOK_PATH}")
    logging.info(f"Webhook set to {WEBHOOK_URL}{WEBHOOK_PATH}")

async def on_shutdown():
    await bot.delete_webhook()
    logging.info("Webhook deleted")

async def handle_webhook(request):
    update = types.Update(**await request.json())
    await dp.feed_update(bot, update)
    return web.Response()

def main():
    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, handle_webhook)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    port = int(os.environ.get("PORT", 8080))
    web.run_app(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()