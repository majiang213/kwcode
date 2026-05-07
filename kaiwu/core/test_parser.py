"""
测试输出解析器：从pytest/go/jest输出提取失败和通过的测试名。
纯正则匹配，零LLM调用。供GapDetector、ExecutionStateTracker、Orchestrator共用。
"""

import re

__all__ = ["extract_failing_tests", "extract_passing_tests"]


def extract_failing_tests(output: str) -> list[str]:
    """从pytest/go/jest输出里提取失败的测试名。"""
    if not output:
        return []

    failing = []

    # pytest: "FAILED test_foo.py::TestBar::test_baz"
    failing += re.findall(r'FAILED\s+([\w/:.]+)', output)

    # go: "--- FAIL: TestFoo (0.00s)"
    failing += re.findall(r'--- FAIL:\s+(\w+)', output)

    # jest: "✕ should do something (5 ms)" or "× should do something"
    failing += re.findall(r'[✕×]\s+(.+?)(?:\s+\(\d+)', output)

    # rust: "test xxx ... FAILED"
    failing += re.findall(r'test\s+(\S+)\s+\.\.\.\s+FAILED', output)

    return list(set(failing))


def extract_passing_tests(output: str) -> list[str]:
    """从输出里提取通过的测试名。"""
    if not output:
        return []

    passing = []

    # pytest: "PASSED test_foo.py::TestBar::test_baz"
    passing += re.findall(r'PASSED\s+([\w/:.]+)', output)

    # go: "--- PASS: TestFoo (0.00s)"
    passing += re.findall(r'--- PASS:\s+(\w+)', output)

    # jest: "✓ should do something (5 ms)" or "✔ should do something"
    passing += re.findall(r'[✓✔]\s+(.+?)(?:\s+\(\d+)', output)

    # rust: "test xxx ... ok"
    passing += re.findall(r'test\s+(\S+)\s+\.\.\.\s+ok', output)

    return list(set(passing))
