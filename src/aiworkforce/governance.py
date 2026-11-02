"""Governance artefacts: RACI matrix and supervisor capacity planning."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .roster import Roster

R_SHARE = 0.10  # an agent is Responsible for a task type if it handled >= 10% of it
COMPLIANCE_KEYWORD = "Compliance"


def compliance_lead(roster: Roster) -> str | None:
    for s in roster.supervisors:
        if COMPLIANCE_KEYWORD.lower() in s.title.lower():
            return s.id
    return None


def raci_matrix(outcomes: pd.DataFrame, roster: Roster, catalog) -> pd.DataFrame:
    """Rows: task types. Columns: humans then AI agents. Cells: R, A, C, I or '-'.

    R = AI agents that did >= 10% of the type (or the most proficient eligible agent).
    A = manager of the agent that did most of the type; regulated work -> compliance lead.
    C = managers of other Responsible agents, the compliance lead on high/regulated work,
        and eligible backup agents.
    I = every other supervisor.
    """
    comp = compliance_lead(roster)
    rows = []
    for ttype, cat in catalog.items():
        df = outcomes[outcomes["type"] == ttype]
        shares = df["agent"].value_counts(normalize=True) if len(df) else pd.Series(dtype=float)
        responsible = [a for a, s in shares.items() if s >= R_SHARE]
        eligible = roster.eligible(cat.required_skills)
        if not responsible:
            best = max(eligible, key=lambda a: (a.proficiency(cat.required_skills), a.id))
            responsible = [best.id]
        primary = shares.index[0] if len(shares) else responsible[0]
        accountable = roster.agent(primary).manager
        if cat.risk == "regulated" and comp:
            accountable = comp
        row = {"task_type": ttype, "risk": cat.risk}
        consulted = {roster.agent(a).manager for a in responsible} - {accountable}
        if cat.risk in ("high", "regulated") and comp and comp != accountable:
            consulted.add(comp)
        for s in roster.supervisors:
            row[s.name] = "A" if s.id == accountable else ("C" if s.id in consulted else "I")
        for a in roster.agents:
            if a.id in responsible:
                row[a.name] = "R"
            elif a in eligible:
                row[a.name] = "C"
            else:
                row[a.name] = "-"
        rows.append(row)
    return pd.DataFrame(rows)


def raci_markdown(raci: pd.DataFrame) -> str:
    cols = list(raci.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in raci.iterrows():
        lines.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
    legend = ("\nR = Responsible (does the work), A = Accountable (signs off, owns the outcome), "
              "C = Consulted (backup agent or co-owner), I = Informed, - = not involved.\n")
    return "\n".join(lines) + "\n" + legend


def capacity_plan(tasks: pd.DataFrame, outcomes: pd.DataFrame, roster: Roster,
                  horizon_days: int = 20, buffer: float = 0.15,
                  freed_hours_per_week: float = 0.0) -> dict:
    """Forecast supervisor review hours needed over the next `horizon_days` business days.

    volume: linear trend fitted to daily arrivals, extrapolated over the horizon.
    effort: observed human minutes per task (approvals, rework, escalations, audits).
    needed = forecast tasks x minutes per task / 60 x (1 + buffer).
    """
    daily = tasks.groupby("day").size().reindex(range(int(tasks["day"].max()) + 1), fill_value=0)
    x = np.arange(len(daily))
    slope, intercept = np.polyfit(x, daily.to_numpy(dtype=float), 1)
    future_x = np.arange(len(daily), len(daily) + horizon_days)
    forecast = np.clip(intercept + slope * future_x, 0, None)
    forecast_tasks = float(forecast.sum())
    minutes_per_task = float(outcomes["human_minutes"].sum() / max(len(outcomes), 1))
    base_hours = forecast_tasks * minutes_per_task / 60.0
    needed = base_hours * (1.0 + buffer)
    available = sum(s.review_hours_per_day for s in roster.supervisors) * horizon_days
    weeks = horizon_days / 5.0
    after_recs = max(0.0, base_hours - freed_hours_per_week * weeks) * (1.0 + buffer)

    share = outcomes.groupby("reviewer")["human_minutes"].sum()
    share = share / share.sum() if share.sum() else share
    per_sup = []
    for s in roster.supervisors:
        need = needed * float(share.get(s.id, 0.0))
        avail = s.review_hours_per_day * horizon_days
        per_sup.append({"supervisor": s.name, "needed_hours": round(need, 1),
                        "available_hours": round(avail, 1),
                        "utilization": round(need / avail, 3) if avail else 0.0})
    gap = needed - available
    if gap > 0:
        rec = (f"Short by {gap:.0f} h: add {gap / horizon_days:.1f} reviewer h/day or apply the "
               f"autonomy recommendations")
    else:
        rec = f"Covered with {-gap:.0f} h of headroom ({needed / available:.0%} planned utilization)"
    return {
        "horizon_business_days": horizon_days,
        "trend_tasks_per_day_per_day": round(float(slope), 3),
        "forecast_tasks": round(forecast_tasks),
        "forecast_tasks_per_day": round(forecast_tasks / horizon_days, 1),
        "human_minutes_per_task": round(minutes_per_task, 2),
        "buffer": buffer,
        "hours_needed": round(needed, 1),
        "hours_available": round(available, 1),
        "planned_utilization": round(needed / available, 3) if available else 0.0,
        "hours_needed_after_recommendations": round(after_recs, 1),
        "per_supervisor": per_sup,
        "recommendation": rec,
    }
