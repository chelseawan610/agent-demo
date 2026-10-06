"""File checkpointing for the local, application-owned workflow."""

import json
from pathlib import Path

from travel_agent.schemas.checkpoint import WorkflowCheckpoint


class CheckpointStore:
    """Persist one latest checkpoint without introducing a database."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def save(self, checkpoint: WorkflowCheckpoint) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            checkpoint.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def load(self) -> WorkflowCheckpoint:
        return WorkflowCheckpoint.model_validate_json(
            self.path.read_text(encoding="utf-8")
        )

    def load_or_none(self) -> WorkflowCheckpoint | None:
        """Read a checkpoint for optional recovery without hiding corruption."""

        if not self.path.exists():
            return None
        return self.load()
