"""protocol_version 4: mission control phase 1 -- the UserPromptSubmit and
SessionEnd hook events and session fields on hook queue lines, the MCP
session tools, and GET /sessions, GET /worktrees, POST /sessions/{id}/stop
(3 was the project canvas backend; 2 the W4-W11 verbs and flags, v4
section 4, "versioned via protocol_version"). A repo whose config.toml
says an older version gets a doctor warning, and so do its adapters."""

from __future__ import annotations

import tomllib

from muvue.core import config as config_mod, doctor
from muvue.core.repo_init import init_repo


def test_new_repos_speak_the_current_protocol(tmp_path):
    assert config_mod.PROTOCOL_VERSION == 4
    assert tomllib.loads(config_mod.DEFAULT_CONFIG_TOML)["protocol_version"] == 4
    assert config_mod.MuvueConfig().protocol_version == 4
    init_repo(tmp_path)
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert not [w for w in report.warnings if "protocol_version" in w]


def test_doctor_warns_on_a_config_from_an_older_protocol(tmp_path):
    init_repo(tmp_path)
    path = tmp_path / ".muvue" / "config.toml"
    path.write_text(path.read_text().replace("protocol_version = 4", "protocol_version = 3"))
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert any("protocol_version=3" in w and "protocol_version=4" in w for w in report.warnings)
