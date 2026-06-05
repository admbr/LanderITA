from pydantic import BaseModel, AnyHttpUrl, EmailStr, Field
from datetime import date
from enum import Enum
from models.clients import *


class BillingType(str, Enum):
    BOLETO = "BOLETO"
    PIX = "PIX"


class Invoice(BaseModel):
    group: Group
    value: float
    due_date: date
    description: str
    billing_type: BillingType


class PaymentResult(BaseModel):
    invoice: Invoice
    payment_id: str
    invoice_url: str
    bank_splip_url: str | None
    emails_sent: list[EmailStr] = []


class BillingFailure(BaseModel):
    apartment: str | None
    reason: str


class RunReport(BaseModel):
    success: list[PaymentResult | Invoice] = Field(default_factory=list)
    failed: list[BillingFailure] = Field(default_factory=list)
    skipped: list[BillingFailure] = Field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.success) + len(self.failed) + len(self.skipped)

    @property
    def success_rate(self) -> float:
        return len(self.success) / self.total

    @property
    def total_charged(self) -> float:
        return sum(
            r.invoice.value if isinstance(r, PaymentResult) else r.value
            for r in self.success
        )
