"""End-to-end pipeline: data -> simulation x 3 strategies -> KPIs -> reviews -> reports."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from . import charts
from .governance import capacity_plan, raci_markdown, raci_matrix
from .kpis import agent_kpis, strategy_table, weekly_series
from .policy import STRATEGIES
from .report import write_report, write_summary
from .reviews import performance_reviews
from .roster import load_roster
from .simulation import run_all_strategies
from .workload import load_or_generate_tasks, load_workload_config

SIM_SEED = 7


def run(root: str | Path = ".", verbose: bool = True) -> dict:
    root = Path(root)
    ex, img, rep = root / "examples", root / "docs" / "img", root / "reports"
    img.mkdir(parents=True, exist_ok=True)
    rep.mkdir(parents=True, exist_ok=True)

    roster = load_roster(ex / "roster.yml")
    cfg = load_workload_config(ex / "workload.yml")
    catalog = cfg.catalog()
    tasks = load_or_generate_tasks(cfg, ex / "tasks.csv")

    results = run_all_strategies(tasks, roster, catalog, STRATEGIES, seed=SIM_SEED)
    table = strategy_table(results, roster, cfg.weeks)
    deployed = str(table.sort_values(["net_value", "strategy"], ascending=[False, True])
                   .iloc[0]["strategy"])
    res = results[deployed]
    akpi = agent_kpis(res.outcomes, roster, cfg.weeks, cfg.n_days)
    reviews = performance_reviews(akpi, res.outcomes, roster, catalog, cfg.weeks)
    freed = sum(r.get("freed_hours_per_week", 0.0) for r in reviews
                if r["action"] == "increase autonomy")
    cap = capacity_plan(tasks, res.outcomes, roster, freed_hours_per_week=freed)
    raci = raci_matrix(res.outcomes, roster, catalog)
    raci_md = raci_markdown(raci)
    weekly = {s: weekly_series(r.outcomes, r.daily, cfg.weeks) for s, r in results.items()}

    charts.strategy_comparison(table, deployed, img / "strategy_comparison.png")
    charts.agent_scorecard(akpi, img / "agent_scorecard.png")
    charts.weekly_throughput(weekly[deployed], img / "weekly_throughput.png")
    charts.review_load(weekly, deployed, img / "review_load.png")

    audit_counts = res.audit.counts()
    overall = table[table["strategy"] == deployed].iloc[0].to_dict()
    overall.pop("strategy")
    best = {
        "net_value_gain_vs_round_robin": round(
            overall["net_value"] - float(table.loc[table["strategy"] == "round_robin", "net_value"].iloc[0]), 2),
    }
    summary = {
        "project": "ai-workforce-manager",
        "data": {"synthetic": True, "tasks": int(len(tasks)), "weeks": cfg.weeks,
                 "business_days": cfg.n_days, "agents": len(roster.agents),
                 "supervisors": len(roster.supervisors), "workload_seed": cfg.seed,
                 "simulation_seed": SIM_SEED},
        "deployed_strategy": deployed,
        "strategy_comparison": table.to_dict(orient="records"),
        "overall": overall,
        "headline": best,
        "agents": akpi.to_dict(orient="records"),
        "performance_reviews": reviews,
        "recommendation_counts": dict(sorted(Counter(r["action"] for r in reviews).items())),
        "capacity_plan": cap,
        "raci": raci.to_dict(orient="records"),
        "audit": {"events": len(res.audit.events), "by_type": audit_counts},
    }
    write_summary(rep / "summary.json", summary)
    write_report(rep / "workforce_report.md", summary, table, akpi, raci_md)
    (rep / "raci_matrix.md").write_text("# RACI matrix\n\n" + raci_md, encoding="utf-8")
    table.to_csv(rep / "strategy_comparison.csv", index=False)
    akpi.to_csv(rep / "agent_kpis.csv", index=False)
    res.audit.to_csv(rep / "audit_log.csv")
    res.audit.to_csv(rep / "audit_log_sample.csv", limit=200)

    if verbose:
        print(f"Simulated {len(tasks):,} tasks x {len(STRATEGIES)} strategies; deployed = {deployed}")
        for r in table.itertuples():
            print(f"  {r.strategy:<12} cost/task ${r.cost_per_task:6.2f}  FPQ {r.first_pass_quality:.1%}"
                  f"  SLA {r.sla_attainment:.1%}  incidents {r.incidents:4d}  net ${r.net_value:,.0f}")
        print(f"Reviews: {summary['recommendation_counts']}")
        print(f"Capacity next month: {cap['hours_needed']} h needed / {cap['hours_available']} h available")
        print(f"Wrote {rep / 'workforce_report.md'}, {rep / 'summary.json'} and 4 charts in {img}")
    return summary
