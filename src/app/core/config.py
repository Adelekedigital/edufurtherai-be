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


class ServiceCaller(BaseModel):
    subject: str = Field(min_length=1, max_length=128)
    issuer: str = Field(min_length=1, max_length=256)
    audience: str = Field(min_length=1, max_length=256)
    keys: dict[str, str] = Field(default_factory=dict)
    scopes: set[str] = Field(default_factory=set)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    allow_unauthenticated_development: bool = False
    service_name: str = "edufurther-ai-router"
    service_jwt_algorithm: str = "RS256"
    service_jwt_required_scope: str = ""
    service_callers: dict[str, ServiceCaller] = Field(default_factory=dict)
    primary_model: str = ""
    fallback_model: str = ""
    task_models: dict[str, str] = Field(default_factory=dict)
    provider_keys: dict[str, str] = Field(default_factory=dict)
    routing_policy: dict[str, TaskRoutingPolicy] = Field(default_factory=dict)
    model_policy_version: str = "ai-policy-v1"
    daily_budget_usd: float = 25.0
    product_task_budgets: dict[str, float] = Field(default_factory=dict)
    product_task_rate_limits: dict[str, int] = Field(default_factory=dict)
    max_source_bytes: int = 16_384
    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    database_url: str = ""

    def budget_for(self, product_id: str, task: str) -> float:
        return self.product_task_budgets.get(f"{product_id}:{task}", self.daily_budget_usd)

    def rate_limit_for(self, product_id: str, task: str) -> int:
        return self.product_task_rate_limits.get(f"{product_id}:{task}", 0)


settings = Settings()
