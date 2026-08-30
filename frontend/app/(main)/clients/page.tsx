"use client";

import { useEffect, useState } from "react";

import {
  fetchClients, fetchClientDetail, fetchParties, fetchSimilarParties,
  createParty, createClient, addContact, relateParties,
  RELATION_LABEL, CLIENT_STATUS_LABEL,
  type Client, type ClientDetail, type Party,
} from "@/lib/api";
import { useAuth, Can } from "@/lib/auth";
import { DataTable, Notice, Section, Tag, useNotice, type Column } from "@/components/ui";

export default function ClientsPage() {
  const { can } = useAuth();
  const { notice, clear, ok, fail } = useNotice();

  const [clients, setClients] = useState<Client[]>([]);
  const [parties, setParties] = useState<Party[]>([]);
  const [detail, setDetail] = useState<ClientDetail | null>(null);
  const [keyword, setKeyword] = useState("");

  // 新建主体
  const [newName, setNewName] = useState("");
  const [newIdNo, setNewIdNo] = useState("");
  const [similar, setSimilar] = useState<Party[]>([]);
  /** 建主体时顺手建成客户 —— 大多数情况就是这个 */
  const [asClient, setAsClient] = useState(true);
  const [newSource, setNewSource] = useState("");
  /** 刚建好但没建成客户的主体，给一个补建入口 */
  const [justMade, setJustMade] = useState<Party | null>(null);

  function reload() {
    fetchClients().then(setClients).catch(fail);
    fetchParties(keyword).then(setParties).catch(fail);
  }
  useEffect(reload, []);
  useEffect(() => { fetchParties(keyword).then(setParties).catch(() => {}); }, [keyword]);

  // 输名称时实时提示重名 —— 库里出现两条同一主体，利冲就形同虚设
  useEffect(() => {
    if (newName.trim().length < 2) { setSimilar([]); return; }
    const t = setTimeout(() => {
      fetchSimilarParties(newName).then(setSimilar).catch(() => setSimilar([]));
    }, 300);
    return () => clearTimeout(t);
  }, [newName]);

  /**
   * ★ **建完主体要接着问"要不要建成客户"。**
   *
   *   主体（当事人库）和客户（有委托关系的）是两个东西 —— 对方当事人、
   *   关联公司都要进主体库，但他们不是客户。
   *
   *   可这个区别只有我知道：用户点了"新建"，填了名字，成功了，
   *   然后**客户表一行没多** —— 他只会认为保存失败了。
   *   第一版这里就到 reload() 为止，`createClient` 从头到尾没人调用过，
   *   所以客户表里永远只有种子数据。
   */
  async function doCreateParty(allowSimilar: boolean) {
    try {
      const p = await createParty({
        name: newName, id_no: newIdNo, allow_similar: allowSimilar,
      });
      setNewName(""); setNewIdNo(""); setSimilar([]);
      reload();
      if (asClient && can("crm.manage")) {
        const c = await createClient({ party_id: p.id, source: newSource });
        ok(`已建客户「${p.name}」，编号 ${c.code}`);
        reload();
      } else {
        ok(`已建主体「${p.name}」。` +
           `他还不是客户 —— 主体库存的是所有当事人，包括对方。`);
        setJustMade(p);
      }
    } catch (e) { fail(e); }
  }

  /** 把一个已有主体转成客户 —— 对方当事人后来来找你代理，就走这条 */
  async function makeClient(p: Party) {
    try {
      const c = await createClient({ party_id: p.id });
      ok(`已建客户「${p.name}」，编号 ${c.code}`);
      setJustMade(null);
      reload();
    } catch (e) { fail(e); }
  }

  async function open(c: Client) {
    try { setDetail(await fetchClientDetail(c.id)); } catch (e) { fail(e); }
  }

  // ★ 主体 ≠ 客户。主体库存的是所有当事人（包括对方），
  //   只有签了委托的才是客户。这里挑出还没建成客户的那些。
  const clientPartyIds = new Set(clients.map((c) => c.party_id));
  const orphans = parties.filter((p) => !clientPartyIds.has(p.id));

  const columns: Column<Client>[] = [
    { key: "code", title: "编号", width: 90 },
    { key: "party", title: "客户",
      render: (c) => parties.find((p) => p.id === c.party_id)?.name ?? `主体${c.party_id}` },
    { key: "status", title: "状态", width: 90,
      render: (c) => <Tag>{CLIENT_STATUS_LABEL[c.status] ?? c.status}</Tag> },
    { key: "source", title: "案源", render: (c) => c.source || "—" },
  ];

  return (
    <>
      <Notice text={notice.text} kind={notice.kind} onClose={clear} />

      {/* ★ 两栏用 grid，不是 flex row。
          迁移时 `.col` 被换成了 `.stack`（flex-column），
          于是右栏没有宽度约束、直接换行掉到最下面 ——
          「客户详情」孤零零挂在页面右下角。 */}
      <div className="grid aside">
        <div className="stack">
          <Section title="客户" extra={
            <span className="muted small">只显示你的数据范围内的客户</span>
          }>
            <DataTable columns={columns} rows={clients} onRowClick={open}
                       activeId={detail?.id ?? null}
                       empty={orphans.length > 0
                              ? `范围内还没有客户 —— 但主体库里有 ${orphans.length} 个还没建成客户的，见下方`
                              : "范围内还没有客户"} />
          </Section>

          <Can perm="crm.manage_party">
            <Section title={asClient ? "新建客户" : "新建主体"} extra={
              <span className="muted small">主体库是利冲检查的基本单位</span>
            }>
              {/* ★ 用人话把两个概念的区别摆在建之前，不要等他建完困惑。
                  默认勾上"同时建成客户" —— 大多数人点进这一页就是要加客户。 */}
              <label style={{ display: "flex", gap: 8, alignItems: "center",
                              marginBottom: 10 }}>
                <input type="checkbox" checked={asClient} style={{ width: "auto" }}
                       onChange={(e) => setAsClient(e.target.checked)} />
                <span>同时建成<b>客户</b>（有委托关系的）</span>
              </label>
              <p className="muted small" style={{ marginTop: -4 }}>
                不勾就只进<b>主体库</b>：对方当事人、关联公司都要登记，
                但他们不是客户。利冲检查查的是主体库。
              </p>

              <label>名称</label>
              <input value={newName} onChange={(e) => setNewName(e.target.value)}
                     placeholder="公司全称或个人姓名" />
              <label>统一社会信用代码 / 身份证号（可留空）</label>
              <input value={newIdNo} onChange={(e) => setNewIdNo(e.target.value)} />

              {similar.length > 0 && (
                <div className="alert bad" style={{ marginTop: 12 }}>
                  库里已有近似主体：{similar.map((p) => p.name).join("、")}
                  <div className="small" style={{ marginTop: 6 }}>
                    同一个主体建成两条，利冲就查不出来了。确认不是同一个再继续。
                  </div>
                </div>
              )}

              {asClient && (
                <>
                  <label>案源（谁介绍来的，可留空）</label>
                  <input value={newSource}
                         onChange={(e) => setNewSource(e.target.value)}
                         placeholder="老客户转介 / 官网 / 熟人" />
                </>
              )}

              <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
                <button className="btn" disabled={!newName.trim() || similar.length > 0}
                        onClick={() => doCreateParty(false)}>
                  {asClient ? "建立客户" : "建立主体"}
                </button>
                {similar.length > 0 && (
                  <button className="btn secondary" onClick={() => doCreateParty(true)}>
                    确认不是同一个，仍要建立
                  </button>
                )}
              </div>

              {justMade && can("crm.manage") && (
                <div className="alert" style={{ marginTop: 12 }}>
                  「{justMade.name}」已进主体库，但<b>还不是客户</b>。
                  <div style={{ marginTop: 8, display: "flex", gap: 8 }}>
                    <button className="btn ghost sm"
                            onClick={() => void makeClient(justMade)}>
                      建成客户
                    </button>
                    <button className="btn ghost sm"
                            onClick={() => setJustMade(null)}>知道了</button>
                  </div>
                </div>
              )}
            </Section>
          </Can>

          {/* ★ **躺在主体库里、还不是客户的那些。**

              这一栏是补出来的：原来只在"刚建完"那一刻给了补建入口，
              **已经躺在库里的主体没有任何办法转成客户** ——
              用户之前建的两个主体就卡在这里，
              而界面上没有一个字告诉他为什么客户表里看不到。

              这一栏还有第二个用途：对方当事人后来反过来找你代理，
              他早就在主体库里（利冲查过），直接建成客户就行，别再建一遍。 */}
          {can("crm.manage") && orphans.length > 0 && (
            <Section title={`主体库里还不是客户的 · ${orphans.length}`} extra={
              <span className="muted small">建成客户才会出现在上面</span>
            }>
              <p className="muted small" style={{ marginTop: 0 }}>
                主体库存的是<b>所有当事人</b>，包括对方 —— 他们本来就不该都是客户。
                下面这些如果是你的委托人，点一下补成客户。
              </p>
              <table >
                <tbody>
                  {orphans.slice(0, 20).map((p) => (
                    <tr key={p.id}>
                      <td>{p.name}</td>
                      <td className="muted small">
                        {p.kind === "person" ? "自然人" : "法人/组织"}
                        {p.id_no ? `　${p.id_no}` : ""}
                      </td>
                      <td style={{ width: 96 }}>
                        <button className="btn ghost sm"
                                onClick={() => void makeClient(p)}>
                          建成客户
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {orphans.length > 20 && (
                <p className="muted small">
                  还有 {orphans.length - 20} 个，用上面的搜索框找。
                </p>
              )}
            </Section>
          )}
        </div>

        <div className="stack">
          {!detail && (
            <Section title="客户详情">
              <p className="muted">点左侧任一客户查看详情</p>
            </Section>
          )}

          {detail && (
            <>
              <Section title={detail.party.name}>
                <table>
                  <tbody>
                    <tr><th style={{ width: 90 }}>编号</th><td>{detail.code}</td></tr>
                    <tr><th>类型</th><td>{detail.party.kind === "person" ? "自然人" : "法人/组织"}</td></tr>
                    <tr><th>证件号</th><td>{detail.party.id_no || <span className="muted">未登记</span>}</td></tr>
                    <tr><th>状态</th><td>{CLIENT_STATUS_LABEL[detail.status] ?? detail.status}</td></tr>
                    <tr><th>案源</th><td>{detail.source || "—"}</td></tr>
                  </tbody>
                </table>
              </Section>

              <Section title="关联方" extra={
                <span className="muted small">利冲检查会顺着这些关系往外查一层</span>
              }>
                {detail.related_party_ids.length === 0 && (
                  <p className="muted">尚未登记关联方</p>
                )}
                {detail.related_party_ids.map((pid) => (
                  <div key={pid}>
                    {parties.find((p) => p.id === pid)?.name ?? `主体${pid}`}
                  </div>
                ))}
              </Section>

              <Section title="联系人">
                <table>
                  <tbody>
                    {detail.contacts.map((c) => (
                      <tr key={c.id}>
                        <td>{c.name}{c.is_primary && <Tag tone="brand">主要</Tag>}</td>
                        <td className="muted small">{c.title}</td>
                        <td className="muted small">{c.phone}</td>
                      </tr>
                    ))}
                    {detail.contacts.length === 0 && (
                      <tr><td className="muted">暂无联系人</td></tr>
                    )}
                  </tbody>
                </table>
              </Section>
            </>
          )}
        </div>
      </div>
    </>
  );
}
