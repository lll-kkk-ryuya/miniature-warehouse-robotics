# web/console — Web Observability コンソール（Next.js static export）

Mode A/B のキャラLLM会話・稟議・司令官判断・ロボット状態・緊急を**リアルタイム観測**する観測専用 SPA。`warehouse_web_bridge`（:8646）の WebSocket / REST を消費する。設計正本: [docs/architecture/22-web-observability.md](../../docs/architecture/22-web-observability.md) §15。

## スタック
- **Next.js App Router・static export（`output:'export'`）= CSR SPA**（doc22 §2.1）。build 成果物 `out/` を **web_bridge が同一オリジン :8646 配信**（Jetson に Node 常駐させない）。
- TypeScript(strict) + Tailwind + Zustand + virtua（仮想化）。
- `web/console` と `web/e2e` は **2 つの独立 sibling npm project**（doc22:340・workspace 化しない）。

## 開発
```bash
cd web/console
npm install
npm run dev        # next dev :3000（HMR）。gateway は別途 :8646 で起動（CORS は dev overlay で許可）
# 別オリジンの gateway を指す場合: NEXT_PUBLIC_GATEWAY_URL=http://localhost:8646 npm run dev
```

## ビルド（CI / prod）
```bash
npm run lint && npm run typecheck && npm run build   # web-quality 三点（doc22:348）
# → out/ を生成。web_bridge.static_dir を out/ に向けると同一オリジン配信。
```

## 構成
- `app/`：`layout.tsx`（`ConnectionProvider` + `RunHeader` を1回 mount・doc22:329）／`/live`（既定画面）／`/runs`（picker + 読み取り replay）。
- `providers/` `hooks/`：単一 WebSocket ライフサイクル（`/config` bootstrap → `/ws?since_seq` backfill→live・exp backoff+jitter 再接続・doc22:235）。
- `store/useStore.ts`：Zustand。**seq が唯一の apply 順序**（dedup・doc22:160,:330）。snapshot=last-write-wins / 会話・稟議・司令官・緊急=append。
- `components/`：`RunHeader`（canned/live バッジ）・`ConversationTimeline`（virtua・idle/empty）・`RingiFlow`・`CommanderDecision`・`SituationFleet`（battery band）・`EmergencyPanel`・`MapView2D`（SVG・9 KNOWN_LOCATIONS）・`ModeGate`（Mode C で会話/稟議 hide・doc22 §12.1）・撮影 skin（`PresentationToggle`）。
- `lib/`：types（ObsEvent）・config（`/config` + ws URL 解決）・api（`/runs` `/events`）・locations（地図座標）・format。

## 観測専用（R-26）
ブラウザ→ロボットの操作経路を持たない。`/config` は secret を返さない（doc22:254）。生 ROS グラフ（/scan・/map・costmap）はブラウザに流さない（doc22:25）。

## 残（follow-up）
- **稟議グルーピングの本質解（doc22 §14 additive・llm-bridge track / contract）**: `/negotiation/turn`・`/negotiation/abort` の wire には `negotiation_id`/`gen_id` が載らない（`negotiation_messages.py`）。現状 store は「キーレスな turn/abort を**直近 active 稟議に紐付ける heuristic**」で対応（稟議は逐次なので実用上正しい）。`/negotiation/turn` に `negotiation_id` を載せる additive 契約が入れば heuristic を外せる。
- **run 境界**: envelope `run_id` 変化で store を reset 済み（per-run seq 再開対応・doc22:309）。正式には S2.5 `/run/header` が run 境界を通知（現状は synthetic run_id で代替）。
- web-quality / web-e2e の **CI 配線は governance**（`.github/**`・doc22:348）。
- `/runs?run_id` deep-link + live パネルへの replay-scrub（v1 は client state の読み取り表）。
- Langfuse deep-link の実 URL（`NEXT_PUBLIC_LANGFUSE_URL` 未設定時は trace_id 表示のみ）。
- KNOWN_LOCATIONS を `/config` 配信化（現状は base.yaml 由来の暫定座標を埋め込み）。
- dev は `next dev` 既定 :3000（別ポートでも :8646 gateway を指す）。非標準構成は `NEXT_PUBLIC_GATEWAY_URL`。
