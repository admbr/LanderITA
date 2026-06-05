import httpx
from tenacity import retry, stop_after_attempt, wait_exponential
from src.config import settings
from models.clients import *
from models.billing import Invoice, PaymentResult


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

    def list_all_customers(self) -> list[tuple[str, str]]:
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
                        (customer["name"], customer["cpfCnpj"])
                        for customer in response.json()["data"]
                    ]
                )
                index += 100
            except:
                break
        return output
