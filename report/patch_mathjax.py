"""Patch nbconvert HTML to swap its MathJax 2.7 (cdnjs `latest.js`) for a
direct-load MathJax 3 bundle from jsDelivr.

The MathJax 2.7 bootstrap used by nbconvert (cdnjs/latest.js) issues several
chained async loads after page load and frequently leaves typesetting
incomplete inside headless Chromium. MathJax 3 ships a single ES5 bundle with
an explicit `typesetPromise()` we can await deterministically.

Usage: python patch_mathjax.py <html-file> [...]
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

MATHJAX3 = """
<!-- MathJax 3 (replaces nbconvert's MathJax 2.7) -->
<script>
window.MathJax = {
  tex: {
    inlineMath:  [['$', '$'], ['\\\\(', '\\\\)']],
    displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']],
    processEscapes: true,
    processEnvironments: true
  },
  options: { renderActions: { addMenu: [] } },
  startup: {
    pageReady: () => {
      return MathJax.startup.defaultPageReady().then(() => {
        window._mathjax_ready = true;
      });
    }
  }
};
</script>
<script id="MathJax-script" async
        src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"></script>
"""


def patch(html_path: Path) -> None:
    html = html_path.read_text(encoding="utf-8")
    before = html

    # 1. Remove the nbconvert MathJax 2.7 script tag (note: nbconvert closes
    #    the tag with `> </script>`, a space before the closing tag, so we
    #    allow optional whitespace inside.)
    html = re.sub(
        r'<script\b[^>]*?(?:cdnjs|cloudflare)[^>]*?mathjax[^>]*?>\s*</script>',
        '',
        html,
        flags=re.IGNORECASE,
    )
    # 2. Remove any inline MathJax 2 config block
    html = re.sub(
        r'<script\s+type=["\']text/x-mathjax-config["\']\s*>.*?</script>',
        '',
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # 3. Remove HTML comments that bracket the MathJax block (cosmetic only)
    html = re.sub(r'<!--\s*Load mathjax\s*-->\s*', '', html, flags=re.IGNORECASE)
    html = re.sub(r'<!--\s*MathJax configuration\s*-->\s*', '', html, flags=re.IGNORECASE)
    html = re.sub(r'<!--\s*End of mathjax configuration\s*-->\s*', '', html, flags=re.IGNORECASE)

    # 4. Inject MathJax 3 just before </head>
    if '</head>' in html:
        html = html.replace('</head>', MATHJAX3 + '</head>', 1)
    else:
        html = MATHJAX3 + html  # fallback: prepend

    if html == before:
        print(f"  (no MathJax tag found in {html_path}, left unchanged)")
        return
    html_path.write_text(html, encoding="utf-8")
    print(f"  patched {html_path} ({len(html)} bytes)")


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    for p in sys.argv[1:]:
        patch(Path(p))
