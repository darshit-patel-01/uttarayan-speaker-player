"""
Single entry point for the whole project.

Starts Kafka (docker compose up -d), waits until it's reachable, then
launches the API server, the consumer/player, and the WhatsApp/Telegram
bridges as child processes. Ctrl+C stops everything (Kafka container keeps
running; add --stop-kafka to also tear it down).
"""
import os
import signal
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WHATSAPP_BRIDGE_DIR = os.path.join(HERE, "whatsapp-bridge")
TELEGRAM_BRIDGE_DIR = os.path.join(HERE, "telegram-bridge")
KAFKA_HOST = os.getenv("KAFKA_HOST", "localhost")
KAFKA_PORT = int(os.getenv("KAFKA_PORT", "9092"))
REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6380"))
STOP_KAFKA_ON_EXIT = "--stop-kafka" in sys.argv
SKIP_WHATSAPP = "--no-whatsapp" in sys.argv
SKIP_TELEGRAM = "--no-telegram" in sys.argv

procs = []


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


def start_bridge(name, bridge_dir, skip, required_env_key=None):
    if skip:
        print(f"Skipping {name} bridge (--no-{name}).")
    elif not os.path.isdir(os.path.join(bridge_dir, "node_modules")):
        print(f"Skipping {name} bridge: run 'npm install' in {os.path.basename(bridge_dir)}/ first.")
    elif required_env_key and not env_has_value(os.path.join(bridge_dir, ".env"), required_env_key):
        print(f"Skipping {name} bridge: set {required_env_key} in {os.path.basename(bridge_dir)}/.env first.")
    else:
        print(f"Starting {name} bridge...")
        procs.append(subprocess.Popen(["node", "index.js"], cwd=bridge_dir))


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
    if STOP_KAFKA_ON_EXIT:
        print("Stopping Kafka (docker compose down)...")
        subprocess.run(["docker", "compose", "down"], cwd=HERE)
    sys.exit(0)


def main():
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("Starting Kafka + Redis (docker compose up -d)...")
    subprocess.run(["docker", "compose", "up", "-d"], cwd=HERE, check=True)

    if not wait_for_port("Kafka", KAFKA_HOST, KAFKA_PORT):
        shutdown()
        return

    # Redis is a soft dependency (real_time_validation's rate limiter fails
    # open if it's unreachable), so a timeout here just logs and continues
    # rather than aborting startup like Kafka does.
    wait_for_port("Redis", REDIS_HOST, REDIS_PORT, timeout=20)

    print("Starting API server on http://localhost:8000 ...")
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "producer_api:app", "--host", "0.0.0.0", "--port", "8000"],
        cwd=HERE,
    )
    procs.append(api)

    print("Starting consumer worker (this plays the audio)...")
    worker = subprocess.Popen([sys.executable, "consumer_worker.py"], cwd=HERE)
    procs.append(worker)

    start_bridge("whatsapp", WHATSAPP_BRIDGE_DIR, SKIP_WHATSAPP)
    start_bridge("telegram", TELEGRAM_BRIDGE_DIR, SKIP_TELEGRAM, required_env_key="TELEGRAM_BOT_TOKEN")

    print("\nAll services running. Press Ctrl+C to stop everything.\n")

    # If either child process dies unexpectedly, tear down the rest too.
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
