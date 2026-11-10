import httpx
from tenacity import retry, stop_after_attempt, wait_exponential
from src.config import settings
from models.clients import *
from models.billing import Invoice, PaymentResult
from loguru import logger
import time


class AsaasClient:
    def __init__(self):
        self._http = httpx.Client(
            base_url=settings.asaas_api_base,
            headers={
                "access_token": settings.asaas_access_token,
                "Content-Type": "application/json",
            },
            timeout=30,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=16))
    def get_by_id(self, id: str) -> tuple[str, ...]:  # type: ignore
        response = self._http.get(f"/customers/{id}")
        response.raise_for_status()
        if data := response.json():
            logger.info(f"get: {id} = {data["name"]}")
            return data["name"], data["cpfCnpj"]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=16))
    def _disable_notifications(self, customer_id: str) -> dict:
        response = self._http.put(
            f"/customers/{customer_id}", json={"notificationDisabled": True}
        )
        response.raise_for_status()
        return response.json()

    def desativar_notificacoes(self) -> tuple[int, list[tuple[str, str]]]:
        """Desativa as notificações do Asaas de todos os clientes.
        Devolve (quantos foram atualizados, [(nome, erro), ...])."""
        ok = 0
        falhas: list[tuple[str, str]] = []
        for nome, _cpf, customer_id in self.list_all_customers():
            try:
                self._disable_notifications(customer_id)
                ok += 1
                logger.info(f"put: {customer_id} = {nome} teve as notificações desativadas")
            except Exception as e:
                logger.error(f"Falha ao desativar notificações de {nome}: {e!r}")
                falhas.append((nome, str(e)))
        return ok, falhas

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=16))
    def _find_customer_by_cpf(self, cpf_cnpj: str) -> str | None:
        """Busca (GET, seguro de repetir) o id do cliente com esse CPF."""
        response = self._http.get("/customers", params={"cpfCnpj": cpf_cnpj})
        response.raise_for_status()
        if data := response.json()["data"]:
            return data[0]["id"]
        return None

    def get_or_create_customer(self, resident: Resident):
        # Ve se ja ta cadastrado
        if resident.cpf_cnpj:
            if customer_id := self._find_customer_by_cpf(resident.cpf_cnpj):
                return customer_id, "existe"

        # Se n tiver, cadastra. Sem retry automático: se o POST der timeout
        # depois de o Asaas criar o cliente, repetir criaria um duplicado.
        payload = {
            "name": resident.name,
            "cpfCnpj": resident.cpf_cnpj,
            "email": resident.email,
            "notificationDisabled": True,
        }
        response = self._http.post(
            "/customers", json={k: v for k, v in payload.items() if v}
        )
        response.raise_for_status()
        return response.json()["id"], "criado"

    def create_payment(self, customer_id: str, invoice: Invoice):
        # Sem retry automático: se o POST der timeout depois de o Asaas criar
        # a cobrança, repetir criaria uma cobrança duplicada.
        payload = {
            "customer": customer_id,
            "billingType": invoice.billing_type.value,
            "value": invoice.value,
            "dueDate": invoice.due_date.isoformat(),
            "description": invoice.description,
            "postalService": False,
        }
        if invoice.external_reference:
            payload["externalReference"] = invoice.external_reference
        response = self._http.post("/payments", json=payload)
        response.raise_for_status()
        return response.json()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=16))
    def find_payment_by_reference(self, external_reference: str) -> dict | None:
        """Cobrança (não removida) com esse externalReference, se existir."""
        response = self._http.get(
            "/payments", params={"externalReference": external_reference}
        )
        response.raise_for_status()
        if data := response.json()["data"]:
            return data[0]
        return None

    def _list_all(self, path: str, max_falhas: int = 3) -> list[dict]:
        """Percorre todas as páginas de um endpoint de listagem do Asaas.
        Espera em caso de rate limit e levanta erro depois de `max_falhas`
        falhas seguidas, em vez de ficar em loop ou devolver lista truncada."""
        index = 0
        falhas = 0
        output: list[dict] = []
        while True:
            try:
                response = self._http.get(path, params={"offset": index, "limit": 100})
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 5))
                    logger.warning(f"Rate limited em {path}, aguardando {retry_after}s...")
                    time.sleep(retry_after)
                    continue
                response.raise_for_status()
                body = response.json()
            except Exception as e:
                falhas += 1
                logger.error(f"Erro ao listar {path} (offset {index}, tentativa {falhas}): {e!r}")
                if falhas >= max_falhas:
                    raise
                time.sleep(2**falhas)
                continue

            falhas = 0
            data = body.get("data") or []
            output.extend(data)
            if not body.get("hasMore") or not data:
                break
            index += len(data)
        return output

    def list_all_payments(self) -> list[tuple[str, ...]]:
        # Um único mapa de clientes em vez de um GET por cobrança
        clientes = {c[2]: (c[0], c[1]) for c in self.list_all_customers()}
        output = []
        for payment in self._list_all("/payments"):
            customer = clientes.get(payment["customer"])
            if customer is None:
                # Cliente removido não aparece na listagem: busca individual
                try:
                    customer = self.get_by_id(payment["customer"]) or ("?", "-")
                except Exception as e:
                    logger.error(f"Cliente {payment['customer']} não encontrado: {e!r}")
                    customer = ("?", "-")
            output.append(
                (
                    payment["id"],
                    customer[0],
                    payment["dateCreated"],
                    payment["dueDate"],
                    str(payment["value"]),
                    str(payment.get("netValue")),
                    payment["status"],
                    customer[1],
                    payment.get("bankSlipUrl"),
                    payment.get("invoiceUrl"),
                )
            )
        return output

    def list_all_customers(self) -> list[tuple[str, ...]]:
        return [
            (customer["name"], customer["cpfCnpj"], customer["id"])
            for customer in self._list_all("/customers")
        ]

    def del_boleto_by_id(self, id: str) -> tuple[bool, str]:
        """Remove a cobrança. Devolve (removida?, id ou mensagem de erro)."""
        try:
            response = self._http.delete(f"/payments/{id}")
            response.raise_for_status()
            data = response.json()
            logger.info(f"deleted {data.get('id', id)}")
            return bool(data.get("deleted")), id
        except Exception as e:
            logger.error(f"Falha ao deletar pagamento de id {id}: {e!r}")
            return False, str(e)
