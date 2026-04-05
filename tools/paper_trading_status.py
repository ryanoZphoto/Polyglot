from __future__ import annotations

import re
from pathlib import Path


LOG_PATH = Path("/opt/cursor/artifacts/paper_trading_loop.log")
SUMMARY_RE = re.compile(
    r"Cycle summary scanned=(?P<scanned>\d+) eligible=(?P<eligible>\d+) exec=(?P<exec>\d+)"
)


def main() -> None:
    if not LOG_PATH.exists():
        print("No paper-trading log found yet.")
        return

    lines = LOG_PATH.read_text().splitlines()
    summaries = []
    for line in lines:
        match = SUMMARY_RE.search(line)
        if not match:
            continue
        summaries.append(
            (
                int(match.group("scanned")),
                int(match.group("eligible")),
                int(match.group("exec")),
                line,
            )
        )

    print(f"log_path={LOG_PATH}")
    print(f"total_lines={len(lines)}")
    print(f"cycles_observed={len(summaries)}")
    if not summaries:
        print("No completed cycles yet.")
        return

    total_scanned = sum(s[0] for s in summaries)
    total_eligible = sum(s[1] for s in summaries)
    total_exec = sum(s[2] for s in summaries)
    avg_scanned = total_scanned / len(summaries)
    avg_eligible = total_eligible / len(summaries)

    print(f"avg_scanned_per_cycle={avg_scanned:.2f}")
    print(f"avg_eligible_per_cycle={avg_eligible:.2f}")
    print(f"total_exec={total_exec}")
    print("latest_cycle:")
    print(summaries[-1][3])


if __name__ == "__main__":
    main()
