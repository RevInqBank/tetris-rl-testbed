#!/usr/bin/env python3
"""Build web/standalone.html -- the whole app in one file, zero network requests.

WHY THIS EXISTS
    The lab server serves the app fine, but the campus firewall blocks port
    8080, so the user cannot open it from their laptop. Every computation in
    this app runs in the browser, so no server is actually needed: inline
    everything and the file works from file:// or any static host.

WHY IT IS GENERATED
    The four UI files are ES modules and cannot simply be concatenated:
      * `import` / `export` are syntax errors in a classic <script>
      * inline `<script type="module">` still cannot resolve relative imports
        without fetch, which is exactly what we must avoid
      * policies.js and arena.js BOTH declare top-level COLS / ROWS / VIS, so
        pasting them into one scope throws "Identifier already declared"
    So each module is wrapped in its own IIFE that returns its exports, and
    imports become arguments. That preserves module scoping without a bundler
    dependency.

    Hand-merging would drift the moment any source file changes -- the same
    failure this project hit repeatedly today. Regenerate instead:

        python3 web/gen_standalone.py

SOURCE OF TRUTH
    The ES modules in web/ remain authoritative. index.html (the served build)
    is untouched by this script.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent
ROOT = WEB.parent
OUT = WEB / "standalone.html"

TITLE = "테트리스 강화학습 실험대"

# module file -> (variable name, [(param name, source module var)])
MODULES = [
    ("policies.js", "P", [("E", "__ENGINE")]),
    ("arena.js", "A", [("E", "__ENGINE"), ("P", "P")]),
    ("ui.js", None, [("E", "__ENGINE"), ("P", "P"), ("__A", "A")]),
]

IMPORT_RE = re.compile(r"^\s*import\s+.*?;\s*$", re.MULTILINE | re.DOTALL)


def collect_exports(src: str) -> tuple[list[str], set[str]]:
    """Names a module exports, plus which of them are REASSIGNABLE.

    The second set matters more than it looks. ES module exports are live
    bindings: `export let EVAL_SUMMARY = null` that is assigned later is
    visible to importers as the new value. An IIFE returning a plain object
    captures the value once, so a later assignment is invisible to consumers.

    That is not hypothetical -- it silently emptied the LEARN comparison table
    in the first build of this file: EVAL_SUMMARY is populated during
    loadWeights(), long after the module body finishes, so importers kept
    seeing null. No error, no console warning, just a missing panel.

    So `let`/`var` exports are emitted as getters to preserve live binding.
    `const`, functions and classes cannot be reassigned and are returned
    directly. Mutated-in-place objects (WEIGHTS, LOAD_ERRORS) are const and
    work either way.

    Handles multi-declarator `export const ROWS = 22, VIS = 20, COLS = 10;`.
    """
    names: list[str] = []
    live: set[str] = set()
    for m in re.finditer(r"^export\s+(async\s+)?(function|class)\s+([A-Za-z_$][\w$]*)",
                         src, re.MULTILINE):
        names.append(m.group(3))
    for m in re.finditer(r"^export\s+(const|let|var)\s+(.+?)(?:;|$)", src, re.MULTILINE):
        kind = m.group(1)
        decl = m.group(2)
        # split top-level commas only (depth 0 w.r.t. brackets)
        depth, cur, parts = 0, "", []
        for ch in decl:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append(cur); cur = ""
            else:
                cur += ch
        parts.append(cur)
        for p in parts:
            nm = re.match(r"\s*([A-Za-z_$][\w$]*)", p)
            if nm:
                names.append(nm.group(1))
                if kind in ("let", "var"):
                    live.add(nm.group(1))
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n); out.append(n)
    return out, live


def strip_module_syntax(src: str) -> str:
    """Remove import statements and the `export` keyword."""
    src = IMPORT_RE.sub("", src)
    src = re.sub(r"^export\s+(?=(async\s+)?(function|class|const|let|var)\b)",
                 "", src, flags=re.MULTILINE)
    # `export { a, b };` re-export blocks (none today, but fail loudly if added)
    if re.search(r"^export\s*\{", src, re.MULTILINE):
        sys.exit("ERROR: `export { ... }` block found; extend the generator.")
    if re.search(r"\bexport\s+default\b", src):
        sys.exit("ERROR: `export default` found; extend the generator.")
    # import.meta is a SyntaxError outside a module. Only used to locate
    # weights/, which the inline bundle makes unnecessary.
    src = src.replace("import.meta.url", "location.href")
    if "import.meta" in src:
        sys.exit("ERROR: leftover import.meta; the classic build would not parse.")
    return src


def read(p: Path) -> str:
    if not p.exists():
        sys.exit(f"ERROR: missing required input {p}")
    return p.read_text(encoding="utf-8")


def main() -> None:
    css = read(WEB / "style.css")
    engine_classic = read(WEB / "engine.classic.js")
    weights_bundle = read(ROOT / "weights" / "weights_bundle.js")
    index_html = read(WEB / "index.html")

    # --- body markup, lifted from the served build so the two cannot drift ---
    m = re.search(r"<body[^>]*>(.*?)<script", index_html, re.DOTALL)
    if not m:
        sys.exit("ERROR: could not locate <body> markup in index.html")
    body = m.group(1).strip()
    if "<script" in body:
        sys.exit("ERROR: body slice still contains a <script>; check index.html")

    # --- sanity: the bundle must actually define the weights ---
    for g in ("TETRIS_WEIGHTS", "TETRIS_WEIGHTS_INDEX", "TETRIS_EVAL_SUMMARY"):
        if f"window.{g}" not in weights_bundle:
            sys.exit(f"ERROR: weights_bundle.js does not define {g}. "
                     f"Ask rl to regenerate it (rl/artifacts.py).")

    parts: list[str] = []
    for fname, var, params in MODULES:
        src = read(WEB / fname)
        exports, live = collect_exports(src)
        stripped = strip_module_syntax(src)
        args = ", ".join(p for p, _ in params)
        vals = ", ".join(v for _, v in params)
        if var:
            fields = []
            for n in exports:
                # live binding for reassignable exports (see collect_exports)
                fields.append(f"get {n}() {{ return {n}; }}" if n in live else n)
            ret = "return { " + ", ".join(fields) + " };"
            parts.append(
                f"/* ==== {fname} ==== */\n"
                f"var {var} = (function ({args}) {{\n{stripped}\n{ret}\n}})({vals});"
            )
        else:
            # ui.js is the entry point: it imports named bindings from arena
            parts.append(
                f"/* ==== {fname} ==== */\n"
                f"(function ({args}) {{\n"
                f"const {{ Arena, SPEEDS, drawBoard, drawPiecePreview, PIECE_COLORS }} = __A;\n"
                f"{stripped}\n}})({vals});"
            )
    app_js = "\n\n".join(parts)

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>{TITLE}</title>
<!-- GENERATED by web/gen_standalone.py -- do not edit by hand.
     Single-file build: no fetch, no import, no CDN, no external font or image.
     Regenerate with:  python3 web/gen_standalone.py
     The ES modules in web/ remain the source of truth. -->
<style>
{css}
</style>
</head>
<body>

{body}

<script>
{weights_bundle}
</script>

<script>
{engine_classic}
</script>

<script>
(function () {{
"use strict";
var __ENGINE = window.TetrisEngine;
if (!__ENGINE) {{ document.body.innerHTML = '<p style="padding:2rem">엔진 로드 실패</p>'; return; }}

{app_js}
}})();
</script>
</body>
</html>
"""

    OUT.write_text(html, encoding="utf-8")
    size = OUT.stat().st_size
    print(f"wrote {OUT}  ({size:,} bytes = {size/1024/1024:.2f} MB)")

    # --- post-checks the file must satisfy to be worth shipping ---
    text = OUT.read_text(encoding="utf-8")
    bad = []
    for pat, why in [
        (r"\bfetch\s*\(", "fetch( call"),
        (r"^\s*import\s+", "import statement"),
        (r"\bimport\.meta\b", "import.meta"),
        (r'src\s*=\s*["\']https?://', "external script src"),
        (r'href\s*=\s*["\']https?://', "external href"),
        (r"@import\s+url", "css @import"),
    ]:
        for mm in re.finditer(pat, text, re.MULTILINE):
            line = text[:mm.start()].count("\n") + 1
            bad.append(f"  line {line}: {why}")
    # fetch appears inside policies.js's server path; it must be unreachable,
    # not absent, so report it as information rather than failure.
    if bad:
        print("NOTE: patterns that would need network (verify they are unreachable):")
        for b in bad[:10]:
            print(b)
    if size > 16 * 1024 * 1024:
        sys.exit(f"ERROR: {size} bytes exceeds the 16MB limit")


if __name__ == "__main__":
    main()
