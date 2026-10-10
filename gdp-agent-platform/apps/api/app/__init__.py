"""Settings from apps/api/.env (gitignored) are loaded before any module reads os.environ.

Variables already set in the process environment win, so a production host's own environment is never overridden.
"""

from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv is optional: without it only the process environment is used
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
