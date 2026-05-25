"""
Builds tests/stress_test_report.ipynb with all sanity_cases + edge_cases + the
new stochastic_stress tests rendered with dispatch tables and per-test plots.

Mirrors the storopt_advisor_report.ipynb format: each case has description,
expected behaviour, real-world context, check verdicts, and hourly tables.

Generates one self-contained notebook that, when executed, runs every test
and produces verdicts + plots.
"""
from __future__ import annotations

from pathlib import Path

import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "tests" / "stress_test_report.ipynb"


def md(text: str):
    return new_markdown_cell(text)


def code(src: str):
    return new_code_cell(src)


def build() -> None:
    cells: list = []

    cells.append(md(
        "# Optimizer Stress Test Report\n"
        "## Deterministic Sanity + Edge + Stochastic Stress\n\n"
        "| Section | Cases | Purpose |\n"
        "|---|---|---|\n"
        "| 1. Sanity cases | 5 | Hand-crafted single-scenario price/gen profiles with analytically known optimal decisions |\n"
        "| 2. Edge cases | 8 | Boundary conditions: zero / negative prices, break-even thresholds, SOC limits, alternating extremes |\n"
        "| 3. **Stochastic stress (new)** | 6 | Multi-scenario tests for non-anticipativity, recourse feasibility, true VSS, CVaR, cycle cap, DA-ID basis |\n\n"
        "BESS configuration: **40 MW / 80 MWh** (Horns Rev 1, 25 % plant capacity).\n\n"
        "RTE = 0.9025, deg = 10 €/MWh, SOC range = [8, 72] MWh (init = 40 MWh).\n\n"
        "All cases must PASS to validate that the MILP responds correctly to both deterministic "
        "and stochastic inputs across edge regimes.\n\n"
        "---\n"
    ))

    # ── Section 1: sanity cases
    cells.append(md('<a id="sanity"></a>\n## 1. Sanity Cases (Deterministic, Single Scenario)\n\n'
                    'Runs `sanity_cases.run_and_report()` and renders the verdict block + per-hour table.'))

    cells.append(code("""\
import os, sys
from pathlib import Path

# Always resolve repo root robustly so the notebook runs whether kernel cwd is
# the repo root or the tests/ folder.
def _repo_root():
    for p in [Path.cwd(), *Path.cwd().parents]:
        if (p / 'tests' / 'sanity_cases.py').exists() and (p / 'src').exists():
            return p
    raise RuntimeError(f'Cannot resolve repo root from {Path.cwd()}')

REPO = _repo_root()
os.chdir(REPO)                                             # tests load config via relative path
for p in (REPO / 'src', REPO / 'tests'):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

import numpy as np
import matplotlib.pyplot as plt
from sanity_cases import run_and_report as run_sanity
print("Running sanity cases...")
sanity_md = run_sanity(output_path=None)
print(f"Done ({sanity_md.count('PASS')} PASS, {sanity_md.count('FAIL')} FAIL markers in output)")
"""))

    cells.append(code("""\
from IPython.display import Markdown
Markdown(sanity_md)
"""))

    # ── Section 2: edge cases
    cells.append(md('<a id="edge"></a>\n## 2. Edge Cases (Deterministic, Single Scenario)'))

    cells.append(code("""\
from edge_cases import run_and_report as run_edge
print("Running edge cases...")
edge_md = run_edge(output_path=None)
print(f"Done ({edge_md.count('PASS')} PASS, {edge_md.count('FAIL')} FAIL markers in output)")
"""))

    cells.append(code("""\
Markdown(edge_md)
"""))

    # ── Section 3: stochastic stress
    cells.append(md(
        '<a id="stress"></a>\n## 3. Stochastic Stress Tests (Multi-Scenario)\n\n'
        'These tests construct hand-crafted multi-scenario `ScenarioBundle`s and probe behaviours that '
        'cannot be exercised with single-scenario inputs:\n\n'
        '- **Stress 1** Non-anticipativity — opposite intraday price patterns across scenarios; verify '
        '`q_da` stays a single vector while recourse adapts per scenario.\n'
        '- **Stress 2** Generation spread — calm and storm days in one bundle; verify the EEV q_id bound '
        'relaxation fix prevents infeasibility.\n'
        '- **Stress 3** Analytical VSS — mirror-symmetric scenarios where VSS ≈ 0 by construction; '
        'verify the VSS computation does not over-report.\n'
        '- **Stress 4** CVaR — asymmetric tail with 1 loss day + 4 normal days; verify CVaR weight '
        'raises worst-case profit at the cost of mean.\n'
        '- **Stress 5** Cycle cap — set 0.5 cycles/day and verify throughput is capped; profit ≤ uncapped.\n'
        '- **Stress 6** DA-ID basis trade — flat DA, variable ID; verify model captures the basis via '
        'naked q_da / q_id opposite positions.\n'
    ))

    cells.append(code("""\
from stochastic_stress import CASES, StressResult

results = []
for fn in CASES:
    print(f"  {fn.__name__}...", end=" ", flush=True)
    r = fn()
    results.append(r)
    print("PASS" if r.passed else "FAIL")
print(f"\\nOverall: {sum(r.passed for r in results)}/{len(results)} PASS")
"""))

    cells.append(code("""\
def render_case(r: StressResult, idx: int) -> str:
    # r.name already starts with 'Stress N — ...', so render it as-is rather
    # than prepending another 'Stress {idx} —' prefix.
    lines = [f"### {r.name}\\n",
             f"**Description:** {r.description}\\n",
             f"**Expected:** {r.expected}\\n",
             f"**Real-world context:** {r.real_world}\\n",
             "**Checks:**\\n"]
    for label, ok, detail in r.checks:
        icon = "✓" if ok else "✗"
        lines.append(f"- {icon} {label} — {detail}")
    verdict = "**PASS**" if r.passed else "**FAIL**"
    lines.append(f"\\n{verdict}\\n\\n---\\n")
    return "\\n".join(lines)

stress_md = "\\n".join(render_case(r, i + 1) for i, r in enumerate(results))
Markdown(stress_md)
"""))

    cells.append(md("### Aggregate stress test bundle visualisations"))

    cells.append(code("""\
fig, axes = plt.subplots(2, 3, figsize=(15, 9), tight_layout=True)
for ax, r, idx in zip(axes.flat, results, range(1, len(results) + 1)):
    bundle = r.bundle
    hours = np.arange(bundle.da_prices.shape[1])
    for s in range(bundle.n_scenarios):
        ax.plot(hours, bundle.da_prices[s], lw=1.2, alpha=0.8, label=f's{s} DA' if s < 3 else None)
        if not np.allclose(bundle.id_prices[s], bundle.da_prices[s]):
            ax.plot(hours, bundle.id_prices[s], lw=1.0, alpha=0.6, ls='--', label=f's{s} ID' if s < 3 else None)
    ax.set_title(r.name[:55] + ("..." if len(r.name) > 55 else ""), fontsize=9)
    ax.set_xlabel('Hour')
    ax.set_ylabel('€/MWh')
    ax.legend(fontsize=7, loc='best')
fig.suptitle('Scenario bundle price profiles per stress test', y=1.01, fontsize=12)
fig
"""))

    cells.append(md(
        "## Overall Verdict\n\n"
        "If all three sections (5 sanity + 8 edge + 6 stochastic stress) pass, the MILP is "
        "validated for:\n\n"
        "- Single-scenario deterministic profit maximisation (sanity)\n"
        "- Boundary economic regimes (edge)\n"
        "- Two-stage stochastic structure: non-anticipativity, recourse, VSS/EEV/EVPI "
        "computation, CVaR, cycle cap, and basis arbitrage (stress)\n\n"
        "A failing stress test indicates the stochastic machinery is unreliable for that regime "
        "and the model should not be deployed for that use case without further fixes."
    ))

    nb = new_notebook(cells=cells)
    nb.metadata = {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.11"},
    }
    with open(OUT, "w", encoding="utf-8") as f:
        nbformat.write(nb, f)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    build()
