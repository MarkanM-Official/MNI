import os


def test_setup_status_does_not_expose_google_allowlist(tmp_path, monkeypatch):
    db_path = tmp_path / "mni.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("MARKANM_DISABLE_BACKGROUND_WORKERS", "true")

    from backend.app import create_app
    from backend.database import db
    from backend.models.config_model import BotConfig

    app = create_app()
    with app.app_context():
        db.session.add(BotConfig(key="admin_setup_complete", value="true"))
        db.session.add(BotConfig(key="admin_auth_method", value="google_oauth"))
        db.session.add(BotConfig(key="admin_google_client_id", value="client.apps.googleusercontent.com"))
        db.session.add(BotConfig(key="admin_google_allowed_emails", value="owner@example.com"))
        db.session.commit()

    data = app.test_client().get("/api/auth/setup-status").get_json()
    assert data["setup_complete"] is True
    assert "google_login_client_id" in data["settings"]
    assert "google_allowed_emails" not in data["settings"]
