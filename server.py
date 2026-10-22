import os
import random
import hashlib
import hmac
import json
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qsl
from flask import Flask, jsonify, request, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "miniapp"
DB_FILE = BASE_DIR / "bingo.db"

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
def connect_db():
    db = sqlite3.connect(DB_FILE, timeout=20)
    db.row_factory = sqlite3.Row
    return db
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
        row = db.execute('SELECT user_id, username, balance FROM users WHERE user_id=?', (user['id'],)).fetchone()
    if row is None: return jsonify(error='Please open the bot and send /start first'), 404
    return jsonify(user_id=row['user_id'], username=row['username'], balance=row['balance'])
@app.get('/api/game')
def game_state():
    user = verified_user()
    if user is None:
        return jsonify(error='Telegram verification required'), 401

    with connect_db() as db:
        row = db.execute(
            'SELECT id, active, called, winner_id '
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
                'SELECT username FROM users WHERE user_id=?',
                (row['winner_id'],)
            ).fetchone()

            if winner:
                winner_name = winner['username']

    try:
        called = json.loads(row['called'] or '[]')
        if not isinstance(called, list):
            called = []
    except (ValueError, TypeError):
        called = []

    return jsonify(
        round_id=row['id'],
        active=bool(row['active']),
        called=called,
        winner_id=row['winner_id'],
        winner_name=winner_name
    )
@app.post('/api/cards')
def buy_card():
    user = verified_user()
    if user is None:
        return jsonify(error='Telegram verification required'), 401

    db = connect_db()
    try:
        db.execute('BEGIN IMMEDIATE')

        game = db.execute(
            'SELECT id, active, buy_until FROM rounds ORDER BY id DESC LIMIT 1'
        ).fetchone()

        if game is None or not game['active']:
            db.rollback()
            return jsonify(error='No active game'), 409

        if game['buy_until'] is not None and time.time() >= game['buy_until']:
            db.rollback()
            return jsonify(error='Card purchase time has ended'), 409

        account = db.execute(
            'SELECT balance FROM users WHERE user_id=?',
            (user['id'],)
        ).fetchone()

        if account is None:
            db.rollback()
            return jsonify(error='Send /start to the bot first'), 404

        owned = db.execute(
            'SELECT COUNT(*) FROM cards WHERE round_id=? AND user_id=?',
            (game['id'], user['id'])
        ).fetchone()[0]

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
            'UPDATE users SET balance=balance-10 WHERE user_id=?',
            (user['id'],)
        )
        result = db.execute(
            'INSERT INTO cards (round_id, user_id, numbers) VALUES (?, ?, ?)',
            (game['id'], user['id'], json.dumps(card))
        )
        db.commit()

        balance = db.execute(
            'SELECT balance FROM users WHERE user_id=?',
            (user['id'],)
        ).fetchone()['balance']

        return jsonify(card_id=result.lastrowid, card=card, balance=balance)

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
            'SELECT id, numbers FROM cards WHERE round_id=? AND user_id=? ORDER BY id',
            (game['id'], user['id'])
        ).fetchall()

        return jsonify(
            cards=[
                {'id': row['id'], 'numbers': json.loads(row['numbers'])}
                for row in rows
            ],
            called=json.loads(game['called'] or '[]')
        )
if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    print(f'Bingo Mini App API starting on port {port}')
    app.run(host='0.0.0.0', port=port, debug=False)
