"""
专项1：GapDetector分类准确率测试
目标：验证GapDetector的分类是否准确，false positive/negative率多少
成功标准：准确率 > 90%（20个样本中至少18个正确）
"""

import pytest
from kaiwu.core.gap_detector import GapDetector, GapType


@pytest.fixture
def detector():
    return GapDetector()


# ══════════════════════════════════════
# 测试样本：每种GapType 2-3个样本
# ══════════════════════════════════════

CASES = [
    # --- MISSING_TOOLCHAIN ---
    (
        "bash: go: not found\n",
        GapType.MISSING_TOOLCHAIN,
        "go not found",
    ),
    (
        "/bin/sh: 1: node: not found\nnpm ERR! code ELIFECYCLE",
        GapType.MISSING_TOOLCHAIN,
        "node not found via /bin/sh",
    ),
    (
        "command not found: cargo\nerror: could not compile",
        GapType.MISSING_TOOLCHAIN,
        "cargo not found",
    ),

    # --- MISSING_DEP ---
    (
        "ModuleNotFoundError: No module named 'redis'\n"
        "ImportError: cannot import name 'Client'",
        GapType.MISSING_DEP,
        "missing redis module",
    ),
    (
        "FAILED tests/test_api.py::test_connect\n"
        "ModuleNotFoundError: No module named 'fastapi'",
        GapType.MISSING_DEP,
        "missing fastapi",
    ),

    # --- NOT_IMPLEMENTED ---
    (
        "FAILED tests/test_calc.py::test_add\n"
        "NotImplementedError\n"
        "raise NotImplementedError",
        GapType.NOT_IMPLEMENTED,
        "explicit NotImplementedError",
    ),
    (
        "tests/test_parser.py::test_parse FAILED\n"
        "E       NotImplementedError: not implemented yet",
        GapType.NOT_IMPLEMENTED,
        "not implemented message",
    ),

    # --- STUB_RETURNS_NONE ---
    (
        "FAILED tests/test_config.py::test_load\n"
        "AttributeError: 'NoneType' object has no attribute 'get'\n"
        "E       TypeError: 'NoneType' object is not iterable",
        GapType.STUB_RETURNS_NONE,
        "NoneType has no attribute (pass stub)",
    ),
    (
        "TypeError: unsupported operand type(s) for +: 'NoneType' and 'int'\n"
        "FAILED test_math.py::test_sum",
        GapType.STUB_RETURNS_NONE,
        "NoneType unsupported operand",
    ),

    # --- SYNTAX_STRUCTURAL ---
    (
        "  File \"src/main.py\", line 15\n"
        "    def foo()\n"
        "           ^\n"
        "SyntaxError: expected ':'",
        GapType.SYNTAX_STRUCTURAL,
        "Python SyntaxError",
    ),
    (
        "  File \"src/utils.py\", line 8\n"
        "    return x\n"
        "IndentationError: unexpected indent",
        GapType.SYNTAX_STRUCTURAL,
        "IndentationError",
    ),

    # --- LOGIC_ERROR ---
    (
        "FAILED tests/test_sort.py::test_bubble_sort\n"
        "AssertionError: assert [3, 1, 2] == [1, 2, 3]\n"
        "E       At index 0 diff: 3 != 1",
        GapType.LOGIC_ERROR,
        "assertion failure in sort",
    ),
    (
        "--- FAIL: TestAdd (0.00s)\n"
        "    calc_test.go:15: expected 5, got 4\n"
        "FAIL\n"
        "exit status 1",
        GapType.LOGIC_ERROR,
        "Go test FAIL",
    ),
    (
        "FAILED tests/test_api.py::test_response_code\n"
        "AssertionError: assert 404 == 200",
        GapType.LOGIC_ERROR,
        "HTTP status assertion",
    ),

    # --- NONE (all passed) ---
    (
        "============================= 5 passed in 0.12s =============================",
        GapType.NONE,
        "pytest all passed",
    ),
    (
        "ok  \tgithub.com/user/project\t0.005s\nPASS",
        GapType.NONE,
        "go test PASS",
    ),

    # --- UNKNOWN ---
    (
        "some random output that doesn't match any pattern\nfoo bar baz",
        GapType.UNKNOWN,
        "unrecognizable output",
    ),

    # --- 边界情况：多gap叠加（ImportError + AssertionError）---
    # 按优先级，ImportError应该优先
    (
        "ImportError: No module named 'numpy'\n"
        "AssertionError: assert False",
        GapType.MISSING_DEP,
        "multi-gap: import takes priority over assertion",
    ),

    # --- 边界情况：go test正常输出（无测试文件）---
    (
        "?   \tgithub.com/user/project\t[no test files]\nok",
        GapType.NONE,
        "go no test files but ok",
    ),
]


class TestGapDetectorAccuracy:
    """GapDetector分类准确率测试：20个手工样本，目标>90%。"""

    @pytest.mark.parametrize("output,expected_type,description", CASES,
                             ids=[c[2] for c in CASES])
    def test_classification(self, detector, output, expected_type, description):
        """验证单个样本的分类结果。"""
        gap = detector.compute(output)
        assert gap.gap_type == expected_type, (
            f"[{description}] 期望 {expected_type.value}，"
            f"实际 {gap.gap_type.value}"
        )

    def test_overall_accuracy(self, detector):
        """验证整体准确率 > 90%。"""
        correct = 0
        total = len(CASES)
        failures = []

        for output, expected_type, description in CASES:
            gap = detector.compute(output)
            if gap.gap_type == expected_type:
                correct += 1
            else:
                failures.append(
                    f"  [{description}] 期望={expected_type.value} 实际={gap.gap_type.value}"
                )

        accuracy = correct / total
        assert accuracy >= 0.9, (
            f"准确率 {accuracy:.0%} ({correct}/{total}) 低于90%阈值。\n"
            f"失败样本：\n" + "\n".join(failures)
        )

    def test_confidence_ranges(self, detector):
        """验证confidence值在合理范围内。"""
        for output, expected_type, description in CASES:
            gap = detector.compute(output)
            assert 0.0 <= gap.confidence <= 1.0, (
                f"[{description}] confidence={gap.confidence} 超出[0,1]范围"
            )
            # 高确定性类型应该有高confidence
            if expected_type in (GapType.MISSING_TOOLCHAIN, GapType.MISSING_DEP,
                                 GapType.SYNTAX_STRUCTURAL):
                assert gap.confidence >= 0.8, (
                    f"[{description}] 高确定性类型confidence应>=0.8，"
                    f"实际={gap.confidence}"
                )

    def test_empty_input(self, detector):
        """空输入应返回NO_TEST。"""
        gap = detector.compute("")
        assert gap.gap_type == GapType.NO_TEST

        gap = detector.compute("   ")
        assert gap.gap_type == GapType.NO_TEST

    def test_none_input(self, detector):
        """None输入应返回NO_TEST。"""
        gap = detector.compute(None)
        assert gap.gap_type == GapType.NO_TEST
