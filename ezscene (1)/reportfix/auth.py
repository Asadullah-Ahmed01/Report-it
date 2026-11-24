"""Authentication: portal-checked login with hashing, lockout, rate limiting and audit."""
from __future__ import annotations

import re
import secrets
from datetime import timedelta
from functools import lru_cache
from typing import Optional, Tuple

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.http import HttpRequest
from django.utils import timezone

from reportfix.models import LoginAttempt, PortalAccount, UserProfile

GENERIC_FAIL = "Invalid username or password."
USERNAME_RX = re.compile(r"^[a-z0-9_.@-]{1,64}$")


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return make_password(secrets.token_urlsafe(16))


@lru_cache(maxsize=4)
def _cached_hash(raw: str) -> str:
    """Seeded demo accounts share one demo password; hash it once per process (hashing is slow by design)."""
    return make_password(raw)


def client_ip(request: HttpRequest) -> str:
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR", ""))[:64]


def create_account(user: UserProfile, password: Optional[str] = None, seeded: bool = False,
                   email: Optional[str] = None) -> Tuple[PortalAccount, str]:
    """Create the credential row for a user. The portal is always derived from the user's role.
    Username = CMS ID (students), Staff ID (staff) or the admin username; email also works for sign-in."""
    raw = password or secrets.token_urlsafe(9)
    acct = PortalAccount(user=user, username=user.code.lower(), portal=user.role, email=(email or None) and email.lower(),
                         department=user.department if user.role == UserProfile.ROLE_STAFF else None)
    acct.password_hash = _cached_hash(raw) if seeded else make_password(raw)
    acct.save()
    return acct, raw


def _log(username: str, portal: str, success: bool, reason: str, ip: str) -> None:
    LoginAttempt.objects.create(username=username[:64], portal=portal[:16], success=success, reason=reason, ip=ip)


def login(request: HttpRequest, portal: str, username: str, password: str) -> Tuple[Optional[UserProfile], str, int]:
    ip = client_ip(request)
    username = (username or "").strip().lower()
    portal = (portal or "").strip().lower()  # empty = let the system identify the portal from the credentials
    if portal and portal not in dict(PortalAccount.PORTAL_CHOICES):
        return None, "Unknown portal.", 400
    if not USERNAME_RX.match(username) or not password or len(password) > 128:
        _log(username or "?", portal, False, "malformed input", ip)
        return None, GENERIC_FAIL, 401

    window = timezone.now() - timedelta(minutes=10)
    if LoginAttempt.objects.filter(ip=ip, success=False, created_at__gte=window).count() >= settings.LOGIN_IP_MAX_FAILURES:
        _log(username, portal, False, "rate limited (ip)", ip)
        return None, "Too many failed attempts from this network. Try again in a few minutes.", 429

    acct = (PortalAccount.objects.select_related("user").filter(username=username).first()
            or PortalAccount.objects.select_related("user").filter(email=username).first())
    if acct is None:
        check_password(password, _dummy_hash())  # same timing as a real check: no username probing
        _log(username, portal, False, "unknown username", ip)
        return None, GENERIC_FAIL, 401
    if acct.is_locked():
        _log(username, portal, False, "account locked", ip)
        return None, "Too many failed attempts. This account is locked for a few minutes.", 429
    if not acct.check_password(password):
        acct.failed_attempts += 1
        if acct.failed_attempts >= settings.LOGIN_MAX_FAILURES:
            acct.locked_until = timezone.now() + timedelta(minutes=settings.LOGIN_LOCK_MINUTES)
            acct.failed_attempts = 0
        acct.save(update_fields=["failed_attempts", "locked_until"])
        _log(username, portal, False, "wrong password", ip)
        return None, GENERIC_FAIL, 401
    if not acct.is_active:
        _log(username, portal, False, "account disabled", ip)
        return None, "This account is disabled.", 403
    # Portal check happens only after the password is proven, so it reveals nothing to a guesser.
    if acct.user.role != acct.portal:
        _log(username, portal or acct.portal, False, "account portal does not match role", ip)
        return None, "This account is misconfigured. Ask the admin.", 403
    if portal and acct.portal != portal:
        _log(username, portal, False, f"wrong portal (account is {acct.portal})", ip)
        return None, f"This account cannot sign in to the {portal} portal. Use the {acct.portal} portal.", 403

    acct.failed_attempts, acct.locked_until, acct.last_login_at = 0, None, timezone.now()
    acct.save(update_fields=["failed_attempts", "locked_until", "last_login_at"])
    start_session(request, acct.user, via="password")
    _log(username, acct.portal, True, f"signed in to {acct.portal} portal", ip)
    return acct.user, "Signed in.", 200


def landing(user: UserProfile) -> Tuple[str, str]:
    """Where a user lands after sign-in: (portal, path). Staff go straight to their assigned department."""
    if user.role == UserProfile.ROLE_STAFF:
        dept = user.department
        return "staff", f"/staff/{dept.lower()}/" if dept else "/staff/unassigned/"
    return user.role, f"/{user.role}/"


def start_session(request: HttpRequest, user: UserProfile, via: str) -> None:
    request.session.cycle_key()  # new session id at every sign-in (prevents session fixation)
    request.session["uid"] = user.code
    request.session["portal"] = user.role
    request.session["via"] = via


def session_user(request: HttpRequest) -> Optional[UserProfile]:
    code = request.session.get("uid")
    if not code:
        return None
    user = UserProfile.objects.filter(code=code).first()
    if not user or request.session.get("portal") != user.role:
        request.session.flush()
        return None
    if request.session.get("via") == "password":
        acct = PortalAccount.objects.filter(user=user).first()
        if not acct or not acct.is_active:
            request.session.flush()
            return None
    elif not settings.JUDGE_MODE:  # judge-switch sessions die when judge mode is turned off
        request.session.flush()
        return None
    return user
