from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://mgmt:mgmt@db:5432/management"
    slack_bot_token: str = ""
    linear_api_key: str = ""
    fireflies_api_key: str = ""
    notion_api_key: str = ""
    slack_workspace_url: str = ""
    api_port: int = 8100
    log_level: str = "info"
    epics_police_html_path: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
