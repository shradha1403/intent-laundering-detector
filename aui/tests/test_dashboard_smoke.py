"""
Smoke test for scripts/dashboard.py using Streamlit's AppTest, which
runs the script in-process without a browser. This is not a
replacement for actually eyeballing the UI, but it does catch "the
script throws on import/run" and "the banner says the wrong thing"
without anyone having to click through it by hand every time the
fidelity engine changes.
"""
import os

from streamlit.testing.v1 import AppTest

DASHBOARD_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "dashboard.py")


def _run_scenario(scenario_label: str) -> AppTest:
    at = AppTest.from_file(DASHBOARD_PATH, default_timeout=30)
    at.run()
    assert not at.exception, [str(e) for e in at.exception]

    at.selectbox[0].select(scenario_label).run()
    at.button[0].click().run()  # "Run scenario" is the first button
    assert not at.exception, [str(e) for e in at.exception]
    return at


def test_dashboard_loads_without_error():
    at = AppTest.from_file(DASHBOARD_PATH, default_timeout=30)
    at.run()
    assert not at.exception, [str(e) for e in at.exception]
    assert "Authority-Use Integrity" in at.title[0].value


def test_normal_scenario_shows_success_banner():
    at = _run_scenario("Normal delegation")
    assert any("No laundering detected" in s.value for s in at.success)
    assert not list(at.error)


def test_action_mismatch_scenario_shows_error_banner():
    at = _run_scenario("Action-mismatch laundering")
    assert any("INTENT LAUNDERING DETECTED" in e.value for e in at.error)


def test_cumulative_drift_scenario_shows_error_banner():
    at = _run_scenario("Cumulative-drift laundering")
    assert any("INTENT LAUNDERING DETECTED" in e.value for e in at.error)


def test_reset_clears_the_report():
    at = _run_scenario("Normal delegation")
    assert list(at.success) or list(at.error)

    at.button[1].click().run()  # "Reset ledger" is the second button
    assert not at.exception, [str(e) for e in at.exception]
    assert any("Pick a scenario" in i.value for i in at.info)
