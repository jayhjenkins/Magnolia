"""Tests for scripts/compaction.py - lightweight compaction signals.

These are two best-effort signals used by the Adapt build session, not a safety
net (Claude Code auto-compacts in -p mode on its own):
  1. compact_turn_message() - the literal "/compact" turn sent after a ship.
  2. should_recommend_compact(usage, ...) - a best-effort "window is filling"
     nudge, which MUST degrade gracefully (never raise, never block) on
     missing/empty/None/all-zero usage.
"""

import compaction


# ─── compact_turn_message ────────────────────────────────────────────────────

def test_compact_turn_message_is_literal_slash_compact():
    assert compaction.compact_turn_message() == "/compact"


# ─── should_recommend_compact: above / below threshold ───────────────────────

def test_recommends_when_input_plus_cache_read_exceeds_default_threshold():
    # 170000 + 0 = 170000 > 0.8 * 200000 (=160000) -> True
    usage = {"input_tokens": 170000, "cache_read_input_tokens": 0}
    assert compaction.should_recommend_compact(usage) is True


def test_cache_read_tokens_count_toward_the_window():
    # 90000 + 90000 = 180000 > 160000 -> True (proves cache_read is summed in)
    usage = {"input_tokens": 90000, "cache_read_input_tokens": 90000}
    assert compaction.should_recommend_compact(usage) is True


def test_does_not_recommend_below_threshold():
    # A large fixed baseline must NOT trip the nudge on its own: 120000 is well
    # past the OLD 0.5 default (100000) but below the 0.8 default (160000).
    usage = {"input_tokens": 120000}
    assert compaction.should_recommend_compact(usage) is False


# ─── boundary: exactly at threshold is NOT past it (strict >) ─────────────────

def test_exactly_at_threshold_does_not_recommend():
    # 160000 == 0.8 * 200000 -> not strictly past -> False
    usage = {"input_tokens": 160000}
    assert compaction.should_recommend_compact(usage) is False


def test_one_token_past_threshold_recommends():
    usage = {"input_tokens": 160001}
    assert compaction.should_recommend_compact(usage) is True


# ─── graceful degradation: missing / empty / None / zero -> False ─────────────

def test_missing_usage_none_returns_false():
    assert compaction.should_recommend_compact(None) is False


def test_empty_usage_dict_returns_false():
    assert compaction.should_recommend_compact({}) is False


def test_all_zero_usage_returns_false():
    assert compaction.should_recommend_compact({"input_tokens": 0}) is False


def test_garbage_usage_returns_false_does_not_raise():
    # Non-numeric / unexpected shapes must be a silent no-nudge, not a crash.
    assert compaction.should_recommend_compact("not a dict") is False
    assert compaction.should_recommend_compact({"input_tokens": "huge"}) is False


# ─── threshold honored ────────────────────────────────────────────────────────

def test_custom_threshold_is_honored():
    usage = {"input_tokens": 50000}
    # 50000 > 0.2 * 200000 (=40000) -> True
    assert compaction.should_recommend_compact(usage, threshold=0.2) is True
    # 50000 > 0.5 * 200000 (=100000) -> False
    assert compaction.should_recommend_compact(usage, threshold=0.5) is False


# ─── model window: 1m variant raises the window ──────────────────────────────

def test_1m_model_raises_window_so_same_tokens_no_longer_trip():
    usage = {"input_tokens": 170000}
    # Trips the 200k default (170000 > 0.8 * 200000 = 160000)...
    assert compaction.should_recommend_compact(usage) is True
    # ...but not a 1m-context model (0.8 * 1_000_000 = 800000).
    assert compaction.should_recommend_compact(usage, model="claude-opus-4-8[1m]") is False


def test_context_window_default_and_1m():
    assert compaction.context_window() == 200000
    assert compaction.context_window(None) == 200000
    assert compaction.context_window("claude-sonnet-4-6") == 200000
    assert compaction.context_window("claude-opus-4-8[1m]") == 1000000
    assert compaction.context_window("some-1m-model") == 1000000
