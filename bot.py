# TEshortlinksbot
# Single-file Telegram bot (Python) for generating deep links and tracking "solves" via start payloads.
# Designed for deployment on Railway. Data stored in SQLite (bot.db).

"""
Features:
- /generate (owner only) -> creates 10 deep links (t.me/<bot>?start=<token>) and stores them in DB
- /restart  (owner only) -> resets links and creates new 10
- /stats    (owner only) -> overview: total users, total solves
- /user <id|username> (owner only) -> per-user details (which tokens solved, date/time)
- /latest   (owner only) -> recent 10 solve logs
- Users cannot send commands or messages (bot ignores non-owner commands)
- When a user opens the bot with a start parameter (from a shortened url), the bot records the solve.
- Bot replies to the user with "First link solved" on their very first solve and shows total solved count.

Usage idea for owner: after /generate, you'll receive 10 t.me deep links. Manually shorten those 10 links using your preferred urlshortener and drive traffic to them. When users complete the shortener flow they will be redirected to the t.me deep link which opens the bot and registers their solve.

Security: set BOT_TOKEN and OWNER_ID as environment variables in Railway. Do NOT commit your bot token publicly.
"""

import os
import sqlite3
import uuid
from datetime import datetime
from typing import Optional

from telegram import Update, _version_ as ptb_version
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

DB_PATH = 'bot.db'

# -------------------------
# Database helpers
# -------------------------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
    CREATE TABLE IF NOT EXISTS links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT UNIQUE,
        label TEXT,
        created_at TEXT
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE,
        username TEXT,
        first_seen TEXT
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS solves (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        token TEXT,
        solved_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    ''')
    conn.commit()
    conn.close()


def db_execute(query: str, params: tuple = (), fetch: bool = False):
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
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        owner_id = int(os.environ.get('OWNER_ID'))
        user = update.effective_user
        if not user or user.id != owner_id:
            # silently ignore or politely deny
            if update.message:
                await update.message.reply_text('Unauthorized.')
            return
        return await func(update, context)

    return wrapper

# -------------------------
# Bot command handlers
# -------------------------

@owner_only
async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate 10 deep links (tokens). Owner will manually shorten t.me links."""
    bot_username = (await context.bot.get_me()).username
    tokens = []
    now = datetime.utcnow().isoformat()
    for i in range(10):
        token = uuid.uuid4().hex
        label = f'Link-{i+1}'
        db_execute('INSERT INTO links (token, label, created_at) VALUES (?, ?, ?)', (token, label, now))
        tokens.append((label, token))

    lines = []
    for label, token in tokens:
        deep = f'https://t.me/{bot_username}?start={token}'
        lines.append(f'{label}: {deep}')

    text = 'Generated 10 deep links (manually shorten these with your urlshortener):\n\n' + '\n'.join(lines)
    await update.message.reply_text(text)


@owner_only
async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Clear links and solves, then generate new 10
    db_execute('DELETE FROM solves')
    db_execute('DELETE FROM links')
    await update.message.reply_text('Old links and solves cleared. Generating 10 new links...')
    await generate(update, context)


@owner_only
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total_users = db_execute('SELECT COUNT(*) FROM users', fetch=True)[0][0]
    total_solves = db_execute('SELECT COUNT(*) FROM solves', fetch=True)[0][0]
    text = f'Total users: {total_users}\nTotal links solved: {total_solves}'
    await update.message.reply_text(text)


@owner_only
async def user_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text('Usage: /user <telegram_id_or_username>')
        return
    identifier = args[0]
    user_row = None
    if identifier.isdigit():
        user_row = db_execute('SELECT id, telegram_id, username, first_seen FROM users WHERE telegram_id=?', (int(identifier),), fetch=True)
    else:
        # strip leading @ if present
        identifier = identifier.lstrip('@')
        user_row = db_execute('SELECT id, telegram_id, username, first_seen FROM users WHERE username=?', (identifier,), fetch=True)
    if not user_row:
        await update.message.reply_text('User not found in DB.')
        return
    user_row = user_row[0]
    uid = user_row[0]
    solves = db_execute('SELECT token, solved_at FROM solves WHERE user_id=? ORDER BY solved_at', (uid,), fetch=True)
    lines = [f'User: {user_row[2]} ({user_row[1]})\nFirst seen: {user_row[3]}\n\nSolved links:']
    if not solves:
        lines.append('No solves yet')
    else:
        for tok, when in solves:
            # find label for token
            lbl = db_execute('SELECT label FROM links WHERE token=?', (tok,), fetch=True)
            lbl = lbl[0][0] if lbl else tok
            lines.append(f'- {lbl} | token: {tok} | at: {when}')
    await update.message.reply_text('\n'.join(lines))


@owner_only
async def latest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db_execute('SELECT s.token, s.solved_at, u.telegram_id, u.username FROM solves s JOIN users u ON s.user_id=u.id ORDER BY s.solved_at DESC LIMIT 10', fetch=True)
    if not rows:
        await update.message.reply_text('No recent solves')
        return
    lines = []
    for token, solved_at, tg_id, username in rows:
        lbl = db_execute('SELECT label FROM links WHERE token=?', (token,), fetch=True)
        lbl = lbl[0][0] if lbl else token
        lines.append(f'{solved_at} | {username} ({tg_id}) | {lbl} | {token}')
    await update.message.reply_text('\n'.join(lines))


# -------------------------
# Start handler: records solves when start param present
# -------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    now = datetime.utcnow().isoformat()

    # Ensure user exists in users table
    existing = db_execute('SELECT id FROM users WHERE telegram_id=?', (user.id,), fetch=True)
    if not existing:
        db_execute('INSERT INTO users (telegram_id, username, first_seen) VALUES (?, ?, ?)', (user.id, user.username or '', now))
        user_db_id = db_execute('SELECT id FROM users WHERE telegram_id=?', (user.id,), fetch=True)[0][0]
    else:
        user_db_id = existing[0][0]

    if args:
        token = args[0]
        # check token exists
        tok_exists = db_execute('SELECT label FROM links WHERE token=?', (token,), fetch=True)
        if tok_exists:
            # check if this user already solved this token
            already = db_execute('SELECT id FROM solves WHERE user_id=? AND token=?', (user_db_id, token), fetch=True)
            if already:
                await update.message.reply_text('You have already solved this link.')
                return
            # record solve
            db_execute('INSERT INTO solves (user_id, token, solved_at) VALUES (?, ?, ?)', (user_db_id, token, now))
            # count solves for this user
            total = db_execute('SELECT COUNT(*) FROM solves WHERE user_id=?', (user_db_id,), fetch=True)[0][0]
            if total == 1:
                await update.message.reply_text('First link solved ✅\nThank you!')
            else:
                await update.message.reply_text(f'Link solved ✅\nYou have solved {total} links so far.')
            return
        else:
            await update.message.reply_text('Invalid or expired link token.')
            return

    # If no args, just greet but disable user commands
    await update.message.reply_text('Welcome. This bot only records solves via special links.')


# Ignore messages and commands from non-owner (except start)
async def ignore_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # If owner, allow normal processing
    owner_id = int(os.environ.get('OWNER_ID'))
    user = update.effective_user
    if user and user.id == owner_id:
        # allow owner messages to be processed by handlers (owner uses commands)
        return
    # otherwise silently ignore or send short note
    if update.message:
        # don't allow chat, comments or commands from users
        await update.message.reply_text('This bot does not accept messages or commands. Use the links provided.')


# -------------------------
# Main
# -------------------------

def main():
    init_db()
    token = os.environ.get('BOT_TOKEN')
    owner = os.environ.get('OWNER_ID')
    if not token or not owner:
        print('ERROR: BOT_TOKEN and OWNER_ID must be set as environment variables')
        return

    app = Application.builder().token(token).build()

    # Owner commands
    app.add_handler(CommandHandler('generate', generate))
    app.add_handler(CommandHandler('restart', restart))
    app.add_handler(CommandHandler('stats', stats))
    app.add_handler(CommandHandler('user', user_info))
    app.add_handler(CommandHandler('latest', latest))

    # Start handler (captures start payloads)
    app.add_handler(CommandHandler('start', start))

    # All other messages ignored
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), ignore_messages))

    print('Bot is running...')
    app.run_polling()


if _name_ == '_main_':
    main()
