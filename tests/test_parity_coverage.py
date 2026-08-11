"""Coverage attack on the Python<->JS parity proof.

engine reports "byte-identical" for 3 seeds x 2 policies. This file does not
dispute that result -- it attacks WHAT THAT TRACE NEVER TOUCHED.

Three parts:

  1. COVERAGE AUDIT of engine's own parity configuration (seeds
     [1, 12345, 0xDEADBEEF] x policies first/lowest). Counts, as numbers:
       - lines_cleared histogram (is a 4-line tetris ever in there?)
       - non-contiguous multi-row clears
       - clears that actually move cells down (real row gravity)
       - placements that occupy a buffer row (y < 2)
       - which of the 7x4 = 28 (piece, rot) states appear
     Anything at zero is an unverified rule path.

  2. WIDENED GAME PARITY: 50 seeds x 4 policies, including a `well` policy
     written to manufacture 4-line clears (columns 0..8 flat, column 9
     reserved for a vertical I). Python and JS traces compared move by move
     on board_hash and state_hash. Coverage recounted.

  3. FIXTURE PARITY: hand-built boards that force the rule paths a policy
     cannot be relied on to reach -- tetris, non-contiguous double/triple,
     clear with floating cells above, placement resting in the buffer rows,
     board full but one column empty, spawn collision. Each fixture is
     serialized, replayed in both engines, and compared on board_hash,
     state_hash and every `info` field.

Run:  python3 tests/test_parity_coverage.py
Owner of the code under test: engine.  This file is checker-owned.
"""

import json
import os
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "engine"))

import engine as E  # noqa: E402

#: engine renamed column_heights -> column_tops (it returns the topmost
#: FILLED ROW INDEX, not a height). Bind whichever the current engine has.
_TOPS = getattr(E, "column_tops", None) or getattr(E, "column_heights")
from rng import next_u32, seed_state  # noqa: E402

WEB_ENGINE = os.path.join(_ROOT, "web", "engine.js")
JS_RUNNER = os.path.join(_HERE, "_coverage_runner.mjs")
FIXTURE_JSON = os.path.join(_HERE, "_coverage_fixtures.json")

ENGINE_SEEDS = [1, 12345, 0xDEADBEEF]
ENGINE_POLICIES = ["first", "lowest"]
WIDE_SEEDS = [1, 12345, 0xDEADBEEF] + [7919 * k + 3 for k in range(47)]
WIDE_POLICIES = ["first", "lowest", "well", "flat"]
MAX_MOVES = 4000

FAILURES = []


def fail(tag, msg):
    FAILURES.append((tag, msg))
    print("  FAIL [%s] %s" % (tag, msg))


def ok(msg):
    print("  ok   %s" % msg)


# ---------------------------------------------------------------------------
# policies -- integer arithmetic only, so the JS mirror cannot drift on floats
# ---------------------------------------------------------------------------

def _heights_holes(rows):
    h = [0] * E.W
    holes = 0
    for c in range(E.W):
        top = E.ROWS
        for y in range(E.ROWS):
            if (rows[y] >> c) & 1:
                top = y
                break
        h[c] = E.ROWS - top
        for y in range(top + 1, E.ROWS):
            if not ((rows[y] >> c) & 1):
                holes += 1
    return h, holes


def _eval_well(rows, cleared, piece, rot, cols):
    """Reserve column 9 as a tetris well. Integers only."""
    h, holes = _heights_holes(rows)
    agg = sum(h[:9])
    bump = sum(abs(h[c] - h[c + 1]) for c in range(8))
    maxh = max(h[:9])
    s = -(holes * 1000 + agg * 10 + bump * 20 + maxh * 30 + h[9] * 500)
    if 9 in cols and not (piece == E.I and rot == 1):
        s -= 100000
    s += 4000 if cleared == 4 else -2000 * cleared
    return s


def _eval_flat(rows, cleared, piece, rot, cols):
    """Plain flat-stacking greedy: clear whenever possible, hate holes."""
    h, holes = _heights_holes(rows)
    agg = sum(h)
    bump = sum(abs(h[c] - h[c + 1]) for c in range(E.W - 1))
    return -(holes * 1000 + agg * 10 + bump * 20) + cleared * 3000


def _cols_of(piece, rot, x):
    return set(x + dx for dx, _dy in E.PIECE_CELLS[piece][rot])


def pick_index(state, ps, policy):
    """Index of the chosen placement. The JS runner must match exactly."""
    if policy == "first":
        return 0
    if policy == "lowest":
        best, best_y = 0, ps[0][2]
        for i in range(1, len(ps)):
            if ps[i][2] > best_y:
                best_y, best = ps[i][2], i
        return best
    ev = _eval_well if policy == "well" else _eval_flat
    best, best_s = 0, None
    for i, p in enumerate(ps):
        rot, x, _y, piece = p
        ns, info = E.apply_placement(state, p)
        s = ev(ns.rows, info["lines_cleared"], piece, rot, _cols_of(piece, rot, x))
        if best_s is None or s > best_s:
            best_s, best = s, i
    return best


# ---------------------------------------------------------------------------
# coverage-instrumented Python trace
# ---------------------------------------------------------------------------

def new_cov():
    return {
        "moves": 0,
        "games": 0,
        "lines_hist": {0: 0, 1: 0, 2: 0, 3: 0, 4: 0},
        "noncontiguous_clears": 0,
        "clears_with_cells_above": 0,
        "buffer_row_placements": 0,
        "rot_states": set(),
        "total_lines": 0,
        "spawn_collisions": 0,
        "no_placement_gameovers": 0,
    }


def trace_one(seed, policy, cov, max_moves=MAX_MOVES):
    s = E.new_game(seed)
    cov["games"] += 1
    out = [(E.board_hash(s.rows), E.state_hash(s), 0, s.score)]
    for _i in range(max_moves):
        ps = E.legal_placements(s)
        if not ps:
            cov["no_placement_gameovers"] += 1
            break
        p = ps[pick_index(s, ps, policy)]
        rot, x, y_rest, piece = p
        before = s.rows
        entry = next(e for e in E._TABLE[piece][rot] if e[1] == x)
        min_dy = entry[4]
        cov["rot_states"].add((piece, rot))
        if y_rest + min_dy < E.BUFFER_ROWS:
            cov["buffer_row_placements"] += 1

        s, info = E.apply_placement(s, p)
        cov["moves"] += 1
        n = info["lines_cleared"]
        cov["lines_hist"][n] += 1
        cov["total_lines"] += n
        if n >= 2:
            cr = info["cleared_rows"]
            if cr[-1] - cr[0] + 1 != len(cr):
                cov["noncontiguous_clears"] += 1
        if n:
            # did any occupied cell sit strictly above the topmost cleared row?
            # if so this clear really exercised the shift-down path
            topmost = info["cleared_rows"][0]
            filled_above = any(before[y] for y in range(topmost))
            # the piece itself was written before the clear, so also allow the
            # piece's own rows above the cut
            if filled_above or (y_rest + min_dy) < topmost:
                cov["clears_with_cells_above"] += 1
        out.append((E.board_hash(s.rows), E.state_hash(s), n, s.score))
        if s.game_over:
            if info["lines_cleared"] >= 0 and E.legal_placements(s) == []:
                pass
            cov["spawn_collisions"] += 1
            break
    return out


#: (piece, rot) pairs the AGENT path can reach at all. The others are
#: deliberately deduplicated by UNIQUE_ROTS, so a game trace can never show
#: them -- only fixtures can. Not covering them in a game is not a gap.
REACHABLE = set((p, r) for p in range(7) for r in E.UNIQUE_ROTS[p])
ALL_ROTS = set((p, r) for p in range(7) for r in range(4))


def report_cov(label, cov, expect=REACHABLE):
    print("  --- coverage: %s ---" % label)
    print("    games=%d moves=%d total_lines=%d"
          % (cov["games"], cov["moves"], cov["total_lines"]))
    print("    lines_cleared histogram: 0=%d 1=%d 2=%d 3=%d 4=%d"
          % tuple(cov["lines_hist"][k] for k in range(5)))
    print("    non-contiguous multi-row clears : %d" % cov["noncontiguous_clears"])
    print("    clears with cells above the cut : %d" % cov["clears_with_cells_above"])
    print("    placements occupying a buffer row: %d" % cov["buffer_row_placements"])
    print("    (piece, rot) states seen        : %d / %d agent-reachable "
          "(%d / 28 of all rotation states)"
          % (len(cov["rot_states"] & expect), len(expect),
             len(cov["rot_states"])))
    missing = [(E.PIECE_NAMES[p], r) for (p, r) in sorted(expect)
               if (p, r) not in cov["rot_states"]]
    print("    missing agent-reachable states  : %s"
          % (", ".join("%s r%d" % m for m in missing) if missing else "none"))
    return missing


# ---------------------------------------------------------------------------
# attack the UNIQUE_ROTS dedup claim itself
# ---------------------------------------------------------------------------

def attack_rot_dedup():
    """engine claims "r2 duplicates r0 and r3 duplicates r1 for the symmetric
    pieces", so legal_placements skips them. If that is wrong, the agent is
    silently denied legal moves -- boards top out earlier than they should and
    every performance number is DEPRESSED (and the JS mirror would agree, so
    parity cannot catch it either).

    Test: for each piece, the set of absolute cell-sets reachable by hard drop
    using ALL FOUR rotations must equal the set reachable using UNIQUE_ROTS
    only. Checked over random boards.
    """
    print("\n0. attack the UNIQUE_ROTS dedup claim (r2==r0, r3==r1)")
    st = seed_state(0xD0D0)
    boards = [tuple([0] * E.ROWS)]
    for _ in range(3000):
        rows = [0] * E.ROWS
        st, v = next_u32(st)
        ceiling = 2 + v % 14
        for y in range(ceiling, E.ROWS):
            st, a = next_u32(st)
            st, b = next_u32(st)
            rows[y] = (a ^ (b >> 9)) & E.FULL_ROW
        boards.append(tuple(rows))

    def cellsets(rows, piece, rots):
        top = _TOPS(rows)
        found = set()
        for rot in rots:
            for rot_i, x, masks, bottom, min_dy, max_dy in E._TABLE[piece][rot]:
                y_rest = min(top[c] - b - 1 for c, b in bottom)
                if y_rest + min_dy < 0:
                    continue
                cs = frozenset((y_rest + dy, x + dx)
                               for dx, dy in E.PIECE_CELLS[piece][rot])
                found.add(cs)
        return found

    lost = 0
    for rows in boards:
        for piece in range(7):
            full = cellsets(rows, piece, range(4))
            uniq = cellsets(rows, piece, E.UNIQUE_ROTS[piece])
            if full != uniq:
                lost += 1
                if lost <= 3:
                    miss = full - uniq
                    fail("R", "%s: UNIQUE_ROTS loses %d reachable cell-set(s), "
                              "e.g. %r  rows=%r"
                         % (E.PIECE_NAMES[piece], len(miss),
                            sorted(next(iter(miss))), list(rows)))
    if lost == 0:
        ok("%d boards x 7 pieces: UNIQUE_ROTS reaches exactly the same set of "
           "landing cell-sets as all 4 rotations -- dedup is lossless"
           % len(boards))
    else:
        fail("R", "%d (board, piece) cases lose placements to the dedup" % lost)


# ---------------------------------------------------------------------------
# fixtures -- deterministic boards that force specific rule paths
# ---------------------------------------------------------------------------

def rows_from_ascii(lines):
    """`lines` is 22 strings of 10 chars, '#'=filled. Top row first."""
    assert len(lines) == E.ROWS, len(lines)
    out = []
    for ln in lines:
        assert len(ln) == E.W, ln
        v = 0
        for c, ch in enumerate(ln):
            if ch == "#":
                v |= 1 << c
        out.append(v)
    return tuple(out)


def blank(n):
    return ["." * E.W for _ in range(n)]


def mk_state(rows, piece, seed=1, lines=0, score=0, hold=None):
    s = E.State()
    s.rows = rows
    s.rng = seed_state(seed)
    s.rng, s.queue = E._refill(s.rng, ())
    s.current = piece
    s.rot = 0
    s.x = E.SPAWN_X[piece]
    s.y = 0
    s.lines = lines
    s.level = 1 + lines // 10
    s.score = score
    s.hold = hold
    return s


def build_fixtures():
    """Each fixture: (name, state_dict, placement, why)."""
    fx = []

    # 1. TETRIS: columns 0..8 filled for 4 rows, column 9 empty. Vertical I
    #    into the well clears 4 rows at once.
    rows = rows_from_ascii(blank(18) + ["#########."] * 4)
    st = mk_state(rows, E.I)
    fx.append(("tetris_4line", st, (1, 7, 18, E.I),
               "4 rows full at once via a vertical I in a 4-deep well"))

    # 2. NON-CONTIGUOUS DOUBLE: two separated rows complete on the same move.
    #    Vertical I in column 9 with a gap row in between that is not full.
    rows = rows_from_ascii(
        blank(18)
        + ["#########.",     # y=18 completes
           "########..",     # y=19 does NOT (col 8 empty)
           "#########.",     # y=20 completes
           "########.."]     # y=21 does NOT
    )
    st = mk_state(rows, E.I)
    fx.append(("noncontiguous_double", st, (1, 7, 18, E.I),
               "rows 18 and 20 complete, 19 and 21 do not -> gap in cleared_rows"))

    # 3. NON-CONTIGUOUS TRIPLE with cells above the cut, so the shift-down
    #    path has to move real content over a gap.
    rows = rows_from_ascii(
        blank(15)
        + ["..#####...",     # floating junk above
           "###.......",
           "#########.",     # completes
           "########..",     # no
           "#########.",     # completes
           "#########.",     # completes
           "........#."]
    )
    st = mk_state(rows, E.I)
    fx.append(("noncontiguous_triple_with_junk_above", st, (1, 7, 17, E.I),
               "3 non-adjacent rows clear while junk sits above -> gravity"))

    # 4. SINGLE clear with a tall stack above: pure row-gravity check.
    rows = rows_from_ascii(
        blank(10)
        + ["#.........",
           "##........",
           "###.......",
           "####......",
           "#####.....",
           "######....",
           "#######...",
           "########..",
           "#########.",
           "#########.",
           "#########.",
           "#########."]
    )
    st = mk_state(rows, E.I)
    fx.append(("single_deep_stack_above", st, (1, 7, 18, E.I),
               "vertical I clears rows 18-21; 8 junk rows must shift down 4"))

    # 5. BUFFER-ROW PLACEMENT: stack up to row 4 so the piece rests inside
    #    the hidden buffer (y < 2).
    rows = rows_from_ascii(blank(4) + ["#########."] * 18)
    st = mk_state(rows, E.I)
    fx.append(("buffer_row_placement", st, (1, 7, 0, E.I),
               "vertical I resting at y_rest=0 -> occupies rows 0..3, "
               "two of them hidden buffer rows"))

    # 6. ONE COLUMN EMPTY, board otherwise full to the top of the buffer:
    #    the extreme legal board.
    rows = rows_from_ascii(["#########."] * E.ROWS)
    st = mk_state(rows, E.I)
    fx.append(("full_except_one_column", st, None,
               "every row needs only column 9; no placement can fit "
               "(y_rest+min_dy < 0) -> legal_placements must be empty"))

    # 7. SPAWN COLLISION: cell sitting exactly on a spawn square.
    rows = rows_from_ascii([".....#....", ".........."] + blank(20))
    st = mk_state(rows, E.T)
    fx.append(("spawn_blocked_elsewhere_open", st, (0, 0, 20, E.T),
               "placing far left succeeds, then the NEXT spawn may collide "
               "with the buffer cell -> spawn-collision game over path"))

    # 8. O piece in a 2-wide notch, and a clear that empties the board fully.
    rows = rows_from_ascii(blank(20) + ["########..", "########.."])
    st = mk_state(rows, E.O)
    fx.append(("double_clear_empties_board", st, (0, 8, 20, E.O),
               "O fills columns 8,9 on rows 20,21 -> both clear, board empty"))

    # 9. Level/score boundary: 9 lines already, this clear crosses to level 2.
    rows = rows_from_ascii(blank(21) + ["#########."])
    st = mk_state(rows, E.I, lines=9, score=12345)
    fx.append(("level_boundary_single", st, (0, 6, 20, E.I),
               "lines 9 -> 10 crosses the level boundary; score must use the "
               "level BEFORE the clear (spec 5)"))

    # 10. S/Z in a well -- awkward rotation resting on uneven ground.
    rows = rows_from_ascii(blank(19) + ["..........", "###....###", "###....###"])
    st = mk_state(rows, E.S)
    fx.append(("s_piece_uneven_ground", st, (1, 3, 18, E.S),
               "S rot1 vertical into a 3-wide trench"))

    # 11..N: every (piece, rot) from the unique list dropped onto a jagged
    #        board, so all 28 rotation states get a parity check.
    jag = rows_from_ascii(
        blank(14)
        + ["..........",
           ".#........",
           ".#..#.....",
           "##..#..#..",
           "##.###.#..",
           "##.###.##.",
           "##.#####.#",
           "###.####.#"]
    )
    for piece in range(7):
        for rot in range(4):
            st = mk_state(jag, piece)
            st.rot = rot
            ps = [p for p in E.legal_placements(st) if p[0] == rot]
            if not ps:
                # rot not in UNIQUE_ROTS; construct the placement by hand
                entries = E._TABLE[piece][rot]
                top = _TOPS(jag)
                cand = []
                for rot_i, x, masks, bottom, min_dy, max_dy in entries:
                    y_rest = min(top[c] - b - 1 for c, b in bottom)
                    if y_rest + min_dy >= 0:
                        cand.append((rot_i, x, y_rest, piece))
                ps = cand
            if ps:
                mid = ps[len(ps) // 2]
                fx.append(("rot_%s_r%d" % (E.PIECE_NAMES[piece], rot), st, mid,
                           "parity for rotation state %s r%d on a jagged board"
                           % (E.PIECE_NAMES[piece], rot)))
            else:
                fail("FX", "no placement for %s r%d on the jagged board"
                     % (E.PIECE_NAMES[piece], rot))
    return fx


def run_fixtures_python(fx):
    out = []
    for name, st, p, _why in fx:
        rec = {"name": name, "state": st.to_dict()}
        ps = E.legal_placements(st)
        rec["n_legal"] = len(ps)
        rec["legal"] = [list(q) for q in ps]
        rec["placement"] = list(p) if p is not None else None
        if p is not None:
            ns, info = E.apply_placement(st, p)
            rec["board_hash"] = E.board_hash(ns.rows)
            rec["state_hash"] = E.state_hash(ns)
            rec["info"] = {k: norm(v) for k, v in info.items()}
            rec["rows_after"] = list(ns.rows)
        out.append(rec)
    return out


def norm(v):
    """Tuples and lists are the same value across the language boundary."""
    if isinstance(v, (list, tuple)):
        return [norm(x) for x in v]
    if isinstance(v, bool):
        return v
    if isinstance(v, float) and v == int(v):
        return float(v)
    return v


# ---------------------------------------------------------------------------
# JS side
# ---------------------------------------------------------------------------

JS_SRC = r"""// generated by tests/test_parity_coverage.py -- checker owned
import * as E from '%(engine)s';
import { readFileSync } from 'fs';

const SEEDS = %(seeds)s;
const POLICIES = %(policies)s;
const MAX_MOVES = %(max_moves)d;
const W = E.W, ROWS = E.ROWS;

function heightsHoles(rows) {
  const h = new Array(W).fill(0); let holes = 0;
  for (let c = 0; c < W; c++) {
    let top = ROWS;
    for (let y = 0; y < ROWS; y++) { if ((rows[y] >> c) & 1) { top = y; break; } }
    h[c] = ROWS - top;
    for (let y = top + 1; y < ROWS; y++) if (!((rows[y] >> c) & 1)) holes++;
  }
  return [h, holes];
}
function evalWell(rows, cleared, piece, rot, cols) {
  const [h, holes] = heightsHoles(rows);
  let agg = 0; for (let c = 0; c < 9; c++) agg += h[c];
  let bump = 0; for (let c = 0; c < 8; c++) bump += Math.abs(h[c] - h[c + 1]);
  let maxh = h[0]; for (let c = 1; c < 9; c++) if (h[c] > maxh) maxh = h[c];
  let s = -(holes * 1000 + agg * 10 + bump * 20 + maxh * 30 + h[9] * 500);
  if (cols.has(9) && !(piece === E.I && rot === 1)) s -= 100000;
  s += (cleared === 4) ? 4000 : -2000 * cleared;
  return s;
}
function evalFlat(rows, cleared) {
  const [h, holes] = heightsHoles(rows);
  let agg = 0; for (let c = 0; c < W; c++) agg += h[c];
  let bump = 0; for (let c = 0; c < W - 1; c++) bump += Math.abs(h[c] - h[c + 1]);
  return -(holes * 1000 + agg * 10 + bump * 20) + cleared * 3000;
}
function colsOf(piece, rot, x) {
  const s = new Set();
  for (const [dx] of E.PIECE_CELLS[piece][rot]) s.add(x + dx);
  return s;
}
function pickIndex(state, ps, policy) {
  if (policy === 'first') return 0;
  if (policy === 'lowest') {
    let b = 0, by = ps[0][2];
    for (let i = 1; i < ps.length; i++) if (ps[i][2] > by) { by = ps[i][2]; b = i; }
    return b;
  }
  const useWell = policy === 'well';
  let b = 0, bs = null;
  for (let i = 0; i < ps.length; i++) {
    const [rot, x, , piece] = ps[i];
    const [ns, info] = E.applyPlacement(state, ps[i]);
    const s = useWell
      ? evalWell(ns.rows, info.lines_cleared, piece, rot, colsOf(piece, rot, x))
      : evalFlat(ns.rows, info.lines_cleared);
    if (bs === null || s > bs) { bs = s; b = i; }
  }
  return b;
}
function traceOne(seed, policy) {
  let s = E.newGame(seed);
  const out = [[E.boardHash(s.rows), E.stateHash(s), 0, s.score]];
  for (let i = 0; i < MAX_MOVES; i++) {
    const ps = E.legalPlacements(s);
    if (ps.length === 0) break;
    const [ns, info] = E.applyPlacement(s, ps[pickIndex(s, ps, policy)]);
    s = ns;
    out.push([E.boardHash(s.rows), E.stateHash(s), info.lines_cleared, s.score]);
    if (s.game_over) break;
  }
  return out;
}

const traces = {};
for (const seed of SEEDS)
  for (const pol of POLICIES) traces[seed + '/' + pol] = traceOne(seed, pol);

// fixtures
const fx = JSON.parse(readFileSync('%(fixtures)s', 'utf8'));
const fxOut = [];
for (const rec of fx) {
  const st = E.stateFromObject(rec.state);
  const ps = E.legalPlacements(st);
  const o = { name: rec.name, n_legal: ps.length, legal: ps.map(p => Array.from(p)) };
  if (rec.placement !== null) {
    const [ns, info] = E.applyPlacement(st, rec.placement);
    o.board_hash = E.boardHash(ns.rows);
    o.state_hash = E.stateHash(ns);
    o.info = info;
    o.rows_after = Array.from(ns.rows);
  }
  fxOut.push(o);
}
process.stdout.write(JSON.stringify({ traces, fixtures: fxOut }));
"""


def run_js(seeds, policies, max_moves, fixtures_path):
    node = shutil.which("node")
    if not node:
        return None
    src = JS_SRC % {
        "engine": WEB_ENGINE.replace("\\", "/"),
        "seeds": json.dumps(seeds),
        "policies": json.dumps(policies),
        "max_moves": max_moves,
        "fixtures": fixtures_path.replace("\\", "/"),
    }
    with open(JS_RUNNER, "w") as f:
        f.write(src)
    proc = subprocess.run([node, JS_RUNNER], capture_output=True, text=True)
    if proc.returncode != 0:
        fail("JS", "node runner failed:\n" + proc.stderr[-3000:])
        return None
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    print("=" * 74)
    print("checker: coverage attack on the Python<->JS parity proof")
    print("=" * 74)

    attack_rot_dedup()

    # ---- part 1: audit engine's own parity configuration
    print("\n1. AUDIT of engine's parity config (3 seeds x [first, lowest])")
    cov0 = new_cov()
    for seed in ENGINE_SEEDS:
        for pol in ENGINE_POLICIES:
            trace_one(seed, pol, cov0, max_moves=200)
    missing0 = report_cov("engine's config", cov0)
    gaps = []
    if cov0["lines_hist"][4] == 0:
        gaps.append("no 4-line clear (tetris) anywhere in the trace")
    if cov0["lines_hist"][3] == 0:
        gaps.append("no 3-line clear")
    if cov0["noncontiguous_clears"] == 0:
        gaps.append("no non-contiguous multi-row clear")
    if cov0["clears_with_cells_above"] == 0:
        gaps.append("no clear ever had cells above the cut -> row gravity "
                    "(shift-down) never verified")
    if cov0["buffer_row_placements"] == 0:
        gaps.append("no placement ever occupied a buffer row (y < 2)")
    if missing0:
        gaps.append("%d of the %d agent-reachable (piece, rot) states never "
                    "appear: %s" % (len(missing0), len(REACHABLE),
                                    ", ".join("%s r%d" % m for m in missing0)))
    if gaps:
        print("  >>> UNVERIFIED PATHS in engine's parity config:")
        for g in gaps:
            print("      - %s" % g)
    else:
        ok("engine's config already covers every path checked here")

    # ---- part 2 + 3 prep
    print("\n2. FIXTURES: build boards that force the missing paths")
    fx = build_fixtures()
    py_fx = run_fixtures_python(fx)
    with open(FIXTURE_JSON, "w") as f:
        json.dump([{"name": r["name"], "state": r["state"],
                    "placement": r["placement"]} for r in py_fx], f)
    ok("%d fixtures built" % len(fx))
    # what do the fixtures cover?
    fx_hist = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}
    fx_noncontig = 0
    for r in py_fx:
        if "info" in r:
            n = r["info"]["lines_cleared"]
            fx_hist[n] += 1
            if n >= 2:
                cr = r["info"]["cleared_rows"]
                if cr[-1] - cr[0] + 1 != len(cr):
                    fx_noncontig += 1
    print("    fixture lines_cleared histogram: 0=%d 1=%d 2=%d 3=%d 4=%d"
          % tuple(fx_hist[k] for k in range(5)))
    print("    fixture non-contiguous clears  : %d" % fx_noncontig)
    if fx_hist[4] == 0:
        fail("FX", "fixtures failed to produce a 4-line clear")
    if fx_noncontig == 0:
        fail("FX", "fixtures failed to produce a non-contiguous clear")

    # ---- part 3: widened game trace
    print("\n3. WIDENED game parity: %d seeds x %s"
          % (len(WIDE_SEEDS), WIDE_POLICIES))
    cov1 = new_cov()
    py_traces = {}
    for seed in WIDE_SEEDS:
        for pol in WIDE_POLICIES:
            py_traces["%d/%s" % (seed, pol)] = trace_one(seed, pol, cov1)
    missing1 = report_cov("widened config", cov1)
    for label, val in (("tetris (4-line)", cov1["lines_hist"][4]),
                       ("triple", cov1["lines_hist"][3]),
                       ("non-contiguous clear", cov1["noncontiguous_clears"]),
                       ("clear with cells above", cov1["clears_with_cells_above"]),
                       ("buffer-row placement", cov1["buffer_row_placements"])):
        if val == 0:
            fail("COV", "widened trace still has 0 x %s" % label)
    if missing1:
        fail("COV", "widened trace still misses agent-reachable rot states: %s"
             % ", ".join("%s r%d" % m for m in missing1))
    # the 9 deduplicated states can only come from fixtures -- check they do
    fx_rots = set()
    for name, st_, p_, _w in fx:
        if p_ is not None:
            fx_rots.add((p_[3], p_[0]))
    together = cov1["rot_states"] | fx_rots
    if together != ALL_ROTS:
        fail("COV", "even games + fixtures miss rotation states: %s"
             % ", ".join("%s r%d" % (E.PIECE_NAMES[p], r)
                         for p, r in sorted(ALL_ROTS - together)))
    else:
        ok("all 28 (piece, rot) states are parity-checked: %d by the game "
           "traces, the remaining %d (deduplicated by UNIQUE_ROTS, "
           "unreachable from the agent path) by fixtures"
           % (len(cov1["rot_states"]), len(ALL_ROTS - cov1["rot_states"])))

    # ---- compare against JS
    print("\n4. COMPARE Python vs JS (node)")
    js = run_js(WIDE_SEEDS, WIDE_POLICIES, MAX_MOVES, FIXTURE_JSON)
    if js is None:
        fail("JS", "node unavailable or runner failed -- parity NOT verified")
    else:
        # traces
        bad = 0
        for key, pt in py_traces.items():
            jt = js["traces"].get(key)
            if jt is None:
                fail("JS", "JS trace missing for %s" % key)
                bad += 1
                continue
            if len(pt) != len(jt):
                fail("JS", "%s length differs: py=%d js=%d"
                     % (key, len(pt), len(jt)))
                bad += 1
                continue
            for i, (a, b) in enumerate(zip(pt, jt)):
                if list(a) != list(b):
                    fail("JS", "%s diverges at move %d: py=%r js=%r"
                         % (key, i, a, b))
                    bad += 1
                    break
        if bad == 0:
            n = sum(len(v) for v in py_traces.values())
            ok("%d traces, %d move records: board_hash + state_hash + "
               "lines_cleared + score all identical"
               % (len(py_traces), n))

        # fixtures
        jfx = {r["name"]: r for r in js["fixtures"]}
        bad = 0
        for r in py_fx:
            j = jfx.get(r["name"])
            if j is None:
                fail("JS", "fixture %s missing from JS output" % r["name"])
                bad += 1
                continue
            if r["n_legal"] != j["n_legal"] or r["legal"] != j["legal"]:
                fail("JS", "fixture %s legal_placements differ: py=%d js=%d"
                     % (r["name"], r["n_legal"], j["n_legal"]))
                bad += 1
            if "info" not in r:
                continue
            if r["board_hash"] != j["board_hash"]:
                fail("JS", "fixture %s board_hash py=%d js=%d"
                     % (r["name"], r["board_hash"], j["board_hash"]))
                bad += 1
            if r["state_hash"] != j["state_hash"]:
                fail("JS", "fixture %s state_hash py=%d js=%d"
                     % (r["name"], r["state_hash"], j["state_hash"]))
                bad += 1
            if r["rows_after"] != j["rows_after"]:
                fail("JS", "fixture %s rows_after differ" % r["name"])
                bad += 1
            py_keys = set(r["info"].keys())
            js_keys = set(j["info"].keys())
            if py_keys != js_keys:
                fail("JS", "fixture %s info keys differ: py-only=%s js-only=%s"
                     % (r["name"], sorted(py_keys - js_keys),
                        sorted(js_keys - py_keys)))
                bad += 1
            for k, v in r["info"].items():
                if k not in js_keys:
                    continue
                jv = norm(j["info"][k])
                if isinstance(v, float) or isinstance(jv, float):
                    try:
                        same = abs(float(v) - float(jv)) < 1e-12
                    except (TypeError, ValueError):
                        same = v == jv
                else:
                    same = v == jv
                if not same:
                    fail("JS", "fixture %s info[%s] py=%r js=%r"
                         % (r["name"], k, v, jv))
                    bad += 1
        if bad == 0:
            ok("%d fixtures: legal_placements, board_hash, state_hash, "
               "rows_after and every info field identical" % len(py_fx))

    print("\n" + "=" * 74)
    if FAILURES:
        print("RESULT: %d FAILURE(S)" % len(FAILURES))
        for tag, msg in FAILURES:
            print("  [%s] %s" % (tag, msg.splitlines()[0]))
        return 1
    print("RESULT: parity holds on the widened trace and on all fixtures.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
