from pydantic import BaseModel, EmailStr, field_validator, ConfigDict, Field
import re

class Resident(BaseModel):
  model_config = ConfigDict(frozen=True)
  name: str 
  cpf_cnpj: str
  email: EmailStr | None = None 
  phone: str | None = None
  apartment: str | None = None
  turma: str| None = None

  @field_validator("apartment", mode="before")
  @classmethod
  def strip(cls, value: str) -> str | None:
    ap = value.strip()
    if ap == '':
      return None
    return ap

  @field_validator("cpf_cnpj", mode="before")
  @classmethod
  def strip_non_digits(cls, value: str) -> str:
    number = list(re.sub(r"\D", "", value))
    while len(number) < 11:
      number.insert(0, '0')
    return "".join(number)
  
  @field_validator("phone", mode="before")
  @classmethod
  def normlize_br_phone(cls, value: str) -> str:
    number = list(re.sub(r"\D", "", value))
    while len(number)>11 or number[0] == '0':
      number.pop(0)
    return "".join(number)

class Group(BaseModel):
  apartment: str | None
  leader: Resident 
  residents: list[Resident]

  @property
  def emails(self) -> list[tuple[Resident, EmailStr | None]]:
    return [(resident, resident.email) for resident in self.residents]

class ClientFailure(BaseModel):
  apartment: str | None
  reason: str

class UploadReport(BaseModel):
    created:  int = 0
    existing: int = 0
    failed:   list[ClientFailure] = Field(default_factory=list)

    @property
    def total(self) -> int:
        return self.created + self.existing + len(self.failed)
