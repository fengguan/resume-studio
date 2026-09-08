from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_page_loads_and_switches_providers():
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
    assert not app.exception
    assert app.title[0].value == "Resume Studio"
    provider = next(s for s in app.selectbox if s.label == "AI 服务")
    provider.select("gemini").run()
    assert not app.exception
    next(s for s in app.selectbox if s.label == "AI 服务").select("claude").run()
    assert not app.exception


def test_samples_parse_without_api_key():
    root = Path(__file__).resolve().parents[1]
    if not (root / ".data").exists():
        return
    app = AppTest.from_file(str(root / "app.py")).run()
    app.radio[0].set_value("使用本地样本").run()
    assert not app.exception
    assert not next(b for b in app.button if b.label == "生成调整简历").disabled
    assert any("已识别" in c.value for c in app.caption)


def test_result_and_resolution_in_page(monkeypatch, tmp_path, fake_model):
    import resume_studio.llm
    import resume_studio.pipeline

    root = Path(__file__).resolve().parents[1]
    if not (root / ".data").exists():
        return
    real_pipeline = resume_studio.pipeline.run_pipeline

    def local_pipeline(rb, jb, client, output_root, **kwargs):
        return real_pipeline(rb, jb, client, tmp_path, **kwargs)

    monkeypatch.setattr(resume_studio.pipeline, "run_pipeline", local_pipeline)
    monkeypatch.setattr(resume_studio.llm, "LLMClient", lambda cfg: fake_model())
    app = AppTest.from_file(str(root / "app.py")).run()
    app.radio[0].set_value("使用本地样本").run()
    next(x for x in app.text_input if x.label == "模型 ID").set_value("TEST-DOUBLE")
    next(x for x in app.text_input if x.label == "API 密钥").set_value("test-secret")
    next(b for b in app.button if b.label == "生成调整简历").click().run(timeout=30)
    assert not app.exception
    assert app.session_state["result"].clean_available is False
    assert any(b.label == "下载 Word 修订对照版" for b in app.get("download_button"))
    assert app.warning
    next(x for x in app.selectbox if x.label == "处理方式").select("删除该段")
    next(b for b in app.button if b.label == "保存处理并重新审校").click().run(timeout=30)
    assert not app.exception
    assert app.session_state["result"].clean_available is True
    assert app.success
