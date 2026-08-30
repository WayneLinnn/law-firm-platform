"use client";

/**
 * 案卷（Step 11）。
 *
 * 界面上必须让律师看清三件事：
 *
 *   ① 案卷里有什么          → 按卷宗分类列出，带版本号
 *   ② ★ **哪些搜不到**       → 扫描件/图片单独标出来
 *   ③ 每一版是谁什么时候传的 → 展开看版本历史，都能下载
 *
 * ★ ② 是这个面板最容易被做丢、也最要紧的东西。律所案卷里全是扫描件，
 *   如果它们在检索里静默缺席，律师搜不到时会以为「案卷里没有这份材料」——
 *   但它明明躺在那儿。**搜不到比搜错更危险，因为搜不到不会引起怀疑。**
 */
import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchFolders, fetchDocs, fetchDoc, fetchDocStats, fetchUnsearchable,
  searchDocs, uploadDoc, downloadVersion, renameDoc, deleteDoc, restoreDoc,
  TEXT_STATUS_LABEL,
  type DocRow, type DocDetail, type DocHit, type Unsearchable, type User,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Notice, Section, Tag, useNotice } from "@/components/ui";

function humanSize(n: number): string {
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  return (n / 1024 / 1024).toFixed(1) + " MB";
}

export function DocPanel({ caseId, users }: {
  caseId: number;
  users: Record<number, User>;
}) {
  const { can } = useAuth();
  const { notice, clear, ok, fail } = useNotice();

  const [folders, setFolders] = useState<string[]>([]);
  const [docs, setDocs] = useState<DocRow[]>([]);
  const [blind, setBlind] = useState<Unsearchable[]>([]);
  const [stats, setStats] = useState({ documents: 0, bytes: 0, unsearchable: 0 });
  const [open, setOpen] = useState<DocDetail | null>(null);
  const [keyword, setKeyword] = useState("");
  const [hits, setHits] = useState<DocHit[] | null>(null);
  const [folder, setFolder] = useState("其他");
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const canUpload = can("doc.upload");
  const canManage = can("doc.manage");
  const canDelete = can("doc.delete");

  const reload = useCallback(() => {
    fetchDocs(caseId).then(setDocs).catch(() => {});
    fetchUnsearchable(caseId).then(setBlind).catch(() => {});
    fetchDocStats(caseId).then(setStats).catch(() => {});
  }, [caseId]);

  useEffect(() => {
    fetchFolders().then(setFolders).catch(() => {});
  }, []);
  useEffect(() => {
    setOpen(null); setHits(null); setKeyword("");
    reload();
  }, [caseId, reload]);

  async function doUpload(file: File, documentId?: number) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      const d = await uploadDoc({
        case_id: caseId, file, folder,
        title: documentId ? undefined : file.name,
        document_id: documentId,
      });
      setOpen(d);
      const last = d.versions[0];
      if (last && last.text_status !== "ok") {
        // ★ 传上来了但搜不到，必须当场说清楚
        ok(`已存入案卷 v${last.version}，但${TEXT_STATUS_LABEL[last.text_status] ?? ""}：${last.text_note}`);
      } else {
        ok(`已存入案卷 v${last?.version ?? 1}`);
      }
      reload();
    } catch (e) { fail(e); } finally { inFlight.current = false; setBusy(false); }
  }

  async function runSearch() {
    if (keyword.trim().length < 2) { fail(new Error("关键词至少两个字")); return; }
    try {
      setHits(await searchDocs(keyword.trim(), caseId));
    } catch (e) { fail(e); }
  }

  async function move(doc: DocRow, to: string) {
    try {
      await renameDoc(doc.id, { folder: to });
      ok(`已移到「${to}」`);
      reload();
      if (open?.id === doc.id) setOpen(await fetchDoc(doc.id));
    } catch (e) { fail(e); }
  }

  async function remove(doc: DocRow) {
    try {
      await deleteDoc(doc.id);
      ok("已删除（软删，文件本体和记录都还在）");
      setOpen(null);
      reload();
    } catch (e) { fail(e); }
  }

  const byFolder: Record<string, DocRow[]> = {};
  for (const d of docs) (byFolder[d.folder] ||= []).push(d);

  return (
    <Section title="案卷" collapsible rememberKey={`doc-${caseId}`}
             extra={
      <span className="muted small">
        {stats.documents} 份 · {humanSize(stats.bytes)}
        {stats.unsearchable > 0 && (
          <span style={{ color: "var(--warn)" }}> · {stats.unsearchable} 份搜不到</span>
        )}
      </span>
    }>
      <Notice text={notice.text} kind={notice.kind} onClose={clear} />

      {/* 上传 */}
      {canUpload && (
        <div className="doc-upload">
          <select value={folder} onChange={(e) => setFolder(e.target.value)}
                  style={{ width: 130 }}>
            {folders.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
          <input ref={fileRef} type="file" style={{ display: "none" }}
                 onChange={(e) => {
                   const f = e.target.files?.[0];
                   if (f) doUpload(f);
                   e.target.value = "";
                 }} />
          <button className="btn secondary" disabled={busy}
                  onClick={() => fileRef.current?.click()}>
            {busy ? "上传中…" : "上传文件"}
          </button>
          <span className="muted small">
            存进对象存储，库里只留元数据和正文
          </span>
        </div>
      )}

      {/* ★ 搜不到的文件 —— 这块不能省 */}
      {blind.length > 0 && (
        <div className="alert">
          <strong>这 {blind.length} 份文件全文检索搜不到</strong>
          <p className="small" style={{ margin: "4px 0 8px" }}>
            文件本身已经存好了，但里面提取不出文字。
            <b>搜不到不等于案卷里没有</b> —— 查这几份要靠人翻。
          </p>
          {blind.map((b) => (
            <div key={b.document_id} className="small" style={{ marginBottom: 3 }}>
              · {b.title}
              <span className="badge" style={{ margin: "0 6px" }}>
                {TEXT_STATUS_LABEL[b.text_status] ?? b.text_status}
              </span>
              <span className="muted">{b.reason}</span>
            </div>
          ))}
        </div>
      )}

      {/* 全文检索 */}
      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <input value={keyword} onChange={(e) => setKeyword(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter" && !e.repeat) runSearch(); }}
               placeholder="在本案案卷正文里搜，至少两个字" />
        <button className="btn secondary" onClick={runSearch} style={{ whiteSpace: "nowrap" }}>
          搜案卷
        </button>
        {hits !== null && (
          <button className="btn secondary" onClick={() => { setHits(null); setKeyword(""); }}>
            清除
          </button>
        )}
      </div>

      {hits !== null && (
        <div style={{ marginTop: 10 }}>
          <div className="muted small" style={{ marginBottom: 6 }}>
            命中 {hits.length} 份
            {blind.length > 0 && ` · 另有 ${blind.length} 份搜不到（见上）`}
          </div>
          {hits.map((h) => (
            <div key={h.document_id} className="citation"
                 onClick={() => fetchDoc(h.document_id).then(setOpen).catch(fail)}>
              <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                <strong style={{ flex: 1 }}>{h.title}</strong>
                <Tag>{h.folder}</Tag>
                <span className="muted small">v{h.version}</span>
              </div>
              <div className="muted small" style={{ marginTop: 3 }}>{h.snippet}</div>
            </div>
          ))}
          {hits.length === 0 && (
            <p className="muted small">
              没搜到。注意上面那批搜不到的文件 —— 它可能就在里面。
            </p>
          )}
        </div>
      )}

      {/* 按卷宗列出 */}
      {hits === null && (
        <div style={{ marginTop: 12 }}>
          {docs.length === 0 && (
            <p className="muted small" style={{ margin: 0 }}>
              本案还没有案卷材料。流程终审通过的意见书会自动存到这里。
            </p>
          )}
          {Object.keys(byFolder).map((f) => (
            <div key={f} style={{ marginBottom: 12 }}>
              <div className="muted small" style={{ marginBottom: 5 }}>
                {f}（{byFolder[f].length}）
              </div>
              {byFolder[f].map((d) => (
                <div key={d.id} className="docrow"
                     onClick={() => fetchDoc(d.id).then(setOpen).catch(fail)}>
                  <span style={{ flex: 1 }}>{d.title}</span>
                  <span className="muted small">v{d.current_version}</span>
                  <span className="muted small">
                    {d.updated_at.slice(5, 10)}
                  </span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {/* 详情 */}
      {open && (
        <div className="decide-box">
          <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
            <strong style={{ flex: 1 }}>{open.title}</strong>
            {open.deleted && <span className="badge bad">已删除</span>}
            <button className="btn ghost sm" onClick={() => setOpen(null)}>关闭</button>
          </div>

          {canManage && !open.deleted && (
            <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
              <span className="muted small" style={{ alignSelf: "center" }}>移到：</span>
              {folders.filter((f) => f !== open.folder).map((f) => (
                <button key={f} className="btn ghost sm" onClick={() => move(open, f)}>
                  {f}
                </button>
              ))}
            </div>
          )}

          <div className="muted small" style={{ marginTop: 10, marginBottom: 6 }}>
            版本历史 —— <b>只增不改</b>，旧版本永远留着
          </div>
          <table>
            <tbody>
              {open.versions.map((v) => (
                <tr key={v.id}>
                  <td style={{ width: 40 }}>v{v.version}</td>
                  <td className="small">{v.filename}</td>
                  <td style={{ width: 76 }}>
                    <span className={"badge" + (v.text_status === "ok" ? " ok" : "")}>
                      {TEXT_STATUS_LABEL[v.text_status] ?? v.text_status}
                    </span>
                  </td>
                  <td className="muted small" style={{ width: 70 }}>{humanSize(v.size)}</td>
                  <td className="muted small" style={{ width: 90 }}>
                    {users[v.uploaded_by]?.full_name ?? `用户${v.uploaded_by}`}
                  </td>
                  <td className="muted small" style={{ width: 84 }}>
                    {v.uploaded_at.slice(5, 16).replace("T", " ")}
                  </td>
                  <td style={{ width: 54, textAlign: "right" }}>
                    <button className="btn ghost sm"
                            onClick={() => downloadVersion(v.id, v.filename).catch(fail)}>
                      下载
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {open.versions.some((v) => v.text_status !== "ok") && (
            <p className="small" style={{ color: "var(--warn)", marginTop: 6 }}>
              ⚠ {open.versions.find((v) => v.text_status !== "ok")?.text_note}
            </p>
          )}

          <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
            {canUpload && !open.deleted && (
              <>
                <input type="file" id={`ver-${open.id}`} style={{ display: "none" }}
                       onChange={(e) => {
                         const f = e.target.files?.[0];
                         if (f) doUpload(f, open.id);
                         e.target.value = "";
                       }} />
                <button className="btn secondary" disabled={busy}
                        onClick={() => document.getElementById(`ver-${open.id}`)?.click()}>
                  上传新版本
                </button>
              </>
            )}
            {canDelete && !open.deleted && (
              <button className="btn secondary" onClick={() => remove(open)}>删除</button>
            )}
            {canDelete && open.deleted && (
              <button className="btn secondary" onClick={async () => {
                try {
                  await restoreDoc(open.id);
                  ok("已恢复"); setOpen(await fetchDoc(open.id)); reload();
                } catch (e) { fail(e); }
              }}>恢复</button>
            )}
          </div>
        </div>
      )}
    </Section>
  );
}
