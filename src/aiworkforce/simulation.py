"""Day-step simulation of an AI workforce with human oversight.

Each business day:
  1. New tasks join the agent queue (earliest SLA deadline first).
  2. The router assigns each task to an eligible agent with spare daily capacity.
  3. The agent produces an output: correct with probability
     logistic(k * (proficiency + tier bonus - difficulty)), plus a noisy confidence score.
  4. Low confidence -> escalation to a human. Otherwise, if the agent's autonomy is below
     the policy threshold for the task's risk -> held for human approval. Otherwise the
     agent executes directly (act-with-audit agents get a post-hoc audit sample).
  5. Supervisors work the approval/escalation queue up to their daily review capacity
     (review hours are spread across the working day, so queues build wall-clock delay).
     Reviewers catch a share of errors (less when fatigued); uncaught errors and
     unreviewed autonomous errors become incidents.

Random draws are made once per task (common random numbers), so the three routing
strategies are compared on identical luck: differences come from routing alone.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .policy import (
    AuditLog,
    TaskView,
    make_router,
    review_minutes_for,
    should_escalate,
    success_probability,
)
from .roster import Roster
from .workload import TaskType

DAY_START_HOUR = 8.0
WORKDAY_HOURS = 8.0
REWORK_SHARE = 0.5  # rework after a caught error costs half the human task effort
MAX_EXTRA_DAYS = 30


@dataclass
class SimResult:
    strategy: str
    outcomes: pd.DataFrame
    daily: pd.DataFrame
    audit: AuditLog


def simulate(tasks: pd.DataFrame, roster: Roster, catalog: dict[str, TaskType],
             strategy: str, seed: int = 7) -> SimResult:
    pol = roster.policy
    router = make_router(strategy, roster)
    audit = AuditLog(strategy=strategy)
    n = len(tasks)
    rng = np.random.default_rng(seed)
    u_success = rng.random(n)
    conf_noise = rng.normal(0.0, pol.confidence_noise_sd, n)
    u_catch = rng.random(n)
    u_audit = rng.random(n)
    u_human = rng.random(n)

    t_id = tasks["task_id"].to_numpy()
    t_day = tasks["day"].to_numpy(dtype=int)
    t_hour = tasks["arrival_hour"].to_numpy(dtype=float)
    t_type = tasks["type"].to_numpy()
    t_risk = tasks["risk"].to_numpy()
    t_value = tasks["value"].to_numpy(dtype=float)
    t_diff = tasks["difficulty"].to_numpy(dtype=float)
    t_sla = tasks["sla_hours"].to_numpy(dtype=float)
    arrival_abs = t_day * 24.0 + DAY_START_HOUR + t_hour
    deadline = arrival_abs + t_sla

    eligible = {tt: roster.eligible(c.required_skills) for tt, c in catalog.items()}
    for tt, agents in eligible.items():
        if not agents:
            raise ValueError(f"no agent can handle task type {tt!r}")

    rec = [
        {
            "task_id": t_id[i], "day": int(t_day[i]), "week": int(t_day[i]) // 5 + 1,
            "type": t_type[i], "risk": t_risk[i], "value": float(t_value[i]),
            "difficulty": float(t_diff[i]), "sla_hours": float(t_sla[i]),
            "agent": None, "proficiency": np.nan, "p_success": np.nan,
            "agent_correct": False, "confidence": np.nan, "escalated": False,
            "approval_required": False, "reviewed": False, "error_caught": False,
            "audited": False, "audit_found_error": False, "first_pass": False,
            "status": "unfinished", "reviewer": None, "model_cost": 0.0,
            "human_minutes": 0.0, "completion_day": -1, "elapsed_hours": np.nan,
            "sla_met": False, "value_delivered": 0.0, "incident_cost": 0.0,
        }
        for i in range(n)
    ]

    def finalize(i: int, correct: bool, finish_abs: float, day: int) -> None:
        r = rec[i]
        r["status"] = "correct" if correct else "incident"
        r["completion_day"] = day
        elapsed = finish_abs - arrival_abs[i]
        r["elapsed_hours"] = round(elapsed, 3)
        r["sla_met"] = bool(elapsed <= t_sla[i] + 1e-9)
        if correct:
            factor = 1.0 if r["sla_met"] else pol.sla_miss_value_factor
            r["value_delivered"] = float(t_value[i]) * factor
        else:
            r["incident_cost"] = float(pol.incident_cost[t_risk[i]])
            audit.record(day, t_id[i], "incident", r["agent"] or "human",
                         f"risk={t_risk[i]} cost={pol.incident_cost[t_risk[i]]}")

    by_day: dict[int, list[int]] = {}
    for i in range(n):
        by_day.setdefault(int(t_day[i]), []).append(i)
    n_days = int(t_day.max()) + 1 if n else 0
    sup_cap = {s.id: s.review_minutes_per_day for s in roster.supervisors}
    stretch = {s: WORKDAY_HOURS * 60.0 / m for s, m in sup_cap.items()}

    agent_queue: list[int] = []
    human_queue: list[dict] = []
    daily_rows = []
    day = 0
    while day < n_days + MAX_EXTRA_DAYS:
        if day >= n_days and not agent_queue and not human_queue:
            break
        agent_queue.extend(by_day.get(day, []))
        agent_queue.sort(key=lambda i: (deadline[i], i))
        load = {a.id: 0 for a in roster.agents}
        demand = {"approval": 0.0, "escalation": 0.0, "audit": 0.0}
        waiting: list[int] = []
        day_open = day * 24.0 + DAY_START_HOUR

        # 1) agents work their queue
        for i in agent_queue:
            cat = catalog[t_type[i]]
            cands = [a for a in eligible[t_type[i]] if load[a.id] < a.daily_capacity]
            if not cands:
                waiting.append(i)
                continue
            view = TaskView(t_id[i], t_type[i], cat.required_skills, t_risk[i],
                            float(t_value[i]), cat.review_minutes, cat.difficulty)
            agent, score = router.choose(view, cands, load)
            load[agent.id] += 1
            tier = roster.tier(agent)
            prof = agent.proficiency(cat.required_skills)
            p = success_probability(prof, t_diff[i], tier.quality_bonus)
            ok = bool(u_success[i] < p)
            conf = float(np.clip(p + conf_noise[i], 0.0, 1.0))
            finish_agent = max(arrival_abs[i], day_open) + tier.latency_minutes / 60.0
            r = rec[i]
            r.update(agent=agent.id, proficiency=round(prof, 4), p_success=round(p, 4),
                     agent_correct=ok, confidence=round(conf, 4), model_cost=tier.cost_per_task)
            audit.record(day, t_id[i], "route", agent.id,
                         f"strategy={strategy} score={score:.2f} candidates={len(cands)}")
            if should_escalate(conf, pol):
                r["escalated"] = True
                audit.record(day, t_id[i], "escalate", agent.id,
                             f"confidence={conf:.2f} < {pol.escalation_confidence}")
                human_queue.append({"i": i, "kind": "escalation", "minutes": cat.human_minutes,
                                    "manager": agent.manager, "ready": finish_agent})
                demand["escalation"] += cat.human_minutes
            elif pol.requires_approval(agent.autonomy, t_risk[i]):
                r["approval_required"] = True
                mins = review_minutes_for(agent, cat.review_minutes, pol)
                audit.record(day, t_id[i], "hitl_hold", agent.id,
                             f"autonomy L{agent.autonomy} < L{pol.autonomy_thresholds[t_risk[i]]} "
                             f"required for {t_risk[i]} risk")
                human_queue.append({"i": i, "kind": "approval", "minutes": mins,
                                    "manager": agent.manager, "ready": finish_agent})
                demand["approval"] += mins
            else:
                audit.record(day, t_id[i], "auto_execute", agent.id,
                             f"autonomy L{agent.autonomy} >= L{pol.autonomy_thresholds[t_risk[i]]}")
                r["first_pass"] = ok
                finalize(i, ok, finish_agent, day)
                if agent.autonomy == 3 and u_audit[i] < pol.audit_sample_rate:
                    human_queue.append({"i": i, "kind": "audit", "minutes": pol.audit_minutes,
                                        "manager": agent.manager, "ready": finish_agent})
                    demand["audit"] += pol.audit_minutes
        agent_queue = waiting

        # 2) supervisors work approvals and escalations. Review hours are spread over the
        #    8-hour day, so one review minute takes (8h / review hours) of wall clock.
        day_close = day_open + WORKDAY_HOURS
        clock = {s: day_open for s in sup_cap}
        used = {s: 0.0 for s in sup_cap}
        consumed = {"approval": 0.0, "escalation": 0.0, "audit": 0.0}

        def pick(manager: str, ready: float) -> str | None:
            open_sups = {s: max(clock[s], ready) for s in sup_cap if max(clock[s], ready) < day_close}
            if manager in open_sups:
                return manager
            if not open_sups:
                return None
            return min(open_sups.items(), key=lambda kv: (kv[1], kv[0]))[0]

        def work(sup: str, ready: float, minutes: float) -> float:
            start = max(clock[sup], ready, day_open)
            clock[sup] = start + minutes * stretch[sup] / 60.0
            used[sup] += minutes
            return clock[sup]

        human_queue.sort(key=lambda it: (max(it["ready"], day_open), deadline[it["i"]], it["i"]))
        carry = []
        for item in human_queue:
            i = item["i"]
            sup = pick(item["manager"], item["ready"])
            if sup is None:
                if item["kind"] == "audit":  # audits are same-day only; a miss is an audit gap
                    audit.record(day, t_id[i], "audit_skipped", "none", "no reviewer capacity left")
                else:
                    carry.append(item)
                continue
            r = rec[i]
            if item["kind"] == "audit":  # post-hoc sample of work already executed
                r["audited"] = True
                r["audit_found_error"] = not r["agent_correct"]
                work(sup, item["ready"], item["minutes"])
                r["human_minutes"] += item["minutes"]
                consumed["audit"] += item["minutes"]
                audit.record(day, t_id[i], "audit_sample", sup,
                             "error found" if not r["agent_correct"] else "no issue")
                continue
            cat = catalog[t_type[i]]
            minutes = item["minutes"]
            r["reviewer"] = sup
            if item["kind"] == "approval":
                r["reviewed"] = True
                if r["agent_correct"]:
                    r["first_pass"] = True
                    correct = True
                    audit.record(day, t_id[i], "review_approve", sup, "output correct")
                else:
                    fatigued = used[sup] / sup_cap[sup] > pol.fatigue_threshold
                    catch = pol.review_catch_rate - (pol.fatigue_catch_drop if fatigued else 0.0)
                    if u_catch[i] < catch:
                        r["error_caught"] = True
                        minutes += cat.human_minutes * REWORK_SHARE
                        correct = True
                        audit.record(day, t_id[i], "review_reject", sup,
                                     f"error caught; rework {cat.human_minutes * REWORK_SHARE:.0f} min")
                    else:
                        correct = False
                        audit.record(day, t_id[i], "review_approve", sup,
                                     "error missed" + (" (fatigued reviewer)" if fatigued else ""))
            else:
                correct = bool(u_human[i] < pol.human_success_rate)
                audit.record(day, t_id[i], "human_resolve", sup,
                             f"escalation handled in {minutes:.0f} min")
            finish = work(sup, item["ready"], minutes) + pol.review_turnaround_hours
            consumed[item["kind"]] += minutes
            r["human_minutes"] += minutes
            finalize(i, correct, finish, day)
        human_queue = carry

        cap_total = sum(sup_cap.values())
        daily_rows.append({
            "day": day, "week": day // 5 + 1, "arrivals": len(by_day.get(day, [])),
            "agent_tasks": int(sum(load.values())),
            "demand_approval_min": demand["approval"], "demand_escalation_min": demand["escalation"],
            "demand_audit_min": demand["audit"],
            "consumed_approval_min": consumed["approval"],
            "consumed_escalation_min": consumed["escalation"],
            "consumed_audit_min": consumed["audit"],
            "consumed_total_min": sum(consumed.values()), "capacity_min": cap_total,
            "overtime_min": max(0.0, sum(consumed.values()) - cap_total),
            "backlog_agent": len(agent_queue), "backlog_human": len(human_queue),
        })
        day += 1

    for i in range(n):
        if rec[i]["status"] == "unfinished":
            rec[i]["elapsed_hours"] = np.nan
    outcomes = pd.DataFrame(rec)
    outcomes["strategy"] = strategy
    return SimResult(strategy=strategy, outcomes=outcomes, daily=pd.DataFrame(daily_rows),
                     audit=audit)


def run_all_strategies(tasks, roster, catalog, strategies, seed: int = 7) -> dict[str, SimResult]:
    return {s: simulate(tasks, roster, catalog, s, seed=seed) for s in strategies}
