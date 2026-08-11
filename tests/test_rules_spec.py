"""Rule correctness against docs/spec.md, derived independently of engine's
own tests, plus the human-path parity the existing parity proof never ran.

Two parts.

PART A -- HUMAN-PATH PARITY (the gap).
`engine/parity.py` drives only `legalPlacements`/`applyPlacement`. It never
calls move / rotate / softDrop / hardDrop / tick / tickMs / hold, so the whole
interactive path -- 40 wall-kick entries, rotation-failure conditions, drop
scoring, lock delay and its reset budget, hold round-trip -- has never been
compared between Python and JS. Human play is the user's requirement #1.
This part replays deterministic KEY SEQUENCES in both engines and diffs the
complete serialized state after every single input.

PART B -- SPEC-DERIVED RULE TESTS.
Cases and tables are transcribed here straight from docs/spec.md, not from
engine.py, so a table typo copied into both the engine and its own tests still
fails here. Boundaries covered: rotation beside both walls, rotation while
resting on the floor, board full, one column empty, vertical I, the level
boundary, the score "level before the clear" rule, 7-bag validity, placement
ordering, apply_placement purity, and a from-scratch reimplementation of row
clearing / board hashing compared over random boards.

Run:  python3 tests/test_rules_spec.py
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
from rng import next_bag, next_u32, seed_state  # noqa: E402

WEB_ENGINE = os.path.join(_ROOT, "web", "engine.js")
JS_RUNNER = os.path.join(_HERE, "_human_runner.mjs")
SEQ_JSON = os.path.join(_HERE, "_human_sequences.json")

FAILURES = []


def fail(tag, msg):
    FAILURES.append((tag, msg))
    print("  FAIL [%s] %s" % (tag, msg))


def ok(msg):
    print("  ok   %s" % msg)


def section(t):
    print("\n" + t)


# ===========================================================================
# PART A -- human-path parity
# ===========================================================================

#: Input alphabet. dt values are integers so lock_ms accumulates identically.
#: 0 left, 1 right, 2 rotate CW, 3 rotate CCW, 4 soft drop, 5 hard drop,
#: 6 hold, 7..10 tickMs(dt)
OPS = ["left", "right", "cw", "ccw", "soft", "hard", "hold",
       "tick16", "tick17", "tick120", "tick600"]
TICK_DT = {"tick16": 16, "tick17": 17, "tick120": 120, "tick600": 600}


def build_sequences(n_seq=60, length=400):
    """Deterministic key sequences, weighted toward rotation and movement."""
    # weights: exercise rotation and movement hardest, hard-drop sparingly so
    # games last long enough to reach deep boards
    weighted = (["left"] * 5 + ["right"] * 5 + ["cw"] * 6 + ["ccw"] * 6
                + ["soft"] * 4 + ["hard"] * 2 + ["hold"] * 2
                + ["tick16"] * 6 + ["tick17"] * 3 + ["tick120"] * 3
                + ["tick600"] * 2)
    st = seed_state(0xA11CE)
    seqs = []
    for i in range(n_seq):
        seed = 1 + i * 104729
        ops = []
        for _ in range(length):
            st, v = next_u32(st)
            ops.append(weighted[v % len(weighted)])
        seqs.append({"seed": seed, "ops": ops})

    # Random key mashing dies before it ever completes a row, so the human
    # path's line-clear/score/level code would go unparried. These scripted
    # "sweep" sequences walk each piece to a target column and hard-drop it,
    # cycling the target left to right, which fills the bottom rows and clears.
    for i in range(12):
        seed = 555 + i * 7919
        ops = []
        for k in range(120):
            target = (k * 3 + i) % 10
            ops += ["left"] * 9              # slam to the left wall
            if i % 3 == 1:
                ops += ["cw"]                # vary orientation
            elif i % 3 == 2:
                ops += ["cw", "cw"]
            ops += ["right"] * target
            ops += ["hard"]
        seqs.append({"seed": seed, "ops": ops})

    # Neither random mashing nor the sweeps clear many rows, so the human
    # path's clear/score/level/B2B code would still go unparried. These
    # sequences are PRECOMPUTED IN PYTHON: pick a good placement with a flat
    # greedy, then emit the literal key presses that reach it. The JS side
    # replays the same key list, so this is still a pure input replay.
    for i in range(10):
        seqs.append({"seed": 31337 + i * 2749,
                     "ops": _greedy_keys(31337 + i * 2749, pieces=220)})
    return seqs


def _greedy_keys(seed, pieces):
    """Key presses that play a decent game, so line clears actually happen."""
    s = E.new_game(seed)
    ops = []
    for _ in range(pieces):
        if s.game_over or s.current is None:
            break
        ps = E.legal_placements(s)
        if not ps:
            break
        # Only rotations the player can actually REACH from the spawn position
        # count. At y=0 several kicks fail (they would push the piece above the
        # board), so blindly targeting a rotation makes the greedy play a shape
        # it never achieved. Probe each rotation on a clone first.
        reach = {}
        for turns in range(4):
            probe = s.clone()
            failed = False
            for _t in range(turns):
                if not E.rotate(probe, True):
                    failed = True
                    break
            if not failed and probe.rot not in reach:
                reach[probe.rot] = turns
        ps = [p for p in ps if p[0] in reach]
        if not ps:
            # cannot rotate anywhere useful; just drop it where it stands
            E.hard_drop(s)
            ops.append("hard")
            continue
        # flat greedy on the afterstate: hate holes, keep the stack low
        best, best_score = None, None
        for p in ps:
            ns, info = E.apply_placement(s, p)
            top = _TOPS(ns.rows)
            h = [E.ROWS - t for t in top]
            holes = 0
            for c in range(E.W):
                for y in range(top[c] + 1, E.ROWS):
                    if not ((ns.rows[y] >> c) & 1):
                        holes += 1
            sc = -(holes * 1000 + sum(h) * 10
                   + sum(abs(h[c] - h[c + 1]) for c in range(E.W - 1)) * 20)
            sc += info["lines_cleared"] * 3000
            if best_score is None or sc > best_score:
                best_score, best = sc, p
        rot, x, _y, _pc = best
        for _t in range(reach[rot]):
            if E.rotate(s, True):
                ops.append("cw")
            else:
                break
        # walk to column x
        guard = 0
        while s.x != x and guard < 20:
            guard += 1
            # capture the direction BEFORE the move: E.move mutates s.x, so
            # reading `x > s.x` afterwards mislabels the final step
            step = 1 if x > s.x else -1
            if E.move(s, step):
                ops.append("right" if step == 1 else "left")
            else:
                break
        E.hard_drop(s)
        ops.append("hard")
    return ops


def _ret(v):
    """Normalize a human-path return value for cross-language comparison."""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    return _norm(v)


def _norm(v):
    if isinstance(v, (list, tuple)):
        return [_norm(x) for x in v]
    if isinstance(v, bool):
        return v
    if isinstance(v, float):
        return round(v, 9)
    return v


def run_sequence_python(seed, ops):
    s = E.new_game(seed)
    out = [{"op": "init", "ret": None, "state": _norm(s.to_dict())}]
    for op in ops:
        if op == "left":
            r = E.move(s, -1)
        elif op == "right":
            r = E.move(s, 1)
        elif op == "cw":
            r = E.rotate(s, True)
        elif op == "ccw":
            r = E.rotate(s, False)
        elif op == "soft":
            r = E.soft_drop(s)
        elif op == "hard":
            r = E.hard_drop(s)
        elif op == "hold":
            r = E.hold(s)
        else:
            r = E.tick_ms(s, TICK_DT[op])
        out.append({"op": op, "ret": _ret(r), "state": _norm(s.to_dict())})
    return out


JS_SRC = r"""// generated by tests/test_rules_spec.py -- checker owned
import * as E from '%(engine)s';
import { readFileSync } from 'fs';

const TICK_DT = { tick16: 16, tick17: 17, tick120: 120, tick600: 600 };

function norm(v) {
  if (Array.isArray(v)) return v.map(norm);
  if (typeof v === 'number' && !Number.isInteger(v)) return Math.round(v * 1e9) / 1e9;
  return v;
}
function normObj(o) {
  if (o === null || o === undefined) return null;
  if (typeof o === 'boolean') return o;
  if (typeof o !== 'object') return norm(o);
  const out = {};
  for (const k of Object.keys(o)) out[k] = norm(o[k]);
  return out;
}

function runSeq(seed, ops) {
  const s = E.newGame(seed);
  const out = [{ op: 'init', ret: null, state: normObj(E.stateToObject(s)) }];
  for (const op of ops) {
    let r;
    if (op === 'left') r = E.move(s, -1);
    else if (op === 'right') r = E.move(s, 1);
    else if (op === 'cw') r = E.rotate(s, true);
    else if (op === 'ccw') r = E.rotate(s, false);
    else if (op === 'soft') r = E.softDrop(s);
    else if (op === 'hard') r = E.hardDrop(s);
    else if (op === 'hold') r = E.hold(s);
    else r = E.tickMs(s, TICK_DT[op]);
    out.push({ op, ret: normObj(r), state: normObj(E.stateToObject(s)) });
  }
  return out;
}

const seqs = JSON.parse(readFileSync('%(seqs)s', 'utf8'));
const res = seqs.map(q => runSeq(q.seed, q.ops));
process.stdout.write(JSON.stringify(res));
"""


def part_a():
    section("PART A. human-path parity (move/rotate/drop/hold/tickMs)")
    node = shutil.which("node")
    seqs = build_sequences()
    with open(SEQ_JSON, "w") as f:
        json.dump(seqs, f)

    py = [run_sequence_python(q["seed"], q["ops"]) for q in seqs]
    n_steps = sum(len(t) for t in py)

    # what did the sequences actually exercise?
    stats = {"rotations_ok": 0, "rotations_failed": 0, "kicked": 0,
             "holds_ok": 0, "locks": 0, "clears": 0, "gameovers": 0,
             "lock_resets_maxed": 0}
    for t in py:
        prev = t[0]["state"]
        for rec in t[1:]:
            cur = rec["state"]
            if rec["op"] in ("cw", "ccw"):
                if rec["ret"] is True:
                    stats["rotations_ok"] += 1
                    if cur["x"] != prev["x"] or (cur["y"] != prev["y"]):
                        stats["kicked"] += 1
                else:
                    stats["rotations_failed"] += 1
            if rec["op"] == "hold" and rec["ret"] is True:
                stats["holds_ok"] += 1
            if isinstance(rec["ret"], dict):
                stats["locks"] += 1
                if rec["ret"]["lines_cleared"]:
                    stats["clears"] += 1
            if cur["game_over"] and not prev["game_over"]:
                stats["gameovers"] += 1
            if cur["lock_resets"] >= 15:
                stats["lock_resets_maxed"] += 1
            prev = cur
    print("    %d sequences, %d input steps" % (len(seqs), n_steps))
    print("    rotations: %d succeeded (%d needed a wall kick), %d refused"
          % (stats["rotations_ok"], stats["kicked"], stats["rotations_failed"]))
    print("    holds: %d, locks: %d, locks that cleared: %d, game overs: %d"
          % (stats["holds_ok"], stats["locks"], stats["clears"],
             stats["gameovers"]))
    print("    steps at the lock-reset limit (15): %d"
          % stats["lock_resets_maxed"])
    for k in ("rotations_ok", "kicked", "rotations_failed", "holds_ok",
              "locks", "clears", "gameovers", "lock_resets_maxed"):
        if stats[k] == 0:
            fail("A-cov", "human-path sequences never exercised: %s" % k)

    if not node:
        fail("A", "node unavailable -- human-path parity NOT verified")
        return
    src = JS_SRC % {"engine": WEB_ENGINE.replace("\\", "/"),
                    "seqs": SEQ_JSON.replace("\\", "/")}
    with open(JS_RUNNER, "w") as f:
        f.write(src)
    proc = subprocess.run([node, JS_RUNNER], capture_output=True, text=True)
    if proc.returncode != 0:
        fail("A", "node runner failed:\n" + proc.stderr[-2500:])
        return
    js = json.loads(proc.stdout)

    if len(js) != len(py):
        fail("A", "sequence count differs py=%d js=%d" % (len(py), len(js)))
        return
    bad = 0
    for qi, (pt, jt) in enumerate(zip(py, js)):
        if len(pt) != len(jt):
            fail("A", "seq %d length differs py=%d js=%d"
                 % (qi, len(pt), len(jt)))
            bad += 1
            continue
        for i, (a, b) in enumerate(zip(pt, jt)):
            if a["ret"] != b["ret"]:
                fail("A", "seq %d step %d op=%s return differs: py=%r js=%r"
                     % (qi, i, a["op"], a["ret"], b["ret"]))
                bad += 1
                break
            if a["state"] != b["state"]:
                diff = {k: (a["state"][k], b["state"].get(k))
                        for k in a["state"]
                        if a["state"][k] != b["state"].get(k)}
                fail("A", "seq %d step %d op=%s state differs: %r"
                     % (qi, i, a["op"], diff))
                bad += 1
                break
    if bad == 0:
        ok("%d input steps: return value AND all 18 state fields identical "
           "in Python and JS after every single key press" % n_steps)


# ===========================================================================
# PART B -- spec-derived rule tests
# ===========================================================================

# --- tables transcribed from docs/spec.md sections 2, 4, 5 -----------------

SPEC_CELLS = {
    "I": [[(0, 1), (1, 1), (2, 1), (3, 1)], [(2, 0), (2, 1), (2, 2), (2, 3)],
          [(0, 2), (1, 2), (2, 2), (3, 2)], [(1, 0), (1, 1), (1, 2), (1, 3)]],
    "O": [[(0, 0), (1, 0), (0, 1), (1, 1)]] * 4,
    "T": [[(1, 0), (0, 1), (1, 1), (2, 1)], [(1, 0), (1, 1), (2, 1), (1, 2)],
          [(0, 1), (1, 1), (2, 1), (1, 2)], [(1, 0), (0, 1), (1, 1), (1, 2)]],
    "S": [[(1, 0), (2, 0), (0, 1), (1, 1)], [(1, 0), (1, 1), (2, 1), (2, 2)],
          [(1, 1), (2, 1), (0, 2), (1, 2)], [(0, 0), (0, 1), (1, 1), (1, 2)]],
    "Z": [[(0, 0), (1, 0), (1, 1), (2, 1)], [(2, 0), (1, 1), (2, 1), (1, 2)],
          [(0, 1), (1, 1), (1, 2), (2, 2)], [(1, 0), (0, 1), (1, 1), (0, 2)]],
    "J": [[(0, 0), (0, 1), (1, 1), (2, 1)], [(1, 0), (2, 0), (1, 1), (1, 2)],
          [(0, 1), (1, 1), (2, 1), (2, 2)], [(1, 0), (1, 1), (0, 2), (1, 2)]],
    "L": [[(2, 0), (0, 1), (1, 1), (2, 1)], [(1, 0), (1, 1), (1, 2), (2, 2)],
          [(0, 1), (1, 1), (2, 1), (0, 2)], [(0, 0), (1, 0), (1, 1), (1, 2)]],
}

SPEC_KICK_JLSTZ = {
    (0, 1): [(0, 0), (-1, 0), (-1, -1), (0, 2), (-1, 2)],
    (1, 0): [(0, 0), (1, 0), (1, 1), (0, -2), (1, -2)],
    (1, 2): [(0, 0), (1, 0), (1, 1), (0, -2), (1, -2)],
    (2, 1): [(0, 0), (-1, 0), (-1, -1), (0, 2), (-1, 2)],
    (2, 3): [(0, 0), (1, 0), (1, -1), (0, 2), (1, 2)],
    (3, 2): [(0, 0), (-1, 0), (-1, 1), (0, -2), (-1, -2)],
    (3, 0): [(0, 0), (-1, 0), (-1, 1), (0, -2), (-1, -2)],
    (0, 3): [(0, 0), (1, 0), (1, -1), (0, 2), (1, 2)],
}

SPEC_KICK_I = {
    (0, 1): [(0, 0), (-2, 0), (1, 0), (-2, 1), (1, -2)],
    (1, 0): [(0, 0), (2, 0), (-1, 0), (2, -1), (-1, 2)],
    (1, 2): [(0, 0), (-1, 0), (2, 0), (-1, -2), (2, 1)],
    (2, 1): [(0, 0), (1, 0), (-2, 0), (1, 2), (-2, -1)],
    (2, 3): [(0, 0), (2, 0), (-1, 0), (2, -1), (-1, 2)],
    (3, 2): [(0, 0), (-2, 0), (1, 0), (-2, 1), (1, -2)],
    (3, 0): [(0, 0), (1, 0), (-2, 0), (1, 2), (-2, -1)],
    (0, 3): [(0, 0), (-1, 0), (2, 0), (-1, -2), (2, 1)],
}

SPEC_FRAMES = ([48, 43, 38, 33, 28, 23, 18, 13, 8, 6]
               + [5] * 3 + [4] * 3 + [3] * 3 + [2] * 9 + [1] * 5)
SPEC_SCORE = [0, 100, 300, 500, 800]
SPEC_SPAWN_X = {"I": 3, "O": 4, "T": 3, "S": 3, "Z": 3, "J": 3, "L": 3}
SPEC_UNIQUE_ROTS = {"I": [0, 1], "O": [0], "T": [0, 1, 2, 3], "S": [0, 1],
                    "Z": [0, 1], "J": [0, 1, 2, 3], "L": [0, 1, 2, 3]}


def b_tables():
    section("PART B1. constant tables vs docs/spec.md (transcribed by hand)")
    for name, rots in SPEC_CELLS.items():
        p = E.PIECE_NAMES.index(name)
        for r in range(4):
            got = sorted(tuple(c) for c in E.PIECE_CELLS[p][r])
            want = sorted(tuple(c) for c in rots[r])
            if got != want:
                fail("B1", "%s r%d cells: spec=%r engine=%r" % (name, r, want, got))
    ok("PIECE_CELLS: all 28 rotations match spec section 2")

    for name in SPEC_SPAWN_X:
        p = E.PIECE_NAMES.index(name)
        if E.SPAWN_X[p] != SPEC_SPAWN_X[name]:
            fail("B1", "%s spawn x: spec=%d engine=%d"
                 % (name, SPEC_SPAWN_X[name], E.SPAWN_X[p]))
    if E.SPAWN_Y != 0:
        fail("B1", "SPAWN_Y should be 0, got %d" % E.SPAWN_Y)
    ok("spawn x/y match spec section 3")

    for name, rots in SPEC_UNIQUE_ROTS.items():
        p = E.PIECE_NAMES.index(name)
        if list(E.UNIQUE_ROTS[p]) != rots:
            fail("B1", "%s UNIQUE_ROTS: spec=%r engine=%r"
                 % (name, rots, list(E.UNIQUE_ROTS[p])))
    ok("UNIQUE_ROTS matches spec section 2")

    for name in ("T", "S", "Z", "J", "L"):
        p = E.PIECE_NAMES.index(name)
        for key, want in SPEC_KICK_JLSTZ.items():
            got = [tuple(o) for o in E.kick_offsets(p, key[0], key[1])]
            if got != [tuple(o) for o in want]:
                fail("B1", "%s kick %d->%d: spec=%r engine=%r"
                     % (name, key[0], key[1], want, got))
    for key, want in SPEC_KICK_I.items():
        got = [tuple(o) for o in E.kick_offsets(E.I, key[0], key[1])]
        if got != [tuple(o) for o in want]:
            fail("B1", "I kick %d->%d: spec=%r engine=%r"
                 % (key[0], key[1], want, got))
    if [tuple(o) for o in E.kick_offsets(E.O, 0, 1)] != [(0, 0)]:
        fail("B1", "O must have no kick offsets, got %r"
             % (E.kick_offsets(E.O, 0, 1),))
    ok("wall-kick tables: all 8 transitions x (JLSTZ, I) match spec section 4, "
       "O has none")

    for lvl in range(1, 41):
        want = SPEC_FRAMES[min(lvl, 29) - 1]
        got = E.frames_per_cell(lvl)
        if got != want:
            fail("B1", "frames_per_cell(%d): spec=%d engine=%d"
                 % (lvl, want, got))
    if E.frames_per_cell(0) != 48 or E.frames_per_cell(-5) != 48:
        fail("B1", "level <= 0 must clamp to level 1 (48 frames)")
    ok("gravity curve matches spec section 5 for levels 1..40 (and clamps <=0)")

    if list(E.SCORE_TABLE) != SPEC_SCORE:
        fail("B1", "SCORE_TABLE: spec=%r engine=%r"
             % (SPEC_SCORE, list(E.SCORE_TABLE)))
    ok("score table matches spec section 5")


# --- independent reimplementation of the rule core -------------------------

def ref_apply(rows, cells):
    """Write `cells`, clear full rows, compact. Written from spec section 5
    prose only: a list-of-lists board, no bitmasks, no shared code."""
    g = [[(rows[y] >> x) & 1 for x in range(E.W)] for y in range(E.ROWS)]
    for (y, x) in cells:
        g[y][x] = 1
    full = [y for y in range(E.ROWS) if all(g[y])]
    kept = [g[y] for y in range(E.ROWS) if y not in set(full)]
    new = [[0] * E.W for _ in range(len(full))] + kept
    out = []
    for row in new:
        v = 0
        for x in range(E.W):
            if row[x]:
                v |= 1 << x
        out.append(v)
    return tuple(out), full


def ref_board_hash(rows):
    h = 2166136261
    for y in range(E.ROWS):
        h = ((h ^ (rows[y] & 0xFF)) * 16777619) % (2 ** 32)
        h = ((h ^ ((rows[y] >> 8) & 0xFF)) * 16777619) % (2 ** 32)
    return h


def b_reference_core():
    section("PART B2. independent reimplementation of clear + hash "
            "(list-of-lists, no bitmasks)")
    st = seed_state(0xFEED)
    n = 0
    for _ in range(20000):
        rows = [0] * E.ROWS
        st, v = next_u32(st)
        ceiling = 2 + v % 16
        for y in range(ceiling, E.ROWS):
            st, a = next_u32(st)
            st, b = next_u32(st)
            # bias toward nearly-full rows so clears actually happen
            m = (a | b) & E.FULL_ROW
            st, c = next_u32(st)
            if c % 3 == 0:
                m = E.FULL_ROW & ~(1 << (c % E.W))
            if m == E.FULL_ROW:
                # A reachable board never holds an already-full row -- it would
                # have been cleared on the move that filled it. Generating one
                # would make `apply_placement`'s "only rows the piece touched
                # can have become full" optimisation look wrong when it is not.
                m &= ~(1 << (c % E.W))
            rows[y] = m
        rows = tuple(rows)
        st, v = next_u32(st)
        piece = v % 7
        s = E.State()
        s.rows = rows
        s.current = piece
        s.rot = 0
        s.x = E.SPAWN_X[piece]
        ps = E.legal_placements(s)
        if not ps:
            continue
        st, v = next_u32(st)
        p = ps[v % len(ps)]
        rot, x, y_rest, _pc = p
        cells = [(y_rest + dy, x + dx) for dx, dy in E.PIECE_CELLS[piece][rot]]
        ns, info = E.apply_placement(s, p)
        want_rows, want_full = ref_apply(rows, cells)
        if ns.rows != want_rows:
            fail("B2", "board after placement differs from the reference "
                       "implementation. piece=%s rot=%d x=%d y=%d"
                 % (E.PIECE_NAMES[piece], rot, x, y_rest))
            break
        if info["cleared_rows"] != want_full:
            fail("B2", "cleared_rows=%r reference=%r"
                 % (info["cleared_rows"], want_full))
            break
        if info["lines_cleared"] != len(want_full):
            fail("B2", "lines_cleared=%d reference=%d"
                 % (info["lines_cleared"], len(want_full)))
            break
        if E.board_hash(ns.rows) != ref_board_hash(ns.rows):
            fail("B2", "board_hash differs from the reference FNV-1a")
            break
        # spec section 8 formulas, recomputed here
        min_dy = min(dy for _dx, dy in E.PIECE_CELLS[piece][rot])
        max_dy = max(dy for _dx, dy in E.PIECE_CELLS[piece][rot])
        # `landing_height` is no longer an engine field (ownership moved to
        # rl/features.py). What the engine DOES promise is the raw geometry it
        # is reconstructible from -- check that instead.
        if sorted(map(tuple, info["piece_cells"])) != sorted(cells):
            fail("B2", "piece_cells=%r, want %r"
                 % (info["piece_cells"], sorted(cells)))
            break
        want_eroded = len(want_full) * sum(
            1 for (cy, _cx) in cells if cy in set(want_full))
        if info["eroded_piece_cells"] != want_eroded:
            fail("B2", "eroded_piece_cells=%d spec formula=%d"
                 % (info["eroded_piece_cells"], want_eroded))
            break
        if info["landing_row_top"] != y_rest + min_dy or \
                info["landing_row_bottom"] != y_rest + max_dy:
            fail("B2", "landing_row_top/bottom wrong")
            break
        n += 1
    ok("%d random (board, placement) pairs: rows after clear, cleared_rows, "
       "lines_cleared, board_hash, piece_cells, eroded_piece_cells and "
       "landing_row_top/bottom all match the from-scratch reference" % n)


def b_hard_drop_equals_placement():
    section("PART B3. hard drop must land exactly where legal_placements says")
    st = seed_state(0x0DD1)
    checked = 0
    for _ in range(20000):
        rows = [0] * E.ROWS
        st, v = next_u32(st)
        ceiling = 4 + v % 14
        for y in range(ceiling, E.ROWS):
            st, a = next_u32(st)
            st, b = next_u32(st)
            rows[y] = (a ^ (b >> 11)) & E.FULL_ROW
        rows = tuple(rows)
        st, v = next_u32(st)
        piece = v % 7
        s = E.State()
        s.rows = rows
        s.current = piece
        for rot, x, y_rest, _pc in E.legal_placements(s):
            # drive the piece there with the human path and hard drop
            h = E.State()
            h.rows = rows
            h.current = piece
            h.rot = rot
            h.x = x
            h.y = y_rest - 1 if y_rest > 0 else 0
            if not E._fits(h.rows, piece, rot, x, h.y):
                continue
            got = h.y + E.drop_distance(h)
            if got != y_rest:
                fail("B3", "%s rot=%d x=%d: legal_placements says y_rest=%d "
                           "but drop_distance lands at %d"
                     % (E.PIECE_NAMES[piece], rot, x, y_rest, got))
                return
            checked += 1
    ok("%d placements: the y_rest formula in spec section 5 agrees with an "
       "actual cell-by-cell hard drop" % checked)


def b_boundaries():
    section("PART B4. boundary cases pulled from the spec by hand")

    def mk(rows, piece, rot=0, x=None, y=0):
        s = E.State()
        s.rows = rows
        s.rng, s.queue = E._refill(seed_state(1), ())
        s.current = piece
        s.rot = rot
        s.x = E.SPAWN_X[piece] if x is None else x
        s.y = y
        return s

    empty = E.EMPTY_ROWS

    # 1. rotation beside the left wall: vertical I at x such that its column is
    #    column 0, rotating to horizontal must kick right, not go out of bounds
    s = mk(empty, E.I, rot=1, x=-2, y=8)
    if not E._fits(s.rows, E.I, 1, -2, 8):
        fail("B4", "setup: vertical I at x=-2 should fit (its cells are dx=2)")
    before = (s.x, s.y, s.rot)
    r = E.rotate(s, True)     # 1 -> 2, horizontal
    for dx, dy in E.PIECE_CELLS[s.current][s.rot]:
        if not (0 <= s.x + dx < E.W and 0 <= s.y + dy < E.ROWS):
            fail("B4", "left-wall rotation left the piece out of bounds: "
                       "%r -> rot=%d x=%d y=%d" % (before, s.rot, s.x, s.y))
            break
    else:
        ok("left-wall rotation (vertical I in column 0): %s, ends in bounds "
           "at rot=%d x=%d" % ("rotated" if r else "refused", s.rot, s.x))

    # 2. rotation beside the right wall
    s = mk(empty, E.I, rot=1, x=7, y=8)
    r = E.rotate(s, True)
    for dx, dy in E.PIECE_CELLS[s.current][s.rot]:
        if not (0 <= s.x + dx < E.W and 0 <= s.y + dy < E.ROWS):
            fail("B4", "right-wall rotation out of bounds: rot=%d x=%d y=%d"
                 % (s.rot, s.x, s.y))
            break
    else:
        ok("right-wall rotation (vertical I in column 9): %s, ends in bounds"
           % ("rotated" if r else "refused"))

    # 3. rotation while resting on the floor: T on the floor rotating CW must
    #    either stay in bounds or be refused -- never fall through y=21
    for piece in range(7):
        for rot in range(4):
            s = mk(empty, piece, rot=rot, x=4, y=0)
            s.y = s.y + E.drop_distance(s)      # rest on the floor
            y_before, x_before, rot_before = s.y, s.x, s.rot
            E.rotate(s, True)
            for dx, dy in E.PIECE_CELLS[s.current][s.rot]:
                if not (0 <= s.x + dx < E.W and 0 <= s.y + dy < E.ROWS):
                    fail("B4", "floor rotation out of bounds: %s r%d -> r%d "
                               "y %d->%d" % (E.PIECE_NAMES[piece], rot_before,
                                             s.rot, y_before, s.y))
                    break
    ok("floor rotation: all 28 (piece, rot) states resting on y=21 stay in "
       "bounds after a CW rotation")

    # 4. rotation into an occupied cell must be refused, not silently allowed
    rows = list(E.EMPTY_ROWS)
    for y in range(E.ROWS):
        rows[y] = E.FULL_ROW & ~(1 << 4)      # only column 4 free
    rows = tuple(rows)
    s = mk(rows, E.I, rot=1, x=2, y=10)
    if not E._fits(rows, E.I, 1, 2, 10):
        fail("B4", "setup: vertical I should fit in the single free column")
    else:
        got = E.rotate(s, True)
        if got:
            fail("B4", "rotation succeeded inside a 1-wide shaft: rot=%d x=%d "
                       "y=%d" % (s.rot, s.x, s.y))
        elif (s.rot, s.x, s.y) != (1, 2, 10):
            fail("B4", "refused rotation must leave state untouched, got "
                       "rot=%d x=%d y=%d" % (s.rot, s.x, s.y))
        else:
            ok("rotation inside a 1-wide vertical shaft is refused and leaves "
               "rot/x/y untouched (all 5 kicks fail)")

    # 5. board full: no placement, and spawn collides
    full = tuple([E.FULL_ROW] * E.ROWS)
    for piece in range(7):
        s = mk(full, piece)
        if E.legal_placements(s):
            fail("B4", "full board still offers placements for %s"
                 % E.PIECE_NAMES[piece])
    ok("completely full board: legal_placements is empty for all 7 pieces")

    # 6. one column empty for the FULL height: only the vertical I fits (it is
    #    the only piece 1 column wide), and it must clear exactly 4 rows.
    onecol = tuple([E.FULL_ROW & ~(1 << 9)] * E.ROWS)
    got = {}
    for piece in range(7):
        s = mk(onecol, piece)
        got[E.PIECE_NAMES[piece]] = len(E.legal_placements(s))
    want = {"I": 1, "O": 0, "T": 0, "S": 0, "Z": 0, "J": 0, "L": 0}
    if got != want:
        fail("B4", "board full except column 9: placements %r, want %r "
                   "(only a vertical I is 1 column wide)" % (got, want))
    else:
        s = mk(onecol, E.I)
        p = E.legal_placements(s)[0]
        ns, info = E.apply_placement(s, p)
        if p[0] != 1:
            fail("B4", "the single placement should be the vertical I (rot 1), "
                       "got rot=%d" % p[0])
        elif info["lines_cleared"] != 4:
            fail("B4", "vertical I into a 22-deep 1-wide well cleared %d rows, "
                       "want 4" % info["lines_cleared"])
        else:
            ok("board full except column 9: exactly 1 placement exists (the "
               "vertical I, the only 1-column-wide shape), it lands at y=%d "
               "and clears 4 rows for %d points"
               % (p[2], info["score_delta"]))

    # 6b. same board but the well only 4 deep -> the vertical I must fit
    rows = list(E.EMPTY_ROWS)
    for y in range(E.ROWS - 4, E.ROWS):
        rows[y] = E.FULL_ROW & ~(1 << 9)
    s = mk(tuple(rows), E.I)
    ps = [p for p in E.legal_placements(s) if p[0] == 1 and 9 in
          set(p[1] + dx for dx, _dy in E.PIECE_CELLS[E.I][1])]
    if not ps:
        fail("B4", "vertical I must fit a 4-deep well in column 9")
    else:
        ns, info = E.apply_placement(s, ps[0])
        if info["lines_cleared"] != 4:
            fail("B4", "vertical I into a 4-deep well cleared %d rows, want 4"
                 % info["lines_cleared"])
        elif ns.rows != E.EMPTY_ROWS:
            fail("B4", "tetris should leave the board empty, got %r"
                 % (ns.rows,))
        else:
            ok("vertical I into a 4-deep well clears 4 rows and empties the "
               "board; score_delta=%d" % info["score_delta"])

    # 7. score uses the level BEFORE the clear (spec section 5)
    rows = list(E.EMPTY_ROWS)
    rows[E.ROWS - 1] = E.FULL_ROW & ~(1 << 9)
    s = mk(tuple(rows), E.I)
    s.lines = 9
    s.level = 1 + 9 // 10          # == 1
    # vertical I (rot 1) at x=7 occupies column 9 only, so it drops all the way
    # to the floor and completes exactly the bottom row.
    ps = [p for p in E.legal_placements(s) if p[0] == 1 and p[1] == 7]
    if not ps:
        fail("B4", "setup: vertical I in column 9 on an otherwise empty board")
    else:
        ns, info = E.apply_placement(s, ps[0])
        if info["lines_cleared"] != 1:
            fail("B4", "expected a single clear, got %d" % info["lines_cleared"])
        elif ns.lines != 10 or ns.level != 2:
            fail("B4", "lines 9->%d level ->%d, want 10 and 2"
                 % (ns.lines, ns.level))
        elif info["score_delta"] != 100 * 1:
            fail("B4", "score_delta=%d; spec says base(100) x level BEFORE the "
                       "clear(1) = 100, not x2" % info["score_delta"])
        else:
            ok("crossing the level boundary: lines 9->10, level 1->2, and "
               "score_delta=100 uses the level BEFORE the clear")

    # 8. apply_placement must not mutate the input state
    s = mk(E.EMPTY_ROWS, E.T)
    snap = json.dumps(s.to_dict(), sort_keys=True)
    ps = E.legal_placements(s)
    E.apply_placement(s, ps[len(ps) // 2])
    if json.dumps(s.to_dict(), sort_keys=True) != snap:
        fail("B4", "apply_placement mutated the input state (spec section 8 "
                   "says it must not)")
    else:
        ok("apply_placement leaves the input state byte-identical")

    # 9. placement ordering: rot ascending, then x ascending (spec section 8)
    st = seed_state(0x5017)
    bad = 0
    for _ in range(2000):
        rows = [0] * E.ROWS
        st, v = next_u32(st)
        for y in range(6 + v % 12, E.ROWS):
            st, a = next_u32(st)
            rows[y] = a & E.FULL_ROW
        st, v = next_u32(st)
        s = mk(tuple(rows), v % 7)
        ps = E.legal_placements(s)
        keys = [(p[0], p[1]) for p in ps]
        if keys != sorted(keys):
            bad += 1
        if any(p[0] not in E.UNIQUE_ROTS[s.current] for p in ps):
            fail("B4", "legal_placements returned a rot outside UNIQUE_ROTS")
            break
        if any(p[3] != s.current for p in ps):
            fail("B4", "placement piece field != state.current")
            break
    if bad:
        fail("B4", "%d boards had placements out of (rot, x) order" % bad)
    else:
        ok("2000 boards: placements sorted by (rot, x), rot always in "
           "UNIQUE_ROTS, piece field always == state.current")

    # 10. to_dict / from_dict round trip (spec section 7)
    s = E.new_game(4242)
    for _ in range(40):
        ps = E.legal_placements(s)
        if not ps:
            break
        s, _ = E.apply_placement(s, ps[len(ps) // 3])
    d = s.to_dict()
    back = E.from_dict(json.loads(json.dumps(d)))
    if back.to_dict() != d:
        diff = {k: (d[k], back.to_dict().get(k))
                for k in d if d[k] != back.to_dict().get(k)}
        fail("B4", "to_dict/from_dict round trip is not exact: %r" % diff)
    elif E.state_hash(back) != E.state_hash(s):
        fail("B4", "state_hash differs after a round trip")
    else:
        ok("to_dict -> JSON -> from_dict round trip is exact (%d fields) and "
           "preserves state_hash" % len(d))


def b_bag():
    section("PART B5. 7-bag validity and queue guarantee (spec sections 3, 6)")
    st = seed_state(1)
    counts = [0] * 7
    for _ in range(20000):
        st, bag = next_bag(st)
        if sorted(bag) != list(range(7)):
            fail("B5", "bag is not a permutation of 0..6: %r" % (bag,))
            break
        for p in bag:
            counts[p] += 1
    ok("20000 bags: every one is a permutation of 0..6; piece frequencies "
       "%r (max deviation from 20000: %d)"
       % (counts, max(abs(c - 20000) for c in counts)))

    # Every 7 consecutive pieces drawn within ONE game must be a permutation.
    # Restarting a game resets bag alignment, so each game is counted alone.
    total_pieces = 0
    games = 0
    bad = 0
    short_queue = 0
    for g in range(60):
        s = E.new_game(777 + g * 131)
        drawn = []
        while not s.game_over:
            drawn.append(s.current)
            if len(s.queue) < 5:
                short_queue += 1
            ps = E.legal_placements(s)
            if not ps:
                break
            # `flat`-ish choice so games run long enough to span many bags
            best, best_y = ps[0], ps[0][2]
            for p in ps:
                if p[2] > best_y:
                    best_y, best = p[2], p
            s, _ = E.apply_placement(s, best)
        games += 1
        total_pieces += len(drawn)
        for i in range(0, len(drawn) - 6, 7):
            if sorted(drawn[i:i + 7]) != list(range(7)):
                bad += 1
    if short_queue:
        fail("B5", "queue dropped below the 5 the spec guarantees, %d times"
             % short_queue)
    if bad:
        fail("B5", "%d aligned windows of 7 drawn pieces were not a "
                   "permutation" % bad)
    else:
        ok("%d games, %d pieces drawn: every aligned window of 7 within a game "
           "is a permutation of I,O,T,S,Z,J,L and the queue never dropped "
           "below the 5 the spec guarantees" % (games, total_pieces))


def main():
    print("=" * 74)
    print("checker: docs/spec.md rule correctness + human-path parity")
    print("=" * 74)
    part_a()
    b_tables()
    b_reference_core()
    b_hard_drop_equals_placement()
    b_boundaries()
    b_bag()
    print("\n" + "=" * 74)
    if FAILURES:
        print("RESULT: %d FAILURE(S)" % len(FAILURES))
        for tag, msg in FAILURES:
            print("  [%s] %s" % (tag, msg.splitlines()[0]))
        return 1
    print("RESULT: no rule violation found; human path is parity-clean too.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
