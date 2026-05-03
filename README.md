# pdf2epub

スキャン画像PDFを **YomiToku** OCR で読み取り、日本語EPUB 3 を生成する Python CLI ツールです。

- 縦書き / 横書き — OCR結果から自動判定（フラグで上書き可）
- 右開き / 左開き — 縦書き→`rtl`、横書き→`ltr` を既定で設定
- ルビ（振り仮名） — 親漢字との bbox 近接で結びつけ、`<ruby>` 要素として埋め込む
- 挿絵 — `figures` の bbox を PDFから crop して PNG として埋め込む
- 章分け — レイアウト解析の見出しで章を分割し、極小章・重複タイトルは自動マージ
- 逆順スキャンPDF — `--reverse-pages` で物理本のページ順に並べ替え
- 再構築 — OCRした生JSONを保存しておき `--rebuild-from` で再OCRなしに再生成

## クイックスタート

```bash
git clone https://github.com/ki-84/pdf2epub.git
cd pdf2epub
python -m venv .venv
source .venv/bin/activate
pip install -e .

pdf2epub input.pdf -o output.epub
```

## ライセンスに関する重要事項

本ツールは OCR エンジンに [YomiToku](https://github.com/kotaro-kinoshita/yomitoku) を使用しています。YomiTokuは **CC BY-NC-SA 4.0** ライセンスで配布されており、**商用利用は禁止** されています。本ツールおよび本ツールで生成された EPUB ファイルも、YomiTokuのライセンスに従い商用利用できません。個人・研究用途でのみ使用してください。

ツール本体（pdf2epub）のソースコードは [MIT ライセンス](LICENSE) です。

## インストール

```bash
git clone https://github.com/ki-84/pdf2epub.git
cd pdf2epub
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

PyTorch は YomiToku 依存で導入されます。Apple Silicon の場合は MPS が自動検出されます。

## 使い方

```bash
# 基本（writing-mode と page-direction は自動判定）
pdf2epub input.pdf -o output.epub

# 明示指定
pdf2epub input.pdf -o output.epub --writing-mode vertical --page-direction rtl

# CPU で実行
pdf2epub input.pdf -o output.epub --device cpu

# ルビを抽出しない
pdf2epub input.pdf -o output.epub --no-ruby

# デバッグ: 中間XHTMLと正規化済みドキュメントを書き出す
pdf2epub input.pdf -o output.epub --debug-html debug/ --dump-json doc.json

# 生のYomiToku JSONも保存
pdf2epub input.pdf -o output.epub --dump-raw-json raw_json/

# 物理本の最終ページから先頭へ逆順にスキャンされたPDF
pdf2epub input.pdf -o output.epub --reverse-pages

# 挿絵を抜きでテキストのみ抽出
pdf2epub input.pdf -o output.epub --no-figures

# 既に取得した生JSONからEPUBだけ作り直す（OCRをスキップ、数秒）
pdf2epub input.pdf -o output.epub --rebuild-from raw_json/
```

## 主要オプション

| オプション | 既定値 | 説明 |
|---|---|---|
| `-o`, `--output` | （必須） | 出力EPUBパス |
| `--device` | `auto` | `auto` / `mps` / `cuda` / `cpu` |
| `--writing-mode` | `auto` | `auto` / `vertical` / `horizontal` |
| `--page-direction` | `auto` | `auto` / `rtl` / `ltr` |
| `--no-ruby` | off | ルビを抽出しない |
| `--ruby-threshold` | `0.5` | YomiToku のルビ判定閾値 |
| `--title` | PDFファイル名 | EPUB タイトル |
| `--author` | 空 | 著者名 |
| `--debug-html DIR` | なし | 中間XHTMLを書き出す |
| `--dump-json FILE` | なし | 正規化済み Document を JSON で出力 |
| `--dump-raw-json DIR` | なし | YomiToku の生 JSON をページ単位で出力 |
| `--reverse-pages` | off | PDFが物理本の最終ページから先頭へ逆順にスキャンされている場合に指定 |
| `--start-page N` | `1` | 開始ページ番号（1始まり） |
| `--max-pages N` | なし | 先頭Nページのみ処理（動作確認用） |
| `--no-figures` | off | 挿絵をEPUBに埋め込まない（テキストのみ） |
| `--rebuild-from DIR` | なし | OCRをスキップし `--dump-raw-json` で保存した生JSONディレクトリから再生成 |

## 検証

```bash
# EPUB バリデーション
brew install epubcheck
epubcheck output.epub

# Apple Books で表示確認（縦書き＋rtl の動作確認に最適）
open -a "Books" output.epub
```

## アーキテクチャ

```
PDF
 └─ ocr.py           : YomiToku DocumentAnalyzer をページごとに実行
      └─ raw JSON   : paragraphs / words / direction / role / box ...
           └─ normalize.py : ルビ結合・方向多数決・章分け → 内部 Document
                └─ render.py : Block → XHTML（<ruby>、見出しレベル付与）
                     └─ epub.py : ebooklib でEPUB組立
                                  + OPFに page-progression-direction を後挿入
                          └─ output.epub
```

| ファイル | 役割 |
|---|---|
| `src/pdf2epub/cli.py` | argparse、エントリポイント |
| `src/pdf2epub/ocr.py` | YomiToku 呼び出しとデバイス判定 |
| `src/pdf2epub/model.py` | 内部データクラス |
| `src/pdf2epub/normalize.py` | 生 JSON → 内部 Document |
| `src/pdf2epub/render.py` | XHTML 生成 |
| `src/pdf2epub/epub.py` | EPUB 組立 + OPF 後処理 |
| `src/pdf2epub/styles/vertical.css` | 縦書き用 CSS |
| `src/pdf2epub/styles/horizontal.css` | 横書き用 CSS |

## 注意点

- 縦書きEPUBの表示はリーダー差があります。Apple Books と Thorium Reader での確認を推奨。
- 短い文書では見出しが検出されないことがあります。その場合は単一章「本文」にまとめます。
- 図表（figure）は本ツールでは扱いません。テキストのみ抽出します。
- MPS で未対応 OP に当たった場合に備え `PYTORCH_ENABLE_MPS_FALLBACK=1` を自動設定します。

## リンク

- リポジトリ: <https://github.com/ki-84/pdf2epub>
- 不具合報告・要望: <https://github.com/ki-84/pdf2epub/issues>
