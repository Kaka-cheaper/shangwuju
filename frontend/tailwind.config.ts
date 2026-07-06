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
        // 设计系统 v4「美团黄白黑」浅色主题
        //
        // 核心思路：白底 + 美团黄强调 + 黑色文字
        // ink 色阶恢复正常方向：50=最浅，900=最深
        // 组件代码中 text-ink-800 = 深色文字，bg-ink-50 = 浅色背景
        // ============================================================
        ink: {
          50: "#ffffff",   // 页面底色 · 纯白
          100: "#f9fafb",  // 二级背景（极浅灰）
          200: "#f3f4f6",  // 卡片背景 / hover
          300: "#e5e7eb",  // 边框 / 分隔线
          400: "#9ca3af",  // 占位文字
          500: "#6b7280",  // 次要文字
          600: "#4b5563",  // 正常文字次轴
          700: "#374151",  // 标题次
          800: "#1f2937",  // 主标题 / 主文字
          900: "#111827",  // 最深文字
          950: "#030712",  // 极深强调
        },
        accent: {
          // 美团黄的深色变体（用于 Agent 思考链路 / 进度等强调）
          50: "#fffbeb",
          100: "#fef3c7",
          200: "#fde68a",
          300: "#fcd34d",
          400: "#fbbf24",
          500: "#f59e0b",  // 主 accent 琥珀
          600: "#d97706",
          700: "#b45309",
          800: "#92400e",
          900: "#78350f",
        },
        // brand：美团黄系 —— 主操作 / 按钮 / hover
        brand: {
          50: "#fffef5",
          100: "#fffcdb",
          200: "#fff8b8",
          300: "#fff085",
          400: "#ffe552",  // 美团黄主色
          500: "#ffd100",  // 核心品牌黄
          600: "#e6bc00",
          700: "#bfa000",
          800: "#997f00",
          900: "#7a6600",
        },
        // sunset：保留兼容（组件可能引用），改为黄色系
        sunset: {
          400: "#fbbf24",
          500: "#f59e0b",
          600: "#d97706",
          700: "#b45309",
        },
        // dusk：保留兼容，改为暖灰
        dusk: {
          400: "#9ca3af",
          500: "#6b7280",
          600: "#4b5563",
          700: "#374151",
        },
        // caramel：保留兼容，改为暖黄棕
        caramel: {
          50: "#fffbf0",
          100: "#fef3d0",
          200: "#fde4a8",
          300: "#fcd07b",
          400: "#f5b84a",
          500: "#e6a020",
          600: "#c78510",
          700: "#a06a08",
          800: "#7a5006",
          900: "#5c3c04",
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
        "aurora-drift": "auroraDrift 18s ease-in-out infinite",
        "aurora-drift-slow": "auroraDrift 28s ease-in-out infinite reverse",
        "spotlight-once": "spotlightPulse 2400ms ease-out 1",
        "confetti-fly": "confettiFly 1600ms cubic-bezier(0.22, 0.61, 0.36, 1) forwards",
        // 信任带（AI 思考流）§七动效参数：进场 opacity 0→1 + translateY(8px→0)
        // ~400ms cubic-bezier(0.16,1,0.3,1)（区别于既有 fade-in-up 的 6px/280ms，
        // 信任带设计终稿明确要这套参数，不复用旧值）。
        "trust-belt-enter": "trustBeltEnter 400ms cubic-bezier(0.16,1,0.3,1)",
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
        trustBeltEnter: {
          from: { opacity: "0", transform: "translateY(8px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        auroraDrift: {
          "0%, 100%": {
            transform: "translate3d(0, 0, 0) scale(1)",
            opacity: "0.5",
          },
          "33%": {
            transform: "translate3d(40px, -30px, 0) scale(1.1)",
            opacity: "0.7",
          },
          "66%": {
            transform: "translate3d(-30px, 40px, 0) scale(0.95)",
            opacity: "0.4",
          },
        },
        spotlightPulse: {
          "0%": {
            boxShadow:
              "0 0 0 0 rgba(255, 209, 0, 0.4), 0 0 0 0 rgba(245, 158, 11, 0.3), 0 8px 32px -12px rgba(0,0,0,0.1)",
            transform: "scale(0.985)",
          },
          "30%": {
            boxShadow:
              "0 0 0 8px rgba(255, 209, 0, 0.15), 0 0 0 18px rgba(245, 158, 11, 0.08), 0 16px 48px -12px rgba(255, 209, 0, 0.2)",
            transform: "scale(1.005)",
          },
          "100%": {
            boxShadow:
              "0 0 0 24px rgba(255, 209, 0, 0), 0 0 0 40px rgba(245, 158, 11, 0), 0 8px 32px -12px rgba(0,0,0,0.1)",
            transform: "scale(1)",
          },
        },
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
        elevated:
          "0 1px 3px rgba(0,0,0,0.08), 0 4px 16px -4px rgba(0,0,0,0.06)",
        glow: "0 0 0 1px rgba(255,209,0,0.3), 0 0 20px rgba(255,209,0,0.1)",
        "glow-accent":
          "0 0 0 1px rgba(245,158,11,0.3), 0 0 20px rgba(245,158,11,0.1)",
        "glow-caramel":
          "0 0 0 1px rgba(245,158,11,0.2), 0 0 16px rgba(245,158,11,0.08)",
      },
    },
  },
  plugins: [],
};

export default config;
