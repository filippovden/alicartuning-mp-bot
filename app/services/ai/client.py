"""Обёртка над Anthropic API для генерации контента карточки товара (раздел 12 ТЗ)."""

from __future__ import annotations

from dataclasses import dataclass

from anthropic import AsyncAnthropic

from app.config import settings
from app.services.ai import prompts


@dataclass
class ProductDraft:
    category: str = ""
    draft_title: str = ""
    car_model: str = ""
    color: str = ""
    material: str = ""
    package_contents: str = ""


class AIContentGenerationError(Exception):
    pass


class AIContentService:
    """Генерирует SEO-название, описание, буллеты и ключевые слова в стиле бренда."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or settings.anthropic_api_key
        self.model = model or settings.anthropic_model
        self._client: AsyncAnthropic | None = None

    @property
    def client(self) -> AsyncAnthropic:
        if self._client is None:
            self._client = AsyncAnthropic(api_key=self.api_key)
        return self._client

    async def _complete(self, system: str, user_prompt: str, max_tokens: int = 1024) -> str:
        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except Exception as exc:  # анthropic-специфичные ошибки сети/квоты
            raise AIContentGenerationError(f"Ошибка обращения к AI: {exc}") from exc

        text_parts = [block.text for block in response.content if getattr(block, "type", None) == "text"]
        return "".join(text_parts).strip()

    def _system(self) -> str:
        return prompts.SYSTEM_PROMPT.format(brand=settings.brand_name)

    async def generate_title(self, draft: ProductDraft) -> str:
        prompt = prompts.TITLE_PROMPT.format(
            brand=settings.brand_name,
            category=draft.category,
            draft_title=draft.draft_title,
            car_model=draft.car_model,
            color=draft.color,
            material=draft.material,
        )
        return await self._complete(self._system(), prompt, max_tokens=200)

    async def generate_description(self, title: str, draft: ProductDraft) -> str:
        prompt = prompts.DESCRIPTION_PROMPT.format(
            brand=settings.brand_name,
            title=title,
            material=draft.material,
            color=draft.color,
            car_model=draft.car_model,
            package_contents=draft.package_contents,
        )
        text = await self._complete(self._system(), prompt, max_tokens=1500)
        prefix = "Описание товара:"
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
        return text

    async def generate_bullets(self, title: str, draft: ProductDraft) -> list[str]:
        prompt = prompts.BULLETS_PROMPT.format(
            brand=settings.brand_name,
            title=title,
            material=draft.material,
            car_model=draft.car_model,
            package_contents=draft.package_contents,
        )
        text = await self._complete(self._system(), prompt, max_tokens=300)
        return [line.strip("-• ").strip() for line in text.splitlines() if line.strip()]

    async def generate_keywords(self, title: str, description: str) -> list[str]:
        prompt = prompts.KEYWORDS_PROMPT.format(title=title, description=description)
        text = await self._complete(self._system(), prompt, max_tokens=150)
        return [kw.strip().strip('"') for kw in text.split(",") if kw.strip()]

    async def generate_full_content(self, draft: ProductDraft) -> dict[str, str | list[str]]:
        title = await self.generate_title(draft)
        description = await self.generate_description(title, draft)
        bullets = await self.generate_bullets(title, draft)
        keywords = await self.generate_keywords(title, description)
        return {
            "title": title,
            "description": description,
            "bullets": bullets,
            "keywords": keywords,
        }
