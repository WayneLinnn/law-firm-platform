"use client";

/**
 * 统一待办中心。
 *
 * ★ **为什么要有这一页。**
 *
 *   待办原来散在三个地方：待办页有时限、单据页有「待我处理」、
 *   工作台还有一排格子。律师早上要点三个地方才知道今天有什么会出事，
 *   **于是就不点了** —— 一件事需要三个入口，等于没有入口。
 *
 *   这是国产 OA（泛微/致远）做得最对的一件事：**统一待办中心**，
 *   所有流程的待办推到一处。
 *
 * ★ 排序：**已过期 → 算不出 → 临近 → 正常**。
 *   「算不出」排在「临近」前面是刻意的 —— 临近的至少系统在盯着，
 *   算不出的**没人在盯**（它缺起算日，谁也不知道还剩几天）。
 *
 * ★ 每一条都必须能点开，而且要说清**为什么轮到我** ——
 *   单据是按权限算的不是按指派，不说的话团队里每个人都以为是别人的事。
 */
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import {
  fetchInboxCenter, INBOX_KIND_LABEL, URGENCY_LABEL,
  type Inbox, type InboxItem,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import {
  Alert, Badge, Card, CardContent, CardHeader, Metric, Notice, Section,
  useNotice, type Tone,
} from "@/components/ui";

const URGENCY_TONE: Record<string, Tone> = {
  expired: "bad", unknown: "bad", urgent: "warn", normal: "outline",
};

const KIND_ICON: Record<string, string> = {
  deadline: "◷", task: "☑", ticket: "▤",
};

export default function InboxPage() {
  const { can } = useAuth();
  const { notice, clear, fail } = useNotice();
  const [data, setData] = useState<Inbox | null>(null);
  const [kind, setKind] = useState("");
  const [urgency, setUrgency] = useState("");

  const load = useCallback(async () => {
    try { setData(await fetchInboxCenter()); } catch (e) { fail(e); }
  }, [fail]);

  useEffect(() => { void load(); }, [load]);

  if (!can("analytics.view")) {
    return <Section title="待办中心">
      <p className="muted">你没有 analytics.view 权限。</p></Section>;
  }
  if (!data) {
    return <Section title="待办中心"><p className="muted">加载中…</p></Section>;
  }

  const items = data.items.filter(
    (i) => (!kind || i.kind === kind) && (!urgency || i.urgency === urgency));

  const n = (u: string) => data.by_urgency[u] ?? 0;

  return (
    <>
      <Notice {...notice} onClose={clear} />

      {/* ★ 某个模块没报上来必须**明说**。
          一个漏掉了单据的待办列表，比没有列表更危险 ——
          它让人以为自己已经看全了。 */}
      {data.missing.length > 0 && (
        <Alert tone="bad" title={`这些模块没报上来：${data.missing.join("、")}`}>
          下面这张表<b>不完整</b> —— 别把它当成"今天就这些事"。
        </Alert>
      )}

      <Section title="待办中心"
               desc="所有等你动手的事，一处看全。点格子只看那一档">
        <div className="metrics">
          <Cell n={n("expired")} label="已过期" tone="bad"
                on={urgency === "expired"}
                onClick={() => setUrgency(urgency === "expired" ? "" : "expired")}
                hint="到期日已经过了。除斥期间过期即失权，没有补救。" />
          <Cell n={n("unknown")} label="算不出" tone="bad"
                on={urgency === "unknown"}
                onClick={() => setUrgency(urgency === "unknown" ? "" : "unknown")}
                hint="缺起算日 —— 这些没人在盯，必须你自己去确认。" />
          <Cell n={n("urgent")} label="临近" tone="warn"
                on={urgency === "urgent"}
                onClick={() => setUrgency(urgency === "urgent" ? "" : "urgent")}
                hint="进入预警窗口，或者单据卡了三天以上。" />
          <Cell n={n("normal")} label="正常" tone={undefined}
                on={urgency === "normal"}
                onClick={() => setUrgency(urgency === "normal" ? "" : "normal")}
                hint="在跑着，暂时不用动。" />
        </div>

        {/* 按来源筛 —— 律师只想看时限、财务只想看单据 */}
        <div className="row tight" style={{ marginTop: 12 }}>
          <span className="muted small">按来源：</span>
          <button className={"btn sm " + (kind === "" ? "" : "ghost")}
                  onClick={() => setKind("")}>
            全部 {data.total}
          </button>
          {Object.entries(data.by_kind).map(([k, c]) => (
            <button key={k} className={"btn sm " + (kind === k ? "" : "ghost")}
                    onClick={() => setKind(kind === k ? "" : k)}>
              {KIND_ICON[k]} {INBOX_KIND_LABEL[k] ?? k} {c}
            </button>
          ))}
        </div>
      </Section>

      <Card>
        <CardHeader
          title={`清单 · ${items.length} 条`}
          desc="已过期 → 算不出 → 临近 → 正常。★「算不出」排在「临近」前面：临近的至少系统在盯，算不出的没人在盯"
          action={
            (kind || urgency) && (
              <button className="btn ghost sm"
                      onClick={() => { setKind(""); setUrgency(""); }}>
                看全部 ✕
              </button>
            )
          }
        />
        <CardContent flush>
          {items.length === 0 ? (
            <div className="empty">
              {data.total === 0
                ? "没有等你动手的事。"
                : "这一档是空的。"}
              <div className="field-hint" style={{ marginTop: 6 }}>
                注意这只覆盖<b>已经录进系统</b>的案件和<b>已配置</b>的规则。
              </div>
            </div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th style={{ width: 68 }}>来源</th>
                  <th>要做什么</th>
                  <th style={{ width: 108 }}>到期</th>
                  <th style={{ width: 78 }}>状态</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it) => <Row key={it.key} it={it} />)}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </>
  );
}

function Row({ it }: { it: InboxItem }) {
  return (
    <tr>
      <td className="muted small">
        {KIND_ICON[it.kind]} {INBOX_KIND_LABEL[it.kind] ?? it.kind}
      </td>
      <td>
        {/* ★ 每一条都能点开 —— 看到了却不知道去哪做，等于没提醒 */}
        <Link href={it.link}><b>{it.title}</b></Link>
        {it.detail && <div className="muted small">{it.detail}</div>}
        {/* ★ 说清为什么轮到我 —— 单据按权限算，不说的话
            团队里每个人都以为是别人的事 */}
        <div className="muted small">{it.why_me}</div>
      </td>
      <td>
        {/* ★ 算不出的时候这一格不能空着 —— 空白会被读成「没事」 */}
        {it.due_on
          ? <span className={it.urgency === "expired" ? "bad" : undefined}>
              {it.due_on}
            </span>
          : it.urgency === "unknown"
            ? <span className="bad small">算不出</span>
            : <span className="muted small">—</span>}
      </td>
      <td>
        <Badge tone={URGENCY_TONE[it.urgency]}>
          {URGENCY_LABEL[it.urgency] ?? it.urgency}
        </Badge>
      </td>
    </tr>
  );
}

/** 可点的统计格 —— 点了只看那一档，再点取消 */
function Cell({ n, label, tone, hint, on, onClick }: {
  n: number; label: string; tone: "bad" | "warn" | undefined;
  hint: string; on: boolean; onClick: () => void;
}) {
  return (
    <button className={"metric metricbtn" + (on ? " on" : "")}
            title={hint} onClick={onClick}>
      <div className={"metric-n " + (n > 0 && tone ? tone : "muted")}>{n}</div>
      <div className="metric-l">{label}</div>
    </button>
  );
}
