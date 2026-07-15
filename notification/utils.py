"""
Notification URL generation utilities.

Centralizes all notification link construction so staging/production
differences are handled in one place.  Always use these functions
rather than hard-coding notification URLs.

Key principle:
  - Relative URLs (starting with /) are stored in the DB link field
    so they work regardless of which frontend (staging or production)
    resolves them.
  - Absolute URLs are built via get_notification_absolute_url() when
    a full URL is needed (emails, external links, etc.).
"""

from django.urls import reverse
from options.options import SysOptions


def get_notification_relative_url(notification_or_id):
    """
    Return the relative URL for a notification detail page.

    >>> get_notification_relative_url(256)
    '/api/notification/256/'

    Use this for the Notification.link field stored in the DB.
    """
    nid = notification_or_id if isinstance(notification_or_id, int) else notification_or_id.id
    return f'/api/notification/{nid}/'


def get_notification_absolute_url(notification_or_id, request=None):
    """
    Return the absolute URL for a notification detail page.

    Priority:
      1. If ``request`` is provided, use request.build_absolute_uri()
         (which derives the host from the request's Host header).
      2. Otherwise use the configured website_base_url from SysOptions.

    >>> get_notification_absolute_url(256)
    'http://122.51.69.77/api/notification/256/'
    """
    relative = get_notification_relative_url(notification_or_id)

    if request is not None:
        return request.build_absolute_uri(relative)

    base = SysOptions.website_base_url.rstrip('/')
    return f'{base}{relative}'


def get_problem_url(problem_id, contest_id=None):
    """
    Return the relative URL for a problem page.

    >>> get_problem_url('LinK19')
    '/problem/LinK19'
    >>> get_problem_url('LinK19', contest_id=365)
    '/contest/365/problem/LinK19/'
    """
    if contest_id:
        return f'/contest/{contest_id}/problem/{problem_id}/'
    return f'/problem/{problem_id}'


def resolve_problem_url(problem_id):
    """
    Determine the correct problem URL by looking up the problem in the DB.

    Checks for a standalone (non-contest) copy first, then falls back
    to a contest copy.  Returns a relative URL.

    This is useful when building notification links and you don't know
    ahead of time whether the problem is in a contest.
    """
    from problem.models import Problem

    standalone = Problem.objects.filter(
        _id=problem_id, contest_id__isnull=True, visible=True
    ).first()
    if standalone:
        return get_problem_url(problem_id)

    contest_p = Problem.objects.filter(
        _id=problem_id, visible=True
    ).first()
    if contest_p and contest_p.contest_id:
        return get_problem_url(problem_id, contest_id=contest_p.contest_id)

    # Fallback -- assume standalone
    return get_problem_url(problem_id)
