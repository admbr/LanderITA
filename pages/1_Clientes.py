import streamlit as st
import pandas as pd
from loguru import logger

svc_billing = st.session_state.services["billing"]
svc_clients = st.session_state.services["client"]
dt_residents = st.session_state.data["residents"]
dt_groups = st.session_state.data["groups"]
dt_customers = st.session_state.data["customers"]

tab_sync, tab_groups, tab_asaas = st.tabs(
    ["📝 Sincronizar com o Sheets", "🏢 Grupos de Apartamentos", "🪽 Cadastro na Asaas"]
)

with tab_sync:
    st.subheader("Moradores na Planilha do Sheets")
    st.caption("Fonte: uma das ♾️ planilhas")

    df = pd.DataFrame(
        [
            {
                "Nome": r.name,
                "CPF": r.cpf_cnpj,
                "Apartamento": r.apartment or "-",
                "E-mail": r.email or "-",
                "Turma": r.turma or "-",
                "Telefone": r.phone or "-",
            }
            for r in dt_residents
        ]
    )

    apts = sorted(df["Apartamento"].unique())
    selected = st.multiselect("Filtrar por apartamento", apts)
    if selected:
        df = df[df["Apartamento"].isin(selected)]

    st.dataframe(df, width="stretch", hide_index=True)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total de moradores", len(dt_residents))
    col2.metric("Apartamentos únicos", df["Apartamento"].nunique())
    col3.metric("Sem apartamento", df[df["Apartamento"] == "-"].shape[0])
    col4.metric("Sem e-mail", df[df["E-mail"] == "-"].shape[0])

with tab_groups:
    st.subheader("Grupos por apartamento")
    st.caption("Gerado apartir da lista de residentes")

    df_groups = pd.DataFrame(
        [
            {
                "Apartamento": g.apartment or "-",
                "Líder": g.leader.name,
                "CPF líder": g.leader.cpf_cnpj,
                "Moradores": len(g.residents),
                "E-mails": len(g.emails),
                "E-mails lista": ", ".join(
                    [tup[1] if tup[1] else "-" for tup in g.emails]
                ),
            }
            for g in dt_groups
        ]
    )

    st.dataframe(
        df_groups.drop(columns=["E-mails lista"]),
        width="stretch",
        hide_index=True,
    )

    st.metric(label="🧮 # de Grupos", value=len(df_groups))

    st.divider()
    st.subheader("Detalhes de um apartamento")
    ap_selecionado = st.selectbox(
        "Escolha o apartamento",
        options=[g.apartment for g in dt_groups],
    )

    grupo = next(g for g in dt_groups if g.apartment == ap_selecionado)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Moradores**")
        for r in grupo.residents:
            st.write(f"- {r.name} — `{r.cpf_cnpj}`")
    with col2:
        st.markdown("**E-mails para notificação**")
        for _, email in grupo.emails:
            st.write(f"- {email}")

with tab_asaas:
    st.subheader("Cadastro na Asaas")
    st.caption("Cria ou atualiza os clientes na plataforma de cobranças.")

    dry_run = st.toggle("Modo simulação (dry-run)", value=True)
    if dry_run:
        st.info("Nenhum cliente será criado na Asaas.")

    if st.button("🪽 Sincronizar com Asaas", type="primary"):
        progress = st.progress(0)
        status = st.status("Sincronizando...", expanded=True)
        resultados = {"criados": 0, "existentes": 0, "falhas": []}
        n = len(dt_groups)

        def on_progress(msg: str, success: bool):
            icon = "✅" if success else "❌"
            status.write(f"{icon} {msg}")
            done = (
                resultados["criados"]
                + resultados["existentes"]
                + len(resultados["falhas"])
            )
            progress.progress(min(done / n, 1.0))

        with st.spinner(""):
            upload_report = svc_clients.sync_to_asaas(
                dt_groups,
                dry_run=dry_run,
                on_progress=on_progress,
            )

        status.update(label="Sincronização concluída!", state="complete")

        col1, col2, col3 = st.columns(3)
        col1.metric("🐇 Criados", upload_report.created)
        col2.metric("🐢 Existentes", upload_report.existing)
        col3.metric("🦓 Falhas", len(upload_report.failed))

        st.divider()

        if st.button("Desativar Notificações do Asaas"):
            svc_clients.desativar_notificacoes_from_asaas()

        diffs = svc_clients.diff(residents=dt_residents, customers=dt_customers)

        col1, col2, col3 = st.columns(3)
        col1.metric(
            "😴 # Moradores existentes apenas na planilha", len(diffs["only_sheets"])
        )
        col2.metric(
            "🫥 # Moradores existentes apenas na asaas", len(diffs["only_asaas"])
        )
        col3.metric(
            "🤑 # Moradores existentes em ambas",
            len(svc_clients.equality(dt_residents, dt_customers).items()),
        )

        if upload_report.failed:
            with st.expander(f"Ver {len(upload_report.failed)} falhas"):
                st.dataframe(
                    [f.model_dump() for f in upload_report.failed],
                    use_container_width=True,
                )

        apenas_sheets = pd.DataFrame(
            [
                {
                    "Nome": r.name,
                    "CPF": r.cpf_cnpj,
                    "Apartamento": r.apartment or "-",
                    "Email": r.email or "-",
                    "Turma": r.turma or "-",
                    "Telefone": r.phone or "-",
                }
                for r in diffs["only_sheets"]
            ]
        )
        apenas_asaas = pd.DataFrame(
            [
                {
                    "Nome": c[0],
                    "CPF": c[1],
                }
                for c in diffs["only_asaas"]
            ]
        )
        asaas_completa = pd.DataFrame(
            [
                {
                    "Nome": c[0],
                    "CPF": c[1],
                }
                for c in dt_customers
            ]
        )

        st.subheader("Existentes apenas no sheets")
        st.dataframe(apenas_sheets, width="stretch", hide_index=True)

        col11, col21 = st.columns(2)
        with col11:
            st.subheader("Todos os clientes da Asaas")
        with col21:
            st.subheader("Clientes da Asaas que não estão na planilha")

        col12, col22 = st.columns(2)
        with col12:
            st.dataframe(asaas_completa, width="stretch", hide_index=True)
        with col22:
            st.dataframe(apenas_asaas, width="stretch", hide_index=True)
