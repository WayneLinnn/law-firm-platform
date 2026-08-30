"use client";

/**
 * 利冲检查面板。挂在案件详情页 —— 它是**建案的强制卡点**，不是可选功能。
 *
 * 界面上要说清三件事，缺一件这个模块就等于没做：
 *
 *   ① 现在到底能不能接这个案子      → 顶部状态条，一眼看到
 *   ② 为什么（命中了什么）           → 逐条列出，按严重度排序，不折叠
 *   ③ 谁在什么时候以什么理由放的行   → 处置留痕，事后追责唯一依据
 *
 * ★ 「放行」必须写理由。合规检查看的就是这句话 ——
 *   没有理由的放行，和没做利冲是一回事。
 */
import { useEffect, useRef, useState } from "react";

import {
  runConflictCheck, fetchLatestConflict, fetchConflictChecks,
  decideConflict, resolveHit, fetchWalls, removeWall,
  CONFLICT_PASSING, CONFLICT_STATUS_LABEL, HIT_LEVEL_LABEL, HIT_KIND_LABEL,
  type ConflictCheckDetail, type ConflictCheck, type ConflictHit,
  type Party, type User, type Wall,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Notice, Section, Tag, useNotice } from "@/components/ui";

export function ConflictPanel({
  caseId, parties, users, onDecided,
}: {
  caseId: number;
  /** 主体库，用来把 party_id 显示成人名 */
  parties: Record<number, Party>;
  users: Record<number, User>;
  /** 处置完通知父组件刷新案件 —— 状态机的下一步会跟着变 */
  onDecided?: () => void;
}) {
  const { can, me } = useAuth();
  const { notice, clear, ok, fail } = useNotice();

  const [latest, setLatest] = useState<ConflictCheckDetail | null>(null);
  const [history, setHistory] = useState<ConflictCheck[]>([]);
  const [walls, setWalls] = useState<Wall[]>([]);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [wallIds, setWallIds] = useState<number[]>([]);
  const [selfWallOk, setSelfWallOk] = useState(false);
  const [showDecide, setShowDecide] = useState(false);
  // ★ busy 是 state，下一次渲染才生效。连按两下按钮时两个回调看到的都是
  //   旧的 false，于是提交两次。挡重复提交必须用同步的 ref。
  const inFlight = useRef(false);

  const canRun = can("conflict.run");
  const canDecide = can("conflict.decide");
  const canManageWall = can("identity.manage_wall");
  const myId = me?.user.id ?? 0;

  function reload() {
    fetchLatestConflict(caseId).then(setLatest).catch(() => setLatest(null));
    fetchConflictChecks(caseId).then(setHistory).catch(() => {});
    // 墙要看得见 —— 看不见的墙没法排查也没法撤
    if (canManageWall) fetchWalls("case", caseId).then(setWalls).catch(() => setWalls([]));
  }
  useEffect(() => {
    setLatest(null); setHistory([]); setWalls([]);
    setShowDecide(false); setNote(""); setWallIds([]); setSelfWallOk(false);
    reload();
  }, [caseId]);

  async function run() {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      const d = await runConflictCheck(caseId);
      setLatest(d);
      setShowDecide(false);
      ok(`检查完成：${CONFLICT_STATUS_LABEL[d.status] ?? d.status} · 命中 ${d.hits.length} 条`);
      fetchConflictChecks(caseId).then(setHistory).catch(() => {});
      onDecided?.();
    } catch (e) { fail(e); } finally { inFlight.current = false; setBusy(false); }
  }

  async function decide(status: "waived" | "blocked") {
    if (!latest || inFlight.current) return;
    if (!note.trim()) { fail(new Error("必须写明理由 —— 事后追责要靠它")); return; }
    // ★ 把自己挡在外面是很容易的误操作，而且做完就看不到本案了。必须明确确认。
    if (status === "waived" && wallIds.includes(myId) && !selfWallOk) {
      fail(new Error("你把自己也勾进了隔离墙 —— 请先勾选下方的确认框"));
      return;
    }
    inFlight.current = true;
    setBusy(true);
    try {
      const d = await decideConflict(latest.id, {
        status, note: note.trim(),
        // 隔离墙只在放行时有意义：允许接，但把相关的人挡在案件外
        walls: status === "waived"
          ? wallIds.map((user_id) => ({ user_id, mode: "deny", reason: "利冲隔离" }))
          : [],
      });
      setLatest(d);
      setShowDecide(false); setNote(""); setWallIds([]);
      setSelfWallOk(false);
      ok(status === "waived" ? "已放行，理由已留痕" : "已阻断，本案不能继续");
      fetchConflictChecks(caseId).then(setHistory).catch(() => {});
      if (canManageWall) fetchWalls("case", caseId).then(setWalls).catch(() => {});
      onDecided?.();
    } catch (e) { fail(e); } finally { inFlight.current = false; setBusy(false); }
  }

  async function dropWall(w: Wall) {
    try {
      await removeWall(w.id);
      ok(`已撤销对「${nameOf(users, w.user_id)}」的隔离`);
      fetchWalls("case", caseId).then(setWalls).catch(() => {});
      onDecided?.();
    } catch (e) { fail(e); }
  }

  async function resolve(hitId: number, text: string) {
    try {
      await resolveHit(hitId, text);
      ok("该条已处置");
      reload();
    } catch (e) { fail(e); }
  }

  const passing = latest ? CONFLICT_PASSING.includes(latest.status) : false;
  const needsDecision = latest?.status === "pending";

  /* ★ **已阻断的案子也要能重新处置。**
   *
   *   原来处置区只在 `pending` 时渲染 —— 一旦阻断，界面上**没有任何出口**：
   *   按钮全没了，只剩一条"已阻断"。而实际业务里改主意很常见：
   *   对方撤了、征得双方书面同意了、发现是同名不同人。
   *
   *   **一个做完就没法回头的决定，人就不敢做** —— 然后利冲这道闸
   *   要么没人点，要么全点"放行"。 */
  const canRedecide = latest?.status === "blocked";
  const showDecideBox = needsDecision || canRedecide;

  // ★ **没通过、或者还等着处置的，必须默认展开。**
  //   那是"不能往下走"的信号 —— 收起来就等于没查。
  //   已经放行的可以收起来，它只是个历史记录了。
  const mustSee = !passing || needsDecision;

  return (
    <Section title="利冲检查" collapsible rememberKey={`conflict-${caseId}`}
             defaultOpen={mustSee}
             desc={needsDecision ? "★ 等你处置 —— 处置之前不能往下走"
                   : !passing ? "★ 没通过 —— 本案不能继续" : undefined}
             extra={
      canRun && (
        <button className="btn secondary" onClick={run} disabled={busy}>
          {busy ? "检查中…" : latest ? "重跑检查" : "跑一次检查"}
        </button>
      )
    }>
      <Notice text={notice.text} kind={notice.kind} onClose={clear} />

      {/* ① 现在到底能不能接 —— 一眼看到 */}
      <div className={"conflict-bar " + (
        latest === null ? "none" : passing ? "pass" : latest.status === "blocked" ? "stop" : "warn"
      )}>
        {latest === null ? (
          <>
            <strong>尚未做利冲检查</strong>
            <span className="muted small">
              建案前必须过这一关 —— 案件推进到「待立案」时会被卡住。
            </span>
          </>
        ) : (
          <>
            <strong>{CONFLICT_STATUS_LABEL[latest.status] ?? latest.status}</strong>
            <span className="muted small">
              {latest.hits.length === 0
                ? "没有命中任何冲突"
                : `命中 ${latest.hits.length} 条`}
              {" · "}{latest.requested_at.slice(0, 16).replace("T", " ")}
              {latest.requested_by && ` · ${nameOf(users, latest.requested_by)} 发起`}
            </span>
          </>
        )}
      </div>

      {/* ③ 处置留痕 —— 谁、什么时候、什么理由 */}
      {latest?.decided_at && (
        <div className="alert" style={{ marginTop: 10 }}>
          <strong>{nameOf(users, latest.decided_by)}</strong>
          {" 于 "}{latest.decided_at.slice(0, 16).replace("T", " ")}
          {latest.status === "waived" ? " 放行：" : " 阻断："}
          {latest.decision_note || "（未填理由）"}
        </div>
      )}

      {/* ② 命中了什么 —— 逐条列出，按严重度排序，不折叠 */}
      {latest && latest.hits.length > 0 && (
        <div style={{ marginTop: 12 }}>
          {latest.hits.map((h) => (
            <HitRow key={h.id} hit={h} parties={parties}
                    canDecide={canDecide} onResolve={resolve} />
          ))}
          <p className="muted small" style={{ marginTop: 6 }}>
            逐条给理由，比整体一句「已确认无冲突」更经得起检查。
          </p>
        </div>
      )}

      {/* 管委会处置 —— pending 要处置，blocked 可以改主意 */}
      {showDecideBox && (
        canDecide ? (
          showDecide ? (
            <div className="decide-box">
              <div className="muted small" style={{ marginBottom: 6 }}>
                ★ 理由必填 —— 这是事后唯一能解释「为什么当时接了」的东西
              </div>
              <textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)}
                        placeholder="例：对方关联方系十年前已结案的旧客户，无在办业务，已征得双方书面同意" />

              <div style={{ marginTop: 10 }}>
                <div className="muted small" style={{ marginBottom: 4 }}>
                  放行时设隔离墙（可选）：<strong>勾中的人将看不到本案</strong>
                </div>
                <div className="wall-picker">
                  {Object.values(users).map((u) => (
                    <label key={u.id}
                           className={"small" + (u.id === myId ? " self" : "")}>
                      <input type="checkbox" checked={wallIds.includes(u.id)}
                             onChange={(e) => setWallIds(
                               e.target.checked
                                 ? [...wallIds, u.id]
                                 : wallIds.filter((x) => x !== u.id))}
                             style={{ width: "auto", marginRight: 4 }} />
                      {u.full_name}{u.id === myId && "（你自己）"}
                    </label>
                  ))}
                </div>

                {/* 把自己挡在外面是最容易的误操作，做完就看不到本案了 */}
                {wallIds.includes(myId) && (
                  <label className="small danger-note">
                    <input type="checkbox" checked={selfWallOk}
                           onChange={(e) => setSelfWallOk(e.target.checked)}
                           style={{ width: "auto", marginRight: 6 }} />
                    我确认把<strong>我自己</strong>也隔离 —— 放行后我将立刻看不到本案，
                    需要另一位有 <code>identity.manage_wall</code> 权限的人才能撤销。
                  </label>
                )}
              </div>

              <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
                <button className="btn" onClick={() => decide("waived")} disabled={busy}>放行</button>
                <button className="btn secondary" onClick={() => decide("blocked")} disabled={busy}>
                  {canRedecide ? "维持阻断" : "阻断，不接这个案子"}
                </button>
                <button className="btn secondary" onClick={() => setShowDecide(false)}>取消</button>
              </div>
            </div>
          ) : (
            <div style={{ marginTop: 12 }}>
              <button className="btn" onClick={() => setShowDecide(true)}>
                {canRedecide ? "重新处置" : "管委会处置"}
              </button>
              {canRedecide && (
                <p className="field-hint" style={{ marginTop: 6 }}>
                  本案已阻断。★ 情况变了可以改 —— 对方撤了、征得双方书面同意、
                  查实是同名不同人。<b>改判同样要写理由，同样留痕。</b>
                </p>
              )}
            </div>
          )
        ) : (
          <p className="muted small" style={{ marginTop: 12 }}>
            等待管委会决定。放行/阻断需要 <code>conflict.decide</code> 权限。
          </p>
        )
      )}

      {/* 已生效的隔离墙 —— 必须看得见，否则没人知道是谁在什么时候挡的 */}
      {canManageWall && walls.length > 0 && (
        <div style={{ marginTop: 14 }}>
          <div className="muted small" style={{ marginBottom: 6 }}>
            本案已生效的隔离墙 {walls.length} 堵
          </div>
          <table>
            <tbody>
              {walls.map((w) => (
                <tr key={w.id}>
                  <td style={{ width: 100 }}>
                    {nameOf(users, w.user_id)}
                    {w.user_id === myId && <span className="badge bad">你自己</span>}
                  </td>
                  <td><Tag>{w.mode === "deny" ? "禁止访问" : "白名单"}</Tag></td>
                  <td className="muted small">{w.reason}</td>
                  <td style={{ width: 60, textAlign: "right" }}>
                    <button className="btn ghost sm" onClick={() => dropWall(w)}>撤销</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {history.length > 1 && (
        <details style={{ marginTop: 14 }}>
          <summary className="muted small" style={{ cursor: "pointer" }}>
            历史检查记录（{history.length} 次）—— 每次都留档
          </summary>
          <table style={{ marginTop: 6 }}>
            <tbody>
              {history.map((c) => (
                <tr key={c.id}>
                  <td style={{ width: 100 }}>
                    <Tag tone={CONFLICT_PASSING.includes(c.status) ? "brand" : undefined}>
                      {CONFLICT_STATUS_LABEL[c.status] ?? c.status}
                    </Tag>
                  </td>
                  <td className="muted small">
                    {c.requested_at.slice(0, 16).replace("T", " ")}
                  </td>
                  <td className="muted small">{c.decision_note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </Section>
  );
}

function HitRow({ hit, parties, canDecide, onResolve }: {
  hit: ConflictHit;
  parties: Record<number, Party>;
  canDecide: boolean;
  onResolve: (hitId: number, text: string) => void;
}) {
  // 处置理由内联写，不用 prompt ——
  // 这句话是合规检查要看的东西，得让人能改、能看全，弹窗盛不下
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(hit.resolution);

  return (
    <div className={"hit hit-" + hit.level}>
      <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
        <span className={"badge " + (
          hit.level === "blocked" ? "danger" : hit.level === "review" ? "" : "ok"
        )}>
          {HIT_LEVEL_LABEL[hit.level] ?? hit.level}
        </span>
        <strong style={{ flex: 1 }}>{HIT_KIND_LABEL[hit.kind] ?? hit.kind}</strong>
        {hit.resolved && <span className="badge ok">已处置</span>}
        {canDecide && !editing && (
          <button className="btn ghost sm" onClick={() => setEditing(true)}>
            {hit.resolved ? "改理由" : "处置"}
          </button>
        )}
      </div>
      <div className="small" style={{ marginTop: 4 }}>{hit.description}</div>
      <div className="muted small" style={{ marginTop: 2 }}>
        涉及主体：{nameOfParty(parties, hit.subject_party_id)}
        {hit.matched_party_id && ` ↔ ${nameOfParty(parties, hit.matched_party_id)}`}
        {hit.related_case_id && ` · 关联案件 #${hit.related_case_id}`}
      </div>
      {hit.resolution && !editing && (
        <div className="muted small" style={{ marginTop: 2 }}>
          处置：{hit.resolution}
        </div>
      )}

      {editing && (
        <div style={{ marginTop: 6 }}>
          <textarea rows={2} value={text} onChange={(e) => setText(e.target.value)}
                    placeholder="这条为什么可以不构成障碍？写清楚"
                    style={{ width: "100%", font: "inherit" }} />
          <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
            <button className="small" disabled={!text.trim()}
                    onClick={() => { onResolve(hit.id, text.trim()); setEditing(false); }}>
              保存
            </button>
            <button className="btn ghost sm"
                    onClick={() => { setText(hit.resolution); setEditing(false); }}>
              取消
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function nameOf(users: Record<number, User>, id: number | null): string {
  if (!id) return "系统";
  return users[id]?.full_name ?? `用户${id}`;
}

function nameOfParty(parties: Record<number, Party>, id: number): string {
  return parties[id]?.name ?? `主体${id}`;
}
