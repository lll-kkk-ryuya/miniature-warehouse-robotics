<!--
先頭行に worktree タグ（merge-and-communication.md §2）。タイトルは `[<track>] 要約`（命令形）。
track ラベル必須・凍結契約に触れたら contract ラベル必須（parallel-workflow.md §4）。
提出前に `git merge origin/main` で最新取込。①PR提出→②CI緑/レビュー可視→③別ステップでマージ（同一ターン self-merge 禁止）。
-->
[worktree: <name> | branch: <branch> | track: #N]

## 何を / なぜ
<!-- 変更概要と動機（1-3 点） -->

## 設計正本（docs/ リンク）
<!-- 起票時と同じく必須。docs/architecture/NN-....md（§...） -->

## 影響範囲
<!-- パッケージ / 契約 / トピック。他トラックへの影響有無 -->

## テスト
- [ ] `colcon build` 通過（ROS 環境がある場合）
- [ ] `ruff check .` / `ruff format --check .` / `pytest` 緑
- [ ] 安全機構（Emergency Guardian / Policy Gate）unit テスト通過（R-26）

## 契約変更
<!-- warehouse_interfaces（凍結契約）に触れたか -->
- [ ] なし
- [ ] あり → `contract` ラベル付与＋依存トラック Issue に予告・合意（parallel-workflow.md §4）

Closes #N
