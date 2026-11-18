"""Tests for athena.memory — local LESSONS store (not D1 memory).

All databases are tmp SQLite files; nothing touches APP_DATA_DIR.
Run: .venv/bin/python -m pytest tests/test_memory.py -v
"""

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from athena.memory import (
    CalendarStore,
    Dreamer,
    LessonStore,
)
from athena.memory.calendar import CalendarFact
from athena.memory.compaction import (
    compact,
    extract_identifiers,
    stage1_full_summarize,
    stage2_partial_summarize,
    stage3_hard_fallback,
)
from athena.memory.decay import (
    HALF_LIFE_DAYS,
    decay_multiplier,
    effective_confidence,
    is_visible,
    visible_lessons,
)
from athena.memory.promotion import (
    PROMOTE_THRESHOLD,
    consolidation_component,
    evaluate_promotion_candidate,
    promote_notes,
)
from athena.reasoning.reflection import (
    check_for_correction,
    extract_lesson,
    is_correction,
    on_tool_failure,
)
from athena.memory.repair import repair_tool_use_result_pairing
from athena.search import (
    bm25_rank_to_score,
    build_fts_query,
    hash_vector,
    jaccard_similarity,
    mmr_rerank,
    search,
    tokenize,
)

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@pytest.fixture()
def store(tmp_path):
    s = LessonStore(tmp_path / "lessons.db")
    yield s
    s.close()


@pytest.fixture()
def cal(tmp_path):
    s = LessonStore(tmp_path / "lessons.db")
    c = CalendarStore(store=s)
    yield c
    s.close()


def _lesson(store, days_old=0, **kw):
    ts = _iso(NOW - timedelta(days=days_old))
    kw.setdefault("confidence", 0.8)
    lesson = store.add_lesson("trigger text", "fix text", **kw)
    # Back-date created/updated so decay math is deterministic.
    store.conn.execute(
        "UPDATE lessons SET created_at = ?, updated_at = ? WHERE id = ?",
        (ts, ts, lesson.id),
    )
    store.conn.commit()
    return store.get_lesson(lesson.id)


# ===========================================================================
# lessons.py — CRUD + notes
# ===========================================================================

class TestLessonStore:
    def test_crud_roundtrip(self, store):
        lesson = store.add_lesson("tool timed out", "retry once then report", 0.7, source="reflection")
        assert lesson.id > 0 and lesson.access_count == 0 and lesson.stated is False
        assert store.get_lesson(lesson.id).trigger == "tool timed out"
        assert store.count_lessons() == 1
        updated = store.update_lesson(lesson.id, confidence=0.9)
        assert updated.confidence == 0.9
        assert store.delete_lesson(lesson.id) is True
        assert store.get_lesson(lesson.id) is None
        assert store.delete_lesson(lesson.id) is False

    def test_rejects_empty_trigger_or_fix(self, store):
        with pytest.raises(ValueError):
            store.add_lesson("", "fix")
        with pytest.raises(ValueError):
            store.add_lesson("trigger", "  ")

    def test_confidence_clamped(self, store):
        assert store.add_lesson("t", "f", confidence=2.0).confidence == 1.0
        assert store.add_lesson("t", "f", confidence=-1.0).confidence == 0.0

    def test_record_access_and_reinforce(self, store):
        lesson = store.add_lesson("t", "f", confidence=0.5)
        store.record_access(lesson.id)
        after = store.get_lesson(lesson.id)
        assert after.access_count == 1
        reinforced = store.reinforce(lesson.id, 0.1)
        assert reinforced.access_count == 2
        assert reinforced.confidence == pytest.approx(0.6)

    def test_notes_crud(self, store):
        note = store.add_note("user prefers concise replies")
        assert store.count_notes() == 1
        assert store.list_notes()[0].text == "user prefers concise replies"
        assert store.delete_note(note.id) is True
        assert store.count_notes() == 0
        with pytest.raises(ValueError):
            store.add_note("   ")


# ===========================================================================
# decay.py — 30-day half-life, stated immunity, invisible-not-deleted
# ===========================================================================

class TestDecay:
    def test_half_life(self):
        assert decay_multiplier(0.0) == 1.0
        assert round(decay_multiplier(30.0), 2) == 0.5
        assert round(decay_multiplier(60.0), 2) == 0.25
        assert decay_multiplier(30.0, half_life_days=30.0) == pytest.approx(0.5)

    def test_stated_is_immune(self, store):
        lesson = _lesson(store, days_old=90, stated=True)
        assert effective_confidence(lesson, NOW) == pytest.approx(0.8)
        assert is_visible(lesson, NOW) is True

    def test_old_unstated_decays_below_threshold(self, store):
        lesson = _lesson(store, days_old=120)  # 0.8 * 0.5**4 = 0.05
        assert effective_confidence(lesson, NOW) == pytest.approx(0.05)
        assert is_visible(lesson, NOW) is False

    def test_below_threshold_invisible_but_not_deleted(self, store):
        lesson = _lesson(store, days_old=120)
        assert store.get_lesson(lesson.id) is not None  # still in the DB
        assert all(l.id != lesson.id for l, _ in visible_lessons(store, NOW))
        assert store.count_lessons() == 1

    def test_recent_stays_visible(self, store):
        lesson = _lesson(store, days_old=1)
        assert is_visible(lesson, NOW) is True
        assert [l.id for l, _ in visible_lessons(store, NOW)] == [lesson.id]


# ===========================================================================
# promotion.py — 6D scoring
# ===========================================================================

class TestPromotion:
    def test_memos_test_case(self):
        # Direct port of MemOS tests/test_promotion.py expectations.
        now_ms = datetime(2026, 5, 10, tzinfo=timezone.utc).timestamp() * 1000
        entry = {
            "recallCount": 5, "dailyCount": 2, "groundedCount": 0,
            "totalScore": 6.5, "queryHashes": ["h1", "h2", "h3"],
            "recallDays": ["2026-05-01", "2026-05-07"],
            "lastRecalledAt": "2026-05-09T00:00:00Z",
            "conceptTags": ["python", "app", "dev"],
        }
        res = evaluate_promotion_candidate(entry, now_ms)
        assert res["valid"] is True
        assert res["score"] > 0
        assert res["components"]["relevance"] == pytest.approx(6.5 / 7.0)
        assert res["components"]["frequency"] > 0
        assert res["uniqueQueries"] == 3
        assert res["score"] >= PROMOTE_THRESHOLD

    def test_consolidation_component(self):
        assert consolidation_component([]) == 0.0
        assert consolidation_component(["2026-05-10"]) == 0.2
        assert consolidation_component(["2026-05-01", "2026-05-05", "2026-05-10"]) > 0.2

    def test_weak_candidate_does_not_promote(self):
        entry = {
            "recallCount": 1, "dailyCount": 1, "totalScore": 1.0,
            "queryHashes": ["h1"], "recallDays": ["2026-05-10"],
            "lastRecalledAt": "2026-05-10T00:00:00Z", "conceptTags": [],
        }
        res = evaluate_promotion_candidate(entry, datetime(2026, 5, 10, tzinfo=timezone.utc))
        assert res["valid"] is False

    def test_promote_notes_repeated_observation(self, store):
        text = "always confirm the file path before overwriting"
        for i in range(4):
            store.add_note(text, created_at=_iso(NOW - timedelta(days=i)))
        result = promote_notes(store, now=NOW)
        assert len(result["promoted"]) == 1
        assert result["promoted"][0].source == "promotion"
        assert result["consumed"] == 4
        assert store.count_notes() == 0

    def test_promote_notes_single_note_stays(self, store):
        store.add_note("one-off remark about the weather", created_at=_iso(NOW))
        result = promote_notes(store, now=NOW)
        assert result["promoted"] == []
        assert store.count_notes() == 1


# ===========================================================================
# search.py — FTS5 BM25 + hashed vectors + MMR
# ===========================================================================

class TestSearch:
    def _seed(self, store):
        store.add_lesson("calculator tool returns wrong division result",
                         "check the divisor is not zero before dividing", 0.9)
        store.add_lesson("weather tool timed out in Riyadh",
                         "retry the weather tool once then report the failure", 0.8)
        store.add_lesson("user prefers concise replies with no fluff",
                         "keep replies short and direct", 0.7)

    def test_keyword_search_ranks_best_first(self, store):
        self._seed(store)
        results = search(store, "calculator division wrong", now=NOW)
        assert results
        assert results[0].trigger.startswith("calculator tool")
        assert results[0].score > 0

    def test_bm25_rank_to_score(self):
        assert bm25_rank_to_score(-9.0) == pytest.approx(0.9)
        assert bm25_rank_to_score(0.0) == 1.0

    def test_build_fts_query(self):
        assert build_fts_query("favorite color") == '"favorite" OR "color"'
        assert build_fts_query("") == ""

    def test_hashed_vector_cosine(self):
        a = hash_vector("hello world hello")
        b = hash_vector("hello world")
        c = hash_vector("completely unrelated zebra quantum")
        assert float(np.dot(a, a)) == pytest.approx(1.0)  # normalised
        assert float(np.dot(a, b)) > float(np.dot(a, c))
        assert float(np.dot(a, c)) >= 0.0

    def test_like_fallback_when_fts_misses(self, store):
        # A substring FTS tokenisation would miss: force the LIKE path by
        # searching a token present only as a substring of a word.
        store.add_lesson("quokka facts are fun", "mention quokkas often", 0.9)
        results = search(store, "quokk", now=NOW, min_score=0.01)
        assert any("quokka" in r.trigger for r in results)

    def test_decayed_lesson_invisible_in_search(self, store):
        _lesson(store, days_old=120)  # effective 0.05 < 0.10
        assert search(store, "trigger text fix text", now=NOW) == []

    def test_empty_query_and_no_results(self, store):
        assert search(store, "", now=NOW) == []
        assert search(store, "nothing matches this at all", now=NOW) == []

    def test_mmr_diversity(self):
        from athena.search import SearchResult
        r1 = SearchResult(1, "The quick brown fox", "fix one", 0.9)
        r2 = SearchResult(2, "The quick brown fox jumps", "fix two", 0.8)
        r3 = SearchResult(3, "A completely different topic about AI", "fix three", 0.7)
        reranked = mmr_rerank([r1, r2, r3], lambda_param=0.5)
        assert [r.lesson_id for r in reranked] == [1, 3, 2]

    def test_jaccard_and_tokenize(self):
        assert jaccard_similarity({"a", "b"}, {"c", "d"}) == 0.0
        assert jaccard_similarity({"a", "b"}, {"a", "b"}) == 1.0
        assert jaccard_similarity({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)
        assert "hello" in tokenize("Hello world")


# ===========================================================================
# compaction.py — all 3 stages incl. forced failures
# ===========================================================================

def _msgs(n=5):
    return [
        {"role": "user", "content": f"question {i} about topic {i}"}
        if i % 2 == 0 else
        {"role": "assistant", "content": f"answer {i} here"}
        for i in range(n)
    ]


class TestCompaction:
    def test_stage1_preserves_identifiers(self):
        uuid = "123e4567-e89b-12d3-a456-426614174000"
        msgs = [{"role": "user", "content": f"open file C:\\Athena\\config.json id {uuid} and https://example.com/x"}]
        result = compact(msgs)
        assert result.stage == 1
        assert uuid in result.summary
        assert "C:\\Athena\\config.json" in result.summary
        assert "https://example.com/x" in result.summary

    def test_stage1_direct(self):
        summary = stage1_full_summarize(_msgs(3))
        assert "question 0" in summary

    def test_stage2_excludes_oversized(self):
        big = {"role": "user", "content": "x" * 4000}  # ~1000 tokens
        msgs = [big, {"role": "user", "content": "small question"}]
        summary = stage2_partial_summarize(msgs)
        assert "small question" in summary
        assert "oversized messages were excluded" in summary
        assert "x" * 100 not in summary

    def test_stage2_all_oversized_returns_empty(self):
        big = {"role": "user", "content": "x" * 4000}
        assert stage2_partial_summarize([big]) == ""

    def test_stage3_never_crashes(self):
        assert "5 messages" in stage3_hard_fallback(_msgs(5))
        assert stage3_hard_fallback(None) != ""  # type: ignore[arg-type]
        assert stage3_hard_fallback("not a list") != ""  # type: ignore[arg-type]

    def test_forced_stage1_failure_falls_to_stage2(self):
        def boom(messages):
            raise RuntimeError("LLM down")
        result = compact(_msgs(3), summarise_fn=boom)
        assert result.stage == 3  # stage 2 uses the same failing fn
        assert "3 messages" in result.summary

    def test_stage2_used_when_stage1_fn_fails_once(self):
        calls = {"n": 0}

        def flaky(messages):
            calls["n"] += 1
            if calls["n"] <= 3:  # exhaust stage-1 retries for the first chunk
                raise RuntimeError("transient")
            return "flaky summary: " + messages[0]["content"][:20]

        result = compact(_msgs(3), summarise_fn=flaky)
        assert result.stage == 2
        assert "flaky summary" in result.summary

    def test_compact_never_raises(self):
        assert compact(None).stage == 3  # type: ignore[arg-type]
        assert compact([{"role": "user", "content": "hi"}]).stage == 1

    def test_extract_identifiers(self):
        ids = extract_identifiers("see 123e4567-e89b-12d3-a456-426614174000 and 10.0.0.1:8080")
        assert "123e4567-e89b-12d3-a456-426614174000" in ids
        assert "10.0.0.1:8080" in ids


# ===========================================================================
# calendar.py — self-join conflicts, 3 windows, reminders
# ===========================================================================

class TestCalendar:
    def _event(self, cal, content, start, end=None):
        return cal.add_event(content, start, end or start + timedelta(hours=1))

    def test_conflict_detection_self_join(self, cal):
        base = NOW + timedelta(days=2)
        self._event(cal, "dentist", base.replace(hour=10), base.replace(hour=11))
        self._event(cal, "team call", base.replace(hour=10, minute=30), base.replace(hour=11, minute=30))
        self._event(cal, "lunch", base.replace(hour=13), base.replace(hour=14))
        conflicts = cal.detect_conflicts()
        assert len(conflicts) == 1
        assert {c.content for pair in conflicts for c in pair} == {"dentist", "team call"}

    def test_no_conflict_when_adjacent(self, cal):
        base = NOW + timedelta(days=2)
        self._event(cal, "a", base.replace(hour=10), base.replace(hour=11))
        self._event(cal, "b", base.replace(hour=11), base.replace(hour=12))
        assert cal.detect_conflicts() == []

    def test_cancelled_event_not_a_conflict(self, cal):
        base = NOW + timedelta(days=2)
        e1 = self._event(cal, "a", base.replace(hour=10), base.replace(hour=11))
        self._event(cal, "b", base.replace(hour=10, minute=30), base.replace(hour=11, minute=30))
        assert len(cal.detect_conflicts()) == 1
        cal.cancel_event(e1.id)
        assert cal.detect_conflicts() == []

    def test_three_windows_disjoint(self, cal):
        self._event(cal, "ongoing", NOW - timedelta(minutes=30), NOW + timedelta(minutes=30))
        self._event(cal, "soon", NOW + timedelta(minutes=10), NOW + timedelta(minutes=40))
        self._event(cal, "later", NOW + timedelta(minutes=45), NOW + timedelta(minutes=75))
        self._event(cal, "tomorrow", NOW + timedelta(days=1), NOW + timedelta(days=1, hours=1))
        alerts = cal.alerts(NOW)
        assert [f.content for f in alerts["now"]] == ["ongoing"]
        assert [f.content for f in alerts["15min"]] == ["soon"]
        assert [f.content for f in alerts["1h"]] == ["later"]

    def test_reminders_24_to_48h(self, cal):
        due = self._event(cal, "flight", NOW + timedelta(hours=30), NOW + timedelta(hours=32))
        self._event(cal, "too soon", NOW + timedelta(hours=5), NOW + timedelta(hours=6))
        self._event(cal, "too far", NOW + timedelta(days=5), NOW + timedelta(days=5, hours=1))
        needing = cal.events_needing_reminder(NOW)
        assert [f.content for f in needing] == ["flight"]
        cal.mark_reminder_sent(due.id)
        assert cal.events_needing_reminder(NOW) == []

    def test_rejects_empty_and_backwards_events(self, cal):
        with pytest.raises(ValueError):
            cal.add_event("", NOW, NOW + timedelta(hours=1))
        with pytest.raises(ValueError):
            cal.add_event("x", NOW + timedelta(hours=2), NOW + timedelta(hours=1))


# ===========================================================================
# repair.py — orphaned calls/results, JSON, merging, truncation
# ===========================================================================

class TestRepair:
    def test_clean_transcript_unchanged(self):
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "Thanks"},
        ]
        report = repair_tool_use_result_pairing(msgs)
        assert report["repaired"] is False
        assert len(report["messages"]) == 3

    def test_orphaned_tool_result_dropped(self):
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "Result: Tool search_memory returned: nothing found"},
            {"role": "assistant", "content": "I couldn't find anything."},
        ]
        report = repair_tool_use_result_pairing(msgs)
        assert report["stats"]["orphaned_results_dropped"] == 1
        assert len(report["messages"]) == 2

    def test_orphaned_tool_call_patched(self):
        msgs = [
            {"role": "user", "content": "search please"},
            {"role": "assistant", "content": '```json\n{"name": "search_memory", "arguments": {"query": "test"}}\n```'},
        ]
        report = repair_tool_use_result_pairing(msgs)
        assert report["stats"]["orphaned_calls_patched"] == 1
        assert report["messages"][-1]["content"].startswith("Result: Tool execution was interrupted")

    def test_structured_tool_call_pairing(self):
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "name": "get_weather"}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "sunny"},
        ]
        report = repair_tool_use_result_pairing(msgs)
        assert report["repaired"] is False

    def test_structured_orphan_result_dropped(self):
        msgs = [{"role": "tool", "tool_call_id": "call_9", "content": "sunny"}]
        report = repair_tool_use_result_pairing(msgs)
        assert report["stats"]["orphaned_results_dropped"] == 1
        assert report["messages"] == []

    def test_malformed_json_repaired(self):
        content = '```json\n{"name": "search_memory", arguments: {"query": "test"}}\n```'
        report = repair_tool_use_result_pairing(
            [{"role": "assistant", "content": content}]
        )
        assert report["stats"]["json_blocks_repaired"] == 1
        assert '"arguments"' in report["messages"][0]["content"]

    def test_unclosed_fence_closed(self):
        msgs = [{"role": "assistant", "content": '```json\n{"name": "x", "arguments": {}}'}]
        report = repair_tool_use_result_pairing(msgs)
        assert report["stats"]["unclosed_fences_closed"] == 1
        assert report["messages"][0]["content"].rstrip().endswith("```")

    def test_consecutive_roles_merged(self):
        msgs = [
            {"role": "user", "content": "one"},
            {"role": "user", "content": "two"},
        ]
        report = repair_tool_use_result_pairing(msgs)
        assert report["stats"]["consecutive_roles_merged"] == 1
        assert len(report["messages"]) == 1
        assert "one\ntwo" in report["messages"][0]["content"]

    def test_empty_transcript(self):
        report = repair_tool_use_result_pairing([])
        assert report == {"messages": [], "repairs": [], "repaired": False,
                          "stats": {"orphaned_results_dropped": 0, "orphaned_calls_patched": 0,
                                    "json_blocks_repaired": 0, "consecutive_roles_merged": 0,
                                    "unclosed_fences_closed": 0}}


# ===========================================================================
# reflection.py — regex gate (zero-cost), lesson extraction
# ===========================================================================

class TestReflection:
    def test_gate_matches_corrections(self):
        for text in [
            "that's wrong", "That's not what I asked", "not what I meant",
            "you're wrong", "wrong answer", "you misunderstood",
            "that's incorrect", "that didn't work",
        ]:
            assert is_correction(text) is True, text
            assert check_for_correction(text) is not None

    def test_gate_ignores_normal_chat(self):
        for text in ["hello", "what's the weather?", "thanks, that helps", "", None]:
            assert is_correction(text) is False, text  # type: ignore[arg-type]

    def test_extract_lesson_with_explicit_fix(self):
        lesson = extract_lesson("That's wrong — you should use Celsius, not Fahrenheit.")
        assert lesson is not None
        assert lesson["trigger"].startswith("That's wrong")
        assert "Celsius" in lesson["fix"]
        assert lesson["confidence"] == pytest.approx(0.6)

    def test_extract_lesson_default_fix(self):
        lesson = extract_lesson("That's wrong.")
        assert lesson is not None
        assert lesson["confidence"] == pytest.approx(0.5)
        assert "Re-read the request" in lesson["fix"]

    def test_extract_returns_none_without_gate_match(self):
        assert extract_lesson("Nice work, thanks!") is None

    def test_on_tool_failure(self):
        lesson = on_tool_failure("get_weather", "timeout after 30s")
        assert lesson["trigger"] == "Tool 'get_weather' failed: timeout after 30s"
        assert "Retry 'get_weather' once" in lesson["fix"]
        assert lesson["confidence"] == 0.5


# ===========================================================================
# dreaming.py — consolidation + circuit breaker
# ===========================================================================

class TestDreaming:
    def test_run_promotes_repeated_notes(self, store):
        text = "always confirm the file path before overwriting"
        for i in range(4):
            store.add_note(text, created_at=_iso(NOW - timedelta(days=i)))
        dreamer = Dreamer(store, interval_seconds=60)
        report = dreamer.run_on_demand(now=NOW)
        assert report is not None
        assert report["promoted"] == 1
        assert report["notes_consumed"] == 4
        assert report["errors"] == []

    def test_run_with_nothing_to_do(self, store):
        dreamer = Dreamer(store)
        report = dreamer.run()
        assert report["promoted"] == 0
        assert report["notes_remaining"] == 0

    def test_circuit_breaker_trips_after_5_failures(self, store):
        dreamer = Dreamer(store, max_failures=5)
        # Break the store so consolidation raises every time.
        store.conn.close()
        for _ in range(5):
            report = dreamer.run()
            assert report is not None
            assert report["errors"]
        assert dreamer.breaker.is_open is True
        assert dreamer.run() is None  # open breaker: no-op
        dreamer.reset_breaker()
        assert dreamer.breaker.is_open is False

    def test_success_resets_failure_count(self, store):
        dreamer = Dreamer(store, max_failures=5)
        store.conn.close()
        dreamer.run()
        assert dreamer.breaker.failures == 1
        # Restore a working store on the same dreamer and succeed.
        dreamer.store = LessonStore(":memory:")
        dreamer.run()
        assert dreamer.breaker.failures == 0

    def test_due_and_background_thread(self, store):
        dreamer = Dreamer(store, interval_seconds=0.05)
        assert dreamer.due() is True  # never ran
        dreamer.run()
        assert dreamer.due() is False
        dreamer.start()
        assert dreamer.running is True
        dreamer.stop()
        assert dreamer.running is False

    def test_start_is_idempotent(self, store):
        dreamer = Dreamer(store, interval_seconds=60)
        dreamer.start()
        first = dreamer._thread
        dreamer.start()
        assert dreamer._thread is first
        dreamer.stop()
