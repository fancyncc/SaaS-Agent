from backend.evaluation import run_fixed_evaluation


def test_fixed_dataset_is_valid_and_large_enough():
    result = run_fixed_evaluation()
    assert result["dataset_size"] >= 50
    assert result["schema_valid"]
    assert result["duplicate_ids"] == 0
    assert len(result["categories"]) >= 8
