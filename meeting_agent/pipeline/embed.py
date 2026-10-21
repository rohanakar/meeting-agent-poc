from openai import OpenAI

from ..config import get_settings


def embed_texts(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url or None)
    response = client.embeddings.create(model=settings.embedding_model, input=texts)
    return [item.embedding for item in response.data]
