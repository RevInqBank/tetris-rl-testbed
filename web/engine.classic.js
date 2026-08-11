/**
 * GENERATED FILE -- DO NOT EDIT.
 *
 * Single-file classic-script build of web/tables.js + web/engine.js, produced
 * by engine/gen_classic_bundle.py. Exists so the app also opens from file://,
 * where ES module loading is blocked by CORS.
 *
 * Edit the ES modules and regenerate:
 *     python3 engine/gen_classic_bundle.py
 *
 * Browser : window.TetrisEngine
 * node    : require('./engine.classic.js')
 *
 * The ES modules remain the source of truth. If this file and engine.js ever
 * disagree, this one is stale -- regenerate it.
 */
(function (global) {
  'use strict';

  // ---- web/tables.js ----
/**
 * GENERATED FILE -- DO NOT EDIT.
 *
 * Mirror of engine/tables.py, produced by engine/gen_tables_js.py.
 * Edit the Python file and regenerate:
 *
 *     python3 engine/gen_tables_js.py
 *
 * engine/parity.py --tests fails if this file is stale.
 */

const W = 10;
const VISIBLE_ROWS = 20;
const BUFFER_ROWS = 2;
const ROWS = 22;
const BOTTOM_ROW = 21;
const FULL_ROW = 1023;
const PIECE_NAMES = ['I', 'O', 'T', 'S', 'Z', 'J', 'L'];
const BOX_SIZE = [4, 2, 3, 3, 3, 3, 3];
const PIECE_CELLS = [
  [
    [[0, 1], [1, 1], [2, 1], [3, 1]],
    [[2, 0], [2, 1], [2, 2], [2, 3]],
    [[0, 2], [1, 2], [2, 2], [3, 2]],
    [[1, 0], [1, 1], [1, 2], [1, 3]]
  ],
  [
    [[0, 0], [1, 0], [0, 1], [1, 1]],
    [[0, 0], [1, 0], [0, 1], [1, 1]],
    [[0, 0], [1, 0], [0, 1], [1, 1]],
    [[0, 0], [1, 0], [0, 1], [1, 1]]
  ],
  [
    [[1, 0], [0, 1], [1, 1], [2, 1]],
    [[1, 0], [1, 1], [2, 1], [1, 2]],
    [[0, 1], [1, 1], [2, 1], [1, 2]],
    [[1, 0], [0, 1], [1, 1], [1, 2]]
  ],
  [
    [[1, 0], [2, 0], [0, 1], [1, 1]],
    [[1, 0], [1, 1], [2, 1], [2, 2]],
    [[1, 1], [2, 1], [0, 2], [1, 2]],
    [[0, 0], [0, 1], [1, 1], [1, 2]]
  ],
  [
    [[0, 0], [1, 0], [1, 1], [2, 1]],
    [[2, 0], [1, 1], [2, 1], [1, 2]],
    [[0, 1], [1, 1], [1, 2], [2, 2]],
    [[1, 0], [0, 1], [1, 1], [0, 2]]
  ],
  [
    [[0, 0], [0, 1], [1, 1], [2, 1]],
    [[1, 0], [2, 0], [1, 1], [1, 2]],
    [[0, 1], [1, 1], [2, 1], [2, 2]],
    [[1, 0], [1, 1], [0, 2], [1, 2]]
  ],
  [
    [[2, 0], [0, 1], [1, 1], [2, 1]],
    [[1, 0], [1, 1], [1, 2], [2, 2]],
    [[0, 1], [1, 1], [2, 1], [0, 2]],
    [[0, 0], [1, 0], [1, 1], [1, 2]]
  ]
];
const UNIQUE_ROTS = [[0, 1], [0], [0, 1, 2, 3], [0, 1], [0, 1], [0, 1, 2, 3], [0, 1, 2, 3]];
const SPAWN_Y = 0;
const SPAWN_X = [3, 4, 3, 3, 3, 3, 3];
const SPAWN_ROT = 0;
const SCORE_TABLE = [0, 100, 300, 500, 800];
const SOFT_DROP_POINTS_PER_CELL = 1;
const HARD_DROP_POINTS_PER_CELL = 2;
const LINES_PER_LEVEL = 10;
const GRAVITY_L1_L10 = [48, 43, 38, 33, 28, 23, 18, 13, 8, 6];
const GRAVITY_TAIL = [[13, 5], [16, 4], [19, 3], [28, 2]];
const GRAVITY_MIN = 1;
const LOCK_DELAY_MS = 500;
const LOCK_RESET_LIMIT = 15;
const B2B_LINES = 4;
const B2B_MULT_NUM = 3;
const B2B_MULT_DEN = 2;
const COMBO_BONUS_PER_STEP = 50;
const DIFFICULTY_NORMAL = 0;
const DIFFICULTY_HARD = 1;
const DIFFICULTY_EXTREME = 2;
const DIFFICULTY_NAMES = ['normal', 'hard', 'extreme'];
const DIFFICULTY_NEXT_VISIBLE = [5, 1, 0];
const DIFFICULTY_HOLD_ENABLED = [true, false, false];
const DIFFICULTY_DEFAULT = 0;
const KICKS_JLSTZ = {
  '0,1': [[0, 0], [-1, 0], [-1, -1], [0, 2], [-1, 2]],
  '1,0': [[0, 0], [1, 0], [1, 1], [0, -2], [1, -2]],
  '1,2': [[0, 0], [1, 0], [1, 1], [0, -2], [1, -2]],
  '2,1': [[0, 0], [-1, 0], [-1, -1], [0, 2], [-1, 2]],
  '2,3': [[0, 0], [1, 0], [1, -1], [0, 2], [1, 2]],
  '3,2': [[0, 0], [-1, 0], [-1, 1], [0, -2], [-1, -2]],
  '3,0': [[0, 0], [-1, 0], [-1, 1], [0, -2], [-1, -2]],
  '0,3': [[0, 0], [1, 0], [1, -1], [0, 2], [1, 2]]
};
const KICKS_I = {
  '0,1': [[0, 0], [-2, 0], [1, 0], [-2, 1], [1, -2]],
  '1,0': [[0, 0], [2, 0], [-1, 0], [2, -1], [-1, 2]],
  '1,2': [[0, 0], [-1, 0], [2, 0], [-1, -2], [2, 1]],
  '2,1': [[0, 0], [1, 0], [-2, 0], [1, 2], [-2, -1]],
  '2,3': [[0, 0], [2, 0], [-1, 0], [2, -1], [-1, 2]],
  '3,2': [[0, 0], [-2, 0], [1, 0], [-2, 1], [1, -2]],
  '3,0': [[0, 0], [1, 0], [-2, 0], [1, 2], [-2, -1]],
  '0,3': [[0, 0], [-1, 0], [2, 0], [-1, -2], [2, 1]]
};
const KICKS_NONE = [[0, 0]];
const MIN_DX = [[0, 2, 0, 1], [0, 0, 0, 0], [0, 1, 0, 0], [0, 1, 0, 0], [0, 1, 0, 0], [0, 1, 0, 0], [0, 1, 0, 0]];
const MAX_DX = [[3, 2, 3, 1], [1, 1, 1, 1], [2, 2, 2, 1], [2, 2, 2, 1], [2, 2, 2, 1], [2, 2, 2, 1], [2, 2, 2, 1]];
const MIN_DY = [[1, 0, 2, 0], [0, 0, 0, 0], [0, 0, 1, 0], [0, 0, 1, 0], [0, 0, 1, 0], [0, 0, 1, 0], [0, 0, 1, 0]];
const MAX_DY = [[1, 3, 2, 3], [1, 1, 1, 1], [1, 2, 2, 2], [1, 2, 2, 2], [1, 2, 2, 2], [1, 2, 2, 2], [1, 2, 2, 2]];
const BOTTOM_PROFILE = [
  [
    [[0, 1], [1, 1], [2, 1], [3, 1]],
    [[2, 3]],
    [[0, 2], [1, 2], [2, 2], [3, 2]],
    [[1, 3]]
  ],
  [
    [[0, 1], [1, 1]],
    [[0, 1], [1, 1]],
    [[0, 1], [1, 1]],
    [[0, 1], [1, 1]]
  ],
  [
    [[0, 1], [1, 1], [2, 1]],
    [[1, 2], [2, 1]],
    [[0, 1], [1, 2], [2, 1]],
    [[0, 1], [1, 2]]
  ],
  [
    [[0, 1], [1, 1], [2, 0]],
    [[1, 1], [2, 2]],
    [[0, 2], [1, 2], [2, 1]],
    [[0, 1], [1, 2]]
  ],
  [
    [[0, 0], [1, 1], [2, 1]],
    [[1, 2], [2, 1]],
    [[0, 1], [1, 2], [2, 2]],
    [[0, 2], [1, 1]]
  ],
  [
    [[0, 1], [1, 1], [2, 1]],
    [[1, 2], [2, 0]],
    [[0, 1], [1, 1], [2, 2]],
    [[0, 2], [1, 2]]
  ],
  [
    [[0, 1], [1, 1], [2, 1]],
    [[1, 2], [2, 2]],
    [[0, 2], [1, 1], [2, 1]],
    [[0, 0], [1, 2]]
  ]
];
const X_RANGE = [
  [[0, 7], [-2, 8], [0, 7], [-1, 9]],
  [[0, 9], [0, 9], [0, 9], [0, 9]],
  [[0, 8], [-1, 8], [0, 8], [0, 9]],
  [[0, 8], [-1, 8], [0, 8], [0, 9]],
  [[0, 8], [-1, 8], [0, 8], [0, 9]],
  [[0, 8], [-1, 8], [0, 8], [0, 9]],
  [[0, 8], [-1, 8], [0, 8], [0, 9]]
];
const PLACEMENT_COUNT = [[7, 10, 7, 10], [9, 9, 9, 9], [8, 9, 8, 9], [8, 9, 8, 9], [8, 9, 8, 9], [8, 9, 8, 9], [8, 9, 8, 9]];
const MAX_PIECE_VEXTENT = 4;
const GUARD_ROWS = 4;
const FNV_OFFSET_32 = 2166136261;
const FNV_PRIME_32 = 16777619;
const MASK32 = 4294967295;
const XORSHIFT_FALLBACK_STATE = 2654435769;
const BAG_SIZE = 7;
const QUEUE_MIN = 7;
const NEXT_VISIBLE = 5;

// piece index constants
const I = 0;
const O = 1;
const T = 2;
const S = 3;
const Z = 4;
const J = 5;
const L = 6;

/** KICKS[piece] -> kick table, or null for O (which never needs one). */
const KICKS = [KICKS_I, null, KICKS_JLSTZ, KICKS_JLSTZ, KICKS_JLSTZ,
                      KICKS_JLSTZ, KICKS_JLSTZ];

  // ---- web/engine.js ----
  var PIECE_O = O;
/**
 * Tetris core -- JS mirror of engine/engine.py.
 *
 * Implements docs/spec.md v1. Every rule, table and constant here must match
 * the Python engine bit for bit; engine/parity.py proves it by replaying the
 * same placement sequence in both and comparing board hashes.
 *
 * ES module, zero dependencies. If you change a rule here, change it in
 * engine/engine.py and docs/spec.md in the same breath.
 *
 * Both camelCase (JS-idiomatic) and snake_case (Python-identical) names are
 * exported for every public function.
 */

// ---------------------------------------------------------------------------
// constants -- re-exported from tables.js, which is GENERATED from
// engine/tables.py by engine/gen_tables_js.py. Never hand-edit either the
// tables or these values; edit the Python file and regenerate.
// ---------------------------------------------------------------------------



// ---------------------------------------------------------------------------
// wall kicks (docs/spec.md section 4) -- human play only
// ---------------------------------------------------------------------------



/** SRS kick candidates for a rotation, in test order. */
function kickOffsets(piece, frm, to) {
  const table = KICKS[piece];
  if (!table) return KICKS_NONE;
  return table[frm + ',' + to] || KICKS_NONE;
}

// ---------------------------------------------------------------------------
// gravity (docs/spec.md section 5)
// ---------------------------------------------------------------------------

const MS_PER_FRAME = 1000 / 60;

/** Frames at 60 fps for gravity to pull the piece down one cell. */
function framesPerCell(level) {
  const lv = level <= 0 ? 1 : level;
  if (lv <= 10) return GRAVITY_L1_L10[lv - 1];
  for (const [maxLevel, fpc] of GRAVITY_TAIL) if (lv <= maxLevel) return fpc;
  return GRAVITY_MIN;
}

/** Milliseconds per gravity step at this level. */
function gravityIntervalMs(level) {
  return framesPerCell(level) * MS_PER_FRAME;
}

// ---------------------------------------------------------------------------
// xorshift32 + 7-bag (docs/spec.md section 6)
// ---------------------------------------------------------------------------

const DEFAULT_RNG_STATE = XORSHIFT_FALLBACK_STATE;

function seedState(seed) {
  const s = seed >>> 0;
  return s === 0 ? DEFAULT_RNG_STATE : s;
}

/** Advance xorshift32. Returns the new state (== the drawn value). */
function nextU32(state) {
  let x = state >>> 0;
  x ^= (x << 13);
  x >>>= 0;
  x ^= (x >>> 17);
  x ^= (x << 5);
  return x >>> 0;
}

/** Fisher-Yates 7-bag. Returns [newState, bagArray]. */
function nextBag(state) {
  const bag = [0, 1, 2, 3, 4, 5, 6];
  let st = state >>> 0;
  for (let i = 6; i >= 1; i--) {
    st = nextU32(st);
    const j = st % (i + 1);
    const tmp = bag[i]; bag[i] = bag[j]; bag[j] = tmp;
  }
  return [st, bag];
}

// ---------------------------------------------------------------------------
// precomputed placement tables (mirror of engine.py _build_tables)
//
// TABLE[piece][rot] -> [{rot, x, masks:[[dy,mask],...],
//                        bottom:[[col,bottomDy],...], minDy, maxDy}, ...]
// ---------------------------------------------------------------------------

const TABLE = (() => {
  const out = [];
  for (let piece = 0; piece < 7; piece++) {
    const perRot = [];
    for (let rot = 0; rot < 4; rot++) {
      const cells = PIECE_CELLS[piece][rot];
      // Geometry comes from tables.js -- never re-derive it here, or the three
      // implementations can drift.
      const minDy = MIN_DY[piece][rot];
      const maxDy = MAX_DY[piece][rot];
      const bottomByCol = new Map();
      for (const [dx, dy] of cells) {
        if (!bottomByCol.has(dx) || dy > bottomByCol.get(dx)) bottomByCol.set(dx, dy);
      }
      const bottomSorted = [...bottomByCol.entries()].sort((a, b) => a[0] - b[0]);

      const entries = [];
      const [xLo, xHi] = X_RANGE[piece][rot];
      for (let x = xLo; x < xHi; x++) {
        const masksByDy = new Map();
        for (const [dx, dy] of cells) {
          masksByDy.set(dy, (masksByDy.get(dy) || 0) | (1 << (x + dx)));
        }
        const masks = [...masksByDy.entries()].sort((a, b) => a[0] - b[0]);
        const bottom = bottomSorted.map(([dx, bdy]) => [x + dx, bdy]);
        entries.push({ rot, x, masks, bottom, minDy, maxDy });
      }
      perRot.push(entries);
    }
    out.push(perRot);
  }
  return out;
})();

const SPAWN_MASKS = [];
for (let p = 0; p < 7; p++) {
  SPAWN_MASKS.push(TABLE[p][0].find((e) => e.x === SPAWN_X[p]).masks);
}

function tableEntry(piece, rot, x) {
  const entries = TABLE[piece][rot];
  for (let i = 0; i < entries.length; i++) if (entries[i].x === x) return entries[i];
  return null;
}

// ---------------------------------------------------------------------------
// state
// ---------------------------------------------------------------------------

function emptyRows() {
  return new Array(ROWS).fill(0);
}

/**
 * Game state. Field names match docs/spec.md section 7 and the Python
 * State slots exactly, so JSON crosses the language boundary unchanged.
 */
class State {
  constructor() {
    this.rows = emptyRows();
    this.current = null;
    this.rot = 0;
    this.x = 0;
    this.y = 0;
    this.queue = [];
    this.hold = null;
    this.can_hold = true;
    this.rng = seedState(1);
    this.score = 0;
    this.lines = 0;
    this.level = 1;
    this.pieces = 0;
    this.game_over = false;
    // Interactive path only; the agent path never reads these.
    this.lock_ms = 0;
    this.lock_resets = 0;
    this.grav_ms = 0;
    /**
     * Deepest row this piece has reached. Only descending PAST it resets the
     * lock timer -- see tickMs.
     */
    this.lowest_y = SPAWN_Y;
    /**
     * True once the piece has rested on the stack at least once. From then on
     * the lock timer runs every frame, even if a kick lifts the piece.
     */
    this.touched_down = false;
    /** Length of the current back-to-back tetris chain; 0 = not in a chain. */
    this.b2b = 0;
    /** Consecutive line-clearing pieces so far; 0 = no combo running. */
    this.combo = 0;
    /**
     * Information-restriction mode. Never affects board rules or the piece
     * sequence -- see docs/spec.md section 14.
     */
    this.difficulty = DIFFICULTY_DEFAULT;
  }

  clone() {
    const s = new State();
    s.rows = this.rows.slice();
    s.current = this.current;
    s.rot = this.rot;
    s.x = this.x;
    s.y = this.y;
    s.queue = this.queue.slice();
    s.hold = this.hold;
    s.can_hold = this.can_hold;
    s.rng = this.rng;
    s.score = this.score;
    s.lines = this.lines;
    s.level = this.level;
    s.pieces = this.pieces;
    s.game_over = this.game_over;
    s.lock_ms = this.lock_ms;
    s.lock_resets = this.lock_resets;
    s.grav_ms = this.grav_ms;
    s.lowest_y = this.lowest_y;
    s.touched_down = this.touched_down;
    s.b2b = this.b2b;
    s.combo = this.combo;
    s.difficulty = this.difficulty;
    return s;
  }

  toObject() {
    return {
      rows: this.rows.slice(),
      current: this.current,
      rot: this.rot,
      x: this.x,
      y: this.y,
      queue: this.queue.slice(),
      hold: this.hold,
      can_hold: this.can_hold,
      rng: this.rng >>> 0,
      score: this.score,
      lines: this.lines,
      level: this.level,
      pieces: this.pieces,
      game_over: this.game_over,
      lock_ms: this.lock_ms,
      lock_resets: this.lock_resets,
      grav_ms: this.grav_ms,
      lowest_y: this.lowest_y,
      touched_down: this.touched_down,
      b2b: this.b2b,
      combo: this.combo,
      difficulty: this.difficulty,
    };
  }
}

function stateFromObject(d) {
  const s = new State();
  s.rows = d.rows.slice();
  s.current = d.current;
  s.rot = d.rot;
  s.x = d.x;
  s.y = d.y;
  s.queue = d.queue.slice();
  s.hold = d.hold;
  s.can_hold = !!d.can_hold;
  s.rng = d.rng >>> 0;
  s.score = d.score;
  s.lines = d.lines;
  s.level = d.level;
  s.pieces = d.pieces;
  s.game_over = !!d.game_over;
  s.lock_ms = d.lock_ms || 0;
  s.lock_resets = d.lock_resets || 0;
  s.grav_ms = d.grav_ms || 0;
  s.lowest_y = (d.lowest_y === undefined) ? SPAWN_Y : d.lowest_y;
  s.touched_down = !!d.touched_down;
  s.b2b = d.b2b || 0;
  s.combo = d.combo || 0;
  s.difficulty = (d.difficulty === undefined) ? DIFFICULTY_DEFAULT : d.difficulty;
  return s;
}

function stateToObject(state) {
  return state.toObject();
}

// ---------------------------------------------------------------------------
// queue management
// ---------------------------------------------------------------------------

function refill(state) {
  while (state.queue.length < QUEUE_MIN) {
    const [st, bag] = nextBag(state.rng);
    state.rng = st;
    for (let i = 0; i < 7; i++) state.queue.push(bag[i]);
  }
}

/**
 * Raised when code asks for upcoming pieces that the mode hides.
 *
 * Deliberately loud. Returning [] here would let a lookahead agent quietly
 * degrade to zero-ply and still report a number -- which is exactly the
 * experiment this mode exists to run, so a silent empty value would make the
 * result meaningless instead of visibly wrong.
 */
class NextPeekBlocked extends Error {}

/** How many upcoming pieces this state's mode reveals. */
function nextVisibleCount(state) {
  return DIFFICULTY_NEXT_VISIBLE[state.difficulty];
}

/** Whether the hold slot exists in this state's mode. */
function holdEnabled(state) {
  return DIFFICULTY_HOLD_ENABLED[state.difficulty];
}

function difficultyName(state) {
  return DIFFICULTY_NAMES[state.difficulty];
}

/**
 * The upcoming pieces this mode permits seeing. THE sanctioned accessor.
 *
 * `state.queue` is engine-internal bookkeeping: it must hold future pieces so
 * the generator stays identical across modes. Reading it directly bypasses the
 * difficulty setting. Anything outside the engine -- UI, features, search --
 * must come through here.
 *
 * Throws NextPeekBlocked in extreme mode rather than returning [].
 */
function visibleNext(state) {
  const n = nextVisibleCount(state);
  if (n === 0) {
    throw new NextPeekBlocked(
      'difficulty ' + DIFFICULTY_NAMES[state.difficulty] + ' reveals no ' +
      'upcoming pieces. A lookahead policy must detect this and fall back to ' +
      'zero-ply explicitly (catch NextPeekBlocked or check nextVisibleCount) ' +
      '-- it must not silently search an empty future.');
  }
  return state.queue.slice(0, n);
}

/**
 * Fresh game with a deterministic piece sequence.
 *
 * `difficulty` restricts information only. The same seed produces the same
 * pieces in every mode -- see docs/spec.md section 14.
 */
function newGame(seed = 1, difficulty = DIFFICULTY_DEFAULT) {
  if (DIFFICULTY_NEXT_VISIBLE[difficulty] === undefined) {
    throw new Error('unknown difficulty ' + difficulty);
  }
  const s = new State();
  s.difficulty = difficulty;
  s.rng = seedState(seed);
  refill(s);
  spawnNext(s);
  return s;
}

function spawnNext(state) {
  refill(state);
  const piece = state.queue.shift();
  state.current = piece;
  state.rot = 0;
  state.x = SPAWN_X[piece];
  state.y = SPAWN_Y;
  state.can_hold = true;
  state.lock_ms = 0;
  state.lock_resets = 0;
  state.grav_ms = 0;
  state.lowest_y = SPAWN_Y;
  state.touched_down = false;

  const masks = SPAWN_MASKS[piece];
  for (let i = 0; i < masks.length; i++) {
    if (state.rows[masks[i][0]] & masks[i][1]) {
      state.game_over = true;
      return false;
    }
  }
  return true;
}

// ---------------------------------------------------------------------------
// collision / heights
// ---------------------------------------------------------------------------

function fits(rows, piece, rot, x, y) {
  const cells = PIECE_CELLS[piece][rot];
  for (let i = 0; i < 4; i++) {
    const cx = x + cells[i][0];
    const cy = y + cells[i][1];
    if (cx < 0 || cx >= W || cy < 0 || cy >= ROWS) return false;
    if ((rows[cy] >> cx) & 1) return false;
  }
  return true;
}

/**
 * `fits`, exposed only so engine/parity.py can probe rotations from known
 * poses. Not part of the UI contract.
 */
function fitsForParity(rows, piece, rot, x, y) {
  return fits(rows, piece, rot, x, y);
}

/**
 * Topmost FILLED ROW INDEX per column; ROWS (22) for an empty column.
 *
 * This is the `top[]` of docs/spec.md section 5, NOT a height. Row indices grow
 * downward, so a taller stack gives a SMALLER number:
 *
 *   rows[21] full, rows[20] has column 0 only
 *   columnTops(rows) -> [20, 21, 21, 21, 21, 21, 21, 21, 21, 21]
 *   heights would be -> [ 2,  1,  1,  1,  1,  1,  1,  1,  1,  1]
 *
 * For a height use `ROWS - columnTops(rows)[c]`.
 *
 * Named `columnHeights` until web caught the mismatch against
 * rl/features.py:column_heights, which returns true heights. The old name now
 * throws rather than silently returning the other quantity.
 */
function columnTops(rows) {
  const top = new Array(W).fill(ROWS);
  let pending = FULL_ROW;
  for (let y = 0; y < ROWS; y++) {
    let hit = rows[y] & pending;
    if (hit) {
      pending &= ~hit;
      let x = 0;
      while (hit) {
        if (hit & 1) top[x] = y;
        hit >>>= 1;
        x++;
      }
      if (!pending) break;
    }
  }
  return top;
}

// ---------------------------------------------------------------------------
// agent path
// ---------------------------------------------------------------------------

/**
 * Hard-drop-reachable placements as [rot, x, yRest, piece].
 * Sorted rot then x -- docs/spec.md section 8 fixes this order.
 */
function legalPlacements(state) {
  if (state.game_over || state.current === null) return [];
  const piece = state.current;
  const top = columnTops(state.rows);
  const rots = UNIQUE_ROTS[piece];
  const out = [];
  for (let ri = 0; ri < rots.length; ri++) {
    const entries = TABLE[piece][rots[ri]];
    for (let ei = 0; ei < entries.length; ei++) {
      const e = entries[ei];
      const bottom = e.bottom;
      let yRest = ROWS;
      for (let bi = 0; bi < bottom.length; bi++) {
        const cand = top[bottom[bi][0]] - bottom[bi][1] - 1;
        if (cand < yRest) yRest = cand;
      }
      if (yRest + e.minDy < 0) continue;
      out.push([e.rot, e.x, yRest, piece]);
    }
  }
  return out;
}

function popcount10(v) {
  let c = 0;
  while (v) { v &= v - 1; c++; }
  return c;
}

/**
 * True when no hard-drop placement fits -- i.e. game over.
 *
 * Mirror of engine.py `_is_stuck`, including its guard. READ THAT DOCSTRING
 * before touching this: the guard is only valid while
 * GUARD_ROWS >= MAX_PIECE_VEXTENT, and the slack is currently EXACTLY ZERO.
 * If a taller piece is added, delete the guard rather than adjusting it --
 * getting it wrong silently misses game overs instead of crashing.
 */
function isStuck(state) {
  const r = state.rows;
  for (let y = 0; y < GUARD_ROWS; y++) {
    if (r[y]) return legalPlacements(state).length === 0;
  }
  return false;
}

if (GUARD_ROWS < MAX_PIECE_VEXTENT) {
  throw new Error('isStuck guard is invalid: GUARD_ROWS=' + GUARD_ROWS +
    ' < MAX_PIECE_VEXTENT=' + MAX_PIECE_VEXTENT + '. Delete the guard.');
}

/**
 * Lock the piece at p, clear lines, spawn the next piece.
 * `state` is not modified. Returns [nextState, info].
 */
/**
 * Score one lock. Returns [scoreDelta, b2bNext, comboNext, b2bApplied].
 *
 * Exact mirror of engine.py `_score_clear` -- docs/spec.md section 5.
 *
 * * A move clearing no lines resets the combo but LEAVES the back-to-back
 *   chain alone; placing without clearing does not break B2B.
 * * A tetris continues the chain and, if a chain was already running, scores
 *   the x1.5 bonus. A 1-3 line clear breaks the chain.
 * * Combo bonus uses the chain length BEFORE this clear.
 *
 * All arithmetic is integer, and every SCORE_TABLE entry is even, so the x1.5
 * is exact -- no float rounding to drift from Python.
 */
function scoreClear(n, level, b2b, combo) {
  if (n === 0) return [0, b2b, 0, false];

  let base = SCORE_TABLE[n];
  let b2bApplied, b2bNext;
  if (n === B2B_LINES) {
    b2bApplied = b2b > 0;
    b2bNext = b2b + 1;
  } else {
    b2bApplied = false;
    b2bNext = 0;
  }
  if (b2bApplied) base = Math.floor((base * B2B_MULT_NUM) / B2B_MULT_DEN);

  const comboBonus = COMBO_BONUS_PER_STEP * combo * level;
  return [base * level + comboBonus, b2bNext, combo + 1, b2bApplied];
}

/**
 * scoreClear, exposed only so engine/parity.py can prove it matches Python.
 * Not part of the UI contract -- do not call it from ui.js.
 */
function scoreClearForParity(n, level, b2b, combo) {
  return scoreClear(n, level, b2b, combo);
}

function applyPlacement(state, p) {
  const rot = p[0], x = p[1], yRest = p[2], piece = p[3];
  const entry = tableEntry(piece, rot, x);
  if (!entry) throw new Error('illegal placement column: ' + JSON.stringify(p));
  const masks = entry.masks;

  const rows = state.rows.slice();
  const touched = [];
  for (let i = 0; i < masks.length; i++) {
    const y = yRest + masks[i][0];
    rows[y] |= masks[i][1];
    touched.push([y, masks[i][1]]);
  }

  // Absolute [y, x] of the four cells, before any clearing. Feature code
  // reconstructs all landing geometry from this.
  const pieceCells = PIECE_CELLS[piece][rot].map(([dx, dy]) => [yRest + dy, x + dx]);

  const clearedRows = [];
  for (let i = 0; i < touched.length; i++) {
    if (rows[touched[i][0]] === FULL_ROW) clearedRows.push(touched[i][0]);
  }
  const n = clearedRows.length;

  let erodedCells = 0;
  let newRows = rows;
  if (n) {
    clearedRows.sort((a, b) => a - b);
    const cleared = new Set(clearedRows);
    for (let i = 0; i < touched.length; i++) {
      if (cleared.has(touched[i][0])) erodedCells += popcount10(touched[i][1]);
    }
    const kept = [];
    for (let y = 0; y < ROWS; y++) if (!cleared.has(y)) kept.push(rows[y]);
    newRows = new Array(n).fill(0).concat(kept);
  }

  const levelBefore = state.level;
  const [scoreDelta, b2bNext, comboNext, b2bApplied] =
    scoreClear(n, levelBefore, state.b2b, state.combo);

  const nxt = state.clone();
  nxt.rows = newRows;
  nxt.lines = state.lines + n;
  nxt.level = 1 + Math.floor(nxt.lines / LINES_PER_LEVEL);
  nxt.pieces = state.pieces + 1;
  nxt.score = state.score + scoreDelta;
  nxt.b2b = b2bNext;
  nxt.combo = comboNext;
  nxt.can_hold = true;

  const spawned = spawnNext(nxt);

  // Raw geometry only. Feature definitions live in rl/features.py; a second
  // copy of a feature formula here would guarantee the two drift apart.
  const info = {
    lines_cleared: n,
    cleared_rows: clearedRows,
    game_over: !spawned,
    piece_cells: pieceCells,
    cleared_piece_cells: erodedCells,
    eroded_piece_cells: n * erodedCells,
    landing_row_top: yRest + entry.minDy,
    landing_row_bottom: yRest + entry.maxDy,
    score_delta: scoreDelta,
    is_tetris: n === B2B_LINES,
    b2b_active: b2bApplied,
    b2b_chain: b2bNext,
    combo_count: comboNext,
    level: levelBefore,
    total_lines: nxt.lines,
    piece,
    rot,
    x,
    y: yRest,
  };
  if (spawned && isStuck(nxt)) {
    nxt.game_over = true;
    info.game_over = true;
  }
  return [nxt, info];
}

// ---------------------------------------------------------------------------
// human path -- mutates state in place
// ---------------------------------------------------------------------------

/**
 * Reset the lock-delay countdown after a successful move or rotation.
 *
 * Only while resting, and only up to LOCK_RESET_LIMIT times per piece -- that
 * bound stops infinite spinning without taking tuck and slide away from the
 * player. Once the budget is spent the timer runs to expiry.
 */
/**
 * Reset the lock timer only if the piece reached a NEW deepest row.
 * The other half of the infinite-spin defence -- and the half that was missing.
 */
function descendReset(state) {
  if (state.y > state.lowest_y) {
    state.lowest_y = state.y;
    state.lock_ms = 0;
  }
}

function touchLockTimer(state) {
  if (state.lock_resets >= LOCK_RESET_LIMIT) return;
  if (fits(state.rows, state.current, state.rot, state.x, state.y + 1)) return;
  state.lock_ms = 0;
  state.lock_resets += 1;
}

function move(state, dx) {
  if (state.game_over || state.current === null) return false;
  if (fits(state.rows, state.current, state.rot, state.x + dx, state.y)) {
    state.x += dx;
    touchLockTimer(state);
    return true;
  }
  return false;
}

function rotate(state, cw = true) {
  if (state.game_over || state.current === null) return false;
  const piece = state.current;
  const frm = state.rot;
  const to = cw ? (frm + 1) % 4 : (frm + 3) % 4;
  if (piece === PIECE_O) { state.rot = to; touchLockTimer(state); return true; }
  const offs = kickOffsets(piece, frm, to);
  for (let i = 0; i < offs.length; i++) {
    const nx = state.x + offs[i][0];
    const ny = state.y + offs[i][1];
    if (fits(state.rows, piece, to, nx, ny)) {
      state.rot = to; state.x = nx; state.y = ny;
      touchLockTimer(state);
      return true;
    }
  }
  return false;
}

function softDrop(state) {
  if (state.game_over || state.current === null) return false;
  if (fits(state.rows, state.current, state.rot, state.x, state.y + 1)) {
    state.y += 1;
    state.score += SOFT_DROP_POINTS_PER_CELL;
    descendReset(state);
    return true;
  }
  return false;
}

/** Cells between the active piece and its hard-drop resting position. */
function dropDistance(state) {
  let d = 0;
  while (fits(state.rows, state.current, state.rot, state.x, state.y + d + 1)) d++;
  return d;
}

/** Resting row of the active piece -- for the UI drop preview. */
function ghostY(state) {
  return state.y + dropDistance(state);
}

function hardDrop(state) {
  if (state.game_over || state.current === null) return null;
  const d = dropDistance(state);
  state.y += d;
  state.score += HARD_DROP_POINTS_PER_CELL * d;
  return lock(state);          // hard drop always locks immediately
}

/** One cell of gravity. Locks on landing and returns info, else null. */
function tick(state) {
  if (state.game_over || state.current === null) return null;
  if (fits(state.rows, state.current, state.rot, state.x, state.y + 1)) {
    state.y += 1;
    return null;
  }
  return lock(state);
}

function lock(state) {
  if (state.game_over || state.current === null) return null;
  const p = [state.rot, state.x, state.y, state.current];
  const [nxt, info] = applyPlacement(state, p);
  state.rows = nxt.rows;
  state.current = nxt.current;
  state.rot = nxt.rot;
  state.x = nxt.x;
  state.y = nxt.y;
  state.queue = nxt.queue;
  state.hold = nxt.hold;
  state.can_hold = nxt.can_hold;
  state.rng = nxt.rng;
  state.score = nxt.score;      // already includes info.score_delta
  state.lines = nxt.lines;
  state.level = nxt.level;
  state.pieces = nxt.pieces;
  state.game_over = nxt.game_over;
  return info;
}

/**
 * Swap the active piece with the hold slot. Once per spawn.
 * Returns false in modes without a hold slot -- a refusal, not an error.
 */
function hold(state) {
  if (!holdEnabled(state)) return false;
  if (state.game_over || state.current === null || !state.can_hold) return false;
  const cur = state.current;
  if (state.hold === null) {
    state.hold = cur;
    if (!spawnNext(state)) return true;
  } else {
    const swapped = state.hold;
    state.hold = cur;
    state.current = swapped;
    state.rot = 0;
    state.x = SPAWN_X[swapped];
    state.y = SPAWN_Y;
    const masks = SPAWN_MASKS[swapped];
    for (let i = 0; i < masks.length; i++) {
      if (state.rows[masks[i][0]] & masks[i][1]) { state.game_over = true; break; }
    }
  }
  state.can_hold = false;
  return true;
}

/**
 * Advance the interactive game by `dtMs` real milliseconds.
 *
 * Call this once per animation frame; it folds gravity and lock delay
 * together. Returns the lock `info` on the frame the piece locks, else null.
 *
 * Lock delay is LOCK_DELAY_MS while the piece rests on the stack, so tuck and
 * slide work. Moves and rotations restart the countdown up to
 * LOCK_RESET_LIMIT times per piece; after that it expires on schedule.
 *
 * docs/spec.md section 5. Interactive path only -- legalPlacements enumerates
 * hard drops and is unaffected by lock delay.
 */
function tickMs(state, dtMs) {
  if (state.game_over || state.current === null) return null;

  let resting = !fits(state.rows, state.current, state.rot, state.x, state.y + 1);

  if (!resting) {
    // Gravity first, so downward progress this frame counts.
    state.grav_ms += dtMs;
    const step = gravityIntervalMs(state.level);
    while (state.grav_ms >= step) {
      if (!fits(state.rows, state.current, state.rot, state.x, state.y + 1)) break;
      state.y += 1;
      state.grav_ms -= step;
    }
    descendReset(state);
    resting = !fits(state.rows, state.current, state.rot, state.x, state.y + 1);
  }

  if (resting) state.touched_down = true;

  // The lock timer runs once the piece has touched down -- and it KEEPS running
  // even on frames where a kick has lifted the piece off the stack.
  //
  // Two bugs lived here, both found by web's 20,000-cycle rotate-spam:
  //   1. The airborne branch used to zero lock_ms unconditionally, bypassing
  //      the LOCK_RESET_LIMIT budget (which lives in touchLockTimer and only
  //      applies while resting).
  //   2. Gating the timer on `resting` alone was still not enough: spamming
  //      rotation lifts the piece EVERY frame, so it was never resting and the
  //      timer never advanced at all.
  // Hence touched_down: once grounded, time always moves toward the lock.
  if (!state.touched_down) return null;

  state.lock_ms += dtMs;
  if (state.lock_ms >= LOCK_DELAY_MS) {
    // Expiry while a kick holds the piece aloft must not freeze a floating
    // block: settle it to its resting row first. No drop points -- this is the
    // clock running out, not a player hard drop.
    state.y += dropDistance(state);
    return lock(state);
  }
  return null;
}

/** 0..1 fraction of the lock delay elapsed; 0 while the piece is airborne. */
function lockDelayProgress(state) {
  if (state.game_over || state.current === null) return 0;
  if (fits(state.rows, state.current, state.rot, state.x, state.y + 1)) return 0;
  return Math.min(1, state.lock_ms / LOCK_DELAY_MS);
}

// ---------------------------------------------------------------------------
// coordinate helpers
// ---------------------------------------------------------------------------

/**
 * Absolute leftmost occupied column of a placement.
 *
 * `placement[1]` is the bounding-box origin, which is what the cell tables
 * multiply against. Anything that wants "the leftmost filled column" -- feature
 * code, or a UI animating a piece into position -- should call this instead of
 * reimplementing the offset, which is the classic off-by-one here.
 */
function placementLeftCol(p) {
  return p[1] + MIN_DX[p[3]][p[0]];
}

/** Absolute [[y, x] x4] a placement would occupy, before clearing. */
function placementCells(p) {
  const [rot, x, yRest, piece] = p;
  return PIECE_CELLS[piece][rot].map(([dx, dy]) => [yRest + dy, x + dx]);
}

/** Absolute [[y, x] x4] the active piece occupies right now. */
function activeCells(state) {
  if (state.current === null) return [];
  return PIECE_CELLS[state.current][state.rot]
    .map(([dx, dy]) => [state.y + dy, state.x + dx]);
}

/** Absolute [[y, x] x4] where the active piece would land on a hard drop. */
function ghostCells(state) {
  if (state.current === null) return [];
  const gy = ghostY(state);
  return PIECE_CELLS[state.current][state.rot]
    .map(([dx, dy]) => [gy + dy, state.x + dx]);
}

// ---------------------------------------------------------------------------
// Game -- convenience wrapper for the UI
//
// Wraps a State with the frame-loop bookkeeping and read accessors the UI
// needs, so ui.js never has to reimplement a rule. The rules all live in the
// functions above; this class only calls them.
// ---------------------------------------------------------------------------

class Game {
  /** @param {{seed?: number}} opts */
  constructor(opts = {}) {
    this.seed = opts.seed === undefined ? 1 : opts.seed;
    this.difficulty = opts.difficulty === undefined
      ? DIFFICULTY_DEFAULT : opts.difficulty;
    this.reset(this.seed);
  }

  reset(seed, difficulty) {
    if (seed !== undefined) this.seed = seed;
    if (difficulty !== undefined) this.difficulty = difficulty;
    this.state = newGame(this.seed, this.difficulty);
    /** Rows cleared by the most recent lock, for the flash animation. */
    this.lastClearedRows = [];
    /** Cells the most recent lock filled, absolute [y, x]. */
    this.lastLockCells = [];
    this.lastInfo = null;
    return this;
  }

  // --- frame loop --------------------------------------------------------

  /** Advance by dtMs. Returns lock info on the locking frame, else null. */
  tick(dtMs) {
    const info = tickMs(this.state, dtMs);
    if (info) this._recordLock(info);
    return info;
  }

  _recordLock(info) {
    this.lastInfo = info;
    this.lastClearedRows = info.cleared_rows;
    this.lastLockCells = info.piece_cells;
  }

  // --- input -------------------------------------------------------------

  moveLeft() { return move(this.state, -1); }
  moveRight() { return move(this.state, 1); }
  rotateCW() { return rotate(this.state, true); }
  rotateCCW() { return rotate(this.state, false); }
  softDrop() { return softDrop(this.state); }

  hardDrop() {
    const info = hardDrop(this.state);
    if (info) this._recordLock(info);
    return info;
  }

  hold() { return hold(this.state); }

  // --- read accessors (called every frame; none of these allocate a board) --

  /** 22 ints, bit x of rows[y]. Visible rows are y = BUFFER_ROWS..ROWS-1. */
  get rows() { return this.state.rows; }
  /** True if the cell at (y, x) in *board* coords is filled. */
  cell(y, x) { return (this.state.rows[y] >> x) & 1; }

  get active() {
    const s = this.state;
    if (s.current === null) return null;
    return { type: s.current, rot: s.rot, r: s.y, c: s.x, cells: activeCells(s) };
  }

  get ghostCells() { return ghostCells(this.state); }
  /**
   * Upcoming pieces the current mode reveals. Empty in extreme mode -- the UI
   * simply draws no preview panel there, which is not an error condition, so
   * this getter swallows NextPeekBlocked. Agent code must call visibleNext()
   * directly and handle the throw.
   */
  get nextQueue() {
    if (nextVisibleCount(this.state) === 0) return [];
    return visibleNext(this.state);
  }

  /** How many previews this mode shows; 0 means draw no preview panel. */
  get nextVisibleCount() { return nextVisibleCount(this.state); }
  /** Whether to draw a hold slot at all. */
  get holdEnabled() { return holdEnabled(this.state); }
  get difficultyName() { return difficultyName(this.state); }

  get heldType() { return this.state.hold; }
  get holdLocked() { return !this.state.can_hold; }
  get score() { return this.state.score; }
  get lines() { return this.state.lines; }
  get level() { return this.state.level; }
  get pieces() { return this.state.pieces; }
  get gameOver() { return this.state.game_over; }
  get lockDelayProgress() { return lockDelayProgress(this.state); }

  // --- agent bridge ------------------------------------------------------

  /** The placement-API state. Same object -- the agent path never mutates it. */
  toState() { return this.state; }

  legalPlacements() { return legalPlacements(this.state); }

  /**
   * Apply a placement instantly (no animation). Use for AI panels that do not
   * need to show the piece travelling.
   */
  applyPlacement(p) {
    const [ns, info] = applyPlacement(this.state, p);
    this.state = ns;
    this._recordLock(info);
    return info;
  }

  /**
   * Steer the active piece one step toward placement `p`, for animated AI
   * replay: rotate first, then walk horizontally, then hard drop.
   *
   * Returns what HAPPENED, not what was attempted:
   *   'rotate'  | rotated one step toward the target
   *   'move'    | moved one column toward the target
   *   'drop'    | reached the target and hard dropped; the piece is locked
   *   'blocked' | could not progress, so it hard dropped WHERE IT STOOD;
   *              the piece is locked but NOT at the requested placement
   *   'done'    | nothing to do (no active piece, or the game is over)
   *
   * Every return value except 'done' leaves the game strictly advanced, so a
   * caller that loops on this cannot hang -- see below for why that matters.
   *
   * Use this rather than driving rot/x yourself: `p[1]` is the bounding-box
   * origin, not the leftmost cell, so hand-rolled animation lands a column off.
   *
   * Why 'blocked' exists
   * --------------------
   * `legalPlacements` enumerates VERTICAL hard drops from above (docs/spec.md
   * section 8: no tucks, no spins under an overhang). This animator uses a
   * different reachability model -- rotate, slide along the spawn row, then
   * drop. Once the stack reaches the spawn row that slide is blocked, yet the
   * target placement is still perfectly legal from above. The two models
   * disagree, and only the animator can notice.
   *
   * This used to discard the rotate()/move() return values. Those functions
   * refuse and change nothing when blocked (correctly), so `s.x` never reached
   * the target and this returned 'move' forever: the PLAY screen's AI mode
   * froze, game_over never became true, and the frame loop spun ~40 no-ops per
   * frame with no error anywhere. checker caught it in a real browser.
   *
   * The fix hard drops on a block rather than only reporting it. Reporting
   * alone would leave the hang one ignored return value away, and this is the
   * infinite-loop-shaped failure mode -- fail-safe beats fail-informative here.
   * The cost is honest and bounded: the piece lands somewhere the policy did
   * not choose, and the caller is told via 'blocked'. On a board this full the
   * next spawn tops out almost immediately anyway, which is the correct
   * outcome for a lost board.
   */
  stepToward(p) {
    const s = this.state;
    if (s.current === null || s.game_over) return 'done';
    const [rot, x] = p;
    if (s.rot !== rot) {
      if (rotate(s, true)) return 'rotate';
      return this._commitBlocked();
    }
    if (s.x !== x) {
      if (move(s, s.x < x ? 1 : -1)) return 'move';
      return this._commitBlocked();
    }
    this.hardDrop();
    return 'drop';
  }

  /** Lock where the piece stands because the target is unreachable. */
  _commitBlocked() {
    this.hardDrop();
    return 'blocked';
  }
}

/** Fresh interactive game. `createGame({seed})` mirrors the web request. */
function createGame(opts = {}) {
  return new Game(opts);
}

/** The placement-API state behind a Game. */
function stateFromGame(game) {
  return game.state;
}

/** Standalone RNG handle, for verifying two panels share a piece sequence. */
function makeRng(seed) {
  let st = seedState(seed);
  return {
    next() { st = nextU32(st); return st; },
    bag() { const [ns, b] = nextBag(st); st = ns; return b; },
    get state() { return st; },
  };
}

// ---------------------------------------------------------------------------
// hashing (docs/spec.md section 7)
// ---------------------------------------------------------------------------

const FNV_PRIME = FNV_PRIME_32;
const FNV_OFFSET = FNV_OFFSET_32;

/** FNV-1a 32 over the 22 rows, low byte then high byte. */
function boardHash(rows) {
  let h = FNV_OFFSET >>> 0;
  for (let y = 0; y < ROWS; y++) {
    const r = rows[y];
    h = Math.imul(h ^ (r & 0xFF), FNV_PRIME) >>> 0;
    h = Math.imul(h ^ ((r >>> 8) & 0xFF), FNV_PRIME) >>> 0;
  }
  return h >>> 0;
}

/** boardHash extended with the piece/hold/rng/progress fields. */
function stateHash(state) {
  let h = boardHash(state.rows);
  const extra = [
    state.current === null ? 7 : state.current,
    state.rot,
    state.hold === null ? 7 : state.hold,
    state.can_hold ? 1 : 0,
    state.rng >>> 0,
    state.lines,
    state.level,
    state.b2b,
    state.combo,
  ];
  for (let i = 0; i < extra.length; i++) {
    const v = extra[i] >>> 0;
    for (const shift of [0, 8, 16, 24]) {
      h = Math.imul(h ^ ((v >>> shift) & 0xFF), FNV_PRIME) >>> 0;
    }
  }
  return h >>> 0;
}

/** ASCII board, for debugging only. Does not draw the active piece. */
function renderAscii(state, includeBuffer = false) {
  const start = includeBuffer ? 0 : BUFFER_ROWS;
  const out = [];
  for (let y = start; y < ROWS; y++) {
    let line = '|';
    for (let x = 0; x < W; x++) line += ((state.rows[y] >> x) & 1) ? '#' : '.';
    out.push(line + '|');
  }
  out.push('+' + '-'.repeat(W) + '+');
  return out.join('\n');
}

// ---------------------------------------------------------------------------
// snake_case aliases -- identical names to engine.py, for cross-checking
// ---------------------------------------------------------------------------

const legal_placements = legalPlacements;
const apply_placement = applyPlacement;
const new_game = newGame;
const board_hash = boardHash;
const state_hash = stateHash;
/**
 * Removed -- this name returned row indices, not heights. See columnTops.
 * A silent alias would preserve the trap it was renamed to avoid.
 */
function columnHeights() {
  throw new Error(
    'columnHeights was renamed to columnTops because it returns the topmost ' +
    'FILLED ROW INDEX (empty column = ROWS = 22), not a height. Row indices ' +
    'grow downward. For a height use ROWS - columnTops(rows)[c]. Do not ' +
    'confuse this with rl/features.py:column_heights, which returns heights.');
}
const column_tops = columnTops;
const frames_per_cell = framesPerCell;
const soft_drop = softDrop;
const hard_drop = hardDrop;
const drop_distance = dropDistance;
const ghost_y = ghostY;
const kick_offsets = kickOffsets;
const seed_state = seedState;
const next_u32 = nextU32;
const next_bag = nextBag;
const from_dict = stateFromObject;
const visible_next = visibleNext;
const next_visible_count = nextVisibleCount;
const hold_enabled = holdEnabled;
const difficulty_name = difficultyName;
const tick_ms = tickMs;
const lock_delay_progress = lockDelayProgress;
const placement_left_col = placementLeftCol;
const placement_cells = placementCells;
const gravity_interval_ms = gravityIntervalMs;

  var TetrisEngine = {
    B2B_LINES: B2B_LINES,
    B2B_MULT_DEN: B2B_MULT_DEN,
    B2B_MULT_NUM: B2B_MULT_NUM,
    BAG_SIZE: BAG_SIZE,
    BOTTOM_PROFILE: BOTTOM_PROFILE,
    BOTTOM_ROW: BOTTOM_ROW,
    BOX_SIZE: BOX_SIZE,
    BUFFER_ROWS: BUFFER_ROWS,
    COMBO_BONUS_PER_STEP: COMBO_BONUS_PER_STEP,
    DEFAULT_RNG_STATE: DEFAULT_RNG_STATE,
    DIFFICULTY_DEFAULT: DIFFICULTY_DEFAULT,
    DIFFICULTY_EXTREME: DIFFICULTY_EXTREME,
    DIFFICULTY_HARD: DIFFICULTY_HARD,
    DIFFICULTY_HOLD_ENABLED: DIFFICULTY_HOLD_ENABLED,
    DIFFICULTY_NAMES: DIFFICULTY_NAMES,
    DIFFICULTY_NEXT_VISIBLE: DIFFICULTY_NEXT_VISIBLE,
    DIFFICULTY_NORMAL: DIFFICULTY_NORMAL,
    FNV_OFFSET_32: FNV_OFFSET_32,
    FNV_PRIME_32: FNV_PRIME_32,
    FULL_ROW: FULL_ROW,
    GRAVITY_L1_L10: GRAVITY_L1_L10,
    GRAVITY_MIN: GRAVITY_MIN,
    GRAVITY_TAIL: GRAVITY_TAIL,
    GUARD_ROWS: GUARD_ROWS,
    Game: Game,
    HARD_DROP_POINTS_PER_CELL: HARD_DROP_POINTS_PER_CELL,
    I: I,
    J: J,
    KICKS: KICKS,
    KICKS_I: KICKS_I,
    KICKS_JLSTZ: KICKS_JLSTZ,
    KICKS_NONE: KICKS_NONE,
    L: L,
    LINES_PER_LEVEL: LINES_PER_LEVEL,
    LOCK_DELAY_MS: LOCK_DELAY_MS,
    LOCK_RESET_LIMIT: LOCK_RESET_LIMIT,
    MASK32: MASK32,
    MAX_DX: MAX_DX,
    MAX_DY: MAX_DY,
    MAX_PIECE_VEXTENT: MAX_PIECE_VEXTENT,
    MIN_DX: MIN_DX,
    MIN_DY: MIN_DY,
    MS_PER_FRAME: MS_PER_FRAME,
    NEXT_VISIBLE: NEXT_VISIBLE,
    NextPeekBlocked: NextPeekBlocked,
    O: O,
    PIECE_CELLS: PIECE_CELLS,
    PIECE_NAMES: PIECE_NAMES,
    PLACEMENT_COUNT: PLACEMENT_COUNT,
    QUEUE_MIN: QUEUE_MIN,
    ROWS: ROWS,
    S: S,
    SCORE_TABLE: SCORE_TABLE,
    SOFT_DROP_POINTS_PER_CELL: SOFT_DROP_POINTS_PER_CELL,
    SPAWN_ROT: SPAWN_ROT,
    SPAWN_X: SPAWN_X,
    SPAWN_Y: SPAWN_Y,
    State: State,
    T: T,
    UNIQUE_ROTS: UNIQUE_ROTS,
    VISIBLE_ROWS: VISIBLE_ROWS,
    W: W,
    XORSHIFT_FALLBACK_STATE: XORSHIFT_FALLBACK_STATE,
    X_RANGE: X_RANGE,
    Z: Z,
    activeCells: activeCells,
    applyPlacement: applyPlacement,
    apply_placement: apply_placement,
    boardHash: boardHash,
    board_hash: board_hash,
    columnHeights: columnHeights,
    columnTops: columnTops,
    column_tops: column_tops,
    createGame: createGame,
    difficultyName: difficultyName,
    difficulty_name: difficulty_name,
    dropDistance: dropDistance,
    drop_distance: drop_distance,
    emptyRows: emptyRows,
    fitsForParity: fitsForParity,
    framesPerCell: framesPerCell,
    frames_per_cell: frames_per_cell,
    from_dict: from_dict,
    ghostCells: ghostCells,
    ghostY: ghostY,
    ghost_y: ghost_y,
    gravityIntervalMs: gravityIntervalMs,
    gravity_interval_ms: gravity_interval_ms,
    hardDrop: hardDrop,
    hard_drop: hard_drop,
    hold: hold,
    holdEnabled: holdEnabled,
    hold_enabled: hold_enabled,
    kickOffsets: kickOffsets,
    kick_offsets: kick_offsets,
    legalPlacements: legalPlacements,
    legal_placements: legal_placements,
    lock: lock,
    lockDelayProgress: lockDelayProgress,
    lock_delay_progress: lock_delay_progress,
    makeRng: makeRng,
    move: move,
    newGame: newGame,
    new_game: new_game,
    nextBag: nextBag,
    nextU32: nextU32,
    nextVisibleCount: nextVisibleCount,
    next_bag: next_bag,
    next_u32: next_u32,
    next_visible_count: next_visible_count,
    placementCells: placementCells,
    placementLeftCol: placementLeftCol,
    placement_cells: placement_cells,
    placement_left_col: placement_left_col,
    renderAscii: renderAscii,
    rotate: rotate,
    scoreClearForParity: scoreClearForParity,
    seedState: seedState,
    seed_state: seed_state,
    softDrop: softDrop,
    soft_drop: soft_drop,
    stateFromGame: stateFromGame,
    stateFromObject: stateFromObject,
    stateHash: stateHash,
    stateToObject: stateToObject,
    state_hash: state_hash,
    tick: tick,
    tickMs: tickMs,
    tick_ms: tick_ms,
    visibleNext: visibleNext,
    visible_next: visible_next
  };

  global.TetrisEngine = TetrisEngine;
  if (typeof module !== 'undefined' && module.exports) module.exports = TetrisEngine;
})(typeof window !== 'undefined' ? window : globalThis);
