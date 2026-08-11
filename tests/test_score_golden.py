"""Golden ABSOLUTE score test, transcribed by hand from docs/spec.md section 5.

Why this file exists (checker's finding that prompted it):

    The parity proof only checks that Python and JS agree WITH EACH OTHER.
    If both drift from the document, parity still passes. Nothing anywhere
    asserted an absolute score value. Task #9 (train for score) is
    uninterpretable if the score definition and the document disagree.

So every expected number below is READ OFF docs/spec.md AND TYPED IN BY HAND.
Nothing here imports SCORE_TABLE, B2B_MULT_NUM, COMBO_BONUS_PER_STEP or any
other engine constant to build an expectation. A typo copied into both the
engine and the engine's own tests still fails here.

Source read: docs/spec.md, section 5 "점수" + "B2B(back-to-back) 와 콤보"
    read at   2026-08-07 19:37:43
    mtime     2026-08-07 19:25:44
If the spec moves, update the SPEC_* tables here IN THE SAME EDIT and record
the new mtime. Do not "fix" a mismatch by reading the engine.

Run:  python3 tests/test_score_golden.py
Owner of the code under test: engine.  This file is checker-owned.
"""

import os
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "engine"))

import engine as E  # noqa: E402

WEB_ENGINE = os.path.join(_ROOT, "web", "engine.js")
JS_RUNNER = os.path.join(_HERE, "_score_golden.mjs")

SPEC_READ_AT = "2026-08-07 19:37:43"
SPEC_MTIME = "2026-08-07 19:25:44"

PASSED = []
FAILED = []
SKIPPED = []


def ok(msg):
    PASSED.append(msg)
    print("  ok   %s" % msg)


def fail(tag, msg):
    FAILED.append((tag, msg))
    print("  FAIL [%s] %s" % (tag, msg))


def skip(tag, msg):
    """A check that could NOT BE RUN. Never conflated with a pass."""
    SKIPPED.append((tag, msg))
    print("  SKIP [%s] %s" % (tag, msg))


# ===========================================================================
# transcribed from docs/spec.md section 5 -- BY HAND
# ===========================================================================

#: "lines_cleared: 1 -> 100, 2 -> 300, 3 -> 500, 4 -> 800"
SPEC_BASE = {0: 0, 1: 100, 2: 300, 3: 500, 4: 800}

#: "soft drop : 내려간 칸당 +1" / "hard drop : 내려간 칸당 +2"
SPEC_SOFT_PER_CELL = 1
SPEC_HARD_PER_CELL = 2

#: "combo_bonus = 50 * combo * level"
SPEC_COMBO_PER_STEP = 50

#: "base = base * 3 // 2" -- integer division, only for a tetris that
#: continues an existing chain
SPEC_B2B_NUM = 3
SPEC_B2B_DEN = 2

#: "어려운 삭제는 tetris(4줄)뿐이다"
SPEC_HARD_CLEAR_LINES = 4


def spec_score(lines, level, b2b_in, combo_in):
    """The spec's pseudocode, retyped as executable Python.

    Returns (score_delta, b2b_out, combo_out, b2b_applied).
    Transcribed line by line from the ``LOCK 시점의 점수 계산`` block.
    """
    if lines == 0:
        return 0, b2b_in, 0, False          # combo breaks, b2b SURVIVES
    base = SPEC_BASE[lines]
    if lines == SPEC_HARD_CLEAR_LINES:
        b2b_applied = b2b_in > 0
        b2b_out = b2b_in + 1
    else:
        b2b_applied = False
        b2b_out = 0                          # 1-3 lines break the chain
    if b2b_applied:
        base = base * SPEC_B2B_NUM // SPEC_B2B_DEN
    combo_bonus = SPEC_COMBO_PER_STEP * combo_in * level
    return base * level + combo_bonus, b2b_out, combo_in + 1, b2b_applied


# ===========================================================================
# 1. the engine's own scoring function against the transcribed one
# ===========================================================================

def engine_score(lines, level, b2b_in, combo_in):
    """Call whatever the engine exposes, without importing its constants."""
    fn = getattr(E, "_score_clear", None) or getattr(E, "score_clear", None)
    if fn is None:
        return None
    return fn(lines, level, b2b_in, combo_in)


def check_table():
    print("\n1. engine scoring vs the hand-transcribed spec table")
    if engine_score(0, 1, 0, 0) is None:
        skip("G1", "engine exposes no _score_clear/score_clear -- cannot test "
                   "the scoring function in isolation")
        return
    bad = 0
    n = 0
    for lines in (0, 1, 2, 3, 4):
        for level in (1, 2, 3, 5, 10, 13, 20, 29, 40):
            for b2b_in in (0, 1, 2, 5):
                for combo_in in (0, 1, 2, 3, 9):
                    want = spec_score(lines, level, b2b_in, combo_in)
                    got = engine_score(lines, level, b2b_in, combo_in)
                    n += 1
                    g = tuple(got)[:4]
                    if g != want:
                        bad += 1
                        if bad <= 5:
                            fail("G1", "lines=%d level=%d b2b_in=%d combo_in=%d"
                                       " -> engine %r, spec %r"
                                 % (lines, level, b2b_in, combo_in, g, want))
    if bad == 0:
        ok("%d (lines x level x b2b x combo) combinations: score_delta, b2b_out,"
           " combo_out and b2b_applied all match the transcribed spec" % n)
    else:
        fail("G1", "%d of %d combinations disagree with the spec" % (bad, n))


# ===========================================================================
# 2. headline absolute values, spelled out
# ===========================================================================

GOLDEN = [
    # (label, [(lines, level), ...], expected TOTAL score_delta)
    ("single at level 1", [(1, 1)], 100),
    ("double at level 1", [(2, 1)], 300),
    ("triple at level 1", [(3, 1)], 500),
    ("tetris at level 1", [(4, 1)], 800),
    ("single at level 5", [(1, 5)], 500),
    ("tetris at level 5", [(4, 5)], 4000),
    # two tetrises back to back: 800 then 800*3//2 = 1200, plus combo 50*1*1=50
    ("two tetrises back to back at level 1", [(4, 1), (4, 1)], 800 + 1250),
    # three in a row: 800 / 1200+50 / 1200+100
    ("three tetrises back to back at level 1",
     [(4, 1), (4, 1), (4, 1)], 800 + 1250 + 1300),
    # a single between two tetrises breaks B2B: the second tetris gets no x1.5
    ("tetris, single, tetris at level 1",
     [(4, 1), (1, 1), (4, 1)], 800 + (100 + 50) + (800 + 100)),
    # an empty placement breaks the combo but NOT the b2b chain
    ("tetris, no clear, tetris at level 1",
     [(4, 1), (0, 1), (4, 1)], 800 + 0 + 1200),
    # combo only starts paying on the SECOND consecutive clear
    ("three singles in a row at level 1",
     [(1, 1), (1, 1), (1, 1)], 100 + 150 + 200),
    ("three singles in a row at level 3",
     [(1, 3), (1, 3), (1, 3)], 300 + (300 + 150) + (300 + 300)),
]


def check_golden():
    print("\n2. golden absolute totals (every number typed from the spec)")
    if engine_score(0, 1, 0, 0) is None:
        skip("G2", "engine exposes no scoring function -- golden totals not run")
        return
    for label, seq, want in GOLDEN:
        b2b, combo, total = 0, 0, 0
        for lines, level in seq:
            d, b2b, combo, _a = tuple(engine_score(lines, level, b2b, combo))[:4]
            total += d
        if total != want:
            fail("G2", "%s: engine total %d, spec says %d" % (label, total, want))
        else:
            ok("%s = %d" % (label, total))


# ===========================================================================
# 3. the same totals produced by really playing the engine (not just the
#    scoring function) -- catches a correct formula wired up wrongly
# ===========================================================================

def rows_from_ascii(lines):
    out = []
    for ln in lines:
        v = 0
        for c, ch in enumerate(ln):
            if ch == "#":
                v |= 1 << c
        out.append(v)
    return tuple(out)


def blank(n):
    return ["." * E.W for _ in range(n)]


def tetris_ready_board(n_wells):
    """A board with `n_wells` stacked 4-row blocks, column 9 empty."""
    return rows_from_ascii(blank(E.ROWS - 4 * n_wells)
                           + ["#########."] * (4 * n_wells))


def check_played():
    print("\n3. the same numbers by actually playing apply_placement")
    # three consecutive tetrises: 12 rows of #########. and vertical I x3
    rows = tetris_ready_board(3)
    s = E.State()
    s.rows = rows
    s.rng, s.queue = E._refill(getattr(E, "seed_state", None)(1)
                               if hasattr(E, "seed_state") else 1, ())
    total = 0
    deltas = []
    for _ in range(3):
        s.current = E.I
        s.rot = 0
        s.x = E.SPAWN_X[E.I]
        s.y = 0
        ps = [p for p in E.legal_placements(s) if p[0] == 1 and p[1] == 7]
        if not ps:
            skip("G3", "could not place a vertical I in column 9 on the "
                       "prepared board -- played-through check not run")
            return
        s, info = E.apply_placement(s, ps[0])
        deltas.append(info["score_delta"])
        total += info["score_delta"]
        if info["lines_cleared"] != 4:
            fail("G3", "expected a tetris, got %d lines" % info["lines_cleared"])
            return
    want = 800 + 1250 + 1300
    if total != want:
        fail("G3", "three played tetrises scored %d (deltas %r); the spec says "
                   "%d (800 + 1250 + 1300)" % (total, deltas, want))
    else:
        ok("three tetrises played through apply_placement: deltas %r, total "
           "%d -- matches the spec" % (deltas, total))

    # state.score must equal the sum of score_delta over the game
    s2 = E.new_game(20260807)
    acc = 0
    for _ in range(400):
        ps = E.legal_placements(s2)
        if not ps:
            break
        best, by = ps[0], ps[0][2]
        for p in ps:
            if p[2] > by:
                by, best = p[2], p
        s2, info = E.apply_placement(s2, best)
        acc += info["score_delta"]
        if s2.game_over:
            break
    if s2.score != acc:
        fail("G3", "state.score=%d but the sum of info['score_delta'] is %d -- "
                   "the agent path adds points somewhere it does not report"
             % (s2.score, acc))
    else:
        ok("over a %d-point game, state.score equals the sum of every "
           "reported score_delta exactly" % acc)


# ===========================================================================
# 4. drop scoring (human path) against the spec's per-cell numbers
# ===========================================================================

def check_drop_points():
    print("\n4. soft/hard drop points per cell (spec section 5)")
    s = E.new_game(7)
    before = s.score
    moved = E.soft_drop(s)
    if not moved:
        skip("G4", "soft_drop refused on a fresh board -- not run")
    elif s.score - before != SPEC_SOFT_PER_CELL:
        fail("G4", "one soft drop scored %d, spec says %d"
             % (s.score - before, SPEC_SOFT_PER_CELL))
    else:
        ok("one soft drop = %d point" % SPEC_SOFT_PER_CELL)

    s = E.new_game(7)
    d = E.drop_distance(s)
    before = s.score
    info = E.hard_drop(s)
    if info is None:
        skip("G4", "hard_drop returned None on a fresh board -- not run")
        return
    gained = s.score - before - info["score_delta"]
    if gained != SPEC_HARD_PER_CELL * d:
        fail("G4", "hard drop over %d cells added %d drop points, spec says "
                   "%d (= %d x %d)"
             % (d, gained, SPEC_HARD_PER_CELL * d, SPEC_HARD_PER_CELL, d))
    else:
        ok("hard drop over %d cells = %d points (%d/cell)"
           % (d, gained, SPEC_HARD_PER_CELL))


# ===========================================================================
# 5. the JS mirror against the SAME hand-transcribed table
#    (engine owns Python<->JS parity; this checks JS against the DOCUMENT,
#     which parity cannot do)
# ===========================================================================

JS_SRC = r"""
import * as E from '%(engine)s';
const out = [];
const fn = E.scoreClear || E._scoreClear || E.score_clear || null;
if (!fn) { process.stdout.write(JSON.stringify({unavailable: true})); }
else {
  for (const lines of [0,1,2,3,4])
    for (const level of [1,2,3,5,10,13,20,29,40])
      for (const b2b of [0,1,2,5])
        for (const combo of [0,1,2,3,9]) {
          const r = fn(lines, level, b2b, combo);
          out.push([lines, level, b2b, combo, Array.from(r).slice(0,4)]);
        }
  process.stdout.write(JSON.stringify({rows: out}));
}
"""


def check_js_against_spec():
    print("\n5. JS mirror against the same transcribed spec table")
    node = shutil.which("node")
    if not node:
        skip("G5", "node not installed -- the JS side was NOT checked against "
                   "the document")
        return
    with open(JS_RUNNER, "w") as f:
        f.write(JS_SRC % {"engine": WEB_ENGINE.replace("\\", "/")})
    proc = subprocess.run([node, JS_RUNNER], capture_output=True, text=True)
    if proc.returncode != 0:
        # THIS IS A FAILURE, NOT A SKIP: node exists and the module is broken.
        fail("G5", "web/engine.js failed to run -- the whole web app is down, "
                   "not merely unverified:\n%s" % proc.stderr[-1200:])
        return
    import json
    data = json.loads(proc.stdout)
    if data.get("unavailable"):
        print("      (web/engine.js exports no scoreClear; checking the JS "
              "side by PLAYING instead, which tests the same numbers)")
        check_js_played()
        return
    bad = 0
    for lines, level, b2b, combo, got in data["rows"]:
        want = list(spec_score(lines, level, b2b, combo))
        if [got[0], got[1], got[2], bool(got[3])] != \
                [want[0], want[1], want[2], bool(want[3])]:
            bad += 1
            if bad <= 5:
                fail("G5", "JS lines=%d level=%d b2b=%d combo=%d -> %r, "
                           "spec %r" % (lines, level, b2b, combo, got, want))
    if bad == 0:
        ok("%d combinations: the JS mirror matches the transcribed spec too"
           % len(data["rows"]))


JS_PLAY_SRC = r"""
import * as E from '%(engine)s';

// Build the same board the Python check uses: 12 rows of #########. so three
// vertical I drops in column 9 make three consecutive tetrises.
const rows = new Array(E.ROWS).fill(0);
const almost = E.FULL_ROW & ~(1 << 9);
for (let y = E.ROWS - 12; y < E.ROWS; y++) rows[y] = almost;

let s = E.stateFromObject({
  rows, current: E.I, rot: 0, x: E.SPAWN_X[E.I], y: 0,
  queue: [0,1,2,3,4,5,6], hold: null, can_hold: true,
  rng: 1, score: 0, lines: 0, level: 1, pieces: 0, game_over: false,
});

const deltas = [];
const drops = [];
for (let k = 0; k < 3; k++) {
  s.current = E.I; s.rot = 0; s.x = E.SPAWN_X[E.I]; s.y = 0;
  const ps = E.legalPlacements(s).filter(p => p[0] === 1 && p[1] === 7);
  if (!ps.length) { process.stdout.write(JSON.stringify({error: 'no vertical I at k=' + k})); process.exit(0); }
  const [ns, info] = E.applyPlacement(s, ps[0]);
  s = ns;
  deltas.push(info.score_delta);
  drops.push(info.lines_cleared);
}

// and a soft/hard drop point check
let h = E.newGame(7);
const before = h.score;
E.softDrop(h);
const soft = h.score - before;
h = E.newGame(7);
const dist = E.dropDistance(h);
const b2 = h.score;
const hinfo = E.hardDrop(h);
const hardPts = h.score - b2 - hinfo.score_delta;

process.stdout.write(JSON.stringify({deltas, drops, soft, dist, hardPts}));
"""


def check_js_played():
    """Verify JS scoring against the DOCUMENT by playing, when the scoring
    function is not exported on its own."""
    node = shutil.which("node")
    if not node:
        skip("G5", "node not installed -- JS scoring not checked against the "
                   "document")
        return
    runner = os.path.join(_HERE, "_score_golden_play.mjs")
    with open(runner, "w") as f:
        f.write(JS_PLAY_SRC % {"engine": WEB_ENGINE.replace("\\", "/")})
    proc = subprocess.run([node, runner], capture_output=True, text=True)
    if proc.returncode != 0:
        fail("G5", "web/engine.js failed to run:\n%s" % proc.stderr[-1200:])
        return
    import json
    d = json.loads(proc.stdout)
    if d.get("error"):
        fail("G5", "JS setup failed: %s" % d["error"])
        return
    if d["drops"] != [4, 4, 4]:
        fail("G5", "JS did not produce three tetrises: lines_cleared=%r"
             % d["drops"])
        return
    want = [800, 1250, 1300]
    if d["deltas"] != want:
        fail("G5", "JS three back-to-back tetrises scored %r, the spec says %r "
                   "(800, then 800*3//2+50, then 800*3//2+100)"
             % (d["deltas"], want))
    else:
        ok("JS played through: three back-to-back tetrises = %r, total %d -- "
           "matches the transcribed spec" % (d["deltas"], sum(d["deltas"])))
    if d["soft"] != SPEC_SOFT_PER_CELL:
        fail("G5", "JS soft drop scored %d, spec says %d"
             % (d["soft"], SPEC_SOFT_PER_CELL))
    elif d["hardPts"] != SPEC_HARD_PER_CELL * d["dist"]:
        fail("G5", "JS hard drop over %d cells added %d, spec says %d"
             % (d["dist"], d["hardPts"], SPEC_HARD_PER_CELL * d["dist"]))
    else:
        ok("JS drop points: soft=%d/cell, hard=%d/cell over %d cells -- match "
           "the spec" % (d["soft"], SPEC_HARD_PER_CELL, d["dist"]))


def main():
    print("=" * 74)
    print("checker: GOLDEN absolute score test vs docs/spec.md section 5")
    print("  spec read at %s (file mtime %s)" % (SPEC_READ_AT, SPEC_MTIME))
    print("=" * 74)
    check_table()
    check_golden()
    check_played()
    check_drop_points()
    check_js_against_spec()

    print("\n" + "=" * 74)
    print("PASS %d   FAIL %d   SKIP %d" % (len(PASSED), len(FAILED),
                                           len(SKIPPED)))
    if SKIPPED:
        print("skipped (NOT verified, not passed):")
        for tag, msg in SKIPPED:
            print("  [%s] %s" % (tag, msg.splitlines()[0]))
    if FAILED:
        print("FAILURES:")
        for tag, msg in FAILED:
            print("  [%s] %s" % (tag, msg.splitlines()[0]))
        return 1
    print("RESULT: the engine's scoring matches the document, absolutely.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
