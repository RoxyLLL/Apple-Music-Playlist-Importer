"""
Matcher package.
"""

def __getattr__(name: str):
    if name == "TextCleaner":
        from applemusic.matcher.cleaner import TextCleaner
        return TextCleaner
    if name == "TrackScorer":
        from applemusic.matcher.scorer import TrackScorer
        return TrackScorer
    if name == "MatchingEngine":
        from applemusic.matcher.engine import MatchingEngine
        return MatchingEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["TextCleaner", "TrackScorer", "MatchingEngine"]
