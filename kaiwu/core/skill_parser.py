"""
SkillParser: 解析SKILL.md里的模板块。
模板格式：```template:name\n...内容...\n```
使用string.Template变量风格（${variable}）。
"""

import os
import re
import glob as glob_mod
from string import Template

__all__ = ["SkillParser"]


class SkillParser:
    """从SKILL.md文件中提取代码模板。"""

    _PATTERN = re.compile(r'```template:(\w+)\n(.*?)```', re.DOTALL)

    def parse_templates(self, skill_md_content: str) -> dict[str, Template]:
        """
        从SKILL.md内容里提取所有```template:name...```块。
        返回 {模板名: Template对象}
        """
        templates = {}
        for match in self._PATTERN.finditer(skill_md_content):
            name = match.group(1)
            content = match.group(2)
            templates[name] = Template(content)
        return templates

    def load_all_templates(self, builtin_dir: str,
                           project_dir: str = ".") -> dict[str, Template]:
        """
        加载所有SKILL.md里的模板：
        1. builtin_experts/*/SKILL.md（内置）
        2. .kaiwu/skills/*/SKILL.md（飞轮自动提炼）
        3. SKILL.md（项目根目录）
        后加载的同名模板覆盖先加载的（项目级优先级最高）。
        """
        all_templates = {}

        paths = (
            glob_mod.glob(os.path.join(builtin_dir, "**", "SKILL.md"), recursive=True) +
            glob_mod.glob(os.path.join(project_dir, ".kaiwu", "skills", "**", "SKILL.md"), recursive=True) +
            [os.path.join(project_dir, "SKILL.md")]
        )
        for path in paths:
            if os.path.exists(path):
                try:
                    with open(path, encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    all_templates.update(self.parse_templates(content))
                except Exception:
                    pass  # 单文件读取失败不阻塞

        return all_templates
