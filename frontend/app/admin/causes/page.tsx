"use client";

import { useEffect, useMemo, useState } from "react";

import {
  fetchCauses, createCause, CASE_CATEGORY_LABEL, type Cause,
} from "@/lib/api";
import { useAuth, Can } from "@/lib/auth";
import { Badge, Button, DataTable, Field, Notice, Section, Tag, type Column, useNotice } from "@/components/ui";

/**
 * 案由字典 —— 薄管理页的样板：列表 + 新增，就这些。
 * 批量导入、拖拽排序、自定义列一律不做，等律所真提了再说。
 */
export default function CausesPage() {
  const { can } = useAuth();
  const { notice, clear, ok, fail } = useNotice();

  const [causes, setCauses] = useState<Cause[]>([]);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [category, setCategory] = useState("civil");
  const [parentId, setParentId] = useState<string>("");
  const [template, setTemplate] = useState("");

  const editable = can("case.manage_cause");

  function reload() { fetchCauses().then(setCauses).catch(fail); }
  useEffect(reload, []);

  const byId = useMemo(() => Object.fromEntries(causes.map((c) => [c.id, c])), [causes]);
  const roots = causes.filter((c) => c.parent_id === null);

  async function submit() {
    try {
      await createCause({
        code, name, category,
        parent_id: parentId ? Number(parentId) : null,
        workflow_template: template,
      });
      ok(`已新增案由「${name}」`);
      setCode(""); setName(""); setTemplate("");
      reload();
    } catch (e) { fail(e); }
  }

  const columns: Column<Cause>[] = [
    { key: "name", title: "案由", render: (c) => (
        <span style={{ paddingLeft: c.parent_id ? 16 : 0 }}>
          {c.name}
          {!c.parent_id && <Badge>一级</Badge>}
        </span>
      ) },
    { key: "code", title: "编码", width: 130 },
    { key: "category", title: "大类", width: 80,
      render: (c) => CASE_CATEGORY_LABEL[c.category] ?? c.category },
    { key: "workflow_template", title: "办案流程模板", width: 180,
      render: (c) => c.workflow_template
        ? <Badge tone="brand">{c.workflow_template}</Badge>
        : <span className="muted small">未配置，走通用流程</span> },
  ];

  // 一级在前，二级紧跟其后
  const ordered = roots.flatMap((r) => [r, ...causes.filter((c) => c.parent_id === r.id)]);

  return (
    <>
      <Notice text={notice.text} kind={notice.kind} onClose={clear} />

      <Section title="案由字典" extra={
        <span className="muted small">
          案由决定建案时套用哪张办案流程图（Step 9-10）
        </span>
      }>
        <DataTable columns={columns} rows={ordered} />
        {!editable && (
          <p className="muted small">你没有 case.manage_cause，只读。</p>
        )}
      </Section>

      <Can perm="case.manage_cause">
        <Section title="新增案由">
          <div className="row">
            <div className="stack" style={{ minWidth: 200 }}>
              <label>编码</label>
              <input value={code} onChange={(e) => setCode(e.target.value)}
                     placeholder="如 maritime" />
              <label>名称</label>
              <input value={name} onChange={(e) => setName(e.target.value)}
                     placeholder="如 海事海商纠纷" />
            </div>
            <div className="stack" style={{ minWidth: 200 }}>
              <label>大类</label>
              <select value={category} onChange={(e) => setCategory(e.target.value)}>
                {Object.entries(CASE_CATEGORY_LABEL).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
              <label>上级案由（留空为一级）</label>
              <select value={parentId} onChange={(e) => setParentId(e.target.value)}>
                <option value="">（一级案由）</option>
                {roots.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select>
            </div>
            <div className="stack" style={{ minWidth: 200 }}>
              <label>办案流程模板（可留空）</label>
              <input value={template} onChange={(e) => setTemplate(e.target.value)}
                     placeholder="如 labor_v1" />
              <p className="muted small" style={{ marginTop: 6 }}>
                填了之后，该案由的新案件会自动套上对应流程图。
              </p>
            </div>
          </div>
          <button className="btn" style={{ marginTop: 14 }}
                  disabled={!code.trim() || !name.trim()} onClick={submit}>
            新增
          </button>
        </Section>
      </Can>
    </>
  );
}
