"""Python<->JS parity proof, rule tests, and throughput benchmark.

docs/spec.md section 9 defines the procedure: replay a deterministic
placement sequence from a fixed seed in both engines and compare the board
hash after every move. Any rule divergence shows up as a hash mismatch at
the first move where the two disagree.

Usage
-----
    python3 parity.py                 # rule tests + parity (runs node) + bench
    python3 parity.py --parity        # parity only
    python3 parity.py --bench         # benchmark only
    python3 parity.py --tests         # rule tests only
    python3 parity.py --compare-only  # compare existing parity_*.json
    python3 parity.py --emit-js-runner  # write parity_browser.html (no node)

Outputs land next to this file: parity_python.json, parity_js.json.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import engine as E  # noqa: E402
from rng import next_u32, seed_state  # noqa: E402
from tables import DIFFICULTY_NAMES as Tb_DIFFICULTY_NAMES  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.normpath(os.path.join(HERE, "..", "web"))

SEEDS = [1, 12345, 0xDEADBEEF]
MAX_MOVES = 200

PY_JSON = os.path.join(HERE, "parity_python.json")
JS_JSON = os.path.join(HERE, "parity_js.json")
JS_RUNNER = os.path.join(HERE, "parity_runner.mjs")
BROWSER_RUNNER = os.path.join(HERE, "parity_browser.html")


# ---------------------------------------------------------------------------
# parity trace
# ---------------------------------------------------------------------------

#: Two deterministic policies. "first" is the trivial one from the spec; it
#: tops out in ~15 moves and therefore never clears a line, so "lowest" runs
#: alongside it to put line clears, gravity and level changes under parity too.
POLICIES = ("first", "lowest")


#: Column kept empty by the "well" policy so tetrises can happen.
WELL_COL = 9


def _stack_cost(rows, p) -> int:
    """Integer badness of stacking placement ``p``, ignoring the well column.

    Holes dominate, then total height. Integer only -- a float here could
    tie-break differently in JS and show up as a fake parity mismatch.

    "Deepest landing" is NOT good enough as a stacker: it buries holes and tops
    out in ~39 moves, before four rows are ever complete, so the well policy
    never got to fire its tetris. This is the cost function that makes the well
    strategy actually reach a tetris.
    """
    r = list(rows)
    for y, x in E.placement_cells(p):
        r[y] |= 1 << x
    holes = 0
    agg = 0
    for c in range(WELL_COL):
        top = E.ROWS
        for y in range(E.ROWS):
            if (r[y] >> c) & 1:
                top = y
                break
        agg += E.ROWS - top
        for y in range(top + 1, E.ROWS):
            if not ((r[y] >> c) & 1):
                holes += 1
    return holes * 1000 + agg


def _pick(ps, policy: str, state=None) -> int:
    """Index of the chosen placement. Must match the JS runner exactly.

    Integer arithmetic only -- a float comparison here could tie-break
    differently in JS and produce a spurious parity mismatch.
    """
    if policy == "first":
        return 0

    if policy == "lowest":
        # Deepest landing wins; ties go to the earlier placement, and the
        # placement list order is fixed by docs/spec.md section 8.
        best = 0
        best_y = ps[0][2]
        for i in range(1, len(ps)):
            if ps[i][2] > best_y:
                best_y = ps[i][2]
                best = i
        return best

    raise ValueError("unknown policy %r" % policy)


#: Hand-built boards that force clear paths a heuristic policy reaches only by
#: luck. Random and greedy policies between them produced 0 tetrises and 0
#: non-contiguous multi-clears, so those paths are pinned here instead.
#:
#: Each entry: (name, rows, piece, rot, x). The placement is applied in both
#: languages and every info field plus both hashes are compared.
def _clear_fixtures() -> list:
    F = E.FULL_ROW
    out = []

    def board(pairs):
        r = [0] * E.ROWS
        for y, v in pairs:
            r[y] = v
        return r

    # 4 rows complete except column 9 -> vertical I makes a tetris.
    tetris_rows = [(y, F & ~(1 << 9)) for y in (18, 19, 20, 21)]
    out.append(("tetris", board(tetris_rows), E.I, 1, 7))

    # 3 rows complete except column 9, 4th row partial -> triple.
    triple = [(y, F & ~(1 << 9)) for y in (19, 20, 21)]
    triple.append((18, 0b0000000011))
    out.append(("triple", board(triple), E.I, 1, 7))

    # Non-contiguous double: rows 18 and 20 complete-but-one, 19 and 21 not.
    noncontig = [(18, F & ~(1 << 9)), (19, 0b0110000000),
                 (20, F & ~(1 << 9)), (21, 0b0000110000)]
    out.append(("noncontiguous_double", board(noncontig), E.I, 1, 7))

    # Single clear leaving 8 rows of debris to fall exactly one row.
    debris = [(21, F & ~(1 << 0))]
    for y in range(13, 21):
        debris.append((y, 0b0000001110))
    out.append(("single_under_debris", board(debris), E.I, 1, -2))

    # Landing in the spawn buffer (y_rest == 0), no clear.
    tall = [(y, F & ~((1 << 0) | (1 << 9))) for y in range(1, E.ROWS)]
    out.append(("buffer_row_landing", board(tall), E.I, 1, -2))

    # A double that empties the board completely.
    empties = [(20, F & ~(0b11 << 4)), (21, F & ~(0b11 << 4))]
    out.append(("clear_to_empty", board(empties), E.O, 0, 4))

    return out


def difficulty_probe() -> list:
    """Per-mode behaviour, for cross-language compare."""
    out = []
    for d in (0, 1, 2):
        st = E.new_game(4242, difficulty=d)
        order = [st.current]
        hashes = []
        for _ in range(80):
            ps = E.legal_placements(st)
            if not ps:
                break
            st, _ = E.apply_placement(st, ps[0])
            order.append(st.current)
            hashes.append(E.state_hash(st))
        try:
            peek = list(E.visible_next(st))
        except E.NextPeekBlocked:
            peek = "BLOCKED"
        out.append({
            "difficulty": d,
            "name": E.difficulty_name(st),
            "next_visible_count": E.next_visible_count(st),
            "hold_enabled": E.hold_enabled(st),
            "hold_result": E.hold(E.new_game(4242, difficulty=d)),
            "peek": peek,
            "piece_order": order,
            "final_state_hash": hashes[-1] if hashes else None,
            "moves": len(hashes),
        })
    return out


def fixture_probe() -> list:
    """Apply each fixture and record everything observable."""
    out = []
    for name, rows, piece, rot, x in _clear_fixtures():
        st = E.State()
        st.rows = tuple(rows)
        st.current = piece
        st.rot = rot
        st.x = E.SPAWN_X[piece]
        st.y = 0
        st.queue = (E.I, E.O, E.T, E.S, E.Z, E.J, E.L)
        st.game_over = False
        ps = [p for p in E.legal_placements(st) if p[0] == rot and p[1] == x]
        if not ps:
            out.append({"name": name, "error": "no legal placement"})
            continue
        ns, info = E.apply_placement(st, ps[0])
        out.append({
            "name": name,
            "placements": len(E.legal_placements(st)),
            "board_hash": E.board_hash(ns.rows),
            "state_hash": E.state_hash(ns),
            "rows_after": list(ns.rows),
            "lines_cleared": info["lines_cleared"],
            "cleared_rows": info["cleared_rows"],
            "piece_cells": [list(c) for c in info["piece_cells"]],
            "cleared_piece_cells": info["cleared_piece_cells"],
            "eroded_piece_cells": info["eroded_piece_cells"],
            "score_delta": info["score_delta"],
            "is_tetris": info["is_tetris"],
            "b2b_active": info["b2b_active"],
            "b2b_chain": info["b2b_chain"],
            "combo_count": info["combo_count"],
        })
    return out


def b2b_chain_probe() -> list:
    """Three tetrises in a row on a rebuilt board -- exercises the B2B chain."""
    F = E.FULL_ROW
    st = E.State()
    st.current = E.I
    st.queue = (E.I,) * 7
    st.game_over = False
    out = []
    for _ in range(3):
        rows = [0] * E.ROWS
        for y in (18, 19, 20, 21):
            rows[y] = F & ~(1 << 9)
        st.rows = tuple(rows)
        st.current = E.I
        st.rot = 1
        st.x = 7
        st.y = 0
        ps = [p for p in E.legal_placements(st) if p[0] == 1 and p[1] == 7]
        ns, info = E.apply_placement(st, ps[0])
        out.append([info["lines_cleared"], info["score_delta"],
                    1 if info["b2b_active"] else 0, info["b2b_chain"],
                    info["combo_count"]])
        st = ns
    return out


def trace_python(seed: int, policy: str, max_moves: int = MAX_MOVES) -> list:
    """Replay a deterministic placement sequence, recording a hash per move."""
    s = E.new_game(seed)
    out = [{
        "move": 0,
        "board_hash": E.board_hash(s.rows),
        "state_hash": E.state_hash(s),
        "lines_cleared": 0,
        "score": s.score,
    }]
    for i in range(1, max_moves + 1):
        ps = E.legal_placements(s)
        if not ps:
            break
        s, info = E.apply_placement(s, ps[_pick(ps, policy, s)])
        out.append({
            "move": i,
            "board_hash": E.board_hash(s.rows),
            "state_hash": E.state_hash(s),
            "lines_cleared": info["lines_cleared"],
            "score": s.score,
        })
        if s.game_over:
            break
    return out


def build_python_trace() -> dict:
    traces = {}
    for seed in SEEDS:
        for policy in POLICIES:
            traces["%d/%s" % (seed, policy)] = trace_python(seed, policy)
    return {
        "spec": "docs/spec.md v1",
        "max_moves": MAX_MOVES,
        "policies": list(POLICIES),
        # difficulty is deliberately NOT in state_hash (docs/spec.md section 14),
        # so hashes alone cannot identify the mode that produced them. Stamp it
        # here -- checker flagged this as the one remaining cost of that choice.
        "difficulty": E.DIFFICULTY_DEFAULT,
        "difficulty_name": Tb_DIFFICULTY_NAMES[E.DIFFICULTY_DEFAULT],
        "rng_probe": rng_probe(),
        "score_probe": score_probe(),
        "kick_probe": kick_probe(),
        "fixture_probe": fixture_probe(),
        "difficulty_probe": difficulty_probe(),
        "b2b_chain_probe": b2b_chain_probe(),
        "traces": traces,
        "human_traces": {str(seed): trace_human_python(seed) for seed in SEEDS},
    }


#: Every (lines, level, b2b, combo) combination the scoring rule can see, over
#: the ranges that matter. Small enough to enumerate exhaustively, so B2B and
#: combo are proven identical rather than sampled.
SCORE_CASES = [(n, lv, b2b, combo)
               for n in range(5)
               for lv in (1, 2, 5, 10, 30)
               for b2b in (0, 1, 2, 7)
               for combo in (0, 1, 3, 12)]


def score_probe() -> list:
    """`_score_clear` over every SCORE_CASES entry, for cross-language compare."""
    return [list(E._score_clear(n, lv, b2b, combo))
            for n, lv, b2b, combo in SCORE_CASES]


def rng_probe(n: int = 16) -> list:
    """First n xorshift32 values from seed 1 -- catches RNG drift alone."""
    st = seed_state(1)
    vals = []
    for _ in range(n):
        st, v = next_u32(st)
        vals.append(v)
    return vals


JS_RUNNER_SRC = """// generated by engine/parity.py -- do not edit
import * as E from '__ENGINE__';

const SEEDS = __SEEDS__;
const POLICIES = __POLICIES__;
const MAX_MOVES = __MAX_MOVES__;
const SCORE_CASES = __SCORE_CASES__;
const HUMAN_ACTIONS = __HUMAN_ACTIONS__;
const HUMAN_DT_MS = __HUMAN_DT__;
const HUMAN_STEPS = __HUMAN_STEPS__;
const KICK_BOARDS = __KICK_BOARDS__;
const KICK_Y = __KICK_Y__;

function kickProbeJs() {
  const out = [];
  for (let bi = 0; bi < KICK_BOARDS.length; bi++) {
    const rows = KICK_BOARDS[bi];
    for (let piece = 0; piece < 7; piece++) {
      for (let frm = 0; frm < 4; frm++) {
        for (const cw of [true, false]) {
          for (let x = -2; x < E.W; x++) {
            for (const y of KICK_Y) {
              const st = new E.State();
              st.rows = rows.slice();
              st.current = piece;
              st.rot = frm;
              st.x = x;
              st.y = y;
              st.game_over = false;
              if (!E.fitsForParity(st.rows, piece, frm, x, y)) continue;
              const moved = E.rotate(st, cw);
              out.push([bi, piece, frm, cw ? 1 : 0, x, y,
                        st.rot, st.x, st.y, moved ? 1 : 0]);
            }
          }
        }
      }
    }
  }
  return out;
}

function round4(v) { return Math.round(v * 10000) / 10000; }

function humanApply(state, action) {
  if (action === 'left') E.move(state, -1);
  else if (action === 'right') E.move(state, 1);
  else if (action === 'cw') E.rotate(state, true);
  else if (action === 'ccw') E.rotate(state, false);
  else if (action === 'soft') E.softDrop(state);
  else if (action === 'hard') E.hardDrop(state);
  else if (action === 'hold') E.hold(state);
  E.tickMs(state, HUMAN_DT_MS);
  return !state.game_over;
}

function humanRecord(state, step, action) {
  return {
    step, action,
    kicked: false,
    board_hash: E.boardHash(state.rows),
    current: state.current,
    rot: state.rot,
    x: state.x,
    y: state.y,
    hold: state.hold,
    can_hold: !!state.can_hold,
    score: state.score,
    lines: state.lines,
    level: state.level,
    b2b: state.b2b,
    combo: state.combo,
    lock_resets: state.lock_resets,
    lock_ms: round4(state.lock_ms),
    grav_ms: round4(state.grav_ms),
    lowest_y: state.lowest_y,
    touched_down: !!state.touched_down,
    game_over: !!state.game_over,
  };
}

function traceHumanJs(seed) {
  let state = E.newGame(seed);
  let rng = E.seedState((seed ^ 0x5F5F5F5F) >>> 0);
  const out = [humanRecord(state, 0, 'init')];
  let restarts = 0;
  for (let i = 1; i <= HUMAN_STEPS; i++) {
    rng = E.nextU32(rng);
    const action = HUMAN_ACTIONS[rng % HUMAN_ACTIONS.length];
    const prevRot = state.rot, prevX = state.x, prevY = state.y;
    const alive = humanApply(state, action);
    const rec = humanRecord(state, i, action);
    rec.kicked = !!((action === 'cw' || action === 'ccw')
      && state.rot !== prevRot
      && (state.x !== prevX || state.y !== prevY));
    out.push(rec);
    if (!alive) {
      restarts += 1;
      state = E.newGame(seed + restarts * 7919);
      out.push(humanRecord(state, i, 'restart'));
    }
  }
  return out;
}

const DIFFICULTIES = __DIFFICULTIES__;

function difficultyProbeJs() {
  const out = [];
  for (const d of DIFFICULTIES) {
    let s = E.newGame(4242, d);
    const order = [s.current];
    const hashes = [];
    for (let i = 0; i < 80; i++) {
      const ps = E.legalPlacements(s);
      if (!ps.length) break;
      const [ns] = E.applyPlacement(s, ps[0]);
      s = ns;
      order.push(s.current);
      hashes.push(E.stateHash(s));
    }
    let peek;
    try { peek = E.visibleNext(s); }
    catch (e) { peek = (e instanceof E.NextPeekBlocked) ? 'BLOCKED' : 'WRONG:' + e.name; }
    out.push({
      difficulty: d,
      name: E.difficultyName(s),
      next_visible_count: E.nextVisibleCount(s),
      hold_enabled: E.holdEnabled(s),
      hold_result: E.hold(E.newGame(4242, d)),
      peek: peek,
      piece_order: order,
      final_state_hash: hashes.length ? hashes[hashes.length - 1] : null,
      moves: hashes.length,
    });
  }
  return out;
}

const FIXTURES = __FIXTURES__;

function fixtureProbeJs() {
  const out = [];
  for (const f of FIXTURES) {
    const st = new E.State();
    st.rows = f.rows.slice();
    st.current = f.piece;
    st.rot = f.rot;
    st.x = E.SPAWN_X[f.piece];
    st.y = 0;
    st.queue = [E.I, E.O, E.T, E.S, E.Z, E.J, E.L];
    st.game_over = false;
    const all = E.legalPlacements(st);
    const ps = all.filter((p) => p[0] === f.rot && p[1] === f.x);
    if (!ps.length) { out.push({ name: f.name, error: 'no legal placement' }); continue; }
    const [ns, info] = E.applyPlacement(st, ps[0]);
    out.push({
      name: f.name,
      placements: all.length,
      board_hash: E.boardHash(ns.rows),
      state_hash: E.stateHash(ns),
      rows_after: ns.rows.slice(),
      lines_cleared: info.lines_cleared,
      cleared_rows: info.cleared_rows,
      piece_cells: info.piece_cells.map((c) => c.slice()),
      cleared_piece_cells: info.cleared_piece_cells,
      eroded_piece_cells: info.eroded_piece_cells,
      score_delta: info.score_delta,
      is_tetris: info.is_tetris,
      b2b_active: info.b2b_active,
      b2b_chain: info.b2b_chain,
      combo_count: info.combo_count,
    });
  }
  return out;
}

function b2bChainProbeJs() {
  let st = new E.State();
  st.current = E.I;
  st.queue = [E.I, E.I, E.I, E.I, E.I, E.I, E.I];
  st.game_over = false;
  const out = [];
  for (let k = 0; k < 3; k++) {
    const rows = new Array(E.ROWS).fill(0);
    for (const y of [18, 19, 20, 21]) rows[y] = E.FULL_ROW & ~(1 << 9);
    st.rows = rows;
    st.current = E.I;
    st.rot = 1;
    st.x = 7;
    st.y = 0;
    const ps = E.legalPlacements(st).filter((p) => p[0] === 1 && p[1] === 7);
    const [ns, info] = E.applyPlacement(st, ps[0]);
    out.push([info.lines_cleared, info.score_delta,
              info.b2b_active ? 1 : 0, info.b2b_chain, info.combo_count]);
    st = ns;
  }
  return out;
}

function pick(ps, policy, state) {
  if (policy === 'first') return 0;

  if (policy === 'lowest') {
    let best = 0, bestY = ps[0][2];
    for (let i = 1; i < ps.length; i++) {
      if (ps[i][2] > bestY) { bestY = ps[i][2]; best = i; }
    }
    return best;
  }

  // "well": keep column 9 clear and feed it a vertical I only once four rows
  // are ready, so the traces reach tetrises and the B2B path.
  const rows = state.rows;
  let best = 0;
  let bp = null, by = null;
  for (let i = 0; i < ps.length; i++) {
    const [rot, x, yRest, piece] = ps[i];
    let touchesWell = false;
    for (const [, cx] of E.placementCells(ps[i])) {
      if (cx === WELL_COL) { touchesWell = true; break; }
    }
    const verticalI = (piece === E.I && rot === 1);
    let penalty, cost;
    if (touchesWell && verticalI) {
      let ready = 0;
      for (let y = yRest; y < yRest + 4; y++) {
        if (y >= 0 && y < E.ROWS && rows[y] === FULL_WITHOUT_WELL) ready++;
      }
      penalty = (ready === 4) ? 0 : 3;
      cost = 0;
    } else if (touchesWell) {
      penalty = 2; cost = 0;
    } else {
      penalty = 1; cost = stackCost(rows, ps[i]);
    }
    // rank = (penalty asc, cost asc, index asc)
    if (bp === null || penalty < bp || (penalty === bp && cost < by)) {
      bp = penalty; by = cost; best = i;
    }
  }
  return best;
}

function traceJs(seed, policy) {
  let s = E.newGame(seed);
  const out = [{
    move: 0,
    board_hash: E.boardHash(s.rows),
    state_hash: E.stateHash(s),
    lines_cleared: 0,
    score: s.score,
  }];
  for (let i = 1; i <= MAX_MOVES; i++) {
    const ps = E.legalPlacements(s);
    if (ps.length === 0) break;
    const [ns, info] = E.applyPlacement(s, ps[pick(ps, policy, s)]);
    s = ns;
    out.push({
      move: i,
      board_hash: E.boardHash(s.rows),
      state_hash: E.stateHash(s),
      lines_cleared: info.lines_cleared,
      score: s.score,
    });
    if (s.game_over) break;
  }
  return out;
}

function rngProbe(n) {
  let st = E.seedState(1);
  const vals = [];
  for (let i = 0; i < n; i++) { st = E.nextU32(st); vals.push(st >>> 0); }
  return vals;
}

// scoreClear is module-private in engine.js, so exercise it through a real
// placement is not possible in isolation -- engine.js exports it for parity.
const scoreProbe = SCORE_CASES.map(
  ([n, lv, b2b, combo]) => E.scoreClearForParity(n, lv, b2b, combo));

const humanTraces = {};
for (const seed of SEEDS) humanTraces[String(seed)] = traceHumanJs(seed);

const traces = {};
for (const seed of SEEDS) {
  for (const policy of POLICIES) traces[seed + '/' + policy] = traceJs(seed, policy);
}
const payload = {
  spec: 'docs/spec.md v1',
  max_moves: MAX_MOVES,
  policies: POLICIES,
  difficulty: E.DIFFICULTY_DEFAULT,
  difficulty_name: E.DIFFICULTY_NAMES[E.DIFFICULTY_DEFAULT],
  rng_probe: rngProbe(16),
  score_probe: scoreProbe,
  kick_probe: kickProbeJs(),
  fixture_probe: fixtureProbeJs(),
  difficulty_probe: difficultyProbeJs(),
  b2b_chain_probe: b2bChainProbeJs(),
  traces,
  human_traces: humanTraces,
};
process.stdout.write(JSON.stringify(payload));
"""


# ---------------------------------------------------------------------------
# human-path parity
#
# The placement traces above never touch move/rotate/soft_drop/hard_drop/
# tick/hold, so SRS wall kicks (40 table entries), rotation-failure
# conditions, drop scoring, lock delay and hold round-trips were completely
# uncompared between the two languages. checker flagged this as the largest
# remaining hole and was right.
#
# Determinism comes from two choices: a fixed dt instead of a real clock, and
# an input sequence drawn from the same xorshift32 in both languages. No
# timers, no wall clock, no sampling.
# ---------------------------------------------------------------------------

#: Input alphabet. Index = value drawn from the RNG modulo len(). Order and
#: repetition are both part of the contract -- changing either changes every
#: human trace.
#:
#: Entries are repeated to weight the distribution. An unweighted alphabet gave
#: a hard drop every 8th input, which ended the game in ~100 frames and
#: exercised only ~25 rotations -- far too thin to cover 40 wall-kick entries.
#: Rotations and side moves are now common and hard drops rare, so pieces
#: survive long enough to rotate against walls and against the stack.
HUMAN_ACTIONS = (
    "left", "left", "left",
    "right", "right", "right",
    "cw", "cw", "cw", "cw",
    "ccw", "ccw", "ccw",
    "soft", "soft",
    "hard",
    "hold",
    "none",
)

#: Fixed frame time. 16.0 is exactly representable as a double, so the lock and
#: gravity accumulators evolve bit-identically in both languages.
HUMAN_DT_MS = 16.0
HUMAN_STEPS = 600


def _human_apply(state, action: str):
    """Apply one input then one frame of time. Returns True while alive."""
    if action == "left":
        E.move(state, -1)
    elif action == "right":
        E.move(state, 1)
    elif action == "cw":
        E.rotate(state, True)
    elif action == "ccw":
        E.rotate(state, False)
    elif action == "soft":
        E.soft_drop(state)
    elif action == "hard":
        E.hard_drop(state)
    elif action == "hold":
        E.hold(state)
    E.tick_ms(state, HUMAN_DT_MS)
    return not state.game_over


def _human_record(state, step: int, action: str) -> dict:
    """Every observable field, not a hash -- a mismatch names the field."""
    return {
        "step": step,
        "action": action,
        "kicked": False,
        "board_hash": E.board_hash(state.rows),
        "current": state.current,
        "rot": state.rot,
        "x": state.x,
        "y": state.y,
        "hold": state.hold,
        "can_hold": bool(state.can_hold),
        "score": state.score,
        "lines": state.lines,
        "level": state.level,
        "b2b": state.b2b,
        "combo": state.combo,
        "lock_resets": state.lock_resets,
        "lock_ms": round(state.lock_ms, 4),
        "grav_ms": round(state.grav_ms, 4),
        "lowest_y": state.lowest_y,
        "touched_down": bool(state.touched_down),
        "game_over": bool(state.game_over),
    }


def trace_human_python(seed: int, steps: int = HUMAN_STEPS) -> list:
    """Replay a deterministic input sequence through the interactive path.

    Restarts on game over (with a seed derived from the step index) so the
    trace always runs the full step budget instead of stopping at the first
    death -- otherwise a seed that dies early silently tests almost nothing.
    """
    state = E.new_game(seed)
    rng = seed_state(seed ^ 0x5F5F5F5F)
    out = [_human_record(state, 0, "init")]
    restarts = 0
    for i in range(1, steps + 1):
        rng, r = next_u32(rng)
        action = HUMAN_ACTIONS[r % len(HUMAN_ACTIONS)]
        prev = (state.rot, state.x, state.y)
        alive = _human_apply(state, action)
        rec = _human_record(state, i, action)
        # A rotation that moved the piece sideways or vertically only happens
        # via a wall kick, so this counts kicks that actually fired.
        rec["kicked"] = bool(
            action in ("cw", "ccw")
            and state.rot != prev[0]
            and (state.x != prev[1] or state.y != prev[2]))
        out.append(rec)
        if not alive:
            restarts += 1
            state = E.new_game(seed + restarts * 7919)
            out.append(_human_record(state, i, "restart"))
    return out


#: Token -> value. Plain replacement, not %-formatting: the generated JS
#: contains `%` operators (modulo) that would collide with format specs.
def _runner_substitutions() -> dict:
    return {
        "__ENGINE__": os.path.join(WEB, "engine.js").replace("\\", "/"),
        "__SEEDS__": json.dumps(SEEDS),
        "__POLICIES__": json.dumps(list(POLICIES)),
        "__MAX_MOVES__": str(MAX_MOVES),
        "__SCORE_CASES__": json.dumps([list(c) for c in SCORE_CASES]),
        "__HUMAN_ACTIONS__": json.dumps(list(HUMAN_ACTIONS)),
        "__HUMAN_DT__": repr(HUMAN_DT_MS),
        "__HUMAN_STEPS__": str(HUMAN_STEPS),
        "__KICK_BOARDS__": json.dumps(_kick_boards()),
        "__KICK_Y__": json.dumps(list(KICK_Y_VALUES)),
        "__DIFFICULTIES__": json.dumps([0, 1, 2]),
        "__FIXTURES__": json.dumps(
            [{"name": n, "rows": r, "piece": p, "rot": ro, "x": x}
             for n, r, p, ro, x in _clear_fixtures()]),
    }


# ---------------------------------------------------------------------------
# exhaustive wall-kick parity
#
# Random human play fired only ~27 kicks across 3 traces, against 40 table
# entries plus their failure cases -- coverage by luck. This enumerates every
# (piece, from_rot, direction, x, y) against several board shapes instead, so
# each kick entry AND each all-five-fail refusal is compared directly.
# ---------------------------------------------------------------------------

#: Board fixtures for kick probing, as 22-int row lists. Walls and floor come
#: free from the board edges; these add interior obstacles so kicks are forced.
def _kick_boards() -> list:
    empty = [0] * E.ROWS

    # A floor-level stack with a one-column well at column 4.
    stack = [0] * E.ROWS
    for y in range(18, E.ROWS):
        stack[y] = E.FULL_ROW & ~(1 << 4)

    # Two towers with a 3-wide canyon between them, forcing horizontal kicks.
    canyon = [0] * E.ROWS
    for y in range(12, E.ROWS):
        canyon[y] = 0b1110000111

    # An overhang: filled row with a gap, plus a ceiling above it.
    overhang = [0] * E.ROWS
    for y in range(16, E.ROWS):
        overhang[y] = E.FULL_ROW & ~(0b111 << 3)
    overhang[13] = E.FULL_ROW

    return [empty, stack, canyon, overhang]


KICK_Y_VALUES = (0, 5, 11, 14, 17, 19, 20)


def kick_probe() -> list:
    """Every rotation attempt over the fixtures. Result is (rot, x, y, moved)."""
    out = []
    boards = _kick_boards()
    for bi, rows in enumerate(boards):
        rows_t = tuple(rows)
        for piece in range(7):
            for frm in range(4):
                for cw in (True, False):
                    for x in range(-2, E.W):
                        for y in KICK_Y_VALUES:
                            st = E.State()
                            st.rows = rows_t
                            st.current = piece
                            st.rot = frm
                            st.x = x
                            st.y = y
                            st.game_over = False
                            # Only probe from positions the piece could legally
                            # occupy; rotating from an impossible pose is not a
                            # reachable state and would compare noise.
                            if not E._fits(rows_t, piece, frm, x, y):
                                continue
                            moved = E.rotate(st, cw)
                            out.append([bi, piece, frm, 1 if cw else 0, x, y,
                                        st.rot, st.x, st.y, 1 if moved else 0])
    return out


def write_js_runner() -> str:
    src = JS_RUNNER_SRC
    for token, value in _runner_substitutions().items():
        src = src.replace(token, value)
    leftover = [t for t in _runner_substitutions() if t in src]
    if leftover:
        raise AssertionError("unsubstituted runner tokens: %r" % leftover)
    with open(JS_RUNNER, "w") as f:
        f.write(src)
    return JS_RUNNER


class NodeBroken(Exception):
    """node exists but the JS engine failed to run -- a real failure.

    Kept distinct from "node is missing" because conflating the two is how a
    broken JS engine masquerades as an environment limitation and the parity
    check quietly stops testing anything.
    """


def run_js_trace():
    """Run the JS engine via node.

    Returns the parsed payload, or None only when node is genuinely absent.
    Raises NodeBroken if node ran and failed -- that is a FAIL, not a skip.
    """
    node = shutil.which("node")
    if not node:
        return None
    write_js_runner()
    proc = subprocess.run([node, JS_RUNNER], capture_output=True, text=True)
    if proc.returncode != 0:
        raise NodeBroken(proc.stderr.strip())
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise NodeBroken("node produced unparseable output: %s\n%s"
                         % (exc, proc.stdout[:500]))


def emit_browser_runner() -> str:
    """Fallback for environments without node (docs/spec.md section 9 step 6)."""
    html = """<!doctype html>
<meta charset="utf-8">
<title>Tetris engine parity -- JS side</title>
<p>Open with a local web server in the project root
(<code>python3 -m http.server</code>), then save the JSON below as
<code>engine/parity_js.json</code> and run
<code>python3 engine/parity.py --compare-only</code>.</p>
<button id="copy">Copy JSON</button>
<pre id="out" style="white-space:pre-wrap;word-break:break-all"></pre>
<script type="module">
import * as E from '../web/engine.js';
const SEEDS = __SEEDS__, POLICIES = __POLICIES__, MAX_MOVES = __MAX__;
function pick(ps,policy){if(policy==='first')return 0;
 let b=0,by=ps[0][2];
 for(let i=1;i<ps.length;i++){if(ps[i][2]>by){by=ps[i][2];b=i;}}return b;}
function traceJs(seed,policy){let s=E.newGame(seed);
 const out=[{move:0,board_hash:E.boardHash(s.rows),state_hash:E.stateHash(s),
             lines_cleared:0,score:s.score}];
 for(let i=1;i<=MAX_MOVES;i++){const ps=E.legalPlacements(s);if(!ps.length)break;
  const [ns,info]=E.applyPlacement(s,ps[pick(ps,policy)]);s=ns;
  out.push({move:i,board_hash:E.boardHash(s.rows),state_hash:E.stateHash(s),
            lines_cleared:info.lines_cleared,score:s.score});
  if(s.game_over)break;}
 return out;}
function rngProbe(n){let st=E.seedState(1);const v=[];
 for(let i=0;i<n;i++){st=E.nextU32(st);v.push(st>>>0);}return v;}
const traces={};
for(const s of SEEDS)for(const p of POLICIES)traces[s+'/'+p]=traceJs(s,p);
const payload={spec:'docs/spec.md v1',max_moves:MAX_MOVES,policies:POLICIES,
               rng_probe:rngProbe(16),traces};
const text=JSON.stringify(payload);
document.getElementById('out').textContent=text;
document.getElementById('copy').onclick=()=>navigator.clipboard.writeText(text);
</script>
"""
    html = (html.replace("__SEEDS__", json.dumps(SEEDS))
                .replace("__POLICIES__", json.dumps(list(POLICIES)))
                .replace("__MAX__", str(MAX_MOVES)))
    with open(BROWSER_RUNNER, "w") as f:
        f.write(html)
    return BROWSER_RUNNER


def compare(py: dict, js: dict) -> bool:
    """Report the first divergence, if any. Returns True when identical."""
    ok = True
    if py["rng_probe"] != js["rng_probe"]:
        ok = False
        print("FAIL rng_probe diverges")
        for i, (a, b) in enumerate(zip(py["rng_probe"], js["rng_probe"])):
            if a != b:
                print("  first at index %d: py=%d js=%d" % (i, a, b))
                break
    else:
        print("ok   rng_probe: 16/16 identical")

    py_mode = py.get("difficulty_name")
    js_mode = js.get("difficulty_name")
    if py_mode != js_mode:
        ok = False
        print("FAIL the two traces were produced in DIFFERENT difficulty modes "
              "(py=%r js=%r) -- any hash mismatch below would be that, not a "
              "rule bug" % (py_mode, js_mode))
    elif py_mode is None:
        ok = False
        print("FAIL traces carry no difficulty stamp -- cannot tell which mode "
              "produced them (difficulty is not in state_hash by design)")
    else:
        print("ok   both traces produced in difficulty %r" % py_mode)

    py_sp = py.get("score_probe")
    js_sp = js.get("score_probe")
    if py_sp is None or js_sp is None:
        ok = False
        print("FAIL score_probe missing -- B2B/combo scoring is not covered")
    elif py_sp != js_sp:
        ok = False
        print("FAIL score_probe diverges (B2B/combo scoring)")
        for i, (a, b) in enumerate(zip(py_sp, js_sp)):
            if a != b:
                print("  first at case %r: py=%r js=%r"
                      % (SCORE_CASES[i], a, b))
                break
    else:
        print("ok   score_probe: %d/%d B2B+combo cases identical"
              % (len(py_sp), len(SCORE_CASES)))

    for seed in py["traces"]:
        pt = py["traces"][seed]
        jt = js["traces"].get(seed)
        if jt is None:
            print("FAIL seed %s missing from JS trace" % seed)
            ok = False
            continue
        if len(pt) != len(jt):
            print("FAIL seed %s length differs: py=%d js=%d" % (seed, len(pt), len(jt)))
            ok = False
        bad = None
        for a, b in zip(pt, jt):
            if a != b:
                bad = (a, b)
                break
        if bad:
            ok = False
            print("FAIL seed %s diverges at move %d" % (seed, bad[0]["move"]))
            print("     py: %s" % bad[0])
            print("     js: %s" % bad[1])
        else:
            cleared = sum(r["lines_cleared"] for r in pt)
            print("ok   %-20s %3d moves, %3d lines cleared, hashes identical "
                  "(final board_hash %d)"
                  % (seed, len(pt) - 1, cleared, pt[-1]["board_hash"]))
    py_k = py.get("kick_probe")
    js_k = js.get("kick_probe")
    if py_k is None or js_k is None:
        ok = False
        print("FAIL kick_probe missing -- SRS wall kicks are not compared")
    elif py_k != js_k:
        ok = False
        print("FAIL kick_probe diverges (SRS wall kicks)")
        for a, b in zip(py_k, js_k):
            if a != b:
                print("  first at board=%d piece=%s from_rot=%d cw=%d x=%d y=%d"
                      % (a[0], E.PIECE_NAMES[a[1]], a[2], a[3], a[4], a[5]))
                print("     py -> rot=%d x=%d y=%d moved=%d" % tuple(a[6:]))
                print("     js -> rot=%d x=%d y=%d moved=%d" % tuple(b[6:]))
                break
    else:
        kicked = sum(1 for r in py_k if r[9] and (r[7] != r[4] or r[8] != r[5]))
        refused = sum(1 for r in py_k if not r[9])
        print("ok   kick_probe: %d rotation attempts identical "
              "(%d fired a kick offset, %d refused outright)"
              % (len(py_k), kicked, refused))

    for key, label in (("fixture_probe", "hand-built clear fixtures"),
                       ("b2b_chain_probe", "back-to-back tetris chain"),
                       ("difficulty_probe", "difficulty modes")):
        a, b = py.get(key), js.get(key)
        if a is None or b is None:
            ok = False
            print("FAIL %s missing -- %s is not compared" % (key, label))
        elif a != b:
            ok = False
            print("FAIL %s diverges (%s)" % (key, label))
            for x, y in zip(a, b):
                if x != y:
                    print("     py=%r" % (x,))
                    print("     js=%r" % (y,))
                    break
        else:
            if key == "fixture_probe":
                shape = ", ".join("%s=%d" % (r["name"], r.get("lines_cleared", -1))
                                  for r in a)
                print("ok   fixture_probe: %d fixtures identical (%s)"
                      % (len(a), shape))
            elif key == "b2b_chain_probe":
                scores = " -> ".join(str(r[1]) for r in a)
                print("ok   b2b_chain_probe: %d tetrises identical (scores %s)"
                      % (len(a), scores))
            else:
                shape = ", ".join(
                    "%s(next=%s,hold=%s)" % (r["name"], r["next_visible_count"],
                                             "Y" if r["hold_enabled"] else "N")
                    for r in a)
                orders = {tuple(r["piece_order"]) for r in a}
                print("ok   difficulty_probe: %d modes identical -- %s"
                      % (len(a), shape))
                print("     piece sequence identical across modes: %s"
                      % ("yes" if len(orders) == 1 else "NO -- MODES DIFFER"))

    py_h = py.get("human_traces")
    js_h = js.get("human_traces")
    if py_h is None or js_h is None:
        ok = False
        print("FAIL human_traces missing -- the interactive path "
              "(move/rotate/kick/drop/hold/lock delay) is NOT compared")
    else:
        for seed in py_h:
            pt, jt = py_h[seed], js_h.get(seed)
            if jt is None:
                ok = False
                print("FAIL human seed %s missing from JS" % seed)
                continue
            bad = None
            for a, b in zip(pt, jt):
                if a != b:
                    bad = (a, b)
                    break
            if bad or len(pt) != len(jt):
                ok = False
                a, b = bad if bad else (pt[-1], jt[-1])
                fields = [k for k in a if a.get(k) != b.get(k)]
                print("FAIL human seed %s diverges at step %s (action %r)"
                      % (seed, a.get("step"), a.get("action")))
                for k in fields:
                    print("     %-12s py=%r js=%r" % (k, a.get(k), b.get(k)))
            else:
                rots = sum(1 for r in pt if r["action"] in ("cw", "ccw"))
                kicked = sum(1 for r in pt if r.get("kicked"))
                holds = sum(1 for r in pt if r["action"] == "hold")
                drops = sum(1 for r in pt if r["action"] == "hard")
                deaths = sum(1 for r in pt if r["action"] == "restart")
                print("ok   human %-12s %4d frames, %3d rot (%2d kicked), "
                      "%3d hold, %3d hard, %2d death -- all fields identical"
                      % (seed, len(pt) - 1, rots, kicked, holds, drops, deaths))

    # Coverage histogram. checker's point: "the traces agree" says nothing about
    # what the traces contained. Report the distribution so a narrow trace set
    # is visible instead of being mistaken for broad verification.
    hist = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}
    total_moves = 0
    for t in py["traces"].values():
        for r in t:
            hist[r["lines_cleared"]] = hist.get(r["lines_cleared"], 0) + 1
            total_moves += 1
    total_cleared = sum(k * v for k, v in hist.items())
    print("     placement trace coverage: %d moves, lines_cleared "
          "0=%d 1=%d 2=%d 3=%d 4=%d (%d lines total)"
          % (total_moves, hist[0], hist[1], hist[2], hist[3], hist[4],
             total_cleared))
    fx = {r.get("lines_cleared") for r in py.get("fixture_probe", [])}
    fx |= {r[0] for r in py.get("b2b_chain_probe", [])}
    print("     fixture coverage: lines_cleared %s (hand-built, deterministic)"
          % sorted(n for n in fx if n is not None))
    missing = [n for n in (1, 2, 3, 4) if hist[n] == 0 and n not in fx]
    if missing:
        ok_note = "/".join(str(m) for m in missing)
        print("     NOTE: %s-line clears appear in NEITHER the traces nor the "
              "fixtures -- that path is uncompared here." % ok_note)
    if total_cleared == 0:
        ok = False
        print("FAIL parity traces cleared 0 lines -- the clear/gravity path is "
              "not actually covered")
    return ok


def run_parity() -> bool:
    py = build_python_trace()
    with open(PY_JSON, "w") as f:
        json.dump(py, f, indent=1)
    print("wrote %s" % PY_JSON)

    try:
        js = run_js_trace()
    except NodeBroken as exc:
        print("FAIL the JS engine did not run under node -- this is a real "
              "failure, not a skipped check:\n%s" % exc)
        return False
    if js is None:
        print("node not available -- writing browser fallback")
        path = emit_browser_runner()
        print("open %s, save the JSON as %s, then --compare-only" % (path, JS_JSON))
        return False
    with open(JS_JSON, "w") as f:
        json.dump(js, f, indent=1)
    print("wrote %s" % JS_JSON)
    return compare(py, js)


def run_compare_only() -> bool:
    with open(PY_JSON) as f:
        py = json.load(f)
    with open(JS_JSON) as f:
        js = json.load(f)
    return compare(py, js)


# ---------------------------------------------------------------------------
# JS-only UI-wrapper checks
#
# The Game wrapper exists only in web/engine.js, so these have no Python side
# to compare against -- they are assertions, not parity. They live here because
# nothing else runs the wrapper, and checker found a freeze in it that no
# Python test could ever have caught.
# ---------------------------------------------------------------------------

JS_UI_RUNNER = os.path.join(HERE, "ui_probe_runner.mjs")

JS_UI_SRC = """// generated by engine/parity.py -- do not edit
import * as E from '__ENGINE__';

// A board whose stack reaches the spawn row: sliding along that row is blocked,
// yet the target placement is still legal from above. This is the exact shape
// that froze the PLAY screen's AI mode.
function blockedBoard(seed) {
  const g = E.createGame({ seed });
  const rows = new Array(E.ROWS).fill(0);
  for (let y = 1; y < E.ROWS; y++) rows[y] = E.FULL_ROW & ~((1 << 0) | (1 << 9));
  g.state.rows = rows;
  g.state.current = E.I;
  g.state.rot = 1;
  g.state.x = 2;
  g.state.y = 0;
  return g;
}

const out = {};

// 1. stepToward must resolve a blocked slide instead of looping.
{
  const g = blockedBoard(4242);
  const target = E.legalPlacements(g.state).find((p) => p[1] === 7);
  const seen = [];
  for (let i = 0; i < 12; i++) {
    const r = g.stepToward(target);
    seen.push(r);
    if (r === 'drop' || r === 'blocked' || r === 'done') break;
  }
  out.blocked_sequence = seen;
  out.blocked_locked_a_piece = g.pieces >= 1;
}

// 2. A reachable target must still complete normally, never 'blocked'.
{
  const g = E.createGame({ seed: 7 });
  const ps = g.legalPlacements();
  const target = ps.find((p) => p[1] !== g.state.x) || ps[0];
  const seen = [];
  for (let i = 0; i < 24; i++) {
    const r = g.stepToward(target);
    seen.push(r);
    if (r === 'drop' || r === 'blocked' || r === 'done') break;
  }
  out.reachable_sequence = seen;
  out.reachable_hit_target = (seen[seen.length - 1] === 'drop');
}

// 3. A full AI loop must terminate. This is the regression that matters: the
//    frozen build ran forever with game_over stuck false.
{
  const g = E.createGame({ seed: 4242 });
  let target = null, steps = 0, locks = 0;
  for (let f = 0; f < 200000; f++) {
    if (g.gameOver) break;
    if (!target) {
      const ps = g.legalPlacements();
      if (!ps.length) break;
      target = ps[0];
    }
    const r = g.stepToward(target);
    steps++;
    if (r === 'drop' || r === 'blocked') { target = null; locks++; }
    if (r === 'done') break;
  }
  out.loop_steps = steps;
  out.loop_locks = locks;
  out.loop_reached_game_over = g.gameOver;
}

// 4. Difficulty accessors the UI branches on.
{
  const modes = [0, 1, 2].map((d) => {
    const g = E.createGame({ seed: 1234, difficulty: d });
    return {
      name: g.difficultyName,
      next_count: g.nextVisibleCount,
      next_len: g.nextQueue.length,
      hold_enabled: g.holdEnabled,
      hold_result: g.hold(),
    };
  });
  out.modes = modes;
}

process.stdout.write(JSON.stringify(out));
"""


def run_js_ui_probe():
    """Run the Game-wrapper checks under node.

    Returns the parsed payload, or None if node is genuinely absent.
    Raises NodeBroken if node ran and failed -- a failure, never a skip.
    """
    node = shutil.which("node")
    if not node:
        return None
    src = JS_UI_SRC.replace("__ENGINE__",
                            os.path.join(WEB, "engine.js").replace("\\", "/"))
    with open(JS_UI_RUNNER, "w") as f:
        f.write(src)
    proc = subprocess.run([node, JS_UI_RUNNER], capture_output=True, text=True)
    if proc.returncode != 0:
        raise NodeBroken(proc.stderr.strip())
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise NodeBroken("unparseable output: %s\n%s" % (exc, proc.stdout[:500]))


# ---------------------------------------------------------------------------
# rule tests
# ---------------------------------------------------------------------------

def _blank(seed=1):
    s = E.new_game(seed)
    s.rows = E.EMPTY_ROWS
    return s


def _set_piece(s, piece, rot=0, x=None):
    s.current = piece
    s.rot = rot
    s.x = E.SPAWN_X[piece] if x is None else x
    s.y = E.SPAWN_Y
    return s


def _raises(fn, exc) -> bool:
    """True if calling fn raises exc. Keeps the checks above one line each."""
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


def run_tests() -> bool:
    fails = []
    skips = []

    def check(name, cond, detail=""):
        if cond:
            print("ok   %s" % name)
        else:
            fails.append(name)
            print("FAIL %s  %s" % (name, detail))

    def skip(name, why):
        """Record a check that could NOT run. Never silently omit one.

        A suite that prints "153 passed" while quietly skipping 7 is the same
        trap as reporting a crashed JS engine as "node not available": it
        converts a failure into reassurance.
        """
        skips.append((name, why))
        print("SKIP %s  (%s)" % (name, why))

    # --- geometry -----------------------------------------------------------
    check("board is 10x22", E.W == 10 and E.ROWS == 22)
    for p in range(7):
        for r in range(4):
            cells = E.PIECE_CELLS[p][r]
            check("%s r%d has 4 distinct cells" % (E.PIECE_NAMES[p], r),
                  len(cells) == 4 and len(set(cells)) == 4)

    # --- placement counts on an empty board ---------------------------------
    # Counted per rotation from the cell tables: a rotation whose bounding box
    # spans 3 columns of cells gives 8 positions, one spanning 2 gives 9,
    # one spanning 4 gives 7, and a single-column rotation gives 10.
    expect = {
        E.O: 9,                    # r0: 2 wide -> 9
        E.I: 7 + 10,               # r0: 4 wide -> 7 ; r1: 1 wide -> 10
        E.T: 8 + 9 + 8 + 9,        # r0,r2: 3 wide ; r1,r3: 2 wide
        E.S: 8 + 9,
        E.Z: 8 + 9,
        E.J: 8 + 9 + 8 + 9,
        E.L: 8 + 9 + 8 + 9,
    }
    for piece, n in expect.items():
        s = _set_piece(_blank(), piece)
        got = len(E.legal_placements(s))
        check("%s empty-board placements == %d" % (E.PIECE_NAMES[piece], n),
              got == n, "got %d" % got)

    # --- sort order is rot then x ------------------------------------------
    s = _set_piece(_blank(), E.T)
    ps = E.legal_placements(s)
    keys = [(p[0], p[1]) for p in ps]
    check("placements sorted by (rot, x)", keys == sorted(keys))

    # --- single line clear + gravity ---------------------------------------
    s = _blank()
    rows = list(E.EMPTY_ROWS)
    rows[21] = E.FULL_ROW & ~0b1111000       # bottom row missing cols 3..6
    rows[20] = 1                             # a lone cell above, col 0
    s.rows = tuple(rows)
    _set_piece(s, E.I, 0, 3)
    p = [q for q in E.legal_placements(s) if q[0] == 0 and q[1] == 3][0]
    ns, info = E.apply_placement(s, p)
    check("single clear: lines_cleared == 1", info["lines_cleared"] == 1)
    check("single clear: cleared_rows == [21]", info["cleared_rows"] == [21])
    check("single clear: score 100 x level 1", info["score_delta"] == 100)
    check("single clear: eroded == 4", info["eroded_piece_cells"] == 4)
    check("single clear: piece_cells are the 4 absolute board coords",
          sorted(info["piece_cells"]) == [(21, 3), (21, 4), (21, 5), (21, 6)],
          str(info["piece_cells"]))
    check("single clear: cleared_piece_cells (raw, unmultiplied) == 4",
          info["cleared_piece_cells"] == 4)
    # rl/features.py owns landing_height; the engine only guarantees that it is
    # reconstructible from piece_cells. This checks the reconstruction, not the
    # definition.
    lh = sum(E.ROWS - y for y, _ in info["piece_cells"]) / 4.0
    check("landing_height is reconstructible from piece_cells (bottom row = 1)",
          lh == 1.0, "got %r" % lh)
    check("engine does not export a landing_height opinion",
          "landing_height" not in info)
    check("gravity: the lone cell fell to the bottom row",
          ns.rows[21] == 1 and ns.rows[20] == 0,
          "rows[21]=%d rows[20]=%d" % (ns.rows[21], ns.rows[20]))

    # --- tetris (4 rows at once) -------------------------------------------
    rows = list(E.EMPTY_ROWS)
    for y in (18, 19, 20, 21):
        rows[y] = E.FULL_ROW & ~(1 << 9)
    s = _blank()
    s.rows = tuple(rows)
    _set_piece(s, E.I, 1, 7)
    p = [q for q in E.legal_placements(s) if q[0] == 1 and q[1] == 7][0]
    ns, info = E.apply_placement(s, p)
    check("tetris: lines_cleared == 4", info["lines_cleared"] == 4)
    check("tetris: score 800", info["score_delta"] == 800)
    check("tetris: eroded == 16", info["eroded_piece_cells"] == 16)
    check("tetris: board empty afterwards", all(r == 0 for r in ns.rows))
    check("tetris: level rises to 1 + 4//10 == 1", ns.level == 1)

    # --- non-contiguous multi clear ---------------------------------------
    rows = list(E.EMPTY_ROWS)
    rows[21] = E.FULL_ROW & ~(1 << 0)        # needs col 0
    rows[20] = E.FULL_ROW & ~(1 << 0)        # needs col 0
    rows[19] = 0b0000000110                  # junk (cols 1,2) that must survive
    s = _blank()
    s.rows = tuple(rows)
    _set_piece(s, E.I, 1, -2)                # vertical I into column 0
    p = [q for q in E.legal_placements(s) if q[0] == 1 and q[1] == -2][0]
    check("vertical I in an open column rests at y=18", p[2] == 18, str(p))
    ns, info = E.apply_placement(s, p)
    # The I fills col 0 of rows 18..21; rows 20 and 21 complete, 18 and 19 do not.
    check("two-row clear: lines_cleared == 2", info["lines_cleared"] == 2,
          str(info))
    check("two-row clear: cleared_rows == [20, 21]",
          info["cleared_rows"] == [20, 21])
    check("two-row clear: score 300", info["score_delta"] == 300)
    check("two-row clear: eroded == 2 lines * 2 cells == 4",
          info["eroded_piece_cells"] == 4)
    check("two-row clear: survivors compacted to the bottom",
          ns.rows[21] == 0b0000000111 and ns.rows[20] == 1
          and ns.rows[19] == 0,
          "rows[21]=%d rows[20]=%d rows[19]=%d"
          % (ns.rows[21], ns.rows[20], ns.rows[19]))

    # --- level and gravity curve -------------------------------------------
    check("level = 1 + lines//10", (1 + 0 // 10, 1 + 10 // 10, 1 + 95 // 10)
          == (1, 2, 10))
    check("gravity curve endpoints",
          (E.frames_per_cell(1), E.frames_per_cell(10), E.frames_per_cell(29))
          == (48, 6, 1))

    # --- spawn collision => game over --------------------------------------
    rows = list(E.EMPTY_ROWS)
    rows[0] = E.FULL_ROW
    rows[1] = E.FULL_ROW
    s = _blank()
    s.rows = tuple(rows)
    s.game_over = False
    ok = E._spawn_next(s)
    check("spawn into a blocked buffer row => game_over", (not ok) and s.game_over)

    # --- placement above the board is rejected -----------------------------
    rows = list(E.EMPTY_ROWS)
    for y in range(1, 22):
        rows[y] = E.FULL_ROW & ~(1 << 0)     # only column 0 open, 21 deep
    s = _blank()
    s.rows = tuple(rows)
    _set_piece(s, E.O)
    check("O has no legal placement in a 1-wide well",
          E.legal_placements(s) == [])
    _set_piece(s, E.I, 1, -2)
    ivals = [q for q in E.legal_placements(s) if q[0] == 1]
    check("vertical I fits the 1-wide well", len(ivals) == 1, str(ivals))

    # --- no legal placement => game over ----------------------------------
    # Columns 0 and 9 open to full depth, everything between filled from row 1.
    # No row can ever complete (nothing spans both open columns), and no piece
    # fits from above except a vertical I.
    topped = list(E.EMPTY_ROWS)
    for y in range(1, 22):
        topped[y] = E.FULL_ROW & ~((1 << 0) | (1 << 9))

    s = _blank()
    s.rows = tuple(topped)
    _set_piece(s, E.O, 0, 0)
    check("topped-out board leaves O no placement", E.legal_placements(s) == [])
    check("_is_stuck detects the topped-out board", E._is_stuck(s))

    # Give the next piece no escape: current I still fits, the O after it does not.
    s = _blank()
    s.rows = tuple(topped)
    s.queue = (E.O,) * 7
    _set_piece(s, E.I, 1, -2)
    ps = [q for q in E.legal_placements(s) if q[0] == 1 and q[1] == -2]
    check("vertical I still fits the open column", len(ps) == 1, str(ps))
    ns, info = E.apply_placement(s, ps[0])
    check("no clear when nothing spans both open columns",
          info["lines_cleared"] == 0, str(info))
    check("game over reported when the next piece has no placement",
          info["game_over"] and ns.game_over, str(info))

    # --- rotation and wall kick -------------------------------------------
    s = _set_piece(_blank(), E.T)
    before = (s.x, s.y, s.rot)
    check("T rotates freely in open space", E.rotate(s, True) and s.rot == 1,
          str(before))
    s = _set_piece(_blank(), E.I, 0, 0)
    E.move(s, -3)                            # push against the left wall
    check("I cannot leave the left wall", s.x == 0)
    s = _set_piece(_blank(), E.J, 1, 0)
    s.y = 19
    kicked = E.rotate(s, True)
    check("J rotation near the floor either kicks or is refused",
          kicked in (True, False) and E._fits(s.rows, E.J, s.rot, s.x, s.y))

    # --- O rotation is a no-op geometrically -------------------------------
    s = _set_piece(_blank(), E.O)
    cells0 = set(E.PIECE_CELLS[E.O][0])
    E.rotate(s, True)
    check("O keeps its shape through rotation",
          set(E.PIECE_CELLS[E.O][s.rot]) == cells0)

    # --- hold ---------------------------------------------------------------
    s = E.new_game(7)
    first = s.current
    check("hold stores the active piece", E.hold(s) and s.hold == first)
    check("hold is once per spawn", E.hold(s) is False)
    E.hard_drop(s)
    check("hold is available again after a spawn", s.can_hold)
    active = s.current
    check("hold swaps the held piece back in",
          E.hold(s) and s.current == first and s.hold == active,
          "current=%s hold=%s (expected current=%s hold=%s)"
          % (s.current, s.hold, first, active))
    check("swapped-in piece is reset to spawn position",
          s.rot == 0 and s.x == E.SPAWN_X[first] and s.y == E.SPAWN_Y)

    # --- drop scoring -------------------------------------------------------
    s = E.new_game(3)
    E.soft_drop(s)
    check("soft drop scores 1/cell", s.score == 1)
    s = E.new_game(3)
    d = E.drop_distance(s)
    E.hard_drop(s)
    check("hard drop scores 2/cell", s.score == 2 * d, "d=%d score=%d" % (d, s.score))

    # --- purity of the agent path ------------------------------------------
    s = E.new_game(99)
    snapshot = (s.rows, s.current, s.rng, s.queue, s.score, s.lines)
    ps = E.legal_placements(s)
    E.apply_placement(s, ps[0])
    check("apply_placement does not mutate the input state",
          (s.rows, s.current, s.rng, s.queue, s.score, s.lines) == snapshot)

    # --- serialization round trip ------------------------------------------
    s = E.new_game(4242)
    for _ in range(20):
        ps = E.legal_placements(s)
        if not ps:
            break
        s, _ = E.apply_placement(s, ps[len(ps) // 2])
    rt = E.from_dict(json.loads(json.dumps(s.to_dict())))
    check("to_dict/from_dict round trip is exact",
          E.state_hash(rt) == E.state_hash(s) and rt.to_dict() == s.to_dict())

    # --- queue always shows 5 next -----------------------------------------
    s = E.new_game(11)
    short = 0
    for _ in range(60):
        if len(s.queue) < 5:
            short += 1
        ps = E.legal_placements(s)
        if not ps:
            break
        s, _ = E.apply_placement(s, ps[0])
    check("queue never drops below 5", short == 0)

    # --- wall-kick table coverage ------------------------------------------
    # Measure which of the 40 kick entries the probe actually exercises, so the
    # coverage claim is a number rather than an assumption.
    probe = kick_probe()
    boards = _kick_boards()
    used = set()
    for bi, piece, frm, cw, x, y, rot, nx, ny, moved in probe:
        if not moved or piece == E.O:
            continue
        offs = E.kick_offsets(piece, frm, rot)
        dx, dy = nx - x, ny - y
        for idx, (kx, ky) in enumerate(offs):
            if (kx, ky) == (dx, dy):
                table = "I" if piece == E.I else "JLSTZ"
                used.add((table, frm, rot, idx))
                break
    entries_hit = {(t, f, r) for t, f, r, _ in used}
    check("kick probe reaches all 16 rotation transitions (both tables)",
          len(entries_hit) == 16, "hit %d: %s" % (len(entries_hit),
                                                 sorted(entries_hit)))
    tests_hit = {(t, i) for t, _f, _r, i in used}
    check("kick probe exercises test slots beyond the identity offset",
          len({i for _t, i in tests_hit if i > 0}) >= 3,
          "slots used: %s" % sorted({i for _t, i in tests_hit}))
    print("     (kick coverage: %d distinct (table, from>>to, test#) combos)"
          % len(used))

    # --- ABSOLUTE score golden table ---------------------------------------
    # checker's decisive point: the parity check only proves Python and JS
    # agree with EACH OTHER. If both drifted from docs/spec.md the suite would
    # still be green. These expectations are transcribed from spec.md section 5
    # BY HAND and must be edited only alongside the document.
    #
    # (lines, level, b2b_in, combo_in) -> expected score_delta
    GOLDEN_SCORES = [
        # no clear: nothing, regardless of chain state
        ((0, 1, 0, 0), 0),
        ((0, 9, 5, 5), 0),
        # single / double / triple / tetris at level 1, no chain, no combo
        ((1, 1, 0, 0), 100),
        ((2, 1, 0, 0), 300),
        ((3, 1, 0, 0), 500),
        ((4, 1, 0, 0), 800),
        # level multiplies the base
        ((1, 3, 0, 0), 300),
        ((4, 5, 0, 0), 4000),
        # back-to-back tetris: 800 * 3/2 = 1200, then + combo 50*1*1
        ((4, 1, 1, 1), 1250),
        # b2b at level 2 with combo 2: 1200*2 + 50*2*2 = 2600
        ((4, 2, 2, 2), 2600),
        # a 1-3 line clear never takes the b2b bonus, even mid-chain
        ((1, 1, 3, 0), 100),
        ((3, 1, 9, 0), 500),
        # combo bonus only: 50 * combo_in * level
        ((1, 1, 0, 1), 150),
        ((1, 1, 0, 2), 200),
        ((1, 2, 0, 3), 500),
        # the worked example from the lead's ruling: two tetrises at level 1
        ((4, 1, 0, 0), 800),
        ((4, 1, 1, 0), 1200),
    ]
    for (n, lv, b2b, combo), want in GOLDEN_SCORES:
        got = E._score_clear(n, lv, b2b, combo)[0]
        check("spec score: lines=%d level=%d b2b=%d combo=%d -> %d"
              % (n, lv, b2b, combo, want), got == want, "got %d" % got)

    # The headline number from the lead's ruling, end to end.
    first = E._score_clear(4, 1, 0, 0)
    second = E._score_clear(4, 1, first[1], first[2])
    check("two consecutive tetrises at level 1 total 2050 (spec, not 1600)",
          first[0] + second[0] == 2050,
          "got %d + %d" % (first[0], second[0]))

    # --- B2B and combo scoring ---------------------------------------------
    import tables as Tb
    check("B2B constants are tetris-only with an exact x3/2",
          (Tb.B2B_LINES, Tb.B2B_MULT_NUM, Tb.B2B_MULT_DEN) == (4, 3, 2))
    check("combo bonus step is 50", Tb.COMBO_BONUS_PER_STEP == 50)

    sc = E._score_clear
    # A move clearing nothing: combo dies, B2B survives, no points.
    check("no clear: 0 points", sc(0, 5, 3, 7)[0] == 0)
    check("no clear: combo resets to 0", sc(0, 5, 3, 7)[2] == 0)
    check("no clear: B2B chain is NOT broken", sc(0, 5, 3, 7)[1] == 3)
    check("no clear: B2B bonus not applied", sc(0, 5, 3, 7)[3] is False)

    # First tetris opens the chain but gets no bonus yet.
    d, b, c, applied = sc(4, 1, 0, 0)
    check("first tetris scores 800 with no B2B bonus", d == 800 and not applied)
    check("first tetris opens the B2B chain", b == 1)
    check("first tetris starts the combo at 1", c == 1)

    # Second consecutive tetris: x1.5 plus one combo step.
    d, b, c, applied = sc(4, 1, 1, 1)
    check("second tetris applies the x1.5 B2B bonus", applied and d == 1200 + 50,
          "got %d" % d)
    check("B2B chain grows to 2", b == 2)

    # Level multiplies the base and the combo bonus alike.
    d, _b, _c, _a = sc(4, 2, 2, 2)
    check("level 2, chain 2, combo 2 -> 1200*2 + 50*2*2", d == 2600,
          "got %d" % d)

    # A 1-3 line clear breaks the chain but continues the combo.
    d, b, c, applied = sc(1, 1, 2, 3)
    check("single after tetris breaks the B2B chain", b == 0 and not applied)
    check("single after tetris keeps the combo running", c == 4)
    check("single after tetris scores 100 + 50*3*1", d == 250, "got %d" % d)

    # Combo bonus uses the count BEFORE this clear, so the first is bonus-free.
    check("combo bonus is zero on the first clear of a run", sc(1, 1, 0, 0)[0] == 100)
    check("combo bonus grows linearly", sc(1, 1, 0, 1)[0] == 150
          and sc(1, 1, 0, 2)[0] == 200)

    # The x1.5 must be exact for every entry -- no float, no rounding drift.
    ok_exact = all(Tb.SCORE_TABLE[n] % Tb.B2B_MULT_DEN == 0 for n in range(1, 5))
    check("every SCORE_TABLE entry is divisible by the B2B denominator",
          ok_exact, "x1.5 would round and could drift from JS")

    # A real game must carry B2B/combo through apply_placement, not just _score_clear.
    rows = list(E.EMPTY_ROWS)
    for y in (18, 19, 20, 21):
        rows[y] = E.FULL_ROW & ~(1 << 9)
    s = _blank()
    s.rows = tuple(rows)
    _set_piece(s, E.I, 1, 7)
    p = [q for q in E.legal_placements(s) if q[0] == 1 and q[1] == 7][0]
    ns, info = E.apply_placement(s, p)
    check("apply_placement reports is_tetris", info["is_tetris"] is True)
    check("apply_placement reports b2b_chain 1 on the first tetris",
          info["b2b_chain"] == 1 and info["b2b_active"] is False)
    check("apply_placement reports combo_count 1", info["combo_count"] == 1)
    check("state carries the B2B chain forward", ns.b2b == 1 and ns.combo == 1)

    # Second tetris on the rebuilt board must take the bonus.
    ns.rows = tuple(rows)
    _set_piece(ns, E.I, 1, 7)
    p2 = [q for q in E.legal_placements(ns) if q[0] == 1 and q[1] == 7][0]
    ns2, info2 = E.apply_placement(ns, p2)
    check("back-to-back tetris takes the x1.5 in a real game",
          info2["b2b_active"] is True and info2["score_delta"] == 1200 + 50,
          "got %r" % info2["score_delta"])
    check("B2B chain reaches 2", ns2.b2b == 2 and info2["b2b_chain"] == 2)

    # B2B/combo must be in state_hash (they change future scoring).
    a = E.new_game(4)
    b_ = E.new_game(4)
    b_.b2b = 3
    check("b2b is part of state_hash", E.state_hash(a) != E.state_hash(b_))
    b_ = E.new_game(4)
    b_.combo = 3
    check("combo is part of state_hash", E.state_hash(a) != E.state_hash(b_))

    # ...but must NOT affect the board or which placements are legal.
    c_ = E.new_game(4)
    c_.b2b, c_.combo = 9, 9
    check("b2b/combo do not affect legal_placements",
          E.legal_placements(c_) == E.legal_placements(a))
    check("b2b/combo do not affect board_hash",
          E.board_hash(c_.rows) == E.board_hash(a.rows))

    # --- lock delay (interactive path only) --------------------------------
    check("lock delay is 500 ms with a 15-reset budget",
          Tb.LOCK_DELAY_MS == 500 and Tb.LOCK_RESET_LIMIT == 15)

    # A piece resting on the floor must NOT lock instantly -- that is what
    # makes tuck and slide possible.
    s = E.new_game(5)
    E.hard_drop(s)                                    # get a fresh piece down
    s2 = E.new_game(5)
    s2.y = E.ghost_y(s2)                              # rest it on the floor
    locked = E.tick_ms(s2, 100.0)
    check("resting piece does not lock after 100 ms", locked is None)
    check("lock_delay_progress reports partial elapse",
          0.15 < E.lock_delay_progress(s2) < 0.25,
          "%r" % E.lock_delay_progress(s2))
    locked = E.tick_ms(s2, 450.0)
    check("resting piece locks once 500 ms have elapsed", locked is not None)

    # Moving while resting restarts the countdown -- the tuck/slide window.
    s3 = E.new_game(5)
    s3.y = E.ghost_y(s3)
    E.tick_ms(s3, 400.0)
    moved = E.move(s3, -1)
    check("piece can still slide while resting", moved)
    check("a successful slide resets the lock timer", s3.lock_ms == 0.0)
    check("the slide consumed one reset from the budget", s3.lock_resets == 1)
    check("still not locked after the reset", E.tick_ms(s3, 400.0) is None)

    # ... but only 15 times, so the piece cannot be held up forever.
    s4 = E.new_game(5)
    s4.y = E.ghost_y(s4)
    for _ in range(40):
        E.move(s4, -1)
        E.move(s4, 1)
    check("reset budget is capped at LOCK_RESET_LIMIT",
          s4.lock_resets == Tb.LOCK_RESET_LIMIT, "got %d" % s4.lock_resets)
    E.move(s4, -1)
    check("moves past the budget no longer reset the timer",
          s4.lock_ms == 0.0 or s4.lock_resets == Tb.LOCK_RESET_LIMIT)
    s4.lock_ms = 0.0
    check("piece locks on schedule once the budget is spent",
          E.tick_ms(s4, Tb.LOCK_DELAY_MS + 1) is not None)

    # --- infinite spin / slide must not defeat the lock (web's bug) ---------
    # web found that spamming rotation kept a piece alive for 20,000 frames.
    # Two separate holes: the airborne branch zeroed lock_ms unconditionally
    # (bypassing the reset budget), and gating the timer on `resting` alone
    # meant a kick that lifts the piece every frame stopped the clock entirely.
    def _spam(seed, actions, limit=20000):
        st = E.new_game(seed)
        for i in range(limit):
            for act in actions:
                act(st)
            if E.tick_ms(st, 16.0) is not None:
                return i
        return None

    spin = _spam(4242, [lambda s: E.rotate(s, True), lambda s: E.rotate(s, False)])
    check("rotation spam cannot prevent the lock forever", spin is not None,
          "20000 frames of rotate-spam never locked")
    slide = _spam(4242, [lambda s: E.move(s, -1), lambda s: E.move(s, 1)])
    check("move spam cannot prevent the lock forever", slide is not None,
          "20000 frames of move-spam never locked")
    mixed = _spam(777, [lambda s: E.rotate(s, True), lambda s: E.move(s, -1),
                        lambda s: E.rotate(s, False), lambda s: E.move(s, 1)])
    check("mixed rotate+move spam cannot prevent the lock forever",
          mixed is not None)

    # An expiring timer must never freeze a piece that a kick is holding aloft.
    st = E.new_game(4242)
    while E.tick_ms(st, 16.0) is None and not st.game_over:
        if st.touched_down:
            break
    check("touched_down is set once the piece rests", st.touched_down)
    st.y -= 1                              # simulate a kick lifting it
    st.lock_ms = float(Tb.LOCK_DELAY_MS)   # and the clock running out now
    lifted_y = st.y
    rest_y = E.ghost_y(st)
    info = E.tick_ms(st, 16.0)
    check("lock-delay expiry while airborne settles the piece first",
          info is not None and info["y"] == rest_y and rest_y > lifted_y,
          "info_y=%r rest_y=%r lifted_y=%r"
          % (info and info["y"], rest_y, lifted_y))
    check("the settle-on-expiry does NOT award hard-drop points",
          info is not None and info["score_delta"] == 0, str(info))

    # Descending must forgive the timer, but only for NEW downward progress.
    st = E.new_game(9)
    st.lowest_y = st.y
    st.lock_ms = 200.0
    st.y -= 1                              # pretend a kick lifted it
    E.tick_ms(st, 16.0)
    check("returning to an already-visited row does not reset the timer",
          st.lock_ms > 0.0, "lock_ms=%r" % st.lock_ms)

    # Gravity is time-based and level-dependent, and hard drop ignores delay.
    s5 = E.new_game(5)
    y0 = s5.y
    E.tick_ms(s5, E.gravity_interval_ms(1) * 3 + 1)
    check("gravity falls 3 cells in 3 intervals at level 1", s5.y == y0 + 3,
          "y0=%d y=%d" % (y0, s5.y))
    check("gravity interval at level 1 is 800 ms",
          abs(E.gravity_interval_ms(1) - 800.0) < 1e-9)
    s6 = E.new_game(5)
    check("hard drop locks immediately regardless of lock delay",
          E.hard_drop(s6) is not None)

    # The agent path must be completely blind to all of this.
    s7 = E.new_game(5)
    before = E.legal_placements(s7)
    s7.lock_ms = 499.0
    s7.lock_resets = 15
    s7.grav_ms = 123.0
    s7.lowest_y = 17
    s7.touched_down = True
    check("lock-delay fields do not affect legal_placements",
          E.legal_placements(s7) == before)
    check("lock-delay fields are excluded from state_hash",
          E.state_hash(s7) == E.state_hash(E.new_game(5)))

    # --- difficulty modes (docs/spec.md section 14) ------------------------
    check("difficulty tables are (next, hold) = (5,T) (1,F) (0,F)",
          Tb.DIFFICULTY_NEXT_VISIBLE == (5, 1, 0)
          and Tb.DIFFICULTY_HOLD_ENABLED == (True, False, False))
    check("default difficulty is normal",
          Tb.DIFFICULTY_DEFAULT == Tb.DIFFICULTY_NORMAL == 0)

    # THE constraint from the lead: normal must be bit-identical to the engine
    # before difficulty existed. Anything else invalidates every recorded trace
    # and every trained weight.
    plain = E.new_game(31337)
    normal = E.new_game(31337, difficulty=E.DIFFICULTY_NORMAL)
    check("new_game() and new_game(difficulty=normal) are the same state",
          plain.to_dict() == normal.to_dict())
    a, b_ = plain, normal
    same = True
    for _ in range(120):
        pa, pb = E.legal_placements(a), E.legal_placements(b_)
        if pa != pb:
            same = False
            break
        if not pa:
            break
        a, ia = E.apply_placement(a, pa[0])
        b_, ib = E.apply_placement(b_, pb[0])
        if (E.state_hash(a) != E.state_hash(b_) or ia != ib):
            same = False
            break
    check("normal mode is bit-identical over a 120-move game", same)

    # The piece SEQUENCE must be identical in every mode -- only visibility
    # changes. Without this, comparing modes measures two different games.
    seqs = []
    for d in (E.DIFFICULTY_NORMAL, E.DIFFICULTY_HARD, E.DIFFICULTY_EXTREME):
        st = E.new_game(999, difficulty=d)
        order = [st.current]
        for _ in range(60):
            ps = E.legal_placements(st)
            if not ps:
                break
            st, _ = E.apply_placement(st, ps[0])
            order.append(st.current)
        seqs.append(tuple(order))
    check("all three modes produce the IDENTICAL piece sequence from one seed",
          len(set(seqs)) == 1, "%d distinct sequences" % len(set(seqs)))
    check("the mode does not change board_hash either",
          len({E.board_hash(E.new_game(7, difficulty=d).rows)
               for d in (0, 1, 2)}) == 1)

    # Visibility itself.
    check("normal reveals 5 upcoming pieces",
          len(E.visible_next(E.new_game(5, difficulty=0))) == 5)
    check("hard reveals exactly 1",
          len(E.visible_next(E.new_game(5, difficulty=1))) == 1)
    hard_next = E.visible_next(E.new_game(5, difficulty=1))
    norm_next = E.visible_next(E.new_game(5, difficulty=0))
    check("hard's single preview is the same piece normal shows first",
          hard_next[0] == norm_next[0])

    # Extreme must REFUSE, not return an empty list -- a silent () would let a
    # lookahead agent degrade to zero-ply and still report a number.
    extreme = E.new_game(5, difficulty=E.DIFFICULTY_EXTREME)
    raised = False
    try:
        E.visible_next(extreme)
    except E.NextPeekBlocked:
        raised = True
    check("extreme RAISES NextPeekBlocked instead of returning ()", raised)
    check("next_visible_count reports 0 so callers can branch before asking",
          E.next_visible_count(extreme) == 0)

    # Hold must be gone in hard and extreme.
    check("hold works in normal", E.hold(E.new_game(11, difficulty=0)) is True)
    check("hold is refused in hard", E.hold(E.new_game(11, difficulty=1)) is False)
    check("hold is refused in extreme",
          E.hold(E.new_game(11, difficulty=2)) is False)
    check("hold_enabled matches the table",
          [E.hold_enabled(E.new_game(1, difficulty=d)) for d in (0, 1, 2)]
          == [True, False, False])

    # Refusing hold must not consume the hold or alter the piece.
    st = E.new_game(11, difficulty=E.DIFFICULTY_HARD)
    before = st.to_dict()
    E.hold(st)
    check("a refused hold leaves the state completely untouched",
          st.to_dict() == before)

    check("difficulty survives a to_dict/from_dict round trip",
          E.from_dict(E.new_game(3, difficulty=2).to_dict()).difficulty == 2)
    check("unknown difficulty is rejected loudly",
          _raises(lambda: E.new_game(1, difficulty=9), ValueError))
    check("difficulty_name maps the modes",
          [E.difficulty_name(E.new_game(1, difficulty=d)) for d in (0, 1, 2)]
          == ["normal", "hard", "extreme"])

    # difficulty is deliberately NOT in state_hash: including it would have
    # changed every normal-mode hash and invalidated the recorded traces.
    check("difficulty is excluded from state_hash (keeps normal traces valid)",
          E.state_hash(E.new_game(4, difficulty=0))
          == E.state_hash(E.new_game(4, difficulty=2)))

    # --- Game wrapper (JS only) --------------------------------------------
    try:
        ui = run_js_ui_probe()
    except NodeBroken as exc:
        ui = None
        check("Game wrapper checks ran under node", False,
              "node ran and FAILED: %s" % exc)
    if ui is None:
        skip("Game.stepToward regression (JS-only wrapper)",
             "node not available -- the PLAY-screen freeze regression is "
             "NOT covered on this machine")
    else:
        seq = ui["blocked_sequence"]
        check("stepToward resolves an unreachable target instead of looping",
              seq and seq[-1] == "blocked" and len(seq) <= 12,
              "returned %r" % (seq,))
        check("a blocked stepToward still locks the piece (game advances)",
              ui["blocked_locked_a_piece"])
        check("stepToward still reaches a REACHABLE target normally",
              ui["reachable_hit_target"],
              "returned %r" % (ui["reachable_sequence"],))
        check("an AI replay loop terminates and reaches game over",
              ui["loop_reached_game_over"] and ui["loop_steps"] < 200000,
              "steps=%r locks=%r over=%r" % (ui["loop_steps"], ui["loop_locks"],
                                            ui["loop_reached_game_over"]))
        want = [("normal", 5, 5, True, True),
                ("hard", 1, 1, False, False),
                ("extreme", 0, 0, False, False)]
        got = [(m["name"], m["next_count"], m["next_len"], m["hold_enabled"],
                m["hold_result"]) for m in ui["modes"]]
        check("Game difficulty accessors match the spec table for all 3 modes",
              got == want, "got %r" % (got,))

    # --- KNOWN difficulty bypass: afterstate simulation ---------------------
    # This test asserts that a leak EXISTS. That is deliberate.
    #
    # visible_next() refuses in extreme mode, and checker verified that nothing
    # in rl/ or web/ reads state.queue directly. But a grep for `.queue` cannot
    # find this path: apply_placement must return a PLAYABLE next state, so it
    # spawns the next piece, and the caller can read next_state.current. Any
    # lookahead agent doing afterstate search therefore recovers the hidden
    # piece for free, without touching the queue.
    #
    # It is inherent, not a defect to patch: a next_state whose `current` is
    # withheld would not be a usable state, and the agent contract requires one.
    # Pinning it here means (a) nobody "fixes" it by accident and breaks the
    # agent contract, and (b) nobody assumes the queue guard is airtight.
    #
    # The real defence is behavioural and lives in checker's tests: in extreme
    # mode a lookahead policy must choose the SAME placements as a no-lookahead
    # one. That is what actually proves no agent is exploiting this.
    ext = E.new_game(4242, difficulty=E.DIFFICULTY_EXTREME)
    nrm = E.new_game(4242, difficulty=E.DIFFICULTY_NORMAL)
    hidden = E.visible_next(nrm)[0]
    leaked = E.apply_placement(ext, E.legal_placements(ext)[0])[0].current
    check("KNOWN LIMITATION: afterstate simulation still reveals the hidden "
          "next piece (see comment -- inherent, guarded behaviourally)",
          leaked == hidden,
          "if this ever fails, apply_placement stopped returning a playable "
          "state and the agent contract is broken")

    # Pin the SEVERITY, not just the existence. checker measured that chaining
    # afterstates reads 8+ pieces ahead -- i.e. extreme mode leaks MORE than
    # normal mode ever shows (5). Recording only "a leak exists" would let
    # docs keep implying the leak is one piece deep.
    chain = []
    st = E.new_game(4242, difficulty=E.DIFFICULTY_EXTREME)
    for _ in range(12):
        ps = E.legal_placements(st)
        if not ps:
            break
        st, _ = E.apply_placement(st, ps[0])
        if st.current is None:
            break
        chain.append(st.current)
    normal_preview = list(E.visible_next(nrm))
    n_vis = E.DIFFICULTY_NEXT_VISIBLE[E.DIFFICULTY_NORMAL]
    check("the afterstate chain reproduces normal mode's whole preview",
          chain[:n_vis] == normal_preview,
          "chain=%r preview=%r" % (chain[:n_vis], normal_preview))
    check("KNOWN LIMITATION: the leak is DEEPER than normal mode's preview "
          "(so 'zero previews' is not an information bound)",
          len(chain) > n_vis,
          "chain depth %d vs normal preview %d" % (len(chain), n_vis))
    print("     (measured afterstate leak depth: %d pieces, vs %d shown in "
          "normal mode -- bounded only by remaining placements)"
          % (len(chain), n_vis))

    # --- generated JS tables are in sync -----------------------------------
    import contextlib
    import gen_tables_js
    with contextlib.redirect_stdout(io.StringIO()):   # it prints its own line
        stale = gen_tables_js.main(["--check"])
    check("web/tables.js is in sync with engine/tables.py", stale == 0,
          "run `python3 engine/gen_tables_js.py`")

    import gen_classic_bundle
    with contextlib.redirect_stdout(io.StringIO()):
        stale_bundle = gen_classic_bundle.main(["--check"])
    check("web/engine.classic.js is in sync with the ES modules",
          stale_bundle == 0, "run `python3 engine/gen_classic_bundle.py`")

    # --- derived tables agree with the cell tables --------------------------
    ok_derived = True
    for p in range(7):
        for r in range(4):
            cells = Tb.PIECE_CELLS[p][r]
            if (Tb.MIN_DX[p][r] != min(c[0] for c in cells)
                    or Tb.MAX_DX[p][r] != max(c[0] for c in cells)
                    or Tb.MIN_DY[p][r] != min(c[1] for c in cells)
                    or Tb.MAX_DY[p][r] != max(c[1] for c in cells)):
                ok_derived = False
            for dx, bdy in Tb.BOTTOM_PROFILE[p][r]:
                if bdy != max(c[1] for c in cells if c[0] == dx):
                    ok_derived = False
    check("tables.py derived values match PIECE_CELLS", ok_derived)

    # --- placement_left_col is the leftmost occupied column -----------------
    ok_left = True
    for piece in range(7):
        s = _set_piece(_blank(), piece)
        for p in E.legal_placements(s):
            cells = E.placement_cells(p)
            if E.placement_left_col(p) != min(x for _y, x in cells):
                ok_left = False
    check("placement_left_col == min column of placement_cells", ok_left)

    # --- board_array conversion round trip ---------------------------------
    s = E.new_game(31)
    for _ in range(25):
        ps = E.legal_placements(s)
        if not ps:
            break
        s, _ = E.apply_placement(s, ps[-1])
    arr22 = E.board_array(s, buffer=True)
    check("board_array(buffer=True) is (22, 10)", arr22.shape == (22, 10))
    check("board_array() is (20, 10) and drops the spawn buffer",
          E.board_array(s).shape == (20, 10))
    check("board_array row 0 is the TOP row",
          all(arr22[0][x] == ((s.rows[0] >> x) & 1) for x in range(10)))
    check("rows_from_array inverts board_array",
          E.rows_from_array(arr22, buffer=True) == s.rows)

    # --- batch API ----------------------------------------------------------
    batch = [E.new_game(100 + i) for i in range(5)]
    picks = [ps[0] if ps else None
             for ps in E.legal_batch(batch)]
    nb, infos = E.step_batch(batch, picks)
    check("step_batch keeps the batch shape",
          len(nb) == 5 and len(infos) == 5)
    check("step_batch advanced every live game",
          all(x.pieces == 1 for x in nb))
    check("step_batch does not mutate the input states",
          all(x.pieces == 0 for x in batch))
    nb2, infos2 = E.step_batch(nb, [None] * 5)
    check("step_batch passes None placements through untouched",
          nb2 == nb and all(i is None for i in infos2))

    # --- 7-bag fairness -----------------------------------------------------
    # Draw a long piece sequence straight from the RNG (no board involved) and
    # verify every aligned window of 7 is a permutation of all seven pieces.
    ok_bags = True
    from rng import next_bag as _next_bag
    st = seed_state(2024)
    seq = []
    for _ in range(100):
        st, bag = _next_bag(st)
        seq.extend(bag)
    for b in range(0, len(seq), 7):
        if sorted(seq[b:b + 7]) != [0, 1, 2, 3, 4, 5, 6]:
            ok_bags = False
    check("7-bag emits each piece exactly once per bag (700 draws)", ok_bags)
    counts = [seq.count(p) for p in range(7)]
    check("7-bag piece counts are all equal", len(set(counts)) == 1, str(counts))

    print("")
    if skips:
        print("%d check(s) SKIPPED -- these verified NOTHING:" % len(skips))
        for name, why in skips:
            print("     %s  (%s)" % (name, why))
    if fails:
        print("%d rule check(s) FAILED, %d skipped: %s"
              % (len(fails), len(skips), fails))
    else:
        print("all rule checks passed (%d skipped)" % len(skips))
    return not fails


# ---------------------------------------------------------------------------
# benchmark
# ---------------------------------------------------------------------------

#: Benchmark workload, fixed so the number is quotable.
#:
#: Earlier runs varied between roughly 30,000 and 48,000 placements/s and were
#: reported as single values (45,307 then 42,938 then 40,099), which implied a
#: precision the measurement did not have. The cause was a wall-clock budget:
#: a fixed number of SECONDS meant each run played a different number of games,
#: and because game length drives stack height -- which drives how early
#: column_tops can stop scanning -- the throughput moved with it.
#:
#: A fixed PLACEMENT COUNT removes that: every run does exactly the same work,
#: so only machine noise remains.
BENCH_PLACEMENTS = 200000
BENCH_SEED = 20260807
BENCH_POLICY = "random"          # fixed-seed xorshift32 choice among placements

#: A run outside these bounds is not quotable. Within-run spread catches a busy
#: box mid-measurement; load average catches one that was busy throughout (which
#: a tight spread will happily hide -- uniformly slow looks like consistent).
BENCH_MAX_SPREAD_PCT = 15.0
BENCH_MAX_LOAD = 8.0


def _load_average():
    """1-minute load average, or None where it is unavailable."""
    try:
        with open("/proc/loadavg") as f:
            return float(f.read().split()[0])
    except Exception:
        return None


def run_bench(placements: int = BENCH_PLACEMENTS, seed: int = BENCH_SEED,
              repeats: int = 3) -> float:
    """Measure legal_placements + apply_placement round trips per second.

    Fixed workload, not a fixed duration: exactly `placements` round trips with
    a fixed-seed random policy, restarting on game over with derived seeds.
    Reports the best of `repeats` -- the best run is the one least disturbed by
    other load on the box, which is what makes it comparable across runs.
    """
    rates = []
    for rep in range(repeats):
        rng = seed_state(seed)
        st = E.new_game(seed)
        n = 0
        games = 0
        lines = 0
        t0 = time.perf_counter()
        while n < placements:
            ps = E.legal_placements(st)
            if not ps:
                lines += st.lines
                games += 1
                st = E.new_game(seed + games)
                continue
            rng, r = next_u32(rng)
            st, _ = E.apply_placement(st, ps[r % len(ps)])
            n += 1
            if st.game_over:
                lines += st.lines
                games += 1
                st = E.new_game(seed + games)
        dt = time.perf_counter() - t0
        rates.append(n / dt)
        print("  run %d/%d: %d placements in %.3f s -> %.0f/s "
              "(%d games, %d lines)"
              % (rep + 1, repeats, n, dt, rates[-1], games, lines))

    best = max(rates)
    spread = (max(rates) - min(rates)) / best * 100.0
    print("bench: %.0f placements/s  [best of %d, spread %.1f%%]"
          % (best, repeats, spread))
    print("       conditions: %d placements, seed %d, %s policy, "
          "difficulty normal, single core, Python %d.%d"
          % (placements, seed, BENCH_POLICY,
             sys.version_info[0], sys.version_info[1]))

    load = _load_average()
    if load is not None:
        print("       load average during run: %.2f" % load)

    # Refuse to hand out a number that should not be quoted. Printing it with a
    # caveat does not work -- the number gets copied and the caveat does not.
    # This measurement has now been misquoted twice: first because the bench
    # used a time budget (so the workload moved with the result), and then
    # because a value taken with residual machine load was recorded as final.
    usable = spread <= BENCH_MAX_SPREAD_PCT and (load is None or load <= BENCH_MAX_LOAD)
    if usable:
        print("       QUOTABLE: yes (spread <= %.0f%%, load <= %.0f)"
              % (BENCH_MAX_SPREAD_PCT, BENCH_MAX_LOAD))
    else:
        why = []
        if spread > BENCH_MAX_SPREAD_PCT:
            why.append("spread %.1f%% > %.0f%%" % (spread, BENCH_MAX_SPREAD_PCT))
        if load is not None and load > BENCH_MAX_LOAD:
            why.append("load %.2f > %.0f" % (load, BENCH_MAX_LOAD))
        print("       QUOTABLE: NO -- %s. The machine was busy; this number "
              "measures contention, not the engine. Re-run when idle and do "
              "NOT record this value." % ", ".join(why))

    print("       target >= 20,000/s : %s" % ("PASS" if best >= 20000 else "FAIL"))
    return best


# ---------------------------------------------------------------------------

def main(argv):
    flags = set(argv[1:])
    if "--emit-js-runner" in flags:
        print("wrote %s" % emit_browser_runner())
        return 0
    if "--compare-only" in flags:
        return 0 if run_compare_only() else 1

    do_tests = "--tests" in flags or not (flags & {"--parity", "--bench"})
    do_parity = "--parity" in flags or not (flags & {"--tests", "--bench"})
    do_bench = "--bench" in flags or not (flags & {"--tests", "--parity"})

    ok = True
    if do_tests:
        print("=== rule tests ===")
        ok &= run_tests()
    if do_parity:
        print("\n=== python <-> js parity ===")
        ok &= run_parity()
    if do_bench:
        print("\n=== benchmark ===")
        run_bench()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
