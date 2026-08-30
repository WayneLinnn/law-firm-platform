"use client";

/**
 * 单据（Step 18）。
 *
 * 两块：**待我处理**在上，**我提的单**在下。
 *
 * ★ 待我处理是按**权限**算的，不是按指派。指派到人的做法在律所里会卡住 ——
 *   负责人出差三天，所有单子停摆。按权限走，团队里任何一个有权限的人
 *   都能批，谁批的记在链上。
 *
 * ★ 这一页最要紧的一件事：**财务在这里看不到案情**。
 *   单据本身就没有案情字段；看不见关联案件的人会看到一句
 *   「本单关联了一个案件，但你没有查看权限 —— 这不是错误」。
 *   明说，是为了让人不去猜、不去问、不去在备注里补 ——
 *   那才是隔离墙真正会漏的地方。
 */
import { Fragment, useCallback, useEffect, useState } from "react";
import Link from "next/link";

import {
  fetchInbox, fetchMyTickets, fetchTicket, decideTicket, payTicket,
  executeSeal, cancelTicket, submitTicket,
  TICKET_KIND_LABEL, yuan,
  type Ticket, type TicketStep, type User,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Notice, Rich, Section, Tag, useNotice } from "@/components/ui";
import { DrillBanner, useDrill } from "@/components/Drill";
import { NewTicket } from "@/components/NewTicket";

const TONE: Record<string, "ok" | "warn" | "bad" | "muted"> = {
  draft: "muted", pending: "warn", approved: "warn",
  rejected: "bad", paid: "ok", done: "ok", cancelled: "muted",
};

export default function TicketsPage() {
  const { can } = useAuth();
  const { notice, clear, ok, fail } = useNotice();

  const drill = useDrill();
  const [inbox, setInbox] = useState<Ticket[]>([]);
  const [mine, setMine] = useState<Ticket[]>([]);
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    try {
      const [i, m] = await Promise.all([fetchInbox(), fetchMyTickets()]);
      setInbox(i);
      setMine(m);
    } catch (e) { fail(e); }
  }, [fail]);

  useEffect(() => { void load(); }, [load]);

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
      return false;
    } finally { setBusy(false); }
  }, [clear, load, ok, fail]);

  if (!can("approval.view")) {
    return <Section title="单据">
      <p className="muted">你没有 approval.view 权限。</p></Section>;
  }

  // ★ 和 approval.metrics() 同一套口径：pending = 待我批，approved = 待付款。
  //   两边各写一遍迟早会漂，漂了就是"格子里 3 张，点进去 5 张"。
  const shown = drill.f === "to_decide" ? inbox.filter((t) => t.status === "pending")
              : drill.f === "to_pay" ? inbox.filter((t) => t.status === "approved")
              : inbox;

  return (
    <>
      <Notice {...notice} onClose={clear} />

      <DrillBanner drill={drill} n={shown.length} total={inbox.length}
                   label={drill.f === "to_decide" ? "待我批"
                          : drill.f === "to_pay" ? "待付款" : "筛选"} />

      <Section title={`待我处理${shown.length ? " · " + shown.length : ""}`}>
        {shown.length === 0 ? (
          <p className="muted small">
            {drill.f ? "这一档现在是空的。" : "没有等你的单子。"}
            <br />
            这里按<b>权限</b>算，不按指派 —— 有权限批这一道的人都会看到，
            谁批的记在链上。
          </p>
        ) : (
          <TicketTable rows={shown} busy={busy} onRun={run} showActions />
        )}
      </Section>

      <Section title="我提的单"
               extra={can("approval.request") && (
                 <button className="btn secondary" disabled={busy}
                         onClick={() => setCreating(!creating)}>
                   {creating ? "收起" : "+ 提单"}
                 </button>
               )}>
        {creating && (
          <NewTicket busy={busy}
                     onDone={() => { setCreating(false); void load(); }}
                     onNotice={{ ok, fail, clear }} />
        )}
        {mine.length === 0 ? (
          <p className="muted small">还没提过单。</p>
        ) : (
          <TicketTable rows={mine} busy={busy} onRun={run} />
        )}
      </Section>
    </>
  );
}

// ---------------------------------------------------------------- 列表

function TicketTable({ rows, busy, onRun, showActions }: {
  rows: Ticket[]; busy: boolean;
  onRun: (label: string, fn: () => Promise<unknown>) => Promise<boolean>;
  showActions?: boolean;
}) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <table >
      <thead>
        <tr>
          <th>单号</th><th>类型</th><th>事由</th><th>金额</th>
          <th>状态</th><th />
        </tr>
      </thead>
      <tbody>
        {rows.map((t) => (
          <Fragment key={t.id}>
            <tr>
              <td>{t.number}</td>
              <td>{t.kind_label}</td>
              <td style={{ maxWidth: 300 }}>
                {t.subject}
                {t.payee && <div className="muted small">付给 {t.payee}</div>}
              </td>
              <td>
                {t.kind === "seal"
                  ? <span className="muted">—</span>
                  : `¥${yuan(t.amount)}`}
              </td>
              <td><Tag tone={TONE[t.status] ?? "muted"}>{t.status_label}</Tag></td>
              <td>
                <button className="btn ghost sm"
                        onClick={() => setOpen(open === t.id ? null : t.id)}>
                  {open === t.id ? "收起" : "详情"}
                </button>
              </td>
            </tr>
            {open === t.id && (
              <tr>
                <td colSpan={6} className="stack">
                  <TicketDetail id={t.id} busy={busy} onRun={onRun}
                                showActions={showActions} />
                </td>
              </tr>
            )}
          </Fragment>
        ))}
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------- 详情

function TicketDetail({ id, busy, onRun, showActions }: {
  id: number; busy: boolean;
  onRun: (label: string, fn: () => Promise<unknown>) => Promise<boolean>;
  showActions?: boolean;
}) {
  const { can } = useAuth();
  const [t, setT] = useState<Ticket | null>(null);
  const [note, setNote] = useState("");
  const [ref, setRef] = useState("");
  const [used, setUsed] = useState("");

  const load = useCallback(async () => {
    try { setT(await fetchTicket(id)); } catch { /* 上层已经报过了 */ }
  }, [id]);

  useEffect(() => { void load(); }, [load]);

  const run = (label: string, fn: () => Promise<unknown>) =>
    onRun(label, fn).then(async (good) => { await load(); return good; });

  if (!t) return <p className="muted small">加载中…</p>;

  return (
    <>
      {/* ★ 隔离墙落地的地方。看不到案件时**明说**，不悄悄抹掉 */}
      {t.case_visible ? (
        t.case_id && (
          <p className="small" style={{ marginTop: 0 }}>
            关联案件 <Link href={`/cases?id=${t.case_id}`}>#{t.case_id}</Link>
          </p>
        )
      ) : (
        <p className="alert" style={{ whiteSpace: "pre-wrap", marginTop: 0 }}>
          <Rich text={t.case_note} />
        </p>
      )}

      {t.detail && <p className="small">{t.detail}</p>}
      {t.payee_account && (
        <p className="small">
          收款账号 <code>{t.payee_account}</code>
          {t.account_masked && (
            <span className="muted small">　（完整账号只对付款人显示）</span>
          )}
        </p>
      )}
      {t.kind === "seal" && (
        <p className="small">
          {t.seal_kind_label}　《{t.seal_document}》　申请 {t.seal_copies} 份
          {t.seal_used > 0 && <b>　实际用印 {t.seal_used} 份</b>}
          {t.seal_takeaway && <Tag tone="warn">原件外带</Tag>}
        </p>
      )}
      {t.paid_on && (
        <p className="small">
          {t.kind === "seal" ? "用印于" : "付款于"} {t.paid_on}
          {t.pay_reference && `　流水号 ${t.pay_reference}`}
        </p>
      )}
      {t.close_note && <p className="alert warn">{t.close_note}</p>}

      {/* 流转链 —— 走到哪一步、谁批的，一眼看到 */}
      {t.steps && t.steps.length > 0 && (
        <table  style={{ marginTop: 6 }}>
          <tbody>
            {t.steps.map((s) => (
              <tr key={s.id} className={s.status === "waiting" ? undefined : "muted"}>
                <td style={{ width: 30 }}>{s.seq}</td>
                <td style={{ width: 120 }}>{s.name}</td>
                <td style={{ width: 80 }}>
                  <Tag tone={s.status === "approved" ? "ok"
                              : s.status === "rejected" ? "bad"
                              : t.current_seq === s.seq ? "warn" : "muted"}>
                    {s.status_label}
                  </Tag>
                </td>
                <td>{s.note || (s.status === "waiting" ? "" : "—")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* ---- 动作 ---- */}
      {showActions && t.status === "pending" && (
        <div className="row" style={{ gap: 8, marginTop: 10, flexWrap: "wrap" }}>
          <input placeholder="意见（驳回必填）" value={note} disabled={busy}
                 style={{ minWidth: 280 }}
                 onChange={(e) => setNote(e.target.value)} />
          <button className="btn ghost sm" disabled={busy}
                  onClick={() => void run("已通过",
                    () => decideTicket(t.id, true, note))}>
            通过
          </button>
          <button className="btn ghost sm" disabled={busy || !note.trim()}
                  title={note.trim() ? "" : "驳回必须写理由 —— 不说为什么，对方只会原样再提一次"}
                  onClick={() => void run("已驳回",
                    () => decideTicket(t.id, false, note))}>
            驳回
          </button>
        </div>
      )}

      {showActions && t.status === "approved" && t.kind !== "seal" && can("approval.pay") && (
        <div className="row" style={{ gap: 8, marginTop: 10, flexWrap: "wrap" }}>
          <input placeholder="银行流水号" value={ref} disabled={busy}
                 style={{ minWidth: 220 }}
                 onChange={(e) => setRef(e.target.value)} />
          <button className="btn secondary" disabled={busy}
                  onClick={() => void run("已付款",
                    () => payTicket(t.id, { reference: ref }))}>
            确认付款 ¥{yuan(t.amount)}
          </button>
          <span className="muted small">
            付完会自动在案件上挂一笔代垫，等开票时拉进账单。
          </span>
        </div>
      )}

      {showActions && t.status === "approved" && t.kind === "seal" && can("approval.seal") && (
        <div className="row" style={{ gap: 8, marginTop: 10, flexWrap: "wrap" }}>
          <label className="small">
            实际盖了
            <input type="number" min="1" max={t.seal_copies} value={used}
                   style={{ width: 70 }} disabled={busy}
                   onChange={(e) => setUsed(e.target.value)} /> 份
          </label>
          <button className="btn secondary" disabled={busy || !used}
                  onClick={() => void run("用印已登记",
                    () => executeSeal(t.id, Number(used)))}>
            登记用印
          </button>
          <span className="muted small">
            ★ 申请 {t.seal_copies} 份。超过会被拦 —— 多盖的那几份查不清就是事故。
          </span>
        </div>
      )}

      {!showActions && t.status === "draft" && (
        <div className="row" style={{ gap: 8, marginTop: 10 }}>
          <button className="btn ghost sm" disabled={busy}
                  onClick={() => void run("已提交", () => submitTicket(t.id))}>
            提交审批
          </button>
        </div>
      )}

      {!showActions && !t.locked && (
        <div style={{ marginTop: 8 }}>
          <button className="btn ghost sm" disabled={busy}
                  onClick={() => {
                    const r = window.prompt("作废原因（会留痕）：");
                    if (r) void run("已作废", () => cancelTicket(t.id, r));
                  }}>
            作废
          </button>
        </div>
      )}
    </>
  );
}
