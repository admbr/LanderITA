from pydantic import AnyHttpUrl, field_validator, model_validator, EmailStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ASAAS
    asaas_access_token: str
    asaas_api_base: str

    # SHEETS
    google_application_credentials: Path
    sheet_vagas_url: str
    sheet_sirleyde_url: str
    scope_spreadsheets: str
    scope_drive: str
    planilha_url: str

    # GMAIL
    gmail_user: EmailStr
    gmail_password: str
    sender_name: str
    aiosmtp_max_connections: int = 3

    @field_validator("asaas_access_token")
    @classmethod
    def token_nao_pode_ser_vazio(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("ASAAS_ACCESS_TOKEN não pode ser vazio")
        return v.strip()

    @field_validator(
        "asaas_access_token", "sheet_sirleyde_url", "sheet_vagas_url", mode="before"
    )
    @classmethod
    def api_base_nao_pode_ser_vazio(cls, v: AnyHttpUrl) -> str:
        if not str(v).strip():
            raise ValueError("ASAAS_API_BASE não pode ser vazio")
        return str(v).strip()


settings = Settings()  # type: ignore
