"use client";

/**
 * 基础组件。
 *
 * ★ **解剖照 shadcn/ui，实现不引 Tailwind/Radix。**
 *   `Card / CardHeader / CardContent`、`Tabs / TabsList / TabsTrigger`、
 *   `Badge` 的拆法和命名都是 shadcn 的 —— 将来真要换成 Radix，
 *   调用处一行不用改，只换这个文件里的实现。
 *
 * ★ 组件只用语义 token（`--primary`、`--muted-foreground`…），
 *   **不写死任何颜色**。明暗两套是换变量，不是改组件。
 */
import Link from "next/link";
import { useState } from "react";

export type { NoticeKind } from "@/lib/notice";
export { useNotice } from "@/lib/notice";

const cx = (...xs: (string | false | undefined | null)[]) =>
  xs.filter(Boolean).join(" ");

// ---------------------------------------------------------------- Card

export function Card({ children, className }: {
  children: React.ReactNode; className?: string;
}) {
  return <section className={cx("card", className)}>{children}</section>;
}

export function CardHeader({ title, desc, action }: {
  title: React.ReactNode;
  /** 一句话说清这张卡是干什么的 —— 律所的规则太多，不当场说用户会用错 */
  desc?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="card-head">
      <div className="grow">
        <h3>{title}</h3>
        {desc && <div className="card-desc">{desc}</div>}
      </div>
      {action}
    </div>
  );
}

export function CardContent({ children, flush }: {
  children: React.ReactNode;
  /** 表格贴边 —— 表格自己有内边距，再套一层就浪费空间 */
  flush?: boolean;
}) {
  return <div className={cx("card-body", flush && "flush")}>{children}</div>;
}

/** 最常见的组合：标题 + 内容。可折叠。
 *
 * ★ **折叠是为了长内容，不是为了藏东西。**
 *   案件页一个页签里常常并排放着两三块（「办案」里有利冲＋流程），
 *   一份文书本身就能拉几屏 —— 不折叠就得一直往下滚，
 *   而滚过去的东西等于没看见。
 *
 * ★ **默认开还是关，看那一屏最该先看到什么。**
 *   利冲「已阻断」必须默认展开 —— 那是不能往下走的信号；
 *   已经通过的可以收起来。所以 `defaultOpen` 由调用处按**内容**决定，
 *   不是一律折叠。
 *
 * ★ 折叠状态**记住**（`rememberKey`）—— 律师一天里反复进出同一个案子，
 *   每次都要重新收起来会比不折叠还烦。
 */
export function Section({ title, desc, extra, flush, children,
                          collapsible, defaultOpen = true, rememberKey,
                          count }: {
  title: React.ReactNode;
  desc?: React.ReactNode;
  extra?: React.ReactNode;
  flush?: boolean;
  children: React.ReactNode;
  collapsible?: boolean;
  defaultOpen?: boolean;
  /** 传了就把开合状态记在 localStorage */
  rememberKey?: string;
  /** 折起来时也要看得见里面有几件 —— 否则收起来就等于藏起来 */
  count?: number;
}) {
  const [open, setOpen] = useState(() => {
    if (!collapsible || !rememberKey || typeof window === "undefined") {
      return defaultOpen;
    }
    try {
      const v = window.localStorage.getItem("lfp-sec-" + rememberKey);
      if (v === "0") return false;
      if (v === "1") return true;
    } catch { /* 隐私模式下会抛 —— 不能让它把页面带崩 */ }
    return defaultOpen;
  });

  function toggle() {
    const next = !open;
    setOpen(next);
    try {
      if (rememberKey) {
        window.localStorage.setItem("lfp-sec-" + rememberKey, next ? "1" : "0");
      }
    } catch { /* 同上 */ }
  }

  if (!collapsible) {
    return (
      <Card>
        <CardHeader title={title} desc={desc} action={extra} />
        <CardContent flush={flush}>{children}</CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <div className="card-head">
        {/* 整个标题区可点 —— 只有一个小三角能点，命中率太低 */}
        <button className="sec-toggle grow" onClick={toggle}
                aria-expanded={open}>
          <span className={"sec-caret" + (open ? " open" : "")}>▸</span>
          <span className="grow">
            <h3>
              {title}
              {typeof count === "number" && count > 0 && (
                <span className="tab-count" style={{ marginLeft: 7 }}>{count}</span>
              )}
            </h3>
            {desc && <div className="card-desc">{desc}</div>}
          </span>
        </button>
        {/* ★ 折起来时不显示操作按钮 —— 一个看不到内容的地方摆着「新建」，
            点了才发现要先展开 */}
        {open && extra}
      </div>
      {open && <CardContent flush={flush}>{children}</CardContent>}
    </Card>
  );
}

// ---------------------------------------------------------------- Button

type BtnVariant = "primary" | "secondary" | "ghost" | "danger";

export function Button({ variant = "secondary", size, className,
                         children, ...rest }: {
  variant?: BtnVariant;
  size?: "sm";
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      className={cx("btn", variant !== "primary" && variant, size, className)}
      {...rest}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------- Badge

/** ★ `muted` 是"说明，不是警报" —— 比如「无固定期间」那种。
 *  少了它，调用处就只能拿 warn 凑数，然后一条永远变不绿的黄字
 *  会把真警报一起拖下水。 */
export type Tone = "ok" | "warn" | "bad" | "brand" | "outline"
                 | "muted" | undefined;

export function Badge({ tone, title, children }: {
  tone?: Tone; title?: string; children: React.ReactNode;
}) {
  return <span className={cx("badge", tone)} title={title}>{children}</span>;
}

/** 旧名字，还有一堆地方在用 */
export const Tag = Badge;

// ---------------------------------------------------------------- Tabs

export type TabDef = {
  key: string;
  label: string;
  /** 右上角的数字。★ **0 不渲染** —— 一个常驻的 0 是纯噪音 */
  count?: number;
  /** 数字标红（有东西要出事） */
  alert?: boolean;
  /** 没权限就整个不显示 —— 不是禁用。Clio 也是这么做的：
   *  没有开票权限的人根本看不到 Bills 那个页签 */
  hidden?: boolean;
};

export function Tabs({ tabs, value, onChange }: {
  tabs: TabDef[]; value: string; onChange: (k: string) => void;
}) {
  const shown = tabs.filter((t) => !t.hidden);
  return (
    <div className="tabs" role="tablist">
      {shown.map((t) => (
        <button
          key={t.key}
          role="tab"
          type="button"
          className="tab"
          aria-selected={value === t.key}
          onClick={() => onChange(t.key)}
        >
          {t.label}
          {!!t.count && (
            <span className={cx("tab-count", t.alert && "bad")}>{t.count}</span>
          )}
        </button>
      ))}
    </div>
  );
}

/** 记住上次停在哪个页签 —— 律师一天里会反复进出同一个案子 */
export function useTabs(tabs: TabDef[], storageKey?: string) {
  const first = tabs.find((t) => !t.hidden)?.key ?? "";
  const [value, setValue] = useState(() => {
    if (!storageKey || typeof window === "undefined") return first;
    try {
      const saved = window.localStorage.getItem(storageKey);
      if (saved && tabs.some((t) => t.key === saved && !t.hidden)) return saved;
    } catch { /* 隐私模式下 localStorage 会抛 —— 不能让它把页面带崩 */ }
    return first;
  });
  function change(k: string) {
    setValue(k);
    try { if (storageKey) window.localStorage.setItem(storageKey, k); } catch { /* 同上 */ }
  }
  return [value, change] as const;
}

// ---------------------------------------------------------------- Table

export type Column<T> = {
  key: string;
  title: string;
  width?: number;
  align?: "right";
  render?: (row: T) => React.ReactNode;
};

export function DataTable<T extends { id: number | string }>({
  columns, rows, onRowClick, activeId, empty = "没有数据",
}: {
  columns: Column<T>[];
  rows: T[];
  onRowClick?: (row: T) => void;
  activeId?: number | string | null;
  empty?: React.ReactNode;
}) {
  if (rows.length === 0) return <div className="empty">{empty}</div>;
  return (
    <div className="scroll-x">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key}
                  style={{ width: c.width, textAlign: c.align }}>{c.title}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}
                className={cx(onRowClick && "click", activeId === r.id && "on")}
                onClick={onRowClick ? () => onRowClick(r) : undefined}>
              {columns.map((c) => (
                <td key={c.key} style={{ textAlign: c.align }}>
                  {c.render ? c.render(r) : String((r as never)[c.key] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- Alert

export function Alert({ tone, title, children }: {
  tone?: "ok" | "warn" | "bad";
  title?: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <div className={cx("alert", tone)}>
      {title && <div className="alert-head"><b>{title}</b></div>}
      {children && <div className="lines small">{children}</div>}
    </div>
  );
}

/** 操作结果提示。空文本时什么也不渲染 */
export function Notice({ text, kind = "info", onClose }: {
  text: string;
  kind?: "info" | "ok" | "error";
  onClose?: () => void;
}) {
  if (!text) return null;
  const tone = kind === "error" ? "bad" : kind === "ok" ? "ok" : undefined;
  return (
    <div className={cx("alert", tone)}>
      <div className="alert-head">
        {/* ★ 报错常常是多行的（为什么 + 怎么办），换行不能被吞掉 */}
        <span className="lines grow">{text}</span>
        {onClose && (
          <button className="linkbtn small" onClick={onClose}>关闭</button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- 字段

export function Field({ label, hint, children }: {
  label?: React.ReactNode;
  /** 规则解释。★ 不当场说清，用户就会用错然后怪系统 */
  hint?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="field">
      {label && <label>{label}</label>}
      {children}
      {hint && <div className="field-hint">{hint}</div>}
    </div>
  );
}

export function Toggle({ checked, onChange, label, hint, disabled }: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: React.ReactNode;
  hint?: React.ReactNode;
  disabled?: boolean;
}) {
  return (
    <div className="field">
      <label className="inline">
        <input type="checkbox" checked={checked} disabled={disabled}
               onChange={(e) => onChange(e.target.checked)} />
        <span>{label}</span>
      </label>
      {hint && <div className="field-hint">{hint}</div>}
    </div>
  );
}

// ---------------------------------------------------------------- 指标

export function Metric({ n, label, tone, href, title }: {
  n: React.ReactNode;
  label: React.ReactNode;
  tone?: "ok" | "warn" | "bad";
  /** ★ 有 href 才画箭头 —— 点不开的数字是装饰 */
  href?: string;
  title?: string;
}) {
  const body = (
    <>
      <div className={cx("metric-n", tone)}>{n}</div>
      <div className="metric-l">{label}</div>
    </>
  );
  if (href) {
    return <Link href={href} className="metric" title={title}>{body}</Link>;
  }
  return <div className="metric" title={title}>{body}</div>;
}

// ---------------------------------------------------------------- 富文本

/** 把 **粗** 和换行渲染出来 —— 后端报错里带这两种标记 */
export function Rich({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return (
    <span className="lines">
      {parts.map((p, i) =>
        p.startsWith("**") && p.endsWith("**")
          ? <b key={i}>{p.slice(2, -2)}</b>
          : <span key={i}>{p}</span>
      )}
    </span>
  );
}
