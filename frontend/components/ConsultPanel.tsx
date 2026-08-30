"use client";

/**
 * 法律咨询。
 *
 * ★ **这是工时模块拆掉之后剩下的那一点点。**
 *   提成制律所不按工时给客户开账单 —— 团队成员和助理是工资制/分成制，
 *   他们的时间不换算成客户的钱。真正按小时收钱的只有法律咨询。
 *
 * ★ 没有计时器、没有审批、没有漏记提醒。
 *   一次咨询是**已经发生的收费事实**，聊完了填一条就完了。
 *   加计时器就要处理"忘了停"，加审批就会有人拖着不记。
 *
 * ★ 金额**当场算给你看**。填完时长立刻显示"1.5 小时 × 1000 = 1500"，
 *   因为半小时取整这件事必须让人看见 —— 不然客户问起来律师自己都说不清。
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  fetchConsults, createConsult, voidConsult, fetchConsultRate,
  CHANNEL_LABEL, type Consult, type ConsultRate,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Section, Tag } from "@/components/ui";

const CHANNELS = ["office", "phone", "online", "visit"];

export function ConsultPanel({ clientId, caseId, onNotice }: {
  /** 挂哪个客户。★ 必填 —— 收了钱得知道是谁的钱 */
  clientId: number;
  /** 挂哪个案件。★ 选填 —— 多数咨询发生在立案之前 */
  caseId?: number;
  onNotice: (kind: "ok" | "error", text: string) => void;
}) {
  const { can } = useAuth();
  const [rows, setRows] = useState<Consult[]>([]);
  const [rate, setRate] = useState<ConsultRate | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);

  const [form, setForm] = useState({
    happened_on: new Date().toISOString().slice(0, 10),
    minutes: "60",
    channel: "office",
    summary: "",
    hourly_rate: "",
    free: false,
    free_reason: "",
  });

  const load = useCallback(async () => {
    try {
      setRows(await fetchConsults({ client_id: clientId, case_id: caseId }));
    } catch (e) {
      onNotice("error", e instanceof Error ? e.message : "读取失败");
    }
    fetchConsultRate().then(setRate).catch(() => {});
  }, [clientId, caseId, onNotice]);

  useEffect(() => { void load(); }, [load]);

  /** ★ 当场把取整和金额算给用户看 —— 半小时制必须可见 */
  const preview = useMemo(() => {
    const m = Number(form.minutes) || 0;
    if (!rate || m <= 0) return null;
    if (form.free) return { minutes: 0, amount: 0, note: "免费" };
    const inc = rate.increment_minutes;
    const bmin = m < rate.min_billable_minutes ? 0 : Math.ceil(m / inc) * inc;
    const r = Number(form.hourly_rate || rate.hourly_rate) || 0;
    return {
      minutes: bmin,
      amount: (bmin / 60) * r,
      note: bmin === 0
        ? `不足 ${rate.min_billable_minutes} 分钟，不计费`
        : `${(bmin / 60).toFixed(1)} 小时 × ¥${r}`,
    };
  }, [form.minutes, form.free, form.hourly_rate, rate]);

  async function save() {
    setBusy(true);
    try {
      await createConsult({
        client_id: clientId,
        case_id: caseId ?? null,
        happened_on: form.happened_on,
        minutes: Number(form.minutes),
        channel: form.channel,
        summary: form.summary,
        hourly_rate: form.hourly_rate || undefined,
        free: form.free,
        free_reason: form.free_reason,
      });
      onNotice("ok", "已记录");
      setForm({ ...form, minutes: "60", summary: "", free: false,
                free_reason: "" });
      setOpen(false);
      await load();
    } catch (e) {
      onNotice("error", e instanceof Error ? e.message : "保存失败");
    } finally { setBusy(false); }
  }

  async function drop(c: Consult) {
    const why = window.prompt("作废原因（会记在这条记录上）");
    if (!why) return;
    try {
      await voidConsult(c.id, why);
      onNotice("ok", "已作废");
      await load();
    } catch (e) {
      onNotice("error", e instanceof Error ? e.message : "作废失败");
    }
  }

  const live = rows.filter((r) => r.status !== "void");
  const total = live.reduce((s, r) => s + Number(r.amount), 0);
  const unbilled = live.filter((r) => r.status === "draft" && !r.free);

  return (
    <Section title="法律咨询" collapsible rememberKey={`consult-${clientId}`}
             count={live.length}
             extra={
      can("consult.log") && (
        <button className="btn secondary" disabled={busy}
                onClick={() => setOpen(!open)}>
          {open ? "收起" : "+ 记一次咨询"}
        </button>
      )
    }>
      {open && (
        <div className="stack" style={{ marginBottom: 14 }}>
          <label>日期</label>
          <input type="date" value={form.happened_on}
                 onChange={(e) => setForm({ ...form, happened_on: e.target.value })} />

          <label>聊了多久（分钟）</label>
          <input type="number" value={form.minutes}
                 onChange={(e) => setForm({ ...form, minutes: e.target.value })} />

          <label>方式</label>
          <select value={form.channel}
                  onChange={(e) => setForm({ ...form, channel: e.target.value })}>
            {CHANNELS.map((c) => (
              <option key={c} value={c}>{CHANNEL_LABEL[c]}</option>
            ))}
          </select>

          <label>聊了什么</label>
          <textarea rows={2} value={form.summary}
                    onChange={(e) => setForm({ ...form, summary: e.target.value })}
                    placeholder="解答工程款优先受偿权的行使期限" />
          <p className="muted small">
            ★ 客户砍账单时，这句话是你唯一能拿出来的东西。
          </p>

          <label style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input type="checkbox" checked={form.free} style={{ width: "auto" }}
                   onChange={(e) => setForm({ ...form, free: e.target.checked })} />
            <span>免费</span>
          </label>
          {form.free && (
            <>
              <input value={form.free_reason}
                     onChange={(e) => setForm({ ...form, free_reason: e.target.value })}
                     placeholder="首次面谈 / 老客户 / 未成案" />
              <p className="muted small">
                ★ 要写原因 —— 不写理由的免费，事后分不清是优惠还是漏收。
              </p>
            </>
          )}

          {!form.free && (
            <>
              <label>费率（留空用本所价 ¥{rate?.hourly_rate ?? "—"}/小时）</label>
              <input type="number" value={form.hourly_rate}
                     onChange={(e) => setForm({ ...form, hourly_rate: e.target.value })} />
            </>
          )}

          {/* ★ 当场算给你看 —— 半小时取整这件事必须可见 */}
          {preview && (
            <p className="alert" style={{ marginTop: 10 }}>
              按 <b>{rate?.increment_minutes} 分钟</b>取整　→　{preview.note}
              {preview.amount > 0 && (
                <>　=　<b>¥{preview.amount.toFixed(2)}</b></>
              )}
            </p>
          )}

          <div style={{ marginTop: 12 }}>
            <button className="btn" disabled={busy || !Number(form.minutes)}
                    onClick={() => void save()}>保存</button>
          </div>
        </div>
      )}

      {live.length === 0 ? (
        <p className="muted small">还没有咨询记录。</p>
      ) : (
        <>
          <table >
            <thead>
              <tr><th>日期</th><th>时长</th><th>方式</th><th>内容</th>
                  <th>金额</th><th>状态</th><th /></tr>
            </thead>
            <tbody>
              {live.map((c) => (
                <tr key={c.id}>
                  <td>{c.happened_on}</td>
                  <td>{c.minutes} 分
                    {c.billable_minutes > 0 && c.billable_minutes !== c.minutes && (
                      <span className="muted small">　计 {c.billable_minutes}</span>
                    )}
                  </td>
                  <td className="muted small">{c.channel_label}</td>
                  <td className="small">{c.summary || <span className="muted">—</span>}</td>
                  <td>
                    {c.free
                      ? <span className="muted">免费<br />
                          <span className="small">{c.free_reason}</span></span>
                      : <b>¥{Number(c.amount).toFixed(2)}</b>}
                  </td>
                  <td>
                    <Tag tone={c.status === "billed" ? "ok" : undefined}>
                      {c.status_label}
                    </Tag>
                    {c.invoice_no && (
                      <div className="muted small">{c.invoice_no}</div>
                    )}
                  </td>
                  <td>
                    {can("consult.log") && c.status === "draft" && (
                      <button className="btn ghost sm"
                              onClick={() => void drop(c)}>作废</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <p className="muted small" style={{ marginTop: 8 }}>
            共 <b>¥{total.toFixed(2)}</b>
            {unbilled.length > 0 && (
              <>　其中 <b>{unbilled.length}</b> 条还没出账 ——
                去「收费与账单」里新建账单，然后「拉入咨询」。</>
            )}
          </p>
        </>
      )}
    </Section>
  );
}
