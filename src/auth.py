import streamlit as st
import streamlit_authenticator as stauth
from db_supabase import get_conn, init_db, get_user_by_username  # <-- 🔥 adicionar aqui
import bcrypt


def login_box(conn):
    """Caixa de login com verificação de senha e retorno de dicionário completo do usuário."""
    st.subheader("Login de Acesso")

    username = st.text_input("Usuário")
    password = st.text_input("Senha", type="password")

    if st.button("Entrar"):
        user_row = get_user_by_username(conn, username)
        if not user_row:
            st.error("Usuário não encontrado.")
            return None

        # user_row = (id, username, name, email, pwd_hash, role, is_active, store_id)
        id_, uname, name, email, pwd_hash, role, is_active, store_id = user_row

        if not is_active:
            st.error("Usuário desativado.")
            return None

        if bcrypt.checkpw(password.encode("utf-8"), pwd_hash.encode("utf-8")):
            # ✅ retorna o user completo com o store_id
            st.session_state["user"] = {
                "id": id_,
                "username": uname,
                "name": name,
                "email": email,
                "role": role,
                "store_id": store_id,
            }
            st.success(f"Bem-vindo, {name} 👋")
            st.rerun()
        else:
            st.error("Senha incorreta.")
            return None

    # Se já estiver logado
    if "user" in st.session_state:
        return st.session_state["user"]

    return None
