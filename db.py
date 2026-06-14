"""Storage layer — Postgres (preferred) with SQLite fallback.

Set DATABASE_URL (Render -> PostgreSQL -> Internal Database URL) to use
Postgres. Without it, the bot uses a local SQLite file `bot.db`.

The public API is a tiny wrapper:
    with db() as c:
        c.execute("SELECT ... WHERE id=?", (id,))
        row = c.fetchone()

`?` placeholders are auto-translated to `%s` on Postgres. Rows are
dict-like (`row["col"]`) on both backends.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import string
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional, Sequence

from config import DATABASE_URL, DB_PATH

IS_PG = bool(DATABASE_URL)

if IS_PG:
    import psycopg
    from psycopg.rows import dict_row


# ---------------------------------------------------------------------------
# unified cursor wrapper
# ---------------------------------------------------------------------------
class _Cur:
    def __init__(self, raw, is_pg: bool):
        self._raw = raw
        self._is_pg = is_pg

    def execute(self, sql: str, params: Sequence[Any] = ()) -> "_Cur":
        if self._is_pg:
            sql = sql.replace("?", "%s")
        self._raw.execute(sql, params)
        return self

    def executescript(self, sql: str) -> None:
        if self._is_pg:
            self._raw.execute(sql)
        else:
            self._raw.executescript(sql)

    def fetchone(self):
        row = self._raw.fetchone()
        if row is None:
            return None
        if self._is_pg:
            return _Row(row)
        return row

    def fetchall(self):
        rows = self._raw.fetchall()
        if self._is_pg:
            return [_Row(r) for r in rows]
        return rows

    @property
    def rowcount(self) -> int:
        return self._raw.rowcount

    @property
    def lastrowid(self) -> Optional[int]:
        return getattr(self._raw, "lastrowid", None)


class _Row(dict):
    def __getitem__(self, key):
        return super().__getitem__(key)


class _Conn:
    def __init__(self, raw, is_pg: bool):
        self._raw = raw
        self._is_pg = is_pg

    def execute(self, sql: str, params: Sequence[Any] = ()) -> _Cur:
        cur = _Cur(self._raw.cursor(row_factory=dict_row) if self._is_pg else self._raw.cursor(), self._is_pg)
        return cur.execute(sql, params)

    def executescript(self, sql: str) -> None:
        cur = _Cur(self._raw.cursor() if self._is_pg else self._raw.cursor(), self._is_pg)
        cur.executescript(sql)

    def commit(self) -> None:
        if self._is_pg:
            self._raw.commit()

    def close(self) -> None:
        self._raw.close()


def _connect() -> _Conn:
    if IS_PG:
        conn = psycopg.connect(DATABASE_URL, autocommit=True)
        return _Conn(conn, True)
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return _Conn(conn, False)


@contextmanager
def db() -> Iterator[_Conn]:
    c = _connect()
    try:
        yield c
    finally:
        c.close()


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------
def init_db() -> None:
    if IS_PG:
        ddl = """
        CREATE TABLE IF NOT EXISTS users (
            user_id      BIGINT PRIMARY KEY,
            username     TEXT,
            first_name   TEXT,
            free_used    INTEGER NOT NULL DEFAULT 0,
            licence_type TEXT,
            licence_exp  BIGINT,
            banned       INTEGER NOT NULL DEFAULT 0,
            created_at   BIGINT NOT NULL,
            last_seen    BIGINT NOT NULL,
            warn3_sent   INTEGER NOT NULL DEFAULT 0,
            warn1_sent   INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS licence_codes (
            code       TEXT PRIMARY KEY,
            kind       TEXT NOT NULL,
            used_by    BIGINT,
            used_at    BIGINT,
            revoked    INTEGER NOT NULL DEFAULT 0,
            created_at BIGINT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS broker_links (
            name     TEXT PRIMARY KEY,
            url      TEXT NOT NULL,
            added_at BIGINT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_msg_stack (
            user_id BIGINT PRIMARY KEY,
            msg_id  BIGINT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS signals (
            id          BIGSERIAL PRIMARY KEY,
            user_id     BIGINT NOT NULL,
            pair        TEXT NOT NULL,
            direction   TEXT NOT NULL,
            tf_seconds  INTEGER NOT NULL,
            entry       DOUBLE PRECISION,
            exit        DOUBLE PRECISION,
            result      TEXT,
            strength    INTEGER,
            created_at  BIGINT NOT NULL,
            resolved_at BIGINT
        );
        CREATE TABLE IF NOT EXISTS otc_format (
            k          TEXT PRIMARY KEY,
            format     INTEGER NOT NULL,
            streak_dir TEXT,
            streak_n   INTEGER NOT NULL DEFAULT 0,
            changed_at BIGINT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS join_requests (
            user_id      BIGINT PRIMARY KEY,
            chat_id      BIGINT NOT NULL,
            requested_at BIGINT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            k TEXT PRIMARY KEY,
            v TEXT
        );
        """
    else:
        ddl = """
        CREATE TABLE IF NOT EXISTS users (
            user_id      INTEGER PRIMARY KEY,
            username     TEXT,
            first_name   TEXT,
            free_used    INTEGER NOT NULL DEFAULT 0,
            licence_type TEXT,
            licence_exp  INTEGER,
            banned       INTEGER NOT NULL DEFAULT 0,
            created_at   INTEGER NOT NULL,
            last_seen    INTEGER NOT NULL,
            warn3_sent   INTEGER NOT NULL DEFAULT 0,
            warn1_sent   INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS licence_codes (
            code       TEXT PRIMARY KEY,
            kind       TEXT NOT NULL,
            used_by    INTEGER,
            used_at    INTEGER,
            revoked    INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS broker_links (
            name     TEXT PRIMARY KEY,
            url      TEXT NOT NULL,
            added_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS user_msg_stack (
            user_id INTEGER PRIMARY KEY,
            msg_id  INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS signals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            pair        TEXT NOT NULL,
            direction   TEXT NOT NULL,
            tf_seconds  INTEGER NOT NULL,
            entry       REAL,
            exit        REAL,
            result      TEXT,
            strength    INTEGER,
            created_at  INTEGER NOT NULL,
            resolved_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS otc_format (
            k          TEXT PRIMARY KEY,
            format     INTEGER NOT NULL,
            streak_dir TEXT,
            streak_n   INTEGER NOT NULL DEFAULT 0,
            changed_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS join_requests (
            user_id      INTEGER PRIMARY KEY,
            chat_id      INTEGER NOT NULL,
            requested_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            k TEXT PRIMARY KEY,
            v TEXT
        );
        """
    with db() as c:
        c.executescript(ddl)


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------
def upsert_user(user_id: int, username: Optional[str], first_name: Optional[str]) -> None:
    now = int(time.time())
    with db() as c:
        c.execute(
            """
            INSERT INTO users(user_id, username, first_name, created_at, last_seen)
            VALUES(?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                last_seen=excluded.last_seen
            """,
            (user_id, username, first_name, now, now),
        )


def get_user(user_id: int):
    with db() as c:
        return c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def all_users():
    with db() as c:
        return c.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()


def set_banned(user_id: int, banned: bool) -> None:
    with db() as c:
        c.execute("UPDATE users SET banned=? WHERE user_id=?", (1 if banned else 0, user_id))


def is_banned(user_id: int) -> bool:
    u = get_user(user_id)
    return bool(u and u["banned"])


def increment_free(user_id: int) -> None:
    with db() as c:
        c.execute("UPDATE users SET free_used=free_used+1 WHERE user_id=?", (user_id,))


def has_active_licence(user_id: int) -> bool:
    u = get_user(user_id)
    if not u or not u["licence_type"]:
        return False
    if u["licence_type"] == "lifetime":
        return True
    return bool(u["licence_exp"] and u["licence_exp"] > int(time.time()))


def can_request_signal(user_id: int) -> tuple[bool, str]:
    import config
    if user_id == config.ADMIN_ID:
        return True, ""
    u = get_user(user_id)
    if not u:
        return False, "User not found."
    if u["banned"]:
        return False, "You have been banned from this bot."
    if has_active_licence(user_id):
        return True, ""
    if u["free_used"] < 3:
        return True, ""
    return False, (
        "🔒 You used all 3 free signals.\n\n"
        "Activate a licence to keep getting signals.\n"
        f"Contact @{config.SUPPORT_BOT} for a code, then send it here."
    )


# ---------------------------------------------------------------------------
# licence codes
# ---------------------------------------------------------------------------
def gen_code(kind: str) -> str:
    alphabet = string.ascii_uppercase + string.digits
    code = "EW-" + "".join(secrets.choice(alphabet) for _ in range(10))
    with db() as c:
        c.execute(
            "INSERT INTO licence_codes(code,kind,created_at) VALUES(?,?,?)",
            (code, kind, int(time.time())),
        )
    return code


def revoke_code(code: str) -> bool:
    with db() as c:
        cur = c.execute("UPDATE licence_codes SET revoked=1 WHERE code=?", (code,))
        return cur.rowcount > 0


def redeem_code(code: str, user_id: int) -> tuple[bool, str]:
    with db() as c:
        row = c.execute("SELECT * FROM licence_codes WHERE code=?", (code,)).fetchone()
        if not row:
            return False, "❌ Invalid licence code."
        if row["revoked"]:
            return False, "❌ This code has been revoked."
        if row["used_by"]:
            return False, "❌ This code has already been used."
        kind = row["kind"]
        exp = None if kind == "lifetime" else int(time.time()) + 30 * 86400
        now = int(time.time())
        c.execute(
            "UPDATE licence_codes SET used_by=?, used_at=? WHERE code=?",
            (user_id, now, code),
        )
        c.execute(
            "UPDATE users SET licence_type=?, licence_exp=?, warn3_sent=0, warn1_sent=0 WHERE user_id=?",
            (kind, exp, user_id),
        )
    if kind == "lifetime":
        return True, "✅ Lifetime licence activated. Welcome!"
    return True, "✅ Monthly licence activated (30 days). Enjoy!"


# ---------------------------------------------------------------------------
# broker links
# ---------------------------------------------------------------------------
def set_broker(name: str, url: str) -> None:
    with db() as c:
        c.execute(
            "INSERT INTO broker_links(name,url,added_at) VALUES(?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET url=excluded.url",
            (name, url, int(time.time())),
        )


def remove_broker(name: str) -> bool:
    with db() as c:
        cur = c.execute("DELETE FROM broker_links WHERE LOWER(name)=LOWER(?)", (name,))
        return cur.rowcount > 0


def list_brokers():
    with db() as c:
        return c.execute("SELECT * FROM broker_links ORDER BY added_at ASC").fetchall()


# ---------------------------------------------------------------------------
# message stack (chat cleanup)
# ---------------------------------------------------------------------------
def push_msg(user_id: int, msg_id: int) -> None:
    with db() as c:
        c.execute(
            "INSERT INTO user_msg_stack(user_id,msg_id) VALUES(?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET msg_id=excluded.msg_id",
            (user_id, msg_id),
        )


def pop_msg(user_id: int) -> Optional[int]:
    with db() as c:
        row = c.execute("SELECT msg_id FROM user_msg_stack WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return None
        c.execute("DELETE FROM user_msg_stack WHERE user_id=?", (user_id,))
        return row["msg_id"]


# ---------------------------------------------------------------------------
# signals
# ---------------------------------------------------------------------------
def record_signal(user_id: int, pair: str, direction: str, tf: int, entry: Optional[float], strength: int) -> int:
    with db() as c:
        if IS_PG:
            cur = c.execute(
                "INSERT INTO signals(user_id,pair,direction,tf_seconds,entry,strength,created_at) "
                "VALUES(?,?,?,?,?,?,?) RETURNING id",
                (user_id, pair, direction, tf, entry, strength, int(time.time())),
            )
            return cur.fetchone()["id"]
        cur = c.execute(
            "INSERT INTO signals(user_id,pair,direction,tf_seconds,entry,strength,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (user_id, pair, direction, tf, entry, strength, int(time.time())),
        )
        return cur.lastrowid or 0


def finalize_signal(signal_id: int, exit_price: Optional[float], result: str) -> None:
    with db() as c:
        c.execute(
            "UPDATE signals SET exit=?, result=?, resolved_at=? WHERE id=?",
            (exit_price, result, int(time.time()), signal_id),
        )


def stats() -> dict:
    with db() as c:
        total = c.execute("SELECT COUNT(*) AS n FROM signals").fetchone()["n"]
        wins = c.execute("SELECT COUNT(*) AS n FROM signals WHERE result='WIN'").fetchone()["n"]
        losses = c.execute("SELECT COUNT(*) AS n FROM signals WHERE result='LOSS'").fetchone()["n"]
        dojis = c.execute("SELECT COUNT(*) AS n FROM signals WHERE result='DOJI'").fetchone()["n"]
        users = c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        active = c.execute(
            "SELECT COUNT(*) AS n FROM users WHERE licence_type='lifetime' "
            "OR (licence_type='monthly' AND licence_exp>?)",
            (int(time.time()),),
        ).fetchone()["n"]
    resolved = wins + losses
    win_rate = (wins / resolved * 100) if resolved else 0.0
    return {
        "total": total, "wins": wins, "losses": losses, "dojis": dojis,
        "win_rate": win_rate, "users": users, "active_licences": active,
    }


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
def get_setting(k: str, default: Optional[str] = None) -> Optional[str]:
    with db() as c:
        row = c.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
        return row["v"] if row else default


def set_setting(k: str, v: str) -> None:
    with db() as c:
        c.execute(
            "INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (k, v),
        )


# ---------------------------------------------------------------------------
# join requests
# ---------------------------------------------------------------------------
def add_join_request(user_id: int, chat_id: int) -> None:
    with db() as c:
        c.execute(
            "INSERT INTO join_requests(user_id,chat_id,requested_at) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET chat_id=excluded.chat_id, requested_at=excluded.requested_at",
            (user_id, chat_id, int(time.time())),
        )


def pop_join_request(user_id: int):
    with db() as c:
        row = c.execute("SELECT * FROM join_requests WHERE user_id=?", (user_id,)).fetchone()
        if row:
            c.execute("DELETE FROM join_requests WHERE user_id=?", (user_id,))
        return row


def has_join_request(user_id: int) -> bool:
    with db() as c:
        row = c.execute("SELECT 1 FROM join_requests WHERE user_id=?", (user_id,)).fetchone()
        return row is not None


# ---------------------------------------------------------------------------
# OTC format state
# ---------------------------------------------------------------------------
def get_otc_state():
    with db() as c:
        row = c.execute("SELECT * FROM otc_format WHERE k='current'").fetchone()
        if row:
            return row
        c.execute(
            "INSERT INTO otc_format(k,format,changed_at) VALUES('current',3,?)",
            (int(time.time()),),
        )
        return c.execute("SELECT * FROM otc_format WHERE k='current'").fetchone()


def set_otc_state(fmt: int, streak_dir: Optional[str], streak_n: int, update_timer: bool = False) -> None:
    with db() as c:
        if update_timer:
            c.execute(
                "UPDATE otc_format SET format=?, streak_dir=?, streak_n=?, changed_at=? WHERE k='current'",
                (fmt, streak_dir, streak_n, int(time.time())),
            )
        else:
            c.execute(
                "UPDATE otc_format SET format=?, streak_dir=?, streak_n=? WHERE k='current'",
                (fmt, streak_dir, streak_n),
            )
