import json
from supervisor.registry import ModelRegistry

MODELS = [
    {"id": "nvidia/personaplex-7b-v1", "name": "PersonaPlex (Original)", "description": "base"},
    {"id": "kyutai/personaplex-rl-seamless", "name": "PersonaPlex RL Seamless", "description": "rl"},
    {"id": "no-name/model"},
]


def test_models_returns_list():
    reg = ModelRegistry(MODELS)
    assert reg.models == MODELS


def test_has_known_and_unknown():
    reg = ModelRegistry(MODELS)
    assert reg.has("nvidia/personaplex-7b-v1") is True
    assert reg.has("does/not-exist") is False


def test_display_name_and_fallbacks():
    reg = ModelRegistry(MODELS)
    assert reg.display_name("nvidia/personaplex-7b-v1") == "PersonaPlex (Original)"
    assert reg.display_name("no-name/model") == "no-name/model"   # missing name -> id
    assert reg.display_name("does/not-exist") == "does/not-exist"  # unknown -> id


def test_from_file(tmp_path):
    p = tmp_path / "models.json"
    p.write_text(json.dumps(MODELS), encoding="utf-8")
    reg = ModelRegistry.from_file(str(p))
    assert reg.has("kyutai/personaplex-rl-seamless")
