from __future__ import annotations

import subprocess

from app.design.services.common import plantuml as plantuml


def _use_stub_docker_backend(monkeypatch, run):
    monkeypatch.setattr(plantuml.shutil, "which", lambda _name: None)
    monkeypatch.setattr(plantuml, "_find_plantuml_jar", lambda: None)
    monkeypatch.setattr(plantuml.subprocess, "run", run)
    with plantuml._syntax_cache_lock:
        plantuml._syntax_cache.clear()


def test_successful_self_contained_check_is_cached(monkeypatch):
    calls = []
    timing = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout=b"ok", stderr=b"")

    _use_stub_docker_backend(monkeypatch, run)
    monkeypatch.setattr(
        plantuml,
        "log_design_timing",
        lambda event, **values: timing.append((event, values)),
    )

    source = "@startuml\n!theme plain\nAlice -> Bob: hi\n@enduml"
    assert plantuml.check_plantuml_syntax(source) == []
    assert plantuml.check_plantuml_syntax(source) == []

    assert len(calls) == 1
    assert timing[-1][0] == "plantuml.syntax_check.completed"
    assert timing[-1][1]["cache_hit"] is True


def test_invalid_result_is_not_cached(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command, 0, stdout=b"ERROR\n1\nsyntax error", stderr=b""
        )

    _use_stub_docker_backend(monkeypatch, run)
    source = "@startuml\nnot valid\n@enduml"

    assert plantuml.check_plantuml_syntax(source)
    assert plantuml.check_plantuml_syntax(source)
    assert len(calls) == 2


def test_preprocessor_directive_bypasses_cache(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=b"ok", stderr=b"")

    _use_stub_docker_backend(monkeypatch, run)
    source = "@startuml\n  !include common.iuml\n@enduml"

    assert plantuml.check_plantuml_syntax(source) == []
    assert plantuml.check_plantuml_syntax(source) == []
    assert len(calls) == 2


def test_non_plain_theme_bypasses_cache(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=b"ok", stderr=b"")

    _use_stub_docker_backend(monkeypatch, run)
    source = "@startuml\n!theme spacelab\nAlice -> Bob: hi\n@enduml"

    assert plantuml.check_plantuml_syntax(source) == []
    assert plantuml.check_plantuml_syntax(source) == []
    assert len(calls) == 2


def test_backend_identity_is_part_of_cache_key(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=b"ok", stderr=b"")

    _use_stub_docker_backend(monkeypatch, run)
    source = "@startuml\nAlice -> Bob: hi\n@enduml"

    assert plantuml.check_plantuml_syntax(source) == []
    monkeypatch.setattr(plantuml, "PLANTUML_IMAGE", "plantuml/plantuml@sha256:other")
    assert plantuml.check_plantuml_syntax(source) == []

    assert len(calls) == 2
