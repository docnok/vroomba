"""Application configuration via environment variables / .env file."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "VROOMBA_", "env_file": ".env"}

    # Serial / Arduino
    serial_port: str = "auto"
    serial_baud: int = 115200

    # LLM
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "gemma4:26b"
    llm_api_key: str = "ollama"
    llm_max_tokens: int = 256

    # Autopilot
    turn_timeout_seconds: float = 30.0
    turn_send_hz: int = 20

    # Server
    server_host: str = "127.0.0.1"
    server_port: int = 8420


settings = Settings()
