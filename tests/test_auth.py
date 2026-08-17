"""
Acceptance tests for LAB51 Auth Core.

Covers the key scenarios from the Technical Specification:
- Registration (Web, Telegram, Extension)
- Login
- Duplicate protection
- Telegram linking
- Onboarding + Bonus + Trial
- Sessions
"""

import pytest
from httpx import AsyncClient


# ══════════════════════════════════════════════
#  Test 1: Web Registration → Login → Onboarding
# ══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_web_registration_and_login(client: AsyncClient):
    """Acceptance Test — Web first registration."""
    # 1. Register
    resp = await client.post("/v1/auth/register", json={
        "email": "test@mail.ru",
        "password": "securepass123",
        "registration_source": "WEB",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "user_id" in data
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["onboarding_required"] is True
    user_id = data["user_id"]
    access_token = data["access_token"]

    # 2. Login
    resp = await client.post("/v1/auth/login", json={
        "identity": "test@mail.ru",
        "password": "securepass123",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == user_id
    assert data["onboarding_completed"] is False

    # 3. Get profile
    resp = await client.get("/v1/me", headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 200
    profile = resp.json()
    assert profile["id"] == user_id
    assert profile["onboarding_completed"] is False

    # 4. Complete onboarding
    resp = await client.patch("/v1/me", json={
        "display_name": "Test User",
        "avito_profile_url": "https://www.avito.ru/user/test123/profile",
        "primary_category_id": "00000000-0000-0000-0000-000000000001",
    }, headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 200
    profile = resp.json()
    assert profile["onboarding_completed"] is True
    assert profile["display_name"] == "Test User"

    # 5. Check bonus
    resp = await client.get("/v1/me/bonus", headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 200
    bonus = resp.json()
    assert bonus["total"] == 300

    # 6. Check trial
    resp = await client.get("/v1/me/trials", headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 200
    trials = resp.json()
    assert len(trials) == 1
    assert trials[0]["status"] == "ACTIVE"

    # 7. Check entitlements
    resp = await client.get("/v1/me/entitlements", headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 200
    entitlements = resp.json()
    assert "max_searches" in entitlements["entitlements"]


# ══════════════════════════════════════════════
#  Test 2: Duplicate protection
# ══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_duplicate_email_protection(client: AsyncClient):
    """Acceptance Test — Duplicate protection."""
    # Register first user
    resp = await client.post("/v1/auth/register", json={
        "email": "duplicate@mail.ru",
        "password": "securepass123",
        "registration_source": "WEB",
    })
    assert resp.status_code == 200
    first_user_id = resp.json()["user_id"]

    # Try to register with same email
    resp = await client.post("/v1/auth/register", json={
        "email": "duplicate@mail.ru",
        "password": "anotherpass123",
        "registration_source": "EXTENSION",
    })
    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "IDENTITY_ALREADY_EXISTS"


# ══════════════════════════════════════════════
#  Test 3: Bonus idempotency
# ══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_bonus_idempotency(client: AsyncClient):
    """Acceptance Test — Bonus granted only once."""
    # Register and get token
    resp = await client.post("/v1/auth/register", json={
        "email": "bonus@mail.ru",
        "password": "securepass123",
        "registration_source": "WEB",
    })
    access_token = resp.json()["access_token"]

    # Complete onboarding
    resp = await client.patch("/v1/me", json={
        "display_name": "Bonus User",
        "avito_profile_url": "https://www.avito.ru/user/bonus123/profile",
        "primary_category_id": "00000000-0000-0000-0000-000000000001",
    }, headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 200

    # Check bonus
    resp = await client.get("/v1/me/bonus", headers={"Authorization": f"Bearer {access_token}"})
    assert resp.json()["total"] == 300

    # Try to trigger onboarding again (should not double bonus)
    resp = await client.patch("/v1/me", json={
        "display_name": "Bonus User Updated",
    }, headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 200

    # Bonus should still be 300
    resp = await client.get("/v1/me/bonus", headers={"Authorization": f"Bearer {access_token}"})
    assert resp.json()["total"] == 300


# ══════════════════════════════════════════════
#  Test 4: Trial — one per user
# ══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_trial_one_per_user(client: AsyncClient):
    """Acceptance Test — Trial used once, not duplicated."""
    resp = await client.post("/v1/auth/register", json={
        "email": "trial@mail.ru",
        "password": "securepass123",
        "registration_source": "TELEGRAM",
    })
    access_token = resp.json()["access_token"]

    # Complete onboarding → trial starts
    resp = await client.patch("/v1/me", json={
        "display_name": "Trial User",
        "avito_profile_url": "https://www.avito.ru/user/trial123/profile",
        "primary_category_id": "00000000-0000-0000-0000-000000000001",
    }, headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 200

    # Check trials
    resp = await client.get("/v1/me/trials", headers={"Authorization": f"Bearer {access_token}"})
    trials = resp.json()
    assert len(trials) == 1

    # Try to trigger onboarding again
    resp = await client.patch("/v1/me", json={
        "display_name": "Trial User Updated",
    }, headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 200

    # Still only one trial
    resp = await client.get("/v1/me/trials", headers={"Authorization": f"Bearer {access_token}"})
    trials = resp.json()
    assert len(trials) == 1


# ══════════════════════════════════════════════
#  Test 5: Login with invalid credentials
# ══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_login_invalid_credentials(client: AsyncClient):
    """Test login with wrong password."""
    # Register
    await client.post("/v1/auth/register", json={
        "email": "login_test@mail.ru",
        "password": "securepass123",
        "registration_source": "WEB",
    })

    # Login with wrong password
    resp = await client.post("/v1/auth/login", json={
        "identity": "login_test@mail.ru",
        "password": "wrongpassword",
    })
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_CREDENTIALS"


# ══════════════════════════════════════════════
#  Test 6: Token refresh
# ══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_token_refresh(client: AsyncClient):
    """Test refresh token rotation."""
    # Register
    resp = await client.post("/v1/auth/register", json={
        "email": "refresh@mail.ru",
        "password": "securepass123",
        "registration_source": "WEB",
    })
    refresh_token = resp.json()["refresh_token"]

    # Refresh
    resp = await client.post("/v1/auth/refresh", json={
        "refresh_token": refresh_token,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["refresh_token"] != refresh_token  # Rotation

    # Old refresh token should be invalid
    resp = await client.post("/v1/auth/refresh", json={
        "refresh_token": refresh_token,
    })
    assert resp.status_code == 401


# ══════════════════════════════════════════════
#  Test 7: Logout
# ══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_logout(client: AsyncClient):
    """Test logout revokes refresh token."""
    resp = await client.post("/v1/auth/register", json={
        "email": "logout@mail.ru",
        "password": "securepass123",
        "registration_source": "WEB",
    })
    refresh_token = resp.json()["refresh_token"]

    # Logout
    resp = await client.post("/v1/auth/logout", json={
        "refresh_token": refresh_token,
    })
    assert resp.status_code == 200

    # Refresh should fail
    resp = await client.post("/v1/auth/refresh", json={
        "refresh_token": refresh_token,
    })
    assert resp.status_code == 401


# ══════════════════════════════════════════════
#  Test 8: Telegram link flow
# ══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_telegram_linking(client: AsyncClient):
    """Test Telegram account linking flow."""
    # Register user
    resp = await client.post("/v1/auth/register", json={
        "email": "telegram_link@mail.ru",
        "password": "securepass123",
        "registration_source": "WEB",
    })
    access_token = resp.json()["access_token"]

    # Create link token
    resp = await client.post(
        "/v1/auth/link/telegram/create",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "link_token" in data
    assert "deep_link" in data
    link_token = data["link_token"]

    # Confirm link
    resp = await client.post("/v1/auth/link/telegram/confirm", json={
        "link_token": link_token,
        "telegram_user_id": "123456789",
        "telegram_username": "testuser",
    })
    assert resp.status_code == 200
    assert resp.json()["message"] == "Telegram linked successfully"

    # Check identities
    resp = await client.get("/v1/me/identities", headers={"Authorization": f"Bearer {access_token}"})
    identities = resp.json()
    types = [i["type"] for i in identities]
    assert "TELEGRAM" in types
    assert "EMAIL" in types


# ══════════════════════════════════════════════
#  Test 9: Telegram find user
# ══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_telegram_find_user(client: AsyncClient):
    """Test Telegram bot finding existing user."""
    # Register user with phone
    resp = await client.post("/v1/auth/register", json={
        "phone": "+79991234567",
        "password": "securepass123",
        "registration_source": "WEB",
    })
    user_id = resp.json()["user_id"]

    # Find by phone (Scenario B)
    resp = await client.post("/v1/auth/telegram/find", params={
        "telegram_user_id": "987654321",
        "phone": "+79991234567",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is True
    assert data["scenario"] == "B"

    # Find by unknown (Scenario C)
    resp = await client.post("/v1/auth/telegram/find", params={
        "telegram_user_id": "111111111",
        "phone": "+79990000000",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["found"] is False
    assert data["scenario"] == "C"


# ══════════════════════════════════════════════
#  Test 10: Sessions management
# ══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_sessions_management(client: AsyncClient):
    """Test listing and revoking sessions."""
    # Register
    resp = await client.post("/v1/auth/register", json={
        "email": "sessions@mail.ru",
        "password": "securepass123",
        "registration_source": "WEB",
    })
    access_token = resp.json()["access_token"]

    # List sessions
    resp = await client.get("/v1/auth/sessions", headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    assert len(sessions) >= 1

    # Revoke a session
    session_id = sessions[0]["id"]
    resp = await client.delete(f"/v1/auth/sessions/{session_id}", headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 200


# ══════════════════════════════════════════════
#  Test 11: Categories catalog
# ══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_categories(client: AsyncClient):
    """Test getting categories."""
    resp = await client.get("/v1/catalog/categories")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ══════════════════════════════════════════════
#  Test 12: Referral creation and click
# ══════════════════════════════════════════════
@pytest.mark.asyncio
async def test_referral_flow(client: AsyncClient):
    """Test referral link creation and click tracking."""
    # Create referral
    resp = await client.post("/v1/referrals", json={
        "manager_id": "00000000-0000-0000-0000-000000000001",
        "channel": "TELEGRAM",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    assert "link" in data
    token = data["token"]

    # Record click
    resp = await client.post("/v1/referrals/click", json={
        "token": token,
        "telegram_user_id": "123456789",
    })
    assert resp.status_code == 200
    assert "referral_id" in resp.json()