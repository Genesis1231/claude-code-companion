import json
import logging
import tempfile
from pathlib import Path

HERE = Path(__file__).parent

_config = json.loads((HERE / "config.json").read_text())

MODEL           = _config["model"]
VOICE           = _config["voice"]
PORT            = _config["port"]
PERSONA         = _config.get("persona", "")
GREETING_PROMPT = _config.get("greeting_prompt", "")
REPLY_PROMPT    = _config.get("reply_prompt", "")
FAREWELL_PROMPT = _config.get("farewell_prompt", "")

VOICES_DIR   = HERE / "voices"
RUNTIME_DIR  = Path(tempfile.gettempdir()) / "claude-code-companion"
OUT_DIR      = RUNTIME_DIR
SESSIONS_DIR = RUNTIME_DIR / "sessions"

LOGS_DIR = HERE / "logs"


def session_log(session_id: str) -> Path:
    """Path to a session's running conversation log (one .md per Claude session)."""
    return LOGS_DIR / f"{session_id}.md"

# Plain append FileHandler, no rotation: several short-lived processes
# (reply_hook, goodbye, mcp_server) may write at once, and POSIX append is atomic
# per line while rotation rollover races. delay=True so the file is created only
# when something actually logs. Collects errors from every component (hooks, MCP
# server, daemon, engine). The daemon also keeps its own operational narration in
# daemon.log (its stdout via voiced.sh); errors additionally land here.
logger = logging.getLogger("companion")
logger.setLevel(logging.WARNING)
logger.propagate = False
if not logger.handlers:
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        _handler: logging.Handler = logging.FileHandler(LOGS_DIR / "companion.log", delay=True)
    except OSError:
        _handler = logging.StreamHandler()    # fall back to stderr if logs/ isn't writable
    _handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(module)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
    logger.addHandler(_handler)
