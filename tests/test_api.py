"""API tests: auth, ownership boundaries, and status codes.

The ownership tests exist because "returning 404 for someone else's resource" is a
decision, not an accident -- and a future refactor to 403 should fail loudly.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient


def _register(client: TestClient) -> str:
    email = f"u-{uuid.uuid4().hex[:10]}@markaz.dev"
    r = client.post(
        "/api/auth/register",
        json={"email": email, "password": "good-password-9", "display_name": "Test"},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"]


class TestHealth:
    def test_health_reports_real_database_state(self, client: TestClient) -> None:
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["database"] == "ok"

    def test_security_headers_present(self, client: TestClient) -> None:
        r = client.get("/api/health")
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert "default-src 'self'" in r.headers["Content-Security-Policy"]
        assert r.headers["X-Request-ID"]


class TestAuth:
    def test_register_returns_token_and_user(self, client: TestClient) -> None:
        r = client.post(
            "/api/auth/register",
            json={
                "email": f"a-{uuid.uuid4().hex[:8]}@markaz.dev",
                "password": "good-password-9",
                "display_name": "  Padded Name  ",
            },
        )
        assert r.status_code == 201
        body = r.json()
        assert body["token_type"] == "bearer"
        assert body["user"]["display_name"] == "Padded Name"  # trimmed
        assert "password" not in body["user"]

    def test_duplicate_email_is_409(self, client: TestClient) -> None:
        email = f"dup-{uuid.uuid4().hex[:8]}@markaz.dev"
        payload = {"email": email, "password": "good-password-9", "display_name": "X"}
        assert client.post("/api/auth/register", json=payload).status_code == 201
        assert client.post("/api/auth/register", json=payload).status_code == 409

    def test_all_letters_password_rejected(self, client: TestClient) -> None:
        r = client.post(
            "/api/auth/register",
            json={
                "email": f"weak-{uuid.uuid4().hex[:8]}@markaz.dev",
                "password": "onlyletters",
                "display_name": "X",
            },
        )
        assert r.status_code == 422

    def test_short_password_rejected(self, client: TestClient) -> None:
        r = client.post(
            "/api/auth/register",
            json={
                "email": f"short-{uuid.uuid4().hex[:8]}@markaz.dev",
                "password": "a1",
                "display_name": "X",
            },
        )
        assert r.status_code == 422

    def test_wrong_password_is_401(self, client: TestClient) -> None:
        email = f"wp-{uuid.uuid4().hex[:8]}@markaz.dev"
        client.post(
            "/api/auth/register",
            json={"email": email, "password": "good-password-9", "display_name": "X"},
        )
        r = client.post("/api/auth/login", json={"email": email, "password": "wrong-pass-9"})
        assert r.status_code == 401

    def test_unknown_email_gives_identical_error(self, client: TestClient) -> None:
        """No user-enumeration: same status and same message as a bad password."""
        r = client.post(
            "/api/auth/login",
            json={"email": "nobody-here@markaz.dev", "password": "good-password-9"},
        )
        assert r.status_code == 401
        assert r.json()["detail"] == "Email or password is incorrect."

    def test_missing_token_is_401(self, client: TestClient) -> None:
        assert client.get("/api/auth/me").status_code == 401

    def test_garbage_token_is_401(self, client: TestClient) -> None:
        r = client.get("/api/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
        assert r.status_code == 401


class TestRegions:
    def test_requires_auth(self, client: TestClient) -> None:
        assert client.get("/api/regions").status_code == 401

    def test_capacity_derived_from_no_deployments(self, auth_client: TestClient) -> None:
        r = auth_client.get("/api/regions")
        assert r.status_code == 200
        for region in r.json():
            assert region["committed_gpus"] == 0
            assert region["headroom_gpus"] == region["gpu_capacity"]
            assert region["utilisation"] == 0.0

    def test_unknown_region_models_is_404(self, auth_client: TestClient) -> None:
        assert auth_client.get("/api/regions/mars-1/models").status_code == 404

    def test_models_endpoint_excludes_sovereign(self, auth_client: TestClient) -> None:
        r = auth_client.get("/api/regions/us-east/models")
        assert r.status_code == 200
        assert "med-llm-sovereign" in r.json()["excluded"]


class TestDeployments:
    def _payload(self, **over: str | int) -> dict:
        base = {
            "name": "embedder",
            "model_code": "llama-3-8b",
            "region_code": "ap-south",
            "sku_code": "l4-24",
            "replicas": 2,
        }
        base.update(over)
        return base

    def test_plan_accepted_computes_cost(self, auth_client: TestClient) -> None:
        r = auth_client.post("/api/deployments/plan", json=self._payload())
        assert r.status_code == 200
        body = r.json()
        assert body["verdict"]["accepted"] is True
        # llama-3-8b needs 16GB; one 24GB L4 per replica -> 2 replicas = 2 GPUs.
        assert body["verdict"]["required_gpus"] == 2
        # 2 GPUs * $1.10/h * 730h
        assert body["cost"]["monthly_usd"] == 1606.0

    def test_plan_rejected_reports_reasons_without_writing(self, auth_client: TestClient) -> None:
        """A plan is advice, not an action -- a rejection is a 200 with a verdict."""
        before = len(auth_client.get("/api/deployments").json())
        r = auth_client.post("/api/deployments/plan", json=self._payload(model_code="fin-llm-us"))
        assert r.status_code == 200
        body = r.json()
        assert body["verdict"]["accepted"] is False
        assert "residency_violation" in [x["code"] for x in body["verdict"]["reasons"]]
        assert len(auth_client.get("/api/deployments").json()) == before

    def test_create_rejected_is_409_with_reasons(self, auth_client: TestClient) -> None:
        """Applying a rejected plan *is* an action, so it must fail loudly."""
        r = auth_client.post("/api/deployments", json=self._payload(model_code="fin-llm-us"))
        assert r.status_code == 409
        assert r.json()["detail"]["message"] == "Deployment rejected by admission control."
        assert r.json()["detail"]["reasons"]

    def test_create_persists_and_snapshots_cost(self, auth_client: TestClient) -> None:
        r = auth_client.post(
            "/api/deployments", json=self._payload(), headers={"X-Request-ID": "r-1"}
        )
        assert r.status_code == 201
        d = r.json()
        assert d["status"] == "active"
        assert d["required_gpus"] == 2
        assert d["monthly_cost_usd"] == 1606.0

    def test_create_unknown_model_is_422(self, auth_client: TestClient) -> None:
        r = auth_client.post("/api/deployments", json=self._payload(model_code="ghost-1"))
        assert r.status_code == 422

    def test_create_zero_replicas_is_422(self, auth_client: TestClient) -> None:
        assert (
            auth_client.post("/api/deployments", json=self._payload(replicas=0)).status_code == 422
        )

    def test_capacity_is_consumed_by_creation(self, auth_client: TestClient) -> None:
        before = {r["code"]: r for r in auth_client.get("/api/regions").json()}
        auth_client.post("/api/deployments", json=self._payload(replicas=3))
        after = {r["code"]: r for r in auth_client.get("/api/regions").json()}
        assert after["ap-south"]["committed_gpus"] == before["ap-south"]["committed_gpus"] + 3

    def test_scale_beyond_capacity_is_409(self, auth_client: TestClient) -> None:
        created = auth_client.post("/api/deployments", json=self._payload()).json()
        r = auth_client.patch(f"/api/deployments/{created['id']}/scale", json={"replicas": 64})
        assert r.status_code == 409
        assert r.json()["detail"]["verdict"]["accepted"] is False

    def test_scale_keeps_existing_usage_not_double_counted(self, auth_client: TestClient) -> None:
        """Scaling 2 -> 3 must not be rejected for the 2 you already own."""
        created = auth_client.post("/api/deployments", json=self._payload()).json()
        r = auth_client.patch(f"/api/deployments/{created['id']}/scale", json={"replicas": 3})
        assert r.status_code == 200
        assert r.json()["replicas"] == 3
        assert r.json()["required_gpus"] == 3

    def test_observe_records_drift(self, auth_client: TestClient) -> None:
        created = auth_client.post("/api/deployments", json=self._payload()).json()
        r = auth_client.post(
            f"/api/deployments/{created['id']}/observe", json={"observed_replicas": 5}
        )
        assert r.status_code == 200
        assert r.json()["observed_replicas"] == 5

        drift = auth_client.get("/api/deployments/drift/report")
        assert drift.status_code == 200
        finding = next(f for f in drift.json()["findings"] if f["deployment_id"] == created["id"])
        assert finding["state"] == "scaled_out"
        assert finding["severity"] == "critical"

    def test_drift_report_counts_reality(self, auth_client: TestClient) -> None:
        d1 = auth_client.post("/api/deployments", json=self._payload()).json()
        d2 = auth_client.post("/api/deployments", json=self._payload(name="second-deploy")).json()
        auth_client.post(f"/api/deployments/{d1['id']}/observe", json={"observed_replicas": 2})
        auth_client.post(f"/api/deployments/{d2['id']}/observe", json={"observed_replicas": 0})
        body = auth_client.get("/api/deployments/drift/report").json()
        summary = body["summary"]
        assert summary["total"] == 2
        assert summary["in_sync"] == 1
        assert summary["drifted"] == 1
        assert summary["critical"] == 1
        assert summary["clean"] is False

    def test_other_users_deployment_is_404_not_403(self, client: TestClient) -> None:
        owner = client
        token_a = _register(owner)
        payload = self._payload()
        created = owner.post(
            "/api/deployments", json=payload, headers={"Authorization": f"Bearer {token_a}"}
        )
        assert created.status_code == 201
        dep_id = created.json()["id"]

        token_b = _register(client)
        r = client.patch(
            f"/api/deployments/{dep_id}/scale",
            json={"replicas": 1},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert r.status_code == 404

    def test_delete_returns_204_and_removes(self, auth_client: TestClient) -> None:
        created = auth_client.post("/api/deployments", json=self._payload()).json()
        r = auth_client.delete(f"/api/deployments/{created['id']}")
        assert r.status_code == 204
        assert auth_client.get(f"/api/deployments/{created['id']}").status_code == 404


class TestPipelines:
    def _payload(self) -> dict:
        return {
            "name": "rag-ingest",
            "description": "chunk, embed, index",
            "steps": [
                {"key": "ingest", "operation": "fetch", "duration_seconds": 45},
                {"key": "chunk", "operation": "split", "duration_seconds": 90},
                {"key": "embed", "operation": "encode", "duration_seconds": 240},
            ],
            "edges": [
                {"before": "ingest", "after": "chunk"},
                {"before": "chunk", "after": "embed"},
            ],
        }

    def test_create_valid_pipeline(self, auth_client: TestClient) -> None:
        r = auth_client.post("/api/pipelines", json=self._payload())
        assert r.status_code == 201
        body = r.json()
        assert body["is_valid"] is True
        assert body["critical_path_seconds"] == 375

    def test_cycle_is_422_with_named_nodes(self, auth_client: TestClient) -> None:
        payload = self._payload()
        payload["edges"].append({"before": "embed", "after": "ingest"})
        r = auth_client.post("/api/pipelines", json=payload)
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert detail["issues"][0]["code"] == "cycle_detected"
        assert set(detail["cycle_nodes"]) == {"ingest", "chunk", "embed"}

    def test_detail_includes_run_order(self, auth_client: TestClient) -> None:
        created = auth_client.post("/api/pipelines", json=self._payload()).json()
        r = auth_client.get(f"/api/pipelines/{created['id']}")
        assert r.status_code == 200
        assert r.json()["validation"]["order"] == ["ingest", "chunk", "embed"]

    def test_unknown_model_on_step_is_422(self, auth_client: TestClient) -> None:
        payload = self._payload()
        payload["steps"][0]["model_code"] = "ghost-1"
        assert auth_client.post("/api/pipelines", json=payload).status_code == 422

    def test_duplicate_step_key_is_422(self, auth_client: TestClient) -> None:
        payload = self._payload()
        payload["steps"].append({"key": "ingest", "operation": "again", "duration_seconds": 5})
        assert auth_client.post("/api/pipelines", json=payload).status_code == 422

    def test_empty_steps_is_422(self, auth_client: TestClient) -> None:
        assert (
            auth_client.post(
                "/api/pipelines", json={"name": "x", "steps": [], "edges": []}
            ).status_code
            == 422
        )

    def test_other_users_pipeline_is_404(self, client: TestClient) -> None:
        token_a = _register(client)
        created = client.post(
            "/api/pipelines", json=self._payload(), headers={"Authorization": f"Bearer {token_a}"}
        )
        assert created.status_code == 201
        pid = created.json()["id"]

        token_b = _register(client)
        r = client.get(f"/api/pipelines/{pid}", headers={"Authorization": f"Bearer {token_b}"})
        assert r.status_code == 404


class TestRouting:
    def test_api_404_is_json_not_html(self, client: TestClient) -> None:
        r = client.get("/api/does-not-exist")
        assert r.status_code == 404
        assert r.headers["content-type"].startswith("application/json")

    def test_root_serves_the_spa(self, client: TestClient) -> None:
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "Markaz" in r.text
