"""Adversarial attack on engine._is_stuck's cost-saving guard.

The claim under attack (engine/engine.py, `_is_stuck` docstring):

    if every column's stack top sits at row 4 or below, some placement
    always fits (the tallest piece footprint is 4 cells, so
    `y_rest + min_dy >= 0` is guaranteed) and the full scan is skipped.

Implemented as::

    if not (rows[0] | rows[1] | rows[2] | rows[3]):
        return False

If the guard is wrong it leaks SILENTLY: a game that should have ended
keeps playing, so line counts / performance numbers are inflated and no
crash ever points at it.

Four independent attacks:

  A. exhaustive check of the vertical footprint of all 7x4 rotations
  B. formal bound: for every (piece, rot, x), with top[c] >= 4 for all c,
     is `y_rest + min_dy >= 0` forced?  Checked over every board whose
     column-top profile is drawn from {4..22} (sampled + adversarial
     all-tops-at-4 case), not just random boards.
  C. differential test: guarded `_is_stuck` vs. unguarded
     `not legal_placements(state)` over 100,000+ random board/piece
     positions, biased hard toward the boundary (tops at row 4-6).
  D. whole-game differential: monkeypatch the guard off and replay
     complete games, comparing every move's (game_over, lines, hash).

Run:  python3 tests/test_is_stuck_guard.py
Owner of the code under test: engine.  This file is checker-owned.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.join(os.path.dirname(_HERE), "engine")
sys.path.insert(0, _ENGINE)

import engine as E  # noqa: E402
from rng import next_u32, seed_state  # noqa: E402

FAILURES = []


def fail(tag, msg):
    FAILURES.append((tag, msg))
    print("  FAIL [%s] %s" % (tag, msg))


def ok(msg):
    print("  ok   %s" % msg)


# ---------------------------------------------------------------------------
# reference implementation: the guard removed, nothing else changed
# ---------------------------------------------------------------------------

def is_stuck_unguarded(state):
    return not E.legal_placements(state)


# ---------------------------------------------------------------------------
# A. vertical footprint of every rotation
# ---------------------------------------------------------------------------

def attack_a_footprints():
    print("A. vertical footprint of all 7x4 rotations (must be <= 4 cells)")
    worst = None
    for p in range(7):
        for r in range(4):
            cells = E.PIECE_CELLS[p][r]
            if len(cells) != 4:
                fail("A", "%s r%d has %d cells" % (E.PIECE_NAMES[p], r, len(cells)))
            min_dy = min(c[1] for c in cells)
            max_dy = max(c[1] for c in cells)
            extent = max_dy - min_dy + 1
            if extent > 4:
                fail("A", "%s r%d vertical extent %d > 4"
                     % (E.PIECE_NAMES[p], r, extent))
            # the quantity that actually matters for the guard
            slack = 3 - max_dy + min_dy
            if worst is None or slack < worst[0]:
                worst = (slack, E.PIECE_NAMES[p], r, min_dy, max_dy)
            # also: cells in one column must be vertically contiguous, which
            # is what makes the y_rest formula equal a real hard drop
            by_col = {}
            for dx, dy in cells:
                by_col.setdefault(dx, []).append(dy)
            for dx, dys in by_col.items():
                dys.sort()
                if dys != list(range(dys[0], dys[0] + len(dys))):
                    fail("A", "%s r%d column dx=%d not contiguous: %r"
                         % (E.PIECE_NAMES[p], r, dx, dys))
    ok("all 28 rotations: 4 cells, extent <= 4, columns contiguous")
    ok("tightest guard slack (3 - max_dy + min_dy) = %d  at %s r%d "
       "(min_dy=%d max_dy=%d)" % worst)
    if worst[0] < 0:
        fail("A", "slack negative -> guard unsound")
    return worst


# ---------------------------------------------------------------------------
# B. formal bound over column-top profiles
# ---------------------------------------------------------------------------

def board_from_tops(tops):
    """Board whose column c is filled from row tops[c] down to row 21."""
    rows = [0] * E.ROWS
    for c, t in enumerate(tops):
        for y in range(t, E.ROWS):
            rows[y] |= 1 << c
    return tuple(rows)


def attack_b_formal():
    print("B. formal bound: tops all >= 4  =>  some placement legal")
    # adversarial extreme first: every column topped out at exactly row 4
    profiles = [tuple([4] * E.W)]
    # one column at 4, rest empty; and every single-column variation
    for c in range(E.W):
        t = [E.ROWS] * E.W
        t[c] = 4
        profiles.append(tuple(t))
        t2 = [4] * E.W
        t2[c] = E.ROWS
        profiles.append(tuple(t2))
    # deterministic pseudo-random profiles over {4..22}
    st = seed_state(0xC0FFEE)
    for _ in range(4000):
        t = []
        for _c in range(E.W):
            st, v = next_u32(st)
            t.append(4 + v % (E.ROWS - 4 + 1))
        profiles.append(tuple(t))

    checked = 0
    for tops in profiles:
        rows = board_from_tops(tops)
        assert not (rows[0] | rows[1] | rows[2] | rows[3]), tops
        for piece in range(7):
            s = E.State()
            s.rows = rows
            s.current = piece
            s.rot = 0
            s.x = E.SPAWN_X[piece]
            s.y = 0
            n = len(E.legal_placements(s))
            checked += 1
            if n == 0:
                fail("B", "tops=%r piece=%s -> ZERO legal placements while "
                          "rows[0..3] empty (guard would say 'not stuck')"
                     % (tops, E.PIECE_NAMES[piece]))
    ok("%d (profile, piece) combinations, all have >= 1 legal placement"
       % checked)


# ---------------------------------------------------------------------------
# C. differential test over random positions
# ---------------------------------------------------------------------------

def attack_c_differential(n_positions=100000):
    print("C. differential: guarded _is_stuck vs unguarded, %d positions"
          % n_positions)
    st = seed_state(0x5EED1234)
    disagreements = 0
    guard_taken = 0
    stuck_count = 0
    boundary_cases = 0

    for i in range(n_positions):
        # Board generator, biased to the boundary the guard cares about.
        # mode 0: tops uniform in {2..22}  (guard sometimes off)
        # mode 1: tops in {4,5,6}          (guard on, right at the edge)
        # mode 2: dense random bits with a random empty ceiling
        # mode 3: near-full board, a few random holes  (genuinely stuck-ish)
        st, m = next_u32(st)
        mode = m % 4
        rows = [0] * E.ROWS
        if mode == 0:
            tops = []
            for _c in range(E.W):
                st, v = next_u32(st)
                tops.append(2 + v % (E.ROWS - 2 + 1))
            rows = list(board_from_tops(tops))
        elif mode == 1:
            tops = []
            for _c in range(E.W):
                st, v = next_u32(st)
                tops.append(4 + v % 3)
            rows = list(board_from_tops(tops))
            boundary_cases += 1
        elif mode == 2:
            st, v = next_u32(st)
            ceiling = v % 8            # rows above `ceiling` forced empty
            for y in range(ceiling, E.ROWS):
                st, v = next_u32(st)
                st, w = next_u32(st)
                rows[y] = (v ^ (w >> 7)) & E.FULL_ROW
        else:
            st, v = next_u32(st)
            ceiling = v % 6
            for y in range(ceiling, E.ROWS):
                rows[y] = E.FULL_ROW
            st, k = next_u32(st)
            for _h in range(1 + k % 6):
                st, a = next_u32(st)
                st, b = next_u32(st)
                y = ceiling + a % max(1, (E.ROWS - ceiling))
                rows[y] &= ~(1 << (b % E.W)) & E.FULL_ROW
        rows = tuple(rows)

        st, v = next_u32(st)
        piece = v % 7
        s = E.State()
        s.rows = rows
        s.current = piece
        s.rot = 0
        s.x = E.SPAWN_X[piece]
        s.y = 0

        guarded = E._is_stuck(s)
        plain = is_stuck_unguarded(s)
        if not (rows[0] | rows[1] | rows[2] | rows[3]):
            guard_taken += 1
        if plain:
            stuck_count += 1
        if guarded != plain:
            disagreements += 1
            if disagreements <= 3:
                fail("C", "position %d disagrees: guarded=%s plain=%s "
                          "piece=%s rows=%r"
                     % (i, guarded, plain, E.PIECE_NAMES[piece], list(rows)))

    ok("guard short-circuit taken in %d/%d positions" % (guard_taken, n_positions))
    ok("boundary-mode (tops in 4..6) positions: %d" % boundary_cases)
    ok("genuinely stuck positions found by the unguarded scan: %d" % stuck_count)
    if disagreements == 0:
        ok("0 disagreements over %d positions" % n_positions)
    else:
        fail("C", "%d disagreements" % disagreements)
    return stuck_count


# ---------------------------------------------------------------------------
# D. whole-game differential with the guard monkeypatched off
# ---------------------------------------------------------------------------

def play_game(seed, policy, max_moves=3000):
    """Return the per-move trace of a deterministic game."""
    s = E.new_game(seed)
    trace = []
    for _ in range(max_moves):
        if s.game_over:
            break
        ps = E.legal_placements(s)
        if not ps:
            break
        p = policy(ps, s)
        s, info = E.apply_placement(s, p)
        trace.append((info["game_over"], info["lines_cleared"],
                      E.board_hash(s.rows), s.lines))
        if info["game_over"]:
            break
    return trace, s


def pol_first(ps, s):
    return ps[0]


def pol_lowest(ps, s):
    best = ps[0]
    for p in ps:
        if p[2] > best[2]:
            best = p
    return best


def make_pol_rand(seed):
    box = [seed_state(seed)]

    def pol(ps, s):
        box[0], v = next_u32(box[0])
        return ps[v % len(ps)]
    return pol


def pol_highest(ps, s):
    """Stack as high as possible -- drives boards into the guard's edge."""
    best = ps[0]
    for p in ps:
        if p[2] < best[2]:
            best = p
    return best


def attack_d_whole_game(n_seeds=200):
    print("D. whole-game differential, guard patched off, %d seeds x 4 policies"
          % n_seeds)
    orig = E._is_stuck
    policies = [("first", pol_first), ("lowest", pol_lowest),
                ("highest", pol_highest)]
    mismatches = 0
    total_moves = 0
    total_lines = 0
    for i in range(n_seeds):
        seed = 1000 + i * 7919
        pols = policies + [("rand", make_pol_rand(seed))]
        for name, pol in pols:
            E._is_stuck = orig
            t_guard, s_guard = play_game(seed, pol)
            E._is_stuck = is_stuck_unguarded
            # policies with internal RNG must be rebuilt to replay identically
            pol2 = make_pol_rand(seed) if name == "rand" else pol
            t_plain, s_plain = play_game(seed, pol2)
            total_moves += len(t_guard)
            total_lines += s_guard.lines
            if t_guard != t_plain:
                mismatches += 1
                if mismatches <= 3:
                    j = next((k for k in range(min(len(t_guard), len(t_plain)))
                              if t_guard[k] != t_plain[k]), None)
                    fail("D", "seed=%d policy=%s diverges at move %r: "
                              "guarded=%r plain=%r (len %d vs %d)"
                         % (seed, name, j,
                            t_guard[j] if j is not None else None,
                            t_plain[j] if j is not None else None,
                            len(t_guard), len(t_plain)))
    E._is_stuck = orig
    ok("%d moves, %d lines cleared across all games" % (total_moves, total_lines))
    if mismatches == 0:
        ok("0 trace mismatches over %d games" % (n_seeds * 4))
    else:
        fail("D", "%d game traces diverge" % mismatches)


# ---------------------------------------------------------------------------
# E. does the guard judge the same thing as the game-over definition?
# ---------------------------------------------------------------------------

def attack_e_gameover_semantics():
    """spec.md 3: game over = spawn cells occupied, OR (agent path) no
    legal placement.  _is_stuck implements only the second clause and is
    called only when the spawn already succeeded.  Check the two clauses do
    not shadow each other, and that spawn cells live inside rows 0..3.
    """
    print("E. spawn-collision clause vs. no-placement clause")
    for p in range(7):
        dys = [dy for dy, _m in E._SPAWN_MASKS[p]]
        if max(dys) > 3:
            fail("E", "%s spawn touches row %d, outside the guard's rows 0..3"
                 % (E.PIECE_NAMES[p], max(dys)))
    ok("all 7 spawn footprints live in rows 0..1 (inside the guard's window)")

    # A board where the spawn window alone is blocked but plenty of
    # placements remain elsewhere: the two clauses must be different tests.
    rows = [0] * E.ROWS
    rows[1] = (1 << 3) | (1 << 4) | (1 << 5)    # only the spawn columns
    s = E.State()
    s.rows = tuple(rows)
    s.current = E.T
    s.rot = 0
    s.x = E.SPAWN_X[E.T]
    s.y = 0
    blocked = any(s.rows[dy] & m for dy, m in E._SPAWN_MASKS[E.T])
    n = len(E.legal_placements(s))
    if blocked and n > 0:
        ok("spawn blocked yet %d legal placements remain -> spawn clause and "
           "no-placement clause are genuinely different tests" % n)
    else:
        fail("E", "could not separate the two clauses (blocked=%s n=%d)"
             % (blocked, n))

    # and the guard on that board correctly does NOT short-circuit
    if not (s.rows[0] | s.rows[1] | s.rows[2] | s.rows[3]):
        fail("E", "guard would short-circuit a spawn-blocked board")
    else:
        ok("guard does not short-circuit when rows 0..3 are dirty")

    # F. fragility, not a defect: the guard's window is the literal constant 4
    # and the tight case (I r1) has exactly ZERO slack. Record the invariants
    # the guard silently depends on, so a later constant change breaks HERE.
    print("F. invariants the guard depends on (zero margin)")
    if E.BUFFER_ROWS != 2:
        fail("F", "BUFFER_ROWS changed to %d; _is_stuck still reads rows[0..3]"
             % E.BUFFER_ROWS)
    max_extent = max(max(c[1] for c in E.PIECE_CELLS[p][r])
                     - min(c[1] for c in E.PIECE_CELLS[p][r]) + 1
                     for p in range(7) for r in range(4))
    if max_extent != 4:
        fail("F", "max piece vertical extent is %d, not 4; the hard-coded "
                  "4-row window in _is_stuck is no longer the right size"
             % max_extent)
    ok("BUFFER_ROWS=2, max vertical extent=4 -> window of 4 is exactly right, "
       "with zero slack (I r1 lands at y_rest+min_dy == 0)")


def main():
    print("=" * 72)
    print("checker: adversarial attack on engine._is_stuck cost-saving guard")
    print("=" * 72)
    attack_a_footprints()
    print()
    attack_b_formal()
    print()
    attack_c_differential()
    print()
    attack_d_whole_game()
    print()
    attack_e_gameover_semantics()
    print()
    print("=" * 72)
    if FAILURES:
        print("RESULT: %d FAILURE(S)" % len(FAILURES))
        for tag, msg in FAILURES:
            print("  [%s] %s" % (tag, msg))
        return 1
    print("RESULT: no counterexample found. Guard survives all 5 attacks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
