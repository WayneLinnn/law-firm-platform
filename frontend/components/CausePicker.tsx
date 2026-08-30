"use client";

/**
 * 案由选择器。
 *
 * ★ **为什么不能用 `<select>`。**
 *
 *   案由字典现在是最高法《民事案件案由规定》全表 —— **408 条，四级**。
 *   一个 408 项的扁平下拉，人只能一直滚，滚到第三屏就放弃了，
 *   然后所有案子都挂在最上面那几个上。
 *
 * ★ **只有叶子能选。** "第四部分 合同、准合同纠纷"是分类不是案由，
 *   选它等于没填 —— 法院立案不认这一级。
 *
 * ★ 搜索**同时匹配名称和路径**：搜"施工"能出来，
 *   搜"建设工程"也能把下面几个都带出来。
 *
 * ★ 结果里显示**完整路径**（合同纠纷 › 建设工程合同纠纷 › 建设工程施工合同纠纷）——
 *   光看"承揽合同纠纷"分不清是哪一部分下的，而案由规定里同名的不止一处
 *   （"技术合同纠纷"在第四部分和第五部分各有一个）。
 */
import { useEffect, useMemo, useRef, useState } from "react";

import type { Cause } from "@/lib/api";

export function CausePicker({ causes, value, onChange, disabled }: {
  causes: Cause[];
  value: number | "";
  onChange: (id: number | "") => void;
  disabled?: boolean;
}) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  /** id → 完整路径（从顶层到自己） */
  const paths = useMemo(() => {
    const byId = new Map(causes.map((c) => [c.id, c]));
    const out = new Map<number, string[]>();
    for (const c of causes) {
      const chain: string[] = [];
      let cur: Cause | undefined = c;
      let guard = 0;
      while (cur && guard++ < 8) {
        chain.unshift(cur.name);
        cur = cur.parent_id ? byId.get(cur.parent_id) : undefined;
      }
      out.set(c.id, chain);
    }
    return out;
  }, [causes]);

  /** ★ 只有叶子能选 —— 有孩子的是分类，法院立案不认那一级 */
  const leaves = useMemo(() => {
    const hasChild = new Set(causes.map((c) => c.parent_id).filter(Boolean));
    return causes.filter((c) => !hasChild.has(c.id));
  }, [causes]);

  const hits = useMemo(() => {
    const k = q.trim();
    if (!k) return leaves.slice(0, 40);
    return leaves
      .filter((c) => (paths.get(c.id) ?? []).join(" ").includes(k))
      .slice(0, 60);
  }, [q, leaves, paths]);

  const picked = value === "" ? null : causes.find((c) => c.id === value);

  // 点外面收起来
  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  return (
    <div className="causepick" ref={boxRef}>
      {picked ? (
        <div className="causepick-on">
          <span className="grow">
            <b>{picked.name}</b>
            <div className="muted small">
              {(paths.get(picked.id) ?? []).slice(0, -1).join(" › ")}
            </div>
          </span>
          <button type="button" className="btn ghost sm" disabled={disabled}
                  onClick={() => { onChange(""); setQ(""); setOpen(true); }}>
            换一个
          </button>
        </div>
      ) : (
        <>
          <input
            value={q}
            disabled={disabled}
            placeholder="搜案由，如「施工」「借贷」「劳动」"
            onChange={(e) => { setQ(e.target.value); setOpen(true); }}
            onFocus={() => setOpen(true)}
          />
          {open && (
            <div className="causepick-list">
              {hits.length === 0 ? (
                <div className="empty small">
                  没找到「{q}」。
                  <br />
                  案由用的是<b>最高法《民事案件案由规定》原文</b> ——
                  试试法院立案时写的那个说法。
                </div>
              ) : (
                hits.map((c) => {
                  const chain = paths.get(c.id) ?? [];
                  return (
                    <button key={c.id} type="button" className="causepick-item"
                            onClick={() => { onChange(c.id); setOpen(false); }}>
                      <span>{c.name}</span>
                      <span className="muted small">
                        {chain.slice(0, -1).join(" › ")}
                      </span>
                    </button>
                  );
                })
              )}
              {!q && leaves.length > hits.length && (
                <div className="causepick-more small muted">
                  共 {leaves.length} 个案由 —— 输入关键词继续找
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
