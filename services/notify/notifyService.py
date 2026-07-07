from jinja2 import Environment, FileSystemLoader, select_autoescape
from models.billing import *
from models.clients import *
from pathlib import Path
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from src.config import settings
from loguru import logger
from pydantic import BaseModel, Field, EmailStr
from collections.abc import Callable
from src.utils import get_group_by_cpf


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


def render_boleto_email(payment_result: PaymentResult, resident: Resident) -> str:
    template = _env.get_template("email_boleto.html")
    return template.render(
        apartment=payment_result.invoice.group.apartment,
        value=payment_result.invoice.value,
        due_date=str(payment_result.invoice.due_date),
        bank_slip_url=payment_result.bank_splip_url,
        invoice_url=str(payment_result.invoice_url),
        residents=[r.name for r in payment_result.invoice.group.residents],
    )


def render_boleto_email_some(
    resident: Resident,
    value: float,
    due_date: str,
    boleto_url: str,
    pix_url: str,
    residents: list[Resident],
) -> str:
    template = _env.get_template("email_boleto.html")
    return template.render(
        apartment=resident.apartment,
        value=value,
        due_date=due_date,
        bank_slip_url=boleto_url,
        invoice_url=pix_url,
        residents=[r.name for r in residents],
    )


def render_inadimplente_email(payment_result: PaymentResult, resident: Resident) -> str:
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
    email: EmailStr | None
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

    def send_one_sync(
        self,
        to: EmailStr | None,
        subject: str,
        html_body: str,
    ) -> None:

        logger.info("Entrou no send_one_sync")

        try:
            msg = MIMEMultipart()
            msg["From"] = f"{settings.sender_name} <{settings.gmail_user}>"
            msg["To"] = to  # type: ignore
            msg["Subject"] = subject
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            with smtplib.SMTP("smtp.gmail.com", 587) as server:
                server.ehlo()
                server.starttls()
                server.login(settings.gmail_user, settings.gmail_password)
                server.sendmail(settings.gmail_user, str(to), msg.as_string())
                logger.info(f"Email mandado para {to}")

        except Exception as e:
            logger.error(f"falha ao enviar o email: {e}")

    def notify_all_sync(
        self,
        payment_results: list[PaymentResult],
        on_progress: Callable[[str, bool], None] | None = None,
    ) -> EmailReport:

        logger.info("Entrou no notify_all_sync")

        report = EmailReport()

        for result in payment_results:
            for resident in result.invoice.group.residents:
                subject = f"[CASD] Boleto Lavanderia — {result.invoice.group.apartment}"
                html = render_boleto_email(payment_result=result, resident=resident)
                try:

                    logger.info("Entrou no send_one_sync")

                    with smtplib.SMTP("smtp.gmail.com", 587) as server:
                        msg = MIMEMultipart()
                        msg["From"] = f"{settings.sender_name} <{settings.gmail_user}>"
                        msg["To"] = resident.email  # type: ignore
                        msg["Subject"] = subject
                        msg.attach(MIMEText(html, "html", "utf-8"))

                        server.ehlo()
                        server.starttls()
                        server.login(settings.gmail_user, settings.gmail_password)
                        server.sendmail(
                            settings.gmail_user, str(resident.email), msg.as_string()
                        )

                        logger.info(f"Email mandado para {resident.email}")

                    report.sent += 1
                    logger.success(
                        f"V {result.invoice.group.apartment} ==> {resident.email}"
                    )
                    if on_progress:
                        on_progress(f"V {resident.email}", True)
                except Exception as e:
                    report.failed.append(
                        EmailFailure(
                            apartment=result.invoice.group.apartment,
                            email=resident.email,
                            reason=str(e),
                        )
                    )
                    logger.error(f"X {resident.email}: {e}")
                    if on_progress:
                        on_progress(f"X {resident.email}: {e}", False)

        return report

    def notify_some(
        self,
        boletos: list[dict[str, str]],
        groups: list[Group],
        on_progress: Callable[[str, bool], None] | None = None,
    ) -> EmailReport:

        logger.info("Entrou no notify_some")

        report = EmailReport()

        with smtplib.SMTP("smtp.gmail.com", 587) as server:

            try:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(settings.gmail_user, settings.gmail_password)

                logger.info("SMTP Authenticated successfully with Gmail.")

            except Exception as conn_err:

                logger.critical(
                    f"Failed to connect or log into SMTP server: {conn_err}"
                )

                return report

            for boleto in boletos:
                due_date = boleto["Vencimento"]
                value = boleto["value"]
                cpf_leader = boleto["CPF"]
                link_boleto = boleto["Boleto"]
                link_pix = boleto["PIX"]

                group = get_group_by_cpf(cpf=cpf_leader, groups=groups)

                if group is not None:
                    for resident in group.residents:
                        subject = f"[CASD] Boleto Lavanderia — {group.apartment}"
                        html = render_boleto_email_some(
                            resident=resident,
                            value=float(value),
                            due_date=due_date,
                            boleto_url=link_boleto,
                            pix_url=link_pix,
                            residents=group.residents,
                        )
                        try:

                            msg = MIMEMultipart()
                            msg["From"] = (
                                f"{settings.sender_name} <{settings.gmail_user}>"
                            )
                            msg["To"] = resident.email  # type: ignore
                            msg["Subject"] = subject
                            msg.attach(MIMEText(html, "html", "utf-8"))
                            server.sendmail(
                                settings.gmail_user,
                                str(resident.email),
                                msg.as_string(),
                            )

                            logger.info(f"Email mandado para {resident.email}")

                            report.sent += 1
                            logger.success(f"V {group.apartment} ==> {resident.email}")
                            if on_progress:
                                on_progress(f"V {resident.email}", True)
                        except Exception as e:
                            report.failed.append(
                                EmailFailure(
                                    apartment=group.apartment,
                                    email=resident.email,
                                    reason=str(e),
                                )
                            )
                            logger.error(f"X {resident.email}: {e}")
                            if on_progress:
                                on_progress(f"X {resident.email}: {e}", False)
        return report


if __name__ == "__main__":
    pass
