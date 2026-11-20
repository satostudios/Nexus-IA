"""Tool registration, schemas, safety classes, trigger surface, validation."""

from app.tools import load_all
from app.tools.registry import Safety, ToolRegistry, nexus_tool, ok

EXPECTED = {
    "open_application", "close_application", "open_url", "open_file", "open_folder", "search_files",
    "create_file", "create_folder", "rename_file", "copy_file", "move_file", "delete_file", "take_screenshot",
    "get_system_time", "get_system_information", "lock_windows", "shutdown_windows", "restart_windows",
    "remember_location", "remember_app_alias", "forget_location",
    "search_web", "type_text", "press_keys", "media_control",
}


def test_all_spec_tools_registered():
    registry = load_all()
    assert EXPECTED <= set(registry.names())


def test_safety_classes():
    r = load_all()
    for name in ("delete_file", "move_file", "copy_file", "rename_file", "shutdown_windows", "restart_windows"):
        assert r.get(name).safety is Safety.CONFIRMATION_REQUIRED, name
    for name in ("get_system_time", "get_system_information", "open_application", "open_folder", "take_screenshot"):
        assert r.get(name).safety is Safety.SAFE, name
    assert r.get("delete_file").destructive and r.get("shutdown_windows").destructive


def test_schemas_come_from_needle_decorator():
    spec = load_all().get("search_files")
    params = spec.schema["parameters"]
    assert spec.schema["description"].startswith("Search for or find files")
    assert params["required"] == ["query"]
    assert set(params["properties"]) == {"query", "extension", "folder"}
    assert spec.schema["triggers"]  # triggers ride along to the engine


def test_trigger_surface():
    r = load_all()
    assert r.matching("Open Notepad.") == {"open_application"}
    assert r.matching("close chrome") == {"close_application"}
    assert r.matching("Shutdown the computer.") == {"shutdown_windows"}
    assert r.matching("restart the computer") == {"restart_windows"}
    assert r.matching("Take a screenshot.") == {"take_screenshot"}
    assert "delete_file" in r.matching("delete old.txt")
    assert {"open_file", "open_folder"} <= r.matching("Open my programming project and then open README.md.")
    assert r.matching("search for carrot cake recipes") == {"search_web"}
    assert r.matching("type hello world") == {"type_text"}
    assert "media_control" in r.matching("turn the volume up")
    for off_topic in ("tell me a joke", "hello", "don't open chrome", "restart chrome", "don't delete that",
                      "what is the meaning of life"):
        assert r.matching(off_topic) == set(), off_topic


def test_argument_validation():
    r = load_all()
    assert r.validate_arguments("open_application", {"application": "Chrome"}) is None
    assert r.validate_arguments("open_application", {}) == "missing application"
    assert r.validate_arguments("open_application", {"application": "  "}) == "missing application"
    assert r.validate_arguments("open_application", {"application": 3}) == "application must be text"
    assert r.validate_arguments("open_application", {"application": "x", "evil": 1}) == "unexpected argument evil"
    assert r.validate_arguments("nope", {}) == "unknown tool nope"


def test_adding_a_tool_is_one_decorator():
    registry = ToolRegistry()

    @nexus_tool(triggers=[r"\bping\b"], registry=registry)
    def ping_tool():
        """Answer a ping."""
        return ok("Pong.")

    spec = registry.get("ping_tool")
    assert spec is not None and spec.safety is Safety.SAFE
    assert registry.matching("ping") == {"ping_tool"}
    assert registry.execute("ping_tool", {}) == {"success": True, "message": "Pong.", "data": {}}


def test_execute_normalises_crashes():
    registry = ToolRegistry()

    @nexus_tool(registry=registry)
    def broken():
        """Always fails."""
        raise RuntimeError("boom")

    result = registry.execute("broken", {})
    assert result["success"] is False and result["error"] == "TOOL_FAILURE"
    assert "boom" not in result["message"]  # no internals shown to the user
