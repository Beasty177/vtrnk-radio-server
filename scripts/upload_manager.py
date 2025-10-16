import os
import subprocess
from mutagen.id3 import ID3, TIT2, TPE1, APIC
from mutagen.flac import FLAC
from mutagen.wave import WAVE
import logging
from datetime import datetime
import time
import shutil
import json
from dotenv import load_dotenv

# Загрузка .env
load_dotenv()

# Настройка логирования
try:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(os.path.join(os.getenv('LOGS_DIR'), 'upload_manager.log'), mode='a'),
            logging.StreamHandler()
        ]
    )
except Exception as e:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )
    print(f"Ошибка настройки файл-лога: {str(e)}. Используем только консоль.")

logger = logging.getLogger(__name__)

# Константы из .env
UPLOAD_DIR = os.getenv('UPLOAD_DIR')
AUDIO_DIR = os.getenv('AUDIO_DIR')
AUDIO_RADIO_SHOW_DIR = os.getenv('AUDIO_RADIO_SHOW_DIR')
AUDIO_JINGLES_DIR = os.getenv('AUDIO_JINGLES_DIR')
COVER_DIR = os.getenv('COVER_DIR')
SHOW_COVER_DIR = os.getenv('SHOW_COVER_DIR')
JINGLE_COVER_DIR = os.getenv('JINGLE_COVER_DIR')
TRACKS_DATA_DIR = os.getenv('TRACKS_DATA_DIR')
TEMP_DIR = os.getenv('TEMP_DIR')
PLACEHOLDER_POSTER = os.getenv('PLACEHOLDER_POSTER')
PLACEHOLDER_RELATIVE = os.getenv('PLACEHOLDER_RELATIVE')
MP3_LIMIT = int(os.getenv('MP3_LIMIT', 300))
RADIO_SHOW_LIMIT = int(os.getenv('RADIO_SHOW_LIMIT', 20))

# Title validation settings
MAX_TITLE_LENGTH = 200  # Maximum length for track/set titles
MAX_ARTIST_LENGTH = 100  # Maximum length for artist names

# Проверка переменных окружения
logger.debug(f"UPLOAD_DIR: {UPLOAD_DIR}")
logger.debug(f"AUDIO_DIR: {AUDIO_DIR}")
logger.debug(f"AUDIO_RADIO_SHOW_DIR: {AUDIO_RADIO_SHOW_DIR}")
logger.debug(f"AUDIO_JINGLES_DIR: {AUDIO_JINGLES_DIR}")
logger.debug(f"COVER_DIR: {COVER_DIR}")
logger.debug(f"SHOW_COVER_DIR: {SHOW_COVER_DIR}")
logger.debug(f"JINGLE_COVER_DIR: {JINGLE_COVER_DIR}")
logger.debug(f"TRACKS_DATA_DIR: {TRACKS_DATA_DIR}")
logger.debug(f"TEMP_DIR: {TEMP_DIR}")
logger.debug(f"PLACEHOLDER_POSTER: {PLACEHOLDER_POSTER}")
logger.debug(f"PLACEHOLDER_RELATIVE: {PLACEHOLDER_RELATIVE}")
logger.debug(f"MP3_LIMIT: {MP3_LIMIT}")
logger.debug(f"RADIO_SHOW_LIMIT: {RADIO_SHOW_LIMIT}")

# Создание директорий
for dir_path in [UPLOAD_DIR, AUDIO_DIR, AUDIO_RADIO_SHOW_DIR, AUDIO_JINGLES_DIR, COVER_DIR, SHOW_COVER_DIR, JINGLE_COVER_DIR, TRACKS_DATA_DIR, TEMP_DIR]:
    os.makedirs(dir_path, exist_ok=True)
    logger.debug(f"Ensured directory exists: {dir_path}")

def validate_title_length(title, max_length=MAX_TITLE_LENGTH):
    """Validate and truncate title if too long"""
    if not title:
        return title
    if len(title) > max_length:
        logger.warning(f"Title too long ({len(title)} chars), truncating to {max_length}: {title[:50]}...")
        return title[:max_length].rstrip()
    return title

def validate_artist_length(artist, max_length=MAX_ARTIST_LENGTH):
    """Validate and truncate artist name if too long"""
    if not artist:
        return artist
    if len(artist) > max_length:
        logger.warning(f"Artist name too long ({len(artist)} chars), truncating to {max_length}: {artist[:50]}...")
        return artist[:max_length].rstrip()
    return artist

def check_file_stable(file_path, check_interval=2, checks=3):
    """Проверяем, стабилен ли файл (не меняется размер)."""
    prev_size = -1
    for _ in range(checks):
        if not os.path.exists(file_path):
            logger.warning(f"File {file_path} does not exist during stability check.")
            return False
        current_size = os.path.getsize(file_path)
        if current_size == prev_size:
            time.sleep(check_interval)
            continue
        prev_size = current_size
        time.sleep(check_interval)
    return prev_size > 0

def get_cover_art(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    try:
        data = None
        mime = 'image/jpeg'  # Default
        if ext == '.mp3':
            audio = ID3(file_path)
            for tag in audio.values():
                if tag.FrameID == 'APIC':
                    data = tag.data
                    mime = tag.mime
                    break
        elif ext == '.flac':
            audio = FLAC(file_path)
            if audio.pictures:
                data = audio.pictures[0].data
                mime = audio.pictures[0].mime
        elif ext == '.wav':
            audio = WAVE(file_path)
            for tag in audio.values():
                if tag.FrameID == 'APIC':
                    data = tag.data
                    mime = tag.mime
                    break
        if data:
            logger.debug(f"Extracted cover art from {file_path}, mime: {mime}")
            if 'png' in mime.lower():
                return data, '.png'
            else:
                return data, '.jpg'
        logger.debug(f"No cover art found in {file_path}")
        return None, None
    except Exception as e:
        logger.error(f"Ошибка извлечения обложки из {file_path}: {str(e)}")
        return None, None

def get_track_info(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == '.mp3':
            audio = ID3(file_path)
            artist = audio.get('TPE1', ["Unknown Artist"]).text[0]
            title = audio.get('TIT2', [os.path.basename(file_path).split('.')[0]]).text[0]
        elif ext == '.flac':
            audio = FLAC(file_path)
            artist = audio.get('artist', ["Unknown Artist"])[0]
            title = audio.get('title', [os.path.basename(file_path).split('.')[0]])[0]
        elif ext == '.wav':
            audio = WAVE(file_path)
            artist = audio.get('TPE1', ["Unknown Artist"]).text[0]
            title = audio.get('TIT2', [os.path.basename(file_path).split('.')[0]]).text[0]
        logger.debug(f"Extracted metadata from {file_path}: artist={artist}, title={title}")
        return artist, title
    except Exception as e:
        logger.error(f"Ошибка извлечения метаданных из {file_path}: {str(e)}")
        return "Unknown Artist", os.path.basename(file_path).split('.')[0]

def update_metadata(file_path, title, author, cover_data=None, cover_mime='image/jpeg'):
    try:
        audio = ID3(file_path)
        audio['TIT2'] = TIT2(encoding=3, text=title)
        audio['TPE1'] = TPE1(encoding=3, text=author)
        if cover_data:
            audio['APIC'] = APIC(
                encoding=3,
                mime=cover_mime,
                type=3,
                desc='Cover',
                data=cover_data
            )
        audio.save()
        logger.info(f"Обновлены метаданные для {file_path}: title={title}, author={author}, cover={'added' if cover_data else 'not added'}")
    except Exception as e:
        logger.error(f"Ошибка обновления метаданных для {file_path}: {str(e)}")
        raise

def convert_to_mp3(input_path, output_path):
    try:
        subprocess.run([
            'ffmpeg', '-i', input_path, '-c:a', 'libmp3lame', '-q:a', '2',
            '-map_metadata', '0', '-metadata', 'playcount=0', output_path, '-y', '-loglevel', 'error'
        ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        logger.info(f"Конвертирован в MP3: {os.path.basename(output_path)}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Ошибка конвертации в MP3: {e.stderr.decode()}")
        raise
    except Exception as e:
        logger.error(f"Неизвестная ошибка при конвертации: {str(e)}")
        raise

def process_file(file_path):
    file_name = os.path.basename(file_path)
    logger.info(f"Обнаружен файл: {file_name}, жду завершения загрузки...")

    # Проверка стабильности файла
    if not check_file_stable(file_path):
        logger.warning(f"Файл {file_name} ещё загружается или отсутствует, пропускаю.")
        return

    # Копируем в TEMP_DIR
    temp_path = os.path.join(TEMP_DIR, file_name)
    shutil.copy2(file_path, temp_path)
    logger.debug(f"Скопирован файл в {temp_path}")

    # Извлекаем метаданные
    author, title = get_track_info(temp_path)
    # Validate and truncate if too long
    author = validate_artist_length(author)
    title = validate_title_length(title)
    cover_data, cover_ext = get_cover_art(temp_path)
    cover_mime = 'image/png' if cover_ext == '.png' else 'image/jpeg'

    # Все файлы из UPLOAD_DIR считаются обычными треками
    audio_dir = AUDIO_DIR
    cover_dir = COVER_DIR
    cover_base_path = '/images/track_covers/'
    logger.info(f"Обработка файла как трек: audio_dir={audio_dir}, cover_dir={cover_dir}")

    # Конвертация или перемещение
    mp3_name = file_name.rsplit('.', 1)[0] + '.mp3' if file_name.lower().endswith(('.flac', '.wav')) else file_name
    mp3_path = os.path.join(audio_dir, mp3_name)

    if file_name.lower().endswith(('.flac', '.wav')):
        convert_to_mp3(temp_path, mp3_path)
        update_metadata(mp3_path, title, author, cover_data, cover_mime)
    elif file_name.lower().endswith('.mp3'):
        shutil.move(temp_path, mp3_path)
        update_metadata(mp3_path, title, author, cover_data, cover_mime)
    else:
        logger.error(f"Не поддерживаемый формат: {file_name}")
        os.remove(temp_path)
        os.remove(file_path)
        return

    # Проверяем, создался ли MP3
    if not os.path.exists(mp3_path):
        logger.error(f"MP3 не создан: {mp3_path}")
        os.remove(temp_path)
        os.remove(file_path)
        return

    # Сохранение обложки
    cover_saved = False
    cover_path = os.path.join(cover_dir, mp3_name.replace('.mp3', cover_ext or '.jpg'))
    if cover_data:
        try:
            with open(cover_path, 'wb') as f:
                f.write(cover_data)
            logger.info(f"Сохранена обложка: {cover_path}")
            cover_saved = True
        except Exception as e:
            logger.error(f"Ошибка сохранения обложки: {str(e)}")
    if not cover_saved:
        cover_path = os.path.join(cover_dir, mp3_name.replace('.mp3', '.jpg'))
        shutil.copy2(PLACEHOLDER_POSTER, cover_path)
        logger.info(f"Скопирован плейсхолдер для обложки: {cover_path}")
        cover_ext = '.jpg'

    # Сохранение JSON с данными трека
    track_data_path = os.path.join(TRACKS_DATA_DIR, mp3_name.replace('.mp3', '.json'))
    os.makedirs(TRACKS_DATA_DIR, exist_ok=True)
    # Validate full title as well
    full_title = f"{author} - {title}"
    full_title = validate_title_length(full_title)
    track_data = {
        "name": mp3_name,
        "cover": f"{cover_base_path}{mp3_name.replace('.mp3', cover_ext)}" if cover_saved else PLACEHOLDER_RELATIVE,
        "title": full_title,
        "style": "",
        "history": "",
        "uploaded_by": "manual_upload",
        "upload_date": datetime.now().strftime("%Y-%m-%d")
    }
    try:
        with open(track_data_path, 'w', encoding='utf-8') as f:
            json.dump(track_data, f, ensure_ascii=False)
        logger.info(f"Сохранены данные трека: {track_data_path}")
    except Exception as e:
        logger.error(f"Ошибка сохранения JSON: {track_data_path}, ошибка: {str(e)}")

    # Установка прав
    os.chmod(mp3_path, 0o644)
    logger.info(f"Установлены права 644 на {mp3_path}")
    if cover_saved:
        os.chmod(cover_path, 0o644)
        logger.info(f"Установлены права 644 на {cover_path}")

    # Удаление исходного и временного файлов
    os.remove(temp_path)
    os.remove(file_path)
    logger.info(f"Удалены файлы: {temp_path}, {file_path}")

def manage_files():
    # Управление mp3 в AUDIO_DIR
    files = [f for f in os.listdir(AUDIO_DIR) if f.endswith('.mp3')]
    if len(files) > MP3_LIMIT:
        logger.info(f"Найдено {len(files)} mp3-файлов в {AUDIO_DIR}, превышает MP3_LIMIT ({MP3_LIMIT}), удаляю старые.")
        files.sort(key=lambda x: os.path.getctime(os.path.join(AUDIO_DIR, x)))
        for old_file in files[:-MP3_LIMIT]:
            file_path = os.path.join(AUDIO_DIR, old_file)
            os.remove(file_path)
            logger.info(f"Удалён старый mp3-файл: {file_path}")
            for ext in ['.jpg', '.png']:
                cover_path = os.path.join(COVER_DIR, old_file.replace('.mp3', ext))
                if os.path.exists(cover_path):
                    os.remove(cover_path)
                    logger.info(f"Удалена обложка: {cover_path}")

    # Управление радио-шоу
    radio_files = [f for f in os.listdir(AUDIO_RADIO_SHOW_DIR) if f.endswith('.mp3')]
    if len(radio_files) > RADIO_SHOW_LIMIT:
        logger.info(f"Найдено {len(radio_files)} радио-шоу в {AUDIO_RADIO_SHOW_DIR}, превышает RADIO_SHOW_LIMIT ({RADIO_SHOW_LIMIT}), удаляю старые.")
        radio_files.sort(key=lambda x: os.path.getctime(os.path.join(AUDIO_RADIO_SHOW_DIR, x)))
        for old_file in radio_files[:-RADIO_SHOW_LIMIT]:
            file_path = os.path.join(AUDIO_RADIO_SHOW_DIR, old_file)
            os.remove(file_path)
            logger.info(f"Удалён старый файл радио-шоу: {file_path}")
            for ext in ['.jpg', '.png']:
                cover_path = os.path.join(SHOW_COVER_DIR, old_file.replace('.mp3', ext))
                if os.path.exists(cover_path):
                    os.remove(cover_path)
                    logger.info(f"Удалена обложка радио-шоу: {cover_path}")

    # Управление джинглов
    jingle_files = [f for f in os.listdir(AUDIO_JINGLES_DIR) if f.endswith('.mp3')]
    if len(jingle_files) > MP3_LIMIT:
        logger.info(f"Найдено {len(jingle_files)} джинглов в {AUDIO_JINGLES_DIR}, превышает MP3_LIMIT ({MP3_LIMIT}), удаляю старые.")
        jingle_files.sort(key=lambda x: os.path.getctime(os.path.join(AUDIO_JINGLES_DIR, x)))
        for old_file in jingle_files[:-MP3_LIMIT]:
            file_path = os.path.join(AUDIO_JINGLES_DIR, old_file)
            os.remove(file_path)
            logger.info(f"Удалён старый джингл: {file_path}")
            for ext in ['.jpg', '.png']:
                cover_path = os.path.join(JINGLE_COVER_DIR, old_file.replace('.mp3', ext))
                if os.path.exists(cover_path):
                    os.remove(cover_path)
                    logger.info(f"Удалена обложка джингла: {cover_path}")

def main():
    logger.info("Запуск скрипта для управления загрузками...")
    while True:
        for file_name in os.listdir(UPLOAD_DIR):
            file_path = os.path.join(UPLOAD_DIR, file_name)
            if os.path.isfile(file_path):
                logger.info(f"Обнаружен файл для обработки: {file_name}")
                try:
                    process_file(file_path)
                    manage_files()
                except Exception as e:
                    logger.error(f"Ошибка обработки файла {file_name}: {str(e)}")
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        logger.info(f"Удалён проблемный файл: {file_path}")
        time.sleep(10)

if __name__ == "__main__":
    main()