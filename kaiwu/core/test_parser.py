"""
测试输出解析器：从pytest/go/jest输出提取失败和通过的测试名。
纯正则匹配，零LLM调用。供GapDetector、ExecutionStateTracker、Orchestrator共用。
"""

import re

__all__ = ["extract_failing_tests", "extract_passing_tests", "parse_test_failures"]


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


def parse_test_failures(output: str) -> list[dict]:
    """
    从pytest --tb=short输出解析每个失败测试的结构化信息。
    返回: [{"test_name", "expected", "actual", "error_type", "file", "line", "snippet"}]
    """
    if not output:
        return []

    failures = []

    # 按pytest的FAILURES section分割每个测试块
    # 格式: _____ test_name _____\n file:line: in func\n ... \nE   assert ...
    blocks = re.split(r'_{3,}\s+([\w:.]+)\s+_{3,}', output)

    # blocks[0]是前缀，之后是 [test_name, block_content, test_name, block_content, ...]
    i = 1
    while i < len(blocks) - 1:
        test_name = blocks[i].strip()
        block = blocks[i + 1] if i + 1 < len(blocks) else ""
        i += 2

        failure = {
            "test_name": test_name,
            "expected": "",
            "actual": "",
            "error_type": "",
            "file": "",
            "line": 0,
            "snippet": "",
        }

        # 提取文件和行号: "file.py:42: in test_func"
        loc_match = re.search(r'([\w/\\._-]+\.py):(\d+):', block)
        if loc_match:
            failure["file"] = loc_match.group(1)
            failure["line"] = int(loc_match.group(2))

        # 提取E行（pytest的错误详情）
        e_lines = re.findall(r'^E\s+(.+)$', block, re.MULTILINE)
        if e_lines:
            failure["snippet"] = "\n".join(e_lines[:5])

        # 解析assert失败: "assert None == 3" / "assert False == True"
        assert_match = re.search(r'assert\s+(.+?)\s*==\s*(.+?)(?:\s*$|\s+where)', "\n".join(e_lines))
        if assert_match:
            failure["actual"] = assert_match.group(1).strip()
            failure["expected"] = assert_match.group(2).strip()
            failure["error_type"] = "AssertionError"
        # "assert X is True" / "assert X is not None"
        elif re.search(r'assert\s+', "\n".join(e_lines)):
            failure["error_type"] = "AssertionError"
            # 提取 "where X = func()"
            where_match = re.search(r'where\s+(.+?)\s*=\s*(.+?)(?:\s*$)', "\n".join(e_lines))
            if where_match:
                failure["actual"] = where_match.group(1).strip()

        # 异常类型: "TypeError: ..." / "AttributeError: ..."
        exc_match = re.search(r'(TypeError|AttributeError|ValueError|KeyError|IndexError|ZeroDivisionError|NotImplementedError):\s*(.+)', block)
        if exc_match:
            failure["error_type"] = exc_match.group(1)
            failure["snippet"] = exc_match.group(2).strip()[:200]

        # "where None = func()" 模式
        where_none = re.search(r'where None = (\w+)\(', block)
        if where_none and not failure["actual"]:
            failure["actual"] = "None"
            failure["error_type"] = failure["error_type"] or "AssertionError"

        failures.append(failure)

    # 如果上面的block分割没找到，用更宽松的模式
    if not failures:
        # 直接从 "FAILED xxx - reason" 行提取
        for match in re.finditer(r'FAILED\s+([\w/:.]+)\s*-\s*(.+)', output):
            test_name = match.group(1)
            reason = match.group(2).strip()
            failure = {
                "test_name": test_name,
                "expected": "",
                "actual": "",
                "error_type": "AssertionError",
                "file": "",
                "line": 0,
                "snippet": reason[:200],
            }
            # "assert None == 3"
            assert_m = re.search(r'assert\s+(.+?)\s*==\s*(.+)', reason)
            if assert_m:
                failure["actual"] = assert_m.group(1).strip()
                failure["expected"] = assert_m.group(2).strip()
            failures.append(failure)

    return failures[:10]  # 最多10个，避免prompt过长
