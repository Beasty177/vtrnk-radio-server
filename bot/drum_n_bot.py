import os
import logging
import logging.handlers
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes
import aiohttp
import asyncio
import sqlite3

# === ЛОГИРОВАНИЕ ===
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Теперь всё логируем, даже DEBUG

log_path = '/home/beasty197/projects/vtrnk_radio/logs/drum_n_bot.log'
os.makedirs(os.path.dirname(log_path), exist_ok=True)

handler = logging.handlers.RotatingFileHandler(log_path, maxBytes=10*1024*1024, backupCount=10)
console_handler = logging.StreamHandler()

formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s')
handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

logger.addHandler(handler)
logger.addHandler(console_handler)

# === КОНФИГ ===
load_dotenv('/home/beasty197/projects/vtrnk_radio/.env')
BOT_TOKEN = os.getenv('BOT_TOKEN_DMB')
if not BOT_TOKEN:
    logger.error("BOT_TOKEN_DMB не найден в .env!")
    exit(1)

#CHANNEL_ID = '-1001900735646'  # @vtornikshow пока закоментировал
CHANNEL_ID = '-1002134999444'  #чат вторника бисти битс23 пока пусть работает как костыль, потому что наверно вернем обратно

RADIO_SHOW_DIR = '/home/beasty197/projects/vtrnk_radio/audio/radio_show'
BASE_DIR = '/home/beasty197/projects/vtrnk_radio'
DB_PATH = '/home/beasty197/projects/vtrnk_radio/data/radio.db'

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_show_description(path):
    try:
        with get_db() as conn:
            row = conn.execute("SELECT description FROM tracks WHERE path = ? AND track_info = 'radio_show'", (path,)).fetchone()
            return row['description'].strip() if row and row['description'] else None
    except Exception as e:
        logger.error(f"get_show_description error: {e}")
        return None

def get_live_stream_description(show_code):
    if not show_code:
        return None, None
    try:
        with get_db() as conn:
            row = conn.execute("SELECT description, cover_path FROM live_streams WHERE show_code = ? LIMIT 1", (show_code,)).fetchone()
            if row:
                desc = row['description'].strip() if row['description'] else None
                cover = row['cover_path'] or '/images/placeholder_live_stream.png'
                return desc, cover
            return None, None
    except Exception as e:
        logger.error(f"get_live_stream_description error: {e}")
        return None, None

# === ОТПРАВКА В КАНАЛ ===
async def post_to_channel(context, cover_path: str, caption_text: str):
    keyboard = [[InlineKeyboardButton("Слушать радио в Telegram", url="https://t.me/drum_n_bot/radio")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    full_caption = f"{caption_text}\n\n[Слушать радио в Telegram](https://t.me/drum_n_bot/radio)"
    file_path = f"{BASE_DIR}{cover_path}" if cover_path.startswith("/") else cover_path

    try:
        if os.path.isfile(file_path):
            with open(file_path, 'rb') as photo:
                await context.bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=photo,
                    caption=full_caption,
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
            logger.info(f"Опубликовано в канал с обложкой: {os.path.basename(file_path)}")
        else:
            logger.warning(f"Обложка не найдена локально: {file_path} → используем заглушку")
            await context.bot.send_photo(
                chat_id=CHANNEL_ID,
                photo="https://vtrnk.online/images/placeholder2.png",
                caption=full_caption + "\n\n(обложка временно недоступна)",
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
            logger.info("Опубликовано с заглушкой (обложка не найдена)")
    except Exception as e:
        logger.error(f"ОШИБКА при публикации в канал: {e}", exc_info=True)
        try:
            await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=full_caption,
                parse_mode='Markdown',
                disable_web_page_preview=True
            )
            logger.info("Опубликовано как текст (без фото)")
        except Exception as e2:
            logger.critical(f"КРИТИЧЕСКАЯ ОШИБКА: не удалось отправить даже текст: {e2}")

# === /radio ===
async def radio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://vtrnk.online/track") as r:
                    if r.status != 200:
                        await update.message.reply_text("Радио временно недоступно")
                        return
                    data = await r.json()

            artist = data[1][1] if len(data) > 1 else "VTRNK"
            title = data[2][1] if len(data) > 2 else "Unknown Track"
            cover = data[5][1] if len(data) > 5 else "/images/placeholder2.png"
            file_path = f"{BASE_DIR}{cover}" if cover.startswith("/") else cover

            keyboard = [[InlineKeyboardButton("Слушать радио в Telegram", url="https://t.me/drum_n_bot/radio")]]
            caption = f"*Сейчас в эфире:*\n{title} — {artist}"

            if os.path.isfile(file_path):
                with open(file_path, 'rb') as f:
                    await update.message.reply_photo(photo=f, caption=caption, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
            else:
                await update.message.reply_photo(photo="https://vtrnk.online/images/placeholder2.png", caption=caption, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        except Exception as e:
            logger.error(f"/radio в личке — ошибка: {e}")
            await update.message.reply_text("Не могу получить текущий трек")

    else:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://vtrnk.online/track") as r:
                    data = await r.json()
                artist = data[1][1] if len(data) > 1 else "VTRNK"
                title = data[2][1] if len(data) > 2 else "Radio"
                cover = data[5][1] if len(data) > 5 else "/images/placeholder2.png"
                caption = f"*Сейчас играет:*\n{title} — {artist}"
            await post_to_channel(context, cover, caption)
        except Exception as e:
            logger.error(f"/radio в группе — ошибка: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args and context.args[0] == 'launch_radio':
        kb = [[InlineKeyboardButton("Слушать радио в Telegram", url="https://t.me/drum_n_bot/radio")]]
        await update.message.reply_text("VTRNK Radio запущено!", reply_markup=InlineKeyboardMarkup(kb))

# === ГЛАВНЫЙ МОНИТОРИНГ С МАКСИМАЛЬНЫМИ ЛОГАМИ ===
async def monitor_events(context: ContextTypes.DEFAULT_TYPE):
    last_podcast = None
    announced_podcast = None
    last_live_code = None
    announced_live_code = None

    logger.info("ЗАПУСК МОНИТОРИНГА — детальное логирование включено")

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        while True:
            try:
                async with session.get("https://vtrnk.online/track") as resp:
                    if resp.status != 200:
                        logger.warning(f"API вернул {resp.status}, ждём...")
                        await asyncio.sleep(60)
                        continue
                    data = await resp.json()

                filename   = data[0][1] if len(data) > 0 else ""
                artist     = data[1][1] if len(data) > 1 else "VTRNK"
                title      = data[2][1] if len(data) > 2 else "Unknown"
                is_live    = data[4][1] if len(data) > 4 else False
                cover_path = data[5][1] if len(data) > 5 else "/images/placeholder2.png"
                show_code  = data[6][1] if len(data) > 6 else ""

                logger.info(f"ТРЕК: file='{os.path.basename(filename)}' | is_live={is_live} | show_code='{show_code}' | {artist} — {title}")

                # === ПОДКАСТЫ ===
                if filename.startswith(RADIO_SHOW_DIR):
                    if filename == last_podcast:
                        logger.debug("Подкаст уже обрабатывался на прошлом шаге")
                    else:
                        logger.info(f"НОВЫЙ ПОДКАСТ: {os.path.basename(filename)}")
                        await asyncio.sleep(60)

                        async with session.get("https://vtrnk.online/track") as r:
                            check = await r.json()
                            check_file = check[0][1] if len(check) > 0 else ""

                        if check_file == filename and filename != announced_podcast:
                            desc = get_show_description(filename)
                            cap = f"Новый подкаст!\n*{title}* от {artist}"
                            if desc:
                                cap += f"\n\n{desc}"
                            await post_to_channel(context, cover_path, cap)
                            logger.info(f"ПОДКАСТ ОПУБЛИКОВАН: {title} — {artist}")
                            announced_podcast = filename
                        else:
                            logger.warning(f"Подкаст НЕ опубликован: сменился файл или уже был → было: {os.path.basename(filename)}, стало: {os.path.basename(check_file)}")
                        last_podcast = filename

                # === ЛАЙВ-СТРИМЫ ===
                elif is_live and show_code:
                    if show_code == last_live_code:
                        logger.debug(f"Тот же лайв ({show_code}), пропускаем")
                    else:
                        logger.info(f"НОВЫЙ ПРЯМОЙ ЭФИР: {show_code} | {artist} — {title}")
                        await asyncio.sleep(60)

                        async with session.get("https://vtrnk.online/track") as r:
                            check = await r.json()

                        check_live = check[4][1] if len(check) > 4 else False
                        check_code = check[6][1] if len(check) > 6 else ""

                        if check_live and check_code == show_code and show_code != announced_live_code:
                            desc, db_cover = get_live_stream_description(show_code)
                            final_cover = db_cover or cover_path
                            cap = f"ПРЯМОЙ ЭФИР!\n*{artist}* — {title}"
                            if desc:
                                cap += f"\n\n{desc}"
                            await post_to_channel(context, final_cover, cap)
                            logger.info(f"ПРЯМОЙ ЭФИР ОПУБЛИКОВАН: {artist} — {title} ({show_code})")
                            announced_live_code = show_code
                        else:
                            reasons = []
                            if not check_live: reasons.append("is_live упал")
                            if check_code != show_code: reasons.append(f"код сменился → {check_code}")
                            if show_code == announced_live_code: reasons.append("уже публиковали")
                            logger.warning(f"Лайв НЕ опубликован: {', '.join(reasons)}")

                        last_live_code = show_code

                # === ОБЫЧНЫЙ ТРЕК ===
                else:
                    logger.debug("Обычный трек из плейлиста — ничего не делаем")

                # Сброс лайва при завершении
                if not is_live and last_live_code:
                    logger.info(f"Прямой эфир ЗАВЕРШЁН (is_live=False), код был: {last_live_code}")
                    last_live_code = None

            except asyncio.TimeoutError:
                logger.error("Таймаут при запросе к /track")
            except Exception as e:
                logger.error(f"КРИТИЧЕСКАЯ ОШИБКА В МОНИТОРИНГЕ: {e}", exc_info=True)

            await asyncio.sleep(60)

def main():
    logger.info("Запуск drum_n_bot → постим только в @vtornikshow")
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("radio", radio))
    app.add_handler(CommandHandler("start", start))

    app.job_queue.run_repeating(monitor_events, interval=60, first=10)

    logger.info("Бот запущен и мониторит стрим + подкасты")
    app.run_polling()

if __name__ == "__main__":
    main()