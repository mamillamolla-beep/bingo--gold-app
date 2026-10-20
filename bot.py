import time
import os
import json
import random
import sqlite3

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ========= SETTINGS =========

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

# እዚህ የራስህን Telegram numeric ID አስገባ
ADMIN_IDS = {5850978599}

CARD_PRICE = 10
PRIZE_CREDITS = 100
MAX_CARDS_PER_PLAYER = 2
DB_FILE = "bingo.db"

# ============================


def connect_db():
    db = sqlite3.connect(DB_FILE, timeout=20)
    db.row_factory = sqlite3.Row
    return db


def setup_db():
    with connect_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                chat_id INTEGER,
                balance INTEGER NOT NULL DEFAULT 0
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS rounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                active INTEGER NOT NULL DEFAULT 0,
                called TEXT NOT NULL DEFAULT '[]',
                winner_id INTEGER
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                round_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                numbers TEXT NOT NULL
            )
        """)
        columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(rounds)")
        }

        if "buy_until" not in columns:
            db.execute(
                "ALTER TABLE rounds ADD COLUMN buy_until REAL"
            )
        count = db.execute(
            "SELECT COUNT(*) FROM rounds"
        ).fetchone()[0]

        if count == 0:
            db.execute(
                "INSERT INTO rounds (active) VALUES (0)"
            )


def register_user(update):
    user = update.effective_user
    chat = update.effective_chat

    with connect_db() as db:
        db.execute("""
            INSERT INTO users (user_id, username, chat_id)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                chat_id = excluded.chat_id
        """, (
            user.id,
            user.username or user.first_name,
            chat.id,
        ))


def current_round(db):
    return db.execute(
        "SELECT * FROM rounds ORDER BY id DESC LIMIT 1"
    ).fetchone()


def make_card():
    columns = [
        random.sample(range(1, 16), 5),
        random.sample(range(16, 31), 5),
        random.sample(range(31, 46), 5),
        random.sample(range(46, 61), 5),
        random.sample(range(61, 76), 5),
    ]

    card = [
        [columns[c][r] for c in range(5)]
        for r in range(5)
    ]

    card[2][2] = 0
    return card


def format_card(card, called):
    lines = [
        " B    I    N    G    O"
    ]

    for r in range(5):
        row = []

        for c in range(5):
            n = card[r][c]

            if n == 0:
                value = " ★ "
            elif n in called:
                value = f"[{n:02d}]"
            else:
                value = f" {n:02d} "

            row.append(value)

        lines.append(" ".join(row))

    return "\n".join(lines)


def has_bingo(card, called):
    called = set(called)

    marked = {
        (r, c)
        for r in range(5)
        for c in range(5)
        if card[r][c] == 0 or card[r][c] in called
    }

    # 1. ሙሉ ረድፍ
    for r in range(5):
        if all((r, c) in marked for c in range(5)):
            return True

    # 2. ሙሉ አምድ
    for c in range(5):
        if all((r, c) in marked for r in range(5)):
            return True

    # 3. ዲያጎናል: ግራ ላይኛው ወደ ቀኝ ታችኛው
    if all((i, i) in marked for i in range(5)):
        return True

    # 4. ዲያጎናል: ቀኝ ላይኛው ወደ ግራ ታችኛው
    if all((i, 4 - i) in marked for i in range(5)):
        return True

    # 5. አራቱ ማዕዘኖች
    corners = {(0, 0), (0, 4), (4, 0), (4, 4)}
    if corners.issubset(marked):
        return True

    return False


def is_admin(user_id):
    return user_id in ADMIN_IDS


def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🎟️ ካርድ ግዛ",
                callback_data="buy"
            )
        ],
        [
            InlineKeyboardButton(
                "🃏 ካርዶቼ",
                callback_data="mycards"
            )
        ],
        [
            InlineKeyboardButton(
                "💰 ቀሪ ሂሳብ",
                callback_data="balance"
            )
        ],
        [
            InlineKeyboardButton(
                "🏆 BINGO አረጋግጥ",
                callback_data="claim"
            )
        ],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update)

    await update.message.reply_text(
        "🎱 እንኳን ወደ Bingo Bot በደህና መጡ!\n\n"
        f"🎟️ የአንድ ካርድ ዋጋ: {CARD_PRICE} ክሬዲት\n"
        f"🏆 የተወሰነ ሽልማት: {PRIZE_CREDITS} ክሬዲት\n\n"
        "ትዕዛዞች:\n"
        "/play - የጨዋታ ምናሌ\n"
        "/balance - ቀሪ ሂሳብ\n"
        "/mycards - የገዛኋቸው ካርዶች\n"
        "/help - እገዛ",
        reply_markup=main_keyboard()
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        "/play - ካርድ ለመግዛት\n"
        "/balance - ቀሪ ሂሳብ\n"
        "/mycards - ካርዶችህን ለማየት\n\n"
        "ካርድ ለመግዛት በሂሳብህ ውስጥ በቂ ክሬዲት "
        "ሊኖር ይገባል።\n"
        "የውስጥ ክሬዲት እውነተኛ ገንዘብ አይደለም።"
    )


async def play(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update)

    with connect_db() as db:
        game = current_round(db)

    if not game["active"]:
        await update.message.reply_text(
            "⏳ አሁን ጨዋታ አልተጀመረም። "
            "አስተዳዳሪው እስኪጀምር ይጠብቁ።",
            reply_markup=main_keyboard()
        )
        return

    await update.message.reply_text(
        f"🎱 Bingo ጨዋታ #{game['id']}\n"
        f"🎟️ የካርድ ዋጋ: {CARD_PRICE} ክሬዲት\n"
        f"🏆 ሽልማት: {PRIZE_CREDITS} ክሬዲት\n"
        "ከታች ያለውን ቁልፍ ተጠቅመው ካርድ ይግዙ።",
        reply_markup=main_keyboard()
    )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update)
    user_id = update.effective_user.id

    with connect_db() as db:
        user = db.execute(
            "SELECT balance FROM users WHERE user_id = ?",
            (user_id,)
        ).fetchone()

    await update.message.reply_text(
        f"💰 ቀሪ ሂሳብህ: {user['balance']} ክሬዲት"
    )


async def mycards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update)
    user_id = update.effective_user.id

    with connect_db() as db:
        game = current_round(db)

        cards = db.execute("""
            SELECT id, numbers FROM cards
            WHERE round_id = ? AND user_id = ?
            ORDER BY id
        """, (game["id"], user_id)).fetchall()

        called = set(json.loads(game["called"]))

    if not cards:
        await update.message.reply_text(
            "አሁን በዚህ ጨዋታ የገዛኸው ካርድ የለም።"
        )
        return

    for item in cards:
        card = json.loads(item["numbers"])

        await update.message.reply_text(
            f"🃏 ካርድ #{item['id']}\n"
            + format_card(card, called)
        )


async def buy_card(query):
    user_id = query.from_user.id

    with connect_db() as db:
        db.execute("BEGIN IMMEDIATE")

        game = current_round(db)

        if not game or not game["active"]:
            db.rollback()
            await query.answer(
                "ጨዋታው አልተጀመረም።",
                show_alert=True
            )
            return

        if (
            game["buy_until"] is not None
            and time.time() >= game["buy_until"]
        ):
            db.rollback()
            await query.answer(
                "የካርድ ግዢ ጊዜ አልቋል። ቀጣይ ዙርን ይጠብቁ።",
                show_alert=True
            )
            return

        user = db.execute(
            "SELECT balance FROM users WHERE user_id = ?",
            (user_id,)
        ).fetchone()

        if user is None:
            db.rollback()
            await query.answer(
                "መጀመሪያ /start ይጫኑ።",
                show_alert=True
            )
            return

        owned = db.execute("""
            SELECT COUNT(*) FROM cards
            WHERE round_id = ? AND user_id = ?
        """, (game["id"], user_id)).fetchone()[0]

        if owned >= MAX_CARDS_PER_PLAYER:
            db.rollback()
            await query.answer(
                "በዚህ ጨዋታ ከ2 ካርድ በላይ አይፈቀድም።",
                show_alert=True
            )
            return

        if user["balance"] < CARD_PRICE:
            db.rollback()
            await query.answer(
                "በቂ ክሬዲት የለህም። አስተዳዳሪውን አግኝ።",
                show_alert=True
            )
            return

        card = make_card()

        db.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ?",
            (CARD_PRICE, user_id)
        )

        db.execute("""
            INSERT INTO cards (round_id, user_id, numbers)
            VALUES (?, ?, ?)
        """, (game["id"], user_id, json.dumps(card)))

        db.commit()

    await query.answer("ካርድ ተገዝቷል!")
    await query.message.reply_text(
        f"✅ ካርድህ ተገዝቷል።\n"
        f"💳 የተቀነሰው: {CARD_PRICE} ክሬዲት\n"
        f"🃏 ካርድህ:\n{format_card(card, set())}"
    )


async def show_balance(query):
    with connect_db() as db:
        user = db.execute(
            "SELECT balance FROM users WHERE user_id = ?",
            (query.from_user.id,)
        ).fetchone()

    amount = user["balance"] if user else 0

    await query.answer(
        f"ቀሪ ሂሳብ: {amount} ክሬዲት",
        show_alert=True
    )


async def show_cards(query):
    with connect_db() as db:
        game = current_round(db)

        cards = db.execute("""
            SELECT id, numbers FROM cards
            WHERE round_id = ? AND user_id = ?
            ORDER BY id
        """, (game["id"], query.from_user.id)).fetchall()

        called = set(json.loads(game["called"]))

    await query.answer()

    if not cards:
        await query.message.reply_text(
            "በዚህ ጨዋታ ካርድ አልገዛህም።"
        )
        return

    for item in cards:
        await query.message.reply_text(
            f"🃏 ካርድ #{item['id']}\n"
            + format_card(
                json.loads(item["numbers"]),
                called
            )
        )


async def claim_bingo(query):
    user_id = query.from_user.id

    with connect_db() as db:
        db.execute("BEGIN IMMEDIATE")

        game = current_round(db)

        if not game["active"]:
            db.rollback()
            await query.answer(
                "ጨዋታው አልተጀመረም።",
                show_alert=True
            )
            return

        if game["winner_id"] is not None:
            db.rollback()
            await query.answer(
                "አሸናፊ ተወስኗል።",
                show_alert=True
            )
            return

        called = set(json.loads(game["called"]))

        cards = db.execute("""
            SELECT numbers FROM cards
            WHERE round_id = ? AND user_id = ?
        """, (game["id"], user_id)).fetchall()

        winning = any(
            has_bingo(json.loads(item["numbers"]), called)
            for item in cards
        )

        if not winning:
            db.rollback()
            await query.answer(
                "ገና Bingo አልሆነም።",
                show_alert=True
            )
            return

        changed = db.execute("""
            UPDATE rounds
            SET winner_id = ?, active = 0
            WHERE id = ? AND active = 1 AND winner_id IS NULL
        """, (user_id, game["id"])).rowcount

        if changed != 1:
            db.rollback()
            await query.answer(
                "አሸናፊ ቀድሞ ተወስኗል።",
                show_alert=True
            )
            return

        db.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (PRIZE_CREDITS, user_id)
        )

        db.commit()

    await query.answer("🎉 Bingo! አሸንፈሃል!", show_alert=True)

    await query.message.reply_text(
        f"🏆 BINGO!\n"
        f"አሸናፊ: {query.from_user.first_name}\n"
        f"የተጨመረው ሽልማት: {PRIZE_CREDITS} ክሬዲት"
    )


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    register_user(update)

    if data == "buy":
        await buy_card(query)
    elif data == "balance":
        await show_balance(query)
    elif data == "mycards":
        await show_cards(query)
    elif data == "claim":
        await claim_bingo(query)


async def admin_only(update):
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.effective_message.reply_text(
            "⛔ ይህ ትዕዛዝ ለአስተዳዳሪ ብቻ ነው።"
        )
        return False

    return True


async def startgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return

    with connect_db() as db:
        old = current_round(db)

        if old["active"]:
            await update.message.reply_text(
                "ጨዋታ አስቀድሞ እየተካሄደ ነው።"
            )
            return

        db.execute(
            "INSERT INTO rounds (active, called) VALUES (1, '[]')"
        )
        game = current_round(db)
        chats = db.execute(
            "SELECT chat_id FROM users"
        ).fetchall()

    await update.message.reply_text(
        f"✅ Bingo ጨዋታ #{game['id']} ተጀምሯል።"
    )

    for item in chats:
        try:
            await context.bot.send_message(
                chat_id=item["chat_id"],
                text=(
                    f"🎱 Bingo ጨዋታ #{game['id']} ተጀምሯል!\n"
                    "/play ብለው ካርድ ይግዙ።"
                )
            )
        except Exception:
            pass


async def call_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return

    with connect_db() as db:
        db.execute("BEGIN IMMEDIATE")
        game = current_round(db)

        if not game["active"]:
            db.rollback()
            await update.message.reply_text(
                "ንቁ ጨዋታ የለም። /startgame ይጠቀሙ።"
            )
            return

        called = set(json.loads(game["called"]))
        available = [
            n for n in range(1, 76)
            if n not in called
        ]

        if not available:
            db.rollback()
            await update.message.reply_text(
                "75ቱም ቁጥሮች ተጠርተዋል።"
            )
            return

        number = random.choice(available)
        called.add(number)

        db.execute(
            "UPDATE rounds SET called = ? WHERE id = ?",
            (json.dumps(sorted(called)), game["id"])
        )

        chats = db.execute(
            "SELECT chat_id FROM users"
        ).fetchall()

        db.commit()

    letter = (
        "B" if number <= 15 else
        "I" if number <= 30 else
        "N" if number <= 45 else
        "G" if number <= 60 else
        "O"
    )

    message = (
        f"🎲 የተጠራው ቁጥር: {letter}-{number}\n"
        f"በዚህ ጨዋታ የተጠሩ ቁጥሮች: {len(called)}/75\n"
        "ካርድህን በ /mycards ተመልከት።"
    )


    for item in chats:
        try:
            await context.bot.send_message(
                chat_id=item["chat_id"],
                text=message,
                reply_markup=main_keyboard()
            )
        except Exception:
            pass


async def endgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return

    with connect_db() as db:
        game = current_round(db)

        if not game["active"]:
            await update.message.reply_text(
                "ንቁ ጨዋታ የለም።"
            )
            return

        db.execute(
            "UPDATE rounds SET active = 0 WHERE id = ?",
            (game["id"],)
        )

    await update.message.reply_text(
        f"🛑 ጨዋታ #{game['id']} ተዘግቷል።"
    )


async def credit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "አጠቃቀም:\n/credit USER_ID AMOUNT\n"
            "ምሳሌ: /credit 123456789 100"
        )
        return

    try:
        user_id = int(context.args[0])
        amount = int(context.args[1])
    except ValueError:
        await update.message.reply_text(
            "USER_ID እና AMOUNT ቁጥሮች መሆን አለባቸው።"
        )
        return

    if amount <= 0:
        await update.message.reply_text(
            "መጠኑ ከ0 በላይ መሆን አለበት።"
        )
        return

    with connect_db() as db:
        user = db.execute(
            "SELECT user_id FROM users WHERE user_id = ?",
            (user_id,)
        ).fetchone()

        if user is None:
            await update.message.reply_text(
                "ተጫዋቹ አልተመዘገበም። "
                "ተጫዋቹ መጀመሪያ /start ይላክ።"
            )
            return

        db.execute(
            "UPDATE users SET balance = balance + ? WHERE user_id = ?",
            (amount, user_id)
        )

        new_balance = db.execute(
            "SELECT balance FROM users WHERE user_id = ?",
            (user_id,)
        ).fetchone()["balance"]

    await update.message.reply_text(
        f"✅ {amount} ክሬዲት ተጨምሯል።\n"
        f"አዲስ ቀሪ ሂሳብ: {new_balance}"
    )

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"💰 {amount} ክሬዲት ተጨምሮልሃል።"
        )
    except Exception:
        pass


async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return

    await update.message.reply_text(
        "የአስተዳዳሪ ትዕዛዞች:\n\n"
        "/startgame - አዲስ ዙር ጀምር\n"
        "/call - አዲስ ቁጥር ጥራ\n"
        "/endgame - ጨዋታውን ዝጋ\n"
        "/credit USER_ID AMOUNT - ክሬዲት ጨምር\n"
        "/admin - ይህን ዝርዝር አሳይ"
    )


def main():
    if BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
        print("እባክዎ BOT_TOKEN በbot.py ውስጥ ያስገቡ።")
        return

    if 123456789 in ADMIN_IDS:
        print("እባክዎ ADMIN_IDS ውስጥ የራስዎን Telegram ID ያስገቡ።")
        return

    setup_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("play", play))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("mycards", mycards))

    app.add_handler(CommandHandler("startgame", startgame))
    app.add_handler(CommandHandler("call", call_number))
    app.add_handler(CommandHandler("endgame", endgame))
    app.add_handler(CommandHandler("credit", credit))
    app.add_handler(CommandHandler("admin", admin_help))

    app.add_handler(CallbackQueryHandler(buttons))
    from auto_game import auto_game_start

    if app.job_queue is None:
        raise RuntimeError("JobQueue አልተገናኘም")

    app.job_queue.run_once(
        auto_game_start,
        5,
        name="bingo-auto-start"
    )

    print("Bingo Bot is running!")
    app.run_polling()


if __name__ == "__main__":
    main()
