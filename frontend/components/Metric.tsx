"use client";

/**
 * 看板上的一格数字。
 *
 * ★ **点不开的数字是装饰。** 看板的价值不在数字本身，在它是**入口** ——
 *   所以只要指标声明了 drill，这一格就是个链接，并且画一个箭头。
 *
 * ★ 拿不到的时候显示「查不到」，**绝不显示 0**。
 *   一个模块挂了却显示"本月回款 ¥0"，会吓死人，而且方向是反的 ——
 *   真实情况可能收得很好，只是那个模块没响应。
 */
import { Metric as MetricBox } from "@/components/ui";

import type { MetricDef, MetricValue } from "@/lib/api";

export type Tone = "ok" | "warn" | "bad" | undefined;

export function Metric({ def, value, missing, tone, hint }: {
  def: MetricDef;
  /** 这个指标所属的模块没报上来 */
  missing?: boolean;
  value?: MetricValue;
  tone?: Tone;
  hint?: string;
}) {
  return (
    <MetricBox
      n={missing ? <span className="muted">查不到</span> : (value?.text ?? "—")}
      label={
        missing
          ? <>{def.name}　<span className="muted">这个模块没响应，不是 0</span></>
          : def.name
      }
      tone={missing ? undefined : tone}
      href={def.drill && !missing ? def.drill : undefined}
      title={hint || def.description || undefined}
    />
  );
}

/** 一排数字 */
export function MetricRow({ defs, values, missing, tones }: {
  defs: MetricDef[];
  values: Record<string, MetricValue>;
  missing: string[];
  tones?: Record<string, Tone>;
}) {
  return (
    <div className="metrics">
      {defs.map((d) => (
        <Metric key={d.key} def={d} value={values[d.key]}
                missing={missing.includes(d.key.split(".")[0])}
                tone={tones?.[d.key]} />
      ))}
    </div>
  );
}

/** 按 key 从目录里挑出要显示的几个，保持给定顺序 */
export function pick(catalog: MetricDef[], keys: string[]): MetricDef[] {
  const by = Object.fromEntries(catalog.map((d) => [d.key, d]));
  return keys.map((k) => by[k]).filter(Boolean);
}
