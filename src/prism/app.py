"""Shared desktop appearance and resource discovery."""

import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple


COLORS = {
    "nav": "#17243B",
    "nav_hover": "#223451",
    "nav_active": "#2E63D4",
    "canvas": "#F4F6FA",
    "surface": "#FFFFFF",
    "border": "#DDE3EC",
    "text": "#1D2939",
    "muted": "#667085",
    "soft": "#EEF3FB",
    "primary": "#2E63D4",
    "primary_dark": "#2453B7",
    "success": "#178A62",
    "warning": "#B7791F",
    "danger": "#C2414B",
}


FONT_FAMILY = "Microsoft YaHei UI"


def find_project_root(start: Optional[Path] = None) -> Path:
    """Return the repository root when running from source or a package."""
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    current = (start or Path(__file__)).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "assets").is_dir() and (candidate / "src").is_dir():
            return candidate
    return Path.cwd()


def discover_templates(project_root: Path) -> List[Tuple[str, str, str]]:
    """Discover the imported DOCX and XLSX templates used by the UI."""
    rows: List[Tuple[str, str, str]] = []
    docx_root = project_root / "assets" / "templates" / "docx"
    workbook_root = project_root / "assets" / "reference" / "workbooks"

    if docx_root.is_dir():
        for path in sorted(docx_root.glob("*.docx"), key=lambda item: item.name):
            rows.append(("DOCX", infer_stage(path.name), path.name))
    if workbook_root.is_dir():
        for path in sorted(workbook_root.glob("*.xlsx"), key=lambda item: item.name):
            if "键值表" not in path.name:
                rows.append(("XLSX", "项目管理", path.name))
    return rows


def infer_stage(filename: str) -> str:
    """Infer a concise lifecycle stage for display purposes."""
    rules: Sequence[Tuple[Iterable[str], str]] = (
        (("任务书",), "项目启动"),
        (("需求", "开发计划", "质量保证计划", "配置管理计划"), "需求与策划"),
        (("设计",), "软件设计"),
        (("单元测试", "仿真测试"), "开发与测试"),
        (("系统软件测试",), "软件测试"),
        (("版本说明", "保障手册", "管理报告", "保证报告", "总结报告"), "交付与验收"),
    )
    for keywords, stage in rules:
        if any(keyword in filename for keyword in keywords):
            return stage
    return "其他"


def main():
    from .functional_app import main as run_desktop
    return run_desktop()


if __name__ == "__main__":
    main()
