"""KPIs per agent, per strategy and per week."""

from __future__ import annotations

import pandas as pd

from .roster import Roster


def _safe(num: float, den: float) -> float:
    return float(num) / float(den) if den else 0.0


def kpi_block(df: pd.DataFrame, hourly_cost: float, n_weeks: int) -> dict:
    """Core KPI definitions shared by agent-level and fleet-level views."""
    attempts = len(df)
    done = df[df["status"] != "unfinished"]
    completed = len(done)
    incidents = int((df["status"] == "incident").sum())
    review_hours = df["human_minutes"].sum() / 60.0
    model_cost = df["model_cost"].sum()
    human_cost = review_hours * hourly_cost
    incident_cost = df["incident_cost"].sum()
    value = df["value_delivered"].sum()
    return {
        "tasks": attempts,
        "completed": completed,
        "throughput_per_week": round(_safe(completed, n_weeks), 1),
        "first_pass_quality": round(_safe(df["first_pass"].sum(), attempts), 4),
        "escalation_rate": round(_safe(df["escalated"].sum(), attempts), 4),
        "approval_share": round(_safe(df["approval_required"].sum(), attempts), 4),
        "human_review_hours": round(review_hours, 1),
        "model_cost": round(model_cost, 2),
        "human_cost": round(human_cost, 2),
        "cost_per_task": round(_safe(model_cost + human_cost, completed), 2),
        "sla_attainment": round(_safe(done["sla_met"].sum(), attempts), 4),
        "incidents": incidents,
        "incident_rate": round(_safe(incidents, completed), 4),
        "incident_cost": round(float(incident_cost), 2),
        "value_delivered": round(float(value), 2),
        "net_value": round(float(value - model_cost - human_cost - incident_cost), 2),
    }


def agent_kpis(outcomes: pd.DataFrame, roster: Roster, n_weeks: int, n_days: int) -> pd.DataFrame:
    rows = []
    hourly = roster.policy.human_hourly_cost
    for a in roster.agents:
        df = outcomes[outcomes["agent"] == a.id]
        row = {"agent": a.id, "name": a.name, "role": a.role, "model_tier": a.model_tier,
               "autonomy": a.autonomy, "manager": a.manager}
        row.update(kpi_block(df, hourly, n_weeks))
        row["utilization"] = round(_safe(len(df), a.daily_capacity * n_days), 4)
        row["share_of_tasks"] = round(_safe(len(df), len(outcomes)), 4)
        rows.append(row)
    return pd.DataFrame(rows)


def overall_kpis(outcomes: pd.DataFrame, daily: pd.DataFrame, roster: Roster, n_weeks: int) -> dict:
    out = kpi_block(outcomes, roster.policy.human_hourly_cost, n_weeks)
    cap = daily["capacity_min"].sum()
    out["reviewer_utilization"] = round(_safe(daily["consumed_total_min"].sum(), cap), 4)
    out["overtime_hours"] = round(daily["overtime_min"].sum() / 60.0, 1)
    out["peak_human_backlog"] = int(daily["backlog_human"].max())
    out["unfinished"] = int((outcomes["status"] == "unfinished").sum())
    out["sim_days"] = int(len(daily))
    out["audits_performed"] = int(outcomes["audited"].sum())
    return out


def strategy_table(results: dict, roster: Roster, n_weeks: int) -> pd.DataFrame:
    rows = []
    for name, res in results.items():
        k = overall_kpis(res.outcomes, res.daily, roster, n_weeks)
        k["strategy"] = name
        rows.append(k)
    cols = ["strategy"] + [c for c in rows[0] if c != "strategy"]
    return pd.DataFrame(rows)[cols]


def weekly_series(outcomes: pd.DataFrame, daily: pd.DataFrame, n_weeks: int) -> pd.DataFrame:
    """Throughput and incidents by completion week; review load vs capacity by week.

    Work done while draining queues after the last arrival day is folded into the final week
    (capacity is counted for the scheduled weeks only)."""
    done = outcomes[outcomes["completion_day"] >= 0].copy()
    done["cweek"] = (done["completion_day"] // 5 + 1).clip(upper=n_weeks)
    daily = daily.copy()
    drain = daily["week"] > n_weeks
    daily.loc[drain, "capacity_min"] = 0.0
    daily["week"] = daily["week"].clip(upper=n_weeks)
    thr = done.groupby("cweek").size().rename("completed")
    inc = done[done["status"] == "incident"].groupby("cweek").size().rename("incidents")
    d = daily.groupby("week").agg(
        approval_h=("consumed_approval_min", lambda s: s.sum() / 60),
        escalation_h=("consumed_escalation_min", lambda s: s.sum() / 60),
        audit_h=("consumed_audit_min", lambda s: s.sum() / 60),
        demand_h=("demand_approval_min", lambda s: s.sum() / 60),
        demand_esc_h=("demand_escalation_min", lambda s: s.sum() / 60),
        demand_audit_h=("demand_audit_min", lambda s: s.sum() / 60),
        capacity_h=("capacity_min", lambda s: s.sum() / 60),
    )
    d["demand_total_h"] = d["demand_h"] + d["demand_esc_h"] + d["demand_audit_h"]
    out = pd.concat([thr, inc], axis=1).fillna(0).astype(int)
    out.index.name = "week"
    return out.join(d, how="outer").fillna(0).reset_index()
