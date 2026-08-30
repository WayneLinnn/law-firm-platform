"use client";

/** 团队看板。和全所页共用一个组件，靠 scope 切 —— 版面一样，范围不同。 */
import { Board } from "@/app/(main)/firm/page";

export default function TeamBoard() {
  return <Board scope="team" />;
}
