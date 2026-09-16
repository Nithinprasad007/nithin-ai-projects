import os
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from langchain_core.language_models.chat_models import BaseChatModel

def get_chat_model(
    provider: str = "ollama",
    model_name: str = "qwen2.5:7b",
    base_url: str = "http://localhost:11434",
    temperature: float = 0.0
) -> BaseChatModel:
    """
    Returns an instantiated chat model with tool/function calling support.
    """
    if provider.lower() == "ollama":
        return ChatOllama(
            model=model_name,
            base_url=base_url,
            temperature=temperature
        )
    elif provider.lower() == "openai":
        return ChatOpenAI(
            model=model_name or "gpt-4o",
            api_key=os.getenv("OPENAI_API_KEY"),
            temperature=temperature
        )
    else:
        raise ValueError(f"Unsupported provider: {provider}")
