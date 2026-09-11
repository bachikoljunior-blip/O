"""Fixed issue-2847 completion protocol cases; no upstream effects."""
import json
import sys

import click
from click.testing import CliRunner


@click.command()
@click.option("--color", type=click.Choice(["auto", "always", "never"]))
@click.option("--name")
def gobble(color, name):
    pass


rows = []
for shell in ("bash", "zsh"):
    for syntax in ("equals", "space"):
        for partial in ("", "a", "al"):
            expected = [x for x in ("auto", "always", "never") if x.startswith(partial)]
            if syntax == "equals" and shell == "bash":
                words = ["gobble", "--color", "="] + ([partial] if partial else [])
                index = len(words) - 1
            elif syntax == "equals":
                words = ["gobble", "--color=" + partial]
                index = 1
                expected = ["--color=" + x for x in expected]
            else:
                words = ["gobble", "--color"] + ([partial] if partial else [])
                index = 2
            result = CliRunner().invoke(gobble, prog_name="gobble", env={
                "_GOBBLE_COMPLETE": shell + "_complete",
                "COMP_WORDS": " ".join(words),
                "COMP_CWORD": str(index),
            })
            lines = result.output.splitlines()
            actual = ([line.split(",", 1)[1] for line in lines if line.startswith("plain,")]
                      if shell == "bash" else lines[1::3])
            rows.append({"shell": shell, "syntax": syntax, "partial": partial,
                         "expected": expected, "actual": actual,
                         "exit_code": result.exit_code,
                         "passed": actual == expected and result.exit_code == 0})
print(json.dumps({"source": click.__file__, "cases": rows,
                  "passed": sum(row["passed"] for row in rows), "total": len(rows)}, indent=2))
sys.exit(0 if all(row["passed"] for row in rows) else 1)
