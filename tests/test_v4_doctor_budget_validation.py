"""v4 section 2: "`doctor` errors if `budget.unit` is not producible by
that driver's `cost_model`." Mirrors decision #72's existing
cost_model -> agent_spend-unit mapping (core.runner.EXPECTED_BUDGET_UNIT):
usd -> usd, tokens -> tokens, quota -> requests."""

from __future__ import annotations

from pathlib import Path

from muvue.core import doctor
from muvue.core.repo_init import init_repo


def _write_agent_budget(repo_root: Path, cost_model: str, unit: str) -> None:
    config_path = repo_root / ".muvue" / "config.toml"
    text = config_path.read_text()
    text = text.replace(
        "[agents.fake]\ncommand = \"muvue-fake-agent\"\ncost_model = \"tokens\"",
        f'[agents.fake]\ncommand = "muvue-fake-agent"\ncost_model = "{cost_model}"\n\n'
        f'  [agents.fake.budget]\n  unit = "{unit}"\n  limit = 10\n',
    )
    config_path.write_text(text)


def test_doctor_errors_on_mismatched_budget_unit(tmp_path: Path):
    init_repo(tmp_path)
    _write_agent_budget(tmp_path, cost_model="usd", unit="tokens")
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert report.ok is False
    assert any("budget.unit" in issue for issue in report.issues)


def test_doctor_ok_on_matching_budget_unit(tmp_path: Path):
    init_repo(tmp_path)
    _write_agent_budget(tmp_path, cost_model="tokens", unit="tokens")
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert report.ok is True


def test_doctor_errors_on_quota_driver_with_usd_budget(tmp_path: Path):
    init_repo(tmp_path)
    _write_agent_budget(tmp_path, cost_model="quota", unit="usd")
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert report.ok is False


def test_doctor_ok_on_quota_driver_with_requests_budget(tmp_path: Path):
    init_repo(tmp_path)
    _write_agent_budget(tmp_path, cost_model="quota", unit="requests")
    report = doctor.run_doctor(tmp_path, skip_security_probes=True)
    assert report.ok is True
