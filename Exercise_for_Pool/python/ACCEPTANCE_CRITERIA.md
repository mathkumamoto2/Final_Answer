# 本間さんの採点フィードバックに基づく受け入れ基準

本ドキュメントは2026-11-03に受領した4点の追加確認への回答に基づく。
元の課題説明 (`ex1_web-scraping/explanation.md` と `ex2_docker_and_db/explanation.md`) も適用する。

## A. リンクの意味と優先順位

- 店舗ページの「お店のホームページ」を第一優先。「オフィシャルページ」はその項目がない場合だけ採用。
- 優先リンクが掲載済みでURLも判明している場合は、接続失敗してもリンクを保持し、下位候補に切り替えない。
- `href="#"` はURLではない。対応するHTMLの `onclick`、`data-*`、明示的なID参照スクリプトなどから静的にリンク先を特定する。
- 1-1はRequestsのみを使用。JavaScriptは実行しない。静的に特定できないときはログに `UNRESOLVED_LINK` を残す。別のURLを推測して埋めない。

## B. メール

- 「お店に直接メールする」に対応した `mailto:` または同じラベルに明記されたメールアドレスだけを採用。
- JSON-LDの `email` やページ内で最初に見つけた無関係の `mailto:` は採用しない。
- 正例と負例をテストする。

## C. URLとSSL

- 掲載URLを抽出した事実と、ネットワーク接続・TLS証明書検証・HTTPステータスを分離。
- HTTPSのTLS検証が成功すればHTTP 404でもSSL=`TRUE`。HTTPステータスは別途ログ。
- TLS検証失敗時は掲載URL保持、SSL=`FALSE`、`TLS_ERROR` と記録。
- 未接続・タイムアウトでは掲載URL保持、SSL=`FALSE`、`UNVERIFIED` と原因を記録。
- HTTP→HTTPS、HTTPS→別ドメインへの正常転送では最終URLを候補とする。ただし別ドメインは店舗名または十分に具体的な住所の一致を条件とし、CAPTCHA・中継・無関係なサイトは採用しない。
- CAPTCHAに転送されてもCAPTCHA URLを保存せず、元の掲載URLを保持。
- URL自体が存在しない場合はURLとSSLをともに空欄にする。

## D. 3課題の一貫性・再現性

- 1-1 / 1-2は同じ `shared.gnavi_rules` / `shared.site_verifier` を利用。
- 2-2は1-1の `collect()` を再利用し、MySQLで9列全項目を比較する。
- 1-1・1-2は50件・9列、重複なし、住所分割が適切、URL/SSLの不整合なし。
- 2-2は `SELECT COUNT(*)` と `COUNT(URL)` を区別し、9列すべて検証。
- 第三者が環境設定・依存ライブラリ・実行方法・テスト手順を理解できるよう README を置く。

## 残る実サイト検証

この配布バンドルでは実サイトの生HTML／Requests通信／Windows Selenium／Docker MySQLを実行できていない。
とくに `https://r.gnavi.co.jp/n814504/` の `href="#"` に対応するURLの属性・スクリプトを `inspect_gnavi_links.py` で確認し、
`http://lignosa.com/` に到達する証拠を保存すること。静的抽出関数で確認できなければ推測で埋めず、DOM証拠を用いて追加修正する。
