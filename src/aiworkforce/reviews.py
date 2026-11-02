"""Automated, rule-based performance reviews for each AI agent.

Rules are checked in a fixed order; the first one that fires is the recommendation:
  1. retire                 idle (utilisation below threshold) and fully redundant
  2. retrain / prompt-tune  first-pass quality, escalation or incident rate out of bounds
  3. downgrade model tier   the next cheaper tier is estimated to cost <= 3 pts of quality
  4. increase autonomy      strong record on exactly the work one more level would free up
  5. keep                   none of the above
Every recommendation carries the evidence (numbers) that triggered it.
"""

from __future__ import annotations

import pandas as pd

from .policy import success_probability
from .roster import TIER_ORDER, Roster

THRESHOLDS = {
    "retire_max_utilization": 0.05,
    "retrain_min_fpq": 0.75,
    "retrain_max_escalation": 0.12,
    "retrain_max_incident": 0.08,
    "downgrade_max_quality_drop": 0.03,
    "downgrade_min_fpq": 0.85,
    "promote_min_fpq": 0.90,
    "promote_max_escalation": 0.05,
    "promote_max_incident": 0.03,
    "promote_min_tasks": 30,
    "promote_min_hours": 5.0,
}

ACTIONS = ("retire", "retrain / prompt-tune", "downgrade model tier", "increase autonomy", "keep")


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _usd(v: float) -> str:
    return f"-${abs(v):,.0f}" if v < 0 else f"${v:,.0f}"


def tier_change_estimate(agent_tasks: pd.DataFrame, current_bonus: float, new_bonus: float) -> float:
    """Mean change in success probability if the same tasks ran on another tier."""
    if agent_tasks.empty:
        return 0.0
    deltas = [
        success_probability(p, d, new_bonus) - success_probability(p, d, current_bonus)
        for p, d in zip(agent_tasks["proficiency"], agent_tasks["difficulty"])
    ]
    return float(sum(deltas) / len(deltas))


def review_agent(agent, kpi: dict, outcomes: pd.DataFrame, roster: Roster, catalog,
                 n_weeks: int, th: dict | None = None) -> dict:
    th = {**THRESHOLDS, **(th or {})}
    pol = roster.policy
    mine = outcomes[outcomes["agent"] == agent.id]
    evidence: list[str] = []
    action = "keep"

    # 1) retire: idle and every task type it can do is covered by someone else
    my_types = [t for t, c in catalog.items() if agent.can_do(c.required_skills)]
    redundant = all(len(roster.eligible(catalog[t].required_skills)) > 1 for t in my_types)
    if kpi["utilization"] < th["retire_max_utilization"] and redundant:
        action = "retire"
        evidence.append(f"utilization {_pct(kpi['utilization'])} < {_pct(th['retire_max_utilization'])}")
        evidence.append(f"handled {kpi['tasks']} of {len(outcomes)} tasks")
        covered = ", ".join(my_types) if my_types else "none"
        evidence.append(f"every task type it can take ({covered}) has another eligible agent")
        return {"agent": agent.id, "name": agent.name, "action": action, "evidence": evidence}

    # 2) retrain / prompt-tune
    reasons = []
    if kpi["first_pass_quality"] < th["retrain_min_fpq"]:
        reasons.append(f"first-pass quality {_pct(kpi['first_pass_quality'])} < {_pct(th['retrain_min_fpq'])}")
    if kpi["escalation_rate"] > th["retrain_max_escalation"]:
        reasons.append(f"escalation rate {_pct(kpi['escalation_rate'])} > {_pct(th['retrain_max_escalation'])}")
    if kpi["incident_rate"] > th["retrain_max_incident"]:
        reasons.append(f"incident rate {_pct(kpi['incident_rate'])} > {_pct(th['retrain_max_incident'])}")
    if reasons and not mine.empty:
        by_type = mine.groupby("type")["first_pass"].agg(["mean", "size"])
        by_type = by_type[by_type["size"] >= 10].sort_values("mean")
        if not by_type.empty:
            worst = by_type.index[0]
            reasons.append(f"weakest task type: {worst} (FPQ {_pct(by_type.loc[worst, 'mean'])}, "
                           f"n={int(by_type.loc[worst, 'size'])})")
        reasons.append(f"incident cost {_usd(kpi['incident_cost'])}; net value {_usd(kpi['net_value'])}")
        return {"agent": agent.id, "name": agent.name, "action": "retrain / prompt-tune",
                "evidence": reasons}

    # 3) downgrade model tier
    cheaper = [t for t in TIER_ORDER[:TIER_ORDER.index(agent.model_tier)] if t in roster.tiers]
    if cheaper and not mine.empty and kpi["first_pass_quality"] >= th["downgrade_min_fpq"]:
        cur, low = roster.tiers[agent.model_tier], roster.tiers[cheaper[-1]]
        delta = tier_change_estimate(mine, cur.quality_bonus, low.quality_bonus)
        if -delta <= th["downgrade_max_quality_drop"]:
            saving = (cur.cost_per_task - low.cost_per_task) * len(mine)
            return {"agent": agent.id, "name": agent.name, "action": "downgrade model tier",
                    "evidence": [
                        f"first-pass quality {_pct(kpi['first_pass_quality'])} on {agent.model_tier}",
                        f"estimated quality change on {low.name}: {100 * delta:+.1f} pts "
                        f"(limit -{100 * th['downgrade_max_quality_drop']:.0f} pts)",
                        f"model spend saving ${saving:,.0f} over {n_weeks} weeks "
                        f"(${cur.cost_per_task:.2f} -> ${low.cost_per_task:.2f} per task)",
                    ]}
        evidence.append(f"downgrade to {low.name} would cost {100 * -delta:.1f} pts of quality")

    # 4) increase autonomy: judge only the work the next level would stop sending to review
    if agent.autonomy < 4 and not mine.empty:
        nxt = agent.autonomy + 1
        freed_mask = mine["risk"].map(
            lambda r: pol.requires_approval(agent.autonomy, r) and not pol.requires_approval(nxt, r))
        freed = mine[freed_mask]
        hours = freed["human_minutes"].sum() / 60.0
        if len(freed) >= th["promote_min_tasks"]:
            fpq = float(freed["first_pass"].mean())
            esc = float(freed["escalated"].mean())
            ok = (fpq >= th["promote_min_fpq"] and esc <= th["promote_max_escalation"]
                  and kpi["incident_rate"] <= th["promote_max_incident"]
                  and hours >= th["promote_min_hours"])
            if ok:
                risks = ", ".join(sorted(freed["risk"].unique()))
                return {"agent": agent.id, "name": agent.name, "action": "increase autonomy",
                        "evidence": [
                            f"L{agent.autonomy} -> L{nxt} would remove approval for {risks}-risk work "
                            f"({len(freed)} tasks)",
                            f"first-pass quality on that work {_pct(fpq)}, escalation {_pct(esc)}",
                            f"overall incident rate {_pct(kpi['incident_rate'])}",
                            f"frees ~{hours:.1f} reviewer hours over {n_weeks} weeks "
                            f"({hours / n_weeks:.1f} h/week)",
                        ], "freed_hours_per_week": round(hours / n_weeks, 2)}
            evidence.append(f"L{nxt} would free {hours:.1f} h, but FPQ on that work is {_pct(fpq)}")
        elif len(freed) == 0:
            evidence.append(f"L{nxt} would not remove any approvals for its current work")

    evidence.insert(0, f"FPQ {_pct(kpi['first_pass_quality'])}, escalation "
                       f"{_pct(kpi['escalation_rate'])}, incidents {_pct(kpi['incident_rate'])}, "
                       f"SLA {_pct(kpi['sla_attainment'])}")
    return {"agent": agent.id, "name": agent.name, "action": "keep", "evidence": evidence}


def performance_reviews(agent_kpi_df: pd.DataFrame, outcomes: pd.DataFrame, roster: Roster,
                        catalog, n_weeks: int, th: dict | None = None) -> list[dict]:
    out = []
    for a in roster.agents:
        kpi = agent_kpi_df[agent_kpi_df["agent"] == a.id].iloc[0].to_dict()
        out.append(review_agent(a, kpi, outcomes, roster, catalog, n_weeks, th))
    return out
