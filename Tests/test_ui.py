import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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
    tokens = (ROOT / "Static" / "css" / "tokens.css").read_text(encoding="utf-8")
    components = (ROOT / "Static" / "css" / "components.css").read_text(encoding="utf-8")
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
    assert 'method="post"' in page
    assert 'name="csrf_token"' in page
    assert 'data-password-toggle' in page
    assert 'autocomplete="current-password"' in page


def test_dashboard_consumes_one_time_login_loader_flag(client, admin_dashboard):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = "admin"
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
        session["full_name"] = "Demo Administrator"

    response = client.get("/admin")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'data-theme-toggle' in page
    assert 'data-user-menu-toggle' in page
    assert 'role="menu"' in page
    assert 'name="csrf_token"' in page
