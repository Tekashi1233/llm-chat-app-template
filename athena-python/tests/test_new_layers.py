"""Tests for the new layers: agents, smart router, execution engine,
background scheduler, terminal chat, security filters, FTS search,
chain-of-thought, and the config/policies re-export shims.

All collaborators are fakes — no network, no Qt, no audio.
Run: .venv/bin/python -m pytest tests/test_new_layers.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from athena.agents.agent import AgentLoop, parse_tool_call
from athena.agents.base_agent import BaseAgent
from athena.background.decay_watcher import DecayWatcher, sweep
from athena.background.reminders import check_reminders, due_alerts
from athena.background.scheduler import IntervalScheduler
from athena.execution.engine import ExecutionEngine, Plan, PlanStep
from athena.execution.state_manager import ExecutionStateManager
from athena.interfaces.terminal.chat import run_chat
from athena.memory.calendar import CalendarStore
from athena.memory.lessons import LessonStore
from athena.reasoning.chain_of_thought import (
    add_thought_instruction,
    build_thought_instruction,
    strip_thought_block,
)
from athena.router.smart_router import RouteDecision, SmartRouter, Tier
from athena.search.fts_search import bm25_rank_to_score, build_fts_query, keyword_scores
from athena.security.filters import (
    is_sensitive_key,
    redact,
    redact_mapping,
    sanitize_for_log,
    validate_message,
)
from athena.tools.base_tool import ToolResult

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


# ----------------------------------------------------------------------
# fakes
# ----------------------------------------------------------------------

class FakeProvider:
    """Scripted provider: replies in order, records messages."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.name = "fake"

    @property
    def is_available(self):
        return True

    def generate(self, messages, **kwargs):
        self.calls.append(messages)
        if not self.replies:
            return ""
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeRouter:
    """Category router fake with one tool."""

    def __init__(self, category="calc"):
        self.category = category
        self.dispatched = []

    def route(self, text):
        return self.category

    def category_schemas(self, category):
        return [{"type": "function", "function": {
            "name": "calculate",
            "description": "Evaluate a maths expression.",
            "parameters": {"type": "object", "properties": {}},
        }}]

    def dispatch(self, category, tool_name, args, context=None):
        self.dispatched.append((category, tool_name, args))
        if tool_name == "calculate":
            return ToolResult.success("4")
        return ToolResult.failure(f"Unknown tool: {tool_name!r}.")


class FakeMemoryStore:
    """Lesson-store stand-in recording add_lesson calls."""

    def __init__(self):
        self.lessons = []

    def add_lesson(self, **kwargs):
        self.lessons.append(kwargs)
        return kwargs


# ----------------------------------------------------------------------
# chain of thought
# ----------------------------------------------------------------------

def test_strip_thought_block_removes_closed_and_unclosed():
    assert strip_thought_block("<thought>hmm</thought>42") == "42"
    assert strip_thought_block("before <thought>never finished") == "before"
    assert strip_thought_block("plain answer") == "plain answer"


def test_build_thought_instruction_asks_for_thought_block():
    text = build_thought_instruction()
    assert "<thought>" in text and "</thought>" in text
    messages = add_thought_instruction([{"role": "user", "content": "hi"}])
    assert messages[0]["role"] == "system" and messages[0]["content"] == text
    assert messages[1]["content"] == "hi"


def test_parse_tool_call_bare_fenced_and_none():
    assert parse_tool_call('{"tool": "calculate", "args": {"expression": "2+2"}}') == {
        "tool": "calculate", "args": {"expression": "2+2"}}
    assert parse_tool_call('```json\n{"tool": "calculate", "args": {"x": {"y": 1}}}\n```') == {
        "tool": "calculate", "args": {"x": {"y": 1}}}
    assert parse_tool_call("Just a plain answer.") is None
    assert parse_tool_call('{"tool": ""}') is None


# ----------------------------------------------------------------------
# agent loop
# ----------------------------------------------------------------------

def test_agent_loop_simple_turn_no_tools():
    provider = FakeProvider(["Hello there."])
    loop = AgentLoop(provider, router=FakeRouter(category="general"))
    assert loop.run_turn("hello") == "Hello there."
    assert provider.calls[0][-1] == {"role": "user", "content": "hello"}


def test_agent_loop_tool_call_then_final_answer():
    provider = FakeProvider([
        '{"tool": "calculate", "args": {"expression": "2+2"}}',
        "2 + 2 is 4.",
    ])
    loop = AgentLoop(provider, router=FakeRouter(category="calc"))
    assert loop.run_turn("what is 2+2?") == "2 + 2 is 4."
    assert loop._router.dispatched == [("calc", "calculate", {"expression": "2+2"})]


def test_agent_loop_tool_call_cap():
    # Provider always asks for another tool call: the loop must stop
    # at max_tool_calls and still answer (from the tool results).
    provider = FakeProvider(
        ['{"tool": "calculate", "args": {}}'] * 2 + ["Final: 4."]
    )
    loop = AgentLoop(provider, router=FakeRouter(category="calc"), max_tool_calls=2)
    assert loop.run_turn("calc please") == "Final: 4."
    assert len(loop._router.dispatched) == 2


def test_agent_loop_tool_failure_reflects_and_stops_after_two():
    provider = FakeProvider([
        '{"tool": "nope", "args": {}}',
        '{"tool": "nope", "args": {}}',
    ])
    store = FakeMemoryStore()
    loop = AgentLoop(provider, router=FakeRouter(category="calc"), memory_store=store)
    reply = loop.run_turn("do the thing")
    assert "twice" in reply and "kept failing" in reply
    assert len(store.lessons) == 2  # one reflection per failure


def test_agent_loop_never_raises_when_provider_down():
    provider = FakeProvider([RuntimeError("boom")])
    loop = AgentLoop(provider, router=FakeRouter(category="general"))
    reply = loop.run_turn("hello")
    assert "couldn't reach" in reply.lower() or "sorry" in reply.lower()


def test_agent_loop_is_a_base_agent():
    assert isinstance(AgentLoop(FakeProvider([])), BaseAgent)


def test_agent_loop_correction_stores_lesson():
    provider = FakeProvider(["Noted."])
    store = FakeMemoryStore()
    loop = AgentLoop(provider, router=FakeRouter(category="general"), memory_store=store)
    loop.run_turn("That's wrong, you should have used the local model instead.")
    assert len(store.lessons) == 1
    assert "local model" in store.lessons[0]["fix"]


# ----------------------------------------------------------------------
# smart router
# ----------------------------------------------------------------------

def test_smart_router_simple_greeting():
    decision = SmartRouter().classify("hello there")
    assert decision.tier is Tier.SIMPLE


def test_smart_router_medium_tool_category():
    decision = SmartRouter().classify("will it rain in Riyadh tomorrow?")
    assert decision.tier is Tier.MEDIUM
    assert decision.category == "weather"


def test_smart_router_complex_multi_step():
    decision = SmartRouter().classify("first check the weather and then remind me tomorrow")
    assert decision.tier is Tier.COMPLEX


def test_smart_router_clarify_then_escalate():
    router = SmartRouter()
    first = router.classify("do something")
    assert first.tier is Tier.CLARIFY
    assert first.question
    escalated = router.classify("do something", clarifications_asked=3)
    assert escalated.tier is Tier.COMPLEX
    assert escalated.question is None


def test_smart_router_llm_failure_falls_back_to_rules():
    # Rules cannot classify this, so the LLM is consulted — and it
    # fails. The router must still answer (SIMPLE default) and never
    # raise or block.
    provider = FakeProvider([RuntimeError("llm down")])
    decision = SmartRouter(provider).classify("quasars and dark matter")
    assert decision.tier is Tier.SIMPLE
    # Rules alone classify this one; a dead LLM is never even asked.
    decision = SmartRouter(provider).classify("what time is it?")
    assert decision.tier is Tier.MEDIUM
    assert decision.category == "time"


def test_smart_router_llm_second_opinion_used_when_rules_unsure():
    provider = FakeProvider(["MEDIUM"])
    decision = SmartRouter(provider).classify("quasars and dark matter")
    assert decision.tier is Tier.MEDIUM


def test_smart_router_never_raises():
    decision = SmartRouter(provider=FakeProvider([None])).classify("hello")
    assert isinstance(decision, RouteDecision)


# ----------------------------------------------------------------------
# execution engine
# ----------------------------------------------------------------------

def _fake_dispatch_factory(results):
    def dispatch(category, tool, args, context=None):
        if tool in results:
            return results[tool]
        return ToolResult.failure(f"Unknown tool: {tool!r}.")
    return dispatch


def test_execution_engine_sequential_plan():
    engine = ExecutionEngine(dispatch_fn=_fake_dispatch_factory({
        "a": ToolResult.success("A done"), "b": ToolResult.success("B done"),
    }))
    result = engine.run(Plan("p", [
        PlanStep("s1", "calc", "a"), PlanStep("s2", "calc", "b"),
    ]))
    assert result.ok and result.summary.startswith("2/2")
    assert all(s.status == "done" for s in result.steps.values())


def test_execution_engine_parallel_group():
    seen = []

    def dispatch(category, tool, args, context=None):
        seen.append(tool)
        return ToolResult.success(f"{tool} ok")

    engine = ExecutionEngine(dispatch_fn=dispatch)
    result = engine.run(Plan("p", [
        PlanStep("s1", "calc", "a", group="g"),
        PlanStep("s2", "calc", "b", group="g"),
        PlanStep("s3", "calc", "c"),
    ]))
    assert result.ok
    assert set(seen) == {"a", "b", "c"}


def test_execution_engine_stops_on_repeated_failure():
    calls = {"n": 0}

    def dispatch(category, tool, args, context=None):
        calls["n"] += 1
        return ToolResult.failure("boom")

    store = FakeMemoryStore()
    engine = ExecutionEngine(dispatch_fn=dispatch, memory_store=store)
    result = engine.run(Plan("p", [
        PlanStep("s1", "calc", "bad"),
        PlanStep("s2", "calc", "bad"),
        PlanStep("s3", "calc", "bad"),
        PlanStep("s4", "calc", "good"),
    ]))
    assert not result.ok and result.stopped_early
    assert result.steps["s4"].status == "skipped"
    assert calls["n"] == 2  # stopped after the second failure, not four
    assert len(store.lessons) == 2  # reflection hook fired per failure


def test_execution_state_manager_tracks_running():
    manager = ExecutionStateManager()
    manager.start("e1", "plan A")
    manager.start("e2", "plan B")
    assert len(manager.running()) == 2
    manager.finish("e1", "done")
    assert [s.execution_id for s in manager.running()] == ["e2"]
    assert manager.get("e1").status == "done"
    assert manager.finish("nope", "done") is None


# ----------------------------------------------------------------------
# background
# ----------------------------------------------------------------------

def test_scheduler_tick_runs_due_jobs_and_survives_failures():
    scheduler = IntervalScheduler()
    calls = []
    scheduler.schedule("ok", 10, lambda: calls.append("ok"))
    scheduler.schedule("bad", 10, lambda: 1 / 0)
    ran = scheduler.tick_once(now=1000.0)
    assert ran == ["ok", "bad"] and calls == ["ok"]
    assert scheduler.jobs()[1].last_error is not None
    # Not due again until the interval elapses.
    assert scheduler.tick_once(now=1005.0) == []
    assert scheduler.tick_once(now=1010.0) == ["ok", "bad"]
    assert scheduler.unschedule("ok") is True
    assert scheduler.unschedule("ok") is False


def test_scheduler_start_stop_round_trip():
    scheduler = IntervalScheduler(tick_seconds=0.05)
    calls = []
    scheduler.schedule("fast", 0.05, lambda: calls.append(1))
    scheduler.start()
    assert scheduler.running
    import time as _time
    deadline = _time.time() + 2.0
    while _time.time() < deadline and len(calls) < 1:
        _time.sleep(0.05)
    scheduler.stop()
    assert not scheduler.running
    assert calls, "background loop never ticked"


def test_reminders_and_alerts(tmp_path):
    store = CalendarStore(db_path=tmp_path / "cal.db")
    start = NOW + timedelta(hours=30)
    store.add_event("Lunch", start, start + timedelta(hours=1))
    store.add_event("Soon", NOW + timedelta(minutes=10), NOW + timedelta(minutes=40))
    due = check_reminders(store, now=NOW)
    assert [e.content for e in due] == ["Lunch"]
    alerts = due_alerts(store, now=NOW)
    assert [e.content for e in alerts["15min"]] == ["Soon"]
    assert alerts["now"] == [] and alerts["1h"] == []


def test_decay_sweep_counts_visible_vs_faded(tmp_path):
    store = LessonStore(db_path=tmp_path / "lessons.db")
    store.add_lesson(trigger="fresh", fix="do it", confidence=0.9)
    store.add_lesson(trigger="old", fix="do it", confidence=0.9,
                     created_at="2020-01-01T00:00:00+00:00")
    report = sweep(store, now=NOW)
    assert report["total"] == 2 and report["visible"] == 1 and report["faded"] == 1
    watcher = DecayWatcher(store)
    assert watcher.run(now=NOW) == report
    assert watcher.run_count == 1


# ----------------------------------------------------------------------
# terminal chat
# ----------------------------------------------------------------------

class _ScriptedAgent:
    def __init__(self):
        self.seen = []

    def run_turn(self, text):
        self.seen.append(text)
        return f"echo: {text}"


def test_terminal_chat_loop_with_fakes():
    agent = _ScriptedAgent()
    lines = iter(["hello", "", "how are you?", "/quit", "never reached"])
    out = []
    run_chat(agent, input_fn=lambda prompt: next(lines), output_fn=out.append,
             greeting="hi")
    assert out[0] == "hi"
    assert out[1] == "echo: hello"
    assert out[2] == "echo: how are you?"
    assert out[-1] == "Goodbye."
    assert agent.seen == ["hello", "how are you?"]


def test_terminal_chat_eof_ends_cleanly():
    out = []

    def _eof(prompt):
        raise EOFError

    run_chat(_ScriptedAgent(), input_fn=_eof, output_fn=out.append, greeting="")
    assert out == ["Goodbye."]


def test_terminal_chat_agent_failure_ends_honestly():
    class _Broken:
        def run_turn(self, text):
            raise RuntimeError("kaboom")

    out = []
    run_chat(_Broken(), input_fn=lambda prompt: "hi", output_fn=out.append, greeting="")
    assert "kaboom" in out[0]


# ----------------------------------------------------------------------
# security filters
# ----------------------------------------------------------------------

def test_redact_covers_keys_tokens_passwords():
    assert redact("key is sk-abc1234567890xyz") == "key is <redacted>"
    assert redact("Authorization: Bearer abcdefgh12345678") == "Authorization: <redacted>"
    assert redact('password="hunter2" here') == "<redacted> here"
    assert redact("nothing secret here") == "nothing secret here"
    assert redact(None) == ""


def test_redact_mapping_redacts_by_key_name():
    out = redact_mapping({"api_key": "whatever", "city": "Riyadh",
                          "nested": {"password": "x", "n": 1}})
    assert out["api_key"] == "<redacted>"
    assert out["city"] == "Riyadh"
    assert out["nested"]["password"] == "<redacted>"
    assert out["nested"]["n"] == 1


def test_is_sensitive_key_and_sanitize_and_validate():
    assert is_sensitive_key("x_vesper_key")
    assert is_sensitive_key("password")
    assert not is_sensitive_key("city")
    assert sanitize_for_log("a\nb  sk-abc1234567890xyz") == "a b <redacted>"
    assert validate_message("") == "Message is empty."
    assert validate_message("x" * 8001) is not None
    assert validate_message("hello") is None


# ----------------------------------------------------------------------
# fts search helper
# ----------------------------------------------------------------------

def test_fts_query_and_bm25_mapping():
    assert build_fts_query("hello world") == '"hello" OR "world"'
    assert build_fts_query("  ") == ""
    assert bm25_rank_to_score(-3.0) == pytest.approx(0.75)
    assert bm25_rank_to_score(0.0) == pytest.approx(1.0)


def test_keyword_scores_uses_fts_then_like(tmp_path):
    store = LessonStore(db_path=tmp_path / "lessons.db")
    lesson = store.add_lesson(trigger="weather in Riyadh", fix="check forecast", confidence=0.9)
    scores = keyword_scores(store, "Riyadh weather", 10)
    assert lesson.id in scores and scores[lesson.id] > 0


# ----------------------------------------------------------------------
# config / policies shims
# ----------------------------------------------------------------------

def test_config_and_policies_shims():
    import athena.config as config
    import athena.policies as policies

    assert config.APP_DATA_DIR.name == "Athena"
    assert config.WakeWordPolicy is policies.WakeWordPolicy
    assert config.ListeningControl is policies.ListeningControl
    assert config.GREETING_TEXT == policies.GREETING_TEXT == "Welcome, boss."
    assert config.SecureStore is not None
    assert config.CONFIG_PATH.name == "config.json"


def test_no_rapidocr_import_in_perception():
    import inspect

    import athena.tools.perception as perception

    src = inspect.getsource(perception)
    assert "rapidocr" not in src
    assert "pytesseract" in src
