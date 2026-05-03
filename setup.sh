#!/usr/bin/env bash
set -euo pipefail

# pdf2epub セットアップスクリプト
# - Python venv (.venv) を作成
# - pdf2epub を editable インストール
# - epubcheck の有無を確認（任意）
#
# 使い方:
#   ./setup.sh              # 既定の python3 で .venv を作成
#   PYTHON=python3.12 ./setup.sh
#   ./setup.sh --recreate   # 既存の .venv を作り直す

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

PYTHON="${PYTHON:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"
RECREATE=0

for arg in "$@"; do
    case "$arg" in
        --recreate) RECREATE=1 ;;
        -h|--help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        *)
            echo "unknown option: $arg" >&2
            exit 2
            ;;
    esac
done

# ---------- Python バージョン確認 ----------
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "error: '$PYTHON' が見つかりません。PYTHON=python3.12 のように指定してください" >&2
    exit 1
fi

PY_VER="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PY_OK="$("$PYTHON" -c 'import sys; print(1 if (3,10) <= sys.version_info[:2] < (3,14) else 0)')"
if [ "$PY_OK" != "1" ]; then
    echo "error: Python 3.10 以上 3.14 未満が必要です（検出: $PY_VER）" >&2
    echo "       PYTHON=python3.12 ./setup.sh のように明示してください" >&2
    exit 1
fi
echo "==> Python $PY_VER を使用 ($("$PYTHON" -c 'import sys;print(sys.executable)'))"

# ---------- venv 作成 ----------
if [ "$RECREATE" = "1" ] && [ -d "$VENV_DIR" ]; then
    echo "==> 既存の $VENV_DIR を削除"
    rm -rf "$VENV_DIR"
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "==> venv を作成: $VENV_DIR"
    "$PYTHON" -m venv "$VENV_DIR"
else
    echo "==> 既存の venv を再利用: $VENV_DIR"
fi

# venv の python / pip を直接使う（activate 不要）
VENV_PY="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

# ---------- pip 更新 ----------
echo "==> pip / setuptools / wheel を更新"
"$VENV_PY" -m pip install --upgrade pip setuptools wheel

# ---------- pdf2epub インストール ----------
echo "==> pdf2epub を editable インストール（依存に YomiToku / PyTorch を含むため数分かかります）"
"$VENV_PIP" install -e .

# ---------- 動作確認 ----------
echo "==> インストール確認"
"$VENV_DIR/bin/pdf2epub" --help >/dev/null
echo "    pdf2epub CLI OK"

# epubcheck は EPUB バリデーションに使用（任意）
if command -v epubcheck >/dev/null 2>&1; then
    echo "    epubcheck: $(epubcheck --version 2>&1 | head -n1)"
else
    echo "    epubcheck は未インストール（任意）。検証する場合は: brew install epubcheck"
fi

cat <<EOF

==> セットアップ完了

次のステップ:
  source $VENV_DIR/bin/activate
  pdf2epub input.pdf -o output.epub

EOF
