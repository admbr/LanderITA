import streamlit as st
from st_keyup import st_keyup
from pathlib import Path
from datetime import date, timedelta
from models.clients import Group
from loguru import logger
from models.billing import RunReport
import pandas as pd
from src.utils import *

svc_billing = st.session_state.services["billing"]
svc_clients = st.session_state.services["client"]
svc_notify = st.session_state.services["notify"]
dt_residents = st.session_state.data["residents"]
dt_groups = st.session_state.data["groups"]
dt_customers = st.session_state.data["customers"]
dt_boletos = st.session_state.data["boletos"]

boletos = boletos_to_df(dt_boletos)

st.title("Notificar")

col1, col2 = st.columns(2)

with col1:
    with st.form("test"):
        st.header("Notificar isoladamente")

        emails = st.multiselect(
            label="emails",
            options=[resident.email for resident in dt_residents],
        )

        subject = st.text_input(
            label="Assunto do email", value="Assunto financeiro?..."
        )

        body = st.text_area(label="Corpo do email", value="Oi do FinCASD...")

        submitted = st.form_submit_button("Mandar")
        if submitted:
            for email in emails:
                svc_notify.send_one_sync(to=email, subject=subject, html_body=body)
            st.write("Mandado!")

with col2:
    st.header("Teste?")

st.divider()

col1, col2, col3 = st.columns(3)

with col1:
    selection1 = st.multiselect("Filtrar por pessoa", sorted(boletos["Nome"].unique()))
with col2:
    selection2 = st.multiselect(
        "Filtrar por Status", sorted(boletos["Status"].unique())
    )
with col3:
    selection3 = st.multiselect(
        "Filtrar por data", sorted(boletos["Criado em"].unique())
    )

if selection1:
    boletos = boletos[boletos["Nome"].isin(selection1)]
if selection2:
    boletos = boletos[boletos["Status"].isin(selection2)]
if selection3:
    boletos = boletos[boletos["Criado em"].isin(selection3)]

boletos = boletos.reset_index(drop=True)
event = st.dataframe(data=boletos, on_select="rerun", selection_mode=["multi-row"])

col1, col2 = st.columns(2)

with col1:
    if st.button("Notificar Selecionados"):
        with st.spinner("Enviando..."):
            email_report = svc_notify.notify_some(
                boletos=[tuple(boletos.loc[row]) for row in event.selection.rows],  # type: ignore
                groups=dt_groups,
            )
        st.success(f"✅ {email_report.sent} e-mails enviados")
        if email_report.failed:
            st.error(f"❌ {len(email_report.failed)} falhas")
            st.json(email_report.failed)
