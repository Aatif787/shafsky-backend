from app.monitoring.health import HealthCheckSuite


def test_deep_health_shape_does_not_fake_notifications():
    payload = HealthCheckSuite.run_deep_health(use_cache=False)
    assert payload["status"] in ("UP", "DEGRADED")
    assert "timestamp" in payload
    db = payload["subsystems"]["database"]
    assert db["status"] in ("HEALTHY", "SLOW", "UNHEALTHY")
    notify = payload["subsystems"]["notificationService"]
    assert notify["status"] in ("CONFIGURED", "NOT_CONFIGURED")
    assert "emailConfigured" in notify
    wa = payload["subsystems"]["whatsapp"]
    assert "configured" in wa
    assert wa["status"] in ("CONFIGURED", "NOT_CONFIGURED")
    res = payload["subsystems"]["systemResources"]
    assert "processRssMB" in res
    assert "memoryUsagePercent" in res


def test_liveness_is_cheap():
    live = HealthCheckSuite.run_liveness()
    assert live["status"] == "ALIVE"
