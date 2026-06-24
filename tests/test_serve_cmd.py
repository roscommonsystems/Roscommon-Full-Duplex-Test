import sys

import serve
from supervisor.registry import ModelRegistry


async def test_download_weight_runs_in_executor(monkeypatch):
    # Regression: _download_weight uses asyncio.get_running_loop(); a missing
    # `import asyncio` in serve.py only surfaces here (the pharma path), not in
    # build_moshi_cmd tests that stub _download_weight out entirely.
    calls = {}

    def fake_hf_hub_download(repo, filename):
        calls["repo"], calls["filename"] = repo, filename
        return "/cache/weights.safetensors"

    fake_hub = type(sys)("huggingface_hub")
    fake_hub.hf_hub_download = fake_hf_hub_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)

    path = await serve._download_weight("some/repo", "weights.safetensors")
    assert path == "/cache/weights.safetensors"
    assert calls == {"repo": "some/repo", "filename": "weights.safetensors"}


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


async def test_build_cmd_uses_server_python_when_set(monkeypatch):
    reg = ModelRegistry([{
        "id": "demegire/personaplex-finetune-pharma",
        "name": "Pharma",
        "base_repo": "nvidia/personaplex-7b-v1",
        "moshi_weight_file": "merged_step448/model.safetensors",
        "server_python": "/venv/pharma/bin/python",
    }])

    async def fake_dl(weight_repo, weight_file):
        return "/cache/model.safetensors"

    monkeypatch.setattr(serve, "_download_weight", fake_dl)
    monkeypatch.setattr(serve, "shared_voices_dir", lambda: "/voices")

    cmd = await serve.build_moshi_cmd(
        "demegire/personaplex-finetune-pharma", 8999, registry=reg
    )
    assert cmd[0] == "/venv/pharma/bin/python"
    assert cmd[1:3] == ["-m", "moshi.server"]
    # forked server needs an indexed CUDA device (torch 2.11 rejects bare "cuda")
    assert cmd[cmd.index("--device") + 1] == "cuda:0"


async def test_build_cmd_defaults_to_sys_executable():
    import sys
    reg = ModelRegistry([{"id": "nvidia/personaplex-7b-v1", "name": "Base"}])
    cmd = await serve.build_moshi_cmd("nvidia/personaplex-7b-v1", 8999, registry=reg)
    assert cmd[0] == sys.executable
    # stock models must NOT get the forked-only --device override
    assert "--device" not in cmd
