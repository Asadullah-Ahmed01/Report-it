"""Template filters for the ReportFix UI."""
from django import template

register = template.Library()

STATUS_LABELS = {
    "open": "Open", "in_progress": "In progress", "resolved": "Resolved — awaiting students",
    "no_issue": "No issue found — awaiting students", "verified": "Fixed (verified)", "reopened": "Reopened",
    "closed": "Closed", "spam": "Rejected (spam)",
}


@register.filter
def stars(rating) -> str:
    """0–100 rating as 5 stars, e.g. 74 -> '★★★★☆' (rounded to the nearest star)."""
    try:
        n = max(0, min(5, round(float(rating) / 20)))
    except (TypeError, ValueError):
        return ""
    return "★" * n + "☆" * (5 - n)


@register.filter
def five(rating) -> str:
    """0–100 rating on a 5-point scale with one decimal, e.g. 74 -> '3.7'."""
    try:
        return f"{float(rating) / 20:.1f}"
    except (TypeError, ValueError):
        return ""


@register.filter
def status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status.replace("_", " ").title())


@register.filter
def lower(value) -> str:
    return str(value or "").lower()


# ---- EzScene brand helpers -------------------------------------------------
STATUS_CLASS = {"open": "s-open", "in_progress": "s-info", "resolved": "s-wait", "no_issue": "s-wait",
                "verified": "s-ok", "closed": "s-ok", "reopened": "s-bad", "spam": "s-muted"}
PRIORITY_CLASS = {"CRITICAL": "s-bad", "HIGH": "s-wait", "MEDIUM": "s-info", "LOW": "s-open"}
PROGRESS = {"open": 1, "reopened": 1, "in_progress": 2, "resolved": 2, "no_issue": 2, "verified": 3, "closed": 3, "spam": 0}


@register.filter
def status_class(status: str) -> str:
    """Brand status pill class: approved = green, rejected = red, waiting = amber, info = cyan tint."""
    return STATUS_CLASS.get(status, "s-open")


@register.filter
def prio_class(priority: str) -> str:
    return PRIORITY_CLASS.get(priority, "s-open")


@register.filter
def progress(status: str):
    """Three progress dashes: reported -> being fixed -> confirmed. Returns e.g. [True, True, False]."""
    n = PROGRESS.get(status, 0)
    return [i < n for i in range(3)]


@register.filter
def initials(name: str) -> str:
    parts = [p for p in str(name or "").replace(".", " ").split() if p[:1].isalpha()]
    return "".join(p[0] for p in parts[:2]).upper() or "?"
