import os, logging, json, time, subprocess, tempfile, asyncio, re, urllib.request, urllib.parse
from io import BytesIO
from datetime import datetime
from pathlib import Path
from PIL import Image
import img2pdf
from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ChatMember, InputFile, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo)
from telegram.ext import (Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters)

# ═══════════════════════════════════════════════════════════════
#  SOZLAMALAR
# ═══════════════════════════════════════════════════════════════
BOT_TOKEN        = os.environ.get("BOT_TOKEN",         "8778116378:AAHvHV0ce7WlKItOfAGCOWL44I3AqRZHbBw")
CHANNEL_USERNAME = "@nurislomai"
ADMIN_ID         = 7406325328
REDIS_URL        = os.environ.get("REDIS_URL",         "https://mutual-satyr-95515.upstash.io")
REDIS_TOKEN      = os.environ.get("REDIS_TOKEN",       "gQAAAAAAAXUbAAIgcDE2ZWY1NjFlZWM0NTU0ODQxYjI1NDBlM2VlNWU3OTgzNA")
ANTHROPIC_KEY    = os.environ.get("ANTHROPIC_API_KEY", "")

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

user_images:   dict = {}   # {user_id: [bytes, ...]}
user_pdf_list: dict = {}   # {user_id: [(name, bytes), ...]}

# ═══════════════════════════════════════════════════════════════
#  KLAVIATURA
# ═══════════════════════════════════════════════════════════════
def kb_main():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📸 JPG → PDF"),            KeyboardButton("📄 Word → PDF")],
        [KeyboardButton("📊 PPTX → PDF"),           KeyboardButton("🔗 PDF Birlashtirish")],
        [KeyboardButton("✍️ Referat yozish"),       KeyboardButton("🎮 O'yin")],
        [KeyboardButton("👨‍💻 Dasturchi haqida"),   KeyboardButton("💬 Admin bilan bog'lanish")],
    ], resize_keyboard=True, input_field_placeholder="Xizmatni tanlang...")

def kb_admin():
    return ReplyKeyboardMarkup([
        [KeyboardButton("👥 Foydalanuvchilar"), KeyboardButton("📊 Statistika")],
        [KeyboardButton("📣 Broadcast"),        KeyboardButton("🔍 Foydalanuvchi qidirish")],
        [KeyboardButton("🔙 Asosiy menyu")],
    ], resize_keyboard=True, input_field_placeholder="Admin amali...")

# ═══════════════════════════════════════════════════════════════
#  REDIS
# ═══════════════════════════════════════════════════════════════
def rget(key):
    try:
        url = f"{REDIS_URL}/get/{urllib.parse.quote(key, safe='')}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {REDIS_TOKEN}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            res = json.loads(r.read()).get("result")
            return json.loads(res) if res else None
    except Exception as e:
        logger.error(f"Redis get: {e}"); return None

def rset(key, value):
    try:
        encoded = urllib.parse.quote(json.dumps(value, ensure_ascii=False), safe="")
        url = f"{REDIS_URL}/set/{urllib.parse.quote(key, safe='')}/{encoded}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {REDIS_TOKEN}"})
        with urllib.request.urlopen(req, timeout=10): pass
    except Exception as e:
        logger.error(f"Redis set: {e}")

def load_db():
    db = rget("bot_db")
    if not db: db = {"users": {}, "total_pdfs": 0, "total_requests": 0}
    return db

def save_db(db): rset("bot_db", db)

def register_user(user):
    db = load_db(); uid = str(user.id); now = datetime.now().strftime("%Y-%m-%d %H:%M")
    if uid not in db["users"]:
        db["users"][uid] = {"id": user.id, "name": user.full_name,
            "username": user.username or "", "joined": now,
            "last_active": now, "pdfs": 0, "blocked": False}
    else:
        db["users"][uid]["last_active"] = now
    save_db(db)

def is_blocked(uid): return load_db()["users"].get(str(uid), {}).get("blocked", False)
def inc_req():
    db = load_db(); db["total_requests"] = db.get("total_requests", 0) + 1; save_db(db)

# ═══════════════════════════════════════════════════════════════
#  A'ZOLIK TEKSHIRUVI
# ═══════════════════════════════════════════════════════════════
async def check_sub(bot, uid):
    try:
        m = await bot.get_chat_member(CHANNEL_USERNAME, uid)
        return m.status in (ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER)
    except: return False

async def require_sub(update, context):
    if update.effective_user.id == ADMIN_ID: return True
    if await check_sub(context.bot, update.effective_user.id): return True
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📢 Kanalga a'zo bo'lish", url=f"https://t.me/{CHANNEL_USERNAME[1:]}")],
        [InlineKeyboardButton("✅ A'zo bo'ldim", callback_data="check_sub")]])
    await update.message.reply_text("⚠️ Botdan foydalanish uchun kanalga a'zo bo'ling!", reply_markup=kb)
    return False

# ═══════════════════════════════════════════════════════════════
#  /start
# ═══════════════════════════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)
    if is_blocked(user.id):
        await update.message.reply_text("❌ Siz botdan bloklangansiz."); return
    if user.id == ADMIN_ID:
        db = load_db()
        await update.message.reply_text(
            f"🛠 <b>Admin Panel</b>\n👥 Foydalanuvchilar: <b>{len(db['users'])}</b>",
            parse_mode="HTML", reply_markup=kb_admin()); return
    if not await check_sub(context.bot, user.id):
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📢 Kanalga a'zo bo'lish", url=f"https://t.me/{CHANNEL_USERNAME[1:]}")],
            [InlineKeyboardButton("✅ A'zo bo'ldim", callback_data="check_sub")]])
        await update.message.reply_text(
            f"👋 Salom, <b>{user.first_name}</b>!\n\n⚠️ Botdan foydalanish uchun kanalga a'zo bo'ling:",
            parse_mode="HTML", reply_markup=kb); return
    await update.message.reply_text(
        f"👋 Xush kelibsiz, <b>{user.first_name}</b>!\n\nXizmatni tanlang 👇",
        parse_mode="HTML", reply_markup=kb_main())

# ═══════════════════════════════════════════════════════════════
#  TEXT HANDLER
# ═══════════════════════════════════════════════════════════════
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()
    mode = context.user_data.get("mode", "")
    if is_blocked(user.id): return

    # ── ADMIN TUGMALARI ────────────────────────────────────────
    if user.id == ADMIN_ID:
        if text == "👥 Foydalanuvchilar": await admin_show_users(update, context, 0); return
        if text == "📊 Statistika":       await admin_show_stats(update, context); return
        if text == "🔙 Asosiy menyu":
            context.user_data.clear()
            await update.message.reply_text("🛠 Admin panel:", reply_markup=kb_admin()); return
        if text == "📣 Broadcast":
            context.user_data["mode"] = "broadcast"
            await update.message.reply_text("📣 Xabarni yozing:"); return
        if text == "🔍 Foydalanuvchi qidirish":
            context.user_data["mode"] = "admin_search"
            await update.message.reply_text("🔍 ID yoki @username kiriting:"); return
        if text == "❌ Bekor qilish":
            context.user_data.clear()
            await update.message.reply_text("❌ Bekor qilindi.", reply_markup=kb_admin()); return
        if mode == "broadcast":    await do_broadcast(update, context, text); return
        if mode == "admin_search": await do_admin_search(update, context, text); return

    # ── FOYDALANUVCHI TUGMALARI ────────────────────────────────
    if text == "📸 JPG → PDF":
        if not await require_sub(update, context): return
        context.user_data["mode"] = "jpg"; user_images[user.id] = []
        await update.message.reply_text("📸 Rasmlarni yuboring (1–100).\nTayyor bo'lgach 👉 /done"); return

    if text == "📄 Word → PDF":
        if not await require_sub(update, context): return
        context.user_data["mode"] = "word"
        await update.message.reply_text("📄 .docx yoki .doc faylni yuboring."); return

    if text == "📊 PPTX → PDF":
        if not await require_sub(update, context): return
        context.user_data["mode"] = "pptx"
        await update.message.reply_text("📊 .pptx yoki .ppt faylni yuboring."); return

    if text == "🔗 PDF Birlashtirish":
        if not await require_sub(update, context): return
        context.user_data["mode"] = "pdf_merge"; user_pdf_list[user.id] = []
        await update.message.reply_text("🔗 PDFlarni yuboring (2–20).\nTayyor bo'lgach 👉 /done"); return

    if text == "✍️ Referat yozish":
        if not await require_sub(update, context): return
        context.user_data["mode"] = "referat"
        await update.message.reply_text(
            "✍️ <b>Referat yozish</b>\n\nMavzuni yozing:\n"
            "Masalan: <code>Amir Temur davlati va uning ahamiyati</code>",
            parse_mode="HTML"); return

    if text == "⬇️ Video/Rasm yuklab olish":
        if not await require_sub(update, context): return
        context.user_data["mode"] = "downloader"
        await update.message.reply_text(
            "⬇️ <b>Video/Rasm yuklab olish</b>\n\n"
            "Havola yuboring:\n\n"
            "▶️ YouTube: <code>https://youtube.com/watch?v=...</code>\n"
            "📸 Instagram: <code>https://instagram.com/p/...</code>\n"
            "🎬 Instagram Reel: <code>https://instagram.com/reel/...</code>\n"
            "🎵 TikTok: <code>https://tiktok.com/@.../video/...</code>",
            parse_mode="HTML"); return

    if text == "🎮 O'yin":
        if not await require_sub(update, context): return
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "🐍 Snake o'yinini ochish",
                web_app=WebAppInfo(url="https://nurislom855.github.io/jpg-to-pdf-bot/")
            )
        ]])
        await update.message.reply_text(
            "🎮 <b>Snake O'yini</b>\n\n"
            "Ilonni boshqar, ovqat ye, o'sib bor!\n"
            "Quyidagi tugmani bosing 👇",
            parse_mode="HTML", reply_markup=kb
        ); return

    if text == "👨‍💻 Dasturchi haqida":
        await about_dev(update, context); return

    if text == "💬 Admin bilan bog'lanish":
        context.user_data["mode"] = "feedback"
        await update.message.reply_text("💬 Xabaringizni yozing:"); return

    # ── MODE HANDLERS ──────────────────────────────────────────
    if mode == "referat":       await do_referat(update, context, text); return
    if mode == "downloader":    await do_download(update, context, text); return
    if mode == "feedback":
        uname = f"@{user.username}" if user.username else user.full_name
        await context.bot.send_message(ADMIN_ID,
            f"💬 <b>Yangi xabar</b>\n👤 {uname} (<code>{user.id}</code>)\n\n{text}", parse_mode="HTML")
        await update.message.reply_text("✅ Xabar yuborildi!")
        context.user_data.clear(); return
    if mode == "waiting_pdf_name":
        context.user_data["mode"] = ""; await make_pdf(update, context, text); return
    if mode == "waiting_pptx_name":
        context.user_data["mode"] = ""
        await send_pdf_result(update, context, "pptx", text); return
    if mode == "waiting_word_name":
        context.user_data["mode"] = ""
        await send_pdf_result(update, context, "word", text); return

    # Default
    kb = kb_admin() if user.id == ADMIN_ID else kb_main()
    await update.message.reply_text("👆 Tugmani tanlang.", reply_markup=kb)

# ═══════════════════════════════════════════════════════════════
#  RASM HANDLER
# ═══════════════════════════════════════════════════════════════
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_blocked(user.id): return
    if not await require_sub(update, context): return
    if context.user_data.get("mode") != "jpg":
        context.user_data["mode"] = "jpg"; user_images[user.id] = []
    data = bytes(await (await update.message.photo[-1].get_file()).download_as_bytearray())
    user_images.setdefault(user.id, []).append(data)
    count = len(user_images[user.id])
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📄 PDF yaratish", callback_data="create_pdf"),
        InlineKeyboardButton("🗑 Bekor",        callback_data="cancel_all")]])
    await update.message.reply_text(f"✅ {count} ta rasm. /done yoki:", reply_markup=kb)

# ═══════════════════════════════════════════════════════════════
#  FAYL HANDLER
# ═══════════════════════════════════════════════════════════════
async def handle_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_blocked(user.id): return
    if not await require_sub(update, context): return
    doc = update.message.document
    ext = Path(doc.file_name or "").suffix.lower()
    mode = context.user_data.get("mode", "")

    if ext in (".pptx", ".ppt") or mode == "pptx":
        if ext not in (".pptx", ".ppt"):
            await update.message.reply_text("❌ Faqat .pptx yoki .ppt yuboring!"); return
        await convert_to_pdf(update, context, doc, ext, "pptx")
    elif ext in (".docx", ".doc") or mode == "word":
        if ext not in (".docx", ".doc"):
            await update.message.reply_text("❌ Faqat .docx yoki .doc yuboring!"); return
        await convert_to_pdf(update, context, doc, ext, "word")
    elif ext == ".pdf" or mode == "pdf_merge":
        lst = user_pdf_list.setdefault(user.id, [])
        if len(lst) >= 20:
            await update.message.reply_text("⚠️ Maksimal 20 ta PDF!"); return
        data = bytes(await (await doc.get_file()).download_as_bytearray())
        lst.append((doc.file_name or f"file{len(lst)}.pdf", data))
        context.user_data["mode"] = "pdf_merge"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(f"✅ Birlashtirish ({len(lst)} ta)", callback_data="merge_now"),
            InlineKeyboardButton("🗑 Bekor", callback_data="cancel_all")]])
        await update.message.reply_text(f"📎 {len(lst)} ta PDF. Yana yuboring yoki birlashtiring:", reply_markup=kb)
    else:
        await update.message.reply_text("❓ Avval xizmat turini tanlang.", reply_markup=kb_main())

# ═══════════════════════════════════════════════════════════════
#  OFFICE → PDF
# ═══════════════════════════════════════════════════════════════
async def convert_to_pdf(update, context, doc, ext, ftype):
    msg = await update.message.reply_text("⏳ Konvertatsiya qilinmoqda...")
    try:
        raw = bytes(await (await doc.get_file()).download_as_bytearray())
        with tempfile.TemporaryDirectory() as tmp:
            in_p  = os.path.join(tmp, f"input{ext}")
            out_p = os.path.join(tmp, "input.pdf")
            with open(in_p, "wb") as f: f.write(raw)
            subprocess.run(["libreoffice", "--headless", "--convert-to", "pdf",
                            "--outdir", tmp, in_p], check=True, timeout=120, capture_output=True)
            if not os.path.exists(out_p): raise FileNotFoundError("PDF yaratilmadi")
            with open(out_p, "rb") as f: pdf_bytes = f.read()
        context.user_data[f"{ftype}_pdf"]      = pdf_bytes
        context.user_data[f"{ftype}_pdf_name"] = doc.file_name.replace(ext, ".pdf")
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Yuborish",             callback_data=f"send_{ftype}"),
            InlineKeyboardButton("✏️ Nomini o'zgartirish", callback_data=f"rename_{ftype}")]])
        await msg.edit_text(
            f"✅ Tayyor! Fayl: <b>{context.user_data[f'{ftype}_pdf_name']}</b>",
            reply_markup=kb, parse_mode="HTML")
        inc_req()
    except subprocess.CalledProcessError:
        await msg.edit_text("❌ LibreOffice xatosi. Fayl shikastlangan bo'lishi mumkin.")
    except Exception as e:
        await msg.edit_text(f"❌ Xatolik: {str(e)[:200]}")
    finally:
        context.user_data.pop("mode", None)

async def send_pdf_result(update, context, ftype, new_name):
    pdf_bytes = context.user_data.pop(f"{ftype}_pdf", None)
    filename = new_name if new_name.endswith(".pdf") else new_name + ".pdf"
    if pdf_bytes:
        await update.message.reply_document(
            InputFile(BytesIO(pdf_bytes), filename=filename),
            caption=f"✅ <b>{filename}</b>", parse_mode="HTML")
    await update.message.reply_text("Boshqa xizmat:", reply_markup=kb_main())

# ═══════════════════════════════════════════════════════════════
#  JPG → PDF
# ═══════════════════════════════════════════════════════════════
async def make_pdf(update, context, filename="rasmlar"):
    user    = update.effective_user
    msg_obj = update.message or (update.callback_query.message if update.callback_query else None)
    images  = user_images.get(user.id, [])
    if not images: await msg_obj.reply_text("❌ Rasm topilmadi!"); return
    wait = await msg_obj.reply_text(f"⏳ {len(images)} ta rasmdan PDF yaratilmoqda...")
    try:
        processed = []
        for b in images:
            img = Image.open(BytesIO(b))
            if img.mode != "RGB": img = img.convert("RGB")
            out = BytesIO(); img.save(out, format="JPEG", quality=95); processed.append(out.getvalue())
        pdf = BytesIO(img2pdf.convert(processed))
        safe = filename if filename.endswith(".pdf") else f"{filename}.pdf"
        await msg_obj.reply_document(InputFile(pdf, filename=safe),
            caption=f"✅ {len(images)} ta rasmdan <b>{safe}</b>!", parse_mode="HTML")
        await wait.delete()
        db = load_db(); db["total_pdfs"] = db.get("total_pdfs", 0) + 1
        uid = str(user.id)
        if uid in db["users"]: db["users"][uid]["pdfs"] += 1
        save_db(db); user_images.pop(user.id, None); context.user_data.clear()
        await msg_obj.reply_text("Boshqa xizmat:", reply_markup=kb_main())
    except Exception as e:
        await wait.edit_text(f"❌ Xato: {str(e)[:200]}")

# ═══════════════════════════════════════════════════════════════
#  PDF MERGE
# ═══════════════════════════════════════════════════════════════
async def do_merge(update, context):
    user    = update.effective_user
    msg_obj = update.callback_query.message if update.callback_query else update.message
    lst     = user_pdf_list.get(user.id, [])
    if len(lst) < 2: await msg_obj.reply_text("⚠️ Kamida 2 ta PDF kerak!"); return
    wait = await msg_obj.reply_text(f"⏳ {len(lst)} ta PDF birlashtirilmoqda...")
    try:
        from pypdf import PdfWriter, PdfReader
        writer = PdfWriter()
        for _, data in lst:
            for page in PdfReader(BytesIO(data)).pages: writer.add_page(page)
        out = BytesIO(); writer.write(out); out.seek(0)
        await msg_obj.reply_document(InputFile(out, filename="merged.pdf"),
            caption=f"✅ {len(lst)} ta PDF birlashtirildi!")
        await wait.delete(); user_pdf_list.pop(user.id, None); context.user_data.clear(); inc_req()
        await msg_obj.reply_text("Boshqa xizmat:", reply_markup=kb_main())
    except ImportError:
        await wait.edit_text("❌ pypdf o'rnatilmagan. requirements.txt ga qo'shing.")
    except Exception as e:
        await wait.edit_text(f"❌ Xato: {str(e)[:200]}")

# ═══════════════════════════════════════════════════════════════
#  REFERAT (Claude AI)
# ═══════════════════════════════════════════════════════════════
async def do_referat(update: Update, context: ContextTypes.DEFAULT_TYPE, mavzu: str):
    if not ANTHROPIC_KEY:
        await update.message.reply_text(
            "❌ ANTHROPIC_API_KEY sozlanmagan.\nGitHub Secrets ga qo'shing.", reply_markup=kb_main())
        context.user_data.clear(); return
    msg = await update.message.reply_text(f"✍️ <b>«{mavzu}»</b>\n\nReferat yozilmoqda... ⏳", parse_mode="HTML")
    try:
        payload = json.dumps({"model": "claude-sonnet-4-20250514", "max_tokens": 4000,
            "messages": [{"role": "user", "content":
                f"O'zbek tilida quyidagi mavzuda to'liq akademik referat yoz:\n\nMavzu: {mavzu}\n\n"
                "Tuzilma:\n1. Kirish\n2. Asosiy qism (2-3 kichik bo'lim)\n3. Xulosa\n4. Adabiyotlar\n\n"
                "Ilmiy uslubda, 1000–1500 so'z hajmida yoz."}]}).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=payload,
            headers={"Content-Type": "application/json", "anthropic-version": "2023-06-01",
                     "x-api-key": ANTHROPIC_KEY})
        with urllib.request.urlopen(req, timeout=120) as r:
            referat = json.loads(r.read())["content"][0]["text"]
        await msg.delete()
        chunks = [referat[i:i+4000] for i in range(0, len(referat), 4000)]
        for i, chunk in enumerate(chunks):
            prefix = f"📄 <b>«{mavzu}»</b>\n\n" if i == 0 else ""
            await update.message.reply_text(prefix + chunk, parse_mode="HTML")
        # Word fayl sifatida
        try:
            with tempfile.TemporaryDirectory() as tmp:
                txt_p = os.path.join(tmp, "referat.txt"); doc_p = os.path.join(tmp, "referat.docx")
                with open(txt_p, "w", encoding="utf-8") as f: f.write(f"MAVZU: {mavzu}\n\n{referat}")
                subprocess.run(["libreoffice", "--headless", "--convert-to", "docx", "--outdir", tmp, txt_p],
                    capture_output=True, timeout=30)
                if os.path.exists(doc_p):
                    safe = mavzu[:30].replace("/", "-") + ".docx"
                    with open(doc_p, "rb") as f:
                        await update.message.reply_document(InputFile(f, filename=safe), caption="📎 Word fayl")
        except Exception: pass
        inc_req()
    except Exception as e:
        await msg.edit_text(f"❌ Xatolik: {str(e)[:200]}")
    finally:
        context.user_data.clear()
        await update.message.reply_text("Boshqa xizmat:", reply_markup=kb_main())

# ═══════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════
#  VIDEO / RASM YUKLAB OLISH
# ═══════════════════════════════════════════════════════════════
RAPIDAPI_KEY  = os.environ.get("RAPIDAPI_KEY", "")
RAPIDAPI_HOST = "social-download-all-in-one.p.rapidapi.com"
RAPIDAPI_URL  = "https://social-download-all-in-one.p.rapidapi.com/v1/social/autolink"

def is_valid_media_url(url):
    return bool(re.match(
        r'https?://(www\.)?(youtube\.com|youtu\.be|instagram\.com|instagr\.am|tiktok\.com|vm\.tiktok\.com)',
        url.strip()
    ))

async def fetch_download_url(url: str) -> dict:
    payload = json.dumps({"url": url}).encode()
    req = urllib.request.Request(
        RAPIDAPI_URL, data=payload,
        headers={
            "Content-Type":    "application/json",
            "x-rapidapi-host": RAPIDAPI_HOST,
            "x-rapidapi-key":  RAPIDAPI_KEY,
        }
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

async def do_download(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    url = url.strip()
    if not is_valid_media_url(url):
        await update.message.reply_text(
            "❌ Faqat quyidagi havolalar qabul qilinadi:\n\n"
            "▶️ YouTube: <code>https://youtube.com/watch?v=...</code>\n"
            "📸 Instagram: <code>https://instagram.com/p/...</code>\n"
            "🎵 TikTok: <code>https://tiktok.com/@.../video/...</code>",
            parse_mode="HTML"
        ); return

    if not RAPIDAPI_KEY:
        await update.message.reply_text(
            "❌ <b>RAPIDAPI_KEY</b> sozlanmagan!\nGitHub Secrets ga qo'shing.",
            parse_mode="HTML"
        ); return

    msg = await update.message.reply_text("⏳ Yuklanmoqda... 15–30 soniya kutib turing.")
    try:
        data = await fetch_download_url(url)

        if data.get("error"):
            await msg.edit_text("❌ Video topilmadi yoki mavjud emas."); return

        title  = (data.get("title") or data.get("author") or "video")[:50]
        medias = data.get("medias") or []
        dl_url = None
        is_photo = False

        # Eng yaxshi video sifatini tanlash: hd_no_watermark > no_watermark > watermark
        quality_order = ["hd_no_watermark", "hd", "no_watermark", "360", "480", "720", "watermark"]
        video_medias  = [m for m in medias if isinstance(m, dict) and m.get("type") == "video"]
        audio_medias  = [m for m in medias if isinstance(m, dict) and m.get("type") == "audio"]
        photo_medias  = [m for m in medias if isinstance(m, dict) and m.get("type") in ("image","photo")]

        if video_medias:
            for q in quality_order:
                for m in video_medias:
                    if q in str(m.get("quality","")).lower():
                        dl_url = m.get("url",""); break
                if dl_url: break
            if not dl_url:
                dl_url = video_medias[0].get("url","")
        elif photo_medias:
            dl_url   = photo_medias[0].get("url","")
            is_photo = True
        elif audio_medias:
            dl_url = audio_medias[0].get("url","")

        if not dl_url:
            await msg.edit_text("❌ Media URL topilmadi."); return

        await msg.edit_text("📥 Fayl yuklanmoqda...")
        req2 = urllib.request.Request(dl_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req2, timeout=60) as r:
            file_data = r.read()

        size_mb = len(file_data) / (1024 * 1024)
        if size_mb > 50:
            await msg.edit_text(f"❌ Fayl {size_mb:.1f} MB — Telegram 50 MB limitdan katta."); return

        me      = (await context.bot.get_me()).username
        caption = f"{'🖼' if is_photo else '🎬'} <b>{title}</b>\n📥 @{me}"
        await msg.edit_text("📤 Yuborilmoqda...")

        if is_photo:
            await update.message.reply_photo(
                InputFile(BytesIO(file_data), filename="photo.jpg"),
                caption=caption, parse_mode="HTML")
        else:
            await update.message.reply_video(
                InputFile(BytesIO(file_data), filename="video.mp4"),
                caption=caption, parse_mode="HTML", supports_streaming=True)
        await msg.delete()
        inc_req()

    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode()[:150]
        except: pass
        if e.code in (401, 403):
            await msg.edit_text("❌ API key noto'g'ri yoki subscribe qilinmagan.")
        elif e.code == 429:
            await msg.edit_text("❌ API limit tugadi (oylik 500 bepul so'rov).")
        else:
            await msg.edit_text(f"❌ API xatosi {e.code}: {body}")
    except Exception as e:
        err = str(e)
        if "private" in err.lower():
            await msg.edit_text("❌ Bu post yopiq (private).")
        else:
            await msg.edit_text(f"❌ Xatolik: {err[:200]}")
    finally:
        context.user_data.clear()
        await update.message.reply_text("Boshqa xizmat:", reply_markup=kb_main())

# ═══════════════════════════════════════════════════════════════
#  DASTURCHI HAQIDA
# ═══════════════════════════════════════════════════════════════
async def about_dev(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📱 Telegram",  url="https://t.me/nurislomdev"),
        InlineKeyboardButton("📢 Kanal",     url=f"https://t.me/{CHANNEL_USERNAME[1:]}"),
    ]])
    await update.message.reply_text(
        "👨‍💻 <b>Nurislom Narzullayev</b>\n\n"
        "🤖 <b>AI Engineer</b>\n"
        "🐍 <b>Python Developer</b>\n"
        "📍 <b>Toshkent, O'zbekiston</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "💼 <b>Ixtisosliklar:</b>\n"
        "• AI/ML tizimlarini ishlab chiqish\n"
        "• Telegram botlar yaratish\n"
        "• Backend dasturlash (Python/FastAPI)\n"
        "• Jarayonlarni avtomatlashtirish\n\n"
        "🛠 <b>Texnologiyalar:</b>\n"
        "<code>Python • PyTorch • Anthropic Claude</code>\n"
        "<code>FastAPI • Redis • Docker • GitHub Actions</code>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📬 Murojaat: @nurislomdev",
        parse_mode="HTML", reply_markup=kb)

# ═══════════════════════════════════════════════════════════════
#  /done
# ═══════════════════════════════════════════════════════════════
async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    mode = context.user_data.get("mode", "")
    if mode == "jpg" and user_images.get(user.id):
        await make_pdf(update, context, "rasmlar")
    elif mode == "pdf_merge":
        await do_merge(update, context)
    else:
        await update.message.reply_text("ℹ️ Hech qanday fayl topilmadi.")

# ═══════════════════════════════════════════════════════════════
#  CALLBACK HANDLER
# ═══════════════════════════════════════════════════════════════
async def handle_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; data = q.data; user = q.from_user
    await q.answer()

    if data == "check_sub":
        if is_blocked(user.id): await q.edit_message_text("❌ Bloklangansiz."); return
        if await check_sub(context.bot, user.id):
            await q.edit_message_text(f"✅ Rahmat, {user.first_name}!")
            await context.bot.send_message(user.id, "Xizmatni tanlang 👇", reply_markup=kb_main())
        else:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Kanalga a'zo bo'lish",
                url=f"https://t.me/{CHANNEL_USERNAME[1:]}")], [InlineKeyboardButton("✅ A'zo bo'ldim", callback_data="check_sub")]])
            await q.edit_message_text("❌ Hali a'zo bo'lmadingiz!", reply_markup=kb)
        return

    if data == "create_pdf":
        imgs = user_images.get(user.id, [])
        if not imgs: await q.edit_message_text("❌ Rasm topilmadi!"); return
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Standart nom", callback_data="pdf_default"),
            InlineKeyboardButton("✏️ Nom o'zgartirish", callback_data="pdf_rename")]])
        await q.edit_message_text(f"📄 {len(imgs)} ta rasm. Fayl nomini tanlang:", reply_markup=kb); return

    if data == "pdf_default":
        await q.edit_message_text("⏳ PDF yaratilmoqda...")
        await make_pdf(update, context, "rasmlar"); return

    if data == "pdf_rename":
        context.user_data["mode"] = "waiting_pdf_name"
        await q.edit_message_text("✏️ Yangi fayl nomini yozing:"); return

    if data == "cancel_all":
        user_images.pop(user.id, None); user_pdf_list.pop(user.id, None); context.user_data.clear()
        await q.edit_message_text("🗑 Bekor qilindi.")
        await context.bot.send_message(user.id, "Xizmatni tanlang:", reply_markup=kb_main()); return

    if data == "merge_now":
        await do_merge(update, context); return

    for ftype in ("pptx", "word"):
        if data == f"send_{ftype}":
            pdf_bytes = context.user_data.pop(f"{ftype}_pdf", None)
            filename  = context.user_data.pop(f"{ftype}_pdf_name", "fayl.pdf")
            if pdf_bytes:
                await q.message.reply_document(InputFile(BytesIO(pdf_bytes), filename=filename),
                    caption=f"✅ <b>{filename}</b>", parse_mode="HTML")
            await q.message.reply_text("Boshqa xizmat:", reply_markup=kb_main()); return
        if data == f"rename_{ftype}":
            context.user_data["mode"] = f"waiting_{ftype}_name"
            await q.edit_message_text("✏️ Yangi fayl nomini yozing:"); return

    # Admin callbacks
    if user.id != ADMIN_ID: return
    if data == "au0": await admin_users_cb(q, 0); return
    if data.startswith("ap_"): await admin_users_cb(q, int(data[3:])); return
    if data.startswith("ad_"): await admin_detail_cb(q, data[3:]); return
    if data.startswith("ab_"):
        uid = data[3:]; db = load_db()
        if uid in db["users"]: db["users"][uid]["blocked"] = True; save_db(db)
        await admin_detail_cb(q, uid); return
    if data.startswith("aub_"):
        uid = data[4:]; db = load_db()
        if uid in db["users"]: db["users"][uid]["blocked"] = False; save_db(db)
        await admin_detail_cb(q, uid); return

# ═══════════════════════════════════════════════════════════════
#  ADMIN FUNKSIYALAR
# ═══════════════════════════════════════════════════════════════
PAGE = 8

async def admin_show_stats(update, context):
    db = load_db(); bl = sum(1 for u in db["users"].values() if u.get("blocked"))
    await update.message.reply_text(
        "📊 <b>Statistika</b>\n\n"
        f"👥 Jami: <b>{len(db['users'])}</b>\n"
        f"✅ Faol: <b>{len(db['users'])-bl}</b>\n"
        f"🚫 Bloklangan: <b>{bl}</b>\n"
        f"📄 PDF: <b>{db.get('total_pdfs',0)}</b>\n"
        f"🔄 So'rovlar: <b>{db.get('total_requests',0)}</b>",
        parse_mode="HTML", reply_markup=kb_admin())

async def admin_show_users(update, context, page):
    db = load_db(); users = list(db["users"].values()); total = len(users)
    chunk = users[page*PAGE:(page+1)*PAGE]
    lines = [f"👥 <b>Foydalanuvchilar ({total})</b> — {page+1}-sahifa\n"]
    btns  = []
    for u in chunk:
        icon = "🚫" if u.get("blocked") else "✅"
        uname = f"@{u['username']}" if u["username"] else "—"
        lines.append(f"{icon} {u['name']} ({uname})")
        btns.append([InlineKeyboardButton(f"{icon} {u['name'][:22]}", callback_data=f"ad_{u['id']}")])
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"ap_{page-1}"))
    if (page+1)*PAGE < total: nav.append(InlineKeyboardButton("➡️", callback_data=f"ap_{page+1}"))
    if nav: btns.append(nav)
    await update.message.reply_text("\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(btns))

async def admin_users_cb(q, page):
    db = load_db(); users = list(db["users"].values()); total = len(users)
    chunk = users[page*PAGE:(page+1)*PAGE]
    lines = [f"👥 <b>Foydalanuvchilar ({total})</b> — {page+1}-sahifa\n"]
    btns  = []
    for u in chunk:
        icon = "🚫" if u.get("blocked") else "✅"
        uname = f"@{u['username']}" if u["username"] else "—"
        lines.append(f"{icon} {u['name']} ({uname})")
        btns.append([InlineKeyboardButton(f"{icon} {u['name'][:22]}", callback_data=f"ad_{u['id']}")])
    nav = []
    if page > 0: nav.append(InlineKeyboardButton("⬅️", callback_data=f"ap_{page-1}"))
    if (page+1)*PAGE < total: nav.append(InlineKeyboardButton("➡️", callback_data=f"ap_{page+1}"))
    if nav: btns.append(nav)
    await q.edit_message_text("\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(btns))

async def admin_detail_cb(q, uid):
    db = load_db(); u = db["users"].get(str(uid))
    if not u: await q.edit_message_text("❌ Topilmadi."); return
    icon = "🚫 Bloklangan" if u.get("blocked") else "✅ Faol"
    uname = f"@{u['username']}" if u["username"] else "—"
    action = (InlineKeyboardButton("🔓 Blokni ochish", callback_data=f"aub_{uid}")
              if u.get("blocked") else
              InlineKeyboardButton("🚫 Bloklash", callback_data=f"ab_{uid}"))
    await q.edit_message_text(
        f"👤 <b>{u['name']}</b>\n🆔 <code>{u['id']}</code>\n📱 {uname}\n"
        f"📅 Qo'shildi: {u.get('joined','—')}\n⏱ Oxirgi: {u.get('last_active','—')}\n"
        f"📄 PDF: {u.get('pdfs',0)}\nStatus: {icon}",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
            [action], [InlineKeyboardButton("🔙 Orqaga", callback_data="au0")]]))

async def do_admin_search(update, context, query):
    db = load_db(); ql = query.lower().lstrip("@")
    found = [u for u in db["users"].values()
             if ql in str(u["id"]) or ql in u.get("username","").lower() or ql in u["name"].lower()]
    if not found:
        await update.message.reply_text(f"❌ '{query}' topilmadi.", reply_markup=kb_admin())
        context.user_data.clear(); return
    lines = [f"🔍 <b>{len(found)} natija:</b>\n"]
    btns  = []
    for u in found[:10]:
        icon = "🚫" if u.get("blocked") else "✅"
        uname = f"@{u['username']}" if u["username"] else "—"
        lines.append(f"{icon} {u['name']} ({uname}) | <code>{u['id']}</code>")
        btns.append([InlineKeyboardButton(f"{icon} {u['name'][:22]}", callback_data=f"ad_{u['id']}")])
    await update.message.reply_text("\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(btns))
    context.user_data.clear()

async def do_broadcast(update, context, text):
    db = load_db(); sent = fail = 0
    for uid, u in db["users"].items():
        if not u.get("blocked"):
            try:
                await context.bot.send_message(int(uid), f"📢 <b>Xabar:</b>\n\n{text}", parse_mode="HTML")
                sent += 1
            except: fail += 1
    await update.message.reply_text(f"📣 ✅ {sent} | ❌ {fail}", reply_markup=kb_admin())
    context.user_data.clear()

# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    while True:
        try:
            logger.info("Bot ishga tushdi...")
            app = Application.builder().token(BOT_TOKEN).build()
            app.add_handler(CommandHandler("start", cmd_start))
            app.add_handler(CommandHandler("done",  cmd_done))
            app.add_handler(CallbackQueryHandler(handle_cb))
            app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
            app.add_handler(MessageHandler(filters.Document.ALL, handle_doc))
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
            app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
        except Exception as e:
            logger.error(f"Bot xato: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
