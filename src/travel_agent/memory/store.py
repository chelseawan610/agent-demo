"""Small opt-in long-term preference memory for local research runs."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field


MEMORY_SCHEMA_VERSION = 2


class UserMemory(BaseModel):
    """Facts deliberately limited to reusable travel preferences."""

    schema_version: int = MEMORY_SCHEMA_VERSION
    user_id: str
    preferences: list[str] = Field(default_factory=list)
    updated_at: datetime
    source: Literal["explicit_user_preference"] = "explicit_user_preference"


class MemoryStore(Protocol):
    """Storage contract that JSON, SQLite, or a hosted store can implement."""

    def get(self, user_id: str) -> UserMemory:
        ...

    def remember_preferences(self, user_id: str, preferences: list[str]) -> UserMemory:
        ...

    def forget_preferences(self, user_id: str, preferences: list[str]) -> UserMemory:
        ...

    def forget(self, user_id: str) -> None:
        ...


class JsonMemoryStore:
    """An atomic, local JSON store; no database or external service required."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def get(self, user_id: str) -> UserMemory:
        data = self._read()
        users = data.get("users", {})
        if not isinstance(users, dict):
            users = {}
        raw = users.get(user_id)
        if not raw:
            return UserMemory(
                user_id=user_id,
                preferences=[],
                updated_at=datetime.now(timezone.utc),
            )
        try:
            return UserMemory.model_validate(raw)
        except ValueError:
            return UserMemory(
                user_id=user_id,
                preferences=[],
                updated_at=datetime.now(timezone.utc),
            )

    def remember_preferences(self, user_id: str, preferences: list[str]) -> UserMemory:
        current = self.get(user_id)
        # The caller is expected to pass only preferences explicitly expressed
        # by the user. We intentionally do not persist the full prompt,
        # model output, tool results, dates, or budget.
        cleaned = [
            item.strip()[:120]
            for item in preferences
            if isinstance(item, str) and item.strip()
        ]
        merged = list(dict.fromkeys([*current.preferences, *cleaned]))
        # Keep memory bounded and avoid storing giant model-generated strings.
        bounded = [item for item in merged if item.strip()][:50]
        updated = UserMemory(
            user_id=user_id,
            preferences=bounded,
            updated_at=datetime.now(timezone.utc),
        )
        data = self._read()
        users = data.setdefault("users", {})
        if not isinstance(users, dict):
            users = {}
            data["users"] = users
        users[user_id] = updated.model_dump(mode="json")
        self._write(data)
        return updated

    def forget_preferences(self, user_id: str, preferences: list[str]) -> UserMemory:
        """Remove selected preferences without deleting the whole user record."""

        current = self.get(user_id)
        remove = {item.strip() for item in preferences if isinstance(item, str)}
        remaining = [item for item in current.preferences if item not in remove]
        updated = UserMemory(
            user_id=user_id,
            preferences=remaining,
            updated_at=datetime.now(timezone.utc),
        )
        data = self._read()
        users = data.setdefault("users", {})
        if isinstance(users, dict):
            users[user_id] = updated.model_dump(mode="json")
        self._write(data)
        return updated

    def forget(self, user_id: str) -> None:
        data = self._read()
        users = data.setdefault("users", {})
        if isinstance(users, dict):
            users.pop(user_id, None)
        self._write(data)

    def _read(self) -> dict:
        if not self.path.exists():
            return {"version": MEMORY_SCHEMA_VERSION, "users": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A corrupt local memory file must not crash a travel request.
            return {"version": MEMORY_SCHEMA_VERSION, "users": {}}
        if not isinstance(value, dict):
            return {"version": MEMORY_SCHEMA_VERSION, "users": {}}
        users = value.get("users")
        return {
            "version": MEMORY_SCHEMA_VERSION,
            "users": users if isinstance(users, dict) else {},
        }

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data["version"] = MEMORY_SCHEMA_VERSION
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)
