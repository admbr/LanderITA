import gspread
from google.oauth2.service_account import Credentials
from models.clients import *
from src.config import settings
from gspread.utils import ValueRenderOption
import pandas as pd
from pathlib import Path
from loguru import logger
import re
import os
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    settings.scope_spreadsheets,
    settings.scope_drive,
]


def _apartment_conversion(ap: str) -> str | None:
    if ap.strip() == "-":
        return None
    templist = list(re.sub(r"/[^a-z0-9]/i", "", ap))
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
                    name=row["ALUNOS"],  # type: ignore
                    cpf_cnpj=str(row.get("CPF")) if row.get("CPF") is not None else "-",  # type: ignore
                    email=row.get("EMAIL") or None,  # type: ignore
                    phone=(
                        str(row.get("CELULAR"))
                        if row.get("CELULAR") is not None
                        else None
                    ),
                    apartment=row.get("ALOJ APTO"),  # type: ignore
                    turma=str(row["TURMA"]),
                )
            )
        except Exception as e:
            # logger.warning(f"Linha ignorada: {row} — {e}")
            continue

    return residents


def fetch_residents_value(
    residents: list[Resident], path_csv: Path
) -> dict[Resident, float]:
    relacao: dict[Resident, float] = {}
    logger.info("fetch_apartment_values() chamada")
    try:
        planilha = pd.read_csv(path_csv)
        planilha["Documento"] = planilha["Documento"].apply(
            lambda x: re.sub(r"\D", "", str(x))
        )
    except:
        logger.error(f"{path_csv} pode ser lido com .csv")
    try:
        for resident in residents:
            divida: float = (
                planilha[planilha["Documento"] == resident.cpf_cnpj].iloc[:, 7].max()
                * 3.5
            )
            relacao[resident] = divida
        return relacao
    except Exception as e:
        logger.error(
            f"Erro na criação de da relacao da divida dos moradores (fetch_resident_value): {e}"
        )

    return {}


def fetch_groups(residents: list[Resident]) -> list[Group]:
    logger.info("fetch_groups() chamada")
    groups: list[Group] = []
    for resident in residents:
        if not resident.apartment:
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
