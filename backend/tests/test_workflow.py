"""Two-step approval, comments & mentions, assignment, notifications and reports."""

from tests.conftest import upload_sample


def add_member(client, auth, email, role):
    r = client.post("/api/users", headers=auth, json={"email": email, "full_name": email.split("@")[0].title(),
                                                      "password": "password123", "role": role})
    assert r.status_code == 201, r.text
    token = client.post("/api/auth/login", data={"username": email, "password": "password123"}).json()["access_token"]
    return r.json()["id"], {"Authorization": f"Bearer {token}"}


def generate(client, auth):
    catalog_id = upload_sample(client, auth)["catalog"]["id"]
    client.post("/api/jobs", headers=auth, json={"catalog_id": catalog_id})
    products = client.get(f"/api/catalogs/{catalog_id}/products", headers=auth).json()["items"]
    return catalog_id, products


def notes(client, headers, unread=True):
    return client.get(f"/api/notifications?unread={'true' if unread else 'false'}", headers=headers).json()


def test_two_step_approval(client, auth):
    _, reviewer = add_member(client, auth, "rev@shop.com", "reviewer")
    catalog_id, products = generate(client, auth)
    assert client.patch("/api/settings", headers=auth, json={"approval_steps": 2}).json()["approval_steps"] == 2

    v = products[0]["current"]["en"]
    first = client.post(f"/api/descriptions/{v['id']}/approve", headers=reviewer, json={}).json()
    assert first["status"] == "in_review"
    assert client.post(f"/api/descriptions/{v['id']}/approve", headers=reviewer, json={}).status_code == 403
    assert any(n["kind"] == "approval_needed" for n in notes(client, auth))

    # Not exportable until final approval.
    export = client.post("/api/exports", headers=auth, json={"catalog_id": catalog_id, "format": "csv_generic"}).json()
    assert client.get(f"/api/exports/{export['id']}", headers=auth).json()["total"] == 0

    final = client.post(f"/api/descriptions/{v['id']}/approve", headers=auth, json={}).json()
    assert final["status"] == "approved"

    r = client.post(f"/api/catalogs/{catalog_id}/approve-drafts", headers=reviewer,
                    json={"language": "en", "min_seo_score": 0, "skip_flagged": False}).json()
    assert r["updated"] == 7 and r["awaiting_final_approval"] == 7
    stats = client.get(f"/api/catalogs/{catalog_id}", headers=auth).json()["stats"]
    assert stats["by_language"]["en"]["in_review"] == 7 and stats["approval_steps"] == 2
    assert client.get(f"/api/catalogs/{catalog_id}/products?status=in_review", headers=auth).json()["total"] == 7

    r = client.post(f"/api/catalogs/{catalog_id}/approve-drafts", headers=auth,
                    json={"language": "en", "min_seo_score": 0, "skip_flagged": False}).json()
    assert r["updated"] == 7 and r["awaiting_final_approval"] == 0
    assert client.get(f"/api/catalogs/{catalog_id}", headers=auth).json()["stats"]["by_language"]["en"]["approved"] == 8


def test_comments_mentions_and_assignment(client, auth):
    rev_id, reviewer = add_member(client, auth, "rev@shop.com", "reviewer")
    catalog_id, products = generate(client, auth)
    product = products[0]

    r = client.patch(f"/api/products/{product['id']}/assign", headers=auth, json={"user_id": rev_id})
    assert r.json()["assignee_id"] == rev_id
    assigned = notes(client, reviewer)
    assert assigned[0]["kind"] == "assigned" and assigned[0]["link"].endswith(f"/products/{product['id']}")

    mine = client.get(f"/api/catalogs/{catalog_id}/products?assignee=me", headers=reviewer).json()
    assert [p["id"] for p in mine["items"]] == [product["id"]]
    bulk = client.post(f"/api/catalogs/{catalog_id}/assign", headers=auth,
                       json={"product_ids": [p["id"] for p in products[1:4]], "user_id": rev_id}).json()
    assert bulk["updated"] == 3
    assert client.get(f"/api/catalogs/{catalog_id}/products?assignee=me", headers=reviewer).json()["total"] == 4
    assert client.get(f"/api/catalogs/{catalog_id}/products?assignee=none", headers=auth).json()["total"] == 4

    c = client.post(f"/api/products/{product['id']}/comments", headers=auth,
                    json={"body": "@Rev please check the bullets", "language": "en", "mentions": [rev_id, 999]}).json()
    assert c["mentions"] == [rev_id] and c["author"] == "Owner"
    mention = notes(client, reviewer)[0]
    assert mention["kind"] == "mention" and "mentioned you" in mention["title"]
    reply = client.post(f"/api/products/{product['id']}/comments", headers=reviewer, json={"body": "Done", "language": "en"})
    assert reply.status_code == 201
    thread = client.get(f"/api/products/{product['id']}/comments?language=en", headers=auth).json()
    assert [t["body"] for t in thread] == ["@Rev please check the bullets", "Done"]
    assert client.delete(f"/api/comments/{c['id']}", headers=reviewer).status_code == 403
    assert client.patch(f"/api/comments/{c['id']}?resolved=true", headers=reviewer).json()["resolved"] is True

    count = client.get("/api/notifications/unread-count", headers=reviewer).json()["count"]
    assert count >= 3
    client.post("/api/notifications/read", headers=reviewer, json={})
    assert client.get("/api/notifications/unread-count", headers=reviewer).json()["count"] == 0
    assert len(notes(client, reviewer, unread=False)) == count


def test_rejection_and_job_notifications(client, auth):
    _, reviewer = add_member(client, auth, "rev@shop.com", "reviewer")
    _, products = generate(client, auth)
    assert any(n["kind"] == "job_finished" for n in notes(client, auth))
    v = products[0]["current"]["en"]
    client.post(f"/api/descriptions/{v['id']}/reject", headers=reviewer, json={"note": "Too generic"})
    rejected = [n for n in notes(client, auth) if n["kind"] == "rejected"]
    assert rejected and rejected[0]["body"] == "Too generic"


def test_reports(client, auth):
    catalog_id, _ = generate(client, auth)
    client.post(f"/api/catalogs/{catalog_id}/approve-drafts", headers=auth,
                json={"language": "en", "min_seo_score": 0, "skip_flagged": False})
    report = client.get("/api/reports/summary?days=7", headers=auth).json()
    assert len(report["series"]) == 7
    assert report["totals"]["generated"] == 8 and report["totals"]["approved"] == 8
    assert report["totals"]["avg_seo"] > 0
    assert report["series"][-1]["generated"] == 8  # today
    owner = report["team"][0]
    assert owner["email"] == "owner@shop.com" and owner["generated"] == 8 and owner["approved"] == 8
    assert report["cost_by_catalog"][0]["catalog_id"] == catalog_id and report["cost_by_catalog"][0]["generated"] == 8
    assert report["languages"]["en"]["approved"] == 8
    assert sum(report["seo_distribution"].values()) == 8

    scoped = client.get(f"/api/reports/summary?days=7&catalog_id={catalog_id}", headers=auth).json()
    assert scoped["totals"]["generated"] == 8
    csv_text = client.get("/api/reports/daily.csv?days=7", headers=auth).content.decode("utf-8-sig")
    assert csv_text.startswith("date,generated,edited,approved,rejected,avg_seo") and len(csv_text.splitlines()) == 8
