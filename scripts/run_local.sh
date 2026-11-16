#!/usr/bin/env bash
# RIDE THE WORLD — 手元のマシン（Mac mini / DGX Spark）で毎朝実行するためのスクリプト
#
#   1) 最新の main を取得
#   2) update_news.py でローカルLLMに更新させる
#   3) data.json が変わっていればコミットして push（→ GitHub Pages に反映）
#
# 設定は scripts/.env に書きます（scripts/.env.example をコピー）。
# cron 例（毎朝6:00 JST、マシンのタイムゾーンがJSTの場合）:
#   0 6 * * * /path/to/micromobility-news/scripts/run_local.sh >> /tmp/ride-the-world.log 2>&1
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

if [ -f scripts/.env ]; then
  set -a; . scripts/.env; set +a
fi

PYTHON="${PYTHON:-python3}"
if [ -x "$REPO_DIR/.venv/bin/python" ]; then
  PYTHON="$REPO_DIR/.venv/bin/python"
fi

echo "=== $(date '+%Y-%m-%d %H:%M:%S') RIDE THE WORLD update ==="

# LLMサーバーが起きているか先に確認（落ちていたら何もしない）
if ! curl -fsS -m 10 ${LLM_API_KEY:+-H "Authorization: Bearer $LLM_API_KEY"} "${LLM_BASE_URL%/}/models" > /dev/null; then
  echo "LLMサーバー(${LLM_BASE_URL})に接続できないため中止します"
  exit 1
fi

git pull --ff-only --quiet

"$PYTHON" scripts/update_news.py

git add data.json
if git diff --cached --quiet; then
  echo "No changes to commit."
else
  git -c user.name="ride-the-world-bot" -c user.email="actions@github.com" \
    commit -m "🚲 Daily news update ($(TZ=Asia/Tokyo date +%Y.%m.%d))" --quiet
  git push --quiet
  echo "pushed."
fi
