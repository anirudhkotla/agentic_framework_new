# plugins/community/skills/hi_anirudh.py

def say_hi(message: str = "", **kwargs) -> str:
    """Greets Anirudh. Returns a greeting message."""
    greeting = "Hi Anirudh!"
    if message:
        return f"{greeting} {message}"
    return greeting
