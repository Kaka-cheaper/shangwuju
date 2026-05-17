import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";

import "./globals.css";

// Inter：B+D 范式标配的现代无衬线字体（Linear / Vercel / Anthropic 在用）
const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

// JetBrains Mono：等宽用于 Tool 输入 JSON / session id / 订单号
const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-jetbrains",
});

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
    <html
      lang="zh-CN"
      className={`${inter.variable} ${jetbrains.variable}`}
    >
      <body>{children}</body>
    </html>
  );
}
