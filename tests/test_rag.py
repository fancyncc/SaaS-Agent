from backend.rag import search


def test_search_returns_citations():
    result = search("成员 CSV 导入 重复邮箱")
    assert result
    assert all(item["id"] and item["score"] > 0 for item in result)


def test_metadata_filter():
    assert all(item["module"] == "import" for item in search("导入", module="import"))

