from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie


@ensure_csrf_cookie  # gives the page a CSRF token; every POST must send it back
def index(request):
    return render(request, "reportfix/classic.html")
