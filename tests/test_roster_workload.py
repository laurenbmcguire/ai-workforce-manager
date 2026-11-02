import copy

import pandas as pd
import pytest

from aiworkforce.roster import AUTONOMY_LEVELS, RosterError, roster_from_dict
from aiworkforce.workload import generate_tasks

from conftest import ROOT, tiny_roster_dict


def test_example_roster_schema(roster):
    assert len(roster.agents) >= 8
    assert len(roster.supervisors) == 3
    sup_ids = {s.id for s in roster.supervisors}
    for a in roster.agents:
        assert a.autonomy in AUTONOMY_LEVELS
        assert a.manager in sup_ids
        assert a.model_tier in roster.tiers
        assert all(0.0 <= p <= 1.0 for p in a.skills.values())


@pytest.mark.parametrize("mutate", [
    lambda d: d["agents"][0]["skills"].update(triage=1.3),
    lambda d: d["agents"][0].update(autonomy=5),
    lambda d: d["agents"][0].update(manager="nobody"),
    lambda d: d["agents"][0].update(model_tier="xl"),
    lambda d: d["policy"]["autonomy_thresholds"].pop("regulated"),
])
def test_invalid_roster_is_rejected(mutate):
    d = copy.deepcopy(tiny_roster_dict())
    mutate(d)
    with pytest.raises(RosterError):
        roster_from_dict(d)


def test_proficiency_is_mean_of_required_skills(tiny_roster):
    b = tiny_roster.agent("b")
    assert b.proficiency(["triage", "writing"]) == pytest.approx(0.75)
    assert b.proficiency(["triage", "missing"]) == pytest.approx(0.3)
    assert b.can_do(["triage", "writing"]) and not b.can_do(["sql"])


def test_hitl_rule_autonomy_vs_risk(tiny_roster):
    pol = tiny_roster.policy
    # level 2 (draft-for-approval) always needs approval
    assert all(pol.requires_approval(2, r) for r in ("low", "medium", "high", "regulated"))
    # level 3 (act-with-audit) acts on low/medium, needs approval on high/regulated
    assert not pol.requires_approval(3, "low") and not pol.requires_approval(3, "medium")
    assert pol.requires_approval(3, "high") and pol.requires_approval(3, "regulated")
    # level 4 acts on high risk, but regulated work always gets a human sign-off
    assert not pol.requires_approval(4, "high") and pol.requires_approval(4, "regulated")


def test_workload_is_deterministic_and_valid(workload, tasks):
    again = generate_tasks(workload)
    pd.testing.assert_frame_equal(tasks, again)
    assert tasks["day"].max() == workload.n_days - 1
    assert set(tasks["risk"]) <= {"low", "medium", "high", "regulated"}
    assert tasks["difficulty"].between(0.05, 0.95).all()
    assert tasks["task_id"].is_unique
    # weekly growth shows up in volume
    per_week = tasks.groupby("week").size()
    assert per_week.iloc[-2:].mean() > per_week.iloc[:2].mean()


def test_committed_sample_matches_generator(tasks):
    committed = pd.read_csv(ROOT / "examples" / "tasks.csv")
    pd.testing.assert_frame_equal(committed, tasks, check_dtype=False)
