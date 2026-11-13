"""What a play test says: a list of checks, each passed, failed or skipped, with the story of the run beside them."""

from dataclasses import dataclass, field

PASS, FAIL, SKIP = "pass", "fail", "skip"
MARK = {PASS: "PASS", FAIL: "FAIL", SKIP: "SKIP"}


@dataclass
class Check:
    label: str
    status: str
    detail: str = ""


@dataclass
class Result:
    """One scenario's outcome: what happened (``story``, as it ran), and the checks made on it."""

    name: str
    about: str
    story: list[str] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    skipped: str = ""
    crashed: str = ""

    def say(self, line: str) -> None:
        self.story.append(line)

    def check(self, ok: bool, label: str, detail: str = "") -> bool:
        """Records that $label held ($ok) or not; $detail says what was seen when it did not (or what is worth showing when it did)."""
        self.checks.append(Check(label, PASS if ok else FAIL, detail))
        return ok

    @property
    def status(self) -> str:
        if self.crashed or any(each.status == FAIL for each in self.checks):
            return FAIL
        return SKIP if self.skipped or not self.checks else PASS


def table(headers: list[str], rows: list[list]) -> list[str]:
    """Plain-text table lines (for the story)."""
    cells = [[str(each) for each in row] for row in rows]
    widths = [max(len(head), *(len(row[i]) for row in cells)) if cells else len(head) for i, head in enumerate(headers)]
    line = lambda row: "  ".join(value.rjust(widths[i]) if i else value.ljust(widths[i]) for i, value in enumerate(row))  # noqa: E731
    return [line(headers), "  ".join("-" * width for width in widths), *(line(row) for row in cells)]


def render(results: list[Result], verbose: bool = True) -> str:
    out = []
    for each in results:
        out.append(f"== {each.name}: {MARK[each.status]} == {each.about}")
        if each.skipped:
            out.append(f"   skipped: {each.skipped}")
        if each.crashed:
            out.append("   CRASHED:")
            out.extend(f"   {line}" for line in each.crashed.rstrip().splitlines())
        if verbose:
            out.extend(f"   {line}" for line in each.story)
        for check in each.checks:
            out.append(f"   [{MARK[check.status]}] {check.label}" + (f"  ({check.detail})" if check.detail and (verbose or check.status == FAIL) else ""))
        out.append("")
    counts = {key: sum(1 for each in results if each.status == key) for key in (PASS, FAIL, SKIP)}
    made = [check for each in results for check in each.checks]
    out.append(f"{len(results)} scenarios: {counts[PASS]} passed, {counts[FAIL]} failed, {counts[SKIP]} skipped; "
               f"{sum(1 for c in made if c.status == PASS)} of {len(made)} checks held")
    return "\n".join(out)


def as_json(results: list[Result]) -> list[dict]:
    return [{"name": each.name, "status": each.status, "skipped": each.skipped, "crashed": each.crashed, "story": each.story,
             "checks": [{"label": c.label, "status": c.status, "detail": c.detail} for c in each.checks]} for each in results]
