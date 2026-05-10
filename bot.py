import os
import logging
from PIL import Image
import img2pdf
from io import BytesIO
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
# ====================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Har bir user uchun rasmlarni vaqtinchalik saqlash
user_images: dict[int, list[bytes]] = {}


async def check_subscription(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Foydalanuvchi kanalga a'zo ekanligini tekshiradi"""
    try:
        member = await context.bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in [
            ChatMember.MEMBER,
            ChatMember.ADMINISTRATOR,
            ChatMember.OWNER,
        ]
    except Exception as e:
        logger.error(f"A'zolikni tekshirishda xato: {e}")
        return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start komandasi"""
    user = update.effective_user
    is_subscribed = await check_subscription(user.id, context)

    if is_subscribed:
        await update.message.reply_text(
            f"👋 Xush kelibsiz, {user.first_name}!\n\n"
            "📸 Menga rasm yuboring — men ularni PDF ga aylantirib beraman!\n\n"
            "✅ 1 ta yoki 100 ta rasm ham yuborsa bo'ladi.\n"
            "📄 Rasmlar tugagach /pdf buyrug'ini yuboring."
        )
    else:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Kanalga a'zo bo'lish", url=f"https://t.me/jpg_to_pdf_otkaz")],
            [InlineKeyboardButton("✅ A'zo bo'ldim", callback_data="check_subscription")],
        ])
        await update.message.reply_text(
            f"👋 Salom, {user.first_name}!\n\n"
            "⚠️ Botdan foydalanish uchun avval kanalga a'zo bo'ling:",
            reply_markup=keyboard,
        )


async def check_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """'A'zo bo'ldim' tugmasi bosilganda"""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    is_subscribed = await check_subscription(user.id, context)

    if is_subscribed:
        await query.edit_message_text(
            f"✅ Rahmat, {user.first_name}! Endi botdan foydalanishingiz mumkin.\n\n"
            "📸 Rasm yuboring — men PDF ga aylantirib beraman!\n"
            "📄 Rasmlar tugagach /pdf buyrug'ini yuboring."
        )
    else:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Kanalga a'zo bo'lish", url=f"https://t.me/jpg_to_pdf_otkaz")],
            [InlineKeyboardButton("✅ A'zo bo'ldim", callback_data="check_subscription")],
        ])
        await query.edit_message_text(
            "❌ Siz hali kanalga a'zo bo'lmagansiz!\n\n"
            "Quyidagi tugmani bosib, kanalga a'zo bo'ling:",
            reply_markup=keyboard,
        )


async def receive_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Rasm qabul qilish"""
    user = update.effective_user
    is_subscribed = await check_subscription(user.id, context)

    if not is_subscribed:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Kanalga a'zo bo'lish", url=f"https://t.me/jpg_to_pdf_otkaz")],
            [InlineKeyboardButton("✅ A'zo bo'ldim", callback_data="check_subscription")],
        ])
        await update.message.reply_text(
            "⚠️ Botdan foydalanish uchun avval kanalga a'zo bo'ling:",
            reply_markup=keyboard,
        )
        return

    # Rasmni yuklab olish
    photo = update.message.photo[-1]  # Eng yuqori sifatli rasm
    file = await photo.get_file()
    image_bytes = await file.download_as_bytearray()

    # User rasmlarini saqlash
    if user.id not in user_images:
        user_images[user.id] = []
    user_images[user.id].append(bytes(image_bytes))

    count = len(user_images[user.id])
    await update.message.reply_text(
        f"✅ {count} ta rasm qabul qilindi.\n"
        "📤 Ko'proq rasm yuboring yoki /pdf buyrug'ini yuboring."
    )


async def generate_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/pdf buyrug'i — rasmlarni PDF ga aylantirish"""
    user = update.effective_user
    is_subscribed = await check_subscription(user.id, context)

    if not is_subscribed:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Kanalga a'zo bo'lish", url=f"https://t.me/jpg_to_pdf_otkaz")],
            [InlineKeyboardButton("✅ A'zo bo'ldim", callback_data="check_subscription")],
        ])
        await update.message.reply_text(
            "⚠️ Botdan foydalanish uchun avval kanalga a'zo bo'ling:",
            reply_markup=keyboard,
        )
        return

    if user.id not in user_images or not user_images[user.id]:
        await update.message.reply_text(
            "❌ Hech qanday rasm topilmadi!\n"
            "📸 Avval rasm yuboring."
        )
        return

    await update.message.reply_text("⏳ PDF yaratilmoqda, iltimos kuting...")

    try:
        images = user_images[user.id]
        pdf_bytes_list = []

        for img_bytes in images:
            # Pillow bilan rasmni o'qib, JPEG ga aylantiramiz
            img = Image.open(BytesIO(img_bytes))
            if img.mode != "RGB":
                img = img.convert("RGB")
            output = BytesIO()
            img.save(output, format="JPEG", quality=95)
            pdf_bytes_list.append(output.getvalue())

        # img2pdf bilan PDF yaratish
        pdf_output = BytesIO(img2pdf.convert(pdf_bytes_list))
        pdf_output.name = "rasmlar.pdf"

        count = len(images)
        await update.message.reply_document(
            document=pdf_output,
            filename="rasmlar.pdf",
            caption=f"✅ {count} ta rasmdan PDF yaratildi!"
        )

        # Foydalanuvchi rasmlarini tozalash
        del user_images[user.id]

    except Exception as e:
        logger.error(f"PDF yaratishda xato: {e}")
        await update.message.reply_text(
            "❌ PDF yaratishda xato yuz berdi. Iltimos qaytadan urinib ko'ring."
        )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cancel — barcha rasmlarni o'chirish"""
    user = update.effective_user
    if user.id in user_images:
        del user_images[user.id]
    await update.message.reply_text(
        "🗑 Barcha rasmlar o'chirildi. Qaytadan boshlang!"
    )


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pdf", generate_pdf))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CallbackQueryHandler(check_subscription_callback, pattern="check_subscription"))
    app.add_handler(MessageHandler(filters.PHOTO, receive_image))

    logger.info("Bot ishga tushdi...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
