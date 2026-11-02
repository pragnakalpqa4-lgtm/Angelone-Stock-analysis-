"""Web dashboard for control and the latest Angel One quote."""
from datetime import datetime
import streamlit as st
from runtime_state import REPORT_FILE, is_enabled, read_json, set_enabled

st.set_page_config(page_title="Angelone Stock Analysis", page_icon="📈", layout="wide")
st.title("Angelone Stock Analysis")
st.caption("Live quote monitor and analysis system control")
enabled = is_enabled()
left, right = st.columns([1, 3])
with left:
    st.subheader("System control")
    st.success("RUNNING") if enabled else st.warning("STOPPED")
    if enabled and st.button("Stop analysis", type="primary", use_container_width=True):
        set_enabled(False)
        st.rerun()
    if not enabled and st.button("Start analysis", type="primary", use_container_width=True):
        set_enabled(True)
        st.rerun()
    if st.button("Refresh", use_container_width=True):
        st.rerun()

report = read_json(REPORT_FILE, {})
with right:
    st.subheader("Latest data")
    if not report:
        st.info("Worker has not produced a report yet.")
    elif report.get("status") == "error":
        st.error(report.get("error", "Unknown data error"))
    elif report.get("status") == "stopped":
        st.warning("Analysis is stopped from the dashboard switch.")
    else:
        a, b, c, d = st.columns(4)
        a.metric("Symbol", report.get("symbol", "—"))
        b.metric("Last price", report.get("last_price", "—"))
        c.metric("Day high", report.get("high", "—"))
        d.metric("Day low", report.get("low", "—"))
    checked = report.get("checked_at")
    if checked:
        try:
            checked = datetime.fromisoformat(checked).astimezone().strftime("%d %b %Y, %I:%M:%S %p %Z")
        except ValueError:
            pass
        st.caption(f"Last checked: {checked}")
st.divider()
st.write("Raw status report")
st.json(report or {"status": "waiting"})
