# /home/beasty197/projects/vtrnk_radio/scripts/schedule_server.py
# Отдельный сервер для расписания на порту 5005

import sqlite3  # Pylance fix: стандартный модуль
import os
import logging
import json
import secrets
from datetime import datetime
from functools import wraps
from flask import Flask, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename
from schedule_db import (
    get_all_programs, add_program, update_program, delete_program, init_db,
    get_program, set_program_air, clear_program_timer, clear_program_file,
    count_programs_with_track,
)
from dotenv import load_dotenv

# Загружаем .env
load_dotenv('/home/beasty197/projects/vtrnk_radio/.env')

app = Flask(__name__)
MAX_PODCAST_BYTES = 300 * 1024 * 1024
app.config['MAX_CONTENT_LENGTH'] = MAX_PODCAST_BYTES

# Настройка логирования (в терминал + файл)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # в терминал
        logging.FileHandler('/home/beasty197/projects/vtrnk_radio/logs/schedule_server.log')
    ]
)
logger = logging.getLogger(__name__)

# Папка для афиш
COVERS_DIR = '/home/beasty197/projects/vtrnk_radio/images/schedule_covers'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}
RADIO_DB_PATH = '/home/beasty197/projects/vtrnk_radio/data/radio.db'
RADIO_SHOW_DIR = '/home/beasty197/projects/vtrnk_radio/audio/radio_show'
PLACEHOLDER_POSTER = '/images/placeholder2.png'

# ==================== RESTRIM ====================
RESTRIM_SETTINGS_FILE = '/home/beasty197/projects/vtrnk_radio/data/restrim_settings.json'
RESTRIM_ADMIN_PASSWORD = os.getenv('RESTRIM_ADMIN_PASSWORD')
valid_tokens = set()


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def allowed_podcast(filename):
    return bool(filename) and filename.lower().endswith('.mp3')


def get_radio_db():
    conn = sqlite3.connect(RADIO_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def normalize_start_time(start_time):
    if not start_time:
        return start_time
    raw = start_time.strip()
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M'):
        try:
            return datetime.strptime(raw, fmt).strftime('%Y-%m-%dT%H:%M')
        except ValueError:
            continue
    return raw[:16]


def poster_for_track(program):
    url = (program.get('poster_url') or '').strip()
    return url if url else PLACEHOLDER_POSTER


def upsert_track_from_program(track_path, program):
    """Пишет карточку программы в radio.db.tracks — это читает бот и /track."""
    title = (program.get('name') or '').strip() or 'Radio Show'
    artist = (program.get('author') or '').strip() or 'VTRNK'
    description = (program.get('description') or '').strip()
    poster = poster_for_track(program)
    name = os.path.basename(track_path)
    upload_date = datetime.now().isoformat()
    conn = get_radio_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT path FROM tracks WHERE path = ?", (track_path,))
        if cur.fetchone():
            cur.execute(
                """UPDATE tracks SET
                       name = ?, track_title = ?, artist = ?, description = ?,
                       path_img = ?, track_info = 'radio_show', status = 'available'
                   WHERE path = ?""",
                (name, title, artist, description, poster, track_path),
            )
        else:
            cur.execute(
                """INSERT INTO tracks (
                       path, name, track_title, artist, style, track_info,
                       description, path_img, upload_date, status
                   ) VALUES (?, ?, ?, ?, 'Unknown', 'radio_show', ?, ?, ?, 'available')""",
                (track_path, name, title, artist, description, poster, upload_date),
            )
        conn.commit()
    finally:
        conn.close()
    logger.info(f"tracks upsert path={track_path} title={title!r} artist={artist!r}")


def delete_timer_slot(timer_id):
    if not timer_id:
        return
    conn = get_radio_db()
    try:
        conn.execute("DELETE FROM schedule WHERE id = ?", (timer_id,))
        conn.commit()
        logger.info(f"Deleted radio.db schedule id={timer_id}")
    finally:
        conn.close()


def create_timer_slot(track_path, start_time, old_timer_id=None):
    start_time = normalize_start_time(start_time)
    conn = get_radio_db()
    try:
        cur = conn.cursor()
        if old_timer_id:
            cur.execute("DELETE FROM schedule WHERE id = ?", (old_timer_id,))
        cur.execute(
            "INSERT INTO schedule (track_path, start_time, enabled, queued, repeat_daily) VALUES (?, ?, 1, 0, 0)",
            (track_path, start_time),
        )
        timer_id = cur.lastrowid
        conn.commit()
        logger.info(f"Created radio.db schedule id={timer_id} at {start_time}")
        return timer_id
    finally:
        conn.close()


def count_other_timer_slots(track_path, except_timer_id=None):
    if not track_path:
        return 0
    conn = get_radio_db()
    try:
        cur = conn.cursor()
        if except_timer_id:
            cur.execute(
                "SELECT COUNT(*) FROM schedule WHERE track_path = ? AND id != ?",
                (track_path, except_timer_id),
            )
        else:
            cur.execute("SELECT COUNT(*) FROM schedule WHERE track_path = ?", (track_path,))
        return cur.fetchone()[0]
    finally:
        conn.close()


def remove_track_if_unused(track_path, except_program_id=None, except_timer_id=None):
    if not track_path:
        return
    if count_programs_with_track(track_path, except_id=except_program_id) > 0:
        return
    if count_other_timer_slots(track_path, except_timer_id=except_timer_id) > 0:
        return
    conn = get_radio_db()
    try:
        conn.execute("DELETE FROM tracks WHERE path = ? AND track_info = 'radio_show'", (track_path,))
        conn.commit()
    finally:
        conn.close()
    if os.path.isfile(track_path):
        try:
            os.remove(track_path)
            logger.info(f"Removed unused podcast file: {track_path}")
        except OSError as e:
            logger.error(f"Failed to remove {track_path}: {e}")


def sync_attached_track(program):
    track_path = program.get('track_path')
    if not track_path:
        return
    try:
        upsert_track_from_program(track_path, program)
    except Exception as e:
        logger.error(f"sync_attached_track failed for {track_path}: {e}")


def save_podcast_file(file_storage):
    filename = secure_filename(file_storage.filename or '')
    if not allowed_podcast(filename):
        raise ValueError('Нужен файл MP3')
    os.makedirs(RADIO_SHOW_DIR, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    save_name = f"{timestamp}_{filename}"
    save_path = os.path.join(RADIO_SHOW_DIR, save_name)
    file_storage.save(save_path)
    os.chmod(save_path, 0o644)
    logger.info(f"Saved podcast file: {save_path}")
    return save_path


def collect_form_program():
    return {
        'name': request.form.get('name'),
        'start_time': request.form.get('start_time'),
        'duration_minutes': int(request.form.get('duration_minutes', 0)),
        'program_type': request.form.get('program_type'),
        'custom_type': request.form.get('custom_type') or None,
        'description': request.form.get('description'),
        'author': request.form.get('author'),
        'social_links': request.form.get('social_links'),
        'poster_url': request.form.get('poster_url'),
    }


def apply_poster_upload(data):
    if 'poster' not in request.files:
        return data
    file = request.files['poster']
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        save_filename = f"{timestamp}_{filename}"
        save_path = os.path.join(COVERS_DIR, save_filename)
        file.save(save_path)
        data['poster_url'] = f'/images/schedule_covers/{save_filename}'
        logger.info(f"Новый файл афиши сохранён: {data['poster_url']}")
    else:
        logger.warning("Неверный файл афиши, игнорируем")
    return data


def air_required_missing(program):
    missing = []
    if not (program.get('name') or '').strip():
        missing.append('название')
    if not (program.get('author') or '').strip():
        missing.append('автор')
    if not (program.get('start_time') or '').strip():
        missing.append('дата и время')
    return missing


@app.before_first_request
def before_first_request():
    init_db()
    logger.info("Schedule server started. Database initialized.")


@app.route('/')
def home():
    logger.info("Home endpoint accessed")
    return "Schedule server v1.0 running on port 5005"


@app.route('/next-show', methods=['GET'])
def api_get_programs():
    try:
        programs = get_all_programs()
        logger.info(f"GET /next-show: returned {len(programs)} programs")
        return jsonify(programs)
    except Exception as e:
        logger.error(f"GET /next-show error: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/next-show', methods=['POST'])
def api_add_program():
    logger.info("POST /next-show received")
    try:
        data = collect_form_program()
        logger.info(f"Полученные данные из формы: {data}")
        apply_poster_upload(data)
        logger.info(f"Итоговый poster_url перед сохранением: {data.get('poster_url')}")
        new_id = add_program(data)
        logger.info(f"Программа добавлена, ID: {new_id}, poster_url в базе: {data.get('poster_url')}")
        return jsonify({'id': new_id, 'status': 'created'}), 201
    except Exception as e:
        logger.error(f"POST /next-show ошибка: {str(e)}")
        return jsonify({'error': str(e)}), 400


@app.route('/next-show/<int:id>', methods=['PUT'])
def api_update_program(id):
    logger.info(f"PUT /next-show/{id} received")
    try:
        existing = get_program(id)
        data = collect_form_program()
        apply_poster_upload(data)
        if not data.get('poster_url') and existing:
            data['poster_url'] = existing.get('poster_url')
        update_program(id, data)
        updated = get_program(id)
        if updated:
            sync_attached_track(updated)
        logger.info(f"Program {id} updated")
        return jsonify({'status': 'updated'})
    except Exception as e:
        logger.error(f"PUT /next-show/{id} error: {str(e)}")
        return jsonify({'error': str(e)}), 400


@app.route('/next-show/<int:id>', methods=['DELETE'])
def api_delete_program(id):
    logger.info(f"DELETE /next-show/{id} received")
    try:
        program = get_program(id)
        if program:
            delete_timer_slot(program.get('timer_id'))
            remove_track_if_unused(
                program.get('track_path'),
                except_program_id=id,
                except_timer_id=program.get('timer_id'),
            )
        delete_program(id)
        logger.info(f"Program {id} deleted")
        return jsonify({'status': 'deleted'})
    except Exception as e:
        logger.error(f"DELETE /next-show/{id} error: {str(e)}")
        return jsonify({'error': str(e)}), 400


@app.route('/next-show/<int:id>/clear_poster', methods=['POST'])
def api_clear_poster(id):
    logger.info(f"CLEAR POSTER /next-show/{id}/clear_poster")
    try:
        # Получаем текущий poster_url
        conn = sqlite3.connect('/home/beasty197/projects/vtrnk_radio/data/schedule.db')
        cursor = conn.cursor()
        cursor.execute("SELECT poster_url FROM programs WHERE id = ?", (id,))
        row = cursor.fetchone()
        if row and row[0]:
            file_path = os.path.join('/home/beasty197/projects/vtrnk_radio/web', row[0].lstrip('/'))
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Deleted poster file: {file_path}")
        cursor.execute("UPDATE programs SET poster_url = NULL WHERE id = ?", (id,))
        conn.commit()
        conn.close()
        return jsonify({'status': 'poster cleared'})
    except Exception as e:
        logger.error(f"Error clearing poster for {id}: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/next-show/<int:id>/air', methods=['POST'])
def api_air_program(id):
    logger.info(f"POST /next-show/{id}/air")
    try:
        program = get_program(id)
        if not program:
            return jsonify({'error': 'Программа не найдена'}), 404

        missing = air_required_missing(program)
        if missing:
            return jsonify({'error': 'Не хватает: ' + ', '.join(missing)}), 400

        new_file = request.files.get('podcast')
        old_path = program.get('track_path')
        track_path = old_path

        if new_file and new_file.filename:
            if not allowed_podcast(new_file.filename):
                return jsonify({'error': 'Нужен файл MP3'}), 400
            track_path = save_podcast_file(new_file)
            if old_path and old_path != track_path:
                remove_track_if_unused(
                    old_path,
                    except_program_id=id,
                    except_timer_id=program.get('timer_id'),
                )
        if not track_path:
            return jsonify({'error': 'Нет файла подкаста'}), 400
        if not os.path.isfile(track_path):
            return jsonify({'error': 'Файл подкаста не найден на диске'}), 400

        upsert_track_from_program(track_path, program)
        try:
            timer_id = create_timer_slot(
                track_path,
                program.get('start_time'),
                old_timer_id=program.get('timer_id'),
            )
        except Exception as e:
            logger.error(f"Timer insert failed, card kept: {e}")
            set_program_air(id, track_path, None)
            return jsonify({
                'error': 'Файл сохранён, но слот таймера не создан: ' + str(e)
            }), 500

        set_program_air(id, track_path, timer_id)
        updated = get_program(id)
        return jsonify({'status': 'on_air', 'program': updated})
    except Exception as e:
        logger.error(f"POST /next-show/{id}/air error: {e}")
        return jsonify({'error': str(e)}), 400


@app.route('/next-show/<int:id>/unair', methods=['POST'])
def api_unair_program(id):
    logger.info(f"POST /next-show/{id}/unair")
    try:
        program = get_program(id)
        if not program:
            return jsonify({'error': 'Программа не найдена'}), 404
        delete_timer_slot(program.get('timer_id'))
        clear_program_timer(id)
        return jsonify({'status': 'off_air', 'program': get_program(id)})
    except Exception as e:
        logger.error(f"POST /next-show/{id}/unair error: {e}")
        return jsonify({'error': str(e)}), 400


@app.route('/next-show/<int:id>/file', methods=['DELETE'])
def api_delete_program_file(id):
    logger.info(f"DELETE /next-show/{id}/file")
    try:
        program = get_program(id)
        if not program:
            return jsonify({'error': 'Программа не найдена'}), 404
        delete_timer_slot(program.get('timer_id'))
        remove_track_if_unused(
            program.get('track_path'),
            except_program_id=id,
            except_timer_id=program.get('timer_id'),
        )
        clear_program_file(id)
        return jsonify({'status': 'file_removed', 'program': get_program(id)})
    except Exception as e:
        logger.error(f"DELETE /next-show/{id}/file error: {e}")
        return jsonify({'error': str(e)}), 400


@app.route('/images/schedule_covers/<path:filename>')
def serve_cover(filename):
    return send_from_directory('/home/beasty197/projects/vtrnk_radio/images/schedule_covers', filename)


# ==================== RESTRIM API ====================

def mask_key(key: str) -> str:
    """Маскирует ключ: показывает только последние 4 символа"""
    if not key or len(key) < 5:
        return '••••'
    return '••••••••' + key[-4:]


def load_restrim_settings():
    """Загружает настройки рестрима"""
    default = {
        "mixcloud": {
            "enabled": False,
            "quality": "750",
            "server": "rtmp://rtmp.mixcloud.com/stream",
            "key": ""
        },
        "extra": {
            "enabled": False,
            "name": "",
            "quality": "750",
            "server": "",
            "key": ""
        }
    }
    try:
        if os.path.exists(RESTRIM_SETTINGS_FILE):
            with open(RESTRIM_SETTINGS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if "mixcloud" not in data:
                    data = default
                return data
    except Exception as e:
        logger.error(f"Ошибка чтения restrim_settings: {e}")
    return default


def save_restrim_settings(data: dict):
    """Сохраняет настройки"""
    os.makedirs(os.path.dirname(RESTRIM_SETTINGS_FILE), exist_ok=True)
    with open(RESTRIM_SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.chmod(RESTRIM_SETTINGS_FILE, 0o600)


def require_restrim_auth(f):
    """Декоратор проверки токена"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('X-Restrim-Token')
        if not token or token not in valid_tokens:
            return jsonify({"success": False, "message": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


@app.route('/api/restrim/login', methods=['POST'])
def restrim_login():
    try:
        data = request.get_json(silent=True) or {}
        password = data.get('password', '')

        if RESTRIM_ADMIN_PASSWORD and password == RESTRIM_ADMIN_PASSWORD:
            token = secrets.token_hex(16)
            valid_tokens.add(token)
            logger.info("Restrim: успешный вход")
            return jsonify({"success": True, "token": token})
        else:
            logger.warning("Restrim: неверный пароль")
            return jsonify({"success": False, "message": "Неверный пароль"}), 401
    except Exception as e:
        logger.error(f"Restrim login error: {e}")
        return jsonify({"success": False, "message": "Ошибка сервера"}), 500


@app.route('/api/restrim/settings', methods=['GET'])
@require_restrim_auth
def get_restrim_settings():
    try:
        settings = load_restrim_settings()

        # Маскируем ключи
        if settings["mixcloud"].get("key"):
            settings["mixcloud"]["key"] = mask_key(settings["mixcloud"]["key"])
        if settings["extra"].get("key"):
            settings["extra"]["key"] = mask_key(settings["extra"]["key"])

        return jsonify(settings)
    except Exception as e:
        logger.error(f"GET restrim settings error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/restrim/settings', methods=['POST'])
@require_restrim_auth
def save_restrim_settings_api():
    try:
        data = request.get_json(silent=True) or {}
        current = load_restrim_settings()

        # Mixcloud
        if "mixcloud" in data:
            mc = data["mixcloud"]
            current["mixcloud"]["enabled"] = bool(mc.get("enabled", False))
            current["mixcloud"]["quality"] = mc.get("quality", "750")
            current["mixcloud"]["server"] = mc.get("server", "").strip()

            new_key = mc.get("key", "").strip()
            if new_key and not new_key.startswith('••••'):
                current["mixcloud"]["key"] = new_key

        # Extra
        if "extra" in data:
            ex = data["extra"]
            current["extra"]["enabled"] = bool(ex.get("enabled", False))
            current["extra"]["name"] = ex.get("name", "").strip()
            current["extra"]["quality"] = ex.get("quality", "750")
            current["extra"]["server"] = ex.get("server", "").strip()

            new_key = ex.get("key", "").strip()
            if new_key and not new_key.startswith('••••'):
                current["extra"]["key"] = new_key

        save_restrim_settings(current)
        logger.info("Restrim settings saved")
        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"POST restrim settings error: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/api/restrim/status', methods=['GET'])
@require_restrim_auth
def restrim_status():
    """Пока заглушка. Позже здесь будет реальный статус ffmpeg"""
    settings = load_restrim_settings()
    return jsonify({
        "mixcloud": {
            "enabled": settings["mixcloud"]["enabled"],
            "running": False,
            "quality": settings["mixcloud"]["quality"]
        },
        "extra": {
            "enabled": settings["extra"]["enabled"],
            "running": False,
            "name": settings["extra"]["name"],
            "quality": settings["extra"]["quality"]
        }
    })


init_db()

if __name__ == '__main__':
    logger.info("Starting Schedule Server on port 5005...")
    app.run(host='0.0.0.0', port=5005, debug=True)