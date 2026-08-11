/* ============================================================
   ui.js — screens, human play loop, AI handover, LEARN overlay
   Owner: web        ES module (entry point).
   ============================================================ */

import * as E from './engine.js';
import * as P from './policies.js';
import { Arena, SPEEDS, drawBoard, drawPiecePreview, PIECE_COLORS } from './arena.js';

const $  = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const VIS = E.VISIBLE_ROWS, COLS = E.W, ROWS = E.ROWS;

/* ============================================================
   Weight loading / status
   ============================================================ */
function refreshWeightStatus() {
  const el = $('#weights-status');
  const want = P.STRATEGIES.filter(s => s.needsWeights);
  const have = want.filter(s => P.isReady(s));
  const t = el.querySelector('.txt');

  el.className = have.length === 0 ? 'none' : have.length < want.length ? 'part' : 'ok';
  t.textContent = `학습 가중치 ${have.length}/${want.length}`;
  const missing = want.filter(s => !P.isReady(s)).map(s => s.label);
  el.title = missing.length ? `학습 전: ${missing.join(', ')}` : '전 전략 가중치 로드 완료';

  if (P.LOAD_ERRORS.length) {
    const w = $('#load-warning');
    w.style.display = '';
    w.textContent = `가중치 경고 ${P.LOAD_ERRORS.length}건 — 콘솔 확인`;
    w.title = P.LOAD_ERRORS.join('\n');
  }
}

/* ============================================================
   Tabs
   ============================================================ */
let currentScreen = 'play';
function showScreen(name) {
  currentScreen = name;
  $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.screen === name));
  $$('.screen').forEach(s => s.classList.toggle('active', s.id === `screen-${name}`));
  if (name === 'arena') {
    arena.sizeCanvases();
    /* Auto-run on entry. A grid of eight motionless boards reads as
       broken, and the whole point of the screen is watching them
       diverge; the pause button is there for anyone who wants to
       stop it. */
    if (!arena.running) arena.start();
    syncArenaButton();
  } else {
    arena.stop();
    syncArenaButton();
  }
  if (name === 'play') play.resize();
}
$$('.tab').forEach(t => t.addEventListener('click', () => showScreen(t.dataset.screen)));

/* ============================================================
   ① PLAY

   The engine's Game wrapper owns gravity AND lock delay
   (500ms, up to 15 resets on move/rotate — docs/spec.md as
   corrected by the lead). We just feed it dtMs each frame; we do
   NOT run our own gravity timer, or the two would fight and tuck
   would break.
   ============================================================ */

const DAS = 150, ARR = 40, SOFT_ARR = 35;

const AI_SPEEDS = [
  { label: '아주 느림', ms: 260 },
  { label: '느림',     ms: 130 },
  { label: '보통',     ms: 55  },
  { label: '빠름',     ms: 18  },
  { label: '최고속',   ms: 0   },
];

const play = {
  game: null,
  colorGrid: null,
  paused: false,
  raf: null,
  last: 0,
  clearFlash: null,
  flashT: 0,

  keys: {}, dasT: {}, arrT: {},

  hint: null,
  hintDirty: true,
  aiAcc: 0,
  aiTarget: null,
  aiStats: { games: 0, best: 0, sumLines: 0, pieces: 0, t0: 0 },
};

play.resize = function () {
  const c = $('#play-board');
  const wrap = $('#play-board-wrap');
  const maxH = Math.max(320, globalThis.innerHeight - 150);
  const s = Math.max(12, Math.min(34, Math.floor(maxH / VIS)));
  c.width = s * COLS;
  c.height = s * VIS;
  wrap.style.width = `${c.width}px`;
  wrap.style.height = `${c.height}px`;
  play.draw();
};

play.newColorGrid = function () {
  const g = new Array(ROWS);
  for (let y = 0; y < ROWS; y++) { g[y] = new Int8Array(COLS).fill(-1); }
  return g;
};

play.reset = function (seed) {
  const s = seed === undefined ? ((Math.random() * 0xFFFFFFFF) >>> 0) : seed;
  play.game = E.createGame({
    seed: s,
    difficulty: P.difficultyById(P.difficulty.current).engineId,
  });
  play.colorGrid = play.newColorGrid();
  play.paused = false;
  play.last = 0; play.aiAcc = 0;
  play.clearFlash = null;
  play.aiTarget = null;
  play.hint = null; play.hintDirty = true;
  play.overlay(false);
  const b2b = $('#b2b-tag'), combo = $('#combo-tag');
  if (b2b) b2b.style.display = 'none';
  if (combo) combo.style.display = 'none';
  play.updateHud();
  play.draw();
};

/* --- colour layer ------------------------------------------
   The engine's board is an occupancy bitmask with no piece type
   (it shares a core with the training engine and cannot carry
   one). So PLAY keeps a parallel colour grid: paint the locked
   cells from info.piece_cells + info.piece, then replay the same
   row removal the engine performed. */
play.recordLock = function (info) {
  if (!info) return;
  const g = play.colorGrid;
  for (const [y, x] of (info.piece_cells || [])) {
    if (y >= 0 && y < ROWS && x >= 0 && x < COLS) g[y][x] = info.piece;
  }
  const cleared = info.cleared_rows || [];
  if (cleared.length) {
    const keep = [];
    for (let y = 0; y < ROWS; y++) if (!cleared.includes(y)) keep.push(g[y]);
    while (keep.length < ROWS) keep.unshift(new Int8Array(COLS).fill(-1));
    play.colorGrid = keep;
    play.clearFlash = cleared.slice();
    play.flashT = 110;
  }
  play.hintDirty = true;
  play.aiTarget = null;
};

/* --- drawing ----------------------------------------------- */
play.draw = function () {
  const c = $('#play-board');
  const game = play.game;
  if (!game) return;

  const opts = { colorGrid: play.colorGrid, grid: true };
  const act = game.active;
  if (act && !game.gameOver) {
    opts.active = act.cells;
    opts.activePiece = act.type;
    opts.ghost = game.ghostCells;
    opts.lockProgress = game.lockDelayProgress;
    if ($('#opt-hint').checked && play.hint) {
      opts.hint = play.hint.cells;
      opts.hintPiece = play.hint.piece;
    }
  }
  if (play.clearFlash) opts.clearedRows = play.clearFlash;
  drawBoard(c, game.rows, opts);
  play.drawSide();
};

play.drawSide = function () {
  const game = play.game;
  if (game.holdEnabled) drawPiecePreview($('#hold-canvas'), game.heldType ?? -1, game.holdLocked);
  const list = $('#next-list');
  if (list.children.length !== 5) {
    list.innerHTML = '';
    for (let i = 0; i < 5; i++) {
      const cv = document.createElement('canvas');
      cv.className = 'mini-canvas';
      cv.width = 116; cv.height = 42;
      list.appendChild(cv);
    }
  }
  /* Ask the GAME how much it reveals, not the UI's mode flag — the
     engine truncates nextQueue itself, and one source of truth
     means the two can never drift apart.
     (game.nextQueue is the UI-safe accessor; it returns [] in
     extreme. E.visibleNext() throws there instead, which is the
     agent-facing contract.) */
  const visible = game.nextVisibleCount;
  const q = game.nextQueue;
  for (let j = 0; j < 5; j++) {
    list.children[j].style.display = j < visible ? '' : 'none';
    if (j < visible) drawPiecePreview(list.children[j], q[j] ?? -1, j > 0);
  }
  const empty = $('#next-empty');
  if (empty) empty.style.display = visible === 0 ? '' : 'none';
};

play.updateHud = function () {
  const g = play.game;
  $('#st-score').textContent  = g ? g.score : 0;
  $('#st-lines').textContent  = g ? g.lines : 0;
  $('#st-level').textContent  = g ? g.level : 1;
  $('#st-pieces').textContent = g ? g.pieces : 0;

  const a = play.aiStats;
  $('#ai-games').textContent = a.games;
  $('#ai-best').textContent  = a.best;
  $('#ai-mean').textContent  = a.games ? (a.sumLines / a.games).toFixed(1) : '0';
  const secs = a.t0 ? (Date.now() - a.t0) / 1000 : 0;
  $('#ai-pps').textContent   = secs > 0.5 ? (a.pieces / secs).toFixed(1) : '0';
};

play.overlay = function (show, title, sub) {
  $('#play-overlay').classList.toggle('show', !!show);
  if (title) $('#ov-title').textContent = title;
  if (sub !== undefined) $('#ov-sub').textContent = sub;
};

/* --- input -------------------------------------------------- */
const KEYMAP = {
  ArrowLeft: 'left', ArrowRight: 'right', ArrowDown: 'soft',
  ArrowUp: 'cw', KeyX: 'cw', KeyZ: 'ccw',
  Space: 'hard', KeyC: 'hold', KeyP: 'pause', KeyR: 'restart',
};

document.addEventListener('keydown', (e) => {
  const a = KEYMAP[e.code];
  if (!a || currentScreen !== 'play') return;
  e.preventDefault();

  if (a === 'restart') {
    play.aiStats = { games: 0, best: 0, sumLines: 0, pieces: 0, t0: Date.now() };
    play.reset();
    return;
  }
  if (a === 'pause') {
    if (play.game.gameOver) { play.reset(); return; }
    play.paused = !play.paused;
    play.overlay(play.paused, '일시정지', 'P 키로 계속');
    return;
  }
  if (!play.game || play.paused || play.game.gameOver) return;
  if ($('#opt-autoplay').checked) return;         // AI holds the controls

  if (play.keys[a]) return;                       // ignore OS auto-repeat
  play.keys[a] = true;
  play.dasT[a] = 0; play.arrT[a] = 0;
  play.doAction(a);
});

document.addEventListener('keyup', (e) => {
  const a = KEYMAP[e.code];
  if (a) play.keys[a] = false;
});

play.doAction = function (a) {
  const g = play.game;
  let info = null;
  switch (a) {
    case 'left':  g.moveLeft();  break;
    case 'right': g.moveRight(); break;
    case 'cw':    g.rotateCW();  break;
    case 'ccw':   g.rotateCCW(); break;
    case 'soft':  g.softDrop();  break;   // engine locks via tick, not here
    case 'hard':  info = g.hardDrop(); break;
    case 'hold':  if (g.holdEnabled) g.hold(); break;   // engine also refuses; this avoids a dead keypress
  }
  if (info) play.afterLock(info);
  play.hintDirty = true;
};

play.afterLock = function (info) {
  play.recordLock(info);
  play.showChainTags(info);
  if (play.game.gameOver) play.gameOver();
};

/* B2B and combo come straight from the engine's info (added when
   the goal moved from line count to score). Shown only while the
   chain is alive, so they read as live feedback, not decoration. */
play.showChainTags = function (info) {
  const b2b = $('#b2b-tag'), combo = $('#combo-tag');
  const chain = info.b2b_chain || 0;
  b2b.style.display = (info.b2b_active || chain > 0) ? '' : 'none';
  b2b.textContent = chain > 1 ? `B2B x${chain}` : 'B2B';
  const c = info.combo_count || 0;
  combo.style.display = c > 0 ? '' : 'none';
  combo.textContent = `COMBO x${c}`;
};

play.gameOver = function () {
  const g = play.game;
  if ($('#opt-autoplay').checked) {
    const a = play.aiStats;
    a.games++;
    a.sumLines += g.lines;
    if (g.lines > a.best) a.best = g.lines;
    play.updateHud();
    play.reset();                                  // 무한 모드: 즉시 재시작
    return;
  }
  play.overlay(true, '게임 오버', `지운 줄 ${g.lines} · 점수 ${g.score} — R 로 재시작`);
};

/* --- AI hint / handover ------------------------------------- */
play.computeHint = function () {
  play.hint = null;
  const g = play.game;
  if (!g || g.gameOver) return;
  if (!($('#opt-hint').checked || $('#opt-autoplay').checked)) return;

  const strat = P.byId($('#opt-agent').value);
  if (!P.isReady(strat)) return;
  try {
    const pick = P.chooseAction(g.toState(), strat.id, Math.random);
    if (!pick) return;
    /* placement is [rot, x, y_rest, piece]; x is the bounding-box
       origin and can be negative, so let the engine expand it
       rather than deriving cells here. */
    play.hint = { p: pick.placement, piece: pick.placement[3], cells: E.placementCells(pick.placement) };
  } catch (err) {
    if (!play.hintErr) { console.error('[play] 힌트 계산 실패:', err); play.hintErr = true; }
  }
};

/* One animation step of the AI's move. stepToward rotates, then
   walks horizontally, then hard drops — driving rot/x directly is
   what lands the piece a column off, because p[1] is the bounding
   box origin, not the leftmost cell. */
/* One animation step of the AI's move.

   PROGRESS GUARD (checker U3). stepToward() calls rotate()/move()
   and ignores their return value, so when the piece is blocked it
   keeps reporting 'rotate'/'move' while nothing changes. aiStep
   then returns true forever, the frame loop spins 40 no-op
   iterations, gameOver() is never reached, and infinite mode stops
   dead — at 60fps, with no error, no exception and a responsive
   UI. It reads as "the AI gave up", i.e. as weak training rather
   than as a defect.

   Why blocking happens at all: legalPlacements enumerates hard
   drops from above, but this animator rotates and slides along the
   spawn row first. Once the stack reaches that row the slide is
   obstructed, so the placement is legal while the *path to it* is
   not. The engine and the animator disagree about reachability.

   engine now returns 'blocked' AND hard drops on its own, so the
   engine alone cannot freeze. The backstop below stays anyway,
   because relying on a single signal is what produced this class
   of bug all day.

   The backstop keys off game.pieces, NOT the piece pose. engine
   pointed out the flaw in a pose comparison: if a blocked piece
   sat at the spawn pose, the lock spawns the next piece at that
   same pose, so "pose unchanged" would read as "no progress" when
   a lock in fact happened — and we would hard drop a second time.
   The piece counter marks a lock exactly, with no such ambiguity.
   (Measured over 40 seeds / 678 locks the two never disagreed, but
   the ambiguity is real in principle, so use the exact signal.) */
play.aiStep = function () {
  const g = play.game;
  if (!play.aiTarget) {
    if (!play.hint) play.computeHint();
    if (!play.hint) { play.aiTarget = null; return false; }
    play.aiTarget = play.hint.p;
  }

  const piecesBefore = g.pieces;
  const a0 = g.active;
  const poseBefore = a0 ? `${a0.rot},${a0.c},${a0.r}` : null;

  const what = g.stepToward(play.aiTarget);

  /* 'blocked' (engine): the animator could not reach the requested
     placement, so the engine hard dropped where it was. The piece
     IS locked — same as 'drop' — but it landed somewhere the
     policy did not choose. Both must clear aiTarget, or the next
     piece would be steered toward a stale goal.
     g.pieces !== piecesBefore catches any other lock path. */
  const locked = what === 'drop' || what === 'blocked' || g.pieces !== piecesBefore;
  if (locked) {
    play.aiStats.pieces++;
    if (what === 'blocked') play.aiStats.blocked = (play.aiStats.blocked || 0) + 1;
    play.aiTarget = null;
    play.hint = null;
    if (g.lastInfo) play.afterLock(g.lastInfo);
    else if (g.gameOver) play.gameOver();
    return true;
  }

  if (what === 'done') {
    /* engine confirms 'done' means exactly: no active piece, or
       already over. Drop the stale target so the next frame
       re-plans instead of steering a dead pose. */
    play.aiTarget = null;
    play.hint = null;
    if (g.gameOver) play.gameOver();
    return false;
  }

  /* 'rotate'/'move' reported, no lock, and nothing actually moved:
     truly stuck. Should be unreachable now that engine resolves
     this itself; kept so a future change to either side cannot
     resurrect a silent freeze. */
  const a1 = g.active;
  const poseAfter = a1 ? `${a1.rot},${a1.c},${a1.r}` : null;
  if (poseBefore !== null && poseBefore === poseAfter) {
    play.aiStats.backstop = (play.aiStats.backstop || 0) + 1;
    const info = g.hardDrop();
    play.aiStats.pieces++;
    play.aiTarget = null;
    play.hint = null;
    if (info) play.afterLock(info);
    else if (g.gameOver) play.gameOver();
    return true;
  }
  return true;
};

/* --- main loop ---------------------------------------------- */
play.frame = function (ts) {
  const dt = play.last ? Math.max(0, Math.min(100, ts - play.last)) : 16;
  play.last = ts;

  if (play.flashT > 0) {
    play.flashT -= dt;
    if (play.flashT <= 0) play.clearFlash = null;
  }

  const g = play.game;

  /* Infinite-mode recovery.

     Restarting used to depend on afterLock() firing on the lock
     that ended the game. But the game can reach game_over by
     paths that never deliver that info — hardDrop() returns null
     once the state is already over, and a placement can set
     game_over on the following spawn. When that happened the AI
     block below was skipped (it requires !gameOver), so nothing
     restarted and the screen sat frozen at 60fps with no error.

     Checking the terminal state here instead of trusting one
     notification path covers all of them. */
  if (g && !play.paused && g.gameOver && $('#opt-autoplay').checked) {
    play.gameOver();          // records the result, then resets
    play.raf = requestAnimationFrame(play.frame);
    return;
  }

  if (g && !play.paused && !g.gameOver) {
    const autop = $('#opt-autoplay').checked;

    if (autop) {
      if (!play.aiStats.t0) play.aiStats.t0 = Date.now();
      const sp = AI_SPEEDS[+$('#ai-speed').value] || AI_SPEEDS[2];
      if (sp.ms === 0) {
        for (let n = 0; n < 40 && !g.gameOver; n++) if (!play.aiStep()) break;
      } else {
        play.aiAcc += dt;
        while (play.aiAcc >= sp.ms && !g.gameOver) { play.aiAcc -= sp.ms; play.aiStep(); }
      }
    } else {
      /* gravity + lock delay live entirely inside the engine */
      const info = g.tick(dt);
      if (info) play.afterLock(info);

      /* DAS / ARR */
      for (const a of ['left', 'right', 'soft']) {
        if (!play.keys[a]) continue;
        play.dasT[a] += dt;
        if (play.dasT[a] < DAS) continue;
        play.arrT[a] += dt;
        const rate = a === 'soft' ? SOFT_ARR : ARR;
        while (play.arrT[a] >= rate) { play.arrT[a] -= rate; play.doAction(a); }
      }
    }

    if (play.hintDirty) { play.computeHint(); play.hintDirty = false; }
  }

  play.updateHud();
  play.draw();
  play.raf = requestAnimationFrame(play.frame);
};

/* --- option wiring ------------------------------------------ */
$('#opt-hint').addEventListener('change', () => { play.hintDirty = true; });

$('#opt-autoplay').addEventListener('change', function () {
  const on = this.checked;
  $('#ai-banner').classList.toggle('show', on);
  $('#ai-stats-panel').style.display = on ? '' : 'none';
  if (on) {
    play.aiStats = { games: 0, best: 0, sumLines: 0, pieces: 0, t0: Date.now() };
    setBannerText();
    if (play.game.gameOver) play.reset();
    play.overlay(false);
  }
  play.aiTarget = null;
  play.hintDirty = true;
});

function setBannerText() {
  const s = P.byId($('#opt-agent').value);
  $('#ai-banner-txt').textContent = `${s ? s.label : 'AI'} 자동 플레이 중 · 게임오버 시 자동 재시작`;
}

$('#opt-agent').addEventListener('change', () => {
  play.hint = null; play.aiTarget = null; play.hintDirty = true;
  setBannerText();
});

$('#ai-speed').addEventListener('input', function () {
  $('#ai-speed-val').textContent = (AI_SPEEDS[+this.value] || AI_SPEEDS[2]).label;
});

/* ============================================================
   ② ARENA
   ============================================================ */
const arena = new Arena({
  gridEl: $('#arena-grid'),
  lbEl: $('#lb-list'),
  strategies: P.STRATEGIES,
  onStats: (s) => {
    $('#ar-elapsed').textContent = `${s.elapsed.toFixed(1)}초`;
    $('#ar-total-pieces').textContent = s.totalPieces;
    $('#ar-fps').textContent = s.fps;
  },
});

function syncArenaButton() {
  $('#ar-toggle').textContent = arena.running ? '일시정지' : '시작';
  $('#ar-toggle').classList.toggle('primary', !arena.running);
}

$('#ar-toggle').addEventListener('click', () => {
  if (arena.running) arena.stop(); else arena.start();
  syncArenaButton();
});
$('#ar-reset').addEventListener('click', () => { arena.stop(); arena.reset(); syncArenaButton(); });
$('#arena-speed').addEventListener('input', function () {
  arena.setSpeed(+this.value);
  $('#arena-speed-val').textContent = arena.speedLabel();
});
$('#ar-same-seed').addEventListener('change', function () { arena.sameSeed = this.checked; arena.reset(); });
$('#ar-autorestart').addEventListener('change', function () { arena.autoRestart = this.checked; });
$('#ar-seed').addEventListener('change', function () {
  arena.seed = (parseInt(this.value, 10) || 1) >>> 0;
  arena.reset();
});
$('#ar-greedy').addEventListener('change', function () {
  P.options.greedyPolicy = this.checked;
  arena.reset();
});

$$('#lb-metric-tabs .mt').forEach(b => b.addEventListener('click', () => {
  $$('#lb-metric-tabs .mt').forEach(x => x.classList.toggle('active', x === b));
  arena.setMetric(b.dataset.metric);
}));


/* ============================================================
   Difficulty (정보량 축소)
   ============================================================ */
function fillDifficultySelect() {
  const sel = $('#opt-difficulty');
  sel.innerHTML = '';
  for (const d of P.DIFFICULTIES) {
    const o = document.createElement('option');
    o.value = d.id;
    o.textContent = `${d.label} — 미리보기 ${d.nextCount}개${d.hold ? ' + 홀드' : ''}`;
    sel.appendChild(o);
  }
  sel.value = P.difficulty.current;
  applyDifficultyNote();
}

function applyDifficultyNote() {
  const d = P.difficultyById(P.difficulty.current);
  $('#difficulty-note').textContent = d.note;
  /* hold is meaningless in hard/extreme, so hide the panel rather
     than leave a control that silently does nothing */
  const holdPanel = $('#hold-panel');
  if (holdPanel) holdPanel.style.display = d.hold ? '' : 'none';

  /* Hide the 홀드 row in the key help too. Leaving it listed
     advertises a key that does nothing in hard/extreme, which
     reads as a broken control rather than a removed feature.
     Found by looking at the screenshot — the programmatic check
     only asked whether the key errors, and it does not. */
  const holdRow = $$('#key-help .row').find(r => r.textContent.includes('홀드'));
  if (holdRow) holdRow.style.display = d.hold ? '' : 'none';
  const badge = $('#difficulty-badge');
  if (badge) {
    badge.textContent = d.label;
    badge.style.display = d.id === 'normal' ? 'none' : '';
  }
}

$('#opt-difficulty').addEventListener('change', function () {
  P.difficulty.current = this.value;
  applyDifficultyNote();
  /* A state carries the difficulty it was created with, so the new
     mode can only take effect on a new game. Restarting here is
     honest; mutating a live game's difficulty mid-drop would not be. */
  play.reset();
});

/* ARENA experiments: the 8-strategy grid, or one strategy run
   under all three difficulties side by side.

   The second one exists to show a specific claim: 1-ply search
   loses its entire advantage in extreme, because with no preview
   there is nothing to expand and it degenerates into the same
   greedy policy as CEM — the two already share weights. That is
   the study plan's week-4 question ("is there a model you can roll
   forward?") turned into something you can watch. */
function fillDiffStratSelect() {
  const sel = $('#ar-diff-strat');
  sel.innerHTML = '';
  for (const s of P.STRATEGIES) {
    if (!P.isReady(s)) continue;
    const o = document.createElement('option');
    o.value = s.id;
    o.textContent = s.label;
    sel.appendChild(o);
  }
  /* default to the strategy the experiment is actually about */
  if (Array.from(sel.children).some(o => o.value === 'search_1ply')) sel.value = 'search_1ply';
}

function applyExperiment() {
  const kind = $('#ar-experiment').value;
  const diffSel = $('#ar-diff-strat');
  diffSel.style.display = kind === 'difficulty' ? '' : 'none';

  if (kind === 'difficulty') {
    const base = P.byId(diffSel.value) || P.byId('search_1ply');
    if (!base) return;
    arena.setStrategies(P.DIFFICULTIES.map(d => ({
      ...base,
      modeOverride: d.id,
      short: `${base.short} · ${d.label}`,
    })));
    /* Line count cannot separate these: checker measured all 9
       mode x strategy cells surviving to the piece cap, so lines
       just equal the cap. Score is sensitive because B2B and combo
       reward tetrises, so the comparison defaults to it. The line
       axis stays available. */
    arena.setMetric('score');
    const scoreTab = document.querySelector('#lb-metric-tabs .mt[data-metric="score"]');
    if (scoreTab) $$('#lb-metric-tabs .mt').forEach(x => x.classList.toggle('active', x === scoreTab));
  } else {
    arena.setStrategies(P.STRATEGIES);
  }
  arena.sizeCanvases();
  syncArenaButton();
}

$('#ar-experiment').addEventListener('change', applyExperiment);
$('#ar-diff-strat').addEventListener('change', applyExperiment);

globalThis.addEventListener('resize', () => {
  if (currentScreen === 'arena') arena.sizeCanvases();
  if (currentScreen === 'play') play.resize();
});

/* ============================================================
   ③ LEARN
   ============================================================ */
let learnSelected = 'a2c';

function buildLearnAxis() {
  const host = $('#axis-list');
  host.innerHTML = '';
  const line = () => { const l = document.createElement('div'); l.className = 'axis-line'; return l; };

  P.STRATEGIES.forEach((s, i) => {
    if (i > 0) host.appendChild(line());
    const d = document.createElement('div');
    d.className = 'axis-item' + (s.id === learnSelected ? ' selected' : '');
    d.dataset.id = s.id;
    d.innerHTML = `<div class="num">${s.plan}</div>
                   <div class="body"><div class="nm"></div><div class="role"></div></div>`;
    d.querySelector('.nm').textContent = s.label;
    d.querySelector('.role').textContent = s.critic;
    d.addEventListener('click', () => selectLearn(s.id));
    host.appendChild(d);
  });

  /* ⑤ and ⑥ — deliberately excluded, greyed out with the reason */
  P.EXCLUDED.forEach((x) => {
    host.appendChild(line());
    const d = document.createElement('div');
    d.className = 'axis-item excluded';
    d.innerHTML = `<div class="num">${x.plan}</div>
                   <div class="body"><div class="nm"></div><div class="why-out"></div></div>`;
    d.querySelector('.nm').textContent = `${x.label} — 제외`;
    d.querySelector('.why-out').textContent = x.reason;
    host.appendChild(d);
  });
}

function selectLearn(id) {
  learnSelected = id;
  $$('#axis-list .axis-item').forEach(e => e.classList.toggle('selected', e.dataset.id === id));
  renderLearnDetail(id);
}

/* Small inline sparkline of meta.history, so "why is ① unstable"
   is visible rather than asserted. */
function sparkline(values, w = 320, h = 46, color = '#4da3ff') {
  const nums = values.filter(v => typeof v === 'number' && isFinite(v));
  if (nums.length < 2) return '';
  const min = Math.min(...nums), max = Math.max(...nums);
  const span = max - min || 1;
  const pts = nums.map((v, i) =>
    `${(i / (nums.length - 1) * (w - 2) + 1).toFixed(1)},${(h - 1 - (v - min) / span * (h - 2)).toFixed(1)}`
  ).join(' ');
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" preserveAspectRatio="none">
            <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.5"/>
          </svg>
          <div class="spark-cap">최소 ${min.toFixed(1)} · 최대 ${max.toFixed(1)} · ${nums.length}점</div>`;
}

function esc(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}


/* rl's cross-strategy comparison table.

   Two rules from rl and the lead, both about not overclaiming:
   (1) Strategies that survived every seed (games_hit_cap == n_games)
       are NOT ranked against each other. 7,997.8 vs 7,998.3 vs
       7,998.5 is seed rounding, not skill, and printing an order
       would assert a difference the data does not support. They are
       shown as "상한 도달 · 죽지 않음" with the line count as a
       lower bound.
   (2) The 100x gap between those and the critic-based four IS the
       point of the app, so the bar is drawn on a log scale to keep
       both ends readable at once.                                 */
function median(xs) {
  if (!xs || !xs.length) return null;
  const a = xs.slice().sort((x, y) => x - y);
  const m = a.length >> 1;
  return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
}

function renderEvalTable(currentId) {
  const sum = P.EVAL_SUMMARY;
  if (!sum?.strategies) return '';
  const entries = Object.entries(sum.strategies)
    .map(([id, v]) => ({ id, ...v }))
    .sort((a, b) => (a.panel || 99) - (b.panel || 99));
  if (!entries.length) return '';

  /* Completeness guard. eval_summary is regenerated by rl, and a
     partial regeneration silently produced a one-row table that
     looked perfectly fine. Missing data must never be able to
     impersonate complete data, so say what is absent. */
  const expected = P.STRATEGIES.length;
  const missing = P.STRATEGIES.filter(s => !sum.strategies[s.id]).map(s => s.label);
  const warnHtml = missing.length
    ? `<div class="eval-warn">이 표에 ${entries.length}/${expected} 전략만 들어 있다.
       빠진 것: ${esc(missing.join(', '))}.
       <b>rl 이 eval_summary.json 을 다시 만들면 채워진다</b> — 표가 짧은 것은 성적이 아니라 데이터 누락이다.</div>`
    : '';

  /* Median is the primary statistic. rl's own check found dqn's
     mean was 53% carried by a single 547-line game (mean 103.7 vs
     median 49), which flips its order against A2C. Heavy tails are
     the norm here, so the mean is shown beside it and a wide
     mean/median gap is called out rather than smoothed over. */
  const withStats = entries.map((e) => {
    const lines = (e.per_game || []).map(g => g.lines).filter(v => typeof v === 'number');
    const med = e.median_lines ?? median(lines);
    const mean = e.mean_lines ?? null;
    const ratio = (med && mean) ? mean / med : null;
    return { ...e, med, mean, ratio, n: lines.length };
  });

  const maxV = Math.max(...withStats.map(e => e.med ?? e.mean ?? 0), 1);
  const logw = (v) => (v <= 0 ? 0 : Math.log10(1 + v) / Math.log10(1 + maxV) * 100);
  const fmtPct = (p) => (p === 0 ? '0%' : p < 1 ? p.toFixed(2) + '%' : p.toFixed(1) + '%');
  const tetrisPct = (v) => (v || 0) * 100;

  const rows = withStats.map((e) => {
    const survived = e.n_games > 0 && e.games_hit_cap === e.n_games;
    const primary = e.med ?? e.mean ?? 0;
    const shown = survived
      ? `${Math.floor(primary).toLocaleString()}줄+`
      : primary.toFixed(1);
    const note = survived ? '상한 도달 · 죽지 않음' : `${e.games_hit_cap ?? 0}/${e.n_games ?? 0} 완주`;
    /* a mean far above the median means one lucky game is carrying
       the average — worth seeing, not hiding */
    const heavy = e.ratio && e.ratio >= 1.5;
    const meanCell = e.mean == null ? '—'
      : `${e.mean.toFixed(1)}${heavy ? ` <span class="heavy" title="평균이 중앙값의 ${e.ratio.toFixed(1)}배 — 한두 판이 평균을 끌어올린다">▲${e.ratio.toFixed(1)}×</span>` : ''}`;
    return `<tr class="${e.id === currentId ? 'here' : ''}${survived ? ' survivor' : ''}">
      <td>${esc(e.label || e.id)}</td>
      <td><b>${shown}</b><br><span class="muted" style="font-size:10px">${note}</span></td>
      <td>${meanCell}</td>
      <td>${(e.median_norm_score_per_piece ?? e.mean_norm_score_per_piece ?? 0).toFixed(2)}</td>
      <td>${fmtPct(tetrisPct(e.median_tetris_rate ?? e.mean_tetris_rate))}</td>
      <td style="width:22%"><span class="bar" style="width:${logw(primary).toFixed(1)}%"></span></td>
    </tr>`;
  }).join('');

  return `<div class="panel"><h3>전략 비교 · rl 평가 (홀드아웃 시드 ${sum.seeds?.length ?? '?'}개, 조각 상한 ${(sum.piece_cap ?? 0).toLocaleString()})</h3>
    ${warnHtml}
    <div style="overflow-x:auto">
    <table class="eval-table">
      <tr><th>전략</th><th>중앙값 줄</th><th>평균</th>
          <th title="레벨로 나눈 조각당 점수. 레벨이 오르면 같은 줄도 점수가 커지므로 보정해야 비교가 된다">레벨보정 점수/조각</th>
          <th>테트리스</th><th>중앙값 줄 (로그)</th></tr>
      ${rows}
    </table></div>
    <div style="font-size:11px;color:var(--fg-faint);line-height:1.6;margin-top:8px">
      <b>중앙값</b>이 주 통계다 — 이 분포는 꼬리가 두꺼워서 한 판이 평균을 크게 흔든다.
      <b>▲</b> 표시는 평균이 중앙값의 1.5배 이상이라는 뜻이고, 그 자체가 결과다.<br>
      막대는 <b>로그 스케일</b>이다 — 전략 간 차이가 100배라 선형으로는 한쪽이 안 보인다.
      "상한 도달" 전략끼리는 <b>순위를 매기지 않았다.</b> 셋 다 한 판도 죽지 않았다.
    </div></div>`;
}

function renderLearnDetail(id) {
  const host = $('#learn-detail');
  const s = P.byId(id);
  if (!host || !s) return;

  const model = P.modelFor(s);
  const meta = model?.meta || {};
  const ready = P.isReady(s);

  /* rl's meta.critic_role is worded to match the study-plan
     document, but it is currently English while the rest of the UI
     is Korean — and this paragraph is the whole reason the screen
     exists for a Korean-speaking learner. So: show Korean prose as
     the body, and quote rl's original verbatim underneath, clearly
     labelled. Nothing here is a machine translation of rl's text;
     the Korean is separately authored from PROJECT.md's table.
     When rl supplies Korean, this collapses back to one block. */
  /* rl now ships critic_role_ko (Korean, worded to match the study
     plan's vocabulary) alongside the English critic_role. Prefer the
     Korean; fall back to English-with-quotation, then to our own
     prose. rl asked that their wording be used verbatim, so nothing
     here paraphrases it. */
  const hasKorean = (t) => /[\uac00-\ud7a3]/.test(t || '');
  const rlKo = meta.critic_role_ko || '';
  const rlEn = meta.critic_role || '';
  const rlText = hasKorean(rlKo) ? rlKo : rlEn;
  const criticText = hasKorean(rlText) ? rlText : s.criticRole;
  const originalHtml = (rlText && !hasKorean(rlText))
    ? `<div class="critic-original"><span class="src">rl 원문 (meta.critic_role)</span>${esc(rlText)}</div>`
    : '';
  const planNo = meta.study_plan_number !== undefined ? `공부계획 ${meta.study_plan_number}` : s.plan;

  let evalHtml;
  if (!s.needsWeights) {
    evalHtml = `<div class="eval-box"><div class="hd">학습 성적</div>
                학습하지 않는 전략이다. 성적은 전략 비교 화면에서 직접 확인한다.</div>`;
  } else if (!ready) {
    evalHtml = `<div class="eval-box"><div class="hd">학습 성적</div>
                <span class="tag warn">학습 전</span>
                이 전략의 가중치 파일이 아직 없다. 전략 비교 화면에서 <b>실행되지 않는다</b>.</div>`;
  } else {
    const ev = meta.eval || {};
    /* Lead directive: median is the primary statistic everywhere,
       not just in the comparison table. DQN's mean of 103.7 is 53%
       carried by one 547-line game (median 40), so leading with the
       mean here while the table leads with the median would show
       the same strategy two different ways on one screen. */
    const evLines = Array.isArray(ev.lines) ? ev.lines : [];
    const med = ev.median_lines ?? median(evLines);
    const mean = ev.mean_lines;
    const ratio = (med && mean) ? mean / med : null;
    const heavy = ratio && ratio >= 1.5;
    const capped = Array.isArray(ev.hit_cap) && ev.hit_cap.some(Boolean);
    evalHtml = `<div class="eval-box"><div class="hd">학습 성적 · rl 보고값 (홀드아웃 시드)</div>
      ${med != null ? `중앙값 <b>${esc(med)}</b>줄` : ''}
      ${mean != null ? ` · 평균 ${esc(mean)}줄` : ''}
      ${heavy ? ` <span class="heavy" title="평균이 중앙값의 ${ratio.toFixed(1)}배">▲${ratio.toFixed(1)}×</span>` : ''}
      ${ev.min_lines !== undefined ? ` · 최소 ${esc(ev.min_lines)} · 최대 ${esc(ev.max_lines)}` : ''}
      ${ev.seeds ? ` · 시드 ${ev.seeds.length}개` : ''}
      ${meta.episodes ? ` · 에피소드 ${esc(meta.episodes)}` : ''}
      ${heavy ? `<br><span class="muted">평균이 중앙값보다 ${ratio.toFixed(1)}배 높다 — 한두 판이 평균을 끌어올렸다는 뜻이므로 중앙값으로 읽어라.</span>` : ''}
      ${capped ? '<br><span class="muted">전 게임이 평가 상한(조각 수)에서 종료됐다. 죽어서 끝난 것이 아니다.</span>' : ''}
      ${ev.greedy ? '<br><span class="muted">탐욕(argmax) 기준 평가값이다.</span>' : ''}
      </div>`;
  }

  const flags = [];
  if (meta.has_critic !== undefined) flags.push(`크리틱 ${meta.has_critic ? '있음' : '없음'}`);
  if (meta.bootstraps !== undefined) flags.push(`부트스트랩 ${meta.bootstraps ? '함' : '안 함'}`);
  const flagHtml = flags.length
    ? `<div class="flag-row">${flags.map(f => `<span class="tag">${esc(f)}</span>`).join(' ')}</div>` : '';

  const hist = Array.isArray(meta.history) ? meta.history : null;
  const histLines = hist?.map(h => h.mean_lines ?? h.lines ?? h.mean_return).filter(v => v !== undefined) || [];
  const sparkHtml = histLines.length > 1
    ? `<div class="panel"><h3>학습 곡선 · 에피소드별 평균 줄 수</h3>${sparkline(histLines, 320, 46, s.color)}</div>`
    : '';

  const offPolicy = meta.off_policy_reason
    ? `<div class="panel"><h3>오프폴리시인 이유</h3><div class="prose">${esc(meta.off_policy_reason)}</div></div>` : '';

  host.innerHTML = `
    <div class="detail-head"><h2></h2><span class="tag">${esc(planNo)}</span></div>
    <div class="lead"></div>
    <div class="formula"></div>
    ${flagHtml}
    <div class="panel"><h3>여기서 크리틱은 무엇을 하는가</h3>
      <div class="prose critic-prose"></div>${originalHtml}</div>
    ${evalHtml}
    ${renderEvalTable(id)}
    ${sparkHtml}
    ${offPolicy}
    <div id="learn-solo">
      <span class="txt">이 전략만 전략 비교 화면에서 단독으로 실행한다.</span>
      <button class="btn primary" id="solo-run" ${ready || !s.needsWeights ? '' : 'disabled'}>단독 실행</button>
      <button class="btn" id="solo-all">${P.STRATEGIES.length}개 전체로 되돌리기</button>
    </div>
    ${(s.id === 'cem_linear' || s.id === 'dellacherie') ? `<div class="obs-caveat">
      <b>이 실험대에서의 관측 — 진화탐색이 사람의 손 가중치를 넘지 못했다.</b><br>
      레벨보정 조각당 점수 중앙값: <code>델라체리 50.39 &nbsp;&gt;&nbsp; CEM 선형 48.95</code>.
      고정 조각 수·동일 시드 비교에서 <b>8시드 중 7시드</b>에서 델라체리가 앞선다.<br><br>
      <b>줄 수로는 이 사실이 보이지 않는다</b> — 둘 다 평가 상한까지 안 죽어서 중앙값이 똑같이 1,198줄이다.
      지표를 점수로 바꾸지 않았으면 영영 드러나지 않았을 결과다.<br><br>
      단정할 수 있는 범위를 지킨다: <b>"CEM 이 실패했다"가 아니라
      "이 특징 집합과 이 학습 예산에서는 사람이 정한 가중치를 넘지 못했다"</b>이다.
      델라체리 가중치는 이 8개 특징에 맞춰 사람이 오래 다듬은 것이고, CEM 은 같은 특징 공간 안에서
      그것을 개선하려 한 것이다. 개선 대상을 못 넘었다는 뜻이므로,
      <b>패널 2를 단순 대조군으로 두었던 전제 자체가 흔들린다.</b>
    </div>` : ''}
    ${s.id === 'search_1ply' ? `<div class="obs-caveat">
      <b>이 실험대에서의 관측 — 탐색이 이득이 아니었다.</b><br>
      평가 규칙을 표준(말단만 평가)으로 고친 뒤에도 <b>탐색 없는 CEM 이 더 높다.</b>
      레벨 보정 조각당 점수, 같은 상한(3,000조각):
      <br><br>
      <code>델라체리 50.39 &nbsp;&gt;&nbsp; CEM 48.95 &nbsp;&gt;&nbsp; 1수 탐색(합산·폐기) 47.02 &nbsp;&gt;&nbsp; 1수 탐색(말단) 46.75</code>
      <br><br>
      규칙 수정 자체는 옳았다 — 옛 규칙은 깊이가 다른 두 국면의 가치를 더해
      중간 노드를 두 번 세고, <code>landing_height</code>·<code>eroded_piece_cells</code> 를
      서로 다른 두 배치에 대해 더했다. 하지만 <b>고쳐도 결론은 바뀌지 않았다.</b><br><br>
      유력한 설명은 가중치가 <b>1스텝 그리디 평가용으로 학습됐다</b>는 것이었다.
      <b>그래서 탐색을 롤아웃 안에 넣고 CEM 을 다시 학습시켜 봤다 — 패널 13이다.</b><br><br>
      <b>그래도 안 된다.</b> 재학습해도 <code>46.75 → 46.88</code> 로 거의 그대로이고
      탐색 없는 CEM(48.95)에 여전히 진다. <b>즉 "가중치가 그리디용이라서"라는 설명도
      이 실험대에서는 지지되지 않는다.</b> 남는 해석은 <b>이 특징 8개로는 한 수 앞을 보는 것이
      이득이 되지 않는다</b>는 것이다.<br><br>
      위 설명의 "탐색이 정책 개선을 한다"는 <b>일반론이며, 이 실험대의 데이터는 그것을 지지하지 않는다.</b>
      폐기된 합산 규칙은 패널 10 에 보존해 나란히 비교할 수 있다.
    </div>` : ''}
    ${s.id === 'reinforce_baseline' ? `<div class="panel"><h3>①과 나란히 볼 것 · 공부계획 1주차</h3>
      <div class="prose">기준선은 갱신 방향의 <b>기댓값을 바꾸지 않고 분산만 깎는다</b>.
      Σ_a b(s)·∇π(a|s) = b(s)·∇1 = 0 이므로 v̂ 를 빼도 경사의 기댓값은 그대로다.
      성적으로도 ① REINFORCE 보다 위에 있고, <b>평균뿐 아니라 중앙값에서도 유지된다</b>
      (중앙값 55.0 → 71.5). 한두 판이 평균을 끌어올린 우연이 아니라는 뜻이다.<br><br>
      다만 ③ A2C 와는 <b>이 예산에서 갈리지 않았다</b> (중앙값 71.5 vs 69.5).
      시드 간 분산이 두 값의 차이보다 커서 우열을 판정할 수 없다 — 그것도 결과다.</div>
    </div>` : ''}
    ${s.id === 'search_1ply' ? `<div class="panel"><h3>난이도 모드와의 연결 · 공부계획 4주차</h3>
      <div class="prose">4주차의 질문은 <b>"미리 돌려볼 수 있는 모델이 있나?"</b>이다.
      이 전략은 다음 조각을 알기 때문에 한 수를 미리 굴려볼 수 있고, 그래서 같은 가치함수를 쓰는
      CEM 선형보다 낫다. <b>익스트림 모드는 그 전제를 걷어낸다</b> — 미리보기가 없으면 전개할 것이
      없으므로 1수 탐색은 0수 탐색이 되고, 가중치가 동일하므로 CEM 과 같은 플레이가 된다.
      전략 비교 화면의 <b>난이도 3단계 비교</b> 실험에서 그 이득이 사라지는 것을 직접 볼 수 있다.</div>
    </div>` : ''}`;

  host.querySelector('h2').textContent = s.label;
  host.querySelector('.lead').textContent = s.tagline;
  /* Prefer the formula from the weight FILE. The file also carries
     the rule the engine actually executes (meta.search.rule), so
     sourcing both from one place means the displayed maths cannot
     drift from the running maths. Our static string is only a
     fallback for strategies with no weight file (Random,
     Dellacherie) — and it had already drifted once. */
  host.querySelector('.formula').textContent = meta.update_formula || s.formula;
  host.querySelector('.critic-prose').textContent = criticText;

  host.querySelector('#solo-run').addEventListener('click', () => {
    arena.setStrategies([s]);
    showScreen('arena');
    arena.sizeCanvases();
    arena.start();
    syncArenaButton();
  });
  host.querySelector('#solo-all').addEventListener('click', () => {
    arena.setStrategies(P.STRATEGIES);
    showScreen('arena');
    arena.sizeCanvases();
    syncArenaButton();
  });
}

/* ============================================================
   Boot
   ============================================================ */
function fillAgentSelect() {
  const sel = $('#opt-agent');
  const prev = sel.value;
  sel.innerHTML = '';
  for (const s of P.STRATEGIES) {
    const o = document.createElement('option');
    o.value = s.id;
    const ready = P.isReady(s);
    o.textContent = ready ? s.label : `${s.label} — 학습 전`;
    o.disabled = !ready;
    sel.appendChild(o);
  }
  const want = [prev, 'cem_linear', 'dellacherie'].find(id => P.isReady(P.byId(id)));
  sel.value = want || 'random';
  setBannerText();
}

async function boot() {
  /* Two different checks, and both are needed.
     selfTest  — the feature FUNCTIONS vs rl's fixtures.
     liveTest  — the feature INPUTS, by playing a real game and
                 asserting nothing is stuck constant. This is the
                 one that would have caught landing_height dying;
                 fixtures never touch the engine's info dict. */
  if (!P.selfTest(true)) {
    console.warn('특징 함수 파리티 실패 — 학습 가중치의 점수를 신뢰할 수 없다.');
  }
  if (!P.liveTest(true)) {
    console.warn('실게임에서 상수인 특징이 있다 — 엔진 info 계약이 바뀌었는지 확인하라.');
  }

  buildLearnAxis();
  fillDifficultySelect();

  /* Derive every "N panels" label from the actual strategy list.
     They read "8" after panel 9 was added, and a hardcoded count
     goes stale every time the roster changes.

     Deliberately keyed off EXISTING structure rather than new ids:
     index.html is edited by more than one process, and an earlier
     pass that added ids here was reverted — which would have made
     these silently do nothing. Structural selectors survive that. */
  const nStrat = P.STRATEGIES.length;

  const tabHint = document.querySelector('.tab[data-screen="arena"] .tab-hint');
  if (tabHint) tabHint.textContent = `${nStrat}개 동시 실행`;

  const allOpt = document.querySelector('#ar-experiment option[value="all8"]');
  if (allOpt) allOpt.textContent = `${nStrat}전략 비교`;

  /* the leaderboard footnote carries a panel count in prose */
  const lbNote = document.querySelector('#arena-board > div:last-child');
  if (lbNote) lbNote.innerHTML = lbNote.innerHTML.replace(/\d+개 패널/, `${nStrat}개 패널`);

  $('#arena-speed-val').textContent = arena.speedLabel();
  $('#ai-speed-val').textContent = AI_SPEEDS[+$('#ai-speed').value].label;
  arena.setSpeed(+$('#arena-speed').value);
  arena.seed = (parseInt($('#ar-seed').value, 10) || 1) >>> 0;
  P.options.greedyPolicy = $('#ar-greedy').checked;

  play.resize();
  play.reset();
  play.raf = requestAnimationFrame(play.frame);

  /* Weights arrive asynchronously; everything above already works
     without them (Random and Dellacherie need no file). */
  try { await P.loadWeights(); } catch (e) { console.error('[weights] 로딩 실패:', e); }
  refreshWeightStatus();
  fillAgentSelect();
  fillDiffStratSelect();
  arena.refreshReadiness();
  arena.reset();
  renderLearnDetail(learnSelected);
  syncArenaButton();
}

boot();

/* console debugging handle */
globalThis.TetrisUI = { play, arena, P, E, showScreen };
