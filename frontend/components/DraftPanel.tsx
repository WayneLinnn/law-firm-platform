"use client";

/**
 * 文书生成（Step 12）。
 *
 * 界面要把两件事摆在明处：
 *
 *   ① **哪一节是填出来的、哪一节是模型写的、哪一节是你自己改的**
 *      —— 律师签字前有权知道每段话的来路
 *   ② ★ **引用校验的告警**，尤其是「这些是编的」
 *      —— 定稿会被硬拦，但要让他在写的时候就看见
 *
 * 当事人、法院、证据清单这类是**自动填充**的，界面上标出来，
 * 提醒律师这些不是模型猜的 —— 反过来也提醒他：模型写的那几节要逐字看。
 */
import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchDraftTemplates, fetchCaseDrafts, fetchDraft, createDraft,
  generateSection, editSection, draftText, finalizeDraft, voidDraft,
  fetchCiteSources, fetchCiteTrace,
  DRAFT_STATUS_LABEL, SOURCE_LABEL,
  type DraftRow, type DraftDetail, type DraftTemplate, type DraftSection,
  type CiteSource, type CiteTrace,
  recheckCitations,
  type Recheck,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Notice, Section, Tag, useNotice } from "@/components/ui";
import { CitationView } from "@/components/CitationView";

export function DraftPanel({ caseId }: { caseId: number }) {
  const { can } = useAuth();
  const { notice, clear, ok, fail } = useNotice();

  const [templates, setTemplates] = useState<DraftTemplate[]>([]);
  const [rows, setRows] = useState<DraftRow[]>([]);
  const [open, setOpen] = useState<DraftDetail | null>(null);
  const [picking, setPicking] = useState(false);
  const [busy, setBusy] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [buffer, setBuffer] = useState("");
  const [preview, setPreview] = useState<string | null>(null);
  const [tab, setTab] = useState<"edit" | "cite" | "check">("edit");
  const [recheck, setRecheck] = useState<Recheck | null>(null);
  const [checking, setChecking] = useState(false);
  /** 体检结果属于哪一份文书 —— 见下面那个 useEffect */
  const [checkedId, setCheckedId] = useState<number | null>(null);
  //: 编号 → 引用 id。★ 为了让正文里的【S1】直接点得动 ——
  //  看到一个引用标记却点不了，是最反直觉的事
  const [citeIds, setCiteIds] = useState<Record<number, number>>({});
  const [trace, setTrace] = useState<CiteTrace | null>(null);
  const inFlight = useRef(false);

  const canCreate = can("draft.create");
  const canFinalize = can("draft.finalize");

  const reload = useCallback(() => {
    fetchCaseDrafts(caseId).then(setRows).catch(() => {});
  }, [caseId]);

  useEffect(() => { fetchDraftTemplates().then(setTemplates).catch(() => {}); }, []);

  // ★ 换了文书就把体检结果清掉。
  //
  //   第一版只写了 `if (!recheck) 才拉`，于是换一份文书之后，
  //   面板上显示的还是**上一份**的结论 —— 一份有 3 条法条改动的文书，
  //   会显示成"全部未变化"。
  //   **一个显示错结论的体检，比不体检危险得多。**
  useEffect(() => {
    if (open?.id !== checkedId) {
      setRecheck(null);
      setCheckedId(null);
    }
  }, [open?.id, checkedId]);

  // 文书一打开就把「编号 → 引用 id」备好，正文里的【S1】才点得动
  useEffect(() => {
    setTrace(null);
    if (!open) { setCiteIds({}); return; }
    fetchCiteSources("draft", open.id)
      .then((xs: CiteSource[]) => {
        const m: Record<number, number> = {};
        for (const x of xs) m[x.ordinal] = x.id;
        setCiteIds(m);
      })
      .catch(() => setCiteIds({}));
  }, [open?.id]);

  async function showSource(ordinal: number) {
    const id = citeIds[ordinal];
    if (!id) { fail(new Error(`【S${ordinal}】没有对应的依据 —— 多半是编造的编号`)); return; }
    try { setTrace(await fetchCiteTrace(id)); } catch (e) { fail(e); }
  }
  useEffect(() => {
    setOpen(null); setPicking(false); setEditing(null); setPreview(null);
    reload();
  }, [caseId, reload]);

  async function begin(key: string) {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const d = await createDraft({ case_id: caseId, template_key: key });
      setOpen(d); setPicking(false); reload();
      ok("已建好骨架 —— 能填的已经填好，剩下的逐节生成");
    } catch (e) { fail(e); } finally { inFlight.current = false; }
  }

  async function gen(key: string, force = false) {
    if (!open || inFlight.current) return;
    inFlight.current = true;
    setBusy(key);
    try {
      setOpen(await generateSection(open.id, key, { force }));
      ok(force ? "已覆盖重写" : "已生成");
    } catch (e) {
      const err = e as { code?: string };
      if (err?.code === "draft.would_overwrite") {
        // 律师改过的东西不能一键冲掉 —— 让他再确认一次
        if (window.confirm("这一节你手工改过。重新生成会覆盖掉你的修改，确定吗？")) {
          inFlight.current = false;
          return gen(key, true);
        }
      } else { fail(e); }
    } finally { inFlight.current = false; setBusy(""); }
  }

  async function save(key: string) {
    if (!open) return;
    try {
      setOpen(await editSection(open.id, key, buffer));
      setEditing(null);
      ok("已保存（引用已重新校验）");
    } catch (e) { fail(e); }
  }

  async function done() {
    if (!open) return;
    try {
      const d = await finalizeDraft(open.id, "律师定稿");
      setOpen(d); reload();
      ok("已定稿并归档进案卷「诉讼文书」");
    } catch (e) { fail(e); }
  }

  const fabricated = (open?.warnings || []).filter((w) => w.includes("这些是编的"));

  return (
    <Section title="文书" collapsible rememberKey={`draft-${caseId}`}
             count={rows.length}
             extra={
      canCreate && !open && !picking && (
        <button className="btn secondary" onClick={() => setPicking(true)}>起草文书</button>
      )
    }>
      <Notice text={notice.text} kind={notice.kind} onClose={clear} />

      {/* 选模板 */}
      {picking && (
        <div className="decide-box">
          <div className="muted small" style={{ marginBottom: 8 }}>
            当事人、法院、证据清单会<b>直接从案件和案卷里填</b>，不过模型 ——
            这些东西让模型猜，猜错一个字立案就退回。
          </div>
          {templates.map((t) => (
            <div key={t.key} className="tplrow" onClick={() => begin(t.key)}>
              <strong style={{ flex: 1 }}>{t.label}</strong>
              <span className="muted small">{t.whenever}</span>
            </div>
          ))}
          <button className="btn secondary" style={{ marginTop: 8 }}
                  onClick={() => setPicking(false)}>取消</button>
        </div>
      )}

      {!open && !picking && rows.length === 0 && (
        <p className="muted small" style={{ margin: 0 }}>
          本案还没有文书。起草时会自动带上流程检索到的依据，
          每个法律论断都要有出处。
        </p>
      )}

      {/* 文书列表 */}
      {!open && rows.length > 0 && (
        <table>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} style={{ cursor: "pointer" }}
                  onClick={() => fetchDraft(r.id).then(setOpen).catch(fail)}>
                <td style={{ width: 74 }}>
                  <Tag tone={r.status === "final" ? "brand" : undefined}>
                    {DRAFT_STATUS_LABEL[r.status] ?? r.status}
                  </Tag>
                </td>
                <td className="small">{r.title}</td>
                <td className="muted small" style={{ width: 84, textAlign: "right" }}>
                  {r.updated_at.slice(5, 10)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* 文书详情 */}
      {open && (
        <>
          <div className="wf-head">
            <Tag tone={open.status === "final" ? "brand" : undefined}>
              {DRAFT_STATUS_LABEL[open.status] ?? open.status}
            </Tag>
            <strong style={{ flex: 1 }}>{open.title}</strong>
            {Number(open.cost_cny) > 0 && (
              <span className="muted small">已花 ¥{Number(open.cost_cny).toFixed(4)}</span>
            )}
            <button className="btn ghost sm" onClick={() => { setOpen(null); setPreview(null); }}>
              返回
            </button>
          </div>

          {/* ★ 编造的引用，最显眼 */}
          {fabricated.length > 0 && (
            <div className="alert bad">
              <strong>发现编造的依据编号 —— 定稿会被拦住：</strong>
              <ul style={{ margin: "6px 0 0 18px" }}>
                {fabricated.map((w, i) => <li key={i}>{w}</li>)}
              </ul>
            </div>
          )}
          {open.warnings.filter((w) => !w.includes("这些是编的")).length > 0 && (
            <div className="alert">
              <strong>引用校验提醒</strong>
              {open.warnings.filter((w) => !w.includes("这些是编的")).map((w, i) => (
                <div key={i} className="small" style={{ marginTop: 3 }}>· {w}</div>
              ))}
            </div>
          )}

          {/* 起草 / 逐句核对 */}
          <div className="mode-row" style={{ marginTop: 10 }}>
            <button className={tab === "edit" ? "" : "ghost"} onClick={() => { setTab("edit"); setTrace(null); }}>
              起草 <span className="small">分节改</span>
            </button>
            <button className={tab === "cite" ? "" : "ghost"} onClick={() => { setTab("cite"); setTrace(null); }}>
              逐句核对 <span className="small">点句子看出处</span>
            </button>
            {/* ★ 引用体检：法条会修订、会废止，而引用存的是当时的快照。
                草稿放久了尤其要跑一次 —— 系统之前是不会主动说的 */}
            <button className={tab === "check" ? "" : "ghost"}
                    onClick={() => {
                      setTab("check"); setTrace(null);
                      if (!recheck && open) {
                        setChecking(true);
                        const id = open.id;
                        recheckCitations("draft", id)
                          .then((r) => { setRecheck(r); setCheckedId(id); })
                          .catch(fail)
                          .finally(() => setChecking(false));
                      }
                    }}>
              引用体检 <span className="small">法条还是当时那样吗</span>
            </button>
          </div>

          {tab === "check" && (
            <div style={{ marginTop: 10 }}>
              {checking && <p className="muted small">正在跟法条库比对…</p>}
              {!checking && recheck && (
                <>
                  <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                    <Tag tone="ok">未变化 {recheck.ok}</Tag>
                    {recheck.changed > 0 && <Tag tone="warn">原文已改动 {recheck.changed}</Tag>}
                    {recheck.expired > 0 && <Tag tone="bad">已失效 {recheck.expired}</Tag>}
                    {recheck.gone > 0 && <Tag tone="bad">库里找不到 {recheck.gone}</Tag>}
                    {recheck.unknown > 0 && (
                      <Tag tone="muted" title="检索服务不可用，这次没查成">
                        查不了 {recheck.unknown}
                      </Tag>
                    )}
                    <button className="btn ghost sm" disabled={checking}
                            onClick={() => {
                              if (!open) return;
                              setChecking(true);
                              const id = open.id;
                              recheckCitations("draft", id)
                                .then((r) => { setRecheck(r); setCheckedId(id); })
                                .catch(fail)
                                .finally(() => setChecking(false));
                            }}>
                      重新体检
                    </button>
                  </div>

                  {recheck.total === 0 ? (
                    <p className="muted small">这份文书还没有引用。</p>
                  ) : (
                    <table  style={{ marginTop: 8 }}>
                      <tbody>
                        {recheck.items
                          .slice()
                          .sort((a, b) => (a.state === "ok" ? 1 : 0) - (b.state === "ok" ? 1 : 0))
                          .map((it) => (
                          <tr key={it.citation_id}>
                            <td style={{ width: 48 }}>
                              <button type="button" className="citemark"
                                      title={`看 S${it.ordinal} 的出处`}
                                      onClick={() => void showSource(it.ordinal)}>
                                S{it.ordinal}
                              </button>
                            </td>
                            <td style={{ width: 92 }}>
                              <Tag tone={it.state === "ok" ? "ok"
                                          : it.state === "unknown" ? "muted" : "bad"}>
                                {it.state_label}
                              </Tag>
                            </td>
                            <td>
                              {it.title || it.source_id}
                              {it.note && <div className="muted small">{it.note}</div>}
                              {/* ★ 变了要把两边原文都摆出来 ——
                                  只说"变了"，律师没法判断变得要不要紧 */}
                              {it.state === "changed" && (
                                <div className="subrow small" style={{ marginTop: 4 }}>
                                  <div><b>当时：</b>{it.snippet_then.slice(0, 160)}</div>
                                  <div style={{ marginTop: 4 }}>
                                    <b>现在：</b>{it.snippet_now.slice(0, 160)}
                                  </div>
                                </div>
                              )}
                              {it.state === "gone" && it.snippet_then && (
                                <div className="subrow small" style={{ marginTop: 4 }}>
                                  <b>当时冻结的原文（库里已无）：</b>
                                  {it.snippet_then.slice(0, 200)}
                                </div>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}

                  <p className="muted small" style={{ marginTop: 8 }}>
                    ★ 这里<b>只报告不改</b>。要不要换成新版本、还是按行为时法引旧版，
                    是你的判断 —— 系统替你换掉，你会以为还是自己核过的那句。
                  </p>
                </>
              )}
            </div>
          )}

          {tab === "cite" && (
            <div style={{ marginTop: 12 }}>
              <CitationView docKind="draft" docId={open.id} />
            </div>
          )}

          {/* ★ 出处卡**只在起草和体检页签显示**。
              原来它渲染在页签之外 —— 切到「逐句核对」时它还挂着，
              再点一句话，CitationView 又弹一张自己的，
              **屏幕上两张一模一样的卡**。用户看到的是"出两个同样的引用"。 */}
          {trace && tab !== "cite" && (
            <div className={"cite-card" + (trace.stale ? " stale" : "")}>
              <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
                <span className="cite-sup">S{trace.ordinal}</span>
                <strong style={{ flex: 1, minWidth: 150 }}>{trace.source_id}</strong>
                <span className={"badge" + (trace.stale ? " danger" : " ok")}>
                  {trace.effective_status || "未标注"}
                </span>
                <button className="btn ghost sm" onClick={() => setTrace(null)}>关闭</button>
              </div>
              {trace.stale && (
                <p className="small" style={{ color: "var(--err)", margin: "6px 0 0" }}>
                  ⚠ 这条依据已失效/废止，不能再作为出处 —— 请换一条。
                </p>
              )}
              <div className="cite-snippet">{trace.snippet || "（当时没有存下原文片段）"}</div>
              <table style={{ marginTop: 8 }}>
                <tbody>
                  <tr><th style={{ width: 78 }}>为什么查它</th><td className="small">{trace.issue || "—"}</td></tr>
                  <tr><th>检索时间</th><td className="small">
                    {trace.retrieved_at ? trace.retrieved_at.slice(0, 16).replace("T", " ") : "—"}</td></tr>
                  <tr><th>语料版本</th><td className="small mono">{trace.corpus_version || "—"}</td></tr>
                  <tr><th>检索 trace</th><td className="small mono">{trace.trace_id || "—"}</td></tr>
                </tbody>
              </table>
            </div>
          )}

          {/* 分节 */}
          <div style={{ marginTop: 12, display: tab === "edit" ? "block" : "none" }}>
            {open.sections.map((s) => (
              <SectionCard
                key={s.key} s={s} readonly={open.status !== "draft"}
                busy={busy === s.key}
                editing={editing === s.key} buffer={buffer}
                onEdit={() => { setEditing(s.key); setBuffer(s.content); }}
                onBuffer={setBuffer}
                onSave={() => save(s.key)}
                onCancel={() => setEditing(null)}
                onGen={() => gen(s.key)}
                onCite={showSource}
                canCreate={canCreate}
              />
            ))}
          </div>

          {/* 依据清单 */}
          {open.citations.length > 0 && (
            <details style={{ marginTop: 12 }}>
              <summary className="muted small" style={{ cursor: "pointer" }}>
                可引用的依据 {open.citations.length} 条 —— 模型只能引这些
              </summary>
              {open.citations.map((c, i) => (
                <div key={i} className="citation" style={{ marginTop: 6 }}>
                  <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                    <span className="muted small" style={{ width: 30 }}>S{i + 1}</span>
                    <strong style={{ flex: 1 }}>{c.source_id}</strong>
                    <span className={"badge" + (c.effective_status ? " ok" : "")}>
                      {c.effective_status || "未标注"}
                    </span>
                  </div>
                  <div className="muted small clip" style={{ marginLeft: 30 }}>{c.snippet}</div>
                </div>
              ))}
            </details>
          )}

          {/* 底部动作 */}
          <div style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap" }}>
            <button className="btn secondary"
                    onClick={() => draftText(open.id).then((d) => setPreview(d.text)).catch(fail)}>
              预览全文
            </button>
            {open.status === "draft" && canFinalize && (
              <button className="btn" onClick={done} disabled={!open.ready}
                      title={open.ready ? undefined : open.blocking.join("；")}>
                定稿并归档
              </button>
            )}
            {open.status === "draft" && canCreate && (
              <button className="btn secondary" onClick={async () => {
                try { await voidDraft(open.id); ok("已作废"); setOpen(null); reload(); }
                catch (e) { fail(e); }
              }}>作废</button>
            )}
            {open.document_id && (
              <span className="muted small" style={{ alignSelf: "center" }}>
                已归档为案卷文档 #{open.document_id}
              </span>
            )}
          </div>

          {!open.ready && open.status === "draft" && (
            <p className="muted small" style={{ marginTop: 6 }}>
              还不能定稿：{open.blocking.join("；")}
            </p>
          )}

          {preview !== null && (
            <div className="answer-box" style={{ marginTop: 12, whiteSpace: "pre-wrap" }}>
              {preview}
            </div>
          )}
        </>
      )}
    </Section>
  );
}

/** 把正文里的【S3】渲染成可点的标记，其余原样输出。
 *
 *  ★ 之前这里是纯文本 —— 律师看到一个引用编号却点不动，只能切到
 *    另一个页签去找，完全反直觉。看到什么就该能点什么。
 */
function renderWithCites(text: string, onCite: (o: number) => void) {
  const out: React.ReactNode[] = [];
  const re = /【S(\d+)】/g;
  let last = 0, m: RegExpExecArray | null, k = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(<span key={k++}>{text.slice(last, m.index)}</span>);
    const o = Number(m[1]);
    out.push(
      <button key={k++} type="button" className="citemark"
              title={`看 S${o} 的出处`}
              onClick={(e) => { e.stopPropagation(); onCite(o); }}>
        S{o}
      </button>
    );
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(<span key={k++}>{text.slice(last)}</span>);
  return out;
}


function SectionCard({ s, readonly, busy, editing, buffer, canCreate,
                       onEdit, onBuffer, onSave, onCancel, onGen, onCite }: {
  s: DraftSection; readonly: boolean; busy: boolean; editing: boolean;
  buffer: string; canCreate: boolean;
  onEdit: () => void; onBuffer: (v: string) => void;
  onSave: () => void; onCancel: () => void; onGen: () => void;
  onCite: (ordinal: number) => void;
}) {
  const bad = s.warnings.some((w) => w.includes("这些是编的"));
  return (
    <div className={"dsec" + (bad ? " bad" : "")}>
      <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
        <strong style={{ flex: 1, minWidth: 120 }}>
          {s.title}{s.required && <span className="muted"> ·必需</span>}
        </strong>
        <span className={"badge" + (s.source === "fill" ? " ok" : "")}>
          {SOURCE_LABEL[s.source] ?? s.source}
        </span>
        {s.edited && <span className="badge">你改过</span>}
        {/* ★ 这里原来是纯文本「引用 S1、S2」。
            正文里的【S1】早就能点了，但**只有正文里出现过的编号能点** ——
            这份文书 6 条引用里只有 S1 写进了正文，
            于是用户看到 S2 却点不动它，以为是坏了。
            **屏幕上每一个 S 编号都该能点开。** */}
        {s.used.length > 0 && (
          <span className="muted small">
            引用{" "}
            {s.used.map((u, i) => (
              <span key={u}>
                {i > 0 && "、"}
                <button type="button" className="citemark"
                        title={`看 S${u} 的出处`}
                        onClick={(e) => { e.stopPropagation(); onCite(u); }}>
                  S{u}
                </button>
              </span>
            ))}
          </span>
        )}
      </div>

      {s.hint && <p className="muted small" style={{ margin: "3px 0 0" }}>{s.hint}</p>}
      {s.note && <p className="small" style={{ margin: "3px 0 0", color: "var(--warn)" }}>
        ⚠ {s.note}
      </p>}

      {editing ? (
        <div style={{ marginTop: 6 }}>
          <textarea rows={8} value={buffer} onChange={(e) => onBuffer(e.target.value)}
                    style={{ width: "100%", font: "inherit" }} />
          <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
            <button className="small" onClick={onSave}>保存</button>
            <button className="btn ghost sm" onClick={onCancel}>取消</button>
          </div>
        </div>
      ) : (
        <>
          {s.content ? (
            <div className="dsec-body">{renderWithCites(s.content, onCite)}</div>
          ) : (
            <p className="muted small" style={{ margin: "6px 0 0" }}>（还是空的）</p>
          )}
          {s.warnings.length > 0 && (
            <div className="small" style={{ marginTop: 5, color: bad ? "var(--err)" : "var(--warn)" }}>
              {s.warnings.map((w, i) => <div key={i}>⚠ {w}</div>)}
            </div>
          )}
          {!readonly && canCreate && (
            <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
              {s.kind !== "manual" && (
                <button className="btn ghost sm" onClick={onGen} disabled={busy}>
                  {busy ? "生成中…" : s.content ? "重新生成" : "生成"}
                </button>
              )}
              <button className="btn ghost sm" onClick={onEdit}>自己改</button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
