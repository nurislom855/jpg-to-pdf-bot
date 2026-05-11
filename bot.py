import os
import sys
import logging
import json
import time
from PIL import Image
import img2pdf
from io import BytesIO
from datetime import datetime
import urllib.request
import urllib.parse

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    ConversationHandler,
)

# ==================== SOZLAMALAR ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8778116378:AAHvHV0ce7WlKItOfAGCOWL44I3AqRZHbBw")
CHANNEL_USERNAME = "@jpg_to_pdf_otkaz"
ADMIN_ID = 7406325328
ADMIN_USERNAME = "nurislomdev"
REDIS_URL = os.environ.get("REDIS_URL", "https://mutual-satyr-95515.upstash.io")
REDIS_TOKEN = os.environ.get("REDIS_TOKEN", "gQAAAAAAAXUbAAIgcDE2ZWY1NjFlZWM0NTU0ODQxYjI1NDBlM2VlNWU3OTgzNA")

# Conversation states
WAITING_PDF_NAME = 1
WAITING_PPTX = 2
# ====================================================

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

user_images: dict[int, list[bytes]] = {}
user_pptx: dict[int, bytes] = {}
user_mode: dict[int, str] = {}  # 'jpg' yoki 'pptx'


# ==================== REDIS ====================
def redis_get(key):
    try:
        url = f"{REDIS_URL}/get/{key}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {REDIS_TOKEN}"})
        with urllib.request.urlopen(req) as r:
            data = json.loads(r.read())
            result = data.get("result")
            if result:
                return json.loads(result)
    except Exception as e:
        logger.error(f"Redis get xato: {e}")
    return None


def redis_set(key, value):
    try:
        encoded = urllib.parse.quote(json.dumps(value, ensure_ascii=False))
        url = f"{REDIS_URL}/set/{key}/{encoded}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {REDIS_TOKEN}"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except Exception as e:
        logger.error(f"Redis set xato: {e}")
    return None


def load_db():
    db = redis_get("bot_db")
    if db is None:
        db = {"users": {}, "total_pdfs": 0}
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
            "blocked": False
        }
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
        logger.error(f"Xato: {e}")
        return False


# ==================== ASOSIY MENYU ====================
def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 JPG → PDF", callback_data="mode_jpg"),
         InlineKeyboardButton("📊 PPTX → PDF", callback_data="mode_pptx")],
        [InlineKeyboardButton("💬 Admin bilan bog'lanish", url=f"https://t.me/{ADMIN_USERNAME}")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)

    if is_blocked(user.id):
        await update.message.reply_text("❌ Siz botdan bloklangansiz.")
        return

    if user.id == ADMIN_ID:
        await show_admin_panel(update, context)
        return

    is_subscribed = await check_subscription(user.id, context)
    if is_subscribed:
        await update.message.reply_text(
            f"👋 Xush kelibsiz, {user.first_name}!\n\n"
            "Quyidagi xizmatlardan birini tanlang:",
            reply_markup=main_menu_keyboard()
        )
    else:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Kanalga a'zo bo'lish", url="https://t.me/jpg_to_pdf_otkaz")],
            [InlineKeyboardButton("✅ A'zo bo'ldim", callback_data="check_sub")],
        ])
        await update.message.reply_text(
            f"👋 Salom, {user.first_name}!\n\n"
            "⚠️ Botdan foydalanish uchun kanalga a'zo bo'ling:",
            reply_markup=keyboard,
        )


# ==================== JPG → PDF ====================
async def receive_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_blocked(user.id):
        await update.message.reply_text("❌ Bloklangansiz.")
        return

    if user.id != ADMIN_ID:
        if not await check_subscription(user.id, context):
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Kanalga a'zo bo'lish", url="https://t.me/jpg_to_pdf_otkaz")],
                [InlineKeyboardButton("✅ A'zo bo'ldim", callback_data="check_sub")],
            ])
            await update.message.reply_text("⚠️ Avval kanalga a'zo bo'ling:", reply_markup=keyboard)
            return

    photo = update.message.photo[-1]
    file = await photo.get_file()
    image_bytes = bytes(await file.download_as_bytearray())

    if user.id not in user_images:
        user_images[user.id] = []
    user_images[user.id].append(image_bytes)
    user_mode[user.id] = "jpg"

    count = len(user_images[user.id])
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 PDF yaratish", callback_data="create_pdf")],
        [InlineKeyboardButton("🗑 Bekor qilish", callback_data="cancel_all")],
    ])
    await update.message.reply_text(
        f"✅ {count} ta rasm qabul qilindi.\n"
        "📤 Ko'proq rasm yuboring yoki PDF yarating:",
        reply_markup=keyboard
    )


async def generate_pdf_from_images(update: Update, context: ContextTypes.DEFAULT_TYPE, filename="rasmlar"):
    user = update.effective_user

    if user.id not in user_images or not user_images[user.id]:
        await update.message.reply_text("❌ Rasm topilmadi! Avval rasm yuboring.")
        return

    msg = update.message or update.callback_query.message
    await msg.reply_text("⏳ PDF yaratilmoqda...")

    try:
        images = user_images[user.id]
        pdf_bytes_list = []
        for img_bytes in images:
            img = Image.open(BytesIO(img_bytes))
            if img.mode != "RGB":
                img = img.convert("RGB")
            out = BytesIO()
            img.save(out, format="JPEG", quality=95)
            pdf_bytes_list.append(out.getvalue())

        pdf_output = BytesIO(img2pdf.convert(pdf_bytes_list))
        safe_name = filename if filename.endswith(".pdf") else f"{filename}.pdf"

        await msg.reply_document(
            document=pdf_output,
            filename=safe_name,
            caption=f"✅ {len(images)} ta rasmdan <b>{safe_name}</b> yaratildi!",
            parse_mode="HTML"
        )

        db = load_db()
        db["total_pdfs"] += 1
        uid = str(user.id)
        if uid in db["users"]:
            db["users"][uid]["pdfs"] += 1
        save_db(db)
        del user_images[user.id]
        if user.id in user_mode:
            del user_mode[user.id]

        await msg.reply_text("Boshqa xizmat:", reply_markup=main_menu_keyboard())

    except Exception as e:
        logger.error(f"PDF xato: {e}")
        await msg.reply_text("❌ Xato yuz berdi. Qaytadan urinib ko'ring.")


# ==================== PPTX → PDF ====================
async def receive_pptx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_blocked(user.id):
        await update.message.reply_text("❌ Bloklangansiz.")
        return

    if user.id != ADMIN_ID:
        if not await check_subscription(user.id, context):
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Kanalga a'zo bo'lish", url="https://t.me/jpg_to_pdf_otkaz")],
                [InlineKeyboardButton("✅ A'zo bo'ldim", callback_data="check_sub")],
            ])
            await update.message.reply_text("⚠️ Avval kanalga a'zo bo'ling:", reply_markup=keyboard)
            return

    doc = update.message.document
    if not doc or not doc.file_name.endswith(".pptx"):
        await update.message.reply_text("❌ Faqat .pptx fayl yuboring!")
        return

    await update.message.reply_text("⏳ PPTX → PDF ga o'tkazilmoqda...")

    try:
        file = await doc.get_file()
        pptx_bytes = bytes(await file.download_as_bytearray())

        # LibreOffice bilan konvert qilish
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            pptx_path = os.path.join(tmpdir, "file.pptx")
            pdf_path = os.path.join(tmpdir, "file.pdf")

            with open(pptx_path, "wb") as f:
                f.write(pptx_bytes)

            result = subprocess.run(
                ["libreoffice", "--headless", "--convert-to", "pdf", "--outdir", tmpdir, pptx_path],
                capture_output=True, timeout=60
            )

            if os.path.exists(pdf_path):
                with open(pdf_path, "rb") as f:
                    pdf_bytes = f.read()

                filename = doc.file_name.replace(".pptx", ".pdf")
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✏️ Fayl nomini o'zgartirish", callback_data="rename_pptx_pdf")],
                    [InlineKeyboardButton("✅ Shu nom bilan yuklash", callback_data="send_pptx_pdf")],
                ])
                context.user_data["pptx_pdf"] = pdf_bytes
                context.user_data["pptx_pdf_name"] = filename

                await update.message.reply_text(
                    f"✅ Tayyor! Fayl nomi: <b>{filename}</b>\n\nNima qilasiz?",
                    reply_markup=keyboard,
                    parse_mode="HTML"
                )
            else:
                await update.message.reply_text("❌ Konvertatsiya muvaffaqiyatsiz. LibreOffice o'rnatilmagan bo'lishi mumkin.")

    except Exception as e:
        logger.error(f"PPTX xato: {e}")
        await update.message.reply_text("❌ Xato yuz berdi.")


# ==================== TEXT HANDLER ====================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # Admin broadcast
    if user.id == ADMIN_ID and context.user_data.get("broadcast"):
        context.user_data["broadcast"] = False
        message = update.message.text
        db = load_db()
        success = fail = 0
        for uid, udata in db["users"].items():
            if not udata.get("blocked"):
                try:
                    await context.bot.send_message(
                        int(uid),
                        f"📢 <b>Admin xabari:</b>\n\n{message}",
                        parse_mode="HTML"
                    )
                    success += 1
                except:
                    fail += 1
        await update.message.reply_text(f"📢 Xabar yuborildi!\n\n✅ {success}\n❌ {fail}")
        return

    # PDF nom o'zgartirish - JPG
    if context.user_data.get("waiting_pdf_name"):
        context.user_data["waiting_pdf_name"] = False
        filename = update.message.text.strip()
        await generate_pdf_from_images(update, context, filename)
        return

    # PDF nom o'zgartirish - PPTX
    if context.user_data.get("waiting_pptx_name"):
        context.user_data["waiting_pptx_name"] = False
        filename = update.message.text.strip()
        if not filename.endswith(".pdf"):
            filename += ".pdf"
        pdf_bytes = context.user_data.get("pptx_pdf")
        if pdf_bytes:
            pdf_output = BytesIO(pdf_bytes)
            await update.message.reply_document(
                document=pdf_output,
                filename=filename,
                caption=f"✅ <b>{filename}</b> tayyor!",
                parse_mode="HTML"
            )
            context.user_data.pop("pptx_pdf", None)
            context.user_data.pop("pptx_pdf_name", None)
            await update.message.reply_text("Boshqa xizmat:", reply_markup=main_menu_keyboard())
        return


# ==================== CALLBACKS ====================
async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    # A'zolik tekshirish
    if data == "check_sub":
        if is_blocked(user.id):
            await query.edit_message_text("❌ Bloklangansiz.")
            return
        if await check_subscription(user.id, context):
            await query.edit_message_text(
                f"✅ Rahmat, {user.first_name}!\n\nQuyidagi xizmatlardan birini tanlang:",
                reply_markup=main_menu_keyboard()
            )
        else:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Kanalga a'zo bo'lish", url="https://t.me/jpg_to_pdf_otkaz")],
                [InlineKeyboardButton("✅ A'zo bo'ldim", callback_data="check_sub")],
            ])
            await query.edit_message_text("❌ Hali a'zo bo'lmagansiz!", reply_markup=keyboard)
        return

    # Rejim tanlash
    if data == "mode_jpg":
        user_mode[user.id] = "jpg"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Orqaga", callback_data="back_menu")]
        ])
        await query.edit_message_text(
            "📸 <b>JPG → PDF rejimi</b>\n\n"
            "Rasmlarni yuboring. Hammasi tayyor bo'lgach '📄 PDF yaratish' tugmasini bosing.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        return

    if data == "mode_pptx":
        user_mode[user.id] = "pptx"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Orqaga", callback_data="back_menu")]
        ])
        await query.edit_message_text(
            "📊 <b>PPTX → PDF rejimi</b>\n\n"
            ".pptx faylini yuboring — PDF ga aylantirib beraman!",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        return

    if data == "back_menu":
        await query.edit_message_text(
            "Quyidagi xizmatlardan birini tanlang:",
            reply_markup=main_menu_keyboard()
        )
        return

    # PDF yaratish tugmasi
    if data == "create_pdf":
        if user.id not in user_images or not user_images[user.id]:
            await query.edit_message_text("❌ Rasm topilmadi!")
            return
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Fayl nomini o'zgartirish", callback_data="rename_pdf")],
            [InlineKeyboardButton("✅ Standart nom bilan", callback_data="default_pdf_name")],
        ])
        await query.edit_message_text(
            f"📄 {len(user_images[user.id])} ta rasm tayyor.\n\nFayl nomini tanlang:",
            reply_markup=keyboard
        )
        return

    if data == "rename_pdf":
        context.user_data["waiting_pdf_name"] = True
        await query.edit_message_text("✏️ Yangi fayl nomini yozing (masalan: mening_hujjatim):")
        return

    if data == "default_pdf_name":
        await query.edit_message_text("⏳ PDF yaratilmoqda...")
        await generate_pdf_from_images(update, context, "rasmlar")
        return

    # Bekor qilish
    if data == "cancel_all":
        if user.id in user_images:
            del user_images[user.id]
        if user.id in user_mode:
            del user_mode[user.id]
        await query.edit_message_text(
            "🗑 Bekor qilindi.",
            reply_markup=main_menu_keyboard()
        )
        return

    # PPTX PDF yuborish
    if data == "send_pptx_pdf":
        pdf_bytes = context.user_data.get("pptx_pdf")
        filename = context.user_data.get("pptx_pdf_name", "fayl.pdf")
        if pdf_bytes:
            pdf_output = BytesIO(pdf_bytes)
            await query.message.reply_document(
                document=pdf_output,
                filename=filename,
                caption=f"✅ <b>{filename}</b> tayyor!",
                parse_mode="HTML"
            )
            context.user_data.pop("pptx_pdf", None)
            context.user_data.pop("pptx_pdf_name", None)
            await query.message.reply_text("Boshqa xizmat:", reply_markup=main_menu_keyboard())
        return

    if data == "rename_pptx_pdf":
        context.user_data["waiting_pptx_name"] = True
        await query.edit_message_text("✏️ Yangi fayl nomini yozing (masalan: mening_taqdimotim):")
        return

    # ==================== ADMIN ====================
    if user.id != ADMIN_ID:
        return

    if data == "admin_stats":
        db = load_db()
        blocked = sum(1 for u in db["users"].values() if u.get("blocked"))
        text = (
            "📊 <b>Statistika:</b>\n\n"
            f"👥 Jami: <b>{len(db['users'])}</b>\n"
            f"✅ Faol: <b>{len(db['users']) - blocked}</b>\n"
            f"🚫 Bloklangan: <b>{blocked}</b>\n"
            f"📄 Jami PDF: <b>{db['total_pdfs']}</b>"
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="admin_back")]])
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")

    elif data == "admin_users":
        db = load_db()
        users = list(db["users"].values())
        if not users:
            await query.edit_message_text("👥 Foydalanuvchilar yo'q.")
            return

        text = "👥 <b>Foydalanuvchilar (oxirgi 10):</b>\n\n"
        buttons = []
        for u in users[-10:]:
            status = "🚫" if u.get("blocked") else "✅"
            uname = f"@{u['username']}" if u['username'] else "—"
            text += f"{status} <b>{u['name']}</b> ({uname})\n"
            text += f"   📄 PDF: {u['pdfs']} | 📅 {u['joined']}\n\n"
            if u.get("blocked"):
                buttons.append([InlineKeyboardButton(f"🔓 {u['name']}", callback_data=f"unblock_{u['id']}")])
            else:
                buttons.append([InlineKeyboardButton(f"🚫 {u['name']}", callback_data=f"block_{u['id']}")])

        buttons.append([InlineKeyboardButton("🔙 Orqaga", callback_data="admin_back")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")

    elif data == "admin_broadcast":
        context.user_data["broadcast"] = True
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Bekor qilish", callback_data="admin_back")]])
        await query.edit_message_text("📢 Xabarni yozing:", reply_markup=keyboard)

    elif data == "admin_back":
        await show_admin_panel(update, context)

    elif data.startswith("block_"):
        uid = data.split("_")[1]
        db = load_db()
        if uid in db["users"]:
            db["users"][uid]["blocked"] = True
            save_db(db)
        await show_admin_panel(update, context)

    elif data.startswith("unblock_"):
        uid = data.split("_")[1]
        db = load_db()
        if uid in db["users"]:
            db["users"][uid]["blocked"] = False
            save_db(db)
        await show_admin_panel(update, context)


# ==================== ADMIN PANEL ====================
async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    total_users = len(db["users"])
    total_pdfs = db["total_pdfs"]
    blocked = sum(1 for u in db["users"].values() if u.get("blocked"))

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Foydalanuvchilar", callback_data="admin_users")],
        [InlineKeyboardButton("📢 Hammaga xabar", callback_data="admin_broadcast")],
        [InlineKeyboardButton("📊 Statistika", callback_data="admin_stats")],
    ])

    text = (
        "🛠 <b>Admin Panel</b>\n\n"
        f"👥 Jami foydalanuvchilar: <b>{total_users}</b>\n"
        f"📄 Jami PDF: <b>{total_pdfs}</b>\n"
        f"🚫 Bloklangan: <b>{blocked}</b>"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Siz admin emassiz!")
        return
    await show_admin_panel(update, context)


def main():
    while True:
        try:
            logger.info("Bot ishga tushdi...")
            app = Application.builder().token(BOT_TOKEN).build()

            app.add_handler(CommandHandler("start", start))
            app.add_handler(CommandHandler("admin", admin_command))
            app.add_handler(CallbackQueryHandler(handle_callbacks))
            app.add_handler(MessageHandler(filters.PHOTO, receive_image))
            app.add_handler(MessageHandler(
                filters.Document.MimeType("application/vnd.openxmlformats-officedocument.presentationml.presentation"),
                receive_pptx
            ))
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

            app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

        except Exception as e:
            logger.error(f"Bot xato: {e}")
            logger.info("5 soniyadan keyin qayta ishga tushadi...")
            time.sleep(5)


if __name__ == "__main__":
    main()
