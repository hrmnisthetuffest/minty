#!/usr/bin/env python3
"""Record the frugal-mode A/B result: run the same evaluation (scripts/eval_agent.py) once with frugal.level = F0
and once with F1, then pass both result files here. The compressed prompts are used under F1 only if F1 used at
least 25 % fewer tokens and lost at most 2 points of success.

  .venv/bin/python scripts/frugal_gate.py results_f0.jsonl results_f1.jsonl
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def rows(path: Path) -> list[dict]:
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def main(argv=None) -> int:
    from minty_core.frugal import frugal_gate

    p = argparse.ArgumentParser()
    p.add_argument("f0", type=Path)
    p.add_argument("f1", type=Path)
    p.add_argument("--target", type=Path, default=ROOT / "src" / "minty_core" / "prompts_terse.json")
    args = p.parse_args(argv)
    gate = frugal_gate(rows(args.f0), rows(args.f1))
    data = json.loads(args.target.read_text())
    data["gate"] = gate
    args.target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(gate))
    return 0


if __name__ == "__main__":
    sys.exit(main())
