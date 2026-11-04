"""Shared connector contract. Real GitHub/Jira/Slack/Calendar/Notion connectors (Stages 2-4) implement this
interface; the importer, action executor and UI only ever talk to it.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class ProviderError(Exception):
    """Normalised provider failure. `kind` drives retry and UI messaging."""

    def __init__(self, kind: str, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind  # auth | permission | rate_limit | timeout | not_found | transient | invalid | unknown
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.kind in ("rate_limit", "timeout", "transient")


@dataclass
class ValidationResult:
    ok: bool
    message: str = ""
    scopes: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)


@dataclass
class SyncBatch:
    """Provider-agnostic normalised records plus the checkpoint to store after a successful import."""
    records: dict[str, list[dict]]
    next_checkpoint: str | None = None
    partial_errors: list[str] = field(default_factory=list)


@dataclass
class ActionResult:
    external_id: str
    external_url: str | None = None
    simulated: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


class Connector(ABC):
    provider: str
    is_demo: bool = False

    @abstractmethod
    def validate_connection(self) -> ValidationResult: ...

    @abstractmethod
    def discover_resources(self) -> list[dict]: ...

    @abstractmethod
    def sync(self, mode: str, checkpoint: str | None) -> SyncBatch:
        """mode is 'initial' or 'incremental'. Incremental receives the previous checkpoint."""

    @abstractmethod
    def execute_action(self, action_type: str, target: dict, payload: dict, idempotency_key: str) -> ActionResult: ...

    @abstractmethod
    def find_existing_action(self, idempotency_key: str) -> ActionResult | None:
        """Reconcile an uncertain outcome: did the provider already perform this action?"""

    def supports(self, action_type: str) -> bool:
        return action_type in self.validate_connection().capabilities
