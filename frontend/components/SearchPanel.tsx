"use client";

/**
 * 法律检索面板。挂在案件详情页里 ——
 * **检索不是一个独立功能页，是办案过程中的一个动作。**
 *
 * 两种模式，差别很大，界面上必须让律师看明白：
 *
 *   检索  0 成本、0.2 秒、结果确定。直接查 BM25+向量。
 *         ★ 不走改写节点，所以**没有「带本案背景」开关** ——
 *           传了也不影响结果，摆一个假开关比没有更糟。
 *
 *   问答  ¥0.015、约 20 秒。走完整流程：分类→改写→多路检索→判相关
 *         →重排→生成→引用校验。**案件上下文在这条路上才真正生效。**
 */
import { useEffect, useRef, useState } from "react";

import {
  doSearch, askQuestion, pollLog, fetchCaseLogs, retrievalHealth,
  RETRIEVAL_KIND_LABEL,
  type SearchLogDetail, type SearchLog, type RetrievalHealth,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Notice, Section, Tag, useNotice } from "@/components/ui";

type Mode = "search" | "answer";

export function SearchPanel({ caseId }: { caseId: number }) {
  const { can } = useAuth();
  const { notice, clear, fail } = useNotice();

  const [health, setHealth] = useState<RetrievalHealth | null>(null);
  const [mode, setMode] = useState<Mode>("search");
  const [query, setQuery] = useState("");
  const [withContext, setWithContext] = useState(true);
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [result, setResult] = useState<SearchLogDetail | null>(null);
  const [history, setHistory] = useState<SearchLog[]>([]);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  //: 同步的提交闸门，见 run() 里的说明
  const inFlight = useRef(false);

  const canAsk = can("retrieval.ask");

  function reloadHistory() {
    fetchCaseLogs(caseId).then(setHistory).catch(() => {});
  }

  useEffect(() => {
    // 健康检查失败也重试一次 —— 页面刚加载时后端可能还在起
    retrievalHealth().then(setHealth).catch(() => {
      setTimeout(() => {
        retrievalHealth().then(setHealth)
          .catch(() => setHealth({ status: "unavailable" }));
      }, 2500);
    });
    reloadHistory();
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [caseId]);

  async function run() {
    // ★ busy 是 state，要等下一次渲染才为 true。
    //   连按回车（或按住回车触发自动重复）时，几个回调看到的都还是旧的 false，
    //   于是同一个问题被提交好几次 —— 检索历史里就冒出一串重复记录。
    //   同步的 ref 才挡得住。
    if (!query.trim() || inFlight.current) return;
    inFlight.current = true;
    clear();              // 上一次的报错横幅到此为止，别赖在屏幕上
    setBusy(true);
    setResult(null);
    setElapsed(0);
    try {
      if (mode === "search") {
        setResult(await doSearch({ query, case_id: caseId, top_k: 8 }));
      } else {
        const log = await askQuestion({
          kind: "answer", question: query, case_id: caseId,
          top_k: 8, with_context: withContext,
        });
        // 任务化：约 20 秒，轮询等结果
        await new Promise<void>((resolve, reject) => {
          const t0 = Date.now();
          // ★ 轮询要容忍抖动：dev server 重编译、后端重启都会让某一次请求失败。
          //   一次失败就把整个问答判死，等于把 20 秒的活白扔了。
          let misses = 0;
          timer.current = setInterval(async () => {
            setElapsed(Math.round((Date.now() - t0) / 1000));
            try {
              const d = await pollLog(log.id);
              misses = 0;
              if (d.status === "done") {
                setResult(d); cleanup(); resolve();
              } else if (d.status === "failed") {
                cleanup(); reject(new Error(d.error || "检索任务失败"));
              } else if (Date.now() - t0 > 180000) {
                cleanup(); reject(new Error("检索超时（超过 3 分钟）"));
              }
            } catch (e) {
              misses += 1;
              if (misses >= 3) { cleanup(); reject(e); }   // 连着三次才认输
            }
          }, 3000);
          function cleanup() {
            if (timer.current) { clearInterval(timer.current); timer.current = null; }
          }
        });
      }
      reloadHistory();
    } catch (e) { fail(e); } finally { inFlight.current = false; setBusy(false); }
  }

  async function openHistory(log: SearchLog) {
    try { setResult(await pollLog(log.id)); } catch (e) { fail(e); }
  }

  const unavailable = health?.status === "unavailable";

  return (
    <Section title="法律检索" collapsible rememberKey={`search-${caseId}`}
             defaultOpen={false}
             extra={
      <span className="muted small">
        {unavailable
          ? <span style={{ color: "var(--err)" }}>检索服务未启动</span>
          : health?.corpus_version
            ? `语料 ${health.corpus_version} · ${health.model ?? ""}`
            : "…"}
      </span>
    }>
      <Notice text={notice.text} kind={notice.kind} onClose={clear} />

      {unavailable && (
        <div className="alert bad">
          法律检索服务未启动。在 <code>langchain-legal-RAG</code> 目录下运行：
          <br /><code>uvicorn src.service:app --port 8100</code>
        </div>
      )}

      {/* 模式：两种代价差一个数量级，必须让律师看明白 */}
      <div className="mode-row">
        <button className={mode === "search" ? "" : "ghost"}
                onClick={() => setMode("search")}>
          检索 <span className="small">即时 · 免费</span>
        </button>
        <button className={mode === "answer" ? "" : "ghost"}
                disabled={!canAsk}
                title={canAsk ? undefined : "需要 retrieval.ask 权限"}
                onClick={() => setMode("answer")}>
          问答 <span className="small">约 20 秒 · ¥0.015</span>
        </button>
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            // e.repeat = 按住不放的自动重复，一样会连发
            if (e.key === "Enter" && !e.repeat) run();
          }}
          placeholder={mode === "search"
            ? "查法条或判例，如：建工解释一 第十四条"
            : "问一个问题，如：完工未验收但已实际使用，能否主张工程款？"}
          disabled={busy || unavailable}
        />
        <button className="btn" onClick={run} disabled={busy || unavailable || !query.trim()}
                style={{ whiteSpace: "nowrap" }}>
          {busy ? (mode === "answer" ? `分析中 ${elapsed}s` : "检索中…") : "开始"}
        </button>
      </div>

      {mode === "answer" ? (
        <label className="small" style={{ marginTop: 8 }}>
          <input type="checkbox" checked={withContext}
                 onChange={(e) => setWithContext(e.target.checked)}
                 style={{ width: "auto", marginRight: 6 }} />
          带本案背景（案由、案情、对方当事人）
          <span className="muted"> —— 关掉可对照效果</span>
        </label>
      ) : (
        <p className="muted small" style={{ marginTop: 8 }}>
          纯检索直接查库，不经过模型，因此**案件背景在这条路上不生效**。
          要让案情影响结果，切到「问答」。
        </p>
      )}

      {result && <ResultView result={result} />}

      {history.length > 0 && (
        <div style={{ marginTop: 18 }}>
          <div className="muted small" style={{ marginBottom: 6 }}>本案检索历史</div>
          <table>
            <tbody>
              {history.slice(0, 8).map((h) => (
                <tr key={h.id} style={{ cursor: "pointer" }} onClick={() => openHistory(h)}>
                  <td style={{ width: 66 }}>
                    <Tag>{RETRIEVAL_KIND_LABEL[h.kind] ?? h.kind}</Tag>
                  </td>
                  <td className="small">{h.question.slice(0, 44)}</td>
                  <td className="muted small" style={{ width: 90, textAlign: "right" }}>
                    {h.status === "done" ? h.created_at.slice(5, 16).replace("T", " ")
                                         : h.status}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

function ResultView({ result }: { result: SearchLogDetail }) {
  return (
    <div style={{ marginTop: 16 }}>
      {/* 引用校验告警：编造条号、查无此案、已废止 —— 必须显眼 */}
      {result.warnings.length > 0 && (
        <div className="alert bad">
          <strong>引用校验发现问题：</strong>
          <ul style={{ margin: "6px 0 0 18px" }}>
            {result.warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </div>
      )}

      {result.answer && (
        <div className="answer-box">
          <div className="muted small" style={{ marginBottom: 6 }}>
            答复 · {result.intent}
            {result.cost_cny > 0 && ` · ¥${result.cost_cny.toFixed(4)}`}
          </div>
          <div style={{ whiteSpace: "pre-wrap" }}>{result.answer}</div>
        </div>
      )}

      {result.queries.length > 1 && (
        <details style={{ marginTop: 10 }}>
          <summary className="muted small" style={{ cursor: "pointer" }}>
            它实际查了什么（{result.queries.length} 条检索式）
          </summary>
          <ul className="muted small" style={{ margin: "6px 0 0 18px" }}>
            {result.queries.map((q, i) => <li key={i}>{q}</li>)}
          </ul>
        </details>
      )}

      <div style={{ marginTop: 12 }}>
        <div className="muted small" style={{ marginBottom: 6 }}>
          引用 {result.citations.length} 条
          {result.answer && "（★ 表示答复实际用到）"}
        </div>
        {result.citations.map((c) => <CitationRow key={c.ordinal} c={c} />)}
        {result.citations.length === 0 && (
          <p className="muted small">没有命中。换个说法试试，或切到「问答」让它改写检索式。</p>
        )}
      </div>

      <p className="muted small" style={{ marginTop: 10 }}>
        语料版本 {result.corpus_version}
        {result.trace_id && ` · trace ${result.trace_id.slice(0, 8)}`}
      </p>
    </div>
  );
}

function CitationRow({ c }: { c: SearchCitationRow }) {
  const [open, setOpen] = useState(false);
  // 时效状态是律师最关心的：引到已废止的条文是事故
  const stale = c.effective_status.includes("失效") || c.effective_status.includes("废止");
  const unknown = c.effective_status.includes("未标注");

  return (
    <div className="citation" onClick={() => setOpen(!open)}>
      <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
        <span className="muted small" style={{ width: 24 }}>
          {c.used ? "★" : ""}S{c.ordinal}
        </span>
        <strong style={{ flex: 1 }}>{c.source_id}</strong>
        <span className={"badge" + (stale ? " danger" : unknown ? "" : " ok")}>
          {c.effective_status || "未标注"}
        </span>
      </div>
      <div className={open ? "" : "clip"} style={{ marginLeft: 32, marginTop: 4 }}>
        <span className="muted small">{c.snippet}</span>
      </div>
    </div>
  );
}

type SearchCitationRow = SearchLogDetail["citations"][number];
