"""Entry point: python app.py

Wires up logging and launches the Tkinter UI. Keep this file tiny --
all real logic lives in core/ and services/.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.logging_config import setup_logging  # noqa: E402


def _load_local_env(base_dir: str):
    """Load API keys etc. from a local, git-ignored .env.local file.

    Kept separate from the more common ``.env`` name so it's obvious this
    file is machine-local and never meant to be shared/committed. Real
    environment variables (e.g. set by run.bat or the shell) always take
    priority -- this only fills in variables that aren't already set.
    """
    env_path = os.path.join(base_dir, ".env.local")
    if not os.path.isfile(env_path):
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        # python-dotenv not installed: silently skip, env vars / UI input
        # still work fine without it.
        return
    load_dotenv(env_path, override=False)


def main():
    # When frozen (PyInstaller), .env.local / logs / output should sit next
    # to the EXE (sys.executable), NOT inside the temp _MEIPASS extraction dir.
    # When running from source, __file__'s directory is the project root.
    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))

    _load_local_env(base_dir)
    setup_logging(log_dir=os.path.join(base_dir, "logs"))

    from ui.main_window import run

    run()


if __name__ == "__main__":
    main()
