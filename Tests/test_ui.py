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
    assert 'href="/Static/css/tokens.css?v=' in page


def test_html_responses_send_nosniff_and_no_hsts_over_plain_http(client):
    response = client.get("/login")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "Strict-Transport-Security" not in response.headers


def test_html_responses_send_hsts_over_https(client):
    response = client.get("/login", base_url="https://localhost")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Strict-Transport-Security"] == (
        "max-age=31536000; includeSubDomains"
    )


def test_hsts_sent_when_production_is_https_only():
    app = create_app({"TESTING": True, "SESSION_COOKIE_SECURE": True})
    response = app.test_client().get("/healthz")

    assert "max-age=31536000" in response.headers["Strict-Transport-Security"]


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
    light = {"bg": "#f7f8fa", "surface": "#ffffff", "text": "#1c1d21",
             "muted": "#5f6673", "link": "#0b6f99", "button": "#c3ebfa",
             "on_accent": "#14161a", "lavender": "#cfceff", "yellow": "#fae27c"}
    dark = {"bg": "#14161c", "surface": "#1c1f27", "text": "#eef0f4",
            "muted": "#9aa3b2", "link": "#7dd3fc", "button": "#a5e0f7"}

    for color in (*light.values(), *dark.values()):
        assert color in tokens, color

    for theme in (light, dark):
        for background in (theme["bg"], theme["surface"]):
            assert contrast_ratio(theme["text"], background) >= 4.5
            assert contrast_ratio(theme["muted"], background) >= 4.5
            assert contrast_ratio(theme["link"], background) >= 4.5
        assert contrast_ratio(light["on_accent"], theme["button"]) >= 4.5
    # Stat tiles keep dark text on the pastel fills in both themes.
    for fill in (light["lavender"], light["yellow"]):
        assert contrast_ratio(light["on_accent"], fill) >= 4.5
        assert contrast_ratio("#3a3f4c", fill) >= 4.5
    # Status pill text on its own 14% tint of white.
    for status in ("#0b6f99", "#8a5300", "#b4233a", "#4a45b8"):
        assert status in tokens
        assert contrast_ratio(status, "#ffffff") >= 5.5


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


def test_pages_have_no_loading_animations_or_demo_notice(client, admin_dashboard):
    removed = (
        b"branded-loader", b"top-progress", b"loader-spinner", b"data-show-loader",
        b"Demo system", b"fictional data",
    )
    login = client.get("/login").data
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = "admin"
        session["mfa_verified"] = True
        session["full_name"] = "Demo Administrator"
    dashboard = client.get("/admin").data

    for page in (login, dashboard):
        for marker in removed:
            assert marker not in page, marker
    for asset in ("css/components.css", "css/layout.css", "css/transitions.css", "js/loader.js"):
        text = client.get(f"/Static/{asset}").data
        for marker in (b"branded-loader", b"top-progress", b"button-spinner",
                       b"view-transition", b"is-navigating", b"demo-note"):
            assert marker not in text, (asset, marker)


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
