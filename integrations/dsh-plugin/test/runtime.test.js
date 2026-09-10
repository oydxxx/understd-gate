// 真实链路测试：假 ctx + 真检查器（会真的调 python3 跑 understd_check.py）。
import { test } from 'node:test';
import assert from 'node:assert/strict';

// 这个测试要真跑插件，得先有 DSH 的依赖包。
// 没有就跳过并说明——clone 下来的人不该被一个缺依赖的测试卡住。
let apply, Config;
try {
  ({ apply, Config } = await import('../lib/index.js'));
} catch (err) {
  console.log(`跳过运行时测试：缺少 DSH 依赖（${err.code ?? err.message}）。装好 DSH 后再跑。`);
  process.exit(0);
}

// 跑这个测试前，把 ROOT 指向你放本项目的位置（插件就是按 dshRoot 找检查器的）。
const ROOT = process.env.UNDERSTD_ROOT
  ?? require('node:path').resolve(import.meta.dirname, '../../..');
const DSH_ROOT = ROOT;

function fakeCtx() {
  const handlers = {};
  const ctx = {
    on: (name, fn) => { (handlers[name] ??= []).push(fn); },
    logger: { warn() {}, info() {} },
    settings: { register() {} },
    systemPrompt: { section() {} },
    agents: { get: (id) => ({ id: `agent-of-${id}` }) },
  };
  return { ctx, handlers };
}

const config = { ...Config({ }), mode: 'supervise', dshRoot: DSH_ROOT, minChars: 10, coldRead: false };

test('长文本触发检查器，并排队提醒', async () => {
  const { ctx, handlers } = fakeCtx();
  apply(ctx, config);
  const [onEvent] = handlers['session/event'];
  const session = { id: 'test-session-1' };
  await onEvent(session, { seq: 1, type: 'step/start', data: { turn: 1, step: 1 } });
  await onEvent(session, {
    seq: 2, type: 'assistant/message',
    data: { message: { content: [{ type: 'text', text: '读者读完全文，脑中若没有一个词被重新称过——这篇文，等于没写。这句话故意写得很长很长，用来触发硬违规判定。' }] } },
  });
  await onEvent(session, { seq: 3, type: 'step/end', data: { turn: 1, step: 1 } });
  await new Promise((r) => setTimeout(r, 2500));

  const [preStep] = handlers['agent/pre-step'];
  const decision = await preStep(
    { agent: { id: 'a1', session: { id: 'test-session-1' } }, messages: [] },
    async () => ({ kind: 'enter', messages: [] }));
  const text = JSON.stringify(decision.messages ?? []);
  assert.match(text, /宪法闸门/);
  assert.match(text, /硬违规/);
  // 提醒必须自带可照抄的原句与改法：以前它只给一个临时文件路径，而那个文件会被删掉
  assert.match(text, /U1/);
  assert.match(text, /原句|改法/);
  assert.match(text, /第 \d+ 段/);
});

test('短文本不触发检查', async () => {
  const { ctx, handlers } = fakeCtx();
  apply(ctx, config);
  const [onEvent] = handlers['session/event'];
  const session = { id: 'test-session-2' };
  await onEvent(session, { seq: 1, type: 'step/start', data: { turn: 1, step: 1 } });
  await onEvent(session, { seq: 2, type: 'assistant/message', data: { message: { content: [{ type: 'text', text: '好的。' }] } } });
  await onEvent(session, { seq: 3, type: 'step/end', data: { turn: 1, step: 1 } });
  await new Promise((r) => setTimeout(r, 500));
  const [preStep] = handlers['agent/pre-step'];
  const decision = await preStep({ agent: { id: 'a2', session: { id: 'test-session-2' } }, messages: [] }, async () => ({ kind: 'enter', messages: [] }));
  assert.equal(decision.messages.length, 0);
});
