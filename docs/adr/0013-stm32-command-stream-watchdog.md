# STM32 command-stream watchdog（W-3）を vendor ソースへの additive 追記で実装する（「埋められない」と G-g の裁定衝突の解消）

**Status**: proposed（2026-09-08 起草。**オペレーター裁定待ち** — 採否そのものと §Decision 2 の前提ゲート 4 点の承認が要る）

M1 の watchdog 多層停止設計のうち **W-3（MCU 側 communication watchdog）**を、stock ファームの不在確定を受けて「埋められない層」のまま恒久受容するのではなく、**vendor 公式 STM32 ソース（V3.6.5）への additive 追記**（通信途絶 → wheel target = 0）として実装する方向を裁定する。あわせて、repo 内で正面衝突している 2 つの land 済み記述 — [mode-m1/02:64](../mode-m1/02-m1-driver-and-watchdog.md)「この層は埋められない」と [mode-x-er/10:486](../mode-x-er/10-room-scale-safety-review.md) G-g「MCU command-stream watchdog を追加し、host test と実機 USB 抜線で停止を確認（PHASE-1-GATE）」 — を本 ADR で解消する。

## Context / 背景

- **守るべき故障**: ホスト（Jetson）死・プロセスクラッシュ・USB 断で、MCU は**最後の速度 PID 目標を保持して走り続ける**（fail-active・[mode-m1/02:25](../mode-m1/02-m1-driver-and-watchdog.md)）。W-1/W-2（ホスト内）も Emergency Guardian（L1・off-MCU）も、死んだリンク越しには停止を押せない（[GLOSSARY §5「command-stream watchdog」](../GLOSSARY.md)）。現行の代替は **W-4 運用**（バッテリー主電源カットオフに手を掛けたまま実施・初回は車輪浮かせ）であり、「W-3 が埋められない以上、W-4 は省略可能な備えではなく必須の層」（[mode-m1/02:67](../mode-m1/02-m1-driver-and-watchdog.md)）。
- **不在の確定**: 公式 STM32 V3.6.5 は serial command timeout を持たず、release config は `ENABLE_IWDG=0`（[mode-x-er/10:446](../mode-x-er/10-room-scale-safety-review.md)・確定根拠 = 取得済み `ROS-Driver-Board-FW-master.zip` の実ソース = [shared/02:797](../shared/02-hardware-design.md)）。
- **repo 内の裁定衝突（本 ADR の直接の動機）**: [mode-m1/02:64](../mode-m1/02-m1-driver-and-watchdog.md) は W-3 を「不在が濃厚 = **この層は埋められない**」と書く（stock ファーム前提の記述）。一方 [mode-x-er/10:456](../mode-x-er/10-room-scale-safety-review.md) は「**G-g の MCU command-stream watchdog を実装・host test・実機 USB 抜線試験で閉じる**」と実装を要求し、[同 :486](../mode-x-er/10-room-scale-safety-review.md) が PHASE-1-GATE として登録済み。両者は同時には成立しない。
- **既存の却下 2 本とその射程（本 ADR が再訪する理由）**:
  1. [shared/02:328](../shared/02-hardware-design.md)「**却下**: STM32 ファームの自前差し替えによる L0 維持 — メカナム逆運動学が STM32 側にあるため、差し替えると 4 輪配分の再実装まで背負う。Phase 1 のスコープに対して過大」— 射程は**フルスクラッチ置換**。vendor ソースをベースに watchdog だけ足す形には IK 再実装の論拠が**当たらない**（IK・±700mm/s 二段 clamp・プロトコルはそのまま残る）。
  2. [adr/0010:56](0010-raise-speed-cap-to-platform-max.md)「STM32 ファームの M1 clamp 自体を上げる: 却下 — custom FW fork と安全再検証を背負い、『stock プラットフォーム上限』という契約意味を失う」— 前段（fork 保守・安全再検証）は本件にも**当たる**（§Trade-offs で引き受ける）が、後段は当たらない: **clamp を変えない追記**なら「stock プラットフォーム上限」の契約意味（ADR-0010 の正本性）は保存される。
  3. [shared/02:798](../shared/02-hardware-design.md)「`rosmaster_V3.6.5.hex` は**書き込まない**（stock FW 置換は現行方針で不採用 = :328）」— 本 ADR が accepted になった場合、この行の意味は「stock hex は**ロールバック用 golden image** として保全する（§Decision 2-(c) の書き戻し実証に使う）」へ改訂する。
- **実現性の新規確定事実（2026-08-31 一次情報検証）**:
  - **書込手段は公式に存在する**: CH340 経由の **UART ISP**（mcuisp / flymcu・`DTR low-level reset, RTS high-level into BootLoader` 設定・**BOOT0 押下 → RESET → BOOT0 離す**で BootLoader 突入）。出典 = Yahboom 公式 wiki「2. STM32 development environment」<https://www.yahboom.net/public/upload/upload-html/1653552087/2.STM32%20Development%20environment.html>（参照日 2026-08-31）。UART ISP は MCU 内蔵 BootLoader ROM 経由のため、書込失敗後も BOOT0+RESET で常に再突入できる。
  - 公式の開発環境は **STM32CubeIDE**（対象 = STM32F103RCT6・HSE 8MHz・HCLK 72MHz。Keil への言及なし）。同 wiki。
  - 公式 GitHub（<https://github.com/YahboomTechnology/ROS-robot-expansion-board>）は **PDF 教材のみでファームソースを含まない**（参照日 2026-08-31）。ソースの実体は Google Drive 配布の `ROS-Driver-Board-FW-master.zip`（**取得済**・「仕様の一次ソース」= [shared/02:797](../shared/02-hardware-design.md)）。
  - **ライセンスは未確認**: Yahboom 配布物は Proprietary 表記があり「再配布・コード流用は個別確認まで不可」（[shared/02:812](../shared/02-hardware-design.md)）。改変して**自機に書き込む**（再配布しない）行為の可否は未検証 → §Decision 2-(b)。
- **最小安全方針との整合**: 通信断での暴走防止は「自機保護の最小線」のど真ん中であり（[GLOSSARY §11「最小安全方針」](../GLOSSARY.md)「hard 安全床は本方針下でも撤去しない」）、一般人向け安全プロセスの追加ではない。多層ガバナンスの新設でもない（既存の W-1〜W-4 体系の W-3 を埋めるだけ）。

## Decision / 決定（proposed）

1. **W-3 を vendor V3.6.5 ソースへの additive 追記として実装する方向を採る**。追記内容は「最後の有効 motion フレーム受信からの経過が閾値超 → wheel velocity target = 0」の communication watchdog のみ。**メカナム IK・±700mm/s 二段 clamp・シリアルプロトコル・auto-report は不変**（[shared/02:328](../shared/02-hardware-design.md) の却下射程＝フルスクラッチ置換とは別物、[adr/0010:56](0010-raise-speed-cap-to-platform-max.md) の「stock 上限の契約意味」は clamp 不変により保存、と裁定する）。
2. **実装着手の前提ゲート 4 点**（すべて通過するまで書き込まない）:
   - **(a) U-5**: 搭載 FW 版と `rosmaster_V3.6.5.hex` の一致確認（[adr/0010 §Open 1 :63](0010-raise-speed-cap-to-platform-max.md) の実機確認と同一セッションで可）。
   - **(b) ライセンス確認**: Proprietary 配布物を改変して自機へ書き込む（fork を repo に置かない・再配布しない）ことの可否。不可なら本 ADR は reject へ倒し W-4 恒久 + §Considered の外付け deadman を再訪する。
   - **(c) ロールバック経路の実証**: カスタム書込の**前に**、取得済み stock `rosmaster_V3.6.5.hex` を UART ISP で書き戻せることを実機で確認する（golden image の保全 = [shared/02:798](../shared/02-hardware-design.md) の改訂）。Mac/Jetson からの書込ツール選定（公式手順は Windows の mcuisp）は §Open。
   - **(d) G-g 実機試験**: host test + 実機 USB 抜線で停止確認（[mode-x-er/10:486](../mode-x-er/10-room-scale-safety-review.md)・手順は [mode-m1/02 §4](02-m1-driver-and-watchdog.md) の車輪浮かせ規律に従う）。±700 二段 clamp が追記後も生きていることの回帰確認を含める。
3. **タイムアウト値は実測で確定する（本 ADR で発明しない）**。外部提案の 0.3s は例示として扱い、W-1（ホスト 0.5s）との整合（層ごとの発火順序をどう設計するか）は実装スライスの doc で裁定する。
4. **IWDG（`ENABLE_IWDG`）の有効化は本 ADR の射程外**。IWDG は MCU 自身のハングを見る別機構であり、通信断は見ない。別項目として評価する。
5. **W-4（運用層）の緩和は自動ではない**。W-3 が実機試験まで閉じた後、[mode-x-er/10:487](../mode-x-er/10-room-scale-safety-review.md) **G-h（OPERATOR-GATE）**で P-1〜P-4 の採否とあわせて明示裁定する。
6. **accepted 時の docs 同期**（同一 PR で）: [mode-m1/02:64](02-m1-driver-and-watchdog.md) の「この層は埋められない」を「**stock では埋められない（本 ADR の additive 追記で埋める）**」へ改訂し、[shared/02:798](../shared/02-hardware-design.md) を golden image 表現へ改訂、G-g（doc10）との衝突を解消する。

## 得られるもの

- **fail-active の根治**: ホスト死・USB 断・プロセスクラッシュで MCU が自力停止する。W-1〜W-4 のうち唯一埋まっていなかった層が閉じ、停止保証がホストの生存に依存しなくなる。
- **W-4 の緩和余地**（G-h 裁定次第）: 「電源に手を掛けたまま・車輪浮かせ」の運用負担が、実証済みの機械層に置き換わりうる。
- **G-g（PHASE-1-GATE・現在「未実装」）のクローズ経路**が初めて具体化する。

## トレードオフ / Trade-offs（隠さない）

- **custom FW fork の保守**: vendor 更新への追従・差分管理を恒久に背負う（[adr/0010:56](0010-raise-speed-cap-to-platform-max.md) が却下理由に挙げた負担そのもの。本 ADR は「通信断暴走の根治」という対価がこの負担に見合うと judge する — 速度 clamp 引き上げには見合わなかった）。
- **書込リスク**: UART ISP は BootLoader ROM 経由で文鎮化リスクは低いが、ゼロではない。前提ゲート (c) のロールバック実証を先行させることで受容する。
- **安全再検証の負担**: 追記が clamp・IK に触れていないことをソース diff と G-g 回帰（上限超指令 → wire に上限超が出ない）で証明する義務を負う。
- **ライセンス次第で全体が倒れる**（前提ゲート (b)）。その場合も本 ADR の Context（衝突の存在・実現性事実）は再訪の土台として残る。

## Considered Options / 却下・非採用

- **W-4 恒久運用のみ（現状維持）**: 本 ADR が reject された場合の既定。毎回の運用負担と人的ミス（手を離す・浮かせ忘れ）に停止保証を依存し続ける。G-g は永久に「未実装」のまま＝doc10 との衝突が残る。
- **外付けハードウェア deadman（電源リレー・スマートプラグ等）**: MCU に触れない代替だが、ハードウェア設計の変更（doc02 所有トラック）・応答レイテンシ・配線の複雑化を伴う。却下ではなく**非優先**（P-1 物理 E-stop の裁定 = [mode-x-er/10:376](../mode-x-er/10-room-scale-safety-review.md) と合流して G-h で再訪可）。
- **IWDG のみ有効化**: CPU ハングしか検出せず、通信断（本件の守備範囲）を見ない。単独では W-3 にならない。
- **ホスト側ソフトのみの対策強化**: W-1/W-2 の改良はホスト死に原理的に効かない（[GLOSSARY §5](../GLOSSARY.md) の定義そのもの）。

## Open / 未決

- ライセンス可否（前提ゲート (b)・最優先）。
- **Mac/Jetson からの UART ISP 書込ツール**: 公式手順は Windows（mcuisp/flymcu）。開発環境は Mac のみ（[shared/02:407](../shared/02-hardware-design.md) と同じ制約）のため、代替 CLI（例: stm32flash）の動作検証を前提ゲート (c) に含める — ツール選定は実装スライスで（発明しない）。
- タイムアウト実測値と W-1 との層間整合（§Decision 3）。
- 搭載 FW 版（U-5・前提ゲート (a)）。
- G-g 試験手順の詳細 doc 化（[mode-m1/02 §4](02-m1-driver-and-watchdog.md) の拡張）。
- 追記ソースの置き場（repo に置かない前提での差分管理方法。[shared/02:812](../shared/02-hardware-design.md) の「repo へ commit しない」規律との整合）。

## References

- [mode-m1/02-m1-driver-and-watchdog.md](../mode-m1/02-m1-driver-and-watchdog.md) — W-1〜W-4 の正本（W-3「埋められない」:64・W-4 必須 :67・fail-active :25・G-g 手順 §4）
- [mode-x-er/10-room-scale-safety-review.md](../mode-x-er/10-room-scale-safety-review.md) — G-g :486（PHASE-1-GATE）・G-h :487（OPERATOR-GATE）・実装要求 :446 / :456・P-1 :376・「G-g 完了までは P-1 必須に近い」:383
- [shared/02-hardware-design.md](../shared/02-hardware-design.md) — FW ソース取得済 :797・hex 書き込まない（改訂対象）:798・ライセンス :812・ベンダーコードの役割 :816・フルスクラッチ置換の却下 :328
- [adr/0010-raise-speed-cap-to-platform-max.md](0010-raise-speed-cap-to-platform-max.md) — clamp 引き上げ却下 :56（射程の切り分け元）・ESP32 凍結 :25・U-5 = §Open 1 :63
- [GLOSSARY.md](../GLOSSARY.md) — §5 command-stream watchdog（comms-loss deadman）/ §11 最小安全方針
- 一次情報（参照日 2026-08-31）: Yahboom 公式 wiki「2. STM32 development environment」<https://www.yahboom.net/public/upload/upload-html/1653552087/2.STM32%20Development%20environment.html>（UART ISP / mcuisp / CH340 / BOOT0+RESET / STM32CubeIDE）・公式 GitHub <https://github.com/YahboomTechnology/ROS-robot-expansion-board>（ファームソース不在の確認）
- backlink: [adr/README.md](README.md) 一覧・[mode-m1/README.md §関連 ADR](../mode-m1/README.md)
