import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from schedule_db import get_on_air_program, parse_program_start, MSK_TZ


SCHEMA = '''
CREATE TABLE programs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    start_time TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL,
    program_type TEXT NOT NULL,
    custom_type TEXT,
    description TEXT,
    author TEXT,
    social_links TEXT,
    poster_url TEXT
)
'''


def _db(tmp_path):
    path = os.path.join(tmp_path, 'schedule.db')
    conn = sqlite3.connect(path)
    conn.execute(SCHEMA)
    conn.commit()
    return path, conn


def _insert(conn, **kwargs):
    cols = {
        'name': 'Show',
        'start_time': '2026-08-20T19:00',
        'duration_minutes': 60,
        'program_type': 'live',
        'custom_type': None,
        'description': None,
        'author': 'DJ',
        'social_links': None,
        'poster_url': None,
    }
    cols.update(kwargs)
    cur = conn.execute(
        '''INSERT INTO programs (
               name, start_time, duration_minutes, program_type, custom_type,
               description, author, social_links, poster_url
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            cols['name'], cols['start_time'], cols['duration_minutes'],
            cols['program_type'], cols['custom_type'], cols['description'],
            cols['author'], cols['social_links'], cols['poster_url'],
        ),
    )
    conn.commit()
    return cur.lastrowid


def test_parse_program_start_is_moscow():
    start = parse_program_start('2026-08-20T19:00')
    assert start.tzinfo == MSK_TZ
    assert start.hour == 19


def test_on_air_inside_window(tmp_path):
    path, conn = _db(tmp_path)
    _insert(conn, name='Night Jungle', author='Beasty', poster_url='/images/schedule_covers/x.jpg')
    conn.close()
    now = datetime(2026, 8, 20, 19, 30, tzinfo=MSK_TZ)
    program = get_on_air_program(now=now, db_path=path)
    assert program is not None
    assert program['name'] == 'Night Jungle'
    assert program['author'] == 'Beasty'
    assert program['poster_url'] == '/images/schedule_covers/x.jpg'


def test_on_air_before_and_after_window_without_grace(tmp_path):
    path, conn = _db(tmp_path)
    _insert(conn, name='Solo', start_time='2026-08-20T19:00', duration_minutes=60)
    conn.close()
    assert get_on_air_program(
        now=datetime(2026, 8, 20, 18, 59, tzinfo=MSK_TZ), db_path=path, grace_minutes=0
    ) is None
    assert get_on_air_program(
        now=datetime(2026, 8, 20, 20, 0, tzinfo=MSK_TZ), db_path=path, grace_minutes=0
    ) is None


def test_grace_covers_early_and_late_without_neighbor(tmp_path):
    path, conn = _db(tmp_path)
    _insert(conn, name='Solo live', start_time='2026-08-20T19:00', duration_minutes=60)
    conn.close()
    early = get_on_air_program(now=datetime(2026, 8, 20, 18, 52, tzinfo=MSK_TZ), db_path=path)
    late = get_on_air_program(now=datetime(2026, 8, 20, 20, 8, tzinfo=MSK_TZ), db_path=path)
    too_early = get_on_air_program(now=datetime(2026, 8, 20, 18, 49, tzinfo=MSK_TZ), db_path=path)
    too_late = get_on_air_program(now=datetime(2026, 8, 20, 20, 10, tzinfo=MSK_TZ), db_path=path)
    assert early['name'] == 'Solo live'
    assert late['name'] == 'Solo live'
    assert too_early is None
    assert too_late is None


def test_adjacent_lives_switch_at_exact_start(tmp_path):
    path, conn = _db(tmp_path)
    _insert(conn, name='Live A', start_time='2026-08-20T19:00', duration_minutes=60)
    _insert(conn, name='Live B', start_time='2026-08-20T20:00', duration_minutes=60)
    conn.close()
    before = get_on_air_program(now=datetime(2026, 8, 20, 19, 55, tzinfo=MSK_TZ), db_path=path)
    at_switch = get_on_air_program(now=datetime(2026, 8, 20, 20, 0, tzinfo=MSK_TZ), db_path=path)
    after = get_on_air_program(now=datetime(2026, 8, 20, 20, 8, tzinfo=MSK_TZ), db_path=path)
    assert before['name'] == 'Live A'
    assert at_switch['name'] == 'Live B'
    assert after['name'] == 'Live B'


def test_small_gap_keeps_previous_until_next_start(tmp_path):
    path, conn = _db(tmp_path)
    _insert(conn, name='Live A', start_time='2026-08-20T19:00', duration_minutes=60)
    _insert(conn, name='Live B', start_time='2026-08-20T20:05', duration_minutes=60)
    conn.close()
    in_gap = get_on_air_program(now=datetime(2026, 8, 20, 20, 2, tzinfo=MSK_TZ), db_path=path)
    at_next = get_on_air_program(now=datetime(2026, 8, 20, 20, 5, tzinfo=MSK_TZ), db_path=path)
    assert in_gap['name'] == 'Live A'
    assert at_next['name'] == 'Live B'


def test_on_air_prefers_live_with_poster(tmp_path):
    path, conn = _db(tmp_path)
    _insert(conn, name='Podcast overlap', program_type='podcast', start_time='2026-08-20T19:00')
    _insert(
        conn,
        name='Live overlap',
        program_type='live',
        start_time='2026-08-20T19:00',
        poster_url='/images/schedule_covers/live.jpg',
    )
    conn.close()
    program = get_on_air_program(now=datetime(2026, 8, 20, 19, 10, tzinfo=MSK_TZ), db_path=path)
    assert program['name'] == 'Live overlap'
    assert program['poster_url'] == '/images/schedule_covers/live.jpg'


def test_duration_default_when_zero(tmp_path):
    path, conn = _db(tmp_path)
    _insert(conn, name='Bad duration', duration_minutes=0, start_time='2026-08-20T19:00')
    conn.close()
    program = get_on_air_program(now=datetime(2026, 8, 20, 19, 45, tzinfo=MSK_TZ), db_path=path)
    assert program is not None
    assert program['name'] == 'Bad duration'
