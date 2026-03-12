import asyncio
from typing import Sequence

from src.config import settings


class AIUnavailableError(Exception):
    pass


class ModelClient:
    def __init__(self):
        self.provider = settings.ai_provider.lower().strip()
        self.model = ""
        self.client = None

        if self.provider == "groq":
            from groq import Groq

            self.client = Groq(api_key=settings.groq_api_key)
            self.model = "llama-3.1-70b-versatile"
        elif self.provider == "gemini":
            import google.generativeai as genai

            genai.configure(api_key=settings.gemini_api_key)
            self.client = genai.GenerativeModel("gemini-1.5-flash")
            self.model = "gemini-1.5-flash"
        elif self.provider == "openai":
            from openai import OpenAI

            self.client = OpenAI(api_key=settings.openai_api_key)
            self.model = "gpt-4o-mini"
        else:
            raise ValueError(f"Unknown AI_PROVIDER: {self.provider}")

    @staticmethod
    def is_provider_configured() -> bool:
        provider = settings.ai_provider.lower().strip()
        if provider == "groq":
            return bool(settings.groq_api_key)
        if provider == "gemini":
            return bool(settings.gemini_api_key)
        if provider == "openai":
            return bool(settings.openai_api_key)
        return False

    async def generate(self, messages: Sequence[dict], max_tokens: int = 1000) -> str:
        try:
            if self.provider == "groq":
                return await asyncio.to_thread(self._generate_groq, messages, max_tokens)
            if self.provider == "gemini":
                return await asyncio.to_thread(self._generate_gemini, messages)
            if self.provider == "openai":
                return await asyncio.to_thread(self._generate_openai, messages, max_tokens)
            raise AIUnavailableError(f"Unsupported provider: {self.provider}")
        except Exception as exc:
            raise AIUnavailableError(str(exc)) from exc

    async def ping(self) -> bool:
        if not self.is_provider_configured():
            return False
        try:
            msg = [{"role": "user", "content": "Reply with PONG"}]
            out = await self.generate(msg, max_tokens=16)
            return "PONG" in out.upper()
        except Exception:
            return False

    def _generate_groq(self, messages: Sequence[dict], max_tokens: int) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=list(messages),
            max_tokens=max_tokens,
            temperature=0.1,
        )
        return response.choices[0].message.content or ""

    def _generate_gemini(self, messages: Sequence[dict]) -> str:
        formatted = []
        for m in messages:
            role = m.get("role", "user").upper()
            formatted.append(f"{role}: {m.get('content', '')}")
        response = self.client.generate_content("\n\n".join(formatted))
        return response.text or ""

    def _generate_openai(self, messages: Sequence[dict], max_tokens: int) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=list(messages),
            max_tokens=max_tokens,
            temperature=0.1,
        )
        return response.choices[0].message.content or ""
