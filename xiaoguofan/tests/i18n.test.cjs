'use strict';
// Browser-side translator (portal/static/i18n.js) with the real English dictionary.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const i18n = require('../portal/static/i18n.js');
const src = fs.readFileSync(path.join(__dirname, '../portal/static/i18n-en.js'), 'utf8');
const dict = JSON.parse(src.slice(src.indexOf('window.XGF_EN=') + 14).trim().replace(/;$/, ''));
const tr = i18n.makeTranslator(dict);

test('language: saved choice wins, otherwise zh* browsers get Chinese and everyone else English', () => {
  assert.strictEqual(i18n.pickLang('zh-CN', null), 'zh');
  assert.strictEqual(i18n.pickLang('zh-TW', null), 'zh');
  assert.strictEqual(i18n.pickLang('en-US', null), 'en');
  assert.strictEqual(i18n.pickLang('ja-JP', null), 'en');
  assert.strictEqual(i18n.pickLang('zh-CN', 'en'), 'en');
  assert.strictEqual(i18n.pickLang('en-GB', 'zh'), 'zh');
});

test('exact text, surrounding spaces kept', () => {
  assert.strictEqual(tr('  刚刚 '), '  just now ');
  assert.strictEqual(tr('Hello'), 'Hello');
  assert.strictEqual(tr('主力号'), '主力号');
});

test('templates translate their pieces and pick plurals', () => {
  assert.strictEqual(tr('近 30 天 1 次请求'), '1 request in the last 30 days');
  assert.strictEqual(tr('近 30 天 7 次请求'), '7 requests in the last 30 days');
  assert.strictEqual(tr('约 18:00 恢复（3 分钟后）'), 'back around 18:00 (in 3 min)');
  assert.strictEqual(tr('成功 26 次，额度用完 7 次，失败 4 次，已取消 5 次'), 'Succeeded 26, Out of quota 7, Failed 4, Cancelled 5');
  assert.strictEqual(tr('近 7 天 · 北京时间 · 更新于 11/24 18:00'), 'Last 7 days · Beijing time · updated 11/24 18:00');
});

test('dictionary values are English and keep their placeholders', () => {
  const han = /[\u3400-\u9fff]/;
  const keep = /\[防截断\]|小锅饭 · 导出 JSON/g;
  for (const [k, v] of Object.entries(dict.exact)) assert.ok(!han.test(v.replace(keep, '')), k);
  for (const [k, v] of Object.entries(dict.templates)) {
    assert.ok(!han.test(v.replace(keep, '')), k);
    const names = new Set([...k.matchAll(/\{(\w+)\}/g)].map(m => m[1]));
    for (const m of v.matchAll(/\{(\w+)(?:\|[^}]*)?\}/g)) assert.ok(names.has(m[1]), `${k} -> ${v}`);
  }
});

test('long untranslated text stays fast', () => {
  const long = Array.from({length: 40}, (_, i) => '用户内容' + i).join('，') + '。' + Array.from({length: 20}, (_, i) => '文字 · ' + i).join('；');
  const t0 = Date.now(); tr(long); assert.ok(Date.now() - t0 < 500);
});
