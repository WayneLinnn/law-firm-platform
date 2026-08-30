import { useCallback, useMemo, useState } from "react";

export type NoticeKind = "info" | "error" | "ok";

/**
 * 页面顶部那条提示横幅。
 *
 * ★ 三个函数**必须**用 useCallback 包住，返回值必须 useMemo。
 *
 * 原来是每次渲染现造一份新的。组件里只要把 `clear` / `fail` 写进
 * useCallback 或 useEffect 的依赖数组，依赖就每次渲染都在变 ——
 * effect 每次渲染都重跑，里面一 setState 又触发渲染，**无限循环**。
 *
 * 实测后果：页面肉眼可见地闪，同时每秒往后端打几百个请求，
 * 把 dev server 的代理冲垮，于是案件页、检索、流程一起报
 * "连不上后端"。反向验证时更直接 —— 退回旧写法跑测试，
 * Node 直接 **JavaScript heap out of memory**。
 *
 * 而这个 bug **tsc 和 next build 都查不出来** —— 类型全对，构建全过，
 * 只有真跑起来才炸。所以身份稳定不是"优化"，是正确性要求。
 * 测试见 tests/notice.test.mjs。
 */
export function useNotice() {
  const [notice, setNotice] = useState<{ text: string; kind: NoticeKind }>({
    text: "",
    kind: "info",
  });

  const clear = useCallback(() => setNotice({ text: "", kind: "info" }), []);

  const ok = useCallback((text: string) => setNotice({ text, kind: "ok" }), []);

  const fail = useCallback((e: unknown) => {
    const err = e as { status?: number; message?: string };
    const text =
      err?.status === 403
        ? `没有权限执行此操作：${err.message ?? ""}`
        : err?.message ?? "操作失败";
    setNotice({ text, kind: "error" });
  }, []);

  // notice 变了才给出新对象 —— 否则解构出来的东西又会每次都变
  return useMemo(() => ({ notice, clear, ok, fail }), [notice, clear, ok, fail]);
}
