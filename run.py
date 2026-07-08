"""
Single entry point for the whole project.

Starts Kafka (docker compose up -d), waits until it's reachable, then
launches the API server and the consumer/player as child processes.
Ctrl+C stops everything (Kafka container keeps running; add --stop-kafka
to also tear it down).
"""
import os
import signal
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
KAFKA_HOST = os.getenv("KAFKA_HOST", "localhost")
KAFKA_PORT = int(os.getenv("KAFKA_PORT", "9092"))
STOP_KAFKA_ON_EXIT = "--stop-kafka" in sys.argv

procs = []


def wait_for_kafka(timeout=60):
    print(f"Waiting for Kafka at {KAFKA_HOST}:{KAFKA_PORT}...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((KAFKA_HOST, KAFKA_PORT), timeout=2):
                print("Kafka is up.")
                return True
        except OSError:
            time.sleep(2)
    print("Timed out waiting for Kafka. Is Docker running?")
    return False


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

    print("Starting Kafka (docker compose up -d)...")
    subprocess.run(["docker", "compose", "up", "-d"], cwd=HERE, check=True)

    if not wait_for_kafka():
        shutdown()
        return

    print("Starting API server on http://localhost:8000 ...")
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "producer_api:app", "--host", "0.0.0.0", "--port", "8000"],
        cwd=HERE,
    )
    procs.append(api)

    print("Starting consumer worker (this plays the audio)...")
    worker = subprocess.Popen([sys.executable, "consumer_worker.py"], cwd=HERE)
    procs.append(worker)

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
