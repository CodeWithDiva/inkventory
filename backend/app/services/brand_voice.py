"""Learn a brand's writing voice from its existing product descriptions."""

import re

from pydantic import BaseModel, Field

from app.services.llm import GenerationResult, LLMProvider
from app.services.prompts import LENGTHS, TONES


class VoiceProfile(BaseModel):
    summary: str = Field(description="2-3 sentences describing the brand's voice")
    closest_tone: str = Field(description=f"One of: {', '.join(TONES)}")
    typical_length: str = Field(description="short, medium or long")
    instructions: str = Field(description="Concrete writing rules (6-12 bullet lines starting with '- ') that "
                                          "reproduce this voice: vocabulary, sentence style, point of view, "
                                          "formatting habits, words the brand favours or avoids")

    @classmethod
    def mock_example(cls, user_prompt: str) -> "VoiceProfile":
        samples = re.findall(r"<sample>(.*?)</sample>", user_prompt, re.S)
        words = [len(s.split()) for s in samples] or [0]
        avg = sum(words) // len(words)
        uses_you = any(re.search(r"\byou(r)?\b", s, re.I) for s in samples)
        return cls(
            summary=f"Learned from {len(samples)} samples. Clear and product-focused writing.",
            closest_tone="friendly" if uses_you else "professional",
            typical_length="short" if avg < 90 else "medium" if avg < 220 else "long",
            instructions="- Address the reader as 'you'.\n- Keep sentences short." if uses_you
            else "- Use a neutral, informative third-person voice.\n- Keep sentences short.",
        )


SYSTEM = (
    "You are a senior brand copy editor. You analyse sample product descriptions from one brand and "
    "write a precise style guide that lets another writer reproduce the same voice for new products. "
    "Describe HOW they write (voice, rhythm, vocabulary, structure), never WHAT the sample products are."
)


def learn(samples: list[str], provider: LLMProvider) -> GenerationResult[VoiceProfile]:
    joined = "\n".join(f"<sample>\n{s.strip()[:3000]}\n</sample>" for s in samples[:20] if s.strip())
    result = provider.generate(SYSTEM, f"Analyse these product descriptions from one brand:\n\n{joined}",
                               output_model=VoiceProfile)
    profile = result.content
    if profile.closest_tone not in TONES:
        profile.closest_tone = "professional"
    if profile.typical_length not in LENGTHS:
        profile.typical_length = "medium"
    return result
