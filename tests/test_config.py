"""P0 acceptance #5: invalid config.toml fails with a precise message."""

from pathlib import Path

import pytest

from muvue.core.config import DEFAULT_CONFIG_TOML, ConfigError, load_config


def test_default_config_is_valid(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text(DEFAULT_CONFIG_TOML)
    cfg = load_config(p)
    assert cfg.mode == "light"
    assert cfg.schema_version == 1


def test_missing_file_raises_config_error(tmp_path: Path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_unknown_mode_rejected(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text(DEFAULT_CONFIG_TOML.replace('mode = "light"', 'mode = "yolo"'))
    with pytest.raises(ConfigError) as excinfo:
        load_config(p)
    msg = str(excinfo.value)
    assert "mode" in msg
    assert "yolo" in msg


def test_unknown_top_level_key_rejected(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text(DEFAULT_CONFIG_TOML + "\nbogus_key = true\n")
    with pytest.raises(ConfigError, match="bogus_key"):
        load_config(p)


def test_agent_missing_required_field_rejected(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text(
        DEFAULT_CONFIG_TOML
        + '\n[agents.broken]\nauth_check = "broken --version"\n'
    )
    with pytest.raises(ConfigError) as excinfo:
        load_config(p)
    msg = str(excinfo.value)
    assert "agents" in msg
    assert "broken" in msg


def test_bad_toml_syntax_rejected(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text("this is not [ valid toml")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(p)


def test_invalid_on_rate_limit_rejected(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text(
        DEFAULT_CONFIG_TOML
        + '\n[agents.x]\ncommand = "x"\ncost_model = "usd"\non_rate_limit = "explode"\n'
    )
    with pytest.raises(ConfigError, match="on_rate_limit"):
        load_config(p)


def test_routing_referencing_unknown_agent_rejected(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text(
        DEFAULT_CONFIG_TOML.replace(
            '[routing]\nspec = "fake"\ntask = "fake"\nsubtask = "fake"',
            '[routing]\nspec = "ghost"\ntask = "fake"\nsubtask = "fake"',
        )
    )
    with pytest.raises(ConfigError, match="ghost"):
        load_config(p)
