#!/usr/bin/env python3
"""
Cinema Bot — Single-file, full-featured Telegram bot using python-telegram-bot v20+
-------------------------------------------------------------------------------
Features:
  • SQLite persistence (users, videos, favorites, feedback)
  • Categories + pagination
  • Upload videos (admin-only) with name, category, and auto code
  • View by code, list by category
  • Favorites (add/remove + view)
  • Search by name or code
  • Admin panel: broadcast, view users, stats, block/unblock users
  • Feedback collection from users
  • Clean inline keyboards, stateful flows via context.user_data

Setup:
  1) pip install python-telegram-bot==20.7 python-dotenv
  2) Create .env with BOT_TOKEN=xxxxxxxx
  3) Run: python cinema_bot_single_file.py

Notes:
  • Replace ADMIN_IDS below with your IDs.
  • Video storage uses Telegram file_id (bot must have access to uploaded videos).
  • This is a single-file monolith for ease of deployment.

Author: ChatGPT
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import sqlite3
import string
from datetime import datetime
from typing import List, Optional, Tuple

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
    InputMediaVideo,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =============================
# Logging & Config
# =============================
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("cinema-bot")

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "7671071954:AAErUy7AHkw_DWKvEvaS4o-m_t5QOrLAPxU")
# ⚠️  Fill your numeric Telegram IDs here
ADMIN_IDS: List[int] = [5826150796]

DB_PATH = os.getenv("DB_PATH", "cinema.db")
PAGE_SIZE = 6  # number of videos per page

# =============================
# SQLite helpers
# =============================
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    is_blocked INTEGER DEFAULT 0,
    joined_at TEXT
);

CREATE TABLE IF NOT EXISTS videos (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    file_id TEXT NOT NULL,
    category TEXT NOT NULL,
    views INTEGER DEFAULT 0,
    uploaded_at TEXT
);

CREATE TABLE IF NOT EXISTS favorites (
    user_id INTEGER NOT NULL,
    video_code TEXT NOT NULL,
    added_at TEXT,
    UNIQUE(user_id, video_code)
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    message TEXT,
    created_at TEXT
);
"""


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with db() as con:
        con.executescript(SCHEMA_SQL)


# --- Users ---

def add_user(user_id: int, username: Optional[str]) -> None:
    with db() as con:
        con.execute(
            "INSERT OR IGNORE INTO users (id, username, joined_at) VALUES (?, ?, ?)",
            (user_id, username, datetime.utcnow().isoformat()),
        )


def set_block(user_id: int, blocked: bool) -> None:
    with db() as con:
        con.execute(
            "UPDATE users SET is_blocked=? WHERE id=?",
            (1 if blocked else 0, user_id),
        )


def is_blocked(user_id: int) -> bool:
    with db() as con:
        row = con.execute("SELECT is_blocked FROM users WHERE id=?", (user_id,)).fetchone()
        return bool(row[0]) if row else False


def get_user_count() -> int:
    with db() as con:
        row = con.execute("SELECT COUNT(*) FROM users").fetchone()
        return int(row[0]) if row else 0


def list_user_ids() -> List[int]:
    with db() as con:
        rows = con.execute("SELECT id FROM users WHERE is_blocked=0").fetchall()
        return [int(r[0]) for r in rows]


# --- Videos ---

def gen_code(length: int = 6) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def add_video(name: str, file_id: str, category: str) -> str:
    code = gen_code()
    with db() as con:
        while True:
            try:
                con.execute(
                    "INSERT INTO videos (code, name, file_id, category, uploaded_at) VALUES (?, ?, ?, ?, ?)",
                    (code, name, file_id, category, datetime.utcnow().isoformat()),
                )
                break
            except sqlite3.IntegrityError:
                code = gen_code()
    return code


def get_video(code: str) -> Optional[sqlite3.Row]:
    with db() as con:
        return con.execute("SELECT * FROM videos WHERE code=?", (code,)).fetchone()


def inc_view(code: str) -> None:
    with db() as con:
        con.execute("UPDATE videos SET views = views + 1 WHERE code=?", (code,))


def count_videos(category: Optional[str] = None, q: Optional[str] = None) -> int:
    with db() as con:
        if q:
            row = con.execute(
                "SELECT COUNT(*) FROM videos WHERE name LIKE ? OR code LIKE ?",
                (f"%{q}%", f"%{q}%"),
            ).fetchone()
            return int(row[0]) if row else 0
        if category:
            row = con.execute(
                "SELECT COUNT(*) FROM videos WHERE category=?",
                (category,),
            ).fetchone()
            return int(row[0]) if row else 0
        row = con.execute("SELECT COUNT(*) FROM videos").fetchone()
        return int(row[0]) if row else 0


def list_videos(page: int, page_size: int = PAGE_SIZE, category: Optional[str] = None, q: Optional[str] = None) -> List[sqlite3.Row]:
    offset = max(page, 0) * page_size
    with db() as con:
        if q:
            return con.execute(
                """
                SELECT code, name, category, views
                FROM videos
                WHERE name LIKE ? OR code LIKE ?
                ORDER BY uploaded_at DESC
                LIMIT ? OFFSET ?
                """,
                (f"%{q}%", f"%{q}%", page_size, offset),
            ).fetchall()
        if category:
            return con.execute(
                """
                SELECT code, name, category, views
                FROM videos
                WHERE category = ?
                ORDER BY uploaded_at DESC
                LIMIT ? OFFSET ?
                """,
                (category, page_size, offset),
            ).fetchall()
        return con.execute(
            """
            SELECT code, name, category, views
            FROM videos
            ORDER BY uploaded_at DESC
            LIMIT ? OFFSET ?
            """,
            (page_size, offset),
        ).fetchall()


def top_videos(limit: int = 5) -> List[sqlite3.Row]:
    with db() as con:
        return con.execute(
            "SELECT code, name, category, views FROM videos ORDER BY views DESC, uploaded_at DESC LIMIT ?",
            (limit,),
        ).fetchall()


# --- Favorites ---

def toggle_fav(user_id: int, code: str) -> bool:
    with db() as con:
        try:
            con.execute(
                "INSERT INTO favorites (user_id, video_code, added_at) VALUES (?, ?, ?)",
                (user_id, code, datetime.utcnow().isoformat()),
            )
            return True  # added
        except sqlite3.IntegrityError:
            con.execute("DELETE FROM favorites WHERE user_id=? AND video_code=?", (user_id, code))
            return False  # removed


def is_fav(user_id: int, code: str) -> bool:
    with db() as con:
        row = con.execute(
            "SELECT 1 FROM favorites WHERE user_id=? AND video_code=?",
            (user_id, code),
        ).fetchone()
        return bool(row)


def list_favs(user_id: int, page: int) -> Tuple[List[sqlite3.Row], int]:
    with db() as con:
        row = con.execute(
            "SELECT COUNT(*) FROM favorites WHERE user_id=?",
            (user_id,),
        ).fetchone()
        total = int(row[0]) if row else 0
        offset = page * PAGE_SIZE
        items = con.execute(
            """
            SELECT v.code, v.name, v.category, v.views
            FROM favorites f
            JOIN videos v ON v.code = f.video_code
            WHERE f.user_id = ?
            ORDER BY f.added_at DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, PAGE_SIZE, offset),
        ).fetchall()
        return items, total


# --- Feedback ---

def add_feedback(user_id: int, message: str) -> None:
    with db() as con:
        con.execute(
            "INSERT INTO feedback (user_id, message, created_at) VALUES (?, ?, ?)",
            (user_id, message, datetime.utcnow().isoformat()),
        )


def count_feedback() -> int:
    with db() as con:
        row = con.execute("SELECT COUNT(*) FROM feedback").fetchone()
        return int(row[0]) if row else 0


# =============================
# Keyboards
# =============================
CATEGORIES = [
    ("👻 Ujis", "horror"),
    ("💓 Romantik", "romantic"),
    ("🎬 Trailer", "trailer"),
]


def main_kb(admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📜 Video Ro'yxati", callback_data="list:0:all")],
        [InlineKeyboardButton("📽️🔎 qidiruv", callback_data="ask_code")],
        [InlineKeyboardButton("🔎 kino kod orqaliy", callback_data="ask_search")],
        [InlineKeyboardButton("⭐ Sevimlilar", callback_data="fav:0")],
        [InlineKeyboardButton("📝 Fikr qoldirish", callback_data="feedback")],
    ]
    if admin:
        rows.append([InlineKeyboardButton("🛠 Admin Panel", callback_data="admin")])
    return InlineKeyboardMarkup(rows)


def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📥 Video yuklash", callback_data="up:start")],
            [InlineKeyboardButton("👤 Foydalanuvchilar", callback_data="adm:users")],
            [InlineKeyboardButton("📬 Xabar tarqatish", callback_data="adm:broadcast")],
            [InlineKeyboardButton("📊 Statistika", callback_data="adm:stats")],
            [InlineKeyboardButton("🚫 Block/Unblock", callback_data="adm:block")],
            [InlineKeyboardButton("⬅️ Orqaga", callback_data="back")],
        ]
    )


def categories_kb(prefix: str = "list", page: int = 0) -> InlineKeyboardMarkup:
    # prefix=list means we will list items in that category
    rows = [[InlineKeyboardButton(text, callback_data=f"{prefix}:{page}:{slug}")] for text, slug in CATEGORIES]
    rows.append([InlineKeyboardButton("📄 Barchasi", callback_data=f"{prefix}:{page}:all")])
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="back")])
    return InlineKeyboardMarkup(rows)


def pagination_kb(prefix: str, page: int, total: int, extra: str = "") -> InlineKeyboardMarkup:
    buttons = []
    max_page = max((total - 1) // PAGE_SIZE, 0)
    if page > 0:
        buttons.append(InlineKeyboardButton("⬅️ Oldingi", callback_data=f"{prefix}:{page-1}{extra}"))
    if page < max_page:
        buttons.append(InlineKeyboardButton("Keyingi ➡️", callback_data=f"{prefix}:{page+1}{extra}"))
    rows = [buttons] if buttons else []
    rows.append([InlineKeyboardButton("🏠 Bosh menyu", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def video_actions_kb(code: str, is_favorite: bool) -> InlineKeyboardMarkup:
    star = "⭐ Olish" if not is_favorite else "🗑 O'chirish"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(star, callback_data=f"fav_toggle:{code}")],
            [InlineKeyboardButton("⬅️ Orqaga", callback_data="back")],
        ]
    )


# =============================
# Handlers
# =============================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    add_user(user.id, user.username)
    if is_blocked(user.id):
        await update.message.reply_text("🚫 Siz bloklangansiz.")
        return
    await update.message.reply_text(
        "🎥 Botga xush kelibsiz! Tanlang:",
        reply_markup=main_kb(admin=user.id in ADMIN_IDS),
    )


async def home_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    user = q.from_user
    try:
        await q.edit_message_text(
            "🏠 Bosh menyu:", 
            reply_markup=main_kb(admin=user.id in ADMIN_IDS)
        )
    except Exception:
        # Agar text yo‘q bo‘lsa yangi xabar yuboriladi
        await q.message.reply_text(
            "🏠 Bosh menyu:", 
            reply_markup=main_kb(admin=user.id in ADMIN_IDS)
        )


# ---------- Listing & search ----------
async def list_entry_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point to select category before listing."""
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("📂 Kategoriya tanlang:", reply_markup=categories_kb())


async def list_page_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    _, page_str, cat = q.data.split(":")
    page = int(page_str)

    if cat == "all":
        total = count_videos()
        items = list_videos(page)
        title = "📜 Barcha videolar"
    else:
        total = count_videos(category=cat)
        items = list_videos(page, category=cat)
        cat_name = next((t for t, s in CATEGORIES if s == cat), cat)
        title = f"📂 {cat_name}"

    if total == 0:
        await q.edit_message_text("❌ Videolar topilmadi.", reply_markup=pagination_kb("list:0:all", 0, 0))
        return

    lines = [f"{i+1+page*PAGE_SIZE}. <b>{row['name']}</b> — <code>{row['code']}</code> (👁 {row['views']})" for i, row in enumerate(items)]
    lines.append("\nKod orqali ko‘rish uchun: <code>KOD</code> ni yuboring yoki tugmadan foydalaning.")

    kb_rows = []
    for row in items:
        kb_rows.append([InlineKeyboardButton(f"▶️ {row['name']} ({row['code']})", callback_data=f"play:{row['code']}")])
    kb_rows.append(pagination_kb("list", page, total, extra=f":{cat}").inline_keyboard[0])
    kb_rows.append([InlineKeyboardButton("🏠 Bosh menyu", callback_data="home")])

    await q.edit_message_text(
        f"{title}:\n\n" + "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb_rows),
    )


async def ask_code_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    context.user_data["awaiting_code"] = True
    await q.edit_message_text("🔢 Kino kodini kiriting (masalan: <code>AB12CD</code>):", parse_mode=ParseMode.HTML)


async def text_message_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = (update.message.text or "").strip()

    if is_blocked(user.id):
        await update.message.reply_text("🚫 Siz bloklangansiz.")
        return

    # --- awaiting code ---
    if context.user_data.pop("awaiting_code", False):
        await play_by_code(update, context, text)
        return

    # --- awaiting search ---
    if context.user_data.get("awaiting_search"):
        query = text
        context.user_data.pop("awaiting_search", None)
        await show_search_results(update, context, query, page=0)
        return

    # --- admin upload flow ---
    if context.user_data.get("up_mode"):
        step = context.user_data.get("up_step")
        if step == "name":
            context.user_data["up_name"] = text
            context.user_data["up_step"] = "category"
            await update.message.reply_text("📂 Kategoriya tanlang:", reply_markup=categories_kb(prefix="upcat"))
            return
        elif step == "broadcast":
            # send broadcast
            await do_broadcast(update, context, text)
            context.user_data.pop("up_mode", None)
            context.user_data.pop("up_step", None)
            return
        elif step == "block":
            try:
                uid = int(text)
                cur = is_blocked(uid)
                set_block(uid, not cur)
                await update.message.reply_text(
                    f"✅ User {uid} {'bloklandi' if not cur else 'blokdan chiqarildi'}"
                )
            except ValueError:
                await update.message.reply_text("ID xato. Raqam kiriting.")
            context.user_data.pop("up_mode", None)
            context.user_data.pop("up_step", None)
            return
        elif step == "feedback":
            add_feedback(user.id, text)
            await update.message.reply_text("✅ Fikringiz qabul qilindi! Rahmat.")
            context.user_data.pop("up_mode", None)
            context.user_data.pop("up_step", None)
            return

    # Default: treat as search shortcut
    if text:
        await show_search_results(update, context, text, page=0)


async def play_by_code(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    row = get_video(code)
    if not row:
        await update.message.reply_text("❌ Bunday kodli video topilmadi.")
        return
    inc_view(code)
    fav = is_fav(update.effective_user.id, code)
    await update.message.reply_video(
        row["file_id"],
        caption=f"<b>{row['name']}</b> — <code>{row['code']}</code>\nKategoriya: <i>{row['category']}</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=video_actions_kb(code, fav),
    )


async def play_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    code = q.data.split(":")[1]
    row = get_video(code)
    if not row:
        await q.edit_message_text("❌ Video topilmadi.")
        return
    inc_view(code)
    fav = is_fav(q.from_user.id, code)
    await q.message.reply_video(
        row["file_id"],
        caption=f"<b>{row['name']}</b> — <code>{row['code']}</code>\nKategoriya: <i>{row['category']}</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=video_actions_kb(code, fav),
    )


# ---------- Favorites ----------
async def fav_list_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    _, page_str = q.data.split(":")
    page = int(page_str)

    items, total = list_favs(q.from_user.id, page)
    if total == 0:
        await q.edit_message_text("⭐ Sevimlilar bo'sh.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Bosh menyu", callback_data="home")]]))
        return

    lines = [f"{i+1+page*PAGE_SIZE}. <b>{row['name']}</b> — <code>{row['code']}</code> (👁 {row['views']})" for i, row in enumerate(items)]
    kb_rows = [[InlineKeyboardButton(f"▶️ {row['name']} ({row['code']})", callback_data=f"play:{row['code']}")] for row in items]
    kb_rows.append(pagination_kb("fav", page, total).inline_keyboard[0])
    kb_rows.append([InlineKeyboardButton("🏠 Bosh menyu", callback_data="home")])

    await q.edit_message_text(
        "⭐ Sevimlilar:\n\n" + "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kb_rows),
    )


async def fav_toggle_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    code = q.data.split(":")[1]
    added = toggle_fav(q.from_user.id, code)
    await q.answer("⭐ Qo'shildi" if added else "🗑 O'chirildi", show_alert=False)


# ---------- Search ----------
async def ask_search_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    context.user_data["awaiting_search"] = True
    await q.edit_message_text("🔎 Qidirmoqchi bo'lgan nom yoki kodni kiriting:")


async def show_search_results(update_or_q, context: ContextTypes.DEFAULT_TYPE, query: str, page: int) -> None:
    # works for Message or CallbackQuery
    total = count_videos(q=query)
    items = list_videos(page, q=query)

    text = f"🔎 Qidiruv natijalari: <b>{query}</b> (topildi: {total})\n\n"
    if total == 0:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Bosh menyu", callback_data="home")]])
        if isinstance(update_or_q, Update):
            await update_or_q.message.reply_text("❌ Hech narsa topilmadi.")
            return
        else:
            await update_or_q.edit_message_text("❌ Hech narsa topilmadi.", reply_markup=kb)
            return

    lines = [f"{i+1+page*PAGE_SIZE}. <b>{row['name']}</b> — <code>{row['code']}</code> (👁 {row['views']})" for i, row in enumerate(items)]
    kb_rows = [[InlineKeyboardButton(f"▶️ {row['name']} ({row['code']})", callback_data=f"play:{row['code']}")] for row in items]
    # pagination key uses prefix 'srch'
    kb_rows.append(pagination_kb("srch", page, total, extra=f":{query}").inline_keyboard[0])
    kb_rows.append([InlineKeyboardButton("🏠 Bosh menyu", callback_data="home")])

    if isinstance(update_or_q, Update):
        await update_or_q.message.reply_text(text + "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb_rows))
    else:
        await update_or_q.edit_message_text(text + "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb_rows))


async def search_page_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    _, page_str, query = q.data.split(":", 2)
    await show_search_results(q, context, query, int(page_str))


# ---------- Feedback ----------
async def feedback_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    context.user_data["up_mode"] = True
    context.user_data["up_step"] = "feedback"
    await q.edit_message_text("📝 Fikringizni yozib yuboring:")


# ---------- Admin panel ----------
async def admin_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("Sizda admin huquqi yo'q!", show_alert=True)
        return
    await q.answer()
    await q.edit_message_text("🛠 Admin panel:", reply_markup=admin_kb())


async def admin_back_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    await home_cb(update, context)


# --- Upload flow ---
async def up_start_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("Adminlar uchun", show_alert=True)
        return
    await q.answer()
    context.user_data["up_mode"] = True
    context.user_data["up_step"] = "video"
    await q.edit_message_text("▶️ Video faylini yuboring (Telegram video).")


async def video_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if is_blocked(user.id):
        return

    # Regular user sending video -> ignore unless admin upload flow
    if user.id not in ADMIN_IDS:
        return

    if not context.user_data.get("up_mode"):
        return

    if context.user_data.get("up_step") != "video":
        return

    v = update.message.video
    if not v:
        await update.message.reply_text("❌ Iltimos, video fayl yuboring.")
        return

    context.user_data["up_file_id"] = v.file_id
    context.user_data["up_step"] = "name"
    await update.message.reply_text("📝 Videoning nomini kiriting:")


async def upcat_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("Adminlar uchun", show_alert=True)
        return
    await q.answer()

    # Expecting category selection in upload flow
    if not (context.user_data.get("up_mode") and context.user_data.get("up_step") == "category"):
        await q.answer("Yuklash oqimi yo'q.", show_alert=True)
        return

    _, page_str, cat = q.data.split(":")
    name = context.user_data.get("up_name")
    file_id = context.user_data.get("up_file_id")

    if not name or not file_id:
        await q.edit_message_text("❌ Oqim buzildi. Qaytadan boshlang.")
        context.user_data.clear()
        return

    code = add_video(name=name, file_id=file_id, category=(cat if cat != "all" else "other"))
    context.user_data.clear()
    await q.edit_message_text(f"✅ Yuklandi!\nNomi: <b>{name}</b>\nKod: <code>{code}</code>", parse_mode=ParseMode.HTML, reply_markup=admin_kb())


# --- Admin users / broadcast / stats / block ---
async def adm_users_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("Adminlar uchun", show_alert=True)
        return
    await q.answer()
    total = get_user_count()
    await q.edit_message_text(f"👥 Jami foydalanuvchilar: <b>{total}</b>", parse_mode=ParseMode.HTML, reply_markup=admin_kb())


async def adm_broadcast_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("Adminlar uchun", show_alert=True)
        return
    await q.answer()
    context.user_data["up_mode"] = True
    context.user_data["up_step"] = "broadcast"
    await q.edit_message_text("📣 Tarqatiladigan xabar matnini yuboring:")


async def do_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    ids = list_user_ids()
    ok = 0
    fail = 0
    for uid in ids:
        try:
            await context.bot.send_message(uid, text)
            ok += 1
        except Exception as e:
            logger.warning("Broadcast fail to %s: %s", uid, e)
            fail += 1
        await asyncio.sleep(0.05)
    await update.message.reply_text(f"📣 Yakunlandi. ✅ {ok} | ❌ {fail}")


async def adm_stats_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("Adminlar uchun", show_alert=True)
        return
    await q.answer()

    users = get_user_count()
    vids = count_videos()
    fb = count_feedback()
    top = top_videos(5)

    text = [
        "📊 Statistika:",
        f"👥 Foydalanuvchilar: <b>{users}</b>",
        f"🎞 Videolar: <b>{vids}</b>",
        f"📝 Fikrlar: <b>{fb}</b>",
        "\n🏆 Top 5 ko‘rilganlar:",
    ]
    for i, r in enumerate(top, 1):
        text.append(f"{i}. <b>{r['name']}</b> — <code>{r['code']}</code> (👁 {r['views']})")

    await q.edit_message_text("\n".join(text), parse_mode=ParseMode.HTML, reply_markup=admin_kb())


async def adm_block_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("Adminlar uchun", show_alert=True)
        return
    await q.answer()
    context.user_data["up_mode"] = True
    context.user_data["up_step"] = "block"
    await q.edit_message_text("🚫 Blok/Unblok qilish uchun user ID ni yuboring:")


# =============================
# Router for callback_data
# =============================
async def callbacks_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    data = q.data

    if data == "home":
        await home_cb(update, context)
        return

    if data == "back":
        await home_cb(update, context)
        return

    if data == "admin":
        await admin_cb(update, context)
        return

    if data == "ask_code":
        await ask_code_cb(update, context)
        return

    if data == "feedback":
        await feedback_cb(update, context)
        return

    if data.startswith("list:") and data.count(":") == 2:
        await list_page_cb(update, context)
        return

    if data == "list:0:all":
        await list_entry_cb(update, context)
        return

    if data.startswith("play:"):
        await play_cb(update, context)
        return

    if data.startswith("fav:"):
        await fav_list_cb(update, context)
        return

    if data.startswith("fav_toggle:"):
        await fav_toggle_cb(update, context)
        return

    if data.startswith("srch:"):
        await search_page_cb(update, context)
        return

    # Upload flow category select
    if data.startswith("upcat:"):
        await upcat_cb(update, context)
        return

    # Admin panel actions
    if data == "adm:users":
        await adm_users_cb(update, context)
        return
    if data == "adm:broadcast":
        await adm_broadcast_cb(update, context)
        return
    if data == "adm:stats":
        await adm_stats_cb(update, context)
        return
    if data == "adm:block":
        await adm_block_cb(update, context)
        return

    if data == "up:start":
        await up_start_cb(update, context)
        return

    if data == "back_admin":
        await admin_cb(update, context)
        return

    if data == "back":
        await home_cb(update, context)
        return


# =============================
# /help and misc
# =============================
HELP_TEXT = (
    "<b>Yordam</b>:\n"
    "• 📜 Video ro'yxati: kategoriyani tanlab ko'ring\n"
    "• 📽️🔎 Kino kodi: videoning <code>kod</code> ini kiriting\n"
    "• ⭐ Sevimlilar: yoqtirganlaringizni saqlang\n"
    "• 🔎 Qidiruv: nom yoki kod bo'yicha toping\n"
    "• 📝 Fikr: taklif va shikoyat yuboring\n"
)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)


# =============================
# Application bootstrap
# =============================
async def post_init(app):
    logger.info("Bot is ready.")


async def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN topilmadi. .env ga BOT_TOKEN=XXXX qo'ying yoki muhit o'zgaruvchisiga kiriting.")

    init_db()

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))

    # Callback router
    app.add_handler(CallbackQueryHandler(callbacks_router))

    # Videos (admin upload)
    app.add_handler(MessageHandler(filters.VIDEO, video_message))

    # Text router (code/search/admin broadcast etc.)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_router))

    logger.info("Running polling...")
    await app.run_polling(close_loop=False)
    
    app.add_handler(CallbackQueryHandler(callbacks_router))



if __name__ == "__main__":
    import nest_asyncio, asyncio
    nest_asyncio.apply()

    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
