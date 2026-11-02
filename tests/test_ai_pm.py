import datetime as dt
import io
import json
import shutil

import pytest

from aiworkforce import ai_pm
from conftest import ROOT

TODAY = dt.date(2026, 11, 2)


@pytest.fixture
def pm_root(tmp_path):
    (tmp_path / "project").mkdir()
    (tmp_path / "reports").mkdir()
    for f in ("ROADMAP.yml", "RISKS.yml"):
        shutil.copy(ROOT / "project" / f, tmp_path / "project" / f)
    summary = {
        "deployed_strategy": "cost_aware",
        "strategy_comparison": [
            {"strategy": "round_robin", "net_value": 100.0, "first_pass_quality": 0.7, "incidents": 50},
            {"strategy": "cost_aware", "net_value": 900.0, "first_pass_quality": 0.85, "incidents": 30},
        ],
        "overall": {"net_value": 900.0, "cost_per_task": 12.5, "sla_attainment": 0.98,
                    "reviewer_utilization": 0.8},
        "performance_reviews": [{"name": "Ledger", "action": "retrain / prompt-tune"},
                                {"name": "Atlas", "action": "keep"}],
        "recommendation_counts": {"keep": 1, "retrain / prompt-tune": 1},
        "capacity_plan": {"hours_needed": 200.0, "hours_available": 180.0,
                          "planned_utilization": 1.11, "hours_needed_after_recommendations": 190.0},
        "audit": {"by_type": {"audit_skipped": 4}},
    }
    (tmp_path / "reports" / "summary.json").write_text(json.dumps(summary))
    return tmp_path


def test_risk_register_sorted_with_heat():
    reg = ai_pm.risk_register([
        {"id": "R1", "probability": 2, "impact": 2}, {"id": "R2", "probability": 5, "impact": 4},
        {"id": "R3", "probability": 3, "impact": 5}])
    assert [r["id"] for r in reg] == ["R2", "R3", "R1"]
    assert [r["heat"] for r in reg] == ["Critical", "High", "Low"]


def test_health_rules():
    ms = [{"id": "M1", "overdue": False}]
    low = [{"id": "R1", "score": 9}]
    assert ai_pm.health(ms, [], low)[0] == "GREEN"
    assert ai_pm.health(ms, [{"id": "B1", "priority": "P0", "status": "todo"}], low)[0] == "AMBER"
    assert ai_pm.health(ms, [], [{"id": "R1", "score": 15}])[0] == "AMBER"
    assert ai_pm.health([{"id": "M1", "overdue": True}], [], low)[0] == "RED"
    p0s = [{"id": f"B{i}", "priority": "P0", "status": "doing"} for i in range(2)]
    rag, reason = ai_pm.health(ms, p0s, low)
    assert rag == "RED" and "2 open P0" in reason


def test_milestones_and_next_actions():
    backlog = [
        {"id": "B1", "priority": "P1", "status": "done", "milestone": "M1"},
        {"id": "B2", "priority": "P2", "status": "todo", "depends_on": ["B1"], "milestone": "M1"},
        {"id": "B3", "priority": "P0", "status": "todo", "depends_on": ["B2"]},  # blocked
        {"id": "B4", "priority": "P2", "status": "todo", "depends_on": []},
        {"id": "B5", "priority": "P1", "status": "todo"},
        {"id": "B6", "priority": "P3", "status": "doing"},
    ]
    nxt = [b["id"] for b in ai_pm.next_actions(backlog)]
    assert nxt == ["B5", "B2", "B4"]  # priority first, then more dependencies satisfied
    ms = ai_pm.milestone_table([{"id": "M1", "name": "x", "due": "2026-10-01", "status": "in_progress"}],
                               backlog, TODAY)
    assert ms[0]["pct_done"] == 50 and ms[0]["overdue"] is True


def test_pm_output_is_deterministic_and_uses_data(pm_root, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out1 = ai_pm.run_pm(pm_root, today=TODAY, verbose=False)
    md1 = (pm_root / "project" / "STATUS.md").read_text()
    out2 = ai_pm.run_pm(pm_root, today=TODAY, verbose=False)
    assert md1 == (pm_root / "project" / "STATUS.md").read_text()
    assert out1 == out2 and out1["summary_source"] == "template"
    assert out1["health"]["rag"] == "AMBER"  # committed RISKS.yml has a score-15 risk
    assert [a["id"] for a in out1["next_actions"]] == ["B10", "B13", "B12"]
    assert 3 <= len(out1["data_says"]) <= 5
    assert "$900" in out1["data_says"][0] and "audit samples were skipped" in out1["data_says"][-1]
    assert "**Date:** 2026-11-02" in md1 and "## Next 3 actions" in md1
    saved = json.loads((pm_root / "project" / "status.json").read_text())
    assert saved["health"] == out1["health"]


def test_llm_failure_falls_back_without_network(pm_root, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def boom(*a, **k):
        raise OSError("network disabled in tests")

    monkeypatch.setattr(ai_pm.urllib.request, "urlopen", boom)
    out = ai_pm.run_pm(pm_root, today=TODAY, verbose=False)
    assert out["summary_source"] == "template"


def test_llm_summary_is_used_when_api_answers(pm_root, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("AI_PM_MODEL", "claude-test")
    seen = {}

    class FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        seen["body"] = json.loads(req.data)
        return FakeResp(json.dumps({"content": [{"type": "text", "text": "Grounded summary."}]}).encode())

    monkeypatch.setattr(ai_pm.urllib.request, "urlopen", fake_urlopen)
    out = ai_pm.run_pm(pm_root, today=TODAY, verbose=False)
    assert out["executive_summary"] == "Grounded summary."
    assert out["summary_source"] == "llm (claude-test)"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["headers"]["x-api-key"] == "test-key"
    assert seen["body"]["model"] == "claude-test"
