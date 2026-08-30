/**
 * 跑法：node --experimental-strip-types tests/notice.test.mjs
 *
 * 测的是一个 **tsc 和 next build 都查不出来** 的 bug：无限重渲染。
 *
 * 起因：`useNotice()` 原来每次渲染都返回全新的函数。组件里把 `clear`/`fail`
 * 写进 useCallback / useEffect 的依赖数组之后，依赖每次渲染都在变 ——
 * effect 每次渲染都重跑，里面一 setState 又触发渲染，就停不下来了。
 *
 * 实际后果（用户先撞上的）：页面肉眼可见地闪，每秒往后端打几百个请求，
 * dev server 代理被冲垮，案件页 / 检索 / 流程一起报"连不上后端"。
 * 类型全对、构建全过，只有真跑起来才炸 —— 所以必须有这个测试。
 */
import assert from "node:assert/strict";

import { JSDOM } from "jsdom";

// ---- 先把 DOM 装好，再 import react-dom（它在加载时就要看 document）----
const dom = new JSDOM("<!doctype html><html><body><div id=root></div></body></html>", {
  url: "http://localhost:3000/",
  pretendToBeVisual: true,
});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
// Node 22 的 globalThis.navigator 只有 getter，直接赋值会炸
Object.defineProperty(globalThis, "navigator", {
  value: dom.window.navigator, configurable: true, writable: true,
});
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.Element = dom.window.Element;
globalThis.Node = dom.window.Node;
globalThis.requestAnimationFrame = dom.window.requestAnimationFrame.bind(dom.window);
globalThis.cancelAnimationFrame = dom.window.cancelAnimationFrame.bind(dom.window);
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const React = (await import("react")).default;
const { createRoot } = await import("react-dom/client");
const { act } = await import("react");
const { useNotice } = await import("../lib/notice.ts");

const { createElement: h, useCallback, useEffect, useState, useRef } = React;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let failures = 0;
async function test(name, fn) {
  try {
    await fn();
    console.log("  ✓ " + name);
  } catch (e) {
    failures += 1;
    console.log("  ✗ " + name);
    console.log("      " + (e && e.message ? e.message.split("\n")[0] : e));
  }
}

console.log("useNotice · 身份稳定性");

await test("同一个组件多次渲染，clear/ok/fail 的身份不变", async () => {
  const seen = [];
  function Probe() {
    const { clear, ok, fail } = useNotice();
    const [n, setN] = useState(0);
    seen.push({ clear, ok, fail });
    useEffect(() => {
      if (n < 3) setN(n + 1);        // 逼它多渲染几次
    }, [n]);
    return null;
  }
  const root = createRoot(document.getElementById("root"));
  await act(async () => { root.render(h(Probe)); });
  await act(async () => { await sleep(60); });
  await act(async () => { root.unmount(); });

  assert.ok(seen.length >= 4, `渲染次数不够（${seen.length}），测不出问题`);
  for (const key of ["clear", "ok", "fail"]) {
    const first = seen[0][key];
    for (let i = 1; i < seen.length; i++) {
      assert.equal(
        seen[i][key], first,
        `第 ${i + 1} 次渲染时 ${key} 换了新身份 —— ` +
        `把它写进 useEffect 依赖就会无限循环`
      );
    }
  }
});

await test("★ 把 clear/fail 放进 effect 依赖，effect 不会无限重跑", async () => {
  // 这就是 WorkflowPanel 当时的写法：
  //   load  = useCallback(..., [clear, fail])
  //   poll  = useCallback(..., [load])
  //   effect(..., [poll])  → 里面 setState
  let effectRuns = 0;
  function Panel() {
    const { clear, fail } = useNotice();
    const [, setDetail] = useState(null);
    const misses = useRef(0);

    const load = useCallback(() => {
      misses.current += 1;
      clear();
    }, [clear, fail]);

    const poll = useCallback(() => { load(); }, [load]);

    useEffect(() => {
      effectRuns += 1;
      // ★ 熔断：坏掉的版本会无限循环，不设上限的话 Node 会直接
      //   JavaScript heap out of memory —— 那样虽然也"失败"了，
      //   但看不到人话，还得等它把内存耗光。到 50 次就停手，
      //   让下面的断言给出能读的报错。
      if (effectRuns > 50) return;
      setDetail(null);               // ← 当年就是这一行把循环点着的
      poll();
    }, [poll]);

    return null;
  }
  const root = createRoot(document.getElementById("root"));
  await act(async () => { root.render(h(Panel)); });
  await act(async () => { await sleep(300); });
  await act(async () => { root.unmount(); });

  assert.ok(
    effectRuns <= 3,
    `effect 跑了 ${effectRuns} 次 —— 无限重渲染回来了。` +
    `页面会闪，并且每秒往后端打几百个请求`
  );
});

await test("notice 内容变化时，函数身份依然不变", async () => {
  const seen = [];
  function Probe() {
    const { notice, clear, fail } = useNotice();
    const [n, setN] = useState(0);
    seen.push({ clear, fail, text: notice.text });
    useEffect(() => {
      if (n === 0) { fail(new Error("出错了")); setN(1); }
      else if (n === 1) { clear(); setN(2); }
    }, [n, clear, fail]);
    return null;
  }
  const root = createRoot(document.getElementById("root"));
  await act(async () => { root.render(h(Probe)); });
  await act(async () => { await sleep(120); });
  await act(async () => { root.unmount(); });

  assert.ok(seen.some((s) => s.text === "出错了"), "fail() 没生效，这条测试没意义");
  assert.ok(seen.some((s) => s.text === ""), "clear() 没生效");
  const first = seen[0];
  for (const s of seen) {
    assert.equal(s.clear, first.clear, "notice 一变，clear 就换了身份");
    assert.equal(s.fail, first.fail, "notice 一变，fail 就换了身份");
  }
});

console.log(failures === 0 ? "\n全部通过" : `\n${failures} 条失败`);
process.exit(failures === 0 ? 0 : 1);
