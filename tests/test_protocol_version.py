"""protocol_version 2: the verbs and flags W4-W11 changed (v4 section 4,
"versioned via protocol_version"). A repo whose config.toml says an
older version gets a doctor warning, and so do its adapters."""

from __future__ import annotations

import tomllib

from muvue.core import config as config_mod, doctor
from muvue.core.repo_init import init_repo


def test_new_repos_speak_the_current_protocol(tmp_path):
    assert config_mod.PROTOCOL_VERSION == 2
    assert tomllib.loads(config_mod.DEFAULT_CONFIG_TOML)["protocol_version"] == 2
    assert config_mod.MuvueConfig().protocol_version == 2
    init_repo(tmp_path)
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert not [w for w in report.warnings if "protocol_version" in w]


def test_doctor_warns_on_a_config_from_an_older_protocol(tmp_path):
    init_repo(tmp_path)
    path = tmp_path / ".muvue" / "config.toml"
    path.write_text(path.read_text().replace("protocol_version = 2", "protocol_version = 1"))
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert any("protocol_version=1" in w and "protocol_version=2" in w for w in report.warnings)
