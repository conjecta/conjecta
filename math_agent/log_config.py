"""Persistent logging for debugging (console + files)."""
from __future__ import annotations

import logging
import re
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import uuid4

LOG_DIR = Path("logs")
SESSION_DIR = LOG_DIR / "sessions"
MAIN_LOG = LOG_DIR / "math-agent.log"

_CONFIGURED = False
_SECRET_PATTERNS = (
    re.compile(r"(sk-[A-Za-z0-9_-]{8,})", re.IGNORECASE),
    re.compile(r"(api[_-]?key[\"']?\s*[:=]\s*[\"']?)([^\"'\s,}]+)", re.IGNORECASE),
)


def redact_secrets(text: str) -> str:
    """Mask API keys and similar secrets before writing to logs."""
    out = _SECRET_PATTERNS[0].sub("sk-***", text)
    return _SECRET_PATTERNS[1].sub(r"\1***", out)


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_secrets(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: (redact_secrets(v) if isinstance(v, str) else v)
                    for k, v in record.args.items()
                }
            else:
                record.args = tuple(
                    redact_secrets(a) if isinstance(a, str) else a for a in record.args
                )
        return True


def setup_logging(
    level: str = "INFO",
    log_dir: Path | str | None = None,
    *,
    console: bool = True,
) -> Path:
    """Configure root logging once. Returns the main log file path."""
    global _CONFIGURED, LOG_DIR, SESSION_DIR, MAIN_LOG
    if _CONFIGURED:
        return MAIN_LOG if log_dir is None else Path(log_dir) / "math-agent.log"

    if log_dir is not None:
        LOG_DIR = Path(log_dir)
        SESSION_DIR = LOG_DIR / "sessions"
        MAIN_LOG = LOG_DIR / "math-agent.log"

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_DIR.mkdir(parents=True, exist_ok=True)

    log_level = getattr(logging, level.upper(), logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    redact = _RedactFilter()

    root = logging.getLogger()
    root.setLevel(log_level)
    if root.handlers:
        root.handlers.clear()

    file_handler = RotatingFileHandler(
        MAIN_LOG,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redact)
    root.addHandler(file_handler)

    if console:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        stream_handler.addFilter(redact)
        root.addHandler(stream_handler)

    # Quieter third-party noise at INFO
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)

    _CONFIGURED = True
    logging.getLogger("math_agent").info(
        "Logging initialized level=%s file=%s",
        level.upper(),
        MAIN_LOG.resolve(),
    )
    return MAIN_LOG


def new_session_logger(problem: str, model: str | None = None) -> tuple[str, logging.Logger]:
    """Create a dedicated log file for one solve run (web or CLI)."""
    if not _CONFIGURED:
        setup_logging()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    session_id = f"{stamp}-{uuid4().hex[:8]}"
    path = SESSION_DIR / f"{session_id}.log"

    logger = logging.getLogger(f"math_agent.session.{session_id}")
    # Respect the globally configured level (INFO by default) to avoid token spam.
    logger.setLevel(logging.getLogger().level or logging.INFO)
    logger.propagate = True  # also write to main log
    logger.handlers.clear()

    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler.addFilter(_RedactFilter())
    logger.addHandler(handler)

    logger.info("=== session start id=%s ===", session_id)
    logger.info("log file: %s", path.resolve())
    if model:
        logger.info("model: %s", model)
    logger.info("problem: %s", redact_secrets(problem[:2000]))
    return session_id, logger


def close_session_logger(logger: logging.Logger | None) -> None:
    """Release a session logger's file handle and drop it from the registry.

    Without this a long-running server accumulates one open file descriptor and
    one permanently-referenced ``Logger`` per solve: ``logging`` keeps every
    logger it ever handed out alive in ``Logger.manager.loggerDict``.
    """
    if logger is None:
        return
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            # A close failure must never break the solve that is finishing.
            pass
    logging.Logger.manager.loggerDict.pop(logger.name, None)
