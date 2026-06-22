# VLA アクセス・実行可否スパイク

作成日: 2026-06-22

## 結論

- **Gemini Robotics-ER 1.6 Preview** は `~/.hermes/.env` の Gemini API key を値表示なしで使い、Gemini API へ直接 call できた。テキスト probe / 画像 probe ともに `HTTP 200`。
- **今回成功した Gemini probe は ER/VLM API の疎通確認**であり、ロボット用の低レベル action policy をこのリポジトリへ統合した確認ではない。
- **OpenVLA** と **NVIDIA Isaac GR00T** は、VLA として実行に進めるには CUDA GPU、モデル checkpoint、action space / embodiment mapping、offline action validation が必要。この Mac worktree では download / GPU 実行せず、必要条件を preflight で判定できるようにした。

## 実測結果

### Gemini Robotics-ER

実行環境:

- worktree: `/private/tmp/mwr-gemini-er-spike`
- model: `gemini-robotics-er-1.6-preview`
- credential source: `~/.hermes/.env` の `GEMINI_API_KEY` / `GOOGLE_API_KEY`（値は表示しない）

結果:

| probe | 結果 | modelVersion | usage |
|---|---:|---|---:|
| text access probe | `HTTP 200` | `gemini-robotics-er-1.6-preview` | 78 tokens |
| image 2D point probe | `HTTP 200` | `gemini-robotics-er-1.6-preview` | 1211 tokens |

画像 probe は一時 PNG `/private/tmp/mwr-gemini-er-scene.png`（赤・青・緑の矩形）を入力し、正規化 2D point と label が返ることを確認した。API 応答に含まれる `thoughtSignature` は永続記録しない。

### ローカル Hermes 状態

- `http://127.0.0.1:8642/health` は `{"status": "ok", "platform": "hermes-agent"}`。
- `~/.hermes/config.yaml` の active provider は `openai-codex` に見えるため、今回の Gemini Robotics-ER probe は Hermes Gateway 経由ではなく Gemini API 直接 call。

## 実行手順

### Gemini Robotics-ER direct probe

通常は環境変数を shell に export して実行する。

```bash
cd /private/tmp/mwr-gemini-er-spike
export GEMINI_API_KEY=<YOUR_API_KEY_HERE>
python3 scripts/probe_gemini_robotics_er.py --model gemini-robotics-er-1.6-preview
```

画像付き:

```bash
python3 scripts/probe_gemini_robotics_er.py \
  --model gemini-robotics-er-1.6-preview \
  --image /path/to/scene.png
```

Hermes local secret を使う場合は、値を表示しない wrapper で必要な key だけをプロセス内に読み込む。`.env` 本文や値を PR / docs に貼らない。

## OpenVLA を動かすために必要なもの

OpenVLA は `openvla/openvla-7b` checkpoint を Hugging Face `transformers` から読み、画像 + instruction から robot action を出す。公式例は BridgeData V2 / WidowX 系の `unnorm_key="bridge_orig"` に基づく 7-DoF action を想定している。

必要条件:

- CUDA GPU。推論でも 7B VLA + vision encoder のため、16GB VRAM 級以上を推奨。
- Python 3.10 近辺、PyTorch、Transformers、timm、tokenizers。高速化には FlashAttention。
- Hugging Face model download。大きな checkpoint を扱うため十分な disk とネットワーク。
- 入力画像、language instruction、対象 embodiment の action normalization key。
- このプロジェクトの差動二輪 mini car に使う場合、OpenVLA の manipulator action をそのまま `cmd_vel` に接続しない。L3 validator で waypoint / intent に落とすか、差動二輪 action space で fine-tune する。

最初の実行候補:

1. GPU サーバで `openvla/openvla-7b` を open-loop inference。
2. 出力 action を実機に送らず JSON / artifact として保存。
3. action dimension、normalization、latency、VRAM を記録。
4. ROS 接続は別 PR で、必ず safety gate / no-actuation gate を先に置く。

## NVIDIA Isaac GR00T を動かすために必要なもの

NVIDIA Isaac-GR00T は N1.7 Early Access が現行で、vision-language foundation model + diffusion transformer action head の VLA。公式 README は inference に 16GB+ VRAM、fine-tuning に 40GB+ VRAM 推奨としている。

必要条件:

- CUDA GPU。dGPU / Jetson Orin / Thor 等で platform 別セットアップ。
- `uv`、Python 3.10 または platform 指定 Python、FFmpeg、git-lfs。
- Hugging Face checkpoint。例: `nvidia/GR00T-N1.7-3B`。
- LeRobot v2 形式の dataset、`meta/modality.json`、`embodiment-tag`。
- policy server/client で action を受ける robot controller。実機前に open-loop / sim validation が必須。

このプロジェクトへの適合:

- GR00T は humanoid / bimanual / manipulator 寄り。Yahboom minicar の差動二輪制御に直接使う候補ではない。
- 本プロジェクトで使うなら、Mode X-ER-VLA の「将来の manipulator / pick-place 拡張」か、Isaac Sim 上の manipulator benchmark が先。

## ローカル preflight

重い依存やモデル download を行わず、現在のマシンが OpenVLA / GR00T の offline inference に向いているか確認する。

```bash
cd /private/tmp/mwr-gemini-er-spike
python3 scripts/check_vla_runtime_readiness.py
```

厳格判定:

```bash
python3 scripts/check_vla_runtime_readiness.py --profile openvla --strict
python3 scripts/check_vla_runtime_readiness.py --profile groot --strict
```

Mac M4 開発機では NVIDIA CUDA GPU がないため、`nvidia_gpu=missing` が期待される。OpenVLA / GR00T の実モデル実行は RunPod / L40 / H100 / A10G などの GPU 環境で行う。

## PR に含める範囲

- Gemini Robotics-ER direct API probe script。
- VLA runtime readiness preflight script。
- 本 spike report。

含めないもの:

- API key / `.env` / response body 全文 / `thoughtSignature`。
- OpenVLA / GR00T checkpoint download。
- ロボット実機制御。
- ROS topic / frozen contract 変更。

## 参照した公式ページ

- Google Developers Blog: `Building the Next Generation of Physical Agents with Gemini Robotics-ER 1.5`
  - https://developers.googleblog.com/en/building-the-next-generation-of-physical-agents-with-gemini-robotics-er-15/
- Gemini API docs: `Gemini Robotics-ER 1.6`
  - https://ai.google.dev/gemini-api/docs/robotics-overview
- Gemini API docs: `Using Gemini API keys`
  - https://ai.google.dev/gemini-api/docs/api-key
- Gemini API docs: `Gemini Developer API pricing`
  - https://ai.google.dev/gemini-api/docs/pricing
- OpenVLA GitHub
  - https://github.com/openvla/openvla
- OpenVLA paper
  - https://arxiv.org/abs/2406.09246
- NVIDIA Isaac-GR00T GitHub
  - https://github.com/NVIDIA/Isaac-GR00T
- GR00T N1 paper
  - https://arxiv.org/abs/2503.14734
