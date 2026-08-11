<!-- Parent: ../PROJECT.md -->
# Tetris Engine Spec v1 (engine 소유 · 규칙 확정본)

이 문서는 `PROJECT.md`의 규칙 결정을 구현 가능한 수준까지 못박은 것이다.
Python(`engine/engine.py`)과 JS(`web/engine.js`)는 **이 문서를 비트 단위로 동일하게** 구현한다.
여기 적히지 않은 동작은 구현 자유가 아니라 **미결**이며, engine 에게 물어야 한다.

용도: rl 은 `legal_placements`/`apply_placement`의 반환 구조와 `info` 필드를 여기서 확인한다.
web 은 좌표계·회전 테이블·wall kick·중력 곡선·상태 필드명을 여기서 확인한다.

---

## 1. 좌표계

- 보드 폭 `W = 10`, 보이는 높이 `VISIBLE_ROWS = 20`, 스폰 버퍼 `BUFFER_ROWS = 2`,
  전체 높이 `ROWS = 22`.
- **원점은 좌상단.** `x`는 오른쪽으로 증가(0..9), `y`는 **아래로** 증가(0..21).
- 행 `y = 0, 1`이 숨은 스폰 버퍼. 화면에 그리는 행은 `y = 2 .. 21`.
- 바닥은 `y = 21`.

### 보드 표현 (비트마스크)

`rows`는 길이 22의 정수 튜플/배열. `rows[y]`의 **bit c** 가 열 `c`의 셀.

```
occupied(x, y)  ==  (rows[y] >> x) & 1
FULL_ROW = 0x3FF   # 10비트 전부
```

bit 0 이 **가장 왼쪽 열**이다. (JS 도 동일. `1 << x`.)

## 2. 조각과 회전 상태

조각 인덱스는 고정이다. **순서를 바꾸면 7-bag 시퀀스가 달라진다.**

| idx | 0 | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|---|
| name | I | O | T | S | Z | J | L |

회전 상태 `r ∈ {0,1,2,3}`, 시계방향(CW)이 `r → (r+1) % 4`.
셀 좌표는 **조각 바운딩 박스 로컬 좌표 `(dx, dy)`**, dy 는 아래로 증가.
보드 좌표는 `(x + dx, y + dy)` — `(x, y)`는 박스 좌상단의 보드 위치.

박스 크기: I 는 4×4, O 는 2×2, T·S·Z·J·L 은 3×3.

```
I  r0: (0,1)(1,1)(2,1)(3,1)     r1: (2,0)(2,1)(2,2)(2,3)
   r2: (0,2)(1,2)(2,2)(3,2)     r3: (1,0)(1,1)(1,2)(1,3)

O  r0=r1=r2=r3: (0,0)(1,0)(0,1)(1,1)

T  r0: (1,0)(0,1)(1,1)(2,1)     r1: (1,0)(1,1)(2,1)(1,2)
   r2: (0,1)(1,1)(2,1)(1,2)     r3: (1,0)(0,1)(1,1)(1,2)

S  r0: (1,0)(2,0)(0,1)(1,1)     r1: (1,0)(1,1)(2,1)(2,2)
   r2: (1,1)(2,1)(0,2)(1,2)     r3: (0,0)(0,1)(1,1)(1,2)

Z  r0: (0,0)(1,0)(1,1)(2,1)     r1: (2,0)(1,1)(2,1)(1,2)
   r2: (0,1)(1,1)(1,2)(2,2)     r3: (1,0)(0,1)(1,1)(0,2)

J  r0: (0,0)(0,1)(1,1)(2,1)     r1: (1,0)(2,0)(1,1)(1,2)
   r2: (0,1)(1,1)(2,1)(2,2)     r3: (1,0)(1,1)(0,2)(1,2)

L  r0: (2,0)(0,1)(1,1)(2,1)     r1: (1,0)(1,1)(1,2)(2,2)
   r2: (0,1)(1,1)(2,1)(0,2)     r3: (0,0)(1,0)(1,1)(1,2)
```

이 표는 SRS 표준과 동일하다 (SRS 의 y-up 을 y-down 으로 뒤집은 것).

### 에이전트가 쓰는 유효 회전 (중복 제거)

```
O          : [0]
I, S, Z    : [0, 1]
T, J, L    : [0, 1, 2, 3]
```

`legal_placements` 는 이 목록만 순회한다. (r2 는 r0 과, r3 은 r1 과 착지 결과가 같다.)
사람 플레이는 4개 회전 모두 쓴다 (wall kick 때문에 도달 결과가 다를 수 있음).

## 3. 스폰

- 스폰 y 는 항상 `SPAWN_Y = 0`.
- 스폰 x: `O → 4`, 그 외 전부 `3`.
  → 3-wide 조각은 열 3,4,5 / I 는 열 3,4,5,6 / O 는 열 4,5 에 뜬다.
- 스폰 회전은 `r = 0`.
- **게임오버 정의 (확정)**: **새 조각의 스폰 위치가 이미 채워져 있으면 종료.**
  스폰 위치 `(spawn_x, 0, r=0)`의 4셀 중 하나라도 채워져 있으면 즉시 `game_over = True`.
  **버퍼 행에서의 충돌도 게임오버다.** 사람 경로와 에이전트 경로가 같은 조건을 쓴다.
  ("착지가 20행 위로 삐져나오면 종료"는 채택하지 않는다.)
- 에이전트 경로에서는 추가로 **`legal_placements` 가 빈 리스트면 게임오버**다.

## 4. Wall kick (사람 플레이 전용)

SRS 5-테스트. `(dx, dy)`, dy 는 아래로 증가 (SRS 원표의 y 부호를 뒤집었다).
회전 시도 순서대로 첫 성공을 채택하고, 다섯 개 모두 실패하면 **회전은 일어나지 않는다.**

JLSTZ (T, S, Z, J, L 공통):

| from>>to | t1 | t2 | t3 | t4 | t5 |
|---|---|---|---|---|---|
| 0>>1 | (0,0) | (-1,0) | (-1,-1) | (0,+2) | (-1,+2) |
| 1>>0 | (0,0) | (+1,0) | (+1,+1) | (0,-2) | (+1,-2) |
| 1>>2 | (0,0) | (+1,0) | (+1,+1) | (0,-2) | (+1,-2) |
| 2>>1 | (0,0) | (-1,0) | (-1,-1) | (0,+2) | (-1,+2) |
| 2>>3 | (0,0) | (+1,0) | (+1,-1) | (0,+2) | (+1,+2) |
| 3>>2 | (0,0) | (-1,0) | (-1,+1) | (0,-2) | (-1,-2) |
| 3>>0 | (0,0) | (-1,0) | (-1,+1) | (0,-2) | (-1,-2) |
| 0>>3 | (0,0) | (+1,0) | (+1,-1) | (0,+2) | (+1,+2) |

I:

| from>>to | t1 | t2 | t3 | t4 | t5 |
|---|---|---|---|---|---|
| 0>>1 | (0,0) | (-2,0) | (+1,0) | (-2,+1) | (+1,-2) |
| 1>>0 | (0,0) | (+2,0) | (-1,0) | (+2,-1) | (-1,+2) |
| 1>>2 | (0,0) | (-1,0) | (+2,0) | (-1,-2) | (+2,+1) |
| 2>>1 | (0,0) | (+1,0) | (-2,0) | (+1,+2) | (-2,-1) |
| 2>>3 | (0,0) | (+2,0) | (-1,0) | (+2,-1) | (-1,+2) |
| 3>>2 | (0,0) | (-2,0) | (+1,0) | (-2,+1) | (+1,-2) |
| 3>>0 | (0,0) | (+1,0) | (-2,0) | (+1,+2) | (-2,-1) |
| 0>>3 | (0,0) | (-1,0) | (+2,0) | (-1,-2) | (+2,+1) |

O 는 회전에 kick 이 필요 없다 (모양이 동일하므로 회전 요청은 상태값만 바꾸고 위치 불변).

## 5. 착지 · 줄 삭제 · 중력

### 하드드롭 착지 위치 (에이전트 경로의 유일한 착지 규칙)

열별 **최상단 채움 행** `top[c]` 를 쓴다 (빈 열이면 `top[c] = ROWS = 22`).
조각의 열별 **최하단 로컬 행** `bottom[dx]` 에 대해

```
y_rest = min over dx of ( top[x+dx] - bottom[dx] - 1 )
```

모든 SRS 조각은 각 열의 셀이 세로로 연속이므로 이 식이 하드드롭 결과와 정확히 일치한다.
`y_rest + min_dy < 0` (조각이 보드 위로 삐져나감) 이면 그 placement 는 **불법**이며
`legal_placements` 에서 제외한다.

### 줄 삭제

1. 조각 4셀을 보드에 기록한다.
2. `rows[y] == FULL_ROW` 인 모든 행을 **동시에** 판정해 수집한다.
3. 수집한 행을 제거하고, **위쪽 행들을 그만큼 아래로 내린다**. 맨 위에는 빈 행을 채운다.
   (즉 아래에서 위로 압축. 여러 줄 동시 삭제 시에도 한 번에 처리한다.)
4. 중력은 **행 단위**다. 떠 있는 셀 덩어리가 개별로 낙하하는 규칙은 없다.

### 점수

```
lines_cleared: 1 → 100, 2 → 300, 3 → 500, 4 → 800
soft drop : 내려간 칸당 +1
hard drop : 내려간 칸당 +2
```

`level` 은 **줄이 지워진 뒤의 레벨**이 아니라 **그 수를 두기 전의 레벨**을 쓴다 (구현 일치용 고정).

### B2B(back-to-back) 와 콤보

한 줄씩 꾸준히 지우는 정책보다 **4줄 동시(tetris)를 노리는 정책이 이기도록** 넣은 체계다.
사람 경로·에이전트 경로가 **동일하게** 적용받는다.

**"어려운 삭제"는 tetris(4줄)뿐이다.** T-spin 은 어려운 삭제로 세지 않는다 —
에이전트는 하드드롭 배치만 하므로 T-spin 을 낼 수 없고, 세면 사람과 에이전트가 다른 게임을 한다.

```
LOCK 시점의 점수 계산 (level = 그 수를 두기 전의 level):

if lines_cleared == 0:
    score_delta = 0
    combo  → 0              # 콤보는 끊긴다
    b2b    → 그대로 유지     # ★ 줄 안 지운 배치는 B2B 를 끊지 않는다
else:
    base = SCORE_TABLE[lines_cleared]
    if lines_cleared == 4:
        b2b_applied = (b2b > 0)         # 이미 체인이 돌고 있었나
        b2b → b2b + 1
    else:
        b2b_applied = False
        b2b → 0                          # 1~3줄은 체인을 끊는다
    if b2b_applied:
        base = base * 3 // 2             # ×1.5, 정수 나눗셈

    combo_bonus = 50 * combo * level     # ★ combo 는 '이번 삭제 전'의 값
    score_delta = base * level + combo_bonus
    combo → combo + 1
```

**정수 연산만 쓴다.** `SCORE_TABLE` 의 모든 값이 짝수이므로 `×3//2` 가 정확하고,
JS 와 부동소수 반올림으로 갈라질 수 없다. (JS 는 `Math.floor(base*3/2)`.)

상태 필드 `b2b`(체인 길이, 0=없음), `combo`(연속 삭제 수, 0=없음) 가 추가되며
**`state_hash` 에 포함된다** (미래 점수에 영향을 주므로). **`board_hash` 는 영향받지 않는다** —
보드 규칙이 안 바뀌었으므로 줄 수 최대화로 학습된 기존 가중치는 그대로 유효하다.

`info` 추가 필드: `score_delta`, `is_tetris`, `b2b_active`(이번 수에 ×1.5 적용됨), `b2b_chain`,
`combo_count`(이번 수 이후 체인 길이).

첫 삭제는 콤보 보너스가 0 이다 (`combo` 가 아직 0). 두 번째 연속 삭제부터 `50 * 1 * level` 이 붙는다.

**T-spin 은 v1 범위 밖이다.** 위 사유로 넣지 않는다.

### 레벨과 중력

```
level = 1 + total_lines // 10
```

중력 = 셀 하나 내려가는 데 걸리는 프레임 수 (60 fps 기준):

| level | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11-13 | 14-16 | 17-19 | 20-28 | 29+ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| frames/cell | 48 | 43 | 38 | 33 | 28 | 23 | 18 | 13 | 8 | 6 | 5 | 4 | 3 | 2 | 1 |

### Lock delay — 사람 플레이 전용

**`LOCK_DELAY_MS = 500`, `LOCK_RESET_LIMIT = 15`.**

조각이 스택에 닿아도 500 ms 동안은 고정되지 않는다. 그래서 tuck(틈에 밀어넣기)과 slide 가 된다.
이동·회전이 성공하면 카운트다운이 처음으로 되돌아가지만, **조각당 15회까지만** 그렇다.
15회를 다 쓰면 타이머는 그대로 흘러 예정대로 고정된다 — 무한 회전은 lock delay 를 0 으로
만들어 막는 것이 아니라 이 리셋 상한으로 막는다.

**하드드롭은 언제나 즉시 고정한다.**

> **lock delay 는 interactive 진입점 전용 개념이며 placement 열거에 영향을 주지 않는다.**
> `legal_placements` 는 하드드롭 도달 집합만 내므로 lock delay 라는 개념 자체가 없다.
> 상태 필드 `lock_ms / lock_resets / grav_ms` 는 `state_hash` 에서도 제외된다.

구동 함수는 `tick_ms(state, dt_ms)` (JS: `tickMs`) 다. 프레임마다 실제 경과 ms 를 넣으면
중력과 lock delay 를 같이 진행하고, 고정되는 프레임에만 `info` 를 반환한다.
시각 피드백용으로 `lock_delay_progress(state)` (0~1) 를 제공한다.

## 6. 난수 (xorshift32) 와 7-bag

Python 과 JS 가 **같은 시드에서 같은 32bit 수열**을 내야 한다.

```
state: uint32, 0 이 아니어야 함. seed == 0 이면 state = 0x9E3779B9 로 치환.

next_u32():
    x = state
    x ^= (x << 13) & 0xFFFFFFFF
    x ^= x >> 17            # logical shift (JS: >>>)
    x ^= (x << 5)  & 0xFFFFFFFF
    state = x
    return x
```

7-bag Fisher-Yates:

```
bag = [0,1,2,3,4,5,6]            # I,O,T,S,Z,J,L 순서로 초기화
for i from 6 down to 1:
    j = next_u32() % (i + 1)
    swap(bag[i], bag[j])
return bag                        # 이 순서로 큐에 push
```

큐 길이가 7 미만이 되면 새 bag 을 생성해 뒤에 붙인다. (`current` 를 뽑기 전에 보충)

## 7. 상태 직렬화

`state.to_dict()` / `stateToObject(state)` 는 아래 필드를 낸다. **필드명 고정.**

```json
{
  "rows": [22 ints],          // 위→아래, 각 10비트
  "current": 2,               // 조각 idx, null 이면 없음
  "rot": 0,
  "x": 3, "y": 0,             // 사람 경로의 현재 위치. 에이전트 경로에서는 스폰값
  "queue": [ints],            // next 조각들, 앞이 먼저 나옴 (>=5개 보장)
  "hold": null,               // 조각 idx 또는 null
  "can_hold": true,
  "rng": 305419896,           // xorshift32 state (uint32)
  "score": 0, "lines": 0, "level": 1, "pieces": 0,
  "game_over": false,

  // 사람 플레이 전용 (§5 lock delay). state_hash 에서 제외된다
  "lock_ms": 0, "lock_resets": 0, "grav_ms": 0
}
```

`from_dict` / `stateFromObject` 는 그 역이며 왕복이 완전해야 한다.
`lock_*`/`grav_ms` 는 없어도 되며 기본값 0 으로 채워진다 (에이전트 경로가 만든 상태와 호환).

### 보드 해시 (패리티 검증용)

FNV-1a 32bit, 행별 하위/상위 바이트 순서로 먹인다.

```
board_hash(rows):
    h = 2166136261
    for y in 0..21:
        h ^= rows[y] & 0xFF;         h = (h * 16777619) mod 2^32
        h ^= (rows[y] >> 8) & 0xFF;  h = (h * 16777619) mod 2^32
    return h                          # uint32
```

JS 는 곱셈에 `Math.imul(h, 16777619) >>> 0` 을 써야 한다 (float 정밀도 손실 방지).

`state_hash(state)` 는 board_hash 에 이어서
`current, rot, hold(없으면 7), can_hold(0/1), rng, lines, level` 을 각각 4바이트 리틀엔디언으로 먹인다.
패리티는 **board_hash 와 state_hash 둘 다** 비교한다.

## 8. 에이전트 진입점 (계약)

### `legal_placements(state) -> list[Placement]`

`Placement` 는 **불변 4-튜플**이다 (Python: `tuple`, JS: `[]`).

```
(rot, x, y_rest, piece)
  rot    : 회전 상태 (유효 회전 목록에서만)
  x      : 바운딩 박스 좌상단 열
  y_rest : 하드드롭 착지 시 박스 좌상단 행
  piece  : 조각 idx (state.current 와 동일, 편의용)
```

정렬 순서는 **`rot` 오름차순, 그 안에서 `x` 오름차순**으로 고정한다.
(패리티 테스트가 "항상 첫 번째 placement" 를 쓰므로 순서가 규칙의 일부다.)

`state.game_over` 이거나 도달 가능한 배치가 없으면 `[]`.

`x` 가 **박스 좌상단 열**이라는 점에 주의하라. 조각의 최소 점유 열이 아니다.
그래서 I 의 수직 회전(r1)에서는 `x` 가 **-2 까지 음수**가 된다 (셀은 dx=2 에 있으므로 보드 안이다).

최소 점유 열이 필요하면 직접 계산하지 말고 아래를 써라 — 한 칸 어긋나는 사고가 여기서 난다:

```
placement_left_col(p)   -> int            절대 최소 점유 열 = p.x + MIN_DX[piece][rot]
placement_cells(p)      -> [(y, x) x4]    그 배치가 점유할 절대 좌표
tables.MIN_DX[piece][rot]                 오프셋 원본
```

### `apply_placement(state, p) -> (next_state, info)`

`state` 는 **변경하지 않는다** (rows 는 튜플, 새 상태를 반환).

`info` (dict / plain object) 필드 — **rl 이 의존하는 계약**:

```
lines_cleared       : int 0..4
cleared_rows        : list[int]    삭제된 행의 보드 y (삭제 전 좌표, 오름차순)
game_over           : bool         다음 조각 스폰 충돌 또는 배치 불가
piece_cells         : list[(y,x)]  ★ 놓인 4셀의 절대 보드좌표, 삭제 전
cleared_piece_cells : int          ★ 삭제된 행에 포함돼 있던 '놓은 조각'의 셀 수 (0..4, 원시값)
eroded_piece_cells  : int          = lines_cleared * cleared_piece_cells (편의값)
landing_row_top     : int          놓인 4셀 중 가장 위 행의 보드 y (삭제 전)
landing_row_bottom  : int          놓인 4셀 중 가장 아래 행의 보드 y (삭제 전)
score_delta         : int          이 수로 얻은 점수
level               : int          이 수를 둔 시점의 level
total_lines         : int          누적 줄 수 (삭제 후)
piece, rot, x       : int
y                   : int          = y_rest
```

### 특징 정의는 엔진 것이 아니다

**엔진은 원시 기하값만 보장한다. `landing_height` 같은 특징 공식은 `rl/features.py` 가 확정본이다.**
같은 공식이 두 곳에 적히면 반드시 갈라지므로 이 문서에는 적지 않는다.

> **폐기 알림**: 이 문서의 이전 판에는 `landing_height` 를 **바운딩박스 중심**
> (`(min_dy + max_dy) / 2`) 으로 정의한 문장이 있었다. **그 문장은 폐기됐다.**
> 확정 정의는 `rl/features.py` docstring 에 있고 **4셀 평균**이다 (rl 이 확인).
> 옛 판을 들고 있다면 그 문장을 믿지 마라 — 4셀 조각에서는 두 값이 우연히 같지만
> 정의가 두 곳에 적혀 있던 것 자체가 문제였다.

`piece_cells` 가 있으면 착지 기하 특징은 전부 복원된다. 참고로 **셀 하나의 높이 환산은
`ROWS - y`** 다: 바닥 행 `y = 21` 의 높이가 1 이다. 보드가 20행이 아니라 **22행**이므로
`20 - y` 로 쓰면 모든 특징이 2 만큼 밀린다 — 가장 밟기 쉬운 함정이다.
(이건 좌표 환산이지 특징 공식이 아니다.)

### 사람 플레이 진입점 (에이전트에는 노출 안 됨)

```
move(state, dx)        -> bool   좌우 1칸. 성공 여부
rotate(state, cw)      -> bool   wall kick 포함. 성공 여부
soft_drop(state)       -> bool   1칸 하강 성공 여부 (성공 시 score +1)
hard_drop(state)       -> info   즉시 착지 + 고정 + 다음 조각 (score += 2*칸수)
tick(state)            -> info|None  중력 1칸. 착지하면 lock 하고 info 반환
hold(state)            -> bool   hold 교체. can_hold=False 로. 성공 여부
lock(state)            -> info   현재 위치에서 고정 (내부용, tick/hard_drop 이 호출)
drop_distance(state)   -> int    현재 위치에서 착지까지 남은 칸수
ghost_y(state)         -> int    착지 행 (UI 낙하 위치 미리보기용)
```

이 함수들은 **`state` 를 제자리에서 변경한다** (사람 플레이는 복사 비용을 낼 이유가 없다).
`hold` 는 조각을 스폰 위치·회전 0 으로 되돌린다. 새 조각 스폰마다 `can_hold = True`.

`lock` 은 내부적으로 `apply_placement` 를 호출한다. 사람 경로와 에이전트 경로가
줄 삭제·점수·스폰 로직을 **같은 코드로** 공유하므로 두 경로가 갈라질 수 없다.
단 사람 경로의 착지는 오버행 아래(tuck/spin 결과)일 수 있고, `apply_placement` 는
도달 가능성을 재검사하지 않으므로 그 배치도 그대로 고정된다.

### JS 쪽 이름

`web/engine.js` 는 **camelCase 와 snake_case 를 둘 다 export** 한다
(`legalPlacements` == `legal_placements`, `applyPlacement` == `apply_placement`,
`newGame` == `new_game`, `boardHash` == `board_hash`, `stateHash` == `state_hash`,
`framesPerCell` == `frames_per_cell`, `softDrop`/`hardDrop`/`dropDistance`/`ghostY` 등).
상태 필드명은 Python 과 동일한 snake_case 를 유지한다 (`can_hold`, `game_over`) —
JSON 이 언어 경계를 그대로 통과해야 하기 때문이다.

## 9. 패리티 절차

`engine/parity.py` 가 하는 일:

1. 시드 `[1, 12345, 0xDEADBEEF]` × 결정적 정책 2종의 조합마다 새 게임을 만든다.
   - `first` : 매 수 `legal_placements` 의 **첫 번째**를 고른다. 왼쪽으로 쌓여 13~15수에
     top out 하므로 **줄 삭제를 한 번도 안 거친다** — 그래서 이것만으로는 부족하다.
   - `lowest` : `y_rest` 가 가장 큰(=가장 깊이 떨어지는) placement, 동점이면 앞의 것.
     보드가 평평하게 유지되어 47~53수를 살고 줄 삭제·중력·점수 경로를 실제로 밟는다.
2. 최대 200수 또는 게임오버까지 진행. 매 수 `(move, board_hash, state_hash,
   lines_cleared, score)` 를 기록.
3. `engine/parity_python.json` 으로 덤프. RNG 단독 검증용으로 시드 1 의
   xorshift32 첫 16값(`rng_probe`)도 같이 덤프한다.
4. `which node` → 있으면 `engine/parity_runner.mjs` 를 생성해 `web/engine.js` 를 같은 방식으로
   돌리고 `engine/parity_js.json` 생성 후 비교. 불일치 첫 지점의 양쪽 레코드를 출력한다.
5. 전체 트레이스의 줄 삭제 합이 0 이면 **삭제 경로가 검증되지 않은 것이므로 실패로 처리**한다.
6. node 가 없으면: `parity.py --emit-js-runner` 가 `engine/parity_browser.html` 을 만든다.
   프로젝트 루트에서 `python3 -m http.server` 를 띄우고 열면 결과 JSON 이 화면에 뜨고
   "Copy JSON" 으로 복사 → `engine/parity_js.json` 에 저장 후
   `parity.py --compare-only` 로 비교.

**이 환경에서는 `/usr/local/bin/node` v22.18.0 이 확인되어 자동 경로를 쓴다.**

### 검증 범위 (확정)

**패리티 검증의 핵심 대상은 에이전트 경로(placement)다.** 사람 경로는 실시간 타이머가 끼어
결정적 비교가 어렵기 때문이다 — **단, "어렵다"가 "안 한다"는 아니다.** 아래처럼 결정화해서
사람 경로도 대조한다:

- 실시간 시계 대신 **고정 dt(16.0 ms)** 를 넣는다. 16.0 은 double 로 정확히 표현되므로
  lock/중력 누산기가 양쪽에서 비트 단위로 같게 진행된다
- 입력 열을 **같은 xorshift32** 로 생성해 Python·JS 가 동일한 키 입력을 받는다
- 매 프레임 18개 필드 전부(보드 해시·x·y·rot·hold·can_hold·점수·레벨·b2b·combo·lock 타이머)를
  비교한다. 해시가 아니라 필드 단위라 불일치가 나면 **어느 필드인지 바로 나온다**
- 게임오버 시 파생 시드로 재시작해 스텝 예산(600)을 다 쓴다 —
  일찍 죽는 시드가 조용히 아무것도 검증하지 않는 것을 막는다

추가로 **wall kick 은 무작위 플레이에 의존하지 않고 전수 조사한다**: 4종 보드 픽스처 ×
7조각 × 4회전 × 2방향 × x(-2..9) × y 7값 조합에서 회전 결과 `(rot, x, y, moved)` 를 비교한다.
(무작위 사람 플레이는 3트레이스에서 kick 이 27회만 발동해 40개 표 항목에 비해 턱없이 얕았다.)

B2B·콤보는 `(lines, level, b2b, combo)` 조합을 **전수 열거**해 비교한다 (400 케이스).

측정 결과 (2026-08-07):
- rng_probe 16/16 일치
- score_probe **400/400** B2B+콤보 케이스 일치
- kick_probe **8,084** 회전 시도 일치 (878회 kick 오프셋 발동, 74회 회전 거부)
- placement 트레이스 6개 전부 board_hash·state_hash 일치 (`lowest` 3개에서 합계 12줄 삭제)
- 사람 경로 트레이스 3개 × **602 프레임** 전부 18필드 일치 (회전 706회, hold 104회, 하드드롭 118회)

## 10. 벤치마크

`parity.py --bench` 가 `legal_placements` + `apply_placement` 왕복 처리율을 잰다.
placement 는 고정 시드 xorshift32 로 고르고, 게임오버 시 파생 시드로 재시작한다.

**측정 조건은 고정돼 있고, 이 조건에서의 값만 인용해야 한다:**

```
placements = 200,000 (고정 개수 — 고정 시간이 아니다)
seed       = 20260807
policy     = 고정 시드 xorshift32 무작위 선택
difficulty = normal
best of 3 repeats, 단일 코어, Python 3.10
```

**확정값: 약 44,500 placements/초** (목표 20,000 대비 2.2배).

**초기에는 이 값이 30,000~48,000 사이로 흔들렸고 단일 값(45,307 → 42,938 → 40,099)으로 보고됐다.
그건 측정의 결함이었다**: 벤치가 **고정 시간**(3초) 예산이라 실행마다 게임 수가 달라졌고,
게임 길이가 스택 높이를 좌우하고 그 높이가 `column_tops` 의 조기 종료 시점을 좌우하므로
처리량이 함께 움직였다. **고정 placement 개수로 바꾸자 매 실행이 정확히 같은 작업(9,155 게임,
144줄)을 하게 되어** 반복 간 편차가 1.5% 이내로 떨어졌다.

교훈: 처리량 벤치에 시간 예산을 쓰면 워크로드가 측정값에 따라 변한다. **작업량을 고정해라.**

## 11. 상수 단일 출처와 코드 생성

세 구현체(`engine/engine.py`, `rl/fastsim.py`, `web/engine.js`)가 상수표를 각자 적으면 반드시 갈라진다.
그래서 표는 **한 곳에만** 있다:

```
engine/tables.py         ← 유일한 원본. 로직 없이 상수만
  ├─ engine/engine.py      import
  ├─ rl/fastsim.py         import  (rl 이 고속 시뮬레이터를 만들 경우)
  └─ web/tables.js         ← engine/gen_tables_js.py 가 생성. 손으로 고치지 말 것
       └─ web/engine.js      import
```

```bash
python3 engine/gen_tables_js.py            # web/tables.js 재생성
python3 engine/gen_tables_js.py --check    # stale 이면 exit 1
```

`engine/parity.py --tests` 가 `--check` 를 호출하므로, **표를 고치고 재생성을 잊으면 테스트가 실패한다.**

`engine/tables.py` 가 제공하는 파생표 (셀표에서 계산되므로 어긋날 수 없다):

| 이름 | 내용 |
|---|---|
| `MIN_DX / MAX_DX / MIN_DY / MAX_DY` | `[piece][rot]` 바운딩박스 점유 범위 |
| `BOTTOM_PROFILE` | `[piece][rot] -> ((dx, bottom_dy), ...)` 하드드롭 착지 계산용 |
| `X_RANGE` | `[piece][rot] -> (x_min, x_max_exclusive)` 유효 박스 원점 범위 |
| `PLACEMENT_COUNT` | `[piece][rot]` 배치 가능 열 수 |

`engine/rng.py` 도 `rl/fastsim.py` 가 그대로 import 한다 — **시그니처를 바꾸지 말 것.**

### 생성물 두 번째: `web/engine.classic.js`

ES module 이 소스지만, `file://` 에서도 열리게 **클래식 스크립트 단일 파일 번들**도 생성한다.

```bash
python3 engine/gen_classic_bundle.py            # web/engine.classic.js 재생성
python3 engine/gen_classic_bundle.py --check    # stale 이면 exit 1
```

`<script src="engine.classic.js">` → `window.TetrisEngine`. node 에서는 `require()` 도 된다.
**소스는 계속 ES module 이다. 번들과 `engine.js` 가 어긋나면 번들이 stale 인 것이다.**
`parity.py --tests` 가 `--check` 를 호출하므로 재생성을 잊으면 테스트가 실패한다.

## 12. 배치 롤아웃 API

학습이 GPU 를 굶기지 않도록 독립 게임 N 개를 함께 진행하는 진입점.

```
legal_batch(states)              -> [[Placement, ...], ...]
step_batch(states, placements)   -> (new_states, infos)
rollout(state, choose, max_pieces=100000) -> {lines, score, pieces, state, game_over}
```

`step_batch` 는 `placements[i] is None` 이거나 그 게임이 이미 끝났으면 그 슬롯을 그대로 통과시키고
`infos[i] = None` 을 넣는다 — 게임들이 서로 다른 시점에 끝나도 배치 모양이 유지된다.

내부는 파이썬 루프다. 행이 10비트, 조각이 4셀이라 numpy 로 벡터화하면 디스패치 오버헤드가
연산량을 넘는다. **진짜 병렬화는 `multiprocessing` 으로 128코어에 게임을 쪼개는 것이고**,
`State.to_dict()/from_dict()` 왕복이 정확하므로 상태가 프로세스 경계를 그대로 넘어간다.

### numpy 변환기 (검증·디버깅용)

```
board_array(state)                -> numpy (20, 10) uint8, 0/1, row 0 = 화면 최상단(보드 y=2)
board_array(state, buffer=True)   -> (22, 10), row 0 = 보드 y=0
rows_from_array(arr, buffer=)     -> 22-int 행 튜플 (역변환)
```

**롤아웃·특징 루프 안에서는 쓰지 말 것.** 비트마스크가 훨씬 빠르고 이건 매번 할당한다.
numpy 는 지연 import 라 엔진 자체는 여전히 의존성이 없다.

## 14. 난이도 — 정보량 축소 (보드 규칙 불변)

| 모드 | `difficulty` | next 미리보기 | hold |
|---|---|---|---|
| 노멀 (기본) | 0 | 5개 | 있음 |
| 하드 | 1 | **1개** | 없음 |
| 익스트림 | 2 | **0개** | 없음 |

### 이 모드가 바꾸지 않는 것 (핵심 불변식)

**난이도는 정보만 제한한다. 보드 규칙도, 조각 순서도 건드리지 않는다.**

- **같은 시드면 세 모드 모두 완전히 같은 조각 순서**가 나온다. 7-bag·xorshift32 는 손대지 않았다.
  **미리 보여주지 않을 뿐 조각은 동일하다** — 그래야 모드 간 점수 차이를 lookahead 만의 효과로
  귀속시킬 수 있다. 이게 이 실험의 전제다.
- `legal_placements` / `apply_placement` 의 계약은 그대로다.
- **`difficulty` 는 `state_hash` 에 포함되지 않는다.** 포함시키면 노멀 모드의 모든 해시가 바뀌어
  기록된 패리티 트레이스와 학습된 가중치가 무효가 된다. 대신 노멀이 난이도 도입 이전 엔진과
  **비트 단위로 동일함**을 테스트로 고정했다 (트레이스 6개의 board_hash 가 전부 이전 값과 같다).

### 미리보기 접근 — 조용히 빈 값을 주지 않는다

```
next_visible_count(state) -> int      0/1/5. 물어보기 전에 분기할 수 있다
visible_next(state)       -> tuple    허용된 만큼. 익스트림에서는 예외를 던진다
hold_enabled(state)       -> bool
difficulty_name(state)    -> "normal" | "hard" | "extreme"
```

**익스트림에서 `visible_next()` 는 `NextPeekBlocked` 예외를 던진다. 빈 튜플을 반환하지 않는다.**

이유: 1-ply 탐색이 빈 미래를 조용히 탐색하면 **0-ply 로 퇴화했는데도 숫자를 정상적으로 보고한다.**
그러면 "익스트림에서 탐색 이득이 증발하는가"라는 이 모드의 실험 목적 자체가 측정 불가능해진다.
**퇴화는 명시적이어야 한다** — `NextPeekBlocked` 를 잡거나 `next_visible_count` 로 미리 분기해라.

`state.queue` 는 **엔진 내부 장부**다. 모드와 무관하게 미래 조각을 담고 있어야 조각 생성기가
동일하게 유지되므로, **직접 읽으면 난이도를 우회한다.** 엔진 밖(UI·특징·탐색)은 반드시
`visible_next()` 를 거쳐라.

`hold()` 는 hold 없는 모드에서 **`False` 를 반환하고 상태를 전혀 바꾸지 않는다** (예외가 아니라 거부다 —
그 모드의 UI 에는 hold 버튼이 아예 없다).

### ★ 우회 경로 두 개 — 코드로 막지 못한다. 알고 있어야 한다

익스트림 모드의 정보 차단은 **완전하지 않다.** 두 경로가 남아 있고 둘 다 구조적이다:

1. **`state.queue` 직접 읽기.** 엔진 내부 장부라 모드와 무관하게 미래를 담고 있다.
   (checker 확인: 현재 `rl/`·`web/` 어디에서도 직접 읽지 않는다.)
2. **afterstate 시뮬레이션.** `apply_placement(s, p)` 가 돌려주는 `next_state.current` 가
   **숨겨진 다음 조각이다.** `apply_placement` 는 **플레이 가능한** 상태를 돌려줘야 하므로
   다음 조각을 스폰할 수밖에 없고, lookahead 에이전트는 큐를 건드리지 않고도 미래를 알아낸다.
   **`.queue` 를 grep 해도 이 경로는 안 잡힌다.**

   ### ★ 그리고 이 누수에는 깊이 상한이 없다 (실측)

   1조각이 아니다. afterstate 를 **연쇄**하면 원하는 만큼 읽힌다:

   ```
   normal  visible_next(5)          : I  Z  L  S  T
   extreme afterstate 연쇄 (12회)   : I  Z  L  S  T  O  I  L  S  T  Z  O
                                      └── 앞 5개가 노멀 미리보기와 정확히 일치 ──┘
   ```

   **즉 익스트림이 노멀보다 더 깊이 샌다.** 노멀은 5개를 보여주는데 연쇄하면 12개 이상을 읽고,
   상한은 남은 배치 수뿐이다. 연쇄 비용은 배치당 `legal_placements` + `apply_placement`
   한 번씩이라 사실상 무료다. (checker 측정 8개, 내 재측정 12개.)

   **그래서 위 표의 "0개 미리보기"를 정보이론적 보장으로 읽으면 안 된다.**
   이 모드가 실제로 제한하는 것은 *"미래를 볼 수 있는가"* 가 아니라
   **"미래를 보려면 afterstate 시뮬레이터를 작성해야 하는가"** 다.
   그것이 실질적 장벽인지는 실험 설계자의 판단이며, **엔진은 그 이상을 보장하지 않는다.**

경로 2 는 **패치할 결함이 아니다.** `current` 를 감춘 `next_state` 는 쓸 수 없는 상태이고,
에이전트 계약이 쓸 수 있는 상태를 요구한다. 이 우회를 막는 유일한 방법은 **행동 검증**이다:

> **익스트림에서 lookahead 정책이 무-lookahead 정책과 동일한 배치를 골라야 한다.**

checker 가 이 대조를 갖고 있다 (3시드 2,700수에서 1-ply 와 CEM 이 완전 일치, 모든 수에서
0-ply 붕괴 플래그, 노멀·하드에서는 한 번도 붕괴 안 함). **난이도 실험 결과를 인용할 때는
그 대조가 통과했는지를 함께 확인해라** — 정보 차단 자체를 신뢰하면 안 된다.

**정적 스캔(`grep .queue`)은 안전의 근거가 될 수 없다.** afterstate 경로를 원리적으로
통과시키기 때문이다. 깨끗한 grep 은 보조 증거일 뿐이고, 실질 방어는 위의 행동 대조 하나뿐이다.

### 실험 설계상의 기대

- **익스트림에서 1-ply 탐색은 원리적으로 0-ply 가 된다.** 볼 미래가 없으므로 탐색이라는 개념이
  사라지고 CEM 과 같은 플레이가 된다. 그 이득이 통째로 증발하는 것을 보이는 것이 목적이다.
- **Dellacherie·CEM 은 next 를 안 보므로 익스트림에서도 성능이 변하지 않아야 한다.**
  변한다면 어딘가에서 next 를 몰래 쓰고 있다는 뜻이다 — 그것이 이 모드의 부수적 검증 효과다.

### 미구현으로 확정된 것

**garbage 줄 주입 모드와 조각 가뭄(drought) 모드는 만들지 않는다.** 사용자가 반려했고,
정보량 축소로 대체됐다. 둘은 보드 규칙·조각 분포를 건드려 기존 패리티와 가중치를 무효화한다.

## 13. 미결 · 결정 사항 기록

- **B2B·콤보는 v1 안이다** (§5). 리드가 범위를 되돌렸다 — rl 의 CEM 이 줄 수를 포화시켜
  (10게임 19,997~19,999줄) 전략 비교축이 죽었고, 학습 목표가 점수로 전환됐기 때문이다.
- **T-spin 보너스는 없다.** 에이전트가 하드드롭 배치만 하므로 T-spin 을 낼 수 없다 —
  넣으면 사람과 에이전트가 다른 게임을 한다. v1 밖이며 넣지 말 것.
- **Next 5개 표시**는 UI 규격이며 엔진은 `queue` 를 5개 이상 항상 보장한다 (실제로는 7 이상).
- **lock delay 500 ms + 리셋 15회 상한**, 사람 경로 전용 (§5). 리드가 "0 프레임" 안을 반려했다 —
  즉시 고정은 tuck·slide 를 봉쇄해 손맛을 죽인다. 무한 회전은 리셋 상한으로 막는다.
- **특징 공식은 엔진 소유가 아니다** (§8). `rl/features.py` 가 확정본이고 엔진은 원시 기하값만 낸다.
- **`level` 은 점수 계산 시 그 수를 두기 전 값** 으로 확정 (§5).
