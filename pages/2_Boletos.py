import streamlit as st
from pathlib import Path
from datetime import date, timedelta
from models.clients import Group
import tempfile
from loguru import logger
from st_keyup import st_keyup
from models.billing import *
import pandas as pd
import json
import math
from src.utils import *

svc_billing = st.session_state.services["billing"]
svc_clients = st.session_state.services["client"]
svc_notify = st.session_state.services["notify"]
dt_residents = st.session_state.data["residents"]
dt_groups = st.session_state.data["groups"]
dt_customers = st.session_state.data["customers"]
dt_boletos = st.session_state.data["boletos"]

st.title("📄 BOLETOS!")

tab_visualization, tab_creation = st.tabs(["🧾 Visualizar boletos", "✒️ Criar boletos"])

boletos = boletos_to_df(dt_boletos, dt_residents)

with tab_visualization:
    col1, col2, col3 = st.columns(3)

    with col1:
        selection1 = st.multiselect(
            "Filtrar por Nome", sorted(boletos["Nome"].unique())
        )
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

    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("Deletar Boletos Selecionados"):
            svc_billing.delete_boletos_by_id([boletos.loc[row, "Id"] for row in event.selection.rows])  # type: ignore
            st.rerun()

    with col2:
        if st.button("Recarregar"):
            st.session_state.data["boletos"] = st.session_state.services[
                "billing"
            ].fetch_boletos_from_asaas()
            dt_boletos = st.session_state.data["boletos"]
            st.rerun()

    with col3:
        st.download_button(
            label="Baixar boletos (CSV)",
            data=boletos.to_csv(index=False).encode("utf-8"),
            file_name="boletos_lavanderita.csv",
            mime="text/csv",
        )

with tab_creation:
    st.header("Cobrar")

    col1, col2 = st.columns(2)

    with col1:
        planilha_csv = st.file_uploader("Planilha da OMO (em .csv)", type="csv")

    with col2:
        due_date = st.date_input(
            "Vencimento",
            value=date.today().replace(day=10),
        )
        dry_run = st.toggle("Modo simulação (dry-run)", value=True)
        if dry_run:
            st.info("Nenhuma cobrança será criada na Asaas.")

    if planilha_csv:

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(planilha_csv.read())
            tmp_path = Path(tmp.name)
        logger.info("tempfile created")

        invoices, values = svc_billing.build_invoices(
            extract_path=tmp_path,
            due_date=due_date,
            residents=dt_residents,
            groups=dt_groups,
        )

        st.divider()
        st.header(f"Preview - {len(invoices)} conbranças")

        df = pd.DataFrame(
            [
                {
                    "Apartamento": invoice.group.apartment or "-",
                    "Responsável": invoice.group.leader.name,
                    "Valor (R$)": invoice.value,
                    "Vencimento": invoice.due_date,
                    "E-mails": len(invoice.group.emails),
                }
                for invoice in invoices
            ]
        )
        logger.info("dataframe created")
        st.dataframe(
            df.style.format({"Valor (R$)": "R$ {:.2f}"}),
            width="stretch",
            hide_index=True,
        )
        logger.info("st-dataframe renderd created")

        total = df["Valor (R$)"].sum()
        st.metric(
            "Total a cobrar",
            f"R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        )

        st.download_button(
            label="Baixar JSON dos valores",
            data=json.dumps(
                {k.cpf_cnpj: v if math.isnan(v) else v for k, v in values.items()},
                indent=4,
            ),
            file_name=f"valores-{due_date}.json",
        )

        if st.button("💸💸💸 Gerar Boletos", type="primary", disabled=not invoices):

            progress_bar = st.progress(0)
            status_box = st.status("Gerando...", expanded=False)
            N = len(invoices)

            def on_progress(message: str, success: bool):
                icon = "✅" if success else "❌"
                status_box.write(f"{icon} {message}")
                done = len(report.success) + len(report.failed)  # type: ignore
                progress_bar.progress(done / N)

            report = RunReport()

            report = svc_billing.post_invoices(
                invoices,
                dry_run=dry_run,
                on_progress=on_progress,
                values=values,
            )

            status_box.update(label="Concluído!", state="complete")

            st.session_state["last_report"] = report

            c1, c2, c3 = st.columns(3)
            c1.metric("✅ Sucesso", len(report.success))
            c2.metric("❌ Falhas", len(report.failed))
            c3.metric("⏭️ Pulados", len(report.skipped))
