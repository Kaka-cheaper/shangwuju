/**
 * 一键跑完前端 4 项静态校验：lint / typecheck / unit test / production build。
 *
 * 用法：node frontend/scripts/verify-all.mjs
 *
 * 退出码：任一步失败 → 1；全部通过 → 0。
 *
 * 不依赖：除 pnpm CLI 外无任何 npm 包。
 */

import { spawnSync } from "node:child_process";
import process from "node:process";

const steps = [
  { name: "ESLint", cmd: "pnpm", args: ["lint"] },
  { name: "TypeScript", cmd: "pnpm", args: ["typecheck"] },
  { name: "Vitest", cmd: "pnpm", args: ["test"] },
  { name: "Next build", cmd: "pnpm", args: ["build"] },
];

let failed = 0;
for (const step of steps) {
  process.stdout.write(`\n=== ${step.name} ===\n`);
  const t0 = Date.now();
  const r = spawnSync(step.cmd, step.args, {
    stdio: "inherit",
    shell: process.platform === "win32",
  });
  const dur = ((Date.now() - t0) / 1000).toFixed(1);
  if (r.status === 0) {
    process.stdout.write(`✓ ${step.name} 通过（${dur}s）\n`);
  } else {
    process.stdout.write(`✗ ${step.name} 失败（${dur}s, exit ${r.status}）\n`);
    failed++;
  }
}

process.stdout.write(
  `\n=== 总结 ===\n  ${steps.length - failed} / ${steps.length} 通过\n`,
);
process.exit(failed === 0 ? 0 : 1);
