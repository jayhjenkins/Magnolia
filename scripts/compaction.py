"""Lightweight compaction signals for the Adapt build session.

Claude Code auto-compacts in `-p` (headless) mode on its own, so this module is
NOT the safety net. It supplies two best-effort signals the Adapt runner uses:

  1. compact_turn_message() -> "/compact"
     After a successful ship, the runner sends this as its own turn to keep the
     session lean for the next build.

  2. should_recommend_compact(usage, threshold=0.5, model=None) -> bool
     A best-effort nudge when the context window is getting full. Computed from
     the `usage` block of the claude stream-json `result` event (surfaced by
     scripts/chat_runner.normalize's `result` branch as `event["usage"]`).

Design choices (documented):

  * Context-used estimate = input_tokens + cache_read_input_tokens +
    cache_creation_input_tokens. These are the prompt-side tokens the model read
    this turn (cached or not), i.e. how full the window is for the NEXT turn's
    prompt. output_tokens are generated, not occupying the next prompt in the
    same sense, so they are deliberately EXCLUDED. Keeping it prompt-side keeps
    the signal honest about "how full is the window".

  * Window size comes from context_window(model): a tiny constant map keyed by
    a substring of the model id, with a safe default of 200000. Claude models
    are ~200k context; 1m-context variants (model id contains "1m") are
    1_000_000. Unknown models fall back to the default.

  * Graceful degradation is REQUIRED: missing / None / empty / all-zero /
    garbage usage -> False (never block, never raise). The CLI does not
    guarantee usage is present, so a missing/garbage usage is a silent no-nudge.

  * "Past threshold" is a STRICT comparison (`>`): used exactly at the threshold
    is not yet past it.

Pure functions. No I/O. Stdlib only. ASCII-safe.
"""

# Window sizes keyed by a substring matched against the model id. Order matters:
# first matching key wins. Kept tiny on purpose; unknown ids use DEFAULT_WINDOW.
DEFAULT_WINDOW = 200000
_WINDOW_BY_MODEL_SUBSTR = {
    "1m": 1000000,  # 1m-context variants, e.g. "claude-opus-4-8[1m]"
}


def compact_turn_message():
    """The literal turn the runner sends after a successful ship."""
    return "/compact"


def context_window(model=None):
    """Context-window size (in tokens) for ``model``.

    Looks up a substring match in a tiny constant map; falls back to
    DEFAULT_WINDOW (200000) for None or any unrecognized model id.
    """
    if model:
        model_lower = str(model).lower()
        for substr, window in _WINDOW_BY_MODEL_SUBSTR.items():
            if substr in model_lower:
                return window
    return DEFAULT_WINDOW


def _context_used(usage):
    """Sum the prompt-side tokens from a `result` usage block.

    Returns a non-negative int; returns 0 for any missing/garbage shape so the
    caller degrades to a no-nudge rather than crashing.
    """
    if not isinstance(usage, dict):
        return 0
    total = 0
    for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        value = usage.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            # bool is an int subclass; reject it and any non-numeric value.
            continue
        if value > 0:
            total += value
    return total


def should_recommend_compact(usage, threshold=0.5, model=None):
    """Best-effort nudge: is the context window past ``threshold`` full?

    True when prompt-side tokens (input + cache_read + cache_creation) strictly
    exceed ``threshold`` * the model's window. False below or exactly at it, and
    False (never raises) for missing / None / empty / all-zero / garbage usage.
    """
    used = _context_used(usage)
    if used <= 0:
        return False
    return used > threshold * context_window(model)
