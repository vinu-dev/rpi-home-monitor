# REQ: SWR-010, SWR-046; RISK: RISK-004, RISK-019; SEC: SC-003, SC-018; TEST: TC-013, TC-043
from monitor import ota_policy


def test_blocks_parseable_downgrade():
    decision = ota_policy.classify_update("1.6.0", "1.4.1-dev")

    assert decision.blocked is True
    assert decision.relation == "downgrade"
    assert "current version is 1.6.0" in decision.reason


def test_allows_upgrade_and_same_version():
    assert ota_policy.classify_update("1.6.0", "1.7.0").relation == "upgrade"
    assert ota_policy.classify_update("1.6.0", "v1.6.0").relation == "same"


def test_unknown_versions_are_not_ordered():
    decision = ota_policy.classify_update("1.6.0", "")

    assert decision.allowed is True
    assert decision.relation == "unknown"


def test_semver_prerelease_ordering():
    assert ota_policy.compare_versions("1.6.0-rc.1", "1.6.0") == -1
    assert ota_policy.compare_versions("1.6.0-rc.2", "1.6.0-rc.1") == 1
