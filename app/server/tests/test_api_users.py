"""Tests for the users API."""
from monitor.auth import hash_password, check_password


def _login(app, client, role="admin"):
    """Helper: create admin user and login. Returns user id."""
    from monitor.models import User
    user = User(
        id="user-admin",
        username="admin",
        password_hash=hash_password("adminpass1"),
        role=role,
    )
    app.store.save_user(user)
    client.post("/api/v1/auth/login", json={
        "username": "admin", "password": "adminpass1",
    })
    return user.id


class TestListUsers:
    """Test GET /api/v1/users."""

    def test_requires_auth(self, client):
        response = client.get("/api/v1/users")
        assert response.status_code == 401

    def test_requires_admin(self, app, client):
        _login(app, client, role="viewer")
        response = client.get("/api/v1/users")
        assert response.status_code == 403

    def test_returns_users(self, app, client):
        _login(app, client)
        response = client.get("/api/v1/users")
        assert response.status_code == 200
        data = response.get_json()
        assert len(data) == 1
        assert data[0]["username"] == "admin"
        assert "password_hash" not in data[0]

    def test_returns_user_fields(self, app, client):
        _login(app, client)
        data = client.get("/api/v1/users").get_json()
        user = data[0]
        assert "id" in user
        assert "username" in user
        assert "role" in user
        assert "created_at" in user
        assert "last_login" in user


class TestCreateUser:
    """Test POST /api/v1/users."""

    def test_requires_admin(self, app, client):
        _login(app, client, role="viewer")
        response = client.post("/api/v1/users", json={
            "username": "newuser", "password": "password123", "role": "viewer",
        })
        assert response.status_code == 403

    def test_creates_user(self, app, client):
        _login(app, client)
        response = client.post("/api/v1/users", json={
            "username": "viewer1",
            "password": "securepass1",
            "role": "viewer",
        })
        assert response.status_code == 201
        data = response.get_json()
        assert data["username"] == "viewer1"
        assert data["role"] == "viewer"
        assert "id" in data

    def test_default_role_is_viewer(self, app, client):
        _login(app, client)
        response = client.post("/api/v1/users", json={
            "username": "newuser",
            "password": "password123",
        })
        assert response.status_code == 201
        assert response.get_json()["role"] == "viewer"

    def test_requires_json(self, app, client):
        _login(app, client)
        response = client.post("/api/v1/users")
        assert response.status_code == 400

    def test_requires_username(self, app, client):
        _login(app, client)
        response = client.post("/api/v1/users", json={"password": "password123"})
        assert response.status_code == 400

    def test_username_too_short(self, app, client):
        _login(app, client)
        response = client.post("/api/v1/users", json={
            "username": "ab", "password": "password123",
        })
        assert response.status_code == 400

    def test_username_too_long(self, app, client):
        _login(app, client)
        response = client.post("/api/v1/users", json={
            "username": "a" * 33, "password": "password123",
        })
        assert response.status_code == 400

    def test_password_too_short(self, app, client):
        _login(app, client)
        response = client.post("/api/v1/users", json={
            "username": "newuser", "password": "short",
        })
        assert response.status_code == 400

    def test_invalid_role(self, app, client):
        _login(app, client)
        response = client.post("/api/v1/users", json={
            "username": "newuser", "password": "password123", "role": "superadmin",
        })
        assert response.status_code == 400

    def test_duplicate_username(self, app, client):
        _login(app, client)
        client.post("/api/v1/users", json={
            "username": "viewer1", "password": "password123",
        })
        response = client.post("/api/v1/users", json={
            "username": "viewer1", "password": "password456",
        })
        assert response.status_code == 409

    def test_create_admin_user(self, app, client):
        _login(app, client)
        response = client.post("/api/v1/users", json={
            "username": "admin2",
            "password": "securepass1",
            "role": "admin",
        })
        assert response.status_code == 201
        assert response.get_json()["role"] == "admin"


class TestDeleteUser:
    """Test DELETE /api/v1/users/<id>."""

    def test_requires_admin(self, app, client):
        _login(app, client, role="viewer")
        response = client.delete("/api/v1/users/user-xyz")
        assert response.status_code == 403

    def test_deletes_user(self, app, client):
        _login(app, client)
        # Create a user to delete
        resp = client.post("/api/v1/users", json={
            "username": "todelete", "password": "password123",
        })
        user_id = resp.get_json()["id"]
        response = client.delete(f"/api/v1/users/{user_id}")
        assert response.status_code == 200

        # Verify deleted
        users = client.get("/api/v1/users").get_json()
        assert all(u["id"] != user_id for u in users)

    def test_cannot_delete_self(self, app, client):
        admin_id = _login(app, client)
        response = client.delete(f"/api/v1/users/{admin_id}")
        assert response.status_code == 400
        assert "own account" in response.get_json()["error"]

    def test_delete_nonexistent_user(self, app, client):
        _login(app, client)
        response = client.delete("/api/v1/users/user-nonexistent")
        assert response.status_code == 404


class TestChangePassword:
    """Test PUT /api/v1/users/<id>/password."""

    def test_requires_auth(self, client):
        response = client.put("/api/v1/users/user-admin/password", json={
            "new_password": "newpassword1",
        })
        assert response.status_code == 401

    def test_admin_changes_other_password(self, app, client):
        _login(app, client)
        resp = client.post("/api/v1/users", json={
            "username": "viewer1", "password": "oldpass123",
        })
        user_id = resp.get_json()["id"]
        response = client.put(f"/api/v1/users/{user_id}/password", json={
            "new_password": "newpass1234",
        })
        assert response.status_code == 200

    def test_user_changes_own_password(self, app, client):
        # Create viewer user and login as viewer
        from monitor.models import User
        viewer = User(
            id="user-viewer",
            username="viewer1",
            password_hash=hash_password("oldpass123"),
            role="viewer",
        )
        app.store.save_user(viewer)
        client.post("/api/v1/auth/login", json={
            "username": "viewer1", "password": "oldpass123",
        })
        response = client.put("/api/v1/users/user-viewer/password", json={
            "new_password": "newpass1234",
        })
        assert response.status_code == 200

    def test_viewer_cannot_change_other_password(self, app, client):
        from monitor.models import User
        # Create two users
        app.store.save_user(User(
            id="user-viewer", username="viewer1",
            password_hash=hash_password("pass12345"), role="viewer",
        ))
        app.store.save_user(User(
            id="user-other", username="other",
            password_hash=hash_password("pass12345"), role="viewer",
        ))
        client.post("/api/v1/auth/login", json={
            "username": "viewer1", "password": "pass12345",
        })
        response = client.put("/api/v1/users/user-other/password", json={
            "new_password": "newpass1234",
        })
        assert response.status_code == 403

    def test_password_too_short(self, app, client):
        admin_id = _login(app, client)
        response = client.put(f"/api/v1/users/{admin_id}/password", json={
            "new_password": "short",
        })
        assert response.status_code == 400

    def test_requires_json(self, app, client):
        admin_id = _login(app, client)
        response = client.put(f"/api/v1/users/{admin_id}/password")
        assert response.status_code == 400

    def test_user_not_found(self, app, client):
        _login(app, client)
        response = client.put("/api/v1/users/user-nonexistent/password", json={
            "new_password": "newpass1234",
        })
        assert response.status_code == 404


class TestUsersAuditLog:
    """Test that user operations are audit logged."""

    def test_create_user_logged(self, app, client):
        _login(app, client)
        client.post("/api/v1/users", json={
            "username": "newuser1", "password": "password123",
        })
        events = app.audit.get_events(limit=10, event_type="USER_CREATED")
        assert len(events) >= 1
        assert "newuser1" in events[0]["detail"]

    def test_delete_user_logged(self, app, client):
        _login(app, client)
        resp = client.post("/api/v1/users", json={
            "username": "todelete", "password": "password123",
        })
        user_id = resp.get_json()["id"]
        client.delete(f"/api/v1/users/{user_id}")
        events = app.audit.get_events(limit=10, event_type="USER_DELETED")
        assert len(events) >= 1

    def test_password_change_logged(self, app, client):
        admin_id = _login(app, client)
        client.put(f"/api/v1/users/{admin_id}/password", json={
            "new_password": "newpass1234",
        })
        events = app.audit.get_events(limit=10, event_type="PASSWORD_CHANGED")
        assert len(events) >= 1
