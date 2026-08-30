"use client";

/**
 * 待办看板（Step 14/15）。
 *
 * 这一页回答的是律师每天早上真正问的那个问题：**今天有什么会出事？**
 *
 * 所以排序不是按创建时间，也不是按案件，而是按「离出事有多近」：
 *
 *   已过期 → 算不出 → 临近 → 正常
 *
 * ★ 「算不出」排在「临近」前面，这是刻意的。临近的期限至少系统在盯；
 *   算不出的那些**没人在盯** —— 它缺起算日，谁也不知道还剩几天。
 *   一个看起来干净的看板如果藏着三条算不出的除斥期间，它就是在骗人。
 */
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import {
  fetchBoard, updateTask, DEADLINE_STATUS_LABEL, TASK_STATUS_LABEL,
  type Board, type DeadlineItem,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import {
  Badge, Button, Card, CardContent, CardHeader, Notice, Section, useNotice,
} from "@/components/ui";
import { DrillBanner, useDrill } from "@/components/Drill";

/** 排序权重 —— 数字越小越靠前。理由见文件头 */
const ORDER: Record<string, number> = {
  expired: 0, unknown: 1, urgent: 2, ok: 3, no_fixed: 4,
};
type Tone = "ok" | "warn" | "bad" | "muted";
const TONE: Record<string, Tone> = {
  expired: "bad", unknown: "bad", urgent: "warn", ok: "ok",
  no_fixed: "muted",   // 说明，不是警报
};

/** 看板上的格子 → 这一页要筛成什么样 */
const DRILL_LABEL: Record<string, string> = {
  expired: "已过期时限", urgent: "临近时限",
  unknown: "算不出的时限", overdue: "逾期待办",
};

export default function TodoPage() {
  const { can } = useAuth();
  const { notice, clear, ok, fail } = useNotice();
  const drill = useDrill();
  const [board, setBoard] = useState<Board | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { setBoard(await fetchBoard()); } catch (e) { fail(e); }
  }, [fail]);

  useEffect(() => { void load(); }, [load]);

  const done = useCallback(async (id: number) => {
    setBusy(true);
    try {
      await updateTask(id, { status: "done" });
      await load();
      ok("已完成");
    } catch (e) { fail(e); } finally { setBusy(false); }
  }, [load, ok, fail]);

  if (!can("task.view")) {
    return <Section title="待办"><p className="muted">你没有 task.view 权限。</p></Section>;
  }
  if (!board) return <Section title="待办"><p className="muted">加载中…</p></Section>;

  const sorted = [...board.deadlines].sort((a, b) => {
    const d = (ORDER[a.status] ?? 9) - (ORDER[b.status] ?? 9);
    if (d !== 0) return d;
    // 同一档里按剩余天数，算不出的（null）排最后
    if (a.days_left === null) return 1;
    if (b.days_left === null) return -1;
    return a.days_left - b.days_left;
  });

  // ★ 从看板点进来的话，只留那一档 —— 点"12 条算不出"就该看到那 12 条。
  //   `overdue` 筛的是待办不是时限，所以时限表这时应该空着。
  const isTaskDrill = drill.f === "overdue";
  const deadlines = drill.f && !isTaskDrill
    ? sorted.filter((d) => d.status === drill.f)
    : sorted;

  const allTasks = board.tasks.filter(
    (t) => t.status !== "done" && t.status !== "cancelled");
  const today = isTaskDrill
    ? allTasks.filter((t) => t.due_on && t.due_on < new Date().toISOString().slice(0, 10))
    : drill.f ? [] : allTasks;

  return (
    <>
      <Notice {...notice} onClose={clear} />

      <DrillBanner drill={drill} label={DRILL_LABEL[drill.f] ?? "筛选"}
                   n={isTaskDrill ? today.length : deadlines.length}
                   total={isTaskDrill ? allTasks.length : sorted.length} />

      <Section title="需要注意"
               desc="点任一格只看那一档">
        <div className="metrics">
          <Stat n={board.expired} label="已过期" tone="bad" f="expired"
                hint="到期日已经过了。除斥期间过期即失权，没有补救。" />
          <Stat n={board.unknown} label="算不出" tone="bad" f="unknown"
                hint="缺起算日期 —— 这些期限系统没在替你盯着，必须你自己去确认。" />
          <Stat n={board.urgent} label="临近" tone="warn" f="urgent"
                hint="进入预警窗口。窗口宽度由规则里的 warn_days 决定。" />
          <Stat n={board.overdue_tasks} label="逾期待办" tone="warn" f="overdue"
                hint="到期日已过但还没标完成的任务。" />
        </div>
        {board.expired === 0 && board.unknown === 0 && board.urgent === 0 && (
          <p className="field-hint" style={{ marginTop: 10 }}>
            当前没有需要立刻处理的期限。
            注意这只覆盖<b>已经录进系统</b>的案件和<b>已配置</b>的规则。
          </p>
        )}
      </Section>

      <Section title="时限"
               desc="已过期 → 算不出 → 临近 → 正常。★「算不出」排在「临近」前面：临近的至少系统在盯，算不出的没人在盯"
               flush>
        {deadlines.length === 0 ? (
          <p className="muted small">
            {isTaskDrill ? "（正在看逾期待办，时限表已收起）"
             : drill.f ? `没有「${DRILL_LABEL[drill.f]}」。`
             : "没有时限记录。"}
          </p>
        ) : (
          <table>
            <thead>
              <tr><th>案件</th><th>期限</th><th>到期</th><th>状态</th></tr>
            </thead>
            <tbody>
              {deadlines.map((d) => <DeadlineLine key={d.id} d={d} />)}
            </tbody>
          </table>
        )}
      </Section>

      <Section title={isTaskDrill ? "逾期待办" : "我的待办"} flush>
        {today.length === 0 ? (
          <p className="muted small">
            {drill.f && !isTaskDrill ? "（正在看时限，待办已收起）" : "没有待办。"}
          </p>
        ) : (
          <table>
            <thead>
              <tr><th>事项</th><th>案件</th><th>到期</th><th>状态</th><th /></tr>
            </thead>
            <tbody>
              {today.map((t) => (
                <tr key={t.id}>
                  <td>
                    {t.priority === "high" && <Badge tone="warn">高</Badge>} {t.title}
                    {t.detail && <div className="muted small">{t.detail}</div>}
                  </td>
                  <td>
                    {t.case_id
                      ? <Link href={`/cases?id=${t.case_id}`}>#{t.case_id}</Link>
                      : <span className="muted">—</span>}
                  </td>
                  <td>{t.due_on || "—"}</td>
                  <td>{TASK_STATUS_LABEL[t.status] ?? t.status}</td>
                  <td>
                    {can("task.manage") && (
                      <Button size="sm" disabled={busy}
                              onClick={() => void done(t.id)}>标记完成</Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>
    </>
  );
}

/** ★ 这四格**能点** —— 点了只留那一档。
 *  一个看得到却点不动的数字是装饰；这里点开之后正好就是那个数。 */
function Stat({ n, label, tone, hint, f }: {
  n: number; label: string; tone: Tone; hint: string; f: string;
}) {
  const body = (
    <>
      <div className={"metric-n " + (n > 0 ? tone : "muted")}>{n}</div>
      <div className="metric-l">{label}</div>
    </>
  );
  if (n === 0) return <div className="metric" title={hint}>{body}</div>;
  return <Link href={`/todo?f=${f}`} className="metric" title={hint}>{body}</Link>;
}

function DeadlineLine({ d }: { d: DeadlineItem }) {
  return (
    <tr>
      <td>
        <Link href={`/cases?id=${d.case_id}`}>#{d.case_id}</Link>
      </td>
      <td>
        {d.fatal && <Badge tone="bad" title="除斥期间：过期即失权，不能中断">不可中断</Badge>}{" "}
        {d.name}
        {!d.confirmed && <Badge tone="muted" title="还没有律师确认过">未确认</Badge>}
        <div className="muted small">{d.basis}</div>
      </td>
      <td>
        {/* ★ 算不出的时候，这一格不能空着 —— 空白会被读成「没事」 */}
        {d.due_on
          ? d.due_on
          : d.status === "no_fixed"
            /* 没有固定期间的，说清楚是「本来就没有」，不是「你没填」 */
            ? <span className="muted small">无固定期间</span>
            : d.anchor_label
              ? <span className="bad small">缺「{d.anchor_label}」</span>
              : <span className="bad small">缺起算日</span>}
      </td>
      <td>
        <Badge tone={TONE[d.status] ?? "muted"}>
          {DEADLINE_STATUS_LABEL[d.status] ?? d.status}
        </Badge>
        {d.days_left !== null && (
          <div className="muted small">
            {d.days_left < 0 ? `已过 ${-d.days_left} 天` : `还剩 ${d.days_left} 天`}
          </div>
        )}
      </td>
    </tr>
  );
}
