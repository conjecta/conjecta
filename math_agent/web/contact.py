"""Public contact-support messages from the marketing homepage."""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_NAME_MAX = 120
_EMAIL_MAX = 254
_MESSAGE_MAX = 4000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_contact_payload(payload: dict[str, Any]) -> dict[str, str]:
    name = str(payload.get("name") or "").strip()
    email = str(payload.get("email") or "").strip()
    message = str(payload.get("message") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required.")
    if len(name) > _NAME_MAX:
        raise HTTPException(status_code=400, detail="Name is too long.")
    if not email or not _EMAIL_RE.match(email) or len(email) > _EMAIL_MAX:
        raise HTTPException(status_code=400, detail="A valid email is required.")
    if not message:
        raise HTTPException(status_code=400, detail="Message is required.")
    if len(message) > _MESSAGE_MAX:
        raise HTTPException(status_code=400, detail="Message is too long.")
    return {"name": name, "email": email.lower(), "message": message}


class ContactStore:
    """Append-only JSONL contact inbox under the logging directory."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def add(self, payload: dict[str, str]) -> dict[str, Any]:
        row = {
            "id": uuid.uuid4().hex,
            "created_at": _now(),
            **payload,
        }
        line = json.dumps(row, ensure_ascii=False) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        return row
