import streamlit as st
from services.billing.billingService import BillingService
from src.asaas import AsaasClient
from services.client.clientService import ClientService
from services.notify.notifyService import NotifyService
from src.utils import fetch_residents, fetch_groups

st.set_page_config(page_title="LanderITA", page_icon="💸", layout="wide")


@st.cache_resource
def get_services():
    asaas = AsaasClient()
    return {
        "billing": BillingService(client=asaas),
        "client": ClientService(client=asaas),
        "notify": NotifyService(),
    }


st.session_state.services = get_services()


@st.cache_data
def get_data():
    residents = fetch_residents()
    return {
        "residents": residents,
        "groups": fetch_groups(residents=residents),
        "customers": st.session_state.services["client"].fetch_from_asaas(),
        "boletos": st.session_state.services["billing"].fetch_boletos_from_asaas(),
    }


st.session_state.data = get_data()

st.title("💸 LavanderITA")
st.caption("Automação da cobrança da LavanderITA")
