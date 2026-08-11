"""Bitboard Tetris simulator built for training throughput.

WHY THIS EXISTS
---------------
`engine/` owns the authoritative game: SRS kicks, hold, gravity, levels, scoring
and the human play path. It is the reference implementation, and the web mirror
is checked against it.

Training cannot run on it. One CEM generation costs roughly

    64 candidates x 8 seeds x 5000 pieces x ~25 candidate placements
    ~= 60 million placement evaluations

and every one needs an 8-feature vector. This file represents the board as ten
Python ints and computes all eight features with a handful of `int.bit_count()`
calls, which is about two orders of magnitude faster than going through State
objects and numpy arrays.

THIS FILE IS A MIRROR, NOT A SECOND SOURCE OF TRUTH
----------------------------------------------------
Two defences against drifting away from the engine:

1. ALL PIECE GEOMETRY IS IMPORTED FROM `engine.tables`. Rotation cells, unique
   rotation lists, legal x ranges and bottom profiles are not restated here.
   An earlier version of this file duplicated them and silently disagreed with
   the engine; deriving them removes that entire class of bug.
2. `parity_fastsim.py` replays identical seeds and identical placement choices
   through both implementations and compares the board after every placement.

If the two ever disagree, THE ENGINE IS RIGHT and this file gets fixed.

BOARD REPRESENTATION
--------------------
    cols[c] : int, 22 significant bits, for c in 0..9
    bit y of cols[c] is set  <=>  engine cell (row=y, col=c) is occupied

Rows follow the ENGINE's coordinates, including the 2-row spawn buffer:

    y = 0, 1        spawn buffer, above the visible field
    y = 2 .. 21     the visible 20 rows
    y = 21          the bottom row (floor sits just below)

The buffer is not decoration. A piece may come to rest partly inside it and
that placement is legal, so a simulator that stops at the visible field
enumerates fewer moves than the engine and declares game over too early. That
was a real divergence found by the parity test.

Features, however, are defined on the VISIBLE FIELD ONLY, matching
`features.py` and `engine.board_array()`. The visible view of a column is

    vis = cols[c] >> 2          bit r of vis  <=>  visible row r

`top[c]` is the engine's own convention: the index of the topmost filled row,
or 22 for an empty column. It drives the drop; it is NOT the feature height.

HOW THE FEATURES BECOME BIT TRICKS
----------------------------------
All identities below operate on `vis` and are checked against `features.py`.

  holes            = height - popcount(vis)
        Every cell from the topmost filled visible row down to the floor is in
        the column's span (that is `height` cells). The filled ones are
        popcount. What remains is empty with something above it -- a hole.

  row_transitions  = (20 - popcount(vis[0]))       # left wall vs column 0
                   + (20 - popcount(vis[9]))       # column 9 vs right wall
                   + sum_c popcount(vis[c] ^ vis[c+1])
        Walls count as filled, so a wall boundary is a transition exactly where
        the edge column is EMPTY -- hence `20 - popcount`. Between two real
        columns a row differs iff the bit differs, which XOR counts for all 20
        rows at once.

  column_transitions:  v = vis | (1 << 20) appends a filled floor, and
        (v << 1) shifts in an empty ceiling at bit 0. XOR lines up every
        adjacent pair (-1,0) ... (19,20), so popcount counts all 21 at once.

  cumulative_wells:  W = ~vis & left & right (walls all-ones) marks well cells.
        A run of depth d owes 1+2+...+d. Peel it in layers:
            X = W                 -> every well cell           (d terms)
            X = (X << 1) & W      -> cells with a well above    (d-1 terms)
        Summing popcount(X) per layer yields the triangular number for every
        run at once, in at-most-max-depth iterations.
"""

from __future__ import annotations

import os
import sys

import numpy as np

_ENGINE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "engine")
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

from tables import (  # noqa: E402
    BUFFER_ROWS, FULL_ROW, LINES_PER_LEVEL, MIN_DX, MIN_DY, PIECE_CELLS,
    PIECE_NAMES, ROWS, SPAWN_X, SPAWN_Y, UNIQUE_ROTS, VISIBLE_ROWS,
    W as BOARD_W, X_RANGE,
)
from engine import _score_clear as _engine_score_clear   # noqa: E402

BOARD_H = VISIBLE_ROWS                  # 20 -- the feature field
TOTAL_ROWS = ROWS                       # 22 -- physics field, engine coords
N_PIECES = len(PIECE_NAMES)
FULL_VIS = (1 << BOARD_H) - 1
COL_TRANS_MASK = (1 << (BOARD_H + 1)) - 1     # 21 boundaries


def _build_placement_table():
    """Precompute every placement, derived from the engine's cell tables.

    PLACEMENTS[piece] is a list of records ordered by (rotation, x) -- the same
    order `engine.legal_placements` emits, which is what lets the parity test
    pair them up index by index and what makes argmax tie-breaking reproducible.

    Record layout:
        (rot, left_col, bottoms, grouped, sum_dy, min_dy)
      left_col : absolute leftmost OCCUPIED column (engine.placement_left_col),
                 not the bounding-box origin x. The distinction matters for
                 pieces whose box has an empty first column.
      bottoms  : ((abs_col, max_dy), ...) -- the cell the drop rests on, per
                 occupied column.
      grouped  : ((abs_col, (dy, ...)), ...) -- all cells, grouped by column,
                 so locking is one OR per column.
      sum_dy   : sum of dy over the 4 cells, for landing_height in one step.
      min_dy   : topmost cell offset, for the above-the-board legality test.
    """
    table = []
    for piece in range(N_PIECES):
        recs = []
        for rot in UNIQUE_ROTS[piece]:
            cells = PIECE_CELLS[piece][rot]
            lo, hi = X_RANGE[piece][rot]
            by_col = {}
            for dx, dy in cells:
                by_col.setdefault(dx, []).append(dy)
            sum_dy = sum(dy for _dx, dy in cells)
            mind = MIN_DY[piece][rot]
            for x in range(lo, hi):
                bottoms = tuple((x + dx, max(dys))
                                for dx, dys in sorted(by_col.items()))
                grouped = tuple((x + dx, tuple(sorted(dys)))
                                for dx, dys in sorted(by_col.items()))
                recs.append((rot, x + MIN_DX[piece][rot], bottoms, grouped,
                             sum_dy, mind))
        table.append(recs)
    return table


PLACEMENTS = _build_placement_table()
PLACEMENT_COUNTS = tuple(len(p) for p in PLACEMENTS)


# Cells a freshly spawned piece occupies: rotation 0, x = SPAWN_X[piece],
# y = SPAWN_Y. Stored as (absolute_column, row) pairs.
SPAWN_CELLS = tuple(
    tuple((SPAWN_X[p] + dx, SPAWN_Y + dy) for dx, dy in PIECE_CELLS[p][0])
    for p in range(N_PIECES)
)


def score_clear(n: int, level: int, b2b: int, combo: int):
    """Score one lock: (score_delta, b2b_next, combo_next, b2b_applied).

    IMPORTED FROM THE ENGINE, NOT REIMPLEMENTED. Back-to-back and combo rules
    are exactly the kind of thing that drifts when written twice, and if the
    training reward drifts from the score on screen, the agent optimises a game
    the user is not watching. `engine._score_clear` is the single source.
    """
    return _engine_score_clear(n, level, b2b, combo)


def level_for(total_lines: int) -> int:
    """Engine levelling: +1 every 10 lines, starting at 1."""
    return 1 + total_lines // LINES_PER_LEVEL


def spawn_blocked(cols, piece: int) -> bool:
    """True if `piece` cannot be spawned -- the engine's game-over condition.

    This is NOT the same as "no legal placement exists", and conflating the two
    was a real divergence caught by the parity test. The stack can reach the
    spawn area in the middle columns while the outer columns still have room,
    so a board can offer plenty of legal hard drops and still be a loss. The
    engine decides on spawn collision, so this file must too.
    """
    for c, y in SPAWN_CELLS[piece]:
        if cols[c] >> y & 1:
            return True
    return False


# ---------------------------------------------------------------------------
# Deterministic RNG -- mirrors engine/rng.py and web/engine.js
# ---------------------------------------------------------------------------

class XorShift32:
    """xorshift32 as fixed in PROJECT.md.

        x ^= x << 13;  x ^= x >>> 17;  x ^= x << 5     (32-bit unsigned)

    Seed 0 is a fixed point (it would emit zeros forever), so it is remapped
    to 1. The engine does the same; parity would break at seed 0 otherwise.
    """

    __slots__ = ("x",)
    MASK = 0xFFFFFFFF

    def __init__(self, seed: int):
        self.x = (seed & self.MASK) or 1

    def next_u32(self) -> int:
        x = self.x
        x ^= (x << 13) & self.MASK
        x ^= x >> 17
        x ^= (x << 5) & self.MASK
        self.x = x
        return x


class BagRandomizer:
    """7-bag: shuffle all seven pieces, deal them, reshuffle.

    Fisher-Yates walking i from the back to the front with
    `j = rand() % (i + 1)`. The order of operations is part of the
    cross-language contract -- do not 'optimise' it.
    """

    __slots__ = ("rng", "bag", "idx")

    def __init__(self, seed: int):
        self.rng = XorShift32(seed)
        self.bag = []
        self.idx = N_PIECES
        self._refill()

    def _refill(self):
        bag = list(range(N_PIECES))
        for i in range(N_PIECES - 1, 0, -1):
            j = self.rng.next_u32() % (i + 1)
            bag[i], bag[j] = bag[j], bag[i]
        self.bag = bag
        self.idx = 0

    def next_piece(self) -> int:
        if self.idx >= N_PIECES:
            self._refill()
        p = self.bag[self.idx]
        self.idx += 1
        return p

    def peek_next(self) -> int:
        """The piece AFTER the one `next_piece` would return -- i.e. the one a
        1-ply search looks ahead to. Consumes nothing.

        The bag is refilled from a deterministic RNG, so peeking across a bag
        boundary must not advance that RNG or the piece stream would change
        depending on whether anyone looked. When the peek falls past the end of
        the current bag, the next bag is computed on a COPY of the generator
        state and thrown away.
        """
        i = self.idx + 1
        if i < N_PIECES:
            return self.bag[i]
        saved = self.rng.x                 # peek across the boundary
        probe = list(range(N_PIECES))
        for k in range(N_PIECES - 1, 0, -1):
            j = self.rng.next_u32() % (k + 1)
            probe[k], probe[j] = probe[j], probe[k]
        self.rng.x = saved                 # rewind: nothing was consumed
        return probe[i - N_PIECES]


# ---------------------------------------------------------------------------
# Features (visible field only)
# ---------------------------------------------------------------------------

def visible(cols):
    """Drop the spawn buffer: bit r of the result is visible row r."""
    return [m >> BUFFER_ROWS for m in cols]


def heights_from_cols(cols):
    """Engine convention: topmost filled row per column, TOTAL_ROWS if empty.

    This drives the drop, not the features.
    """
    top = [TOTAL_ROWS] * BOARD_W
    for c in range(BOARD_W):
        m = cols[c]
        if m:
            top[c] = (m & -m).bit_length() - 1
    return top


def visible_heights(cols):
    """Feature heights: 20 - topmost filled VISIBLE row, 0 if none."""
    h = [0] * BOARD_W
    for c in range(BOARD_W):
        m = cols[c] >> BUFFER_ROWS
        if m:
            h[c] = BOARD_H - ((m & -m).bit_length() - 1)
    return h


def well_extras_bits(cols):
    """(max_well_depth, well_count>=3) from the column masks.

    Same well definition as `cumulative_wells`; see features.well_runs for why
    these two exist alongside it. Reuses the run-peeling trick but tracks run
    lengths instead of summing them.
    """
    vs = [cols[c] >> BUFFER_ROWS for c in range(BOARD_W)]
    max_d = 0
    n3 = 0
    for c in range(BOARD_W):
        left = vs[c - 1] if c > 0 else FULL_VIS
        right = vs[c + 1] if c < BOARD_W - 1 else FULL_VIS
        w = (~vs[c]) & left & right & FULL_VIS
        d = 0
        for r in range(BOARD_H):
            if w >> r & 1:
                d += 1
            elif d:
                if d > max_d:
                    max_d = d
                if d >= 3:
                    n3 += 1
                d = 0
        if d:
            if d > max_d:
                max_d = d
            if d >= 3:
                n3 += 1
    return max_d, n3


def board_features_bits(cols, _top=None):
    """The six board-only features, in FEATURE_NAMES order.

    Returns (row_transitions, column_transitions, holes, cumulative_wells,
             aggregate_height, bumpiness).
    Verified equal to `features.board_features` on random and reachable boards.

    `_top` is accepted and ignored so callers can pass the drop-side height
    array without a second signature; feature heights are derived from the
    visible masks here, because a column filled only inside the spawn buffer
    has a visible height that `top` cannot express.
    """
    v0 = cols[0] >> BUFFER_ROWS
    v9 = cols[9] >> BUFFER_ROWS

    agg = 0
    holes = 0
    ctrans = 0
    wells = 0
    hs = [0] * BOARD_W
    vs = [0] * BOARD_W

    for c in range(BOARD_W):
        m = cols[c] >> BUFFER_ROWS
        vs[c] = m
        h = BOARD_H - ((m & -m).bit_length() - 1) if m else 0
        hs[c] = h
        agg += h
        holes += h - m.bit_count()
        vv = m | (1 << BOARD_H)                 # filled floor below row 19
        ctrans += ((vv ^ (vv << 1)) & COL_TRANS_MASK).bit_count()

    for c in range(BOARD_W):
        left = vs[c - 1] if c > 0 else FULL_VIS
        right = vs[c + 1] if c < BOARD_W - 1 else FULL_VIS
        w0 = (~vs[c]) & left & right & FULL_VIS
        x = w0
        while x:
            wells += x.bit_count()
            x = (x << 1) & w0

    bump = 0
    rtrans = (BOARD_H - v0.bit_count()) + (BOARD_H - v9.bit_count())
    for c in range(BOARD_W - 1):
        d = hs[c] - hs[c + 1]
        bump += d if d >= 0 else -d
        rtrans += (vs[c] ^ vs[c + 1]).bit_count()

    return rtrans, ctrans, holes, wells, agg, bump


# ---------------------------------------------------------------------------
# Placement simulation
# ---------------------------------------------------------------------------

def simulate(cols, top, rec):
    """Drop one piece. `cols` and `top` are not mutated.

    Returns None if the placement is illegal (the piece would stick out above
    row 0, which is the engine's game-over condition), otherwise
        (new_cols, new_top, lines_cleared, landing_height, eroded_piece_cells)

    The resting row is the highest the piece can be pushed down before its
    lowest cell in some column meets that column's stack:

        y_rest = min over occupied columns of ( top[col] - bottom_dy - 1 )

    matching `engine.legal_placements`. Hard-drop semantics: no tucking under
    overhangs, which is exactly the move set a placement-level agent may use.
    """
    _rot, _left, bottoms, grouped, sum_dy, min_dy = rec

    y_rest = TOTAL_ROWS
    for abs_c, bdy in bottoms:
        cand = top[abs_c] - bdy - 1
        if cand < y_rest:
            y_rest = cand
    if y_rest + min_dy < 0:
        return None                    # sticks out above the board -> illegal

    new_cols = list(cols)
    for abs_c, dys in grouped:
        m = new_cols[abs_c]
        for dy in dys:
            m |= 1 << (y_rest + dy)
        new_cols[abs_c] = m

    # landing_height: mean height above the floor of the four locked cells,
    # measured BEFORE clearing. Visible row r = y - 2, height = 20 - r = 22 - y.
    landing_height = TOTAL_ROWS - y_rest - sum_dy * 0.25

    # Only rows the piece touched can have become full.
    full = 0
    for _abs_c, dys in grouped:
        for dy in dys:
            full |= 1 << (y_rest + dy)
    for m in new_cols:
        full &= m
        if not full:
            break

    lines = 0
    eroded = 0
    if full:
        lines = full.bit_count()
        piece_cells_in_cleared = 0
        for _abs_c, dys in grouped:
            for dy in dys:
                if full >> (y_rest + dy) & 1:
                    piece_cells_in_cleared += 1
        eroded = lines * piece_cells_in_cleared

        rows = []
        f = full
        while f:
            low = f & -f
            rows.append(low.bit_length() - 1)
            f ^= low
        # Removing row y shifts every row ABOVE it (index < y) down by one;
        # rows below keep their index, so ascending order stays valid.
        for c in range(BOARD_W):
            m = new_cols[c]
            for y in rows:
                above = m & ((1 << y) - 1)
                below = m & ~((1 << (y + 1)) - 1)
                m = (above << 1) | below
            new_cols[c] = m
        new_top = heights_from_cols(new_cols)
    else:
        new_top = list(top)
        for abs_c, dys in grouped:
            y = y_rest + dys[0]        # dys is sorted; first is topmost
            if y < new_top[abs_c]:
                new_top[abs_c] = y

    return new_cols, new_top, lines, landing_height, eroded


class FastGame:
    """One game, placement by placement."""

    __slots__ = ("cols", "top", "bag", "current", "lines_total",
                 "pieces_total", "game_over", "seed")

    def __init__(self, seed: int):
        self.reset(seed)

    def reset(self, seed: int):
        self.seed = seed
        self.cols = [0] * BOARD_W
        self.top = [TOTAL_ROWS] * BOARD_W
        self.bag = BagRandomizer(seed)
        self.current = self.bag.next_piece()
        self.lines_total = 0
        self.pieces_total = 0
        self.game_over = False

    def legal_placements(self):
        """Records whose drop actually fits, in (rotation, x) order."""
        if self.game_over:
            return []
        return [r for r in PLACEMENTS[self.current]
                if simulate(self.cols, self.top, r) is not None]

    def apply(self, rec):
        res = simulate(self.cols, self.top, rec)
        if res is None:
            self.game_over = True
            return 0
        self.cols, self.top, lines, _lh, _er = res
        self.lines_total += lines
        self.pieces_total += 1
        self.current = self.bag.next_piece()
        # The engine ends the game when the NEXT piece cannot spawn.
        if spawn_blocked(self.cols, self.current):
            self.game_over = True
        return lines

    def to_array(self):
        return cols_to_array(self.cols)


def cols_to_array(cols):
    """(20, 10) uint8 visible board, matching engine.board_array()."""
    b = np.zeros((BOARD_H, BOARD_W), dtype=np.uint8)
    for c in range(BOARD_W):
        m = cols[c] >> BUFFER_ROWS
        for r in range(BOARD_H):
            if m >> r & 1:
                b[r, c] = 1
    return b


def array_to_cols(board):
    """Inverse of cols_to_array. Buffer rows come back empty."""
    cols = [0] * BOARD_W
    for c in range(BOARD_W):
        m = 0
        for r in range(BOARD_H):
            if board[r, c]:
                m |= 1 << r
        cols[c] = m << BUFFER_ROWS
    return cols


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _self_test(verbose=True):
    import features as F

    ok = True

    for name, board, expected in F._parity_fixtures():
        got = board_features_bits(array_to_cols(board))
        if got != expected:
            print(f"  FAIL fixture {name}: bits {got} != expected {expected}")
            ok = False
    if verbose and ok:
        print("  ok   5 hand fixtures match")

    rng = np.random.default_rng(7)
    bad = 0
    for _ in range(2000):
        b = (rng.random((BOARD_H, BOARD_W)) < rng.uniform(0.1, 0.6)).astype(np.uint8)
        if board_features_bits(array_to_cols(b)) != F.board_features(b):
            bad += 1
    if bad:
        print(f"  FAIL {bad}/2000 random boards disagree with features.py")
        ok = False
    elif verbose:
        print("  ok   2000 random boards: bitboard == numpy reference")

    bad = 0
    for seed in range(1, 30):
        g = FastGame(seed)
        r = np.random.default_rng(seed)
        for _ in range(300):
            recs = g.legal_placements()
            if not recs:
                break
            g.apply(recs[r.integers(len(recs))])
            if board_features_bits(g.cols) != F.board_features(g.to_array()):
                bad += 1
                break
    if bad:
        print(f"  FAIL {bad} reachable-board mismatches")
        ok = False
    elif verbose:
        print("  ok   reachable boards from 29 random games match")

    bad = 0
    for seed in range(1, 30):
        g = FastGame(seed)
        r = np.random.default_rng(seed + 100)
        for _ in range(300):
            recs = g.legal_placements()
            if not recs:
                break
            g.apply(recs[r.integers(len(recs))])
            if g.top != heights_from_cols(g.cols):
                bad += 1
                break
    if bad:
        print(f"  FAIL {bad} incremental-top mismatches")
        ok = False
    elif verbose:
        print("  ok   incremental top == recomputed top")

    bad = 0
    for seed in range(1, 20):
        g = FastGame(seed)
        r = np.random.default_rng(seed + 200)
        for _ in range(300):
            recs = g.legal_placements()
            if not recs:
                break
            before = sum(m.bit_count() for m in g.cols)
            lines = g.apply(recs[r.integers(len(recs))])
            after = sum(m.bit_count() for m in g.cols)
            if after != before + 4 - 10 * lines:
                bad += 1
                break
    if bad:
        print(f"  FAIL {bad} cell-count violations after line clear")
        ok = False
    elif verbose:
        print("  ok   cell conservation across line clears")

    for seed in (1, 2, 12345, 0xDEADBEEF):
        bag = BagRandomizer(seed)
        seq = [bag.next_piece() for _ in range(70)]
        for i in range(0, 70, 7):
            if sorted(seq[i:i + 7]) != list(range(7)):
                print(f"  FAIL 7-bag violated at seed {seed} chunk {i}")
                ok = False
                break
    if verbose and ok:
        print("  ok   7-bag permutation property")

    if verbose:
        print(f"  ok   placement counts per piece {PLACEMENT_COUNTS} "
              f"(from engine tables)")

    print("PASS" if ok else "FAILED")
    return ok


def _bench():
    import time
    t0 = time.perf_counter()
    n = 0
    r = np.random.default_rng(0)
    for seed in range(1, 40):
        g = FastGame(seed)
        for _ in range(400):
            recs = PLACEMENTS[g.current]
            legal = []
            for rec in recs:
                res = simulate(g.cols, g.top, rec)
                n += 1
                if res is not None:
                    board_features_bits(res[0])
                    legal.append(rec)
            if not legal:
                break
            g.apply(legal[r.integers(len(legal))])
    dt = time.perf_counter() - t0
    print(f"  {n} placement evaluations in {dt:.2f}s -> {n/dt:,.0f} eval/s")


if __name__ == "__main__":
    _self_test()
    _bench()
