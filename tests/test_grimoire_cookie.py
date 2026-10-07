"""Grimoire login cookies are stretched to GRIMOIRE_COOKIE_DAYS."""
from PyQt6.QtCore import QDateTime
from PyQt6.QtNetwork import QNetworkCookie

from grimoireassist.ui.monster_panel import GRIMOIRE_COOKIE_DAYS, _extend_grimoire_cookie


class _Store:
    def __init__(self):
        self.set = []

    def setCookie(self, cookie):
        self.set.append(cookie)


def _cookie(domain, expires_in_days=None):
    c = QNetworkCookie(b"sid", b"x")
    c.setDomain(domain)
    if expires_in_days is not None:
        c.setExpirationDate(QDateTime.currentDateTimeUtc().addSecs(int(expires_in_days * 86400)))
    return c


def _days_left(cookie):
    return QDateTime.currentDateTimeUtc().secsTo(cookie.expirationDate()) / 86400


def test_session_cookie_gets_expiry():
    store = _Store()
    _extend_grimoire_cookie(store, _cookie("grimoire.laeradsphere.com"))
    assert len(store.set) == 1
    assert abs(_days_left(store.set[0]) - GRIMOIRE_COOKIE_DAYS) < 0.01


def test_short_cookie_extended_on_parent_domain():
    store = _Store()
    _extend_grimoire_cookie(store, _cookie(".laeradsphere.com", 2))
    assert len(store.set) == 1
    assert abs(_days_left(store.set[0]) - GRIMOIRE_COOKIE_DAYS) < 0.01


def test_long_expired_and_foreign_cookies_untouched():
    store = _Store()
    _extend_grimoire_cookie(store, _cookie("grimoire.laeradsphere.com", 30))
    _extend_grimoire_cookie(store, _cookie("grimoire.laeradsphere.com", GRIMOIRE_COOKIE_DAYS))
    _extend_grimoire_cookie(store, _cookie("grimoire.laeradsphere.com", -1))
    _extend_grimoire_cookie(store, _cookie("google.com"))
    _extend_grimoire_cookie(store, _cookie("notlaeradsphere.com", 1))
    assert store.set == []
