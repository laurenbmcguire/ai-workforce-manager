"""Seeded synthetic task stream: 8 weeks of AI-workforce demand."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .roster import RISK_LEVELS


@dataclass(frozen=True)
class TaskType:
    type: str
    weight: float
    required_skills: tuple[str, ...]
    risk: str
    sla_hours: float
    value: float
    difficulty: float
    review_minutes: float
    human_minutes: float


@dataclass
class WorkloadConfig:
    seed: int
    weeks: int
    days_per_week: int
    base_daily_volume: float
    weekly_growth: float
    task_types: list[TaskType]

    @property
    def n_days(self) -> int:
        return self.weeks * self.days_per_week

    def catalog(self) -> dict[str, TaskType]:
        return {t.type: t for t in self.task_types}


def workload_from_dict(raw: dict) -> WorkloadConfig:
    types = []
    for t in raw["task_types"]:
        if t["risk"] not in RISK_LEVELS:
            raise ValueError(f"{t['type']}: unknown risk {t['risk']!r}")
        types.append(
            TaskType(
                type=t["type"],
                weight=float(t["weight"]),
                required_skills=tuple(t["required_skills"]),
                risk=t["risk"],
                sla_hours=float(t["sla_hours"]),
                value=float(t["value"]),
                difficulty=float(t["difficulty"]),
                review_minutes=float(t["review_minutes"]),
                human_minutes=float(t["human_minutes"]),
            )
        )
    return WorkloadConfig(
        seed=int(raw.get("seed", 2026)),
        weeks=int(raw.get("weeks", 8)),
        days_per_week=int(raw.get("days_per_week", 5)),
        base_daily_volume=float(raw.get("base_daily_volume", 50)),
        weekly_growth=float(raw.get("weekly_growth", 0.0)),
        task_types=types,
    )


def load_workload_config(path: str | Path) -> WorkloadConfig:
    with open(path, encoding="utf-8") as fh:
        return workload_from_dict(yaml.safe_load(fh))


def generate_tasks(cfg: WorkloadConfig, seed: int | None = None) -> pd.DataFrame:
    """Generate the task stream. Same config + seed -> identical DataFrame."""
    rng = np.random.default_rng(cfg.seed if seed is None else seed)
    weights = np.array([t.weight for t in cfg.task_types], dtype=float)
    weights = weights / weights.sum()
    rows = []
    tid = 0
    for day in range(cfg.n_days):
        week = day // cfg.days_per_week
        lam = cfg.base_daily_volume * (1.0 + cfg.weekly_growth) ** week
        n = int(rng.poisson(lam))
        type_idx = rng.choice(len(cfg.task_types), size=n, p=weights)
        hours = np.sort(rng.uniform(0.0, 8.0, size=n))
        for k in range(n):
            tt = cfg.task_types[type_idx[k]]
            difficulty = float(np.clip(tt.difficulty + rng.normal(0.0, 0.10), 0.05, 0.95))
            value = float(tt.value * rng.lognormal(0.0, 0.25))
            rows.append(
                {
                    "task_id": f"T{tid:05d}",
                    "day": day,
                    "week": week + 1,
                    "arrival_hour": round(float(hours[k]), 2),
                    "type": tt.type,
                    "required_skills": "|".join(tt.required_skills),
                    "risk": tt.risk,
                    "sla_hours": tt.sla_hours,
                    "value": round(value, 1),
                    "difficulty": round(difficulty, 3),
                }
            )
            tid += 1
    return pd.DataFrame(rows)


def load_or_generate_tasks(cfg: WorkloadConfig, path: str | Path) -> pd.DataFrame:
    """Read tasks.csv if present, otherwise generate and write it."""
    path = Path(path)
    if path.exists():
        return pd.read_csv(path)
    df = generate_tasks(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return df
