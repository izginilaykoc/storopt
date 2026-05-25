"""End-to-end: rebuild source notebooks → execute → HTML → MathJax 3 patch → PDF.

This script wraps the full advisor-PDF render pipeline so we can re-export
both deliverables (`multi_day_knn_report` and `stress_test_report`) with one
command after any source/builder change.

Usage:
  python report/render_advisor_pdf.py
"""
from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

REPO = Path(__file__).resolve().parents[1]
VENV_PY = REPO / ".venv" / "Scripts" / "python.exe"
VENV_JUP = REPO / ".venv" / "Scripts" / "jupyter.exe"

if not VENV_PY.exists():
    raise SystemExit(f"venv python not found at {VENV_PY}")


def run(cmd: list[str]) -> None:
    print(f"$ {' '.join(str(c) for c in cmd)}")
    r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr)
        raise SystemExit(f"command failed: {' '.join(str(c) for c in cmd)}")
    for line in (r.stdout + r.stderr).splitlines()[-2:]:
        print(f"  {line}")


NB_KNN = "report/multi_day_knn_report.ipynb"
NB_KNN_EXEC = "report/_executed_multi_day_knn.ipynb"
NB_KNN_HTML = "report/_executed_multi_day_knn.html"

NB_STRESS = "tests/stress_test_report.ipynb"
NB_STRESS_EXEC = "tests/_executed_stress.ipynb"
NB_STRESS_HTML = "tests/_executed_stress.html"


def main() -> int:
    print("=== 1. Build source notebooks ===")
    run([str(VENV_PY), "report/build_knn_report.py"])
    run([str(VENV_PY), "report/build_knn_edge_section.py"])
    run([str(VENV_PY), "tests/build_stress_test_report.py"])

    print("\n=== 2. Execute notebooks ===")
    run([
        str(VENV_JUP), "nbconvert", "--to", "notebook", "--execute", NB_KNN,
        "--output", "_executed_multi_day_knn.ipynb",
        "--ExecutePreprocessor.timeout=300",
        "--ExecutePreprocessor.kernel_name=storopt-venv",
    ])
    run([
        str(VENV_JUP), "nbconvert", "--to", "notebook", "--execute", NB_STRESS,
        "--output", "_executed_stress.ipynb",
        "--ExecutePreprocessor.timeout=600",
        "--ExecutePreprocessor.kernel_name=storopt-venv",
        "--ExecutePreprocessor.allow_errors=True",
    ])

    print("\n=== 3. Convert to HTML (no input) ===")
    run([str(VENV_JUP), "nbconvert", "--to", "html", "--no-input", NB_KNN_EXEC])
    run([str(VENV_JUP), "nbconvert", "--to", "html", "--no-input", NB_STRESS_EXEC])

    print("\n=== 4. Patch HTML to use MathJax 3 ===")
    run([str(VENV_PY), "report/patch_mathjax.py", NB_KNN_HTML, NB_STRESS_HTML])

    print("\n=== 5. Render PDFs (Playwright + MathJax 3 typeset wait) ===")
    run([str(VENV_PY), "report/html_to_pdf.py", NB_KNN_HTML, NB_STRESS_HTML])

    print("\n=== DONE ===")
    for f in [
        REPO / "report" / "_executed_multi_day_knn.pdf",
        REPO / "tests" / "_executed_stress.pdf",
    ]:
        if f.exists():
            print(f"  {f.relative_to(REPO)}  ({f.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
