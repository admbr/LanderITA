from jinja2 import Environment, FileSystemLoader, select_autoescape
from models.billing import *
from models.clients import *
from pathlib import Path
import asyncio
import aiosmtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from src.config import settings
from loguru import logger
from pydantic import BaseModel, Field, EmailStr
from collections.abc import Callable


def _brl(value: float) -> str:
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _date_br(value: str) -> str:
    # converter as datas pro formato correto
    y, m, d = value.split("-")
    return f"{d}/{m}/{y}"


_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent),
    autoescape=select_autoescape(["html"]),
)
_env.filters["brl"] = _brl
_env.filters["date_br"] = _date_br


def render_boleto_email(payment_result: PaymentResult) -> str:
    template = _env.get_template("email_boleto.html")
    return template.render(
        apartment=payment_result.invoice.group.apartment,
        value=payment_result.invoice.value,
        due_date=payment_result.invoice.due_date,
        bank_slip_url=payment_result.bank_splip_url,
        invoice_url=str(payment_result.invoice_url),
        residents=[r.name for r in payment_result.invoice.group.residents],
    )


def render_inadimplente_email(payment_result: PaymentResult) -> str:
    template = _env.get_template("email_inadimplente.html")
    return template.render(
        apartment=payment_result.invoice.group.apartment,
        value=payment_result.invoice.value,
        due_date=payment_result.invoice.due_date,
        pid=payment_result.payment_id,
        inv_url=payment_result.payment_id,
        slip_url=payment_result.bank_splip_url,
    )


class EmailFailure(BaseModel):
    apartment: str | None
    email: EmailStr
    reason: str


class EmailReport(BaseModel):
    sent: int = 0
    failed: list[EmailFailure] = Field(default_factory=list)

    @property
    def total(self) -> int:
        return self.sent + len(self.failed)

    @property
    def sucess_rate(self) -> float:
        return self.sent / self.total if self.total > 0 else 0.0


class NotifyService:
    async def _send_one(
        self,
        to: EmailStr,
        subject: str,
        html_body: str,
    ) -> None:
        msg = MIMEMultipart()
        msg["From"] = f"{settings.sender_name} <{settings.gmail_user}>"
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        await aiosmtplib.send(
            msg,
            hostname="smtp.gmail.com",
            port=465,
            use_tls=True,
            username=settings.gmail_user,
            password=settings.gmail_password,
        )

    async def _notify_all_async(
        self,
        payment_results: list[PaymentResult],
        on_progress: Callable[[str, bool], None] | None = None,
    ) -> EmailReport:
        report = EmailReport()

        semaphore = asyncio.Semaphore(settings.aiosmtp_max_connections)

        async def send_with_limits(
            result: PaymentResult,
            email: EmailStr,
        ) -> None:
            async with semaphore:
                subject = f"[CASD] Boleto Lavanderia — {result.invoice.group.apartment}"
                html = render_boleto_email(result)
                try:
                    await self._send_one(to=email, subject=subject, html_body=html)
                    report.sent += 1
                    logger.success(f"V {result.invoice.group.apartment} ==> {email}")
                    if on_progress:
                        on_progress(f"V {email}", True)
                except Exception as e:
                    report.failed.append(
                        EmailFailure(
                            apartment=result.invoice.group.apartment,
                            email=email,
                            reason=str(e),
                        )
                    )
                    logger.error(f"X {email}: {e}")
                    if on_progress:
                        on_progress(f"X {email}: {e}", False)

        tasks = [
            send_with_limits(result=result, email=email)
            for result in payment_results
            for email in result.emails_sent
        ]

        await asyncio.gather(*tasks)
        return report

    def notify_all(
        self,
        payment_results: list[PaymentResult],
        on_progress: Callable[[str, bool], None] | None = None,
    ) -> EmailReport:
        # Precisa aparentar ser sincrono essa parte pq é o q o frontend vai chamar
        return asyncio.run(
            self._notify_all_async(
                payment_results=payment_results, on_progress=on_progress
            )
        )


if __name__ == "__main__":
    pass
