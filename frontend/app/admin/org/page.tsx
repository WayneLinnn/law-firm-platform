"use client";

import { useEffect, useMemo, useState } from "react";

import {
  fetchOrgUnits, fetchUsers, fetchMembers, fetchUserPositions,
  KIND_LABEL, type OrgUnit, type User, type Membership, type Position,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Badge, Button, DataTable, Field, Notice, Section, Tag, type Column, useNotice } from "@/components/ui";

export default function OrgPage() {
  const { me } = useAuth();
  const { notice, clear, fail } = useNotice();

  const [units, setUnits] = useState<OrgUnit[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [positions, setPositions] = useState<Record<string, Position | null>>({});
  const [members, setMembers] = useState<Membership[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [at, setAt] = useState("");            // 时间点：留空 = 今天
  const [subtree, setSubtree] = useState(false);

  useEffect(() => {
    Promise.all([fetchOrgUnits(), fetchUsers()])
      .then(([u, us]) => {
        setUnits(u);
        setUsers(us);
        const root = u.find((x) => x.parent_id === null);
        if (root) setSelected(root.id);
      })
      .catch(fail);
  }, []);

  // 职级随时间点变化（晋升有历史），跟着 at 一起重取
  useEffect(() => {
    fetchUserPositions(at || undefined).then(setPositions).catch(fail);
  }, [at]);

  useEffect(() => {
    if (selected == null) return;
    fetchMembers(selected, { at: at || undefined, subtree }).then(setMembers).catch(fail);
  }, [selected, at, subtree]);

  const userById = useMemo(() => Object.fromEntries(users.map((u) => [u.id, u])), [users]);
  const unitById = useMemo(() => Object.fromEntries(units.map((u) => [u.id, u])), [units]);

  function Tree({ pid, depth }: { pid: number | null; depth: number }) {
    const children = units
      .filter((u) => u.parent_id === pid)
      .sort((a, b) => a.sort_order - b.sort_order || a.id - b.id);
    return (
      <>
        {children.map((u) => (
          <div key={u.id}>
            <div
              className={"tree-node" + (selected === u.id ? " active" : "")}
              style={{ paddingLeft: depth * 14 }}
              onClick={() => setSelected(u.id)}
            >
              {u.name}<Badge>{KIND_LABEL[u.kind] ?? u.kind}</Badge>
            </div>
            <Tree pid={u.id} depth={depth + 1} />
          </div>
        ))}
      </>
    );
  }

  const columns: Column<Membership>[] = [
    { key: "name", title: "姓名", render: (m) => userById[m.user_id]?.full_name ?? `用户${m.user_id}` },
    {
      key: "position", title: "职级",
      render: (m) => {
        const p = positions[String(m.user_id)];
        return p ? <>{p.name}{p.is_partner && <Badge tone="brand">合伙人</Badge>}</>
                 : <span className="muted">未定级</span>;
      },
    },
    { key: "org_role", title: "角色", render: (m) => m.org_role || "—" },
    { key: "org", title: "所属", render: (m) => unitById[m.org_unit_id]?.name ?? m.org_unit_id },
    {
      key: "span", title: "生效区间",
      render: (m) => <span className="muted small">{m.valid_from} ~ {m.valid_to ?? "至今"}</span>,
    },
  ];

  return (
    <>
      <Notice text={notice.text} kind={notice.kind} onClose={clear} />
      {/* 组织树窄、内容宽 —— 和别处的 aside 方向相反 */}
      <div className="grid tree">
        <div className="stack">
          <Section title="组织树">
            <Tree pid={null} depth={0} />
          </Section>
        </div>

        <div className="stack" style={{ flex: 1 }}>
          <Section
            title={selected != null ? unitById[selected]?.name ?? "成员" : "成员"}
            extra={
              <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                <input type="date" value={at} onChange={(e) => setAt(e.target.value)}
                       style={{ width: 150 }} title="查某个时间点的编制" />
                <label className="small" style={{ margin: 0, whiteSpace: "nowrap" }}>
                  <input type="checkbox" checked={subtree}
                         onChange={(e) => setSubtree(e.target.checked)}
                         style={{ width: "auto", marginRight: 5 }} />
                  含下级
                </label>
                {at && <button className="btn secondary" onClick={() => setAt("")}>回到今天</button>}
              </div>
            }
          >
            <p className="muted small" style={{ marginTop: 0 }}>
              调岗与晋升都不删历史 —— 把日期调到过去，看到的是当时的编制与职级。
            </p>
            <DataTable columns={columns} rows={members} empty="该组织在此时间点没有成员" />
          </Section>

          <Section title="我的身份">
            <div>
              职级：<strong>{me?.position?.name ?? "未定级"}</strong>
              {me?.position?.can_lead_team && <Badge>可牵头团队</Badge>}
              {me?.position?.can_handle_case && <Badge>可独立承办</Badge>}
            </div>
            {me?.memberships.map((m) => (
              <div key={m.id} className="small" style={{ marginTop: 4 }}>
                {unitById[m.org_unit_id]?.name ?? m.org_unit_id}
                {m.org_role && <Badge>{m.org_role}</Badge>}
                {m.is_primary && <Badge>主归属</Badge>}
              </div>
            ))}
          </Section>
        </div>
      </div>
    </>
  );
}
