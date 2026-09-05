from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TaskRoutingPolicy(BaseModel):
    models: list[str] = Field(min_length=1, max_length=8)

    @field_validator("models")
    @classmethod
    def validate_models(cls, models: list[str]) -> list[str]:
        for model in models:
            if (
                not model.strip()
                or "/" not in model
                or model.startswith("/")
                or model.endswith("/")
            ):
                raise ValueError("routing models must use a non-empty provider/model ID")
        if len(set(models)) != len(models):
            raise ValueError("routing models must not contain duplicates")
        return models


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    service_name: str = "edufurther-ai-router"
    service_issuer: str = "edufurther-ai-router"
    service_audience: str = "edufurther-ai-router"
    service_jwt_public_key: str = ""
    service_jwt_algorithm: str = "RS256"
    primary_model: str = ""
    fallback_model: str = ""
    task_models: dict[str, str] = Field(default_factory=dict)
    provider_keys: dict[str, str] = Field(default_factory=dict)
    routing_policy: dict[str, TaskRoutingPolicy] = Field(default_factory=dict)
    model_policy_version: str = "ai-policy-v1"
    daily_budget_usd: float = 25.0
    max_source_bytes: int = 16_384
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    database_url: str = ""


settings = Settings()
