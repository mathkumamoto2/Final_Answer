# Python課題 1-1 / 1-2 / 2-2 — 実行・検証ガイド

> **ChromeDriver提出について**：課題1-2は、Google Chrome for Testing の公式配布元から取得した `chromedriver.exe` を `ex1_web-scraping/` に同梱し、`Service(executable_path=...)` で起動する方式です。取得・バージョン照合・SHA-256・提出手順は [CHROMEDRIVER_SETUP.md](CHROMEDRIVER_SETUP.md) を参照してください。古い検出済みの実行ファイルは再利用せず、ウイルス対策ソフトの警告を回避しないでください。


このディレクトリは、ぐるなびの店舗情報を取得するPython課題の提出物です。**初めて実行する場合は「1. 準備」から順番に進めてください。** 既存のCSVとDB確認画像を見るだけなら、再スクレイピングは不要です。

- **1-1**：Requestsで店舗50件を取得し、`ex1_web-scraping/1-1.csv` の9列に保存
- **1-2**：Seleniumで一覧の次ページボタンをクリックして店舗50件を取得し、`ex1_web-scraping/1-2.csv` に保存
- **2-1**：Ubuntu 20.04 / Python 3.8 / MySQLの環境構築（採点済み・合格）
- **2-2**：Ubuntuコンテナで1-1と同じ取得処理を行い、MySQLの `ex2.ex2_2` に50件を登録

[課題1の原文](ex1_web-scraping/explanation.md) / [課題2の原文](ex2_docker_and_db/explanation.md) / [レビューへの対応基準](ACCEPTANCE_CRITERIA.md)

## 0. 提出物と検証済みの結果

| 提出対象 | ファイル・結果 | 2026-11-17の確認 |
| --- | --- | --- |
| 1-1（Requests） | [`1-1.py`](ex1_web-scraping/1-1.py)・[`1-1.csv`](ex1_web-scraping/1-1.csv) | 50件 / 指定9列 / URL 49件 |
| 1-2（Selenium） | [`1-2.py`](ex1_web-scraping/1-2.py)・[`1-2.csv`](ex1_web-scraping/1-2.csv) | 50件 / 指定9列 / URL 49件 |
| 2-1（Docker環境） | [`ex2-1.png`](ex2_docker_and_db/ex2-1.png) | 採点済み・合格（変更なし） |
| 2-2（MySQL） | [`2-2.py`](ex2_docker_and_db/2-2.py)・[DB件数](ex2_docker_and_db/ex2-2_count.png)・[列構成](ex2_docker_and_db/ex2-2_columns.png)・[保存内容](ex2_docker_and_db/ex2-2_table.png) | `COUNT(*)=50` / `COUNT(URL)=49` / 9列一致 |
| 共通処理・テスト | [`shared/`](shared/)・[`tests/`](tests/) | Windows / Ubuntu 3.8でそれぞれ80テスト合格 |

上表は**提出者の実行ログと登録済み成果物で確認した結果**です。再実行の成功や、すべての外部サイトの稼働を保証するものではありません。URL未取得の店舗1件は、取得できた証拠のないURLを推測で補完していません。URLとSSLの判定方法は後述します。

## 1. 準備（Windows / PowerShell）

必要なもの：**Git、Python 3.11、インターネット接続**。課題1-2には **Google Chrome**、課題2-2には **Docker Desktop（Linuxコンテナ）** が必要です。Docker Compose v2の `docker compose` を使用します。

### 1-1. リポジトリを取得

作業したいフォルダでPowerShellを開き、以下を実行します。**修正済みの作業ブランチを指定してクローン**します。

```powershell
git clone --branch fix/python-scraping-review-20261116 --single-branch https://github.com/mathkumamoto2/Final_Answer.git
Set-Location .\Final_Answer\Exercise_for_Pool\python
```

すでにクローン済みなら、正しい作業ブランチへ移動してから `Exercise_for_Pool/python` に移動してください。**以下のコマンドはすべてこの `python` ディレクトリを作業ディレクトリとします。**

### 1-2. Python環境と依存ライブラリ

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

以後は仮想環境を有効化する必要はありません。コマンド中の `python.exe` をそのまま使います。Python 3.8の場合は後述のDocker環境と `requirements-py38.txt` を使ってください。

## 2. オフラインテスト（最初に実行）

**外部サイトやDBへ接続せず**、抽出・URL/SSL判定・例外処理の回帰テストを実行します。

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests
.\.venv\Scripts\python.exe -m compileall -q shared ex1_web-scraping ex2_docker_and_db inspect_gnavi_links.py verify_csv.py verify_example.py
```

2026-11-17の提出者環境ではWindowsとUbuntu/Python 3.8の両方で **80 passed** でした。これは模擬HTML・モック通信を含むテストの結果であり、実サイト50件の取得成功とは別の検証です。

## 3. 課題1-1：Requestsで50件取得

サイトの利用条件を確認し、アクセスが許可されている場合のみ実行してください。

```powershell
.\.venv\Scripts\python.exe .\ex1_web-scraping\1-1.py --authorized --count 50 --output .\ex1_web-scraping\1-1_new.csv
if ($LASTEXITCODE -ne 0) { throw '1-1の取得に失敗しました' }
```

出力先：`ex1_web-scraping/1-1_new.csv`。提出済みの `1-1.csv` は上書きしません。3秒以上の待機、robots.txt確認、タイムアウトのため実行には時間がかかります。取得条件を満たさない場合、50件に満たないCSVは生成しません。

## 4. 課題1-2：Seleniumで50件取得

WindowsにGoogle Chromeを導入してください。**ChromeDriverは同梱せずSelenium Managerが自動管理**します（初回はドライバー取得に通信が必要）。元の課題文にはDriver同梱の指定がありますが、以前のドライバーがセキュリティソフトで検出されたため、安全上この方式を採用しています。形式面での扱いは採点者に確認してください。保護機能を無効化しての実行は推奨しません。

```powershell
.\.venv\Scripts\python.exe .\ex1_web-scraping\1-2.py --authorized --count 50 --max-pages 20 --output .\ex1_web-scraping\1-2_new.csv
if ($LASTEXITCODE -ne 0) { throw '1-2の取得に失敗しました' }
```

出力先：`ex1_web-scraping/1-2_new.csv`。Seleniumで実際に一覧の「次へ」ボタンをクリックしてページを進めます。こちらも提出済みCSVは上書きしません。

### 4-1. CSVの検証（1-1・1-2が両方成功した場合）

```powershell
.\.venv\Scripts\python.exe .\verify_csv.py .\ex1_web-scraping\1-1_new.csv .\ex1_web-scraping\1-2_new.csv
if ($LASTEXITCODE -ne 0) { throw 'CSV検証に失敗しました' }
```

50件・指定9列・重複・住所分割・URLとSSLの整合性を検証します。提出済みCSVを検証する場合は、末尾の2ファイル名をそれぞれ `1-1.csv` と `1-2.csv` に置き換えてください。

## 5. 課題2-2：Docker（Ubuntu 20.04 / Python 3.8）とMySQL

**ここからも作業ディレクトリは `Exercise_for_Pool/python` のままです。** Docker Desktopを起動してください。新規構築の場合と既存環境の場合では、`.env` の扱いが異なります。

### 5-1. 初めてDocker環境を作る場合のみ：DBパスワードを設定

以下は`.env`がまだ存在しない場合に、暗号学的乱数からパスワードを生成します。**既存の `.env` やMySQLの永続データは上書きしません。**

```powershell
if (-not (Test-Path .\.env)) {
    $bytes = New-Object 'System.Byte[]' 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $dbPassword = [System.BitConverter]::ToString($bytes).Replace('-', '')
    Set-Content -LiteralPath .\.env -Value "MYSQL_ROOT_PASSWORD=$dbPassword" -Encoding ASCII
    Remove-Variable dbPassword, bytes
    Write-Host 'MySQLパスワードを .env に生成しました（Gitへ追加しないこと）。'
} else {
    Write-Host '.env は既に存在するため変更しません。'
}
```

`.env` は `.gitignore` によりGit管理対象外です。**初期化済みのMySQLボリュームでは、`.env` を変更しても既存のDBパスワードは変わりません。** 接続に失敗したら既存の資格情報を確認し、`docker compose down -v` でデータを消さないでください。

### 5-2. 起動・Python 3.8テスト

```powershell
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { throw 'Docker起動に失敗しました' }

docker compose exec finalanswer-ubuntu python3 --version
docker compose exec finalanswer-ubuntu python3 -m pytest -q tests
if ($LASTEXITCODE -ne 0) { throw 'Docker上のテストに失敗しました' }
```

同梱の `Dockerfile.ubuntu20.04` と `docker-compose.yml` が、Ubuntu/Python用コンテナおよびMySQL 8.0用コンテナを構成します。Python 3.8用の依存パッケージは `requirements-py38.txt` から導入されます。

### 5-3. 実サイトから再取得し、DBへ50件保存

**注意：このコマンドは既存の `ex2.ex2_2` の登録データ50件を置き換えます。** 証拠画像だけ確認したい場合は実行不要です。サイトの利用条件を確認した場合のみ実行してください。

```powershell
docker compose exec finalanswer-ubuntu python3 ex2_docker_and_db/2-2.py --authorized --mysql-user root
if ($LASTEXITCODE -ne 0) { throw '2-2のDB登録に失敗しました' }
```

最後に表示される `MySQLパスワード:` には、初回生成した `.env` の `MYSQL_ROOT_PASSWORD` を入力します。**入力文字は表示されません。** パスワードをコマンドの引数やGitHubに記載しないでください。

### 5-4. MySQLの保存内容を確認（データは変更されません）

```powershell
docker compose exec finalanswer-mysql mysql -uroot -p -D ex2 --default-character-set=utf8mb4 --table
```

パスワードを入力し、`mysql>` プロンプトで以下を**1つずつ**実行します。

```sql
SELECT COUNT(*) AS total_rows, COUNT(URL) AS url_count,
       COUNT(NULLIF(URL, '')) AS non_empty_urls
FROM ex2_2;

SHOW COLUMNS FROM ex2_2;

SELECT * FROM ex2_2 LIMIT 5;
```

`COUNT(*)` は**全行数**、`COUNT(URL)` は**NULLでないURLの件数**です。2026-11-17の結果は `50 / 49 / 49` でした。終了は `exit;`。提出済み画像は[件数](ex2_docker_and_db/ex2-2_count.png)、[9列](ex2_docker_and_db/ex2-2_columns.png)、[実データ](ex2_docker_and_db/ex2-2_table.png)です。

## 6. 本間さんの指摘3点に対する実装・確認方法

| 指摘 | 実装 | 検証 |
| --- | --- | --- |
| `href="#"` の店舗HP取得漏れ | `shared/gnavi_rules.py` の `data-o` 等の静的解析。「お店のホームページ」を優先 | `tests/test_gnavi_rules.py`、下記LIGNOSA実サイト検証 |
| メールの誤取得 | 「お店に直接メールする」に対応するリンクのみ取得。無関係な `mailto:` やJSON-LDメールを代用しない | 正例・負例テスト、LIGNOSAのメール取得 |
| URL接続失敗・SSL判定 | `shared/site_verifier.py` で掲載URLと通信結果を分離し、TLS・HTTP・CAPTCHA・中継を分類 | `tests/test_site_verifier.py`、`tests/test_submission_guardrails.py` |

LIGNOSA CAFE（レビュー指定の例）に対する**実サイトのHTMLからの抽出確認**は、2026-11-17に次の結果で成功しました。

```text
お店のホームページ -> http://lignosa.com/ (source=data-o)
オフィシャルページ -> https://n814504.gorp.jp/
Chosen published URL: http://lignosa.com/
Shop email: info@lignosa.com
[PASS] Live LIGNOSA example verified.
```

再検証する場合は、利用条件・許可を確認後に次を実行します。これは **LIGNOSAページに載ったURLとメールの抽出テスト**であり、`http://lignosa.com/` のサーバー接続成功を証明するテストではありません。

```powershell
.\.venv\Scripts\python.exe .\verify_example.py --authorized
if ($LASTEXITCODE -ne 0) { throw 'LIGNOSAの実サイト検証に失敗しました' }
```

`SSL=TRUE` は検証済みのHTTPS接続を意味し、TLSエラーや確認不能の場合は `FALSE` として理由をログへ残します。**HTTPの404/403やCAPTCHAはTLS証明書の失敗とは別**です。掲載URLを確認できても接続できない場合は、推測で別のURLを補完しません。

## 7. トラブルシューティングと注意事項

- `py -3.11` が見つからない：Windows用Python 3.11を導入し、`py -3.11 --version` で確認する
- `docker compose` が使えない：Docker Desktopが起動中か、Compose v2が利用できるかを確認する
- `Access denied for user 'root'`：既存MySQLボリュームと`.env`のパスワードが一致するか確認する。**ボリュームを削除しない**
- Selenium ManagerがChromeDriverを起動できない：Chromeの導入とネットワークを確認する。セキュリティ保護を回避しない
- DNS・タイムアウト・robots.txtの制限：URLが確認できていれば保持し、判定不能の理由をログに記録する。CAPTCHAやアクセス制限の回避はしない
- `*.csv`：UTF-8 BOM付きで出力。`*_new.csv` は検証用出力で、Gitの提出用CSVを自動上書きしない
- `.env`、ローカル検証ログ、診断JSON、認証情報：**GitHubへコミットしない**

## 8. 関連ファイル

- [提出受け入れ基準](ACCEPTANCE_CRITERIA.md)
- [HTMLのリンク解析ツール](inspect_gnavi_links.py)
- [LIGNOSA実サイト検証](verify_example.py)
- [CSVバリデーター](verify_csv.py)
- [共通HTML抽出規則](shared/gnavi_rules.py)
- [URL/SSL検証](shared/site_verifier.py)
- [テスト一覧](tests/)

このREADMEは実行手順と確認済み結果を説明するものです。採点者による最終合格判定を保証するものではありません。
