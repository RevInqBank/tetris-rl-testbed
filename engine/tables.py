"""Tetris constant tables -- the single source of truth. NO LOGIC HERE.

Three implementations consume these tables and must never diverge:

  engine/engine.py   the reference engine (imports this module)
  rl/fastsim.py      rl's fast rollout simulator (imports this module)
  web/tables.js      generated from this file -- DO NOT HAND-EDIT

Regenerate the JS mirror after any change here:

    python3 engine/gen_tables_js.py

Rules for editing this file:
  * constants only -- no functions, no classes, no computation on import
    beyond the small derivation block at the bottom, which exists so the
    derived tables cannot drift from the cell tables they come from
  * changing PIECE_NAMES order changes every 7-bag sequence ever generated
  * changing PIECE_CELLS invalidates all trained weights

Spec: docs/spec.md sections 1-4.
"""

# --------------------------------------------------------------------------
# board geometry (docs/spec.md section 1)
# --------------------------------------------------------------------------

W = 10                                  # columns
VISIBLE_ROWS = 20                       # rows drawn on screen
BUFFER_ROWS = 2                         # hidden spawn buffer above the board
ROWS = VISIBLE_ROWS + BUFFER_ROWS        # 22 -- total rows in `state.rows`
BOTTOM_ROW = ROWS - 1                    # 21 -- the floor
FULL_ROW = (1 << W) - 1                  # 0x3FF -- a completed row

# Origin is TOP-LEFT. x increases rightward (0..9), y increases DOWNWARD
# (0..21). Rows 0 and 1 are the hidden buffer; the visible board is y=2..21.
# In `state.rows`, bit x of rows[y] is the cell at column x, row y.

# --------------------------------------------------------------------------
# pieces (docs/spec.md section 2)
# --------------------------------------------------------------------------

#: Piece index -> name. THIS ORDER IS THE INITIAL 7-BAG ARRAY.
#: Reordering it changes the piece sequence for every seed.
PIECE_NAMES = ("I", "O", "T", "S", "Z", "J", "L")

I, O, T, S, Z, J, L = 0, 1, 2, 3, 4, 5, 6

#: Bounding box side length per piece. I is 4x4, O is 2x2, the rest 3x3.
BOX_SIZE = (4, 2, 3, 3, 3, 3, 3)

#: PIECE_CELLS[piece][rot] -> ((dx, dy) x4) in bounding-box local coords,
#: dy increasing downward. These are the SRS tables with the y axis flipped.
#: Board coords are (x + dx, y + dy) where (x, y) is the box's top-left.
PIECE_CELLS = (
    # I (4x4 box)
    (((0, 1), (1, 1), (2, 1), (3, 1)),
     ((2, 0), (2, 1), (2, 2), (2, 3)),
     ((0, 2), (1, 2), (2, 2), (3, 2)),
     ((1, 0), (1, 1), (1, 2), (1, 3))),
    # O (2x2 box, rotation-invariant)
    (((0, 0), (1, 0), (0, 1), (1, 1)),
     ((0, 0), (1, 0), (0, 1), (1, 1)),
     ((0, 0), (1, 0), (0, 1), (1, 1)),
     ((0, 0), (1, 0), (0, 1), (1, 1))),
    # T (3x3 box)
    (((1, 0), (0, 1), (1, 1), (2, 1)),
     ((1, 0), (1, 1), (2, 1), (1, 2)),
     ((0, 1), (1, 1), (2, 1), (1, 2)),
     ((1, 0), (0, 1), (1, 1), (1, 2))),
    # S
    (((1, 0), (2, 0), (0, 1), (1, 1)),
     ((1, 0), (1, 1), (2, 1), (2, 2)),
     ((1, 1), (2, 1), (0, 2), (1, 2)),
     ((0, 0), (0, 1), (1, 1), (1, 2))),
    # Z
    (((0, 0), (1, 0), (1, 1), (2, 1)),
     ((2, 0), (1, 1), (2, 1), (1, 2)),
     ((0, 1), (1, 1), (1, 2), (2, 2)),
     ((1, 0), (0, 1), (1, 1), (0, 2))),
    # J
    (((0, 0), (0, 1), (1, 1), (2, 1)),
     ((1, 0), (2, 0), (1, 1), (1, 2)),
     ((0, 1), (1, 1), (2, 1), (2, 2)),
     ((1, 0), (1, 1), (0, 2), (1, 2))),
    # L
    (((2, 0), (0, 1), (1, 1), (2, 1)),
     ((1, 0), (1, 1), (1, 2), (2, 2)),
     ((0, 1), (1, 1), (2, 1), (0, 2)),
     ((0, 0), (1, 0), (1, 1), (1, 2))),
)

#: Rotations giving distinct hard-drop landings. r2 duplicates r0 and r3
#: duplicates r1 for the symmetric pieces, so agents must skip them.
UNIQUE_ROTS = (
    (0, 1),           # I
    (0,),             # O
    (0, 1, 2, 3),     # T
    (0, 1),           # S
    (0, 1),           # Z
    (0, 1, 2, 3),     # J
    (0, 1, 2, 3),     # L
)

# --------------------------------------------------------------------------
# spawn (docs/spec.md section 3)
# --------------------------------------------------------------------------

SPAWN_Y = 0
#: Box top-left column at spawn. O spawns at 4, everything else at 3, which
#: puts 3-wide pieces in columns 3-5, I in 3-6 and O in 4-5.
SPAWN_X = (3, 4, 3, 3, 3, 3, 3)
SPAWN_ROT = 0

# --------------------------------------------------------------------------
# scoring and gravity (docs/spec.md section 5)
# --------------------------------------------------------------------------

#: Base score by lines cleared, multiplied by the level *before* the move.
SCORE_TABLE = (0, 100, 300, 500, 800)
SOFT_DROP_POINTS_PER_CELL = 1
HARD_DROP_POINTS_PER_CELL = 2
LINES_PER_LEVEL = 10

#: Gravity for levels 1..10; see GRAVITY_TAIL for 11+. Frames at 60 fps.
GRAVITY_L1_L10 = (48, 43, 38, 33, 28, 23, 18, 13, 8, 6)
#: (max_level, frames_per_cell) for levels above 10, in ascending order.
GRAVITY_TAIL = ((13, 5), (16, 4), (19, 3), (28, 2))
GRAVITY_MIN = 1

# --- back-to-back and combo (docs/spec.md section 5) ----------------------
#
# Added so that a policy chasing tetrises outscores one clearing single rows
# steadily. Both paths (human and agent) use these identically.

#: A "difficult" clear is a tetris. T-spin is deliberately NOT difficult here:
#: agents place by hard drop and can never produce one, so counting it would
#: make the human and the agent play different games.
B2B_LINES = 4

#: Back-to-back multiplier, applied as an exact integer ratio so Python and JS
#: cannot diverge on a float. Every SCORE_TABLE entry is even, so
#: base * B2B_MULT_NUM // B2B_MULT_DEN is exact.
B2B_MULT_NUM = 3
B2B_MULT_DEN = 2

#: Combo bonus is COMBO_BONUS_PER_STEP * (chain length before this clear) *
#: level. The first clear of a chain therefore scores no combo bonus.
COMBO_BONUS_PER_STEP = 50

#: Lock delay -- HUMAN PLAY ONLY. The agent path enumerates hard-drop
#: placements, so lock delay has no meaning there and does not affect
#: legal_placements. See docs/spec.md section 5.
LOCK_DELAY_MS = 500
LOCK_RESET_LIMIT = 15

# --------------------------------------------------------------------------
# difficulty (docs/spec.md section 14)
#
# Difficulty restricts INFORMATION, never the board rules and never the piece
# sequence. The same seed yields the same pieces in every mode -- the player
# (or agent) is simply shown fewer of them. That is what makes the modes
# comparable: any score difference is attributable to lookahead alone.
# --------------------------------------------------------------------------

DIFFICULTY_NORMAL = 0
DIFFICULTY_HARD = 1
DIFFICULTY_EXTREME = 2

DIFFICULTY_NAMES = ("normal", "hard", "extreme")

#: How many upcoming pieces each mode reveals.
DIFFICULTY_NEXT_VISIBLE = (5, 1, 0)

#: Whether the hold slot exists at all.
DIFFICULTY_HOLD_ENABLED = (True, False, False)

#: Default. Must stay NORMAL: every recorded parity trace and every trained
#: weight assumes it, and the mode is deliberately not part of state_hash so
#: that normal-mode hashes are bit-identical to the pre-difficulty engine.
DIFFICULTY_DEFAULT = DIFFICULTY_NORMAL

# --------------------------------------------------------------------------
# wall kicks (docs/spec.md section 4) -- HUMAN PLAY ONLY
#
# (dx, dy) with dy increasing downward, i.e. the SRS table with y negated.
# Tried in order; the first that fits wins, and if all five fail the
# rotation does not happen.
# --------------------------------------------------------------------------

KICKS_JLSTZ = {
    (0, 1): ((0, 0), (-1, 0), (-1, -1), (0, 2), (-1, 2)),
    (1, 0): ((0, 0), (1, 0), (1, 1), (0, -2), (1, -2)),
    (1, 2): ((0, 0), (1, 0), (1, 1), (0, -2), (1, -2)),
    (2, 1): ((0, 0), (-1, 0), (-1, -1), (0, 2), (-1, 2)),
    (2, 3): ((0, 0), (1, 0), (1, -1), (0, 2), (1, 2)),
    (3, 2): ((0, 0), (-1, 0), (-1, 1), (0, -2), (-1, -2)),
    (3, 0): ((0, 0), (-1, 0), (-1, 1), (0, -2), (-1, -2)),
    (0, 3): ((0, 0), (1, 0), (1, -1), (0, 2), (1, 2)),
}

KICKS_I = {
    (0, 1): ((0, 0), (-2, 0), (1, 0), (-2, 1), (1, -2)),
    (1, 0): ((0, 0), (2, 0), (-1, 0), (2, -1), (-1, 2)),
    (1, 2): ((0, 0), (-1, 0), (2, 0), (-1, -2), (2, 1)),
    (2, 1): ((0, 0), (1, 0), (-2, 0), (1, 2), (-2, -1)),
    (2, 3): ((0, 0), (2, 0), (-1, 0), (2, -1), (-1, 2)),
    (3, 2): ((0, 0), (-2, 0), (1, 0), (-2, 1), (1, -2)),
    (3, 0): ((0, 0), (1, 0), (-2, 0), (1, 2), (-2, -1)),
    (0, 3): ((0, 0), (-1, 0), (2, 0), (-1, -2), (2, 1)),
}

#: O keeps its shape, so rotating it never needs a kick.
KICKS_NONE = ((0, 0),)

#: KICKS[piece] -> the kick table for that piece.
KICKS = (KICKS_I, None, KICKS_JLSTZ, KICKS_JLSTZ, KICKS_JLSTZ,
         KICKS_JLSTZ, KICKS_JLSTZ)

# --------------------------------------------------------------------------
# derived tables
#
# Computed from PIECE_CELLS rather than typed out, so they cannot drift from
# the cell tables. Consumers should read these instead of re-deriving them.
# --------------------------------------------------------------------------

def _derive():
    min_dx, max_dx, min_dy, max_dy, profile = [], [], [], [], []
    for piece in range(7):
        pmin_dx, pmax_dx, pmin_dy, pmax_dy, pprof = [], [], [], [], []
        for rot in range(4):
            cells = PIECE_CELLS[piece][rot]
            pmin_dx.append(min(c[0] for c in cells))
            pmax_dx.append(max(c[0] for c in cells))
            pmin_dy.append(min(c[1] for c in cells))
            pmax_dy.append(max(c[1] for c in cells))
            bottom = {}
            for dx, dy in cells:
                if dy > bottom.get(dx, -1):
                    bottom[dx] = dy
            pprof.append(tuple(sorted(bottom.items())))
        min_dx.append(tuple(pmin_dx))
        max_dx.append(tuple(pmax_dx))
        min_dy.append(tuple(pmin_dy))
        max_dy.append(tuple(pmax_dy))
        profile.append(tuple(pprof))
    return (tuple(min_dx), tuple(max_dx), tuple(min_dy), tuple(max_dy),
            tuple(profile))


#: MIN_DX[piece][rot] -- leftmost occupied column offset inside the box.
#: Absolute leftmost occupied column of a placement = placement.x + MIN_DX.
MIN_DX, MAX_DX, MIN_DY, MAX_DY, BOTTOM_PROFILE = _derive()

#: BOTTOM_PROFILE[piece][rot] -> ((dx, bottom_dy), ...) -- the lowest cell in
#: each occupied column. Hard-drop landing row is
#:     y_rest = min over dx of (column_top[x + dx] - bottom_dy - 1)
#: which is exact because every SRS piece is vertically contiguous per column.

#: X_RANGE[piece][rot] -> (x_min, x_max_exclusive) box origins keeping all
#: cells on the board.
X_RANGE = tuple(
    tuple((-MIN_DX[p][r], W - MAX_DX[p][r]) for r in range(4))
    for p in range(7)
)

#: Number of distinct hard-drop columns per (piece, rot).
PLACEMENT_COUNT = tuple(
    tuple(X_RANGE[p][r][1] - X_RANGE[p][r][0] for r in range(4))
    for p in range(7)
)

#: Tallest vertical extent of any piece in any rotation, in cells (4, from I
#: r1). engine.py's game-over fast path derives its guard window from this, so
#: adding a taller piece automatically widens the guard instead of silently
#: invalidating it. See the _is_stuck docstring for why that matters.
MAX_PIECE_VEXTENT = max(
    max(c[1] for c in PIECE_CELLS[p][r]) - min(c[1] for c in PIECE_CELLS[p][r]) + 1
    for p in range(7) for r in range(4)
)

#: Rows from the top that must be empty for "some placement always fits" to
#: hold. Derivation, with top[c] >= GUARD_ROWS for every column c:
#:     y_rest      = min_c(top[c] - bottom_dy_c - 1) >= GUARD_ROWS - max_dy - 1
#:     y_rest+min_dy >= GUARD_ROWS - max_dy + min_dy - 1
#:                   >= GUARD_ROWS - MAX_PIECE_VEXTENT      (since max_dy-min_dy+1 <= VEXTENT)
#: which is >= 0 exactly when GUARD_ROWS >= MAX_PIECE_VEXTENT.
#: checker measured the slack as exactly 0 at MAX_PIECE_VEXTENT == 4, so this
#: must stay derived -- a hardcoded 4 breaks silently if a piece grows.
GUARD_ROWS = MAX_PIECE_VEXTENT

# --------------------------------------------------------------------------
# hashing (docs/spec.md section 7)
# --------------------------------------------------------------------------

FNV_OFFSET_32 = 2166136261
FNV_PRIME_32 = 16777619
MASK32 = 0xFFFFFFFF

# --------------------------------------------------------------------------
# rng (docs/spec.md section 6) -- the generator itself lives in rng.py
# --------------------------------------------------------------------------

XORSHIFT_FALLBACK_STATE = 0x9E3779B9
BAG_SIZE = 7
#: Queue is topped up whenever it drops below this, so `next` display always
#: has at least 5 entries available.
QUEUE_MIN = 7
NEXT_VISIBLE = 5
