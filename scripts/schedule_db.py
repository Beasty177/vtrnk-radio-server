# /home/beasty197/projects/vtrnk_radio/scripts/schedule_db.py
import sqlite3
import os
from datetime import datetime

# Пути (можно потом вынести в config)
DB_PATH = '/home/beasty197/projects/vtrnk_radio/data/schedule.db'
COVERS_DIR = '/home/beasty197/projects/vtrnk_radio/images/schedule_covers'

# Убедимся, что папка существует
os.makedirs(COVERS_DIR, exist_ok=True)

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # возвращаем словари
    return conn

def init_db():
    """Создаёт таблицу, если её нет (можно вызывать при старте)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS programs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            start_time TEXT NOT NULL,          -- '2025-02-15T14:30:00'
            duration_minutes INTEGER NOT NULL,
            program_type TEXT NOT NULL,
            custom_type TEXT,
            description TEXT,
            author TEXT,
            social_links TEXT,
            poster_url TEXT,                   -- '/images/schedule_covers/123.jpg'
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    conn.commit()
    conn.close()
    print("База schedule.db проверена/создана.")

def get_all_programs():
    """Возвращает список всех программ, отсортированных по времени"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM programs ORDER BY start_time ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def add_program(data, poster_file=None):
    """
    Добавляет новую программу.
    data — словарь из формы
    poster_file — объект файла из request.files (если есть)
    """
    poster_url = None
    if poster_file and poster_file.filename:
        ext = os.path.splitext(poster_file.filename)[1].lower()
        if ext not in ['.jpg', '.jpeg', '.png']:
            raise ValueError("Только jpg/png")
        if poster_file.content_length > 2 * 1024 * 1024:
            raise ValueError("Файл больше 2 МБ")

        filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.urandom(4).hex()}{ext}"
        save_path = os.path.join(COVERS_DIR, filename)
        poster_file.save(save_path)
        poster_url = f'/images/schedule_covers/{filename}'

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO programs (
            name, start_time, duration_minutes, program_type, custom_type,
            description, author, social_links, poster_url
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        data['name'],
        data['start_time'],
        data['duration_minutes'],
        data['program_type'],
        data.get('custom_type'),
        data.get('description'),
        data.get('author'),
        data.get('social_links'),
        poster_url
    ))
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return new_id

def update_program(program_id, data, poster_file=None):
    """Обновляет существующую программу"""
    poster_url = None
    if poster_file and poster_file.filename:
        # можно добавить логику удаления старой афиши, если нужно
        ext = os.path.splitext(poster_file.filename)[1].lower()
        filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.urandom(4).hex()}{ext}"
        save_path = os.path.join(COVERS_DIR, filename)
        poster_file.save(save_path)
        poster_url = f'/images/schedule_covers/{filename}'

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE programs SET
            name = ?, start_time = ?, duration_minutes = ?, program_type = ?, custom_type = ?,
            description = ?, author = ?, social_links = ?, poster_url = ?
        WHERE id = ?
    ''', (
        data['name'],
        data['start_time'],
        data['duration_minutes'],
        data['program_type'],
        data.get('custom_type'),
        data.get('description'),
        data.get('author'),
        data.get('social_links'),
        poster_url or data.get('poster_url'),  # если новый файл не загружен — оставляем старый
        program_id
    ))
    conn.commit()
    conn.close()

def delete_program(program_id):
    """Удаляет программу по id"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM programs WHERE id = ?", (program_id,))
    conn.commit()
    conn.close()

# Для теста из терминала
if __name__ == "__main__":
    init_db()
    print("Тестовое подключение к schedule.db успешно.")
    print("Пример получения всех программ:", get_all_programs())
