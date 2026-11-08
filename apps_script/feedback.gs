/**
 * 評価ボタンの受け口（Google スプレッドシート + Apps Script）
 *
 * 1. Google スプレッドシートを新しく作る → メニュー「拡張機能」→「Apps Script」
 * 2. 最初からあるコードを消して、このファイルの中身を全部貼り付ける
 * 3. 下の TOKEN を好きな合言葉（英数字20文字くらい）に変えて保存
 *    → GitHub の Secret「FEEDBACK_TOKEN」にも同じ文字列を登録する
 * 4. 右上「デプロイ」→「新しいデプロイ」→ 種類「ウェブアプリ」
 *    実行するユーザー「自分」／アクセスできるユーザー「全員」→「デプロイ」→ 許可を求められたら許可
 * 5. 表示された「ウェブアプリの URL」を GitHub の Secret「FEEDBACK_URL」に登録する
 */
const TOKEN = 'ここを好きな合言葉に変える';
const SHEET = 'feedback';
const ACTIONS = ['like', 'dislike', 'deeper', 'request', 'rate1', 'rate2', 'rate3', 'rate4', 'rate5'];

function doGet(e) {
  const p = (e && e.parameter) || {};
  if (p.token !== TOKEN) return out_({ ok: false, error: 'bad token' });
  const sh = sheet_();

  // 番組づくりの側が評価を読み出すとき
  if (p.mode === 'list') {
    const since = String(p.since || '0');
    const rows = sh.getDataRange().getDisplayValues().slice(1)
      .filter(function (r) { return String(r[1]) >= since; })
      .map(function (r) {
        return { ts: r[0], ep: String(r[1]), segment: r[2], interest: r[3], action: r[4], text: r[5] };
      });
    return out_({ ok: true, rows: rows });
  }

  // 番組ページのボタンが押されたとき
  if (ACTIONS.indexOf(p.action) === -1) return out_({ ok: false, error: 'bad action' });
  sh.appendRow([
    "'" + new Date().toISOString(),
    "'" + String(p.ep || '').slice(0, 8),
    safe_(String(p.segment || '').slice(0, 40)),
    safe_(String(p.interest || '').slice(0, 40)),
    p.action,
    safe_(String(p.text || '').slice(0, 200)),
  ]);
  return out_({ ok: true });
}

function sheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(SHEET);
  if (!sh) {
    sh = ss.insertSheet(SHEET);
    sh.appendRow(['ts', 'ep', 'segment', 'interest', 'action', 'text']);
  }
  return sh;
}

// 「=」などで始まる文字が数式として動かないようにする
function safe_(s) {
  return /^[=+\-@]/.test(s) ? "'" + s : s;
}

function out_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
