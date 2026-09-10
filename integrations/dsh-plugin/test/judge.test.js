// 判定核心单测：不装 DSH 也能跑（node --test test/judge.test.js）。
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseHardCount, parseRules, parseViolations, shouldCheck, renderNotice, renderColdNotice } from '../lib/judge.js';

const SAMPLE = `理解检查 · 稿.md
规模：正文 7 块 / 16 句 / 约 293 字

硬违规 · 必须改（3 处）
[U1] 第 2 段第 1 句
    改法：断成短句。现在 26 字。
[U2c] 第 3 段第 1 句
[U1] 第 5 段第 3 句

结论：3 处硬违规，改到零再发。`;

const CLEAN = `理解检查 · 稿.md
规模：正文 3 块 / 8 句

结论：没有硬违规。常识那关请人过。`;

test('读得出硬违规条数', () => {
  assert.equal(parseHardCount(SAMPLE), 3);
  assert.equal(parseHardCount(CLEAN), 0);
  assert.equal(parseHardCount('乱七八糟'), null);
});

test('规则号去重后按出现顺序返回', () => {
  assert.deepEqual(parseRules(SAMPLE), ['U1', 'U2c']);
});

test('只取硬违规规则时，不混进提示级规则', () => {
  const mixed = `硬违规 · 必须改（1 处）
[U1] 第 14 段第 1 句

提示 · 需人确认（2 处）
[U2] 第 13 段第 1 句
[U3] 第 13 段第 1 句`;
  assert.deepEqual(parseRules(mixed), ['U1', 'U2', 'U3']);
  assert.deepEqual(parseRules(mixed, true), ['U1']);
});

test('短步骤不检查，长步骤检查', () => {
  const cfg = { minChars: 120 };
  assert.equal(shouldCheck('嗯。', cfg), false);
  assert.equal(shouldCheck('啊'.repeat(120), cfg), true);
});

test('冷读者的判定 JSON 不被闸门再查一遍（防递归）', () => {
  const cfg = { minChars: 10 };
  assert.equal(shouldCheck('{"复述":"讲的是写作","最该记住":"x","卡住":"无"}', cfg), false);
  assert.equal(shouldCheck('啊'.repeat(50), cfg), true);
});

test('解析出违规明细：规则、位置、原句、改法', () => {
  const v = parseViolations(SAMPLE);
  assert.equal(v.length, 3);
  assert.equal(v[0].rule, 'U1');
  assert.equal(v[0].where, '第 2 段第 1 句');
  assert.match(v[0].fix, /断成短句/);
});

test('提醒文本带条数、规则与可照抄的原句（不再指临时文件）', () => {
  const details = parseViolations(SAMPLE);
  const n = renderNotice(3, ['U1', 'U2c'], details);
  assert.match(n, /3 处硬违规/);
  assert.match(n, /U1、U2c/);
  assert.match(n, /第 2 段第 1 句/);
  assert.match(n, /改法：/);
  assert.doesNotMatch(n, /tmp/);
});

test('冷读提醒说清读者读到什么', () => {
  const n = renderColdNotice({ consistent: '否', recite: '讲的是别的事', stuck: '称词' });
  assert.match(n, /没能复述/);
  assert.match(n, /讲的是别的事/);
  assert.match(n, /称词/);
});
