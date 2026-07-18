def test_health_check_is_public(api_client, db):
    response = api_client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ok"
