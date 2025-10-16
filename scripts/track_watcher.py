import os
import time
import sqlite3
import json
from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3
from mutagen.id3 import ID3
import logging
import logging.handlers
from dotenv import load_dotenv

# Загрузка .env
load_dotenv('/home/beasty197/projects/vtrnk_radio/.env')

# Настройка логирования
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
handler = logging.handlers.RotatingFileHandler(
    filename=os.path.join(os.getenv('LOGS_DIR'), 'track_watcher.log'),
    maxBytes=5*1024*1024,  # 5 МБ
    backupCount=5
)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

# Пути и настройки из .env
AUDIO_DIRS = [
    os.getenv('AUDIO_DIR'),
    os.getenv('AUDIO_RADIO_SHOW_DIR'),
    os.getenv('AUDIO_JINGLES_DIR')
]
COVER_DIR = os.getenv('COVER_DIR')
SHOW_COVER_DIR = os.getenv('SHOW_COVER_DIR')
JINGLE_COVER_DIR = os.getenv('JINGLE_COVER_DIR')
DB_PATH = os.getenv('DB_PATH')
TRACKS_DATA_DIR = os.getenv('TRACKS_DATA_DIR')
PLACEHOLDER_COVER = os.getenv('PLACEHOLDER_COVER')
RADIO_SHOW_LIMIT = int(os.getenv('RADIO_SHOW_LIMIT', 20))

# Title validation settings
MAX_TITLE_LENGTH = 200  # Maximum length for track/set titles
MAX_ARTIST_LENGTH = 100  # Maximum length for artist names

# Проверка переменных
logger.debug(f"AUDIO_DIRS: {AUDIO_DIRS}")
logger.debug(f"COVER_DIR: {COVER_DIR}")
logger.debug(f"SHOW_COVER_DIR: {SHOW_COVER_DIR}")
logger.debug(f"JINGLE_COVER_DIR: {JINGLE_COVER_DIR}")
logger.debug(f"DB_PATH: {DB_PATH}")
logger.debug(f"TRACKS_DATA_DIR: {TRACKS_DATA_DIR}")
logger.debug(f"PLACEHOLDER_COVER: {PLACEHOLDER_COVER}")
logger.debug(f"RADIO_SHOW_LIMIT: {RADIO_SHOW_LIMIT}")

# Создаём директории
os.makedirs(COVER_DIR, exist_ok=True)
os.makedirs(SHOW_COVER_DIR, exist_ok=True)
os.makedirs(JINGLE_COVER_DIR, exist_ok=True)

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

PREDEFINED_STYLES = [
    "Jungle", "Techstep", "Drum & Bass", "Breakbeat", "Liquid Funk", "Neurofunk",
    "Hardstep", "Darkstep", "Ragga Jungle", "Jump Up", "Minimal DnB", "Ambient DnB"
]

STYLE_VARIANTS = {
    "Drum & Bass": [
        "drum and bass", "dnb", "drum n bass", "d&b", "drumnbass", "drum&bass",
        "drum 'n' bass", "d'n'b", "drumn'bass"
    ],
    "Jungle": [
        "ragga jungle", "junglist", "jungle dnb", "jungle drum & bass",
        "jungle drum and bass", "oldskool jungle"
    ],
    "Techstep": ["tech step", "tech-step"],
    "Liquid Funk": ["liquid funk", "liquid dnb", "liquid"],
    "Neurofunk": ["neuro funk", "neuro"],
    "Breakbeat": ["break beat", "breaks"],
    "Hardstep": ["hard step"],
    "Darkstep": ["dark step", "dark dnb"],
    "Jump Up": ["jumpup", "jump-up"],
    "Minimal DnB": ["minimal drum & bass", "minimal dnb"],
    "Ambient DnB": ["ambient drum & bass", "ambient dnb"]
}

def normalize_style(style):
    style = style.lower().strip()
    for canonical, variants in STYLE_VARIANTS.items():
        if style == canonical.lower() or style in [v.lower() for v in variants]:
            return canonical
    return style.title()

def get_db(retries=5, delay=0.1):
    for attempt in range(retries):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            conn.row_factory = sqlite3.Row
            logger.debug(f"Connected to database: {DB_PATH}")
            return conn
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e) and attempt < retries - 1:
                logger.warning(f"Database locked, retrying in {delay}s (attempt {attempt + 1}/{retries})")
                time.sleep(delay)
            else:
                logger.error(f"Failed to connect to database after {retries} attempts: {str(e)}")
                raise

def init_db():
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                title TEXT,
                cover TEXT,
                duration REAL,
                style TEXT,
                uploaded_by TEXT,
                upload_date TEXT,
                playcount INTEGER DEFAULT 0,
                status TEXT DEFAULT 'available',
                artist TEXT DEFAULT 'Unknown Artist',
                track_title TEXT DEFAULT '',
                path TEXT,
                track_info TEXT DEFAULT 'track',
                path_img TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id INTEGER,
                played_at REAL,
                FOREIGN KEY (track_id) REFERENCES tracks(id)
            )
        """)
        conn.commit()
        conn.close()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing database: {str(e)}")

def check_file_stable(file_path, check_interval=2, checks=3):
    """Проверяем, стабилен ли файл (не меняется размер)."""
    prev_size = -1
    for _ in range(checks):
        if not os.path.exists(file_path):
            return False
        current_size = os.path.getsize(file_path)
        if current_size == prev_size:
            time.sleep(check_interval)
            continue
        prev_size = current_size
        time.sleep(check_interval)
    return prev_size > 0

def extract_cover(file_path):
    try:
        audio = ID3(file_path)
        for tag in audio.values():
            if tag.FrameID == 'APIC':
                return tag.data
        return None
    except Exception as e:
        logger.error(f"Ошибка извлечения обложки из {file_path}: {str(e)}")
        return None

def get_track_metadata(file_path):
    try:
        audio = EasyID3(file_path)
        artist = audio.get('artist', [''])[0]
        title = audio.get('title', [''])[0]
        style = audio.get('genre', [''])[0]
        # Определяем тип записи по директории
        if os.getenv('AUDIO_RADIO_SHOW_DIR') in file_path:
            if not artist or artist == '':
                artist = 'VTRNK'
            if not title or title == '':
                title = 'Radio Show'
        elif os.getenv('AUDIO_JINGLES_DIR') in file_path:
            if not artist or artist == '':
                artist = 'VTRNK Jingle'
            if not title or title == '':
                title = os.path.basename(file_path).replace('.mp3', '')
        else:
            if not artist or artist == '':
                artist = 'Unknown Artist'
            if not title or title == '':
                title = os.path.basename(file_path).replace('.mp3', '')
        if style:
            style = normalize_style(style)
        else:
            style = "Unknown"
        if style == "Unknown":
            for predefined in PREDEFINED_STYLES:
                if predefined.lower() in title.lower() or predefined.lower() in artist.lower():
                    style = predefined
                    break
        logger.debug(f"Extracted metadata for {file_path}: artist={artist}, title={title}, style={style}")
        return artist, title, style
    except Exception as e:
        logger.warning(f"No ID3 tags found for {file_path}: {str(e)}")
        if os.getenv('AUDIO_RADIO_SHOW_DIR') in file_path:
            artist = 'VTRNK'
            title = 'Radio Show'
        elif os.getenv('AUDIO_JINGLES_DIR') in file_path:
            artist = 'VTRNK Jingle'
            title = os.path.basename(file_path).replace('.mp3', '')
        else:
            artist = "Unknown Artist"
            title = os.path.basename(file_path).replace('.mp3', '')
        style = "Unknown"
        for predefined in PREDEFINED_STYLES:
            if predefined.lower() in title.lower() or predefined.lower() in artist.lower():
                style = predefined
                break
        return artist, title, style

def get_track_duration(file_path):
    try:
        audio = MP3(file_path)
        duration = int(audio.info.length)
        logger.debug(f"Duration for {file_path}: {duration} seconds")
        return duration
    except Exception as e:
        logger.error(f"Error reading duration for {file_path}: {str(e)}")
        return 180

def get_uploaded_by(mp3_name):
    json_path = os.path.join(TRACKS_DATA_DIR, mp3_name.replace('.mp3', '.json'))
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            uploaded_by = data.get('uploaded_by', 'Unknown')
            if not uploaded_by or uploaded_by == "Unknown":
                logger.warning(f"No valid uploaded_by in {json_path}, defaulting to 'Unknown'")
            return uploaded_by
    except json.JSONDecodeError as e:
        logger.warning(f"JSON decode error for {json_path}: {str(e)}. Attempting to recover.")
        with open(json_path, 'r', encoding='utf-8') as f:
            raw_content = f.read()
            start_idx = raw_content.find('"uploaded_by": "') + 15
            end_idx = raw_content.find('"', start_idx)
            if start_idx > 14 and end_idx > start_idx:
                uploaded_by = raw_content[start_idx:end_idx].strip()
                if uploaded_by:
                    logger.info(f"Recovered uploaded_by: {uploaded_by} from {json_path}")
                    return uploaded_by
            logger.warning(f"Could not recover uploaded_by from {json_path}, defaulting to 'Unknown'")
            return "Unknown"
    except Exception as e:
        logger.warning(f"Could not read uploaded_by from {json_path}: {str(e)}")
        return "Unknown"

def get_existing_track(mp3_name):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tracks WHERE name = ? AND status = 'available'", (mp3_name,))
        track = cursor.fetchone()
        conn.close()
        return track
    except Exception as e:
        logger.error(f"Error checking existing track {mp3_name}: {str(e)}")
        return None

def add_track_to_db(mp3_name, audio_path):
    conn = None
    try:
        existing_track = get_existing_track(mp3_name)
        if existing_track:
            logger.debug(f"Track {mp3_name} already exists in database with status 'available', skipping.")
            return
        # Проверка стабильности файла
        time.sleep(5)
        if not check_file_stable(audio_path):
            logger.warning(f"File {audio_path} is not stable, skipping for now.")
            return
        conn = get_db()
        cursor = conn.cursor()
        # Определяем директорию для обложки по папке аудио
        if os.getenv('AUDIO_RADIO_SHOW_DIR') in audio_path:
            cover_dir = SHOW_COVER_DIR
            cover_base_path = '/images/show_covers/'
            track_info = 'radio_show'
        elif os.getenv('AUDIO_JINGLES_DIR') in audio_path:
            cover_dir = JINGLE_COVER_DIR
            cover_base_path = '/images/jingle_covers/'
            track_info = 'jingle'
        else:
            cover_dir = COVER_DIR
            cover_base_path = '/images/track_covers/'
            track_info = 'track'
        cover_data = extract_cover(audio_path)
        cover_path = os.path.join(cover_dir, mp3_name.replace('.mp3', '.jpg'))
        cover_saved = False
        if cover_data:
            try:
                with open(cover_path, 'wb') as f:
                    f.write(cover_data)
                logger.info(f"Сохранена обложка из MP3: {cover_path}")
                cover_saved = True
            except Exception as e:
                logger.error(f"Ошибка сохранения обложки из MP3 {cover_path}: {str(e)}")
        # Fallback: проверяем, есть ли внешняя обложка
        if not cover_saved and os.path.exists(cover_path):
            logger.info(f"Найдена внешняя обложка: {cover_path}, используем её.")
            cover_saved = True
        cover = cover_base_path + mp3_name.replace('.mp3', '.jpg') if cover_saved else PLACEHOLDER_COVER
        path_img = cover
        artist, title, style = get_track_metadata(audio_path)
        # Validate and truncate if too long
        artist = validate_artist_length(artist)
        title = validate_title_length(title)
        duration = get_track_duration(audio_path)
        full_title = f"{artist} - {title}" if artist != "Unknown Artist" else title
        # Validate full title as well
        full_title = validate_title_length(full_title)
        uploaded_by = get_uploaded_by(mp3_name)
        upload_date = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getctime(audio_path)))
        cursor.execute("""
            INSERT INTO tracks (name, title, artist, track_title, cover, duration, style, status, playcount, upload_date, uploaded_by, path, track_info, path_img)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'available', 0, ?, ?, ?, ?, ?)
        """, (mp3_name, full_title, artist, title, cover, duration, style, upload_date, uploaded_by, audio_path, track_info, path_img))
        conn.commit()
        logger.info(
            f"Added track to db: {mp3_name} | Title: {full_title} | Artist: {artist} | Track Title: {title} | Duration: {duration}s | Cover: {cover} | Style: {style} | Uploaded by: {uploaded_by} | Upload date: {upload_date} | Path: {audio_path} | Track Info: {track_info} | Path Img: {path_img} | Cover saved: {cover_saved}")
    except Exception as e:
        logger.error(f"Error adding track {mp3_name} to database: {str(e)}")
    finally:
        if conn is not None:
            conn.close()

def sync_db_with_folder(current_files):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM tracks WHERE status = 'available'")
        db_files = set(row['name'] for row in cursor.fetchall())
        logger.debug(f"Files in database (status='available'): {db_files}")
        logger.debug(f"Files on disk: {current_files}")
        missing_in_folder = db_files - current_files
        logger.debug(f"Files missing in folder: {missing_in_folder}")
        for mp3_name in missing_in_folder:
            cursor.execute("UPDATE tracks SET status = 'deleted' WHERE name = ?", (mp3_name,))
            logger.info(f"Marked as deleted in db: {mp3_name}")
        conn.commit()
        logger.debug("Database synchronized with folder (marked deleted tracks)")
    except Exception as e:
        logger.error(f"Error syncing database with folder: {str(e)}")
    finally:
        conn.close()

def delete_marked_files():
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, path FROM tracks WHERE status = 'deleted'")
        deleted_tracks = cursor.fetchall()
        logger.debug(f"Tracks to delete (status='deleted'): {[(track['name'], track['path']) for track in deleted_tracks]}")
        for track in deleted_tracks:
            track_id = track['id']
            name = track['name']
            path = track['path']
            try:
                if os.path.exists(path):
                    os.remove(path)
                    logger.info(f"Deleted file from server: {path}")
                else:
                    logger.debug(f"File {path} does not exist on disk, proceeding with database cleanup")
                cursor.execute("DELETE FROM history WHERE track_id = ?", (track_id,))
                logger.info(f"Removed {cursor.rowcount} history entries for track id {track_id}")
                cursor.execute("DELETE FROM tracks WHERE name = ?", (name,))
                logger.info(f"Removed track from database: {name}")
            except Exception as e:
                logger.error(f"Error processing track {name}: {str(e)}")
        conn.commit()
        logger.debug("Processed tracks marked as deleted")
    except Exception as e:
        logger.error(f"Error in delete_marked_files: {str(e)}")
    finally:
        conn.close()

def manage_radio_shows():
    radio_show_dir = os.getenv('AUDIO_RADIO_SHOW_DIR')
    if not os.path.exists(radio_show_dir):
        logger.warning(f"Directory {radio_show_dir} does not exist")
        return
    radio_show_files = [f for f in os.listdir(radio_show_dir) if f.endswith('.mp3')]
    if len(radio_show_files) <= RADIO_SHOW_LIMIT:
        logger.debug(f"Number of radio shows ({len(radio_show_files)}) is within limit ({RADIO_SHOW_LIMIT})")
        return
    radio_show_files_with_paths = [(f, os.path.join(radio_show_dir, f)) for f in radio_show_files]
    radio_show_files_with_paths.sort(key=lambda x: os.path.getctime(x[1]))
    files_to_delete = radio_show_files_with_paths[RADIO_SHOW_LIMIT:]
    for file_name, file_path in files_to_delete:
        try:
            os.remove(file_path)
            logger.info(f"Deleted old radio show: {file_path}")
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("UPDATE tracks SET status = 'deleted' WHERE name = ? AND track_info = 'radio_show'", (file_name,))
            if cursor.rowcount > 0:
                conn.commit()
                logger.info(f"Marked {file_name} as deleted in database")
            conn.close()
        except Exception as e:
            logger.error(f"Error deleting radio show {file_path}: {str(e)}")

def watch_directory():
    logger.info("Starting track watcher")
    init_db()
    initial_files = set()
    for audio_dir in AUDIO_DIRS:
        if audio_dir is None:
            logger.error(f"Audio directory is None, check .env configuration")
            continue
        if os.path.exists(audio_dir):
            dir_files = set(os.path.join(audio_dir, f) for f in os.listdir(audio_dir) if f.endswith('.mp3'))
            initial_files.update(dir_files)
        else:
            logger.warning(f"Directory {audio_dir} does not exist")
    initial_file_names = set(os.path.basename(f) for f in initial_files)
    logger.info(f"Found {len(initial_file_names)} existing tracks to process")
    for file_path in initial_files:
        mp3_name = os.path.basename(file_path)
        logger.debug(f"Processing initial track: {mp3_name}")
        add_track_to_db(mp3_name, file_path)
    known_files = initial_file_names
    logger.debug(f"Initial known files: {known_files}")
    logger.info("Starting watch directory loop")
    while True:
        try:
            logger.debug(f"Starting new scan cycle at {time.strftime('%Y-%m-%d %H:%M:%S')}")
            current_files = set()
            for audio_dir in AUDIO_DIRS:
                if audio_dir is None:
                    logger.error(f"Audio directory is None, check .env configuration")
                    continue
                if os.path.exists(audio_dir):
                    dir_files = set(os.path.join(audio_dir, f) for f in os.listdir(audio_dir) if f.endswith('.mp3'))
                    current_files.update(dir_files)
                else:
                    logger.warning(f"Directory {audio_dir} does not exist")
            current_file_names = set(os.path.basename(f) for f in current_files)
            logger.debug(f"Current files in directories: {current_file_names}")
            logger.debug(f"Known files before update: {known_files}")
            new_files = current_file_names - known_files
            logger.debug(f"Found {len(new_files)} new files: {new_files}")
            for mp3_name in new_files:
                file_path = next((f for f in current_files if os.path.basename(f) == mp3_name), None)
                if file_path:
                    logger.info(f"Detected new track: {mp3_name}")
                    add_track_to_db(mp3_name, file_path)
            logger.debug("Running sync_db_with_folder")
            sync_db_with_folder(current_file_names)
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as count FROM tracks WHERE status = 'deleted'")
            deleted_count = cursor.fetchone()['count']
            logger.info(f"Found {deleted_count} tracks with status='deleted' before cleanup")
            conn.close()
            logger.debug("Running delete_marked_files")
            delete_marked_files()
            logger.debug("Running manage_radio_shows")
            manage_radio_shows()
            known_files = current_file_names
            logger.debug(f"Updated known files: {known_files}")
            time.sleep(10)
        except Exception as e:
            logger.error(f"Error in watch_directory loop: {str(e)}")
            time.sleep(10)

if __name__ == "__main__":
    watch_directory()