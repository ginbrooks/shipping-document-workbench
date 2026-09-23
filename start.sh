#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  if ! command -v python3.11 >/dev/null 2>&1; then
    echo '需要先安装 Python 3.11，再运行本脚本。' >&2
    exit 1
  fi
  python3.11 -m venv .venv
fi
.venv/bin/python -c 'import sys; assert sys.version_info[:2] == (3,11), "请使用 Python 3.11 环境"'
if ! .venv/bin/python -c 'import streamlit,openpyxl,docxtpl,pypdf,pydantic' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python -m streamlit run app.py --server.address=127.0.0.1 --browser.gatherUsageStats=false "$@"
