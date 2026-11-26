"""Reserved local prompt profiles; no extraction or model calls."""
import re
import sys
from pathlib import Path


def get_prompt_profile_path(name):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
        raise ValueError('提示词配置名称无效')
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))
    return root / 'assets' / 'prompts' / (name + '.md')


def load_prompt_profile(name):
    return get_prompt_profile_path(name).read_text(encoding='utf-8')
