#!/usr/bin/env python3
"""Record a scripted multi-turn conversation against the real CLI and model.

Turns are fed through an ordinary pipe. ``input()`` consumes them one line at
a time in the order the CLI asks, so there is no prompt detection and no
timing heuristic to get wrong --- an earlier pty version matched the "> " of
Markdown blockquotes and could answer mid-render.

    PYTHONPATH=src .venv/bin/python scripts/demo_multi_turn.py \
        --out .local/transcript-real.txt

Everything the CLI prints, plus the line fed at each prompt, lands in --out.
Use --turn (repeatable, in order) to replace the scripted conversation.

If the CLI asks one more or one fewer question than the script expects, the
remaining turns shift by one. That misalignment is visible in the transcript,
and it is itself a finding: it means a guard asked for something the script
assumed was already settled.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys


DEFAULT_TURNS = [
    "我想要2026-10-29去马尔代夫玩3天，一定要去马富士岛，但我预算只有3万你帮我规划整个行程。",
    "从上海出发",
    "改成4天",
    "第二天写详细一点",
    "为什么没安排马富士岛？",
    "",
]

ENGLISH_TURNS = [
    "I want to go to the Maldives for 3 days on 2026-10-29, I must visit "
    "Maafushi Island, my budget is only 30000 CNY. Plan the whole trip.",
    "from Shanghai",
    "make it 4 days",
    "write day two in more detail",
    "why is Maafushi Island not scheduled?",
    "",
]


def run(turns: list[str], out_path: str, timeout: float, extra: list[str]) -> int:
    command = [
        sys.executable,
        "-m",
        "travel_agent.app.cli",
        "--output",
        "markdown",
        "--follow-up",
        *extra,
    ]
    env = {**os.environ, "PYTHONPATH": "src", "PYTHONUNBUFFERED": "1"}
    # A trailing blank line ends the follow-up loop; extra blanks are harmless
    # because the loop exits on the first one.
    stdin_text = "\n".join([*turns, "", ""])

    print(f"$ {' '.join(command)}\n", flush=True)
    try:
        completed = subprocess.run(
            command,
            input=stdin_text,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
        body, code = completed.stdout, completed.returncode
        errors = completed.stderr
    except subprocess.TimeoutExpired as expired:
        body = (expired.stdout or b"").decode("utf-8", "replace") if isinstance(
            expired.stdout, bytes
        ) else (expired.stdout or "")
        errors = "TIMEOUT: the CLI did not finish in time."
        code = -1

    header = (
        "# 真实模型多轮对话记录\n\n"
        f"命令：{' '.join(command)}\n"
        f"退出码：{code}\n\n"
        "依次送入的用户输入：\n"
        + "\n".join(f"  {i}. {t!r}" for i, t in enumerate(turns, 1))
        + "\n\n" + "=" * 72 + "\n\n"
    )
    text = header + body
    if errors.strip():
        text += "\n\n" + "=" * 72 + "\nSTDERR:\n" + errors

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as stream:
        stream.write(text)
    print(body)
    if errors.strip():
        print("STDERR:", errors, file=sys.stderr)
    print(f"\n[transcript saved to {out_path}; exit={code}]")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", default=".local/transcript-real.txt")
    parser.add_argument("--turn", action="append", dest="turns")
    parser.add_argument("--english", action="store_true", help="Use the English script")
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument(
        "--cli-arg",
        action="append",
        dest="extra",
        default=[],
        help="Extra argument passed through to the CLI, e.g. --cli-arg --debug",
    )
    args = parser.parse_args()

    turns = args.turns or (ENGLISH_TURNS if args.english else DEFAULT_TURNS)
    if turns and turns[-1] != "":
        turns = [*turns, ""]
    return run(turns, args.out, args.timeout, args.extra)


if __name__ == "__main__":
    raise SystemExit(main())
