/* ============================================================
   policies.js — afterstate features, weight loading, policies
   Owner: web        ES module.

   CONTRACT SOURCES (read these, do not re-derive):
     docs/spec.md    board is a 22-entry BITMASK array `rows`;
                     bit x of rows[y] is column x. y=0,1 are the
                     hidden spawn buffer, y=2..21 are visible.
                     piece idx 0..6 = I,O,T,S,Z,J,L
     rl/features.py  the 8 afterstate features, normative.
                     Computed on the VISIBLE 20 rows only.

   The six board-only features here were verified against
   rl/features.py two ways: all five `_parity_fixtures`, and 300
   random boards cross-checked against the Python implementation
   (300/300 exact). `selfTest()` re-runs the fixtures in-browser.
   ============================================================ */

import * as E from './engine.js';

export const ROWS = 22, VIS = 20, COLS = 10, FULL = 0x3FF;

/* normative order — matches rl FEATURE_NAMES; weight vectors
   and feature_scale are both in this order */
export const FEATURE_ORDER = [
  'landing_height', 'eroded_piece_cells',
  'row_transitions', 'column_transitions',
  'holes', 'cumulative_wells',
  'aggregate_height', 'bumpiness',
];

/* rl's "wells10" set = the 8 above plus these two (rl/features.py
   WELLS_EXTRA). They exist because cumulative_wells aggregates all
   wells into one triangular sum, which cannot tell "one deliberate
   9-deep shaft" from "nine shallow notches" — the two boards can
   score alike. A tetris strategy needs exactly that distinction.

   Both derive from the SAME well-cell test as cumulative_wells:
   an empty cell whose left and right neighbours are filled, walls
   counting as filled; vertically contiguous well cells form one
   well whose depth is the run length. */
export const WELLS_EXTRA = ['max_well_depth', 'well_count'];

/* Every feature this file can compute. buildFeaturePlan validates
   a weight file's `features` array against this, and refuses to
   load anything naming a feature we cannot produce. */
export const ALL_FEATURES = [...FEATURE_ORDER, ...WELLS_EXTRA];

/* well_count counts only wells at least this deep. Counting every
   one-cell notch would just re-measure surface roughness, which
   bumpiness already does; depth 3 is where a well becomes a
   commitment worth keeping. */
const WELL_COUNT_MIN_DEPTH = 3;

function popcount(v) {
  v = v - ((v >> 1) & 0x55555555);
  v = (v & 0x33333333) + ((v >> 2) & 0x33333333);
  return (((v + (v >> 4)) & 0x0F0F0F0F) * 0x01010101) >> 24;
}

/* scratch buffers — the hot loop must not allocate */
const _depth = new Int32Array(COLS);
const _heights = new Int32Array(COLS);
const _feat = new Float64Array(10);   // 8 dellacherie + 2 wells
const _scaled = new Float64Array(10);

/* ------------------------------------------------------------
   The six board-only features, one pass over the visible rows.
   Fills _feat[2..7]; slots 0..1 come from the engine's info.
   ------------------------------------------------------------ */
export function boardFeatures(rows, off) {
  let rowTrans = 0, colTrans = 0, holesN = 0, wells = 0;
  /* well run statistics, collected in the same pass: a run ends
     exactly where _depth[c] resets to 0, so no second scan */
  let maxWell = 0, wellCnt = 0;
  let covered = 0;   // columns with a filled cell strictly above
  let prev = 0;      // ceiling counts as EMPTY
  let c;

  for (c = 0; c < COLS; c++) { _depth[c] = 0; _heights[c] = 0; }

  for (let r = 0; r < VIS; r++) {
    const v = rows[off + r] & FULL;

    /* row_transitions — both walls FILLED. p: bit0 = left wall,
       bits 1..10 = cells, bit 11 = right wall; the 11 adjacent
       pairs live in bits 0..10 of p ^ (p>>1). An empty row
       correctly contributes 2. */
    const p = (v << 1) | 1 | 0x800;
    rowTrans += popcount((p ^ (p >> 1)) & 0x7FF);

    /* column_transitions — vertical adjacency for all 10 columns
       at once. Ceiling EMPTY, floor FILLED (added after loop). */
    colTrans += popcount((v ^ prev) & FULL);

    /* holes — empty cell with something filled above */
    holesN += popcount(covered & ~v & FULL);

    /* column height is fixed by the topmost filled row */
    const newly = v & ~covered & FULL;
    if (newly) {
      for (c = 0; c < COLS; c++) if (newly & (1 << c)) _heights[c] = VIS - r;
    }

    /* cumulative_wells — empty with BOTH neighbours filled (walls
       count filled), triangular weighting via running depth */
    const L = ((v << 1) | 1) & FULL;
    const R = ((v >> 1) | 0x200) & FULL;
    const wm = (~v) & L & R & FULL;
    if (wm || _depth[0] || _depth[1] || _depth[2] || _depth[3] || _depth[4] ||
        _depth[5] || _depth[6] || _depth[7] || _depth[8] || _depth[9]) {
      for (c = 0; c < COLS; c++) {
        if (wm & (1 << c)) { _depth[c]++; wells += _depth[c]; }
        else {
          /* run just ended — record it before clearing */
          if (_depth[c]) {
            if (_depth[c] > maxWell) maxWell = _depth[c];
            if (_depth[c] >= WELL_COUNT_MIN_DEPTH) wellCnt++;
            _depth[c] = 0;
          }
        }
      }
    }

    covered |= v;
    prev = v;
  }

  /* runs still open at the floor are wells too */
  for (c = 0; c < COLS; c++) {
    if (_depth[c]) {
      if (_depth[c] > maxWell) maxWell = _depth[c];
      if (_depth[c] >= WELL_COUNT_MIN_DEPTH) wellCnt++;
    }
  }

  colTrans += popcount((prev ^ FULL) & FULL);   // floor is FILLED

  let agg = 0, bump = 0;
  for (c = 0; c < COLS; c++) agg += _heights[c];
  for (c = 0; c + 1 < COLS; c++) bump += Math.abs(_heights[c] - _heights[c + 1]);

  _feat[2] = rowTrans; _feat[3] = colTrans;
  _feat[4] = holesN;   _feat[5] = wells;
  _feat[6] = agg;      _feat[7] = bump;
  _feat[8] = maxWell;  _feat[9] = wellCnt;
  return _feat;
}

/* features(rows, info) -> shared Float64Array(8).
   landing_height / eroded_piece_cells cannot be recovered from the
   afterstate board (the cleared rows are gone), so they come from
   the engine's info dict. */
/* Loud failure for a missing engine field.

   HISTORY (do not reintroduce): this slot used to be read straight
   off an engine info field with a logical-or default of zero. When
   engine dropped that field — correctly, since feature formulas
   belong to rl — the default swallowed the undefined silently: no
   throw, no NaN, no warning. Feature 0 read zero forever, which
   nulls out Dellacherie's largest-magnitude term (-4.50, the
   height penalty), so every policy played with no height penalty
   and merely looked badly trained. Measured cost: 739 lines and
   death at ~1,872 pieces, versus 1,199 lines and surviving the cap
   once repaired.

   A defect must never be able to disguise itself as poor
   performance, so a required field that goes missing now shouts
   once to the console and keeps shouting in the UI badge. */
let _missingFieldWarned = false;
function requireField(info, name) {
  const v = info?.[name];
  if (v === undefined || v === null) {
    if (!_missingFieldWarned) {
      _missingFieldWarned = true;
      const msg = `[features] 엔진 info 에 '${name}' 가 없다. 특징 계산이 틀린 값을 낸다. ` +
                  `엔진 계약(docs/spec.md §8)이 바뀌었는지 확인하라.`;
      console.error(msg);
      LOAD_ERRORS.push(msg);
    }
    return null;
  }
  return v;
}

/* landing_height — CONFIRMED BY rl (확정본, features.py docstring):
     mean over the piece's 4 locked cells of (ROWS - y)
   measured BEFORE line removal, so the floor row y=21 has height 1.

   ROWS is 22, not 20: the board includes the 2 hidden spawn-buffer
   rows, and using 20 would shift every value by 2.

   This is the MEAN of the four cells, NOT the bounding-box centre.
   docs/spec.md §8 describes the centre; rl confirmed the docstring
   is authoritative and the weights were trained on the mean (the
   two differ by 0.25 on T, J and L). */
function landingHeightFrom(info) {
  const cells = requireField(info, 'piece_cells');
  if (!cells || cells.length === 0) return 0;
  let sum = 0;
  for (const c of cells) sum += (ROWS - c[0]);
  return sum / cells.length;
}

export function features(rows, info) {
  const off = rows.length >= ROWS ? rows.length - VIS : 0;
  boardFeatures(rows, off);
  _feat[0] = info ? landingHeightFrom(info) : 0;
  const eroded = info ? requireField(info, 'eroded_piece_cells') : null;
  _feat[1] = eroded === null ? 0 : eroded;
  return _feat;
}

export function columnHeights(rows) {
  const off = rows.length >= ROWS ? rows.length - VIS : 0;
  boardFeatures(rows, off);
  return Array.from(_heights);
}

/* ============================================================
   Forward passes
   ============================================================ */

function dot(w, f, len) {
  let s = 0;
  const n = len === undefined ? Math.min(w.length, f.length) : Math.min(w.length, len);
  for (let i = 0; i < n; i++) s += w[i] * f[i];
  return s;
}

/* layers: [{W, b}, ...] with W in shape [in][out] (rl's format —
   W[i][j] is input i -> unit j). relu on every layer except the
   last, whose output is a single scalar. */
function mlpForward(layers, f, activation) {
  let x = f;
  for (let i = 0; i < layers.length; i++) {
    const W = layers[i].W, b = layers[i].b;
    const nIn = W.length, nOut = W[0].length;
    const out = new Float64Array(nOut);
    for (let j = 0; j < nOut; j++) out[j] = b ? b[j] : 0;
    for (let k = 0; k < nIn; k++) {
      const xk = x[k];
      if (xk === 0) continue;
      const row = W[k];
      for (let j = 0; j < nOut; j++) out[j] += xk * row[j];
    }
    if (i < layers.length - 1) {
      for (let j = 0; j < nOut; j++) {
        out[j] = activation === 'tanh' ? Math.tanh(out[j]) : (out[j] > 0 ? out[j] : 0);
      }
    }
    x = out;
  }
  return x[0];
}

/* Score one afterstate. `linear` kinds consume RAW features;
   `mlp` kinds were trained on features divided by feature_scale
   and must be normalised identically here. Absence of
   feature_scale means "do not normalise", which makes the linear
   case fall out automatically. */
const _laid = new Float64Array(32);   // room for future feature sets
export function scoreWith(model, f) {
  /* Lay the computed features out in the ORDER THE FILE DECLARES,
     so a reordered or longer feature list stays correct. */
  const plan = model.featurePlan;
  let x = f, n = f.length;
  if (plan) {
    n = plan.length;
    for (let i = 0; i < n; i++) _laid[i] = f[plan[i]];
    x = _laid;
  }

  if (model.kind === 'linear' || model.kind === 'policy_linear') {
    return dot(model.weights, x, n);
  }
  if (model.feature_scale) {
    for (let i = 0; i < n; i++) _scaled[i] = x[i] / model.feature_scale[i];
    x = _scaled;
  }
  return mlpForward(model.layers, x, model.activation);
}

/* ============================================================
   Weight registry
   ============================================================ */

export const WEIGHTS = Object.create(null);
export const LOAD_ERRORS = [];

/* rl's cross-strategy comparison, loaded from weights/eval_summary.json.
   The UI renders whatever this file says rather than hardcoding numbers,
   because rl retrains and the figures move. */
export let EVAL_SUMMARY = null;

/* Build the file's feature layout BY NAME.

   rl is about to ship weight files with 10 features (two extra
   well terms), keeping the 8-feature ones alongside. Anything that
   reads features positionally — or assumes there are 8 — starts
   scoring garbage the moment such a file loads, with no symptom.
   So the file's own `features` array is the authority: we map each
   name to the slot we compute it in, and reuse that permutation
   for every evaluation.

   A name we cannot compute is a hard stop, not a warning. Scoring
   with a missing term is exactly the landing_height failure again
   (a feature silently reading 0), so the model is refused and the
   strategy shows as 학습 전 until rl tells us the formula. */
function buildFeaturePlan(name, fileFeatures) {
  if (!fileFeatures || !fileFeatures.length) {
    return { plan: FEATURE_ORDER.map((_, i) => i), names: FEATURE_ORDER.slice() };
  }
  const unknown = fileFeatures.filter(n => ALL_FEATURES.indexOf(n) < 0);
  if (unknown.length) {
    const msg = `[weights] ${name}: 계산할 줄 모르는 특징 ${JSON.stringify(unknown)} 이 있다. ` +
                `이 전략을 로드하지 않는다 (0 으로 채워 잘못 점수를 내느니 안 도는 게 낫다). ` +
                `rl 에게 정의를 받아 policies.js 에 구현해야 한다.`;
    console.error(msg);
    LOAD_ERRORS.push(msg);
    return null;
  }
  if (fileFeatures.length !== FEATURE_ORDER.length ||
      fileFeatures.some((n, i) => n !== FEATURE_ORDER[i])) {
    /* known names, different order/length — supported, just noted */
    console.warn(`[weights] ${name}: features 순서/개수가 기본과 다르다 (${fileFeatures.length}개). ` +
                 `파일 선언 순서대로 벡터를 만든다.`);
  }
  return { plan: fileFeatures.map(n => ALL_FEATURES.indexOf(n)), names: fileFeatures.slice() };
}

export function registerModel(name, json) {
  if (!json) return false;
  const kind = json.kind || 'linear';

  const fp = buildFeaturePlan(name, json.features);
  if (!fp) return false;

  const m = {
    name, kind,
    activation: json.activation || 'relu',
    feature_scale: json.feature_scale || null,
    meta: json.meta || {},
    featurePlan: fp.plan,
    featureNames: fp.names,
    /* branch on what the file DECLARES, never on its name — rl hit
       exactly this bug: evaluate.py picked the search path by
       filename, so search_1ply_sum quietly ran greedy and reported
       CEM's numbers. */
    searchRule: json.meta?.search?.rule || null,
  };
  if (kind === 'linear' || kind === 'policy_linear') {
    if (!json.weights) return false;
    m.weights = json.weights;
  } else {
    if (!json.layers) return false;
    m.layers = json.layers;
    if (!m.feature_scale) {
      console.warn(`[weights] ${name}: kind=${kind} 인데 feature_scale 이 없다. 정규화 없이 돈다.`);
    }
  }
  WEIGHTS[name] = m;
  return true;
}

/* Where the weight JSONs live, resolved against THIS module's URL
   rather than the page URL, so it does not depend on how deep the
   page is served.

   Two layouts are in play and I do not want to depend on which one
   wins: tools/serve.sh currently serves the project root (page is
   /web/index.html, weights at /weights/), while the deployment
   note described a web/ root with a web/weights symlink. The first
   candidate that answers is used, and the result is cached. */
const WEIGHTS_DIRS = [
  new URL('../weights/', import.meta.url).href,   // project root served
  new URL('./weights/', import.meta.url).href,    // web/ served (symlink)
];
let weightsDir = null;

/* Python's json.dump writes bare NaN / Infinity for non-finite
   floats. That is a Python extension, NOT valid JSON, and both
   JSON.parse and response.json() reject the whole file — so a
   single NaN buried in meta.history makes an entire strategy
   unloadable in the browser. It shows up legitimately (REINFORCE
   has no critic, so its critic_mse is NaN), so rather than block
   on it we retry once with those tokens replaced by null. Only
   bare tokens outside strings are touched. Reported to rl. */
function parseLooseJson(text, path) {
  try {
    return JSON.parse(text);
  } catch (e) {
    const fixed = text.replace(/("(?:[^"\\]|\\.)*")|(?<![\w.])(-?Infinity|NaN)(?![\w.])/g,
                               (m, str) => (str !== undefined ? str : 'null'));
    const out = JSON.parse(fixed);
    const msg = `[weights] ${path}: NaN/Infinity 가 들어 있어 표준 JSON 이 아니다. ` +
                `null 로 치환해 읽었다 (rl 에 보고됨).`;
    console.warn(msg);
    LOAD_ERRORS.push(msg);
    return out;
  }
}

async function fetchJson(path) {
  const r = await fetch(path, { cache: 'no-cache' });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return parseLooseJson(await r.text(), path);
}

/* Prefer weights/index.json (rl maintains it) so the available set
   is data, not a hardcoded list. Fall back to probing the known
   stems when the index is absent. */
/* Probe the candidate directories once, using a file that must
   exist in any working deployment. */
async function resolveWeightsDir() {
  if (weightsDir) return weightsDir;
  for (const dir of WEIGHTS_DIRS) {
    for (const probe of ['index.json', 'cem_linear.json']) {
      try {
        const r = await fetch(dir + probe, { cache: 'no-cache' });
        if (r.ok) { weightsDir = dir; return dir; }
      } catch (e) { /* try the next candidate */ }
    }
  }
  const msg = '[weights] weights/ 디렉터리를 찾지 못했다. 시도한 경로: ' + WEIGHTS_DIRS.join(', ');
  console.error(msg);
  LOAD_ERRORS.push(msg);
  return null;
}

/* Inlined weights, when present.

   The standalone single-file build has no server and no fetch:
   rl's weights_bundle.js defines these globals and the whole
   weight set is already in memory. Taking that path first means
   the standalone build issues ZERO network requests, which is the
   entire point of it (a CSP-restricted or file:// context blocks
   fetch outright, and a blocked fetch would silently leave every
   panel showing 학습 전).

   On the served build these globals are undefined — index.html
   does not load the bundle — so this returns 0 and the normal
   fetch path runs unchanged. */
function loadFromInlineBundle() {
  const bundle = globalThis.TETRIS_WEIGHTS;
  if (!bundle || typeof bundle !== 'object') return 0;

  let n = 0;
  for (const [name, json] of Object.entries(bundle)) {
    /* honour the same trained/file gate the index path uses */
    if (registerModel(name, json)) n++;
  }
  if (globalThis.TETRIS_EVAL_SUMMARY) EVAL_SUMMARY = globalThis.TETRIS_EVAL_SUMMARY;
  return n;
}

export async function loadWeights() {
  const inlined = loadFromInlineBundle();
  if (inlined > 0) return inlined;

  const dir = await resolveWeightsDir();
  if (!dir) return 0;

  let names;
  try {
    const idx = await fetchJson(dir + 'index.json');
    const arr = Array.isArray(idx) ? idx : (idx.strategies || idx.weights || []);
    names = arr
      /* Respect what the index DECLARES. Entries carry trained:
         false for strategies rl has registered but not yet trained
         (cem_score_wells is listed with file present but
         trained:false and no file on disk). Fetching those anyway
         produced a 404 on every page load — harmless to behaviour,
         since a missing file just means 학습 전, but it is a real
         request for a resource that does not exist, and it made
         the deploy smoke test fail. The index already told us; we
         simply were not reading it. */
      .filter(e => typeof e === 'string' ? true : (e.file && e.trained !== false))
      .map(e => (typeof e === 'string' ? e : (e.file || e.name)))
      .filter(Boolean)
      .map(s => s.replace(/\.json$/, ''));
  } catch (e) {
    /* rl has not published index.json yet — fall back to the known
       stems. Absent files are simply 학습 전, not an error. */
    names = STRATEGIES.filter(s => s.key).map(s => s.key);
  }

  await Promise.all(names.map(async (nm) => {
    try { registerModel(nm, await fetchJson(`${dir}${nm}.json`)); }
    catch (e) { /* expected while rl is still training */ }
  }));

  try { EVAL_SUMMARY = await fetchJson(dir + 'eval_summary.json'); }
  catch (e) { EVAL_SUMMARY = null; }

  return Object.keys(WEIGHTS).length;
}

/* ============================================================
   Strategy registry — the 8 panels of PROJECT.md's table.
   Korean labels; internal names never surface in the UI.
   Explanatory prose is a FALLBACK only: when a weight file
   carries meta.critic_role, the UI shows rl's text instead, since
   that is worded to match the study-plan document.
   ============================================================ */

export const STRATEGIES = [
  {
    id: 'random', key: null, needsWeights: false,
    label: '무작위', short: '무작위',
    critic: '크리틱 없음', plan: '대조군', color: '#7d8794',
    tagline: '가능한 자리 중 하나를 균등하게 고른다. 다른 모든 전략의 바닥선.',
    formula: 'a ~ Uniform(가능한 배치 집합)',
    criticRole:
      '학습이 없다. 갱신식 자체가 없으므로 크리틱이 들어갈 자리도 없다. ' +
      '이 패널이 존재하는 이유는 나머지 일곱의 성적이 학습 덕분인지, ' +
      '테트리스가 원래 쉬운 문제라서인지 가르기 위해서다.',
  },
  {
    id: 'dellacherie', key: null, needsWeights: false,
    label: '델라체리 수동 가중치', short: '델라체리',
    critic: '크리틱 없음', plan: '대조군', color: '#c98b3b',
    tagline: '사람이 손으로 정한 평가함수. 학습을 전혀 하지 않고도 강하다.',
    formula: 'a = argmax_a  w·f(afterstate)        (w 는 사람이 고정)',
    criticRole:
      '가중치가 사람의 사전지식으로 고정되어 있다. 크리틱도 없고 학습도 없다. ' +
      '이 패널의 성적이 학습 기반 전략보다 높게 나온다면 그것은 결함이 아니라, ' +
      '좋은 특징이 있으면 학습이 필요 없을 수도 있다는 사실이다.',
  },
  {
    id: 'cem_linear', key: 'cem_linear', needsWeights: true,
    label: 'CEM 선형 (줄 최대화)', short: 'CEM 줄',
    critic: '크리틱 없음 · 진화 탐색', plan: '축 밖', color: '#4da3ff',
    tagline: '교차 엔트로피법이 가중치 벡터를 직접 탐색한다. 기울기도 크리틱도 쓰지 않는다.',
    formula: 'μ ← mean(상위 ρ 표본)\nσ ← std(상위 ρ 표본)',
    criticRole:
      '가치함수를 아예 추정하지 않는다. 정책 파라미터 공간에서 표본을 뽑아 ' +
      '성적 상위 표본의 평균으로 분포를 옮길 뿐이다. ' +
      '공부계획의 세로축 바깥에 있는 항목이며, 크리틱 없이도 강한 정책이 나온다는 것을 보이는 대조군이다.',
  },
  {
    id: 'reinforce', key: 'reinforce', needsWeights: true,
    label: 'REINFORCE', short: 'REINFORCE',
    critic: '크리틱 없음 · 실제 리턴 G_t', plan: '①', color: '#3fd15b',
    tagline: '정책 경사의 출발점. 에피소드가 끝난 뒤 실제로 받은 리턴으로 갱신한다.',
    formula: 'θ ← θ + α · γ^t · G_t · ∇θ log π(a_t | s_t ; θ)',
    criticRole:
      '크리틱이 없다. 갱신 신호 G_t 는 실제로 굴러간 에피소드의 누적 보상 그대로다. ' +
      '편향은 없지만 분산이 크다.',
  },
  {
    id: 'reinforce_baseline', key: 'reinforce_baseline', needsWeights: true,
    label: 'REINFORCE + 기준선', short: '+ 기준선',
    critic: '분산 감소 기준선 (부트스트랩 아님)', plan: '②', color: '#2ad4e6',
    tagline: '리턴에서 상태의 평균적 가치를 빼서 분산만 줄인다. 표적은 여전히 실제 리턴.',
    formula:
      'θ ← θ + α · (G_t − v̂(s_t ; w)) · ∇θ log π(a_t | s_t ; θ)\n' +
      'w ← w + α_w · (G_t − v̂(s_t ; w)) · ∇w v̂(s_t ; w)',
    criticRole:
      '여기서 v̂ 는 아직 크리틱이 아니라 기준선이다. v̂ 를 빼도 정책 경사의 기댓값은 ' +
      '변하지 않는다 — 편향을 넣지 않고 분산만 줄인다. v̂ 가 갱신 표적을 만드는 데 ' +
      '쓰이지 않고 오직 빼는 데만 쓰이기 때문이다.',
  },
  {
    id: 'a2c', key: 'a2c', needsWeights: true,
    label: 'A2C (1-스텝)', short: 'A2C',
    critic: '부트스트랩 표적 v(s_{t+1})', plan: '③', color: '#b45cf0',
    tagline: '실제 리턴을 기다리지 않고 다음 상태의 가치 추정으로 표적을 만든다.',
    formula:
      'δ_t = r_t + γ · v̂(s_{t+1} ; w) − v̂(s_t ; w)\n' +
      'θ  ← θ + α · δ_t · ∇θ log π(a_t | s_t ; θ)\n' +
      'w  ← w + α_w · δ_t · ∇w v̂(s_t ; w)',
    criticRole:
      '세로축의 분기점이다. 표적 r_t + γ·v̂(s_{t+1}) 안에 v̂ 가 들어가 있다 — 부트스트랩이다. ' +
      '그래서 v̂ 는 기준선이 아니라 크리틱이다. 대가로 편향이 생긴다.',
  },
  {
    id: 'dqn', key: 'dqn', needsWeights: true,
    label: 'DQN (애프터스테이트)', short: 'DQN',
    critic: 'Q 가 정책 그 자체 (argmax)', plan: '④', color: '#f0524f',
    tagline: '정책망이 따로 없다. 가치함수의 argmax 가 곧 행동이다.',
    formula:
      'y = r + γ · max_{a\'} Q(s\', a\' ; w⁻)\n' +
      'w ← w − α · ∇w ( Q(s, a ; w) − y )²\n' +
      'a = argmax_a Q(s, a ; w)',
    criticRole:
      '크리틱과 정책이 분리되어 있지 않다. Q 하나뿐이고 행동은 그 Q 의 argmax 로 유도된다. ' +
      '즉 크리틱이 갱신식의 일부가 아니라 갱신식 전체다.',
  },
  {
    id: 'search_1ply', key: 'search_1ply', needsWeights: true,
    label: '1수 탐색 + 학습된 가치', short: '1수 탐색',
    critic: '탐색이 정책 개선 · 가치는 말단 평가', plan: '4주차', color: '#f5921e',
    tagline: 'CEM 과 똑같은 가중치를 쓰되, 다음 조각까지 2수를 전개해서 고른다. ' +
             '이 실험대에서 그것이 이득인지는 아직 확정되지 않았다.',
    /* leaf-only evaluation. This string is a FALLBACK — the UI
       prefers meta.update_formula from the weight file, so the
       displayed rule always comes from the same file that defines
       the executed rule. It went stale once (still showed the
       superseded `v(p₁) +` sum form while running leaf), which no
       functional test can catch: the code was right and only the
       explanation was wrong. On a study tool the explanation IS
       the product. */
    formula: 'score(p₁) = max_{p₂} v(p₁→p₂ 이후)',
    criticRole:
      '정책이 파라미터로 표현되어 있지 않다. 탐색 그 자체가 정책이고, 학습된 가치는 ' +
      '탐색 트리의 잎에서만 호출된다. AlphaZero 의 골격이 여기 있다 — 탐색이 정책 개선 ' +
      '연산자 역할을 하고, 학습은 그 탐색 결과를 값싸게 재현할 평가함수를 만드는 데 쓰인다. ' +
      '이 패널은 CEM 선형과 가중치가 완전히 동일하다. 차이는 오직 2수를 전개한다는 것뿐이다.',
  },
  {
    id: 'search_1ply_sum', key: 'search_1ply_sum', needsWeights: true,
    label: '1수 탐색 — 폐기된 합산 규칙', short: '1수(합산)',
    critic: '같은 가치함수 · 평가 규칙만 다름', plan: '대조군', color: '#8a6bd1',
    tagline: '패널 8과 가중치도 탐색 깊이도 같다. 두 국면의 가치를 더하느냐 마느냐만 다르다.',
    formula: 'score(p₁) = v(p₁ 이후) + max_{p₂} v(p₁→p₂ 이후)   ← 폐기\n' +
             'score(p₁) =              max_{p₂} v(p₁→p₂ 이후)   ← 현재 (패널 8)',
    criticRole:
      '이 패널은 틀린 규칙을 보존한 것이다. 깊이가 다른 두 국면의 가치를 더하면 ' +
      '중간 노드를 두 번 세는 셈이고, landing_height 와 eroded_piece_cells 를 ' +
      '서로 다른 두 배치에 대해 더하는 것은 의미가 없다. 가치함수가 판정하도록 학습된 것은 ' +
      '말단 국면뿐이다.\n\n' +
      '남겨 둔 이유는 무엇을 왜 고쳤는지가 결과 자체보다 배울 것이 많아서다. ' +
      '패널 8과 나란히 두면 같은 가중치·같은 탐색 깊이에서 평가 규칙 하나가 무엇을 바꾸는지 볼 수 있다.',
  },
  {
    id: 'cem_score', key: 'cem_score', needsWeights: true,
    label: 'CEM 선형 (점수 목표)', short: 'CEM 점수',
    critic: '크리틱 없음 · 진화 탐색 · 보상만 다름', plan: '대조군', color: '#e0629b',
    tagline: '패널 3과 완전히 같은 알고리즘·같은 특징. 보상만 줄 수에서 점수로 바꿨다.',
    formula: 'μ ← mean(상위 ρ 표본),  σ ← std(상위 ρ 표본)\n' +
             '적합도 = 지운 줄  →  적합도 = 점수 (테트리스·B2B·콤보 포함)',
    criticRole:
      '알고리즘은 패널 3과 한 글자도 다르지 않다. 크리틱도 없고 부트스트랩도 없다. ' +
      '바뀐 것은 무엇을 최대화하라고 시켰는가 하나뿐이고, 그 결과 가중치의 부호가 뒤집혔다. ' +
      'bumpiness 가 −0.139 에서 +0.217 로 바뀌었다 — 줄 최대화 정책은 울퉁불퉁함을 벌하는데 ' +
      '점수 목표 정책은 오히려 보상한다. 우물을 파고 I 조각을 기다려야 4줄을 한 번에 지우기 ' +
      '때문이다. cumulative_wells 페널티도 −0.181 에서 −0.059 로 3분의 1이 됐다. ' +
      '같은 학습기에 목적만 바꿔도 정책의 성격이 정반대가 된다는 것을 보이는 대조군이다.',
  },
  {
    id: 'cem_score_long', key: 'cem_score_long', needsWeights: true,
    label: 'CEM 점수 목표 (긴 학습)', short: 'CEM 점수·긴',
    critic: '크리틱 없음 · 예산만 늘림', plan: '대조군', color: '#d4568f',
    tagline: '패널 9와 같은 목표·같은 알고리즘. 학습 예산만 15분에서 55분으로 늘렸다.',
    formula: '적합도 = 평균over조각( score_delta / level )     (줄 수가 아니다)',
    criticRole:
      '예산을 늘리면 정책이 어디로 더 밀려가는지를 보는 패널이다. ' +
      '테트리스 비율이 12.5% 에서 39.2% 로 세 배가 됐고 레벨보정 점수/조각도 58.55 → 62.88 로 올랐다. ' +
      '대신 지운 줄 중앙값은 1,099 에서 207 로 떨어졌다.\n\n' +
      '가중치에서도 같은 이야기가 보인다. aggregate_height 가 −0.074 에서 +0.050 으로 ' +
      '부호가 뒤집혔다 — 높이를 벌하던 것이 보상하게 됐다. 우물을 더 깊이 파고 I 를 더 오래 ' +
      '기다린다는 뜻이고, 그래서 4줄을 자주 터뜨리는 대신 자주 죽는다. ' +
      '목표를 점수로 두면 학습이 오래 갈수록 그 방향으로 계속 밀려간다.',
  },
  {
    id: 'cem_score_wells', key: 'cem_score_wells', needsWeights: true,
    label: 'CEM 점수 목표 (우물 특징 추가)', short: 'CEM 점수·우물',
    critic: '크리틱 없음 · 특징 두 개만 추가', plan: '대조군', color: '#ffb454',
    tagline: '패널 11과 같은 55분·같은 목표·같은 알고리즘. 특징 두 개만 더했다.',
    formula: '적합도는 패널 11과 동일. 특징만 8개 → 10개 (+max_well_depth, +well_count)',
    criticRole:
      '패널 11과 다른 것은 특징 두 개뿐이다. 그런데 결과가 바뀌었다 — ' +
      '테트리스 비율 38.9% → 59.2%, 레벨보정 점수/조각 61.8 → 82.2(전 전략 1위). ' +
      '생존은 개선되지 않았다 — 상한 3,000조각으로 재면 6/10이 산 것처럼 보이지만 ' +
      '상한 50,000조각에서는 양쪽 다 0/10이다. 여기서 바뀐 것은 점수이지 생존이 아니다.\n\n' +
      '학습된 가중치가 전략을 그대로 말한다 — max_well_depth +0.575, well_count −0.350. ' +
      '깊은 우물을 정확히 하나 유지하라는 뜻이다. 아무도 그 부호를 지정하지 않았다.\n\n' +
      '왜 8종으로는 안 됐나: cumulative_wells 는 우물의 비용을 재는 항이라 ' +
      '"깊은 우물 하나(자산)"와 "얕은 홈 여럿(부채)"을 구분하지 못한다 — 총량이 같게 나오기 때문이다. ' +
      '선형 정책이 표현할 수 없는 전략은 탐색을 아무리 오래 돌려도 찾지 못한다. ' +
      '학습 시간이 부족했던 게 아니라 표현력이 부족했던 것이다.',
  },
  {
    id: 'cem_score_safe', key: 'cem_score_safe', needsWeights: true,
    label: 'CEM 점수+생존 목표', short: 'CEM 점수+생존', color: '#6fd48a',
    critic: '크리틱 없음 · 적합도만 바꿈', plan: '대조군',
    tagline: '패널 12와 같은 특징·같은 예산·같은 선형 정책. 적합도에 생존을 곱한 것만 다르다.',
    formula: '적합도 = 평균over조각( score_delta / level ) × min(1, 생존조각 / 조각상한)',
    criticRole:
      '패널 12는 조각당 점수만 봤다. 그래서 일찍 죽어도 죽기 전 점수가 높으면 좋은 후보로 ' +
      '뽑혔다 — 생존이 점수 계산을 멈추기는 하지만 나눗셈의 분모도 같이 줄어 평균은 멈추지 ' +
      '않기 때문이다. 죽음에 대가가 없으니 정책이 위험 쪽으로 밀렸다.\n\n' +
      '여기서는 적합도에 생존 비율을 곱한다. 결과(상한 50,000조각, 엔진 기준 10판): ' +
      '중앙값 19,992줄, 7판이 상한까지. 패널 12의 중앙값 1,325줄에서 15배다. ' +
      '가장 못한 판(6,564줄)이 패널 12의 가장 잘한 판(3,789줄)보다 길어서 분포가 겹치지 않는다.\n\n' +
      '대가는 점수다 — 레벨보정 점수/조각 82.4 → 74.8, 테트리스 비율 58.5% → 44.5%. ' +
      '점수를 9% 내주고 생존을 15배 얻은 교환이고, 44.5%는 여전히 지운 줄의 절반 가까이가 ' +
      '4줄 동시라는 뜻이다. 곱셈을 택한 이유는 임의 상수가 없다는 것 — 임계값도 벌점 계수도 ' +
      '고를 필요가 없다.',
  },
  {
    id: 'cem_search', key: 'cem_search', needsWeights: true,
    label: '탐색까지 넣고 학습한 CEM', short: 'CEM+탐색학습',
    critic: '탐색을 롤아웃 안에 넣고 학습', plan: '4주차', color: '#5ec8a8',
    tagline: '패널 8의 교란 요인을 제거한 것. 가중치를 2수 탐색과 함께 학습시켰다.',
    formula: '롤아웃의 수 선택 = argmax_{p₁} max_{p₂} v(p₁→p₂ 이후)   (그리디가 아니다)',
    criticRole:
      '패널 8은 CEM 이 1스텝 그리디용으로 맞춘 가중치를 2수 탐색에 갖다 쓴 것이라, ' +
      '가치함수가 자기가 쓰일 깊이에서 좋도록 학습된 적이 없었다. ' +
      '이 패널은 그 교란 요인을 없앤다 — 학습 롤아웃 자체를 탐색으로 돌린다.\n\n' +
      '결과: 레벨보정 점수/조각이 46.75 → 46.88 로 거의 그대로이고, ' +
      '탐색 없는 CEM 선형(48.95)에 여전히 진다. ' +
      '즉 "가중치가 그리디용이라서 탐색이 손해였다"는 설명은 이 실험대에서 지지되지 않는다. ' +
      '이 특징 8개로는 한 수 앞을 보는 것이 이득이 되지 않는다는 쪽이 남는 해석이다.',
  },
];

/* Deliberately excluded — shown greyed out on the LEARN screen. */
export const EXCLUDED = [
  {
    id: 'ddpg_td3', plan: '⑤', label: 'DDPG / TD3',
    reason:
      '행동공간이 이산이라 탈락. 결정적 정책 경사는 ∇a Q(s, a) 를 행동에 대해 미분해야 하는데, ' +
      '배치(회전, 열)는 셀 수 있는 유한 집합이라 미분할 대상이 없다.',
  },
  {
    id: 'sac', plan: '⑥', label: 'SAC',
    reason:
      '행동공간이 이산이라 탈락. 연속 행동의 재파라미터화 트릭과 엔트로피 항이 설계의 핵심인데, ' +
      '이산 배치에서는 그 장치가 필요 없다.',
  },
];

export function byId(id) {
  return STRATEGIES.find(s => s.id === id) || null;
}

/* Dellacherie's published hand weights, in FEATURE_ORDER. The
   original evaluation function has 6 terms; aggregate_height and
   bumpiness are not among them, hence the two zeros. Needs no
   weight file, so this panel runs before any training finishes. */
export const DELLACHERIE_W = [
  -4.500158825082766,   // landing_height
   3.4181268101392694,  // eroded_piece_cells
  -3.2178882868487753,  // row_transitions
  -9.348695305445199,   // column_transitions
  -7.899265427351652,   // holes
  -3.3855972247263626,  // cumulative_wells
   0.0,                 // aggregate_height
   0.0,                 // bumpiness
];
const DELLACHERIE_MODEL = { kind: 'linear', weights: DELLACHERIE_W };

/* Is this strategy runnable right now? */
export function isReady(strat) {
  if (!strat) return false;
  if (!strat.needsWeights) return true;
  return !!WEIGHTS[strat.key];
}

export function modelFor(strat) {
  if (!strat) return null;
  if (strat.id === 'dellacherie') return DELLACHERIE_MODEL;
  if (strat.key && WEIGHTS[strat.key]) return WEIGHTS[strat.key];
  return null;
}


/* ============================================================
   Difficulty — information available to the player/agent

   The axis is PREVIEW, not garbage rows: how many upcoming pieces
   may be seen, and whether hold exists. Piece ORDER is identical
   across modes (engine confirmed: same seed, same 7-bag, same
   board_hash), so a mode comparison is fair — only information is
   withheld.

   THE ENGINE OWNS THIS. A state carries its own difficulty, and
   E.nextVisibleCount(state) is the single source of truth. We do
   not keep a parallel notion of "what mode are we in", because two
   sources would eventually disagree.

   Note E.visibleNext() THROWS NextPeekBlocked in extreme instead
   of returning []. That is deliberate on engine's part: if it
   returned [] quietly, 1-ply search would expand an empty future
   and still report a number, making the experiment unmeasurable.
   So the fallback below is explicit, never silent.
   ============================================================ */
export const DIFFICULTIES = [
  { id: 'normal',  engineId: E.DIFFICULTY_NORMAL,  label: '노멀',     nextCount: 5, hold: true,
    note: '미리보기 5개 + 홀드. 기본값.' },
  { id: 'hard',    engineId: E.DIFFICULTY_HARD,    label: '하드',     nextCount: 1, hold: false,
    note: '미리보기 1개, 홀드 없음.' },
  { id: 'extreme', engineId: E.DIFFICULTY_EXTREME, label: '익스트림', nextCount: 0, hold: false,
    note: '미리보기 전무. 앞을 못 보므로 1수 탐색이 0수 탐색이 된다.' },
];

export function difficultyById(id) {
  return DIFFICULTIES.find(d => d.id === id) || DIFFICULTIES[0];
}
export function difficultyByEngineId(v) {
  return DIFFICULTIES.find(d => d.engineId === v) || DIFFICULTIES[0];
}

/* UI's currently selected mode for NEW games. Existing states keep
   whatever difficulty they were created with. */
export const difficulty = { current: 'normal' };

/* ============================================================
   Action selection

   Every strategy reduces to: score each legal placement's
   afterstate, then pick. Value-based kinds take the argmax;
   policy_* kinds may sample from a softmax, but rl evaluated them
   with greedy=true, so greedy is the default and the sampling
   toggle is opt-in (otherwise the arena would not reproduce the
   reported numbers).
   ============================================================ */

export const options = { greedyPolicy: true, softmaxTemperature: 1.0 };

const DEATH = -1e6;   // rl's meta.search.death_penalty

/* ------------------------------------------------------------
   Deterministic argmax — rl's normative rule (features.py
   argmax_stable). Round to SCORE_DECIMALS, then take the FIRST
   strict maximum.

   This is not a nicety. Placement scores tie in the last bits far
   more often than one would guess: rl measured
     placement  7 -> -23.665216819989176
     placement 16 -> -23.665216819989173
   a gap of 3e-15. The policy considers those the same move, but
   argmax must pick one, and which one it picks depends on the
   order the dot product was summed in — numpy's vectorised BLAS
   path and a scalar loop genuinely disagree.

   One flipped tie forks the whole game, because the two
   placements are different moves. rl measured the same weights on
   the same seed producing 1,996 lines in one harness and 800 in
   another — 44% apart over 10 seeds. Both were "correct"; they
   took different branches of a coin flip.

   legalPlacements is sorted by (rot, x), so the first maximum is
   the lowest (rot, x) — reproducible in any language.
   ------------------------------------------------------------ */
/* rl's rule, revised: compare RAW scores with an epsilon guard and
   never round. Only `>`, `+` and a literal constant are involved,
   all IEEE-754, so both languages take structurally the same
   branch with no dependence on a rounding mode.

   The previous rule rounded to 9 decimals first. That is genuinely
   fragile across languages — Python's round() is banker's rounding
   on the exact decimal value, while Math.round(v*1e9)/1e9 rounds
   halves toward +Infinity and adds its own multiplication error,
   and placement scores are negative, so the two disagree in
   opposite directions on exact halves.

   Measured caveat, so nobody over-credits this change: on 42,594
   real placement scores the two rounding rules produced ZERO
   disagreements, and the divergence this replaced was actually
   caused by something else (see scoreAll). Switching is worth it
   for the structural guarantee, not because it fixed the bug.
   Verified to leave play unchanged: 10/10 seeds identical under
   both rules. */
const SCORE_EPS = 1e-9;

export function argmaxStable(scores) {
  let best = 0;
  for (let i = 1; i < scores.length; i++) if (scores[i] > scores[best] + SCORE_EPS) best = i;
  return best;
}

/* Score every legal placement's afterstate with `model`.
   Scores are stored ALREADY ROUNDED so every downstream argmax
   and softmax sees the same values the reference does.

   NO DEATH PENALTY HERE — deliberately, and this was a real bug.

   I used to score a game-ending placement as DEATH (-1e6), which
   seemed obviously safer. rl's greedy path does not: it scores the
   afterstate on its features like any other. That difference is
   invisible for almost an entire game, because game-ending
   placements barely exist until the stack is nearly topped out —
   measured on seed 900009, only 29 of 11,707 moves had any, and 6
   of those fell in the last 8 moves. So the two implementations
   agreed for 11,700 pieces and then diverged at the very end,
   with mine surviving slightly longer every time (+30, +1, +4
   pieces). Removing it reproduces rl's per-seed numbers exactly:
       900001 1493/3769 · 900004 951/2423 · 900009 4664/11703

   The penalty arguably plays better, but "plays better" is not the
   goal here: the numbers on screen come from rl's evaluation, and
   an agent that quietly plays a different policy than the one
   being reported is worse than one that plays a slightly weaker
   documented policy. rl owns the rule; we mirror it.

   The 2-ply search DOES apply DEATH, because rl documents it there
   (meta.search.death_penalty). Same constant, different scope. */
function scoreAll(state, placements, model) {
  const scores = new Float64Array(placements.length);
  for (let k = 0; k < placements.length; k++) {
    const [ns, info] = E.applyPlacement(state, placements[k]);
    scores[k] = scoreWith(model, features(ns.rows, info));   // raw; epsilon at compare time
  }
  const bestIdx = argmaxStable(scores);
  return { scores, best: scores[bestIdx], bestIdx };
}

function greedyBest(state, placements, model) {
  const { scores, bestIdx } = scoreAll(state, placements, model);
  return { placement: placements[bestIdx], index: bestIdx, scores };
}

export function chooseAction(state, stratId, rng = Math.random, diffId = null) {
  const placements = E.legalPlacements(state);
  if (!placements || placements.length === 0) return null;

  const strat = byId(stratId);

  if (!strat || strat.id === 'random') {
    const i = Math.min(placements.length - 1, Math.floor(rng() * placements.length));
    return { placement: placements[i], index: i, scores: null };
  }

  const model = modelFor(strat);
  if (!model) return null;               // 학습 전 — caller must not run it

  /* Lookahead needs a visible next piece. Ask the STATE, not a UI
     flag — the engine is the authority on what this game reveals.
     With preview 0 the search has nothing to expand and collapses
     to a greedy evaluation, which is identical to CEM by
     construction (the two files carry the same weights). That
     collapse is the point of extreme mode, so it is explicit and
     reported via `collapsedToZeroPly`, never silent. */
  if (model.searchRule) {
    let canPeek = false;
    try {
      canPeek = E.nextVisibleCount(state) >= 1;
    } catch (e) {
      canPeek = false;
    }
    if (canPeek) return searchTwoPly(state, placements, model);
    const greedy = greedyBest(state, placements, model);
    greedy.collapsedToZeroPly = true;
    return greedy;
  }

  /* scores come back already rounded, and greedyIdx is the first
     strict maximum — rl's argmax_stable rule */
  const { scores, best, bestIdx: greedyIdx } = scoreAll(state, placements, model);
  let bestIdx = greedyIdx;

  const isPolicy = model.kind === 'policy_linear' || model.kind === 'policy_mlp';
  if (isPolicy && !options.greedyPolicy) {
    const T = options.softmaxTemperature || 1;
    let sum = 0;
    const pr = new Float64Array(scores.length);
    for (let j = 0; j < scores.length; j++) { pr[j] = Math.exp((scores[j] - best) / T); sum += pr[j]; }
    let u = rng() * sum, acc = 0;
    for (let j = 0; j < pr.length; j++) { acc += pr[j]; if (u <= acc) { bestIdx = j; break; } }
  }

  return { placement: placements[bestIdx], index: bestIdx, scores };
}

/* Two-ply lookahead. The combining rule comes from the FILE
   (meta.search.rule), never from the strategy name:

     'leaf' (current default)
        score(p1) = max over p2 of v(after p1 -> p2)
     'sum'  (superseded, kept as search_1ply_sum for the record)
        score(p1) = v(after p1) + max over p2 of v(after p1 -> p2)

   rl replaced 'sum' with 'leaf' because summing adds the values of
   two states at different depths — double-counting the middle node
   — and adds landing_height / eroded_piece_cells across two
   different placements, which means nothing. Only the leaf is a
   position the value function was ever trained to judge.

   Dispatching on the declared rule rather than the filename is
   deliberate: rl's evaluate.py picked the search path by name, so
   search_1ply_sum silently ran greedy and reported CEM's numbers.

   A p1 with no legal follow-up is a forced loss and scores DEATH. */
function searchTwoPly(state, placements, model) {
  const scores = new Float64Array(placements.length);
  const addParent = model.searchRule === 'sum';

  for (let k = 0; k < placements.length; k++) {
    const [ns, info] = E.applyPlacement(state, placements[k]);
    if (info.game_over) { scores[k] = DEATH; continue; }

    /* features() hands back a shared buffer, so collapse it to a
       number before the child loop overwrites it. */
    const v1 = addParent ? scoreWith(model, features(ns.rows, info)) : 0;

    const follow = E.legalPlacements(ns);
    if (!follow || follow.length === 0) { scores[k] = DEATH; continue; }

    /* raw at every level — rl's revised rule never rounds; the
       epsilon guard lives in the comparisons instead */
    let bestChild = -Infinity;
    for (let m = 0; m < follow.length; m++) {
      const [ns2, info2] = E.applyPlacement(ns, follow[m]);
      const v2 = info2.game_over ? DEATH : scoreWith(model, features(ns2.rows, info2));
      if (v2 > bestChild + SCORE_EPS) bestChild = v2;
    }

    scores[k] = v1 + bestChild;
  }
  const bestIdx = argmaxStable(scores);
  return { placement: placements[bestIdx], index: bestIdx, scores };
}

/* ============================================================
   Self-test — rl/features.py `_parity_fixtures`, verbatim.
   Tuple order: (row_trans, col_trans, holes, wells, agg, bump)
   ============================================================ */
export function selfTest(verbose = true) {
  const blank = () => new Array(VIS).fill(0);
  const fx = [];
  fx.push(['empty', blank(), [40, 10, 0, 0, 0, 0]]);
  let b = blank(); b[19] = FULL;                    fx.push(['floor_row', b, [38, 10, 0, 0, 10, 0]]);
  b = blank(); b[19] = 0x1FF;                       fx.push(['notch_right', b, [40, 10, 0, 1, 9, 1]]);
  b = blank(); b[15] = 1 << 5;                      fx.push(['floating_cell', b, [42, 12, 4, 0, 5, 10]]);
  b = blank(); b[17] = b[18] = b[19] = 0x1FF;       fx.push(['deep_well', b, [40, 10, 0, 6, 27, 3]]);

  let allOk = true;
  const out = fx.map(([nm, board, want]) => {
    boardFeatures(board, 0);
    const got = [_feat[2], _feat[3], _feat[4], _feat[5], _feat[6], _feat[7]];
    const pass = got.every((g, j) => g === want[j]);
    if (!pass) allOk = false;
    return { fixture: nm, expected: want.join(','), got: got.join(','), pass };
  });
  if (verbose) {
    console.table ? console.table(out) : console.log(out);
    console.log(allOk
      ? 'policies.js features(): rl/features.py 파리티 픽스처 5/5 통과'
      : 'policies.js features(): 파리티 불일치 — rl/features.py 와 다르다');
  }
  return allOk;
}

/* ------------------------------------------------------------
   liveTest — run the REAL game loop and assert no feature is
   stuck at a constant.

   selfTest() checks the feature FUNCTIONS against fixtures. It
   cannot catch a feature whose function is correct but whose
   INPUT is empty — which is exactly how landing_height died: the
   engine dropped info.landing_height, `|| 0` swallowed the
   undefined, and all five fixtures still passed, because fixtures
   never go through the engine's info dict at all.

   So this plays a short game and checks each feature actually
   moves. A constant feature is nearly always a broken wire.
   ------------------------------------------------------------ */
export function liveTest(verbose = true, moves = 200) {
  let state = E.newGame(20260807);
  const seen = ALL_FEATURES.map(() => new Set());
  let n = 0;

  for (let i = 0; i < moves; i++) {
    const places = E.legalPlacements(state);
    if (!places.length) { state = E.newGame(20260807 + i); continue; }
    /* Play with the Dellacherie weights rather than picking a
       placement arithmetically. A dumb policy never completes a
       line, which would leave eroded_piece_cells legitimately
       constant at 0 and produce a false alarm. */
    const pick = chooseAction(state, 'dellacherie');
    const p = pick ? pick.placement : places[0];
    const [ns, info] = E.applyPlacement(state, p);
    const f = features(ns.rows, info);
    for (let k = 0; k < ALL_FEATURES.length; k++) seen[k].add(Math.round(f[k] * 1000) / 1000);
    n++;
    state = info.game_over ? E.newGame(20260807 + i) : ns;
  }

  const rows = ALL_FEATURES.map((name, k) => ({
    feature: name,
    distinct: seen[k].size,
    sample: Array.from(seen[k]).slice(0, 4).join(', '),
    ok: seen[k].size > 1,
  }));
  const bad = rows.filter(r => !r.ok);

  if (verbose) {
    console.table ? console.table(rows) : console.log(rows);
    if (bad.length) {
      console.error('[features] liveTest: ' + n + '수 동안 값이 전혀 변하지 않은 특징 — ' +
        bad.map(b => b.feature + '(항상 ' + b.sample + ')').join(', ') +
        '. 함수가 아니라 입력(엔진 info)이 끊겼을 가능성이 높다.');
    } else {
      console.log('policies.js liveTest: ' + n + '수 동안 8개 특징 전부 변화 확인');
    }
  }
  for (const b of bad) {
    LOAD_ERRORS.push("특징 '" + b.feature + "' 가 실제 게임에서 상수(" + b.sample + ")다 — 배선 끊김 의심");
  }
  return bad.length === 0;
}
