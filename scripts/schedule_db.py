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

def add_program(data):
    """
    Добавляет новую программу из словаря data.
    poster_url берётся напрямую из data['poster_url'] — для копирования/редактирования.
    Если нужен новый файл — он должен обрабатываться в schedule_server.py
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO programs (
            name, start_time, duration_minutes, program_type, custom_type,
            description, author, social_links, poster_url
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        data.get('name'),
        data.get('start_time'),
        data.get('duration_minutes', 0),
        data.get('program_type'),
        data.get('custom_type'),
        data.get('description'),
        data.get('author'),
        data.get('social_links'),
        data.get('poster_url')  # ← берём poster_url из data (главный фикс для копирования!)
    ))
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()
    print(f"Добавлена программа ID {new_id} с poster_url: {data.get('poster_url')}")
    return new_id

def update_program(program_id, data):
    """Обновляет существующую программу из словаря data"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE programs SET
            name = ?, start_time = ?, duration_minutes = ?, program_type = ?, custom_type = ?,
            description = ?, author = ?, social_links = ?, poster_url = ?
        WHERE id = ?
    ''', (
        data.get('name'),
        data.get('start_time'),
        data.get('duration_minutes', 0),
        data.get('program_type'),
        data.get('custom_type'),
        data.get('description'),
        data.get('author'),
        data.get('social_links'),
        data.get('poster_url'),  # ← берём poster_url из data
        program_id
    ))
    conn.commit()
    conn.close()
    print(f"Обновлена программа ID {program_id} с poster_url: {data.get('poster_url')}")

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