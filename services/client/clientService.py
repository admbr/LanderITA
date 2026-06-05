from models.clients import Group, UploadReport, ClientFailure, Resident
from loguru import logger
from collections.abc import Callable
from src.asaas import AsaasClient
from pydantic import BaseModel, ConfigDict


class ClientService(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    client: AsaasClient

    def sync_to_asaas(
        self,
        groups: list[Group],
        dry_run: bool = False,
        on_progress: Callable[[str, bool], None] | None = None,
    ) -> UploadReport:
        report = UploadReport()
        for group in groups:
            if dry_run:
                report.created += 1
                if on_progress:
                    on_progress(f"[dry-run] {group.apartment}", True)
                continue
            try:
                customer_id, existed = self.client.get_or_create_customer(
                    resident=group.leader
                )
                if existed == "existe":
                    report.existing += 1
                else:
                    report.created += 1
                if on_progress:
                    status = "existente" if existed else "criado"
                    on_progress(f"{group.apartment} — {status}", True)
            except Exception as e:
                logger.error(f"Falha ao cadastrar {group.apartment}: {e}")
                report.failed.append(
                    ClientFailure(
                        apartment=group.apartment,
                        reason=str(e),
                    )
                )
                if on_progress:
                    on_progress(f"X {group.apartment}: {e}", False)
        return report

    def fetch_from_asaas(self) -> list[tuple[str, str]]:
        logger.info("entrando em fetch_from_asaas")
        return self.client.list_all_customers()

    def equality(
        self, residents: list[Resident], customers: list[tuple[str, str]]
    ) -> dict:
        logger.info("Entrando em equality")
        customers_cpf_cnpj = [c[1] for c in customers]
        return {r.cpf_cnpj: r for r in residents if r.cpf_cnpj in customers_cpf_cnpj}

    def diff(self, residents: list[Resident], customers: list[tuple[str, str]]) -> dict:
        logger.info("Entrando em diff")
        sheets_map = {r.cpf_cnpj: r for r in residents}
        asaas_map = {c[1]: c[0] for c in customers}

        only_sheets = [r for cpf, r in sheets_map.items() if cpf not in asaas_map]
        only_asaas = [
            (name, cpf) for cpf, name in asaas_map.items() if cpf not in sheets_map
        ]
        return {
            "only_sheets": only_sheets,
            "only_asaas": only_asaas,
        }
