"""Small, transparent lexical retrieval over the supplied business policy."""
from __future__ import annotations

import re


def policy_sections(text: str) -> list[dict[str, str]]:
    sections = []
    for index, block in enumerate(re.split(r"\n\s*\n", text.strip()), 1):
        match = re.match(r"\[(P\d+)\]\s*(.*)", block, flags=re.S)
        if match:
            sections.append({"id": match.group(1), "text": match.group(2).strip()})
        elif block:
            sections.append({"id": f"POL-{index}", "text": block.strip()})
    return sections


def retrieve_policy(text: str, query: str, limit: int = 4) -> list[dict[str, str]]:
    words = set(re.findall(r"[a-z]{3,}", query.lower()))
    scored = []
    for section in policy_sections(text):
        terms = set(re.findall(r"[a-z]{3,}", section["text"].lower()))
        score = len(words & terms)
        if section["id"] in {"P4", "P5"}:
            score += 1
        scored.append((score, section))
    scored.sort(key=lambda pair: -pair[0])
    return [section for _, section in scored[:limit]]
