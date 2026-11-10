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
from src.asaas import *
from src.state import ensure_state, reload_boletos

ensure_state()

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
            ids = [boletos.loc[row, "Id"] for row in event.selection.rows]  # type: ignore
            st.session_state["falhas_delete"] = svc_billing.delete_boletos_by_id(ids)
            st.session_state["n_delete"] = len(ids)
            reload_boletos()
            st.rerun()
        # Resultado da última exclusão (sobrevive ao st.rerun acima)
        if "falhas_delete" in st.session_state:
            falhas_delete = st.session_state.pop("falhas_delete")
            n_delete = st.session_state.pop("n_delete")
            if falhas_delete:
                st.error(
                    f"{len(falhas_delete)} de {n_delete} boleto(s) não foram deletados: "
                    + "; ".join(f"{i} ({motivo})" for i, motivo in falhas_delete)
                )
            elif n_delete:
                st.success(f"{n_delete} boleto(s) deletado(s).")

    with col2:
        if st.button("Recarregar"):
            reload_boletos()
            st.rerun()

    with col3:
        st.download_button(
            label="Baixar boletos (CSV)",
            data=boletos.to_csv(index=False).encode("utf-8"),
            file_name="boletos_lavanderita.csv",
            mime="text/csv",
        )

with tab_creation:
    st.header("Cobrar em massa")

    col1, col2 = st.columns(2)

    with col1:
        planilha_csv = st.file_uploader("Planilha da OMO (em .csv)", type="csv")

    with col2:
        # Próximo dia 10 (depois do dia 10, o do mês seguinte)
        hoje = date.today()
        proximo_dia_10 = (
            hoje.replace(day=10)
            if hoje.day <= 10
            else (hoje.replace(day=1) + timedelta(days=32)).replace(day=10)
        )
        due_date = st.date_input("Vencimento", value=proximo_dia_10, min_value=hoje)
        dry_run = st.toggle("Modo simulação (dry-run)", value=True)
        if dry_run:
            st.info("Nenhuma cobrança será criada na Asaas.")

    if planilha_csv:

        # Lido direto da memória: não grava cópias do CSV (com CPFs) no disco
        csv_bytes = planilha_csv.getvalue()

        invoices, values = svc_billing.build_invoices(
            extract_path=csv_bytes,
            due_date=due_date,
            residents=dt_residents,
            groups=dt_groups,
        )
        logger.info(f"{len(values)=}, {len(invoices)=}")

        st.divider()
        st.header(f"Preview - {len(invoices)} cobranças")

        COLUNAS = ["Apartamento", "Responsável", "Valor (R$)", "Vencimento", "E-mails"]

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
            ],
            columns=COLUNAS,
        )

        if df.empty:
            st.warning(
                "Nenhuma cobrança gerada a partir da planilha. Verifique o arquivo."
            )
        else:
            st.dataframe(
                df.style.format({"Valor (R$)": "R$ {:.2f}"}),
                width="stretch",
                hide_index=True,
            )

            def brl(v: float) -> str:
                return (
                    f"R$ {v:,.2f}".replace(",", "X")
                    .replace(".", ",")
                    .replace("X", ".")
                )

            total = df["Valor (R$)"].sum()
            total_csv = omo_csv_total(csv_bytes)
            diferenca = total_csv - total

            m1, m2 = st.columns(2)
            m1.metric("Total a cobrar", brl(total))
            m2.metric(
                "Total na planilha da OMO",
                brl(total_csv),
                delta=None if abs(diferenca) < 0.005 else f"-{brl(diferenca)} não cobrados",
                delta_color="normal",
            )

            mismatches = find_csv_mismatches(dt_residents, csv_bytes)
            if not mismatches.empty:
                st.error(
                    f"⚠️ {len(mismatches)} morador(es) da planilha da OMO não foram "
                    f"encontrados na planilha do Google pelo CPF e **não serão cobrados** "
                    f"({brl(mismatches['Valor (R$)'].sum())}). Corrija a planilha do "
                    f"Google e recarregue os dados."
                )
                st.dataframe(
                    mismatches.style.format({"Valor (R$)": "R$ {:.2f}"}),
                    width="stretch",
                    hide_index=True,
                )
            elif abs(diferenca) >= 0.005:
                st.warning(
                    f"O total cobrado difere do total da planilha da OMO em "
                    f"{brl(diferenca)}. Verifique o arquivo."
                )

            invalid_cpfs = find_invalid_cpfs(dt_residents)
            if not invalid_cpfs.empty:
                with st.expander(
                    f"⚠️ {len(invalid_cpfs)} morador(es) com CPF inválido ou "
                    f"repetido na planilha do Google"
                ):
                    st.caption(
                        "Esses moradores não vão casar com a planilha da OMO quando "
                        "usarem a lavanderia. Quando um CPF está repetido, o uso "
                        "dele é cobrado só do primeiro morador da planilha."
                    )
                    st.dataframe(invalid_cpfs, width="stretch", hide_index=True)

            st.download_button(
                label="Baixar JSON dos valores",
                data=json.dumps(
                    {
                        k.cpf_cnpj: None if math.isnan(v) else v
                        for k, v in values.items()
                    },
                    indent=4,
                ),
                file_name=f"valores-{due_date}.json",
            )

            lote = (planilha_csv.name, planilha_csv.size, due_date)
            ja_gerado = st.session_state.get("lote_gerado") == lote
            if ja_gerado:
                st.info(
                    "Os boletos desta planilha com este vencimento já foram gerados "
                    "nesta sessão."
                )

            if st.button(
                "💸💸💸 Gerar Boletos", type="primary", disabled=ja_gerado and not dry_run
            ):

                progress_bar = st.progress(0)
                status_box = st.status("Gerando...", expanded=False)
                N = len(invoices)

                done = 0

                def on_progress(message: str, success: bool):
                    global done
                    done += 1
                    icon = "✅" if success else "❌"
                    status_box.write(f"{icon} {message}")
                    progress_bar.progress(min(done / N, 1.0))

                report = svc_billing.post_invoices(
                    invoices,
                    dry_run=dry_run,
                    on_progress=on_progress,
                    values=values,
                )

                progress_bar.progress(1.0)
                status_box.update(label="Concluído!", state="complete")

                st.session_state["last_report"] = report
                if not dry_run:
                    st.session_state["lote_gerado"] = lote
                    reload_boletos()

                c1, c2, c3 = st.columns(3)
                c1.metric("✅ Sucesso", len(report.success))
                c2.metric("❌ Falhas", len(report.failed))
                c3.metric("⏭️ Pulados", len(report.skipped))

                if report.failed:
                    st.error(
                        "Algumas cobranças falharam. Se foi timeout, a cobrança pode "
                        "ter sido criada mesmo assim: gerar de novo pula as que já "
                        "existem."
                    )
                    st.dataframe([f.model_dump() for f in report.failed], hide_index=True)
                if report.skipped:
                    with st.expander(f"Ver {len(report.skipped)} puladas (já existiam)"):
                        st.dataframe(
                            [f.model_dump() for f in report.skipped], hide_index=True
                        )

    st.divider()

    with st.form("test"):
        st.header("Cobrar isoladamente")

        resident = st.multiselect(
            label="Aluno", options=dt_residents, format_func=lambda x: x.name
        )

        value = st.number_input(label="Valor a ser cobrado")

        due_date = st.date_input(label="Data de Vencimento")

        description = st.text_area(label="Descrição")

        submitted = st.form_submit_button("Criar")
        if submitted:
            if not resident:
                st.error("Selecione um aluno.")
            elif value <= 0:
                st.error("O valor precisa ser maior que zero.")
            elif due_date < date.today():
                st.error("O vencimento não pode ser no passado.")
            else:
                responsavel = resident[0]
                group = get_group_by_cpf(responsavel.cpf_cnpj, dt_groups) or Group(
                    apartment=responsavel.apartment,
                    leader=responsavel,
                    residents=[responsavel],
                )
                invoice = svc_billing.build_single_invoice(
                    group=group,
                    value=value,
                    due_date=due_date,
                    description=description,
                )
                try:
                    payment = svc_billing.post_single_invoice(
                        invoice=invoice, responsible=responsavel
                    )
                    st.success(
                        f"Cobrança criada para {responsavel.name}: {payment.get('invoiceUrl', payment.get('id'))}"
                    )
                except Exception as e:
                    logger.error(f"Erro ao criar cobrança avulsa: {e!r}")
                    st.error(f"Falha ao criar a cobrança: {e}")
