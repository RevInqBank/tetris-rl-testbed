"""Afterstate feature extraction for Tetris (Dellacherie 6 + 2).

WHY THIS FILE IS THE CONTRACT
-----------------------------
Every strategy in this project (CEM, REINFORCE, A2C, DQN, 1-ply search) scores a
candidate placement by turning the *afterstate* -- the board as it looks right
after the piece has locked and any completed lines have been removed -- into the
same 8-dimensional vector. The web UI re-implements these same 8 numbers in
JavaScript so it can run the trained weights in the browser. If the two
implementations disagree by even one feature, the JS agent plays a different
game than the trained one.

So the definitions below are NORMATIVE, not descriptive. `web/policies.js` must
mirror them exactly. Ambiguities that bite in practice (does a wall count as
filled? is height measured from the top or the bottom? is a well one cell or a
column of cells?) are each pinned down explicitly.

BOARD CONVENTION (agreed with `engine`)
---------------------------------------
    board : np.ndarray, shape (20, 10), dtype uint8, values {0, 1}
    board[0]  is the TOP visible row
    board[19] is the BOTTOM row (the floor is just below it)
    board[r][c] == 1 means the cell is occupied.

The 2-row invisible spawn buffer above the visible field is NOT part of this
array. Features are computed on the visible 20x10 field only.

"Height" always means distance up from the floor, so a block sitting on the
floor at row 19 has height 1, and a block at row 0 has height 20:

    height_of_row(r) = 20 - r          (equivalently BOARD_H - r)

FEATURE ORDER (normative -- weights vectors are in this order)
--------------------------------------------------------------
    0 landing_height
    1 eroded_piece_cells
    2 row_transitions
    3 column_transitions
    4 holes
    5 cumulative_wells
    6 aggregate_height
    7 bumpiness

Two of these (landing_height, eroded_piece_cells) depend on WHERE THE PIECE
LANDED and cannot be recovered from the afterstate board alone -- once lines are
cleared, the evidence is gone. The engine supplies them in the `info` dict
returned by `apply_placement` / `simulate_placement`. The other six are pure
functions of the afterstate board.

SIGN CONVENTION
---------------
No feature is negated here. Features are reported as raw magnitudes, and the
learned weight vector carries the sign (Dellacherie's hand weights, for
instance, are negative for holes and positive for eroded_piece_cells). Keeping
the extractor sign-free means the JS mirror has nothing extra to remember.
"""

from __future__ import annotations

import numpy as np

BOARD_H = 20
BOARD_W = 10

FEATURE_NAMES = (
    "landing_height",
    "eroded_piece_cells",
    "row_transitions",
    "column_transitions",
    "holes",
    "cumulative_wells",
    "aggregate_height",
    "bumpiness",
)
N_FEATURES = len(FEATURE_NAMES)

# ---------------------------------------------------------------------------
# NAMED FEATURE SETS -- how new features get added without breaking old files
# ---------------------------------------------------------------------------
# FEATURE_NAMES above is FROZEN. Appending to it would silently change
# N_FEATURES and invalidate every weights file already published (they all
# carry 8 numbers), and the failure mode would be a shifted index rather than
# an exception. So a new feature set is a NEW NAMED TUPLE, and each weights
# file records which set it used in its own `features` field.
#
# CONSUMERS MUST READ BY NAME, NOT BY INDEX. Given a weights file, build the
# vector in the order that file's `features` list specifies -- then old files
# keep working no matter what sets are added later.
WELLS_EXTRA = ("max_well_depth", "well_count")

FEATURE_SETS = {
    "dellacherie8": FEATURE_NAMES,
    "wells10": FEATURE_NAMES + WELLS_EXTRA,
}
DEFAULT_FEATURE_SET = "dellacherie8"

# Every name any set may contain. Consumers validate against this and treat an
# unknown name as a hard error -- never as a zero-filled column.
KNOWN_FEATURES = frozenset(n for names in FEATURE_SETS.values() for n in names)

# Input scaling for NETWORK policies only. Linear policies eat raw features --
# argmax is scale-invariant there, so the weights just absorb the units. A net
# cannot: an unnormalised input makes the first layer see only the largest
# feature. Divisors are 99th percentiles from random play, rounded.
# Keyed BY NAME so a new feature set gets its scales automatically.
FEATURE_SCALE_BY_NAME = {
    "landing_height": 20.0,
    "eroded_piece_cells": 4.0,
    "row_transitions": 110.0,
    "column_transitions": 60.0,
    "holes": 75.0,
    "cumulative_wells": 75.0,
    "aggregate_height": 160.0,
    "bumpiness": 40.0,
    "max_well_depth": 20.0,   # a well cannot be deeper than the visible field
    "well_count": 4.0,        # 5 disjoint depth-3 wells is already pathological
}


def feature_scale(names):
    """Scale vector matching `names`, for network inputs."""
    import numpy as _np
    return _np.array([FEATURE_SCALE_BY_NAME[n] for n in names], dtype=_np.float64)

# Index constants, so callers never hard-code positions.
F_LANDING_HEIGHT = 0
F_ERODED_PIECE_CELLS = 1
F_ROW_TRANSITIONS = 2
F_COLUMN_TRANSITIONS = 3
F_HOLES = 4
F_CUMULATIVE_WELLS = 5
F_AGGREGATE_HEIGHT = 6
F_BUMPINESS = 7


# ---------------------------------------------------------------------------
# Column heights -- the shared primitive
# ---------------------------------------------------------------------------

def column_heights(board: np.ndarray) -> np.ndarray:
    """Height of each column, measured up from the floor.

    DEFINITION
        height[c] = 20 - (index of the topmost filled row in column c)
        height[c] = 0 if column c is completely empty.

    Note this is the height of the column's SKYLINE, not a count of filled
    cells: a column with a filled cell at row 5 and nothing else still has
    height 15, and the 14 empty cells beneath it are holes.

    Returns int32 array of shape (10,).
    """
    filled = board.astype(bool)
    any_filled = filled.any(axis=0)
    # argmax on a boolean column returns the first True (topmost filled row).
    top_row = filled.argmax(axis=0)
    return np.where(any_filled, BOARD_H - top_row, 0).astype(np.int32)


# ---------------------------------------------------------------------------
# The six board-only features
# ---------------------------------------------------------------------------

def row_transitions(board: np.ndarray) -> int:
    """Number of horizontal filled/empty alternations, summed over all rows.

    DEFINITION
        Scan each row left to right across 12 positions: a virtual LEFT WALL,
        the 10 cells, and a virtual RIGHT WALL. Both walls count as FILLED.
        Count each adjacent pair whose occupancy differs.

    Rationale for filled walls: a lone cell hugging the wall is not a
    transition (the wall continues it), whereas a gap against the wall is. This
    is Dellacherie's convention.

    Worked example, row = 1 1 0 0 1 1 1 1 1 1 with walls -> W 1 1 0 0 1 1 1 1 1 1 W
        pairs that differ: (1,0) at index 2 and (0,1) at index 4  ->  2

    A COMPLETELY EMPTY ROW CONTRIBUTES 2 (wall->empty, empty->wall), not 0.
    This matters: empty rows above the stack are not free. Both implementations
    must include them.
    """
    filled = board.astype(np.int8)
    # Pad left and right with a filled wall column.
    walls = np.ones((BOARD_H, 1), dtype=np.int8)
    padded = np.concatenate([walls, filled, walls], axis=1)
    return int(np.count_nonzero(padded[:, 1:] != padded[:, :-1]))


def column_transitions(board: np.ndarray) -> int:
    """Number of vertical filled/empty alternations, summed over all columns.

    DEFINITION
        Scan each column top to bottom across 12 positions: a virtual CEILING,
        the 20 cells, and a virtual FLOOR.
            CEILING counts as EMPTY  (open sky above the board)
            FLOOR   counts as FILLED (solid ground below the board)
        Count each adjacent pair whose occupancy differs.

    The asymmetry is deliberate and is the usual Dellacherie convention: the top
    of the board is open, the bottom is not. An empty column therefore
    contributes exactly 1 (the empty->floor step), and a column filled solid to
    the top also contributes 1 (the ceiling->filled step).
    """
    filled = board.astype(np.int8)
    ceiling = np.zeros((1, BOARD_W), dtype=np.int8)   # empty above
    floor = np.ones((1, BOARD_W), dtype=np.int8)      # solid below
    padded = np.concatenate([ceiling, filled, floor], axis=0)
    return int(np.count_nonzero(padded[1:, :] != padded[:-1, :]))


def holes(board: np.ndarray) -> int:
    """Number of empty cells that have at least one filled cell somewhere above
    them in the same column.

    DEFINITION
        A cell (r, c) is a hole iff board[r][c] == 0 AND there exists r' < r
        with board[r'][c] == 1.

    This counts CELLS, not cavities: a column with three stacked empty cells
    under an overhang contributes 3, not 1. Covered cells that are themselves
    reachable from the side still count -- the feature is deliberately blind to
    reachability, because Dellacherie's is.
    """
    filled = board.astype(bool)
    # covered[r, c] is True if any cell strictly above (r, c) is filled.
    covered = np.logical_or.accumulate(filled, axis=0)
    covered_above = np.zeros_like(covered)
    covered_above[1:, :] = covered[:-1, :]
    return int(np.count_nonzero(covered_above & ~filled))


def cumulative_wells(board: np.ndarray) -> int:
    """Sum of triangular numbers over the depth of every well.

    DEFINITION
        A cell (r, c) is a WELL CELL iff it is empty and both horizontal
        neighbours are filled, where the left wall (c == 0) and the right wall
        (c == 9) each count as FILLED.

        Well cells that are vertically contiguous form a well of some depth d.
        For each such run, add  1 + 2 + ... + d  =  d(d+1)/2.
        The total over all runs is the feature.

    The triangular weighting is the whole point: it makes one deep shaft far
    more costly than several shallow notches, which is what discourages the
    agent from digging a 9-deep I-piece trench.

    Worked example: column 9 empty at rows 16,17,18 with column 8 filled beside
    each -> one run of depth 3 -> 1+2+3 = 6.

    Cells lower in a run are the ones weighted more heavily (the deepest cell of
    a depth-3 run contributes 3), which is why we accumulate downward.
    """
    filled = board.astype(bool)
    left = np.ones((BOARD_H, 1), dtype=bool)
    right = np.ones((BOARD_H, 1), dtype=bool)
    padded = np.concatenate([left, filled, right], axis=1)

    # A well cell: empty, with filled neighbours on both sides.
    is_well = (~padded[:, 1:-1]) & padded[:, :-2] & padded[:, 2:]

    total = 0
    # Walk top to bottom accumulating run depth per column.
    depth = np.zeros(BOARD_W, dtype=np.int32)
    for r in range(BOARD_H):
        row = is_well[r]
        depth = np.where(row, depth + 1, 0)
        total += int(depth.sum())
    return total


def well_runs(board: np.ndarray):
    """Depths of every well in the board, as a list.

    A WELL CELL is defined exactly as in `cumulative_wells`: an empty cell whose
    left and right neighbours are both filled, with the walls counting as
    filled. Vertically contiguous well cells form one well, and its DEPTH is
    the number of cells in that run.

    `cumulative_wells` already aggregates these into a triangular-number sum.
    That aggregate answers "how much well am I carrying" but cannot express
    "I am deliberately keeping ONE column open and it is deep enough for an
    I-piece" -- nine shallow notches and one 9-deep shaft can produce a similar
    total. The two features below separate those cases.
    """
    filled = board.astype(bool)
    wall = np.ones((BOARD_H, 1), dtype=bool)
    padded = np.concatenate([wall, filled, wall], axis=1)
    is_well = (~padded[:, 1:-1]) & padded[:, :-2] & padded[:, 2:]
    runs = []
    for c in range(BOARD_W):
        d = 0
        for r in range(BOARD_H):
            if is_well[r, c]:
                d += 1
            elif d:
                runs.append(d)
                d = 0
        if d:
            runs.append(d)
    return runs


def max_well_depth(board: np.ndarray) -> int:
    """Depth of the DEEPEST well, 0 if there are none.

    This is the "is there a shaft ready for an I-piece" feature. A tetris needs
    a well of depth 4, so the difference between 3 and 4 is the difference
    between waiting and scoring -- and `cumulative_wells` cannot say which side
    of that line the board is on.
    """
    runs = well_runs(board)
    return max(runs) if runs else 0


def well_count(board: np.ndarray) -> int:
    """Number of DISTINCT wells of depth >= 3.

    The threshold matters. Counting every one-cell notch would just re-measure
    surface roughness, which `bumpiness` already does. Depth 3+ is the point
    where a well is a commitment: deep enough to be worth keeping, expensive
    enough that having two of them is a real cost. A tetris strategy wants this
    to be exactly 1.
    """
    return sum(1 for d in well_runs(board) if d >= 3)


def aggregate_height(board: np.ndarray) -> int:
    """Sum of the 10 column heights (see `column_heights`)."""
    return int(column_heights(board).sum())


def bumpiness(board: np.ndarray) -> int:
    """Sum of absolute height differences between horizontally adjacent columns.

    DEFINITION
        bumpiness = sum over c in 0..8 of |height[c] - height[c+1]|

    Nine terms, not ten -- the walls are NOT included as height-0 columns. A
    perfectly flat stack of any height has bumpiness 0.
    """
    h = column_heights(board)
    return int(np.abs(np.diff(h)).sum())


# ---------------------------------------------------------------------------
# The two placement-dependent features (documented; supplied by the engine)
# ---------------------------------------------------------------------------
#
# landing_height
#     DEFINITION: the MEAN height, measured up from the floor, of the 4 cells
#     the piece occupies at the moment it locks -- computed BEFORE any completed
#     lines are removed.
#
#     Against ENGINE coordinates (22 rows, y = 21 is the floor), which is what
#     `info["piece_cells"]` reports, this is
#         landing_height = mean(22 - y for (y, x) in piece_cells)
#     and the canonical implementation is `landing_height_from_cells()` below.
#     Against a visible-only row index r (0..19) the same quantity is 20 - r.
#     The two agree because r = y - 2. Mixing them up shifts every value by 2.
#
#     A flat I-piece resting on the floor has landing_height 1.0. A vertical
#     I-piece on the floor occupies the bottom four rows and has
#     landing_height (4+3+2+1)/4 = 2.5.
#
#     Dellacherie's original paper uses the height of the piece's CENTRE. This
#     project uses the MEAN of the occupied cells instead. They coincide for
#     I, O, S and Z but differ by 0.25 for T, J and L. Every weight in
#     `weights/` was trained against the mean; do not "fix" this to the centre.
#
# eroded_piece_cells
#     DEFINITION: (number of lines cleared by this placement)
#                 x (number of cells OF THIS PIECE that sat in those cleared lines)
#     Zero whenever no line was cleared. Maximum 4 x 4 = 16 (an I-piece
#     completing four lines by itself, which cannot actually happen, so the
#     practical max is 4 x 1 = 4 for a tetris where the I contributes one cell
#     per row... in fact a vertical I clearing 4 rows gives 4 x 4 = 16).
#
#     Both quantities must be measured before the rows are removed, which is why
#     only the engine can compute this. It is the sole feature that REWARDS the
#     agent, and CEM will not learn to clear lines without it.
#
# Both arrive in the engine's `info` dict; `extract` below just places them into
# slots 0 and 1.


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def extract(board: np.ndarray, landing_height: float, eroded_piece_cells: int,
            out: np.ndarray | None = None) -> np.ndarray:
    """Build the full 8-dim feature vector for one afterstate.

    Args:
        board: (20, 10) uint8 afterstate -- AFTER the piece locked AND after
            completed lines were removed and the stack settled.
        landing_height: from the engine's info dict (see notes above).
        eroded_piece_cells: from the engine's info dict.
        out: optional float64 buffer of shape (8,) to write into, to avoid
            allocating inside a rollout loop.

    Returns:
        float64 array of shape (8,), ordered per FEATURE_NAMES.
    """
    if out is None:
        out = np.empty(N_FEATURES, dtype=np.float64)
    out[F_LANDING_HEIGHT] = landing_height
    out[F_ERODED_PIECE_CELLS] = eroded_piece_cells
    out[F_ROW_TRANSITIONS] = row_transitions(board)
    out[F_COLUMN_TRANSITIONS] = column_transitions(board)
    out[F_HOLES] = holes(board)
    out[F_CUMULATIVE_WELLS] = cumulative_wells(board)
    out[F_AGGREGATE_HEIGHT] = aggregate_height(board)
    out[F_BUMPINESS] = bumpiness(board)
    return out


ENGINE_ROWS = 22        # engine.ROWS -- 20 visible + 2 hidden spawn buffer


# ---------------------------------------------------------------------------
# Tie-breaking -- NORMATIVE, and not optional
# ---------------------------------------------------------------------------
SCORE_DECIMALS = 9          # retained for callers that still import it
SCORE_EPS = 1e-9            # THE tie tolerance. See argmax_stable.


def argmax_stable(scores):
    """argmax that every implementation agrees on. Ties go to the lowest index.

    THE RULE (no rounding function anywhere -- that is the point):

        best = 0
        for i in 1..n-1:
            if scores[i] > scores[best] + 1e-9:
                best = i

    Only `>`, `+` and a literal constant. Those are IEEE-754 identical in
    Python and JavaScript, so both languages take the same branch by
    construction. Ties resolve to the lowest index, and `legal_placements` is
    sorted by (rotation, column), so the winner is the lowest (rot, col).

    WHY NOT ROUND FIRST -- the previous version did, and it was wrong
        v1 rounded both sides to 9 decimals and compared. Python's `round()`
        does decimal rounding with ties-to-even; JavaScript's
        `Math.round(v*1e9)/1e9` rounds halves toward +infinity AND adds the
        error of multiplying by 1e9. On NEGATIVE scores -- which all of these
        are -- the two go opposite ways. checker measured 302 disagreements in
        708 half-way values, and about one disagreement per 200,000 realistic
        scores. At ~34 placements scored per move that predicts roughly one
        divergence per 11,700-move game, which is exactly what web observed:
        469 checkpoints identical, then a split in the last three moves. The
        bias direction matched too (JS rounded negatives up, so JS liked that
        placement more, so JS survived slightly longer on all three seeds).

        Rounding was never load-bearing -- the job was "treat differences
        below 1e-9 as ties", and an epsilon does that directly without
        depending on any rounding mode.

    WHY THE UNDERLYING PROBLEM IS REAL
        Placement scores collide at the last bit far more often than intuition
        suggests. Measured on `cem_score`, seed 900003, step 1327:

            placement  7 -> -23.665216819989176
            placement 16 -> -23.665216819989173

        Three parts in 1e15. The same move as far as the policy is concerned,
        but argmax must still return one index -- and which one depended on the
        order the dot product accumulated in. numpy's 2-D `X @ w` (BLAS) and a
        scalar loop genuinely disagreed. One flipped tie is not noise: the two
        placements are different moves, so the games diverge completely from
        that point. Measured cost of two flips in one game: 1,996 lines from
        one harness and 800 from the other, for the SAME weights and seed.

    `web` must use exactly this, with the same constant:
        let best = 0;
        for (let i = 1; i < s.length; i++) if (s[i] > s[best] + 1e-9) best = i;

    A LATENT DIVERGENCE THAT IS DOCUMENTED, NOT FIXED
        The rounding version this replaced had a real cross-language hazard:
        Python `round()` sends ties to even, JavaScript `Math.round()` sends
        them toward +infinity, and ALL SCORES HERE ARE NEGATIVE, so the two
        rules go opposite ways. checker measured 302 disagreements among 708
        exactly-half values.

        It was NOT the cause of the divergence we were chasing (that turned out
        to be an undocumented death penalty on the JS side). web checked 42,594
        real in-game scores including the endgame and found ZERO disagreements
        -- exactly-half values do not arise in the real score distribution.

        The epsilon rule above removes the hazard structurally, which is why it
        is kept. But if anyone reintroduces a rounding step, this is the trap.

    EVIDENCE THAT A TIE RULE IS LOAD-BEARING AT ALL
        web compared 6,930 endgame feature vectors scored by numpy BLAS and by
        a JS scalar loop: 34.1% of the RAW dot products differ in the last bit,
        and 0% differ after the tie rule is applied. Without any tie rule this
        project is not reproducible across languages -- the "1,996 lines vs
        800 lines" split below would be routine rather than rare.
    

    WHY THIS EXISTS
        Placement scores collide at the last bit far more often than intuition
        suggests. A measured example from `cem_score` on seed 900003, step 1327:

            placement  7 -> -23.665216819989176
            placement 16 -> -23.665216819989173

        Three parts in 1e15. These are the same move as far as the policy is
        concerned, but `argmax` must still return one index -- and WHICH index
        depends on the order the dot product accumulated in. numpy's 2-D
        `X @ w` (BLAS, blocked/vectorised) and a plain scalar loop genuinely
        disagree here, and so will JavaScript.

        One flipped tie is not a rounding error you can average away. The two
        placements are different moves, so the games diverge completely from
        that point on. Measured cost of exactly two such flips in one game:
        the engine harness reported 1,996 lines and the fastsim harness 800,
        for the SAME weights on the SAME seed. Both were "correct"; they had
        simply taken different branches at a coin flip.

    THE RULE
        Round every score to 9 decimals, then take the FIRST maximum. Since
        `legal_placements` is sorted by (rotation, column), the first maximum is
        the lowest (rotation, column) -- a rule any language can reproduce.

        9 decimals is comfortably below anything the weights express (scores run
        to tens) and comfortably above float64 noise (~1e-14 here).

    `web` must do the same:
        const r = s.map(v => Math.round(v * 1e9) / 1e9);
        let best = 0;
        for (let i = 1; i < r.length; i++) if (r[i] > r[best]) best = i;
    """
    best_i = 0
    best_v = float(scores[0])
    for i in range(1, len(scores)):
        v = float(scores[i])
        if v > best_v + SCORE_EPS:
            best_v = v
            best_i = i
    return best_i


def landing_height_from_cells(piece_cells) -> float:
    """THE NORMATIVE landing_height FORMULA. Everyone computes it from here.

        landing_height = mean over the 4 locked cells of (22 - y)

    `piece_cells` is the engine's `info["piece_cells"]`: the absolute (y, x) of
    the four cells the piece occupies, measured BEFORE any line clear.

    COORDINATES -- this is where off-by-two lives:
        The engine board is 22 rows. y = 0, 1 are the hidden spawn buffer and
        y = 2 .. 21 are the visible field, so the BOTTOM row is y = 21.
        Height above the floor is therefore  22 - y,  which makes a cell resting
        on the floor have height 1 (not 0), and a cell in the top visible row
        (y = 2) have height 20.

        Do NOT write `20 - y`. That would be right only if y were a visible-row
        index; against engine coordinates it shifts every value by 2.

    MEAN, NOT BOUNDING-BOX CENTRE:
        Dellacherie's paper uses the height of the piece's centre. This project
        uses the mean of the four occupied cells. For I, O, S and Z the two
        agree; for T, J and L they differ by 0.25. `docs/spec.md` once quoted
        the centre form -- that text is superseded. Every weight in `weights/`
        was trained against the MEAN, and switching now would silently
        invalidate all of them.
    """
    return sum(ENGINE_ROWS - y for y, _x in piece_cells) / len(piece_cells)


def extract_from_info(board: np.ndarray, info: dict,
                      out: np.ndarray | None = None) -> np.ndarray:
    """Build the feature vector from an afterstate board plus the engine's info.

    The engine deliberately reports raw geometry only -- it has no
    `landing_height` field, because the formula belongs to this file. So the two
    placement features are derived here:

        landing_height     <- info["piece_cells"]      (see above)
        eroded_piece_cells <- info["eroded_piece_cells"], or reconstructed from
                              lines_cleared * cleared_piece_cells

    `board` must be the AFTERSTATE (after locking and after line clears), as
    `engine.board_array(next_state)` returns it.
    """
    eroded = info.get("eroded_piece_cells")
    if eroded is None:
        eroded = info["lines_cleared"] * info["cleared_piece_cells"]
    return extract(board,
                   landing_height_from_cells(info["piece_cells"]),
                   int(eroded),
                   out=out)


# ---------------------------------------------------------------------------
# Fast path: all six board features in one pass
# ---------------------------------------------------------------------------

def board_features(board: np.ndarray) -> tuple[int, int, int, int, int, int]:
    """The six board-only features as a tuple, in FEATURE_NAMES order
    (row_transitions, column_transitions, holes, cumulative_wells,
     aggregate_height, bumpiness).

    Identical results to calling the six functions individually; kept separate
    so hot loops can avoid six passes over the board.
    """
    filled = board.astype(bool)

    # --- heights (shared) ---
    any_filled = filled.any(axis=0)
    top_row = filled.argmax(axis=0)
    h = np.where(any_filled, BOARD_H - top_row, 0).astype(np.int32)
    agg = int(h.sum())
    bump = int(np.abs(np.diff(h)).sum())

    # --- row transitions (walls filled) ---
    wall = np.ones((BOARD_H, 1), dtype=bool)
    hpad = np.concatenate([wall, filled, wall], axis=1)
    rt = int(np.count_nonzero(hpad[:, 1:] != hpad[:, :-1]))

    # --- column transitions (ceiling empty, floor filled) ---
    ceil_ = np.zeros((1, BOARD_W), dtype=bool)
    floor_ = np.ones((1, BOARD_W), dtype=bool)
    vpad = np.concatenate([ceil_, filled, floor_], axis=0)
    ct = int(np.count_nonzero(vpad[1:, :] != vpad[:-1, :]))

    # --- holes ---
    covered = np.logical_or.accumulate(filled, axis=0)
    covered_above = np.zeros_like(covered)
    covered_above[1:, :] = covered[:-1, :]
    ho = int(np.count_nonzero(covered_above & ~filled))

    # --- cumulative wells (reuse the horizontal padding) ---
    is_well = (~hpad[:, 1:-1]) & hpad[:, :-2] & hpad[:, 2:]
    total = 0
    depth = np.zeros(BOARD_W, dtype=np.int32)
    for r in range(BOARD_H):
        depth = np.where(is_well[r], depth + 1, 0)
        total += int(depth.sum())

    return rt, ct, ho, total, agg, bump


def extract_named(names, board: np.ndarray, landing_height: float,
                  eroded_piece_cells: int) -> np.ndarray:
    """Build a feature vector in the order `names` gives.

    This is the name-based path every consumer should use: pass the `features`
    list straight out of the weights file and the vector matches that file, no
    matter which set it was trained with.

    Raises on an unknown name rather than substituting zero -- a silently
    zero-filled feature is exactly the failure this project keeps hitting.
    """
    rt, ct, ho, cw, agg, bump = board_features(board)
    table = {
        "landing_height": landing_height,
        "eroded_piece_cells": eroded_piece_cells,
        "row_transitions": rt,
        "column_transitions": ct,
        "holes": ho,
        "cumulative_wells": cw,
        "aggregate_height": agg,
        "bumpiness": bump,
    }
    if any(n in ("max_well_depth", "well_count") for n in names):
        runs = well_runs(board)
        table["max_well_depth"] = max(runs) if runs else 0
        table["well_count"] = sum(1 for d in runs if d >= 3)
    try:
        return np.array([table[n] for n in names], dtype=np.float64)
    except KeyError as e:
        raise KeyError(f"unknown feature {e}; known: {sorted(table)}") from None


def extract_fast(board: np.ndarray, landing_height: float,
                 eroded_piece_cells: int,
                 out: np.ndarray | None = None) -> np.ndarray:
    """Same result as `extract`, single pass. Used inside rollouts."""
    if out is None:
        out = np.empty(N_FEATURES, dtype=np.float64)
    rt, ct, ho, cw, agg, bump = board_features(board)
    out[0] = landing_height
    out[1] = eroded_piece_cells
    out[2] = rt
    out[3] = ct
    out[4] = ho
    out[5] = cw
    out[6] = agg
    out[7] = bump
    return out


# ---------------------------------------------------------------------------
# Dellacherie's published hand weights -- panel 2's control policy
# ---------------------------------------------------------------------------
#
# These are the original weights from Fahey/Dellacherie, re-ordered into our
# FEATURE_NAMES order. Score = dot(DELLACHERIE_WEIGHTS, features), pick the
# placement with the highest score. They are NOT learned; panel 2 exists to show
# what a human-designed evaluation function achieves with no critic at all.
DELLACHERIE_WEIGHTS = np.array([
    -4.500158825082766,   # landing_height
     3.4181268101392694,  # eroded_piece_cells
    -3.2178882868487753,  # row_transitions
    -9.348695305445199,   # column_transitions
    -7.899265427351652,   # holes
    -3.3855972247263626,  # cumulative_wells
     0.0,                 # aggregate_height   (not in Dellacherie's original 6)
     0.0,                 # bumpiness          (not in Dellacherie's original 6)
], dtype=np.float64)


# ---------------------------------------------------------------------------
# Self-test -- also serves as the parity fixture for the JS mirror
# ---------------------------------------------------------------------------

def _parity_fixtures():
    """Hand-checked boards. `web` should reproduce these six numbers exactly."""
    fixtures = []

    # 1. Empty board.
    #    row_transitions: every row is wall,0*10,wall -> 2 per row -> 40
    #    column_transitions: every column empty -> empty->floor -> 1 per col -> 10
    #    holes 0, wells 0 (an empty cell needs FILLED neighbours), agg 0, bump 0
    b = np.zeros((BOARD_H, BOARD_W), dtype=np.uint8)
    fixtures.append(("empty", b, (40, 10, 0, 0, 0, 0)))

    # 2. Bottom row completely filled.
    #    rows 0..18 empty -> 2 each -> 38 ; row 19 all filled -> 0 -> total 38
    #    each column: ceiling(empty)->...->row19 filled : empty->filled at r19
    #      then filled->floor(filled) no transition -> 1 per column -> 10
    #    holes 0 ; wells 0 ; heights all 1 -> agg 10 ; bump 0
    b = np.zeros((BOARD_H, BOARD_W), dtype=np.uint8)
    b[19, :] = 1
    fixtures.append(("floor_row", b, (38, 10, 0, 0, 10, 0)))

    # 3. Bottom row filled except the last column -> a 1-deep well at col 9.
    #    row 19: W 1 1 1 1 1 1 1 1 1 0 W -> transitions (1,0) and (0,W=1) -> 2
    #      other 19 rows -> 2 each -> 38 ; total 40
    #    cols 0..8: 1 transition each = 9 ; col 9 empty -> 1 ; total 10
    #    holes 0
    #    wells: (19,9) empty, left (19,8) filled, right = wall filled -> depth 1 -> 1
    #    heights: 1 x9 then 0 -> agg 9 ; bump = |1-1|*8 + |1-0| = 1
    b = np.zeros((BOARD_H, BOARD_W), dtype=np.uint8)
    b[19, :9] = 1
    fixtures.append(("notch_right", b, (40, 10, 0, 1, 9, 1)))

    # 4. Overhang creating holes: col 0 filled at rows 17,18,19 is solid;
    #    put a cell at row 15 col 5 with nothing under it -> 4 holes under it
    #    (rows 16,17,18,19 of column 5).
    b = np.zeros((BOARD_H, BOARD_W), dtype=np.uint8)
    b[15, 5] = 1
    #    row 15: W 0 0 0 0 0 1 0 0 0 0 W -> W->0 (1), 0->1 (1), 1->0 (1), 0->W (1) = 4
    #      other 19 rows -> 2 each -> 38 ; total 42
    #    col 5: ceiling(0)->...->r15 filled (1), r15->r16 empty (1), empty->floor (1) = 3
    #      other 9 cols empty -> 1 each -> 9 ; total 12
    #    holes: rows 16..19 of col 5 -> 4
    #    wells: col 5 is the only filled cell; is (15,4) a well? empty, left (15,3)
    #      empty -> no. No well cells at all -> 0
    #    heights: col5 = 20-15 = 5, others 0 -> agg 5
    #    bump: |0-0|*4 + |0-5| + |5-0| + |0-0|*3 = 10
    fixtures.append(("floating_cell", b, (42, 12, 4, 0, 5, 10)))

    # 5. Deep 3-cell well at column 9 (triangular weighting check).
    b = np.zeros((BOARD_H, BOARD_W), dtype=np.uint8)
    b[17:20, :9] = 1
    #    wells: (17,9),(18,9),(19,9) each empty with col8 filled + right wall
    #      -> one run depth 3 -> 1+2+3 = 6
    #    rows 17,18,19: W 1*9 0 W -> 2 each -> 6 ; rows 0..16 -> 2 each -> 34
    #      total 40
    #    cols 0..8: 1 each -> 9 ; col 9 empty -> 1 ; total 10
    #    holes 0 ; heights 3 x9, 0 -> agg 27 ; bump = 3
    fixtures.append(("deep_well", b, (40, 10, 0, 6, 27, 3)))

    return fixtures


def _wells_fixtures():
    """Hand-checked boards for the two wells10 features.

    Kept separate from `_parity_fixtures` so the 6-tuple contract that
    `fastsim` and `parity_fastsim` consume does not change arity.

    Expected values are (max_well_depth, well_count). Remember the well test is
    IDENTICAL to `cumulative_wells`: empty cell, both horizontal neighbours
    filled, walls count as filled. Only the aggregation differs.
    """
    F = []

    # Reuse the six shared boards so web can check both feature families on the
    # same inputs.
    b = np.zeros((BOARD_H, BOARD_W), dtype=np.uint8)
    F.append(("empty", b.copy(), (0, 0)))          # no filled neighbours anywhere

    b = np.zeros((BOARD_H, BOARD_W), dtype=np.uint8); b[19, :] = 1
    F.append(("floor_row", b.copy(), (0, 0)))      # no empty cell has filled sides

    b = np.zeros((BOARD_H, BOARD_W), dtype=np.uint8); b[19, :9] = 1
    #   (19,9) empty, left filled, right = wall -> one well of depth 1.
    #   depth 1 < 3, so well_count is 0. THIS IS THE THRESHOLD TEST.
    F.append(("notch_right", b.copy(), (1, 0)))

    b = np.zeros((BOARD_H, BOARD_W), dtype=np.uint8); b[15, 5] = 1
    F.append(("floating_cell", b.copy(), (0, 0)))  # single cell, no wells

    b = np.zeros((BOARD_H, BOARD_W), dtype=np.uint8); b[17:20, :9] = 1
    #   col 9 empty at rows 17,18,19 between col 8 and the right wall -> depth 3.
    #   depth 3 >= 3 -> counted.
    F.append(("deep_well", b.copy(), (3, 1)))

    # Two separate deep wells: col 0 (left wall + col 1) and col 9 (col 8 + wall).
    b = np.zeros((BOARD_H, BOARD_W), dtype=np.uint8)
    b[16:20, 1:9] = 1
    F.append(("two_deep_wells", b.copy(), (4, 2)))

    # Depth 2 only -- just under the threshold. max_well_depth reports 2,
    # well_count reports 0. Separates the two features.
    b = np.zeros((BOARD_H, BOARD_W), dtype=np.uint8)
    b[18:20, 1:9] = 1
    F.append(("two_shallow_wells", b.copy(), (2, 0)))

    # One well broken in half by a filled cell: depths 2 and 1, not 4.
    # Checks that runs are split by occupancy, not merged per column.
    b = np.zeros((BOARD_H, BOARD_W), dtype=np.uint8)
    b[16:20, :9] = 1
    b[18, 9] = 1
    F.append(("split_well", b.copy(), (2, 0)))

    return F


def _self_test():
    ok = True
    for name, board, expected in _parity_fixtures():
        got = board_features(board)
        slow = (row_transitions(board), column_transitions(board), holes(board),
                cumulative_wells(board), aggregate_height(board), bumpiness(board))
        if got != slow:
            print(f"  FAIL {name}: fast {got} != slow {slow}")
            ok = False
        elif got != expected:
            print(f"  FAIL {name}: got {got}, expected {expected}")
            ok = False
        else:
            print(f"  ok   {name}: {got}")
    for name, board, expected in _wells_fixtures():
        got = (max_well_depth(board), well_count(board))
        if got != expected:
            print(f"  FAIL wells {name}: got {got}, expected {expected}")
            ok = False
        else:
            print(f"  ok   wells {name}: max_depth={got[0]} count={got[1]}")

    # Random cross-check fast vs slow.
    rng = np.random.default_rng(0)
    for _ in range(500):
        b = (rng.random((BOARD_H, BOARD_W)) < 0.35).astype(np.uint8)
        if board_features(b) != (row_transitions(b), column_transitions(b),
                                 holes(b), cumulative_wells(b),
                                 aggregate_height(b), bumpiness(b)):
            print("  FAIL random cross-check")
            ok = False
            break
    else:
        print("  ok   500 random boards: fast == slow")
    print("PASS" if ok else "FAILED")
    return ok


if __name__ == "__main__":
    import json
    _self_test()
    print("\nParity fixtures for web/policies.js:")
    print(json.dumps({
        name: {"board": board.tolist(),
               "expected": dict(zip(FEATURE_NAMES[2:], exp))}
        for name, board, exp in _parity_fixtures()
    }, indent=1)[:400] + " ...")
