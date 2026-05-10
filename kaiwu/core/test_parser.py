"""
测试输出解析器：从pytest/go/jest输出提取失败和通过的测试名。
纯正则匹配，零LLM调用。供GapDetector、ExecutionStateTracker、Orchestrator共用。
"""

import re

__all__ = ["extract_failing_tests", "extract_passing_tests", "parse_test_failures", "generate_diagnosis"]


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


def extract_fault_functions(test_output: str, source_files: list[str] = None) -> list[dict]:
    """
    从pytest stack trace中确定性提取故障函数（文件+函数名+行号）。

    pytest --tb=short 格式:
        scheduler.py:47: in complete_task
            self.tasks[task_id].status = TaskStatus.COMPLETED

    返回: [{"file": "scheduler.py", "function": "complete_task", "line": 47}]
    去重，按出现频率排序（出现越多=越可能是bug所在）。
    """
    if not test_output:
        return []

    import os

    # 标准化source_files为basename集合
    source_basenames = set()
    if source_files:
        source_basenames = {os.path.basename(f) for f in source_files}

    # 从stack trace中提取所有 "file.py:line: in function" 帧
    # pytest --tb=short格式: "file.py:42: in func_name"
    pattern = r'([\w/\\._-]+\.py):(\d+):\s+in\s+(\w+)'
    all_frames = re.findall(pattern, test_output)

    # 过滤：只保留源文件帧（排除test文件和标准库）
    fault_counts = {}  # (file, function, line) -> count
    for fpath, line_str, func_name in all_frames:
        basename = os.path.basename(fpath)
        # 排除test文件
        if 'test' in basename.lower():
            continue
        # 排除标准库/site-packages
        if '/lib/python' in fpath or '/site-packages/' in fpath:
            continue
        # 如果指定了source_files，只保留匹配的
        if source_basenames and basename not in source_basenames:
            continue
        # 排除常见框架函数
        if func_name in ('__init__', '__str__', '__repr__', '<module>'):
            continue

        key = (basename, func_name, int(line_str))
        fault_counts[key] = fault_counts.get(key, 0) + 1

    # 按出现频率排序（出现越多=越可能是bug所在）
    sorted_faults = sorted(fault_counts.items(), key=lambda x: -x[1])

    results = []
    seen_funcs = set()
    for (file, function, line), count in sorted_faults:
        # 去重：同一个函数只保留一次（取出现最多的行号）
        if (file, function) in seen_funcs:
            continue
        seen_funcs.add((file, function))
        results.append({
            "file": file,
            "function": function,
            "line": line,
            "count": count,
        })

    # 补充：从测试断言中提取被测函数（ROOT CAUSE优先于crash location）
    test_called_funcs = set()
    lines = test_output.split("\n")
    for i, line in enumerate(lines):
        if 'test' in line and '.py:' in line and ': in ' in line:
            if i + 1 < len(lines):
                code_line = lines[i + 1].strip()
                calls = re.findall(r'(\w+)\s*\(', code_line)
                skip_funcs = {'assert', 'len', 'abs', 'isinstance', 'hasattr', 'print',
                             'str', 'int', 'float', 'list', 'dict', 'set', 'tuple',
                             'range', 'enumerate', 'zip', 'sorted', 'sum', 'min', 'max'}
                for c in calls:
                    if c not in skip_funcs and not c.startswith('test_'):
                        test_called_funcs.add(c)

    # 将test_called_funcs中的函数提升到结果最前面（ROOT CAUSE优先）
    if test_called_funcs and results:
        root_cause = [r for r in results if r["function"] in test_called_funcs]
        others = [r for r in results if r["function"] not in test_called_funcs]
        results = root_cause + others

    return results[:10]  # 最多10个


def attribute_failures_to_files(test_output: str, source_files: list[str]) -> dict[str, list[str]]:
    """
    失败测试归因：从pytest traceback中判断每个失败测试属于哪个源文件。

    原理：pytest --tb=short 的traceback格式：
        _____ TestFoo.test_bar _____
        test_foo.py:42: in test_bar
            result = my_module.do_something()
        my_module.py:15: in do_something    ← 源文件帧
            return self._helper()
        E   AssertionError: assert 5 == -5

    从traceback中找到source_files中的文件帧，按源文件分组。

    Args:
        test_output: pytest完整输出（含FAILURES块）
        source_files: 源文件列表（不含test文件），如 ['scheduler.py', 'worker.py']

    Returns:
        {source_file: [该文件相关的失败测试snippet列表]}
        未归因的测试放在 '__unattributed__' key下
    """
    if not test_output or not source_files:
        return {}

    # 标准化source_files为basename（去掉路径前缀）
    import os
    basenames = {os.path.basename(f): f for f in source_files}

    result = {f: [] for f in source_files}
    result['__unattributed__'] = []

    # 按FAILURES section分割每个测试块
    blocks = re.split(r'_{3,}\s+([\w:.]+)\s+_{3,}', test_output)

    i = 1
    while i < len(blocks) - 1:
        test_name = blocks[i].strip()
        block = blocks[i + 1] if i + 1 < len(blocks) else ""
        i += 2

        # 构建该测试的snippet（测试名 + E行）
        e_lines = re.findall(r'^E\s+(.+)$', block, re.MULTILINE)
        snippet = f"FAILED {test_name}\n" + "\n".join(e_lines[:5])

        # 从traceback中找源文件帧
        # 格式: "filename.py:line: in function_name"
        frames = re.findall(r'([\w/\\._-]+\.py):(\d+):\s+in\s+(\w+)', block)

        attributed = False
        for frame_file, frame_line, frame_func in reversed(frames):
            # 从后往前找（最接近错误的帧优先）
            frame_basename = os.path.basename(frame_file)
            if frame_basename in basenames and 'test' not in frame_basename.lower():
                target = basenames[frame_basename]
                result[target].append(snippet)
                attributed = True
                break

        if not attributed:
            # 没有在traceback中找到源文件帧，尝试从E行中找文件引用
            for basename, full_path in basenames.items():
                if basename in block and 'test' not in basename.lower():
                    result[full_path].append(snippet)
                    attributed = True
                    break

        if not attributed:
            result['__unattributed__'].append(snippet)

    # 如果block分割没找到任何结果，用FAILED行做简单归因
    if all(len(v) == 0 for v in result.values()):
        for match in re.finditer(r'FAILED\s+([\w/:.]+)\s*-\s*(.+)', test_output):
            test_name = match.group(1)
            reason = match.group(2).strip()
            snippet = f"FAILED {test_name} - {reason}"
            # 尝试从test_name或reason中找源文件线索
            attributed = False
            for basename, full_path in basenames.items():
                module_name = basename.replace('.py', '')
                if module_name in test_name.lower() or module_name in reason.lower():
                    result[full_path].append(snippet)
                    attributed = True
                    break
            if not attributed:
                result['__unattributed__'].append(snippet)

    # 清理空列表
    return {k: v for k, v in result.items() if v}


def generate_diagnosis(structured_failures: list[dict]) -> str:
    """
    把结构化失败信息转成LLM最容易理解的诊断句。
    工程做，不走LLM，确定性。

    输入: parse_test_failures() 的返回值
    输出: 多行诊断文本，每行一个失败，格式清晰直接

    设计原则：
    - 32B模型看原始pytest输出效果差，但看"函数X应该返回Y，实际返回Z"能直接修
    - 按错误类型分类，给出不同格式的诊断
    - 如果能从stack trace提取故障函数，直接指出
    """
    if not structured_failures:
        return ""

    lines = []
    for f in structured_failures[:5]:
        name = f.get('test_name', '')
        expected = f.get('expected', '')
        actual = f.get('actual', '')
        error_type = f.get('error_type', '')
        snippet = f.get('snippet', '')
        file = f.get('file', '')
        line_no = f.get('line', 0)

        # 定位信息前缀
        loc = f" ({file}:{line_no})" if file and line_no else ""

        if error_type == 'AssertionError' and expected and actual:
            # 最精确的情况：知道期望和实际，尝试推断根因
            root_cause = _infer_root_cause(expected, actual, name)
            if root_cause:
                lines.append(
                    f"- {name}{loc}: 应该返回 {expected}，实际返回 {actual}（{root_cause}）"
                )
            else:
                lines.append(
                    f"- {name}{loc}: 应该返回 {expected}，实际返回 {actual}"
                )
        elif error_type == 'AttributeError':
            lines.append(
                f"- {name}{loc}: {snippet[:120]}（对象缺少该属性或方法）"
            )
        elif error_type == 'TypeError':
            lines.append(
                f"- {name}{loc}: {snippet[:120]}（类型不匹配）"
            )
        elif error_type == 'KeyError':
            lines.append(
                f"- {name}{loc}: {snippet[:120]}（字典缺少该key）"
            )
        elif error_type == 'ValueError':
            lines.append(
                f"- {name}{loc}: {snippet[:120]}（值不合法）"
            )
        elif error_type == 'NotImplementedError':
            lines.append(
                f"- {name}{loc}: 函数未实现（需要补充实现）"
            )
        elif error_type == 'IndexError':
            lines.append(
                f"- {name}{loc}: {snippet[:120]}（索引越界）"
            )
        elif error_type == 'ZeroDivisionError':
            lines.append(
                f"- {name}{loc}: 除零错误（需要处理除数为0的情况）"
            )
        elif expected and actual:
            # 有期望/实际但没有明确错误类型
            lines.append(
                f"- {name}{loc}: 期望 {expected}，实际 {actual}"
            )
        elif snippet:
            # 只有snippet，直接展示
            lines.append(f"- {name}{loc}: {snippet[:120]}")
        else:
            lines.append(f"- {name}: 测试失败（无详细信息）")

    return "\n".join(lines)


def _infer_root_cause(expected: str, actual: str, test_name: str) -> str:
    """
    从expected/actual的差异推断根因。确定性工程逻辑，不是写死答案。
    返回空字符串表示无法推断。
    """
    exp_lower = expected.lower()
    act_lower = actual.lower()
    name_lower = test_name.lower()

    # 转义字符相关：actual中有反斜杠但expected中没有，或反之
    if '\\' in actual and '\\' not in expected:
        return "转义字符未被正确处理，反斜杠应该触发转义逻辑"
    if '\\' in expected and '\\' not in actual:
        return "反斜杠被错误消耗，转义后的字面量丢失"

    # 引号相关：expected有引号但actual在引号处截断
    if ('"' in expected or "'" in expected) and len(actual) < len(expected):
        if 'escape' in name_lower or 'quote' in name_lower:
            return "引号转义未处理，遇到转义引号时应继续解析而非结束字符串"

    # 负数/符号相关：符号相反
    try:
        exp_num = float(expected)
        act_num = float(actual)
        if exp_num == -act_num:
            return "符号取反错误，检查负号/unary minus的处理逻辑"
        if exp_num < 0 and act_num > 0:
            return "负数未被正确处理，需要支持unary minus"
    except (ValueError, TypeError):
        pass

    # None vs 有值：函数返回了None
    if act_lower in ('none', 'null') and exp_lower not in ('none', 'null'):
        return "函数返回了None，可能缺少return语句或逻辑分支未覆盖"

    # 空列表/空字符串 vs 有内容
    if act_lower in ('[]', '""', "''", '{}') and exp_lower not in ('[]', '""', "''", '{}'):
        return "返回了空结果，核心逻辑可能未执行"

    # 截断：actual是expected的前缀
    if expected.startswith(actual) and len(actual) < len(expected):
        return "结果被截断，解析/处理提前终止了"

    return ""
