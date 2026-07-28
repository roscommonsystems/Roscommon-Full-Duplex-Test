import sys

import serve


def test_build_cmd_targets_the_requested_repo_and_port():
    cmd = serve.build_moshi_cmd("nvidia/personaplex-7b-v1", 8999)
    assert cmd[0] == sys.executable
    assert cmd[1:3] == ["-m", "moshi.server"]
    assert cmd[cmd.index("--hf-repo") + 1] == "nvidia/personaplex-7b-v1"
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1"
    assert cmd[cmd.index("--port") + 1] == "8999"
    assert "--cpu-offload" not in cmd


def test_build_cmd_appends_cpu_offload_flag():
    cmd = serve.build_moshi_cmd("nvidia/personaplex-7b-v1", 8999, cpu_offload=True)
    assert "--cpu-offload" in cmd
