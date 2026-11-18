"""Start the loopback MCP daemon and Streamlit together."""

import argparse
import atexit
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time


_managed_sse = None
_sse_lock = threading.Lock()


def stop(process):
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def wait_for_sse(process, port):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and process.poll() is None:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.15)
    raise RuntimeError(f"Локальный MCP-сервер не запустился на порту {port}.")


def managed_sse_port() -> int:
    """Start one loopback server per Streamlit process and restart it after a crash."""
    global _managed_sse
    with _sse_lock:
        if _managed_sse and _managed_sse[0].poll() is None:
            return _managed_sse[1]

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        process = subprocess.Popen([sys.executable, "-m", "clinical_mcp.network", "--port", str(port)])
        try:
            wait_for_sse(process, port)
        except RuntimeError:
            stop(process)
            raise
        _managed_sse = (process, port)
        return port


@atexit.register
def stop_managed_sse():
    if _managed_sse:
        stop(_managed_sse[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sse-port", type=int, default=8000)
    parser.add_argument("--web-port", type=int, default=8502)
    args = parser.parse_args()
    with socket.socket() as check:
        try:
            check.bind(("127.0.0.1", args.sse_port))
        except OSError as error:
            parser.exit(1, f"Порт SSE {args.sse_port} занят или недоступен: {error}\n")

    env = {**os.environ, "PIMIIM_SSE_PORT": str(args.sse_port)}
    daemon = subprocess.Popen([sys.executable, "-m", "clinical_mcp.network"], env=env)
    web = None
    try:
        wait_for_sse(daemon, args.sse_port)
        web = subprocess.Popen([sys.executable, "-m", "streamlit", "run", str(Path(__file__).with_name("app.py")),
                                "--server.address", "127.0.0.1", "--server.port", str(args.web_port)], env=env)
        return web.wait()
    except KeyboardInterrupt:
        return 0
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    finally:
        stop(web)
        stop(daemon)


if __name__ == "__main__":
    sys.exit(main())
