from flask import Flask, jsonify, request, Response
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import sqlite3
import json
import logging
import logging.handlers
import telnetlib
import threading
import time
import os
from dotenv import load_dotenv
import random
from datetime import datetime, timedelta
import pytz
from queue import Queue, Empty
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()

# --- Settings ---
# Telnet settings for Liquidsoap
TELNET_HOST = os.getenv('TELNET_HOST', '127.0.0.1')
TELNET_PORT = int(os.getenv('TELNET_PORT', 1234))

# Directories and files
TRACKS_DIR = os.getenv('TRACKS_DIR', '/home/beasty197/projects/vtrnk_radio/audio/mp3')
CURRENT_TRACK_FILE = os.getenv('CURRENT_TRACK_FILE', '/home/beasty197/projects/vtrnk_radio/data/radio_current_track.txt')
LAST_PLAYED_TRACK_FILE = os.getenv('LAST_PLAYED_TRACK_FILE', '/home/beasty197/projects/vtrnk_radio/data/last_played_track.txt')
PLAYBACK_HISTORY_FILE = os.getenv('PLAYBACK_HISTORY_FILE', '/home/beasty197/projects/vtrnk_radio/data/playback_history.txt')
DB_PATH = os.getenv('DB_PATH', '/home/beasty197/projects/vtrnk_radio/data/radio.db')
LOGS_DIR = os.getenv('LOGS_DIR', '/home/beasty197/projects/vtrnk_radio/logs')
LOG_FILE = os.getenv('LOG_FILE', 'radio_player.log')
UPLOAD_RADIO_DIR = os.getenv('UPLOAD_RADIO_DIR', '/home/beasty197/projects/vtrnk_radio/audio/radio_show')
UPLOAD_TRACK_DIR = os.getenv('UPLOAD_TRACK_DIR', '/home/beasty197/projects/vtrnk_radio/audio/upload_dir')
IMAGES_DIR = os.getenv('IMAGES_DIR', '/home/beasty197/projects/vtrnk_radio/images')

# Playback settings
MAX_HISTORY_SIZE = 60  # Maximum tracks in playback history
PLAYBACK_MODE = "random"  # Playback mode (currently fixed as random)
HISTORY_EXCLUDE_SIZE = 60  # Number of recent tracks to exclude from next track selection
NEXT_TRACK_CANDIDATES = 50  # Number of candidates to select random next track from

# Delay settings
SMART_SKIP_DELAY = 10  # Delay in seconds for smart_skip
RADIO_SHOW_SKIP_DELAY = 10  # Delay in seconds for radio show skip in schedule_checker and play_radio_show

# Title validation settings
MAX_TITLE_LENGTH = 200  # Maximum length for track/set titles
MAX_ARTIST_LENGTH = 100  # Maximum length for artist names

# Styles for normalization
PREDEFINED_STYLES = [
    "Jungle", "Techstep", "Drum & Bass", "Breakbeat", "Liquid Funk", "Neurofunk",
    "Hardstep", "Darkstep", "Ragga Jungle", "Jump Up", "Minimal DnB", "Ambient DnB",
    "Electronic", "Dance", "Blues", "Reggae"
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
    "Ambient DnB": ["ambient drum & bass", "ambient dnb"],
    "Electronic": ["электронная музыка", "electronic", "electro"],
    "Dance": ["dance & dj", "dance & dj/general"],
    "Experimental": ["experimental", "drone", "noise", "ambient", "musique concrète"]
}

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

# Normalize style function
def normalize_style(style):
    if not style:
        return "Unknown"
    style = style.lower().strip()
    if ';' in style:
        style = style.split(';')[0].strip()  # Take first genre
    for canonical, variants in STYLE_VARIANTS.items():
        if style == canonical.lower() or style in [v.lower() for v in variants]:
            return canonical
    return "Unknown" if style not in [s.lower() for s in PREDEFINED_STYLES] else style.title()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})
socketio = SocketIO(app, cors_allowed_origins="*")

# Queue for updates
updates = Queue()

# Logging setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
log_path = os.path.join(LOGS_DIR, LOG_FILE)
handler = logging.handlers.RotatingFileHandler(
    filename=log_path,
    maxBytes=5*1024*1024,
    backupCount=5
)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)

class NoDebugFilter(logging.Filter):
    def filter(self, record):
        return record.levelno > logging.DEBUG
handler.addFilter(NoDebugFilter())
logger.addHandler(handler)

# Playback variables
next_track = None
last_played_track_lock = threading.Lock()

def get_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        logger.error(f"Error connecting to database at {DB_PATH}: {str(e)}")
        raise

def liquidsoap_command(command):
    try:
        command = command.encode('utf-8').decode('utf-8')
        start_time = time.time()
        tn = telnetlib.Telnet(TELNET_HOST, TELNET_PORT)
        tn.write((command + "\n").encode('utf-8'))
        response = tn.read_until(b"\n").decode('utf-8').strip()
        tn.write(b"quit\n")
        tn.close()
        elapsed_time = time.time() - start_time
        logger.info(f"Liquidsoap command '{command}' executed, response: '{response}', time: {elapsed_time:.2f}s")
        return response
    except Exception as e:
        logger.error(f"Error sending command to Liquidsoap: {str(e)}")
        return str(e)

def get_normal_queue_length():
    response = liquidsoap_command("get_normal_queue_length")
    if response:
        try:
            return int(response.split("\n")[0])
        except (ValueError, IndexError):
            logger.error("Failed to parse normal_queue_length")
            return 0
    return 0

def get_current_track():
    try:
        with open(CURRENT_TRACK_FILE, 'r') as f:
            data = json.load(f)
        return data
    except Exception as e:
        logger.error(f"Error reading current track: {str(e)}")
        return {"filename": "", "artist": "VTRNK", "title": "Radio Show"}

def get_last_played_track():
    try:
        with open(LAST_PLAYED_TRACK_FILE, 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.error(f"Error reading last played track: {str(e)}")
        return None

def save_last_played_track(track_path):
    try:
        with open(LAST_PLAYED_TRACK_FILE, 'w') as f:
            f.write(track_path)
        logger.info(f"Saved last played track: {track_path}")
    except Exception as e:
        logger.error(f"Error saving last played track: {str(e)}")

def load_playback_history():
    try:
        if not os.path.exists(PLAYBACK_HISTORY_FILE):
            return []
        with open(PLAYBACK_HISTORY_FILE, 'r') as f:
            history = [line.strip() for line in f.readlines() if line.strip()]
        logger.info(f"Loaded playback history, {len(history)} tracks")
        return history
    except Exception as e:
        logger.error(f"Error loading playback history: {str(e)}")
        return []

def save_playback_history(history):
    try:
        history = history[-MAX_HISTORY_SIZE:]
        with open(PLAYBACK_HISTORY_FILE, 'w') as f:
            for track in history:
                f.write(f"{track}\n")
        logger.info(f"Saved playback history, {len(history)} tracks")
    except Exception as e:
        logger.error(f"Error saving playback history: {str(e)}")

def add_to_playback_history(track_path):
    history = load_playback_history()
    if track_path in history:
        history.remove(track_path)
    history.append(track_path)
    save_playback_history(history)
    logger.info(f"Added track {track_path} to playback history")

def select_next_track():
    try:
        conn = get_db()
        cursor = conn.cursor()
        current_track_data = get_current_track()
        current_track = current_track_data.get('filename', '')
        history = load_playback_history()
        exclude_tracks = history[-HISTORY_EXCLUDE_SIZE:]
        if current_track and current_track not in exclude_tracks:
            exclude_tracks.append(current_track)
        placeholders = ','.join(['?'] * len(exclude_tracks))
        exclude_condition = f"path NOT IN ({placeholders})" if exclude_tracks else "1=1"
        cursor.execute(f"""
            SELECT path, playcount, upload_date
            FROM tracks
            WHERE {exclude_condition} AND status = 'available' AND track_info = 'track'
            ORDER BY playcount ASC, upload_date DESC
            LIMIT {NEXT_TRACK_CANDIDATES}
        """, exclude_tracks)
        candidates = [dict(row) for row in cursor.fetchall()]
        conn.close()
        if not candidates:
            logger.warning("No eligible tracks found for selection, excluded tracks: %s", exclude_tracks)
            return None
        selected_track = random.choice(candidates)
        logger.info(f"Selected next track: {repr(selected_track['path'])}, playcount={selected_track['playcount']}, upload_date={selected_track['upload_date']}")
        return selected_track['path']
    except Exception as e:
        logger.error(f"Error selecting next track: {str(e)}")
        return None

def increment_play_count(track_path):
    if not track_path:
        logger.warning("Cannot increment playcount: track_path is empty")
        return
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT playcount FROM tracks WHERE path = ?", (track_path,))
        track = cursor.fetchone()
        if not track:
            logger.warning(f"Track {track_path} does not exist in the database, cannot increment playcount")
            conn.close()
            return
        cursor.execute("UPDATE tracks SET playcount = playcount + 1 WHERE path = ?", (track_path,))
        affected_rows = cursor.rowcount
        conn.commit()
        conn.close()
        if affected_rows == 0:
            logger.warning(f"No track found with path {track_path} to increment playcount")
        else:
            logger.info(f"Incremented playcount for track {track_path}, affected rows: {affected_rows}")
    except Exception as e:
        logger.error(f"Error incrementing playcount for track {str(e)}")

def add_track_to_queue():
    queue_length = get_normal_queue_length()
    if queue_length < 2:
        track_path = select_next_track()
        if track_path:
            global next_track
            next_track = track_path
            response = liquidsoap_command(f"set_next_track {track_path}")
            logger.info(f"Added track to normal_queue: {track_path}, response: {response}")
        else:
            logger.error("No track selected for normal_queue")

def skip_track():
    response = liquidsoap_command("skip_track")
    logger.info(f"Skipped track, response: {response}")
    return response

def smart_skip():
    try:
        logger.info("Starting smart skip process")
        add_track_to_queue()
        logger.info(f"Added next track, waiting {SMART_SKIP_DELAY} seconds")
        time.sleep(SMART_SKIP_DELAY)
        skip_track()
        logger.info("Smart skip completed successfully")
        return {"success": True, "message": "Smart skip executed successfully"}
    except Exception as e:
        logger.error(f"Error in smart_skip: {str(e)}")
        return {"success": False, "error": str(e)}

def get_track_duration(track_path):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT duration FROM tracks WHERE path = ?", (track_path,))
        track = cursor.fetchone()
        conn.close()
        if track and track['duration']:
            logger.info(f"Found duration for {track_path}: {track['duration']}s")
            return track['duration']
        logger.warning(f"No duration found for track {track_path}")
        return None
    except Exception as e:
        logger.error(f"Error fetching duration for track {track_path}: {str(e)}")
        return None

def get_track_metadata(track_path):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT artist, track_title, name FROM tracks WHERE path = ?", (track_path,))
        track = cursor.fetchone()
        conn.close()
        if track:
            artist = track['artist'] if track['artist'] and track['artist'].strip() else "VTRNK"
            title = track['track_title'] if track['track_title'] and track['track_title'].strip() else (track['name'] if track['name'] and track['name'].strip() else "Radio Show")
            logger.info(f"Found metadata for {track_path}: artist={artist}, title={title}")
            return artist, title
        logger.warning(f"No metadata found for track {track_path}")
        return "VTRNK", "Radio Show"
    except Exception as e:
        logger.error(f"Error fetching metadata for track {track_path}: {str(e)}")
        return "VTRNK", "Radio Show"

@app.route('/track_started', methods=['POST'])
def track_started():
    try:
        data = request.get_json()
        track_path = data.get('filename')
        if track_path:
            increment_play_count(track_path)
            add_to_playback_history(track_path)
            save_last_played_track(track_path)
            logger.info(f"Track started: {track_path}, playcount incremented")
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error in track_started: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/track', methods=['GET', 'POST'])
def handle_track():
    if request.method == 'POST':
        try:
            data = request.get_json()
            artist_from_request = data.get('artist', 'Unknown Artist')
            title_from_request = data.get('title', 'Unknown Title')
            filename = data.get('filename', 'Unknown File')
            artist, title = get_track_metadata(filename)
            normal_queue_length = data.get('normal_queue_length', 0)
            special_queue_length = data.get('special_queue_length', 0)
            timestamp = data.get('timestamp', 'Unknown Timestamp')
            special_queue_timestamp = data.get('special_queue_timestamp', '')
            normal_queue_timestamp = data.get('normal_queue_timestamp', '')
            track_queue_timestamp = data.get('track_queue_timestamp', '')
            queue = data.get('queue', 'unknown')
            current_track_json = {
                'filename': filename,
                'artist': artist,
                'title': title,
                'album': 'Radio VTRNK Stream',
                'normal_queue_length': normal_queue_length,
                'special_queue_length': special_queue_length,
                'timestamp': timestamp,
                'special_queue_timestamp': special_queue_timestamp,
                'normal_queue_timestamp': normal_queue_timestamp,
                'track_queue_timestamp': track_queue_timestamp,
                'queue': queue
            }
            with open(CURRENT_TRACK_FILE, 'w') as f:
                json.dump(current_track_json, f)
            last_played = get_last_played_track()
            if last_played != filename:
                logger.info(f"Received and saved track metadata: artist={artist}, title={title}, filename={filename}, queue={queue}")
                increment_play_count(filename)
                add_to_playback_history(filename)
                save_last_played_track(filename)
                add_track_to_queue()
            socketio.emit('track_update', current_track_json)
            if data.get('queue') == 'special':
                try:
                    conn = get_db()
                    cursor = conn.cursor()
                    cursor.execute("UPDATE schedule SET queued = 0 WHERE track_path = ? AND queued = 1", (data.get('filename'),))
                    affected_rows = cursor.rowcount
                    conn.commit()
                    conn.close()
                    if affected_rows > 0:
                        logger.info(f"Cleared queued=0 for started special track: {data.get('filename')}")
                except Exception as e:
                    logger.error(f"Error clearing queued in handle_track: {str(e)}")
            return jsonify({'success': True})
        except Exception as e:
            logger.error(f"Error in handle_track (POST): {str(e)}")
            return jsonify({'error': str(e)}), 500
    else:
        try:
            with open(CURRENT_TRACK_FILE, 'r') as f:
                data = json.load(f)
            return jsonify([
                ["filename", data.get("filename", "Unknown File")],
                ["artist", data.get("artist", "Unknown Artist")],
                ["title", data.get("title", "Unknown Title")],
                ["album", data.get("album", "Radio VTRNK Stream")]
            ])
        except Exception as e:
            logger.error(f"Error in handle_track (GET): {str(e)}")
            return jsonify([
                ["filename", "Unknown File"],
                ["artist", "Unknown Artist"],
                ["title", "Unknown Title"],
                ["album", "Radio VTRNK Stream"]
            ]), 500

@app.route('/track_added_special', methods=['POST'])
def track_added_special():
    try:
        data = request.get_json()
        filename = data.get('filename', 'Unknown File')
        track_type = data.get('type', 'Unknown Type')
        queue = data.get('queue', 'special')
        track_added_json = {
            'filename': filename,
            'type': track_type,
            'queue': queue,
            'timestamp': datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        }
        logger.info(f"Track added to special queue: filename={filename}, type={track_type}, queue={queue}")
        socketio.emit('track_added_special', track_added_json)
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error in track_added_special: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/track_added_normal', methods=['POST'])
def track_added_normal():
    try:
        data = request.get_json()
        filename = data.get('filename', 'Unknown File')
        track_type = data.get('type', 'Unknown Type')
        queue = data.get('queue', 'normal')
        track_added_json = {
            'filename': filename,
            'type': track_type,
            'queue': queue,
            'timestamp': datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        }
        logger.info(f"Track added to normal queue: filename={filename}, type={track_type}, queue={queue}")
        socketio.emit('track_added_normal', track_added_json)
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error in track_added_normal: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/update_show', methods=['POST'])
def update_show():
    try:
        data = request.form
        track_path = data.get('track_path')
        new_artist = data.get('new_artist')
        new_title = data.get('new_title')
        new_style = data.get('new_style')
        if not track_path:
            logger.warning("Missing track_path in update_show request")
            return jsonify({'success': False, 'error': 'Missing track_path'}), 400
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM tracks WHERE path = ?", (track_path,))
        if not cursor.fetchone():
            logger.warning(f"No show found with path {track_path}")
            conn.close()
            return jsonify({'success': False, 'error': f"No show found with path {track_path}"}), 404
        update_query = "UPDATE tracks SET "
        update_params = []
        if new_artist:
            new_artist = validate_artist_length(new_artist)
            update_query += "artist = ?, "
            update_params.append(new_artist)
        if new_title:
            new_title = validate_title_length(new_title)
            update_query += "title = ?, "
            update_params.append(new_title)
        if new_style:
            normalized_style = normalize_style(new_style)
            update_query += "style = ?, "
            update_params.append(normalized_style)
        if 'coverFile' in request.files:
            cover_file = request.files['coverFile']
            if cover_file.filename != '':
                if not cover_file.filename.lower().endswith(('.jpg', '.jpeg', '.png')):
                    logger.warning("Invalid cover file format")
                    return jsonify({'success': False, 'error': 'Invalid cover file format. Only JPG, JPEG, PNG allowed.'}), 400
                cover_dir = os.path.join(IMAGES_DIR, 'show_covers')
                os.makedirs(cover_dir, exist_ok=True)
                cover_path = os.path.join(cover_dir, cover_file.filename)
                cover_file.save(cover_path)
                logger.info(f"Saved cover file to {cover_path}")
                update_query += "path_img = ?, "
                update_params.append('/images/show_covers/' + cover_file.filename)
        if update_params:
            update_query = update_query.rstrip(', ') + " WHERE path = ?"
            update_params.append(track_path)
            cursor.execute(update_query, update_params)
            affected_rows = cursor.rowcount
            conn.commit()
            if affected_rows > 0:
                logger.info(f"Updated show for path {track_path}")
                conn.close()
                return jsonify({'success': True})
            else:
                logger.warning(f"No show found with path {track_path} after update attempt")
                conn.close()
                return jsonify({'success': False, 'error': f"No show found with path {track_path}"}), 404
        else:
            conn.close()
            return jsonify({'success': False, 'error': 'No updates provided'}), 400
    except Exception as e:
        logger.error(f"Error in update_show: {str(e)}")
        try:
            conn.close()
        except:
            pass
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/upload_radio_show', methods=['POST'])
def upload_radio_show():
    try:
        if 'radioFile' not in request.files:
            return "Нет файла", 400
        file = request.files['radioFile']
        if file.filename == '':
            return "Файл не выбран", 400
        if not file.filename.lower().endswith('.mp3'):
            return "Допустим только формат MP3", 400
        file_path = os.path.join(UPLOAD_RADIO_DIR, file.filename)
        os.makedirs(UPLOAD_RADIO_DIR, exist_ok=True)
        file.save(file_path)
        logger.info(f"Uploaded radio show: {file.filename} to {file_path}")
        return "Радио-шоу успешно загружено", 200
    except Exception as e:
        logger.error(f"Error in upload_radio_show: {str(e)}")
        return f"Ошибка загрузки: {str(e)}", 500

@app.route('/delete_radio_show', methods=['POST'])
def delete_radio_show():
    try:
        data = request.get_json()
        track_path = data.get('track_path')
        if not track_path:
            logger.warning("Missing track_path in delete_radio_show request")
            return jsonify({'success': False, 'error': 'Missing track_path'}), 400
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tracks WHERE path = ? AND track_info = 'radio_show'", (track_path,))
        affected_rows = cursor.rowcount
        conn.commit()
        conn.close()
        if affected_rows == 0:
            logger.warning(f"No radio show found with path {track_path}")
            return jsonify({'success': False, 'error': f"No radio show found with path {track_path}"}), 404
        if os.path.exists(track_path):
            os.remove(track_path)
            logger.info(f"Deleted radio show file: {track_path}")
        else:
            logger.warning(f"File not found on disk: {track_path}")
        return jsonify({'success': True, 'message': f"Radio show {track_path} deleted successfully"})
    except Exception as e:
        logger.error(f"Error in delete_radio_show: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/upload_track', methods=['POST'])
def upload_track():
    try:
        if 'trackFile' not in request.files:
            return "Нет файла", 400
        file = request.files['trackFile']
        if file.filename == '':
            return "Файл не выбран", 400
        valid_extensions = {'.mp3', '.wav', '.flac'}
        if not any(file.filename.lower().endswith(ext) for ext in valid_extensions):
            return "Недопустимый формат файла", 400
        file_path = os.path.join(UPLOAD_TRACK_DIR, file.filename)
        file.save(file_path)
        return "Файл успешно загружен", 200
    except Exception as e:
        logger.error(f"Error in upload_track: {str(e)}")
        return f"Ошибка загрузки: {str(e)}", 500

@app.route('/db_schema', methods=['GET'])
def get_db_schema():
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        schema = {}
        for table in tables:
            table_name = table['name']
            cursor.execute(f"PRAGMA table_info({table_name})")
            columns = cursor.fetchall()
            schema[table_name] = [{'name': col['name'], 'type': col['type']} for col in columns]
        conn.close()
        logger.info(f"Fetched database schema: {schema}")
        return jsonify(schema), 200
    except Exception as e:
        logger.error(f"Error fetching database schema: {str(e)}")
        return jsonify({'error': str(e)}), 500

@socketio.on('connect')
def handle_connect():
    logger.info("WebSocket client connected")
    emit('track_update', {'message': 'WebSocket connection established'})

@socketio.on('disconnect')
def handle_disconnect():
    logger.info("WebSocket client disconnected")

def get_special_queue_contents():
    try:
        response = liquidsoap_command("get_special_queue_contents")
        logger.info(f"Special queue contents: {response}")
        return response
    except Exception as e:
        logger.error(f"Error getting special queue contents: {str(e)}")
        return ""

def skip_normal_queue():
    try:
        response = liquidsoap_command("skip_normal")
        logger.info(f"Skipped normal queue track, response: {response}")
        return response
    except Exception as e:
        logger.error(f"Error skipping normal queue: {str(e)}")
        return str(e)

def schedule_checker():
    last_played = None
    conn = get_db()
    cursor = conn.cursor()
    while True:
        try:
            msk_tz = pytz.timezone('Europe/Moscow')
            current_time = datetime.now(msk_tz)
            if current_time.second % 10 != 0:
                time.sleep(1)
                continue
            current_time_rounded = current_time.replace(second=0, microsecond=0)
            current_time_str = current_time_rounded.strftime('%Y-%m-%dT%H:%M')
            logger.info(f"Checking schedule at {current_time_str}")
            cursor.execute("SELECT * FROM schedule WHERE enabled = 1 AND queued = 0")
            schedule = [dict(row) for row in cursor.fetchall()]
            for entry in schedule:
                try:
                    try:
                        scheduled_time = datetime.strptime(entry['start_time'], '%Y-%m-%dT%H:%M').replace(tzinfo=msk_tz)
                    except ValueError:
                        scheduled_time = datetime.strptime(entry['start_time'], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=msk_tz)
                    scheduled_time_str = scheduled_time.strftime('%Y-%m-%dT%H:%M')
                    window_end = scheduled_time + timedelta(minutes=5)
                    window_end_str = window_end.strftime('%Y-%m-%dT%H:%M')
                    logger.info(f"Schedule entry: id={entry['id']}, track_path={entry['track_path']}, start_time={scheduled_time_str}, current_time={current_time_str}, window_end={window_end_str}")
                    if current_time_str >= scheduled_time_str and current_time_str <= window_end_str:
                        success = False
                        for attempt in range(1, 4):
                            logger.info(f"Attempt {attempt}/3 to add show {entry['track_path']} to special_queue")
                            response = liquidsoap_command(f"play_radio_show {entry['track_path']}")
                            time.sleep(RADIO_SHOW_SKIP_DELAY)
                            skip_response = skip_normal_queue()
                            logger.info(f"Skipped normal queue after {RADIO_SHOW_SKIP_DELAY}s delay, response: {skip_response}")
                            time.sleep(55)
                            current_track_data = get_current_track()
                            current_filename = current_track_data.get('filename', '')
                            special_contents = get_special_queue_contents()
                            if current_filename == entry['track_path']:
                                logger.info(f"Success on attempt {attempt}: Show {entry['track_path']} is playing")
                                success = True
                                break
                            elif entry['track_path'] in special_contents.split(','):
                                logger.info(f"Success on attempt {attempt}: Show {entry['track_path']} in special_queue")
                                success = True
                                break
                            else:
                                logger.warning(f"Retry: Show not found, special_contents={special_contents}, current_filename={current_filename}")
                        if success:
                            cursor.execute("UPDATE schedule SET queued = 1, enabled = 0 WHERE id = ?", (entry['id'],))
                            conn.commit()
                            logger.info(f"Marked as queued=1 and enabled=0 for schedule entry id={entry['id']}, track={entry['track_path']}")
                        else:
                            logger.error(f"Failed to add show {entry['track_path']} after 3 attempts")
                    else:
                        logger.info(f"Condition not met for entry id={entry['id']}")
                except ValueError as e:
                    logger.error(f"Invalid start_time for entry {entry['id']}: {str(e)}")
                    continue
            time.sleep(1)
        except Exception as e:
            logger.error(f"Error in schedule_checker: {str(e)}")
            try:
                conn.close()
            except:
                pass
            try:
                conn = get_db()
                cursor = conn.cursor()
            except Exception as db_err:
                logger.error(f"Failed to reconnect to database: {str(db_err)}")
            time.sleep(10)
    try:
        conn.close()
    except:
        pass

threading.Thread(target=schedule_checker, daemon=True).start()
scheduler = AsyncIOScheduler()
scheduler.add_job(add_track_to_queue, "interval", seconds=10)
logger.info("Starting scheduler for add_track_to_queue every 10 seconds")
scheduler.start()
logger.info("Scheduler started")
add_track_to_queue()
logger.info("Starting radio player, initializing Flask server")

def reset_play_counts():
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE tracks SET playcount = 0")
        affected_rows = cursor.rowcount
        conn.commit()
        conn.close()
        logger.info(f"Reset play counts for {affected_rows} tracks")
        return {"success": True, "message": f"Reset play counts for {affected_rows} tracks"}
    except Exception as e:
        logger.error(f"Error resetting play counts: {str(e)}")
        return {"success": False, "error": str(e)}

def fetch_cover_path():
    try:
        with open(CURRENT_TRACK_FILE, 'r') as f:
            data = json.load(f)
        filename = data.get('filename', '')
        if not filename:
            logger.warning("No filename found in JSON")
            return "/images/placeholder2.png"
        static_cover = getattr(fetch_cover_path, 'static_cover', None)
        if static_cover and static_cover['filename'] == filename:
            return static_cover['cover_path']
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT path_img FROM tracks WHERE path = ?", (filename,))
        track = cursor.fetchone()
        conn.close()
        cover_path = track['path_img'] if track and track['path_img'] else "/images/placeholder2.png"
        logger.debug(f"Found cover for {filename}: {cover_path}")
        fetch_cover_path.static_cover = {'filename': filename, 'cover_path': cover_path}
        return cover_path
    except Exception as e:
        logger.error(f"Error fetching cover path: {str(e)}")
        return "/images/placeholder2.png"

@app.route('/get_next_track', methods=['GET'])
def get_next_track_endpoint():
    global next_track
    try:
        if not next_track:
            logger.warning("No next track available")
            return jsonify({"next_track": "", "cover_path": "/images/placeholder2.png"}), 200
        static_next_track = getattr(get_next_track_endpoint, 'static_next_track', None)
        if static_next_track and static_next_track['next_track'] == next_track:
            return jsonify({"next_track": next_track, "cover_path": static_next_track['cover_path']})
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT path_img FROM tracks WHERE path = ?", (next_track,))
        track = cursor.fetchone()
        conn.close()
        cover_path = track['path_img'] if track and track['path_img'] else "/images/placeholder2.png"
        logger.debug(f"Returning next track: {next_track}, cover: {cover_path}")
        get_next_track_endpoint.static_next_track = {'next_track': next_track, 'cover_path': cover_path}
        return jsonify({"next_track": next_track, "cover_path": cover_path})
    except Exception as e:
        logger.error(f"Error in get_next_track_endpoint: {str(e)}")
        return jsonify({"next_track": "", "cover_path": "/images/placeholder2.png"}), 500

@app.route('/test', methods=['GET'])
def test_endpoint():
    logger.info("Test endpoint accessed")
    return jsonify({"message": "Test endpoint works!"})

@app.route('/reset_play_counts', methods=['POST'])
def reset_play_counts_endpoint():
    try:
        result = reset_play_counts()
        if result["success"]:
            return jsonify(result), 200
        else:
            return jsonify(result), 500
    except Exception as e:
        logger.error(f"Error in reset_play_counts_endpoint: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/tracks', methods=['GET'])
def get_tracks():
    try:
        conn = get_db()
        cursor = conn.cursor()
        if 'schedule' in request.url:
            cursor.execute("SELECT * FROM tracks WHERE status = 'available' AND track_info = 'radio_show' ORDER BY upload_date DESC")
        else:
            cursor.execute("SELECT * FROM tracks WHERE status = 'available' ORDER BY upload_date DESC")
        tracks = [dict(row) for row in cursor.fetchall()]
        conn.close()
        logger.info(f"Fetched {len(tracks)} tracks")
        return jsonify(tracks)
    except Exception as e:
        logger.error(f"Error in get_tracks: {str(e)}")
        return jsonify([]), 500

@app.route('/track_duration', methods=['POST'])
def get_track_duration_endpoint():
    try:
        data = request.get_json()
        track_name = data.get('track_name')
        if not track_name:
            logger.warning("Missing track_name in track_duration request")
            return jsonify({'error': 'Missing track_name'}), 400
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT duration FROM tracks WHERE name = ?", (track_name,))
        track = cursor.fetchone()
        conn.close()
        if track and track['duration']:
            logger.info(f"Found duration for {track_name}: {track['duration']}")
            return jsonify({'duration': track['duration']})
        else:
            logger.warning(f"No duration found for {track_name}")
            return jsonify({'error': f"No duration found for {track_name}"}), 404
    except Exception as e:
        logger.error(f"Error in get_track_duration: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/styles', methods=['GET'])
def get_styles():
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT style, COUNT(*) as count FROM tracks WHERE status = 'available' GROUP BY style")
        styles = [{'style': row['style'] or 'Unknown', 'count': row['count']} for row in cursor.fetchall()]
        conn.close()
        logger.info(f"Fetched {len(styles)} styles")
        return jsonify({'styles': styles})
    except Exception as e:
        logger.error(f"Error in get_styles: {str(e)}")
        return jsonify({'styles': []}), 500

@app.route('/update_style', methods=['POST'])
def update_style():
    try:
        data = request.get_json()
        track_name = data.get('track_name')
        new_style = data.get('style')
        if not track_name or not new_style:
            logger.warning("Missing track_name or style in update_style request")
            return jsonify({'error': 'Missing track_name or style'}), 400
        normalized_style = normalize_style(new_style)
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE tracks SET style = ? WHERE name = ?", (normalized_style, track_name))
        affected_rows = cursor.rowcount
        conn.commit()
        conn.close()
        if affected_rows == 0:
            logger.warning(f"No track found with name {track_name}")
            return jsonify({'error': f"No track found with name {track_name}"}), 404
        logger.info(f"Updated style for track {track_name} to {normalized_style}")
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error in update_style: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/update_track_info', methods=['POST'])
def update_track_info():
    try:
        data = request.get_json()
        track_id = data.get('track_id')
        new_track_info = data.get('track_info')
        if not track_id or not new_track_info:
            logger.warning("Missing track_id or track_info in update_track_info request")
            return jsonify({'error': 'Missing track_id or track_info'}), 400
        valid_types = ['track', 'jingle', 'radio_show']
        if new_track_info not in valid_types:
            logger.warning(f"Invalid track_info value: {new_track_info}")
            return jsonify({'error': f"Invalid track_info value. Must be one of {valid_types}"}), 400
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE tracks SET track_info = ? WHERE id = ?", (new_track_info, track_id))
        affected_rows = cursor.rowcount
        conn.commit()
        conn.close()
        if affected_rows == 0:
            logger.warning(f"No track found with id {track_id}")
            return jsonify({'error': f"No track found with id {track_id}"}), 404
        logger.info(f"Updated track_info for track id {track_id} to {new_track_info}")
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error in update_track_info: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/get_cover_path')
def get_cover_path_endpoint():
    try:
        cover_path = fetch_cover_path()
        return jsonify({"cover_path": cover_path})
    except Exception as e:
        logger.error(f"Error in get_cover_path_endpoint: {str(e)}")
        return jsonify({'cover_path': "/images/placeholder2.png"}), 500

@app.route('/schedule', methods=['GET'])
def get_schedule():
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM schedule")
        schedule = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return jsonify(schedule)
    except Exception as e:
        logger.error(f"Error in get_schedule: {str(e)}")
        return jsonify([]), 500

@app.route('/schedule_play', methods=['POST'])
def schedule_play():
    try:
        data = request.get_json()
        track_path = data.get('track_path')
        scheduled_time = data.get('scheduled_time')
        if not track_path or not scheduled_time:
            return jsonify({'error': 'Missing track_path or scheduled_time'}), 400
        try:
            parsed_time = datetime.strptime(scheduled_time, '%Y-%m-%dT%H:%M:%S')
            scheduled_time = parsed_time.strftime('%Y-%m-%dT%H:%M')
        except ValueError:
            pass
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO schedule (track_path, start_time, enabled) VALUES (?, ?, 1)",
                      (track_path, scheduled_time))
        conn.commit()
        conn.close()
        logger.info(f"Scheduled radio show {track_path} for {scheduled_time}")
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error scheduling play: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/schedule/delete/<int:id>', methods=['DELETE'])
def delete_schedule(id):
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM schedule WHERE id = ?", (id,))
        conn.commit()
        conn.close()
        logger.info(f"Deleted schedule entry with id {id}")
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error in delete_schedule: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/play_radio_show', methods=['POST'])
def play_radio_show():
    try:
        data = request.get_json()
        track_path = data.get('track_path')
        if not track_path:
            logger.warning("Missing track_path in play_radio_show request")
            return jsonify({'error': 'Missing track_path'}), 400
        with last_played_track_lock:
            current_track = get_current_track().get('filename', '')
            if current_track == track_path:
                logger.warning(f"Attempted to play the same track {track_path} twice consecutively")
                return jsonify({'error': 'Cannot play the same track twice consecutively'}), 400
            response = liquidsoap_command(f"play_radio_show {track_path}")
            time.sleep(RADIO_SHOW_SKIP_DELAY)
            logger.info(f"Sent to Liquidsoap: play_radio_show {track_path}, response: {response}")
            skip_response = skip_normal_queue()
            logger.info(f"Skipped normal queue after manual show play, response: {skip_response}")
            artist, title = get_track_metadata(track_path)
            save_last_played_track(track_path)
            add_track_to_queue()
            return jsonify({
                'success': True,
                'response': response,
                'skip_response': skip_response,
                'track_path': track_path,
                'artist': artist,
                'title': title
            })
    except Exception as e:
        logger.error(f"Error in play_radio_show: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/play_jingle', methods=['POST'])
def play_jingle():
    try:
        data = request.get_json()
        jingle_path = data.get('jingle_path')
        if not jingle_path:
            logger.warning("Missing jingle_path in play_jingle request")
            return jsonify({'error': 'Missing jingle_path'}), 400
        response = liquidsoap_command(f"play_jingle {jingle_path}")
        logger.info(f"Sent to Liquidsoap: play_jingle {jingle_path}, response: {response}")
        return jsonify({
            'success': True,
            'response': response,
            'jingle_path': jingle_path
        })
    except Exception as e:
        logger.error(f"Error in play_jingle: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/add_track_to_queue', methods=['POST'])
def add_track_to_queue_endpoint():
    try:
        add_track_to_queue()
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error adding track to queue: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/smart_skip', methods=['POST'])
def smart_skip_endpoint():
    try:
        logger.info("Received smart_skip request")
        result = smart_skip()
        if result["success"]:
            logger.info("Smart skip executed successfully")
            return jsonify(result)
        else:
            logger.error(f"Smart skip failed: {result['error']}")
            return jsonify(result), 500
    except Exception as e:
        logger.error(f"Error in smart_skip_endpoint: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/skip_track', methods=['POST'])
def skip_track_endpoint():
    try:
        response = skip_track()
        add_track_to_queue()
        return jsonify({'success': True, 'response': response})
    except Exception as e:
        logger.error(f"Error skipping track: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/play_playlist', methods=['POST'])
def play_playlist():
    try:
        response = liquidsoap_command("play_playlist")
        add_track_to_queue()
        return jsonify({'success': True, 'response': response})
    except Exception as e:
        logger.error(f"Error in play_playlist: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    logger.info("Starting radio player, initializing Flask server...")
    from gevent.pywsgi import WSGIServer
    from geventwebsocket.handler import WebSocketHandler
    http_server = WSGIServer(('0.0.0.0', 5001), app, handler_class=WebSocketHandler)
    http_server.serve_forever()