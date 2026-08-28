# 開発機（Mac）↔ Jetson 実機のアクセス経路（dev link）

作成日: 2026-08-28

> **目的**: bring-up 中に「**Mac から Jetson を操作する**」ための経路を正本化する。初回接続で
> **Mac → Jetson の新規接続だけが届かない**問題に当たり（原因 = macOS のローカルネットワーク権限）、
> その切り分けと対処・動作実績のある fallback をここに固定する。**次回以降は本 doc の §4 か §5 をなぞるだけで再接続できる**状態にする。
>
> **本 doc のスコープ**: 開発時アクセス（ssh / コマンド実行）に閉じる。**prod 常駐化は
> [setup/jetson-deploy.md](../setup/jetson-deploy.md)**、**実機投入前ゲート G0-G7 は
> [01-fidelity-and-validation.md §4](01-fidelity-and-validation.md)**、**物理手順の順序は
> [mode-m1/03:54](../mode-m1/03-joystick-teleop-bringup.md)** が正本。ここでは重複させない。
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

## 3. 対処（権限を許可する）

1. **システム設定 → プライバシーとセキュリティ → ローカルネットワーク**
2. コマンドを実行するアプリ（**Claude Code**・使うターミナル）を **ON**
3. **アプリを再起動する**——ON 直後は反映されないことを実測（トグルだけでは不十分）

> 一覧にアプリが出てこない場合は、そのアプリから一度 LAN 上の機器へ接続を試みると項目が現れる。

## 4. ssh 直結の手順（§3 の権限を通した後の正攻法）

```bash
# 1) 開発機で鍵を1本作る（初回のみ・パスフレーズ無し = 自動化用）
ssh-keygen -t ed25519 -N '' -C 'mwr-mac->jetson' -f ~/.ssh/mwr_jetson

# 2) Jetson 側へ公開鍵を入れる（Jetson の端末で1回・§5 の /a スクリプトと同等）
#    ssh が通る環境なら ssh-copy-id -i ~/.ssh/mwr_jetson.pub ruyuya@<IP> でよい

# 3) 接続
ssh -i ~/.ssh/mwr_jetson ruyuya@<IP>
```

- **秘密鍵はリポジトリに置かない**（`~/.ssh/` に置く。[.claude/rules/safety.md](../../.claude/rules/safety.md)）。
- IP はルータの DHCP 予約（MAC `50:2e:91:95:9c:23`）で固定すると `<IP>` の再確認が要らなくなる。
- **`systemctl enable --now warehouse.target` は G0 未通過では実行しない**
  （[setup/jetson-deploy.md:26](../setup/jetson-deploy.md)）。ssh が通ることと motion を有効化することは別。

## 5. Fallback: pull 型 agent（Jetson → 開発機の一方向だけで完結）

Mac → Jetson が塞がれていても、**Jetson から取りに来る**形なら動く。実装は
[`deploy/dev/jetson-link/`](../../deploy/dev/jetson-link/)。

```
開発機(Mac)                                  Jetson
  serve.py :8000                              mwr-agent (root, 3s ごと)
    GET /a    → agent スクリプト  ────────→   起動時に1回取得
    GET /cmd  → 実行したいコマンド ←──────    3s ごとに polling
    POST /up  → 実行結果を保存     ←──────    実行後に送信
```

**手順（再接続もこれだけ）**:

```bash
# 開発機側（バックグラウンドで起動したまま）
python3 deploy/dev/jetson-link/serve.py
```

起動時に表示される 1 行を **Jetson の端末で 1 回だけ**打つ（**ブート毎に必要**）:

```bash
curl -s <MacのIP>:8000/a | sudo sh
```

以降、開発機からコマンドを送って結果を受け取る:

```bash
deploy/dev/jetson-link/send.sh 'free -h'
```

**性質と制約（隠さない）**:

- agent は **root で常駐**し、`http://<MacのIP>:8000/cmd` の内容を `sh` で実行する。**信頼できる LAN でのみ使う**（平文 HTTP・認証なし）。
- **再起動すると消える**（systemd 登録をしていない＝意図的。恒久常駐は ssh が通ってから検討する）。
- 撤去: `sudo pkill -f mwr-agent; sudo rm -f /usr/local/bin/mwr-agent`
- 3s polling + POST のため**対話的な作業には向かない**。あくまで ssh が通るまでの橋渡し。

## 6. 初回ブートのベースライン実測（2026-08-28・robot-free）

| 項目 | 実測値 | 判定 |
|---|---|---|
| L4T | `R36.4.4`（`/etc/nv_tegra_release`） | JetPack **6.2 系**＝[shared/02:412](../shared/02-hardware-design.md) の想定どおり |
| OS | Ubuntu **22.04.5 LTS (jammy)** | ✅ [ADR-0008:16](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md)（Humble ネイティブ）と整合 |
| bootloader / QSPI | `Current version: 36.4.4`・slot B | ✅ **36.0 以降＝更新不要**（[shared/02:409](../shared/02-hardware-design.md)） |
| 起動デバイス | **`/dev/mmcblk0p1`（microSD 59.5GB）** | ⚠️ **SSD 未移行**。`lsblk` に `nvme` デバイスが**出ない**＝NVMe 未装着 or 未認識（[mode-m1/03:54](../mode-m1/03-joystick-teleop-bringup.md) Phase A が未完） |
| 電力モード | `NV Power Mode: 25W`（mode **1**） | ⚠️ **Super 化未実施**（`nvpmodel -m 2` = [shared/02:150](../shared/02-hardware-design.md)） |
| メモリ | total 7.4Gi / available 5.0Gi / zram swap 3.7Gi | G1 メモリゲートの基準線（スタック未起動時の値） |
| ROS | `/opt/ros` 無し＝**未インストール** | Humble 導入がこの次 |
| USB | Realtek hub ×2 / IMC Bluetooth / Logitech receiver | 拡張ボード（CH340）・LiDAR・HP60C は**未接続** |
| ディスク | `/` 57G 中 22G 使用（41%） | microSD 上 |

> **この表は「実機で初めて判明したこと」の記録**であり、合否基準は
> [01-fidelity-and-validation.md §4](01-fidelity-and-validation.md) の G0-G7 が正本。ここでは値のみ持つ。

## 7. 残課題・未決（隠さない）

- `# TODO(検証)` **権限 ON ＋ アプリ再起動後の ssh 直結が未確認**（本 doc 執筆時点で再起動前）。
- `# TODO(到着後)` **NVMe SSD が見えていない**。物理装着の有無から確認が要る（未装着なら装着 → JetPack 移行）。microSD 起動のまま環境を作り込むと移行時にやり直しになる。
- `# TODO` **Super 化（`nvpmodel -m 2`）未実施**。性能実測（G3/G4）の前に適用する。
- `# TODO` **IP が DHCP のまま**（`192.168.11.12`）。DHCP 予約 or 固定化するまで再接続のたびに IP 確認が要る。
- [01-fidelity-and-validation.md](01-fidelity-and-validation.md) の **G0-G7 は旧世界（ESP32×2 / MS200 / 2台）前提**のまま＝M1 単騎への rescope は別 PR（[mode-m1/README.md:25](../mode-m1/README.md)）。本 doc の §6 はその rescope 後に読み替えが要る。

## References

- [01-fidelity-and-validation.md](01-fidelity-and-validation.md)（実機投入前ゲート G0-G7・robot-free / robot-gated 分類）
- [setup/jetson-deploy.md](../setup/jetson-deploy.md)（prod 常駐化・安全ゲート `:26`）/ [deploy/jetson/](../../deploy/jetson/)
- [mode-m1/03-joystick-teleop-bringup.md](../mode-m1/03-joystick-teleop-bringup.md)（物理手順の順序 `:54`・M0/M1/M2 ゲート）
- [shared/02-hardware-design.md](../shared/02-hardware-design.md)（`:150` Super 化 / `:409` QSPI / `:412` JetPack 6.2 系）
- [ADR-0008](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md)（`:16` Humble / Ubuntu 22.04 を全系の既定 distro）
- 実装: [`deploy/dev/jetson-link/serve.py`](../../deploy/dev/jetson-link/serve.py) / [`send.sh`](../../deploy/dev/jetson-link/send.sh)
- [.claude/rules/safety.md](../../.claude/rules/safety.md)（鍵・secrets 非コミット）/ [.claude/rules/environments.md](../../.claude/rules/environments.md)
