import hashlib
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from resume_studio.llm import PROVIDERS, LLMClient, ModelConfig
from resume_studio.parsing import InputError, parse_job, parse_resume
from resume_studio.pipeline import run_pipeline
from resume_studio.review_ui import interactive_review
from resume_studio.revisions import export_saved_comparison
from resume_studio.workspace import load_result

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
st.write(
    "让真实经历回应职位需求。上传 Word 简历与职位 JSON，查看修改依据与待核实内容。支持 OpenAI、Gemini、Claude 和 DeepSeek。"
)

source_mode = st.radio("输入方式", ["上传文件", "使用本地样本", "继续已有结果"], horizontal=True)
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
elif source_mode == "使用本地样本":
    resume_files = sorted((ROOT / ".data").glob("*.docx"))
    job_files = sorted((ROOT / ".data").rglob("*.json"))
    if resume_files and job_files:
        chosen_resume = st.selectbox("基础简历", resume_files, format_func=lambda p: p.name)
        chosen_job = st.selectbox("职位样本", job_files, format_func=lambda p: p.parent.name)
        resume_bytes, job_bytes = chosen_resume.read_bytes(), chosen_job.read_bytes()
    else:
        st.info("没有找到本地样本，请上传文件。")

if source_mode == "继续已有结果":
    saved = sorted((ROOT / "outputs").glob("*/result.json"), reverse=True)
    if saved:
        chosen = st.selectbox("已保存版本", saved, format_func=lambda p: p.parent.name)
        if st.session_state.get("opened_result") != str(chosen):
            try:
                st.session_state["result"] = load_result(chosen.parent)
                st.session_state["opened_result"] = str(chosen)
            except (ValueError, OSError) as exc:
                st.session_state.pop("result", None)
                st.error(f"无法打开此版本：{exc}")
        active = st.session_state.get("result")
        if active:
            resume_bytes = (Path(active.output_dir) / "source_resume.docx").read_bytes()
            job_bytes = (Path(active.output_dir) / "job.json").read_bytes()
    else:
        st.info("还没有已保存的结果。请先上传文件生成简历。")
        st.session_state.pop("result", None)
else:
    st.session_state.pop("opened_result", None)
    fingerprint = hashlib.sha256((resume_bytes or b"") + (job_bytes or b"")).hexdigest()
    if (
        st.session_state.get("input_fingerprint") != fingerprint
        or st.session_state.get("last_source_mode") == "继续已有结果"
    ):
        st.session_state.pop("result", None)
        st.session_state["input_fingerprint"] = fingerprint
st.session_state["last_source_mode"] = source_mode

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

if st.button(
    "生成调整简历", type="primary", disabled=not (resume and job) or source_mode == "继续已有结果"
):
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
    m1.metric("已保存版本 · 待核实", len(pending))
    m2.metric("已处理 / 回退", len(result.risks) - len(pending))
    m3.metric("保留的改动", sum(b.text != result.final_text[b.id] for b in resume.blocks))
    if not result.audit_complete:
        st.error("全文事实检查未完成。当前文档仅供审阅；不会提供无标注版。")
        st.write(result.audit_error)
    elif pending:
        st.warning("有待核实内容。Word 审阅版已添加高亮与批注，请先处理疑点。")
    else:
        st.success("已保存版本的事实审校已完成，未发现未解决的具体疑点。请核对最终文档。")
    if flash := st.session_state.pop("review_flash", None):
        (st.success if result.audit_complete else st.warning)(flash)
    st.subheader("交互审阅")
    try:
        dirty = interactive_review(result, lambda: LLMClient(ModelConfig(provider, model, secret)))
    except (ValueError, RuntimeError, OSError) as exc:
        st.error(f"交互审阅暂不可用：{exc}")
        dirty = True
    path = Path(result.output_dir)
    st.caption(f"当前版本 {result.run_id} · 模型 {result.provider} / {result.model}")
    if dirty:
        st.warning(
            "当前有尚未全文审校的草稿。请在预览中点击“审校并生成新版本”，完成后下载与预览一致的文件。"
        )
    else:
        st.caption("下方文件对应已审校并保存的版本。编辑框中尚未保存的输入，请先审校生成新版本。")
        st.info(result.layout["message"])
        d0, d1, d2, d3 = st.columns(4)
        comparison_path = path / "resume_comparison.docx"
        try:
            if not comparison_path.exists():
                export_saved_comparison(path)
            d0.download_button(
                "下载 Word 修订对照版",
                comparison_path.read_bytes(),
                file_name="resume_comparison.docx",
            )
        except (ValueError, RuntimeError, OSError) as exc:
            d0.error(f"修订对照暂不可用：{exc}")
        d1.download_button(
            "下载带批注审阅版",
            (path / "resume_review.docx").read_bytes(),
            file_name="resume_review.docx",
        )
        d2.download_button(
            "下载修改说明", (path / "comments.md").read_bytes(), file_name="comments.md"
        )
        if result.clean_available:
            d3.download_button(
                "下载无标注简历",
                (path / "tailored_resume.docx").read_bytes(),
                file_name="tailored_resume.docx",
            )
    with st.expander("已保存版本的完整审校记录与修改说明"):
        st.markdown((path / "comments.md").read_text(encoding="utf-8"))
        st.json(result.usage)
