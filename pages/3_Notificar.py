import streamlit as st
from st_keyup import st_keyup
from pathlib import Path
from datetime import date, timedelta
from models.clients import Group
from loguru import logger
from models.billing import RunReport
import pandas as pd
from src.utils import *
from src.state import ensure_state

ensure_state()

svc_billing = st.session_state.services["billing"]
svc_clients = st.session_state.services["client"]
svc_notify = st.session_state.services["notify"]
dt_residents = st.session_state.data["residents"]
dt_groups = st.session_state.data["groups"]
dt_customers = st.session_state.data["customers"]
dt_boletos = st.session_state.data["boletos"]

boletos = boletos_to_df(dt_boletos, dt_residents)

st.title("Notificar")

col1, col2 = st.columns(2)

with col1:
    with st.form("test"):
        st.header("Notificar isoladamente")

        emails = st.multiselect(
            label="Emails",
            options=[resident.email for resident in dt_residents if resident.email],
        )

        subject = st.text_input(
            label="Assunto do email", value="Assunto financeiro?..."
        )

        body = st.text_area(label="Corpo do email", value="Oi do FinCASD...")

        submitted = st.form_submit_button("Mandar")
        if submitted:
            enviados, falhas = 0, []
            for email in emails:
                try:
                    svc_notify.send_one_sync(to=email, subject=subject, html_body=body)
                    enviados += 1
                except Exception as e:
                    falhas.append(f"{email}: {e}")
            if enviados:
                st.success(f"{enviados} e-mail(s) enviado(s).")
            if falhas:
                st.error("Falharam:\n\n" + "\n\n".join(falhas))

with col2:
    st.header("Teste?")

st.divider()

col1, col2, col3 = st.columns(3)

with col1:
    selection1 = st.multiselect("Filtrar por Nome", sorted(boletos["Nome"].unique()))
    selection4 = st.multiselect(
        "Filtrar por Apartamento", sorted(boletos["Apartamento"].unique())
    )
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
if selection4:
    boletos = boletos[boletos["Apartamento"].isin(selection4)]

boletos = boletos.reset_index(drop=True)
event = st.dataframe(data=boletos, on_select="rerun", selection_mode=["multi-row"])

col1, col2 = st.columns(2)

with col1:
    if st.button("Notificar Selecionados"):
        with st.spinner("Enviando..."):
            email_report = svc_notify.notify_some(
                boletos=[dict(boletos.loc[row]) for row in event.selection.rows],  # type: ignore
                groups=dt_groups,
            )
        st.success(f"✅ {email_report.sent} e-mails enviados")
        if email_report.failed:
            st.error(f"❌ {len(email_report.failed)} não enviados")
            st.dataframe(
                [f.model_dump() for f in email_report.failed], hide_index=True
            )
