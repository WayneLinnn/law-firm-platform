"use client";

/**
 * 引用留痕（Step 13）—— **点文书任一句，看到它的出处。**
 *
 * 这是律师敢在 AI 起草的文书上签字的唯一前提。所以界面上只做三件事，
 * 但每一件都要做透：
 *
 *   ① 每句话按「有没有依据」上色，**没依据的最显眼**
 *      —— 签字前要找的正是那些没出处的论断，不是有出处的
 *   ② 点一句 → 出处、原文片段、时效状态、当时那次检索
 *      —— 律师问「你这条哪来的」，答案得是一条完整的链
 *   ③ 已失效的依据顶到最上面
 *      —— 引一条已废止的条文是事故
 *
 * ★ 「看着像论断但没标依据」是**启发式提示**，措辞必须克制。
 *   说成判定，律师发现几次误报就再也不看这个提示了 ——
 *   那时真正没依据的论断也会被一起忽略。
 */
import { useCallback, useEffect, useState } from "react";

import {
  fetchCiteMap, fetchCiteSources, fetchCiteCoverage, fetchCiteTrace,
  type CitePart, type CiteSource, type CiteCoverage, type CiteTrace,
} from "@/lib/api";
import { Notice, Tag, useNotice } from "@/components/ui";

export function CitationView({ docKind, docId }: { docKind: string; docId: number }) {
  const { notice, clear, fail } = useNotice();
  const [parts, setParts] = useState<CitePart[]>([]);
  const [sources, setSources] = useState<CiteSource[]>([]);
  const [cov, setCov] = useState<CiteCoverage | null>(null);
  const [picked, setPicked] = useState<CiteTrace | null>(null);
  const [note, setNote] = useState("");

  const load = useCallback(() => {
    fetchCiteMap(docKind, docId).then(setParts).catch(() => {});
    fetchCiteSources(docKind, docId).then(setSources).catch(() => {});
    fetchCiteCoverage(docKind, docId).then(setCov).catch(() => {});
  }, [docKind, docId]);

  useEffect(() => { setPicked(null); setNote(""); load(); }, [load]);

  const byOrdinal: Record<number, CiteSource> = {};
  for (const s of sources) byOrdinal[s.ordinal] = s;

  async function openSentence(ords: number[], looksLegal: boolean) {
    setPicked(null);
    if (ords.length === 0) {
      // ★ 没依据也要给个明确答复，不能点了没反应
      setNote(looksLegal
        ? "这句话没有标注依据，而且看着像在下法律结论 —— 建议核对后补上出处，或改成事实陈述。"
        : "这句话没有标注依据。如果它只是陈述事实，可以不引；如果是法律论断，就该有出处。");
      return;
    }
    setNote("");
    try {
      const s = byOrdinal[ords[0]];
      if (s) setPicked(await fetchCiteTrace(s.id));
    } catch (e) { fail(e); }
  }

  const hasAny = parts.some((p) => p.sentences.length > 0);

  return (
    <div className="cite-view">
      <Notice text={notice.text} kind={notice.kind} onClose={clear} />

      {cov && (
        <div className="cite-bar">
          <span><b>{cov.sentences}</b> 句</span>
          <span className="ok">有出处 <b>{cov.cited}</b></span>
          {cov.flagged > 0 && <span className="warn">待核对 <b>{cov.flagged}</b></span>}
          <span className="muted">依据 {cov.sources} 条</span>
          {cov.stale.length > 0 && (
            <span className="bad">
              ⚠ 已失效 {cov.stale.map((o) => "S" + o).join("、")}
            </span>
          )}
          {cov.unused.length > 0 && (
            <span className="muted">
              未用上 {cov.unused.map((o) => "S" + o).join("、")}
            </span>
          )}
        </div>
      )}

      {!hasAny && (
        <p className="muted small" style={{ margin: 0 }}>
          文书还没有正文。生成几节之后再回来看。
        </p>
      )}

      {parts.filter((p) => p.sentences.length > 0).map((p) => (
        <div key={p.key} style={{ marginBottom: 12 }}>
          <div className="muted small" style={{ marginBottom: 4 }}>{p.title}</div>
          <p className="cite-para">
            {p.sentences.map((s) => (
              <span
                key={s.index}
                className={
                  "cite-s" +
                  (s.citations.length ? " has" : s.looks_legal ? " flag" : "")
                }
                title={s.citations.length
                  ? "引用 " + s.citations.map((o) => "S" + o).join("、") + " —— 点开看出处"
                  : s.looks_legal ? "像法律论断但没标依据 —— 点开看说明" : "无依据"}
                onClick={() => openSentence(s.citations, s.looks_legal)}
              >
                {(s.segments && s.segments.length ? s.segments : [{ t: s.plain }])
                  .map((g, i) =>
                    g.cite !== undefined
                      ? <sup key={i} className="cite-sup">S{g.cite}</sup>
                      : <span key={i}>{g.t}</span>
                  )}
              </span>
            ))}
          </p>
        </div>
      ))}

      {note && <div className="alert"><span className="small">{note}</span></div>}

      {/* ★ 出处：律师问「你这条哪来的」时的答案 */}
      {picked && (
        <div className={"cite-card" + (picked.stale ? " stale" : "")}>
          <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
            <span className="cite-sup">S{picked.ordinal}</span>
            <strong style={{ flex: 1, minWidth: 160 }}>{picked.source_id}</strong>
            <span className={"badge" + (picked.stale ? " danger" : " ok")}>
              {picked.effective_status || "未标注"}
            </span>
            <button className="btn ghost sm" onClick={() => setPicked(null)}>关闭</button>
          </div>

          {picked.stale && (
            <p className="small" style={{ color: "var(--err)", margin: "6px 0 0" }}>
              ⚠ 这条依据已失效/废止，不能再作为出处 —— 请换一条。
            </p>
          )}

          <div className="cite-snippet">{picked.snippet || "（当时没有存下原文片段）"}</div>

          <table style={{ marginTop: 8 }}>
            <tbody>
              <tr><th style={{ width: 78 }}>为什么查它</th>
                  <td className="small">{picked.issue || "—"}</td></tr>
              <tr><th>检索时间</th>
                  <td className="small">
                    {picked.retrieved_at ? picked.retrieved_at.slice(0, 16).replace("T", " ") : "—"}
                  </td></tr>
              <tr><th>语料版本</th>
                  <td className="small mono">{picked.corpus_version || "—"}</td></tr>
              <tr><th>检索 trace</th>
                  <td className="small mono">{picked.trace_id || "—"}</td></tr>
            </tbody>
          </table>
          <p className="muted small" style={{ marginTop: 6 }}>
            语料版本和 trace 是为了<b>库重建之后还能还原</b>：
            半年后条文改版了，也说得清当时依据的是哪一版。
          </p>

          {picked.used_in.length > 1 && (
            <div className="muted small" style={{ marginTop: 6 }}>
              这条在文书里用了 {picked.used_in.length} 处
            </div>
          )}
        </div>
      )}
    </div>
  );
}
