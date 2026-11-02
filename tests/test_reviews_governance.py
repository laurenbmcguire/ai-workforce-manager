import pandas as pd
import pytest

from aiworkforce.governance import capacity_plan, raci_markdown, raci_matrix
from aiworkforce.reviews import review_agent
from aiworkforce.simulation import simulate
from aiworkforce.workload import TaskType

CATALOG = {
    "triage": TaskType("triage", 0.5, ("triage",), "medium", 8, 20, 0.4, 5, 15),
    "doc": TaskType("doc", 0.5, ("writing",), "low", 24, 40, 0.5, 8, 30),
}


def kpi(**kw):
    base = dict(tasks=200, utilization=0.5, first_pass_quality=0.92, escalation_rate=0.02,
                incident_rate=0.01, incident_cost=0.0, net_value=1000.0, sla_attainment=0.99)
    base.update(kw)
    return base


def outcomes_for(agent_id, n=60, risk="medium", fp=True, prof=0.9, diff=0.4, mins=5.0):
    return pd.DataFrame({
        "agent": [agent_id] * n, "type": ["triage"] * n, "risk": [risk] * n,
        "first_pass": [fp] * n, "escalated": [False] * n, "proficiency": [prof] * n,
        "difficulty": [diff] * n, "human_minutes": [mins] * n,
    })


def test_review_rules_fire_in_order(tiny_roster):
    a, b, c = (tiny_roster.agent(x) for x in "abc")
    o = outcomes_for("a")
    # retire: idle and redundant (every type it can take has another eligible agent)
    assert review_agent(a, kpi(utilization=0.01, tasks=2), o, tiny_roster, CATALOG, 8)["action"] == "retire"
    # retrain: poor quality, even if idle-ness would not apply
    r = review_agent(a, kpi(first_pass_quality=0.6), o, tiny_roster, CATALOG, 8)
    assert r["action"] == "retrain / prompt-tune"
    assert any("first-pass quality 60.0%" in e for e in r["evidence"])
    # downgrade: large-tier agent on easy work loses < 3 pts on the small tier
    easy = outcomes_for("b", prof=0.95, diff=0.2)
    r = review_agent(b, kpi(), easy, tiny_roster, CATALOG, 8)
    assert r["action"] == "downgrade model tier"
    # increase autonomy: L2 agent strong on medium-risk work that L3 would free from review
    hard = outcomes_for("b", prof=0.7, diff=0.6)  # too hard to downgrade safely
    r = review_agent(b, kpi(), hard, tiny_roster, CATALOG, 8)
    assert r["action"] == "increase autonomy" and r["freed_hours_per_week"] == pytest.approx(60 * 5 / 60 / 8, abs=0.01)
    # keep: L4 agent, small tier, healthy numbers
    assert review_agent(c, kpi(), outcomes_for("c"), tiny_roster, CATALOG, 8)["action"] == "keep"


def test_raci_has_one_accountable_and_a_responsible(tiny_roster):
    tasks = pd.DataFrame({
        "task_id": [f"T{i}" for i in range(40)], "day": [i // 10 for i in range(40)],
        "arrival_hour": [1.0] * 40, "type": ["triage", "doc"] * 20,
        "risk": ["medium", "low"] * 20, "value": [30.0] * 40, "difficulty": [0.4] * 40,
        "sla_hours": [8.0, 24.0] * 20,
    })
    res = simulate(tasks, tiny_roster, CATALOG, "skill_based")
    raci = raci_matrix(res.outcomes, tiny_roster, CATALOG)
    humans = [s.name for s in tiny_roster.supervisors]
    agents = [a.name for a in tiny_roster.agents]
    for _, row in raci.iterrows():
        assert sum(row[h] == "A" for h in humans) == 1
        assert sum(row[a] == "R" for a in agents) >= 1
    md = raci_markdown(raci)
    assert md.startswith("| task_type | risk |") and "R = Responsible" in md


def test_raci_regulated_work_is_owned_by_compliance(tiny_roster):
    cat = {"audit": TaskType("audit", 1.0, ("writing",), "regulated", 48, 100, 0.5, 10, 30)}
    tasks = pd.DataFrame({"task_id": ["T0", "T1"], "day": [0, 0], "arrival_hour": [1.0, 2.0],
                          "type": ["audit"] * 2, "risk": ["regulated"] * 2, "value": [100.0] * 2,
                          "difficulty": [0.5] * 2, "sla_hours": [48.0] * 2})
    res = simulate(tasks, tiny_roster, cat, "skill_based")
    row = raci_matrix(res.outcomes, tiny_roster, cat).iloc[0]
    assert row["Cora"] == "A"  # "Compliance Lead" title
    assert row["Ann"] == "C"   # manager of the responsible agent is consulted


def test_capacity_plan_math(tiny_roster):
    # flat 10 tasks/day for 20 days, 6 human minutes per task
    tasks = pd.DataFrame({"day": [d for d in range(20) for _ in range(10)]})
    outcomes = pd.DataFrame({"human_minutes": [6.0] * 200, "reviewer": ["ann"] * 150 + ["cora"] * 50})
    cap = capacity_plan(tasks, outcomes, tiny_roster, horizon_days=20, buffer=0.10)
    assert cap["forecast_tasks"] == 200
    assert cap["hours_needed"] == pytest.approx(200 * 6 / 60 * 1.10)
    assert cap["hours_available"] == pytest.approx(3 * 20)
    per = {p["supervisor"]: p["needed_hours"] for p in cap["per_supervisor"]}
    assert per["Ann"] == pytest.approx(cap["hours_needed"] * 0.75, abs=0.1)
    cap2 = capacity_plan(tasks, outcomes, tiny_roster, horizon_days=20, buffer=0.10,
                         freed_hours_per_week=2.0)
    assert cap2["hours_needed_after_recommendations"] == pytest.approx((20 - 8) * 1.10)
