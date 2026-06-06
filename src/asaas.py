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
        response = self._http.get("/customers", params={"id": id})
        response.raise_for_status()
        if data := response.json():
            logger.info(f"get: {id} = {data["name"]}")
            return data["name"], data["cpfCnpj"]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=16))
    def desativar_notificacoes(self) -> tuple[str, ...]:  # type: ignore
        clientes = self.list_all_customers()
        for cliente in clientes:
            response = self._http.put(
                "/customers", params={"id": cliente[2], "notificationDisabled": True}
            )
            response.raise_for_status()
            if data := response.json():
                logger.info(
                    f"put: {cliente[2]} = {data["name"]} teve as notificações desativadas"
                )
                return data["name"], data["cpfCnpj"], data["notificationDisabled"]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=16))
    def get_or_create_customer(self, resident: Resident):
        # Ve se ja ta cadastrado
        if resident.cpf_cnpj:
            response = self._http.get(
                "/customers", params={"cpfCnpj": resident.cpf_cnpj}
            )
            response.raise_for_status()
            if data := response.json()["data"]:
                return data[0]["id"], "existe"

        # Se n tiver, cadastra
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

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=16))
    def create_payment(self, customer_id: str, invoice: Invoice):
        payload = {
            "customer": customer_id,
            "billingType": invoice.billing_type.value,
            "value": invoice.value,
            "dueDate": invoice.due_date.isoformat(),
            "description": invoice.description,
            "postalService": False,
        }
        response = self._http.post("/payments", json=payload)
        response.raise_for_status()
        return response.json()

    def list_all_payments(self) -> list[tuple[str, ...]]:
        index = 0
        output = []
        while True:
            try:
                response = self._http.get(
                    "/payments", params={"offset": index, "limit": 100}
                )

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 5))
                    logger.warning(f"Rate limited, aguardando {retry_after}s...")
                    time.sleep(retry_after)
                    continue

                response.raise_for_status()
                data = response.json().get("data")
                if not data:
                    break

                for payment in data:
                    customer = self.get_by_id(payment["customer"])
                    output.append(
                        (
                            payment["id"],
                            customer[0],
                            payment["dateCreated"],
                            payment["dueDate"],
                            str(payment["value"]),
                            str(payment["netValue"]),
                            payment["status"],
                            customer[1],
                            payment["bankSlipUrl"],
                            payment["invoiceUrl"],
                        )
                    )

                index += 100
            except Exception as e:
                logger.error(f"Erro no list_all_payments: {e}")
        return output

    def list_all_customers(self) -> list[tuple[str, ...]]:
        index = 0
        output = []
        while True:
            try:
                response = self._http.get(
                    "/customers", params={"offset": index, "limit": 100}
                )
                if not response.json()["data"]:
                    break
                response.raise_for_status()
                output.extend(
                    [
                        (customer["name"], customer["cpfCnpj"], customer["id"])
                        for customer in response.json()["data"]
                    ]
                )
                index += 100
            except:
                break
        return output

    def del_boleto_by_id(self, id: str) -> tuple[bool, str] | None:
        try:
            response = self._http.delete("/payments", params={"id": id})
            response.raise_for_status()
            if data := response.json():
                logger.info(f"deleted {data["id"]}")
                return data["deleted"], data["id"]
        except Exception as e:
            logger.error(f"Falha ao deletar pagamento de id: {id}")
