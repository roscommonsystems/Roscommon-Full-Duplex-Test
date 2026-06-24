import serve
from supervisor.registry import ModelRegistry


async def test_build_cmd_plain_model_loads_from_its_own_repo():
    reg = ModelRegistry([{"id": "nvidia/personaplex-7b-v1", "name": "Base"}])
    cmd = await serve.build_moshi_cmd("nvidia/personaplex-7b-v1", 8999, registry=reg)
    i = cmd.index("--hf-repo")
    assert cmd[i + 1] == "nvidia/personaplex-7b-v1"
    assert "--moshi-weight" not in cmd


async def test_build_cmd_pharma_uses_base_repo_and_downloaded_weight(monkeypatch):
    reg = ModelRegistry([{
        "id": "demegire/personaplex-finetune-pharma",
        "name": "Pharma",
        "base_repo": "nvidia/personaplex-7b-v1",
        "moshi_weight_file": "merged_step448/model.safetensors",
    }])

    async def fake_dl(weight_repo, weight_file):
        assert weight_repo == "demegire/personaplex-finetune-pharma"
        assert weight_file == "merged_step448/model.safetensors"
        return "/cache/model.safetensors"

    monkeypatch.setattr(serve, "_download_weight", fake_dl)
    monkeypatch.setattr(serve, "shared_voices_dir", lambda: "/voices")

    cmd = await serve.build_moshi_cmd(
        "demegire/personaplex-finetune-pharma", 8999, registry=reg
    )
    # base repo provides config/tokenizer/mimi; pharma repo provides only weights
    assert cmd[cmd.index("--hf-repo") + 1] == "nvidia/personaplex-7b-v1"
    assert cmd[cmd.index("--moshi-weight") + 1] == "/cache/model.safetensors"
    assert cmd[cmd.index("--voice-prompt-dir") + 1] == "/voices"
