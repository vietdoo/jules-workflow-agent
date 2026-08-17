/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The sandbox proxy is only needed for the temporary development preview.
  allowedDevOrigins: ["3013-ivfcciay4kzmvprbzj3eq-51191bc2.sg1.manus.computer"],
  async rewrites() {
    const controlPlane = process.env.HARNESS_API_BASE ?? "http://127.0.0.1:8090";

    return [
      {
        source: "/api/:path*",
        destination: `${controlPlane}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
