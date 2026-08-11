#!/usr/bin/env bash
# 웹 앱 서빙.
#   bash tools/serve.sh [포트]
# 중지: pkill -f "http.server 8080"
#
# 문서 루트는 프로젝트 루트(tetris_rl/)다. web/ 이 아니다.
# 이유: index.html 이 가중치를 ../weights/ 로 참조하는데, 문서 루트를 web/ 으로 잡으면
# 루트 위로 올라가는 경로가 되어 http.server 가 차단한다. 루트를 한 단계 올리면
# 심볼릭 링크 없이 web/ 과 weights/ 가 같은 트리 안에 들어온다.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${1:-8080}"

pkill -f "http.server $PORT" 2>/dev/null || true
cd "$ROOT"
setsid python3 -m http.server "$PORT" --bind 0.0.0.0 > "$ROOT/tools/serve.log" 2>&1 < /dev/null &
sleep 1
echo "로컬 확인 index : $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/web/index.html)"
echo "로컬 확인 가중치: $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/weights/cem_linear.json)"
echo
echo "브라우저에서 열기:  http://localhost:$PORT/web/index.html"
