"""Adapter loader: maps a profile integration family to its provider module.

Each family lives under scripts/adapters/<family>/ with one module per provider
(<provider>.py) conforming to that family's _contract.py Protocol. The loader is
the ONLY place that knows provider-name -> module; adding a provider = drop a
module in and select it in the profile. Calendar/doc_sync generalize to the same
shape when their turn comes.
"""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import profile_lib  # noqa: E402
import adaptations_lib  # noqa: E402


def get(family, root=None):
    """Return the adapter module for the family's active provider, or None.

    Liveness is the OUTER gate: an adapter owned by an OFF adaptation is invisible
    to routing - exactly like "no provider configured" - so publish() naturally
    graceful-degrades to None for it, BEFORE the Tier-2 _is_confirmed check runs.
    """
    provider = profile_lib.provider(family, root)
    if provider == "none":
        return None
    if not adaptations_lib.is_live("adapter", f"{family}/{provider}"):
        return None
    try:
        return importlib.import_module(f"adapters.{family}.{provider}")
    except ModuleNotFoundError:
        return None


class NeedsConfirmation(RuntimeError):
    """Raised when publish() is attempted but the integration has not been
    confirmed for external writes (Tier-2). Stops BEFORE any external call so a
    one-time plain-language confirm can be surfaced. Carries the family so the
    caller can build the confirm card."""

    def __init__(self, family):
        self.family = family
        super().__init__(
            f"{family} integration needs a one-time confirm before its first external write")


def _is_confirmed(family, mod, root=None):
    """Tier-2 consent check for the family's active provider.

    An explicit `confirmed` flag in integrations.yaml wins (False blocks). When the
    flag is ABSENT, an integration the operator configured creds for is treated as
    self-confirmed (grandfather-by-config) so a live install is never retroactively
    blocked; the factory arms the gate by writing an explicit confirmed: false."""
    provider = profile_lib.provider(family, root)
    block = profile_lib.integration(family, root).get(provider) or {}
    if "confirmed" in block:
        return bool(block["confirmed"])
    return bool(mod.is_configured(root))


def publish(family, draft, root=None):
    """Tier-2 gated publish. Returns None when no provider is configured (caller
    degrades gracefully); raises NeedsConfirmation when configured-but-unconfirmed
    (no external call is made); otherwise delegates to the provider adapter."""
    mod = get(family, root)
    if mod is None:
        return None
    if not _is_confirmed(family, mod, root):
        raise NeedsConfirmation(family)
    return mod.publish(draft, root)
