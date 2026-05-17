import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./lib/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        // ============================================================
        // 设计系统 v3「黄昏胶片」深色主题
        //
        // 核心思路：把 ink 色阶语义「反转」——在浅色主题里 ink-50
        // 是最浅、ink-950 是最深；深色主题里 ink-50 = 最深的页面底色，
        // ink-950 = 最亮的强调白。这样所有组件代码（bg-ink-50 / text-ink-800
        // / border-ink-200）不需要动一行，整体语义自动适配深色。
        //
        // 灵感来源（参考问题 12 研究）：
        // - linear.app/agents 的暗色 aurora
        // - Spotify Wrapped / MUBI 的暖光暗底
        // - shadcn.io noise hero block 的颗粒感
        //
        // 三层色板：
        // - ink: stone 暖灰系（带 5% 黄底，比纯 zinc 暖一档）
        // - accent: 暖紫莓（替代原冷蓝），管 Agent 思考链路 / 进度
        // - brand: 夕阳橙（保留 + 强化），管主操作 / 时间轴 / hover
        // - sunset / dusk: 背景光斑专用渐变色组
        // ============================================================
        ink: {
          // 反转后：50=最深页面底，950=最亮强调白
          50: "#0a0a0a", // 页面底色 · 接近黑但带蓝紫底
          100: "#161616", // 二级背景 / hover 浅亮一档
          200: "#1f1f1f", // 边框 / 分隔
          300: "#2e2e2e", // 弱边框
          400: "#52525b", // 占位文字
          500: "#71717a", // 次要文字
          600: "#a3a3a8", // 正常文字次轴
          700: "#d4d4d4", // 标题次
          800: "#e7e5e4", // 主标题
          900: "#f5f5f4", // 最亮文字（暖白系，非冷白）
          950: "#fafaf9", // 极亮强调
        },
        accent: {
          // 暖紫莓（替代原冷蓝 #2f6feb）—— Agent 思考链路色
          50: "#fdf4ff",
          100: "#fae8ff",
          200: "#f5d0fe",
          300: "#f0abfc",
          400: "#e879f9",
          500: "#d946ef", // 主 accent 莓紫
          600: "#c026d3",
          700: "#a21caf",
          800: "#86198f",
          900: "#701a75",
        },
        // brand：夕阳橙系（强化）—— 主操作 / 时间轴 / hover
        brand: {
          50: "#fff7ed",
          100: "#ffedd5",
          200: "#fed7aa",
          300: "#fdba74",
          400: "#fb923c",
          500: "#f97316",
          600: "#ea580c",
          700: "#c2410c",
          800: "#9a3412",
          900: "#7c2d12",
        },
        // sunset：暖橙→玫红光斑色（夕阳）
        sunset: {
          400: "#fb923c", // 暖橙
          500: "#f97316",
          600: "#ec4899", // 莓粉
          700: "#db2777",
        },
        // dusk：紫蓝光斑色（暮光）
        dusk: {
          400: "#a78bfa", // 浅紫
          500: "#8b5cf6", // 紫
          600: "#6366f1", // 靛
          700: "#4f46e5",
        },
        // caramel：焦糖琥珀色（替代偏好画像处的莓紫，去 AI 味）
        // 灵感：中古电影焦糖滤镜 + Aesop 沙漠米色 + 旧版 stripe 焦糖文档
        // 用于：偏好画像 persona icon / chip-warm / 收藏标签
        caramel: {
          50: "#faf6f0",
          100: "#f0e3d0",
          200: "#e0c9a8",
          300: "#cda87b",
          400: "#b8895a", // 主焦糖色（icon）
          500: "#a06a3a",
          600: "#834f25",
          700: "#623818",
          800: "#412410",
          900: "#2a170a",
        },
      },
      fontFamily: {
        sans: [
          "var(--font-inter)",
          "ui-sans-serif",
          "system-ui",
          '"PingFang SC"',
          '"Microsoft YaHei"',
          '"Source Han Sans CN"',
          "sans-serif",
        ],
        mono: [
          "var(--font-jetbrains)",
          "ui-monospace",
          '"SFMono-Regular"',
          "Menlo",
          "Monaco",
          "Consolas",
          "monospace",
        ],
      },
      animation: {
        "pulse-soft": "pulseSoft 1.6s ease-in-out infinite",
        "fade-in": "fadeIn 240ms ease-out",
        "fade-in-up": "fadeInUp 280ms ease-out",
        shimmer: "shimmer 2s linear infinite",
        "tick-up": "tickUp 380ms cubic-bezier(0.34, 1.56, 0.64, 1)",
        "collapse-in": "collapseIn 200ms ease-out",
        // 黄昏光斑缓慢呼吸
        "aurora-drift": "auroraDrift 18s ease-in-out infinite",
        "aurora-drift-slow": "auroraDrift 28s ease-in-out infinite reverse",
        // 行程卡到达聚光灯（2.4s 一次性脉冲）
        "spotlight-once": "spotlightPulse 2400ms ease-out 1",
        // 烟花粒子飞起
        "confetti-fly": "confettiFly 1600ms cubic-bezier(0.22, 0.61, 0.36, 1) forwards",
      },
      keyframes: {
        pulseSoft: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.55" },
        },
        fadeIn: {
          from: { opacity: "0" },
          to: { opacity: "1" },
        },
        fadeInUp: {
          from: { opacity: "0", transform: "translateY(6px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        tickUp: {
          "0%": { transform: "translateY(8px)", opacity: "0" },
          "60%": { transform: "translateY(-2px)", opacity: "1" },
          "100%": { transform: "translateY(0)", opacity: "1" },
        },
        collapseIn: {
          from: { opacity: "0", maxHeight: "0", transform: "translateY(-2px)" },
          to: { opacity: "1", maxHeight: "1000px", transform: "translateY(0)" },
        },
        auroraDrift: {
          "0%, 100%": {
            transform: "translate3d(0, 0, 0) scale(1)",
            opacity: "0.7",
          },
          "33%": {
            transform: "translate3d(40px, -30px, 0) scale(1.1)",
            opacity: "0.85",
          },
          "66%": {
            transform: "translate3d(-30px, 40px, 0) scale(0.95)",
            opacity: "0.6",
          },
        },
        // 行程卡聚光灯：暖橙 → 莓粉两层光环外扩 + 内部微缩放
        spotlightPulse: {
          "0%": {
            boxShadow:
              "0 0 0 0 rgba(249, 115, 22, 0.45), 0 0 0 0 rgba(236, 72, 153, 0.35), 0 8px 32px -12px rgba(0,0,0,0.6)",
            transform: "scale(0.985)",
          },
          "30%": {
            boxShadow:
              "0 0 0 8px rgba(249, 115, 22, 0.18), 0 0 0 18px rgba(236, 72, 153, 0.12), 0 16px 48px -12px rgba(249, 115, 22, 0.4)",
            transform: "scale(1.005)",
          },
          "100%": {
            boxShadow:
              "0 0 0 24px rgba(249, 115, 22, 0), 0 0 0 40px rgba(236, 72, 153, 0), 0 8px 32px -12px rgba(0,0,0,0.6)",
            transform: "scale(1)",
          },
        },
        // 烟花粒子：从中心向四周飞 + 旋转 + 衰减
        confettiFly: {
          "0%": {
            transform: "translate3d(0, 0, 0) rotate(0deg) scale(0.6)",
            opacity: "0",
          },
          "10%": {
            opacity: "1",
          },
          "100%": {
            transform:
              "translate3d(var(--cf-dx, 0px), var(--cf-dy, -120px), 0) rotate(var(--cf-rot, 540deg)) scale(1)",
            opacity: "0",
          },
        },
      },
      boxShadow: {
        // 深色主题阴影：用浅色光晕替代真阴影（深底投不出阴影）
        elevated:
          "0 0 0 1px rgb(255 255 255 / 0.06), 0 8px 32px -8px rgb(0 0 0 / 0.6)",
        glow: "0 0 0 1px rgb(255 255 255 / 0.1), 0 0 24px rgb(249 115 22 / 0.15)",
        "glow-accent":
          "0 0 0 1px rgb(255 255 255 / 0.08), 0 0 24px rgb(217 70 239 / 0.2)",
        "glow-caramel":
          "0 0 0 1px rgb(255 255 255 / 0.08), 0 0 24px rgb(184 137 90 / 0.18)",
      },
    },
  },
  plugins: [],
};

export default config;
