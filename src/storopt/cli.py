"""
storopt CLI — two commands: run-day and run-backtest.

Usage
-----
  storopt run-day 2025-01-15 --config configs/horns_rev1.yaml
  storopt run-day 2025-01-15 --config configs/horns_rev1.yaml --scenario naive
  storopt run-backtest --start 2025-01-01 --end 2025-03-31 \
      --config configs/horns_rev1.yaml --output-dir ./results/q1_2025
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(name="storopt", add_completion=False, pretty_exceptions_short=True)


@app.command("run-day")
def cmd_run_day(
    target_date: str = typer.Argument(..., help="Target trading date, format YYYY-MM-DD"),
    config: str = typer.Option("configs/default.yaml", "--config", "-c", help="Path to case YAML config"),
    scenario: Optional[str] = typer.Option(None, "--scenario", "-s", help="Override scenario method (knn|naive)"),
    n_scenarios: Optional[int] = typer.Option(None, "--n-scenarios", "-n", help="Override n_scenarios"),
    solver: Optional[str] = typer.Option(None, "--solver", help="Override solver (highs|gurobi)"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write result JSON to this path"),
) -> None:
    """Run the dispatch optimizer for a single trading day."""
    from dotenv import load_dotenv

    load_dotenv()

    from storopt import run_day
    from storopt.config.loader import load_config

    td = _parse_date(target_date)
    cfg = load_config(config)

    typer.echo(f"Running {td} | scenario={scenario or cfg.scenarios.method} | solver={solver or cfg.solver.name}")

    result = run_day(td, cfg, scenario_method=scenario, n_scenarios=n_scenarios, solver=solver)

    typer.echo(f"Status:          {result.solve_status}")
    typer.echo(f"Expected profit: €{result.expected_profit:,.2f}")
    typer.echo(f"Solve time:      {result.solve_time_s:.1f}s")
    typer.echo(f"Scenarios:       {result.n_scenarios}")

    if output:
        out_path = Path(output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        summary = {
            "target_date": td.isoformat(),
            "solve_status": result.solve_status,
            "expected_profit_eur": result.expected_profit,
            "solve_time_s": result.solve_time_s,
            "scenario_profits": result.scenario_profits.tolist(),
            "probabilities": result.probabilities.tolist(),
            "extras": {k: v.tolist() if hasattr(v, "tolist") else v for k, v in result.extras.items()},
        }
        out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        typer.echo(f"Result written to {out_path}")


@app.command("run-backtest")
def cmd_run_backtest(
    start: str = typer.Option(..., "--start", help="Backtest start date YYYY-MM-DD"),
    end: str = typer.Option(..., "--end", help="Backtest end date YYYY-MM-DD"),
    config: str = typer.Option("configs/default.yaml", "--config", "-c", help="Path to case YAML config"),
    scenario: Optional[str] = typer.Option(None, "--scenario", "-s", help="Override scenario method"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir", "-o", help="Directory for output parquet/CSV"),
) -> None:
    """Run a rolling daily backtest over a date range."""
    from dotenv import load_dotenv

    load_dotenv()

    from storopt import run_backtest
    from storopt.config.loader import load_config

    s = _parse_date(start)
    e = _parse_date(end)
    cfg = load_config(config)
    n_days = (e - s).days + 1

    typer.echo(f"Backtest {s} → {e} ({n_days} days) | scenario={scenario or cfg.scenarios.method}")

    summary = run_backtest(s, e, cfg, scenario_method=scenario, output_dir=output_dir)

    successful = (summary["solve_status"].isin(["optimal", "feasible"])).sum()
    total_profit = summary["expected_profit_eur"].sum()
    typer.echo(f"Days completed:  {len(summary)}")
    typer.echo(f"Successful:      {successful}")
    typer.echo(f"Total exp. profit: €{total_profit:,.2f}")
    if output_dir:
        typer.echo(f"Results written to {output_dir}/")


def _parse_date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        typer.echo(f"Invalid date: {s!r}. Expected YYYY-MM-DD.", err=True)
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
