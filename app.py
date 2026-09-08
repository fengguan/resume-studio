import hashlib
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from resume_studio.llm import PROVIDERS, LLMClient, ModelConfig
from resume_studio.parsing import InputError, parse_job, parse_resume
from resume_studio.pipeline import run_pipeline
from resume_studio.revisions import export_saved_comparison

load_dotenv()
ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="Resume Studio", page_icon="📄", layout="wide")


def client_config():
    with st.sidebar:
        st.header("模型设置")
        default = os.getenv("RESUME_PROVIDER", "openai")
        provider = st.selectbox(
            "AI 服务",
            list(PROVIDERS),
            index=list(PROVIDERS).index(default) if default in PROVIDERS else 0,
            format_func=lambda p: PROVIDERS[p][0],
        )
        _, key_env, model_env, _ = PROVIDERS[provider]
        model = st.text_input(
            "模型 ID",
            value=os.getenv(model_env, ""),
            key=f"model_{provider}",
            help="填写你的 API 账户可用且支持结构化 JSON 输出的模型 ID。",
        )
        secret = st.text_input(
            "API 密钥",
            type="password",
            key=f"key_{provider}",
            placeholder=f"或在 .env 配置 {key_env}",
        )
        if os.getenv(key_env):
            st.caption(f"已检测到 {key_env}；上方留空即可使用。")
        pages = st.number_input("目标页数", min_value=1, max_value=5, value=2)
        st.caption("简历保持输入语言；修改说明为中文。")
        st.divider()
        st.caption(
            "界面与文件保存在本机。生成会将必要的简历正文、职位和补充事实发送到所选 AI 服务；身份/联系信息块在本地保留。密钥不写入运行文件。"
        )
        return provider, model, secret or os.getenv(key_env, ""), pages


provider, model, secret, pages = client_config()
st.title("Resume Studio")
st.write("让真实经历回应职位需求。上传 Word 简历与职位 JSON，查看修改依据与待核实内容。")

source_mode = st.radio("输入方式", ["上传文件", "使用本地样本"], horizontal=True)
resume_bytes, job_bytes = None, None
if source_mode == "上传文件":
    left, right = st.columns(2)
    with left:
        resume_file = st.file_uploader(
            "原始简历", type=["docx"], help="支持普通段落与表格，最大 10 MB。"
        )
    with right:
        job_file = st.file_uploader("职位描述", type=["json"])
    if resume_file and job_file:
        resume_bytes, job_bytes = resume_file.getvalue(), job_file.getvalue()
else:
    resume_files = sorted((ROOT / ".data").glob("*.docx"))
    job_files = sorted((ROOT / ".data").rglob("*.json"))
    if resume_files and job_files:
        chosen_resume = st.selectbox("基础简历", resume_files, format_func=lambda p: p.name)
        chosen_job = st.selectbox("职位样本", job_files, format_func=lambda p: p.parent.name)
        resume_bytes, job_bytes = chosen_resume.read_bytes(), chosen_job.read_bytes()
    else:
        st.info("没有找到本地样本，请上传文件。")

fingerprint = hashlib.sha256((resume_bytes or b"") + (job_bytes or b"")).hexdigest()
if st.session_state.get("input_fingerprint") != fingerprint:
    st.session_state.pop("result", None)
    st.session_state["input_fingerprint"] = fingerprint

resume, job = None, None
if resume_bytes and job_bytes:
    try:
        resume, job = parse_resume(resume_bytes), parse_job(job_bytes)
        st.subheader(f"{job.company} · {job.title}")
        st.caption(
            f"已识别 {len(resume.blocks)} 个简历文本块、{len(job.requirements)} 条职位要求。"
        )
        with st.expander("检查输入与可编辑范围"):
            st.write(job.summary)
            for b in resume.blocks:
                st.text(f"{b.id} · {'允许改写' if b.editable else '保留原文'} · {b.text}")
    except InputError as exc:
        st.error(str(exc))

if st.button("生成调整简历", type="primary", disabled=not (resume and job)):
    try:
        client = LLMClient(ModelConfig(provider, model, secret))
        with st.status("正在处理…", expanded=True) as status:
            result = run_pipeline(
                resume_bytes,
                job_bytes,
                client,
                ROOT / "outputs",
                progress=st.write,
                target_pages=pages,
            )
            st.session_state["result"] = result
            status.update(label="生成完成，请审阅结果", state="complete", expanded=False)
    except (ValueError, RuntimeError, OSError) as exc:
        st.error(f"本次未完成：{exc}")
        st.caption("已有成功结果保持不变。可以修正配置后重试。")

result = st.session_state.get("result")
if result is not None and resume is not None:
    st.divider()
    pending = [r for r in result.risks if r.status == "pending"]
    m1, m2, m3 = st.columns(3)
    m1.metric("待核实", len(pending))
    m2.metric("已处理 / 回退", len(result.risks) - len(pending))
    m3.metric("保留的改动", sum(b.text != result.final_text[b.id] for b in resume.blocks))
    if not result.audit_complete:
        st.error("全文事实检查未完成。当前文档仅供审阅；不会提供无标注版。")
        st.write(result.audit_error)
    elif pending:
        st.warning("有待核实内容。Word 审阅版已添加高亮与批注，请先处理疑点。")
    else:
        st.success("事实审校已完成，未发现未解决的具体疑点。请核对最终文档。")
    st.info(result.layout["message"])
    path = Path(result.output_dir)
    d0, d1, d2, d3 = st.columns(4)
    comparison_path = path / "resume_comparison.docx"
    try:
        if not comparison_path.exists():
            export_saved_comparison(path)
        d0.download_button(
            "下载 Word 修订对照版",
            comparison_path.read_bytes(),
            file_name="resume_comparison.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    except (ValueError, RuntimeError, OSError) as exc:
        d0.error(f"修订对照暂不可用：{exc}")
    d1.download_button(
        "下载带批注审阅版",
        (path / "resume_review.docx").read_bytes(),
        file_name="resume_review.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    d2.download_button(
        "下载修改说明",
        (path / "comments.md").read_bytes(),
        file_name="comments.md",
        mime="text/markdown",
    )
    if result.clean_available:
        d3.download_button(
            "下载无标注简历",
            (path / "tailored_resume.docx").read_bytes(),
            file_name="tailored_resume.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    st.caption(f"已保存在 {path} · 本次模型 {result.provider} / {result.model}")

    risks_tab, changes_tab, report_tab = st.tabs(["疑点与处理", "改写对照", "完整说明"])
    with risks_tab:
        if not result.risks:
            st.write("本次未发现具体事实风险。")
        originals = {b.id: b.text for b in resume.blocks}
        for risk in sorted(result.risks, key=lambda r: r.status != "pending"):
            label = {
                "pending": "待核实",
                "reverted": "已拦截/回退",
                "resolved": "已处理并复核",
                "deleted": "已删除",
            }[risk.status]
            with st.expander(
                f"{risk.id} · {label} · {risk.reason}", expanded=risk.status == "pending"
            ):
                st.caption(
                    f"{risk.block_id} · {'原文已有疑点' if risk.origin == 'source' else '生成与审校'}"
                )
                st.text("问题表述：" + risk.quote)
                st.text("原文依据：" + risk.source_text)
                st.write("建议：" + risk.suggestion)
                st.text("当前正文：" + (result.final_text[risk.block_id] or "（已删除）"))
        if pending:
            st.subheader("处理待核实内容")
            st.caption(
                "每次处理一个段落，随后重新分析与审校全文；新结果单独保存。恢复原文不会自动消除原文本身的疑点。"
            )
            pending_blocks = list(
                dict.fromkeys(r.block_id for r in pending if r.origin != "system")
            )
            with st.form(f"resolve_{result.run_id}"):
                if pending_blocks:
                    block_id = st.selectbox(
                        "待核实段落",
                        pending_blocks,
                        format_func=lambda key: (result.final_text[key] or originals[key])[:100],
                    )
                    action = st.selectbox(
                        "处理方式", ["恢复原文", "删除该段", "填写修订正文并补充事实"]
                    )
                    edited = st.text_area("修订正文（仅第三种方式需要；保持单段文字）")
                    facts = st.text_area(
                        "补充事实（实际经历、指标定义或证书状态；仅第三种方式需要）"
                    )
                else:
                    block_id, action, edited, facts = None, None, "", ""
                submitted = st.form_submit_button(
                    "保存处理并重新审校" if pending_blocks else "重试全文审校"
                )
            if submitted:
                try:
                    edits, supplements = {}, {}
                    if block_id:
                        if action == "恢复原文":
                            edits[block_id] = originals[block_id]
                        elif action == "删除该段":
                            edits[block_id] = ""
                        else:
                            if not edited.strip() or not facts.strip():
                                raise ValueError("请填写修订正文和具体补充事实。")
                            edits[block_id], supplements[block_id] = edited.strip(), facts.strip()
                    client = LLMClient(ModelConfig(provider, model, secret))
                    with st.status("正在重新审校…", expanded=True):
                        revised = run_pipeline(
                            resume_bytes,
                            job_bytes,
                            client,
                            ROOT / "outputs",
                            progress=st.write,
                            target_pages=pages,
                            previous=result,
                            edits=edits,
                            supplements=supplements,
                        )
                    st.session_state["result"] = revised
                    st.rerun()
                except (ValueError, RuntimeError, OSError) as exc:
                    st.error(f"处理未完成：{exc}")
    with changes_tab:
        st.info(
            "想在原位置查看修改？下载上方的 Word 修订对照版，在 Word 的“审阅”中选择“所有标记”，"
            "即可查看新增与删除，并逐处接受或拒绝。风险提示仍保留为批注；在 Word 中接受修订不会自动解决应用中的待核实项。"
        )
        for block in resume.blocks:
            final = result.final_text[block.id]
            if final != block.text:
                with st.expander(block.id, expanded=True):
                    before, after = st.columns(2)
                    before.caption("原文")
                    before.text(block.text)
                    after.caption("当前改写")
                    after.text(final or "（已删除）")
        with st.expander("调用用量"):
            st.json(result.usage)
    with report_tab:
        st.markdown((path / "comments.md").read_text(encoding="utf-8"))
