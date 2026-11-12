import psycopg

from Routes import admin


def sign_in_admin(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["role"] = "admin"
        session["mfa_verified"] = True
        session["full_name"] = "Admin"


def post_status(client, monkeypatch, status, action="activate"):
    sign_in_admin(client)
    monkeypatch.setattr(admin.accounts, "get_signup_status", lambda _uid: status)
    monkeypatch.setattr(admin.school, "set_user_active", lambda *_a: True)
    client.application.config["WTF_CSRF_ENABLED"] = False
    return client.post(f"/admin/users/407/{action}")


def test_reactivating_a_pending_signup_opens_the_review_page(client, monkeypatch):
    response = post_status(client, monkeypatch, "pending")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/pending-accounts/407")


def test_reactivating_a_rejected_signup_explains_instead_of_404(client, monkeypatch):
    response = post_status(client, monkeypatch, "rejected")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/users")


def test_reactivating_a_normal_inactive_user_still_works(client, monkeypatch):
    response = post_status(client, monkeypatch, None)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/users")


def test_database_outage_shows_a_503_page(client, monkeypatch):
    sign_in_admin(client)

    def boom(*_args, **_kwargs):
        raise psycopg.OperationalError("could not connect")

    monkeypatch.setattr(admin.school, "list_users", boom)
    response = client.get("/admin/users")

    assert response.status_code == 503
    assert b"temporarily unavailable" in response.data
