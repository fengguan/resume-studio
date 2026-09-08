#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$task_root"
if [[ ! -x .venv/bin/streamlit ]]; then
  echo '请先按 README.md 创建 .venv 并安装项目依赖。' >&2
  exit 1
fi
exec .venv/bin/streamlit run app.py --server.address 127.0.0.1 "$@"

