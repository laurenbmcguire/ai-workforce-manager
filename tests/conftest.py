from pathlib import Path

import pytest

from aiworkforce.roster import load_roster, roster_from_dict
from aiworkforce.workload import generate_tasks, load_workload_config

ROOT = Path(__file__).resolve().parents[1]


def tiny_roster_dict() -> dict:
    return {
        "model_tiers": {
            "small": {"cost_per_task": 0.5, "latency_minutes": 2, "quality_bonus": 0.0},
            "large": {"cost_per_task": 5.0, "latency_minutes": 10, "quality_bonus": 0.05},
        },
        "supervisors": [
            {"id": "ann", "name": "Ann", "title": "Ops Manager", "review_hours_per_day": 2},
            {"id": "cora", "name": "Cora", "title": "Compliance Lead", "review_hours_per_day": 1},
        ],
        "agents": [
            {"id": "a", "name": "A", "role": "Triage", "model_tier": "small", "autonomy": 3,
             "manager": "ann", "daily_capacity": 10, "skills": {"triage": 0.9, "writing": 0.4}},
            {"id": "b", "name": "B", "role": "Writer", "model_tier": "large", "autonomy": 2,
             "manager": "ann", "daily_capacity": 10, "skills": {"triage": 0.6, "writing": 0.9}},
            {"id": "c", "name": "C", "role": "Generalist", "model_tier": "small", "autonomy": 4,
             "manager": "cora", "daily_capacity": 10, "skills": {"triage": 0.7, "writing": 0.7}},
        ],
        "policy": {"autonomy_thresholds": {"low": 3, "medium": 3, "high": 4, "regulated": 5}},
    }


@pytest.fixture
def tiny_roster():
    return roster_from_dict(tiny_roster_dict())


@pytest.fixture(scope="session")
def roster():
    return load_roster(ROOT / "examples" / "roster.yml")


@pytest.fixture(scope="session")
def workload():
    return load_workload_config(ROOT / "examples" / "workload.yml")


@pytest.fixture(scope="session")
def tasks(workload):
    return generate_tasks(workload)
