"""Telco support demo UI: pick a user (mimics login), then chat with the support agent."""
import itertools
import uuid

import streamlit as st

from agent_client import send_message

# Demo users: clicking one mimics that customer logging in (a guest is not logged in)
USERS = {
    "guest": {"label": "Guest (not logged in)", "account_id": None},
    "alex": {"label": "Alex · ACC-1001", "account_id": "ACC-1001"},
    "jamie": {"label": "Jamie · ACC-1002", "account_id": "ACC-1002"},
}

st.set_page_config(page_title="Telco Support Demo", page_icon="💬")


def start_session(user_id: str) -> None:
    """Switch user: a new session id and an empty chat, so users never share history."""
    st.session_state.user_id = user_id
    st.session_state.session_id = str(uuid.uuid4())  # 36 chars; AgentCore needs 33+
    st.session_state.messages = []


if "user_id" not in st.session_state:
    start_session("guest")

# --- Sidebar: choose who is chatting ---
with st.sidebar:
    st.header("Sign in as")
    for user_id, user in USERS.items():
        is_current = user_id == st.session_state.user_id
        if st.button(user["label"], key=f"user_{user_id}", use_container_width=True,
                     type="primary" if is_current else "secondary"):
            start_session(user_id)
            st.rerun()

    st.divider()
    current = USERS[st.session_state.user_id]
    st.caption(f"Account: {current['account_id'] or 'none (guest)'}")
    st.caption(f"Session: {st.session_state.session_id}")
    if st.button("New conversation", use_container_width=True):
        start_session(st.session_state.user_id)
        st.rerun()

# --- Chat ---
st.title("Telco Support")
st.caption(f"Chatting as **{current['label']}**")

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if prompt := st.chat_input("Ask about plans, bills or your account…"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        stream = send_message(
            account_id=current["account_id"],
            session_id=st.session_state.session_id,
            prompt=prompt,
        )
        # Spinner while the agent thinks and calls tools; it disappears when the first words arrive
        with st.spinner("Checking…"):
            first_chunk = next(stream, "")
        reply = st.write_stream(itertools.chain([first_chunk], stream))

    st.session_state.messages.append({"role": "assistant", "content": reply})
