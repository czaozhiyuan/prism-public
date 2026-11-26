"""One model identity grammar for models, projects and template IDs."""
import re


MODEL_TOKEN = r"(?!\.{1,2}(?:@|:|$))[A-Za-z0-9_.-]+"
MODEL_TOKEN_PATTERN = "^" + MODEL_TOKEN + r"(?![\s\S])"


def is_model_token(value):
    return isinstance(value, str) and re.fullmatch(MODEL_TOKEN_PATTERN, value) is not None
