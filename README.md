# Resume Studio

本地运行的简历调整应用。输入原始 DOCX 和职位 JSON，使用 OpenAI、Gemini、Claude 或 DeepSeek 改写，再通过规则检查与独立模型审校生成 Word 审阅版及中文修改说明。

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

## 配置 OpenAI / Gemini / Claude / DeepSeek

在侧栏选择服务、填写模型 ID 和 API 密钥，或者复制 `.env.example` 为 `.env`：

```dotenv
RESUME_PROVIDER=openai
OPENAI_API_KEY=你的密钥
OPENAI_MODEL=你的账户可用的模型ID
GEMINI_API_KEY=你的密钥
GEMINI_MODEL=你的账户可用的模型ID
ANTHROPIC_API_KEY=你的密钥
ANTHROPIC_MODEL=你的账户可用的模型ID
DEEPSEEK_API_KEY=你的密钥
DEEPSEEK_MODEL=你的账户可用的模型ID
```

只需填写准备使用的服务。模型须支持对应服务的结构化 JSON 输出；不硬编码模型名称或默默切换服务。页面填写的密钥仅存在当前进程/会话中，不写入运行产物；环境变量优先于 `.env`。

四家使用各自原生接口，非模拟的统一兼容接口：

| 服务 | 接口与输出约束 | 官方文档 |
| --- | --- | --- |
| OpenAI | `POST /v1/responses`，`text.format` JSON schema，`store=false` | [Structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs) |
| Gemini | `POST /v1beta/models/{model}:generateContent`，`responseMimeType` / `responseJsonSchema` | [Generate content API](https://ai.google.dev/api/generate-content) |
| Claude | `POST /v1/messages`，`output_config.format` JSON schema | [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) |
| DeepSeek | `POST /chat/completions`，`response_format: {"type":"json_object"}`，随后由本地 Pydantic 校验具体结构 | [JSON Output](https://api-docs.deepseek.com/guides/json_mode/) |

普通运行包括职位分析、改写和全文审校。发现违规并回退后会追加一次审校。请求超时/限流最多尝试三次，非法结构化输出最多重新请求一次；截断、拒绝或审校覆盖不完整都不会标记为通过。API 调用可能产生费用，实际 token 用量保存于运行记录中。

应用与文件在本地；生成时必要的正文、职位要求和用户补充事实会发送给所选服务。自动识别的姓名、电话、邮箱、LinkedIn 所在块会保留在本地；城市/地区用于检查工作地点要求。该识别不等于通用个人信息脱敏，请根据实际输入内容决定是否调用云端。

## 审阅与处理疑点

1. 上传原始 DOCX 与职位 JSON，展开输入预览确认职位及文本没有选错。
2. 点击“生成调整简历”，等待分析、改写、审校和文件检查。
3. 在 **交互审阅** 中按原文位置查看简历；切换“改写对照 / 当前正文 / 原始简历”。点击正文或上方疑点标签，右侧会定位到该段。
4. **自己改：**直接在正文编辑框修改，左侧立即显示差异。点击“保存草稿”后保存在本机，不调用 API；支持撤销上次修改和恢复原文。需要删除整段时，清空正文后保存。
5. **让 AI 继续改：**在同一段的对话框输入要求或问题。AI 可解释原因，也可给出修订；修改须通过规则检查和一次独立段落审校才会更新预览。明确违规建议会拦截，疑似问题保留标记。每个段落的对话独立保存，支持连续追问。
6. 如果需要加入原文没有的事实，在“补充真实事实”中填写实际经历、技能使用或指标定义。对话中的“加上 SQL”只是修改要求，不会被当作事实依据。
7. 点击 **审校并生成新版本**，将各段已保存草稿和当前未保存输入一并提交全文审校，保存新的运行版本并更新预览，再下载 Word。如果审校将某段回退，预览会同步回退，并在该段对话中说明。仅在无未解决事实风险且审校完成时提供无标注版。
8. 下次打开应用，选择 **继续已有结果**，即可恢复该版本已保存的草稿、补充事实和段落对话。未点击保存或发送的输入不会跨页面刷新保留。

正文输入框中的实时显示是未审校草稿；页面顶部数量和完整报告对应已保存的导出版本。存在已保存的草稿改动时，先全文审校再提供新下载，避免误把旧文件当成新结果。若仅在输入框中打字尚未保存，下方下载仍明确对应上一份已审校版本。

预览保留正文顺序、表格、标题及明确分页位置，页眉页脚单独展示；它不是完整 Word 排版引擎，不模拟字体布局、图片、自动分页或所有合并单元格外观。最终排版仍以 Word 为准。Word 中手动做的修改不会自动回传应用。

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
supplements.json        该导出版本采用的用户补充事实
workspace.json          交互草稿、补充事实、段落对话、撤销记录及请求状态（开始编辑后创建）
```

分析、提议改动和审校结果也保存在同一目录。失败运行保存在 `.failed` 结尾的目录，不能当作完整交付物。`.data`、`outputs`、`.env` 已加入 `.gitignore`；这些本地文件含个人资料，可自行删除。刷新后从“继续已有结果”恢复已保存的交互草稿；已导出版本保持原样。草稿采用原子写入、文件锁和版本检查，重复页面事件不会重复调用 AI；其他窗口有新修改时会提示核对，不会用迟到的 AI 回复覆盖新草稿。

## 支持范围与当前限制

- 首版保留原布局，修改选定的 headline、summary、能力描述和经历段落；日期、身份信息、雇主、教育标题等默认锁定。当前不自动跨段重排、新增章节或重建标准模板。
- 按原顺序解析段落、表格、嵌套/合并单元格、页眉页脚。文本框、修订、内容控件、脚注/尾注、复杂多栏和已有批注会明确拒绝，请先整理输入副本。图片内容不做 OCR。
- 原段落样式、表格、分页和未修改的 run 格式保留；整体改写段落使用原段落基础字符样式，局部混合强调可能需要人工调整。
- 页眉/页脚中的疑点在原位置高亮，相关批注挂在审阅版末尾的定位提示上（Word 不支持在页眉/页脚直接挂批注）。这些提示不会进入无标注版。
- `job` 对象须含 `title`、`company`，以及 `responsibilities`、`skills` 或 `qualifications` 中至少一项文字数组。五份现有 JSON 均有解析回归覆盖，额外新闻与平台评分不参与候选人事实判断。
- 页数目标是检查阈值，当前不会为了压页自动缩小字号或循环改写。可选安装 LibreOffice（`libreoffice` / `soffice` 命令）进行 PDF 页数检查；缺失时明确显示“未验证分页”。PDF 检查不是视觉验收，也不保证所有招聘系统的解析效果。
- 全部风险必须能定位才会接受审校结果。当前同一段只输出一个最高优先级语义审校结论，规则检查可补充多项风险；处理后再次全文审校。
- 交互功能已验证模拟模型的完整浏览器流程及已有本地结果的加载；四家原生 API 适配沿用已有实现。本次没有进行四家真实模型的质量/费用对比，也没有在 Word 桌面版验收最终分页。

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

浏览器交互回归（需要安装 Chromium）：

```bash
.venv/bin/python -m playwright install chromium
RESUME_BROWSER_TEST=1 .venv/bin/pytest tests/test_browser_review.py -q
```

预览组件已附带本地构建产物，普通运行无需 Node.js 或 CDN。修改前端后重新构建：

```bash
cd frontend/review
npm ci
npm run build
```

组件使用 Streamlit 自定义组件通信，正文通过 DOM 文本节点显示，不将用户正文插入 HTML。前端源码在 `frontend/review/`，Python 草稿与审校逻辑在 `src/resume_studio/workspace.py`。

测试使用明确标识的模拟模型响应，不产生 API 费用。覆盖三家的协议、错误/截断、表格和日期解析、指标含义变化、技能新增、证书状态、风险高亮、回退后的复核、人工处理和 UI，以及段落对话、并发草稿冲突、请求去重、失败恢复、刷新持久化、全文审校回退后的预览/Word 一致性。私人样本不在场时，相关样本测试跳过，合成用例仍可运行。

更完整的产品设计见 [PLAN.md](PLAN.md)。后续优先使用真实 API 和新简历验证改写质量，再考虑模板、段落重排和速度优化。
