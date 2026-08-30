import re


def analyze(text: str) -> dict:
    words = re.findall(r"[A-Za-z0-9']+", text)
    sentences = re.split(r"[.!?]+", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    return {
        "words": len(words),
        "characters": len(text),
        "characters_no_spaces": len(text.replace(" ", "").replace("\n", "")),
        "sentences": len(sentences),
        "paragraphs": len(paragraphs),
        "reading_time_min": max(1, round(len(words) / 200)) if words else 0,
    }
