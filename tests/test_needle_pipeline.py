"""End-to-end text pipeline with the real Needle 3 engine (no mocked AI).

TEXT -> NEEDLE -> gated TOOL -> RESULT. Side effects are confined to the test
sandbox; power actions are intercepted.
"""

import pytest

from app.config.settings import Settings
from app.core.events import OutcomeKind
from app.core.pipeline import CommandProcessor, parse_yes_no
from app.tools import load_all

pytestmark = pytest.mark.needle


@pytest.fixture()
def processor(engine, services):
    return CommandProcessor(engine, load_all(), Settings(), services.memory)


def run(engine, fn, *args):
    return engine.submit(fn, *args).result()


def test_needle_initialised(engine):
    assert engine.ready.is_set() and engine.info["tools"] >= 21


def test_text_command_executes_safe_tool(engine, processor):
    out = run(engine, processor.handle, "what time is it")
    assert out.kind is OutcomeKind.DONE and out.success
    assert [r.name for r in out.records] == ["get_system_time"]
    assert out.records[0].route.startswith("execute")
    assert out.message.startswith("It's")


@pytest.mark.parametrize("text,tool", [
    ("Write me a poem.", "write_text"), ("write an email to my boss asking for a day off", "write_text"),
    ("create a python script that renames my photos by date", "write_code"),
    ("open vs code and write a snake game in python", "write_code"),
])
def test_creation_requests_route_to_the_writing_tools(engine, services, text, tool):
    # dry run: routed and repaired, nothing is written or opened
    out = run(engine, CommandProcessor(engine, load_all(), Settings(), services.memory, dry_run=True).handle, text)
    acted = [r.name for r in out.records if r.route.startswith("dry-run")]
    assert acted == [tool], [(r.name, r.route) for r in out.records]
    assert out.records[-1].arguments["request"] == text


@pytest.mark.parametrize("text", ["tell me a joke", "what is the meaning of life", "hello"])
def test_unsupported_requests_do_nothing(engine, processor, text):
    out = run(engine, processor.handle, text)
    assert out.kind is OutcomeKind.UNSUPPORTED and out.message == "I can't do that yet."
    assert all(r.result is None for r in out.records)   # nothing executed


def test_shutdown_requires_confirmation(engine, processor, power_calls):
    out = run(engine, processor.handle, "Shutdown the computer.")
    assert out.kind is OutcomeKind.CONFIRM and out.confirmation.destructive
    assert power_calls == []                            # NO SHUTDOWN YET
    out = run(engine, processor.resolve_pending, False)
    assert out.kind is OutcomeKind.CANCELLED and power_calls == []
    run(engine, processor.handle, "Shutdown the computer.")
    out = run(engine, processor.handle, "yes")           # spoken/typed confirmation
    assert out.kind is OutcomeKind.DONE and power_calls == [["/s", "/t", "5"]]


def test_delete_confirm_and_clarify(engine, processor, services, sandbox):
    (sandbox / "a").mkdir()
    (sandbox / "b").mkdir()
    (sandbox / "a" / "plan.txt").write_text("1")
    (sandbox / "b" / "plan.txt").write_text("2")
    services.files.build()
    out = run(engine, processor.handle, "delete plan.txt")
    assert out.kind is OutcomeKind.CLARIFY and "Which file" in out.message
    out = run(engine, processor.handle, "the second one")
    assert out.kind is OutcomeKind.CONFIRM and out.confirmation.title == "Delete file?"
    assert (sandbox / "b").name in out.confirmation.detail or (sandbox / "a").name in out.confirmation.detail
    out = run(engine, processor.resolve_pending, False)
    assert (sandbox / "a" / "plan.txt").exists() and (sandbox / "b" / "plan.txt").exists()


def test_create_file_by_text(engine, processor, services, sandbox):
    out = run(engine, processor.handle, f"create a file called nexus_test.txt in {sandbox}")
    assert out.kind is OutcomeKind.DONE and out.success, out
    assert (sandbox / "nexus_test.txt").exists()


def test_yes_no_parser():
    assert parse_yes_no("Yes.") is True and parse_yes_no("go ahead") is True
    assert parse_yes_no("no") is False and parse_yes_no("cancel") is False
    assert parse_yes_no("open chrome") is None
