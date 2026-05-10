import os
import logging
import json
from PIL import Image
import img2pdf
from io import BytesIO
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatMember
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ==================== SOZLAMALAR ====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8778116378:AAHvHV0ce7WlKItOfAGCOWL44I3AqRZHbBw")
CHANNEL_USERNAME = "@jpg_to_pdf_otkaz"
ADMIN_ID = 7406325328
DB_FILE = "users.json"
# ====================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

user_images: dict[int, list[bytes]] = {}


def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            return json.load(f)
    return {"users": {}, "total_pdfs": 0}


def save_db(db):
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=2)


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
    return db


def is_blocked(user_id):
    db = load_db()
    return db["users"].get(str(user_id), {}).get("blocked", False)


async def check_subscription(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER]
    except Exception as e:
        logger.error(f"Xato: {e}")
        return False


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
            "📸 Rasm yuboring — PDF ga aylantirib beraman!\n"
            "📄 Rasmlar tugagach /pdf yozing."
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

    count = len(user_images[user.id])
    await update.message.reply_text(f"✅ {count} ta rasm qabul qilindi.\n📤 Ko'proq yuboring yoki /pdf yozing.")


async def generate_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if is_blocked(user.id):
        await update.message.reply_text("❌ Bloklangansiz.")
        return

    if user.id not in user_images or not user_images[user.id]:
        await update.message.reply_text("❌ Rasm topilmadi! Avval rasm yuboring.")
        return

    await update.message.reply_text("⏳ PDF yaratilmoqda...")

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

        await update.message.reply_document(
            document=pdf_output,
            filename="rasmlar.pdf",
            caption=f"✅ {len(images)} ta rasmdan PDF yaratildi!"
        )

        db = load_db()
        db["total_pdfs"] += 1
        uid = str(user.id)
        if uid in db["users"]:
            db["users"][uid]["pdfs"] += 1
        save_db(db)
        del user_images[user.id]

    except Exception as e:
        logger.error(f"PDF xato: {e}")
        await update.message.reply_text("❌ Xato yuz berdi. Qaytadan urinib ko'ring.")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id in user_images:
        del user_images[user.id]
    await update.message.reply_text("🗑 Rasmlar o'chirildi.")


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


async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    if data == "check_sub":
        if is_blocked(user.id):
            await query.edit_message_text("❌ Bloklangansiz.")
            return
        if await check_subscription(user.id, context):
            await query.edit_message_text(
                f"✅ Rahmat, {user.first_name}!\n\n"
                "📸 Rasm yuboring — PDF ga aylantirib beraman!\n"
                "📄 Tugagach /pdf yozing."
            )
        else:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Kanalga a'zo bo'lish", url="https://t.me/jpg_to_pdf_otkaz")],
                [InlineKeyboardButton("✅ A'zo bo'ldim", callback_data="check_sub")],
            ])
            await query.edit_message_text("❌ Hali a'zo bo'lmagansiz!", reply_markup=keyboard)
        return

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


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
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
        await update.message.reply_text(
            f"📢 Xabar yuborildi!\n\n✅ Muvaffaqiyatli: {success}\n❌ Xato: {fail}"
        )


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pdf", generate_pdf))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    app.add_handler(MessageHandler(filters.PHOTO, receive_image))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot ishga tushdi...")
    # run_polling o'zi cheksiz ishlaydi
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
