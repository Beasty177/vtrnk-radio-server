# /home/beasty197/projects/vtrnk_radio/scripts/schedule_server.py
# Отдельный сервер для расписания на порту 5005

import sqlite3  # Pylance fix: стандартный модуль
import os
import logging
from datetime import datetime
from flask import Flask, jsonify, request, send_from_directory
from werkzeug.utils import secure_filename
from schedule_db import get_all_programs, add_program, update_program, delete_program, init_db

app = Flask(__name__)

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

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

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
        data = {
            'name': request.form.get('name'),
            'start_time': request.form.get('start_time'),
            'duration_minutes': int(request.form.get('duration_minutes', 0)),
            'program_type': request.form.get('program_type'),
            'custom_type': request.form.get('custom_type') or None,
            'description': request.form.get('description'),
            'author': request.form.get('author'),
            'social_links': request.form.get('social_links')
        }

        logger.info(f"Received form data: {data}")

        poster_url = None
        if 'poster' in request.files:
            file = request.files['poster']
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                save_filename = f"{timestamp}_{filename}"
                save_path = os.path.join(COVERS_DIR, save_filename)
                file.save(save_path)
                poster_url = f'/images/schedule_covers/{save_filename}'
                logger.info(f"Poster saved: {poster_url}")
            else:
                logger.warning("Invalid poster file")

        data['poster_url'] = poster_url

        new_id = add_program(data)
        logger.info(f"Program added, ID: {new_id}")
        return jsonify({'id': new_id, 'status': 'created'}), 201
    except Exception as e:
        logger.error(f"POST /next-show error: {str(e)}")
        return jsonify({'error': str(e)}), 400

@app.route('/next-show/<int:id>', methods=['PUT'])
def api_update_program(id):
    logger.info(f"PUT /next-show/{id} received")
    try:
        data = {
            'name': request.form.get('name'),
            'start_time': request.form.get('start_time'),
            'duration_minutes': int(request.form.get('duration_minutes', 0)),
            'program_type': request.form.get('program_type'),
            'custom_type': request.form.get('custom_type') or None,
            'description': request.form.get('description'),
            'author': request.form.get('author'),
            'social_links': request.form.get('social_links'),
            'poster_url': None  # будет перезаписано если новый файл
        }

        poster_url = None
        if 'poster' in request.files:
            file = request.files['poster']
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                save_filename = f"{timestamp}_{filename}"
                save_path = os.path.join(COVERS_DIR, save_filename)
                file.save(save_path)
                poster_url = f'/images/schedule_covers/{save_filename}'
                logger.info(f"Updated poster: {poster_url}")
            else:
                logger.warning("Invalid poster file on update")

        if poster_url:
            data['poster_url'] = poster_url

        update_program(id, data)
        logger.info(f"Program {id} updated")
        return jsonify({'status': 'updated'})
    except Exception as e:
        logger.error(f"PUT /next-show/{id} error: {str(e)}")
        return jsonify({'error': str(e)}), 400

@app.route('/next-show/<int:id>', methods=['DELETE'])
def api_delete_program(id):
    logger.info(f"DELETE /next-show/{id} received")
    try:
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

@app.route('/images/schedule_covers/<path:filename>')
def serve_cover(filename):
    return send_from_directory('/home/beasty197/projects/vtrnk_radio/images/schedule_covers', filename)

if __name__ == '__main__':
    logger.info("Starting Schedule Server on port 5005...")
    app.run(host='0.0.0.0', port=5005, debug=True)