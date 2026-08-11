"""Deterministic xorshift32 RNG and 7-bag piece sequence.

Spec: docs/spec.md section 6. This must produce a bit-identical uint32
sequence to `web/engine.js` for the same seed. Do not "improve" the
generator -- the JS mirror and the parity test depend on this exact form.

No external dependencies (pure Python ints, masked to 32 bits).
"""

MASK32 = 0xFFFFFFFF

#: Fallback state when the caller passes seed 0. xorshift32 is stuck at 0.
DEFAULT_STATE = 0x9E3779B9

#: Piece indices. The *order* of this list is the initial 7-bag array and
#: therefore part of the RNG contract -- reordering changes every sequence.
PIECE_COUNT = 7


def seed_state(seed: int) -> int:
    """Normalize an arbitrary integer seed into a valid xorshift32 state."""
    s = int(seed) & MASK32
    return DEFAULT_STATE if s == 0 else s


def next_u32(state: int) -> tuple:
    """Advance the generator. Returns ``(new_state, value)``.

    Value and state are identical for xorshift32; both are returned so
    callers read naturally and never mutate a shared object.
    """
    x = state
    x ^= (x << 13) & MASK32
    x ^= x >> 17
    x ^= (x << 5) & MASK32
    x &= MASK32
    return x, x


def next_bag(state: int) -> tuple:
    """Fisher-Yates shuffle of ``[0..6]``. Returns ``(new_state, bag_tuple)``.

    Indices run from the back forward with ``rand() % (i + 1)``, matching
    docs/spec.md section 6 exactly.
    """
    bag = [0, 1, 2, 3, 4, 5, 6]
    for i in (6, 5, 4, 3, 2, 1):
        state, r = next_u32(state)
        j = r % (i + 1)
        bag[i], bag[j] = bag[j], bag[i]
    return state, tuple(bag)


class Xorshift32:
    """Thin stateful wrapper for callers that prefer an object.

    The engine itself stores the raw integer state inside the game state so
    that copying a state stays a shallow, allocation-free operation.
    """

    __slots__ = ("state",)

    def __init__(self, seed: int = 1):
        self.state = seed_state(seed)

    def next(self) -> int:
        self.state, v = next_u32(self.state)
        return v

    def bag(self) -> tuple:
        self.state, b = next_bag(self.state)
        return b

    def randrange(self, n: int) -> int:
        """Uniform-ish index in ``[0, n)`` via modulo (matches the bag rule)."""
        return self.next() % n
