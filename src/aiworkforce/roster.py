"""Roster schema: AI agents, model tiers, human supervisors and oversight policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

RISK_LEVELS = ("low", "medium", "high", "regulated")
AUTONOMY_LEVELS = {
    1: "suggest-only",
    2: "draft-for-approval",
    3: "act-with-audit",
    4: "fully autonomous",
}
TIER_ORDER = ("small", "medium", "large")


class RosterError(ValueError):
    """Raised when roster.yml violates the schema."""


@dataclass(frozen=True)
class ModelTier:
    name: str
    cost_per_task: float
    latency_minutes: float
    quality_bonus: float


@dataclass
class Agent:
    id: str
    name: str
    role: str
    model_tier: str
    autonomy: int
    manager: str
    daily_capacity: int
    skills: dict[str, float]

    def can_do(self, required_skills) -> bool:
        return all(s in self.skills for s in required_skills)

    def proficiency(self, required_skills) -> float:
        """Mean proficiency over the required skills (missing skill counts as 0)."""
        req = list(required_skills)
        if not req:
            return 0.0
        return sum(self.skills.get(s, 0.0) for s in req) / len(req)


@dataclass(frozen=True)
class Supervisor:
    id: str
    name: str
    title: str
    review_hours_per_day: float

    @property
    def review_minutes_per_day(self) -> float:
        return self.review_hours_per_day * 60.0


@dataclass
class Policy:
    autonomy_thresholds: dict[str, int]
    escalation_confidence: float = 0.55
    confidence_noise_sd: float = 0.12
    review_catch_rate: float = 0.85
    fatigue_threshold: float = 0.85
    fatigue_catch_drop: float = 0.20
    suggest_only_review_multiplier: float = 1.5
    audit_sample_rate: float = 0.10
    audit_minutes: float = 3.0
    review_turnaround_hours: float = 1.5
    human_success_rate: float = 0.97
    human_hourly_cost: float = 85.0
    load_penalty: float = 8.0
    sla_miss_value_factor: float = 0.5
    incident_cost: dict[str, float] = field(
        default_factory=lambda: {"low": 40, "medium": 150, "high": 600, "regulated": 2500}
    )

    def requires_approval(self, autonomy: int, risk: str) -> bool:
        """Human-in-the-loop rule: approval needed when autonomy is below the risk threshold."""
        if risk not in self.autonomy_thresholds:
            raise RosterError(f"unknown risk level {risk!r}")
        return autonomy < self.autonomy_thresholds[risk]


@dataclass
class Roster:
    agents: list[Agent]
    supervisors: list[Supervisor]
    tiers: dict[str, ModelTier]
    policy: Policy

    def agent(self, agent_id: str) -> Agent:
        for a in self.agents:
            if a.id == agent_id:
                return a
        raise KeyError(agent_id)

    def supervisor(self, sup_id: str) -> Supervisor:
        for s in self.supervisors:
            if s.id == sup_id:
                return s
        raise KeyError(sup_id)

    def tier(self, agent: Agent) -> ModelTier:
        return self.tiers[agent.model_tier]

    def eligible(self, required_skills) -> list[Agent]:
        return [a for a in self.agents if a.can_do(required_skills)]


def _validate(raw: dict) -> None:
    for key in ("model_tiers", "supervisors", "agents", "policy"):
        if key not in raw:
            raise RosterError(f"roster is missing section {key!r}")
    sup_ids = {s["id"] for s in raw["supervisors"]}
    agent_ids = set()
    for a in raw["agents"]:
        if a["id"] in agent_ids:
            raise RosterError(f"duplicate agent id {a['id']!r}")
        agent_ids.add(a["id"])
        if a["model_tier"] not in raw["model_tiers"]:
            raise RosterError(f"{a['id']}: unknown model tier {a['model_tier']!r}")
        if int(a["autonomy"]) not in AUTONOMY_LEVELS:
            raise RosterError(f"{a['id']}: autonomy must be 1-4, got {a['autonomy']}")
        if a["manager"] not in sup_ids:
            raise RosterError(f"{a['id']}: manager {a['manager']!r} is not a supervisor")
        if not a.get("skills"):
            raise RosterError(f"{a['id']}: at least one skill is required")
        for skill, p in a["skills"].items():
            if not 0.0 <= float(p) <= 1.0:
                raise RosterError(f"{a['id']}: proficiency for {skill} must be in [0, 1]")
        if int(a.get("daily_capacity", 0)) <= 0:
            raise RosterError(f"{a['id']}: daily_capacity must be positive")
    thresholds = raw["policy"].get("autonomy_thresholds", {})
    missing = [r for r in RISK_LEVELS if r not in thresholds]
    if missing:
        raise RosterError(f"policy.autonomy_thresholds missing {missing}")


def roster_from_dict(raw: dict) -> Roster:
    _validate(raw)
    tiers = {
        name: ModelTier(
            name=name,
            cost_per_task=float(t["cost_per_task"]),
            latency_minutes=float(t["latency_minutes"]),
            quality_bonus=float(t.get("quality_bonus", 0.0)),
        )
        for name, t in raw["model_tiers"].items()
    }
    supervisors = [
        Supervisor(
            id=s["id"],
            name=s["name"],
            title=s.get("title", ""),
            review_hours_per_day=float(s["review_hours_per_day"]),
        )
        for s in raw["supervisors"]
    ]
    agents = [
        Agent(
            id=a["id"],
            name=a["name"],
            role=a["role"],
            model_tier=a["model_tier"],
            autonomy=int(a["autonomy"]),
            manager=a["manager"],
            daily_capacity=int(a["daily_capacity"]),
            skills={k: float(v) for k, v in a["skills"].items()},
        )
        for a in raw["agents"]
    ]
    pol = dict(raw["policy"])
    pol["autonomy_thresholds"] = {k: int(v) for k, v in pol["autonomy_thresholds"].items()}
    policy = Policy(**pol)
    return Roster(agents=agents, supervisors=supervisors, tiers=tiers, policy=policy)


def load_roster(path: str | Path) -> Roster:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return roster_from_dict(raw)
