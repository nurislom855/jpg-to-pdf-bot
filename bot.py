import os
import logging
import asyncio
import tempfile
import subprocess
from pathlib import Path
from io import BytesIO

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    InputFile, ChatMember
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes
)
from PIL import Image
import img2pdf
import redis
from upstash_redis import Redis

# ─────────────────────────── CONFIG ───────────────────────────
BOT_TOKEN   = os.getenv("BOT_TOKEN", "8778116378:AAHvHV0ce7WlKItOfAGCOWL44I3AqRZHbBw")
CHANNEL_ID  = "@jpg_to_pdf_otkaz"
ADMIN_ID    = 7406325328
REDIS_URL   = os.getenv("REDIS_URL", "https://mutual-satyr-95515.upstash.io")
REDIS_TOKEN = os.getenv("REDIS_TOKEN", "gQAAAAAAAXUbAAIgcDE2ZWY1NjFlZWM0NTU0ODQxYjI1NDBlM2VlNWU3OTgzNA")

# ─────────────────────────── STATES ───────────────────────────
WAITING_IMAGES   = 1
WAITING_PPTX     = 2
WAITING_RENAME   = 3
WAITING_FEEDBACK = 4
WAITING_WORD     = 5
WAITING_PDF_MERGE = 6
WAITING_TIMETABLE = 7

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─────────────────────────── REDIS ────────────────────────────
rdb = Redis(url=REDIS_URL, token=REDIS_TOKEN)

def save_user(user_id: int, username: str, full_name: str):
    key = f"user:{user_id}"
    if not rdb.exists(key):
        rdb.hset(key, mapping={
            "username": username or "",
            "full_name": full_name or "",
            "joined": str(asyncio.get_event_loop().time()),
        })
        rdb.incr("stats:total_users")
    rdb.incr("stats:total_requests")

def get_stats():
    total_users    = rdb.get("stats:total_users")    or 0
    total_requests = rdb.get("stats:total_requests") or 0
    return int(total_users), int(total_requests)

def is_blocked(user_id: int) -> bool:
    return bool(rdb.sismember("blocked_users", str(user_id)))

def block_user(user_id: int):   rdb.sadd("blocked_users", str(user_id))
def unblock_user(user_id: int): rdb.srem("blocked_users", str(user_id))

def get_all_users():
    keys = rdb.keys("user:*")
    users = []
    for k in keys:
        uid = k.split(":")[1]
        data = rdb.hgetall(k)
        users.append((uid, data))
    return users

# ─────────────────────────── CHANNEL CHECK ────────────────────
async def is_member(bot, user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(CHANNEL_ID, user_id)
        return m.status in [
            ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER
        ]
    except Exception:
        return False

async def require_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    if await is_member(context.bot, user_id):
        return True
    kb = [[InlineKeyboardButton("📢 Kanalga a'zo bo'lish", url=f"https://t.me/{CHANNEL_ID[1:]}"),
           InlineKeyboardButton("✅ Tekshirish", callback_data="check_membership")]]
    await update.message.reply_text(
        "⚠️ Botdan foydalanish uchun avval kanalga a'zo bo'ling!\n\n"
        f"📢 Kanal: {CHANNEL_ID}",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return False

# ─────────────────────────── /start ───────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_blocked(user.id):
        await update.message.reply_text("❌ Siz bloklangansiz.")
        return

    save_user(user.id, user.username, user.full_name)

    if not await require_membership(update, context):
        return

    kb = [
        [InlineKeyboardButton("📸 JPG → PDF",        callback_data="jpg_pdf"),
         InlineKeyboardButton("📄 Word → PDF",        callback_data="word_pdf")],
        [InlineKeyboardButton("📊 PPTX → PDF",        callback_data="pptx_pdf"),
         InlineKeyboardButton("🔗 PDF Birlashtirish", callback_data="pdf_merge")],
        [InlineKeyboardButton("✏️ Fayl nomini o'zgartirish", callback_data="rename")],
        [InlineKeyboardButton("📅 Dars jadvali",      callback_data="timetable")],
        [InlineKeyboardButton("💬 Admin bilan bog'lanish", callback_data="contact_admin")],
    ]
    await update.message.reply_text(
        f"👋 Salom, {user.first_name}!\n\n"
        "🤖 Men sizga quyidagi xizmatlarni taklif etaman:\n\n"
        "📸 <b>JPG → PDF</b> — rasmlarni PDFga aylantirish\n"
        "📄 <b>Word → PDF</b> — .docx/.doc fayllarni PDFga\n"
        "📊 <b>PPTX → PDF</b> — prezentatsiyani PDFga\n"
        "🔗 <b>PDF Birlashtirish</b> — bir nechta PDFni birlashtirib yuborish\n"
        "✏️ <b>Fayl nomini o'zgartirish</b>\n"
        "📅 <b>Dars jadvali</b> — TSUE dars jadvalini ko'rish\n\n"
        "Kerakli bo'limni tanlang 👇",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# ─────────────────────────── CALLBACK QUERY ───────────────────
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    data = q.data
    await q.answer()

    # membership check
    if data == "check_membership":
        if await is_member(context.bot, q.from_user.id):
            await q.message.reply_text("✅ Rahmat! Endi botdan foydalanishingiz mumkin.")
            await start_from_callback(q, context)
        else:
            await q.answer("❌ Hali a'zo bo'lmadingiz!", show_alert=True)
        return

    if data == "jpg_pdf":
        context.user_data["mode"]   = "jpg_pdf"
        context.user_data["images"] = []
        await q.message.reply_text(
            "📸 Rasmlarni yuboring (1-100 ta JPG/PNG).\n"
            "Hammasi yuborib bo'lgach /done bosing."
        )

    elif data == "word_pdf":
        context.user_data["mode"] = "word_pdf"
        await q.message.reply_text(
            "📄 Word faylini yuboring (.docx yoki .doc format)."
        )

    elif data == "pptx_pdf":
        context.user_data["mode"] = "pptx_pdf"
        await q.message.reply_text(
            "📊 PPTX faylini yuboring."
        )

    elif data == "pdf_merge":
        context.user_data["mode"]     = "pdf_merge"
        context.user_data["pdf_list"] = []
        await q.message.reply_text(
            "🔗 PDFlarni ketma-ket yuboring.\n"
            "Hammasi yuborib bo'lgach /done bosing.\n"
            "⚠️ Minimum 2 ta, maximum 20 ta PDF."
        )

    elif data == "rename":
        context.user_data["mode"] = "rename_waiting_file"
        await q.message.reply_text(
            "✏️ Nomini o'zgartirmoqchi bo'lgan faylni yuboring."
        )

    elif data == "timetable":
        context.user_data["mode"] = "timetable"
        await q.message.reply_text(
            "📅 Guruh nomini kiriting.\n"
            "Masalan: <b>II-52/24</b> yoki <b>IB-11/23</b>",
            parse_mode="HTML"
        )

    elif data == "contact_admin":
        context.user_data["mode"] = "feedback"
        await q.message.reply_text(
            "💬 Xabaringizni yozing, admin @nurislomdev ga yetkazamiz."
        )

    elif data == "admin_panel":
        if q.from_user.id == ADMIN_ID:
            await show_admin_panel(q.message, context)

    elif data == "admin_stats":
        total_u, total_r = get_stats()
        await q.message.reply_text(
            f"📊 <b>Statistika</b>\n\n"
            f"👤 Foydalanuvchilar: <b>{total_u}</b>\n"
            f"🔄 So'rovlar: <b>{total_r}</b>",
            parse_mode="HTML"
        )

    elif data == "admin_broadcast":
        context.user_data["mode"] = "broadcast"
        await q.message.reply_text("📣 Broadcast xabarini yozing:")

    elif data == "admin_users":
        users = get_all_users()
        text  = f"👥 <b>Foydalanuvchilar ({len(users)})</b>\n\n"
        for uid, d in users[:30]:
            uname = d.get("username", "")
            fname = d.get("full_name", "")
            text += f"• {fname} (@{uname}) — <code>{uid}</code>\n"
        await q.message.reply_text(text[:4000], parse_mode="HTML")

# ─────────────────────────── FILE HANDLER ─────────────────────
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_blocked(user.id):
        return

    if not await require_membership(update, context):
        return

    mode = context.user_data.get("mode", "")
    doc  = update.message.document

    # ── WORD → PDF ─────────────────────────────────────────────
    if mode == "word_pdf":
        ext = Path(doc.file_name or "file.docx").suffix.lower()
        if ext not in [".docx", ".doc"]:
            await update.message.reply_text("❌ Faqat .docx yoki .doc fayl yuboring!")
            return
        await convert_office_to_pdf(update, context, doc, ext, "word")

    # ── PPTX → PDF ─────────────────────────────────────────────
    elif mode == "pptx_pdf":
        ext = Path(doc.file_name or "file.pptx").suffix.lower()
        if ext not in [".pptx", ".ppt"]:
            await update.message.reply_text("❌ Faqat .pptx yoki .ppt fayl yuboring!")
            return
        await convert_office_to_pdf(update, context, doc, ext, "pptx")

    # ── PDF MERGE collect ──────────────────────────────────────
    elif mode == "pdf_merge":
        ext = Path(doc.file_name or "file.pdf").suffix.lower()
        if ext != ".pdf":
            await update.message.reply_text("❌ Faqat PDF fayl yuboring!")
            return
        pdf_list = context.user_data.setdefault("pdf_list", [])
        if len(pdf_list) >= 20:
            await update.message.reply_text("⚠️ Maksimal 20 ta PDF!")
            return
        file = await doc.get_file()
        data = await file.download_as_bytearray()
        pdf_list.append((doc.file_name or f"file{len(pdf_list)}.pdf", bytes(data)))
        await update.message.reply_text(
            f"✅ PDF qabul qilindi ({len(pdf_list)} ta). "
            "Davom etish uchun yana yuboring yoki /done bosing."
        )

    # ── RENAME - waiting file ──────────────────────────────────
    elif mode == "rename_waiting_file":
        file = await doc.get_file()
        data = await file.download_as_bytearray()
        context.user_data["rename_data"] = bytes(data)
        context.user_data["rename_ext"]  = Path(doc.file_name or "file").suffix
        context.user_data["mode"]        = "rename_waiting_name"
        await update.message.reply_text(
            "✏️ Yangi fayl nomini kiriting (kengaytimasiz):"
        )


async def convert_office_to_pdf(update, context, doc, ext, ftype):
    msg = await update.message.reply_text("⏳ Konvertatsiya qilinmoqda...")
    with tempfile.TemporaryDirectory() as tmpdir:
        in_path  = os.path.join(tmpdir, f"input{ext}")
        out_path = os.path.join(tmpdir, "input.pdf")

        file = await doc.get_file()
        await file.download_to_drive(in_path)

        try:
            subprocess.run(
                ["libreoffice", "--headless", "--convert-to", "pdf",
                 "--outdir", tmpdir, in_path],
                check=True, timeout=120,
                capture_output=True
            )
            with open(out_path, "rb") as f:
                await update.message.reply_document(
                    document=InputFile(f, filename="converted.pdf"),
                    caption="✅ PDF tayyor!"
                )
            await msg.delete()
            rdb.incr("stats:total_requests")
        except Exception as e:
            await msg.edit_text(f"❌ Xatolik: {e}")
        finally:
            context.user_data.pop("mode", None)

# ─────────────────────────── PHOTO HANDLER ────────────────────
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_blocked(user.id):
        return
    if not await require_membership(update, context):
        return

    mode = context.user_data.get("mode", "")
    if mode != "jpg_pdf":
        # auto-detect: switch to jpg_pdf mode
        context.user_data["mode"]   = "jpg_pdf"
        context.user_data["images"] = []

    photo = update.message.photo[-1]
    file  = await photo.get_file()
    data  = await file.download_as_bytearray()
    context.user_data.setdefault("images", []).append(bytes(data))

    count = len(context.user_data["images"])
    if count == 1:
        await update.message.reply_text(
            "✅ 1 ta rasm qabul qilindi. Ko'proq yuboring yoki /done bosing."
        )
    else:
        await update.message.reply_text(
            f"✅ {count} ta rasm. /done bosing yoki davom eting."
        )

# ─────────────────────────── TEXT HANDLER ─────────────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_blocked(user.id):
        return

    mode = context.user_data.get("mode", "")
    text = update.message.text.strip()

    # ── Timetable query ────────────────────────────────────────
    if mode == "timetable":
        await fetch_timetable(update, context, text)
        return

    # ── Rename ─────────────────────────────────────────────────
    if mode == "rename_waiting_name":
        data = context.user_data.get("rename_data")
        ext  = context.user_data.get("rename_ext", "")
        if data:
            new_name = text + ext
            await update.message.reply_document(
                document=InputFile(BytesIO(data), filename=new_name),
                caption=f"✅ Yangi nom: <b>{new_name}</b>",
                parse_mode="HTML"
            )
        context.user_data.pop("mode", None)
        return

    # ── Feedback ───────────────────────────────────────────────
    if mode == "feedback":
        uname = f"@{user.username}" if user.username else user.full_name
        await context.bot.send_message(
            ADMIN_ID,
            f"💬 <b>Yangi xabar</b>\n"
            f"👤 {uname} (<code>{user.id}</code>)\n\n"
            f"{text}",
            parse_mode="HTML"
        )
        await update.message.reply_text("✅ Xabaringiz adminga yuborildi!")
        context.user_data.pop("mode", None)
        return

    # ── Broadcast (admin only) ─────────────────────────────────
    if mode == "broadcast" and user.id == ADMIN_ID:
        users = get_all_users()
        sent = 0
        for uid, _ in users:
            try:
                await context.bot.send_message(int(uid), text)
                sent += 1
            except Exception:
                pass
        await update.message.reply_text(f"📣 {sent} ta foydalanuvchiga yuborildi.")
        context.user_data.pop("mode", None)
        return

    # ── Admin commands ─────────────────────────────────────────
    if text == "/admin" and user.id == ADMIN_ID:
        await show_admin_panel(update.message, context)
        return

    if text.startswith("/block ") and user.id == ADMIN_ID:
        uid = text.split()[1]
        block_user(int(uid))
        await update.message.reply_text(f"🚫 {uid} bloklandi.")
        return

    if text.startswith("/unblock ") and user.id == ADMIN_ID:
        uid = text.split()[1]
        unblock_user(int(uid))
        await update.message.reply_text(f"✅ {uid} blokdan chiqarildi.")
        return

    # ── Default: show menu ─────────────────────────────────────
    await start(update, context)


# ─────────────────────────── /done ────────────────────────────
async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = context.user_data.get("mode", "")

    # ── JPG → PDF ──────────────────────────────────────────────
    if mode == "jpg_pdf":
        images = context.user_data.get("images", [])
        if not images:
            await update.message.reply_text("❌ Hech qanday rasm yuborilmadi.")
            return
        msg = await update.message.reply_text(f"⏳ {len(images)} ta rasmdan PDF yaratilmoqda...")
        try:
            pdf_bytes = img2pdf.convert(images)
            await update.message.reply_document(
                document=InputFile(BytesIO(pdf_bytes), filename="images.pdf"),
                caption=f"✅ {len(images)} ta rasmdan PDF tayyor!"
            )
            await msg.delete()
            rdb.incr("stats:total_requests")
        except Exception as e:
            await msg.edit_text(f"❌ Xatolik: {e}")
        finally:
            context.user_data.pop("images", None)
            context.user_data.pop("mode", None)

    # ── PDF Merge ──────────────────────────────────────────────
    elif mode == "pdf_merge":
        pdf_list = context.user_data.get("pdf_list", [])
        if len(pdf_list) < 2:
            await update.message.reply_text("⚠️ Birlashtirish uchun kamida 2 ta PDF kerak!")
            return
        msg = await update.message.reply_text(f"⏳ {len(pdf_list)} ta PDF birlashtirilmoqda...")
        try:
            from pypdf import PdfWriter
            writer = PdfWriter()
            for name, data in pdf_list:
                reader_io = BytesIO(data)
                from pypdf import PdfReader
                reader = PdfReader(reader_io)
                for page in reader.pages:
                    writer.add_page(page)
            out_io = BytesIO()
            writer.write(out_io)
            out_io.seek(0)
            await update.message.reply_document(
                document=InputFile(out_io, filename="merged.pdf"),
                caption=f"✅ {len(pdf_list)} ta PDF birlashtirildi!"
            )
            await msg.delete()
            rdb.incr("stats:total_requests")
        except ImportError:
            await msg.edit_text("❌ pypdf kutubxonasi o'rnatilmagan. requirements.txt ga qo'shing.")
        except Exception as e:
            await msg.edit_text(f"❌ Xatolik: {e}")
        finally:
            context.user_data.pop("pdf_list", None)
            context.user_data.pop("mode", None)
    else:
        await update.message.reply_text("ℹ️ Hech qanday fayl yuborilmadi.")


# ─────────────────────────── TIMETABLE ────────────────────────
async def fetch_timetable(update: Update, context: ContextTypes.DEFAULT_TYPE, group: str):
    msg = await update.message.reply_text(
        f"⏳ <b>{group}</b> guruhi dars jadvali qidirilmoqda...",
        parse_mode="HTML"
    )
    try:
        screenshot = await capture_timetable_screenshot(group)
        if screenshot:
            await update.message.reply_photo(
                photo=InputFile(BytesIO(screenshot), filename="timetable.png"),
                caption=f"📅 <b>{group}</b> — Dars jadvali\n🏫 TSUE",
                parse_mode="HTML"
            )
            await msg.delete()
        else:
            await msg.edit_text(
                f"❌ <b>{group}</b> guruhi topilmadi.\n"
                "Guruh nomini to'g'ri kiriting. Masalan: <code>II-52/24</code>",
                parse_mode="HTML"
            )
    except Exception as e:
        await msg.edit_text(f"❌ Xatolik: {str(e)[:200]}")
    finally:
        context.user_data.pop("mode", None)


async def capture_timetable_screenshot(group: str) -> bytes | None:
    """Playwright orqali TSUE timetable saytidan guruh jadvalini screenshot qilish."""
    script = f"""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            args=["--no-sandbox", "--disable-setuid-sandbox",
                  "--disable-dev-shm-usage", "--disable-gpu"]
        )
        page = await browser.new_page(viewport={{"width": 1400, "height": 900}})
        await page.goto("https://tsue.edupage.org/timetable/", wait_until="networkidle", timeout=30000)

        # Guruh qidirish inputini top
        await page.wait_for_selector("input[type='text'], input.search, #search", timeout=10000)

        # Qidiruv
        inputs = await page.query_selector_all("input")
        for inp in inputs:
            try:
                await inp.fill("{group}")
                await asyncio.sleep(1)
                # Suggestion listdan tanlash
                suggestions = await page.query_selector_all(".autocomplete-item, li.item, .suggestion")
                for s in suggestions:
                    t = await s.inner_text()
                    if "{group}".lower() in t.lower():
                        await s.click()
                        await asyncio.sleep(2)
                        break
                break
            except Exception:
                continue

        await asyncio.sleep(2)

        # Jadval elementini topish va screenshot
        timetable = await page.query_selector(".timetable, #timetable, .printTimetable, table")
        if timetable:
            screenshot = await timetable.screenshot()
        else:
            screenshot = await page.screenshot(full_page=False)

        await browser.close()

        import sys
        sys.stdout.buffer.write(screenshot)

asyncio.run(main())
"""
    proc = await asyncio.create_subprocess_exec(
        "python3", "-c", script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

    if proc.returncode == 0 and stdout and len(stdout) > 1000:
        return stdout
    return None


# ─────────────────────────── ADMIN PANEL ──────────────────────
async def show_admin_panel(message, context):
    total_u, total_r = get_stats()
    kb = [
        [InlineKeyboardButton("📊 Statistika",     callback_data="admin_stats"),
         InlineKeyboardButton("👥 Foydalanuvchilar", callback_data="admin_users")],
        [InlineKeyboardButton("📣 Broadcast",      callback_data="admin_broadcast")],
    ]
    await message.reply_text(
        f"🔧 <b>Admin Panel</b>\n\n"
        f"👤 Foydalanuvchilar: <b>{total_u}</b>\n"
        f"🔄 So'rovlar: <b>{total_r}</b>\n\n"
        "Amalni tanlang:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def start_from_callback(query, context):
    kb = [
        [InlineKeyboardButton("📸 JPG → PDF",        callback_data="jpg_pdf"),
         InlineKeyboardButton("📄 Word → PDF",        callback_data="word_pdf")],
        [InlineKeyboardButton("📊 PPTX → PDF",        callback_data="pptx_pdf"),
         InlineKeyboardButton("🔗 PDF Birlashtirish", callback_data="pdf_merge")],
        [InlineKeyboardButton("✏️ Fayl nomini o'zgartirish", callback_data="rename")],
        [InlineKeyboardButton("📅 Dars jadvali",      callback_data="timetable")],
        [InlineKeyboardButton("💬 Admin bilan bog'lanish", callback_data="contact_admin")],
    ]
    await query.message.reply_text(
        "✅ A'zolik tasdiqlandi! Xizmatni tanlang:",
        reply_markup=InlineKeyboardMarkup(kb)
    )


# ─────────────────────────── MAIN ─────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("done",  done))
    app.add_handler(CommandHandler("admin", lambda u, c: show_admin_panel(u.message, c)
                                   if u.effective_user.id == ADMIN_ID else None))

    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.PHOTO,    handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot ishga tushdi...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
