import pandas as pd
import pytest

from aiworkforce.kpis import agent_kpis, kpi_block, strategy_table
from aiworkforce.policy import STRATEGIES
from aiworkforce.simulation import run_all_strategies, simulate


@pytest.fixture(scope="module")
def results(tasks, roster, workload):
    return run_all_strategies(tasks, roster, workload.catalog(), STRATEGIES)


def test_simulation_is_deterministic(tasks, roster, workload):
    a = simulate(tasks, roster, workload.catalog(), "cost_aware")
    b = simulate(tasks, roster, workload.catalog(), "cost_aware")
    pd.testing.assert_frame_equal(a.outcomes, b.outcomes)
    assert a.audit.events == b.audit.events


def test_outcome_invariants(results, roster, workload):
    cat = workload.catalog()
    for res in results.values():
        o = res.outcomes
        assert (o["status"] != "unfinished").all()
        # every task routed to an agent that holds all required skills
        for t, agent in zip(o["type"], o["agent"]):
            assert roster.agent(agent).can_do(cat[t].required_skills)
        # first pass means the agent's own output was correct and not escalated
        assert not (o["first_pass"] & ~o["agent_correct"]).any()
        assert not (o["first_pass"] & o["escalated"]).any()
        # a correct agent output never becomes an incident unless it was escalated to a human
        assert not ((o["status"] == "incident") & o["agent_correct"] & ~o["escalated"]).any()
        # HITL: work held for approval was reviewed; caught errors were fixed, not shipped
        held = o[o["approval_required"]]
        assert held["reviewed"].all()
        assert (held.loc[held["error_caught"], "status"] == "correct").all()
        # agents act alone only when their autonomy covers the task's risk
        auto = o[~o["approval_required"] & ~o["escalated"]]
        for agent, risk in zip(auto["agent"], auto["risk"]):
            assert not roster.policy.requires_approval(roster.agent(agent).autonomy, risk)
        # audit log has one routing decision per task
        assert res.audit.counts()["route"] == len(o)


def test_strategies_differ_in_expected_direction(results, roster, workload):
    table = strategy_table(results, roster, workload.weeks).set_index("strategy")
    # skill-aware routing beats blind rotation on quality and incidents
    assert table.loc["skill_based", "first_pass_quality"] > table.loc["round_robin", "first_pass_quality"]
    assert table.loc["cost_aware", "incidents"] < table.loc["round_robin", "incidents"]
    assert table.loc["cost_aware", "net_value"] > table.loc["round_robin", "net_value"]


def test_kpi_definitions_on_hand_made_rows(roster):
    rows = pd.DataFrame([
        # status, first_pass, escalated, approval, human_min, model, sla, value, incident_cost
        dict(status="correct", first_pass=True, escalated=False, approval_required=True,
             human_minutes=6.0, model_cost=1.0, sla_met=True, value_delivered=40.0, incident_cost=0.0),
        dict(status="incident", first_pass=False, escalated=False, approval_required=False,
             human_minutes=0.0, model_cost=1.0, sla_met=True, value_delivered=0.0, incident_cost=150.0),
        dict(status="correct", first_pass=False, escalated=True, approval_required=False,
             human_minutes=54.0, model_cost=1.0, sla_met=False, value_delivered=10.0, incident_cost=0.0),
        dict(status="correct", first_pass=True, escalated=False, approval_required=False,
             human_minutes=0.0, model_cost=1.0, sla_met=True, value_delivered=20.0, incident_cost=0.0),
    ])
    k = kpi_block(rows, hourly_cost=60.0, n_weeks=2)
    assert k["throughput_per_week"] == 2.0
    assert k["first_pass_quality"] == 0.5
    assert k["escalation_rate"] == 0.25
    assert k["human_review_hours"] == 1.0
    assert k["cost_per_task"] == pytest.approx((4.0 + 60.0) / 4)
    assert k["sla_attainment"] == 0.75
    assert k["incident_rate"] == 0.25
    assert k["net_value"] == pytest.approx(70.0 - 4.0 - 60.0 - 150.0)


def test_agent_kpis_cover_every_agent(results, roster, workload):
    ak = agent_kpis(results["cost_aware"].outcomes, roster, workload.weeks, workload.n_days)
    assert list(ak["agent"]) == [a.id for a in roster.agents]
    assert ak["tasks"].sum() == len(results["cost_aware"].outcomes)
