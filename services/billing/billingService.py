from pathlib import Path
from typing import Callable
from models.billing import (
    Invoice,
    BillingType,
    RunReport,
    PaymentResult,
    BillingFailure,
)
from models.clients import Group, Resident
from src.asaas import AsaasClient
from src.sheets import fetch_groups, fetch_residents_value
from datetime import date
from services.notify.notifyService import _date_br
from pydantic import BaseModel, ConfigDict
from loguru import logger
import math
from time import sleep


class BillingService(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    client: AsaasClient

    def build_invoices(
        self,
        extract_path: Path,
        residents: list[Resident],
        groups: list[Group],
        due_date: date,
    ) -> list[Invoice]:
        logger.info("build_invoices() chamada")
        values = fetch_residents_value(residents=residents, path_csv=extract_path)
        invoices: list[Invoice] = []
        logger.info(f"Valores gerados: {values}")
        for group in groups:
            print(f"\n")
            logger.info(
                f"Procurando divida do apartamento: {group.apartment} (lider: {group.leader.name})"
            )
            value: float = 0.0
            for resident in group.residents:
                divida = values.get(resident, 0.0)
                if math.isnan(divida):
                    divida = 0.0
                logger.info(f"Residente {resident.name} tem divida de a {divida}")
                value += divida
            if value == 0.0:
                continue
            invoices.append(
                Invoice(
                    group=group,
                    value=value,
                    due_date=due_date,
                    description=f"Lavanderia {group.apartment} -- {_date_br(due_date.isoformat())}",
                    billing_type=BillingType.BOLETO,
                )
            )
        return invoices

    def post_invoices(
        self,
        invoices: list[Invoice],
        dry_run: bool = False,
        on_progress: Callable[[str, bool], None] | None = None,
    ) -> RunReport:
        logger.info("post_invoices() chamada")
        ok: list[PaymentResult | Invoice] = []
        failed: list[BillingFailure] = []
        skipped: list[BillingFailure] = []
        for invoice in invoices:
            if dry_run:
                ok.append(
                    PaymentResult(
                        invoice=invoice,
                        payment_id="oiiii",
                        invoice_url="https://httpbin.org/",
                        bank_splip_url="https://httpbin.org/",
                    )
                )
                if on_progress:
                    on_progress(f"[dry_run] {invoice.group.apartment}", True)
                continue
            try:
                custumer_id = self.client.get_or_create_customer(invoice.group.leader)[
                    0
                ]
                payment = self.client.create_payment(
                    customer_id=custumer_id, invoice=invoice
                )
                ok.append(
                    PaymentResult(
                        invoice=invoice,
                        payment_id=payment["id"],
                        invoice_url=payment["invoiceUrl"],
                        bank_splip_url=payment.get("bankSlipUrl"),
                    )
                )
                if on_progress:
                    on_progress(f"V {invoice.group.apartment}", True)
            except Exception as e:
                failed.append(
                    BillingFailure(apartment=invoice.group.apartment, reason=str(e))
                )
                if on_progress:
                    on_progress(f"X {invoice.group.apartment}: {e}", False)
        return RunReport(success=ok, failed=failed, skipped=skipped)
