import os
import signal
import subprocess
import sys
import time

PORT = os.environ.get("PORT", "10000")
env = os.environ.copy()

processes = []

def stop_all(*_):
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
    sys.exit(0)

signal.signal(signal.SIGTERM, stop_all)
signal.signal(signal.SIGINT, stop_all)

processes.append(
    subprocess.Popen([sys.executable, "bot.py"], env=env)
)
processes.append(
    subprocess.Popen(
        [
            sys.executable, "-m", "gunicorn",
            "--bind", f"0.0.0.0:{PORT}",
            "--workers", "1",
            "--threads", "4",
            "server:app",
        ],
        env=env,
    )
)

while True:
    for process in processes:
        status = process.poll()
        if status is not None:
            print(f"Process exited with status {status}; stopping service.", flush=True)
            stop_all()
    time.sleep(2)
