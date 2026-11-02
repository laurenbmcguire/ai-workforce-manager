"""Policy engine: routing strategies, human-in-the-loop rules, escalation and the audit log."""

from __future__ import annotations

import csv
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .roster import Agent, Policy, Roster

STRATEGIES = ("round_robin", "skill_based", "cost_aware")
STRATEGY_LABELS = {
    "round_robin": "Round-robin",
    "skill_based": "Skill-based",
    "cost_aware": "Cost-aware skill-based",
}

SUCCESS_SLOPE = 7.0


def success_probability(proficiency: float, difficulty: float, quality_bonus: float = 0.0,
                        slope: float = SUCCESS_SLOPE) -> float:
    """P(correct output) = logistic(slope * (proficiency + tier bonus - difficulty))."""
    return 1.0 / (1.0 + math.exp(-slope * (proficiency + quality_bonus - difficulty)))


def should_escalate(confidence: float, policy: Policy) -> bool:
    """Low-confidence outputs go to a human instead of shipping or entering review."""
    return confidence < policy.escalation_confidence


def review_minutes_for(agent: Agent, base_review_minutes: float, policy: Policy) -> float:
    """Suggest-only agents (level 1) cost more reviewer time than draft-for-approval agents."""
    mult = policy.suggest_only_review_multiplier if agent.autonomy == 1 else 1.0
    return base_review_minutes * mult


@dataclass
class TaskView:
    """What a router needs to know about one task."""

    task_id: str
    type: str
    required_skills: tuple[str, ...]
    risk: str
    value: float
    review_minutes: float
    difficulty: float = 0.5  # catalog (expected) difficulty; the per-task draw stays hidden


class Router:
    name = "base"

    def __init__(self, roster: Roster):
        self.roster = roster

    def choose(self, task: TaskView, candidates: list[Agent], load: dict[str, int]):
        raise NotImplementedError


class RoundRobinRouter(Router):
    """Cycle through eligible agents per task type, ignoring skill and cost."""

    name = "round_robin"

    def __init__(self, roster: Roster):
        super().__init__(roster)
        self._pointer: dict[str, int] = defaultdict(int)

    def choose(self, task, candidates, load):
        eligible = self.roster.eligible(task.required_skills)
        ids = {a.id for a in candidates}
        n = len(eligible)
        start = self._pointer[task.type]
        for step in range(n):
            agent = eligible[(start + step) % n]
            if agent.id in ids:
                self._pointer[task.type] = (start + step + 1) % n
                return agent, 0.0
        raise LookupError("no candidate available")


class SkillBasedRouter(Router):
    """Pick the most proficient available agent; ties go to the less loaded one."""

    name = "skill_based"

    def choose(self, task, candidates, load):
        best = max(
            candidates,
            key=lambda a: (round(a.proficiency(task.required_skills), 6), -load.get(a.id, 0), a.id),
        )
        return best, best.proficiency(task.required_skills)


class CostAwareRouter(Router):
    """score = proficiency * value - cost - load penalty.

    cost = model cost + expected oversight cost (reviewer minutes x hourly rate, if this
    agent would need approval for this risk level) + expected incident cost
    ((1 - p_hat) x residual miss rate x incident cost for the risk level), where p_hat is
    the success model evaluated at the task type's catalog difficulty.
    load penalty = policy.load_penalty x (tasks already assigned today / daily capacity).
    """

    name = "cost_aware"

    def score(self, agent: Agent, task: TaskView, load: dict[str, int]) -> float:
        pol = self.roster.policy
        prof = agent.proficiency(task.required_skills)
        model_cost = self.roster.tier(agent).cost_per_task
        needs_review = pol.requires_approval(agent.autonomy, task.risk)
        oversight = (
            review_minutes_for(agent, task.review_minutes, pol) / 60.0 * pol.human_hourly_cost
            if needs_review
            else 0.0
        )
        miss = (1.0 - pol.review_catch_rate) if needs_review else 1.0
        p_hat = success_probability(prof, task.difficulty, self.roster.tier(agent).quality_bonus)
        incident = (1.0 - p_hat) * miss * pol.incident_cost[task.risk]
        load_pen = pol.load_penalty * load.get(agent.id, 0) / agent.daily_capacity
        return prof * task.value - (model_cost + oversight + incident) - load_pen

    def choose(self, task, candidates, load):
        scored = [(self.score(a, task, load), a.id, a) for a in candidates]
        s, _, agent = max(scored, key=lambda x: (round(x[0], 9), x[1]))
        return agent, s


def make_router(strategy: str, roster: Roster) -> Router:
    routers = {"round_robin": RoundRobinRouter, "skill_based": SkillBasedRouter,
               "cost_aware": CostAwareRouter}
    if strategy not in routers:
        raise ValueError(f"unknown strategy {strategy!r}; choose from {STRATEGIES}")
    return routers[strategy](roster)


@dataclass
class AuditLog:
    """Append-only record of every routing, oversight and outcome decision."""

    strategy: str
    events: list[dict] = field(default_factory=list)

    def record(self, day: int, task_id: str, event: str, actor: str, detail: str = "") -> None:
        self.events.append(
            {"seq": len(self.events), "strategy": self.strategy, "day": day, "task_id": task_id,
             "event": event, "actor": actor, "detail": detail}
        )

    def counts(self) -> dict[str, int]:
        return dict(sorted(Counter(e["event"] for e in self.events).items()))

    def to_csv(self, path: str | Path, limit: int | None = None) -> None:
        rows = self.events if limit is None else self.events[:limit]
        cols = ["seq", "strategy", "day", "task_id", "event", "actor", "detail"]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
