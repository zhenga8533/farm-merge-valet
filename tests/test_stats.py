from farm_merge_valet.core.stats import RunStats


def test_run_stats_as_dict_contains_expected_keys() -> None:
    stats = RunStats(iterations=3, actions_taken=5, errors=1)

    data = stats.as_dict()

    assert data["iterations"] == 3
    assert data["actions_taken"] == 5
    assert data["errors"] == 1
    assert "started_at" in data
