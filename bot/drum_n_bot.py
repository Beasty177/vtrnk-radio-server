import os
import logging
import logging.handlers
import sqlite3
import json
import re
from datetime import time
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, ChatMemberUpdated
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, CallbackQueryHandler, filters, ChatMemberHandler
import aiohttp
import asyncio

# Настройка логирования
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
log_path = '/home/beasty197/projects/vtrnk_radio/logs/drum_n_bot.log'
handler = logging.handlers.RotatingFileHandler(
    filename=log_path,
    maxBytes=5*1024*1024,  # 5MB
    backupCount=5
)
console_handler = logging.StreamHandler()
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
console_handler.setFormatter(formatter)
class NoDebugFilter(logging.Filter):
    def filter(self, record):
        return record.levelno > logging.DEBUG
handler.addFilter(NoDebugFilter())
console_handler.addFilter(NoDebugFilter())
logger.addHandler(handler)
logger.addHandler(console_handler)

# Загрузка .env
load_dotenv('/home/beasty197/projects/vtrnk_radio/.env')
BOT_TOKEN = os.getenv('BOT_TOKEN_DMB')
CHAT_ID = os.getenv('CHAT_ID')
RADIO_SHOW_DIR = '/home/beasty197/projects/vtrnk_radio/audio/radio_show'
BASE_DIR = '/home/beasty197/projects/vtrnk_radio'
DB_PATH = '/home/beasty197/projects/vtrnk_radio/data/channels.db'

# Состояния для ConversationHandler
ASK_CHANNEL, ASK_MODE, ASK_EXTRA, ASK_CONFIRM_DEFAULT, CONFIRM, ASK_CLEAN = range(6)
ASK_TEST_CHANNEL = 1
ASK_EDIT_CHANNEL = 1

def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users_channels (
                user_id INTEGER,
                channel_id INTEGER,
                post_mode TEXT,
                extra_data TEXT,
                channel_title TEXT,
                user_username TEXT,
                PRIMARY KEY (user_id, channel_id)
            )
        ''')
        conn.commit()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
    finally:
        conn.close()

def get_db_connection():
    return sqlite3.connect(DB_PATH)

async def add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    await asyncio.sleep(2)
    await update.message.reply_text("Добавь бота в админы канала/чата. Укажи username (с @) или ID.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data='cancel')]]))
    context.user_data['message_ids'] = [update.message.message_id]
    return ASK_CHANNEL

async def ask_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    await asyncio.sleep(2)
    if update.callback_query and update.callback_query.data == 'cancel':
        await update.callback_query.message.reply_text("Добавление канала отменено.")
        await clean_chat(update, context)
        return ConversationHandler.END
    channel = update.message.text.strip()
    context.user_data['message_ids'].append(update.message.message_id)
    try:
        if channel.startswith('@'):
            chat = await context.bot.get_chat(channel)
            channel_id = chat.id
        else:
            channel_id = int(channel)
        # Проверка админства бота
        member = await context.bot.get_chat_member(channel_id, context.bot.id)
        if not member.can_post_messages or member.status != 'administrator':
            await update.message.reply_text("Бот не админ в этом канале или не может постить. Добавь права и попробуй заново.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data='cancel')]]))
            await clean_chat(update, context)
            return ConversationHandler.END
        context.user_data['channel_id'] = channel_id
        keyboard = [
            [InlineKeyboardButton("Все радио-шоу", callback_data='all_shows')],
            [InlineKeyboardButton("Ежедневный пост в 16:20 (или свое время)", callback_data='daily_info')],
            [InlineKeyboardButton("Шоу с ключевым словом", callback_data='keyword_show')],
            [InlineKeyboardButton("Без постов только тест", callback_data='no_posts')],
            [InlineKeyboardButton("Назад", callback_data='back')],
            [InlineKeyboardButton("Отмена", callback_data='cancel')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg = await update.message.reply_text("Выбери режим постов:", reply_markup=reply_markup)
        context.user_data['message_ids'].append(msg.message_id)
        return ASK_MODE
    except Exception as e:
        logger.error(f"Error getting channel: {e}")
        await update.message.reply_text("Не удалось найти канал. Проверь ввод.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data='cancel')]]))
        await clean_chat(update, context)
        return ConversationHandler.END

async def ask_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await context.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.TYPING)
    await asyncio.sleep(2)
    mode = query.data
    if mode == 'cancel':
        await query.message.reply_text("Добавление канала отменено.")
        await clean_chat(update, context)
        return ConversationHandler.END
    if mode == 'back':
        keyboard = [
            [InlineKeyboardButton("Все радио-шоу", callback_data='all_shows')],
            [InlineKeyboardButton("Ежедневный пост в 16:20 (или свое время)", callback_data='daily_info')],
            [InlineKeyboardButton("Шоу с ключевым словом", callback_data='keyword_show')],
            [InlineKeyboardButton("Без постов только тест", callback_data='no_posts')],
            [InlineKeyboardButton("Назад", callback_data='back')],
            [InlineKeyboardButton("Отмена", callback_data='cancel')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text("Выбери режим постов:", reply_markup=reply_markup)
        return ASK_MODE
    context.user_data['post_mode'] = mode
    context.user_data['message_ids'].append(query.message.message_id)
    if mode in ['daily_info', 'keyword_show']:
        if mode == 'daily_info':
            text = "Укажи время для ежедневного поста (HH:MM) или выбери по умолчанию (16:20):"
            keyboard = [[InlineKeyboardButton("По умолчанию (16:20)", callback_data='default_time')], [InlineKeyboardButton("Отмена", callback_data='cancel')]]
        else:
            text = "Укажи ключевое слово для шоу:"
            keyboard = [[InlineKeyboardButton("Отмена", callback_data='cancel')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        msg = await query.message.reply_text(text, reply_markup=reply_markup)
        context.user_data['message_ids'].append(msg.message_id)
        return ASK_EXTRA if mode == 'keyword_show' else ASK_CONFIRM_DEFAULT
    return await confirm_setup(update, context)

async def ask_confirm_default(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await context.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.TYPING)
    await asyncio.sleep(2)
    context.user_data['message_ids'].append(query.message.message_id)
    if query.data == 'cancel':
        await query.message.reply_text("Добавление канала отменено.")
        await clean_chat(update, context)
        return ConversationHandler.END
    if query.data == 'default_time':
        context.user_data['extra_data'] = '16:20'
        msg = await query.message.reply_text("Время 16:20 подтверждено.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data='cancel')]]))
        context.user_data['message_ids'].append(msg.message_id)
        return await confirm_setup(update, context)
    return ASK_CONFIRM_DEFAULT

async def ask_extra(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    await asyncio.sleep(2)
    extra = update.message.text.strip()
    if context.user_data['post_mode'] == 'daily_info':
        if not re.match(r'^\d{2}:\d{2}$', extra):
            msg = await update.message.reply_text("Неверный формат времени. Используй HH:MM (например, 16:20).", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data='cancel')]]))
            context.user_data['message_ids'].append(msg.message_id)
            return ASK_EXTRA
    context.user_data['extra_data'] = extra
    context.user_data['message_ids'].append(update.message.message_id)
    return await confirm_setup(update, context)

async def confirm_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.callback_query else update.message.from_user.id
    channel_id = context.user_data['channel_id']
    mode = context.user_data['post_mode']
    extra = context.user_data.get('extra_data', '')
    try:
        chat = await context.bot.get_chat(channel_id)
        channel_title = chat.title or chat.username or "Без названия"
        user_username = update.effective_user.username or "Без username"
    except Exception as e:
        logger.error(f"Error fetching chat info: {e}")
        channel_title = "Без названия"
        user_username = "Без username"
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO users_channels (user_id, channel_id, post_mode, extra_data, channel_title, user_username) VALUES (?, ?, ?, ?, ?, ?)",
                   (user_id, channel_id, mode, extra, channel_title, user_username))
    conn.commit()
    conn.close()
    if update.callback_query:
        await update.callback_query.message.reply_text(f"Настройки для канала '{channel_title}' установлены: режим '{mode}'{f' с параметром {extra}' if extra else ''}.")
    else:
        await update.message.reply_text(f"Настройки для канала '{channel_title}' установлены: режим '{mode}'{f' с параметром {extra}' if extra else ''}.")
    keyboard = [
        [InlineKeyboardButton("Очистить чат", callback_data='clean_chat')],
        [InlineKeyboardButton("Отставить", callback_data='keep_chat')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = await update.message.reply_text("Хотите очистить чат?", reply_markup=reply_markup)
    context.user_data['message_ids'].append(msg.message_id)
    return ASK_CLEAN

async def clean_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for msg_id in context.user_data.get('message_ids', []):
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=msg_id)
        except:
            pass
    context.user_data.clear()

async def handle_clean_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'clean_chat':
        await query.message.reply_text("Чат очищен.")
        await asyncio.sleep(5)
        await clean_chat(update, context)
    else:
        await query.message.reply_text("Чат оставлен без изменений.")
        await clean_chat(update, context)  # Удаляем только последнее сообщение
        context.user_data['message_ids'] = []
    return ConversationHandler.END

async def my_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT channel_id, post_mode, extra_data, channel_title, user_username FROM users_channels WHERE user_id = ?", (user_id,))
    channels = cursor.fetchall()
    conn.close()
    if not channels:
        await update.message.reply_text("У тебя нет добавленных каналов.")
        return
    table = "ID канала | Название канала | Режим | Доп. данные | Пользователь\n" + "-"*60 + "\n"
    for ch_id, mode, extra, ch_title, u_username in channels:
        ch_link = f"[@{ch_title}](https://t.me/{ch_title})" if ch_title != "Без названия" else ch_title
        table += f"{ch_id} | {ch_link} | {mode} | {extra or 'Нет'} | @{u_username}\n"
    keyboard = [[InlineKeyboardButton("Закрыть", callback_data='close_my_channels')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = await update.message.reply_markdown(table, reply_markup=reply_markup)
    context.user_data['my_channels_msg_id'] = msg.message_id

async def close_my_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        await context.bot.delete_message(chat_id=query.message.chat_id, message_id=query.message.message_id)
    except Exception as e:
        logger.error(f"Error closing my_channels: {e}")

async def edit_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT channel_id, channel_title FROM users_channels WHERE user_id = ?", (user_id,))
    channels = cursor.fetchall()
    conn.close()
    if not channels:
        await update.message.reply_text("У тебя нет добавленных каналов для редактирования.")
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton(ch_title, callback_data=str(ch_id))] for ch_id, ch_title in channels]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выбери канал для редактирования настроек:", reply_markup=reply_markup)
    context.user_data['message_ids'] = [update.message.message_id]
    return ASK_EDIT_CHANNEL

async def ask_edit_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await context.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.TYPING)
    await asyncio.sleep(2)
    channel_id = int(query.data)
    context.user_data['channel_id'] = channel_id
    context.user_data['message_ids'].append(query.message.message_id)
    keyboard = [
        [InlineKeyboardButton("Все радио-шоу", callback_data='all_shows')],
        [InlineKeyboardButton("Ежедневный пост в 16:20 (или свое время)", callback_data='daily_info')],
        [InlineKeyboardButton("Шоу с ключевым словом", callback_data='keyword_show')],
        [InlineKeyboardButton("Без постов только тест", callback_data='no_posts')],
        [InlineKeyboardButton("Назад", callback_data='back')],
        [InlineKeyboardButton("Отмена", callback_data='cancel')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = await query.message.reply_text("Выбери новый режим постов:", reply_markup=reply_markup)
    context.user_data['message_ids'].append(msg.message_id)
    return ASK_MODE

async def remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Укажи ID канала для удаления: /remove <channel_id>")
        return
    user_id = update.message.from_user.id
    try:
        channel_id = int(context.args[0])
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users_channels WHERE user_id = ? AND channel_id = ?", (user_id, channel_id))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"Канал {channel_id} удален.")
    except Exception as e:
        logger.error(f"Error removing channel: {e}")
        await update.message.reply_text("Ошибка при удалении канала. Проверь ID.")

async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT channel_id, channel_title FROM users_channels WHERE user_id = ?", (user_id,))
    channels = cursor.fetchall()
    conn.close()
    if not channels:
        await update.message.reply_text("У тебя нет добавленных каналов. Добавь через /add.")
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton(ch_title, callback_data=str(ch_id))] for ch_id, ch_title in channels]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выбери канал для тестового поста из лички:", reply_markup=reply_markup)
    return ASK_TEST_CHANNEL

async def ask_test_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await context.bot.send_chat_action(chat_id=query.message.chat_id, action=ChatAction.TYPING)
    await asyncio.sleep(2)
    channel_id = int(query.data)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            logger.info("Fetching track data for test post")
            async with session.get("https://vtrnk.online/track") as track_response:
                track_data = await track_response.json()
                logger.info(f"Track response: {track_data}")
                artist = track_data[1][1] if track_data and len(track_data) > 1 else "VTRNK"
                title = track_data[2][1] if track_data and len(track_data) > 2 else "Unknown Track"
            async with session.get("https://vtrnk.online/get_cover_path") as cover_response:
                cover_data = await cover_response.json()
                cover_path = cover_data.get("cover_path", "/images/placeholder2.png")
                file_path = f"{BASE_DIR}{cover_path}" if cover_path.startswith("/") else cover_path
                logger.info(f"Local file path for test post: {file_path}")
        keyboard = [[InlineKeyboardButton("Слушать радио в Telegram", url="https://t.me/drum_n_bot")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        caption = f"Сейчас в эфире: {title} от {artist}\nСлушай на VTRNK Radio: https://vtrnk.online"
        if os.path.exists(file_path) and os.path.isfile(file_path):
            logger.info(f"Sending cover as file: {file_path}")
            with open(file_path, 'rb') as photo:
                await context.bot.send_photo(channel_id, photo=photo, caption=caption, reply_markup=reply_markup)
        else:
            logger.error(f"Cover file not found: {file_path}")
            cover_url = "https://vtrnk.online/images/placeholder2.png"
            logger.info(f"Falling back to default cover URL: {cover_url}")
            await context.bot.send_photo(channel_id, photo=cover_url, caption=caption, reply_markup=reply_markup)
        logger.info(f"Sent test post response: {title} by {artist}")
        await query.message.reply_text(f"Тестовый пост отправлен в канал.")
    except Exception as e:
        logger.error(f"Error in test post: {e}")
        await query.message.reply_text("Не удалось отправить тестовый пост. Попробуйте позже!")
    return ConversationHandler.END

async def handle_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_member: ChatMemberUpdated = update.chat_member
    if chat_member.user.id == context.bot.id and chat_member.new_chat_member.status in ['left', 'kicked']:
        channel_id = chat_member.chat.id
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users_channels WHERE channel_id = ?", (channel_id,))
        conn.commit()
        conn.close()
        logger.info(f"Bot removed from channel {channel_id}, deleted from DB.")

async def daily_post_job(context: ContextTypes.DEFAULT_TYPE):
    channel_id = context.job.data['channel_id']
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://vtrnk.online/track") as resp:
                track_data = await resp.json()
                artist = track_data[1][1] if len(track_data) > 1 else "VTRNK"
                title = track_data[2][1] if len(track_data) > 2 else "Unknown"
            async with session.get("https://vtrnk.online/get_cover_path") as resp:
                cover_data = await resp.json()
                cover_path = cover_data.get("cover_path", "/images/placeholder2.png")
                file_path = f"{BASE_DIR}{cover_path}" if cover_path.startswith("/") else cover_path
        keyboard = [[InlineKeyboardButton("Слушать радио в Telegram", url="https://t.me/drum_n_bot")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        caption = f"Сейчас в эфире: {title} от {artist}\nСлушай на VTRNK Radio: https://vtrnk.online"
        if os.path.exists(file_path):
            with open(file_path, 'rb') as photo:
                await context.bot.send_photo(channel_id, photo=photo, caption=caption, reply_markup=reply_markup)
        else:
            await context.bot.send_photo(channel_id, photo="https://vtrnk.online/images/placeholder2.png", caption=caption, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error in daily post: {e}")

async def radio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            logger.info("Fetching track data for /radio")
            async with session.get("https://vtrnk.online/track") as track_response:
                track_data = await track_response.json()
                logger.info(f"Track response: {track_data}")
                artist = track_data[1][1] if track_data and len(track_data) > 1 else "VTRNK"
                title = track_data[2][1] if track_data and len(track_data) > 2 else "Unknown Track"
            async with session.get("https://vtrnk.online/get_cover_path") as cover_response:
                cover_data = await cover_response.json()
                cover_path = cover_data.get("cover_path", "/images/placeholder2.png")
                file_path = f"{BASE_DIR}{cover_path}" if cover_path.startswith("/") else cover_path
                logger.info(f"Local file path for /radio: {file_path}")
        is_group = update.message.chat.type in ['group', 'supergroup']
        button_type = {'url': 'https://t.me/drum_n_bot'} if is_group else {'web_app': {'url': 'https://vtrnk.online/telegram-mini-app.html'}}
        keyboard = [[InlineKeyboardButton("Слушать радио в Telegram", **button_type)]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if os.path.exists(file_path) and os.path.isfile(file_path):
            logger.info(f"Sending cover as file: {file_path}")
            with open(file_path, 'rb') as photo:
                caption = f"Сейчас в эфире: {title} от {artist}\nСлушай на VTRNK Radio: https://vtrnk.online"
                logger.info(f"Sending /radio response: {caption}")
                await update.message.reply_photo(
                    photo=photo,
                    caption=caption,
                    reply_markup=reply_markup
                )
        else:
            logger.error(f"Cover file not found: {file_path}")
            cover_url = "https://vtrnk.online/images/placeholder2.png"
            logger.info(f"Falling back to default cover URL: {cover_url}")
            caption = f"Сейчас в эфире: {title} от {artist}\nСлушай на VTRNK Radio: https://vtrnk.online"
            await update.message.reply_photo(
                photo=cover_url,
                caption=caption,
                reply_markup=reply_markup
            )
        logger.info(f"Sent /radio response: {title} by {artist}")
    except Exception as e:
        logger.error(f"Error in /radio: {e}")
        await update.message.reply_text("Не удалось получить информацию о текущем треке. Попробуйте позже!")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args and context.args[0] == 'launch_radio':
        keyboard = [[InlineKeyboardButton("Слушать радио в Telegram", web_app={"url": "https://vtrnk.online/telegram-mini-app.html"})]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Запускаем VTRNK Radio!", reply_markup=reply_markup)
        logger.info("Launched Mini App from /start launch_radio")

async def monitor_podcast(context: ContextTypes.DEFAULT_TYPE):
    last_track = None
    announced_tracks = {}
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://vtrnk.online/track") as resp:
                    track_data = await resp.json()
                    filename = track_data[0][1] if len(track_data) > 0 else ""
                    artist = track_data[1][1] if len(track_data) > 1 else "VTRNK"
                    title = track_data[2][1] if len(track_data) > 2 else "Radio Show"
                is_podcast = filename.startswith(RADIO_SHOW_DIR)
                if is_podcast and filename != last_track:
                    await asyncio.sleep(60)
                    async with session.get("https://vtrnk.online/track") as resp:
                        track_data = await resp.json()
                        new_filename = track_data[0][1] if len(track_data) > 0 else ""
                        new_artist = track_data[1][1] if len(track_data) > 1 else "VTRNK"
                        new_title = track_data[2][1] if len(track_data) > 2 else "Radio Show"
                    if new_filename == filename:
                        async with session.get("https://vtrnk.online/get_cover_path") as resp:
                            cover_data = await resp.json()
                            cover_path = cover_data.get("cover_path", "/images/placeholder2.png")
                            file_path = f"{BASE_DIR}{cover_path}" if cover_path.startswith("/") else cover_path
                        keyboard = [[InlineKeyboardButton("Слушать радио в Telegram", url="https://t.me/drum_n_bot")]]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        caption = f"Сейчас у нас в эфире радио подкаст {new_title} от {new_artist}. Подключайтесь!\nСлушай на VTRNK Radio: https://vtrnk.online"
                        conn = get_db_connection()
                        cursor = conn.cursor()
                        cursor.execute("SELECT channel_id, post_mode, extra_data FROM users_channels")
                        channels = cursor.fetchall()
                        conn.close()
                        for ch_id, mode, extra in channels:
                            if mode == 'no_posts':
                                continue
                            if mode == 'all_shows' and new_filename != announced_tracks.get(ch_id):
                                await send_podcast_post(context, ch_id, file_path, caption, reply_markup)
                                announced_tracks[ch_id] = new_filename
                            elif mode == 'keyword_show' and extra and extra.lower() in new_title.lower() and new_filename != announced_tracks.get(ch_id):
                                await send_podcast_post(context, ch_id, file_path, caption, reply_markup)
                                announced_tracks[ch_id] = new_filename
                    last_track = new_filename
                else:
                    last_track = filename
        except Exception as e:
            logger.error(f"Error in monitor: {e}")
        await asyncio.sleep(60)

async def send_podcast_post(context, channel_id, file_path, caption, reply_markup):
    try:
        if os.path.exists(file_path):
            with open(file_path, 'rb') as photo:
                await context.bot.send_photo(channel_id, photo=photo, caption=caption, reply_markup=reply_markup)
        else:
            await context.bot.send_photo(channel_id, photo="https://vtrnk.online/images/placeholder2.png", caption=caption, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error posting to {channel_id}: {e}")

def main():
    logger.info("Starting drum_n_bot")
    init_db()
    application = Application.builder().token(BOT_TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('add', add_channel)],
        states={
            ASK_CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_channel), CallbackQueryHandler(ask_channel, pattern='cancel')],
            ASK_MODE: [CallbackQueryHandler(ask_mode)],
            ASK_EXTRA: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_extra), CallbackQueryHandler(ask_extra, pattern='cancel')],
            ASK_CONFIRM_DEFAULT: [CallbackQueryHandler(ask_confirm_default)],
            ASK_CLEAN: [CallbackQueryHandler(handle_clean_choice, pattern='^(clean_chat|keep_chat)$')],
            CONFIRM: []
        },
        fallbacks=[]
    )
    edit_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('edit', edit_channel)],
        states={
            ASK_EDIT_CHANNEL: [CallbackQueryHandler(ask_edit_channel)],
            ASK_MODE: [CallbackQueryHandler(ask_mode)],
            ASK_EXTRA: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_extra), CallbackQueryHandler(ask_extra, pattern='cancel')],
            ASK_CONFIRM_DEFAULT: [CallbackQueryHandler(ask_confirm_default)],
            ASK_CLEAN: [CallbackQueryHandler(handle_clean_choice, pattern='^(clean_chat|keep_chat)$')],
            CONFIRM: []
        },
        fallbacks=[]
    )
    test_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('test', test)],
        states={
            ASK_TEST_CHANNEL: [CallbackQueryHandler(ask_test_channel)]
        },
        fallbacks=[]
    )
    application.add_handler(conv_handler)
    application.add_handler(edit_conv_handler)
    application.add_handler(test_conv_handler)
    application.add_handler(CommandHandler("radio", radio))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("my_channels", my_channels))
    application.add_handler(CallbackQueryHandler(close_my_channels, pattern='close_my_channels'))
    application.add_handler(CommandHandler("remove", remove_channel))
    application.add_handler(ChatMemberHandler(handle_member_update, ChatMemberHandler.CHAT_MEMBER))
    application.job_queue.run_repeating(monitor_podcast, interval=60, first=0)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT channel_id, extra_data FROM users_channels WHERE post_mode = 'daily_info'")
    dailies = cursor.fetchall()
    conn.close()
    for ch_id, extra in dailies:
        time_str = extra or '16:20'
        try:
            hh, mm = map(int, time_str.split(':'))
            application.job_queue.run_daily(daily_post_job, time=time(hh, mm), data={'channel_id': ch_id})
        except ValueError:
            logger.error(f"Invalid time format in DB for channel {ch_id}: {time_str}")
    application.run_polling()

if __name__ == "__main__":
    main()