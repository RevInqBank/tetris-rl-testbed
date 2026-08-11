"""Generate web/tables.js from engine/tables.py.

The constant tables must exist in exactly one authored place. This script
emits the JS mirror so nobody hand-copies a coordinate table and gets one
cell wrong -- a class of bug that shows up only as a rare parity mismatch.

    python3 engine/gen_tables_js.py           # write web/tables.js
    python3 engine/gen_tables_js.py --check   # exit 1 if it is stale

`--check` is what parity.py runs, so a stale mirror fails the test suite
instead of silently drifting.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tables as Tb  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(HERE, "..", "web", "tables.js"))

HEADER = """/**
 * GENERATED FILE -- DO NOT EDIT.
 *
 * Mirror of engine/tables.py, produced by engine/gen_tables_js.py.
 * Edit the Python file and regenerate:
 *
 *     python3 engine/gen_tables_js.py
 *
 * engine/parity.py --tests fails if this file is stale.
 */

"""


def js(value, indent=0):
    """Render a Python constant as a JS literal.

    Tuples become arrays, and dicts keyed by (from, to) rotation pairs become
    objects keyed by "from,to" -- the same key format engine.js already uses
    for its kick lookups.
    """
    pad = "  " * indent
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return "'%s'" % value
    if isinstance(value, dict):
        items = []
        for k, v in value.items():
            key = "'%s'" % ",".join(str(i) for i in k) if isinstance(k, tuple) \
                else "'%s'" % k
            items.append("%s  %s: %s" % (pad, key, js(v, indent + 1)))
        return "{\n" + ",\n".join(items) + "\n" + pad + "}"
    if isinstance(value, (tuple, list)):
        # Keep flat scalar rows on one line; nest deeper structures.
        # Recurse rather than str() -- bool is a subclass of int, so str(True)
        # would emit Python's `True` into JS. That produced a ReferenceError the
        # first time a tuple of bools was exported (DIFFICULTY_HOLD_ENABLED).
        if all(isinstance(v, (int, float, str)) or v is None for v in value):
            return "[" + ", ".join(js(v) for v in value) + "]"
        if all(isinstance(v, (tuple, list))
               and all(isinstance(w, (int, float, str)) or w is None for w in v)
               for v in value):
            return "[" + ", ".join(js(v) for v in value) + "]"
        inner = ",\n".join("%s  %s" % (pad, js(v, indent + 1)) for v in value)
        return "[\n" + inner + "\n" + pad + "]"
    raise TypeError("cannot render %r (%s)" % (value, type(value)))


#: Names exported to JS, in emission order. Anything not listed here stays
#: Python-only on purpose (e.g. the _derive helper).
EXPORTS = [
    # geometry
    "W", "VISIBLE_ROWS", "BUFFER_ROWS", "ROWS", "BOTTOM_ROW", "FULL_ROW",
    # pieces
    "PIECE_NAMES", "BOX_SIZE", "PIECE_CELLS", "UNIQUE_ROTS",
    # spawn
    "SPAWN_Y", "SPAWN_X", "SPAWN_ROT",
    # scoring / gravity
    "SCORE_TABLE", "SOFT_DROP_POINTS_PER_CELL", "HARD_DROP_POINTS_PER_CELL",
    "LINES_PER_LEVEL", "GRAVITY_L1_L10", "GRAVITY_TAIL", "GRAVITY_MIN",
    "LOCK_DELAY_MS", "LOCK_RESET_LIMIT",
    "B2B_LINES", "B2B_MULT_NUM", "B2B_MULT_DEN", "COMBO_BONUS_PER_STEP",
    # difficulty
    "DIFFICULTY_NORMAL", "DIFFICULTY_HARD", "DIFFICULTY_EXTREME",
    "DIFFICULTY_NAMES", "DIFFICULTY_NEXT_VISIBLE", "DIFFICULTY_HOLD_ENABLED",
    "DIFFICULTY_DEFAULT",
    # kicks
    "KICKS_JLSTZ", "KICKS_I", "KICKS_NONE",
    # derived
    "MIN_DX", "MAX_DX", "MIN_DY", "MAX_DY", "BOTTOM_PROFILE", "X_RANGE",
    "PLACEMENT_COUNT", "MAX_PIECE_VEXTENT", "GUARD_ROWS",
    # hashing / rng
    "FNV_OFFSET_32", "FNV_PRIME_32", "MASK32",
    "XORSHIFT_FALLBACK_STATE", "BAG_SIZE", "QUEUE_MIN", "NEXT_VISIBLE",
]

#: Piece index constants, emitted individually so JS reads like Python.
PIECE_CONSTS = ["I", "O", "T", "S", "Z", "J", "L"]


def render() -> str:
    parts = [HEADER]
    for name in EXPORTS:
        value = getattr(Tb, name)
        parts.append("export const %s = %s;\n" % (name, js(value)))
    parts.append("\n// piece index constants\n")
    for name in PIECE_CONSTS:
        parts.append("export const %s = %d;\n" % (name, getattr(Tb, name)))
    parts.append("""
/** KICKS[piece] -> kick table, or null for O (which never needs one). */
export const KICKS = [KICKS_I, null, KICKS_JLSTZ, KICKS_JLSTZ, KICKS_JLSTZ,
                      KICKS_JLSTZ, KICKS_JLSTZ];
""")
    return "".join(parts)


def main(argv):
    text = render()
    check = "--check" in argv
    if check:
        if not os.path.exists(OUT):
            print("STALE: %s does not exist" % OUT)
            return 1
        with open(OUT) as f:
            if f.read() != text:
                print("STALE: %s differs from engine/tables.py -- run "
                      "`python3 engine/gen_tables_js.py`" % OUT)
                return 1
        print("ok   web/tables.js is in sync with engine/tables.py")
        return 0
    with open(OUT, "w") as f:
        f.write(text)
    print("wrote %s (%d bytes)" % (OUT, len(text)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
