import os
import time
import logging
import logging.handlers
import subprocess
import json
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes
import aiohttp
import asyncio
import sqlite3

# === ЛОГИРОВАНИЕ ===
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

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

# CHANNEL_ID = '-1001900735646'  # @vtornikshow
CHANNEL_ID = '-1002134999444'  # чат (временно)

RADIO_SHOW_DIR = '/home/beasty197/projects/vtrnk_radio/audio/radio_show'
BASE_DIR = '/home/beasty197/projects/vtrnk_radio'
DB_PATH = '/home/beasty197/projects/vtrnk_radio/data/radio.db'
SCHEDULE_DB_PATH = '/home/beasty197/projects/vtrnk_radio/data/schedule.db'
RESTRIM_CONFIG_PATH = '/home/beasty197/projects/vtrnk_radio/data/restrim.json'
RESTRIM_POST_DELAY_SEC = 240  # 4 минуты


def track_payload_to_dict(data):
    if isinstance(data, dict):
        return data
    out = {}
    if isinstance(data, list):
        for item in data:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                out[str(item[0])] = item[1]
    return out


def live_announcement_key(payload):
    if not payload.get('is_live'):
        return None
    schedule_id = payload.get('schedule_program_id')
    if payload.get('live_source') == 'schedule' and schedule_id not in (None, '', 0, '0'):
        return f"schedule:{schedule_id}"
    show_code = str(payload.get('show_code') or '').strip()
    if show_code:
        return f"code:{show_code}"
    if schedule_id not in (None, '', 0, '0'):
        return f"schedule:{schedule_id}"
    return None


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_show_description(path):
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT description FROM tracks WHERE path = ? AND track_info = 'radio_show'",
                (path,),
            ).fetchone()
            return row['description'].strip() if row and row['description'] else None
    except Exception as e:
        logger.error(f"get_show_description error: {e}")
        return None


def get_live_stream_description(show_code):
    if not show_code:
        return None, None
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT description, cover_path FROM live_streams WHERE show_code = ? LIMIT 1",
                (show_code,),
            ).fetchone()
            if row:
                desc = row['description'].strip() if row['description'] else None
                cover = row['cover_path'] or '/images/placeholder_live_stream.png'
                return desc, cover
            return None, None
    except Exception as e:
        logger.error(f"get_live_stream_description error: {e}")
        return None, None


def get_schedule_program_meta(program_id):
    if program_id in (None, '', 0, '0'):
        return None, None
    try:
        conn = sqlite3.connect(SCHEDULE_DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT description, poster_url FROM programs WHERE id = ? LIMIT 1",
            (program_id,),
        ).fetchone()
        conn.close()
        if not row:
            return None, None
        desc = row['description'].strip() if row['description'] else None
        cover = row['poster_url'] or None
        return desc, cover
    except Exception as e:
        logger.error(f"get_schedule_program_meta error: {e}")
        return None, None


# === RESTRIM (видео) ===

def load_restrim_targets():
    """Читает targets из restrim.json."""
    try:
        with open(RESTRIM_CONFIG_PATH, 'r') as f:
            data = json.load(f)
        targets = data.get('targets') or []
        out = []
        for t in targets:
            if not isinstance(t, dict):
                continue
            tid = str(t.get('id', '')).strip()
            if not tid:
                continue
            out.append({
                'id': tid,
                'name': (t.get('name') or f'Stream {tid}').strip(),
                'enabled': bool(t.get('enabled')),
                'tg_notify': bool(t.get('tg_notify')),
                'link_url': (t.get('link_url') or '').strip(),
                'link_label': (t.get('link_label') or '').strip(),
            })
        return out
    except Exception as e:
        logger.error(f"load_restrim_targets: {e}")
        return []


def is_restrim_process_running(target_id):
    try:
        r = subprocess.run(
            ['/usr/bin/pgrep', '-f', f'restrim_{target_id}'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return r.returncode == 0
    except Exception as e:
        logger.error(f"is_restrim_process_running({target_id}): {e}")
        return False


async def post_restrim_link(context, target, reason: str):
    """
    Пост только текстом + ссылка (без афиши и кнопок).
    reason: 'start' | 'after_radio'
    """
    name = target.get('name') or f"Поток {target.get('id')}"
    link_url = target.get('link_url') or ''
    link_label = target.get('link_label') or ''

    if not link_url:
        logger.warning(f"restrim post skip id={target.get('id')}: empty link_url")
        return False

    lines = ["🔴 Видео-трансляция", "", name]
    if link_label:
        lines.append(link_label)
    lines.append(link_url)
    text = "\n".join(lines)

    try:
        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=text,
            disable_web_page_preview=False,
        )
        logger.info(
            f"RESTRIM POST ok id={target.get('id')} name={name!r} reason={reason}"
        )
        return True
    except Exception as e:
        logger.error(f"RESTRIM POST fail id={target.get('id')}: {e}", exc_info=True)
        return False


# === ОТПРАВКА В КАНАЛ (аудио / подкасты) ===
async def post_to_channel(context, cover_path: str, caption_text: str):
    keyboard = [[InlineKeyboardButton("Слушать радио в Telegram", url="https://t.me/drum_n_bot/radio")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    full_caption = f"{caption_text}\n\n [Donation](https://t.me/rupor_events_bot?startapp=21547)"
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
                    await update.message.reply_photo(
                        photo=f, caption=caption,
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode='Markdown'
                    )
            else:
                await update.message.reply_photo(
                    photo="https://vtrnk.online/images/placeholder2.png",
                    caption=caption,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
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


# === ГЛАВНЫЙ МОНИТОРИНГ ===
async def monitor_events(context: ContextTypes.DEFAULT_TYPE):
    last_podcast = None
    announced_podcast = None
    last_live_code = None
    announced_live_code = None

    # --- состояние видео-рестрима ---
    # id -> unix time, когда впервые увидели running
    restrim_seen_since = {}
    # id уже получили пост «старт сессии»
    restrim_posted_start = set()
    # время и ключ последнего поста радио/подкаста (для триггера B)
    last_radio_post_at = None
    last_radio_post_key = None
    # пары (radio_key, target_id), по которым уже слали пост после радио
    restrim_posted_after_radio = set()

    logger.info("ЗАПУСК МОНИТОРИНГА — аудио + видео-рестрим (delay=%ss)", RESTRIM_POST_DELAY_SEC)

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        while True:
            try:
                async with session.get("https://vtrnk.online/track") as resp:
                    if resp.status != 200:
                        logger.warning(f"API вернул {resp.status}, ждём...")
                        await asyncio.sleep(60)
                        continue
                    data = await resp.json()

                payload = track_payload_to_dict(data)
                filename = payload.get('filename') or ""
                artist = payload.get('artist') or "VTRNK"
                title = payload.get('title') or "Unknown"
                is_live = payload.get('is_live') or False
                cover_path = payload.get('cover_path') or "/images/placeholder2.png"
                show_code = payload.get('show_code') or ""
                live_key = live_announcement_key(payload)

                logger.info(
                    f"ТРЕК: file='{os.path.basename(filename)}' | is_live={is_live} | "
                    f"show_code='{show_code}' | live_key='{live_key}' | {artist} — {title}"
                )

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
                            cap = f"Сейчас в эфире!\n*{title}* от {artist}"
                            if desc:
                                cap += f"\n\n{desc}"
                            await post_to_channel(context, cover_path, cap)
                            logger.info(f"Радио шоу в записи ОПУБЛИКОВАНО: {title} — {artist}")
                            announced_podcast = filename
                            # триггер B: отсчёт 4 мин после радио-поста
                            last_radio_post_at = time.time()
                            last_radio_post_key = f"podcast:{filename}"
                            restrim_posted_after_radio = {
                                p for p in restrim_posted_after_radio
                                if p[0] == last_radio_post_key
                            }
                        else:
                            logger.warning(
                                f"Подкаст НЕ опубликован: сменился файл или уже был → "
                                f"было: {os.path.basename(filename)}, стало: {os.path.basename(check_file)}"
                            )
                        last_podcast = filename

                # === ЛАЙВ-СТРИМЫ (аудио) ===
                elif live_key:
                    if live_key == last_live_code:
                        logger.debug(f"Тот же лайв ({live_key}), пропускаем")
                    else:
                        logger.info(f"НОВЫЙ ПРЯМОЙ ЭФИР: {live_key} | {artist} — {title}")
                        await asyncio.sleep(60)

                        async with session.get("https://vtrnk.online/track") as r:
                            check_payload = track_payload_to_dict(await r.json())

                        check_key = live_announcement_key(check_payload)
                        check_artist = check_payload.get('artist') or artist
                        check_title = check_payload.get('title') or title
                        check_cover = check_payload.get('cover_path') or cover_path

                        if check_key == live_key and live_key != announced_live_code:
                            schedule_id = check_payload.get('schedule_program_id') or payload.get('schedule_program_id')
                            if check_payload.get('live_source') == 'schedule' or str(live_key).startswith('schedule:'):
                                desc, db_cover = get_schedule_program_meta(schedule_id)
                            else:
                                desc, db_cover = get_live_stream_description(
                                    check_payload.get('show_code') or show_code
                                )
                                if not desc and not db_cover:
                                    desc, db_cover = get_schedule_program_meta(schedule_id)
                            final_cover = db_cover or check_cover
                            cap = f"ПРЯМОЙ ЭФИР!\n*{check_artist}* — {check_title}"
                            if desc:
                                cap += f"\n\n{desc}"
                            await post_to_channel(context, final_cover, cap)
                            logger.info(f"ПРЯМОЙ ЭФИР ОПУБЛИКОВАН: {check_artist} — {check_title} ({live_key})")
                            announced_live_code = live_key
                            last_radio_post_at = time.time()
                            last_radio_post_key = f"live:{live_key}"
                            restrim_posted_after_radio = {
                                p for p in restrim_posted_after_radio
                                if p[0] == last_radio_post_key
                            }
                        else:
                            reasons = []
                            if not check_payload.get('is_live'):
                                reasons.append("is_live упал")
                            if check_key != live_key:
                                reasons.append(f"ключ сменился → {check_key}")
                            if live_key == announced_live_code:
                                reasons.append("уже публиковали")
                            logger.warning(f"Лайв НЕ опубликован: {', '.join(reasons)}")

                        last_live_code = live_key

                else:
                    logger.debug("Обычный трек из плейлиста — ничего не делаем")

                if not is_live and last_live_code:
                    logger.info(f"Прямой эфир ЗАВЕРШЁН (is_live=False), код был: {last_live_code}")
                    last_live_code = None

                # ========== ВИДЕО-РЕСТРИМ (TG) ==========
                now = time.time()
                targets = load_restrim_targets()

                for t in targets:
                    tid = t['id']
                    running = is_restrim_process_running(tid)

                    if not running:
                        if tid in restrim_seen_since:
                            logger.info(f"restrim {tid} process gone — reset session")
                        restrim_seen_since.pop(tid, None)
                        restrim_posted_start.discard(tid)
                        continue

                    # процесс жив
                    if tid not in restrim_seen_since:
                        restrim_seen_since[tid] = now
                        logger.info(
                            f"restrim {tid} ({t.get('name')}) RUNNING detected, "
                            f"wait {RESTRIM_POST_DELAY_SEC}s for start-post"
                        )

                    # нужны галочка и ссылка
                    if not t.get('tg_notify'):
                        continue
                    if not t.get('link_url'):
                        continue

                    age = now - restrim_seen_since[tid]

                    # --- A: пост через 4 мин после старта рестрима ---
                    if age >= RESTRIM_POST_DELAY_SEC and tid not in restrim_posted_start:
                        ok = await post_restrim_link(context, t, reason='start')
                        if ok:
                            restrim_posted_start.add(tid)

                    # --- B: пост через 4 мин после поста радио/подкаста ---
                    if last_radio_post_at and last_radio_post_key:
                        pair = (last_radio_post_key, tid)
                        radio_age = now - last_radio_post_at
                        if (
                            radio_age >= RESTRIM_POST_DELAY_SEC
                            and pair not in restrim_posted_after_radio
                        ):
                            ok = await post_restrim_link(context, t, reason='after_radio')
                            if ok:
                                restrim_posted_after_radio.add(pair)

            except asyncio.TimeoutError:
                logger.error("Таймаут при запросе к /track")
            except Exception as e:
                logger.error(f"КРИТИЧЕСКАЯ ОШИБКА В МОНИТОРИНГЕ: {e}", exc_info=True)

            await asyncio.sleep(60)


def main():
    logger.info("Запуск drum_n_bot с прокси (proxy6.net)")

    PROXY_URL = os.getenv('PROXY_URL')

    if PROXY_URL:
        logger.info("Используем прокси: [HIDDEN]")
    else:
        logger.warning("PROXY_URL не найден в .env!")

    try:
        builder = Application.builder().token(BOT_TOKEN)

        if PROXY_URL:
            builder = builder.proxy(PROXY_URL)

        app = builder.build()

        app.add_handler(CommandHandler("radio", radio))
        app.add_handler(CommandHandler("start", start))

        app.job_queue.run_repeating(monitor_events, interval=60, first=10)

        logger.info("Бот успешно собран и запущен через прокси")
        app.run_polling()

    except Exception as e:
        logger.critical(f"КРИТИЧЕСКАЯ ОШИБКА при запуске бота: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()