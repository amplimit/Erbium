from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass, fields
from json import dumps, loads
from os import PathLike, chmod
from pathlib import Path
from sqlite3 import Connection, IntegrityError, Row, connect
from time import time
from typing import Any, Iterator, Literal, get_args

JobStatus = Literal["queued", "running", "finished", "cancelled"]
EndReason = Literal["expired", "stopped", "left", "admin_stopped", "admin_removed"]
NotificationKind = Literal["queued", "started", "ending", "finished", "admin"]

NOTIFICATION_KINDS: tuple[NotificationKind, ...] = get_args(NotificationKind)
DEFAULT_NOTIFICATIONS: dict[NotificationKind, bool] = {
    "queued": False, "started": True, "ending": True, "finished": False, "admin": True
}
SCHEMA_VERSION: int = 1
SCHEMA: str = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    email TEXT,
    is_admin INTEGER NOT NULL DEFAULT 0,
    public_keys TEXT NOT NULL DEFAULT '[]',
    notifications TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_digest TEXT PRIMARY KEY,
    username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    name TEXT NOT NULL,
    requested_run_time_hrs REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'finished', 'cancelled')),
    position REAL NOT NULL,
    created_at REAL NOT NULL,
    started_at REAL,
    ended_at REAL,
    end_reason TEXT,
    ssh_password TEXT,
    jupyter_token TEXT,
    ending_notified INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS one_running_job ON jobs(status) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS jobs_by_status ON jobs(status, position);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class QuotaError(Exception):
    pass


@dataclass(frozen=True)
class User(object):
    username: str
    password_hash: str
    email: str | None
    is_admin: bool
    public_keys: tuple[str, ...]
    notifications: dict[NotificationKind, bool]
    created_at: float


@dataclass(frozen=True)
class Job(object):
    id: int
    username: str
    name: str
    requested_run_time_hrs: float
    status: JobStatus
    position: float
    created_at: float
    started_at: float | None
    ended_at: float | None
    end_reason: EndReason | None
    ssh_password: str | None
    jupyter_token: str | None
    ending_notified: bool

    def ends_at(self) -> float | None:
        if self.started_at is None:
            return None
        return self.started_at + 3600 * self.requested_run_time_hrs


@dataclass(frozen=True)
class Settings(object):
    max_run_time_hrs: float = 168
    max_jobs_per_user: int = 2
    max_queue_length: int = 50
    ending_notice_minutes: float = 30


def _user(row: Row) -> User:
    stored = loads(row["notifications"])
    return User(
        row["username"], row["password_hash"], row["email"], bool(row["is_admin"]),
        tuple(loads(row["public_keys"])), {kind: bool(stored.get(kind, DEFAULT_NOTIFICATIONS[kind]))
                                           for kind in NOTIFICATION_KINDS},
        row["created_at"]
    )


def _job(row: Row) -> Job:
    return Job(
        row["id"], row["username"], row["name"], row["requested_run_time_hrs"], row["status"], row["position"],
        row["created_at"], row["started_at"], row["ended_at"], row["end_reason"], row["ssh_password"],
        row["jupyter_token"], bool(row["ending_notified"])
    )


class Store(object):
    def __init__(self, path: str | PathLike[str]) -> None:
        self.path: Path = Path(path)
        if not self.path.parent.exists():
            self.path.parent.mkdir(mode=0o700, parents=True)
        self.path.touch(mode=0o600, exist_ok=True)
        chmod(self.path, 0o600)
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"Database schema version {version} is newer than supported {SCHEMA_VERSION}")
            connection.executescript(SCHEMA)
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def _connect(self) -> Connection:
        connection = connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
        connection.row_factory = Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _read(self) -> Iterator[Connection]:
        with closing(self._connect()) as connection:
            yield connection

    @contextmanager
    def _write(self) -> Iterator[Connection]:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            connection.commit()

    def create_user(self, username: str, password_hash: str, *, email: str | None = None,
                    is_admin: bool = False) -> User:
        try:
            with self._write() as connection:
                connection.execute(
                    "INSERT INTO users (username, password_hash, email, is_admin, notifications, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (username, password_hash, email, int(is_admin), dumps(DEFAULT_NOTIFICATIONS), time())
                )
        except IntegrityError as error:
            raise ValueError(f"User {username} already exists") from error
        user = self.get_user(username)
        assert user is not None
        return user

    def get_user(self, username: str) -> User | None:
        with self._read() as connection:
            row = connection.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return None if row is None else _user(row)

    def list_users(self) -> list[User]:
        with self._read() as connection:
            rows = connection.execute("SELECT * FROM users ORDER BY username").fetchall()
        return [_user(row) for row in rows]

    def set_password_hash(self, username: str, password_hash: str) -> bool:
        with self._write() as connection:
            updated = connection.execute(
                "UPDATE users SET password_hash = ? WHERE username = ?", (password_hash, username)
            ).rowcount
            connection.execute("DELETE FROM sessions WHERE username = ?", (username,))
        return updated > 0

    def update_profile(self, username: str, *, email: str | None, public_keys: tuple[str, ...],
                       notifications: dict[NotificationKind, bool]) -> bool:
        with self._write() as connection:
            return connection.execute(
                "UPDATE users SET email = ?, public_keys = ?, notifications = ? WHERE username = ?",
                (email, dumps(list(public_keys)), dumps(notifications), username)
            ).rowcount > 0

    def update_account(self, username: str, *, email: str | None, is_admin: bool) -> bool:
        with self._write() as connection:
            if not is_admin and self._is_last_admin(connection, username):
                raise ValueError("Cannot revoke the last administrator")
            return connection.execute(
                "UPDATE users SET email = ?, is_admin = ? WHERE username = ?", (email, int(is_admin), username)
            ).rowcount > 0

    def delete_user(self, username: str) -> bool:
        with self._write() as connection:
            if self._is_last_admin(connection, username):
                raise ValueError("Cannot delete the last administrator")
            return connection.execute("DELETE FROM users WHERE username = ?", (username,)).rowcount > 0

    @staticmethod
    def _is_last_admin(connection: Connection, username: str) -> bool:
        admins = [row[0] for row in connection.execute("SELECT username FROM users WHERE is_admin = 1")]
        return admins == [username]

    def create_session(self, token_digest: str, username: str, csrf_token: str, expires_at: float) -> None:
        with self._write() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (time(),))
            connection.execute(
                "INSERT INTO sessions (token_digest, username, csrf_token, expires_at) VALUES (?, ?, ?, ?)",
                (token_digest, username, csrf_token, expires_at)
            )

    def get_session(self, token_digest: str) -> tuple[User, str] | None:
        with self._read() as connection:
            row = connection.execute(
                "SELECT users.*, sessions.csrf_token FROM sessions JOIN users USING (username) "
                "WHERE sessions.token_digest = ? AND sessions.expires_at > ?", (token_digest, time())
            ).fetchone()
        return None if row is None else (_user(row), row["csrf_token"])

    def delete_session(self, token_digest: str) -> None:
        with self._write() as connection:
            connection.execute("DELETE FROM sessions WHERE token_digest = ?", (token_digest,))

    def get_settings(self) -> Settings:
        with self._read() as connection:
            return self._settings(connection)

    @staticmethod
    def _settings(connection: Connection) -> Settings:
        stored = {row["key"]: loads(row["value"]) for row in connection.execute("SELECT key, value FROM settings")}
        return Settings(**{f.name: stored[f.name] for f in fields(Settings) if f.name in stored})

    def put_settings(self, settings: Settings) -> None:
        with self._write() as connection:
            connection.executemany(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                [(key, dumps(value)) for key, value in asdict(settings).items()]
            )

    def enqueue(self, username: str, name: str, requested_run_time_hrs: float) -> Job:
        with self._write() as connection:
            settings = self._settings(connection)
            if requested_run_time_hrs > settings.max_run_time_hrs:
                raise QuotaError(f"Requested run time exceeds the limit of {settings.max_run_time_hrs:g} hours")
            active = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE username = ? AND status IN ('queued', 'running')", (username,)
            ).fetchone()[0]
            if active >= settings.max_jobs_per_user:
                raise QuotaError(f"You already have {active} active jobs (limit {settings.max_jobs_per_user})")
            queued, last = connection.execute(
                "SELECT COUNT(*), COALESCE(MAX(position), 0) FROM jobs WHERE status = 'queued'"
            ).fetchone()
            if queued >= settings.max_queue_length:
                raise QuotaError(f"The queue is full (limit {settings.max_queue_length})")
            job_id = connection.execute(
                "INSERT INTO jobs (username, name, requested_run_time_hrs, status, position, created_at) "
                "VALUES (?, ?, ?, 'queued', ?, ?)", (username, name, requested_run_time_hrs, last + 1, time())
            ).lastrowid
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _job(row)

    def get_job(self, job_id: int) -> Job | None:
        with self._read() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return None if row is None else _job(row)

    def running_job(self) -> Job | None:
        with self._read() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE status = 'running'").fetchone()
        return None if row is None else _job(row)

    def queued_jobs(self) -> list[Job]:
        with self._read() as connection:
            rows = connection.execute("SELECT * FROM jobs WHERE status = 'queued' ORDER BY position").fetchall()
        return [_job(row) for row in rows]

    def history(self, limit: int) -> list[Job]:
        with self._read() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs WHERE status IN ('finished', 'cancelled') ORDER BY ended_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_job(row) for row in rows]

    def start_next(self, ssh_password: str, jupyter_token: str, now: float) -> Job | None:
        with self._write() as connection:
            if connection.execute("SELECT 1 FROM jobs WHERE status = 'running'").fetchone():
                return None
            row = connection.execute(
                "SELECT id FROM jobs WHERE status = 'queued' ORDER BY position LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE jobs SET status = 'running', started_at = ?, ssh_password = ?, jupyter_token = ? WHERE id = ?",
                (now, ssh_password, jupyter_token, row["id"])
            )
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
        return _job(row)

    def _end(self, job_id: int, status: JobStatus, reason: EndReason, *, expected: JobStatus,
             username: str | None) -> Job | None:
        query = (
            "UPDATE jobs SET status = ?, end_reason = ?, ended_at = ?, ssh_password = NULL, jupyter_token = NULL "
            "WHERE id = ? AND status = ?"
        )
        parameters: tuple[Any, ...] = (status, reason, time(), job_id, expected)
        if username is not None:
            query += " AND username = ?"
            parameters += (username,)
        with self._write() as connection:
            if connection.execute(query, parameters).rowcount == 0:
                return None
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _job(row)

    def finish_job(self, job_id: int, reason: EndReason, *, username: str | None = None) -> Job | None:
        return self._end(job_id, "finished", reason, expected="running", username=username)

    def cancel_job(self, job_id: int, reason: EndReason, *, username: str | None = None) -> Job | None:
        return self._end(job_id, "cancelled", reason, expected="queued", username=username)

    def mark_ending_notified(self, job_id: int) -> bool:
        with self._write() as connection:
            return connection.execute(
                "UPDATE jobs SET ending_notified = 1 WHERE id = ? AND status = 'running' AND ending_notified = 0",
                (job_id,)
            ).rowcount > 0

    def set_requested_run_time(self, job_id: int, requested_run_time_hrs: float) -> Job | None:
        with self._write() as connection:
            updated = connection.execute(
                "UPDATE jobs SET requested_run_time_hrs = ?, ending_notified = 0 "
                "WHERE id = ? AND status IN ('queued', 'running')", (requested_run_time_hrs, job_id)
            ).rowcount
            if updated == 0:
                return None
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _job(row)

    def move_job(self, job_id: int, offset: Literal[-1, 1]) -> bool:
        with self._write() as connection:
            rows = connection.execute("SELECT id, position FROM jobs WHERE status = 'queued' ORDER BY position").fetchall()
            index = next((i for i, row in enumerate(rows) if row["id"] == job_id), None)
            if index is None or not 0 <= index + offset < len(rows):
                return False
            current, neighbor = rows[index], rows[index + offset]
            connection.execute("UPDATE jobs SET position = ? WHERE id = ?", (neighbor["position"], current["id"]))
            connection.execute("UPDATE jobs SET position = ? WHERE id = ?", (current["position"], neighbor["id"]))
        return True
