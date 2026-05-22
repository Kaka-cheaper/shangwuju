/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // standalone 模式：next build 后只 copy 必需 node_modules + .next/standalone
  // 镜像体积从 ~500MB 缩到 ~80MB，FC 冷启动也快
  // https://nextjs.org/docs/app/api-reference/next-config-js/output
  output: "standalone",
};

export default nextConfig;
