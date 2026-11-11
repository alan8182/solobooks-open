#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# SoloBooks 部署前门禁：后端 pytest 全量 + 前端 vitest / 类型检查 / 构建 / 预渲染
# 用法: bash deploy/preflight.sh [--skip-tests]
#   --skip-tests  跳过回归测试；构建、预渲染与新鲜度检查照跑（镜像能否构建成功取决于它们）
# deploy.sh 会自动调用；也可单独用作「跑全量回归」。各步日志保留在临时目录，结尾打印路径。
# ============================================================

usage() { sed -n '/^# 用法/,/^# deploy.sh/p' "$0" | sed 's/^# \{0,1\}//'; }

SKIP_TESTS=0
for arg in "$@"; do
    case "$arg" in
        --skip-tests) SKIP_TESTS=1 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/solobooks-preflight.XXXXXX")"

# === Colors ===
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# Windows 用 venv/Scripts/python.exe，macOS/Linux 用 venv/bin/python（AGENTS.md B2）
if [ -x "$PROJECT_ROOT/backend/venv/Scripts/python.exe" ]; then
    PY="$PROJECT_ROOT/backend/venv/Scripts/python.exe"
elif [ -x "$PROJECT_ROOT/backend/venv/bin/python" ]; then
    PY="$PROJECT_ROOT/backend/venv/bin/python"
else
    error "未找到 backend/venv 里的 Python，先按 README.local.md 建好后端虚拟环境"
fi
command -v npm >/dev/null 2>&1 || error "缺少 npm"

RESULTS=()
FAILED=()

# === 后端：pytest 全量放后台，与前端并行 ===
BACKEND_PID=""
BACKEND_LOG="$LOG_DIR/backend-pytest.log"
if [ "$SKIP_TESTS" = 1 ]; then
    warn "--skip-tests：跳过后端 pytest、前端 vitest 与类型检查"
else
    info "[后端] pytest 全量（后台运行）..."
    (cd "$PROJECT_ROOT/backend" && exec "$PY" -m pytest -q) > "$BACKEND_LOG" 2>&1 &
    BACKEND_PID=$!
fi
# 前端失败提前退出或被 Ctrl-C 中断时，别把后台 pytest 留成孤儿进程
trap 'if [ -n "$BACKEND_PID" ]; then kill "$BACKEND_PID" 2>/dev/null || true; fi' EXIT

# === 前端：顺序执行，一步失败后面就不跑（后一步依赖前一步产物）===
STEPS=()
if [ "$SKIP_TESTS" = 0 ]; then
    # 计时脆弱用例已在 statementViewMode.test.ts 内逐用例放宽超时（D-016），
    # 这里不再全局放宽，保持默认 5 秒以保留抓真卡死的能力
    STEPS+=("vitest|test|npm run test" "类型检查|types|npm run test:types")
fi
# 构建与预渲染不可跳：预渲染过期只会在服务器构建镜像时才失败，
# 那时代码已同步、后端镜像已构建完（2026-11-06，D-005）
# 路由白名单校验同样不可跳：新增一级路由漏同步 nginx 白名单时，直连访问 404
# （2026-11-11 /tax-copilot 即因此带上线，D-020）；放构建前先拦，避免白跑构建
STEPS+=("路由白名单|nginx-routes|node scripts/check-nginx-routes.mjs" "生产构建|build|npm run build" "预渲染|prerender|npm run prerender" "预渲染新鲜度|prerender-check|node scripts/check-prerender.mjs")

web_failed=0
for s in "${STEPS[@]}"; do
    IFS='|' read -r name key cmd <<< "$s"
    if [ "$web_failed" = 1 ]; then
        RESULTS+=("· 前端 $name：未执行（前一步失败）")
        continue
    fi
    log="$LOG_DIR/web-$key.log"
    info "[前端] $name ..."
    # shellcheck disable=SC2086  # cmd 需要按空格拆成参数
    if (cd "$PROJECT_ROOT/web" && $cmd) > "$log" 2>&1; then
        detail=""
        if [ "$key" = test ]; then
            detail="（$(sed 's/\x1b\[[0-9;]*m//g' "$log" | grep -E '^ *(Test Files|Tests) ' | tr -s ' ' | sed 's/^ //' | tr '\n' ';' | sed 's/;$//; s/;/; /g' || true)）"
        fi
        RESULTS+=("✓ 前端 $name$detail")
    else
        web_failed=1
        FAILED+=("前端 $name")
        RESULTS+=("✗ 前端 $name（日志：$log）")
        echo "------ $name 日志末尾 ------"
        tail -n 30 "$log"
    fi
done

# === 等后端结束 ===
if [ -n "$BACKEND_PID" ]; then
    info "[后端] 等待 pytest 结束..."
    summary() { grep -E '[0-9]+ (passed|failed)|[0-9]+ errors? ' "$BACKEND_LOG" | tail -n 1 | sed 's/=//g; s/^ *//; s/ *$//' || true; }
    if wait "$BACKEND_PID"; then
        RESULTS+=("✓ 后端 pytest（$(summary)）")
    else
        FAILED+=("后端 pytest")
        RESULTS+=("✗ 后端 pytest（$(summary)；日志：$BACKEND_LOG）")
        echo "------ pytest 日志末尾 ------"
        tail -n 40 "$BACKEND_LOG"
    fi
    BACKEND_PID=""
fi

# === 汇总 ===
echo
echo "================ 预检汇总 ================"
printf '  %s\n' "${RESULTS[@]}"
echo "  日志目录：$LOG_DIR"
if [ "${#FAILED[@]}" -gt 0 ]; then
    echo -e "${RED}[FAIL]${NC} 未通过：${FAILED[*]}"
    exit 1
fi
info "预检全部通过"
