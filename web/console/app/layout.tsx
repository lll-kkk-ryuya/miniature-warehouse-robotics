import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "@/providers/Providers";
import { RunHeader } from "@/components/RunHeader";

export const metadata: Metadata = {
  title: "倉庫観測コンソール",
  description: "Mode A 会話・稟議・司令官判断のリアルタイム観測（observe-only・doc22）",
};

// Root layout: the connection provider + run header mount ONCE here (doc22:329) so the single
// WebSocket and the Zustand store survive client navigation between /live and /runs. ja-first.
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ja">
      <body>
        <Providers>
          <div className="flex h-screen flex-col">
            <RunHeader />
            <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
          </div>
        </Providers>
      </body>
    </html>
  );
}
