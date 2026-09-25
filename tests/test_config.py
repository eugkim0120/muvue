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


SPEC_EXAMPLE = Path(__file__).parent / "fixtures" / "v4_spec_config.toml"


def test_v4_spec_example_config_loads_verbatim():
    """The plan's own section 2 example `config.toml` (copied verbatim into
    the fixture) must load -- it used to fail on `[daemon]`,
    `planning.require_auto_criterion_above_tier` and
    `cost_model = "requests"`-style keys the model didn't know."""
    cfg = load_config(SPEC_EXAMPLE)
    assert cfg.daemon.bind == "127.0.0.1"
    assert cfg.daemon.allowed_origins == []
    assert cfg.planning.require_auto_criterion_above_tier == "low"
    assert cfg.agents["claude"].budget.unit == "requests"


def test_requests_cost_model_is_accepted(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[agents.x]\ncommand = "x"\ncost_model = "requests"\n'
                 '[routing]\nspec = "x"\ntask = "x"\nsubtask = "x"\n')
    assert load_config(p).agents["x"].cost_model == "requests"


def test_require_auto_criterion_tier_rejects_unknown_value(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[planning]\nrequire_auto_criterion_above_tier = "extreme"\n')
    with pytest.raises(ConfigError, match="require_auto_criterion_above_tier"):
        load_config(p)
