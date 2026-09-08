"""Opt-in browser regression: RESUME_BROWSER_TEST=1 pytest tests/test_browser_review.py."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RESUME_BROWSER_TEST") != "1", reason="opt-in Chromium test"
)


def test_interactive_document_in_browser(tmp_path):
    from playwright.sync_api import expect, sync_playwright

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = os.environ | {"RESUME_BROWSER_TEST_ROOT": str(tmp_path)}
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(Path(__file__).with_name("browser_review_app.py")),
            "--server.port",
            str(port),
            "--server.headless",
            "true",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(100):
            try:
                if httpx.get(url + "/_stcore/health").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1500, "height": 1180})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(url)
            frame = page.frame_locator('iframe[title="resume_studio.review_ui.resume_review"]')
            expect(frame.get_by_role("button", name="审校并生成新版本")).to_be_visible(
                timeout=30000
            )
            # Select summary from its location, type, verify live inline additions, save, and undo.
            frame.locator('[data-block="b002"]').click()
            old = frame.locator("#editor").input_value()
            revised = old.replace("with experience translating", "translating")
            frame.locator("#editor").fill(revised)
            expect(frame.locator('[data-block="b002"] del')).to_contain_text("with experience")
            frame.get_by_role("button", name="保存草稿", exact=True).click()
            expect(page.get_by_text("DIRTY", exact=True)).to_be_visible(timeout=15000)
            expect(frame.get_by_role("button", name="保存草稿", exact=True)).to_be_enabled()
            frame.get_by_role("button", name="撤销上次").click()
            expect(frame.locator("#editor")).to_have_value(old, timeout=15000)
            # Ask AI; new text is applied and conversation persists by block.
            frame.locator("#question").fill("保留原意，简洁一点")
            frame.get_by_role("button", name="发送给 AI").click()
            expect(frame.locator("#editor")).to_have_value(revised, timeout=15000)
            expect(frame.locator(".chat")).to_contain_text("已精简这段")
            frame.locator('[data-block="b005"]').click()
            expect(frame.locator(".chat")).not_to_contain_text("已精简这段")
            frame.locator('[data-block="b002"]').click()
            expect(frame.locator(".chat")).to_contain_text("已精简这段")
            # Unsupported AI skill is rejected and explanatory annotation remains.
            frame.locator("#question").fill("请加入 SQL")
            frame.get_by_role("button", name="发送给 AI").click()
            expect(frame.locator(".chat")).to_contain_text("这次建议未应用", timeout=15000)
            expect(frame.locator("#editor")).to_have_value(revised)
            expect(frame.get_by_text("未采用的 AI 建议", exact=True)).to_be_visible()
            # Reload restores saved draft and conversation; then publish current changes.
            page.reload()
            expect(frame.get_by_role("button", name="审校并生成新版本")).to_be_visible(
                timeout=30000
            )
            frame.locator('[data-block="b002"]').click()
            expect(frame.locator("#editor")).to_have_value(revised)
            expect(frame.locator(".chat")).to_contain_text("这次建议未应用")
            frame.get_by_role("button", name="审校并生成新版本").click()
            expect(page.get_by_text("SYNCED", exact=True)).to_be_visible(timeout=30000)
            with page.expect_download() as download:
                page.get_by_role("button", name="下载测试 Word").click()
            download.value.save_as(tmp_path / "review.docx")
            frame.locator('[data-block="b002"]').click()
            page.screenshot(path=str(tmp_path / "review.png"), full_page=True)
            assert not errors, errors
            from docx import Document

            exported = Document(str(tmp_path / "review.docx"))
            assert any(p.text == revised for p in exported.paragraphs)
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=15)
