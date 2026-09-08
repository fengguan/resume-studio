"""Streamlit bridge for the local document editor."""

from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from .preview import preview_payload
from .schemas import ReviewEvent
from .workspace import ReviewWorkspace

_component = components.declare_component(
    "resume_review", path=str(Path(__file__).parent / "review_component")
)


def interactive_review(result, client_factory):
    workspace = ReviewWorkspace(result)
    state = workspace.load()
    key = "review_ack_" + result.run_id
    ack = st.session_state.get(key)
    value = _component(
        payload=preview_payload(workspace, state),
        ack=ack,
        key="review_" + result.run_id,
        default=None,
    )
    if value and (not ack or value.get("id") != ack.get("id")):
        event = None
        try:
            event = ReviewEvent.model_validate(value)
            if event.action in ("save", "undo", "restore"):
                state = workspace.local(event)
            else:
                client = client_factory()
                with st.spinner("正在处理，请保留此页面…"):
                    if event.action == "ask":
                        state = workspace.ask(event, client)
                    else:
                        revised = workspace.publish(event, client)
                        if revised:
                            st.session_state["result"] = revised
                            st.session_state["review_flash"] = (
                                "已审校并保存新版本。预览与下载文件现已同步；审校回退会显示在对应段落的对话中。"
                                if revised.audit_complete
                                else "审阅版本已保存，但全文事实审校未完成。请查看错误并重试；不提供无标注版。"
                            )
                            st.rerun()
                        state = workspace.load()
            receipt = state.requests.get(
                event.id, {"status": "failed", "message": "未找到请求结果。"}
            )
            # A request interrupted by process shutdown is not repeated automatically.
            if receipt["status"] == "running":
                receipt = {
                    "status": "failed",
                    "message": "此请求已提交但尚未完成。草稿已保留；如处理已中断，可重新发送。",
                }
            st.session_state[key] = {"id": event.id, **receipt}
        except (ValueError, RuntimeError, OSError) as exc:
            st.session_state[key] = {"id": value.get("id"), "status": "failed", "message": str(exc)}
        st.rerun()
    return workspace.dirty(state)
