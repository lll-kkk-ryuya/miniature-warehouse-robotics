# 開発機（Mac）↔ Jetson 実機のアクセス経路（dev link）

作成日: 2026-08-28 ／ 改訂: 2026-08-30（**§9 追加＝現行の正本**。mDNS 直結・常時通電運用・`jetson` CLI。
§3-B トンネルは dormant fallback へ退役、§5 pull 型 agent はセキュリティ理由で恒久廃止。
同日 §9.6 board 基盤 provisioning（NVMe `/ssd`＝B案・ROS 2 Humble・repo clone）と §9.7 外出先
Tailscale 経路を追記）

> **目的**: bring-up 中に「**Mac から Jetson を操作する**」ための経路を正本化する。初回接続で
> **Mac → Jetson の新規接続だけが届かない**問題に当たり（原因 = macOS のローカルネットワーク権限）、
> その切り分けと対処・動作実績のある fallback をここに固定する。**次回以降は本 doc の §9 をなぞるだけで再接続できる**状態にする。
>
> **本 doc のスコープ**: 開発時アクセス（ssh / コマンド実行）に閉じる。**prod 常駐化は
> [setup/jetson-deploy.md](../setup/jetson-deploy.md)**、**実機投入前ゲート G0-G7 は
> [01-fidelity-and-validation.md §4](01-fidelity-and-validation.md)**、**物理手順の順序は
> [mode-m1/03:56](../mode-m1/03-joystick-teleop-bringup.md)** が正本。ここでは重複させない。
>
> **設計正本（着手前 Read 済・file:line）**:
> - フラッシュ経路・QSPI: [shared/02:409](../shared/02-hardware-design.md) / JetPack 6.2 系＝Humble ネイティブ: [shared/02:412](../shared/02-hardware-design.md)
> - Super 化（`nvpmodel -m 2` + `jetson_clocks`）: [shared/02:150](../shared/02-hardware-design.md)
> - distro 決定: [ADR-0008:16](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md)（Humble / Ubuntu 22.04 を全系の既定）
> - 安全ゲート（motion unit を勝手に enable しない）: [setup/jetson-deploy.md:26](../setup/jetson-deploy.md)
> - secrets / 鍵の扱い: [.claude/rules/safety.md](../../.claude/rules/safety.md) / [.claude/rules/environments.md](../../.claude/rules/environments.md)

---

## 1. 実機の同定情報（2026-08-28 初回接続時の実測）

| 項目 | 値 | 備考 |
|---|---|---|
| ホスト名 / ユーザー | `minicar` / `ruyuya` | 端末プロンプトで確認 |
| 無線 IF / MAC | `wlP1p1s0` / `50:2e:91:95:9c:23` | 有線 `enP8p1s0`（`ac:3a:e2:12:3e:c8`）は DOWN＝ケーブル未接続 |
| IP（DHCP） | `192.168.11.12/24` | 固定化は未実施＝**再起動で変わりうる**（§7 残課題） |
| sshd | active / enabled・`0.0.0.0:22` で待受 | 本 doc の手順で導入済み |
| 開発機（Mac） | `192.168.11.11`・**`en0` = Wi-Fi** | Private Wi-Fi Address 使用（ARP 上は `ba:38:05:49:3f:ac`） |
| ルータ | Buffalo **WSR-3000AX4P** Ver 1.22・ルーターモード・LAN `192.168.11.1/24` | プライバシーセパレーターは 2.4/5GHz とも**使用しない**・EasyMesh 有効・ゲストポート未使用 |

> `hostname -I` / `ip -br link` / `ip -br a` で再取得できる。**記憶で書かず毎回実測する**
> （[.claude/rules/docs-first.md](../../.claude/rules/docs-first.md) §引用）。
> **`networksetup -getairportnetwork en0` は SSID を返さないことがある**（"not associated" と誤答）。
> Mac の SSID は `system_profiler SPAirPortDataType` で確認する——この誤答を根拠に
> 「Mac は有線」と誤判定した（§2 の教訓）。

## 2. 症状と根本原因（切り分けの記録）

**症状**: Jetson → Mac は通るのに、**Mac → Jetson の新規接続だけが 100% 失敗**
（`ping` 全損・`ssh` は即 `No route to host`・`curl` は **1ms で** 失敗）。

**根本原因**: **macOS（Sequoia）の「ローカルネットワーク」プライバシー権限（TCC）**。
コマンドを実行するアプリ（Claude Code / ターミナル）にこの権限が無いと、**そのアプリの通信だけ**が
LAN 上の機器へ届かず `No route to host` になる。**ネットワーク機器側の問題ではない。**

これが観測のすべてを説明する:

| 観測 | 権限モデルでの説明 |
|---|---|
| Jetson の tcpdump に **Mac 発パケットが 0 件** | アプリの送信が OS に拒否され、そもそも送出されない |
| Jetson の ping に対する **Mac の応答は届く** | 応答はカーネルが生成＝アプリ権限の対象外 |
| ルータ `192.168.11.1` へは繋がる | ゲートウェイは権限の対象外 |
| ARP は解決できるのに ping が返らない | ARP はカーネル処理・ICMP 応答だけがアプリに届かない |
| **Safari では** `http://192.168.11.12:8080` が**表示できる** | Safari は権限を持つ＝**アプリ単位の差**（決定的証拠） |

**決定的テスト（再現手順）**: Jetson で `python3 -m http.server 8080` を起動し、
**同じ Mac の Safari と、疑わしいアプリのシェルから同じ URL を叩いて比較する**。
Safari だけ成功したら権限問題で確定。

**除外できたもの（すべて実測）**: Jetson の ufw（未インストール）/ iptables（`-P INPUT ACCEPT`）/
sshd の bind（`0.0.0.0:22`）/ ARP の陳腐化（MAC 一致）/ Mac のルーティング（en0 直結・reject 無し）/
Claude Code のサンドボックス（`dangerouslyDisableSandbox` でも同じ）/ 無線の省電力（`power_save off` でも同じ・
0.2s ping で起こし続けても 0 件）/ IPv6（同じ）/ SSID 差（WPA3・非 WPA3 の双方で失敗）/
**ルータのプライバシーセパレーター（管理画面で「使用しない」を確認）**。

> **誤診の記録（同じ穴に落ちないために）**: tcpdump で「Mac 発 0 件」を見た時点で
> **経路上の機器（ルータ）を疑い、送信元 Mac の OS 権限を最後まで疑わなかった**。
> Claude Code のサンドボックスを無効化して除外したつもりだったが、**それはアプリ層のサンドボックスであり
> OS の TCC 権限とは別物**。教訓 = **「送信側で 1ms で失敗する」ものはネットワークではなくホストを疑う**。

## 3. 対処（2 通り・当時の記録。**2026-08-30 以降の実働は直結＝§9**）

### A. 権限を許可する（正攻法・ただし本環境では未解決）

1. **システム設定 → プライバシーとセキュリティ → ローカルネットワーク**
2. コマンドを実行するアプリ（**Claude Code**・使うターミナル）を **ON**
3. **アプリを再起動する**

> **解決（2026-08-30 実測）**: Mac → Jetson の直接 ssh が**通るようになった**（Claude Code のシェルから
> `ssh ruyuya@minicar.local` 成功・以後これが実働経路＝§9）。**どの操作が効いたかは特定できていない**
> （TCC 設定＋アプリ再起動の遅効か、途中の DHCP/経路変化か）。再発したら §2 の決定的テスト
> （Safari と対象シェルで同一 URL 比較）で切り分け直す。

### B. 逆 SSH トンネル（**退役＝dormant fallback**・2026-08-30）

> **退役の実測根拠**: トンネルは Jetson が Mac を**アドレスで**叩く構造のため DHCP 変化に弱く、
> 実際に**認証失敗の 5 秒間隔リトライループ**（`Too many authentication failures`・restart counter 32）
> に陥っていた。直結（§9）確立に伴い `mwr-setup.sh` が **disable**（unit はボードに残置）。
> unit とスクリプトの写しは [`deploy/dev/jetson-link/mwr-tunnel.service`](../../deploy/dev/jetson-link/mwr-tunnel.service) /
> [`mwr-tunnel`](../../deploy/dev/jetson-link/mwr-tunnel) に保全。外出先 LAN 等で直結が使えないときのみ
> `sudo systemctl enable --now mwr-tunnel` で復活させる。以下は当時の設計記録。

Jetson → Mac 方向は権限の影響を受けないため、**Jetson から Mac へトンネルを張り、
開発機側は `127.0.0.1:2222`（ループバック）へ繋ぐ**。ループバックは
ローカルネットワーク権限の対象外なので、A が直らなくても確実に動く。

```
Mac                                        Jetson
  sshd(:22) ←── ssh -R 127.0.0.1:2222:localhost:22 ── mwr-tunnel.sh（常駐・自動復旧）
  ssh -p 2222 ruyuya@127.0.0.1 ──────────────────────→ Jetson の sshd(:22)
```

**前提**: Mac の **リモートログインが有効**（システム設定 → 一般 → 共有）。

**Mac 側**: `~/.ssh/authorized_keys` に Jetson の公開鍵を**用途限定**で 1 行追加する。
シェルを取らせず、このポート転送だけを許可する:

```
restrict,port-forwarding,permitlisten="127.0.0.1:2222" ssh-ed25519 <Jetsonの公開鍵> mwr-jetson-tunnel
```

**Jetson 側**: `/usr/local/bin/mwr-tunnel`（mDNS → LAN IP の順に Mac を解決して `ssh -N -R` を張る
keeper。写し＝[`deploy/dev/jetson-link/mwr-tunnel`](../../deploy/dev/jetson-link/mwr-tunnel)）を
`mwr-tunnel.service`（`Restart=always`）で常駐させる。**ブート後も自動復帰する**（2026-08-28 実測）。
現在は `mwr-setup.sh` step 1 で **disable 済み**＝復活は `sudo systemctl enable --now mwr-tunnel`。
Mac 側は `~/.ssh/config` に `Host jetson-tunnel`（`HostName 127.0.0.1` / `Port 2222`）を足し、
`JETSON_HOST=jetson-tunnel JETSON_TUNNEL_ADDR=127.0.0.1 JETSON_TUNNEL_PORT=2222` で CLI を向ける
（ssh の実接続は `JETSON_HOST` に従うため、probe 用 2 変数だけでは切り替わらない）。
なお keeper は `StrictHostKeyChecking=no`＋`UserKnownHostsFile=/dev/null` で host key 検証を
落としている（LAN 内前提）——復活させる際はこの性質を了解の上で使う。

**接続**:

```bash
ssh -i ~/.ssh/mwr_jetson -p 2222 ruyuya@127.0.0.1
```

**撤去**: Jetson で `sudo systemctl disable --now mwr-tunnel`、Mac の `authorized_keys` から
`mwr-jetson-tunnel` の行を削除する。

## 4. ssh 直結の手順（A が解決したときの形）

```bash
# 1) 開発機で鍵を1本作る（初回のみ・パスフレーズ無し = 自動化用）
ssh-keygen -t ed25519 -N '' -C 'mwr-mac->jetson' -f ~/.ssh/mwr_jetson

# 2) Jetson 側へ公開鍵を入れる（Jetson の端末で1回）
#    ssh が通る環境なら ssh-copy-id -i ~/.ssh/mwr_jetson.pub ruyuya@<IP> でよい

# 3) 接続
ssh -i ~/.ssh/mwr_jetson ruyuya@<IP>
```

- **秘密鍵はリポジトリに置かない**（`~/.ssh/` に置く。[.claude/rules/safety.md](../../.claude/rules/safety.md)）。
- IP はルータの DHCP 予約（MAC `50:2e:91:95:9c:23`）で固定すると `<IP>` の再確認が要らなくなる。
- **`systemctl enable --now warehouse.target` は G0 未通過では実行しない**
  （[setup/jetson-deploy.md:26](../setup/jetson-deploy.md)）。ssh が通ることと motion を有効化することは別。

## 5. ~~pull 型 agent~~（**恒久廃止**・2026-08-30）

> **廃止**: agent は「Mac の `http://<IP>:8000/cmd` を 3 秒ごとに取得し **root の `sh` で無条件実行**する」
> 平文 HTTP・認証なしの経路だった。直結（§9）確立後はリスクだけが残るため、ボードからバイナリを削除し
> （監査後・控えは Jetson の `/root/mwr-agent.removed.*`）、リポジトリの実装（`serve.py` / `send.sh`）も
> 削除した（git 履歴 `ffbb190` / `517e663` に残る）。**再導入しない**——同等の必要が出たら ssh 経由で設計し直す。
>
> **残す教訓（掲示板 ≠ 命令履歴）**: agent 再起動時に「現在掲示中のコマンド」を実行する設計だと、
> **再起動が前回コマンドの再実行になる**（2026-08-28: 検証用 `reboot` が残っていて実機が再起動した実例）。
> 受け取り側が「起動前の掲示を無視して ID だけ引き継ぐ」形にして初めて安全になった。
> pull 型の何かを再び作るときはこの性質を最初から入れる。

## 6. 初回ブートのベースライン実測（2026-08-28・robot-free）

| 項目 | 実測値 | 判定 |
|---|---|---|
| L4T | `R36.4.4`（`/etc/nv_tegra_release`） | JetPack **6.2 系**＝[shared/02:412](../shared/02-hardware-design.md) の想定どおり |
| OS | Ubuntu **22.04.5 LTS (jammy)** | ✅ [ADR-0008:16](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md)（Humble ネイティブ）と整合 |
| bootloader / QSPI | `Current version: 36.4.4`・slot B | ✅ **36.0 以降＝更新不要**（[shared/02:409](../shared/02-hardware-design.md)） |
| 起動デバイス | **`/dev/mmcblk0p1`（microSD 59.5GB・22G 使用）** | 当時 ⚠️ → **2026-08-30 B案で決着＝rootfs は microSD のまま**（§9.6・§7） |
| NVMe SSD | **`nvme0n1` 931.5GB `KIOXIA-EXCERIA PLUS G3`**（`lspci`: `0004:01:00.0 Non-Volatile memory controller`） | 当時は生ディスク → **2026-08-30 `/ssd` データディスク化済**（GPT+ext4・§9.6） |
| 電力モード | `NV Power Mode: 25W`（mode **1**） | ⚠️ 当時未実施 → **2026-08-30 適用済**（mode 2 = MAXN_SUPER・再起動後も維持を実測・§9） |
| メモリ | total 7.4Gi / available 5.0Gi / zram swap 3.7Gi | G1 メモリゲートの基準線（スタック未起動時の値） |
| ROS | `/opt/ros` 無し＝**未インストール** | 当時 → **2026-08-30 Humble 導入済**（§9.6） |
| USB | Realtek hub ×2 / IMC Bluetooth / Logitech receiver | 当時は拡張ボード（CH340）・LiDAR・HP60C とも**未接続** → **2026-09-09: 拡張ボード CH340（`1a86:7523`）は接続済だが `ch341` ドライバ不在で `/dev/ttyUSB*` が生成されなかった**（out-of-tree 導入で解消＝§10） |
| ディスク | `/` 57G 中 22G 使用（41%） | microSD 上 |

> **この表は「実機で初めて判明したこと」の記録**であり、合否基準は
> [01-fidelity-and-validation.md §4](01-fidelity-and-validation.md) の G0-G7 が正本。ここでは値のみ持つ。

## 7. 残課題・未決（隠さない）

- ~~§3-A（ローカルネットワーク権限）が直らない~~ → **解決（2026-08-30 実測・§3-A）**。
  Mac → Jetson の直接 ssh が通り、実働は mDNS 直結（§9）。
- ~~トンネル / pull 型 agent の常駐整理~~ → **決着（2026-08-30）**: トンネルは dormant fallback（§3-B）、
  pull 型 agent は恒久廃止（§5）。実働は mDNS 直結＋常時通電（§9）。
- ~~`# TODO(次)` NVMe SSD への rootfs 移行が未実施~~ → **決着（2026-08-30・B案＝データディスク）**:
  **rootfs は microSD のまま維持し、NVMe は `/ssd` データディスク**として provisioning 済（§9.6）。
  理由 = rootfs 移行は boot 構成を触る作業で、失敗時に**物理アクセスが必須**になり、常時通電・
  物理操作ゼロ運用（§9.1）と正面衝突する。重い I/O（repo/ws build・docker・地図・録画）は `/ssd` に
  載せたため移行の便益も減った。**移行は物理立会いのある日の任意作業へ降格**。実施する場合の方式は
  実機上 rootfs クローン（[ADR-0008 追記（2026-08-30）](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md)。
  公式 JetPack 7.2 ISO は Ubuntu 24.04 ゆえ採用不可＝同 2026-08-28 追記）。UEFI `BootOrder` は
  すでに SSD 最優先（`Boot0008` = KIOXIA）で、microSD を無傷で残したまま試せる。
- ~~Super 化未実施~~ → **完了（2026-08-30・`mwr-setup.sh` step 8）**。再起動後も MAXN_SUPER 維持を実測。
- `# TODO` **IP が DHCP のまま**（`192.168.11.12`）。ただし §9 の mDNS 直結（`minicar.local`）により**再接続のたびの IP 確認は不要になった**。DHCP 予約（MAC `50:2e:91:95:9c:23`）は mDNS 不調時の保険として依然推奨。
- [01-fidelity-and-validation.md](01-fidelity-and-validation.md) の **G0-G7 は旧世界（ESP32×2 / MS200 / 2台）前提**のまま＝M1 単騎への rescope は別 PR（[mode-m1/README.md:49](../mode-m1/README.md)）。本 doc の §6 はその rescope 後に読み替えが要る。
- `# TODO` **初回 `v0.1.0` タグ未発行**（bring-up は main HEAD 暫定＝§9.6 表 row5 に記録済）。発行は
  [setup/jetson-deploy.md §5→§6](../setup/jetson-deploy.md)（unit 導入 → systemd enable = prod 昇格）の**前**に
  行う（doc19 のタグ pin 規約を昇格時点から満たす。軽い運用でよい＝過剰な儀式化はしない）。

## 8. 運用上の注意：電源の切り方

**電源ケーブルを抜いて止めない。必ずシャットダウンしてから抜く。**

```bash
jetson halt          # Mac から（正準・§9。安全停止ラッパー経由・YES 確認付き）
```

> **素の `sudo poweroff` は使わない**（2026-08-30〜）。`jetson halt` は `/usr/local/sbin/mwr-shutdown`
> 経由で、ros2 / docker が稼働中なら **SAFE-STOP-FAILED で拒否**する（fail-closed）。素の poweroff は
> このゲートを迂回してしまう。GUI の電源オフも同様に避ける。

理由: Linux はディスクへの書き込みをいったんメモリに溜め、シャットダウン時にまとめて書き出す。
通電のまま切ると、この書き出しの途中で電源が落ち、**ファイルシステムと microSD 内部の管理情報が
壊れる**ことがある（摩耗とは別の壊れ方で、新品でも起こる）。Raspberry Pi / Jetson で
「SD が突然死んだ」と言われる故障の主因はこれ（§6 の microSD 依存と合わせて読む）。

- **完全に停止したことを確認してから**ケーブルを抜く（ファンが止まる）。
- **例外＝人身安全**: 安全レビュー（[mode-x-er/10 M-5/P-1](../mode-x-er/10-room-scale-safety-review.md)）の
  緊急電源断はこの限りではない（人身安全がファイルシステムより優先）。
- 走行フェーズでは**バッテリー切れによる電源断**が同じ事故を起こす＝
  [ADR-0005](../adr/0005-l0-battery-brownout-floor.md)（battery brownout floor）と地続きの問題。
- 開発中は **Jetson 上の変更をこまめに commit / push** する。Remote-SSH で実機を直接編集する
  構成では「実機にしか存在しないコード」が生じるため、ストレージ故障が作業損失に直結する。

## 9. 常時通電運用と `jetson` CLI（2026-08-30 確定・**現行の正本**）

### 9.1 物理制約と決定

このボードで poweroff は**一方通行**である: 接続は Wi-Fi のみ（`wlP1p1s0`・Wake-on-LAN 不可）、
Orin Nano に BMC は無く、スマートプラグは不採用。halt 後の復帰は **DC ジャックの物理的な抜き挿しのみ**
（挿しっぱなしのままでは soft-off から起動しない）。

→ **決定: 常時通電**。アイドル実測 DC 入力 約5W（INA3221・壁側は PSU 損失込みで 5–7W 程度）・CPU/GPU 48-49°C・ファン PWM 66/255（2026-08-30）。
sleep/suspend は事故防止のため **mask**（スリープ＝Mac から見ると halt と同じ到達不能）。
電気代（月 4–5kWh 程度）と引き換えに、電源への物理操作を日常から排除する。

### 9.2 経路: mDNS 直結

`minicar.local`（avahi 有効を実測）で**名前解決**して直結する。DHCP でアドレスが変わっても壊れない
（§3-B トンネルが IP 直叩きで壊れた故障クラスの根治）。`~/.ssh/config` の形:

```
Host jetson minicar
  HostName minicar.local
  Port 22
  User ruyuya
  IdentityFile ~/.ssh/mwr_jetson
  UserKnownHostsFile ~/.ssh/known_hosts_jetson
  StrictHostKeyChecking accept-new
  ServerAliveInterval 15
  ServerAliveCountMax 4
  ControlMaster auto
  ControlPath ~/.ssh/cm-%r@%h-%p
  ControlPersist 10m
```

ControlMaster は VS Code Remote-SSH（1 window = 2 接続）と `jetson ssh` の体感を速くする。

### 9.3 `jetson` CLI の意味論（凍結）

実体は Mac の `~/.local/bin/jetson`（写し＝[`deploy/dev/jetson-link/jetson`](../../deploy/dev/jetson-link/jetson)。
更新はリポジトリ側を直して `cp` で配る）。

| コマンド | 意味 | 電源 | 物理操作 |
|---|---|---|---|
| `jetson on` | 作業開始（リンク検証＋dev-readiness 報告） | 触らない | 不要（常時成功・即時） |
| `jetson off` | 作業終了（ワークロード停止のみ） | **切らない** | 不要 |
| `jetson reboot` | 安全停止 → 再起動。**両縁検証**（落ちたこと＋戻ったこと） | 再投入 | 不要 |
| `jetson halt` | 真の電源断。TTY では `YES` 入力必須・非 TTY は `--yes` 必須 | 切る | **復帰に DC 抜き挿し** |
| `jetson status` | 状態のみ・副作用なし | 触らない | 不要 |
| `jetson ssh [cmd]` | シェル / 単発コマンド（リンク事前検証つき） | 触らない | 不要 |

リンク状態は **6 値**: `up` / `down` / `stale` / `auth` / **`nolink`** / **`noname`**。
- `nolink` = **Mac 側に LAN が無い**（デフォルトルート不在等・断定的にローカル故障）。ボードについて
  何も主張しない（exit 2）。`halt`/`reboot` の完了証拠として扱わない（「halt 中に Mac の Wi-Fi が
  落ちる → shutdown 完了と誤報」の穴を閉じる）。
- `noname` = **Mac にネットワークはあるが名前が引けない**。mDNS はボードと共に死ぬため、これは
  「**ボードが停止/起動中**」（halt 後の再通電では avahi が上がるまで ~60s 引けない）と「Mac が別
  ネットワークに居る」の**両義**。`on` はこの状態を**待つ**（名前は avahi 復帰と同時に戻る）。
  `halt` は逆に「もう落ちている」と断定しない（fail-closed）。clean な halt は mDNS goodbye で
  名前が先に消えるため、`noname` は shutdown 完了待ちでは「落ちた」側に数える。

exit code: `0` ok / `1` down / `2` unknown（fail-closed: 不確かなら down と**言わない**）/
`3` shutdown 拒否 / `4` timeout / `5` ローカル前提欠落 / `64` usage。

**実測（2026-08-30）**: `off`→`on` 0.3 秒 ×3 回連続成功・`reboot` 全周 **72 秒**（落ち 8s・復帰 63s・警告ゼロ）。

### 9.4 ボード側セットアップ（`mwr-setup.sh`・idempotent）

正本: [`deploy/dev/jetson-link/mwr-setup.sh`](../../deploy/dev/jetson-link/mwr-setup.sh)。
ボードのホームへ `scp` して `sudo sh ~/mwr-setup.sh`（再実行安全・適用済み項目は skip 表示）。2026-08-30 適用済み:

| # | 内容 | 目的 |
|---|---|---|
| 1 | `mwr-tunnel` disable（unit は残置） | 直結へ移行（§3-B） |
| 2 | `mwr-agent` 削除（監査後・控え `/root/`） | 無認証 root 経路の除去（§5） |
| 3 | sleep/suspend/hibernate を mask | 常時到達可能の担保（§9.1） |
| 4 | 永続ジャーナル（`SystemMaxUse=100M`） | 予期しない再起動の証拠保全 |
| 5 | `/usr/local/sbin/mwr-shutdown` / `mwr-reboot` 設置 | fail-closed 安全停止（§8） |
| 6 | `/etc/sudoers.d/10-mwr`（下記） | パスワードなし運用の最小権限 |
| 7 | `ruyuya` を docker group へ | `docker ps` の可視化（`jetson off` が使う） |
| 8 | `nvpmodel -m 2`（MAXN_SUPER） | Super 化（§6 の ⚠️ 解消・再起動でも維持） |

sudoers は**この 3 つ・引数なし限定**のみ NOPASSWD（`""` は sudoers(5) の「引数なしのみ許可」構文。
`/etc/sudoers.d` はドットを含むファイル名を無視するため名前は `10-mwr`）:

```
ruyuya ALL=(root) NOPASSWD: /usr/local/sbin/mwr-shutdown ""
ruyuya ALL=(root) NOPASSWD: /usr/local/sbin/mwr-reboot ""
ruyuya ALL=(root) NOPASSWD: /usr/sbin/nvpmodel -q
```

`jetson status`/`on` の readiness は同一 SSH 往復で **wtmp（`last -x reboot shutdown`）** も読み、
**直前ブートが shutdown 記録なしで終わっていれば警告**する（電源瞬断 / brownout / 熱 / panic ＝
microSD 書き込み中の電源断の疑い）。意図的な `jetson reboot`/`halt` は記録を書くため無音・履歴不足は
UNKNOWN と表示（clean と断定しない）。警告は `JETSON_UPTIME_WARN`（既定 86400s）以内の新しい
再起動のみ・それより古い unclean は情報表示に格下げ（未対応の古い事象が warning を恒久に汚さない）。

安全停止ラッパーは `mwr_safe_stop()`（[`mwr-setup.sh`](../../deploy/dev/jetson-link/mwr-setup.sh) 内
`mwr-stop-common`）が **ros2 プロセス / docker コンテナ稼働中なら停止を拒否**する。モーター系が
載ったらこの関数に実停止ルーチンを足す（fail-closed の蝶番はここ 1 箇所）。

### 9.5 復旧手順（到達不能になったら）

1. `jetson status` — fail-closed の切り分け（down / stale / auth / **nolink**＝Mac 側 LAN 断 / **noname**＝名前が引けない を区別して表示）
2. `ping minicar.local` — mDNS ごと死んでいるか
3. ルーターの DHCP クライアント一覧 — 新しいアドレスで生きていないか
4. それでも駄目なら物理確認: ファン・LED。halt 済みなら DC 抜き挿し 10 秒（§8 の注意を読んでから）

### 9.6 board 基盤 provisioning（`mwr-provision.sh`・2026-08-30 適用・再起動またぎ実証済）

正本: [`deploy/dev/jetson-link/mwr-provision.sh`](../../deploy/dev/jetson-link/mwr-provision.sh)（idempotent・
どの時点で中断しても再実行で収束する。破壊操作は「**空の** NVMe の初期化」1 点のみで、実行時に lsblk を
表示して `FORMAT` をタイプするまで何も書かないゲート付き。ホスト名 `minicar`・rootfs=microSD の
二重ガードで別マシン誤爆を拒否）。配布は `scp` → `ssh -t jetson 'sudo sh ~/mwr-provision.sh'`。

| # | 内容 | 検証済み事実（2026-08-30 実測） |
|---|---|---|
| 1 | NVMe 931.5GB → GPT + ext4 **`/ssd`**（fstab UUID + `nofail` + device-timeout 30s） | 再起動またぎで自動マウント。`nofail`＝SSD 死亡でも boot は人質にならない |
| 2 | `/ssd/{warehouse,maps,recordings,bags}`（`ruyuya` 所有）+ `/ssd/docker`（root） | recordings は [doc22 未決 #7](../architecture/22-web-observability.md) の SSD 置き場に対応 |
| 3 | docker data-root → `/ssd/docker` ＋ `docker.service` に `RequiresMountsFor=/ssd` drop-in | **fail-closed**: SSD 不在なら docker は起動しない（microSD への silent 書込みを構造的に排除）。boot 順序実測: `ssd.mount` → **+8s** → `docker.service` |
| 4 | **ROS 2 Humble**（`ros-humble-ros-base` + `ros-dev-tools` + `ros-humble-joy` + `python3-serial`・公式 ros2-apt-source 方式） | 実機 Ubuntu 22.04.5 jammy ＝ [ADR-0008:16](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md) と一致。systemd/udev 先行 upgrade + `apt --no-remove` で nvidia-l4t 系の削除カスケードを遮断 |
| 5 | repo clone `/ssd/warehouse` → symlink **`/opt/warehouse`**（規約パス＝[setup/jetson-deploy.md:47](../setup/jetson-deploy.md)） | `v0.x` タグ未発行のため **main SHA 固定が暫定**（タグ発行後にタグ固定へ）。ws **16 pkg `colcon build` 成功**・`ros2 pkg list` で warehouse 15 pkg 可視・`clamp_body_velocity`（L0'）import 確認 |
| 6 | `/etc/warehouse/warehouse.env`（`ROS_DISTRO=humble`） | 雛形 [env.example の `jazzy` 記述](../../deploy/jetson/env/warehouse.env.example) を踏まない。`TRAFFIC_MODE` / `MAP` は**未決マークのまま**（prod traffic 変更は安全レビュー PR＝[mode-m1/01](../mode-m1/01-mode-boundary-and-traffic.md)） |

補足: `ruyuya` へ `input` / `dialout` グループ付与（joy の `/dev/input/event*`・M1 シリアルの
`/dev/ttyUSB*` 用・次ログインから有効）。ただし `dialout` 付与だけでは `/dev/ttyUSB*` は生えない（ドライバが先＝`ch341` 不在・§10）。G1 ベースライン = idle available RAM **6179MB**（スタック
未起動・本計測は G1 ゲートで実施）。systemd unit は **install も enable もしていない**
（[setup/jetson-deploy.md:26](../setup/jetson-deploy.md) の安全ゲート準拠・actuation 経路なし）。

### 9.7 外出先経路: Tailscale 自動フォールバック（off-LAN route・2026-08-30）

mDNS（§9.2）は link-local のため**自宅 LAN 限定**。外出先（カフェ Wi-Fi・スマホテザリング）からは
**Tailscale**（WireGuard メッシュ VPN）で届かせる。個人 Free プランで**費用ゼロ**・ポート開放不要・
CGNAT/テザリング可（直結不能時は DERP リレーへ自動フォールバック）。

- **セットアップ**: board 側 = [`deploy/dev/jetson-link/mwr-tailscale-setup.sh`](../../deploy/dev/jetson-link/mwr-tailscale-setup.sh)
  （idempotent・公式 apt 方式・`tailscale up --hostname=minicar --operator=ruyuya --timeout=5m`。
  認証 URL を Mac のブラウザで承認する。**再実行しても hostname/operator が `tailscale set` で収束**）。
  Mac 側 = `brew install --cask tailscale-app` → アプリでサインイン。
- **`jetson` CLI の自動切替**: mDNS probe 不達のときだけ `tailscale status --json` を **5s 上限**で読み、
  `BackendState=Running` ∧ peer `minicar` が Online の場合のみ **tailnet IP へ retarget**する
  （probe は IP＝MagicDNS 非依存・ssh は FQDN＝`~/.ssh/config` の `Host minicar.*.ts.net` が鍵を供給）。
  切替時は **stderr** に `route = tailscale (...)` を表示（`jetson ssh <cmd>` の stdout を汚さない）。
  peer が **Offline** なら経路は切り替えず「board 自体が落ちている」とより鋭い診断を出す。
  ノブ: `JETSON_TS_NAME`（既定 minicar）/ `JETSON_TS_FALLBACK=0`（無効化）/
  `JETSON_TS_PROBE_TIMEOUT`（既定 8s＝WireGuard/DERP の cold path 用）。明示の
  `JETSON_HOST` / `JETSON_TUNNEL_ADDR` / `JETSON_TUNNEL_PORT` ピンは常にフォールバックより優先。
- **halt は遠隔経路で厳格化**: tailscale route 上の `down` は cold handshake と区別できないため、
  `jetson halt` は「already down」と**断定せず拒否**する（§9.3 の fail-closed と同思想。
  外出先から「落ちてる」と誤certifyして自宅の誰かに電源を抜かせる事故を封じる）。
- **`~/.ssh/config` の形**（**`Host *` より上に置く**こと。ssh_config(5) は first-obtained-value 勝ちで、
  下に置くと keepalive 設定が `Host *` に食われ無効化される＝実測）:

```
Host minicar.*.ts.net
  User ruyuya
  IdentityFile ~/.ssh/mwr_jetson
  UserKnownHostsFile ~/.ssh/known_hosts_jetson
  StrictHostKeyChecking accept-new
  ServerAliveInterval 15
  ServerAliveCountMax 4
  ControlMaster auto
  ControlPath ~/.ssh/cm-%C
  ControlPersist 10m
```

  パターンを `minicar.` に限定（他の tailnet デバイスへ鍵・ユーザー名を配らない）。`ControlPath %C`＝
  固定長ハッシュ（FQDN 入りの `%h` は macOS の unix socket path 104 byte 上限を踏み得る）。
- **鍵期限（重要）**: 管理画面 [Machines](https://login.tailscale.com/admin/machines) で minicar の
  **Disable key expiry を必ず実施**。既定 180 日で**無言で tailnet から脱落**する（実例: 同 tailnet の
  iphone173 が Expired）。常時通電の無人 board には必須の一手。

## References

- [01-fidelity-and-validation.md](01-fidelity-and-validation.md)（実機投入前ゲート G0-G7・robot-free / robot-gated 分類）
- [setup/jetson-deploy.md](../setup/jetson-deploy.md)（prod 常駐化・安全ゲート `:26`）/ [deploy/jetson/](../../deploy/jetson/)
- [mode-m1/03-joystick-teleop-bringup.md](../mode-m1/03-joystick-teleop-bringup.md)（物理手順の順序 `:54`・M0/M1/M2 ゲート）
- [shared/02-hardware-design.md](../shared/02-hardware-design.md)（`:150` Super 化 / `:409` QSPI / `:412` JetPack 6.2 系）
- [ADR-0008](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md)（`:16` Humble / Ubuntu 22.04 を全系の既定 distro）
- 実装: [`deploy/dev/jetson-link/jetson`](../../deploy/dev/jetson-link/jetson)（Mac CLI）/ [`mwr-setup.sh`](../../deploy/dev/jetson-link/mwr-setup.sh)（ボード側 §9.4）/ [`mwr-provision.sh`](../../deploy/dev/jetson-link/mwr-provision.sh)（基盤 §9.6）/ [`mwr-tailscale-setup.sh`](../../deploy/dev/jetson-link/mwr-tailscale-setup.sh)（外出先経路 §9.7）/ [`mwr-tunnel.service`](../../deploy/dev/jetson-link/mwr-tunnel.service)＋[`mwr-tunnel`](../../deploy/dev/jetson-link/mwr-tunnel)（dormant fallback 写し）/ ch341 導入手順＝§10（台本はボード `~/ch341-build/` のみ・repo 化は `# TODO(Phase 1)`）
- [.claude/rules/safety.md](../../.claude/rules/safety.md)（鍵・secrets 非コミット）/ [.claude/rules/environments.md](../../.claude/rules/environments.md)
- [GLOSSARY.md](../GLOSSARY.md) §8「常時通電運用（always-on dev link）」（正準用語・双方向）

## 10. CH340（ch341）カーネルモジュール不在 — out-of-tree ビルドと udev `/dev/myserial`（2026-09-09 実機確定）

> **位置づけ**: §6 の初回ブート表（`:179`）では拡張ボードが**未接続**だったため見えていなかった欠落の記録と、その恒久化手順。対象 layer: **ホスト OS のカーネルドライバ層（L0'＝ホスト側 clamp（[../GLOSSARY.md](../GLOSSARY.md) §3）よりさらに下・レイヤ対応表 [../productization/01-commercial-box-map.md](../productization/01-commercial-box-map.md) に行なし＝帰属未定）**——`/dev/ttyUSB*` を生やすだけで motion を有効化するものではない（§9.6 と同じ安全ゲート＝[setup/jetson-deploy.md:26](../setup/jetson-deploy.md) §0 は不変・actuation 経路なし）。
>
> **forward（この節を前提にする先）**: [../shared/02-hardware-design.md](../shared/02-hardware-design.md) §P-8-2 / §P-8-3（`Rosmaster_Lib` 導入と `m1_probe` 検証＝`/dev/myserial` 前提）・同 doc §P-9d（本節の検証中に鳴り続ける低電圧ブザーの正体と運用）・[`ws/src/warehouse_m1_driver/CLAUDE.md`](../../ws/src/warehouse_m1_driver/CLAUDE.md)（produce/consume・シリアル層 seam）・[../mode-m1/03-joystick-teleop-bringup.md](../mode-m1/03-joystick-teleop-bringup.md) §2「実機プローブ（M1 ゲート内）」。**実装側 doc（package `CLAUDE.md` 等）へのリンクは節名で張り行番号 pin しない**（実装 doc 側の churn で腐るため。設計 doc への行 pin は本 PR で net-zero 同期した行に限る）。

### 10.1 症状と根本原因（ドライバがカーネルに入っていない）

拡張ボードの CH340 は `lsusb` に **`1a86:7523`** として見えるのに、**`/dev/ttyUSB*` が 1 本も生えない**。原因はパーミッションでも配線でもなく、**動作中カーネルに `ch341` ドライバが存在しない**ことだった:

| 観測（2026-09-09 実機） | 意味 |
|---|---|
| `modinfo ch341` → not found | モジュール自体が入っていない（未ロードではなく**不在**） |
| `/lib/modules/5.15.148-tegra/build/.config:6331` ＝ `# CONFIG_USB_SERIAL_CH341 is not set` | **NVIDIA の L4T カーネルがビルド時に無効化**している。同 `.config:6325` は `CONFIG_USB_SERIAL=m`＝USB シリアル基盤（`usbserial`）自体は有効 |
| 同梱の usb-serial ドライバは `usbserial.ko` / `cp210x.ko` / `ftdi_sio.ko` / `option.ko` / `usb_wwan.ko` のみ | CH340 系だけが欠けている |
| ヘッダ package `nvidia-l4t-kernel-headers 5.15.148-tegra-36.4.4` は `.c` を同梱しない | ソースは別途取得が要る（モジュールのビルド自体はヘッダだけで足りる） |

> **docs の訂正**: [../shared/02-hardware-design.md:577](../shared/02-hardware-design.md) の「`ch341` はカーネル標準」は **L4T では誤り**。同行は本節を正として行内訂正済み（`snd-usb-audio` 側は未検証のため触っていない）。

### 10.2 前提（ビルド前に必ず確認する）

- 動作中カーネル **`5.15.148-tegra`（L4T 36.4.4）** と `/lib/modules/$(uname -r)/build` のヘッダ版が**一致**していること（`uname -r` と `dpkg -l nvidia-l4t-kernel-headers` で照合）。
- ⚠️ **ビルド前に `apt upgrade` しない**。apt 候補には **`36.4.7` のヘッダ**が見えており、ヘッダだけ新しくなると動作中カーネルと不一致になって**ロードできないモジュール**が出来上がる（一般則。実観測は apt 候補に `36.4.7` が見えることまで）。
- ツールチェーンは導入済み（`gcc 11.4` / `make`）＝追加インストール不要。

### 10.3 ソース取得とビルド（ボード上 `~/ch341-build/`・sudo 不要）

**① ソース**（動作中カーネルと**同じ tag** から 1 ファイルだけ取る。GPL-2.0）:

```bash
mkdir -p ~/ch341-build && cd ~/ch341-build
curl -fsSL -o ch341.c \
  "https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/plain/drivers/usb/serial/ch341.c?h=v5.15.148"
sha256sum ch341.c   # f66d070eab6235b8a5c7a06a283d2feecb8fa3d81bc1323c847c2d2cbf7bd410
```

取得 2026-09-09・**22,862 B**・sha256 上記（再取得時はこの値で同一性を確認する）。

**② Makefile**（3 行。`$(MAKE)` の行頭は**タブ**）:

```makefile
obj-m += ch341.o
all:
	$(MAKE) -C /lib/modules/$(shell uname -r)/build M=$(PWD) modules
```

**③ ビルド**: `make` → `ch341.ko` が生成される（**警告ゼロ**）。健全性は `modinfo ./ch341.ko` で確認する:

| 確認項目 | 実測値（2026-09-09） |
|---|---|
| vermagic | `5.15.148-tegra SMP preempt mod_unload modversions aarch64`＝**同梱 `cp210x.ko` とバイト一致** |
| alias | `usb:v1A86p7523*`（拡張ボードの VID:PID と一致） |
| depends | `usbserial` |

### 10.4 試験ロード（sudo・恒久化の前に 1 度だけ）

```bash
sudo modprobe usbserial      # 先に依存を入れる（insmod は依存を解決しない）
sudo insmod ./ch341.ko
```

カーネルログに次が出る:

```
ch341: module verification failed: signature and/or required key missing - tainting kernel
usbcore: registered new interface driver ch341
usbserial: USB Serial support registered for ch341-uart
```

> **`module verification failed` は失敗ではない**。未署名 out-of-tree モジュールをロードしたときの既定挙動（カーネルに taint フラグが立つだけ）で、**直後の 2 行が登録成功の証拠**。ここを failure と読み違えて手順を戻さないこと。

### 10.5 恒久化（sudo・`install.sh`）

```bash
sudo install -D -m 0644 ch341.ko /lib/modules/$(uname -r)/extra/ch341.ko
sudo depmod -a
echo ch341 | sudo tee /etc/modules-load.d/ch341.conf            # ブート時に自動ロード
sudo tee /etc/udev/rules.d/99-yahboom-myserial.rules <<'EOF'
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7523", MODE="0660", GROUP="dialout", SYMLINK+="myserial"
EOF
sudo udevadm control --reload-rules && sudo udevadm trigger
sudo modprobe ch341
```

> **注（§10.4 の試験ロードを先に行った場合）**: 末尾の `sudo modprobe ch341` は既にロード済みなら **no-op** で、`extra/` 側の恒久版が実際に load されることの証明にはならない。恒久版の load は §10.8(f) の再起動実測（2026-09-09 17:57）で**確定済**（`/proc/modules` に `(OE)` で載り `modinfo -n` が `extra/` を返す）。

**実施記録（2026-09-09 17:10 JST）**: 3 ファイル（`extra/ch341.ko` / `modules-load.d/ch341.conf` / `udev/rules.d/99-yahboom-myserial.rules`）が **root 所有で存在**し、`modinfo -n ch341` が `extra/` 配下を解決することを確認済み（パス解決の確認。ロード中の実体が `extra/` 側であることは §10.8(f) の再起動実測で確定）。

### 10.6 第 2 層のブロッカー: brltty が CH340 を横取りする（udev・2026-09-09 17:12 実機ログ）

§10.5 まで終えても `/dev/ttyUSB0` が**残らない**。ボード USB を挿し直した直後のカーネルログ（17:12:42 JST）:

```
usb 1-2.1.3: ch341-uart converter now attached to ttyUSB0
usb 1-2.1.3: usbfs: interface 0 claimed by ch341 while 'brltty' sets config #1
ch341-uart ttyUSB0: ch341-uart converter now disconnected from ttyUSB0
ch341 1-2.1.3:1.0: device disconnected
```

> **手順を戻さないこと（初見の最大の罠）**: 1 行目が出ている＝**§10.3–10.5 のドライバ導入は成功している**。`/dev/ttyUSB0` は**一瞬生えてから奪われている**だけで、ビルドや恒久化のやり直しは不要。ここで「ドライバが悪い」と誤診して §10.3 へ戻るのが最大の時間損失。ブロッカーは**カーネル層（§10.1 ＝ `ch341` 不在）とユーザ空間 udev 層（本節）の 2 層**あった。

**原因**: Ubuntu 22.04 が既定で導入する点字ディスプレイ常駐 **`brltty 6.4-4ubuntu3`**。その `/usr/lib/udev/rules.d/85-brltty.rules` に

```
ENV{PRODUCT}=="1a86/7523/*", ENV{BRLTTY_BRAILLE_DRIVER}="bm"
```

があり、**CH340 の汎用 VID:PID `1a86:7523` を Baum 点字ディスプレイと誤認**する（§10.8(b) と同じ「汎用 VID:PID」問題の別の顔）。`brltty-udev.service` が usbfs 経由でインタフェースを掴み、`ch341` を剥がす。CH340 / Arduino 系で広く報告される 22.04 の問題（一次情報は上記ルール行そのもの＝導入済みボードでは `grep 1a86/7523 /usr/lib/udev/rules.d/85-brltty.rules` で再確認できる）。

**対処（実施済・sudo）**:

```bash
sudo apt-get remove --purge -y brltty
```

apt の出力は `The following packages will be REMOVED: brltty*` / `0 upgraded, 0 newly installed, 1 to remove and 495 not upgraded`＝**消えたのは brltty 1 個だけ**（`ubuntu-desktop` は道連れにならなかった＝hard Depends ではない〔`1 to remove` の実出力から判断・Recommends と推定〕）。本プロジェクトは**点字ディスプレイを使わない**ので、mask ではなく purge を選んだ（CH340 側の標準的な対処）。

**除去後はボード USB を挿し直す**（既に奪われた状態は再列挙しないと戻らない）。17:17:15 JST のログ:

```
ch341 1-2.1.3:1.0: ch341-uart converter detected
usb 1-2.1.3: ch341-uart converter now attached to ttyUSB0
```

以後 `ttyUSB0` は**保持される**（切断ログが続かない）。確定した状態は §10.7。

**採らなかった代替（記録のみ）**: `sudo systemctl mask brltty-udev.service brltty.service` ＋ 空の `/etc/udev/rules.d/85-brltty.rules` を置いて lib 側ルールを上書きする方法。brltty を残したまま無効化できるが、本プロジェクトでは残す理由が無いため不採用。

### 10.7 検証（2026-09-09 17:17 JST・実測で確定）

```bash
lsmod | grep ch341                     # ロードされているか
ls -l /dev/ttyUSB* /dev/myserial       # デバイスと symlink が生えているか
udevadm info -q path -n /dev/myserial  # symlink が実デバイスへ解決するか
dpkg -l brltty | tail -1               # brltty が居ないこと（期待: 先頭 `un`＝§10.6）
```

> ⚠️ **この検証状態では拡張ボードが連続ブザーを鳴らす**（Orin は AC 給電・T 抜き・USB のみ接続＝USB VBUS で MCU が起きて電池 0V 読み＝偽陽性・無害）。正体と運用は [../shared/02-hardware-design.md](../shared/02-hardware-design.md) §P-9d。検証が済んだら USB を抜けば止まる（ドライバ・udev 設定は残る）。

**実測（拡張ボード USB 接続状態・Orin は AC 給電・T 抜き）— デバイスノード層まで確認済**:

| 確認項目 | 実測値 |
|---|---|
| デバイス | `crw-rw---- 1 root dialout 188, 0 /dev/ttyUSB0`＝**`dialout` 所有・`0660`**（§10.5 の udev ルールどおり。§9.6 の group 付与と噛み合う） |
| symlink | `lrwxrwxrwx /dev/myserial -> ttyUSB0` |
| 解決先 | `udevadm info -q path -n /dev/myserial` = `/devices/platform/bus@0/3610000.usb/usb1/1-2/1-2.1/1-2.1.3/1-2.1.3:1.0/ttyUSB0/tty/ttyUSB0` |
| モジュール | `lsmod`: `ch341 20480 0` / `usbserial 40960 1 ch341` |
| カーネルログ | `usb 1-2.1.3: ch341-uart converter now attached to ttyUSB0`（以後 detach なし＝§10.6 が効いている） |
| brltty | `dpkg -l brltty` → **`un`（未導入）**・`/usr/lib/udev/rules.d/85-brltty.rules` は消失・`systemctl list-unit-files 'brltty*'` は 0 件 |

**再起動生存（2026-09-09 17:57 `jetson reboot` → 18:04 USB 挿入・§10.8(f)）**:

| 確認項目 | 実測値 |
|---|---|
| 自動ロード | 再起動 25 秒後の `lsmod` に `ch341 20480 0` / `usbserial 40960 1 ch341`（人手なし・USB 未挿入でも載る） |
| ロード元 | `modinfo -n ch341` = `/lib/modules/5.15.148-tegra/extra/ch341.ko`・`/proc/modules` フラグ `(OE)`＝out-of-tree/unsigned・`systemd-modules-load` Result=success |
| 挿入後 | 18:05 `crw-rw---- root dialout 188,0 /dev/ttyUSB0`・`/dev/myserial -> ttyUSB0`・udev path は上表と同一（人手なし） |
| 例外 | 再起動直後の `journalctl -k -b` にブート時の ch341 登録行が無い（理由未特定）＝④ が空でも `lsmod` / `/proc/modules` を正とする |

⚠️ **確認できたのは「デバイスノードと symlink が安定して存在する」層まで**。その先の **`Rosmaster_Lib` / `m1_probe` によるシリアル open（115200 8N1 で MCU と実際に喋る）は未実施** ＝ `# TODO(Phase 1)`。[../shared/02-hardware-design.md](../shared/02-hardware-design.md) §P-8-2 / §P-8-3 の導入・検証手順と同じセッションで潰す。

### 10.8 残注意（隠さない）

- **(a) カーネル更新で消える**: `extra/` は**カーネル版に固定**されるため `nvidia-l4t-kernel` が更新されると失われる。`~/ch341-build/` を保持し、更新後に `make` → §10.5 を再実行する。恒久策の **DKMS 化は `# TODO(Phase 1・別 PR)`**。
- **(b) `myserial` の取り合い**: `1a86:7523` は CH340 の**汎用** VID:PID。別の CH340 機器（例: AI 音声モジュールのシリアル側＝[../shared/02-hardware-design.md](../shared/02-hardware-design.md) §V-5）を同時に挿すと `SYMLINK+="myserial"` を取り合う。serial 番号での絞り込みは同時挿し要件が出た時点で設計する。
- **(c) 台本が repo に無い**: `test-insmod.sh` / `install.sh` / `verify.sh` は現状**ボード上 `~/ch341-build/` のみ**に存在する。repo 化（`deploy/dev/jetson-link/mwr-ch341-setup.sh` として idempotent 化）は **`# TODO(Phase 1)`・別 PR**。**その台本には §10.6 の brltty 除去（または mask）ステップと、`dpkg -l brltty` による事前ガード（既に居なければ skip・居れば purge して USB 再挿入を促す）を必ず含める**——ドライバ導入だけを移植すると、次のボードや再インストール後に**同じ 2 層目**で詰まる。
- **(d) グループ付与だけでは足りない**: §9.6 の `dialout` 付与は**アクセス権**の話で、**ドライバが先**。ch341 不在のままではそもそも `/dev/ttyUSB*` が存在しない（この順序を取り違えると「権限問題」と誤診する）。
- **(e) brltty が黙って戻り得る**: `brltty` は `ubuntu-desktop` の Recommends に居ると推定される（§10.6）ため、将来の `apt install ubuntu-desktop` や `--install-recommends` を伴う操作で**再導入され得る**（戻れば §10.6 の症状が再発し、`/dev/ttyUSB0` は生えた直後に消え `/dev/myserial` も失われる）。§10.7 の検証コマンドに `dpkg -l brltty`（期待 `un`）を入れてあるのはこのため。
- **(f) 再起動生存＝確認済（2026-09-09）**: `jetson reboot`（§9・全周 51 秒）後、人手なしで `ch341` が `extra/` からロードされ、USB 挿入（18:04:57）で `ttyUSB0`＋`/dev/myserial` が生成された（§10.7 の再起動生存表）。途中 18:05:01 に `USB disconnect, device number 6` → 18:05:08 再列挙（device number 9）を 1 回挟むが、`usbfs`/brltty 行は無く**物理的な再挿しと読める**（以後切断なし・`dpkg -l brltty` は `un` のまま）。走行振動下での USB コネクタ緩みは別途未確認（[../shared/02-hardware-design.md](../shared/02-hardware-design.md) P-9c の DC プラグと同じ扱い）。
