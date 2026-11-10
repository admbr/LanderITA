"""Estado compartilhado do app Streamlit.

Toda página chama `ensure_state()` no topo. Assim qualquer página funciona
mesmo se for aberta direto pela URL (sem passar pelo app.py antes).
"""

import streamlit as st
from loguru import logger

from services.billing.billingService import BillingService
from services.client.clientService import ClientService
from services.notify.notifyService import NotifyService
from src.asaas import AsaasClient
from src.utils import fetch_groups, fetch_residents

# Sessões novas reaproveitam os dados por até 10 min; depois disso buscam de novo
DATA_TTL_SECONDS = 600


@st.cache_resource
def get_services() -> dict:
    asaas = AsaasClient()
    return {
        "billing": BillingService(client=asaas),
        "client": ClientService(client=asaas),
        "notify": NotifyService(),
    }


@st.cache_data(ttl=DATA_TTL_SECONDS, show_spinner="Carregando planilha e Asaas...")
def _load_data() -> dict:
    services = get_services()
    residents = fetch_residents()
    return {
        "residents": residents,
        "groups": fetch_groups(residents=residents),
        "customers": services["client"].fetch_from_asaas(),
        "boletos": services["billing"].fetch_boletos_from_asaas(),
    }


def _load_into_session() -> None:
    try:
        st.session_state.data = _load_data()
    except Exception as e:
        logger.exception("Falha ao carregar os dados")
        st.error(
            f"Não foi possível carregar os dados da planilha / Asaas: {e}. "
            "Confira o token, a conexão e tente recarregar."
        )
        if st.button("Tentar de novo"):
            st.rerun()
        st.stop()


def reload_data() -> None:
    """Descarta o cache e busca de novo planilha, clientes e boletos."""
    _load_data.clear()
    st.session_state.pop("data", None)
    _load_into_session()


def reload_boletos() -> None:
    """Atualiza só os boletos desta sessão (depois de criar/deletar)."""
    st.session_state.data["boletos"] = st.session_state.services[
        "billing"
    ].fetch_boletos_from_asaas()


def ensure_state() -> None:
    if "services" not in st.session_state:
        st.session_state.services = get_services()
    if "data" not in st.session_state:
        _load_into_session()

    with st.sidebar:
        if st.button("🔄 Recarregar dados", help="Busca de novo a planilha do Google e o Asaas"):
            reload_data()
            st.rerun()
