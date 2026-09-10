// dsh-constitution-gate — 输出宪法闸门插件（v0.1，提醒档）
//
// 解决一个问题：宪法写好了，检查器也写好了，但"跑不跑"还靠 agent 自觉。
// 这个插件把"跑"变成流程，不靠记性。
//
// 机制：
//   1. 提示层：向系统提示注入闸门协议（injectProtocol，默认开）
//   2. 采集：订阅 session/event，assistant/message 累积每步正文
//   3. 检查：step/end 时把正文落成临时文件，调用 understd_check.py
//   4. 提醒：agent/pre-step 注入提醒（supervise）；gate 模式改为 reject（回合 blocked）
//   5. 冷读（可选，默认关）：长文本异步跑 cold_read.py --lenient，结果下次注入
//   6. 报告：会话结束写 ~/.dsh/constitution-gate-reports/<sessionId>.md
//
// 设计取舍：v0.1 默认只提醒，不拦截。检查器会误报，拦截可能把对话卡死。
// 跑顺（连续二十条无误报）再把 mode 翻成 gate。

import z from '@deepseek-ai/schemastery';
import { settingsNamespace } from '@deepseek-ai/dsh-settings';
import {
  parseHardCount, parseRules, parseViolations, shouldCheck, renderNotice, renderColdNotice,
} from './judge.js';
import { execFile } from 'node:child_process';
import { mkdir, writeFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir, homedir } from 'node:os';
import { randomUUID } from 'node:crypto';

export const name = 'dsh-constitution-gate';
export const inject = ['sessions', 'systemPrompt', 'settings'];


export const Config = z.object({
  /** supervise=只提醒 / gate=硬拦截 / off=关 */
  mode: z.union([z.const('supervise'), z.const('gate'), z.const('off')]).default('supervise'),
  /** 输出宪法所在根目录（工具/ 与 协议/ 都在其下） */
  dshRoot: z.string().default(''),
  pythonBin: z.string().default('python3'),
  /** 短于此字数的步骤不检查（寒暄、单句回答） */
  minChars: z.number().default(120),
  timeoutMs: z.number().default(20000),
  remindOnViolation: z.boolean().default(true),
  injectProtocol: z.boolean().default(true),
  /** 冷读（较慢，每次约 5 秒起） */
  coldRead: z.boolean().default(false),
  coldRuns: z.number().default(3),
  coldMinChars: z.number().default(800),
  reportDir: z.string().default(join(homedir(), '.dsh', 'constitution-gate-reports')),
});

const PROTOCOL_TEXT = [
  '## 输出宪法闸门（dsh-constitution-gate）',
  '系统会在每步回复后自动跑《输出宪法》检查器，不依赖你自觉。',
  '正文超过阈值时，冷读者闸门也可能异步跑一次，判定"读者能否复述出你的落点"。',
  '三件事按此执行：',
  '1. 说人话优先：短句、术语给人话翻译、抽象结论跟具体场景。',
  '2. 长回复交付前，自己先跑：python3 "<DSH>/工具/understd_check.py" 草稿.md',
  '3. 收到闸门提醒时，先改正被指出的句子，再继续下一步。',
].join('\n');

// ---------------------------------------------------------------- 判定核心（纯函数，可单测）

export {
  parseHardCount, parseRules, parseViolations, shouldCheck, renderNotice, renderColdNotice,
} from './judge.js';

// ---------------------------------------------------------------- 插件主体

function apply(ctx, config) {
  if (config.mode === 'off') return;
  const checkerPath = join(config.dshRoot, '工具', 'understd_check.py');
  const coldPath = join(config.dshRoot, '工具', 'cold_read.py');
  console.error(`[constitution-gate] v0.1 loaded (mode=${config.mode}, coldRead=${config.coldRead})`);
  try {
    ctx.settings.register(settingsNamespace('constitution-gate'), Config, { applies: 'restart' });
  } catch (err) {
    console.error(`[constitution-gate] settings register skipped: ${String(err)}`);
  }
  if (config.injectProtocol) {
    ctx.systemPrompt.section({ name: 'constitution-gate-protocol', order: 110, text: PROTOCOL_TEXT });
  }

  const states = new Map();          // sessionId -> { steps: [], current: null }
  const log = (msg) => {
    console.error(`[constitution-gate] ${msg}`);
    try { ctx.logger.warn(`[constitution-gate] ${msg}`); } catch { /* headless 无 logger */ }
  };

  const run = (cmd, args, timeoutMs) => new Promise((resolve) => {
    execFile(cmd, args, { timeout: timeoutMs, maxBuffer: 4 * 1024 * 1024 },
      (err, stdout, stderr) => resolve({ err, stdout: String(stdout ?? ''), stderr: String(stderr ?? '') }));
  });

  /** 把一个步骤的正文交给检查器 */
  const checkText = async (sessionId, step) => {
    const file = join(tmpdir(), `constitution-gate-${sessionId}-${step.seq}.md`);
    try {
      await writeFile(file, step.text, 'utf8');
      const { err, stdout } = await run(config.pythonBin, [checkerPath, file], config.timeoutMs);
      const hard = parseHardCount(stdout);
      if (hard === null) {
        log(`${sessionId} step ${step.seq}: 检查器输出读不懂，跳过`);
        return null;
      }
      return { hard, rules: parseRules(stdout, true), details: parseViolations(stdout), file };
    } catch (e) {
      log(`${sessionId} step ${step.seq}: 检查失败 ${String(e)}`);
      return null;
    }
  };

  /** 冷读异步跑，慢，不挡当前回合 */
  const coldCheck = async (sessionId, step, st) => {
    const file = join(tmpdir(), `constitution-gate-cold-${sessionId}-${step.seq}.md`);
    try {
      await writeFile(file, step.text, 'utf8');
      const { stdout } = await run(config.pythonBin,
        [coldPath, file, '--runs', String(config.coldRuns), '--lenient', '--json'],
        config.timeoutMs * 4);
      const parsed = JSON.parse(stdout.slice(stdout.indexOf('{'), stdout.lastIndexOf('}') + 1));
      if (parsed.verdict === '退回') st.notices.push(renderColdNotice({
        consistent: parsed.consistent, recite: parsed.recites?.[0], stuck: parsed.stuck?.[0],
      }));
      step.cold = parsed.verdict;
      return parsed;
    } catch (e) {
      log(`${sessionId} step ${step.seq}: 冷读失败 ${String(e)}`);
      return null;
    } finally {
      rm(file, { force: true }).catch(() => {});
    }
  };

  /** 一步查完：判定、记录、排队提醒（或接着冷读） */
  const runStepCheck = async (st, step) => {
    const verdict = await checkText(st.sessionId, step);
    if (process.env.CG_DEBUG) console.error(`[cg-debug] checked=${verdict ? verdict.hard : 'null'}`);
    if (!verdict) return;
    step.hard = verdict.hard;
    step.rules = verdict.rules;
    step.details = (verdict.details ?? []).filter((d) => verdict.rules.includes(d.rule));
    step.report = verdict.file;
    st.steps.push(step);
    if (verdict.hard > 0) {
      log(`${st.sessionId} turn ${step.turn}/step ${step.step}: ${verdict.hard} 处硬违规（${verdict.rules.join('、')}）`);
      if (config.remindOnViolation) {
        st.notices.push(renderNotice(verdict.hard, verdict.rules, verdict.details ?? []));
      }
      return;
    }
    if (config.coldRead && step.text.length >= config.coldMinChars) {
      await coldCheck(st.sessionId, step, st);
    }
  };

  const stateOf = (session) => {
    let st = states.get(session.id);
    if (!st) {
      st = { sessionId: session.id, steps: [], current: null, notices: [], startedAt: new Date().toISOString() };
      states.set(session.id, st);
    }
    return st;
  };

  ctx.on('session/event', async (session, event) => {
    if (process.env.CG_DEBUG) console.error(`[cg-debug] event=${event.type} seq=${event.seq}`);
    const st = stateOf(session);
    switch (event.type) {
      case 'step/start': {
        st.current = { seq: event.seq, turn: event.data?.turn, step: event.data?.step, text: '' };
        break;
      }
      case 'assistant/message': {
        if (process.env.CG_DEBUG && !st._dumped) {
          st._dumped = true;
          console.error('[cg-debug] assistant/message payload=' + JSON.stringify(event.data).slice(0, 600));
        }
        if (!st.current) break;
        for (const c of event.data?.message?.content ?? []) {
          if (c.type === 'text') st.current.text += (c.text ?? '');
        }
        if (process.env.CG_DEBUG) {
          const c0 = event.data?.message?.content?.[0];
          console.error(`[cg-debug] am step=${st.current.step} len=${st.current.text.length}`
            + ` contentType=${Object.prototype.toString.call(event.data?.message?.content)}`
            + ` item0=${JSON.stringify(c0)?.slice(0, 120)}`);
        }
        break;
      }
      case 'step/end': {
        const step = st.current;
        st.current = null;
        if (!step) break;
        if (process.env.CG_DEBUG) console.error(`[cg-debug] step/end chars=${step.text.length}`);
        if (!shouldCheck(step.text, config)) {
          step.skipped = 'short';
          break;
        }
        // 检查是异步的：把 promise 存下来，turn/end 要等它，否则报告会漏掉这一步
        st.inflight = runStepCheck(st, step).catch((e) => log(`检查异常：${String(e)}`));
        break;
      }
      case 'turn/end': {
        const inflight = st.inflight;
        st.inflight = null;
        Promise.resolve(inflight).catch(() => {}).then(() => writeReport(st.sessionId).catch(() => {}));
        break;
      }
      default:
        break;
    }
  });

  ctx.on('agent/pre-step', async ({ agent, messages }, next) => {
    const sid = agent?.session?.id ?? agent?.session ?? agent?.sessionId;
    const st = sid === undefined ? undefined : states.get(sid);
    const notices = st ? st.notices.splice(0) : [];
    if (config.mode === 'gate') {
      const last = [...states.values()]
        .flatMap((s) => s.steps)
        .filter((s) => s.hard > 0).pop();
      if (last) {
        log(`${agent.id}: 上一步有 ${last.hard} 处硬违规，闸门拦截（回合 blocked）`);
        return {
          kind: 'reject',
          feedback: `constitution-gate: ${last.hard} hard violations (${(last.rules ?? []).join(',')}). Fix them before continuing.`,
        };
      }
    }
    const decision = await next();
    if (!notices.length) return decision;
    if (decision.kind === 'enter') {
      const extra = notices.map((text) => ({
        id: randomUUID(),
        role: 'user',
        content: [{ type: 'text', text }],
        source: { kind: 'plugin', plugin: 'dsh-constitution-gate', form: 'notice', summary: 'constitution-gate' },
      }));
      return { ...decision, messages: [...extra, ...(decision.messages ?? messages)] };
    }
    return decision;
  });

  const writeReport = async (sessionId) => {
    const st = states.get(sessionId);
    if (!st) return;
    const lines = [
      `# 宪法闸门报告 · ${sessionId}`,
      '',
      `- 模式：${config.mode}`,
      `- 生成时间：${new Date().toISOString()}`,
      `- 检查步数：${st.steps.filter((s) => s.hard !== undefined).length}`,
      `- 硬违规步数：${st.steps.filter((s) => s.hard > 0).length}`,
      '',
      '| 步 | turn/step | 字数 | 硬违规 | 规则 | 冷读 |',
      '|---|---|---|---|---|---|',
    ];
    for (const s of st.steps) {
      if (s.hard === undefined) continue;
      lines.push(`| ${s.seq} | ${s.turn}/${s.step} | ${s.text.length} | ${s.hard} | ${(s.rules ?? []).join('、') || '—'} | ${s.cold ?? '—'} |`);
    }
    const withDetail = st.steps.filter((s) => (s.details ?? []).length);
    if (withDetail.length) {
      lines.push('', '## 违规明细', '');
      for (const s of withDetail) {
        lines.push(`### 步 ${s.seq}（turn ${s.turn}/step ${s.step}）`);
        for (const d of s.details) {
          lines.push(`- [${d.rule}] ${d.where}：${d.text}`);
          if (d.fix) lines.push(`  - 改法：${d.fix}`);
        }
      }
    }
    try {
      await mkdir(config.reportDir, { recursive: true });
      await writeFile(join(config.reportDir, `${sessionId}.md`), lines.join('\n'), 'utf8');
    } catch (e) {
      log(`报告写入失败：${String(e)}`);
    }
  };

  ctx.on('session/disposed', (session) => {
    writeReport(session.id).catch(() => {});
    states.delete(session.id);
  });

  // 导出给测试与运维用
  apply.__test = { states, checkText };
}

export { apply };
