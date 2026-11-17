# Python課題 1-1 / 1-2 / 2-2 — 修正版

## 修正方針

本実装は `ACCEPTANCE_CRITERIA.md` と本間さんからの確認済み回答を基準にしています。

- 「お店のホームページ」を「オフィシャルページ」より優先します。
- `href="#"` のリンクは、同じリンクに付いた `data-o` / `data-url` / `data-href` / `onclick` の静的なURL表現から解析します。JavaScriptは実行しません。
- 「お店に直接メールする」に対応する `mailto:` または同じラベル内のメールアドレスだけを採用します。
- 掲載URLと接続確認結果を分離します。TLSエラー・タイムアウト・HTTPエラー・CAPTCHA・robots.txtの確認不能でも、判明済みの掲載URLは保持します。
- HTTPSのTLS証明書検証が成功したHTTP 404は `SSL=TRUE` とし、`HTTP_ERROR` を別に記録します。
- 別ドメインへ転送された場合、店舗名または具体的な住所の一致を確認できなければ転送先を採用しません。
- Requests / Selenium / URL検証のリクエスト前に3秒待機し、User-Agentとrobots.txtを適用します。アクセス制限やCAPTCHAの回避はしません。
- 1-1 / 1-2は `shared.gnavi_rules` と `shared.site_verifier` を共有し、2-2は1-1の `collect()` を再利用します。

## フォルダ構成

```text
Exercise_for_Pool/python/
├── shared/
│   ├── __init__.py
│   ├── gnavi_rules.py
│   └── site_verifier.py
├── ex1_web-scraping/
│   ├── 1-1.py
│   ├── 1-2.py
│   └── explanation.md
├── ex2_docker_and_db/
│   ├── 2-2.py
│   └── explanation.md
├── tests/
├── inspect_gnavi_links.py
├── verify_example.py
├── verify_csv.py
├── requirements.txt
├── requirements-py38.txt
├── Dockerfile.ubuntu20.04
├── docker-compose.yml
├── .env.example
└── ACCEPTANCE_CRITERIA.md
```

## Windowsでの環境構築

PowerShellでリポジトリ内の `Exercise_for_Pool\python` に移動します。

```powershell
cd C:\Users\KH\Downloads\Final_Answer\Exercise_for_Pool\python
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Python 3.11以降を推奨します。Python 3.8を使う場合は `requirements-py38.txt` を使用してください。

## オフライン回帰テスト・静的検査

```powershell
python -m pytest -q tests
python -m compileall -q shared ex1_web-scraping ex2_docker_and_db inspect_gnavi_links.py verify_csv.py verify_example.py
```

テストは通信をモックして実行するため、外部サイトへアクセスしません。

## LIGNOSA CAFEの静的HTML確認

本間さんから示された例です。サイトの利用条件を確認し、アクセスが許可されている場合のみ実行してください。

```powershell
python .\inspect_gnavi_links.py --url https://r.gnavi.co.jp/n814504/ --output gnavi_link_evidence.json
```

HTML上の `data-o` に `{"a":"lignosa.com\\/","b":"http"}` がある場合、静的解析で `http://lignosa.com/` を復元します。URLはHTMLの証拠から抽出し、スクレイピングコードには店舗固有URLをハードコードしません。

正例・負例を含む実サイト受け入れ確認は、許可・利用条件を確認した場合のみ実行します。

```powershell
python .\verify_example.py --authorized
```

CAPTCHA、robots.txtによる禁止、接続制限が発生した場合は回避せず停止してください。

## 課題1-1（Requests）

```powershell
cd .\ex1_web-scraping
python .\1-1.py --authorized --count 50 --output .\1-1_new.csv 2>&1 | Tee-Object -FilePath .\scrape-1-1.log
```

## 課題1-2（Selenium）

Selenium ManagerにChromeDriverのバージョン解決・取得・キャッシュを任せます。以前の提出物で `chromedriver.exe` がGit管理されている場合は、作業ブランチで `git rm --cached -- Exercise_for_Pool/python/ex1_web-scraping/chromedriver.exe` を実行して追跡対象から外し、ローカルに残っている検出済みファイルも実行・復元せず削除してください。 `.gitignore` でも `chromedriver.exe` を除外しています。プロジェクトに `chromedriver.exe` は同梱せず、手動の `--driver` 指定も受け付けません。初回起動時はChromeのバージョン確認と公式配布元からのドライバー取得のため、インターネット接続が必要です。Norton等が新しく取得したドライバーをブロックした場合、保護を無効化したり除外登録したりせず、そこで停止してください。

```powershell
python .\1-2.py --authorized --count 50 --max-pages 20 --output .\1-2_new.csv 2>&1 | Tee-Object -FilePath .\scrape-1-2.log
```

`1-2.py` はページロード45秒、WebDriverローカル通信60秒に制限します。ページロードタイムアウトは再試行せず記録し、ChromeDriverプロセスとの通信断は処理を中止します。終了時に `driver.quit()` が失敗しても、二次例外で元のエラーを隠さないように後始末します。

CSVの件数・列・重複・住所・URL/SSL整合性を確認します。

```powershell
cd ..
python .\verify_csv.py .\ex1_web-scraping\1-1_new.csv .\ex1_web-scraping\1-2_new.csv
```

確認前に既存の `1-1.csv` / `1-2.csv` を上書きしないでください。

## 課題2-2（Docker / MySQL）

### 既存の課題用コンテナを使う場合

リポジトリ直下で、修正版をUbuntuコンテナへコピーしてテストします。

```powershell
docker cp ".\Exercise_for_Pool\python\." finalanswer-ubuntu:/work/python
docker exec -it finalanswer-ubuntu bash -lc "cd /work/python && python3 -m pip install -r requirements-py38.txt && python3 -m pytest -q tests"
```

MySQLコンテナ `finalanswer-mysql` と同じDockerネットワークに接続されていることを確認してから実行します。

```powershell
docker exec -it -w /work/python/ex2_docker_and_db finalanswer-ubuntu python3 2-2.py --authorized
```

MySQLパスワードは対話入力です。ソースコード・コマンド履歴・GitHubへ保存しないでください。

### 同梱Composeで新規構築する場合

`.env.example` を `.env` にコピーし、強固なMySQLパスワードに変更してから実行します。

```powershell
Copy-Item .\.env.example .\.env
# .env の MYSQL_ROOT_PASSWORD を変更

docker compose up -d --build
docker compose exec finalanswer-ubuntu python3 --version
docker compose exec finalanswer-ubuntu python3 -m pytest -q tests
docker compose exec finalanswer-ubuntu python3 ex2_docker_and_db/2-2.py --authorized
```

既存ボリュームを消す `docker compose down -v` は実行しないでください。

### MySQLの確認

```powershell
docker exec -it finalanswer-mysql mysql -u root -p ex2
```

```sql
SELECT COUNT(*) AS total_rows,
       COUNT(URL) AS non_null_urls,
       COUNT(NULLIF(URL, '')) AS non_empty_urls
FROM ex2_2;
SHOW COLUMNS FROM ex2_2;
SELECT * FROM ex2_2 LIMIT 5;
```

`COUNT(*)` は全行数、`COUNT(URL)` はURLがNULLではない行数です。50行のうちURLが1件空欄なら、`COUNT(*)=50`、`COUNT(URL)=49` となり得ます。

2-2はテーブルの列構成を確認し、9列すべてを照合します。既存テーブルのデータを置き換える処理はトランザクション内で行い、失敗時はロールバックします。

## 検証上の制限

この修正版のオフラインテスト・構文検査はローカルのPython実行環境で実施しています。実際のWindows Chrome/Selenium、Ubuntu 20.04/Python 3.8コンテナ、MySQLコンテナ、外部サイトへの再アクセスは別途ユーザー環境で確認が必要です。テスト合格を実サイト取得やDocker/MySQLの実行済みという意味にはしません。


## 2026-11-17 ラベル検出・診断修正

「公式サイト 最新情報やブログなど多数掲載」のようなリンク表記を、
「オフィシャルページ」と同じ優先度の公式候補として認識します。
「お店のホームページ」が存在する場合の優先順位は変更しません。

`inspect_gnavi_links.py` は LIGNOSA 専用の検索語を標準では使いません。
任意の検索語を使う場合は `--needle uokura-kumamoto.com` のように指定します。
抽出結果の `resolved_site_links` は静的HTML解析の結果です。
特定店舗の現在のHTMLに掲載されていることを保証するものではありません。

この配布物のインストーラは `.env`、CSV、既存DBを変更しません。
リポジトリの対象コードは上書き前にバックアップを作成します。
ZIP付属のテストは外部サイトを呼ばず、実サイトの再取得には利用条件の確認が必要です。
