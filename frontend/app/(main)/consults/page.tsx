"use client";

/**
 * 咨询记录（独立页）。
 *
 * ★ 案件页里的咨询面板挂在某个案子上；这一页看的是**全部** ——
 *   包括那些**根本没立案**的咨询，而那才是多数。
 *   "来聊一下、收 1500、没接案" —— 这种事只有这一页看得到。
 *
 * ★ 这一页是看板上「本期咨询费」那个格子的落点。
 *   点开之后看到的必须正好是那个数字本身，所以要认 URL 上的期间参数。
 */
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import {
  fetchConsults, fetchClients, fetchUsers, fetchParties,
  type Consult, type Client, type User, type Party,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Badge, Button, Metric, Notice, Section, useNotice } from "@/components/ui";
import { DrillBanner, useDrill } from "@/components/Drill";
import { NewConsult } from "@/components/NewConsult";

export default function ConsultsPage() {
  const { can } = useAuth();
  const { notice, clear, ok, fail } = useNotice();
  const drill = useDrill();
  const [creating, setCreating] = useState(false);

  const [rows, setRows] = useState<Consult[]>([]);
  const [clients, setClients] = useState<Record<number, Client>>({});
  const [parties, setParties] = useState<Record<number, Party>>({});
  const [users, setUsers] = useState<Record<number, User>>({});

  const load = useCallback(async () => {
    try {
      setRows(await fetchConsults({
        since: drill.since || undefined,
        until: drill.until || undefined,
      }));
    } catch (e) { fail(e); }
    fetchClients()
      .then((cs) => setClients(Object.fromEntries(cs.map((c) => [c.id, c]))))
      .catch(() => {});
    fetchParties("")
      .then((ps) => setParties(Object.fromEntries(ps.map((p) => [p.id, p]))))
      .catch(() => {});
    fetchUsers()
      .then((us) => setUsers(Object.fromEntries(us.map((u) => [u.id, u]))))
      .catch(() => {});
  }, [drill.since, drill.until, fail]);

  useEffect(() => { void load(); }, [load]);

  if (!can("consult.view")) {
    return <Section title="咨询">
      <p className="muted">你没有 consult.view 权限。</p></Section>;
  }

  const live = rows.filter((r) => r.status !== "void");
  const total = live.reduce((s, r) => s + Number(r.amount), 0);
  const free = live.filter((r) => r.free);
  const noCase = live.filter((r) => !r.case_id);

  function clientName(id: number) {
    const c = clients[id];
    if (!c) return `客户${id}`;
    return parties[c.party_id]?.name ?? c.code;
  }

  return (
    <>
      <Notice {...notice} onClose={clear} />
      <DrillBanner drill={drill} label="本期咨询费" n={live.length} />

      {/* ★ 独立的新建入口。原来 ConsultPanel 只挂在案件页、还要案件挂了客户
          才显示 —— 于是**没接案的咨询根本记不了**，而那才是多数。 */}
      {creating && (
        <NewConsult
          onCancel={() => setCreating(false)}
          onDone={() => { setCreating(false); void load(); }}
          onNotice={(k, t) => (k === "ok" ? ok(t) : fail(new Error(t)))}
        />
      )}

      <Section title="咨询记录" desc="包括没立案的 —— 那才是多数"
               extra={can("consult.log") && !creating && (
                 <Button variant="primary" onClick={() => setCreating(true)}>
                   + 记一次咨询
                 </Button>
               )}>
        <div className="metrics">
          <Metric n={`¥${total.toFixed(2)}`} label="收费合计" tone="ok" />
          <Metric n={live.length} label="次咨询" />
          <Metric n={free.length} label="免费"
                  title="第一次见面聊半小时不收钱，很常见" />
          <Metric n={noCase.length} label="没立案的"
                  title="咨询过但没成案的人，是最现成的案源" />
        </div>

        <p className="field-hint" style={{ marginTop: 10 }}>
          ★ <b>没立案的</b>那一栏值得盯 —— 咨询过但没成案的人，
          是最现成的案源。
        </p>

        {live.length === 0 ? (
          <p className="muted small">这个期间没有咨询记录。</p>
        ) : (
          <table >
            <thead>
              <tr>
                <th>日期</th><th>客户</th><th>律师</th><th>时长</th>
                <th>方式</th><th>内容</th><th>金额</th><th>案件</th><th>状态</th>
              </tr>
            </thead>
            <tbody>
              {live.map((c) => (
                <tr key={c.id}>
                  <td>{c.happened_on}</td>
                  <td>{clientName(c.client_id)}</td>
                  <td className="muted small">
                    {users[c.user_id]?.full_name ?? `#${c.user_id}`}
                  </td>
                  <td>{c.minutes} 分</td>
                  <td className="muted small">{c.channel_label}</td>
                  <td className="small">
                    {c.summary || <span className="muted">—</span>}
                  </td>
                  <td>
                    {c.free
                      ? <span className="muted small">免费·{c.free_reason}</span>
                      : <b>¥{Number(c.amount).toFixed(2)}</b>}
                  </td>
                  <td>
                    {c.case_id
                      ? <Link href={`/cases?id=${c.case_id}`}>#{c.case_id}</Link>
                      : <span className="muted small">没立案</span>}
                  </td>
                  <td>
                    <Badge tone={c.status === "billed" ? "ok" : undefined}>
                      {c.status_label}
                    </Badge>
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
