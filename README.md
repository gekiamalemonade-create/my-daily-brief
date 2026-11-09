# Ken's Daily Brief（無料版）— 費用0円の自分専用AIポッドキャスト

毎朝 5:40 に自動で1本つくり、スマホのポッドキャストアプリに届ける仕組みです。
**使うのは Gemini API の無料枠・公式の RSS・GitHub（公開リポジトリ）・VOICEVOX だけ**なので、クレジットカードの登録も月額費用もいりません。

```
 評価ボタン・リクエスト ─────────────┐
                                     ▼
 ① 収集   NHK・官公庁・BBC日本語・IT専門メディアなどの公式RSS（約18本）から新着を集める
 ② 選定   好みの重み × 最近の評価 × 過去の放送 × リクエスト で今日の構成を決める（Gemini 3.8 Flash）
 ③ 素材   選んだ記事の本文を取り出す ／ 深掘りとスキルを Google 検索つきで調べる（Gemini 2.5 Flash-Lite。使えないプロジェクトでは記事の本文だけで進める）
          → 信頼リストにないサイトだけが根拠の文は捨てる
 ④ 台本   2人の掛け合い。全部の行に出典ID（S1, S2…）を付ける（Gemini 3.8 Flash）
 ⑤ 照合   台本を記事本文と1行ずつ照合 → 修正・削除（Gemini 3.8 Flash）
 ⑥ 音声   Gemini TTS（無料枠）で2人の声に。上限に当たったら VOICEVOX で作る → MP3
 ⑦ 配信   GitHub Pages → ポッドキャストアプリ（番組ページに出典・評価ボタン・台本）
```

## 番組の中身（約15分）

| コーナー | 長さ | ねらい |
|---|---|---|
| オープニング | 20秒 | 今日のラインナップ |
| ヘッドライン ×4 | 各1分 | **浅く広く**（世界情勢か日本経済を必ず1本、2割は興味の外から） |
| 深掘り | 6分 | **深く**：背景 → 仕組み → 影響 → 具体例 |
| 1日1スキル | 3分 | 学習トラック（マーケ理論・会計・交渉・統計）を連載で |
| Kenへの示唆 | 1分 | 自分の事業への提案（事実とは区別して話す） |
| エンディング | 15秒 | 評価のお願い |

## なぜ無料でできるのか（と、その条件）

| 部品 | 無料の根拠 | 1日に使う回数（目安） |
|---|---|---|
| Gemini 3.8 Flash（選定・台本・照合） | Gemini API の無料枠（入出力とも無料） | 3回 |
| Gemini 2.5 Flash-Lite（検索つきの調査） | 無料枠で Google 検索が使えるのは 2.5 系だけ。ただし新しく作ったプロジェクトでは 2.5 系が使えないことがあり、その場合は検索なしで進みます | 2回 |
| Gemini 3.8 Flash TTS（声） | 無料枠あり | 2〜3回 |
| VOICEVOX（声の予備） | 無料。「VOICEVOX:キャラ名」のクレジット表記が条件（自動で表記） | 上限なし |
| GitHub Actions / Pages | 公開リポジトリなら無料 | 1回（10分前後） |
| 公式 RSS | 各サイトが公開しているもの | 約18本 |

1日の上限回数はプロジェクトごとに Google AI Studio の「Rate limit」画面で確認できます（太平洋時間の0時＝日本時間の16〜17時ごろにリセット）。上限に当たったときは、自動で次のモデル → 音声は VOICEVOX に切り替えます。

**無料の代わりに受け入れること（正直な注意点）**
- Google の料金表では、**無料枠で送った内容は Google の製品改善に使われる**扱いです。台本（「示唆」コーナーを含む）も送られるので、公開されて困る事業の中身は `PRIVATE_CONTEXT` に書かないでください。気になる場合は `show.yaml` の `tts.engine` を `voicevox` にすると、少なくとも音声は Google に送りません
- 番組ページ（台本の全文・出典）と MP3 は、URL を知っていれば誰でも開けます（Apple Podcasts の検索には載らない設定）
- 無料枠の内容・上限は Google の判断で変わることがあります
- ニュースの候補は「登録した RSS に載ったもの」だけです。マーケ・EC系は RSS が少ないので、深掘りの検索で補います
- 有料版（Claude を使う版）と比べると、検索の段階でサイトを絞り込めない（あとから除外する）ぶん、深掘りの素材が少なくなる日があります

---

## セットアップ（初回だけ・20〜40分）

### STEP 1. GitHub にリポジトリを作る
GitHub 右上「＋」→「New repository」→ 名前は自由（例: `my-daily-brief`）→ **Public** を選ぶ →「Create repository」

> 無料プランで GitHub Pages を使うには Public が条件です。そのため、コード・`config/`・`state/`（放送履歴と学習結果）は誰でも見られ、番組ページと MP3 も URL を知っていれば誰でも開けます。
> プロフィールを見せたくない場合は、`config/profile.yaml` の中身を Secret の `PROFILE_YAML` に入れ（STEP 4）、リポジトリの `profile.yaml` は一般的な内容に書き換えてください。

### STEP 2. このフォルダの中身をアップロード
作ったリポジトリの「uploading an existing file」→ このフォルダの**中身**（`.github` フォルダも含む）をドラッグ＆ドロップ →「Commit changes」
- `.github` が表示されない場合（Mac など）: 「Add file → Create new file」で名前に `.github/workflows/daily.yml` と入力し、同じ名前のファイルの中身を貼り付け
- ターミナル派は下の「Claude Code / ターミナルで進める場合」へ

### STEP 3. Gemini の API キーを取る（無料・カード登録なし）
Google AI Studio（https://aistudio.google.com ）→「Get API key」→「Create API key」→ キーをコピー
> 課金（Billing）を有効にしない限り、無料枠のまま使われ、料金は発生しません。

### STEP 4. キーを GitHub に登録（Secrets）
「Settings → Secrets and variables → Actions → New repository secret」

| 名前 | 中身 | 必須 |
|---|---|---|
| `GEMINI_API_KEY` | Gemini の API キー | ✅ |
| `FEEDBACK_URL` / `FEEDBACK_TOKEN` | 評価ボタン用（STEP 9） | 任意 |
| `PRIVATE_CONTEXT` | 自分の状況の補足（リポジトリには載らないが、台本に反映された部分は番組ページに載り、無料枠では Google にも送られる） | 任意 |
| `PROFILE_YAML` | `config/profile.yaml` の中身をまるごと（公開リポジトリに置きたくない場合。設定するとファイルより優先） | 任意 |

### STEP 5. GitHub Pages をオンにする
「Settings → Pages → Build and deployment → Source」で **GitHub Actions** を選ぶ。

### STEP 6. テスト実行（API を使わない）
「Actions」タブ → 左の **daily-podcast** →「Run workflow」→ **テスト実行にチェック** →「Run workflow」。
緑のチェックになれば配信までの流れは正常です。`https://<ユーザー名>.github.io/<リポジトリ名>/` に「【テスト】」の回が出ます。

### STEP 7. 本番の1回目
チェックを**外して**「Run workflow」。10分前後で完成します（同じ日のテスト回は置き換わります）。
ログの最初に「RSS ◯/18 本から候補 ◯ 件」と出るので、止まっている RSS があれば `config/sources.yaml` から外すか差し替えてください。

### STEP 8. ポッドキャストアプリに登録
URL: `https://<ユーザー名>.github.io/<リポジトリ名>/feed.xml`（番組ページのトップにも表示）
- **iPhone（Apple Podcasts）**: ライブラリ → 右上の「…」→ URL で番組を追加（フォロー）→ 貼り付け
- **Android など**: Pocket Casts、AntennaPod など「URL（RSS）で追加」できるアプリ

> Apple Podcasts の検索には載らない設定（`itunes:block`）です。URL は他の人に共有しないでください。

### STEP 9（任意・5分）. 評価ボタンをつなぐ（Google スプレッドシート・無料）
1. Google スプレッドシートを新規作成 →「拡張機能 → Apps Script」
2. `apps_script/feedback.gs` の中身を全部貼り付け、`TOKEN` を好きな合言葉に変えて保存
3. 「デプロイ → 新しいデプロイ → 種類: ウェブアプリ」／実行するユーザー: 自分／アクセスできるユーザー: 全員 → デプロイ
4. 表示された URL を Secret `FEEDBACK_URL` に、合言葉を `FEEDBACK_TOKEN` に登録

---

## 毎日の使い方
1. 朝、ポッドキャストアプリに新しい回が届く → 聴く
2. 番組ページでコーナーごとに 👍 / 🔍もっと深く / 👎 → その分野の重みが自動で上下（±2まで。少しずつ元に戻る）
3. 「取り上げてほしいテーマ」に書く → 次の回以降の深掘りに優先して入る

## カスタマイズ
| 変えたいこと | ファイル |
|---|---|
| 興味分野と重み・学習トラック・避けたい話題 | `config/profile.yaml` |
| ニュースを集める RSS ／ 出典として使ってよいサイト | `config/sources.yaml`（`feeds` / `trusted`） |
| 長さ・本数・声・モデル・保存する回数 | `config/show.yaml` |
| 台本や選び方の指示 | `prompts/*.md` |
| 配信時刻 | `.github/workflows/daily.yml` の `cron`（UTC。日本時間 −9時間） |

- RSS を足すときは、そのサイトの「RSS 配信について」のページで URL を確認して `feeds` に追加します。
- 声は Google AI Studio で試聴して `voice` を変更。VOICEVOX の話者は `voicevox`（キャラクター名）で変更。各キャラクターの利用規約は VOICEVOX 公式で確認してください。

## 品質の仕組みと、正直な限界
**信頼性を上げる仕掛け**
- ニュースの候補は、自分で登録した公式 RSS からだけ集める（どこから来た情報かが常にはっきりしている）
- 記事ページは robots.txt を確認し、禁止されていれば取りに行かない（その記事は RSS の要約の範囲でだけ話す）
- 深掘りの検索結果は、Gemini が示した「どの文がどのページに基づくか」を使い、信頼リストのページに基づく文だけを素材に残す
- 台本の全行に出典 ID を付け、別の呼び出しで記事本文と1行ずつ照合 → 修正・削除
- 番組ページに出典リンク・照合結果・台本の全文を残す

**限界**
- 100% の正確さは保証できません。AI の読み違い、元記事の誤り、続報での変化は起こり得ます
- お金や事業の判断に使う数字は、必ず出典を開いて確認してください

## うまくいかないとき
| 症状 | 確認すること |
|---|---|
| Actions が赤（失敗） | 失敗した回を開いてログを確認。途中経過の JSON は「Artifacts」からダウンロードできます |
| `configure-pages` で失敗 | STEP 5（Pages の Source を GitHub Actions に）ができているか |
| 「GEMINI_API_KEY が設定されていません」 | STEP 4 の Secret の名前が正確か |
| 「今日の無料枠を使い切りました」 | 自動で次のモデル・VOICEVOX に切り替わります。毎日続くなら AI Studio で上限を確認し、`episode_minutes` を減らす |
| RSS「◯◯」: 取得できず | その RSS の URL が変わった可能性。`sources.yaml` から外すか差し替え |
| 記録の保存（push）で失敗 | Settings → Actions → General → Workflow permissions を「Read and write」に |
| 定期実行が止まった | 公開リポジトリでは、60日間動きがないと定期実行が自動で止まる仕組みがあります。止まったら Actions 画面で daily-podcast を選び「Enable workflow」で再開 |
| 配信が朝より遅れる | GitHub の定期実行は混雑時に遅れることがあります（毎時0分ちょうどは特に混むので、40分に設定しています） |

## Claude Code / ターミナルで進める場合
このフォルダを Claude Code で開いて「README の STEP 1〜7 を一緒に進めて」と頼むのが一番ラクです。自分で打つ場合は:

```bash
git init && git add -A && git commit -m "first"
gh auth login
gh repo create my-daily-brief --public --source . --push
gh secret set GEMINI_API_KEY          # 貼り付けて Enter（画面には表示されません）
# ブラウザで Settings → Pages → Source を「GitHub Actions」に（STEP 5）
gh workflow run daily-podcast -f mock=true    # テスト実行
```

## 手元のパソコンで試す（任意）
Python 3.11 以上と ffmpeg が必要です。

```bash
pip install -r requirements.txt
python -m podcast --mock      # APIキーなし・ネット接続なしで全工程を実行 → site/index.html を開く
python tests/test_e2e.py      # 4回分まわして、除外・学習・照合・VOICEVOX 切り替えなどを自動チェック
```

## ファイル構成
```
config/          show.yaml（番組）・profile.yaml（自分）・sources.yaml（RSS と信頼リスト）
prompts/         各工程への指示文
podcast/         本体（feeds→editor→research→writer→tts/voicevox→audio→publish）
apps_script/     評価ボタンの受け口（Google スプレッドシート用）
state/           放送履歴と学習結果（毎日自動で更新）
tests/           にせサーバーを使った通しテスト
.github/workflows/daily.yml   毎朝の自動実行
```
