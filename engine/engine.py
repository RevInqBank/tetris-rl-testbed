"""Headless Tetris core -- single rule set, two entry points.

Implements docs/spec.md v1 exactly. `web/engine.js` is the bit-identical
JS mirror; `engine/parity.py` proves they agree.

Two entry points over one core:

  agent path (used by rl/)      pure, returns new states
      legal_placements(state) -> [(rot, x, y_rest, piece), ...]
      apply_placement(state, p) -> (next_state, info)

  human path (used by web/)     mutates the state in place
      move / rotate / soft_drop / hard_drop / tick / hold

Board is a tuple of 22 ints; bit ``x`` of ``rows[y]`` is the cell at
column ``x``, row ``y``. Row 0 is the top; rows 0 and 1 are the hidden
spawn buffer. Bitmask rows keep collision checks to one AND per row and
keep state copies allocation-cheap.

Pure Python, no imports beyond `rng`. numpy is deliberately unused -- the
hot loop works on tiny int tuples where numpy overhead dominates.
"""

from rng import next_bag, next_u32, seed_state

# --------------------------------------------------------------------------
# constants -- all tables live in engine/tables.py, the single source of
# truth shared with rl/fastsim.py and (via gen_tables_js.py) web/tables.js.
# Re-exported here so `engine.W`, `engine.PIECE_CELLS` etc. keep working.
# --------------------------------------------------------------------------

from tables import (  # noqa: F401
    W, VISIBLE_ROWS, BUFFER_ROWS, ROWS, BOTTOM_ROW, FULL_ROW,
    PIECE_NAMES, BOX_SIZE, PIECE_CELLS, UNIQUE_ROTS,
    SPAWN_X, SPAWN_Y, SPAWN_ROT,
    SCORE_TABLE, SOFT_DROP_POINTS_PER_CELL, HARD_DROP_POINTS_PER_CELL,
    LINES_PER_LEVEL, GRAVITY_L1_L10, GRAVITY_TAIL, GRAVITY_MIN,
    LOCK_DELAY_MS, LOCK_RESET_LIMIT,
    B2B_LINES, B2B_MULT_NUM, B2B_MULT_DEN, COMBO_BONUS_PER_STEP,
    DIFFICULTY_NORMAL, DIFFICULTY_HARD, DIFFICULTY_EXTREME, DIFFICULTY_NAMES,
    DIFFICULTY_NEXT_VISIBLE, DIFFICULTY_HOLD_ENABLED, DIFFICULTY_DEFAULT,
    KICKS, KICKS_I, KICKS_JLSTZ, KICKS_NONE,
    MIN_DX, MAX_DX, MIN_DY, MAX_DY, BOTTOM_PROFILE, X_RANGE,
    MAX_PIECE_VEXTENT, GUARD_ROWS,
    FNV_OFFSET_32, FNV_PRIME_32, MASK32,
    BAG_SIZE, QUEUE_MIN, NEXT_VISIBLE,
    I, O, T, S, Z, J, L,
)

EMPTY_ROWS = tuple([0] * ROWS)


def kick_offsets(piece, frm, to):
    """SRS kick candidates for a rotation, in test order. Human path only."""
    table = KICKS[piece]
    if table is None:
        return KICKS_NONE
    return table.get((frm, to), KICKS_NONE)


def frames_per_cell(level):
    """Frames (at 60 fps) for gravity to pull the piece down one cell."""
    lv = 1 if level <= 0 else level
    if lv <= 10:
        return GRAVITY_L1_L10[lv - 1]
    for max_level, fpc in GRAVITY_TAIL:
        if lv <= max_level:
            return fpc
    return GRAVITY_MIN


# --------------------------------------------------------------------------
# precomputed placement tables
#
# For every (piece, rot, x) we cache what the hot loop needs so that
# legal_placements/apply_placement do table lookups instead of geometry:
#   masks      : ((dy, rowmask), ...) already shifted to column x
#   bottom     : ((col, bottom_dy), ...) lowest cell per occupied column
#   min_dy/max_dy
# --------------------------------------------------------------------------

def _build_tables():
    placements = []      # placements[piece][rot] -> list of entries or None
    for piece in range(7):
        per_rot = []
        for rot in range(4):
            cells = PIECE_CELLS[piece][rot]
            # Geometry comes from tables.py -- never re-derive it here, or the
            # three implementations can drift.
            min_dy = MIN_DY[piece][rot]
            max_dy = MAX_DY[piece][rot]
            bottom = BOTTOM_PROFILE[piece][rot]
            x_lo, x_hi = X_RANGE[piece][rot]

            entries = []
            for x in range(x_lo, x_hi):
                masks_by_dy = {}
                for dx, dy in cells:
                    masks_by_dy[dy] = masks_by_dy.get(dy, 0) | (1 << (x + dx))
                masks = tuple(sorted(masks_by_dy.items()))
                shifted_bottom = tuple((x + dx, bdy) for dx, bdy in bottom)
                entries.append((rot, x, masks, shifted_bottom, min_dy, max_dy))
            per_rot.append(entries)
        placements.append(per_rot)
    return placements


#: _TABLE[piece][rot] -> list of (rot, x, masks, bottom, min_dy, max_dy)
_TABLE = _build_tables()

#: Spawn cell masks, precomputed for the spawn-collision check.
_SPAWN_MASKS = tuple(
    next(e[2] for e in _TABLE[p][0] if e[1] == SPAWN_X[p]) for p in range(7)
)


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------

class State:
    """Game state. ``rows`` and ``queue`` are tuples, so a copy is shallow.

    The agent path never mutates a State; the human path always does.
    """

    #: lock_ms / lock_resets / grav_ms drive the interactive path only. The
    #: agent path never reads them (placements are hard drops), so they do not
    #: enter legal_placements and are excluded from state_hash.
    __slots__ = ("rows", "current", "rot", "x", "y", "queue", "hold",
                 "can_hold", "rng", "score", "lines", "level", "pieces",
                 "game_over", "lock_ms", "lock_resets", "grav_ms",
                 "lowest_y", "touched_down", "b2b", "combo", "difficulty")

    def __init__(self):
        self.rows = EMPTY_ROWS
        self.current = None
        self.rot = 0
        self.x = 0
        self.y = 0
        self.queue = ()
        self.hold = None
        self.can_hold = True
        self.rng = seed_state(1)
        self.score = 0
        self.lines = 0
        self.level = 1
        self.pieces = 0
        self.game_over = False
        self.lock_ms = 0.0
        self.lock_resets = 0
        self.grav_ms = 0.0
        #: Deepest row this piece has reached. Only descending PAST it resets
        #: the lock timer -- see tick_ms.
        self.lowest_y = SPAWN_Y
        #: True once the piece has rested on the stack at least once. From then
        #: on the lock timer runs every frame, even if a kick lifts the piece.
        self.touched_down = False
        #: Length of the current back-to-back tetris chain; 0 = not in a chain.
        self.b2b = 0
        #: Consecutive line-clearing pieces so far; 0 = no combo running.
        self.combo = 0
        #: Information-restriction mode. Never affects board rules or the piece
        #: sequence -- see docs/spec.md section 14.
        self.difficulty = DIFFICULTY_DEFAULT

    def clone(self):
        s = State.__new__(State)
        s.rows = self.rows
        s.current = self.current
        s.rot = self.rot
        s.x = self.x
        s.y = self.y
        s.queue = self.queue
        s.hold = self.hold
        s.can_hold = self.can_hold
        s.rng = self.rng
        s.score = self.score
        s.lines = self.lines
        s.level = self.level
        s.pieces = self.pieces
        s.game_over = self.game_over
        s.lock_ms = self.lock_ms
        s.lock_resets = self.lock_resets
        s.grav_ms = self.grav_ms
        s.lowest_y = self.lowest_y
        s.touched_down = self.touched_down
        s.b2b = self.b2b
        s.combo = self.combo
        s.difficulty = self.difficulty
        return s

    def to_dict(self) -> dict:
        return {
            "rows": list(self.rows),
            "current": self.current,
            "rot": self.rot,
            "x": self.x,
            "y": self.y,
            "queue": list(self.queue),
            "hold": self.hold,
            "can_hold": self.can_hold,
            "rng": self.rng,
            "score": self.score,
            "lines": self.lines,
            "level": self.level,
            "pieces": self.pieces,
            "game_over": self.game_over,
            "lock_ms": self.lock_ms,
            "lock_resets": self.lock_resets,
            "grav_ms": self.grav_ms,
            "lowest_y": self.lowest_y,
            "touched_down": self.touched_down,
            "b2b": self.b2b,
            "combo": self.combo,
            "difficulty": self.difficulty,
        }

    def __repr__(self):
        name = PIECE_NAMES[self.current] if self.current is not None else "-"
        return ("State(piece=%s rot=%d x=%d y=%d lines=%d score=%d%s)"
                % (name, self.rot, self.x, self.y, self.lines, self.score,
                   " GAME_OVER" if self.game_over else ""))

    def render(self, include_buffer: bool = False) -> str:
        """ASCII board, for debugging only. Does not draw the active piece."""
        start = 0 if include_buffer else BUFFER_ROWS
        out = []
        for y in range(start, ROWS):
            row = self.rows[y]
            out.append("|" + "".join("#" if (row >> x) & 1 else "."
                                     for x in range(W)) + "|")
        out.append("+" + "-" * W + "+")
        return "\n".join(out)


def from_dict(d: dict) -> State:
    s = State.__new__(State)
    s.rows = tuple(d["rows"])
    s.current = d["current"]
    s.rot = d["rot"]
    s.x = d["x"]
    s.y = d["y"]
    s.queue = tuple(d["queue"])
    s.hold = d["hold"]
    s.can_hold = bool(d["can_hold"])
    s.rng = int(d["rng"]) & 0xFFFFFFFF
    s.score = d["score"]
    s.lines = d["lines"]
    s.level = d["level"]
    s.pieces = d["pieces"]
    s.game_over = bool(d["game_over"])
    s.lock_ms = d.get("lock_ms", 0.0)
    s.lock_resets = d.get("lock_resets", 0)
    s.grav_ms = d.get("grav_ms", 0.0)
    s.lowest_y = d.get("lowest_y", SPAWN_Y)
    s.touched_down = bool(d.get("touched_down", False))
    s.b2b = d.get("b2b", 0)
    s.combo = d.get("combo", 0)
    s.difficulty = d.get("difficulty", DIFFICULTY_DEFAULT)
    return s


# --------------------------------------------------------------------------
# queue management
# --------------------------------------------------------------------------

def _refill(rng: int, queue: tuple) -> tuple:
    """Top the queue up past 7 so `next` display always has 5+ entries."""
    while len(queue) < 7:
        rng, bag = next_bag(rng)
        queue = queue + bag
    return rng, queue


class NextPeekBlocked(Exception):
    """Raised when code asks for upcoming pieces that the mode hides.

    Deliberately loud. Returning an empty list here would let a lookahead
    agent quietly degrade to zero-ply and still report a number, which is
    exactly the experiment this mode exists to run -- a silent empty value
    would make the result meaningless instead of visibly wrong.
    """


def next_visible_count(state: State) -> int:
    """How many upcoming pieces this state's mode reveals."""
    return DIFFICULTY_NEXT_VISIBLE[state.difficulty]


def hold_enabled(state: State) -> bool:
    """Whether the hold slot exists in this state's mode."""
    return DIFFICULTY_HOLD_ENABLED[state.difficulty]


def difficulty_name(state: State) -> str:
    return DIFFICULTY_NAMES[state.difficulty]


def visible_next(state: State) -> tuple:
    """The upcoming pieces this mode permits seeing. THE sanctioned accessor.

    ``state.queue`` is engine-internal bookkeeping: it must hold future pieces
    so the generator stays identical across modes. Reading it directly bypasses
    the difficulty setting. Anything outside the engine -- UI, features, search
    -- must come through here.

    Raises NextPeekBlocked in extreme mode rather than returning ().
    """
    n = next_visible_count(state)
    if n == 0:
        raise NextPeekBlocked(
            "difficulty %r reveals no upcoming pieces. A lookahead policy must "
            "detect this and fall back to zero-ply explicitly (catch "
            "NextPeekBlocked or check next_visible_count(state) first) -- it "
            "must not silently search an empty future."
            % DIFFICULTY_NAMES[state.difficulty])
    return tuple(state.queue[:n])


def new_game(seed: int = 1, difficulty: int = DIFFICULTY_DEFAULT) -> State:
    """Fresh game with a deterministic piece sequence.

    ``difficulty`` restricts information only. The same seed produces the same
    pieces in every mode -- see docs/spec.md section 14.
    """
    if difficulty not in (DIFFICULTY_NORMAL, DIFFICULTY_HARD, DIFFICULTY_EXTREME):
        raise ValueError("unknown difficulty %r" % (difficulty,))
    s = State()
    s.difficulty = difficulty
    s.rng = seed_state(seed)
    s.rng, s.queue = _refill(s.rng, ())
    _spawn_next(s)
    return s


def _spawn_next(state: State) -> bool:
    """Pop the queue into the active piece. Returns False on spawn collision."""
    rng, queue = _refill(state.rng, state.queue)
    piece = queue[0]
    state.queue = queue[1:]
    state.rng = rng
    state.current = piece
    state.rot = 0
    state.x = SPAWN_X[piece]
    state.y = SPAWN_Y
    state.can_hold = True
    state.lock_ms = 0.0
    state.lock_resets = 0
    state.grav_ms = 0.0
    state.lowest_y = SPAWN_Y
    state.touched_down = False

    rows = state.rows
    for dy, mask in _SPAWN_MASKS[piece]:
        if rows[dy] & mask:
            state.game_over = True
            return False
    return True


# --------------------------------------------------------------------------
# collision / heights
# --------------------------------------------------------------------------

def _fits(rows, piece: int, rot: int, x: int, y: int) -> bool:
    """True if the piece at (rot, x, y) is inside the board and unobstructed."""
    for dx, dy in PIECE_CELLS[piece][rot]:
        cx = x + dx
        cy = y + dy
        if cx < 0 or cx >= W or cy < 0 or cy >= ROWS:
            return False
        if (rows[cy] >> cx) & 1:
            return False
    return True


def column_tops(rows) -> list:
    """Topmost FILLED ROW INDEX per column; ``ROWS`` (22) for an empty column.

    This is the ``top[]`` of docs/spec.md section 5, NOT a height. Row indices
    grow downward, so a taller stack gives a SMALLER number:

        rows[21] full, rows[20] has column 0 only
        column_tops(rows) -> [20, 21, 21, 21, 21, 21, 21, 21, 21, 21]
        heights would be   -> [ 2,  1,  1,  1,  1,  1,  1,  1,  1,  1]

    To get a height:  ``ROWS - column_tops(rows)[c]``  (0 for an empty column
    would be ``ROWS - ROWS``, which is 0 -- that works out).

    Named ``column_heights`` until web caught the mismatch: rl/features.py has
    a function of that name returning true heights, so the two would have been
    silently interchangeable and wrong. The old name now raises.

    Walks rows top-down and drops each column out of the pending mask once it
    is resolved, so the loop exits as soon as every column is known.
    """
    top = [ROWS] * W
    pending = FULL_ROW
    for y in range(ROWS):
        hit = rows[y] & pending
        if hit:
            pending &= ~hit
            x = 0
            while hit:
                if hit & 1:
                    top[x] = y
                hit >>= 1
                x += 1
            if not pending:
                break
    return top


# --------------------------------------------------------------------------
# agent path
# --------------------------------------------------------------------------

def column_heights(rows):
    """Removed -- this name returned row indices, not heights. See column_tops.

    Kept as a loud failure on purpose. rl/features.py defines `column_heights`
    returning TRUE HEIGHTS (empty column = 0); this one returned the topmost
    filled ROW INDEX (empty column = 22). Same name, inverted meaning, and
    swapping them produces plausible-looking wrong features with no error. A
    silent alias would have preserved exactly that trap.
    """
    raise AttributeError(
        "engine.column_heights was renamed to engine.column_tops because it "
        "returns the topmost FILLED ROW INDEX (empty column = ROWS = 22), not "
        "a height. Row indices grow downward. For a height use "
        "ROWS - column_tops(rows)[c]. Do not confuse this with "
        "rl/features.py:column_heights, which returns true heights.")


def legal_placements(state: State) -> list:
    """Hard-drop-reachable placements as ``(rot, x, y_rest, piece)`` tuples.

    Sorted by rot then x -- docs/spec.md section 8 fixes this order because
    the parity test always takes the first element.

    Only vertical drops are considered: no tucks, no spins under an
    overhang. Duplicate rotations are removed (O: 1, I/S/Z: 2, T/J/L: 4).
    """
    if state.game_over or state.current is None:
        return []

    piece = state.current
    rows = state.rows
    top = column_tops(rows)
    out = []
    tbl = _TABLE[piece]

    for rot in UNIQUE_ROTS[piece]:
        for rot_i, x, masks, bottom, min_dy, max_dy in tbl[rot]:
            y_rest = ROWS
            for col, bdy in bottom:
                cand = top[col] - bdy - 1
                if cand < y_rest:
                    y_rest = cand
            if y_rest + min_dy < 0:
                continue          # piece would stick out above the board
            out.append((rot_i, x, y_rest, piece))
    return out


def _score_clear(n: int, level: int, b2b: int, combo: int) -> tuple:
    """Score one lock. Returns ``(score_delta, b2b_next, combo_next, b2b_applied)``.

    docs/spec.md section 5. Kept as one small pure function because both entry
    points and both languages must agree on it exactly, and because it is the
    piece of the engine most likely to be argued about later.

    * A move clearing no lines resets the combo but LEAVES the back-to-back
      chain alone -- placing a piece without clearing does not break B2B.
    * A tetris continues the chain and, if a chain was already running, scores
      the x1.5 bonus. A 1-3 line clear breaks the chain.
    * Combo bonus uses the chain length BEFORE this clear, so the first clear
      of a run scores no combo bonus.

    All arithmetic is integer. Every SCORE_TABLE entry is even, so the x1.5 is
    exact and cannot drift from the JS mirror through float rounding.
    """
    if n == 0:
        return 0, b2b, 0, False

    base = SCORE_TABLE[n]
    if n == B2B_LINES:
        b2b_applied = b2b > 0
        b2b_next = b2b + 1
    else:
        b2b_applied = False
        b2b_next = 0
    if b2b_applied:
        base = base * B2B_MULT_NUM // B2B_MULT_DEN

    combo_bonus = COMBO_BONUS_PER_STEP * combo * level
    return base * level + combo_bonus, b2b_next, combo + 1, b2b_applied


def apply_placement(state: State, p) -> tuple:
    """Lock the piece at ``p``, clear lines, spawn the next piece.

    ``state`` is not modified. Returns ``(next_state, info)``; the info
    fields are the rl-facing contract in docs/spec.md section 8.
    """
    rot, x, y_rest, piece = p

    entry = None
    for e in _TABLE[piece][rot]:
        if e[1] == x:
            entry = e
            break
    if entry is None:
        raise ValueError("illegal placement column: %r" % (p,))
    _, _, masks, _, min_dy, max_dy = entry

    rows = list(state.rows)
    touched = []
    for dy, mask in masks:
        y = y_rest + dy
        rows[y] |= mask
        touched.append((y, mask))

    # Absolute (y, x) of the four cells, before any clearing. rl reconstructs
    # every landing-geometry feature from this, so the engine does not need to
    # know any feature definition.
    piece_cells = [(y_rest + dy, x + dx)
                   for dx, dy in PIECE_CELLS[piece][rot]]

    # Only rows the piece touched can have become full.
    cleared_rows = [y for y, _ in touched if rows[y] == FULL_ROW]
    n = len(cleared_rows)

    eroded_cells = 0
    if n:
        cleared_rows.sort()
        cleared_set = set(cleared_rows)
        for y, mask in touched:
            if y in cleared_set:
                eroded_cells += bin(mask).count("1")
        kept = [r for i, r in enumerate(rows) if i not in cleared_set]
        rows = [0] * n + kept

    level_before = state.level
    score_delta, b2b_next, combo_next, b2b_applied = _score_clear(
        n, level_before, state.b2b, state.combo)

    nxt = state.clone()
    nxt.rows = tuple(rows)
    nxt.lines = state.lines + n
    nxt.level = 1 + nxt.lines // LINES_PER_LEVEL
    nxt.pieces = state.pieces + 1
    nxt.score = state.score + score_delta
    nxt.b2b = b2b_next
    nxt.combo = combo_next
    nxt.can_hold = True

    spawned = _spawn_next(nxt)

    # Raw geometry only. Feature definitions (landing_height, holes, ...) are
    # owned by rl/features.py -- see docs/spec.md section 8. Writing a feature
    # formula here too would guarantee the two copies drift apart.
    info = {
        "lines_cleared": n,
        "cleared_rows": cleared_rows,
        "game_over": not spawned,
        "piece_cells": piece_cells,
        "cleared_piece_cells": eroded_cells,
        "eroded_piece_cells": n * eroded_cells,
        "landing_row_top": y_rest + min_dy,
        "landing_row_bottom": y_rest + max_dy,
        "score_delta": score_delta,
        "is_tetris": n == B2B_LINES,
        "b2b_active": b2b_applied,
        "b2b_chain": b2b_next,
        "combo_count": combo_next,
        "level": level_before,
        "total_lines": nxt.lines,
        "piece": piece,
        "rot": rot,
        "x": x,
        "y": y_rest,
    }
    if spawned and _is_stuck(nxt):
        nxt.game_over = True
        info["game_over"] = True
    return nxt, info


def _is_stuck(state: State) -> bool:
    """True when no hard-drop placement fits -- i.e. game over.

    A cheap guard runs first: if the top ``GUARD_ROWS`` rows are all empty,
    some placement is guaranteed to fit and the full scan is skipped. This is
    what keeps the game-over check off the hot path, and it is worth roughly a
    third of the measured throughput.

    ===================================================================
    THIS GUARD IS ONLY VALID WHILE ``GUARD_ROWS >= MAX_PIECE_VEXTENT``.
    Both are derived in tables.py; the safety slack is EXACTLY ZERO at
    the current values (BUFFER_ROWS == 2, tallest piece extent == 4,
    from I in rotation 1) -- measured by checker, not estimated.

    If you add a piece taller than 4 cells, or change BUFFER_ROWS, and
    the derivation in tables.py no longer holds: DELETE THIS GUARD and
    call ``legal_placements`` unconditionally. Do not "adjust" the
    window by hand.

    Getting this wrong does not crash. It makes the engine miss game
    overs on rare boards, which inflates line counts -- a silent,
    plausible-looking wrong answer. That is why the assert below is not
    optional and why tests/test_is_stuck_guard.py pins the invariant.
    ===================================================================
    """
    rows = state.rows
    for y in range(GUARD_ROWS):
        if rows[y]:
            return not legal_placements(state)
    return False


# Fail at import time, not at 3am on a rare board, if the guard's premise dies.
assert GUARD_ROWS >= MAX_PIECE_VEXTENT, (
    "_is_stuck guard is invalid: GUARD_ROWS=%d < MAX_PIECE_VEXTENT=%d. "
    "Delete the guard in _is_stuck and scan unconditionally."
    % (GUARD_ROWS, MAX_PIECE_VEXTENT))


# --------------------------------------------------------------------------
# human path -- mutates state in place
# --------------------------------------------------------------------------

def _touch_lock_timer(state: State) -> None:
    """Reset the lock-delay countdown after a successful move or rotation.

    Only while the piece is resting, and only up to LOCK_RESET_LIMIT times per
    piece -- that bound is what stops infinite spinning without taking tuck and
    slide away from the player. Once the budget is spent the timer keeps
    running and the piece locks on schedule.
    """
    if state.lock_resets >= LOCK_RESET_LIMIT:
        return
    if _fits(state.rows, state.current, state.rot, state.x, state.y + 1):
        return                       # airborne: no lock timer to reset
    state.lock_ms = 0.0
    state.lock_resets += 1


def _descend_reset(state: State) -> None:
    """Reset the lock timer only if the piece reached a NEW deepest row.

    This is the other half of the infinite-spin defence, and the half that was
    missing. See tick_ms for why a plain unconditional reset is wrong.
    """
    if state.y > state.lowest_y:
        state.lowest_y = state.y
        state.lock_ms = 0.0


def move(state: State, dx: int) -> bool:
    """Shift the active piece horizontally. Returns whether it moved."""
    if state.game_over or state.current is None:
        return False
    if _fits(state.rows, state.current, state.rot, state.x + dx, state.y):
        state.x += dx
        _touch_lock_timer(state)
        return True
    return False


def rotate(state: State, cw: bool = True) -> bool:
    """Rotate with SRS wall kicks. Returns whether the rotation happened."""
    if state.game_over or state.current is None:
        return False
    piece = state.current
    frm = state.rot
    to = (frm + 1) % 4 if cw else (frm + 3) % 4
    if piece == O:
        state.rot = to
        _touch_lock_timer(state)
        return True
    for kx, ky in kick_offsets(piece, frm, to):
        nx = state.x + kx
        ny = state.y + ky
        if _fits(state.rows, piece, to, nx, ny):
            state.rot = to
            state.x = nx
            state.y = ny
            _touch_lock_timer(state)
            return True
    return False


def soft_drop(state: State) -> bool:
    """Drop one cell, scoring 1. Returns False if already resting."""
    if state.game_over or state.current is None:
        return False
    if _fits(state.rows, state.current, state.rot, state.x, state.y + 1):
        state.y += 1
        state.score += SOFT_DROP_POINTS_PER_CELL
        _descend_reset(state)
        return True
    return False


def drop_distance(state: State) -> int:
    """Cells between the active piece and its hard-drop resting position."""
    d = 0
    rows, piece, rot, x, y = state.rows, state.current, state.rot, state.x, state.y
    while _fits(rows, piece, rot, x, y + d + 1):
        d += 1
    return d


def ghost_y(state: State) -> int:
    """Resting row of the active piece -- for the UI drop preview."""
    return state.y + drop_distance(state)


def hard_drop(state: State):
    """Slam down, score 2/cell, lock. Returns the same info as a placement."""
    if state.game_over or state.current is None:
        return None
    d = drop_distance(state)
    state.y += d
    state.score += HARD_DROP_POINTS_PER_CELL * d
    return lock(state)           # hard drop always locks immediately


def tick(state: State):
    """Apply one cell of gravity. Locks on landing and returns info, else None."""
    if state.game_over or state.current is None:
        return None
    if _fits(state.rows, state.current, state.rot, state.x, state.y + 1):
        state.y += 1
        return None
    return lock(state)


def lock(state: State):
    """Freeze the active piece where it stands and spawn the next one.

    Shares the clear/score/spawn logic with `apply_placement` by routing
    through it, so the two entry points cannot drift apart.
    """
    if state.game_over or state.current is None:
        return None
    p = (state.rot, state.x, state.y, state.current)
    nxt, info = apply_placement(state, p)
    # copy the fresh state back over the mutable one (human path semantics)
    state.rows = nxt.rows
    state.current = nxt.current
    state.rot = nxt.rot
    state.x = nxt.x
    state.y = nxt.y
    state.queue = nxt.queue
    state.hold = nxt.hold
    state.can_hold = nxt.can_hold
    state.rng = nxt.rng
    state.score = nxt.score          # already includes info["score_delta"]
    state.lines = nxt.lines
    state.level = nxt.level
    state.pieces = nxt.pieces
    state.game_over = nxt.game_over
    return info


def hold(state: State) -> bool:
    """Swap the active piece with the hold slot. Once per spawn.

    Returns False in modes without a hold slot. This is a refusal, not an
    error: the UI simply has no hold control in those modes.
    """
    if not hold_enabled(state):
        return False
    if state.game_over or state.current is None or not state.can_hold:
        return False
    cur = state.current
    if state.hold is None:
        state.hold = cur
        if not _spawn_next(state):
            return True
    else:
        swapped = state.hold
        state.hold = cur
        state.current = swapped
        state.rot = 0
        state.x = SPAWN_X[swapped]
        state.y = SPAWN_Y
        for dy, mask in _SPAWN_MASKS[swapped]:
            if state.rows[dy] & mask:
                state.game_over = True
                break
    state.can_hold = False
    return True


MS_PER_FRAME = 1000.0 / 60.0


def gravity_interval_ms(level: int) -> float:
    """Milliseconds per gravity step at this level."""
    return frames_per_cell(level) * MS_PER_FRAME


def tick_ms(state: State, dt_ms: float):
    """Advance the interactive game by ``dt_ms`` real milliseconds.

    This is the driver a UI should call once per animation frame; it folds
    gravity and lock delay together. Returns the lock ``info`` on the frame the
    piece locks, otherwise None.

    Lock delay is LOCK_DELAY_MS while the piece rests on the stack, so tuck and
    slide work. Moves and rotations restart the countdown up to
    LOCK_RESET_LIMIT times per piece; after that it expires on schedule.

    docs/spec.md section 5. This concept exists only on the interactive path --
    `legal_placements` enumerates hard drops and is unaffected.
    """
    if state.game_over or state.current is None:
        return None

    resting = not _fits(state.rows, state.current, state.rot,
                        state.x, state.y + 1)

    if not resting:
        # Gravity first, so downward progress this frame counts.
        state.grav_ms += dt_ms
        step = gravity_interval_ms(state.level)
        while state.grav_ms >= step:
            if not _fits(state.rows, state.current, state.rot,
                         state.x, state.y + 1):
                break
            state.y += 1
            state.grav_ms -= step
        _descend_reset(state)
        resting = not _fits(state.rows, state.current, state.rot,
                            state.x, state.y + 1)

    if resting:
        state.touched_down = True

    # The lock timer runs once the piece has touched down -- and it KEEPS
    # running even on frames where a kick has lifted the piece off the stack.
    #
    # Two bugs lived here, both found by web's 20,000-cycle rotate-spam:
    #   1. The airborne branch used to zero lock_ms unconditionally, which
    #      bypassed the LOCK_RESET_LIMIT budget (that budget lives in
    #      _touch_lock_timer and only applies while resting).
    #   2. Gating the timer on `resting` alone was still not enough: spamming
    #      rotation lifts the piece EVERY frame, so it was never resting and
    #      the timer never advanced at all.
    # Hence touched_down: once grounded, time always moves toward the lock.
    # Only real downward progress (_descend_reset) or a budgeted move/rotation
    # (_touch_lock_timer) forgives it.
    if not state.touched_down:
        return None

    state.lock_ms += dt_ms
    if state.lock_ms >= LOCK_DELAY_MS:
        # Expiry while a kick holds the piece aloft must not freeze a floating
        # block: settle it to its resting row first. No drop points -- this is
        # the clock running out, not a player hard drop.
        state.y += drop_distance(state)
        return lock(state)
    return None


def lock_delay_progress(state: State) -> float:
    """0..1 fraction of the lock delay elapsed. 0 while the piece is airborne."""
    if state.game_over or state.current is None:
        return 0.0
    if _fits(state.rows, state.current, state.rot, state.x, state.y + 1):
        return 0.0
    return min(1.0, state.lock_ms / float(LOCK_DELAY_MS))


# --------------------------------------------------------------------------
# coordinate helpers and conversions
# --------------------------------------------------------------------------

def placement_left_col(p) -> int:
    """Absolute leftmost occupied column of a placement.

    ``Placement.x`` is the bounding-box origin, which is what the cell tables
    multiply against. Consumers that want "the leftmost filled column" (rl's
    feature code, a UI that animates a piece into position) should call this
    rather than reimplementing the offset.
    """
    rot, x, _y, piece = p
    return x + MIN_DX[piece][rot]


def placement_cells(state_or_piece, p=None):
    """Absolute ``[(y, x) x4]`` a placement would occupy, before clearing.

    Accepts either ``placement_cells(p)`` or ``placement_cells(state, p)``;
    the piece index is carried in the placement either way.
    """
    if p is None:
        p = state_or_piece
    rot, x, y_rest, piece = p
    return [(y_rest + dy, x + dx) for dx, dy in PIECE_CELLS[piece][rot]]


def board_array(state: State, buffer: bool = False):
    """The board as a numpy ``uint8`` array, 0 empty / 1 filled, row 0 at top.

    Shape is (20, 10) by default -- the visible board, so row 0 is board y=2.
    ``buffer=True`` gives (22, 10) with row 0 at board y=0.

    For inspection, plotting and tests. Do NOT use this inside a rollout or
    feature loop: the bitmask rows are far faster, and this allocates. numpy is
    imported lazily so the engine keeps zero hard dependencies.
    """
    import numpy as np
    start = 0 if buffer else BUFFER_ROWS
    out = np.zeros((ROWS - start, W), dtype=np.uint8)
    for i, y in enumerate(range(start, ROWS)):
        row = state.rows[y]
        for x in range(W):
            if (row >> x) & 1:
                out[i, x] = 1
    return out


def rows_from_array(arr, buffer: bool = False) -> tuple:
    """Inverse of `board_array`: (rows, cols) 0/1 array -> 22-int row tuple."""
    n = len(arr)
    pad = 0 if buffer else ROWS - n
    rows = [0] * pad
    for r in range(n):
        v = 0
        for x in range(W):
            if arr[r][x]:
                v |= 1 << x
        rows.append(v)
    return tuple(rows)


# --------------------------------------------------------------------------
# batch rollout (requested by the lead so GPU training is not starved)
# --------------------------------------------------------------------------

def legal_batch(states) -> list:
    """`legal_placements` over N independent games. Finished games give []."""
    return [legal_placements(s) for s in states]


def step_batch(states, placements) -> tuple:
    """Advance N independent games one placement each.

    ``placements[i]`` may be None (or the game may already be over), in which
    case that slot passes through unchanged with a None info -- so the batch
    keeps its shape as games finish at different times.

    Returns ``(new_states, infos)``, both length N.

    This is a loop, not a vectorised kernel, and deliberately so: a row is 10
    bits and a piece is 4 cells, so numpy dispatch overhead exceeds the work.
    Real parallelism comes from `multiprocessing` across the 128 cores --
    `State.to_dict()`/`from_dict()` round-trip exactly, so states cross process
    boundaries intact.
    """
    out_states = []
    out_infos = []
    for i, s in enumerate(states):
        p = placements[i] if i < len(placements) else None
        if p is None or s is None or s.game_over:
            out_states.append(s)
            out_infos.append(None)
            continue
        ns, info = apply_placement(s, p)
        out_states.append(ns)
        out_infos.append(info)
    return out_states, out_infos


def rollout(state: State, choose, max_pieces: int = 100000) -> dict:
    """Play one game to completion. ``choose(state, placements) -> index``.

    Returns a summary dict. Provided so rollout bookkeeping is written once
    rather than re-implemented in cem.py, pg.py and evaluate.py.
    """
    s = state
    pieces = 0
    while not s.game_over and pieces < max_pieces:
        ps = legal_placements(s)
        if not ps:
            break
        s, _info = apply_placement(s, ps[choose(s, ps)])
        pieces += 1
    return {"lines": s.lines, "score": s.score, "pieces": pieces,
            "state": s, "game_over": s.game_over}


# --------------------------------------------------------------------------
# hashing (docs/spec.md section 7)
# --------------------------------------------------------------------------

_FNV_PRIME = 16777619
_FNV_OFFSET = 2166136261


def board_hash(rows) -> int:
    """FNV-1a 32 over the 22 rows, low byte then high byte."""
    h = _FNV_OFFSET
    for y in range(ROWS):
        r = rows[y]
        h = ((h ^ (r & 0xFF)) * _FNV_PRIME) & 0xFFFFFFFF
        h = ((h ^ ((r >> 8) & 0xFF)) * _FNV_PRIME) & 0xFFFFFFFF
    return h


def state_hash(state: State) -> int:
    """board_hash extended with the piece/hold/rng/progress fields."""
    h = board_hash(state.rows)
    extra = (
        7 if state.current is None else state.current,
        state.rot,
        7 if state.hold is None else state.hold,
        1 if state.can_hold else 0,
        state.rng,
        state.lines,
        state.level,
        state.b2b,
        state.combo,
    )
    for v in extra:
        v &= 0xFFFFFFFF
        for shift in (0, 8, 16, 24):
            h = ((h ^ ((v >> shift) & 0xFF)) * _FNV_PRIME) & 0xFFFFFFFF
    return h
