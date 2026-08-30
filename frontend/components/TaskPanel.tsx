"use client";

/**
 * 时限与待办（Step 14/15）。
 *
 * 这个面板要让律师看清四件事，顺序就是它的信息层级：
 *
 *   ① 哪条要命         → 除斥期间（fatal）单独标红，过了就失权，没有补救
 *   ② ★ **哪条算不出**  → 起算日缺失的显示「算不出」，并**点名缺哪个日期**
 *   ③ 这条盖不住什么   → 每条规则自带 caveat，展开就能看
 *   ④ 谁确认过         → 没确认的一律带「未确认」灰标
 *
 * ★ ② 是整个时限引擎最要紧的设计。一个算不出起算日的期限，如果显示成
 *   「正常」或者干脆不显示，律师就会以为系统在替他盯着 —— 而系统其实
 *   什么都没盯。**假的安全感比没有提醒更危险。** 所以宁可显示一个刺眼的
 *   「算不出：缺『竣工验收日』」，也不给一个看起来很安心的空白。
 *
 * ★ 同理，面板顶部那句「提醒不是意见」不是免责套话，是这个模块的边界：
 *   规则是数据，是律所自己维护的，系统只做算术，判断仍然是律师的。
 */
import { useCallback, useEffect, useState } from "react";

import {
  fetchDeadlines, generateDeadlines, setAnchor, interruptDeadline,
  markDeadline, addDeadline, suspendDeadline,
  fetchCaseTasks, createTask, updateTask,
  DEADLINE_STATUS_LABEL, TASK_STATUS_LABEL, PRIORITY_LABEL,
  type DeadlineItem, type TaskRow, type User,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Notice, Rich, Section, Tag, useNotice } from "@/components/ui";

/** 状态 → 配色。expired/unknown 都要刺眼，理由见文件头 ★② */
type Tone = "ok" | "warn" | "bad" | "muted";
const STATUS_TONE: Record<string, Tone> = {
  expired: "bad", urgent: "warn", unknown: "bad",
  // no_fixed 用灰色：它是**说明**，不是警报
  ok: "ok", no_fixed: "muted", done: "muted", waived: "muted",
};

function daysText(d: DeadlineItem): string {
  if (d.days_left === null) return "";
  if (d.days_left < 0) return `已过 ${-d.days_left} 天`;
  if (d.days_left === 0) return "就是今天";
  return `还剩 ${d.days_left} 天`;
}

export function TaskPanel({ caseId, users }: {
  caseId: number;
  users: Record<number, User>;
}) {
  const { can, me } = useAuth();
  const { notice, clear, ok, fail } = useNotice();

  const [deadlines, setDeadlines] = useState<DeadlineItem[]>([]);
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [open, setOpen] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  // 手工加一条 / 加一个待办的表单，默认收起 —— 常用路径是「生成」，不是「手填」
  const [manual, setManual] = useState(false);
  const [newTask, setNewTask] = useState(false);

  const load = useCallback(async () => {
    try {
      const [d, t] = await Promise.all([fetchDeadlines(caseId), fetchCaseTasks(caseId)]);
      setDeadlines(d);
      setTasks(t);
    } catch (e) { fail(e); }
  }, [caseId, fail]);

  useEffect(() => { void load(); }, [load]);

  /** 包一层：所有写操作都是「跑完 → 重新拉 → 报一句」，别各写各的 */
  const run = useCallback(async (label: string, fn: () => Promise<unknown>) => {
    setBusy(true);
    clear();
    try {
      await fn();
      await load();
      ok(label);
      return true;
    } catch (e) {
      fail(e);
      return false;      // 失败时别收表单，用户刚打的字还在里面
    } finally { setBusy(false); }
  }, [clear, load, ok, fail]);

  const canEdit = can("task.deadline");
  const canTask = can("task.manage");

  const expired = deadlines.filter((d) => d.status === "expired");
  const unknown = deadlines.filter((d) => d.status === "unknown");  // 不含 no_fixed
  const urgent = deadlines.filter((d) => d.status === "urgent");

  return (
    <Section title="时限与待办" collapsible rememberKey={`task-${caseId}`}
             defaultOpen
             extra={canEdit && (
               <button className="btn secondary" disabled={busy}
                       title="按本所的时限规则，为本案生成一份时限清单。已确认过的不会被覆盖。"
                       onClick={() => void run("已生成时限清单", () => generateDeadlines(caseId))}>
                 按规则生成
               </button>
             )}>
      <Notice {...notice} onClose={clear} />

      <p className="muted small">
        下面是<b>提醒</b>，不是法律意见。规则由本所自己维护，系统只做算术；
        起算日和是否适用，仍然要承办律师自己判断并确认。
      </p>

      {/* 顶部只放"需要动手的" —— 正常的那些没必要抢注意力 */}
      {(expired.length > 0 || unknown.length > 0 || urgent.length > 0) && (
        <div className="row" style={{ gap: 8, flexWrap: "wrap", margin: "8px 0" }}>
          {expired.length > 0 && <Tag tone="bad">已过期 {expired.length}</Tag>}
          {urgent.length > 0 && <Tag tone="warn">临近 {urgent.length}</Tag>}
          {unknown.length > 0 && (
            <Tag tone="bad" title="缺起算日期，系统算不出来 —— 这些没人在替你盯着">
              算不出 {unknown.length}
            </Tag>
          )}
        </div>
      )}

      {deadlines.length === 0 ? (
        <p className="muted small">
          还没有时限记录。走一遍案件流程会自动生成；也可以点右上角「按规则生成」。
        </p>
      ) : (
        <table >
          <thead>
            <tr>
              <th>期限</th><th>起算</th><th>到期</th><th>状态</th><th />
            </tr>
          </thead>
          <tbody>
            {deadlines.map((d) => (
              <DeadlineRow key={d.id} d={d} open={open === d.id} busy={busy}
                           canEdit={canEdit} users={users}
                           onToggle={() => setOpen(open === d.id ? null : d.id)}
                           onRun={run} />
            ))}
          </tbody>
        </table>
      )}

      {canEdit && (
        <div style={{ marginTop: 10 }}>
          <button className="btn ghost sm" onClick={() => setManual(!manual)}>
            {manual ? "收起" : "+ 手工加一条规则盖不到的时限"}
          </button>
          {manual && (
            <ManualForm caseId={caseId} busy={busy}
                        onSubmit={(b) => run("已添加", () => addDeadline(b))
                                           .then((good) => { if (good) setManual(false); })} />
          )}
        </div>
      )}

      <h4 style={{ marginTop: 18 }}>本案待办</h4>
      {tasks.length === 0 ? (
        <p className="muted small">还没有待办。</p>
      ) : (
        <table >
          <thead>
            <tr><th>事项</th><th>负责</th><th>到期</th><th>状态</th><th /></tr>
          </thead>
          <tbody>
            {tasks.map((t) => (
              <tr key={t.id} className={t.status === "done" ? "muted" : undefined}>
                <td>
                  {t.priority === "high" && <Tag tone="warn">高</Tag>} {t.title}
                  {t.detail && <div className="muted small">{t.detail}</div>}
                </td>
                <td>{t.assignee_id ? (users[t.assignee_id]?.full_name ?? `#${t.assignee_id}`) : "—"}</td>
                <td>{t.due_on || "—"}</td>
                <td>{TASK_STATUS_LABEL[t.status] ?? t.status}</td>
                <td>
                  {canTask && t.status !== "done" && t.status !== "cancelled" && (
                    <button className="btn ghost sm" disabled={busy}
                            onClick={() => void run("已完成",
                              () => updateTask(t.id, { status: "done" }))}>
                      标记完成
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {canTask && (
        <div style={{ marginTop: 8 }}>
          <button className="btn ghost sm" onClick={() => setNewTask(!newTask)}>
            {newTask ? "收起" : "+ 新建待办"}
          </button>
          {newTask && (
            <NewTaskForm caseId={caseId} busy={busy} users={users} me={me?.user.id ?? null}
                         onSubmit={(b) => void run("已创建", () => createTask(b))
                                            .then((good) => { if (good) setNewTask(false); })} />
          )}
        </div>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------- 单行

function DeadlineRow({ d, open, busy, canEdit, users, onToggle, onRun }: {
  d: DeadlineItem; open: boolean; busy: boolean; canEdit: boolean;
  users: Record<number, User>;
  onToggle: () => void;
  onRun: (label: string, fn: () => Promise<unknown>) => Promise<boolean>;
}) {
  const [anchorOn, setAnchorOn] = useState(d.anchor_on || "");
  const [note, setNote] = useState("");
  const [intOn, setIntOn] = useState("");
  const [susEnd, setSusEnd] = useState("");

  const tone = STATUS_TONE[d.status] ?? "muted";

  return (
    <>
      <tr>
        <td>
          {d.fatal && (
            <Tag tone="bad" title="除斥期间：过期即失权，且法律上不能中断、不能中止">
              不可中断
            </Tag>
          )}{" "}
          {d.name}
          {!d.confirmed && (
            <Tag tone="muted" title="还没有律师确认过。未确认的只能当提醒看">未确认</Tag>
          )}
          <div className="muted small">{d.basis}</div>
        </td>
        <td>
          {/* ★ 缺起算日时必须点名缺哪个 —— 只说「算不出」等于没说。
              但手工录的时限本来就没有起算字段，那种要显示「—」，
              不能渲染成一个空括号「缺『』」，看着像数据坏了 */}
          {d.anchor_on
            ? d.anchor_on
            : d.status === "no_fixed" || !d.anchor_label
              ? <span className="muted">—</span>
              : <span className="bad small">缺「{d.anchor_label}」</span>}
        </td>
        <td>{d.due_on || "—"}</td>
        <td>
          <Tag tone={tone}>{DEADLINE_STATUS_LABEL[d.status] ?? d.status}</Tag>
          <div className="muted small">{daysText(d)}</div>
          {/* ★ 顶到最长保护期时，届满日看着只是个普通日期，含义却完全不同：
              再中断也不会往后延。这一点必须在列表里就能看见，不能藏在详情里 */}
          {d.capped && (
            <Tag tone="warn" title="已达《民法典》188 条 2 款的最长保护期，不适用中断中止">
              已封顶
            </Tag>
          )}
        </td>
        <td>
          <button className="btn ghost sm" onClick={onToggle}>{open ? "收起" : "详情"}</button>
        </td>
      </tr>

      {open && (
        <tr>
          <td colSpan={5} className="stack">
            <p className="small"><Rich text={d.message} /></p>

            {/* ★ caveat 摆在最显眼的位置：告诉律师这条**盖不住**什么 */}
            {d.caveat && (
              <p className="alert warn">
                <b>这条盖不住：</b><Rich text={d.caveat} />
              </p>
            )}
            {d.note && <p className="muted small">{d.note}</p>}
            {d.interrupted_on && (
              <p className="small">
                时效<b>中断</b>（195 条）：{d.interrupted_on} 起重新起算　{d.interrupt_note}
              </p>
            )}
            {d.suspend_end_on && (
              <p className="small">
                时效<b>中止</b>（194 条）：事由消除于 {d.suspend_end_on}，
                自该日起满六个月届满　{d.suspend_note}
              </p>
            )}
            {d.outer_due_on && (
              <p className={d.capped ? "warnbox small" : "muted small"}>
                最长保护期届满日：{d.outer_due_on}
                {d.capped
                  ? "　★ 已经顶到这里 —— 再发生中断也不会往后延。"
                  : "　（《民法典》188 条 2 款的 20 年，不适用中断中止）"}
              </p>
            )}

            {canEdit && d.status !== "done" && d.status !== "waived" && (
              <div className="row" style={{ gap: 12, flexWrap: "wrap", marginTop: 8 }}>
                <label className="small">
                  起算日
                  <input type="date" value={anchorOn} disabled={busy}
                         onChange={(e) => setAnchorOn(e.target.value)} />
                </label>
                <input className="small" placeholder="依据（例：竣工验收报告落款日）"
                       value={note} disabled={busy}
                       onChange={(e) => setNote(e.target.value)} style={{ minWidth: 220 }} />
                <button className="btn ghost sm" disabled={busy || !anchorOn}
                        title="填起算日同时视为你已确认这条时限适用于本案"
                        onClick={() => void onRun("已确认起算日",
                          () => setAnchor(d.id, anchorOn, note))}>
                  确认起算日
                </button>

                {/* ★ 不能中断的不给入口 —— 不是禁用按钮，是根本不出现，
                    免得律师以为「有权限的人就能中断」。
                    能不能中断由后端说了算：除斥期间、没有固定期间、
                    规则没标可中断，三种情形都不给 */}
                {d.can_interrupt && (
                  <>
                    <label className="small">
                      中断于
                      <input type="date" value={intOn} disabled={busy}
                             onChange={(e) => setIntOn(e.target.value)} />
                    </label>
                    <button className="btn ghost sm" disabled={busy || !intOn || !note}
                            title="登记时效中断，从该日重新起算。必须写清事由与证据。"
                            onClick={() => void onRun("已登记中断",
                              () => interruptDeadline(d.id, intOn, note))}>
                      登记中断
                    </button>
                  </>
                )}

                {/* ★ 中止和中断分开两个入口，绝不合并成一个下拉。
                    两者算法相反：中断把期限推得很远（整个重来），
                    中止只保证「至少还有六个月」。合并成一个选项，
                    填错的代价是结论差好几年 */}
                {d.can_interrupt && (
                  <>
                    <label className="small" title="填事由「消除」之日，不是发生之日">
                      中止事由消除于
                      <input type="date" value={susEnd} disabled={busy}
                             onChange={(e) => setSusEnd(e.target.value)} />
                    </label>
                    <button className="btn ghost sm" disabled={busy || !susEnd || !note}
                            title="《民法典》194 条：自事由消除之日起满六个月，时效期间届满"
                            onClick={() => void onRun("已登记中止",
                              () => suspendDeadline(d.id, susEnd, note))}>
                      登记中止
                    </button>
                  </>
                )}

                <button className="btn ghost sm" disabled={busy}
                        onClick={() => void onRun("已标记行使",
                          () => markDeadline(d.id, "done", note))}>
                  已行使
                </button>
                <button className="btn ghost sm" disabled={busy}
                        title="本案不适用这条 —— 会保留记录，不删除"
                        onClick={() => void onRun("已标记不适用",
                          () => markDeadline(d.id, "waived", note))}>
                  不适用
                </button>
              </div>
            )}

            {/* 没有「登记中断」按钮时，要说清是**哪一种**不能中断 ——
                三种理由的法律含义完全不同，含混一句会让律师误解这条期限的性质 */}
            {d.fatal ? (
              <p className="muted small" style={{ marginTop: 6 }}>
                这是除斥期间，法律上<b>不能中断也不能中止</b>，所以没有「登记中断」这个选项。
              </p>
            ) : d.status === "no_fixed" ? (
              <p className="muted small" style={{ marginTop: 6 }}>
                这条<b>没有固定期间</b>，没有可以重新起算的东西，所以谈不上中断。
              </p>
            ) : !d.can_interrupt && d.status !== "done" && d.status !== "waived" ? (
              <p className="muted small" style={{ marginTop: 6 }}>
                本所的规则没有把这条标记为可中断。确有中断事由的，
                请让管委会改规则，或手工加一条时限。
              </p>
            ) : null}
            {d.confirmed && <p className="muted small">已由律师确认，重新生成不会覆盖。</p>}
          </td>
        </tr>
      )}
    </>
  );
}

// ---------------------------------------------------------------- 表单

function ManualForm({ caseId, busy, onSubmit }: {
  caseId: number; busy: boolean;
  onSubmit: (b: { case_id: number; name: string; basis: string;
                  due_on: string; fatal: boolean; note: string }) => void;
}) {
  const [name, setName] = useState("");
  const [basis, setBasis] = useState("");
  const [dueOn, setDueOn] = useState("");
  const [fatal, setFatal] = useState(false);
  const [note, setNote] = useState("");

  return (
    <div className="stack" style={{ marginTop: 8 }}>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <input placeholder="期限名称" value={name} onChange={(e) => setName(e.target.value)} />
        {/* ★ 依据必填：算不出来路的期限没人敢用 */}
        <input placeholder="依据（法条/合同条款）" value={basis} style={{ minWidth: 240 }}
               onChange={(e) => setBasis(e.target.value)} />
        <input type="date" value={dueOn} onChange={(e) => setDueOn(e.target.value)} />
        <label className="small" title="除斥期间：过期即失权，不能中断">
          <input type="checkbox" checked={fatal} onChange={(e) => setFatal(e.target.checked)} />
          {" "}不可中断
        </label>
        <input placeholder="备注" value={note} onChange={(e) => setNote(e.target.value)} />
        <button className="btn ghost sm" disabled={busy || !name || !basis || !dueOn}
                onClick={() => void onSubmit({ case_id: caseId, name, basis,
                                               due_on: dueOn, fatal, note })}>
          添加
        </button>
      </div>
      <p className="muted small">
        依据是必填的 —— 一条说不出来路的期限，过两个月连你自己都不敢信。
      </p>
    </div>
  );
}

function NewTaskForm({ caseId, busy, users, me, onSubmit }: {
  caseId: number; busy: boolean; users: Record<number, User>; me: number | null;
  onSubmit: (b: { title: string; case_id: number; detail: string;
                  assignee_id: number | null; due_on: string | null;
                  priority: string }) => void;
}) {
  const [title, setTitle] = useState("");
  const [detail, setDetail] = useState("");
  const [assignee, setAssignee] = useState<string>(me ? String(me) : "");
  const [dueOn, setDueOn] = useState("");
  const [priority, setPriority] = useState("normal");

  return (
    <div className="stack" style={{ marginTop: 8 }}>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <input placeholder="要做什么" value={title} style={{ minWidth: 220 }}
               onChange={(e) => setTitle(e.target.value)} />
        <select value={assignee} onChange={(e) => setAssignee(e.target.value)}>
          <option value="">不指定负责人</option>
          {Object.values(users).map((u) => (
            <option key={u.id} value={u.id}>{u.full_name}</option>
          ))}
        </select>
        <input type="date" value={dueOn} onChange={(e) => setDueOn(e.target.value)} />
        <select value={priority} onChange={(e) => setPriority(e.target.value)}>
          {Object.entries(PRIORITY_LABEL).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
        <button className="btn ghost sm" disabled={busy || !title}
                onClick={() => void onSubmit({
                  title, case_id: caseId, detail,
                  assignee_id: assignee ? Number(assignee) : null,
                  due_on: dueOn || null, priority,
                })}>
          创建
        </button>
      </div>
      <input placeholder="说明（可空）" value={detail} style={{ width: "100%", marginTop: 6 }}
             onChange={(e) => setDetail(e.target.value)} />
    </div>
  );
}
