from dataclasses import replace

import pytest

from aiworkforce.policy import (
    AuditLog,
    CostAwareRouter,
    RoundRobinRouter,
    SkillBasedRouter,
    TaskView,
    should_escalate,
    success_probability,
)


def view(risk="low", value=50.0, skills=("triage", "writing"), difficulty=0.5):
    return TaskView("T1", "kb", tuple(skills), risk, value, review_minutes=6.0, difficulty=difficulty)


def test_success_probability_shape():
    assert success_probability(0.5, 0.5) == pytest.approx(0.5)
    # more skill -> more success; harder task -> less success; bigger model -> more success
    assert success_probability(0.9, 0.5) > success_probability(0.7, 0.5)
    assert success_probability(0.7, 0.7) < success_probability(0.7, 0.4)
    assert success_probability(0.7, 0.5, 0.05) > success_probability(0.7, 0.5, 0.0)
    assert 0.0 < success_probability(0.0, 0.95) < success_probability(1.0, 0.05) < 1.0


def test_skill_based_picks_most_proficient(tiny_roster):
    r = SkillBasedRouter(tiny_roster)
    agent, score = r.choose(view(skills=("writing",)), tiny_roster.agents, {})
    assert agent.id == "b" and score == pytest.approx(0.9)
    # tie on proficiency is broken by the lighter load
    c = tiny_roster.agent("c")
    twin = replace(c, id="d", name="D")
    agent, _ = r.choose(view(), [c, twin], {"c": 5, "d": 1})
    assert agent.id == "d"


def test_round_robin_cycles_through_eligible_agents(tiny_roster):
    r = RoundRobinRouter(tiny_roster)
    picks = [r.choose(view(), tiny_roster.agents, {})[0].id for _ in range(6)]
    assert picks == ["a", "b", "c", "a", "b", "c"]
    # skips agents without capacity but keeps rotating
    assert r.choose(view(), [tiny_roster.agent("c")], {})[0].id == "c"


def test_cost_aware_score_formula(tiny_roster):
    pol = tiny_roster.policy
    r = CostAwareRouter(tiny_roster)
    t = view(risk="low", value=50.0, skills=("writing",), difficulty=0.5)
    b = tiny_roster.agent("b")  # L2 -> needs approval; large tier
    p_hat = success_probability(0.9, 0.5, 0.05)
    expected = (0.9 * 50.0 - 5.0 - 6.0 / 60 * pol.human_hourly_cost
                - (1 - p_hat) * (1 - pol.review_catch_rate) * pol.incident_cost["low"]
                - pol.load_penalty * 4 / 10)
    assert r.score(b, t, {"b": 4}) == pytest.approx(expected)
    c = tiny_roster.agent("c")  # L4 small, no approval, no oversight cost
    p_c = success_probability(0.7, 0.5, 0.0)
    assert r.score(c, t, {}) == pytest.approx(0.7 * 50 - 0.5 - (1 - p_c) * pol.incident_cost["low"])
    agent, best = r.choose(t, [b, c], {"b": 4})
    assert best == pytest.approx(max(r.score(b, t, {"b": 4}), r.score(c, t, {"b": 4})))


def test_escalation_and_audit_log(tiny_roster):
    pol = tiny_roster.policy
    assert should_escalate(pol.escalation_confidence - 0.01, pol)
    assert not should_escalate(pol.escalation_confidence, pol)
    log = AuditLog("skill_based")
    log.record(0, "T1", "route", "a")
    log.record(0, "T1", "escalate", "a", "confidence=0.40")
    log.record(1, "T2", "route", "b")
    assert log.counts() == {"escalate": 1, "route": 2}
    assert [e["seq"] for e in log.events] == [0, 1, 2]
