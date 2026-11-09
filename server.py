import os
import random
import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import parse_qsl
from flask import Flask, jsonify, request, send_from_directory
from db import connect_db

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "miniapp"

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")

app = Flask(__name__)

@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin", "")
    allowed = "https://mamillamolla-beep.github.io"
    if origin == allowed:
        response.headers["Access-Control-Allow-Origin"] = allowed
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Telegram-Init-Data"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

@app.route("/api/<path:unused>", methods=["OPTIONS"])
def api_preflight(unused):
    return ("", 204)

def decode_json(value, default=None):
    if value is None:
        return default
    return json.loads(value) if isinstance(value, str) else value

def make_bingo_numbers():
    columns = [
        random.sample(range(1, 16), 5),
        random.sample(range(16, 31), 5),
        random.sample(range(31, 46), 5),
        random.sample(range(46, 61), 5),
        random.sample(range(61, 76), 5),
    ]
    card = [[columns[c][r] for c in range(5)] for r in range(5)]
    card[2][2] = 0
    return card


def ensure_card_pool(db, round_id):
    count = db.execute(
        "SELECT COUNT(*) AS count FROM card_pool WHERE round_id=%s",
        (round_id,)
    ).fetchone()["count"]

    if count:
        return

    for number in range(1, 51):
        code = f"BG-{number:03d}"
        db.execute(
            "INSERT INTO card_pool (round_id, code, numbers) VALUES (%s, %s, %s::jsonb)",
            (round_id, code, json.dumps(make_bingo_numbers()))
        )


def verified_user():
    raw = request.headers.get('X-Telegram-Init-Data', '')
    if not raw or len(raw) > 8192: return None
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
        data = dict(pairs)
        received_hash = data.pop('hash', '')
        if not received_hash or 'user' not in data: return None
        check_string = '\n'.join(f'{key}={value}' for key, value in sorted(data.items()))
        secret = hmac.new(b'WebAppData', BOT_TOKEN.encode(), hashlib.sha256).digest()
        expected_hash = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_hash, received_hash): return None
        auth_date = int(data.get('auth_date', '0'))
        now = int(time.time())
        if auth_date > now + 60 or now - auth_date > 86400: return None
        user = json.loads(data['user'])
        if not isinstance(user.get('id'), int) or isinstance(user.get('id'), bool): return None
        return user
    except (ValueError, TypeError, KeyError): return None
@app.get('/')
def home(): return send_from_directory(WEB_DIR, 'index.html')
@app.get('/api/me')
def my_profile():
    user = verified_user()
    if user is None: return jsonify(error='Telegram verification required'), 401
    with connect_db() as db:
        row = db.execute('SELECT user_id, username, balance FROM users WHERE user_id=%s', (user['id'],)).fetchone()
    if row is None: return jsonify(error='Please open the bot and send /start first'), 404
    return jsonify(user_id=row['user_id'], username=row['username'], balance=row['balance'])
@app.get('/api/game')
def game_state():
    user = verified_user()
    if user is None:
        return jsonify(error='Telegram verification required'), 401

    with connect_db() as db:
        row = db.execute(
            'SELECT id, active, called, winner_id, buy_until, '
            'winner_card_id, winner_card_code, winner_card '
            'FROM rounds ORDER BY id DESC LIMIT 1'
        ).fetchone()

        if row is None:
            return jsonify(
                active=False,
                called=[],
                winner_id=None,
                winner_name=None,
                round_id=None
            )

        winner_name = None
        if row['winner_id'] is not None:
            winner = db.execute(
                'SELECT username FROM users WHERE user_id=%s',
                (row['winner_id'],)
            ).fetchone()

            if winner:
                winner_name = winner['username']

    try:
        called = decode_json(row['called'], [])
        if not isinstance(called, list):
            called = []
    except (ValueError, TypeError):
        called = []

    return jsonify(
        round_id=row['id'],
        active=bool(row['active']),
        called=called,
        winner_id=row['winner_id'],
        winner_name=winner_name,
        winner_card_id=row['winner_card_id'],
        winner_card_code=row['winner_card_code'],
        winner_card=decode_json(row['winner_card'], []),
        buy_until=row['buy_until'],
        selection_seconds=max(0, int(row['buy_until'] - time.time()))
            if row['buy_until'] is not None and row['active'] else 0
    )
@app.post('/api/cards')
def buy_card():
    user = verified_user()
    if user is None:
        return jsonify(error='Telegram verification required'), 401

    db = connect_db()
    try:
        db.execute('SELECT pg_advisory_xact_lock(20261030)')

        game = db.execute(
            'SELECT id, active, buy_until FROM rounds ORDER BY id DESC LIMIT 1 FOR UPDATE'
        ).fetchone()

        if game is None or not game['active']:
            db.rollback()
            return jsonify(error='No active game'), 409

        if game['buy_until'] is not None and time.time() >= game['buy_until']:
            db.rollback()
            return jsonify(error='Card purchase time has ended'), 409

        account = db.execute(
            'SELECT balance FROM users WHERE user_id=%s FOR UPDATE',
            (user['id'],)
        ).fetchone()

        if account is None:
            db.rollback()
            return jsonify(error='Send /start to the bot first'), 404

        owned = db.execute(
            'SELECT COUNT(*) AS count FROM cards WHERE round_id=%s AND user_id=%s',
            (game['id'], user['id'])
        ).fetchone()["count"]

        if owned >= 2:
            db.rollback()
            return jsonify(error='Maximum 2 cards per game'), 409

        if account['balance'] < 10:
            db.rollback()
            return jsonify(error='Not enough credits'), 409

        columns = [
            random.sample(range(1, 16), 5),
            random.sample(range(16, 31), 5),
            random.sample(range(31, 46), 5),
            random.sample(range(46, 61), 5),
            random.sample(range(61, 76), 5),
        ]
        card = [[columns[c][r] for c in range(5)] for r in range(5)]
        card[2][2] = 0

        db.execute(
            'UPDATE users SET balance=balance-10 WHERE user_id=%s',
            (user['id'],)
        )
        result = db.execute(
            'INSERT INTO cards (round_id, user_id, numbers) VALUES (%s, %s, %s::jsonb) RETURNING id',
            (game['id'], user['id'], json.dumps(card))
        )
        card_id = result.fetchone()['id']

        balance = db.execute(
            'SELECT balance FROM users WHERE user_id=%s',
            (user['id'],)
        ).fetchone()['balance']

        db.commit()
        return jsonify(card_id=card_id, card=card, balance=balance)

    except Exception:
        db.rollback()
        app.logger.exception('Card purchase failed')
        return jsonify(error='Purchase failed'), 500
    finally:
        db.close()


@app.get('/api/cards')
def my_cards():
    user = verified_user()
    if user is None:
        return jsonify(error='Telegram verification required'), 401

    with connect_db() as db:
        game = db.execute(
            'SELECT id, called FROM rounds ORDER BY id DESC LIMIT 1'
        ).fetchone()

        if game is None:
            return jsonify(cards=[], called=[])

        rows = db.execute(
            'SELECT id, numbers FROM cards WHERE round_id=%s AND user_id=%s ORDER BY id',
            (game['id'], user['id'])
        ).fetchall()

        return jsonify(
            cards=[
                {'id': row['id'], 'numbers': decode_json(row['numbers'], [])}
                for row in rows
            ],
            called=decode_json(game['called'], [])
        )

@app.get('/api/card-pool')
def available_card_pool():
    user = verified_user()
    if user is None:
        return jsonify(error='Telegram verification required'), 401

    db = connect_db()
    try:
        game = db.execute(
            'SELECT id, active, buy_until FROM rounds ORDER BY id DESC LIMIT 1 FOR UPDATE'
        ).fetchone()

        if game is None:
            return jsonify(cards=[], active=False, selection_seconds=0)

        ensure_card_pool(db, game['id'])
        db.commit()

        rows = db.execute(
            'SELECT code, user_id FROM card_pool WHERE round_id=%s ORDER BY id',
            (game['id'],)
        ).fetchall()

        seconds = 0
        if game['active'] and game['buy_until'] is not None:
            seconds = max(0, int(game['buy_until'] - time.time()))

        return jsonify(
            round_id=game['id'],
            active=bool(game['active']),
            selection_seconds=seconds,
            cards=[
                {
                    'code': row['code'],
                    'available': row['user_id'] is None,
                    'mine': row['user_id'] == user['id']
                }
                for row in rows
            ]
        )
    except Exception:
        db.rollback()
        app.logger.exception('Card pool request failed')
        return jsonify(error='Could not load card pool'), 500
    finally:
        db.close()


@app.post('/api/card-pool/<code>/select')
def select_prepared_card(code):
    user = verified_user()
    if user is None:
        return jsonify(error='Telegram verification required'), 401

    db = connect_db()
    try:
        db.execute('SELECT pg_advisory_xact_lock(20261030)')

        game = db.execute(
            'SELECT id, active, buy_until FROM rounds ORDER BY id DESC LIMIT 1 FOR UPDATE'
        ).fetchone()

        if game is None or not game['active']:
            db.rollback()
            return jsonify(error='No active game'), 409

        if game['buy_until'] is not None and time.time() >= game['buy_until']:
            db.rollback()
            return jsonify(error='Card selection time has ended'), 409

        account = db.execute(
            'SELECT balance FROM users WHERE user_id=%s FOR UPDATE', (user['id'],)
        ).fetchone()

        if account is None:
            db.rollback()
            return jsonify(error='Send /start to the bot first'), 404

        owned = db.execute(
            'SELECT COUNT(*) AS count FROM cards WHERE round_id=%s AND user_id=%s',
            (game['id'], user['id'])
        ).fetchone()["count"]

        if owned >= 2:
            db.rollback()
            return jsonify(error='Maximum 2 cards per game'), 409

        if account['balance'] < 10:
            db.rollback()
            return jsonify(error='Not enough credits'), 409

        pool_card = db.execute(
            'SELECT id, numbers, user_id FROM card_pool '
            'WHERE round_id=%s AND code=%s',
            (game['id'], code)
        ).fetchone()

        if pool_card is None:
            db.rollback()
            return jsonify(error='Card not found'), 404

        if pool_card['user_id'] is not None:
            db.rollback()
            return jsonify(error='This card has already been selected'), 409

        numbers = decode_json(pool_card['numbers'], [])

        claimed = db.execute(
            'UPDATE card_pool SET user_id=%s WHERE id=%s AND user_id IS NULL RETURNING id',
            (user['id'], pool_card['id'])
        ).fetchone()

        if claimed is None:
            db.rollback()
            return jsonify(error='This card has already been selected'), 409

        result = db.execute(
            'INSERT INTO cards (round_id, user_id, numbers) VALUES (%s, %s, %s::jsonb) RETURNING id',
            (game['id'], user['id'], json.dumps(numbers))
        )
        card_id = result.fetchone()['id']

        db.execute(
            'UPDATE users SET balance=balance-10 WHERE user_id=%s',
            (user['id'],)
        )

        balance = db.execute(
            'SELECT balance FROM users WHERE user_id=%s', (user['id'],)
        ).fetchone()['balance']

        db.commit()
        return jsonify(
            card_id=card_id,
            code=code,
            card=numbers,
            balance=balance,
            cards_owned=owned + 1
        )

    except Exception:
        db.rollback()
        app.logger.exception('Prepared card selection failed')
        return jsonify(error='Card selection failed'), 500
    finally:
        db.close()

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    print(f'Bingo Mini App API starting on port {port}')
    app.run(host='0.0.0.0', port=port, debug=False)
