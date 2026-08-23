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
from src.utils import fetch_groups, fetch_residents_value
from datetime import date
from services.notify.notifyService import _date_br
from pydantic import BaseModel, ConfigDict
from loguru import logger
import math


class BillingService(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    client: AsaasClient

    def build_single_invoice(
        self,
        group: Group,
        value: float,
        due_date: date,
        description: str,
    ) -> Invoice:
        return Invoice(
            group=group,
            value=value,
            due_date=due_date,
            description=description,
            billing_type=BillingType.BOLETO,
        )

    def build_invoices(
        self,
        extract_path: Path,
        residents: list[Resident],
        groups: list[Group],
        due_date: date,
    ) -> tuple[list[Invoice], dict[Resident, float]]:
        logger.info("build_invoices() chamada")
        values = fetch_residents_value(residents=residents, path_csv=extract_path)
        invoices: list[Invoice] = []
        logger.info(f"Valores gerados: {values}")
        for group in groups:
            description = [
                f"Lavanderia {group.apartment} -- {_date_br(due_date.isoformat())}\n"
            ]
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
                if divida > 0.0:
                    description.append(
                        f"Residente {resident.name} tem divida de a {divida}; "
                    )
                value += divida
            if value == 0.0:
                continue
            invoices.append(
                Invoice(
                    group=group,
                    value=value,
                    due_date=due_date,
                    description=" ".join(description),
                    billing_type=BillingType.BOLETO,
                )
            )
        return invoices, values

    def post_single_invoice(
        self,
        invoice: Invoice,
        responsible: Resident,
    ) -> bool:
        try:
            customer_id = self.client.get_or_create_customer(responsible)[0]
            payment = self.client.create_payment(
                customer_id=customer_id, invoice=invoice
            )
            return True
        except Exception as e:
            logger.info(f"Erro ao gerar single invoice - {e}")
            return False

    def post_invoices(
        self,
        invoices: list[Invoice],
        values: dict[Resident, float],
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
                resident_list = [resident for resident in invoice.group.residents]
                length = len(resident_list)
                index = 0
                for k in range(length):
                    if values[resident_list[index]] <= values[resident_list[k]]:
                        index = k
                customer_id = self.client.get_or_create_customer(resident_list[index])[
                    0
                ]
                payment = self.client.create_payment(
                    customer_id=customer_id, invoice=invoice
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
                    on_progress(f"{invoice.group.apartment}", True)
            except Exception as e:
                failed.append(
                    BillingFailure(apartment=invoice.group.apartment, reason=str(e))
                )
                if on_progress:
                    on_progress(f"{invoice.group.apartment}: {e}", False)
        return RunReport(success=ok, failed=failed, skipped=skipped)

    def fetch_boletos_from_asaas(self) -> list[tuple[str, ...]]:
        logger.info("entrando em fetch_boletos_from_asaas")
        return self.client.list_all_payments()

    def delete_boletos_by_id(self, ids: list[str]) -> None:
        logger.info("entrando em delete_boleto_by_id")
        for id in ids:
            self.client.del_boleto_by_id(id)
