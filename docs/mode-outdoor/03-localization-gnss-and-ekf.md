# 03 — 自己位置: RTK-GNSS + navsat_transform + 2 段 EKF（屋外差分）

作成日: 2026-09-12
Status: **箱（skeleton）**。室内 TARGET（[architecture/23](../architecture/23-perception-and-localization.md)）を置換しない。**屋外で反転・追加になる差分だけ**を本 doc に置く。

> 正本ルート: [mode-outdoor/README](README.md)。室内の EKF 単一所有・TF 配信責任は [23 §5-2](../architecture/23-perception-and-localization.md:145)、分類表は [23 §5-1](../architecture/23-perception-and-localization.md:141)。

## 0. 位置づけ

- 室内 TARGET の**構造**（`odom→base_link` は ekf_node 1 個・MOLA-LO は第 2 入力・TF は 1 ノード 1 責務）は屋外でも生かす。
- 変わるのは **global の作り方**: 室内は AMCL（地図あり）、屋外は **GNSS を global EKF に入れる**（地図なし・UTM 系）。

## 1. 分類表の反転（何を書くか）

- [23:141](../architecture/23-perception-and-localization.md:141) は GNSS / RTK-GNSS を「対象外」とし、その理由を「屋内設置・cm オーダ精度は 0.01m 地図でセル数個分・ジオラマ座標系・予算外」と残している。屋外ではこの理由が**すべて消える**ので、行を「採用（Mode Outdoor）」へ反転する。**doc23 本文は書き換えず**、doc23 末尾の追補（2026-09-12）から本 doc へ forward link を張る形にする（複製禁止）。
- `# TODO(設計)` SLAM Toolbox / AMCL を屋外主経路から外す判断と、AMCL 直購読の Guardian pose freshness guard（[23 §5-3](../architecture/23-perception-and-localization.md) の blocker ①）の代替 pose 源（[05 §3](05-safety-envelope-and-intervention.md) GNSS 品質ゲートと同一ソースにする）。

## 2. 構成（何を書くか）

- `# TODO(設計)` `navsat_transform_node`（WGS84 → 直交座標・UTM）・**local EKF**（`odom` frame: wheel + IMU（+ MOLA-LO））・**global EKF**（`map` frame: + GNSS 直交座標）。
- `# TODO(設計)` **datum の固定**（起動ごとに原点が動かないよう緯度経度・yaw を config で固定）。
- `# TODO(設計)` 状態割当（[GLOSSARY §11「状態割当」](../GLOSSARY.md)）の屋外版: GNSS は x,y の絶対観測、IMU 絶対 yaw を入れるか（入れるなら N−1 ルールで differential 切替）。
- `# TODO(設計)` TF 単一所有ルールの屋外版（`map→odom` = global EKF、`odom→base_link` = local EKF、`base_link→gnss_link` = robot_state_publisher。frame 名は凍結契約に additive 追加＝contract PR）。

## 3. Humble 制約と代替（何を書くか）

- Nav2 公式チュートリアル「Navigating using GPS Localization」は **`FollowGPSWaypoints` を ROS 2 Iron 以降の機能**と明記している。本プロジェクトは Humble（[ADR-0008](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md)）。
- 代替: `robot_localization` の **`fromLL` サービス**（Humble にあり）で緯度経度を map 座標に変換し、**既存の座標 goal**（`nav2_bridge` の additive 座標 goal・[ADR-0009 References](../adr/0009-m1-room-scale-operation.md) が指す `orientation.w = 1.0` 固定の seam）へ流す。変換の置き場所（L1 Navigation の bridge か L3 の compile 段か）は `# TODO(裁定)`。
- `# TODO(設計)` rolling global costmap（幅・高さ）と waypoint 間隔。

## 4. RTK（何を書くか）

- `# TODO(選定)` 受信機クラス（u-blox ZED-F9P 系 + NTRIP／QZSS CLAS 対応機）・アンテナ・取付高さ（非常停止柱と同居＝[06 §5](06-hardware-delta-and-base-selection.md)）。
- `# TODO(設計)` 品質指標（fix / float / single・HDOP・衛星数）を **GNSS 品質ゲート**（[05 §3](05-safety-envelope-and-intervention.md)）へ供給する契約（topic・型は additive 提案）。
- Nav2 公式の注意: 単独 GNSS は好条件で 1〜2 m、最大 10 m の誤差と頻繁なジャンプ。精度が要るなら RTK を強く推奨（cm 級）。

## 5. 受け入れ条件・ゲート（何を書くか）

- `# TODO(設計)` 静止時の位置分散・直線走行の横ずれ・ループ復帰誤差（室内の V1〜V10 に倣う）。
- `# TODO(spike)` 樹木・建物の影（urban canyon）での fix 維持率。

## 6. OPEN QUESTIONS（接頭辞 `OQ-OD3*`）

- `OQ-OD30` MOLA-LO（2D LiDAR odometry）は屋外で成立するか（構造物が少ない場所の縮退）。
- `OQ-OD31` IMU 絶対 yaw（磁気）を屋外で使うか。
- `OQ-OD32` fix ロスト時の挙動（減速か停止か）を GNSS 品質ゲートのどの段に置くか。

## References

- [architecture/23-perception-and-localization.md:141](../architecture/23-perception-and-localization.md:141)（GNSS 対象外の理由＝屋外で消える）/ [:145](../architecture/23-perception-and-localization.md:145)（TF 配信責任）
- [ADR-0008](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md)（Humble 固定）/ [ADR-0009](../adr/0009-m1-room-scale-operation.md)（座標 goal seam の参照）
- Nav2 Docs「Navigating using GPS Localization」<https://docs.nav2.org/jazzy/tutorials/general_tutorials/navigation2_with_gps/navigation2_with_gps/>（参照日 2026-09-12。`FollowGPSWaypoints` は Iron 以降・dual EKF・`navsat_transform`・RTK 推奨）
- [05 安全包絡](05-safety-envelope-and-intervention.md) / [06 ハード差分](06-hardware-delta-and-base-selection.md)
