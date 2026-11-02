"""AI Project Manager: manages this repo as a project.

Reads project/ROADMAP.yml, project/RISKS.yml and the pipeline's reports/summary.json,
applies explicit deterministic rules, and writes project/STATUS.md + project/status.json.
If ANTHROPIC_API_KEY is set, an LLM writes the executive summary from the computed facts
only; any failure falls back to a deterministic template. The run never fails on the LLM.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import urllib.request
from pathlib import Path

import yaml

PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-4-5"
STRATEGY_NAMES = {"round_robin": "Round-robin", "skill_based": "Skill-based",
                  "cost_aware": "Cost-aware skill-based"}


def _date(v) -> dt.date:
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v))


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# ---------------------------------------------------------------- deterministic engine
def heat_label(score: int) -> str:
    if score >= 20:
        return "Critical"
    if score >= 15:
        return "High"
    if score >= 8:
        return "Medium"
    return "Low"


def risk_register(risks: list[dict]) -> list[dict]:
    rows = []
    for r in risks:
        score = int(r["probability"]) * int(r["impact"])
        rows.append({**r, "score": score, "heat": heat_label(score)})
    return sorted(rows, key=lambda r: (-r["score"], r["id"]))


def milestone_table(milestones: list[dict], backlog: list[dict], today: dt.date) -> list[dict]:
    rows = []
    for m in milestones:
        items = [b for b in backlog if b.get("milestone") == m["id"]]
        if items:
            pct = round(100.0 * sum(b["status"] == "done" for b in items) / len(items))
        else:
            pct = 100 if m["status"] == "done" else 0
        if m["status"] == "done":
            pct = 100
        due = _date(m["due"])
        rows.append({"id": m["id"], "name": m["name"], "due": due.isoformat(),
                     "status": m["status"], "pct_done": pct,
                     "overdue": m["status"] != "done" and due < today,
                     "done_criteria": m.get("done_criteria", [])})
    return rows


def health(milestones: list[dict], backlog: list[dict], risks: list[dict]) -> tuple[str, str]:
    """RED: any overdue milestone, a risk >= 20, or >= 2 open P0 items.
    AMBER: any open P0 item or a risk >= 15. GREEN otherwise."""
    overdue = [m["id"] for m in milestones if m["overdue"]]
    open_p0 = [b["id"] for b in backlog if b["priority"] == "P0" and b["status"] != "done"]
    critical = [r["id"] for r in risks if r["score"] >= 20]
    high = [r["id"] for r in risks if r["score"] >= 15]
    if overdue or critical or len(open_p0) >= 2:
        parts = []
        if overdue:
            parts.append(f"{len(overdue)} overdue milestone(s) ({', '.join(overdue)})")
        if critical:
            parts.append(f"risk score >= 20 ({', '.join(critical)})")
        if len(open_p0) >= 2:
            parts.append(f"{len(open_p0)} open P0 items")
        return "RED", "; ".join(parts)
    if open_p0 or high:
        parts = []
        if open_p0:
            parts.append(f"open P0 item(s) {', '.join(open_p0)}")
        if high:
            parts.append(f"{len(high)} risk(s) scored >= 15 ({', '.join(high)})")
        return "AMBER", "; ".join(parts) + "; no milestone is overdue"
    return "GREEN", "no overdue milestones, no open P0 items, no risk scored >= 15"


def next_actions(backlog: list[dict], n: int = 3) -> list[dict]:
    """Unblocked todo items, ranked by priority, then by number of dependencies satisfied."""
    status = {b["id"]: b["status"] for b in backlog}
    ready = []
    for b in backlog:
        deps = b.get("depends_on") or []
        if b["status"] != "todo" or not all(status.get(d) == "done" for d in deps):
            continue
        ready.append(b)
    ready.sort(key=lambda b: (PRIORITY_RANK[b["priority"]], -len(b.get("depends_on") or []), b["id"]))
    return ready[:n]


def _usd(v: float) -> str:
    return f"-${abs(v):,.0f}" if v < 0 else f"${v:,.0f}"


def data_bullets(summary: dict) -> list[str]:
    """3-5 bullets on what the latest pipeline run actually found."""
    if not summary:
        return ["No reports/summary.json found yet: run `python -m aiworkforce run` first."]
    dep = summary["deployed_strategy"]
    table = {r["strategy"]: r for r in summary["strategy_comparison"]}
    d, rr = table[dep], table.get("round_robin", table[dep])
    ov = summary["overall"]
    cap = summary["capacity_plan"]
    counts = summary["recommendation_counts"]
    by_action: dict[str, list[str]] = {}
    for r in summary["performance_reviews"]:
        by_action.setdefault(r["action"], []).append(r["name"])
    bullets = [
        f"{STRATEGY_NAMES[dep]} routing has the highest net value ({_usd(d['net_value'])}) vs "
        f"round-robin ({_usd(rr['net_value'])}): first-pass quality {d['first_pass_quality']:.1%} vs "
        f"{rr['first_pass_quality']:.1%}, incidents {d['incidents']} vs {rr['incidents']}.",
        f"Deployed strategy: ${ov['cost_per_task']:.2f} per task fully loaded, SLA attainment "
        f"{ov['sla_attainment']:.1%}, reviewers at {ov['reviewer_utilization']:.0%} of capacity.",
        "Performance reviews: " + ", ".join(
            f"{k} {v} ({', '.join(by_action.get(k, []))})" for k, v in counts.items() if k != "keep")
        + f", keep {counts.get('keep', 0)}.",
        f"Next month needs {cap['hours_needed']:.0f} supervisor hours vs {cap['hours_available']:.0f} "
        f"available ({cap['planned_utilization']:.0%}); {cap['hours_needed_after_recommendations']:.0f} h "
        f"if autonomy recommendations are applied.",
    ]
    skipped = summary.get("audit", {}).get("by_type", {}).get("audit_skipped", 0)
    if skipped:
        bullets.append(f"Audit gap: {skipped} post-hoc audit samples were skipped for lack of "
                       f"reviewer capacity.")
    return bullets[:5]


def change_log_entry(today: dt.date, summary: dict, rag: str) -> str:
    if not summary:
        return f"{today.isoformat()}: status refreshed without pipeline results. Health {rag}."
    dep = summary["deployed_strategy"]
    cap = summary["capacity_plan"]
    return (f"{today.isoformat()}: status refreshed from the latest pipeline run. Deployed routing "
            f"= {dep} (net value {_usd(summary['overall']['net_value'])}); capacity plan "
            f"{cap['hours_needed']:.0f} h needed / {cap['hours_available']:.0f} h available. "
            f"Health {rag}.")


def build_facts(root: Path, today: dt.date) -> dict:
    roadmap = load_yaml(root / "project" / "ROADMAP.yml")
    risks = risk_register(load_yaml(root / "project" / "RISKS.yml").get("risks", []))
    summary_path = root / "reports" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    backlog = roadmap.get("backlog", [])
    milestones = milestone_table(roadmap.get("milestones", []), backlog, today)
    rag, reason = health(milestones, backlog, risks)
    return {
        "date": today.isoformat(),
        "project": roadmap.get("project", "ai-workforce-manager"),
        "health": {"rag": rag, "reason": reason},
        "milestones": milestones,
        "risks": risks,
        "next_actions": [{k: b.get(k) for k in ("id", "title", "priority", "owner", "depends_on")}
                         for b in next_actions(backlog)],
        "backlog_open": sum(b["status"] != "done" for b in backlog),
        "backlog_total": len(backlog),
        "data_says": data_bullets(summary),
        "change_log": change_log_entry(today, summary, rag),
    }


# ---------------------------------------------------------------- executive summary
def template_summary(facts: dict) -> str:
    done = sum(m["status"] == "done" for m in facts["milestones"])
    nxt = ", ".join(f"{a['id']} {a['title']}" for a in facts["next_actions"]) or "none"
    top = facts["risks"][0] if facts["risks"] else None
    risk_txt = f" Top risk: {top['id']} {top['risk']} (score {top['score']})." if top else ""
    return (f"Health is {facts['health']['rag']}: {facts['health']['reason']}. "
            f"{done} of {len(facts['milestones'])} milestones are done and "
            f"{facts['backlog_open']} of {facts['backlog_total']} backlog items remain open. "
            f"{facts['data_says'][0]}{risk_txt} Next actions: {nxt}.")


def llm_summary(facts: dict, api_key: str, model: str, timeout: float = 30.0) -> str:
    prompt = (
        "You are the AI project manager for a portfolio software project. Write a 90-120 word "
        "executive summary in plain prose (no headings, no bullet points). Use ONLY the facts in "
        "the JSON below; do not invent numbers, people, customers or deployments.\n\n"
        + json.dumps(facts, indent=1)
    )
    body = json.dumps({"model": model, "max_tokens": 400,
                       "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "x-api-key": api_key, "anthropic-version": "2023-06-01",
        "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    text = " ".join(text.split())
    if not text:
        raise ValueError("empty LLM response")
    return text


def executive_summary(facts: dict, use_llm: bool = True) -> tuple[str, str]:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if use_llm and key:
        try:
            model = os.environ.get("AI_PM_MODEL", DEFAULT_MODEL)
            return llm_summary(facts, key, model), f"llm ({model})"
        except Exception as exc:  # never fail the run because of the LLM
            print(f"AI PM: LLM summary unavailable ({type(exc).__name__}); using template.")
    return template_summary(facts), "template"


# ---------------------------------------------------------------- rendering
def render_status(facts: dict, summary_text: str, source: str) -> str:
    rag = facts["health"]["rag"]
    lines = [
        f"# Project status: {facts['project']}",
        "",
        f"**Date:** {facts['date']}  ",
        f"**Health:** {rag} - {facts['health']['reason']}",
        "",
        "## Executive summary",
        "",
        summary_text,
        "",
        f"_Source: {source}. Generated by `python -m aiworkforce pm`._",
        "",
        "## Milestones",
        "",
        "| ID | Milestone | Due | Status | % done |",
        "|---|---|---|---|---|",
    ]
    for m in facts["milestones"]:
        flag = " (overdue)" if m["overdue"] else ""
        lines.append(f"| {m['id']} | {m['name']} | {m['due']} | {m['status']}{flag} | {m['pct_done']}% |")
    lines += ["", "## Risk register", "", "| ID | Risk | P | I | Score | Heat | Owner | Mitigation | Status |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in facts["risks"]:
        lines.append(f"| {r['id']} | {r['risk']} | {r['probability']} | {r['impact']} | {r['score']} | "
                     f"{r['heat']} | {r['owner']} | {r['mitigation']} | {r['status']} |")
    lines += ["", "## Next 3 actions", "", "| # | ID | Item | Priority | Owner | Depends on |",
              "|---|---|---|---|---|---|"]
    for k, a in enumerate(facts["next_actions"], 1):
        deps = ", ".join(a.get("depends_on") or []) or "-"
        lines.append(f"| {k} | {a['id']} | {a['title']} | {a['priority']} | {a['owner']} | {deps} |")
    lines += ["", "## What the data says", ""]
    lines += [f"- {b}" for b in facts["data_says"]]
    lines += ["", "## Decision / change log", "", f"- {facts['change_log']}", ""]
    return "\n".join(lines)


def run_pm(root: str | Path = ".", today: dt.date | None = None, use_llm: bool = True,
           verbose: bool = True) -> dict:
    root = Path(root)
    today = today or dt.date.today()
    facts = build_facts(root, today)
    text, source = executive_summary(facts, use_llm=use_llm)
    out = {**facts, "executive_summary": text, "summary_source": source}
    (root / "project").mkdir(exist_ok=True)
    (root / "project" / "STATUS.md").write_text(render_status(facts, text, source), encoding="utf-8")
    (root / "project" / "status.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    if verbose:
        print(f"AI PM: health {facts['health']['rag']} ({facts['health']['reason']})")
        print("Next actions: " + ", ".join(a["id"] for a in facts["next_actions"]))
        print(f"Wrote {root / 'project' / 'STATUS.md'} and status.json (summary: {source})")
    return out
