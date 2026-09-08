# Resume Studio

本地运行的简历调整应用。输入原始 DOCX 和职位 JSON，使用 OpenAI、Gemini 或 Claude 改写，再通过规则检查与独立模型审校生成 Word 审阅版及中文修改说明。

**事实准确优先：明确违规会回退，疑似问题会在 Word 中高亮并添加批注。存在待核实问题或事实审校未完成时，不提供无标注简历。**

## 启动

需要 Python 3.10 或更新版本。当前工作区已创建 `.venv` 并安装依赖，可直接运行：

```bash
./run.sh
```

浏览器打开 <http://127.0.0.1:8501>。页面可上传文件，也可直接选择 `.data` 中的基础简历和五个职位样本。

在新环境安装：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
./run.sh
```

如果 Linux/WSL 缺少 `ensurepip`，安装对应的 `python3-venv` 系统包，或使用已安装的 `python3 -m virtualenv .venv`。Windows 可使用 `.venv\Scripts\python -m streamlit run app.py --server.address 127.0.0.1`。

## 配置 OpenAI / Gemini / Claude

在侧栏选择服务、填写模型 ID 和 API 密钥，或者复制 `.env.example` 为 `.env`：

```dotenv
RESUME_PROVIDER=openai
OPENAI_API_KEY=你的密钥
OPENAI_MODEL=你的账户可用的模型ID
GEMINI_API_KEY=你的密钥
GEMINI_MODEL=你的账户可用的模型ID
ANTHROPIC_API_KEY=你的密钥
ANTHROPIC_MODEL=你的账户可用的模型ID
```

只需填写准备使用的服务。模型须支持对应服务的结构化 JSON 输出；不硬编码模型名称或默默切换服务。页面填写的密钥仅存在当前进程/会话中，不写入运行产物；环境变量优先于 `.env`。

三家使用各自原生接口，非模拟的统一兼容接口：

| 服务 | 接口与输出约束 | 官方文档 |
| --- | --- | --- |
| OpenAI | `POST /v1/responses`，`text.format` JSON schema，`store=false` | [Structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs) |
| Gemini | `POST /v1beta/models/{model}:generateContent`，`responseMimeType` / `responseJsonSchema` | [Generate content API](https://ai.google.dev/api/generate-content) |
| Claude | `POST /v1/messages`，`output_config.format` JSON schema | [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) |

普通运行包括职位分析、改写和全文审校。发现违规并回退后会追加一次审校。请求超时/限流最多尝试三次，非法结构化输出最多重新请求一次；截断、拒绝或审校覆盖不完整都不会标记为通过。API 调用可能产生费用，实际 token 用量保存于运行记录中。

应用与文件在本地；生成时必要的正文、职位要求和用户补充事实会发送给所选服务。自动识别的姓名、电话、邮箱、LinkedIn 所在块会保留在本地；城市/地区用于检查工作地点要求。该识别不等于通用个人信息脱敏，请根据实际输入内容决定是否调用云端。

## 审阅与处理疑点

1. 上传原始 DOCX 与职位 JSON，展开输入预览确认职位及文本没有选错。
2. 点击“生成调整简历”，等待分析、改写、审校和文件检查。
3. 查看页面顶部的待核实/已处理数量。想在原简历的位置查看改写，下载 **Word 修订对照版**；想集中核实风险，下载带高亮的 Word 审阅版。两份文件均保留风险批注。
4. 在“疑点与处理”中选择文本块，恢复原文、删除该段，或填写修订正文和具体事实，再重新审校。仅接受措辞不等于核实新增经历。
5. 全部具体事实风险处理完成且审校完成后，提供无标注版。分页检查状态单独显示，仍应打开 Word 确认排版。

原简历已有疑点也会提示。例如样本中的证书仍为 `in progress; expected Aug 2026`；日期已过并不能证明已取得。恢复原文无法消除此类疑点，应补充实际状态或删除过期表述。

模型审校不是外部背景调查，也不能保证识别所有语义问题。未找到证据不代表经历虚假，只代表当前材料不足。程序不把职位关键词、外部匹配分数或已有 AI 定制样本当候选人事实来源。

## 在 Word 原位置查看改写

点击“下载 Word 修订对照版”，打开 `resume_comparison.docx`，在 Word 的 **审阅 → 显示以供审阅 → 所有标记** 中查看修改。使用“上一处／下一处”定位改动，使用“接受／拒绝”处理单处或全部修订。删除和新增使用 Word 原生修订标记，具体颜色与行内/气球显示取决于 Word 设置。[Microsoft：Word 修订功能](https://support.microsoft.com/en-us/word/training/track-changes-in-word)

对照以本次原始简历为基线、以最终实际保留的正文为结果，按词标记差异，保留原段落、公司/学校表格和未改动的上下文。已经被拦截、回退的错误只出现在处理批注中，不会作为新增修订重新加入。整段删除使用删除修订，接受后保留空段落以维持原布局。多轮处理后的对照仍以最初上传的原简历为基线。

生成时会验证“接受全部修订”的正文等于最终改写，“拒绝全部修订”的正文等于原简历。对照版中的风险批注独立保留；在 Word 中接受修订，不等于已核实事实，也不会自动同步应用中的风险状态。存在待核实项时，对照版仍是审阅文件。页眉/页脚的风险批注会挂在正文开头，并注明实际位置。

对照生成完全在本地执行，不新增 AI 调用。已打开的旧运行结果会自动补出对照下载；也可为磁盘上的旧结果执行：

```bash
.venv/bin/python -m resume_studio.revisions outputs/YOUR_RUN_ID
```

当前只记录文本的插入与删除，不记录格式修订或段落移动。修改后的复杂链接/域/书签段落无法安全导出时会明确提示。比较文档未在 Microsoft Word 桌面版进行视觉验收；自动验证覆盖修订结构、两种正文视图和批注。再次作为输入前，请先在副本中接受/拒绝修订并删除批注。

## 输出

每次运行写入 `outputs/<run_id>/`，不覆盖原文件或上一次结果：

```text
resume_review.docx       带风险批注的审阅版
resume_comparison.docx   Word 原生修订对照版，原位置显示插入和删除；保留风险批注
tailored_resume.docx     仅事实审校通过且没有待核实项时存在
comments.md             中文建议、逐项修改、证据与风险记录
changes.json            最终实际改动
validation.json         风险状态与分页检查
result.json             完整运行结果
metadata.json           模型、版本、输入哈希、耗时与 token 用量
source_resume.docx      本次原始简历副本
job.json                本次职位输入
supplements.json        用户补充事实
```

分析、提议改动和审校结果也保存在同一目录。失败运行保存在 `.failed` 结尾的目录，不能当作完整交付物。`.data`、`outputs`、`.env` 已加入 `.gitignore`；这些本地文件含个人资料，可自行删除。页面刷新后当前交互状态可能消失，已下载/保存的运行文件不会丢失。

## 支持范围与当前限制

- 首版保留原布局，修改选定的 headline、summary、能力描述和经历段落；日期、身份信息、雇主、教育标题等默认锁定。当前不自动跨段重排、新增章节或重建标准模板。
- 按原顺序解析段落、表格、嵌套/合并单元格、页眉页脚。文本框、修订、内容控件、脚注/尾注、复杂多栏和已有批注会明确拒绝，请先整理输入副本。图片内容不做 OCR。
- 原段落样式、表格、分页和未修改的 run 格式保留；整体改写段落使用原段落基础字符样式，局部混合强调可能需要人工调整。
- 页眉/页脚中的疑点在原位置高亮，相关批注挂在审阅版末尾的定位提示上（Word 不支持在页眉/页脚直接挂批注）。这些提示不会进入无标注版。
- `job` 对象须含 `title`、`company`，以及 `responsibilities`、`skills` 或 `qualifications` 中至少一项文字数组。五份现有 JSON 均有解析回归覆盖，额外新闻与平台评分不参与候选人事实判断。
- 页数目标是检查阈值，当前不会为了压页自动缩小字号或循环改写。可选安装 LibreOffice（`libreoffice` / `soffice` 命令）进行 PDF 页数检查；缺失时明确显示“未验证分页”。PDF 检查不是视觉验收，也不保证所有招聘系统的解析效果。
- 全部风险必须能定位才会接受审校结果。当前同一段只输出一个最高优先级语义审校结论，规则检查可补充多项风险；处理后再次全文审校。
- 当前环境没有 API 密钥及 LibreOffice：已验证三家请求协议、异常处理、真实 DOCX 文件链路和页面；**尚未进行真实云端模型效果/费用比较，也未验证实际分页。**

## 命令行

仅解析，不调用 API：

```bash
.venv/bin/resume-studio resume.docx job.json --inspect
```

运行指定服务（密钥从环境变量或 `.env` 读取）：

```bash
.venv/bin/resume-studio resume.docx job.json --provider gemini --model YOUR_MODEL_ID
```

## 开发验证

```bash
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

`requirements.lock.txt` 记录本次完整验证环境的精确依赖版本（含开发工具）；需要复现时先安装该文件，再执行 `pip install --no-deps -e .`。

测试使用明确标识的模拟模型响应，不产生 API 费用。覆盖三家的协议、错误/截断、表格和日期解析、指标含义变化、技能新增、证书状态、风险高亮、回退后的复核、人工处理和 UI。私人样本不在场时，相关样本测试跳过，合成用例仍可运行。

更完整的产品设计见 [PLAN.md](PLAN.md)。后续优先使用真实 API 和新简历验证改写质量，再考虑模板、段落重排和速度优化。
