# 課題1-2：公式ChromeDriverの同梱と実行

この課題では、元の課題文にある「使用したchromedriverを1-2.pyと同じディレクトリに入れる」という提出形式に合わせ、ChromeDriverをリポジトリに含めます。Selenium Managerによる自動解決は利用しません。

**配布元：** Googleの [Chrome for Testing](https://googlechromelabs.github.io/chrome-for-testing/) / [バージョン選択の公式説明](https://developer.chrome.com/docs/chromedriver/downloads/version-selection)。[SeleniumのServiceクラス](https://www.selenium.dev/selenium/docs/api/py/selenium_webdriver_chrome/selenium.webdriver.chrome.service.html) に `executable_path` を渡して使用します。

## 事前条件

- Windows 10/11、64bit版Google Chrome、PowerShell、Python 3.11（または環境に適したPython）
- 対象サイトの利用条件・アクセス許可を確認済みであること
- `Exercise_for_Pool/python/` で `requirements.txt` に記載した依存ライブラリを導入済みであること
- **過去にセキュリティソフトに検出された実行ファイルは、復元・コピー・除外登録・許可リスト追加をしないこと**

## 1. 同梱ChromeDriverを確認（初回ダウンロード不要）

`git clone` で取得したリポジトリには、Windows 64bit用 `chromedriver.exe` を**すでに同梱しています**。PC側のChromeとの互換性があるなら、`DOWNLOAD_CHROMEDRIVER.ps1` を実行する必要はありません。まず [Python課題README](README.md) の「1. 準備」を行ってください。

2026-11-18に確認した組み合わせ：

- Chrome：`157.0.8103.51`
- 同梱ChromeDriver：`157.0.8103.78`（同一 `157.0.8103` ビルド系列）
- `verify_local_driver.py` で `about:blank` の起動確認成功

インストール済みChromeのバージョンは `chrome://version` などで確認できます。**Chromeが更新されて互換性がなくなった場合のみ**、後述の「6. ドライバー更新」を実施してください。

同梱ファイルは次のとおりです。

```text
ex1_web-scraping/
  1-2.py
  chromedriver.exe          # Google Chrome for Testing提供の実行ファイル
  chromedriver.sha256       # ダウンロード後のSHA-256値
  chromedriver.source.json  # インストールChromeとDriverの版・公式URL・SHA-256
```

- 公開GitHubの `chromedriver.exe` は実行可能なバイナリです。ダウンロード元と版、ウイルス対策ソフトの検査結果を提出前に確認してください。
- `chromedriver.sha256` はセットで保存し、起動前に `1-2.py` が実ファイルと照合します。ただし、**同時に作成したハッシュだけで公式署名や安全性が証明されるわけではありません**。
- ウイルス対策ソフトが警告する場合は操作を停止してください。警告を無効化・回避して実行しないでください。その場合は採点者へ安全な代替提出を相談してください。
- Chromeが更新されたら、ChromeDriverも再取得して更新が必要になることがあります。

## 2. オフラインテスト

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests
```

仮想環境名を変えている場合は、その環境のPythonを指定してください。ここでのテストは偽ドライバーをモックするため、実際のChromeDriverは起動しません。

## 3. ChromeDriverの起動確認（通信なし）

ウイルス対策ソフトの警告が出ていないことを確認した後、実行してください。サイトのスクレイピングは行わず、`about:blank` を開いて閉じます。

```powershell
.\.venv\Scripts\python.exe .\verify_local_driver.py
```

`[PASS] ChromeDriver + Chrome launched without Selenium Manager` と表示されれば、同梱実行ファイルを使ってブラウザを起動できています。

## 4. 実際の1-2の実行

実行は対象サイトの利用条件とrobots.txtを確認した場合のみ行います。ブラウザーを起動する前に、ウイルス対策ソフトの警告が出ていないことを確認してください。

```powershell
.\.venv\Scripts\python.exe .\ex1_web-scraping\1-2.py --authorized --count 50 --max-pages 20 --output .\ex1_web-scraping\1-2_new.csv
.\.venv\Scripts\python.exe .\verify_csv.py .\ex1_web-scraping\1-2_new.csv
```

既存の提出済み `1-2.csv` は上書きしません。正常な場合、50件・9列のCSVが保存されます。

## 5. 提出するファイル

`1-2.py`、`1-2.csv` に加え、使用した **`chromedriver.exe`** を同じ `ex1_web-scraping` ディレクトリへ含めます。再現性とファイル検査のため `chromedriver.sha256`、`chromedriver.source.json`、この説明書、依存ライブラリ一覧も合わせてコミットします。

## 6. ドライバー更新（Chromeのバージョンが変わった場合のみ）

通常は更新不要です。Chromeと同梱ドライバーの互換性がなくなった場合に限り、**過去にウイルス対策ソフトが検出した実行ファイルを再利用せず**、新たにGoogle公式Chrome for Testingから取得してください。

`DOWNLOAD_CHROMEDRIVER.ps1` は、同名の実行ファイルが存在する場合に**勝手に上書きせず停止**します。ドライバー更新時には、現在のバイナリを事前に安全な場所へ退避し、次のコマンドを `Exercise_for_Pool/python` で実行します。

```powershell
# リポジトリの Exercise_for_Pool/python で実行
$oldDriver = '.\ex1_web-scraping\chromedriver.exe'
if (Test-Path $oldDriver) {
    $backup = Join-Path $HOME 'Downloads\chromedriver_previous_backup'
    New-Item -ItemType Directory -Path $backup -Force | Out-Null
    Move-Item -LiteralPath $oldDriver -Destination (Join-Path $backup ('chromedriver_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.exe'))
}
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\DOWNLOAD_CHROMEDRIVER.ps1
if ($LASTEXITCODE -ne 0) { throw 'ChromeDriverの更新に失敗しました' }

.\.venv\Scripts\python.exe .\verify_local_driver.py
if ($LASTEXITCODE -ne 0) { throw 'ChromeDriverの起動確認に失敗しました' }
```

更新スクリプトは、Chromeの `MAJOR.MINOR.BUILD` が一致するChromeDriverを公式カタログから選び、公式 `storage.googleapis.com` からダウンロードします。更新時に `chromedriver.sha256` と `chromedriver.source.json` も再生成されるため、**通常は `1-2.py` を編集する必要はありません**。更新した3ファイルは、利用許可とセキュリティ検査結果を確認してからGitへ登録してください。

**注意：** この文書は元課題の「chromedriverを同梱する」形式に合わせたものです。使用するPCのChromeとDriverの一致を保つ必要があるため、環境に依存しない完全な再実行を保証するものではありません。
