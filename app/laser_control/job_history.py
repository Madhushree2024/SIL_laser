"""Private SQLite job/event ledger, separate from exported production counts."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


def now():
    return datetime.now(timezone.utc).isoformat()


class JobHistory:
    def __init__(self, root: Path):
        self.directory = root / 'laser_control_state'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'history.sqlite3'
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, identity TEXT UNIQUE NOT NULL, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, timestamp TEXT NOT NULL,
                kind TEXT NOT NULL, detail TEXT NOT NULL);''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute('PRAGMA busy_timeout=10000')
            with db:
                yield db
        finally:
            db.close()

    def find_identity(self, identity):
        with self.connect() as db:
            row = db.execute('SELECT document FROM jobs WHERE identity=?', (identity,)).fetchone()
        return json.loads(row[0]) if row else None

    def get(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT document FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise KeyError('Job not found.')
        return json.loads(row[0])

    def save(self, job, event, detail=None):
        job['updated_at'] = now()
        with self.connect() as db:
            db.execute('INSERT INTO jobs VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document',
                       (job['id'], job['identity'], json.dumps(job, allow_nan=False)))
            db.execute('INSERT INTO events(job_id,timestamp,kind,detail) VALUES (?,?,?,?)',
                       (job['id'], now(), event, json.dumps(detail or {}, allow_nan=False)))
        return job

    def recent(self, limit=50):
        with self.connect() as db:
            rows = db.execute('SELECT document FROM jobs ORDER BY rowid DESC LIMIT ?', (limit,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def events(self, job_id):
        with self.connect() as db:
            rows = db.execute('SELECT timestamp,kind,detail FROM events WHERE job_id=? ORDER BY sequence', (job_id,)).fetchall()
        return [{'timestamp': row[0], 'kind': row[1], 'detail': json.loads(row[2])} for row in rows]
