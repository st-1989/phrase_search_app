# Japanese Phrase Search

CSV 内の日本語文章を完全一致で検索し、一致箇所のみ黒・太字、それ以外の文脈をグレーで表示する Streamlit アプリです。

## セットアップ

```bash
python -m venv .venv
```

macOS / Linux:

```bash
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

依存関係をインストールします。

```bash
pip install -r requirements.txt
```

## 起動

```bash
streamlit run app.py
```

「データセット」プルダウンから CSV を選び、検索フレーズを入力してください（Enter 不要で、入力が止まると自動で検索されます）。
アプリと同じディレクトリ、または `data/` ディレクトリに置いた `*.csv` がプルダウンに表示されます。それ以外の CSV は「CSVをアップロード…」から読み込めます。

検索結果の文章をクリックすると、全文が（一致箇所をハイライトしたまま）展開されます。

## CSV 例

```csv
id,text
1,本研究では機械学習を用いて解析を行った。
2,ここでは機械学習を利用した手法について述べる。
```

列名は任意です。GUI 上で検索対象の文章列を選べます。

## 現在の検索仕様

- 入力文字列の literal な完全一致
- 1 行に複数一致がある場合はすべてハイライト
- 一致部分は黒・太字
- 前後文脈はグレー
- CSV は UTF-8 / UTF-8 BOM / CP932 / Shift-JIS を順に試行
- 表示件数と前後文脈長を GUI から変更可能

この初期版は全行を逐次検索します。数十万〜数百万行規模で入力ごとの応答速度が問題になる場合は、trigram index や SQLite FTS / Elasticsearch / OpenSearch に検索層を置き換える構成が適しています。
