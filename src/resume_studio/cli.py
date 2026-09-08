import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from .llm import PROVIDERS, LLMClient, ModelConfig
from .parsing import parse_job, parse_resume
from .pipeline import run_pipeline


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="本地简历调整与风险审阅")
    parser.add_argument("resume", type=Path)
    parser.add_argument("job", type=Path)
    parser.add_argument(
        "--provider", choices=PROVIDERS, default=os.getenv("RESUME_PROVIDER", "openai")
    )
    parser.add_argument("--model", help="所选服务中支持结构化输出的模型 ID")
    parser.add_argument("--output", default="outputs")
    parser.add_argument("--target-pages", type=int, choices=range(1, 6), default=2)
    parser.add_argument("--inspect", action="store_true", help="仅解析输入，不调用 API")
    args = parser.parse_args()
    try:
        rb, jb = args.resume.read_bytes(), args.job.read_bytes()
        if args.inspect:
            resume, job = parse_resume(rb), parse_job(jb)
            print(
                f"{job.company} — {job.title}\n{len(resume.blocks)} 文本块，"
                f"{sum(b.editable for b in resume.blocks)} 可改写块，{len(job.requirements)} 职位要求"
            )
            return
        _, key_env, model_env, _ = PROVIDERS[args.provider]
        client = LLMClient(
            ModelConfig(
                args.provider, args.model or os.getenv(model_env, ""), os.getenv(key_env, "")
            )
        )
        result = run_pipeline(
            rb, jb, client, args.output, progress=print, target_pages=args.target_pages
        )
        print(result.output_dir)
    except (ValueError, RuntimeError, OSError) as exc:
        parser.exit(1, f"操作未完成：{exc}\n")


if __name__ == "__main__":
    main()
