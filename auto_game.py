import json
import random
import time

from telegram.ext import ContextTypes
from db import connect_db

PRIZE_CREDITS = 100
BUY_SECONDS = 30
CALL_SECONDS = 3
NEXT_ROUND_SECONDS = 3


def as_list(value):
    if isinstance(value, str):
        return json.loads(value)
    return value or []


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
    called = set(as_list(game["called"]))
    cards = db.execute(
        """
        SELECT c.id AS card_id, c.user_id, c.numbers, p.code
        FROM cards c
        LEFT JOIN card_pool p
          ON p.round_id = c.round_id
         AND p.user_id = c.user_id
         AND p.numbers = c.numbers
        WHERE c.round_id = %s
        ORDER BY c.id
        """,
        (game["id"],),
    ).fetchall()

    for item in cards:
        card = as_list(item["numbers"])
        if has_bingo(card, called):
            return {
                "user_id": item["user_id"],
                "card_id": item["card_id"],
                "code": item["code"],
                "numbers": card,
            }
    return None


async def broadcast(context, message):
    return

async def finish_round(context, round_id, winner=None):
    winner_id = winner["user_id"] if winner else None

    with connect_db() as db:
        changed = db.execute(
            """
            UPDATE rounds
            SET active = FALSE,
                winner_id = %s,
                winner_card_id = %s,
                winner_card_code = %s,
                winner_card = %s::jsonb
            WHERE id = %s AND active = TRUE AND winner_id IS NULL
            """,
            (
                winner_id,
                winner["card_id"] if winner else None,
                winner["code"] if winner else None,
                json.dumps(winner["numbers"]) if winner else None,
                round_id,
            ),
        ).rowcount

        if changed != 1:
            return

        if winner is not None:
            db.execute(
                "UPDATE users SET balance = balance + %s WHERE user_id = %s",
                (PRIZE_CREDITS, winner_id),
            )

    if winner is not None:
        with connect_db() as db:
            user = db.execute(
                "SELECT username FROM users WHERE user_id = %s",
                (winner_id,),
            ).fetchone()

        name = user["username"] if user and user["username"] else str(winner_id)
        message = (
            f"🏆 BINGO GOLD!\nአሸናፊ: {name}\n"
            f"🎟️ የካርድ ቁጥር: {winner['code'] or 'N/A'}\n"
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
        NEXT_ROUND_SECONDS if winner is not None else 0,
        name=f"bingo-next-{round_id}",
    )


async def start_round(context: ContextTypes.DEFAULT_TYPE):
    now = time.time()

    with connect_db() as db:
        db.execute("SELECT pg_advisory_xact_lock(20261030)")
        game = current_round(db)

        if game and game["active"]:
            round_id = game["id"]
            buy_until = game["buy_until"]

            if buy_until is None:
                buy_until = now + BUY_SECONDS
                db.execute(
                    "UPDATE rounds SET buy_until = %s WHERE id = %s",
                    (buy_until, round_id),
                )
        else:
            buy_until = now + BUY_SECONDS
            game = db.execute(
                "INSERT INTO rounds (active, called, winner_id, buy_until) "
                "VALUES (TRUE, '[]'::jsonb, NULL, %s) RETURNING id",
                (buy_until,),
            ).fetchone()
            round_id = game["id"]

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
        db.execute("SELECT pg_advisory_xact_lock(20261030)")
        game = db.execute(
            "SELECT * FROM rounds WHERE id = %s FOR UPDATE",
            (round_id,),
        ).fetchone()

        if not game or not game["active"]:
            return

        buy_until = game["buy_until"]
        if buy_until is not None and now < buy_until:
            remaining = buy_until - now
            reschedule = True
        else:
            reschedule = False
            winner = find_winner(db, game)

            if winner is not None:
                called = set(as_list(game["called"]))
                number = None
            else:
                called_list = as_list(game["called"])
                called = set(called_list)
                available = [n for n in range(1, 76) if n not in called]

                if not available:
                    number = None
                else:
                    number = random.choice(available)
                    called_list.append(number)
                    db.execute(
                        "UPDATE rounds SET called = %s::jsonb WHERE id = %s",
                        (json.dumps(called_list), round_id),
                    )

    if reschedule:
        context.job_queue.run_once(
            call_tick,
            remaining,
            data={"round_id": round_id},
            name=f"bingo-call-{round_id}",
        )
        return

    if winner is not None:
        await finish_round(context, round_id, winner)
        return

    if number is None:
        await finish_round(context, round_id)
        return

    letter = (
        "B" if number <= 15 else
        "I" if number <= 30 else
        "N" if number <= 45 else
        "G" if number <= 60 else
        "O"
    )


    with connect_db() as db:
        game = db.execute(
            "SELECT * FROM rounds WHERE id = %s",
            (round_id,),
        ).fetchone()
        winner = find_winner(db, game) if game and game["active"] else None

    if winner is not None:
        await finish_round(context, round_id, winner)
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
