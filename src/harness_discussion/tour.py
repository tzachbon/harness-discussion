"""The Get Started tour's steps and saved progress, shared by the Mac and Windows windows."""
STEPS = ("welcome", "voice", "read", "rewrite", "review", "dictate", "live", "about")
PRACTICE_STEPS = frozenset({"read", "rewrite", "review", "dictate", "live"})  # steps with a practice box
TRIED, SKIPPED = "tried", "skipped"


def clean(progress) -> dict:
    """Settings validation: known steps with known marks only."""
    if not isinstance(progress, dict):
        return {}
    return {k: v for k, v in progress.items() if k in STEPS and v in (TRIED, SKIPPED)}


def resume_at(progress) -> int:
    """The first step neither tried nor skipped; len(STEPS) is the Done screen."""
    return next((i for i, s in enumerate(STEPS) if s not in progress), len(STEPS))


def mark(progress, step, how) -> dict:
    """A tried step stays tried."""
    return progress if progress.get(step) == TRIED else {**progress, step: how}


def tried_count(progress) -> int:
    return sum(v == TRIED for v in progress.values())