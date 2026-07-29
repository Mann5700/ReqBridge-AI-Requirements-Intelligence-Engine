"""Single-file launcher for ReqBridge.

Run this file (e.g. via Code Runner) to start both the FastAPI backend
and the Vite React frontend dev server in one shot.
Press Ctrl+C to stop both.
"""

import os
import subprocess
import sys
import time
import signal
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(ROOT, "frontend")
BACKEND_HOST = "0.0.0.0"
BACKEND_PORT = 8000
FRONTEND_PORT = 5173
NPM = "npm.cmd" if sys.platform == "win32" else "npm"


def main():
    procs: list[subprocess.Popen] = []

    # --- Start Backend (uvicorn) ---
    backend_env = os.environ.copy()
    backend_env["PYTHONPATH"] = ROOT
    backend_cmd = [
        sys.executable, "-m", "uvicorn",
        "backend.app.main:app",
        "--host", BACKEND_HOST,
        "--port", str(BACKEND_PORT),
        "--reload",
        "--reload-dir", os.path.join(ROOT, "backend"),
    ]
    print(f"[ReqBridge] Starting backend on http://localhost:{BACKEND_PORT} ...")
    backend_proc = subprocess.Popen(backend_cmd, cwd=ROOT, env=backend_env)
    procs.append(backend_proc)

    # --- Start Frontend (vite dev) ---
    frontend_cmd = [NPM, "run", "dev", "--", "--port", str(FRONTEND_PORT)]
    print(f"[ReqBridge] Starting frontend on http://localhost:{FRONTEND_PORT} ...")
    frontend_proc = subprocess.Popen(
        frontend_cmd, cwd=FRONTEND_DIR,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    procs.append(frontend_proc)

    # --- Open browser after a short delay ---
    time.sleep(3)
    webbrowser.open(f"http://localhost:{FRONTEND_PORT}")

    # --- Wait & handle shutdown ---
    print()
    print("=" * 60)
    print(f"  ReqBridge is running!")
    print(f"  UI:      http://localhost:{FRONTEND_PORT}")
    print(f"  API:     http://localhost:{BACKEND_PORT}")
    print(f"  Docs:    http://localhost:{BACKEND_PORT}/docs")
    print("=" * 60)
    print("  Press Ctrl+C to stop all services.")
    print()

    def shutdown(*_):
        print("\n[ReqBridge] Shutting down ...")
        for p in procs:
            try:
                if sys.platform == "win32":
                    p.terminate()
                else:
                    p.send_signal(signal.SIGTERM)
            except OSError:
                pass
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Keep alive — exit when either process dies unexpectedly
    try:
        while True:
            for p in procs:
                ret = p.poll()
                if ret is not None:
                    print(f"[ReqBridge] Process {p.args[0]} exited with code {ret}. Stopping all.")
                    shutdown()
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
