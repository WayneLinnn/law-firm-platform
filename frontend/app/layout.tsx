import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "律所平台",
  description: "以案件为中心的律所操作系统",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
