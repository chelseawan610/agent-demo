#!/usr/bin/env python3
"""Print the validation and tool failures recorded in a JSONL run log."""

import json
import sys


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else ".local/real-run.jsonl"
    with open(path, encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream if line.strip()]
    print(f"runs: {len(rows)}")
    for index, row in enumerate(rows, 1):
        print(f"\n--- run {index} status={row.get('status')} "
              f"source={(row.get('final') or {}).get('generation_source')} ---")
        for event in row.get("trajectory", []):
            if event.get("status") == "error":
                print(f"  [{event.get('stage')}] {str(event.get('error'))[:400]}")
        plan = row.get("final_plan") or {}
        for note in plan.get("notes", []):
            if "校验" in note or "检查" in note:
                print(f"  note: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
