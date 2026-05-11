# plugins/user/skills/my_skill.py

def run(query: str, **kwargs) -> str:
    """Your skill logic here. Can be sync or async."""
    result = f"Processed: {query}"
    return result