"""Application resolver, file tools, memory and screenshots against real Windows."""

from pathlib import Path

import pytest

from app.tools import load_all, targets


# ----------------------------------------------------------- app resolver
def test_resolver_finds_windows_apps(services):
    apps = services.apps
    apps.refresh()
    for name in ("notepad", "Notepad", "calculator", "calc", "file explorer"):
        assert apps.resolve(name).entry is not None, name
    assert apps.resolve("definitely not an installed app 123").entry is None


def test_resolver_aliases_resolve_to_the_same_application(services):
    apps = services.apps
    apps.refresh()
    chrome = [apps.resolve(n) for n in ("chrome", "google chrome", "Google Chrome")]
    if chrome[0].entry is None:
        assert all(r.known_but_missing == "Google Chrome" for r in chrome)
    else:
        assert len({r.entry.target for r in chrome}) == 1
    code = [apps.resolve(n) for n in ("vscode", "visual studio code", "code")]
    if code[0].entry is None:  # not installed on this machine: still recognised as the same app
        assert {r.known_but_missing for r in code} == {"Visual Studio Code"}
    else:
        assert len({r.entry.target for r in code}) == 1


def test_user_alias(services):
    services.memory.remember_alias("my editor", "Notepad")
    res = services.apps.resolve("my editor")
    assert res.entry is not None and "notepad" in res.entry.target.lower()


# ------------------------------------------------------------ file tools
def _tree(root: Path) -> None:
    (root / "Docs").mkdir()
    (root / "Docs" / "invoice_march.pdf").write_text("x")
    (root / "Docs" / "notes.txt").write_text("x")
    (root / "Music").mkdir()
    (root / "Music" / "song.mp3").write_text("x")
    (root / "report.docx").write_text("x")


def test_search_filename_partial_extension_folder(services, sandbox):
    _tree(sandbox)
    services.files.build()
    r = load_all()
    res = r.execute("search_files", {"query": "invoice"})
    assert res["success"] and res["data"]["results"][0]["name"] == "invoice_march.pdf"
    res = r.execute("search_files", {"query": "pdf"})                    # extension given as query
    assert [x["name"] for x in res["data"]["results"]] == ["invoice_march.pdf"]
    res = r.execute("search_files", {"query": "song", "extension": "mp3", "folder": str(sandbox / "Music")})
    assert res["success"] and res["data"]["results"][0]["name"] == "song.mp3"
    res = r.execute("search_files", {"query": "nothing-like-this"})
    assert not res["success"] and res["error"] == "NO_RESULTS"


def test_create_rename_copy_move_delete(services, sandbox):
    _tree(sandbox)
    services.files.build()
    r = load_all()
    res = r.execute("create_file", {"name": "todo.txt", "folder": str(sandbox)})
    assert res["success"] and (sandbox / "todo.txt").exists()
    assert r.execute("create_file", {"name": "todo.txt", "folder": str(sandbox)})["error"] == "FILE_EXISTS"
    assert r.execute("create_folder", {"name": "Archive", "folder": str(sandbox)})["success"]

    pf = r.run_preflight("rename_file", {"file": str(sandbox / "todo.txt"), "new_name": "done"})
    assert pf.error is None and pf.title == "Rename file?"
    assert r.execute("rename_file", pf.arguments)["success"] and (sandbox / "done.txt").exists()

    pf = r.run_preflight("copy_file", {"file": str(sandbox / "done.txt"), "destination": str(sandbox / "Archive")})
    assert r.execute("copy_file", pf.arguments)["success"] and (sandbox / "Archive" / "done.txt").exists()

    pf = r.run_preflight("move_file", {"file": str(sandbox / "report.docx"), "destination": str(sandbox / "Archive")})
    assert r.execute("move_file", pf.arguments)["success"] and (sandbox / "Archive" / "report.docx").exists()

    pf = r.run_preflight("delete_file", {"file": str(sandbox / "done.txt")})
    assert pf.title == "Delete file?" and "done.txt" in pf.detail
    assert r.execute("delete_file", pf.arguments)["success"] and not (sandbox / "done.txt").exists()


def test_this_folder_refers_to_the_last_folder(services, sandbox):
    _tree(sandbox)
    services.session.touch(sandbox / "Docs")
    pf = load_all().run_preflight("delete_file", {"file": "this folder"})
    assert pf.title == "Delete folder?" and "Docs" in pf.detail


def test_protected_locations_are_blocked(services):
    from app import paths
    from app.tools.registry import Safety

    for p in (Path("C:/"), Path("C:/Windows"), Path.home(), paths.user_folder("desktop")):
        if p is not None:
            assert targets.protected(p), p
    pf = load_all().run_preflight("delete_file", {"file": "C:\\Windows"})
    assert pf.safety is Safety.BLOCKED


def test_ambiguous_names_ask_which(services, sandbox):
    (sandbox / "a").mkdir()
    (sandbox / "b").mkdir()
    (sandbox / "a" / "plan.txt").write_text("1")
    (sandbox / "b" / "plan.txt").write_text("2")
    services.files.build()
    pf = load_all().run_preflight("delete_file", {"file": "plan.txt"})
    assert pf.error is not None and pf.error["error"] == "AMBIGUOUS"
    assert pf.error["message"].startswith("Which file do you mean")
    assert len(pf.error["data"]["candidates"]) == 2


# ---------------------------------------------------------------- memory
def test_remember_and_resolve_a_place(services, sandbox):
    project = sandbox / "Nexus"
    project.mkdir()
    services.session.request = f"My coding project is {project}"
    r = load_all()
    # Needle sometimes copies the path into both arguments; the tool repairs it.
    res = r.execute("remember_location", {"name": str(project), "path": str(project)})
    assert res["success"] and services.memory.locations() == {"coding project": str(project)}
    found = targets.resolve_path("my coding project", want_dir=True)
    assert found.path == project
    assert r.execute("forget_location", {"name": "coding project"})["success"]
    assert services.memory.locations() == {}


# ------------------------------------------------------------ screenshot
@pytest.mark.hardware
def test_screenshot(sandbox):
    from app.tools.screenshots import capture

    path = capture(sandbox)
    assert path.exists() and path.suffix == ".png" and path.stat().st_size > 10_000


def test_system_time_and_information():
    r = load_all()
    t = r.execute("get_system_time", {})
    assert t["success"] and t["message"].startswith("It's")
    info = r.execute("get_system_information", {})
    assert info["success"] and "Windows" in info["message"] and info["data"]["memory_gb"] > 0


def test_power_tools_run_shutdown_exe(power_calls):
    r = load_all()
    assert r.execute("shutdown_windows", {})["success"]
    assert r.execute("restart_windows", {})["success"]
    assert r.execute("lock_windows", {})["success"]
    assert power_calls == [["/s", "/t", "5"], ["/r", "/t", "5"], ["lock"]]
