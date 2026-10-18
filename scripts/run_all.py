"""Run every Haggle agent on one server (the build plan's hosting: one always-on box, mailbox mode).

Starts the vendor host, Factory, Negotiator, Discovery and Concierge as child processes in that
order, prefixes their logs, and restarts any that exit. Stop with Ctrl+C (or SIGTERM from the host).

Run from the repo root:  python -m scripts.run_all

Owner: Person D
"""

import os
import signal
import subprocess
import sys
import threading
import time

AGENTS = [  # (name, module, seconds to wait before starting the next one)
    ("vendors", "vendors.vendor_host", 20),
    ("factory", "agents.factory", 3),
    ("negotiator", "agents.negotiator", 2),
    ("discovery", "agents.discovery", 2),
    ("booker", "agents.booker", 2),
    ("concierge", "agents.concierge", 0),
]
RESTART_DELAY_S = 5

procs: dict[str, subprocess.Popen] = {}
stopping = threading.Event()


def pipe(name: str, proc: subprocess.Popen) -> None:
    for line in proc.stdout:
        sys.stdout.write(f"[{name}] {line}")
        sys.stdout.flush()


def start(name: str, module: str) -> None:
    proc = subprocess.Popen([sys.executable, "-u", "-m", module], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, env={**os.environ, "PYTHONUNBUFFERED": "1"})
    procs[name] = proc
    threading.Thread(target=pipe, args=(name, proc), daemon=True).start()
    print(f"[run_all] started {name} (pid {proc.pid})", flush=True)


def stop(*_) -> None:
    stopping.set()
    for proc in procs.values():
        proc.terminate()


def main() -> None:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    for name, module, wait in AGENTS:
        start(name, module)
        time.sleep(wait)
    modules = {name: module for name, module, _ in AGENTS}
    while not stopping.is_set():
        for name, proc in list(procs.items()):
            if proc.poll() is not None and not stopping.is_set():
                print(f"[run_all] {name} exited with {proc.returncode}; restarting in {RESTART_DELAY_S}s", flush=True)
                time.sleep(RESTART_DELAY_S)
                start(name, modules[name])
        stopping.wait(2)
    for proc in procs.values():
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
