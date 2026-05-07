# -*- coding: utf-8 -*-
"""
CLI Entry Point — NSE Option Chain ReactJS UI Launcher

This module provides the ``main()`` entry point that starts the Vite
development server for the bundled NSE Option Chain ReactJS application
and opens the default browser to the served URL.

Execution flow:
  1. Resolves the UI directory (sibling ``src/``, ``package.json``, etc.)
     from the package installation path — no hardcoded filesystem paths.
  2. Detects the ``npm`` (or ``npm.cmd`` on Windows) executable on PATH.
  3. Runs ``npm install`` once if ``node_modules/`` is absent.
  4. Selects a free port (prefers 5173; falls back to OS-assigned).
  5. Spawns ``npm run dev -- --port <port>`` and watches its stdout for
     Vite's ready marker before opening the browser.
  6. Blocks until the Vite process exits or the user sends SIGINT.

Platform: win32 is the primary development target; POSIX is also supported.

:NOTE: Node.js and npm must be installed and available on the system PATH
       before invoking this module. Python ≥ 3.9 is required for
       ``importlib.resources`` compatibility, though no ``importlib``
       usage is needed here because the UI directory is resolved via
       ``Path(__file__).parent``.
"""

# --- standard library ---
import sys
import time
import shutil
import socket
import subprocess
import threading
import webbrowser
from pathlib import Path


# --- section: private helpers ---

def _get_ui_dir() -> Path:
    """
    Resolve the UI Package Directory at Runtime

    Returns the absolute path to the directory that contains the
    ReactJS UI assets — ``index.html``, ``package.json``, ``src/``,
    ``public/``, etc. This is always the same directory as this Python
    file, because the React source is shipped alongside the package.

    No hardcoded filesystem paths are used; the location is derived
    entirely from ``__file__`` so the function works correctly whether
    the package is installed in a venv, a system site-packages directory,
    or run directly from the repository checkout.

    :rtype:  Path
    :return: Absolute ``pathlib.Path`` pointing to the optionsui package
             directory (parent of this file).
    """
    return Path(__file__).parent.resolve()


def _get_npm_cmd() -> str:
    """
    Locate the npm Executable on the System PATH

    On Windows, ``npm`` is typically a ``.cmd`` wrapper script.
    ``shutil.which`` cannot find ``.cmd`` files unless the extension is
    included explicitly, so the Windows branch tries ``npm.cmd`` first
    and falls back to plain ``npm`` (covers cases where the user added
    an unwrapped npm shim to PATH).

    On all other platforms only ``npm`` is tried.

    :rtype:  str
    :return: Full path to the npm executable as a string.

    :raises RuntimeError: If npm cannot be located on PATH.
    """
    npm_path : str | None = None

    if sys.platform == "win32":
        npm_path = shutil.which("npm.cmd")
        if npm_path is None:
            npm_path = shutil.which("npm")
    else:
        npm_path = shutil.which("npm")

    if npm_path is None:
        raise RuntimeError(
            "npm not found on PATH. Install Node.js from "
            "https://nodejs.org/ and ensure npm is available "
            "in your terminal."
        )

    return npm_path


def _build_npm_args(npm_cmd : str, args : list[str]) -> list[str]:
    """
    Construct the Full Command List for an npm Invocation

    On Windows, ``.cmd`` files (the standard npm wrapper installed by
    Node.js) cannot be executed directly by ``subprocess`` when
    ``shell=False`` — they require ``cmd.exe /c`` to interpret them.
    This function prepends ``["cmd", "/c"]`` when running on Windows and
    the resolved npm path ends with ``.cmd``.

    On all other platforms the command list is returned unchanged.

    :type  npm_cmd: str
    :param npm_cmd: Full path to the npm executable as returned by
                    :func:`_get_npm_cmd`.

    :type  args: list[str]
    :param args: Remaining npm command arguments, e.g.
                 ``["install"]`` or ``["run", "dev", "--", "--port", "5173"]``.

    :rtype:  list[str]
    :return: Complete command list ready to pass to ``subprocess.run``
             or ``subprocess.Popen``.
    """
    if sys.platform == "win32" and npm_cmd.lower().endswith(".cmd"):
        # ! .cmd files must be invoked via cmd.exe /c on Windows
        return ["cmd", "/c", npm_cmd] + args

    return [npm_cmd] + args


def _is_port_free(port : int) -> bool:
    """
    Check Whether a TCP Port is Available on the Loopback Interface

    Attempts to bind a temporary TCP socket to ``127.0.0.1:<port>``.
    ``SO_REUSEADDR`` is set so that a port in ``TIME_WAIT`` from a recent
    process exit is treated as free.

    :type  port: int
    :param port: Port number to test (1–65535).

    :rtype:  bool
    :return: ``True`` if the port can be bound (i.e., is free);
             ``False`` if an ``OSError`` occurs (port in use).
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _find_free_port(preferred : int = 5173) -> int:
    """
    Select a Free TCP Port, Preferring the Given Port

    First checks whether ``preferred`` is available. If so, returns it
    immediately. Otherwise, binds a new socket to port 0 (OS-assigned)
    and returns whatever port the operating system allocated.

    :type  preferred: int
    :param preferred: Preferred port number. Defaults to ``5173``
                      (Vite's default).

    :rtype:  int
    :return: An available port number.
    """
    if _is_port_free(preferred):
        return preferred

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _ensure_node_modules(
    ui_dir : Path,
    npm_cmd : str
) -> None:
    """
    Install npm Dependencies If node_modules is Absent

    Checks whether a ``node_modules/`` subdirectory exists inside
    ``ui_dir``. If it is missing, runs ``npm install`` to populate it.
    This is a one-time cost on first run after installation; subsequent
    invocations skip this step entirely.

    :type  ui_dir: Path
    :param ui_dir: Absolute path to the UI directory containing
                   ``package.json``.

    :type  npm_cmd: str
    :param npm_cmd: Full path to the npm executable (from
                    :func:`_get_npm_cmd`).

    :rtype:  None
    :return: Nothing. Raises ``subprocess.CalledProcessError`` if
             ``npm install`` exits with a non-zero status.
    """
    if not (ui_dir / "node_modules").exists():
        print("[optionsui] node_modules not found — running npm install ...")
        subprocess.run(
            _build_npm_args(npm_cmd, ["install"]),
            cwd = str(ui_dir),
            check = True
        )
        print("[optionsui] npm install complete.")


def _open_browser_when_ready(
    proc : subprocess.Popen,
    url : str,
    timeout : float = 60.0
) -> None:
    """
    Monitor Vite Stdout and Open the Browser When the Server Is Ready

    This function is intended to run in a background daemon thread. It
    reads ``proc.stdout`` line by line, forwarding every line to the
    console so the user can see Vite's progress. As soon as one of the
    known ready markers appears in any output line, it calls
    ``webbrowser.open(url)`` and stops watching.

    If ``timeout`` seconds elapse before a ready marker is seen, the
    browser is opened anyway as a fallback (Vite may have started but
    printed an unexpected banner).

    Ready markers (any substring match):
      - ``"Local:"``   — Vite prints ``➜  Local:   http://localhost:NNNN/``
      - ``"ready in"`` — Vite prints ``ready in NNN ms``
      - ``"VITE v"``   — Vite version banner (older versions)

    :type  proc: subprocess.Popen
    :param proc: The running Vite subprocess whose stdout is being
                 monitored.

    :type  url: str
    :param url: The URL to open in the default browser once the server
                is confirmed ready.

    :type  timeout: float
    :param timeout: Maximum seconds to wait for a ready marker before
                    opening the browser unconditionally.
                    Defaults to ``60.0``.

    :rtype:  None
    :return: Nothing.
    """
    ready_markers : tuple[str, ...] = ("Local:", "ready in", "VITE v")
    deadline : float = time.monotonic() + timeout
    opened : bool = False

    # ! proc.stdout may be None if the process was not started with PIPE
    if proc.stdout is None:
        return

    for line in proc.stdout:
        print(line.rstrip(), flush = True)

        if not opened:
            if (any(marker in line for marker in ready_markers)
                    or time.monotonic() > deadline):
                # ? open the browser once; keep looping to drain the pipe
                webbrowser.open(url)
                opened = True

        # ! do NOT break here — draining proc.stdout until the process
        # ! exits prevents the OS pipe buffer from filling and blocking Vite


# --- section: public entry point ---

def main() -> None:
    """
    Launch the NSE Option Chain ReactJS UI

    Orchestrates the full startup sequence:

    1. Resolves the UI directory from the package installation path.
    2. Locates the ``npm`` executable on PATH (raises ``RuntimeError``
       with a helpful message if not found).
    3. Installs npm dependencies if ``node_modules/`` is absent.
    4. Selects a free port (prefers 5173).
    5. Spawns ``npm run dev -- --port <port>`` as a subprocess.
    6. Starts a daemon thread that watches Vite's stdout and opens the
       browser as soon as the server is ready.
    7. Blocks on ``proc.wait()`` until Vite exits.
    8. Handles ``KeyboardInterrupt`` (Ctrl-C) gracefully: terminates the
       Vite process and exits with code 0.

    :rtype:  None
    :return: Nothing. Exits the process via ``sys.exit(0)`` on
             ``KeyboardInterrupt``.

    :raises RuntimeError: If npm is not found on PATH, or if the Vite
                          executable referenced by ``npm run dev`` cannot
                          be found after ``npm install``.
    """
    # --- locate the UI directory ---
    ui_dir : Path = _get_ui_dir()

    # --- resolve npm; RuntimeError propagates naturally to the caller ---
    npm_cmd : str = _get_npm_cmd()

    print(f"[optionsui] UI directory: {ui_dir}")

    # --- install dependencies on first run if needed ---
    _ensure_node_modules(ui_dir, npm_cmd)

    # --- select an available port ---
    port : int = _find_free_port(preferred = 5173)
    url : str = f"http://localhost:{port}"

    print(f"[optionsui] Starting Vite dev server on {url} ...")

    # --- build the Vite launch command ---
    vite_cmd : list = _build_npm_args(npm_cmd, ["run", "dev", "--", "--port", str(port)])

    # --- spawn the Vite subprocess ---
    try:
        proc : subprocess.Popen = subprocess.Popen(
            vite_cmd,
            cwd = str(ui_dir),
            stdout = subprocess.PIPE,
            stderr = subprocess.STDOUT,
            text = True,
            bufsize = 1
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Failed to start Vite dev server. "
            f"Ensure npm dependencies are installed and npm is on PATH. "
            f"Original error: {exc}"
        ) from exc

    # --- background thread: watch stdout and open browser when ready ---
    watcher : threading.Thread = threading.Thread(
        target = _open_browser_when_ready,
        args = (proc, url),
        daemon = True
    )
    watcher.start()

    # --- block until Vite exits; handle Ctrl-C gracefully ---
    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\n[optionsui] Shutting down Vite dev server ...")
        proc.terminate()
        try:
            proc.wait(timeout = 5)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("[optionsui] Server stopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
