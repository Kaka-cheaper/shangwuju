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
        // 设计系统 v2（B+D+C 混搭）
        //
        // - ink：zinc 灰阶（near-black 系），承担 95% 视觉层次
        // - accent：单色蓝（Vercel/Linear 派系），仅在「主操作 / 当前
        //   高亮 / Agent 思考态边框」三处用
        // - 仍保留 brand-orange 作为「正在进行 / 重规划」状态色（次要）
        //
        // 所有色值参考 Tailwind v3 zinc + 自调单色 accent
        // ============================================================
        ink: {
          50: "#fafafa",
          100: "#f4f4f5",
          200: "#e4e4e7",
          300: "#d4d4d8",
          400: "#a1a1aa",
          500: "#71717a",
          600: "#52525b",
          700: "#3f3f46",
          800: "#27272a",
          900: "#18181b",
          950: "#09090b",
        },
        accent: {
          50: "#eff6ff",
          100: "#dbeafe",
          200: "#bfdbfe",
          300: "#93c5fd",
          400: "#60a5fa",
          500: "#2f6feb",
          600: "#1e5dd6",
          700: "#1849b0",
          800: "#173d8a",
          900: "#172d63",
        },
        // brand：保留作为「进行中 / 重规划」状态色，仅在 ToolTracePanel
        // 与 streaming 指示器使用。不再作为品牌主色。
        brand: {
          50: "#fff7ed",
          100: "#ffedd5",
          300: "#fdba74",
          500: "#f97316",
          600: "#ea580c",
          700: "#c2410c",
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
        // C 局部动效：思考态边框流光
        shimmer: "shimmer 2s linear infinite",
        // C 局部动效：数字滚动用 spring 感
        "tick-up": "tickUp 380ms cubic-bezier(0.34, 1.56, 0.64, 1)",
        // B 范式：trace 段展开折叠
        "collapse-in": "collapseIn 200ms ease-out",
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
          // 边框流光：用 background-position 滚动渐变
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
      },
      boxShadow: {
        // 比 shadow-sm 更克制的「分隔感」，用在 hover 微浮起
        elevated: "0 1px 2px 0 rgb(0 0 0 / 0.04), 0 0 0 1px rgb(0 0 0 / 0.04)",
      },
    },
  },
  plugins: [],
};

export default config;
