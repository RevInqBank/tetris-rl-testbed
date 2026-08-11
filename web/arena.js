/* ============================================================
   arena.js — bitboard renderer + the 8-panel comparison
   Owner: web        ES module.

   Board (docs/spec.md §1): `rows` is a 22-entry array of ints;
   bit x of rows[y] is column x. y=0,1 are the hidden spawn
   buffer, y=2..21 are the visible 20 rows.

   The bitmask stores OCCUPANCY ONLY — no piece type — so locked
   cells are drawn in a neutral stack colour. PLAY keeps its own
   colour layer (see ui.js) to get the 7 piece colours; ARENA does
   not, because 8 small boards read better in one colour anyway.
   ============================================================ */

import * as E from './engine.js';
import * as P from './policies.js';

export const COLS = E.W, ROWS = E.ROWS, VIS = E.VISIBLE_ROWS, BUF = E.BUFFER_ROWS;

/* piece idx 0..6 = I,O,T,S,Z,J,L (docs/spec.md §2) */
export const PIECE_COLORS = [
  '#2ad4e6',  // 0 I cyan
  '#f2d024',  // 1 O yellow
  '#b45cf0',  // 2 T purple
  '#3fd15b',  // 3 S green
  '#f0524f',  // 4 Z red
  '#3b82f6',  // 5 J blue
  '#f5921e',  // 6 L orange
];
const STACK_COLOR = '#5b6b7d';

/* ------------------------------------------------------------
   drawing
   ------------------------------------------------------------ */
function shade(hex, amt) {
  const n = parseInt(hex.slice(1), 16);
  const r = Math.min(255, Math.max(0, ((n >> 16) & 255) + amt));
  const g = Math.min(255, Math.max(0, ((n >> 8) & 255) + amt));
  const b = Math.min(255, Math.max(0, (n & 255) + amt));
  return `rgb(${r},${g},${b})`;
}

function drawCell(ctx, x, y, s, color, alpha) {
  ctx.globalAlpha = alpha === undefined ? 1 : alpha;
  ctx.fillStyle = color;
  ctx.fillRect(x, y, s, s);
  if (s >= 10) {
    const e = Math.max(1, Math.round(s * 0.15));
    ctx.fillStyle = shade(color, 45);
    ctx.fillRect(x, y, s, e);
    ctx.fillStyle = shade(color, -55);
    ctx.fillRect(x, y + s - e, s, e);
  }
  ctx.globalAlpha = 1;
}

function drawGrid(ctx, w, h, s) {
  ctx.strokeStyle = 'rgba(255,255,255,0.05)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let x = s; x < w; x += s) { ctx.moveTo(x + .5, 0); ctx.lineTo(x + .5, h); }
  for (let y = s; y < h; y += s) { ctx.moveTo(0, y + .5); ctx.lineTo(w, y + .5); }
  ctx.stroke();
}

/* drawBoard(canvas, rows, opts)
     colorGrid    optional 22x10 of piece idx (-1 empty) for colour
     active/ghost/hint  arrays of absolute [y,x]
     activePiece/hintPiece  piece idx for tinting
     clearedRows  [y,...] flashed white
     lockProgress 0..1, draws a lock-delay bar under the piece   */
export function drawBoard(canvas, rows, opts = {}) {
  const ctx = canvas.getContext('2d');
  const off = rows.length >= ROWS ? rows.length - VIS : 0;
  const s = Math.floor(Math.min(canvas.width / COLS, canvas.height / VIS));
  const offX = Math.floor((canvas.width - s * COLS) / 2);
  const offY = Math.floor((canvas.height - s * VIS) / 2);

  ctx.fillStyle = '#0a0e14';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  if (opts.grid !== false && s >= 9) {
    ctx.save(); ctx.translate(offX, offY);
    drawGrid(ctx, s * COLS, s * VIS, s);
    ctx.restore();
  }

  for (let r = 0; r < VIS; r++) {
    const v = rows[off + r];
    if (!v) continue;
    for (let c = 0; c < COLS; c++) {
      if (!((v >> c) & 1)) continue;
      let col = STACK_COLOR;
      if (opts.colorGrid) {
        const t = opts.colorGrid[off + r]?.[c];
        if (t !== undefined && t >= 0) col = PIECE_COLORS[t];
      }
      drawCell(ctx, offX + c * s, offY + r * s, s, col);
    }
  }

  /* AI hint — where the selected policy would put this piece */
  if (opts.hint?.length) {
    const hcol = opts.hintPiece >= 0 ? PIECE_COLORS[opts.hintPiece] : '#4da3ff';
    ctx.setLineDash([3, 2]);
    for (const [y, x] of opts.hint) {
      const yy = y - off;
      if (yy < 0 || yy >= VIS) continue;
      drawCell(ctx, offX + x * s, offY + yy * s, s, hcol, 0.26);
      ctx.strokeStyle = 'rgba(130,195,255,.75)';
      ctx.lineWidth = 1;
      ctx.strokeRect(offX + x * s + .5, offY + yy * s + .5, s - 1, s - 1);
    }
    ctx.setLineDash([]);
  }

  /* ghost — hard drop landing spot */
  if (opts.ghost?.length) {
    ctx.strokeStyle = opts.activePiece >= 0 ? PIECE_COLORS[opts.activePiece] : '#8a97a6';
    ctx.lineWidth = Math.max(1, Math.floor(s * 0.10));
    ctx.globalAlpha = .45;
    for (const [y, x] of opts.ghost) {
      const yy = y - off;
      if (yy < 0 || yy >= VIS) continue;
      ctx.strokeRect(offX + x * s + 1.5, offY + yy * s + 1.5, s - 3, s - 3);
    }
    ctx.globalAlpha = 1;
  }

  /* active piece */
  if (opts.active?.length) {
    const acol = opts.activePiece >= 0 ? PIECE_COLORS[opts.activePiece] : '#e6edf3';
    for (const [y, x] of opts.active) {
      const yy = y - off;
      if (yy < 0 || yy >= VIS) continue;
      drawCell(ctx, offX + x * s, offY + yy * s, s, acol);
    }
    /* lock delay feedback: a thin bar that fills as the 500ms runs
       out, so the player can see how long a tuck still has */
    if (opts.lockProgress > 0) {
      let maxY = -1, minX = COLS, maxX = -1;
      for (const [y, x] of opts.active) { maxY = Math.max(maxY, y - off); minX = Math.min(minX, x); maxX = Math.max(maxX, x); }
      if (maxY >= 0 && maxY < VIS) {
        const bx = offX + minX * s, bw = (maxX - minX + 1) * s;
        const by = offY + (maxY + 1) * s - 2;
        ctx.fillStyle = 'rgba(0,0,0,.45)';
        ctx.fillRect(bx, by, bw, 3);
        ctx.fillStyle = opts.lockProgress > .75 ? '#f0524f' : '#f2d024';
        ctx.fillRect(bx, by, bw * (1 - opts.lockProgress), 3);
      }
    }
  }

  /* line clear flash */
  if (opts.clearedRows?.length) {
    ctx.fillStyle = 'rgba(255,255,255,.75)';
    for (const y of opts.clearedRows) {
      const yy = y - off;
      if (yy >= 0 && yy < VIS) ctx.fillRect(offX, offY + yy * s, s * COLS, s);
    }
  }
}

/* piece preview for hold / next, from the rot-0 cell table */
export function drawPiecePreview(canvas, piece, dim) {
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#0a0e14';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  if (piece === null || piece === undefined || piece < 0) return;
  const sh = E.PIECE_CELLS[piece]?.[0];
  if (!sh) return;

  let minX = 9, maxX = 0, minY = 9, maxY = 0;
  for (const [dx, dy] of sh) {
    minX = Math.min(minX, dx); maxX = Math.max(maxX, dx);
    minY = Math.min(minY, dy); maxY = Math.max(maxY, dy);
  }
  const w = maxX - minX + 1, h = maxY - minY + 1;
  const s = Math.floor(Math.min((canvas.width - 12) / w, (canvas.height - 12) / h));
  const ox = Math.floor((canvas.width - s * w) / 2) - minX * s;
  const oy = Math.floor((canvas.height - s * h) / 2) - minY * s;
  for (const [dx, dy] of sh) {
    drawCell(ctx, ox + dx * s, oy + dy * s, s, PIECE_COLORS[piece], dim ? .3 : 1);
  }
}


/* Share of cleared lines that came from a 4-line clear. This is
   one of the two axes that still separate policies after line
   count saturates (rl: CEM, Dellacherie and 1-ply all survive the
   piece cap, so line count alone cannot rank them). */
function tetrisRate(p) {
  /* percentage (0..100) of cleared lines that came from a 4-line
     clear. rl's eval_summary stores the same quantity as a
     fraction (0..1); the LEARN table scales it so both screens
     read on this scale. */
  return p.lines > 0 ? (p.tetrisCount * 4) / p.lines * 100 : 0;
}

/* Ranking that refuses to turn noise into a claim.

   rl measured Dellacherie 7,997.8 / CEM 7,998.3 / 1-ply 7,998.5 —
   all three survived every seed, and those gaps are seed rounding,
   not skill. Printing 1/2/3 there would assert a difference the
   data does not support. So panels within TIE_EPS of each other
   share a rank and are marked 공동, and the crown is withheld
   unless the leader is genuinely clear of the pack.               */
const TIE_EPS = 0.01;   // within 1% of the leader counts as tied

function rankWithTies(sorted, valueOf) {
  const out = [];
  let rank = 0, i = 0;
  while (i < sorted.length) {
    const v = valueOf(sorted[i]);
    let j = i;
    while (j < sorted.length &&
           Math.abs(valueOf(sorted[j]) - v) <= Math.max(1, Math.abs(v)) * TIE_EPS) j++;
    rank++;
    const tied = j - i > 1;
    for (let k = i; k < j; k++) out.push({ panel: sorted[k], rank, tied });
    i = j;
  }
  return out;
}

/* ============================================================
   Arena

   8 boards must stay responsive at top speed, so simulation and
   drawing are separate:
     - slow speeds pace one placement per fixed interval
     - fast speeds step panels ROUND-ROBIN until a wall-clock
       budget for the frame is spent, then draw once

   Round-robin (rather than "N steps per panel") is deliberate: it
   gives every panel the SAME piece count, which is what makes the
   leaderboard a fair comparison. It also means the expensive
   2-ply panel throttles everyone equally instead of starving.
   ============================================================ */

export const SPEEDS = [
  { label: '1배 (관찰용)',          interval: 420, budget: 0 },
  { label: '3배',                   interval: 140, budget: 0 },
  { label: '보통',                  interval: 55,  budget: 0 },
  { label: '빠름',                  interval: 0,   budget: 4 },
  { label: '매우 빠름',             interval: 0,   budget: 9 },
  { label: '초고속 (무애니메이션)', interval: 0,   budget: 13, noAnim: true },
];

const now = () => (globalThis.performance?.now ? performance.now() : Date.now());

export class Arena {
  constructor(opts) {
    this.gridEl = opts.gridEl;
    this.lbEl = opts.lbEl;
    this.strategies = opts.strategies;
    this.onStats = opts.onStats || (() => {});

    this.speedIdx = 2;
    this.sameSeed = true;
    this.autoRestart = true;
    this.seed = 12345;
    this.running = false;

    this.panels = [];
    this.raf = null;
    this.lastFrame = 0;
    this.elapsed = 0;
    this.fpsAcc = 0; this.fpsN = 0; this.fps = 0;
    this.sinceDraw = 0; this.statAcc = 0; this.rr = 0;

    this.buildDom();
  }

  buildDom() {
    this.gridEl.innerHTML = '';
    const n = this.strategies.length;
    /* Choose the column count that leaves the fewest empty cells,
       preferring fewer rows (taller boards) on a tie.

       This replaced a chain of special cases that needed a new
       entry every time the strategy roster grew — 8, then 9, then
       10, then 13. A rule that derives the layout does not need
       editing when the next panel lands. */
    const cols = (() => {
      if (n <= 2) return Math.max(1, n);
      let best = 4, bestWaste = Infinity, bestRows = Infinity;
      for (const c of [3, 4, 5]) {
        const rows = Math.ceil(n / c);
        const waste = rows * c - n;
        if (waste < bestWaste || (waste === bestWaste && rows < bestRows)) {
          best = c; bestWaste = waste; bestRows = rows;
        }
      }
      return best;
    })();
    this.gridEl.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
    this.gridEl.style.gridTemplateRows = `repeat(${Math.ceil(n / cols)}, 1fr)`;

    this.panels = this.strategies.map((st) => {
      const cell = document.createElement('div');
      cell.className = 'arena-cell';
      cell.innerHTML =
        `<div class="head">
           <span class="sw" style="width:9px;height:9px;border-radius:2px;background:${st.color}"></span>
           <span class="nm"></span><span class="critic"></span>
         </div>
         <div class="canvas-wrap"><canvas></canvas><div class="untrained-veil"><span></span></div></div>
         <div class="foot">
           <span>줄 <b class="f-lines">0</b></span>
           <span>조각 <b class="f-pieces">0</b></span>
           <span title="조각당 점수"><b class="f-spp">0</b>점/조각</span>
           <span title="지운 줄 중 4줄 동시로 지운 비율"><b class="f-tr">0</b>% 테트리스</span>
         </div>`;
      cell.querySelector('.nm').textContent = st.label;
      cell.querySelector('.critic').textContent = st.modeOverride
        ? P.difficultyById(st.modeOverride).label
        : st.plan;
      this.gridEl.appendChild(cell);
      return {
        strat: st, el: cell,
        canvas: cell.querySelector('canvas'),
        veil: cell.querySelector('.untrained-veil'),
        veilTxt: cell.querySelector('.untrained-veil span'),
        elLines: cell.querySelector('.f-lines'),
        elPieces: cell.querySelector('.f-pieces'),
        elSpp: cell.querySelector('.f-spp'),
        elTr: cell.querySelector('.f-tr'),
        state: null, lines: 0, pieces: 0, games: 0, dead: false,
        score: 0, tetrisCount: 0, ready: false, acc: 0, dirty: true, errLogged: false,
      };
    });
    this.sizeCanvases();
  }

  /* A strategy with no weight file is shown as 학습 전 and is NOT
     run. The lead was explicit: do not quietly substitute another
     policy and let it read as that strategy's result. */
  refreshReadiness() {
    for (const p of this.panels) {
      p.ready = P.isReady(p.strat);
      p.el.classList.toggle('untrained', !p.ready);
      if (!p.ready) p.veilTxt.textContent = '학습 전';
      p.veil.style.display = p.ready ? 'none' : '';
    }
  }

  sizeCanvases() {
    for (const p of this.panels) {
      const wrap = p.el.querySelector('.canvas-wrap');
      let h = Math.max(60, wrap.clientHeight - 2);
      let w = Math.floor(h * COLS / VIS);
      if (w > wrap.clientWidth - 2) { w = Math.max(30, wrap.clientWidth - 2); h = Math.floor(w * VIS / COLS); }
      p.canvas.width = w; p.canvas.height = h;
      p.dirty = true;
    }
    this.render(true);
  }

  setSpeed(i) {
    this.speedIdx = Math.max(0, Math.min(SPEEDS.length - 1, i | 0));
    return SPEEDS[this.speedIdx];
  }
  speedLabel() { return SPEEDS[this.speedIdx].label; }

  reset() {
    this.elapsed = 0; this.lastFrame = 0; this.sinceDraw = 0; this.statAcc = 0;
    this.refreshReadiness();
    this.panels.forEach((p, i) => {
      const seed = (this.sameSeed ? this.seed : this.seed + i * 7919) >>> 0;
      p.baseSeed = seed;
      /* per-panel difficulty: the 3-mode experiment sets modeOverride */
      p.diff = P.difficultyById(p.strat.modeOverride || P.difficulty.current);
      p.state = p.ready ? E.newGame(seed, p.diff.engineId) : null;
      p.rngSeed = ((seed ^ 0x9e3779b9) >>> 0) || 0x9e3779b9;
      p.lines = 0; p.pieces = 0; p.games = 0; p.dead = false;
      p.score = 0; p.tetrisCount = 0;
      p.acc = 0; p.dirty = true; p.errLogged = false;
      p.el.classList.remove('dead', 'leader');
    });
    this.render(true);
    this.updateStatsDom();
  }

  start() {
    if (this.running) return;
    if (!this.panels.some(p => p.ready)) return;
    if (!this.panels.find(p => p.ready)?.state) this.reset();
    this.running = true;
    this.lastFrame = 0;
    const loop = (ts) => {
      if (!this.running) return;
      this.frame(ts ?? now());
      this.raf = requestAnimationFrame(loop);
    };
    this.raf = requestAnimationFrame(loop);
  }

  stop() {
    this.running = false;
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = null;
  }

  frame(ts) {
    /* clamp both ends: a backwards timestamp must never push a
       negative delta into the accumulators */
    const dt = this.lastFrame ? Math.max(0, Math.min(100, ts - this.lastFrame)) : 16;
    this.lastFrame = ts;
    this.elapsed += dt;

    this.fpsAcc += dt; this.fpsN++;
    if (this.fpsAcc >= 500) { this.fps = Math.round(1000 * this.fpsN / this.fpsAcc); this.fpsAcc = 0; this.fpsN = 0; }

    const sp = SPEEDS[this.speedIdx];
    const live = this.panels.filter(p => p.ready);

    if (sp.interval > 0) {
      for (const p of live) {
        p.acc += dt;
        let guard = 0;
        while (p.acc >= sp.interval && guard < 30) { p.acc -= sp.interval; this.step(p); guard++; }
        if (guard) p.dirty = true;
      }
    } else if (live.length) {
      /* Check the deadline INSIDE the sweep, not only between
         sweeps. With 9 panels — one of which expands 2 ply — a
         single full sweep can cost several ms, so waiting for the
         sweep to finish overshot the budget by ~6ms and pushed the
         worst frame past 16.7ms.

         Cutting a sweep short is still fair because `rr` advances
         every sweep, so the panels that got skipped are the ones
         that go first next frame. Piece counts stay within ~1%. */
      const deadline = now() + sp.budget;
      let steps = 0;
      const cap = live.length * 4000;
      outer:
      while (steps < cap) {
        for (let i = 0; i < live.length; i++) {
          const p = live[(this.rr + i) % live.length];
          this.step(p);
          p.dirty = true;
          steps++;
          if ((steps & 7) === 0 && now() >= deadline) { this.rr = (this.rr + i + 1) % live.length; break outer; }
        }
        this.rr = (this.rr + 1) % live.length;
      }
    }

    this.sinceDraw += dt;
    if (sp.noAnim) {
      if (this.sinceDraw >= 250) { this.sinceDraw = 0; this.render(true); }
    } else {
      this.render(false);
      this.sinceDraw = 0;
    }

    this.statAcc += dt;
    if (this.statAcc >= 120) { this.statAcc = 0; this.updateStatsDom(); }
  }

  /* one placement for one panel */
  step(p) {
    if (!p.ready) return false;

    if (p.dead) {
      if (!this.autoRestart) return false;
      const seed = this.sameSeed ? p.baseSeed : ((p.baseSeed + p.games * 2654435761) >>> 0);
      p.state = E.newGame(seed, p.diff ? p.diff.engineId : undefined);
      p.dead = false;
      p.el.classList.remove('dead');
      return true;
    }

    let pick;
    try {
      pick = P.chooseAction(p.state, p.strat.id, rngFor(p));
    } catch (err) {
      if (!p.errLogged) { console.error(`[arena] ${p.strat.id} 실패:`, err); p.errLogged = true; }
      p.dead = true; p.games++; p.el.classList.add('dead');
      return false;
    }
    if (!pick) { p.dead = true; p.games++; p.el.classList.add('dead'); return false; }

    const [ns, info] = E.applyPlacement(p.state, pick.placement);
    p.state = ns;
    p.lines += info.lines_cleared || 0;
    p.score += info.score_delta || 0;
    if (info.is_tetris) p.tetrisCount++;
    p.pieces++;
    if (info.game_over) { p.dead = true; p.games++; p.el.classList.add('dead'); }
    return true;
  }

  render(force) {
    for (const p of this.panels) {
      if (!p.state?.rows) continue;
      if (!p.dirty && !force) continue;
      drawBoard(p.canvas, p.state.rows, { grid: p.canvas.width >= 100 });
      p.dirty = false;
    }
  }

  updateStatsDom() {
    const secs = this.elapsed / 1000;
    let total = 0;
    for (const p of this.panels) {
      total += p.pieces;
      p.elLines.textContent = p.lines;
      p.elPieces.textContent = p.pieces;
      p.elSpp.textContent = p.pieces ? (p.score / p.pieces).toFixed(1) : '0';
      { const tr = tetrisRate(p); p.elTr.textContent = tr === 0 ? '0' : tr < 1 ? tr.toFixed(2) : tr.toFixed(1); }
    }
    this.updateLeaderboard();
    this.onStats({ elapsed: secs, totalPieces: total, fps: this.fps });
  }

  updateLeaderboard() {
    if (!this.lbEl) return;
    const metric = this.lbMetric || 'lines';
    const valueOf = (p) =>
      metric === 'score' ? (p.pieces ? p.score / p.pieces : 0)
      : metric === 'tetris' ? tetrisRate(p)
      : p.lines;
    const fmt = (p) =>
      metric === 'score' ? (p.pieces ? (p.score / p.pieces).toFixed(1) : '0')
      : metric === 'tetris' ? tetrisRate(p).toFixed(0) + '%'
      : String(p.lines);

    const ready = this.panels.filter(p => p.ready);
    const notReady = this.panels.filter(p => !p.ready);
    ready.sort((a, b) => valueOf(b) - valueOf(a));
    const ranked = rankWithTies(ready, valueOf);

    let html = '';
    for (const { panel: p, rank, tied } of ranked) {
      /* crown only a genuinely clear leader */
      const lead = rank === 1 && !tied && valueOf(p) > 0;
      html +=
        `<div class="lb-row${lead ? ' top' : ''}">
           <span class="rk">${rank}${tied ? '<sup class="tie">=</sup>' : ''}</span>
           <span class="sw" style="background:${p.strat.color}"></span>
           <span class="nm">${p.strat.short}</span>
           <span class="sc">${fmt(p)}</span>
         </div>`;
      p.el.classList.toggle('leader', lead);
    }
    for (const p of notReady) {
      html +=
        `<div class="lb-row off">
           <span class="rk">–</span>
           <span class="sw" style="background:${p.strat.color}"></span>
           <span class="nm">${p.strat.short}</span>
           <span class="sc">학습 전</span>
         </div>`;
      p.el.classList.remove('leader');
    }
    if (ranked.some(r => r.tied)) {
      html += `<div class="lb-note">같은 번호 + <b>=</b> 는 <b>공동 순위</b>다. 차이가 잡음 수준이라 우열을 매기지 않았다.</div>`;
    }
    this.lbEl.innerHTML = html;
  }

  setMetric(m) { this.lbMetric = m; this.updateLeaderboard(); }

  setStrategies(list) {
    const wasRunning = this.running;
    this.stop();
    this.strategies = list;
    this.buildDom();
    this.reset();
    if (wasRunning) this.start();
  }
}

/* xorshift32 per panel — used only by the random policy and for
   restart seeds. Piece order always comes from the engine, so the
   same-seed comparison stays honest. */
function rngFor(p) {
  return () => {
    let x = p.rngSeed;
    x ^= x << 13; x >>>= 0;
    x ^= x >>> 17;
    x ^= x << 5;  x >>>= 0;
    p.rngSeed = x || 0x9e3779b9;
    return p.rngSeed / 4294967296;
  };
}
