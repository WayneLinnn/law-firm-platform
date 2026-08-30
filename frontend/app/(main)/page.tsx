"use client";

/**
 * 工作台。
 *
 * ★ **首屏只放"要我动手的"**，不放好看的统计。
 *   律师打开系统第一眼要看的是「今天有什么会出事」，
 *   不是「本月案件数趋势」。
 *
 * ★ **按角色装配，不是一个驾驶舱配权限开关。**
 *   四种人关心的东西几乎不重叠 —— 做成"大而全"，
 *   每个人都要滚过 80% 不相关的东西，然后就没人看了。
 *
 * ★ 每个数字都能点开，而且**点开之后看到的正好是那个数字本身**。
 *
 * ★ 口径默认**本月** —— 律所按月结算，"最近 30 天"跨两个结算月，
 *   看到的数对不上任何一张月报表。
 */
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import {
  fetchOverview, fetchCases, yuan,
  type Overview, type CaseRow,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import {
  Badge, Card, CardContent, CardHeader, Notice, Section, useNotice,
} from "@/components/ui";
import { Metric, pick } from "@/components/Metric";

export default function Workbench() {
  const { me, can } = useAuth();
  const { notice, clear, fail } = useNotice();

  const [mine, setMine] = useState<Overview | null>(null);
  const [firm, setFirm] = useState<Overview | null>(null);
  const [recent, setRecent] = useState<CaseRow[]>([]);

  const load = useCallback(async () => {
    if (!can("analytics.view")) return;
    try {
      setMine(await fetchOverview({ scope: "mine" }));
    } catch (e) { fail(e); }
    if (can("analytics.view_firm")) {
      fetchOverview({ scope: "firm" }).then(setFirm).catch(() => {});
    }
    if (can("case.view")) {
      fetchCases({ f: "open", mine: true })
        .then((r) => setRecent(r.slice(0, 6)))
        .catch(() => {});
    }
  }, [can, fail]);

  useEffect(() => { void load(); }, [load]);

  if (!me) return null;

  const v = mine?.values ?? {};
  const cat = mine?.catalog ?? [];
  const missing = mine?.missing ?? [];
  const urgent = Number(v["task.expired"]?.value ?? 0)
               + Number(v["task.unknown"]?.value ?? 0);

  return (
    <>
      <Notice {...notice} onClose={clear} />

      {/* ---------------- 每个人都有：今天要处理什么 ---------------- */}
      <Section
        title="今天要处理"
        desc="过期和算不出的时限排最前 —— 它们是这一屏最贵的东西"
      >
        {!can("analytics.view") ? (
          <p className="muted small">你没有看板权限（analytics.view）。</p>
        ) : (
          <>
            <div className="metrics">
              {pick(cat, ["task.expired", "task.unknown", "task.urgent",
                          "task.overdue", "approval.to_decide",
                          "approval.to_pay"]).map((d) => (
                <Metric key={d.key} def={d} value={v[d.key]}
                        missing={missing.includes(d.key.split(".")[0])}
                        tone={
                          Number(v[d.key]?.value ?? 0) === 0 ? undefined
                          : d.key === "task.expired" || d.key === "task.unknown"
                            ? "bad" : "warn"} />
              ))}
            </div>
            {urgent === 0 && (
              <p className="field-hint" style={{ marginTop: 10 }}>
                没有过期或算不出的时限。
                注意这只覆盖<b>已经录进系统</b>的案件和<b>已配置</b>的规则。
              </p>
            )}
          </>
        )}
      </Section>

      <div className="grid aside">
        <div className="stack">
          {/* ---------------- 我经办的案件 ---------------- */}
          {can("case.view") && (
            <Card>
              <CardHeader
                title="我经办的案件"
                desc="只列在办的"
                action={
                  <Link href="/cases?f=open&scope=mine"
                        className="btn ghost sm">全部 →</Link>
                }
              />
              <CardContent flush>
                {recent.length === 0 ? (
                  <div className="empty">你名下没有在办案件。</div>
                ) : (
                  <table>
                    <tbody>
                      {recent.map((c) => (
                        <tr key={c.id}>
                          <td style={{ width: 106 }} className="muted small">
                            {c.code}
                          </td>
                          <td>
                            <Link href={`/cases?id=${c.id}`}>{c.title}</Link>
                          </td>
                          <td style={{ width: 70 }}>
                            <Badge tone={c.status === "active"
                                          ? "brand" : "outline"}>
                              {c.status === "active" ? "办理中" : c.status}
                            </Badge>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </CardContent>
            </Card>
          )}
        </div>

        <div className="stack">
          {/* ---------------- 我的创收 ---------------- */}
          {can("billing.view") && (
            <>
              <Section
                title="我的创收 · 本月"
                desc={`${mine?.since ?? ""} 起 · 按主办律师归集`}
              >
                <div className="metrics">
                  {pick(cat, ["billing.received", "billing.billed"]).map((d) => (
                    <Metric key={d.key} def={d} value={v[d.key]}
                            missing={missing.includes("billing")}
                            tone={d.key === "billing.received"
                                  ? "ok" : undefined} />
                  ))}
                </div>
                <p className="field-hint" style={{ marginTop: 10 }}>
                  <b>回款</b>才是创收 —— 开票只是开了张单，钱到账才算数。
                  案源分成还没做。
                </p>
              </Section>

              {/* ★ 流量和存量**分两块**。混在一排会出现
                  「本期开票 12 万、未收 23 万」这种看着矛盾的组合，
                  人一定看错，然后不信任整个看板。 */}
              <Section title="截至今天还欠多少" desc="不分期间，是存量">
                <div className="metrics">
                  {pick(cat, ["billing.outstanding", "billing.overdue"])
                    .map((d) => (
                    <Metric key={d.key} def={d} value={v[d.key]}
                            missing={missing.includes("billing")}
                            tone={d.key === "billing.overdue"
                                  && Number(v[d.key]?.value ?? 0) > 0
                                  ? "bad" : "warn"} />
                  ))}
                </div>
              </Section>
            </>
          )}

          {/* ---------------- 管理者才有的 ---------------- */}
          {firm && can("analytics.view_firm") && (
            <Section title="全所 · 本月" flush>
              <table className="kv">
                <tbody>
                  <tr>
                    <th>回款</th>
                    <td className="num ok">
                      ¥{yuan(String(firm.values["billing.received"]?.value ?? 0))}
                    </td>
                  </tr>
                  <tr>
                    <th>未收</th>
                    <td className="num warn">
                      ¥{yuan(String(firm.values["billing.outstanding"]?.value ?? 0))}
                    </td>
                  </tr>
                  <tr>
                    <th>在办</th>
                    <td className="num">
                      {firm.values["case.open"]?.text ?? "—"}
                    </td>
                  </tr>
                </tbody>
              </table>
              <div style={{ padding: "10px 14px" }}>
                <Link href="/firm" className="btn ghost sm">看全所看板 →</Link>
              </div>
            </Section>
          )}
        </div>
      </div>
    </>
  );
}
