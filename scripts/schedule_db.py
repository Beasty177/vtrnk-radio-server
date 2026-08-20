# /home/beasty197/projects/vtrnk_radio/scripts/schedule_db.py
import sqlite3
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DB_PATH = '/home/beasty197/projects/vtrnk_radio/data/schedule.db'
COVERS_DIR = '/home/beasty197/projects/vtrnk_radio/images/schedule_covers'
MSK_TZ = ZoneInfo('Europe/Moscow')
SCHEDULE_GRACE_MINUTES = 10

os.makedirs(COVERS_DIR, exist_ok=True)


def get_db_connection(db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def parse_program_start(start_time):
    """Naive schedule timestamps are Moscow local time."""
    if not start_time:
        return None
    raw = str(start_time).strip()
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M'):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=MSK_TZ)
        except ValueError:
            continue
    return None


def program_end(program, start=None):
    start = start or parse_program_start(program.get('start_time'))
    if not start:
        return None
    try:
        duration = int(program.get('duration_minutes') or 60)
    except (TypeError, ValueError):
        duration = 60
    if duration <= 0:
        duration = 60
    return start + timedelta(minutes=duration)


def _rank_on_air(item):
    program, start = item
    is_live_type = 0 if program.get('program_type') == 'live' else 1
    has_poster = 0 if (program.get('poster_url') or '').strip() else 1
    return (is_live_type, has_poster, -start.timestamp(), -(program.get('id') or 0))


def _pick_on_air(matches):
    if not matches:
        return None
    matches.sort(key=_rank_on_air)
    return matches[0][0]


def get_on_air_program(now=None, db_path=None, grace_minutes=SCHEDULE_GRACE_MINUTES):
    """Program on air at now (MSK).

    Exact slot [start, end) always wins. If now is only in the ±grace window,
    keep a show that already started (overtime) before an upcoming one (early).
    Adjacent lives therefore switch at the next start_time, not 10 minutes early.
    """
    now = now or datetime.now(MSK_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=MSK_TZ)
    else:
        now = now.astimezone(MSK_TZ)
    try:
        grace = int(grace_minutes)
    except (TypeError, ValueError):
        grace = SCHEDULE_GRACE_MINUTES
    if grace < 0:
        grace = 0

    exact = []
    grace_started = []
    grace_upcoming = []
    conn = get_db_connection(db_path)
    try:
        rows = conn.execute("SELECT * FROM programs").fetchall()
    finally:
        conn.close()

    for row in rows:
        program = dict(row)
        start = parse_program_start(program.get('start_time'))
        end = program_end(program, start)
        if not start or not end:
            continue
        if start <= now < end:
            exact.append((program, start))
            continue
        grace_start = start - timedelta(minutes=grace)
        grace_end = end + timedelta(minutes=grace)
        if grace_start <= now < grace_end:
            if now >= start:
                grace_started.append((program, start))
            else:
                grace_upcoming.append((program, start))

    return _pick_on_air(exact) or _pick_on_air(grace_started) or _pick_on_air(grace_upcoming)


def _column_names(cursor):
    cursor.execute("PRAGMA table_info(programs)")
    return [row[1] for row in cursor.fetchall()]


def init_db():
    """Создаёт таблицу и добавляет колонки таймера, если их ещё нет."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS programs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            start_time TEXT NOT NULL,
            duration_minutes INTEGER NOT NULL,
            program_type TEXT NOT NULL,
            custom_type TEXT,
            description TEXT,
            author TEXT,
            social_links TEXT,
            poster_url TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    ''')
    cols = _column_names(cursor)
    if 'track_path' not in cols:
        cursor.execute('ALTER TABLE programs ADD COLUMN track_path TEXT')
        print("Добавлена колонка programs.track_path")
    if 'timer_id' not in cols:
        cursor.execute('ALTER TABLE programs ADD COLUMN timer_id INTEGER')
        print("Добавлена колонка programs.timer_id")
    cursor.execute('''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_programs_timer_id
        ON programs(timer_id) WHERE timer_id IS NOT NULL
    ''')
    conn.commit()
    conn.close()
    print("База schedule.db проверена/создана.")


def get_all_programs():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM programs ORDER BY start_time ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_program(program_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM programs WHERE id = ?", (program_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def add_program(data):
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
        data.get('poster_url')
    ))
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()
    print(f"Добавлена программа ID {new_id} с poster_url: {data.get('poster_url')}")
    return new_id


def update_program(program_id, data):
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
        data.get('poster_url'),
        program_id
    ))
    conn.commit()
    conn.close()
    print(f"Обновлена программа ID {program_id} с poster_url: {data.get('poster_url')}")


def set_program_air(program_id, track_path, timer_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE programs SET track_path = ?, timer_id = ? WHERE id = ?",
        (track_path, timer_id, program_id)
    )
    conn.commit()
    conn.close()


def clear_program_timer(program_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE programs SET timer_id = NULL WHERE id = ?", (program_id,))
    conn.commit()
    conn.close()


def clear_program_file(program_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE programs SET track_path = NULL, timer_id = NULL WHERE id = ?",
        (program_id,)
    )
    conn.commit()
    conn.close()


def count_programs_with_track(track_path, except_id=None):
    if not track_path:
        return 0
    conn = get_db_connection()
    cursor = conn.cursor()
    if except_id is None:
        cursor.execute(
            "SELECT COUNT(*) FROM programs WHERE track_path = ?",
            (track_path,)
        )
    else:
        cursor.execute(
            "SELECT COUNT(*) FROM programs WHERE track_path = ? AND id != ?",
            (track_path, except_id)
        )
    n = cursor.fetchone()[0]
    conn.close()
    return n


def delete_program(program_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM programs WHERE id = ?", (program_id,))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print("Тестовое подключение к schedule.db успешно.")
    print("Пример получения всех программ:", get_all_programs())
