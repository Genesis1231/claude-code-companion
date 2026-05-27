import json
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
