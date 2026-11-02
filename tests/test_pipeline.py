import json
import shutil

from aiworkforce.pipeline import run
from conftest import ROOT


def test_pipeline_end_to_end(tmp_path):
    (tmp_path / "examples").mkdir()
    for f in ("roster.yml", "workload.yml"):  # tasks.csv is missing -> must be generated
        shutil.copy(ROOT / "examples" / f, tmp_path / "examples" / f)
    summary = run(tmp_path, verbose=False)
    assert (tmp_path / "examples" / "tasks.csv").exists()
    for f in ("workforce_report.md", "summary.json", "raci_matrix.md", "audit_log_sample.csv"):
        assert (tmp_path / "reports" / f).exists()
    for img in ("strategy_comparison", "agent_scorecard", "weekly_throughput", "review_load"):
        assert (tmp_path / "docs" / "img" / f"{img}.png").stat().st_size > 10_000
    saved = json.loads((tmp_path / "reports" / "summary.json").read_text())
    assert saved["deployed_strategy"] == summary["deployed_strategy"]
    best = max(saved["strategy_comparison"], key=lambda r: r["net_value"])
    assert best["strategy"] == saved["deployed_strategy"]
    assert len(saved["performance_reviews"]) == saved["data"]["agents"]
    assert all(r["evidence"] for r in saved["performance_reviews"])
    report = (tmp_path / "reports" / "workforce_report.md").read_text()
    assert "## Routing strategy comparison" in report and "## RACI matrix" in report
