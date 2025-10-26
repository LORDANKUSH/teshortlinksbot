# bot.py
"""
TEshortlinksbot
- Generates 10 deep links (owner only)
- Records solves when users open the bot with start payload
- Stores data in SQLite (bot.db)
- Owner-only commands: /generate, /restart, /stats, /user, /latest
- Users cannot send messages/commands (bot replies that messaging is disabled)
"""

import os
import sqlite3
import uuid
from datetime import datetime
from functools import wraps
from typing import Optional

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

DB_PATH = "bot.db"


# -------------------------
# Database helpers
# -------------------------
def init_db() -> None:
    """Create tables if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS links (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      token TEXT UNIQUE,
      label TEXT,
      created_at TEXT
    )
    """
    )
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      telegram_id INTEGER UNIQUE,
      username TEXT,
      first_seen TEXT
    )
    """
    )
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS solves (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER,
      token TEXT,
      solved_at TEXT,
      FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """
    )
    conn.commit()
    conn.close()


def db_execute(query: str, params: tuple = (), fetch: bool = False):
    """
    Execute a DB query safely. Returns fetched rows if fetch=True.
    Opens and closes a new connection each call (safe for async context).
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(query, params)
    data = None
    if fetch:
        data = cur.fetchall()
    conn.commit()
    conn.close()
    return data


# -------------------------
# Owner check helper
# -------------------------
def owner_only(func):
    """Decorator to allow only OWNER_ID to run the command."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        owner_env = os.environ.get("OWNER_ID")
        try:
            owner_id = int(owner_env) if owner_env else None
        except ValueError:
            owner_id = None

        user = update.effective_user
        if not user or owner_id is None or user.id != owner_id:
            # If owner env not set, politely notify in logs (do not reveal token)
            # For user-facing: short unauthorized message
            if update.message:
                await update.message.reply_text("Unauthorized.")
            return
        return await func(update, context)

    return wrapper


# -------------------------
# Command handlers (owner)
# -------------------------
@owner_only
async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate 10 deep links (tokens). Owner will manually shorten these."""
    bot_user = await context.bot.get_me()
    bot_username = bot_user.username
    tokens = []
    now = datetime.utcnow().isoformat()
    for i in range(10):
        token = uuid.uuid4().hex
        label = f"Link-{i+1}"
        db_execute(
            "INSERT INTO links (token, label, created_at) VALUES (?, ?, ?)",
            (token, label, now),
        )
        tokens.append((label, token))

    lines = []
    for label, token in tokens:
        deep = f"https://t.me/{bot_username}?start={token}"
        lines.append(f"{label}: {deep}")

    text = (
        "Generated 10 deep links (manually shorten these with your urlshortener):\n\n"
        + "\n".join(lines)
    )
    await update.message.reply_text(text)


@owner_only
async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear links and solves, then create 10 new links (calls generate)."""
    db_execute("DELETE FROM solves")
    db_execute("DELETE FROM links")
    await update.message.reply_text("Old links and solves cleared. Generating 10 new links...")
    await generate(update, context)


@owner_only
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show total users and total solves."""
    total_users_row = db_execute("SELECT COUNT(*) FROM users", fetch=True)
    total_solves_row = db_execute("SELECT COUNT(*) FROM solves", fetch=True)
    total_users = total_users_row[0][0] if total_users_row else 0
    total_solves = total_solves_row[0][0] if total_solves_row else 0
    text = f"Total users: {total_users}\nTotal links solved: {total_solves}"
    await update.message.reply_text(text)


@owner_only
async def user_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/user <id|username> -> details for that user (solved tokens with date/time)."""
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /user <telegram_id_or_username>")
        return
    identifier = args[0]
    user_row = None
    if identifier.isdigit():
        rows = db_execute(
            "SELECT id, telegram_id, username, first_seen FROM users WHERE telegram_id=?",
            (int(identifier),),
            fetch=True,
        )
    else:
        identifier = identifier.lstrip("@")
        rows = db_execute(
            "SELECT id, telegram_id, username, first_seen FROM users WHERE username=?",
            (identifier,),
            fetch=True,
        )
    if not rows:
        await update.message.reply_text("User not found in DB.")
        return
    user_row = rows[0]
    uid = user_row[0]
    solves = db_execute(
        "SELECT token, solved_at FROM solves WHERE user_id=? ORDER BY solved_at", (uid,), fetch=True
    )
    lines = [f"User: {user_row[2]} ({user_row[1]})\nFirst seen: {user_row[3]}\n\nSolved links:"]
    if not solves:
        lines.append("No solves yet")
    else:
        for tok, when in solves:
            lbl_row = db_execute("SELECT label FROM links WHERE token=?", (tok,), fetch=True)
            lbl = lbl_row[0][0] if lbl_row else tok
            lines.append(f"- {lbl} | token: {tok} | at: {when}")
    await update.message.reply_text("\n".join(lines))


@owner_only
async def latest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent 10 solve logs (most recent first)."""
    rows = db_execute(
        "SELECT s.token, s.solved_at, u.telegram_id, u.username "
        "FROM solves s JOIN users u ON s.user_id=u.id "
        "ORDER BY s.solved_at DESC LIMIT 10",
        fetch=True,
    )
    if not rows:
        await update.message.reply_text("No recent solves")
        return
    lines = []
    for token, solved_at, tg_id, username in rows:
        lbl_row = db_execute("SELECT label FROM links WHERE token=?", (token,), fetch=True)
        lbl = lbl_row[0][0] if lbl_row else token
        lines.append(f"{solved_at} | {username} ({tg_id}) | {lbl} | {token}")
    await update.message.reply_text("\n".join(lines))


# -------------------------
# Start handler: records solves when start param present
# -------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    When user opens bot (maybe via shortener -> deep link), this records solves.
    If user has solved first link, reply "First link solved", otherwise reply with count.
    """
    user = update.effective_user
    if not user:
        return

    now = datetime.utcnow().isoformat()

    # Ensure user exists in users table
    existing = db_execute("SELECT id FROM users WHERE telegram_id=?", (user.id,), fetch=True)
    if not existing:
        db_execute(
            "INSERT INTO users (telegram_id, username, first_seen) VALUES (?, ?, ?)",
            (user.id, user.username or "", now),
        )
        user_db_id_row = db_execute("SELECT id FROM users WHERE telegram_id=?", (user.id,), fetch=True)
        user_db_id = user_db_id_row[0][0]
    else:
        user_db_id = existing[0][0]

    args = context.args
    if args:
        token = args[0]
        tok_exists = db_execute("SELECT label FROM links WHERE token=?", (token,), fetch=True)
        if tok_exists:
            already = db_execute(
                "SELECT id FROM solves WHERE user_id=? AND token=?", (user_db_id, token), fetch=True
            )
            if already:
                await update.message.reply_text("You have already solved this link.")
                return
            db_execute(
                "INSERT INTO solves (user_id, token, solved_at) VALUES (?, ?, ?)",
                (user_db_id, token, now),
            )
            total_row = db_execute("SELECT COUNT(*) FROM solves WHERE user_id=?", (user_db_id,), fetch=True)
            total = total_row[0][0] if total_row else 0
            if total == 1:
                await update.message.reply_text("First link solved ✅\nThank you!")
            else:
                await update.message.reply_text(f"Link solved ✅\nYou have solved {total} links so far.")
            return
        else:
            await update.message.reply_text("Invalid or expired link token.")
            return

    # If no args, greet but inform bot only records solves
    await update.message.reply_text("Welcome. This bot only records solves via special links.")


# -------------------------
# Ignore messages from non-owner
# -------------------------
async def ignore_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    This captures all messages (text, stickers, commands not handled above).
    If the sender is owner, do nothing (owner commands are handled by CommandHandlers).
    Otherwise inform them that messaging is disabled.
    """
    owner_env = os.environ.get("OWNER_ID")
    try:
        owner_id = int(owner_env) if owner_env else None
    except ValueError:
        owner_id = None

    user = update.effective_user
    if user and owner_id and user.id == owner_id:
        # Owner — let owner use commands (owner command handlers are separate)
        return

    # For regular users: short polite reply
    if update.message:
        await update.message.reply_text("This bot does not accept messages or commands. Use the links provided.")


# -------------------------
# Main
# -------------------------
def main():
    init_db()
    token = os.environ.get("BOT_TOKEN")
    owner = os.environ.get("OWNER_ID")
    if not token or not owner:
        print("ERROR: BOT_TOKEN and OWNER_ID must be set as environment variables")
        return

    app = Application.builder().token(token).build()

    # Owner commands
    app.add_handler(CommandHandler("generate", generate))
    app.add_handler(CommandHandler("restart", restart))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("user", user_info))
    app.add_handler(CommandHandler("latest", latest))

    # Start handler (everyone can start)
    app.add_handler(CommandHandler("start", start))

    # Fallback: ignore all other messages/commands from non-owner
    # This should be last so other handlers run first.
    app.add_handler(MessageHandler(filters.ALL, ignore_messages))

    print("Bot is running...")
    app.run_polling()


if _name_ == "_main_":
    main()
