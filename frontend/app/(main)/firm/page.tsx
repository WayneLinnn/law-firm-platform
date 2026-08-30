"use client";

/**
 * 全所看板 / 团队看板（同一页，靠 scope 切）。
 *
 * ★ **列表 > 图表。** 管理者真正要的是"**哪几个**案子有问题"，
 *   不是分布饼图。饼图给感觉，列表给行动。
 *   这一页唯一的"图"是案由分布的条形，因为它表达的就是分布本身。
 *
 * ★ **不做工时和计费率。** 用户明确：不计工时，记创收。
 *   计费率是英美计时制律所的指标，照搬过来只会得到一堆没人看的数字。
 *
 * ★ 创收按**主办律师**归集。案源分成还没做 —— 界面上要写明，
 *   否则案源律师会以为系统漏算了他。
 */
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import {
  fetchOverview, fetchUsers, yuan,
  type Overview, type User,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Notice, Section, Tag, useNotice } from "@/components/ui";
import { Metric, pick } from "@/components/Metric";

export default function FirmBoard() {
  const path = usePathname();
  const scope = path === "/team" ? "team" : "firm";
  return <Board scope={scope} />;
}

export function Board({ scope }: { scope: string }) {
  const { can, me } = useAuth();
  const { notice, clear, fail } = useNotice();
  const [data, setData] = useState<Overview | null>(null);
  const [users, setUsers] = useState<Record<number, User>>({});

  const load = useCallback(async () => {
    try {
      setData(await fetchOverview({ scope }));
    } catch (e) { fail(e); }
    fetchUsers()
      .then((us) => setUsers(Object.fromEntries(us.map((u) => [u.id, u]))))
      .catch(() => {});
  }, [scope, fail]);

  useEffect(() => { void load(); }, [load]);

  if (!can("analytics.view")) {
    return <Section title="看板">
      <p className="muted">你没有 analytics.view 权限。</p></Section>;
  }
  if (scope === "firm" && !can("analytics.view_firm")) {
    return <Section title="全所看板">
      <p className="muted">
        全所看板需要 analytics.view_firm 权限 —— 那是管委会和财务的视图。
      </p></Section>;
  }
  if (!data) return <Section title="看板"><p className="muted">加载中…</p></Section>;

  const v = data.values;
  const cat = data.catalog;
  const missing = data.missing;
  const causes = v["case.by_cause"]?.detail ?? [];
  const maxCause = Math.max(...causes.map((c) => c.value), 1);
  const byLead = data.by_lead;

  return (
    <>
      <Notice {...notice} onClose={clear} />

      <Section title={`${scope === "firm" ? "全所" : "团队"} · ${data.since} 起`}>
        {missing.length > 0 && (
          <p className="alert warn">
            <b>这些模块没报上来：{missing.join("、")}</b><br />
            相关的格子显示「查不到」而<b>不是 0</b> —— 别把它当成真的没有。
          </p>
        )}

        <h4>本期创收<span className="muted small">　（流量）</span></h4>
        <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
          {pick(cat, ["billing.received", "billing.billed"]).map((d) => (
            <Metric key={d.key} def={d} value={v[d.key]}
                    missing={missing.includes("billing")}
                    tone={d.key === "billing.overdue"
                          && Number(v[d.key]?.value ?? 0) > 0 ? "bad"
                          : d.key === "billing.received" ? "ok" : undefined} />
          ))}
        </div>
        <p className="muted small" style={{ marginTop: 6 }}>
          <b>回款</b>才是创收；开票只是开了张单。
          按<b>主办律师</b>归集，<b>案源分成还没做</b>。
        </p>

        {/* ★ 存量和流量分开 —— 见首页同处的说明 */}
        <h4 style={{ marginTop: 14 }}>
          截至今天还欠多少
          <span className="muted small">　（不分期间，是存量）</span>
        </h4>
        <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
          {pick(cat, ["billing.outstanding", "billing.overdue"]).map((d) => (
            <Metric key={d.key} def={d} value={v[d.key]}
                    missing={missing.includes("billing")}
                    tone={d.key === "billing.overdue"
                          && Number(v[d.key]?.value ?? 0) > 0 ? "bad" : "warn"} />
          ))}
        </div>

        <h4 style={{ marginTop: 16 }}>案件</h4>
        <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
          {pick(cat, ["case.open", "case.new", "case.closed"]).map((d) => (
            <Metric key={d.key} def={d} value={v[d.key]}
                    missing={missing.includes("case")} />
          ))}
        </div>
      </Section>

      {/* ---------------- 按人 ---------------- */}
      {byLead && (
        <Section title="按主办律师">
          {byLead.suppressed ? (
            /* ★ 小样本保护：3 个人以内，知道另外两个就能反推第三个。
                 聚合数字看着无害，实际能反推到个人 */
            <p className="alert">
              {byLead.reason}
              {byLead.total && (
                <><br />合计：开票 ¥{yuan(byLead.total.billed)}
                　回款 ¥{yuan(byLead.total.received)}</>
              )}
            </p>
          ) : byLead.rows.length === 0 ? (
            <p className="muted small">本期没有创收记录。</p>
          ) : (
            <table >
              <thead>
                <tr>
                  <th>律师</th><th>回款</th><th>开票</th><th>未收</th><th>案件</th>
                </tr>
              </thead>
              <tbody>
                {byLead.rows.map((r) => (
                  <tr key={r.user_id}>
                    <td>{users[r.user_id]?.full_name ?? `#${r.user_id}`}</td>
                    <td><b>¥{yuan(r.received)}</b></td>
                    <td className="muted">¥{yuan(r.billed)}</td>
                    <td className={Number(r.outstanding) > 0 ? "warn" : undefined}>
                      ¥{yuan(r.outstanding)}
                    </td>
                    <td className="muted">{r.cases}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      )}

      {/* ---------------- 收不回来的 ---------------- */}
      {data.at_risk && data.at_risk.length > 0 && (
        <Section title="收不回来的（按未收排）">
          <p className="muted small">
            ★ 这一栏是这一页最该看的东西 —— 律所真正的风险不是"没开单"，
            是<b>开了单收不回来</b>。
          </p>
          <table >
            <thead>
              <tr><th>案件</th><th>开票</th><th>已收</th><th>未收</th></tr>
            </thead>
            <tbody>
              {data.at_risk.map((c) => (
                <tr key={c.case_id}>
                  <td><Link href={`/cases?id=${c.case_id}`}>#{c.case_id}</Link></td>
                  <td className="muted">¥{yuan(c.billed)}</td>
                  <td>¥{yuan(c.received)}</td>
                  <td className="bad"><b>¥{yuan(c.outstanding)}</b></td>
                </tr>
              ))}
            </tbody>
          </table>
          <Link href="/receivables" className="btn ghost sm">看账龄 →</Link>
        </Section>
      )}

      {/* ---------------- 案由分布 ---------------- */}
      {causes.length > 0 && (
        <Section title="在办案件的案由分布">
          <table >
            <tbody>
              {causes.map((c) => (
                <tr key={c.label}>
                  <td style={{ width: 180 }}>{c.label}</td>
                  <td style={{ width: 50 }} className="muted">{c.value} 件</td>
                  <td>
                    <div className="bar">
                      <span className="ok"
                            style={{ width: (c.value / maxCause * 100) + "%" }} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}
    </>
  );
}
