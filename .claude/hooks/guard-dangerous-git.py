#!/usr/bin/env python3
"""PreToolUse(Bash) advisory — warn on genuinely destructive/irreversible git ops.

NON-BLOCKING: injects additionalContext only (never a permissionDecision), so it
can't false-deny. It warns on the truly data-losing subset and stays SILENT on
this repo's sanctioned ops, because a hard block would break our own workflow:
  - `git reset --hard origin/<ref>`  = sanctioned worktree resync (allow, silent)
  - `--force-with-lease`             = sanctioned feature-branch force (allow, silent)
  - `git branch -d/-D`, `git push origin --delete` = sanctioned cleanup (allow, silent)
Warned subset: `reset --hard` to a NON-remote ref, `clean -f`, whole-tree
`checkout .` / `restore .`, and force-push WITHOUT `--force-with-lease`.

Fail-open: any parse error exits 0 with no output. Boundary rule:
.claude/rules/merge-and-communication.md (破壊的 git 操作の境界). Wiring is human-only.
See https://code.claude.com/docs/en/hooks (PreToolUse).
"""

import json
import re
import sys


def _allow():
    sys.exit(0)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        _allow()

    cmd = (data.get("tool_input") or {}).get("command") or ""
    if not cmd or "git" not in cmd:
        _allow()

    warns = []

    # reset --hard to a non-remote ref (bare, HEAD, HEAD~n, a local branch) loses work.
    for m in re.finditer(r"git\s+reset\s+--hard\s*(\S+)?", cmd):
        tgt = m.group(1) or ""
        if not (tgt.startswith("origin/") or tgt.startswith("refs/remotes/")):
            warns.append(
                "`git reset --hard`（remote ref 以外）＝作業ツリー/ローカルコミット喪失。"
                "resync なら `origin/<ref>` を明示。意図的な破棄か確認を。"
            )
            break

    if re.search(r"git\s+clean\s+[^\n|;&]*-\w*f", cmd):
        warns.append(
            "`git clean -f`＝未追跡ファイルを git 復元不能に削除。まず `-n`（dry-run）で確認を。"
        )

    if re.search(r"git\s+checkout\s+(--\s+)?\.(\s|$)", cmd) or re.search(
        r"git\s+restore\s+(--\s+)?\.(\s|$)", cmd
    ):
        warns.append(
            "`git checkout .` / `git restore .`＝作業ツリー全体の変更を破棄。対象を限定するか意図確認を。"
        )

    if (
        re.search(r"git\s+push\b", cmd)
        and re.search(r"(--force\b|\s-f\b|(^|\s)\+)", cmd)
        and "--force-with-lease" not in cmd
    ):
        warns.append(
            "`git push --force`（lease 無し）＝他者の push を上書き。"
            "`--force-with-lease` を使う（本 repo の sanctioned force）。"
        )

    if not warns:
        _allow()

    msg = (
        "[guard-dangerous-git] 破壊的 git 操作の可能性:\n- "
        + "\n- ".join(warns)
        + "\nsanctioned な `reset --hard origin/<ref>` / `--force-with-lease` / "
        "`branch -d/-D` / `push origin --delete` は対象外。意図的なら続行可"
        "（本 hook は非ブロッキング advisory）。境界: .claude/rules/merge-and-communication.md。"
    )
    print(
        json.dumps(
            {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg}}
        )
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
