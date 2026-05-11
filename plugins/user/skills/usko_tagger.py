# plugins/user/skills/usko_tagger.py

def tag(text: str = "", query: str = "", **kwargs) -> str:
    """Appends USKO to any text."""
    content = text or query or ""
    return f"{content} — USKO"