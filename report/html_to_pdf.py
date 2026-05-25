"""Render HTML files to PDF using headless Chromium via Playwright."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright


async def html_to_pdf(html_path: Path, pdf_path: Path) -> None:
    """Render an exported notebook HTML to PDF, ensuring MathJax has rendered.

    nbconvert HTML embeds MathJax which typesets `$...$` and `$$...$$` blocks
    asynchronously after page load. networkidle alone is not enough — we
    explicitly wait for the MathJax typeset promise to resolve, then sleep a
    grace period to let the DOM settle before printing.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 1024})
        await page.goto(html_path.absolute().as_uri(), wait_until="networkidle")
        # Wait for MathJax 3 (injected by patch_mathjax.py) to finish typesetting.
        # The pageReady hook sets window._mathjax_ready = true once the default
        # page typeset has resolved. Then we explicitly typeset once more in
        # case dynamic content (Styler, Markdown displays) appeared after.
        try:
            await page.wait_for_function(
                "window._mathjax_ready === true || (window.MathJax && window.MathJax.typesetPromise)",
                timeout=30000,
            )
            await page.evaluate(
                """() => new Promise((resolve) => {
                    const mj = window.MathJax;
                    if (mj && mj.typesetPromise) return mj.typesetPromise().then(resolve);
                    resolve();
                })"""
            )
            rendered = await page.evaluate(
                "document.querySelectorAll('mjx-container').length"
            )
            print(f"  MathJax rendered nodes: {rendered}")
        except Exception as e:
            print(f"  (MathJax wait skipped: {e.__class__.__name__})")
        await page.wait_for_timeout(1500)
        await page.pdf(
            path=str(pdf_path),
            format="A4",
            margin={"top": "20mm", "right": "15mm", "bottom": "20mm", "left": "15mm"},
            print_background=True,
            prefer_css_page_size=False,
        )
        await browser.close()
    print(f"  {pdf_path}  ({pdf_path.stat().st_size / 1024:.0f} KB)")


async def main(paths: list[str]) -> None:
    for html in paths:
        h = Path(html)
        pdf = h.with_suffix(".pdf")
        print(f"Rendering {h} -> {pdf}")
        await html_to_pdf(h, pdf)


if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    asyncio.run(main(sys.argv[1:]))
