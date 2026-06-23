# OpenVLA Research Plan For Mode X-ER-VLA

作成日: 2026-06-22

> **状態**: 調査計画。OpenVLA の採用可否、runtime、license、input/output、今回 task への適合性は未検証。実装や依存追加の前に、ここを更新してから進める。

## 調査目的

OpenVLA を Gemini Robotics-ER と統合して使う価値があるかを、実装前に判断する。

この調査では、単純な named location への移動を OpenVLA の主用途にしない。移動
だけなら Mode X-ER の ER + L3 + Nav2 経路で扱う。OpenVLA の価値は、赤箱の中の
赤ボールを掴む、トレーに置く、ドッキングする、近接位置合わせをする、など局所的な
物体操作が入った task で評価する。詳細な制御フローは
[04-openvla-use-cases-and-control-flow.md](04-openvla-use-cases-and-control-flow.md)
に従う。

確認したい task:

- ER が作った high-level task graph を、OpenVLA が visual grounding / action candidate として補強できるか。
- 赤箱 / 青箱などの object target を視覚入力から扱えるか。
- `bot1 が赤箱へ、到達後 bot2 が青箱へ` のような順序 task に対し、VLA が L3 のどこを代行できるか。
- `赤箱へ移動 -> 赤ボールを掴む -> トレーへ置く` のような manipulation subtask を、L3 が到着判定後に VLA へ限定 request として渡せるか。
- output を既存 `Command`、または専用 `ActionCandidate` へ安全に変換できるか。
- Jetson Orin Nano / RunPod / Isaac Sim のどこで現実的に動かせるか。

## 調査項目

| 項目 | 確認内容 | 判断に使う結果 |
|---|---|---|
| model availability | model weight / API / runtime の入手方法 | local / cloud / not feasible |
| license | 商用利用、再配布、demo 公開の制約 | 商用化で使えるか |
| input shape | 画像、言語、state、robot observation の要求 | 現在の camera/state と合うか |
| output shape | action、trajectory、token、plan など | Cross-check / L3 candidate / Safety Compiler の選択 |
| ER integration | ER の `task_graph` や target 候補を VLA に渡せるか | ER+VLA 統合方式 |
| robot embodiment | training robot / action space が今回の minicar と合うか | 変換難度 |
| runtime | GPU memory、latency、batch、quantization | Jetson / RunPod 可否 |
| simulation | Isaac Sim / offline image fixture で評価できるか | 実機前 gate |
| safety | low-level action をどう止めるか | Safety Compiler 必要度 |
| observability | raw output / failure / confidence を audit できるか | 商用運用可否 |

## 初期 spike の完了条件

OpenVLA を Mode X-ER-VLA の実装候補に進めるには、最低限以下を満たす。

- 静止画像または sim camera frame を入力にして、offline で response を得られる。
- output の型を説明できる。
- ER output と VLA output を突き合わせる方法を説明できる。
- VLA が L3 のどの component を代行できそうかを判断できる。
- runtime と GPU 要件を記録できる。
- license / commercial use の確認結果を docs に残せる。
- 既存 MCP / Policy Gate を bypass しない接続案を書ける。

## 調査後の分岐

| 結果 | 次の扱い |
|---|---|
| target confidence / grounding に使える | Option A: ER Primary + VLA Cross-Check |
| Visual Resolver / action candidate 生成に使える | Option B: ER Planner + VLA L3 Candidate Generator |
| sim-only 評価が妥当 | `03-simulation-and-safety-gates.md` を拡張 |
| runtime / license が厳しい | 採用 defer として本ファイルに記録 |
