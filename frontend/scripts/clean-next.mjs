#!/usr/bin/env node
/**
 * scripts/clean-next.mjs —— 强删 .next/ 目录（绕开 Windows EPERM）。
 *
 * 背景（pitfalls 候选条目）：
 *   pnpm + Windows + Next.js 14 + output:'standalone' 组合下，
 *   `.next/standalone/node_modules/` 是 pnpm 软链/硬链结构 + 只读位，
 *   下次 `next dev` 启动时 Next 自身的 recursive-delete 会撞 EPERM 失败。
 *
 *   业界已知问题（Next.js issue #29773 / pnpm issue #2829）。
 *
 * 解法：
 *   1. 用 fs.rm 的 force:true + retries（不依赖 OS 权限位）
 *   2. 失败时降级到逐个 chmod +rwx 后再删
 *   3. 仍失败时友好提示用户手动关闭占用 .next 的进程（VSCode / 编辑器 / 之前的 dev）
 *
 * 由 `predev` / `prebuild` 自动调用；用户也可手动 `pnpm clean:next`。
 */

import { rm, chmod, readdir, stat } from "node:fs/promises";
import { existsSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = dirname(__dirname); // frontend/
const NEXT_DIR = join(ROOT, ".next");

async function chmodRecursive(p) {
  let st;
  try {
    st = await stat(p);
  } catch {
    return;
  }
  try {
    await chmod(p, 0o777);
  } catch {
    /* 静默 */
  }
  if (st.isDirectory()) {
    let entries;
    try {
      entries = await readdir(p);
    } catch {
      return;
    }
    await Promise.all(entries.map((e) => chmodRecursive(join(p, e))));
  }
}

async function tryRm() {
  await rm(NEXT_DIR, {
    recursive: true,
    force: true,
    maxRetries: 5,
    retryDelay: 100,
  });
}

(async function main() {
  if (!existsSync(NEXT_DIR)) {
    console.log("[clean-next] .next/ 不存在，跳过");
    return;
  }

  // 第一道：直接强删
  try {
    await tryRm();
    console.log("[clean-next] .next/ 已清理");
    return;
  } catch (e) {
    if (e.code !== "EPERM" && e.code !== "EBUSY" && e.code !== "ENOTEMPTY") {
      throw e;
    }
    console.log(`[clean-next] 第一次删除失败（${e.code}），尝试 chmod +rwx 后重试`);
  }

  // 第二道：递归 chmod 0o777 + 再删
  try {
    await chmodRecursive(NEXT_DIR);
    await tryRm();
    console.log("[clean-next] .next/ 已清理（chmod 后）");
    return;
  } catch (e2) {
    console.error("\n[clean-next] 仍然失败：", e2.code, e2.path || "");
    console.error("\n可能原因（按概率排序）：");
    console.error("  1. .next/ 被另一个 next dev 进程占用 → 关闭那个终端再试");
    console.error("  2. 编辑器（VSCode/WebStorm）锁了 .next/cache 文件 → 关闭编辑器再试");
    console.error("  3. 杀毒软件实时扫描 → 把 frontend/.next 加白名单");
    console.error(`\n手动方法：在 frontend/ 目录下用管理员 PowerShell 跑：`);
    console.error(`  Remove-Item -Recurse -Force .next`);
    process.exit(1);
  }
})();
