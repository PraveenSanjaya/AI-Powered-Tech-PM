import json
import logging
import re
import sys
from datetime import datetime, timezone

_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization|password|token|secret|api[_-]?key|cookie)(\"?\s*[:=]\s*\"?)([^\s\",;}]+)"),
    re.compile(r"\b(ghp_|github_pat_|xox[abp]-|sk-ant-|secret_)[A-Za-z0-9_\-]+"),
]


def redact(text: str) -> str:
    text = _SECRET_PATTERNS[0].sub(r"\1\2[REDACTED]", text)
    return _SECRET_PATTERNS[1].sub("[REDACTED]", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        for k in ("request_id", "path", "status", "workspace_id"):
            if hasattr(record, k):
                payload[k] = getattr(record, k)
        return json.dumps(payload)


def configure_logging(level: str = "INFO") -> None:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [h]
    root.setLevel(level)
