"""Contract every project-management adapter must satisfy (legibility only)."""
from typing import Protocol


class NotConfigured(RuntimeError):
    """Raised when publish() is called but the provider/profile isn't set up."""


class ProjectManagementAdapter(Protocol):
    def is_configured(self, root=None) -> bool: ...
    def publish(self, draft: dict, root=None) -> tuple: ...  # -> (issue_key, issue_url)
    def update(self, update_dict: dict, root=None) -> tuple: ...  # -> (issue_key, issue_url)
    def comment(self, update_dict: dict, root=None) -> tuple: ...  # -> (issue_key, issue_url)
    # READ op (NOT an external write -> never Tier-2 gated). Returns the tracker
    # facts for an issue, or None when the issue is not found. Raises NotConfigured
    # when the provider/profile isn't set up (mirror publish). NEVER fabricates.
    def fetch_status(self, issue_key: str, root=None) -> dict | None:  # noqa: F811
        ...  # -> {"status": str, "title": str, "due": str | None} | None
