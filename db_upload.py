import os
import hmac
import sqlite3
import tempfile
from pathlib import Path
from flask import Flask, request, jsonify

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

DB_PATH = Path(os.environ.get("BINGO_DB_PATH", "/var/data/bingo.db"))
SECRET = os.environ.get("DB_UPLOAD_SECRET", "")
PORT = int(os.environ.get("PORT", "10000"))

@app.get("/")
def health():
    return jsonify({"status": "temporary database transfer service"})

@app.post("/upload-db")
def upload_db():
    supplied = request.headers.get("X-Upload-Secret", "")
    if not SECRET or not hmac.compare_digest(supplied, SECRET):
        return jsonify({"error": "Unauthorized"}), 401

    if DB_PATH.exists():
        return jsonify({"error": "Database already exists; refusing to overwrite"}), 409

    data = request.get_data()
    if not data:
        return jsonify({"error": "Empty upload"}), 400

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            dir=DB_PATH.parent, prefix="bingo-upload-", delete=False
        ) as temp:
            temp.write(data)
            temp_path = Path(temp.name)

        with sqlite3.connect(str(temp_path)) as conn:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }

        if result != "ok" or not {"users", "rounds", "cards"}.issubset(tables):
            return jsonify({"error": "Invalid Bingo database"}), 400

        if DB_PATH.exists():
            return jsonify({"error": "Database appeared; refusing to overwrite"}), 409

        os.replace(temp_path, DB_PATH)
        temp_path = None
        return jsonify({"status": "uploaded", "bytes": DB_PATH.stat().st_size})

    except Exception:
        app.logger.exception("Database upload failed")
        return jsonify({"error": "Upload failed"}), 500
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
