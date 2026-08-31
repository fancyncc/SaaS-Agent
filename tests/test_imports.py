from backend.imports import validate_member_csv


def test_valid_csv():
    result = validate_member_csv("name,email,department,role\n张三,zhang@example.com,设计,member")
    assert result.valid and result.row_count == 1


def test_duplicate_and_missing_department():
    result = validate_member_csv("姓名,邮箱,部门,角色\n甲,a@example.com,,成员\n乙,a@example.com,设计,成员")
    assert not result.valid
    assert {error.row for error in result.errors} == {2, 3}


def test_missing_column():
    result = validate_member_csv("name,email\nA,a@example.com")
    assert not result.valid
    assert {e.field for e in result.errors} >= {"department", "role"}

