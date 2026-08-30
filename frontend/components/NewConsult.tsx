"use client";

/**
 * 记一次咨询 —— **独立入口**，自己选客户。
 *
 * ★ 为什么单独做这个：`ConsultPanel` 只挂在案件页，而且要案件挂了客户
 *   才显示。于是**没接案的咨询根本记不了** —— 而那才是多数：
 *   「来聊一下、收 1500、没接案」正是咨询费最典型的形态。
 *
 * ★ 金额**当场算给你看**。半小时取整这件事必须让人看见 ——
 *   不然客户问起来律师自己都说不清。
 */
import { useEffect, useMemo, useState } from "react";

import {
  fetchClients, fetchParties, fetchConsultRate, createConsult,
  CHANNEL_LABEL,
  type Client, type Party, type ConsultRate,
} from "@/lib/api";
import { Button, Field, Section } from "@/components/ui";

const CHANNELS = ["office", "phone", "online", "visit"];

export function NewConsult({ onDone, onCancel, onNotice }: {
  onDone: () => void;
  onCancel: () => void;
  onNotice: (kind: "ok" | "error", text: string) => void;
}) {
  const [clients, setClients] = useState<Client[]>([]);
  const [parties, setParties] = useState<Record<number, Party>>({});
  const [rate, setRate] = useState<ConsultRate | null>(null);
  const [busy, setBusy] = useState(false);

  const [form, setForm] = useState({
    client_id: "" as number | "",
    happened_on: new Date().toISOString().slice(0, 10),
    minutes: "60",
    channel: "office",
    summary: "",
    hourly_rate: "",
    free: false,
    free_reason: "",
  });

  useEffect(() => {
    fetchClients().then(setClients).catch(() => {});
    fetchParties("")
      .then((ps) => setParties(Object.fromEntries(ps.map((p) => [p.id, p]))))
      .catch(() => {});
    fetchConsultRate().then(setRate).catch(() => {});
  }, []);

  /** ★ 当场把取整和金额算给用户看 */
  const preview = useMemo(() => {
    const m = Number(form.minutes) || 0;
    if (!rate || m <= 0) return null;
    if (form.free) return { note: "免费", amount: 0 };
    const inc = rate.increment_minutes;
    const bmin = m < rate.min_billable_minutes ? 0 : Math.ceil(m / inc) * inc;
    const r = Number(form.hourly_rate || rate.hourly_rate) || 0;
    return {
      amount: (bmin / 60) * r,
      note: bmin === 0
        ? `不足 ${rate.min_billable_minutes} 分钟，不计费`
        : `${(bmin / 60).toFixed(1)} 小时 × ¥${r}`,
    };
  }, [form.minutes, form.free, form.hourly_rate, rate]);

  function clientName(c: Client) {
    return parties[c.party_id]?.name ?? c.code;
  }

  async function save() {
    if (form.client_id === "") {
      onNotice("error", "要先选客户 —— 收了钱得知道是谁的钱");
      return;
    }
    setBusy(true);
    try {
      await createConsult({
        client_id: Number(form.client_id),
        happened_on: form.happened_on,
        minutes: Number(form.minutes),
        channel: form.channel,
        summary: form.summary,
        hourly_rate: form.hourly_rate || undefined,
        free: form.free,
        free_reason: form.free_reason,
      });
      onNotice("ok", "已记录");
      onDone();
    } catch (e) {
      onNotice("error", e instanceof Error ? e.message : "保存失败");
    } finally { setBusy(false); }
  }

  return (
    <Section title="记一次咨询"
             desc="★ 不用先建案件 —— 多数咨询发生在立案之前，甚至根本不会立案"
             extra={<Button onClick={onCancel}>取消</Button>}>
      <div className="grid two">
        <div>
          <Field label="客户 *"
                 hint="没有的话先去「客户」建一个">
            <select value={form.client_id}
                    onChange={(e) => setForm({ ...form,
                      client_id: e.target.value ? Number(e.target.value) : "" })}>
              <option value="">选一个客户</option>
              {clients.map((c) => (
                <option key={c.id} value={c.id}>{clientName(c)}</option>
              ))}
            </select>
          </Field>

          <Field label="日期">
            <input type="date" value={form.happened_on}
                   onChange={(e) => setForm({ ...form, happened_on: e.target.value })} />
          </Field>

          <Field label="聊了多久（分钟）">
            <input type="number" value={form.minutes}
                   onChange={(e) => setForm({ ...form, minutes: e.target.value })} />
          </Field>

          <Field label="方式">
            <select value={form.channel}
                    onChange={(e) => setForm({ ...form, channel: e.target.value })}>
              {CHANNELS.map((c) => (
                <option key={c} value={c}>{CHANNEL_LABEL[c]}</option>
              ))}
            </select>
          </Field>
        </div>

        <div>
          <Field label="聊了什么"
                 hint="★ 客户砍账单时，这句话是你唯一能拿出来的东西">
            <textarea rows={3} value={form.summary}
                      onChange={(e) => setForm({ ...form, summary: e.target.value })}
                      placeholder="解答工程款优先受偿权的行使期限" />
          </Field>

          <div className="field">
            <label className="inline">
              <input type="checkbox" checked={form.free}
                     onChange={(e) => setForm({ ...form, free: e.target.checked })} />
              <span>免费</span>
            </label>
          </div>

          {form.free ? (
            <Field hint="★ 不写理由的免费，事后分不清是优惠还是漏收">
              <input value={form.free_reason}
                     onChange={(e) => setForm({ ...form, free_reason: e.target.value })}
                     placeholder="首次面谈 / 老客户 / 未成案" />
            </Field>
          ) : (
            <Field label={`单价（留空用本所价 ¥${rate?.hourly_rate ?? "—"}/小时）`}>
              <input type="number" value={form.hourly_rate}
                     onChange={(e) => setForm({ ...form, hourly_rate: e.target.value })} />
            </Field>
          )}

          {preview && (
            <div className="alert" style={{ marginTop: 4 }}>
              按 <b>{rate?.increment_minutes} 分钟</b>取整　→　{preview.note}
              {preview.amount > 0 && <>　=　<b>¥{preview.amount.toFixed(2)}</b></>}
            </div>
          )}
        </div>
      </div>

      <div className="row tight" style={{ marginTop: 8 }}>
        <Button variant="primary" disabled={busy || !Number(form.minutes)}
                onClick={() => void save()}>保存</Button>
        <Button onClick={onCancel}>取消</Button>
      </div>
    </Section>
  );
}
