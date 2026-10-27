#!/usr/bin/env python3
"""Summarize a recorded transcript: turns, routing, plans and tool activity."""

import re
import sys


PATTERNS = (
    ("turn", re.compile(r"^(?:You can also reply in English\. Press Enter to finish\.\n)?> (?!\[debug)(\S.*)$")),
    ("route", re.compile(r"follow-up action=(\S+)")),
    ("plan", re.compile(r"^# (.+?)(?:（|$)")),
    ("ask", re.compile(r"^(从哪个城市出发？|哪天出发.*|打算玩几天.*)$")),
    ("reuse", re.compile(r"(查询条件未变)")),
    ("stuck", re.compile(r"(信息仍然不完整)")),
    ("gate", re.compile(r"(⚠ 检查未通过.*|（上一次生成未通过.*)")),
    ("tool", re.compile(r"^\[tool\] (\w+)")),
)


def main() -> int:
    path = sys.argv[1]
    tools_since = 0
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            line = line.rstrip("\n")
            for kind, pattern in PATTERNS:
                match = pattern.search(line)
                if not match:
                    continue
                if kind == "tool":
                    tools_since += 1
                    break
                if kind == "turn":
                    print(f"\nUSER> {match.group(1)}")
                elif kind == "plan":
                    print(f"   PLAN      {match.group(1)}   (tools this turn: {tools_since})")
                    tools_since = 0
                else:
                    print(f"   {kind.upper():9} {match.group(1)}")
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
