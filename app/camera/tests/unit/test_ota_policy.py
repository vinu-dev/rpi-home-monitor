# REQ: SWR-010, SWR-038; RISK: RISK-004, RISK-019; SEC: SC-003, SC-018; TEST: TC-013, TC-036
from camera_streamer import ota_policy


def test_blocks_parseable_downgrade():
    decision = ota_policy.classify_update("1.6.0", "1.4.1-dev")

    assert decision.blocked is True
    assert decision.relation == "downgrade"


def test_allows_upgrade_and_same_version():
    assert ota_policy.classify_update("1.6.0", "1.7.0").relation == "upgrade"
    assert ota_policy.classify_update("1.6.0", "v1.6.0").relation == "same"


def test_build_profile_suffix_matches_runtime_version():
    decision = ota_policy.classify_update("1.6.10", "v1.6.10-dev")

    assert decision.allowed is True
    assert decision.relation == "same"
    assert ota_policy.versions_match("1.6.10", "v1.6.10-dev") is True


def test_unknown_versions_are_not_ordered():
    decision = ota_policy.classify_update("1.6.0", "")

    assert decision.allowed is True
    assert decision.relation == "unknown"
