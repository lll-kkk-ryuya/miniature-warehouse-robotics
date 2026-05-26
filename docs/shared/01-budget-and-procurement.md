# 予算・調達リスト

作成日: 2026-05-21
更新日: 2026-05-23
総予算: 500,000円（初期投資 270,000円 + 予備 230,000円）

## 予算配分

| # | カテゴリ | 製品 | 金額 |
|---|---------|------|------|
| 1 | ロボット×2 | Yahboom MicroROS ESP32 Car | 60,000円 |
| 2 | 3Dプリンター | Bambu Lab A1 mini | 30,000円（※価格変動あり、発注前に要確認） |
| 3 | フィラメント | PLA 1kg × 2（グレー+白） | 4,000円 |
| 4 | ベースボード | ラワン合板9mm + 木枠 + テクスチャーペイント | 7,000円 |
| 5 | エッジコンピュータ | Jetson Orin Nano Super Dev Kit | 57,200円 |
| 6 | 2D LiDAR | RPLiDAR A1 | 15,000円 |
| 7 | カメラ+スタンド | Logicool C922n + アームスタンド | 13,500円 |
| 8 | LED照明 | テープLED 昼白色 + USBバー | 3,000円 |
| 9 | クラウドGPU | RunPod A10G（月10h × 3ヶ月） | 15,000円 |
| 10 | LLM API | Claude / ChatGPT / Gemini / Grok API利用料 | 3,000円 |
| 11 | 予備・送料 | バッテリー予備、配線、センサー等 | 62,300円 |
| | **初期合計** | | **270,000円** |
| | **予備費** | | **230,000円** |

※ 当初3台構成から2台構成に変更。LLM司令官コンセプトに集中するため。

## 予備費の投資優先順位

| 優先度 | 投資先 | 金額 | 効果 |
|--------|-------|------|------|
| 1 | RPLiDAR A2（高精度版）へアップグレード | +10,000円 | 外部トラッキング補正精度向上（SLAMにはminicar搭載ORBBEC MS200を使用） |
| 2 | ロボット追加1台（計3台） | +30,000円 | より複雑な協調デモ |
| 3 | 深度カメラ（RealSense D435） | +40,000円 | FoundationPoseで荷物認識 |
| 4 | クラウドGPU追加時間 | +20,000円 | Isaac Sim撮影時間拡大 |
| 5 | 予備として温存 | 130,000円 | 故障・追加要件対応 |

方針: 最初は27万円で始めて、2台+LLMで動いてから追加投資を判断する。

---

## 調達先一覧

### 即発注可能（オンライン）

| 品目 | 購入先 | 備考 |
|------|--------|------|
| Yahboom MicroROS ESP32 Car × 2 | Amazon.co.jp | ASIN: B0CWQT5VKX |
| Bambu Lab A1 mini | Amazon.co.jp（Bambu Japan公式） | ASIN: B0CRYJBKQQ |
| PLA フィラメント × 2 | Amazon.co.jp | 汎用PLA 1kg |
| Logicool C922n | Amazon.co.jp | — |
| アームスタンド | サンワダイレクト | 200-DGCAM028 |
| テープLED + USBバー | Amazon.co.jp | 昼白色5000K |
| RPLiDAR A1 | Amazon.co.jp / Slamtec公式 | — |

### 要在庫確認

| 品目 | 購入先 | 備考 |
|------|--------|------|
| Jetson Orin Nano Super Dev Kit | スイッチサイエンス（57,200円税込） | 品切れ頻発 |
| 同上 | 菱洋エレクトロ | 法人向け、要見積、保守付き |

### クラウドサービス・API（要アカウント登録）

| サービス | 用途 | 備考 |
|---------|------|------|
| RunPod | Isaac Sim用クラウドGPU（A10G） | アカウント登録要、従量課金 |
| Anthropic API | Claude（LLM司令官） | APIキー取得要、従量課金 |
| OpenAI API | ChatGPT（LLM比較検証用） | APIキー取得要、従量課金 |
| Google AI API | Gemini（LLM比較検証用） | APIキー取得要、従量課金 |

### ホームセンター（コーナン/カインズ）

| 品目 | 価格 |
|------|------|
| ラワン合板 1820×910mm 9mm | ~3,500円 |
| 角材 30×40mm（補強用） | ~1,500円 |
| テクスチャーペイント（グレー） | ~1,500円 |
| ビニールテープ（黄・白） | ~200円（100円ショップ） |

---

## References

- [Yahboom ESP32 MicroROS Robot Car — 公式](https://category.yahboom.net/products/microros-esp32) — 参照日: 2026-05-19
- [Yahboom MicroROS Robot Car — Amazon.co.jp](https://www.amazon.co.jp/dp/B0CWQT5VKX) — 参照日: 2026-05-19
- [Bambu Lab A1 mini — Amazon.co.jp](https://www.amazon.co.jp/dp/B0CRYJBKQQ) — 参照日: 2026-05-19
- [Jetson Orin Nano Super Dev Kit — スイッチサイエンス](https://www.switch-science.com/products/10188) — 参照日: 2026-05-19
- [Jetson Orin Nano Super — 菱洋エレクトロ](https://ryoyo-gpu.jp/product/jetson/orin_nano_super_devkit/) — 参照日: 2026-05-19
- [サンワサプライ 俯瞰撮影スタンド 200-DGCAM028](https://direct.sanwa.co.jp/ItemPage/200-DGCAM028) — 参照日: 2026-05-19
