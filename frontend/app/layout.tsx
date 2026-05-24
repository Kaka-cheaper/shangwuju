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
      // 沉浸式翻译 / 划词翻译 / Grammarly 等浏览器扩展会向 <html> / <body>
      // 注入属性（如 data-immersive-translate-page-theme），触发 React
      // hydration mismatch warning。仅在最外层标签 suppress 不影响子树校验。
      // 业界标配（Next.js / Remix / Astro 官方文档都建议根标签设此项）。
      suppressHydrationWarning
    >
      <body suppressHydrationWarning>{children}</body>
    </html>
  );
}
