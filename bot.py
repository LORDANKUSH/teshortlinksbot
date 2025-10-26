import os
import sqlite3
import uuid
from datetime import datetime
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# --- Database Setup ---
DB_PATH = "bot.db"
OWNER_ID = int(os.environ.get("OWNER_ID", "123456789"))  # your ID or set in Railway vars

conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT UNIQUE,
    used INTEGER DEFAULT 0,
    used_by INTEGER,
    used_at TEXT
)
""")
conn.commit()


# --- Helper Functions ---
def create_links(count=10):
    links = []
    for _ in range(count):
        token = str(uuid.uuid4())[:8]
        cur.execute("INSERT OR IGNORE INTO links (token) VALUES (?)", (token,))
        links.append(token)
    conn.commit()
    return links


def mark_link_used(token, user_id):
    cur.execute("SELECT used FROM links WHERE token=?", (token,))
    row = cur.fetchone()
    if not row:
        return "❌ Invalid link."
    if row[0] == 1:
        return "⚠️ This link has already been used."

    cur.execute("UPDATE links SET used=1, used_by=?, used_at=? WHERE token=?",
                (user_id, datetime.now().isoformat(), token))
    conn.commit()
    return "✅ Link verified successfully!"


# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    if args:
        token = args[0]
        msg = mark_link_used(token, user.id)
        await update.message.reply_text(msg)
    else:
        await update.message.reply_text("👋 Welcome! Use a deep link to verify.")


async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ You are not authorized.")
        return

    links = create_links()
    base = f"https://t.me/{context.bot.username}?start="
    text = "\n".join([base + token for token in links])
    await update.message.reply_text(f"✅ Generated links:\n\n{text}")


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ You are not authorized.")
        return

    cur.execute("SELECT COUNT(*) FROM links WHERE used=1")
    used = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM links WHERE used=0")
    unused = cur.fetchone()[0]

    await update.message.reply_text(f"📊 Stats:\nUsed: {used}\nUnused: {unused}")


async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ You are not authorized.")
        return

    cur.execute("DELETE FROM links")
    conn.commit()
    await update.message.reply_text("♻️ Database reset complete!")


# --- Main ---
def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        print("❌ BOT_TOKEN not found in environment variables.")
        return

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("generate", generate))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("restart", restart))

    print("✅ Bot is running...")
    app.run_polling(stop_signals=None)


if __name__ == "__main__":
    main()
