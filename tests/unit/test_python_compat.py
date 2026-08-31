from compileiq.types import ProblemType, WorkerTypes
from compileiq.utils.validation import validate_scores


def test_explicit_string_enums_keep_stdlib_strenum_semantics():
    assert str(ProblemType.MIN) == "min"
    assert ProblemType("max") is ProblemType.MAX
    assert str(WorkerTypes.NATIVE) == "native"


def test_dynamic_multi_score_validation_avoids_python_311_starred_subscript():
    assert validate_scores((1.0, 2.0), 2) == (1.0, 2.0)
