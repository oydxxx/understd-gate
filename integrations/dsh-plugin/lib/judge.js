// 判定核心（纯函数，不依赖任何 DSH 包）。
//
// 单独成文件的原因：这部分能脱离宿主环境测试。
// 任何人 clone 下来，不用装 DSH 就能跑 list 里的测试。

/** 从检查器输出里读出硬违规条数（读不到返回 null） */
export function parseHardCount(stdout) {
  const m = /硬违规\s*·\s*必须改（(\d+)\s*处）/.exec(String(stdout ?? ''));
  if (m) return Number(m[1]);
  if (/结论：没有硬违规/.test(String(stdout ?? ''))) return 0;
  return null;
}

/** 抓出违规明细：规则号、位置、原句、改法 */
export function parseViolations(stdout) {
  const out = [];
  let cur = null;
  for (const line of String(stdout ?? '').split('\n')) {
    const m = /^\[(U\d+[a-z]?)\]\s*(.*)$/.exec(line.trim());
    if (m) {
      if (cur) out.push(cur);
      cur = { rule: m[1], where: m[2], text: '', fix: '' };
      continue;
    }
    if (!cur) continue;
    const t = line.trim();
    if (t.startsWith('原句：')) cur.text = t.slice(3);
    else if (t.startsWith('改法：')) cur.fix = t.slice(3);
  }
  if (cur) out.push(cur);
  return out;
}

/** 抓出命中的规则号，去重。onlyHard=true 时只取「硬违规」那一段，
 *  免得把提示级的规则（U2、U3）混进硬违规清单里。 */
export function parseRules(stdout, onlyHard = false) {
  let text = String(stdout ?? '');
  if (onlyHard) text = text.split(/提示\s*·\s*需人确认/)[0];
  const rules = new Set();
  for (const m of text.matchAll(/\[(U\d+[a-z]?)\]/g)) rules.add(m[1]);
  return [...rules];
}

/** 这一步要不要查 */
export function shouldCheck(text, cfg) {
  const s = String(text ?? '').trim();
  if (s.length < cfg.minChars) return false;
  // 冷读者跑在 headless 会话里，输出就是判定 JSON。
  // 不排除它，闸门会查自己，冷读套冷读，直接递归。
  if (/"复述"|"一致"|"卡住"|"most_remembered"/.test(s)) return false;
  return true;
}

/** 提醒文本：短、可执行、带原句（不指临时文件，那会被删） */
export function renderNotice(hard, rules, details = []) {
  const list = rules.length ? `（${rules.join('、')}）` : '';
  const lines = [
    `[宪法闸门] 上一条回复有 ${hard} 处硬违规${list}。`,
    '先把违规句子改短、改明白，再继续下一步。',
  ];
  for (const d of details.slice(0, 2)) {
    lines.push(`- [${d.rule}] ${d.where}：${String(d.text || '').slice(0, 40)}`);
    if (d.fix) lines.push(`  改法：${d.fix}`);
  }
  if (details.length > 2) lines.push(`（另有 ${details.length - 2} 处，见报告）`);
  return lines.join('\n');
}

export function renderColdNotice(result) {
  const head = result.consistent === '否'
    ? '[宪法闸门·冷读] 读者没能复述出你的落点。'
    : '[宪法闸门·冷读] 读者读懂了，但有卡词。';
  return [
    head,
    `读者读到：${String(result.recite ?? '').slice(0, 120)}`,
    result.stuck && result.stuck !== '无' ? `卡在：${String(result.stuck).slice(0, 120)}` : '',
  ].filter(Boolean).join('\n');
}
