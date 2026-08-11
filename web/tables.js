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

export const W = 10;
export const VISIBLE_ROWS = 20;
export const BUFFER_ROWS = 2;
export const ROWS = 22;
export const BOTTOM_ROW = 21;
export const FULL_ROW = 1023;
export const PIECE_NAMES = ['I', 'O', 'T', 'S', 'Z', 'J', 'L'];
export const BOX_SIZE = [4, 2, 3, 3, 3, 3, 3];
export const PIECE_CELLS = [
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
export const UNIQUE_ROTS = [[0, 1], [0], [0, 1, 2, 3], [0, 1], [0, 1], [0, 1, 2, 3], [0, 1, 2, 3]];
export const SPAWN_Y = 0;
export const SPAWN_X = [3, 4, 3, 3, 3, 3, 3];
export const SPAWN_ROT = 0;
export const SCORE_TABLE = [0, 100, 300, 500, 800];
export const SOFT_DROP_POINTS_PER_CELL = 1;
export const HARD_DROP_POINTS_PER_CELL = 2;
export const LINES_PER_LEVEL = 10;
export const GRAVITY_L1_L10 = [48, 43, 38, 33, 28, 23, 18, 13, 8, 6];
export const GRAVITY_TAIL = [[13, 5], [16, 4], [19, 3], [28, 2]];
export const GRAVITY_MIN = 1;
export const LOCK_DELAY_MS = 500;
export const LOCK_RESET_LIMIT = 15;
export const B2B_LINES = 4;
export const B2B_MULT_NUM = 3;
export const B2B_MULT_DEN = 2;
export const COMBO_BONUS_PER_STEP = 50;
export const DIFFICULTY_NORMAL = 0;
export const DIFFICULTY_HARD = 1;
export const DIFFICULTY_EXTREME = 2;
export const DIFFICULTY_NAMES = ['normal', 'hard', 'extreme'];
export const DIFFICULTY_NEXT_VISIBLE = [5, 1, 0];
export const DIFFICULTY_HOLD_ENABLED = [true, false, false];
export const DIFFICULTY_DEFAULT = 0;
export const KICKS_JLSTZ = {
  '0,1': [[0, 0], [-1, 0], [-1, -1], [0, 2], [-1, 2]],
  '1,0': [[0, 0], [1, 0], [1, 1], [0, -2], [1, -2]],
  '1,2': [[0, 0], [1, 0], [1, 1], [0, -2], [1, -2]],
  '2,1': [[0, 0], [-1, 0], [-1, -1], [0, 2], [-1, 2]],
  '2,3': [[0, 0], [1, 0], [1, -1], [0, 2], [1, 2]],
  '3,2': [[0, 0], [-1, 0], [-1, 1], [0, -2], [-1, -2]],
  '3,0': [[0, 0], [-1, 0], [-1, 1], [0, -2], [-1, -2]],
  '0,3': [[0, 0], [1, 0], [1, -1], [0, 2], [1, 2]]
};
export const KICKS_I = {
  '0,1': [[0, 0], [-2, 0], [1, 0], [-2, 1], [1, -2]],
  '1,0': [[0, 0], [2, 0], [-1, 0], [2, -1], [-1, 2]],
  '1,2': [[0, 0], [-1, 0], [2, 0], [-1, -2], [2, 1]],
  '2,1': [[0, 0], [1, 0], [-2, 0], [1, 2], [-2, -1]],
  '2,3': [[0, 0], [2, 0], [-1, 0], [2, -1], [-1, 2]],
  '3,2': [[0, 0], [-2, 0], [1, 0], [-2, 1], [1, -2]],
  '3,0': [[0, 0], [1, 0], [-2, 0], [1, 2], [-2, -1]],
  '0,3': [[0, 0], [-1, 0], [2, 0], [-1, -2], [2, 1]]
};
export const KICKS_NONE = [[0, 0]];
export const MIN_DX = [[0, 2, 0, 1], [0, 0, 0, 0], [0, 1, 0, 0], [0, 1, 0, 0], [0, 1, 0, 0], [0, 1, 0, 0], [0, 1, 0, 0]];
export const MAX_DX = [[3, 2, 3, 1], [1, 1, 1, 1], [2, 2, 2, 1], [2, 2, 2, 1], [2, 2, 2, 1], [2, 2, 2, 1], [2, 2, 2, 1]];
export const MIN_DY = [[1, 0, 2, 0], [0, 0, 0, 0], [0, 0, 1, 0], [0, 0, 1, 0], [0, 0, 1, 0], [0, 0, 1, 0], [0, 0, 1, 0]];
export const MAX_DY = [[1, 3, 2, 3], [1, 1, 1, 1], [1, 2, 2, 2], [1, 2, 2, 2], [1, 2, 2, 2], [1, 2, 2, 2], [1, 2, 2, 2]];
export const BOTTOM_PROFILE = [
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
export const X_RANGE = [
  [[0, 7], [-2, 8], [0, 7], [-1, 9]],
  [[0, 9], [0, 9], [0, 9], [0, 9]],
  [[0, 8], [-1, 8], [0, 8], [0, 9]],
  [[0, 8], [-1, 8], [0, 8], [0, 9]],
  [[0, 8], [-1, 8], [0, 8], [0, 9]],
  [[0, 8], [-1, 8], [0, 8], [0, 9]],
  [[0, 8], [-1, 8], [0, 8], [0, 9]]
];
export const PLACEMENT_COUNT = [[7, 10, 7, 10], [9, 9, 9, 9], [8, 9, 8, 9], [8, 9, 8, 9], [8, 9, 8, 9], [8, 9, 8, 9], [8, 9, 8, 9]];
export const MAX_PIECE_VEXTENT = 4;
export const GUARD_ROWS = 4;
export const FNV_OFFSET_32 = 2166136261;
export const FNV_PRIME_32 = 16777619;
export const MASK32 = 4294967295;
export const XORSHIFT_FALLBACK_STATE = 2654435769;
export const BAG_SIZE = 7;
export const QUEUE_MIN = 7;
export const NEXT_VISIBLE = 5;

// piece index constants
export const I = 0;
export const O = 1;
export const T = 2;
export const S = 3;
export const Z = 4;
export const J = 5;
export const L = 6;

/** KICKS[piece] -> kick table, or null for O (which never needs one). */
export const KICKS = [KICKS_I, null, KICKS_JLSTZ, KICKS_JLSTZ, KICKS_JLSTZ,
                      KICKS_JLSTZ, KICKS_JLSTZ];
