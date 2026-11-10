import streamlit as st
from src.state import ensure_state

st.set_page_config(page_title="LanderITA", page_icon="💸", layout="wide")

ensure_state()

st.title("💸 LavanderITA")
st.caption("Automação da cobrança da LavanderITA")
