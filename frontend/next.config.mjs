/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    // 开发期把 /api/* 代理到后端，避免跨域配置
    return [{ source: "/api/:path*", destination: "http://127.0.0.1:8000/api/:path*" }];
  },
};
export default nextConfig;
