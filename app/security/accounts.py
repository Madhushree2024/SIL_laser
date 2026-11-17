"""Password-hashed local accounts, expiring sessions and an activity log."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
from pathlib import Path
import re
import secrets
import sqlite3
import time
from uuid import uuid4

ROLES = {'admin','editor','viewer'}
WORKFLOW_MODES = {'automated','manual'}
_UNSET = object()
PERMISSIONS = {'admin': ['read','download','operate','templates','configure','users'],
               'editor': ['read','download','operate','templates'], 'viewer': ['read','download']}
ITERATIONS = 600_000
SESSION_SECONDS = 12 * 60 * 60


class AccessError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def password_hash(password):
    if not isinstance(password,str) or not 12 <= len(password) <= 256:
        raise AccessError('Use a password between 12 and 256 characters.')
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),bytes.fromhex(salt),ITERATIONS).hex()
    return f'pbkdf2_sha256${ITERATIONS}${salt}${digest}'


def password_matches(password, saved):
    if not isinstance(password,str) or len(password)>256:
        return False
    try:
        algorithm, rounds, salt, expected = saved.split('$')
        if algorithm != 'pbkdf2_sha256':return False
        actual=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),bytes.fromhex(salt),int(rounds)).hex()
        return hmac.compare_digest(actual,expected)
    except (ValueError,TypeError):
        return False


def _user(row):
    mode = 'automated' if row['role']=='admin' else (row['workflow_mode'] or ('automated' if row['role']=='editor' else None))
    permissions = list(PERMISSIONS[row['role']])
    if row['role']=='viewer' and mode in WORKFLOW_MODES: permissions.append('operate')
    return {'id':row['id'],'username':row['username'],'role':row['role'],'enabled':bool(row['enabled']),
            'permissions':permissions,'workflow_mode':mode}


class AccountStore:
    def __init__(self,path:Path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','editor','viewer')),
                    enabled INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),
                    csrf_token TEXT NOT NULL, expires_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (address TEXT NOT NULL, attempted_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS login_attempt_time ON attempts(attempted_at);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
                    username TEXT NOT NULL, action TEXT NOT NULL, resource TEXT NOT NULL,
                    outcome TEXT NOT NULL, workflow_id TEXT);
            ''')
            db.execute('BEGIN IMMEDIATE')
            if 'workflow_mode' not in {r[1] for r in db.execute('PRAGMA table_info(users)')}:
                db.execute("ALTER TABLE users ADD COLUMN workflow_mode TEXT CHECK(workflow_mode IN ('automated','manual'))")

    @contextmanager
    def connect(self,write=False):
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            if write:db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback();raise
        finally:db.close()

    def configured(self):
        with self.connect() as db:return bool(db.execute('SELECT COUNT(*) FROM users').fetchone()[0])

    def _audit(self,db,username,action,resource='',outcome='success',workflow_id=None):
        db.execute('INSERT INTO audit(at,username,action,resource,outcome,workflow_id) VALUES(?,?,?,?,?,?)',
                   (datetime.now(timezone.utc).isoformat(),username,action,resource,str(outcome),workflow_id))

    def audit(self,username,action,resource='',outcome='success',workflow_id=None):
        with self.connect(write=True) as db:self._audit(db,username,action,resource,outcome,workflow_id)

    def _validate(self,username,role):
        if not isinstance(username,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{3,50}',username):
            raise AccessError('Username must contain 3 to 50 letters, numbers, dots, underscores or hyphens.')
        if role not in ROLES:raise AccessError('Choose Admin, Editor/Manager or Read-only.')

    def create(self,username,password,role,actor=None,initial=False,workflow_mode=None):
        self._validate(username,role)
        if workflow_mode is not None and workflow_mode not in WORKFLOW_MODES: raise AccessError('Choose Automated or Manual workflow.')
        if role=='admin' and workflow_mode is not None: raise AccessError('Admin retains full workflow access.')
        hashed=password_hash(password)
        with self.connect(write=True) as db:
            if initial and db.execute('SELECT COUNT(*) FROM users').fetchone()[0]:
                raise AccessError('Administrator setup is already complete. Sign in instead.',409)
            if initial and role!='admin':raise AccessError('The first account must be an administrator.')
            user_id=str(uuid4())
            try:
                db.execute('INSERT INTO users(id,username,password_hash,role,enabled,created_at,workflow_mode) VALUES(?,?,?,?,1,?,?)',(user_id,username,hashed,role,time.time(),workflow_mode))
            except sqlite3.IntegrityError as exc:
                raise AccessError('That username already exists.',409) from exc
            self._audit(db,actor or username,'admin setup' if initial else 'create user',username)
            return _user(db.execute('SELECT * FROM users WHERE id=?',(user_id,)).fetchone())

    def login(self,username,password,address='local'):
        now=time.time()
        key=f'{address}|{str(username).casefold()[:50]}'
        # Consume an attempt before checking credentials, including unknown usernames.
        with self.connect(write=True) as db:
            db.execute('DELETE FROM attempts WHERE attempted_at<?',(now-900,))
            if (db.execute('SELECT COUNT(*) FROM attempts WHERE address=?',(key,)).fetchone()[0]>=10
                    or db.execute('SELECT COUNT(*) FROM attempts WHERE substr(address,1,?)=?',(len(address)+1,address+'|')).fetchone()[0]>=50):
                raise AccessError('Too many sign-in attempts. Try again in 15 minutes.',429)
            db.execute('INSERT INTO attempts VALUES(?,?)',(key,now))
            row=db.execute('SELECT * FROM users WHERE username=?',(username if isinstance(username,str) else '',)).fetchone()
        # Fixed-cost dummy verification avoids revealing whether a username exists.
        dummy=f'pbkdf2_sha256${ITERATIONS}$'+'0'*32+'$'+'0'*64
        valid=password_matches(password,row['password_hash'] if row else dummy)
        if not row or not valid or not row['enabled']:
            self.audit(str(username)[:50],'sign in',outcome='denied')
            raise AccessError('Username or password is incorrect, or the account is disabled.',401)
        token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
        with self.connect(write=True) as db:
            # Recheck state after the password calculation to handle concurrent resets.
            fresh=db.execute('SELECT * FROM users WHERE id=?',(row['id'],)).fetchone()
            if fresh is None or not fresh['enabled'] or fresh['password_hash']!=row['password_hash']:
                raise AccessError('Account changed. Sign in again.',401)
            db.execute('DELETE FROM sessions WHERE expires_at<?',(now,))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),row['id'],csrf,now+SESSION_SECONDS))
            db.execute('DELETE FROM attempts WHERE address=?',(key,))
            self._audit(db,row['username'],'sign in')
            return token,{'user':_user(fresh),'csrf_token':csrf,'setup_required':False}

    def session(self,token):
        if not token or len(token)>200:return None
        with self.connect() as db:
            row=db.execute('SELECT users.*,sessions.csrf_token FROM sessions JOIN users ON users.id=sessions.user_id '
                           'WHERE token_hash=? AND expires_at>? AND enabled=1',
                           (hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
            return {'user':_user(row),'csrf_token':row['csrf_token'],'setup_required':False} if row else None

    def logout(self,token,username):
        with self.connect(write=True) as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
            self._audit(db,username,'sign out')

    def users(self):
        with self.connect() as db:return [_user(row) for row in db.execute('SELECT * FROM users ORDER BY username COLLATE NOCASE')]

    def update(self,user_id,role,enabled,actor,workflow_mode=_UNSET):
        if role not in ROLES or not isinstance(enabled,bool):raise AccessError('Invalid role or account status.')
        with self.connect(write=True) as db:
            row=db.execute('SELECT * FROM users WHERE id=?',(user_id,)).fetchone()
            if row is None:raise AccessError('Account no longer exists.',404)
            mode=row['workflow_mode'] if workflow_mode is _UNSET else workflow_mode
            if mode is not None and mode not in WORKFLOW_MODES:raise AccessError('Choose Automated or Manual workflow.')
            if role=='admin':
                if workflow_mode is not _UNSET and mode is not None:raise AccessError('Admin retains full workflow access.')
                mode=None
            if row['role']=='admin' and row['enabled'] and (role!='admin' or not enabled):
                if db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1").fetchone()[0]<=1:
                    raise AccessError('Keep at least one active administrator. Create another Admin account first.',409)
            db.execute('UPDATE users SET role=?,enabled=?,workflow_mode=? WHERE id=?',(role,int(enabled),mode,user_id))
            if role!=row['role'] or mode!=row['workflow_mode'] or not enabled:db.execute('DELETE FROM sessions WHERE user_id=?',(user_id,))
            self._audit(db,actor,'update user',f"{row['username']} / {role} / {'enabled' if enabled else 'disabled'} / workflow {mode or 'default'}")
            return _user(db.execute('SELECT * FROM users WHERE id=?',(user_id,)).fetchone())

    def delete(self,user_id,actor_id,actor):
        with self.connect(write=True) as db:
            row=db.execute('SELECT * FROM users WHERE id=?',(user_id,)).fetchone()
            if row is None:raise AccessError('Account no longer exists.',404)
            if row['role']=='admin' and row['enabled']:
                if db.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1").fetchone()[0]<=1:
                    raise AccessError('Keep at least one active administrator. Create another Admin account first.',409)
            if user_id==actor_id:
                raise AccessError('You cannot delete your signed-in account. Ask another Admin to delete it.',409)
            db.execute('DELETE FROM sessions WHERE user_id=?',(user_id,))
            db.execute('DELETE FROM users WHERE id=?',(user_id,))
            self._audit(db,actor,'delete user',row['username'])

    def reset_password(self,user_id,password,actor,current_password=None):
        hashed=password_hash(password)
        with self.connect(write=True) as db:
            row=db.execute('SELECT * FROM users WHERE id=?',(user_id,)).fetchone()
            if row is None:raise AccessError('Account no longer exists.',404)
            if current_password is not None and not password_matches(current_password,row['password_hash']):
                raise AccessError('Current password is incorrect.',403)
            db.execute('UPDATE users SET password_hash=? WHERE id=?',(hashed,user_id))
            db.execute('DELETE FROM sessions WHERE user_id=?',(user_id,))
            self._audit(db,actor,'change password' if current_password is not None else 'reset password',row['username'])

    def events(self):
        with self.connect() as db:return [dict(row) for row in db.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 200')]
