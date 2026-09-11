"""
Single entry point for the whole project.

Launches the API server, the player, and the WhatsApp/Telegram bridges as
child processes. Everything they share lives in the SQLite database, so
there are no external services to start first. Ctrl+C stops everything.

If the app is already running (port 8000 occupied), the second invocation
attaches to the shared log file instead of starting a duplicate.
"""
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(HERE, ".app.log")
PID_FILE = os.path.join(HERE, ".app.pid")
WHATSAPP_BRIDGE_DIR = os.path.join(HERE, "whatsapp-bridge")
TELEGRAM_BRIDGE_DIR = os.path.join(HERE, "telegram-bridge")
API_PORT = int(os.getenv("API_PORT", "8000"))
SKIP_WHATSAPP = "--no-whatsapp" in sys.argv
SKIP_TELEGRAM = "--no-telegram" in sys.argv

procs = []


def _port_in_use():
    try:
        with socket.create_connection(("localhost", API_PORT), timeout=1):
            return True
    except OSError:
        return False


def is_already_running():
    """
    True only if OUR app is answering on the port. Something else squatting
    on it (Splunk's web UI defaults to 8000, for one) must not be mistaken for
    a running instance, or start.bat would "attach" to nothing forever.
    """
    if not _port_in_use():
        return False
    try:
        with urllib.request.urlopen(f"http://localhost:{API_PORT}/wait-time", timeout=2) as resp:
            body = json.load(resp)
        return isinstance(body, dict) and "queue_length" in body
    except (OSError, ValueError):
        return False


def _read_pid():
    try:
        with open(PID_FILE, "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None


def attach_to_logs():
    """Tail the shared log file. Ctrl+C stops the main app."""
    pid = _read_pid()
    print(f"App is already running on port {API_PORT} (pid {pid or '?'}).")
    print(f"Attaching to log output... Press Ctrl+C to stop the app.\n")
    if not os.path.exists(LOG_FILE):
        print("(log file not found yet — waiting for output...)")
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    print(line, end="", flush=True)
                else:
                    time.sleep(0.3)
    except KeyboardInterrupt:
        if pid:
            print(f"\nStopping app (pid {pid})...")
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        else:
            print("\nCould not find app PID — stop it manually.")
        sys.exit(0)


def env_has_value(env_path, key):
    """Checks a bridge's .env for a non-empty KEY=value line (avoids parsing
    dependencies just to detect an unconfigured bridge before spawning it)."""
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{key}=") and line.split("=", 1)[1].strip():
                    return True
    except FileNotFoundError:
        pass
    return False


def start_bridge(name, bridge_dir, skip, required_env_key=None, log_fh=None):
    if skip:
        print(f"Skipping {name} bridge (--no-{name}).")
    elif not os.path.isdir(os.path.join(bridge_dir, "node_modules")):
        print(f"Skipping {name} bridge: run 'npm install' in {os.path.basename(bridge_dir)}/ first.")
    elif required_env_key and not env_has_value(os.path.join(bridge_dir, ".env"), required_env_key):
        print(f"Skipping {name} bridge: set {required_env_key} in {os.path.basename(bridge_dir)}/.env first.")
    else:
        print(f"Starting {name} bridge...")
        kw = {"cwd": bridge_dir}
        if log_fh:
            kw["stdout"] = log_fh
            kw["stderr"] = subprocess.STDOUT
        procs.append(subprocess.Popen(["node", "index.js"], **kw))


def shutdown(signum=None, frame=None):
    print("\nShutting down...")
    for p in procs:
        if p.poll() is None:
            p.terminate()
    for p in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass
    sys.exit(0)


def run_tests():
    print("Running tests...")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
        cwd=HERE,
    )
    if result.returncode != 0:
        print("\nTests failed! Fix the issues above before starting the app.")
        sys.exit(1)
    print()


class _Tee:
    """Write to both a file and the original stream."""
    def __init__(self, stream, log_file):
        self._stream = stream
        self._log = log_file

    def write(self, data):
        self._stream.write(data)
        self._stream.flush()
        try:
            self._log.write(data)
            self._log.flush()
        except Exception:
            pass

    def flush(self):
        self._stream.flush()
        try:
            self._log.flush()
        except Exception:
            pass


def main():
    if is_already_running():
        attach_to_logs()
        return

    if _port_in_use():
        print(f"Port {API_PORT} is already taken by another program (not this app), so the")
        print(f"API can't start. Either stop that program, or set API_PORT in .env to a")
        print(f"free port (e.g. API_PORT=8010) and run this again.")
        sys.exit(1)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    run_tests()

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    log_fh = open(LOG_FILE, "w", encoding="utf-8")
    sys.stdout = _Tee(sys.__stdout__, log_fh)
    sys.stderr = _Tee(sys.__stderr__, log_fh)

    print(f"Starting API server on http://localhost:{API_PORT} ...")
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "producer_api:app", "--host", "0.0.0.0", "--port", str(API_PORT)],
        cwd=HERE, stdout=log_fh, stderr=subprocess.STDOUT,
    )
    procs.append(api)

    print("Starting player (this plays the audio)...")
    worker = subprocess.Popen(
        [sys.executable, "consumer_worker.py"],
        cwd=HERE, stdout=log_fh, stderr=subprocess.STDOUT,
    )
    procs.append(worker)

    start_bridge("whatsapp", WHATSAPP_BRIDGE_DIR, SKIP_WHATSAPP, log_fh=log_fh)
    start_bridge("telegram", TELEGRAM_BRIDGE_DIR, SKIP_TELEGRAM, required_env_key="TELEGRAM_BOT_TOKEN", log_fh=log_fh)

    print("\nAll services running. Press Ctrl+C to stop everything.\n")

    while True:
        for p in procs:
            ret = p.poll()
            if ret is not None:
                print(f"A process (pid {p.pid}) exited with code {ret}, stopping the rest.")
                shutdown()
                return
        time.sleep(1)


if __name__ == "__main__":
    main()
