import gspread
from google.oauth2.service_account import Credentials
from models.clients import *
from src.config import settings
from gspread.utils import ValueRenderOption
import pandas as pd
from pathlib import Path
from loguru import logger
import io
import re
import os
import unicodedata
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    settings.scope_spreadsheets,
    settings.scope_drive,
]


def _apartment_conversion(ap: str) -> str | None:
    if ap.strip() == "-":
        return None
    templist = list(re.sub(r"[^a-z0-9]", "", ap, flags=re.IGNORECASE))
    assert len(templist) == 6, f"Erro na conversão de notação de apartamento: {ap}"
    return "".join(["H", "-", "8", templist[-1], templist[0], templist[1], templist[2]])


def _get_client() -> gspread.Client:
    logger.info("_get_client() chamada")
    creds = Credentials.from_service_account_file(
        settings.google_application_credentials,
        scopes=SCOPES,
    )
    return gspread.authorize(creds)


def fetch_residents() -> list[Resident]:
    logger.info("fetch_residents() chamada")
    gc = _get_client()
    sheet = gc.open_by_url(settings.planilha_url).worksheet("Asaas - OMO")  # type: ignore
    rows = sheet.get_all_records(value_render_option=ValueRenderOption.formatted)

    residents = []
    for row in rows:
        try:
            residents.append(
                Resident(
                    name=row["NOME"],  # type: ignore
                    cpf_cnpj=str(row.get("CPF")) if row.get("CPF") is not None else "-",  # type: ignore
                    email=row.get("EMAIL") or None,  # type: ignore
                    phone=(
                        str(row.get("CELULAR"))
                        if row.get("CELULAR") is not None
                        else "-"
                    ),
                    apartment=row.get("ALOJ APTO"),  # type: ignore
                    turma=(str(row["TURMA"])),
                )
            )
        except Exception as e:
            logger.warning(f"Linha ignorada: {row} — {e}")
            continue

    return residents


PRECO_CICLO = 3.5


def read_omo_csv(path_csv: Path | bytes) -> pd.DataFrame | None:
    """Lê a planilha da OMO (caminho ou bytes do upload), normaliza o CPF e
    descarta a linha de TOTAL."""
    source = io.BytesIO(path_csv) if isinstance(path_csv, bytes) else path_csv
    try:
        # A planilha da OMO usa ";" como separador e vem com BOM
        planilha = pd.read_csv(
            source, sep=None, engine="python", encoding="utf-8-sig"
        )
        planilha["Documento"] = planilha["Documento"].apply(
            lambda x: re.sub(r"\D", "", str(x)).zfill(11) if pd.notna(x) else ""
        )
    except Exception as e:
        nome = "CSV enviado" if isinstance(path_csv, bytes) else path_csv
        logger.error(f"{nome} não pode ser lido com .csv: {e!r}")
        return None
    # A última linha ("TOTAL") não tem documento
    return planilha[planilha["Documento"] != ""].reset_index(drop=True)


def fetch_residents_value(
    residents: list[Resident], path_csv: Path | bytes
) -> dict[Resident, float]:
    logger.info("fetch_apartment_values() chamada")

    planilha = read_omo_csv(path_csv)
    if planilha is None:
        return {}

    relacao: dict[Resident, float] = {}
    cobrados: dict[str, Resident] = {}
    for resident in residents:
        matches = planilha[planilha["Documento"] == resident.cpf_cnpj]
        if matches.empty:
            relacao[resident] = 0.0
            continue
        # CPF repetido na planilha do Google: o uso desse CPF só pode ser
        # cobrado uma vez, senão os dois moradores pagam pelo mesmo uso
        if resident.cpf_cnpj in cobrados:
            logger.warning(
                f"CPF {resident.cpf_cnpj} repetido: uso já cobrado de "
                f"{cobrados[resident.cpf_cnpj].name}, não será cobrado de "
                f"{resident.name}"
            )
            relacao[resident] = 0.0
            continue
        cobrados[resident.cpf_cnpj] = resident
        divida = matches["Reservas Usadas"].max() * PRECO_CICLO
        relacao[resident] = divida

    return relacao


def cpf_valido(cpf: str) -> bool:
    """Confere os dígitos verificadores de um CPF (só dígitos)."""
    if len(cpf) != 11 or not cpf.isdigit() or cpf == cpf[0] * 11:
        return False
    d = [int(c) for c in cpf]
    for n in (9, 10):
        soma = sum(d[i] * (n + 1 - i) for i in range(n))
        if (soma * 10) % 11 % 10 != d[n]:
            return False
    return True


_STOPWORDS_NOME = {"de", "da", "do", "das", "dos", "e"}


def _tokens_nome(nome: str) -> set[str]:
    sem_acento = (
        unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode().lower()
    )
    return {t for t in re.findall(r"[a-z]+", sem_acento) if t not in _STOPWORDS_NOME}


def _omo_apartment_to_sheet(ap: str) -> str | None:
    """Converte '114 - H8A' (OMO) para 'H-8A 114' (planilha)."""
    m = re.match(r"\s*(\d+)\s*-\s*H-?8\s*([A-Za-z])", str(ap))
    if not m:
        return None
    return f"H-8{m.group(2).upper()} {m.group(1)}"


def omo_csv_total(path_csv: Path | bytes) -> float:
    planilha = read_omo_csv(path_csv)
    if planilha is None:
        return 0.0
    return float(planilha["Reservas Usadas"].sum()) * PRECO_CICLO


def find_csv_mismatches(residents: list[Resident], path_csv: Path | bytes) -> pd.DataFrame:
    """Linhas do CSV da OMO com reservas usadas cujo CPF não está na planilha
    (portanto não seriam cobradas), com um palpite de quem pode ser."""
    colunas = [
        "Usuário (OMO)",
        "CPF no CSV",
        "Apartamento (OMO)",
        "Reservas Usadas",
        "Valor (R$)",
        "Problema",
    ]
    planilha = read_omo_csv(path_csv)
    if planilha is None:
        return pd.DataFrame(columns=colunas)

    cpfs_planilha = {r.cpf_cnpj for r in residents}
    cpfs_csv = set(planilha["Documento"])
    sem_par = planilha[
        (~planilha["Documento"].isin(cpfs_planilha))
        & (planilha["Reservas Usadas"] > 0)
    ]

    linhas = []
    for _, row in sem_par.iterrows():
        tokens = _tokens_nome(str(row["Usuário"]))
        ap = _omo_apartment_to_sheet(row["Apartamento"])
        # Candidatos: moradores que não aparecem no CSV e têm nome parecido,
        # dando preferência a quem mora no mesmo apartamento
        candidatos = [
            r
            for r in residents
            if r.cpf_cnpj not in cpfs_csv and len(tokens & _tokens_nome(r.name)) >= 2
        ]
        candidatos.sort(key=lambda r: r.apartment != ap)

        if candidatos:
            r = candidatos[0]
            motivo = "CPF inválido" if not cpf_valido(r.cpf_cnpj) else "CPF diferente"
            problema = (
                f"{motivo} na planilha: '{r.name}' ({r.apartment}) "
                f"está com CPF {r.cpf_cnpj}"
            )
        else:
            problema = "Não cadastrado na planilha"

        linhas.append(
            {
                "Usuário (OMO)": row["Usuário"],
                "CPF no CSV": row["Documento"],
                "Apartamento (OMO)": row["Apartamento"],
                "Reservas Usadas": int(row["Reservas Usadas"]),
                "Valor (R$)": float(row["Reservas Usadas"]) * PRECO_CICLO,
                "Problema": problema,
            }
        )
    return pd.DataFrame(linhas, columns=colunas)


def find_invalid_cpfs(residents: list[Resident]) -> pd.DataFrame:
    """Moradores da planilha com CPF inválido ou repetido."""
    contagem: dict[str, int] = {}
    for r in residents:
        contagem[r.cpf_cnpj] = contagem.get(r.cpf_cnpj, 0) + 1
    linhas = []
    for r in residents:
        problemas = []
        if not cpf_valido(r.cpf_cnpj):
            problemas.append("CPF inválido")
        if contagem[r.cpf_cnpj] > 1:
            problemas.append("CPF repetido")
        if problemas:
            linhas.append(
                {
                    "Nome": r.name,
                    "Apartamento": r.apartment,
                    "CPF": r.cpf_cnpj,
                    "Problema": ", ".join(problemas),
                }
            )
    return pd.DataFrame(linhas, columns=["Nome", "Apartamento", "CPF", "Problema"])


def fetch_groups(residents: list[Resident]) -> list[Group]:
    logger.info("fetch_groups() chamada")
    groups: list[Group] = []
    for resident in residents:
        # O validator de Resident converte apartamento vazio em "-": cada
        # morador sem apartamento precisa de um grupo só dele
        if resident.apartment in (None, "-"):
            groups.append(
                Group(
                    apartment=None,  # type: ignore
                    leader=resident,
                    residents=[resident],
                )
            )
            continue
        achado: bool = False
        for group in groups:
            if group.apartment == resident.apartment:
                group.residents.append(resident)
                achado = True
                break
        if not achado:
            groups.append(
                Group(
                    apartment=resident.apartment,  # type: ignore
                    leader=resident,
                    residents=[resident],
                )
            )
    return groups


def get_group_by_cpf(cpf: str, groups: list[Group]) -> Group | None:
    for group in groups:
        for resident in group.residents:
            if cpf == resident.cpf_cnpj:
                return group
    logger.info(f"Falha ao encontrar grupo para {cpf}")


def get_group_apartment(group: Group | None) -> str | None:
    if group:
        return group.apartment
    return "-"


def get_ap_by_cpf(cpf: str, residents: list[Resident]) -> str | None:
    for resident in residents:
        if cpf == resident.cpf_cnpj:
            return resident.apartment
    return "-"


def boletos_to_df(
    boletos: list[tuple[str, ...]], residents: list[Resident]
) -> pd.DataFrame:
    df = pd.DataFrame(
        [
            {
                "Id": boleto[0],
                "Nome": boleto[1],
                "Apartamento": get_ap_by_cpf(boleto[7], residents) or "-",
                "Criado em": boleto[2],
                "Vencimento": boleto[3],
                "value": boleto[4],
                "netValue": boleto[5],
                "Status": boleto[6],
                "CPF": boleto[7],
                "Boleto": boleto[8],
                "PIX": boleto[9],
            }
            for boleto in boletos
        ]
    ).fillna("-")
    return df
