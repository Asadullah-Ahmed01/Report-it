from __future__ import annotations

from django.core.management.base import BaseCommand

from reportfix.engine import DjangoReportFixEngine
from reportfix.models import Issue, UserProfile


HELP_TEXT = """
Commands:
  report <text>                 Submit a report (English, Roman Urdu, mixed, typos)
  metoo <ISS-ID>                Join an existing issue ('me too')
  vote <ISS-ID> confirm|dispute Confirm or dispute a 'resolved' issue (affected students only)
  update <ISS-ID> <status>      [Staff] Set status: open, in_progress, resolved
  issues                        List visible issues sorted by crowd & priority weight
  myissues                      [Student] Show issues you reported or joined with live status
  trending                      [Bonus] Show trending board
  summary                       [Bonus] Show admin executive summary
  alerts                        [Bonus] Show spam & brigading alerts
  trust [user_code]             Show trust score & explainable history
  users                         List all users/roles

Judge Controls:
  switch <user_code_or_name>    Switch active user instantly (no password)
  newstudent <Name>             Create a new student account & switch to it
  reset                         Reset all data cleanly
  help                          Show this help menu
  exit                          Quit
"""


def format_issue(it: Issue) -> str:
    return (
        f"[{it.issue_code}] {it.title} | Dept: {it.department} | Priority: {it.priority} "
        f"| Status: {it.status.upper()} | Me-Too: {it.me_too_count} | Score: {it.crowd_score():.1f}\n"
        f"       Reason: {it.reason}"
    )


class Command(BaseCommand):
    help = "Run the ReportFix Django CLI backend and interactive shell"

    def handle(self, *args, **options) -> None:
        engine = DjangoReportFixEngine()
        engine.ensure_initialized()
        u = engine.current_user()
        self.stdout.write("=== ReportFix (Django CLI) Ready ===")
        self.stdout.write(f"Active User: {u.name} ({u.code}) | Role: {u.role} | Trust: {u.trust_score:.2f}")
        self.stdout.write("Type 'help' for commands.\n")

        while True:
            cur = engine.current_user()
            role_tag = f"{cur.role}:{cur.department}" if cur.department else cur.role
            try:
                raw = input(f"[{cur.name} ({role_tag})]> ").strip()
            except (EOFError, KeyboardInterrupt):
                self.stdout.write("\nExiting ReportFix.")
                break

            if not raw:
                continue

            parts = raw.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""

            if cmd in {"exit", "quit"}:
                self.stdout.write("Goodbye.")
                break
            elif cmd == "help":
                self.stdout.write(HELP_TEXT)
            elif cmd == "reset":
                self.stdout.write(engine.reset_all_data())
            elif cmd == "switch":
                if not arg:
                    self.stdout.write("Usage: switch <user_code_or_name>")
                else:
                    _, msg = engine.switch_user(arg)
                    self.stdout.write(msg)
            elif cmd == "newstudent":
                if not arg:
                    self.stdout.write("Usage: newstudent <Name>")
                else:
                    _, msg = engine.create_student_account(arg)
                    self.stdout.write(msg)
            elif cmd == "users":
                for usr in UserProfile.objects.all().order_by("id"):
                    extra = f"Dept={usr.department}" if usr.department else f"Trust={usr.trust_score:.2f}"
                    marker = " <-- ACTIVE" if usr.code == cur.code else ""
                    self.stdout.write(f"  {usr.code:10s} | {usr.name:16s} | {usr.role:7s} | {extra}{marker}")
            elif cmd == "report":
                if not arg:
                    self.stdout.write("Usage: report <problem description>")
                    continue
                res = engine.submit_report(cur.code, arg)
                if res.get("injection_ignored"):
                    self.stdout.write("[Guardrail] Command-like instructions in report were ignored as plain data.")
                if not res.get("ok"):
                    self.stdout.write(str(res.get("clarification") or res.get("message") or "Could not submit report."))
                else:
                    self.stdout.write(str(res["message"]))
            elif cmd == "metoo":
                if not arg:
                    self.stdout.write("Usage: metoo <ISS-ID>")
                    continue
                _, msg = engine.join_me_too(cur.code, arg)
                self.stdout.write(msg)
            elif cmd == "vote":
                vparts = arg.split()
                if len(vparts) != 2:
                    self.stdout.write("Usage: vote <ISS-ID> confirm|dispute")
                    continue
                _, msg = engine.vote_resolution(cur.code, vparts[0], vparts[1])
                self.stdout.write(msg)
            elif cmd == "update":
                uparts = arg.split()
                if len(uparts) != 2:
                    self.stdout.write("Usage: update <ISS-ID> <open|in_progress|resolved|no_issue|spam>")
                    continue
                _, msg = engine.staff_update_status(cur.code, uparts[0], uparts[1])
                self.stdout.write(msg)
            elif cmd == "issues":
                visible = engine.visible_issues_for_current_user()
                if not visible:
                    self.stdout.write("No issues visible in current view.")
                for it in visible:
                    if it.confidential and cur.role != UserProfile.ROLE_ADMIN:
                        continue
                    if it.confidential and cur.role == UserProfile.ROLE_ADMIN:
                        self.stdout.write(
                            f"[{it.issue_code}] {it.title} | Status: {it.status.upper()} | "
                            f"Reporter Identity: PROTECTED/ANONYMIZED"
                        )
                    else:
                        self.stdout.write(format_issue(it))
            elif cmd == "myissues":
                if cur.role != UserProfile.ROLE_STUDENT:
                    self.stdout.write("Only students have a personal reported/joined issue list.")
                    continue
                mine = list(Issue.objects.filter(confidential=False, affected_students=cur))
                if not mine:
                    self.stdout.write("You have not reported or joined any public issues yet.")
                for it in mine:
                    self.stdout.write(format_issue(it))
            elif cmd == "trending":
                self.stdout.write("=== TRENDING BOARD ===")
                for rank, (it, sc) in enumerate(engine.trending_board()[:5], start=1):
                    self.stdout.write(
                        f"  {rank}. [{it.issue_code}] {it.title} ({it.department}) — "
                        f"Score: {sc:.1f}, Me-Too: {it.me_too_count}, Status: {it.status}"
                    )
            elif cmd == "summary":
                if cur.role != UserProfile.ROLE_ADMIN:
                    self.stdout.write("Admin summary is available to Admin (use 'switch admin1').")
                else:
                    self.stdout.write(engine.admin_summary())
            elif cmd == "alerts":
                alerts = engine.brigading_alerts()
                if not alerts:
                    self.stdout.write("No spam or brigading alerts right now.")
                else:
                    for a in alerts:
                        self.stdout.write(f"  [ALERT] {a}")
            elif cmd == "trust":
                target_code = arg.strip() if arg.strip() else cur.code
                tu = UserProfile.objects.filter(code__iexact=target_code).first()
                if not tu:
                    self.stdout.write(f"Unknown user '{target_code}'.")
                    continue
                self.stdout.write(f"Trust for {tu.name} ({tu.code}): {tu.trust_score:.1f} / 100")
                for ev in tu.trust_events.order_by("-id")[:6]:
                    self.stdout.write(f"  - {ev.reason}")
            else:
                self.stdout.write(f"Unknown command '{cmd}'. Type 'help' for available commands.")
