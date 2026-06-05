import streamlit as st
from pathlib import Path
from datetime import date, timedelta
from models.clients import Group
import tempfile
from loguru import logger
from rapidfuzz import process, fuzz
from st_keyup import st_keyup
from models.billing import RunReport
import asyncio

svc_billing = st.session_state.services["billing"]
svc_clients = st.session_state.services["clients"]
svc_notify = st.session_state.services["notify"]
dt_residents = st.session_state.data["residents"]
dt_groups = st.session_state.data["groups"]
dt_customers = st.session_state.data["customers"]

st.header("Notificar")

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
                asyncio.run(
                    svc_notify._send_one(to=email, subject=subject, html_body=body)
                )
            st.write("Mandado!")

with col2:
    st.header("Teste?")

st.divider()

st.header("Cobrar")

col1, col2 = st.columns(2)

with col1:
    planilha_csv = st.file_uploader("Planilha da OMO (em .csv)", type="csv")

with col2:
    due_date = st.date_input(
        "Vencimento",
        value=date.today().replace(day=10) + timedelta(days=30),
    )
    dry_run = st.toggle("Modo simulação (dry-run)", value=True)
    if dry_run:
        st.info("Nenhuma cobrança será criada na Asaas.")

if planilha_csv:

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(planilha_csv.read())
        tmp_path = Path(tmp.name)
    logger.info("tempfile created")

    invoices = svc_billing.build_invoices(
        extract_path=tmp_path,
        due_date=due_date,
        residents=dt_residents,
        groups=dt_groups,
    )
    logger.info(
        f"invoice list created! Lideres: {[inv.group.leader.name for inv in invoices]}"
    )

    st.divider()
    st.header(f"Preview - {len(invoices)} conbranças")

    import pandas as pd

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

    if st.button("💸💸💸 Gerar Boletos", type="primary", disabled=not invoices):

        progress_bar = st.progress(0)
        status_box = st.status("Gerando...", expanded=True)
        N = len(invoices)

        def on_progress(message: str, success: bool):
            icon = "✅" if success else "❌"
            status_box.write(f"{icon} {message}")
            done = len(report.success) + len(report.failed)
            progress_bar.progress(done / N)

        report = RunReport()

        report = svc_billing.post_invoices(
            invoices,
            dry_run=dry_run,
            on_progress=on_progress,
        )

        status_box.update(label="Concluído!", state="complete")
        st.session_state["last_report"] = report

        c1, c2, c3 = st.columns(3)
        c1.metric("✅ Sucesso", len(report.success))
        c2.metric("❌ Falhas", len(report.failed))
        c3.metric("⏭️ Pulados", len(report.skipped))

        st.divider()

        st.header("Notificar")

        if report:
            st.info(f"{len(report.success)} cobranças prontas para notificação.")

            preview_item = report.success[0] if report.success else None
            if preview_item:
                with st.expander("Preview do e-mail"):
                    st.markdown(f"""
          **Para:** {', '.join([r.email for r in preview_item.invoice.group.residents])}
          **Assunto:** [CASD] Boleto Lavanderia — {preview_item.invoice.group.apartment}

          Valor: **R$ {preview_item.invoice.value:.2f}**
          Vencimento: **{preview_item.invoice.due_date}**

          🔗 [Boleto]({preview_item.bank_splip_url})
          """)

            if st.button("📧 Enviar e-mails", type="primary"):
                with st.spinner("Enviando..."):
                    email_report = svc_notify.notify_all(report.success)
                st.success(f"✅ {email_report.sent} e-mails enviados")
                if email_report.failed:
                    st.error(f"❌ {len(email_report.failed)} falhas")
                    st.json(email_report.failed)
