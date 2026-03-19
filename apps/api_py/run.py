from __future__ import annotations

import atexit
import os
import secrets
import shutil
import socket
import subprocess
import time

import uvicorn

def is_port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def choose_port(preferred: int, *, avoid: set[int] | None = None) -> int:
    avoid = avoid or set()
    candidate = preferred
    while candidate in avoid or not is_port_free(candidate):
        candidate += 1
    return candidate


def start_auth_sidecar(root_dir: str) -> subprocess.Popen[str]:
    env = {
        **os.environ,
        "AUTH_PORT": os.environ["AUTH_PORT"],
        "APP_URL": os.environ["APP_URL"],
        "INTERNAL_SERVICE_SECRET": os.environ["INTERNAL_SERVICE_SECRET"],
    }
    return subprocess.Popen(["node", "apps/auth/server.js"], cwd=root_dir, env=env)


def start_trigger_dev_sidecar(root_dir: str) -> subprocess.Popen[str] | None:
    if os.getenv("START_TRIGGER_DEV", "1") == "0":
        print("Trigger.dev worker auto-start is disabled.")
        return None

    if not os.getenv("TRIGGER_SECRET_KEY"):
        print("Trigger.dev worker skipped because TRIGGER_SECRET_KEY is not set.")
        return None

    npx_command = shutil.which("npx.cmd") or shutil.which("npx")
    if not npx_command:
        print("Trigger.dev CLI was not found. Background jobs will fall back to Python.")
        return None

    try:
        return subprocess.Popen(
            [npx_command, "trigger.dev", "dev", "--skip-update-check", "--log-level", "warn"],
            cwd=root_dir,
            env={**os.environ},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        print(f"Failed to start Trigger.dev worker: {exc}")
        return None


def main() -> None:
    preferred_port = int(os.getenv("PORT", "3000"))
    app_port = choose_port(preferred_port)
    auth_port = choose_port(int(os.getenv("AUTH_PORT", "3001")), avoid={app_port})

    if app_port != preferred_port:
        print(f"Port {preferred_port} is busy, starting Python API on http://localhost:{app_port} instead.")

    os.environ["PORT"] = str(app_port)
    os.environ["AUTH_PORT"] = str(auth_port)
    os.environ["APP_URL"] = f"http://localhost:{app_port}"
    os.environ["AUTH_SERVICE_URL"] = f"http://127.0.0.1:{auth_port}"
    os.environ.setdefault("INTERNAL_SERVICE_SECRET", secrets.token_urlsafe(24))

    print(f"Python API target URL: http://localhost:{app_port}")
    print(f"Better Auth sidecar target URL: http://127.0.0.1:{auth_port}")
    print(f"Open this in your browser: http://localhost:{app_port}")
    print(f"Auth sidecar health check: http://127.0.0.1:{auth_port}/health")

    from .config import settings
    from .main import app

    auth_process = start_auth_sidecar(str(settings.root_dir))
    trigger_process = start_trigger_dev_sidecar(str(settings.root_dir))

    def cleanup() -> None:
        if auth_process.poll() is None:
            auth_process.terminate()
            try:
                auth_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                auth_process.kill()
        if trigger_process and trigger_process.poll() is None:
            trigger_process.terminate()
            try:
                trigger_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                trigger_process.kill()

    atexit.register(cleanup)
    time.sleep(1)
    uvicorn.run(app, host="127.0.0.1", port=settings.port)


if __name__ == "__main__":
    main()
