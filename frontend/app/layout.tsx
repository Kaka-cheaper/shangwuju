import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "晌午局 · 半日出行管家",
  description:
    "一句话搞定下午行程：意图解析 + Tool 编排 + 异常重规划全程可见。",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
