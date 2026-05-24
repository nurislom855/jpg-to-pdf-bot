import os
import sys
import logging
import json
import time
import subprocess
import tempfile
import asyncio
from PIL import Image
import img2pdf
from io import BytesIO
from datetime import datetime
import urllib.request
import urllib.parse

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ChatMember, InputFile, ReplyKeyboardMarkup, KeyboardButton,
    ReplyKeyboardRemove
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ==================== SOZLAMALAR ====================
BOT_TOKEN         = os.environ.get("BOT_TOKEN", "8778116378:AAHvHV0ce7WlKItOfAGCOWL44I3AqRZHbBw")
CHANNEL_USERNAME  = "@jpg_to_pdf_otkaz"
ADMIN_ID          = 7406325328
ADMIN_USERNAME    = "nurislomdev"
REDIS_URL         = os.environ.get("REDIS_URL",   "https://mutual-satyr-95515.upstash.io")
REDIS_TOKEN       = os.environ.get("REDIS_TOKEN", "gQAAAAAAAXUbAAIgcDE2ZWY1NjFlZWM0NTU0ODQxYjI1NDBlM2VlNWU3OTgzNA")

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# xotira
user_images: dict[int, list[bytes]] = {}
user_pdf_list: dict[int, list[tuple]] = {}   # PDF merge uchun [(name, bytes)]
user_mode: dict[int, str] = {}


# ==================== REPLY KEYBOARD ====================
def main_reply_keyboard():
    """Foydalanuvchi uchun quyi tugmalar paneli"""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📸 JPG → PDF"),    KeyboardButton("📄 Word → PDF")],
            [KeyboardButton("📊 PPTX → PDF"),   KeyboardButton("🔗 PDF Birlashtirish")],
            [KeyboardButton("📅 Dars jadvali"), KeyboardButton("💬 Admin")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Xizmatni tanlang..."
    )

def admin_reply_keyboard():
    """Admin uchun quyi tugmalar paneli"""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("👥 Foydalanuvchilar"), KeyboardButton("📊 Statistika")],
            [KeyboardButton("📣 Broadcast"),        KeyboardButton("🔍 Qidirish")],
            [KeyboardButton("🔙 Asosiy menyu")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Admin amali..."
    )


# ==================== REDIS ====================
def redis_get(key):
    try:
        url = f"{REDIS_URL}/get/{urllib.parse.quote(key, safe='')}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {REDIS_TOKEN}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            result = data.get("result")
            if result:
                return json.loads(result)
    except Exception as e:
        logger.error(f"Redis get xato: {e}")
    return None


def redis_set(key, value):
    try:
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        encoded = urllib.parse.quote(payload, safe="")
        url = f"{REDIS_URL}/set/{urllib.parse.quote(key, safe='')}/{encoded}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {REDIS_TOKEN}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        logger.error(f"Redis set xato: {e}")
    return None


def load_db():
    db = redis_get("bot_db")
    if db is None:
        db = {"users": {}, "total_pdfs": 0, "total_requests": 0}
    if "total_requests" not in db:
        db["total_requests"] = 0
    return db


def save_db(db):
    redis_set("bot_db", db)


def register_user(user):
    db = load_db()
    uid = str(user.id)
    if uid not in db["users"]:
        db["users"][uid] = {
            "id": user.id,
            "name": user.full_name,
            "username": user.username or "",
            "joined": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "pdfs": 0,
            "blocked": False,
            "last_active": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
    else:
        db["users"][uid]["last_active"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    save_db(db)


def is_blocked(user_id):
    db = load_db()
    return db["users"].get(str(user_id), {}).get("blocked", False)


# ==================== A'ZOLIK ====================
async def check_subscription(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER]
    except Exception as e:
        logger.error(f"Sub tekshirish xato: {e}")
        return False


async def require_sub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """False qaytarsa — foydalanuvchi a'zo emas, xabar yuborildi."""
    user = update.effective_user
    if user.id == ADMIN_ID:
        return True
    if await check_subscription(user.id, context):
        return True
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Kanalga a'zo bo'lish", url=f"https://t.me/{CHANNEL_USERNAME[1:]}")],
        [InlineKeyboardButton("✅ A'zo bo'ldim", callback_data="check_sub")],
    ])
    await update.message.reply_text(
        "⚠️ Botdan foydalanish uchun kanalga a'zo bo'ling!",
        reply_markup=keyboard
    )
    return False


# ==================== /start ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)

    if is_blocked(user.id):
        await update.message.reply_text("❌ Siz botdan bloklangansiz.")
        return

    if user.id == ADMIN_ID:
        await update.message.reply_text(
            "🛠 <b>Admin sifatida kirdingiz!</b>\n\nQuyidan amal tanlang:",
            parse_mode="HTML",
            reply_markup=admin_reply_keyboard()
        )
        return

    if not await check_subscription(user.id, context):
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Kanalga a'zo bo'lish", url=f"https://t.me/{CHANNEL_USERNAME[1:]}")],
            [InlineKeyboardButton("✅ A'zo bo'ldim", callback_data="check_sub")],
        ])
        await update.message.reply_text(
            f"👋 Salom, {user.first_name}!\n\n⚠️ Botdan foydalanish uchun kanalga a'zo bo'ling:",
            reply_markup=keyboard
        )
        return

    await update.message.reply_text(
        f"👋 Xush kelibsiz, <b>{user.first_name}</b>!\n\n"
        "Quyidagi tugmalardan xizmatni tanlang 👇",
        parse_mode="HTML",
        reply_markup=main_reply_keyboard()
    )


# ==================== TEXT HANDLER ====================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user  = update.effective_user
    text  = update.message.text.strip()
    mode  = context.user_data.get("mode", "")

    if is_blocked(user.id):
        await update.message.reply_text("❌ Bloklangansiz.")
        return

    # ── ADMIN tugmalari ────────────────────────────────────────
    if user.id == ADMIN_ID:
        if text == "👥 Foydalanuvchilar":
            await admin_show_users(update, context, page=0)
            return
        if text == "📊 Statistika":
            await admin_show_stats(update, context)
            return
        if text == "📣 Broadcast":
            context.user_data["mode"] = "broadcast"
            await update.message.reply_text(
                "📣 Broadcast xabarini yozing:\n(Bekor qilish: /cancel)",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Bekor qilish")]], resize_keyboard=True)
            )
            return
        if text == "🔍 Qidirish":
            context.user_data["mode"] = "admin_search"
            await update.message.reply_text(
                "🔍 Foydalanuvchi ID yoki @username kiriting:",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Bekor qilish")]], resize_keyboard=True)
            )
            return
        if text == "🔙 Asosiy menyu":
            context.user_data.clear()
            await update.message.reply_text(
                "🛠 Admin panel:", reply_markup=admin_reply_keyboard()
            )
            return
        if text == "❌ Bekor qilish":
            context.user_data.clear()
            await update.message.reply_text("❌ Bekor qilindi.", reply_markup=admin_reply_keyboard())
            return

    # ── FOYDALANUVCHI tugmalari ────────────────────────────────
    if text == "📸 JPG → PDF":
        if not await require_sub(update, context): return
        context.user_data["mode"] = "jpg"
        user_images[user.id] = []
        await update.message.reply_text(
            "📸 Rasmlarni yuboring (1-100 ta JPG/PNG).\n"
            "Tayyor bo'lgach 👉 /done"
        )
        return

    if text == "📄 Word → PDF":
        if not await require_sub(update, context): return
        context.user_data["mode"] = "word"
        await update.message.reply_text("📄 Word faylini yuboring (.docx yoki .doc).")
        return

    if text == "📊 PPTX → PDF":
        if not await require_sub(update, context): return
        context.user_data["mode"] = "pptx"
        await update.message.reply_text("📊 PPTX faylini yuboring.")
        return

    if text == "🔗 PDF Birlashtirish":
        if not await require_sub(update, context): return
        context.user_data["mode"] = "pdf_merge"
        user_pdf_list[user.id] = []
        await update.message.reply_text(
            "🔗 PDFlarni ketma-ket yuboring (2-20 ta).\n"
            "Tayyor bo'lgach 👉 /done"
        )
        return

    if text == "📅 Dars jadvali":
        if not await require_sub(update, context): return
        context.user_data["mode"] = "timetable"
        await update.message.reply_text(
            "📅 Guruh nomini kiriting:\nMasalan: <code>II-52/24</code>",
            parse_mode="HTML"
        )
        return

    if text == "💬 Admin":
        context.user_data["mode"] = "feedback"
        await update.message.reply_text(
            "💬 Xabaringizni yozing, @nurislomdev ga yetkazamiz:"
        )
        return

    # ── MODE ga qarab keyingi harakatlar ──────────────────────

    if mode == "timetable":
        await fetch_timetable(update, context, text)
        return

    if mode == "feedback":
        uname = f"@{user.username}" if user.username else user.full_name
        await context.bot.send_message(
            ADMIN_ID,
            f"💬 <b>Yangi xabar</b>\n"
            f"👤 {uname} (<code>{user.id}</code>)\n\n{text}",
            parse_mode="HTML"
        )
        await update.message.reply_text("✅ Xabaringiz adminga yuborildi!")
        context.user_data.clear()
        return

    if mode == "broadcast" and user.id == ADMIN_ID:
        db    = load_db()
        sent  = fail = 0
        for uid, udata in db["users"].items():
            if not udata.get("blocked"):
                try:
                    await context.bot.send_message(
                        int(uid), f"📢 <b>Admin xabari:</b>\n\n{text}", parse_mode="HTML"
                    )
                    sent += 1
                except Exception:
                    fail += 1
        await update.message.reply_text(
            f"📣 Natija:\n✅ Yuborildi: {sent}\n❌ Xato: {fail}",
            reply_markup=admin_reply_keyboard()
        )
        context.user_data.clear()
        return

    if mode == "admin_search" and user.id == ADMIN_ID:
        await admin_search_user(update, context, text)
        return

    if mode == "waiting_pdf_name":
        context.user_data["mode"] = ""
        await generate_pdf_from_images(update, context, text)
        return

    if mode == "waiting_pptx_name":
        context.user_data["mode"] = ""
        filename = text if text.endswith(".pdf") else text + ".pdf"
        pdf_bytes = context.user_data.pop("pptx_pdf", None)
        if pdf_bytes:
            await update.message.reply_document(
                document=InputFile(BytesIO(pdf_bytes), filename=filename),
                caption=f"✅ <b>{filename}</b> tayyor!", parse_mode="HTML"
            )
        await update.message.reply_text("Boshqa xizmat:", reply_markup=main_reply_keyboard())
        return

    if mode == "waiting_word_name":
        context.user_data["mode"] = ""
        filename = text if text.endswith(".pdf") else text + ".pdf"
        pdf_bytes = context.user_data.pop("word_pdf", None)
        if pdf_bytes:
            await update.message.reply_document(
                document=InputFile(BytesIO(pdf_bytes), filename=filename),
                caption=f"✅ <b>{filename}</b> tayyor!", parse_mode="HTML"
            )
        await update.message.reply_text("Boshqa xizmat:", reply_markup=main_reply_keyboard())
        return

    # Default
    if user.id == ADMIN_ID:
        await update.message.reply_text("👆 Yuqoridagi tugmalardan foydalaning.", reply_markup=admin_reply_keyboard())
    else:
        await update.message.reply_text("👆 Tugmalardan xizmatni tanlang.", reply_markup=main_reply_keyboard())


# ==================== PHOTO HANDLER ====================
async def receive_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_blocked(user.id): return
    if not await require_sub(update, context): return

    if context.user_data.get("mode") != "jpg":
        context.user_data["mode"] = "jpg"
        user_images[user.id] = []

    photo = update.message.photo[-1]
    file  = await photo.get_file()
    data  = bytes(await file.download_as_bytearray())
    user_images.setdefault(user.id, []).append(data)

    count = len(user_images[user.id])
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 PDF yaratish", callback_data="create_pdf")],
        [InlineKeyboardButton("🗑 Bekor qilish",  callback_data="cancel_all")],
    ])
    await update.message.reply_text(
        f"✅ {count} ta rasm qabul qilindi.\n/done yoki tugma bosing:",
        reply_markup=kb
    )


# ==================== DOCUMENT HANDLER ====================
async def receive_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_blocked(user.id): return
    if not await require_sub(update, context): return

    doc  = update.message.document
    mode = context.user_data.get("mode", "")
    ext  = os.path.splitext(doc.file_name or "")[1].lower()

    # ── PPTX → PDF ────────────────────────────────────────────
    if mode == "pptx" or ext in (".pptx", ".ppt"):
        if ext not in (".pptx", ".ppt"):
            await update.message.reply_text("❌ Faqat .pptx yoki .ppt fayl yuboring!")
            return
        await convert_office_to_pdf(update, context, doc, ext, "pptx")

    # ── WORD → PDF ────────────────────────────────────────────
    elif mode == "word" or ext in (".docx", ".doc"):
        if ext not in (".docx", ".doc"):
            await update.message.reply_text("❌ Faqat .docx yoki .doc fayl yuboring!")
            return
        await convert_office_to_pdf(update, context, doc, ext, "word")

    # ── PDF MERGE ─────────────────────────────────────────────
    elif mode == "pdf_merge" or ext == ".pdf":
        lst = user_pdf_list.setdefault(user.id, [])
        if len(lst) >= 20:
            await update.message.reply_text("⚠️ Maksimal 20 ta PDF!")
            return
        file = await doc.get_file()
        data = bytes(await file.download_as_bytearray())
        lst.append((doc.file_name or f"file{len(lst)}.pdf", data))
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"✅ Birlashtirish ({len(lst)} ta)", callback_data="merge_now")],
            [InlineKeyboardButton("🗑 Bekor qilish", callback_data="cancel_all")],
        ])
        await update.message.reply_text(
            f"📎 {len(lst)} ta PDF qabul qilindi. Yana yuboring yoki birlashtiring:",
            reply_markup=kb
        )
        context.user_data["mode"] = "pdf_merge"

    else:
        await update.message.reply_text("❓ Noma'lum fayl. Avval xizmat tanlang.")


# ==================== OFFICE → PDF ====================
async def convert_office_to_pdf(update, context, doc, ext, ftype):
    msg = await update.message.reply_text("⏳ Konvertatsiya qilinmoqda...")
    try:
        file = await doc.get_file()
        raw  = bytes(await file.download_as_bytearray())

        with tempfile.TemporaryDirectory() as tmp:
            in_path  = os.path.join(tmp, f"input{ext}")
            out_path = os.path.join(tmp, "input.pdf")
            with open(in_path, "wb") as f:
                f.write(raw)

            subprocess.run(
                ["libreoffice", "--headless", "--convert-to", "pdf",
                 "--outdir", tmp, in_path],
                check=True, timeout=120, capture_output=True
            )

            if not os.path.exists(out_path):
                raise FileNotFoundError("PDF yaratilmadi")

            with open(out_path, "rb") as f:
                pdf_bytes = f.read()

        context.user_data[f"{ftype}_pdf"]      = pdf_bytes
        context.user_data[f"{ftype}_pdf_name"] = doc.file_name.replace(ext, ".pdf")

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yuborish",            callback_data=f"send_{ftype}_pdf")],
            [InlineKeyboardButton("✏️ Nomini o'zgartirish", callback_data=f"rename_{ftype}_pdf")],
        ])
        await msg.edit_text(
            f"✅ Tayyor! Fayl: <b>{context.user_data[f'{ftype}_pdf_name']}</b>\nNima qilasiz?",
            reply_markup=kb, parse_mode="HTML"
        )
        _inc_requests()

    except subprocess.CalledProcessError:
        await msg.edit_text("❌ LibreOffice xatosi. Fayl shikastlangan bo'lishi mumkin.")
    except Exception as e:
        await msg.edit_text(f"❌ Xatolik: {str(e)[:200]}")
    finally:
        context.user_data.pop("mode", None)


def _inc_requests():
    db = load_db()
    db["total_requests"] = db.get("total_requests", 0) + 1
    save_db(db)


# ==================== JPG → PDF ====================
async def generate_pdf_from_images(update, context, filename="rasmlar"):
    user   = update.effective_user
    msg_fn = update.message or (update.callback_query.message if update.callback_query else None)
    images = user_images.get(user.id, [])

    if not images:
        await msg_fn.reply_text("❌ Rasm topilmadi!")
        return

    wait = await msg_fn.reply_text(f"⏳ {len(images)} ta rasmdan PDF yaratilmoqda...")
    try:
        pdf_imgs = []
        for b in images:
            img = Image.open(BytesIO(b))
            if img.mode != "RGB":
                img = img.convert("RGB")
            out = BytesIO()
            img.save(out, format="JPEG", quality=95)
            pdf_imgs.append(out.getvalue())

        pdf_output = BytesIO(img2pdf.convert(pdf_imgs))
        safe = filename if filename.endswith(".pdf") else f"{filename}.pdf"

        await msg_fn.reply_document(
            document=InputFile(pdf_output, filename=safe),
            caption=f"✅ {len(images)} ta rasmdan <b>{safe}</b> yaratildi!",
            parse_mode="HTML"
        )
        await wait.delete()

        db  = load_db()
        uid = str(user.id)
        db["total_pdfs"] = db.get("total_pdfs", 0) + 1
        if uid in db["users"]:
            db["users"][uid]["pdfs"] += 1
        save_db(db)

        user_images.pop(user.id, None)
        context.user_data.clear()
        await msg_fn.reply_text("Boshqa xizmat:", reply_markup=main_reply_keyboard())

    except Exception as e:
        logger.error(f"PDF xato: {e}")
        await wait.edit_text(f"❌ Xato: {str(e)[:200]}")


# ==================== PDF MERGE ====================
async def do_merge_pdfs(update, context):
    user    = update.effective_user
    msg_obj = update.callback_query.message if update.callback_query else update.message
    lst     = user_pdf_list.get(user.id, [])

    if len(lst) < 2:
        await msg_obj.reply_text("⚠️ Kamida 2 ta PDF kerak!")
        return

    wait = await msg_obj.reply_text(f"⏳ {len(lst)} ta PDF birlashtirilmoqda...")
    try:
        from pypdf import PdfWriter, PdfReader
        writer = PdfWriter()
        for name, data in lst:
            reader = PdfReader(BytesIO(data))
            for page in reader.pages:
                writer.add_page(page)
        out = BytesIO()
        writer.write(out)
        out.seek(0)

        await msg_obj.reply_document(
            document=InputFile(out, filename="merged.pdf"),
            caption=f"✅ {len(lst)} ta PDF birlashtirildi!"
        )
        await wait.delete()
        user_pdf_list.pop(user.id, None)
        context.user_data.clear()
        _inc_requests()
        await msg_obj.reply_text("Boshqa xizmat:", reply_markup=main_reply_keyboard())

    except ImportError:
        await wait.edit_text("❌ <code>pypdf</code> o'rnatilmagan. requirements.txt ga qo'shing.", parse_mode="HTML")
    except Exception as e:
        await wait.edit_text(f"❌ Xato: {str(e)[:200]}")


# ==================== TIMETABLE ====================
async def fetch_timetable(update: Update, context: ContextTypes.DEFAULT_TYPE, group: str):
    msg = await update.message.reply_text(
        f"⏳ <b>{group}</b> guruhi dars jadvali qidirilmoqda...", parse_mode="HTML"
    )
    try:
        screenshot = await capture_timetable(group)
        if screenshot and len(screenshot) > 2000:
            await update.message.reply_photo(
                photo=InputFile(BytesIO(screenshot), filename="timetable.png"),
                caption=f"📅 <b>{group}</b> — Dars jadvali\n🏫 TSUE", parse_mode="HTML"
            )
            await msg.delete()
        else:
            await msg.edit_text(
                f"❌ <b>{group}</b> guruhi topilmadi.\n"
                "To'g'ri format: <code>II-52/24</code>", parse_mode="HTML"
            )
    except Exception as e:
        await msg.edit_text(f"❌ Xatolik: {str(e)[:200]}")
    finally:
        context.user_data.clear()
        await update.message.reply_text("Boshqa xizmat:", reply_markup=main_reply_keyboard())


async def capture_timetable(group: str) -> bytes | None:
    script = f"""
import asyncio, sys
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=[
            "--no-sandbox","--disable-setuid-sandbox",
            "--disable-dev-shm-usage","--disable-gpu"
        ])
        page = await browser.new_page(viewport={{"width":1400,"height":900}})
        await page.goto("https://tsue.edupage.org/timetable/",
                        wait_until="networkidle", timeout=30000)

        # input topib guruh nomini yoz
        for sel in ["input.search-input","input[type='text']","#search",".searchField input"]:
            try:
                el = await page.wait_for_selector(sel, timeout=4000)
                await el.fill("{group}")
                await asyncio.sleep(1.5)
                # suggestion bor-yoqligini tekshir
                for item_sel in [".autocomplete-item",".suggestion","li.ui-menu-item","ul.ui-autocomplete li"]:
                    items = await page.query_selector_all(item_sel)
                    for item in items:
                        t = await item.inner_text()
                        if "{group}".lower().replace("-","") in t.lower().replace("-",""):
                            await item.click()
                            await asyncio.sleep(2.5)
                            break
                break
            except Exception:
                continue

        await asyncio.sleep(2)
        # jadval elementini qidirish
        for sel in [".printTimetable","#timetable",".timetable","table.table"]:
            el = await page.query_selector(sel)
            if el:
                shot = await el.screenshot(type="png")
                sys.stdout.buffer.write(shot)
                await browser.close()
                return
        # to'liq sahifa screenshot
        shot = await page.screenshot(full_page=False, type="png")
        sys.stdout.buffer.write(shot)
        await browser.close()

asyncio.run(main())
"""
    proc = await asyncio.create_subprocess_exec(
        "python3", "-c", script,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    except asyncio.TimeoutError:
        proc.kill()
        return None
    return stdout if proc.returncode == 0 else None


# ==================== /done ====================
async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    mode = context.user_data.get("mode", "")

    if mode == "jpg" and user.id in user_images and user_images[user.id]:
        await generate_pdf_from_images(update, context, "rasmlar")
    elif mode == "pdf_merge":
        await do_merge_pdfs(update, context)
    else:
        await update.message.reply_text("ℹ️ Hech qanday fayl topilmadi.")


# ==================== CALLBACKS ====================
async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    data = q.data
    user = q.from_user
    await q.answer()

    # A'zolik
    if data == "check_sub":
        if is_blocked(user.id):
            await q.edit_message_text("❌ Bloklangansiz."); return
        if await check_subscription(user.id, context):
            await q.edit_message_text(f"✅ Rahmat, {user.first_name}!")
            await context.bot.send_message(
                user.id,
                "Xizmatni tanlang 👇",
                reply_markup=main_reply_keyboard()
            )
        else:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Kanalga a'zo bo'lish", url=f"https://t.me/{CHANNEL_USERNAME[1:]}")],
                [InlineKeyboardButton("✅ A'zo bo'ldim", callback_data="check_sub")],
            ])
            await q.edit_message_text("❌ Hali a'zo bo'lmadingiz!", reply_markup=kb)
        return

    # JPG → PDF
    if data == "create_pdf":
        images = user_images.get(user.id, [])
        if not images:
            await q.edit_message_text("❌ Rasm topilmadi!"); return
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Standart nom", callback_data="default_pdf_name")],
            [InlineKeyboardButton("✏️ Nom o'zgartirish", callback_data="rename_jpg_pdf")],
        ])
        await q.edit_message_text(
            f"📄 {len(images)} ta rasm tayyor. Fayl nomini tanlang:", reply_markup=kb
        )
        return

    if data == "default_pdf_name":
        await q.edit_message_text("⏳ PDF yaratilmoqda...")
        await generate_pdf_from_images(update, context, "rasmlar")
        return

    if data == "rename_jpg_pdf":
        context.user_data["mode"] = "waiting_pdf_name"
        await q.edit_message_text("✏️ Yangi fayl nomini yozing:")
        return

    if data == "cancel_all":
        user_images.pop(user.id, None)
        user_pdf_list.pop(user.id, None)
        context.user_data.clear()
        await q.edit_message_text("🗑 Bekor qilindi.")
        await context.bot.send_message(user.id, "Xizmatni tanlang:", reply_markup=main_reply_keyboard())
        return

    # PDF merge
    if data == "merge_now":
        await do_merge_pdfs(update, context)
        return

    # PPTX PDF yuborish
    if data == "send_pptx_pdf":
        pdf_bytes = context.user_data.pop("pptx_pdf", None)
        filename  = context.user_data.pop("pptx_pdf_name", "fayl.pdf")
        if pdf_bytes:
            await q.message.reply_document(
                InputFile(BytesIO(pdf_bytes), filename=filename),
                caption=f"✅ <b>{filename}</b> tayyor!", parse_mode="HTML"
            )
        await q.message.reply_text("Boshqa xizmat:", reply_markup=main_reply_keyboard())
        return

    if data == "rename_pptx_pdf":
        context.user_data["mode"] = "waiting_pptx_name"
        await q.edit_message_text("✏️ Yangi fayl nomini yozing:")
        return

    # Word PDF yuborish
    if data == "send_word_pdf":
        pdf_bytes = context.user_data.pop("word_pdf", None)
        filename  = context.user_data.pop("word_pdf_name", "fayl.pdf")
        if pdf_bytes:
            await q.message.reply_document(
                InputFile(BytesIO(pdf_bytes), filename=filename),
                caption=f"✅ <b>{filename}</b> tayyor!", parse_mode="HTML"
            )
        await q.message.reply_text("Boshqa xizmat:", reply_markup=main_reply_keyboard())
        return

    if data == "rename_word_pdf":
        context.user_data["mode"] = "waiting_word_name"
        await q.edit_message_text("✏️ Yangi fayl nomini yozing:")
        return

    # ── ADMIN callbacks ────────────────────────────────────────
    if user.id != ADMIN_ID:
        return

    if data == "admin_stats":
        await admin_show_stats_cb(q)
        return

    if data == "admin_users":
        await admin_show_users_cb(q, context, page=0)
        return

    if data == "admin_broadcast":
        context.user_data["mode"] = "broadcast"
        await q.edit_message_text("📣 Broadcast xabarini yozing:")
        return

    if data == "admin_back":
        db = load_db()
        blocked = sum(1 for u in db["users"].values() if u.get("blocked"))
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 Foydalanuvchilar", callback_data="admin_users")],
            [InlineKeyboardButton("📊 Statistika",       callback_data="admin_stats")],
            [InlineKeyboardButton("📣 Broadcast",        callback_data="admin_broadcast")],
        ])
        await q.edit_message_text(
            f"🛠 <b>Admin Panel</b>\n\n"
            f"👥 Jami: <b>{len(db['users'])}</b>\n"
            f"📄 PDF: <b>{db.get('total_pdfs',0)}</b>\n"
            f"🚫 Bloklangan: <b>{blocked}</b>",
            reply_markup=kb, parse_mode="HTML"
        )
        return

    if data.startswith("block_"):
        uid = data.split("_", 1)[1]
        db  = load_db()
        if uid in db["users"]:
            db["users"][uid]["blocked"] = True
            save_db(db)
        await admin_show_users_cb(q, context, page=0)
        return

    if data.startswith("unblock_"):
        uid = data.split("_", 1)[1]
        db  = load_db()
        if uid in db["users"]:
            db["users"][uid]["blocked"] = False
            save_db(db)
        await admin_show_users_cb(q, context, page=0)
        return

    if data.startswith("users_page_"):
        page = int(data.split("_")[-1])
        await admin_show_users_cb(q, context, page=page)
        return

    if data.startswith("user_detail_"):
        uid = data.split("_", 2)[2]
        await admin_user_detail_cb(q, uid)
        return


# ==================== ADMIN HELPERS ====================
PAGE_SIZE = 8

async def admin_show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db      = load_db()
    blocked = sum(1 for u in db["users"].values() if u.get("blocked"))
    active  = len(db["users"]) - blocked
    await update.message.reply_text(
        "📊 <b>Statistika</b>\n\n"
        f"👥 Jami foydalanuvchilar: <b>{len(db['users'])}</b>\n"
        f"✅ Faol: <b>{active}</b>\n"
        f"🚫 Bloklangan: <b>{blocked}</b>\n"
        f"📄 Jami PDF: <b>{db.get('total_pdfs', 0)}</b>\n"
        f"🔄 Jami so'rovlar: <b>{db.get('total_requests', 0)}</b>",
        parse_mode="HTML",
        reply_markup=admin_reply_keyboard()
    )

async def admin_show_stats_cb(q):
    db      = load_db()
    blocked = sum(1 for u in db["users"].values() if u.get("blocked"))
    active  = len(db["users"]) - blocked
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="admin_back")]])
    await q.edit_message_text(
        "📊 <b>Statistika</b>\n\n"
        f"👥 Jami: <b>{len(db['users'])}</b>\n"
        f"✅ Faol: <b>{active}</b>\n"
        f"🚫 Bloklangan: <b>{blocked}</b>\n"
        f"📄 PDF: <b>{db.get('total_pdfs', 0)}</b>\n"
        f"🔄 So'rovlar: <b>{db.get('total_requests', 0)}</b>",
        reply_markup=kb, parse_mode="HTML"
    )


async def admin_show_users(update: Update, context: ContextTypes.DEFAULT_TYPE, page=0):
    db    = load_db()
    users = list(db["users"].values())
    total = len(users)
    start = page * PAGE_SIZE
    chunk = users[start: start + PAGE_SIZE]

    lines = [f"👥 <b>Foydalanuvchilar</b> ({total} ta) — {page+1}-sahifa\n"]
    buttons = []
    for u in chunk:
        icon  = "🚫" if u.get("blocked") else "✅"
        uname = f"@{u['username']}" if u["username"] else "—"
        lines.append(f"{icon} <b>{u['name']}</b> ({uname}) | PDF: {u.get('pdfs',0)} | {u.get('joined','')}")
        buttons.append([
            InlineKeyboardButton(
                f"{icon} {u['name'][:18]}",
                callback_data=f"user_detail_{u['id']}"
            )
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"users_page_{page-1}"))
    if start + PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"users_page_{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="admin_back")])

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def admin_show_users_cb(q, context, page=0):
    db    = load_db()
    users = list(db["users"].values())
    total = len(users)
    start = page * PAGE_SIZE
    chunk = users[start: start + PAGE_SIZE]

    lines   = [f"👥 <b>Foydalanuvchilar</b> ({total} ta) — {page+1}-sahifa\n"]
    buttons = []
    for u in chunk:
        icon  = "🚫" if u.get("blocked") else "✅"
        uname = f"@{u['username']}" if u["username"] else "—"
        lines.append(f"{icon} <b>{u['name']}</b> ({uname}) | PDF: {u.get('pdfs',0)}")
        buttons.append([
            InlineKeyboardButton(
                f"{icon} {u['name'][:18]}",
                callback_data=f"user_detail_{u['id']}"
            )
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"users_page_{page-1}"))
    if start + PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"users_page_{page+1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="admin_back")])

    await q.edit_message_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def admin_user_detail_cb(q, uid: str):
    db   = load_db()
    u    = db["users"].get(uid)
    if not u:
        await q.edit_message_text("❌ Foydalanuvchi topilmadi."); return

    icon  = "🚫 Bloklangan" if u.get("blocked") else "✅ Faol"
    uname = f"@{u['username']}" if u["username"] else "—"
    text  = (
        f"👤 <b>{u['name']}</b>\n"
        f"🆔 <code>{u['id']}</code>\n"
        f"📱 Username: {uname}\n"
        f"📅 Qo'shildi: {u.get('joined','—')}\n"
        f"⏱ Oxirgi faollik: {u.get('last_active','—')}\n"
        f"📄 PDF yaratdi: {u.get('pdfs', 0)}\n"
        f"Status: {icon}"
    )

    if u.get("blocked"):
        action_btn = InlineKeyboardButton("🔓 Blokni ochish", callback_data=f"unblock_{uid}")
    else:
        action_btn = InlineKeyboardButton("🚫 Bloklash",      callback_data=f"block_{uid}")

    kb = InlineKeyboardMarkup([
        [action_btn],
        [InlineKeyboardButton("🔙 Orqaga", callback_data="admin_users")],
    ])
    await q.edit_message_text(text, reply_markup=kb, parse_mode="HTML")


async def admin_search_user(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str):
    db    = load_db()
    found = []
    q_low = query.lower().lstrip("@")
    for uid, u in db["users"].items():
        if (q_low in str(u["id"])) or (q_low in u.get("username","").lower()) or (q_low in u["name"].lower()):
            found.append(u)

    if not found:
        await update.message.reply_text(
            f"❌ '{query}' topilmadi.", reply_markup=admin_reply_keyboard()
        )
        context.user_data.clear()
        return

    lines   = [f"🔍 <b>Natijalar ({len(found)} ta):</b>\n"]
    buttons = []
    for u in found[:10]:
        icon  = "🚫" if u.get("blocked") else "✅"
        uname = f"@{u['username']}" if u["username"] else "—"
        lines.append(f"{icon} <b>{u['name']}</b> ({uname}) | <code>{u['id']}</code>")
        buttons.append([
            InlineKeyboardButton(
                f"{icon} {u['name'][:20]}",
                callback_data=f"user_detail_{u['id']}"
            )
        ])
    buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="admin_back")])

    await update.message.reply_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    context.user_data.clear()


# ==================== MAIN ====================
def main():
    while True:
        try:
            logger.info("Bot ishga tushdi...")
            app = Application.builder().token(BOT_TOKEN).build()

            app.add_handler(CommandHandler("start", start))
            app.add_handler(CommandHandler("done",  done))
            app.add_handler(CommandHandler("admin", lambda u, c: show_admin_panel_cmd(u, c)))

            app.add_handler(CallbackQueryHandler(handle_callbacks))
            app.add_handler(MessageHandler(filters.PHOTO, receive_image))
            app.add_handler(MessageHandler(filters.Document.ALL, receive_document))
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

            app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

        except Exception as e:
            logger.error(f"Bot xato: {e}")
            logger.info("5 soniyadan keyin qayta ishga tushadi...")
            time.sleep(5)


async def show_admin_panel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Siz admin emassiz!")
        return
    db      = load_db()
    blocked = sum(1 for u in db["users"].values() if u.get("blocked"))
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Foydalanuvchilar", callback_data="admin_users")],
        [InlineKeyboardButton("📊 Statistika",       callback_data="admin_stats")],
        [InlineKeyboardButton("📣 Broadcast",        callback_data="admin_broadcast")],
    ])
    await update.message.reply_text(
        f"🛠 <b>Admin Panel</b>\n\n"
        f"👥 Jami: <b>{len(db['users'])}</b>\n"
        f"📄 PDF: <b>{db.get('total_pdfs', 0)}</b>\n"
        f"🚫 Bloklangan: <b>{blocked}</b>",
        reply_markup=kb, parse_mode="HTML"
    )


if __name__ == "__main__":
    main()
