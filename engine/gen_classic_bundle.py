"""Generate web/engine.classic.js from web/tables.js + web/engine.js.

Why this exists
---------------
The engine source is an ES module, per the lead's ruling after the deployment
target became a served URL. But a classic script still has one advantage: it
works from ``file://``, where module loading is blocked by CORS. Rather than
degrade the source to classic script (which would break the generated-tables
structure), this emits a single-file classic build alongside it.

    <script src="engine.classic.js"></script>     <!-- window.TetrisEngine -->

The bundle is a GENERATED ARTIFACT. Never edit web/engine.classic.js; edit the
ES modules and regenerate:

    python3 engine/gen_classic_bundle.py
    python3 engine/gen_classic_bundle.py --check   # exit 1 if stale

How it works
------------
Both inputs are concatenated inside one IIFE, with module syntax rewritten:

  * ``import { a, b } from './tables.js'`` is dropped -- after concatenation the
    names are already in scope. ``import { O as PIECE_O }`` becomes a local
    alias assignment, since renaming imports have no other way to survive.
  * ``export { a, b } from './tables.js'`` is dropped for the same reason, but
    the names are recorded so they land on the exported object.
  * A leading ``export `` on a declaration is stripped.

Every exported name is then attached to ``window.TetrisEngine``. The name list
is derived from the source, not maintained by hand, so a new export appears in
the bundle automatically.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.normpath(os.path.join(HERE, "..", "web"))
TABLES = os.path.join(WEB, "tables.js")
ENGINE = os.path.join(WEB, "engine.js")
OUT = os.path.join(WEB, "engine.classic.js")

HEADER = """/**
 * GENERATED FILE -- DO NOT EDIT.
 *
 * Single-file classic-script build of web/tables.js + web/engine.js, produced
 * by engine/gen_classic_bundle.py. Exists so the app also opens from file://,
 * where ES module loading is blocked by CORS.
 *
 * Edit the ES modules and regenerate:
 *     python3 engine/gen_classic_bundle.py
 *
 * Browser : window.TetrisEngine
 * node    : require('./engine.classic.js')
 *
 * The ES modules remain the source of truth. If this file and engine.js ever
 * disagree, this one is stale -- regenerate it.
 */
(function (global) {
  'use strict';

"""

FOOTER_TEMPLATE = """
  var TetrisEngine = {
%(entries)s
  };

  global.TetrisEngine = TetrisEngine;
  if (typeof module !== 'undefined' && module.exports) module.exports = TetrisEngine;
})(typeof window !== 'undefined' ? window : globalThis);
"""

#: `export { a, b, c as d } from './x.js'` / `import { ... } from './x.js'`
_BRACED = re.compile(
    r"^(?P<kind>export|import)\s*\{(?P<names>[^}]*)\}\s*from\s*'[^']*';?\s*$",
    re.MULTILINE | re.DOTALL)

#: A leading `export ` on a declaration (const/let/var/function/class).
_EXPORT_DECL = re.compile(
    r"^export\s+(?=(?:const|let|var|function|class|async)\b)", re.MULTILINE)

def _split_specifiers(text: str):
    """Yield (source_name, local_name) from a braced import/export clause."""
    for raw in text.split(","):
        part = raw.strip()
        if not part:
            continue
        if " as " in part:
            src, local = [p.strip() for p in part.split(" as ", 1)]
        else:
            src = local = part
        yield src, local


def transform(source: str) -> tuple:
    """Rewrite one module. Returns ``(code, exported_names)``."""
    exported = []
    aliases = []

    def handle_braced(match):
        kind = match.group("kind")
        for src, local in _split_specifiers(match.group("names")):
            if kind == "export":
                # Re-exported from another module; after concatenation the name
                # is already in scope, so only record it.
                exported.append(local)
            elif src != local:
                # A renaming import has no other way to survive concatenation.
                aliases.append((local, src))
        return ""

    code = _BRACED.sub(handle_braced, source)

    # Collect exported declaration names before stripping the keyword.
    for match in re.finditer(
            r"^export\s+(?:async\s+)?(?:const|let|var|function|class)\s+"
            r"(?P<name>[A-Za-z_$][\w$]*)", code, re.MULTILINE):
        exported.append(match.group("name"))

    code = _EXPORT_DECL.sub("", code)

    if aliases:
        lines = "\n".join("  var %s = %s;" % (a, b) for a, b in aliases)
        code = lines + "\n" + code

    return code, exported


def render() -> str:
    with open(TABLES) as f:
        tables_src = f.read()
    with open(ENGINE) as f:
        engine_src = f.read()

    tables_code, tables_names = transform(tables_src)
    engine_code, engine_names = transform(engine_src)

    names = []
    for n in tables_names + engine_names:
        if n not in names:
            names.append(n)
    if not names:
        raise AssertionError("no exports found -- the transform is broken")

    entries = ",\n".join("    %s: %s" % (n, n) for n in sorted(names))

    body = (HEADER
            + "  // ---- web/tables.js ----\n" + tables_code
            + "\n  // ---- web/engine.js ----\n" + engine_code
            + FOOTER_TEMPLATE % {"entries": entries})

    if "export " in body or re.search(r"^\s*import\s", body, re.MULTILINE):
        leftover = [ln for ln in body.splitlines()
                    if ln.strip().startswith(("export ", "import "))]
        raise AssertionError("module syntax survived the transform: %r"
                             % leftover[:5])
    return body


def main(argv):
    text = render()
    if "--check" in argv:
        if not os.path.exists(OUT):
            print("STALE: %s does not exist" % OUT)
            return 1
        with open(OUT) as f:
            if f.read() != text:
                print("STALE: %s differs -- run "
                      "`python3 engine/gen_classic_bundle.py`" % OUT)
                return 1
        print("ok   web/engine.classic.js is in sync with the ES modules")
        return 0
    with open(OUT, "w") as f:
        f.write(text)
    print("wrote %s (%d bytes)" % (OUT, len(text)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
