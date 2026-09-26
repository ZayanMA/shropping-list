"""Shropping List: a self-hosted shared shopping list for a household."""

import os
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationInfo, field_validator

from . import auth, db

STATIC_DIR = Path(__file__).parent / "static"
SECURE_COOKIES = os.environ.get("SHOPLIST_SECURE_COOKIES", "false").lower() in ("1", "true", "yes")
CSRF_HEADER = "x-requested-with"

limiter = auth.LoginLimiter()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Shropping List", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def require_csrf_header(request: Request, call_next):
    # Browsers can't add custom headers to cross-site requests without a CORS preflight,
    # which this app never approves, so requiring one blocks CSRF on state-changing calls.
    if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get(CSRF_HEADER) != "shoplist":
            return JSONResponse({"detail": "Missing X-Requested-With header"}, status_code=403)
    return await call_next(request)


# ---------- dependencies ----------


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


def current_user(request: Request, conn: Conn) -> sqlite3.Row:
    token = request.cookies.get(auth.SESSION_COOKIE)
    if token:
        row = conn.execute(
            """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ? AND s.expires_at > ? AND u.active = 1""",
            (auth.hash_token(token), db.now()),
        ).fetchone()
        if row:
            return row
    raise HTTPException(401, "Not logged in")


User = Annotated[sqlite3.Row, Depends(current_user)]


def admin_user(user: User) -> sqlite3.Row:
    if not user["is_admin"]:
        raise HTTPException(403, "Admins only")
    return user


Admin = Annotated[sqlite3.Row, Depends(admin_user)]


# ---------- request models ----------

Username = Annotated[str, Field(min_length=2, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")]
DisplayName = Annotated[str, Field(min_length=1, max_length=40)]
Password = Annotated[str, Field(min_length=8, max_length=200)]
ItemName = Annotated[str, Field(min_length=1, max_length=100)]
Quantity = Annotated[int, Field(ge=1, le=999)]


class Stripped(BaseModel):
    @field_validator("*", mode="before")
    @classmethod
    def strip(cls, v, info: ValidationInfo):
        if isinstance(v, str) and "password" not in info.field_name:
            return v.strip()
        return v


class SetupIn(Stripped):
    username: Username
    display_name: DisplayName
    password: Password


class LoginIn(Stripped):
    username: str = Field(max_length=32)
    password: str = Field(max_length=200)


class NewUserIn(SetupIn):
    is_admin: bool = False


class UpdateUserIn(Stripped):
    display_name: DisplayName | None = None
    is_admin: bool | None = None
    active: bool | None = None
    password: Password | None = None


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: Password


class NewItemIn(Stripped):
    name: ItemName
    quantity: Quantity = 1


class UpdateItemIn(Stripped):
    name: ItemName | None = None
    quantity: Quantity | None = None
    checked: bool | None = None


class CompleteIn(BaseModel):
    list_id: int
    mark_all_bought: bool = False


# ---------- helpers ----------


def user_out(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "is_admin": bool(row["is_admin"]),
        "active": bool(row["active"]),
    }


ITEM_SELECT = """
    SELECT i.*, a.display_name AS added_by_name, c.display_name AS checked_by_name
    FROM items i
    LEFT JOIN users a ON a.id = i.added_by
    LEFT JOIN users c ON c.id = i.checked_by
"""


def item_out(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "quantity": row["quantity"],
        "added_by": row["added_by_name"],
        "added_at": row["added_at"],
        "checked": bool(row["checked"]),
        "checked_by": row["checked_by_name"],
        "checked_at": row["checked_at"],
    }


def load_item(conn: sqlite3.Connection, item_id: int) -> dict:
    return item_out(conn.execute(ITEM_SELECT + " WHERE i.id = ?", (item_id,)).fetchone())


def open_list_item(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row:
    row = conn.execute(
        """SELECT i.* FROM items i JOIN lists l ON l.id = i.list_id
           WHERE i.id = ? AND l.completed_at IS NULL""",
        (item_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Item not found on the current list")
    return row


def start_session(conn: sqlite3.Connection, response: Response, user_id: int) -> None:
    token, token_hash = auth.new_session_token()
    expires = datetime.now(timezone.utc) + timedelta(days=auth.SESSION_DAYS)
    conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (db.now(),))
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token_hash, user_id, db.now(), expires.isoformat(timespec="seconds")),
    )
    response.set_cookie(
        auth.SESSION_COOKIE,
        token,
        max_age=auth.SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=SECURE_COOKIES,
    )


def active_admin_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND active = 1").fetchone()[0]


# ---------- session & setup ----------


@app.get("/api/session")
def session_info(request: Request, conn: Conn):
    setup_required = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    try:
        user = user_out(current_user(request, conn))
    except HTTPException:
        user = None
    return {"setup_required": setup_required, "user": user}


@app.post("/api/setup", status_code=201)
def setup(body: SetupIn, response: Response, conn: Conn):
    """Create the first (admin) account. Only works while there are no users."""
    with db.transaction(conn):
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            raise HTTPException(409, "Setup has already been completed")
        user_id = conn.execute(
            """INSERT INTO users (username, display_name, password_hash, is_admin, created_at)
               VALUES (?, ?, ?, 1, ?)""",
            (body.username, body.display_name, auth.hash_password(body.password), db.now()),
        ).lastrowid
        start_session(conn, response, user_id)
    return user_out(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


@app.post("/api/login")
def login(body: LoginIn, request: Request, response: Response, conn: Conn):
    key = request.client.host if request.client else "unknown"
    if limiter.blocked(key):
        raise HTTPException(429, "Too many failed logins. Try again in a few minutes.")
    row = conn.execute(
        "SELECT * FROM users WHERE username = ? AND active = 1", (body.username,)
    ).fetchone()
    ok = auth.verify_password(body.password, row["password_hash"] if row else auth.DUMMY_HASH)
    if not (row and ok):
        limiter.record_failure(key)
        raise HTTPException(401, "Wrong username or password")
    limiter.reset(key)
    with db.transaction(conn):
        start_session(conn, response, row["id"])
    return user_out(row)


@app.post("/api/logout", status_code=204)
def logout(request: Request, response: Response, conn: Conn):
    token = request.cookies.get(auth.SESSION_COOKIE)
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (auth.hash_token(token),))
    response.delete_cookie(auth.SESSION_COOKIE)


@app.post("/api/me/password", status_code=204)
def change_own_password(body: ChangePasswordIn, request: Request, user: User, conn: Conn):
    if not auth.verify_password(body.current_password, user["password_hash"]):
        raise HTTPException(400, "Current password is wrong")
    keep = auth.hash_token(request.cookies.get(auth.SESSION_COOKIE, ""))
    with db.transaction(conn):
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (auth.hash_password(body.new_password), user["id"]),
        )
        # Sign out every other device.
        conn.execute(
            "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user["id"], keep)
        )


# ---------- the current list ----------


@app.get("/api/list")
def get_list(user: User, conn: Conn):
    lst = conn.execute("SELECT * FROM lists WHERE completed_at IS NULL").fetchone()
    if not lst:
        with db.transaction(conn):
            db.ensure_open_list(conn)
        lst = conn.execute("SELECT * FROM lists WHERE completed_at IS NULL").fetchone()
    rows = conn.execute(
        ITEM_SELECT + " WHERE i.list_id = ? ORDER BY i.checked, i.added_at, i.id", (lst["id"],)
    ).fetchall()
    return {
        "id": lst["id"],
        "created_at": lst["created_at"],
        "items": [item_out(r) for r in rows],
    }


@app.post("/api/items", status_code=201)
def add_item(body: NewItemIn, user: User, conn: Conn):
    """Add an item. If the same thing is already on the list (and not ticked), bump its count."""
    with db.transaction(conn):
        list_id = db.ensure_open_list(conn)
        existing = conn.execute(
            """SELECT id, quantity FROM items
               WHERE list_id = ? AND checked = 0 AND lower(name) = lower(?)""",
            (list_id, body.name),
        ).fetchone()
        if existing:
            item_id = existing["id"]
            conn.execute(
                "UPDATE items SET quantity = min(999, quantity + ?) WHERE id = ?",
                (body.quantity, item_id),
            )
        else:
            item_id = conn.execute(
                """INSERT INTO items (list_id, name, quantity, added_by, added_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (list_id, body.name, body.quantity, user["id"], db.now()),
            ).lastrowid
    return load_item(conn, item_id)


@app.patch("/api/items/{item_id}")
def update_item(item_id: int, body: UpdateItemIn, user: User, conn: Conn):
    with db.transaction(conn):
        item = open_list_item(conn, item_id)
        if body.name is not None:
            conn.execute("UPDATE items SET name = ? WHERE id = ?", (body.name, item_id))
        if body.quantity is not None:
            conn.execute("UPDATE items SET quantity = ? WHERE id = ?", (body.quantity, item_id))
        if body.checked is not None and body.checked != bool(item["checked"]):
            if body.checked:
                conn.execute(
                    "UPDATE items SET checked = 1, checked_by = ?, checked_at = ? WHERE id = ?",
                    (user["id"], db.now(), item_id),
                )
            else:
                conn.execute(
                    "UPDATE items SET checked = 0, checked_by = NULL, checked_at = NULL WHERE id = ?",
                    (item_id,),
                )
    return load_item(conn, item_id)


@app.delete("/api/items/{item_id}", status_code=204)
def delete_item(item_id: int, user: User, conn: Conn):
    with db.transaction(conn):
        open_list_item(conn, item_id)
        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))


@app.post("/api/list/complete")
def complete_shop(body: CompleteIn, user: User, conn: Conn):
    """Close the current list, save it to history and start a fresh one.

    With `mark_all_bought`, every remaining item is ticked off as bought by this user.
    Otherwise anything not ticked is moved onto the new list for the next shop.

    `list_id` must be the list the client is looking at, so two people pressing
    "Shop complete" at the same time can't close the new, empty list by accident.
    """
    with db.transaction(conn):
        current = db.ensure_open_list(conn)
        if body.list_id != current:
            raise HTTPException(409, "This list was already completed by someone else")
        completed_at = db.now()
        conn.execute(
            "UPDATE lists SET completed_at = ?, completed_by = ? WHERE id = ?",
            (completed_at, user["id"], current),
        )
        new_id = db.ensure_open_list(conn)
        carried = 0
        if body.mark_all_bought:
            conn.execute(
                """UPDATE items SET checked = 1, checked_by = ?, checked_at = ?
                   WHERE list_id = ? AND checked = 0""",
                (user["id"], completed_at, current),
            )
        else:
            carried = conn.execute(
                """INSERT INTO items (list_id, name, quantity, added_by, added_at)
                   SELECT ?, name, quantity, added_by, added_at FROM items
                   WHERE list_id = ? AND checked = 0""",
                (new_id, current),
            ).rowcount
    return {"completed_list_id": current, "new_list_id": new_id, "carried_over": carried}


# ---------- history ----------


@app.get("/api/history")
def history(user: User, conn: Conn, limit: int = 50, offset: int = 0):
    limit = max(1, min(limit, 200))
    rows = conn.execute(
        """SELECT l.id, l.created_at, l.completed_at, u.display_name AS completed_by,
                  COUNT(i.id) AS item_count, COALESCE(SUM(i.checked), 0) AS bought_count
           FROM lists l
           LEFT JOIN users u ON u.id = l.completed_by
           LEFT JOIN items i ON i.list_id = l.id
           WHERE l.completed_at IS NOT NULL
           GROUP BY l.id ORDER BY l.completed_at DESC, l.id DESC LIMIT ? OFFSET ?""",
        (limit, max(0, offset)),
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/history/{list_id}")
def history_detail(list_id: int, user: User, conn: Conn):
    lst = conn.execute(
        """SELECT l.*, u.display_name AS completed_by_name FROM lists l
           LEFT JOIN users u ON u.id = l.completed_by
           WHERE l.id = ? AND l.completed_at IS NOT NULL""",
        (list_id,),
    ).fetchone()
    if not lst:
        raise HTTPException(404, "No completed list with that id")
    rows = conn.execute(
        ITEM_SELECT + " WHERE i.list_id = ? ORDER BY i.checked DESC, i.added_at, i.id", (list_id,)
    ).fetchall()
    return {
        "id": lst["id"],
        "created_at": lst["created_at"],
        "completed_at": lst["completed_at"],
        "completed_by": lst["completed_by_name"],
        "items": [item_out(r) for r in rows],
    }


# ---------- household members (admin) ----------


@app.get("/api/users")
def list_users(admin: Admin, conn: Conn):
    rows = conn.execute("SELECT * FROM users ORDER BY active DESC, display_name").fetchall()
    return [user_out(r) for r in rows]


@app.post("/api/users", status_code=201)
def create_user(body: NewUserIn, admin: Admin, conn: Conn):
    with db.transaction(conn):
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (body.username,)).fetchone():
            raise HTTPException(409, "That username is taken")
        user_id = conn.execute(
            """INSERT INTO users (username, display_name, password_hash, is_admin, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (body.username, body.display_name, auth.hash_password(body.password),
             int(body.is_admin), db.now()),
        ).lastrowid
    return user_out(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


@app.patch("/api/users/{user_id}")
def update_user(user_id: int, body: UpdateUserIn, admin: Admin, conn: Conn):
    """Rename, promote/demote, disable/enable, or reset a member's password.

    Members are disabled rather than deleted so history still shows who added what.
    """
    with db.transaction(conn):
        target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            raise HTTPException(404, "No such user")
        removing_admin = target["is_admin"] and target["active"] and (
            body.is_admin is False or body.active is False
        )
        if removing_admin and active_admin_count(conn) <= 1:
            raise HTTPException(400, "The household needs at least one active admin")
        if body.display_name is not None:
            conn.execute("UPDATE users SET display_name = ? WHERE id = ?", (body.display_name, user_id))
        if body.is_admin is not None:
            conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (int(body.is_admin), user_id))
        if body.active is not None:
            conn.execute("UPDATE users SET active = ? WHERE id = ?", (int(body.active), user_id))
        if body.password is not None:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (auth.hash_password(body.password), user_id),
            )
        if body.active is False or body.password is not None:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return user_out(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


# ---------- frontend ----------

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})
