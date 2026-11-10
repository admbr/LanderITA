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
        extract_path: Path | bytes,
        residents: list[Resident],
        groups: list[Group],
        due_date: date,
    ) -> tuple[list[Invoice], dict[Resident, float]]:
        logger.info("build_invoices() chamada")
        values = fetch_residents_value(residents=residents, path_csv=extract_path)
        invoices: list[Invoice] = []
        # Não loga o dict inteiro: tem nome, CPF e e-mail de todos os moradores
        logger.info(
            f"Valores gerados: {sum(1 for v in values.values() if v > 0)} moradores "
            f"com dívida, total R$ {sum(v for v in values.values() if v == v):.2f}"
        )
        for group in groups:
            description = [
                f"Lavanderia {group.apartment or group.leader.name} -- {_date_br(due_date.isoformat())}\n"
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
                    external_reference=self.external_reference(group, due_date),
                )
            )
        return invoices, values

    @staticmethod
    def external_reference(group: Group, due_date: date) -> str:
        """Chave estável de uma cobrança em massa: grupo + vencimento."""
        chave = group.apartment or group.leader.cpf_cnpj
        return f"lavanderia-{due_date.isoformat()}-{chave.replace(' ', '_')}"

    def post_single_invoice(
        self,
        invoice: Invoice,
        responsible: Resident,
    ) -> dict:
        """Cria a cobrança no Asaas e devolve o pagamento criado.
        Erros são propagados para a página mostrar o motivo."""
        customer_id = self.client.get_or_create_customer(responsible)[0]
        return self.client.create_payment(customer_id=customer_id, invoice=invoice)

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
                if invoice.external_reference:
                    existente = self.client.find_payment_by_reference(
                        invoice.external_reference
                    )
                    if existente:
                        motivo = f"já existe a cobrança {existente['id']} ({invoice.external_reference})"
                        skipped.append(
                            BillingFailure(apartment=invoice.group.apartment, reason=motivo)
                        )
                        if on_progress:
                            on_progress(f"[pulado] {invoice.group.apartment}: {motivo}", True)
                        continue

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

    def delete_boletos_by_id(self, ids: list[str]) -> list[tuple[str, str]]:
        """Remove as cobranças e devolve as que falharam: [(id, motivo)]."""
        logger.info("entrando em delete_boleto_by_id")
        falhas = []
        for id in ids:
            deleted, info = self.client.del_boleto_by_id(id)
            if not deleted:
                falhas.append((id, info))
        return falhas
