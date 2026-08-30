"use client";

/**
 * 归档（卷宗）。
 *
 * ★ 这个面板的主角是**卷宗目录**，不是"归档"那个按钮。
 *   律所归档要出一份「本卷共 X 件」，而律师在按下归档之前，
 *   唯一能确认"该进卷的都进了"的办法就是把这份目录看一遍。
 *
 * ★ 归档后卷**只读**。所以按之前要把「还有什么没了结」摆得足够显眼 ——
 *   归了档再想补东西只能解档，那是管委会的权限。
 */
import { useCallback, useEffect, useState } from "react";

import {
  fetchInventory, fetchArchiveBlockers, fetchArchiveRecord,
  fetchArchiveHistory, doArchive, doUnarchive, verifyArchive,
  type Inventory, type ArchiveBlocker, type ArchiveRecord, type User,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Notice, Rich, Section, Tag, useNotice } from "@/components/ui";

export function ArchivePanel({ caseId, users }: {
  caseId: number;
  users: Record<number, User>;
}) {
  const { can } = useAuth();
  const { notice, clear, ok, fail } = useNotice();

  const [inv, setInv] = useState<Inventory | null>(null);
  const [blockers, setBlockers] = useState<ArchiveBlocker[]>([]);
  const [record, setRecord] = useState<ArchiveRecord | null>(null);
  const [history, setHistory] = useState<ArchiveRecord[]>([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [showHistory, setShowHistory] = useState(false);

  const load = useCallback(async () => {
    try {
      const [i, b, r, h] = await Promise.all([
        fetchInventory(caseId), fetchArchiveBlockers(caseId),
        fetchArchiveRecord(caseId), fetchArchiveHistory(caseId),
      ]);
      setInv(i);
      setBlockers(b);
      setRecord((r as ArchiveRecord)?.id ? (r as ArchiveRecord) : null);
      setHistory(h);
    } catch (e) { fail(e); }
  }, [caseId, fail]);

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

  if (!can("archive.view")) return null;

  const archived = !!record;

  return (
    <Section title={archived ? `卷宗 · ${record!.volume_no}` : "卷宗"}>
      <Notice {...notice} onClose={clear} />

      {/* ---------------- 已归档 ---------------- */}
      {archived && (
        <div className="alert" style={{ whiteSpace: "pre-wrap" }}>
          <b>本卷共 {record!.item_count} 件</b>
          　归档于 {record!.archived_at.slice(0, 10)}
          　{users[record!.archived_by]?.full_name ?? ""}
          {record!.keep_until && `　保存至 ${record!.keep_until}`}
          <br />
          归档后**卷已定死**，不能再往里写东西。要补充只能解档。
          {record!.note && <><br />{record!.note}</>}
        </div>
      )}

      {/* ---------------- 卷宗目录 ---------------- */}
      <h4 style={{ marginTop: archived ? 14 : 0 }}>
        卷宗目录{inv && `　本卷共 ${inv.item_count} 件`}
      </h4>
      {!inv ? (
        <p className="muted small">加载中…</p>
      ) : inv.groups.length === 0 ? (
        <p className="muted small">这个案子还没有任何可归档的内容。</p>
      ) : (
        <table >
          <tbody>
            {inv.groups.map((g, i) => (
              <tr key={g.module + g.name + i}>
                <td style={{ width: 30 }} className="muted small">{i + 1}</td>
                <td>{g.name}</td>
                <td style={{ width: 60, textAlign: "right" }}>{g.count} 件</td>
                <td className="muted small">{g.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* ★ 有模块没报数 —— 卷少了一块，必须明说 */}
      {inv && inv.skipped.length > 0 && (
        <p className="alert warn">
          <b>这些模块没报数：{inv.skipped.join("、")}</b><br />
          卷里可能少了一部分内容，而**少的那部分目录上不会出现**。
          归档前请让管理员查一下。
        </p>
      )}

      {/* ---------------- 阻断项 ---------------- */}
      {!archived && blockers.length > 0 && (
        <div className="alert warn" style={{ marginTop: 12 }}>
          <b>还有 {blockers.length} 件事没了结，先处理完再归档：</b>
          <ul style={{ margin: "6px 0 0", paddingLeft: 20 }}>
            {blockers.map((b, i) => (
              <li key={i}>
                {b.what}
                <span className="muted">　→ {b.how}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* ---------------- 动作 ---------------- */}
      {!archived && can("archive.do") && (
        <div style={{ marginTop: 12 }}>
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            <input placeholder="归档备注（可空）" value={note} disabled={busy}
                   style={{ minWidth: 260 }}
                   onChange={(e) => setNote(e.target.value)} />
            {blockers.length === 0 ? (
              <button className="btn secondary" disabled={busy}
                      onClick={() => {
                        if (window.confirm(
                          `归档本卷，共 ${inv?.item_count ?? 0} 件。\n\n` +
                          "★ 归档后卷就定死了：不能再传材料、记工时、开账单、提单据。\n" +
                          "要补充只能解档（需要管委会权限）。")) {
                          void run("已归档", () => doArchive(caseId, { note }));
                        }
                      }}>
                归档 ⚠
              </button>
            ) : can("archive.force") ? (
              <button className="btn secondary" disabled={busy}
                      title="带着没了结的事归档 —— 那些事会原样记进归档备注"
                      onClick={() => {
                        if (window.confirm(
                          "还有没了结的事，确定强制归档？\n\n" +
                          blockers.map((b) => "· " + b.what).join("\n") +
                          "\n\n★ 这些会原样记进归档备注，事后查得到。")) {
                          void run("已强制归档",
                            () => doArchive(caseId, { note, force: true }));
                        }
                      }}>
                强制归档 ⚠⚠
              </button>
            ) : (
              <span className="muted small">
                先把上面的事处理完 —— 强制归档需要管委会权限。
              </span>
            )}
          </div>
        </div>
      )}

      {archived && (
        <div className="row" style={{ gap: 8, marginTop: 12, flexWrap: "wrap" }}>
          {can("archive.unarchive") && (
            <button className="btn secondary" disabled={busy}
                    title="案子要再审、要申请执行 —— 解档回到「已结案」"
                    onClick={() => {
                      const r = window.prompt(
                        "解档理由（必填，会留痕）：\n" +
                        "例如：对方上诉，要出二审 / 申请强制执行");
                      if (r) void run("已解档", () => doUnarchive(caseId, r));
                    }}>
              解档
            </button>
          )}
          <button className="btn ghost sm" disabled={busy}
                  title="重算指纹和归档时比 —— 正常永远一致"
                  onClick={() => {
                    void verifyArchive(caseId).then((v) => {
                      if (v.ok) ok(`卷与归档时一致（${v.item_count_now} 件）`);
                      else fail(new Error(
                        `${v.message}\n归档时 ${v.item_count_then} 件，` +
                        `现在 ${v.item_count_now} 件。`));
                    }).catch(fail);
                  }}>
            校验完整性
          </button>
        </div>
      )}

      {/* ---------------- 历史 ---------------- */}
      {history.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <button className="btn ghost sm" onClick={() => setShowHistory(!showHistory)}>
            {showHistory ? "收起" : `归档历史（${history.length} 次）`}
          </button>
          {showHistory && (
            <table  style={{ marginTop: 6 }}>
              <tbody>
                {history.map((h) => (
                  <tr key={h.id}>
                    <td style={{ width: 100 }}>{h.volume_no}</td>
                    <td style={{ width: 90 }}>{h.archived_at.slice(0, 10)}</td>
                    <td style={{ width: 60 }}>{h.item_count} 件</td>
                    <td>
                      {h.active
                        ? <Tag tone="ok">生效中</Tag>
                        : <>
                            <Tag tone="muted">已解档</Tag>
                            <span className="muted small">
                              　{h.unarchived_at.slice(0, 10)}　{h.unarchive_reason}
                            </span>
                          </>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </Section>
  );
}
