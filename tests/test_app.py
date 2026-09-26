import pytest
from fastapi.testclient import TestClient

from shoplist import main

HEADERS = {"X-Requested-With": "shoplist"}


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SHOPLIST_DB", str(tmp_path / "test.db"))
    main.limiter.reset("testclient")
    clients = []

    def factory():
        c = TestClient(main.app, headers=HEADERS)
        c.__enter__()
        clients.append(c)
        return c

    yield factory
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def admin(make_client):
    c = make_client()
    r = c.post("/api/setup", json={"username": "alex", "display_name": "Alex", "password": "password1"})
    assert r.status_code == 201
    return c


def login(make_client, username, password):
    c = make_client()
    r = c.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


def test_setup_only_once(make_client):
    c = make_client()
    assert c.get("/api/session").json() == {"setup_required": True, "user": None}
    c.post("/api/setup", json={"username": "alex", "display_name": "Alex", "password": "password1"})
    r = c.post("/api/setup", json={"username": "eve", "display_name": "Eve", "password": "password1"})
    assert r.status_code == 409
    assert c.get("/api/session").json()["user"]["is_admin"] is True


def test_requires_login_and_csrf_header(admin, make_client):
    anon = make_client()
    assert anon.get("/api/list").status_code == 401
    r = admin.post("/api/items", json={"name": "Eggs"}, headers={"X-Requested-With": ""})
    assert r.status_code == 403


def test_household_members_see_who_added_what(admin, make_client):
    admin.post("/api/users", json={"username": "sam", "display_name": "Sam", "password": "password2"})
    sam = login(make_client, "sam", "password2")

    admin.post("/api/items", json={"name": "Eggs", "quantity": 12})
    sam.post("/api/items", json={"name": "Milk", "quantity": 2})

    items = {i["name"]: i for i in sam.get("/api/list").json()["items"]}
    assert items["Eggs"]["added_by"] == "Alex" and items["Eggs"]["quantity"] == 12
    assert items["Milk"]["added_by"] == "Sam"


def test_adding_same_item_bumps_quantity(admin):
    admin.post("/api/items", json={"name": "Eggs", "quantity": 6})
    r = admin.post("/api/items", json={"name": "  eggs ", "quantity": 6})
    assert r.json()["quantity"] == 12
    assert len(admin.get("/api/list").json()["items"]) == 1


def test_check_edit_delete(admin):
    item = admin.post("/api/items", json={"name": "Bread"}).json()
    r = admin.patch(f"/api/items/{item['id']}", json={"checked": True, "quantity": 2})
    assert r.json()["checked"] is True and r.json()["checked_by"] == "Alex"
    assert r.json()["quantity"] == 2
    assert admin.delete(f"/api/items/{item['id']}").status_code == 204
    assert admin.get("/api/list").json()["items"] == []


def test_shop_complete_moves_list_to_history(admin):
    list_id = admin.get("/api/list").json()["id"]
    bought = admin.post("/api/items", json={"name": "Eggs"}).json()
    admin.post("/api/items", json={"name": "Saffron"})
    admin.patch(f"/api/items/{bought['id']}", json={"checked": True})

    r = admin.post("/api/list/complete", json={"list_id": list_id})
    assert r.json()["carried_over"] == 1

    new = admin.get("/api/list").json()
    assert new["id"] != list_id
    assert [i["name"] for i in new["items"]] == ["Saffron"]

    hist = admin.get("/api/history").json()
    assert hist[0]["id"] == list_id and hist[0]["item_count"] == 2 and hist[0]["bought_count"] == 1
    assert hist[0]["completed_by"] == "Alex"
    detail = admin.get(f"/api/history/{list_id}").json()
    assert {i["name"] for i in detail["items"]} == {"Eggs", "Saffron"}

    # Items on a completed list can't be edited any more.
    assert admin.patch(f"/api/items/{bought['id']}", json={"checked": False}).status_code == 404


def test_shop_complete_mark_all_bought(admin):
    list_id = admin.get("/api/list").json()["id"]
    admin.post("/api/items", json={"name": "Eggs"})
    admin.post("/api/items", json={"name": "Saffron"})

    r = admin.post("/api/list/complete", json={"list_id": list_id, "mark_all_bought": True})
    assert r.json()["carried_over"] == 0
    assert admin.get("/api/list").json()["items"] == []

    detail = admin.get(f"/api/history/{list_id}").json()
    assert all(i["checked"] and i["checked_by"] == "Alex" for i in detail["items"])
    assert admin.get("/api/history").json()[0]["bought_count"] == 2


def test_double_complete_is_rejected(admin):
    list_id = admin.get("/api/list").json()["id"]
    assert admin.post("/api/list/complete", json={"list_id": list_id}).status_code == 200
    assert admin.post("/api/list/complete", json={"list_id": list_id}).status_code == 409


def test_only_admins_manage_members(admin, make_client):
    admin.post("/api/users", json={"username": "sam", "display_name": "Sam", "password": "password2"})
    sam = login(make_client, "sam", "password2")
    assert sam.get("/api/users").status_code == 403
    r = sam.post("/api/users", json={"username": "x", "display_name": "X", "password": "password3"})
    assert r.status_code == 403


def test_disabled_member_is_logged_out_but_kept_in_history(admin, make_client):
    user = admin.post(
        "/api/users", json={"username": "sam", "display_name": "Sam", "password": "password2"}
    ).json()
    sam = login(make_client, "sam", "password2")
    sam.post("/api/items", json={"name": "Milk"})

    admin.patch(f"/api/users/{user['id']}", json={"active": False})
    assert sam.get("/api/list").status_code == 401
    assert make_client().post(
        "/api/login", json={"username": "sam", "password": "password2"}
    ).status_code == 401
    assert admin.get("/api/list").json()["items"][0]["added_by"] == "Sam"


def test_last_admin_cannot_be_removed(admin):
    me = admin.get("/api/session").json()["user"]
    r = admin.patch(f"/api/users/{me['id']}", json={"is_admin": False})
    assert r.status_code == 400


def test_password_change_and_whitespace_preserved(make_client):
    c = make_client()
    c.post("/api/setup", json={"username": "alex", "display_name": "Alex", "password": " spaced pw "})
    login(make_client, "alex", " spaced pw ")
    r = c.post("/api/me/password", json={"current_password": "wrong", "new_password": "newpassword"})
    assert r.status_code == 400
    r = c.post("/api/me/password", json={"current_password": " spaced pw ", "new_password": "newpassword"})
    assert r.status_code == 204
    login(make_client, "alex", "newpassword")


def test_login_rate_limit(admin, make_client):
    c = make_client()
    for _ in range(10):
        assert c.post("/api/login", json={"username": "alex", "password": "nope"}).status_code == 401
    assert c.post("/api/login", json={"username": "alex", "password": "password1"}).status_code == 429
