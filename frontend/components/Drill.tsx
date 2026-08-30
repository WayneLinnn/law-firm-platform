"use client";

/**
 * 从看板点一个数字进来之后的落地处理。
 *
 * ★ **点开之后看到的必须正好是那个数字本身。**
 *   第一版 14 个指标只有 4 个落点，谁都不带筛选 —— 你从"12 条算不出"
 *   点进去，面前是几十行的全量表，还得自己再找一遍那 12 条。
 *   **那这个格子就白点了**，看板退回成一堵装饰墙。
 *
 * ★ 被筛过的列表**必须自己说出来**。一张悄悄筛过的表比不筛更糟 ——
 *   用户看到的行数比预期少，第一反应是"系统漏数据了"，
 *   然后连没筛的那些页面也一起不信了。
 */
import { useRouter, useSearchParams } from "next/navigation";

export type Drill = {
  /** 筛什么，来自指标的 drill（f=expired 之类）。空表示没筛 */
  f: string;
  /** 范围：mine / team / firm。空表示按页面默认 */
  scope: string;
  org: number | null;
  since: string;
  until: string;
  /** 有没有从看板点进来 */
  active: boolean;
};

export function useDrill(): Drill {
  const sp = useSearchParams();
  const org = sp.get("org");
  const f = sp.get("f") ?? "";
  const scope = sp.get("scope") ?? "";
  return {
    f, scope,
    org: org ? Number(org) : null,
    since: sp.get("since") ?? "",
    until: sp.get("until") ?? "",
    active: Boolean(f || scope),
  };
}

const SCOPE_LABEL: Record<string, string> = {
  mine: "我的", team: "本团队", firm: "全所",
};

/**
 * 横幅：说清现在这张表被筛成了什么样，并给一个退出口。
 *
 * ★ 一定要有"看全部"。**筛进来容易，退出去也得容易** ——
 *   否则用户会以为这一页就长这样，然后去别处找完整列表。
 */
export function DrillBanner({ drill, label, n, total }: {
  drill: Drill;
  /** 这个筛选的人话名字，比如"算不出的时限" */
  label: string;
  /** 筛完剩几条 */
  n?: number;
  /** 一共几条 */
  total?: number;
}) {
  const router = useRouter();
  if (!drill.active) return null;

  const bits = [label];
  if (drill.scope && SCOPE_LABEL[drill.scope]) {
    bits.push(SCOPE_LABEL[drill.scope]);
  }
  if (drill.since) {
    bits.push(`${drill.since} 起`);
  }

  return (
    <div className="drillbar">
      <span>
        正在看：<b>{bits.join(" · ")}</b>
        {typeof n === "number" && typeof total === "number" && (
          <span className="muted small">　{n} / {total} 条</span>
        )}
      </span>
      <button className="btn ghost sm"
              onClick={() => router.replace(window.location.pathname)}>
        看全部 ✕
      </button>
    </div>
  );
}
