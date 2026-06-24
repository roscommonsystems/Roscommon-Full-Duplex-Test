import json
import pytest
from supervisor.scenarios import ScenarioStore


def test_from_file_loads_scenarios(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps([
        {"id": "a", "name": "A", "description": "d",
         "injections": [{"at_seconds": 1.0, "text": "hi"}]}
    ]), encoding="utf-8")
    store = ScenarioStore.from_file(str(p))
    assert len(store.scenarios) == 1
    assert store.scenarios[0]["id"] == "a"


def test_rejects_injection_without_text(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps([
        {"id": "a", "name": "A", "description": "d",
         "injections": [{"at_seconds": 1.0, "text": ""}]}
    ]), encoding="utf-8")
    with pytest.raises(ValueError):
        ScenarioStore.from_file(str(p))


def test_rejects_non_numeric_at_seconds(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps([
        {"id": "a", "name": "A", "description": "d",
         "injections": [{"at_seconds": "soon", "text": "hi"}]}
    ]), encoding="utf-8")
    with pytest.raises(ValueError):
        ScenarioStore.from_file(str(p))


def test_repo_scenarios_json_is_valid():
    # The shipped scenarios.json must load and validate.
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    store = ScenarioStore.from_file(os.path.join(root, "scenarios.json"))
    assert len(store.scenarios) >= 1
