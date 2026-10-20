import json
import random
import sqlite3
import time

from telegram.ext import ContextTypes

PRIZE_CREDITS = 100
DB_FILE = "bingo.db"
BUY_SECONDS = 30
CALL_SECONDS = 5
NEXT_ROUND_SECONDS = 30


def connect_db():
    db = sqlite3.connect(DB_FILE, timeout=20)
    db.row_factory = sqlite3.Row
    return db


def current_round(db):
    return db.execute(
        "SELECT * FROM rounds ORDER BY id DESC LIMIT 1"
    ).fetchone()


def has_bingo(card, called):
    marked = {
        (r, c)
        for r in range(5)
        for c in range(5)
        if card[r][c] == 0 or card[r][c] in called
    }
    return (
        any(all((r, c) in marked for c in range(5)) for r in range(5))
        or any(all((r, c) in marked for r in range(5)) for c in range(5))
    )


def find_winner(db, game):
    called = set(json.loads(game["called"]))
    cards = db.execute(
        "SELECT user_id, numbers FROM cards WHERE round_id = ? ORDER BY id",
        (game["id"],),
    ).fetchall()

    for item in cards:
        card = json.loads(item["numbers"])
        if has_bingo(card, called):
            return item["user_id"]
    return None


async def broadcast(context, message):
    with connect_db() as db:
        chats = db.execute(
            "SELECT DISTINCT chat_id FROM users WHERE chat_id IS NOT NULL"
        ).fetchall()

    for row in chats:
        try:
            await context.bot.send_message(
                chat_id=row["chat_id"], text=message
            )
        except Exception:
            pass


async def finish_round(context, round_id, winner_id=None):
    with connect_db() as db:
        db.execute("BEGIN IMMEDIATE")
        changed = db.execute(
            "UPDATE rounds SET active = 0, winner_id = ? "
            "WHERE id = ? AND active = 1 AND winner_id IS NULL",
            (winner_id, round_id),
        ).rowcount

        if changed != 1:
            db.rollback()
            return

        if winner_id is not None:
            db.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                (PRIZE_CREDITS, winner_id),
            )
        db.commit()

    if winner_id is not None:
        with connect_db() as db:
            user = db.execute(
                "SELECT username FROM users WHERE user_id = ?",
                (winner_id,),
            ).fetchone()

        name = user["username"] if user and user["username"] else str(winner_id)
        message = (
            f"🏆 BINGO GOLD!\nአሸናፊ: {name}\n"
            f"🎁 ሽልማት: {PRIZE_CREDITS} ክሬዲት\n"
            f"⏳ ቀጣይ ዙር በ{NEXT_ROUND_SECONDS} ሰከንድ ይጀምራል።"
        )
    else:
        message = (
            f"ዙር #{round_id}: 75ቱም ቁጥሮች ተጠርተዋል፤ "
            "አሸናፊ አልተገኘም።"
        )

    await broadcast(context, message)
    context.job_queue.run_once(
        start_round,
        NEXT_ROUND_SECONDS,
        name=f"bingo-next-{round_id}",
    )


async def start_round(context: ContextTypes.DEFAULT_TYPE):
    now = time.time()

    with connect_db() as db:
        game = current_round(db)

        if game and game["active"]:
            round_id = game["id"]
            buy_until = game["buy_until"]

            if buy_until is None:
                buy_until = now + BUY_SECONDS
                db.execute(
                    "UPDATE rounds SET buy_until = ? WHERE id = ?",
                    (buy_until, round_id),
                )
                db.commit()
        else:
            buy_until = now + BUY_SECONDS
            db.execute(
                "INSERT INTO rounds (active, called, winner_id, buy_until) "
                "VALUES (1, '[]', NULL, ?)",
                (buy_until,),
            )
            game = current_round(db)
            round_id = game["id"]
            db.commit()

    if buy_until > now:
        await broadcast(
            context,
            f"🎱 Bingo Gold — ዙር #{round_id}\n"
            f"🎟️ የካርድ ግዢ ጊዜ: {BUY_SECONDS} ሰከንድ\n"
            "ካርድ ለመግዛት /play ይጫኑ።",
        )

    context.job_queue.run_once(
        call_tick,
        max(0, buy_until - time.time()),
        data={"round_id": round_id},
        name=f"bingo-call-{round_id}",
    )


async def call_tick(context: ContextTypes.DEFAULT_TYPE):
    round_id = context.job.data["round_id"]
    now = time.time()

    with connect_db() as db:
        db.execute("BEGIN IMMEDIATE")
        game = db.execute(
            "SELECT * FROM rounds WHERE id = ?", (round_id,)
        ).fetchone()

        if not game or not game["active"]:
            db.rollback()
            return

        buy_until = game["buy_until"]
        if buy_until is not None and now < buy_until:
            db.commit()
            context.job_queue.run_once(
                call_tick,
                buy_until - now,
                data={"round_id": round_id},
                name=f"bingo-call-{round_id}",
            )
            return

        winner_id = find_winner(db, game)
        if winner_id is not None:
            db.commit()
            await finish_round(context, round_id, winner_id)
            return

        called = set(json.loads(game["called"]))
        available = [n for n in range(1, 76) if n not in called]

        if not available:
            db.commit()
            await finish_round(context, round_id)
            return

        number = random.choice(available)
        called.add(number)
        db.execute(
            "UPDATE rounds SET called = ? WHERE id = ?",
            (json.dumps(sorted(called)), round_id),
        )
        db.commit()

    letter = (
        "B" if number <= 15 else
        "I" if number <= 30 else
        "N" if number <= 45 else
        "G" if number <= 60 else
        "O"
    )

    await broadcast(
        context,
        f"🎲 የተጠራው ቁጥር: {letter}-{number}\n"
        f"የተጠሩ ቁጥሮች: {len(called)}/75\n"
        "ካርድህን /mycards በመጫን ተመልከት።",
    )

    with connect_db() as db:
        game = db.execute(
            "SELECT * FROM rounds WHERE id = ?", (round_id,)
        ).fetchone()
        winner_id = find_winner(db, game) if game and game["active"] else None

    if winner_id is not None:
        await finish_round(context, round_id, winner_id)
    elif len(called) >= 75:
        await finish_round(context, round_id)
    else:
        context.job_queue.run_once(
            call_tick,
            CALL_SECONDS,
            data={"round_id": round_id},
            name=f"bingo-call-{round_id}",
        )


async def auto_game_start(context: ContextTypes.DEFAULT_TYPE):
    await start_round(context)

