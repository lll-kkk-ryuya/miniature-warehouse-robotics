/** @type {import('next').NextConfig} */
// Static export (CSR SPA) — warehouse_web_bridge serves the build output `out/` via FastAPI
// StaticFiles at the same origin :8646 (doc22 §2.1/§16). No Node server at runtime on Jetson.
const nextConfig = {
  output: "export",
  // next/image's optimizer needs a server (incompatible with export) → plain <img>/SVG only.
  images: { unoptimized: true },
  // emit `/<route>/index.html` so FastAPI StaticFiles resolves /live, /runs without rewrites.
  trailingSlash: true,
};

export default nextConfig;
