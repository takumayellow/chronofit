"""テスト全体の共通設定。"""
import pytest

from chronofit.sources import phone


@pytest.fixture(autouse=True)
def _no_phone_link(monkeypatch):
    """この PC に phone-link が入って設定済みでも、テストから実機の adb に触らせない。"""
    monkeypatch.setattr(phone, "phone_link", None)
