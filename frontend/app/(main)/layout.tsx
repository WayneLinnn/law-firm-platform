"use client";

/**
 * 应用外壳。
 *
 * ★ **从顶部导航改成左侧导航（Clio 的做法）。**
 *
 *   顶部一排放得下 5–7 项，再多就要挤或折行。这个系统已经有 8 个日常入口
 *   ＋管理入口，而且**还会长**。左侧是纵向的，加一项只是多一行。
 *
 * ★ 更要紧的是**能分组**。原来顶部把两类东西混在一排：
 *
 *       案件、客户          ← **实体**（东西本身）
 *       待办、账款、单据    ← **跨案件的工作流**（我今天要处理什么）
 *
 *   挤在一起就看不出层次 —— 用户报的"太散了"，一半是这个造成的。
 *
 * ★ 导航上带**未处理数**，而且只在大于 0 时渲染。
 *   一个常驻的 "0" 是纯噪音；有数字才说明那里有事等着。
 */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { fetchInbox, fetchInboxCenter } from "@/lib/api";
import { SCOPE_LABEL } from "@/lib/api";
import { AuthProvider, useAuth } from "@/lib/auth";

/** 日常业务导航，**按性质分三组**。管理类走底部的「所务管理」 */
const GROUPS: {
  name: string;
  items: { href: string; label: string; ico: string; perm: string;
           badge?: "inbox" | "tickets" }[];
}[] = [
  {
    name: "办案",
    items: [
      { href: "/", label: "工作台", ico: "▣", perm: "" },
      // ★ 待办中心紧挨着工作台 —— 它是每天第一个点开的东西。
      //   所有等你动手的事（时限、任务、单据）都汇总在这一页。
      { href: "/inbox", label: "待办中心", ico: "◎", perm: "analytics.view",
        badge: "inbox" },
      { href: "/cases", label: "案件", ico: "⚖", perm: "case.view" },
      { href: "/todo", label: "时限明细", ico: "◷", perm: "task.view" },
    ],
  },
  {
    name: "客户",
    items: [
      { href: "/clients", label: "客户", ico: "◱", perm: "crm.view" },
      { href: "/consults", label: "法律咨询", ico: "◔", perm: "consult.view" },
    ],
  },
  {
    name: "财务与事务",
    items: [
      { href: "/receivables", label: "账款", ico: "¥", perm: "billing.view" },
      { href: "/tickets", label: "单据", ico: "▤", perm: "approval.view",
        badge: "tickets" },
    ],
  },
  {
    name: "管理",
    items: [
      { href: "/team", label: "团队看板", ico: "◫", perm: "analytics.view" },
      { href: "/firm", label: "全所看板", ico: "◨", perm: "analytics.view_firm" },
    ],
  },
];

/** 有任意一项管理权限才显示「所务管理」 */
const ADMIN_PERMS = [
  "identity.manage_org", "identity.manage_user",
  "identity.manage_role", "identity.promote", "audit.view",
  "case.manage_cause",
];

/** 页面标题跟着路由走 —— 顶栏要说清"我现在在哪" */
const TITLES: Record<string, string> = {
  "/": "工作台",
  "/cases": "案件",
  "/inbox": "待办中心",
  "/todo": "时限明细",
  "/clients": "客户",
  "/consults": "法律咨询",
  "/receivables": "账款",
  "/tickets": "单据",
  "/team": "团队看板",
  "/firm": "全所看板",
};

function Shell({ children }: { children: React.ReactNode }) {
  const { me, loading, error, can, scopeOf, logout } = useAuth();
  const path = usePathname();

  // 导航上的未处理数
  const [inboxN, setInboxN] = useState(0);
  const [ticketN, setTicketN] = useState(0);

  useEffect(() => {
    if (!me) return;
    // ★ 待办中心的角标只算**会出事的**（过期/算不出/临近），
    //   不算"正常"那一档 —— 把在跑着的期限也算进红点，
    //   红点就永远不会消失，然后它就不再是信号了。
    if (can("analytics.view")) {
      fetchInboxCenter()
        .then((b) => setInboxN((b.by_urgency.expired ?? 0)
                             + (b.by_urgency.unknown ?? 0)
                             + (b.by_urgency.urgent ?? 0)))
        .catch(() => {});
    }
    if (can("approval.view")) {
      fetchInbox().then((t) => setTicketN(t.length)).catch(() => {});
    }
  }, [me, can, path]);

  if (loading) return <div className="content muted">加载中…</div>;
  if (error) {
    return <div className="content"><div className="alert bad">{error}</div></div>;
  }
  if (!me) return null;

  const counts = { inbox: inboxN, tickets: ticketN };
  const showAdmin = ADMIN_PERMS.some(can);
  const initials = me.user.full_name.slice(0, 1);

  return (
    <div className="app">
      <aside className="nav">
        <div className="nav-brand">
          <span className="nav-mark">律</span>
          <span>康桥律所</span>
        </div>

        <nav className="nav-body">
          {GROUPS.map((g) => {
            const items = g.items.filter((i) => !i.perm || can(i.perm));
            if (items.length === 0) return null;
            return (
              <div key={g.name}>
                <div className="nav-group">{g.name}</div>
                {items.map((i) => {
                  const n = i.badge ? counts[i.badge] : 0;
                  return (
                    <Link key={i.href} href={i.href}
                          className={path === i.href ? "active" : ""}>
                      <span className="nav-ico">{i.ico}</span>
                      <span className="grow truncate">{i.label}</span>
                      {/* ★ 0 不渲染 */}
                      {n > 0 && <span className="nav-count">{n}</span>}
                    </Link>
                  );
                })}
              </div>
            );
          })}
        </nav>

        <div className="nav-foot">
          <div className="nav-me">
            <span className="nav-avatar">{initials}</span>
            <div className="nav-me-txt grow">
              <b>{me.user.full_name}</b>
              <span>
                {me.position?.name ?? "未定级"}
                {" · "}
                {SCOPE_LABEL[scopeOf("identity.view")] ?? scopeOf("identity.view")}
              </span>
            </div>
          </div>
          <div className="row tight">
            {showAdmin && (
              <Link href="/admin/org" className="btn ghost sm grow">
                所务管理
              </Link>
            )}
            <button className="btn ghost sm" onClick={logout}>退出</button>
          </div>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <h1>{TITLES[path] ?? "律所平台"}</h1>
        </header>
        <div className="content">{children}</div>
      </div>
    </div>
  );
}

export default function MainLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthProvider>
      <Shell>{children}</Shell>
    </AuthProvider>
  );
}
