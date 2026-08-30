"use client";

/**
 * 应收账款（Step 17）。
 *
 * 这一页的主角是**账龄**，不是总额。
 *
 * ★ 一笔挂了 200 天的应收，和一笔上周开的同额账单，是完全不同的两回事：
 *   前者多半要不回来了，后者只是还没到付款日。
 *   一个只显示"应收合计 380 万"的看板，等于什么也没说 ——
 *   它让一堆烂账和一堆正常在途的钱长得一模一样。
 *
 * ★ 排序按账龄倒序：最老的排最前面。律所催款的顺序就该是这个 ——
 *   越老越难要，越该先打电话。
 */
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { fetchReceivables, fetchRevenueFlow, yuan,
         type Receivables, type RevenueFlow } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Badge, Metric, Notice, Section, useNotice } from "@/components/ui";
import { DrillBanner, useDrill } from "@/components/Drill";

/** 账龄越长越刺眼。
 *
 *  ★ 档位要和上面账龄分布的条形**用同一套** —— 原来这里是
 *    90/180 而条形是按第 4 档，于是一笔 76 天的账在表里是绿标、
 *    在条形里是黄条。**同一份数据两种颜色，人只会更糊涂。**
 *
 *  ★ 而且 60 天以上就该变黄：三个月没收回来在律所已经是个问题，
 *    画成绿的等于替它开脱。
 */
function ageTone(days: number): "ok" | "warn" | "bad" {
  if (days > 180) return "bad";
  if (days > 60) return "warn";
  return "ok";
}

const FLOW_LABEL: Record<string, string> = {
  billed: "本期开票", received: "本期回款",
  outstanding: "未收", overdue: "已逾期",
};

export default function ReceivablesPage() {
  const { can } = useAuth();
  const { notice, clear, fail } = useNotice();
  const drill = useDrill();
  const [data, setData] = useState<Receivables | null>(null);
  const [flow, setFlow] = useState<RevenueFlow | null>(null);

  // ★ 开票/回款是**流量**，未收/逾期是**存量** —— 两套数据，两个接口。
  //   第一版四个指标都指到这一页，于是"本期回款 ¥206,000"点进来
  //   看到的是一张讲未收的表，两个数字八竿子打不着。
  const isFlow = drill.f === "billed" || drill.f === "received";

  const load = useCallback(async () => {
    try { setData(await fetchReceivables()); } catch (e) { fail(e); }
    if (isFlow) {
      fetchRevenueFlow({
        scope: drill.scope || "mine",
        org_id: drill.org ?? undefined,
        since: drill.since || undefined,
        until: drill.until || undefined,
      }).then(setFlow).catch(fail);
    } else {
      setFlow(null);
    }
  }, [fail, isFlow, drill.scope, drill.org, drill.since, drill.until]);

  useEffect(() => { void load(); }, [load]);

  if (!can("billing.view")) {
    return <Section title="应收账款">
      <p className="muted">你没有 billing.view 权限。</p></Section>;
  }
  if (!data) return <Section title="应收账款"><p className="muted">加载中…</p></Section>;

  const max = Math.max(...data.aging.map((b) => Number(b.amount)), 1);

  if (isFlow) {
    return (
      <>
        <Notice {...notice} onClose={clear} />
        <DrillBanner drill={drill} label={FLOW_LABEL[drill.f] ?? "本期流水"} />
        <FlowView flow={flow} which={drill.f} />
      </>
    );
  }

  // ★ 逾期是存量的一个子集，同一张表筛一下就行 —— 不用另拉数据
  const items = drill.f === "overdue"
    ? data.items.filter((i) => i.overdue) : data.items;

  return (
    <>
      <Notice {...notice} onClose={clear} />

      <DrillBanner drill={drill} label={FLOW_LABEL[drill.f] ?? "筛选"}
                   n={items.length} total={data.items.length} />

      <Section title="应收账款">
        <div className="metrics">
          <Metric n={`¥${yuan(data.total_outstanding)}`} label="未收合计"
                  tone="warn" />
          <Metric n={`¥${yuan(data.overdue_amount)}`} label="已逾期"
                  tone={Number(data.overdue_amount) > 0 ? "bad" : undefined}
                  title="已经过了约定付款日还没收到的" />
          <Metric n={data.invoice_count} label="张未结清" />
        </div>

        {/* 账龄分布。★ 这才是这一页的重点 */}
        <h4 style={{ marginTop: 18 }}>账龄分布</h4>
        {data.invoice_count === 0 ? (
          <p className="muted small">没有未结清的账单。</p>
        ) : (
          <table >
            <tbody>
              {data.aging.map((b, i) => (
                <tr key={b.label}>
                  <td style={{ width: 100 }}>{b.label}</td>
                  <td style={{ width: 60 }} className="muted">{b.count} 张</td>
                  <td>
                    {/* 用条形长度说话 —— 一列数字看不出分布 */}
                    <div className="bar">
                      {/* ★ 只有 30 天内算"正常" —— 三个月没收回来
                          在律所已经是个问题了，不该画成绿的 */}
                      <span className={i >= 4 ? "bad" : i >= 2 ? "warn" : "ok"}
                            style={{ width: (Number(b.amount) / max * 100) + "%" }} />
                    </div>
                  </td>
                  <td style={{ width: 130, textAlign: "right" }}>
                    ¥{yuan(b.amount)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      {items.length > 0 && (
        <Section title={drill.f === "overdue"
                          ? "逾期逐笔（最老的排最前）"
                          : "逐笔（最老的排最前）"}>
          <p className="muted small">
            催款就该按这个顺序打电话 —— 越老越难要。
          </p>
          <table >
            <thead>
              <tr>
                <th>单号</th><th>案件</th><th>开出</th><th>到期</th>
                <th>未收</th><th>账龄</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.id}>
                  <td>{it.number}</td>
                  <td>
                    {it.case_id
                      ? <Link href={`/cases?id=${it.case_id}`}>#{it.case_id}</Link>
                      : <span className="muted">—</span>}
                  </td>
                  <td className="muted small">{it.issued_on || "—"}</td>
                  <td className={it.overdue ? "bad" : undefined}>
                    {it.due_on || <span className="muted">未约定</span>}
                  </td>
                  <td>¥{yuan(it.outstanding)}</td>
                  <td>
                    <Badge tone={ageTone(it.age_days)}>{it.age_days} 天</Badge>
                    {it.overdue && <Badge tone="bad">已逾期</Badge>}
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

/**
 * 本期流水（流量）—— 「本期开票 / 本期回款」点开看到的东西。
 *
 * ★ 底部合计必须和看板格子里的数**完全一样**。对不上就是口径漂了，
 *   而口径一漂，用户对整个看板的信任就没了。
 */
function FlowView({ flow, which }: { flow: RevenueFlow | null; which: string }) {
  if (!flow) return <Section title="本期流水"><p className="muted">加载中…</p></Section>;

  const range = flow.since ? `${flow.since} 至 ${flow.until}` : "本期";

  if (which === "received") {
    return (
      <Section title={`本期回款 · ${range}`}>
        <p className="muted small">
          按<b>收款日</b>算，不按账单日 —— 三月开的单五月收到，是五月的创收。
        </p>
        {flow.received.length === 0 ? (
          <p className="muted small">本期没有回款。</p>
        ) : (
          <table >
            <thead>
              <tr><th>收款日</th><th>账单</th><th>案件</th><th>方式</th><th>金额</th></tr>
            </thead>
            <tbody>
              {flow.received.map((p) => (
                <tr key={p.id}>
                  <td>{p.paid_on}</td>
                  <td className="muted small">{p.number}</td>
                  <td>
                    {p.case_id
                      ? <Link href={`/cases?id=${p.case_id}`}>#{p.case_id}</Link>
                      : <span className="muted">—</span>}
                  </td>
                  <td className="muted small">{p.method || "—"}</td>
                  <td className="ok"><b>¥{yuan(p.amount)}</b></td>
                </tr>
              ))}
              <tr>
                <td colSpan={4} style={{ textAlign: "right" }}><b>合计</b></td>
                <td className="ok"><b>¥{yuan(flow.received_total)}</b></td>
              </tr>
            </tbody>
          </table>
        )}
      </Section>
    );
  }

  return (
    <Section title={`本期开票 · ${range}`}>
      <p className="muted small">
        开票<b>不等于</b>创收 —— 钱到账才算。右边"已收"那列才是真的进来了的。
      </p>
      {flow.billed.length === 0 ? (
        <p className="muted small">本期没有开出账单。</p>
      ) : (
        <table >
          <thead>
            <tr><th>开出</th><th>单号</th><th>案件</th><th>金额</th><th>已收</th></tr>
          </thead>
          <tbody>
            {flow.billed.map((i) => (
              <tr key={i.id}>
                <td>{i.issued_on}</td>
                <td className="muted small">{i.number}</td>
                <td>
                  {i.case_id
                    ? <Link href={`/cases?id=${i.case_id}`}>#{i.case_id}</Link>
                    : <span className="muted">—</span>}
                </td>
                <td><b>¥{yuan(i.amount)}</b></td>
                <td className={Number(i.paid) === 0 ? "bad" : "ok"}>
                  ¥{yuan(i.paid)}
                </td>
              </tr>
            ))}
            <tr>
              <td colSpan={3} style={{ textAlign: "right" }}><b>合计</b></td>
              <td><b>¥{yuan(flow.billed_total)}</b></td>
              <td />
            </tr>
          </tbody>
        </table>
      )}
    </Section>
  );
}
