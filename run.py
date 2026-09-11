"""
Single entry point for the whole project.

Starts Kafka (docker compose up -d), waits until it's reachable, then
launches the API server, the consumer/player, and the WhatsApp/Telegram
bridges as child processes. Ctrl+C stops everything (Kafka container keeps
running; add --stop-kafka to also tear it down).

If the app is already running (port 8000 occupied), the second invocation
attaches to the shared log file instead of starting a duplicate.
"""
import os
import signal
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(HERE, ".app.log")
PID_FILE = os.path.join(HERE, ".app.pid")
WHATSAPP_BRIDGE_DIR = os.path.join(HERE, "whatsapp-bridge")
TELEGRAM_BRIDGE_DIR = os.path.join(HERE, "telegram-bridge")
KAFKA_HOST = os.getenv("KAFKA_HOST", "localhost")
KAFKA_PORT = int(os.getenv("KAFKA_PORT", "9092"))
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6380"))
API_PORT = int(os.getenv("API_PORT", "8000"))
STOP_KAFKA_ON_EXIT = "--stop-kafka" in sys.argv
SKIP_WHATSAPP = "--no-whatsapp" in sys.argv
SKIP_TELEGRAM = "--no-telegram" in sys.argv

procs = []


def is_already_running():
    try:
        with socket.create_connection(("localhost", API_PORT), timeout=1):
            return True
    except OSError:
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


def wait_for_port(name, host, port, timeout=60):
    print(f"Waiting for {name} at {host}:{port}...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                print(f"{name} is up.")
                return True
        except OSError:
            time.sleep(2)
    print(f"Timed out waiting for {name}. Is Docker running?")
    return False


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
    if STOP_KAFKA_ON_EXIT:
        print("Stopping Kafka (docker compose down)...")
        subprocess.run(["docker", "compose", "down"], cwd=HERE)
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

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    run_tests()

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    log_fh = open(LOG_FILE, "w", encoding="utf-8")
    sys.stdout = _Tee(sys.__stdout__, log_fh)
    sys.stderr = _Tee(sys.__stderr__, log_fh)

    print("Starting Kafka + Redis (docker compose up -d)...")
    subprocess.run(["docker", "compose", "up", "-d"], cwd=HERE, check=True)

    if not wait_for_port("Kafka", KAFKA_HOST, KAFKA_PORT):
        shutdown()
        return

    wait_for_port("Redis", REDIS_HOST, REDIS_PORT, timeout=20)

    print(f"Starting API server on http://localhost:{API_PORT} ...")
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "producer_api:app", "--host", "0.0.0.0", "--port", str(API_PORT)],
        cwd=HERE, stdout=log_fh, stderr=subprocess.STDOUT,
    )
    procs.append(api)

    print("Starting consumer worker (this plays the audio)...")
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
