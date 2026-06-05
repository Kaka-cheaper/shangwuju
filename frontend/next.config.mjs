const isGithubPages = process.env.GITHUB_PAGES === "true";
const repositoryName = process.env.GITHUB_REPOSITORY?.split("/")[1] ?? "shangwuju";
const rawBasePath =
  process.env.NEXT_PUBLIC_BASE_PATH ?? (isGithubPages ? `/${repositoryName}` : "");
const basePath = rawBasePath === "/" ? "" : rawBasePath.replace(/\/$/, "");

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // 本地 / Docker / FC 前端容器仍用 standalone；GitHub Pages 走静态导出。
  output: isGithubPages ? "export" : "standalone",
  basePath: basePath || undefined,
  assetPrefix: basePath ? `${basePath}/` : undefined,
  images: {
    unoptimized: isGithubPages,
  },
  trailingSlash: isGithubPages,
  env: {
    NEXT_PUBLIC_BASE_PATH: basePath,
  },
};

export default nextConfig;
