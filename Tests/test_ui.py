import re
from html.parser import HTMLParser
from pathlib import Path

from flask import request

from app import create_app


ROOT = Path(__file__).resolve().parents[1]


class ExternalAssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.external_assets = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "script":
            asset_url = attributes.get("src")
        elif tag == "link":
            relations = attributes.get("rel", "").split()
            is_stylesheet_or_font = (
                "stylesheet" in relations
                or "font" in relations
                or attributes.get("as") == "font"
            )
            asset_url = attributes.get("href") if is_stylesheet_or_font else None
        else:
            asset_url = None

        if asset_url and asset_url.startswith(("http://", "https://")):
            self.external_assets.append(asset_url)


def contrast_ratio(foreground: str, background: str) -> float:
    def luminance(color: str) -> float:
        channels = [
            int(color[index:index + 2], 16) / 255
            for index in (1, 3, 5)
        ]
        linear = [
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return (
            0.2126 * linear[0]
            + 0.7152 * linear[1]
            + 0.0722 * linear[2]
        )

    high, low = sorted(
        (luminance(foreground), luminance(background)),
        reverse=True,
    )
    return (high + 0.05) / (low + 0.05)


def test_login_has_nonce_protected_theme_script(client):
    response = client.get("/login")
    page = response.get_data(as_text=True)
    policy = response.headers["Content-Security-Policy"]
    nonce = re.search(r'<script nonce="([^"]+)">', page)

    assert response.status_code == 200
    assert nonce is not None
    assert f"'nonce-{nonce.group(1)}'" in policy
    assert "localStorage.getItem" in page
    assert 'href="/Static/css/tokens.css"' in page


def test_static_icons_and_theme_assets_are_served_locally(client):
    required_assets = (
        "/Static/icons/sprite.svg",
        "/Static/icons/favicon.svg",
        "/Static/css/tokens.css",
        "/Static/css/transitions.css",
        "/Static/js/theme.js",
        "/Static/js/loader.js",
        "/Static/js/app.js",
        "/Static/vendor/chart.umd.min.js",
        "/Static/vendor/CHARTJS-LICENSE.md",
    )

    for asset in required_assets:
        response = client.get(asset)
        assert response.status_code == 200, asset

    sprite = client.get("/Static/icons/sprite.svg").get_data(as_text=True)
    required_symbols = (
        "logo-mark", "dashboard", "users", "user-plus", "graduation-cap",
        "calendar-check", "clipboard-list", "bar-chart", "file-text",
        "download", "shield", "lock", "key", "log-out", "sun", "moon",
        "search", "menu", "x", "check", "alert-triangle", "info", "eye",
        "eye-off", "chevron-right", "settings", "activity",
    )
    for symbol in required_symbols:
        assert f'id="{symbol}"' in sprite
    chart_bundle = client.get("/Static/vendor/chart.umd.min.js").get_data(as_text=True)
    assert "Chart.js v4.6.1" in chart_bundle


def test_theme_tokens_meet_wcag_aa_text_contrast():
    tokens = (ROOT / "public" / "Static" / "css" / "tokens.css").read_text(encoding="utf-8")
    components = (ROOT / "public" / "Static" / "css" / "components.css").read_text(encoding="utf-8")
    expected_colors = (
        "#0b1417", "#e6f1f2", "#8fa6aa",
        "#f2f8f8", "#0f1f22", "#52696d",
        "#14b8a6", "#0d9488", "#0f766e",
    )

    for color in expected_colors:
        assert color in tokens or color in components

    assert contrast_ratio("#e6f1f2", "#0b1417") >= 4.5
    assert contrast_ratio("#8fa6aa", "#0b1417") >= 4.5
    assert contrast_ratio("#0f1f22", "#f2f8f8") >= 4.5
    assert contrast_ratio("#52696d", "#f2f8f8") >= 4.5
    assert contrast_ratio("#000000", "#14b8a6") >= 4.5
    assert contrast_ratio("#000000", "#0d9488") >= 4.5
    assert contrast_ratio("#ffffff", "#0f766e") >= 4.5


def test_login_forms_use_post_and_csrf(client):
    response = client.get("/login")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'href="/curriculum"' in page
    assert 'method="post"' in page
    assert 'name="csrf_token"' in page
    assert 'data-password-toggle' in page
    assert 'autocomplete="current-password"' in page


def test_health_check_is_available_for_deployment_monitoring(client):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json == {"status": "ok"}


def test_curriculum_is_public_and_contains_all_stages(client):
    response = client.get("/curriculum")
    page = response.get_data(as_text=True)
    stages = (
        "Early Years Foundation Stage",
        "Key Stage 1",
        "Key Stage 2",
        "Key Stage 3",
        "Key Stage 4",
        "Post-16",
    )

    assert response.status_code == 200
    assert "Our Curriculum" in page
    assert (
        '<meta name="description" content="Learn about the British curriculum at '
        'Greenfield Academy, from Early Years to IGCSE, in Accra, Ghana.">'
    ) in page
    for stage in stages:
        assert stage in page


def test_curriculum_has_no_external_scripts_stylesheets_or_fonts(client):
    response = client.get("/curriculum")
    parser = ExternalAssetParser()
    parser.feed(response.get_data(as_text=True))

    assert response.status_code == 200
    assert parser.external_assets == []


def test_account_lifecycle_pages_load_only_local_assets(client, monkeypatch):
    from Routes import admin
    from database import accounts

    monkeypatch.setattr(accounts, "list_pending_signups", lambda: [])
    monkeypatch.setattr(accounts, "list_password_reset_requests", lambda: [])
    monkeypatch.setattr(
        accounts,
        "get_pending_signup",
        lambda _user_id: {
            "id": 41,
            "full_name": "Avery Student",
            "email": "avery@greenfield.test",
            "role": "student",
        },
    )
    monkeypatch.setattr(
        admin.school,
        "get_create_user_options",
        lambda: {"classes": [{"id": 3, "name": "Year 1"}], "subjects": []},
    )
    monkeypatch.setattr(
        admin.school,
        "get_user_edit_data",
        lambda _user_id: {
            "id": 9,
            "full_name": "Avery Student",
            "role": "student",
            "is_active": True,
        },
    )
    monkeypatch.setattr(
        admin.school,
        "list_users",
        lambda *_args: {"total": 0, "per_page": 20, "users": [], "page": 1},
    )
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = "admin"
        session["full_name"] = "Demo Administrator"
        session["mfa_verified"] = True

    paths = (
        "/signup",
        "/request-reset",
        "/admin/pending-accounts",
        "/admin/pending-accounts/41",
        "/admin/password-reset-requests",
        "/admin/users/9/reset-password",
        "/admin/users",
    )
    for path in paths:
        response = client.get(path)
        parser = ExternalAssetParser()
        parser.feed(response.get_data(as_text=True))

        assert response.status_code == 200, path
        assert parser.external_assets == [], path


def test_proxy_headers_are_trusted_only_when_enabled():
    app = create_app(
        {
            "TESTING": True,
            "TRUST_PROXY_HEADERS": True,
        }
    )

    @app.get("/test-proxy-ip")
    def proxy_ip():
        return {"ip": request.remote_addr}

    response = app.test_client().get(
        "/test-proxy-ip",
        headers={"X-Forwarded-For": "198.51.100.10"},
    )

    assert response.json == {"ip": "198.51.100.10"}


def test_dashboard_consumes_one_time_login_loader_flag(client, admin_dashboard):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = "admin"
        session["mfa_verified"] = True
        session["full_name"] = "Demo Administrator"
        session["show_branded_loader"] = True

    response = client.get("/admin")

    assert response.status_code == 200
    assert b'data-show-loader="true"' in response.data
    with client.session_transaction() as session:
        assert "show_branded_loader" not in session


def test_authenticated_shell_has_theme_and_user_menu_controls(client, admin_dashboard):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = "admin"
        session["mfa_verified"] = True
        session["full_name"] = "Demo Administrator"

    response = client.get("/admin")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'data-theme-toggle' in page
    assert 'data-user-menu-toggle' in page
    assert 'href="/curriculum"' in page
    assert 'role="menu"' in page
    assert 'name="csrf_token"' in page
