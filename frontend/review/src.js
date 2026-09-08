import { Streamlit } from "streamlit-component-lib";
import { diffWordsWithSpace } from "diff";

let data,
  selected,
  mode = "diff",
  pending = null,
  locals = {},
  notice = "",
  initialized = false;
const root = document.querySelector("#app");
const make = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};
const button = (label, fn, cls = "") => {
  const e = make("button", cls, label);
  e.type = "button";
  e.onclick = fn;
  return e;
};
function draft(key) {
  return (
    locals[key] || {
      text: data.blocks[key].text,
      facts: data.blocks[key].facts,
      question: "",
    }
  );
}
function hasLocal(key) {
  const b = data.blocks[key],
    d = draft(key);
  return d.text !== b.text || d.facts !== b.facts;
}
function capture() {
  if (!selected || !data.blocks[selected].editable) return;
  locals[selected] = {
    text: document.querySelector("#editor")?.value ?? draft(selected).text,
    facts: document.querySelector("#facts")?.value ?? draft(selected).facts,
    question:
      document.querySelector("#question")?.value ?? draft(selected).question,
  };
}
function emit(action) {
  if (pending) return;
  capture();
  const d = draft(selected),
    event = {
      id: crypto.randomUUID(),
      version: data.version,
      action,
      block_id: selected,
      text: d.text,
      facts: d.facts,
      message: d.question,
    };
  if (action === "ask" && !d.question.trim()) {
    notice = "请先输入问题或修改要求。";
    render();
    return;
  }
  if (action === "publish") {
    event.edits = {};
    event.facts_by_block = {};
    for (const key of Object.keys(locals))
      if (hasLocal(key)) {
        event.edits[key] = locals[key].text;
        event.facts_by_block[key] = locals[key].facts;
      }
  }
  pending = event;
  notice =
    action === "ask"
      ? "AI 正在修改并检查依据…"
      : action === "publish"
        ? "正在审校全文并生成 Word…"
        : "正在保存…";
  render();
  Streamlit.setComponentValue(event);
}
function select(key) {
  capture();
  selected = key;
  render();
  document
    .querySelector(`[data-block="${key}"]`)
    ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
}
function textDiff(parent, key) {
  const b = data.blocks[key],
    current = draft(key).text;
  if (mode === "original") parent.textContent = b.original;
  else if (mode === "current") parent.textContent = current || "（已删除）";
  else
    for (const piece of diffWordsWithSpace(b.original, current))
      parent.append(
        make(
          piece.added ? "ins" : piece.removed ? "del" : "span",
          "",
          piece.value,
        ),
      );
}
function renderNodes(nodes, parent) {
  for (const n of nodes) {
    if (n.type === "paragraph") {
      const b = data.blocks[n.id];
      const wrap = make(
        "div",
        `paragraph${n.heading ? " heading" : ""}${n.center ? " centered" : ""}${n.page_break ? " page-break" : ""}${n.id === selected ? " selected" : ""}`,
      );
      wrap.dataset.block = n.id;
      wrap.tabIndex = 0;
      wrap.setAttribute("role", "button");
      wrap.setAttribute("aria-label", `选择段落：${b.original.slice(0, 70)}`);
      wrap.onclick = () => select(n.id);
      wrap.onkeydown = (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          select(n.id);
        }
      };
      const t = make("div", "paragraph-text");
      t.style.paddingLeft = `${n.indent}px`;
      if (n.bullet) t.classList.add("bullet");
      textDiff(t, n.id);
      wrap.append(t);
      const issues =
        b.check?.status === "ai_checked" ? b.check.issues : b.risks;
      if (issues.length)
        wrap.append(make("span", "risk-badge", `${issues.length} 处待核实`));
      else if (hasLocal(n.id) || b.check?.status === "manual")
        wrap.append(make("span", "draft-badge", "待审校"));
      parent.append(wrap);
    } else if (n.type === "table") {
      const table = make("table", "resume-table");
      for (const row of n.rows) {
        const tr = make("tr");
        for (const cell of row) {
          const td = make("td");
          td.colSpan = cell.span;
          renderNodes(cell.nodes, td);
          tr.append(td);
        }
        table.append(tr);
      }
      parent.append(table);
    } else if (n.type === "aside") {
      const aside = make("aside", "document-aside");
      aside.append(make("p", "muted", "页眉 / 页脚"));
      renderNodes(n.nodes, aside);
      parent.append(aside);
    } else
      parent.append(make("div", n.type === "break" ? "page-break" : "space"));
  }
}
function updateDocument() {
  for (const key of Object.keys(locals)) {
    const p = document.querySelector(`[data-block="${key}"] .paragraph-text`);
    if (p) {
      p.replaceChildren();
      textDiff(p, key);
    }
  }
  const status = document.querySelector("#save-status");
  if (status)
    status.textContent = hasLocal(selected)
      ? "输入已显示在预览中 · 尚未保存"
      : "已保存到本机";
}
function issueCard(r, title) {
  const c = make("div", "issue");
  c.append(make("strong", "", title), make("p", "", r.reason));
  if (r.quote) c.append(make("blockquote", "", r.quote));
  if (r.suggestion) c.append(make("p", "muted", r.suggestion));
  return c;
}
function render() {
  if (!data) return;
  const scroll = document.querySelector(".document-scroll")?.scrollTop || 0;
  const oldPanel = document.querySelector(".panel");
  const panelScroll =
    oldPanel?.dataset.block === selected ? oldPanel.scrollTop : 0;
  root.replaceChildren();
  const toolbar = make("div", "toolbar"),
    modes = make("div", "segmented");
  for (const [value, label] of [
    ["diff", "改写对照"],
    ["current", "当前正文"],
    ["original", "原始简历"],
  ])
    modes.append(
      button(
        label,
        () => {
          capture();
          mode = value;
          render();
        },
        mode === value ? "active" : "",
      ),
    );
  toolbar.append(modes, make("span", "legend", "绿色：新增　红色：删除"));
  const publish = button("审校并生成新版本", () => emit("publish"), "primary");
  publish.disabled = !!pending;
  toolbar.append(publish);
  root.append(toolbar);
  const subtitle = make(
    "div",
    "preview-note",
    "点击正文选择段落；右侧编辑时，预览立即更新。这里按文档结构显示，最终分页请以 Word 为准。",
  );
  root.append(subtitle);
  const chips = make("div", "issue-nav");
  for (const [key, b] of Object.entries(data.blocks)) {
    const count = (b.check?.status === "ai_checked" ? b.check.issues : b.risks)
      .length;
    if (count)
      chips.append(
        button(
          `${count} 处疑点 · ${b.original.slice(0, 24)}…`,
          () => select(key),
          "chip",
        ),
      );
  }
  if (!chips.children.length)
    chips.append(
      make("span", "muted", "点击任意正文段落，直接编辑或继续向 AI 提要求。"),
    );
  root.append(chips);
  if (notice) {
    const status = make("div", "notice", notice);
    status.setAttribute("role", "status");
    root.append(status);
  }
  const layout = make("div", "layout"),
    docScroll = make("div", "document-scroll"),
    paper = make("article", "paper");
  renderNodes(data.nodes, paper);
  docScroll.append(paper);
  layout.append(docScroll);
  const panel = make("section", "panel");
  panel.dataset.block = selected;
  panel.setAttribute("aria-label", "段落编辑与对话");
  const b = data.blocks[selected],
    d = draft(selected);
  panel.append(
    make("div", "eyebrow", "当前段落"),
    make(
      "h2",
      "",
      b.original.slice(0, 56) + (b.original.length > 56 ? "…" : ""),
    ),
  );
  const original = make("details", "original");
  original.append(
    make("summary", "", "查看完整原文"),
    make("p", "", b.original),
  );
  panel.append(original);
  for (const r of b.risks)
    panel.append(
      issueCard(r, b.check ? "上次全文审校记录 · 本段修改后需复核" : "待核实"),
    );
  if (b.check) {
    if (b.check.status === "manual")
      panel.append(
        make(
          "p",
          "pending-note",
          "手动草稿尚未审校；保存不代表事实已通过检查。",
        ),
      );
    for (const r of b.check.issues)
      panel.append(issueCard(r, "本次修改仍有疑点"));
    for (const r of b.check.proposal_issues || [])
      panel.append(issueCard(r, "未采用的 AI 建议"));
  }
  if (!b.editable)
    panel.append(
      make(
        "p",
        "muted",
        "此段为身份信息、标题或复杂内容，当前保持原文。请选择正文段落进行修改。",
      ),
    );
  else {
    const editorLabel = make("label", "", "直接修改正文");
    editorLabel.htmlFor = "editor";
    panel.append(editorLabel);
    const editor = make("textarea");
    editor.id = "editor";
    editor.value = d.text;
    editor.rows = 6;
    editor.maxLength = 4000;
    editor.disabled = !!pending;
    editor.oninput = () => {
      capture();
      updateDocument();
    };
    panel.append(editor);
    const status = make(
      "p",
      "muted",
      hasLocal(selected) ? "输入已显示在预览中 · 尚未保存" : "已保存到本机",
    );
    status.id = "save-status";
    panel.append(status);
    const factsDetails = make("details", "facts");
    factsDetails.open = !!d.facts;
    factsDetails.append(
      make("summary", "", "补充真实事实（可选）"),
      make(
        "p",
        "muted",
        "如需加入原文没有的经历、技能或指标，请在这里明确提供真实依据。对话中的修改要求不会自动成为事实依据。",
      ),
    );
    const facts = make("textarea");
    facts.id = "facts";
    facts.setAttribute("aria-label", "补充真实事实");
    facts.value = d.facts;
    facts.rows = 3;
    facts.maxLength = 4000;
    facts.disabled = !!pending;
    facts.oninput = () => {
      capture();
      updateDocument();
    };
    factsDetails.append(facts);
    panel.append(factsDetails);
    const actions = make("div", "actions");
    for (const [a, label] of [
      ["save", "保存草稿"],
      ["undo", "撤销上次"],
      ["restore", "恢复原文"],
    ]) {
      const btn = button(label, () => emit(a));
      btn.disabled = !!pending || (a === "undo" && !b.can_undo);
      actions.append(btn);
    }
    panel.append(actions);
    panel.append(make("h3", "", "和 AI 继续修改"));
    const chat = make("div", "chat");
    for (const msg of b.conversation) {
      const item = make("div", `message ${msg.role}`);
      item.append(
        make("strong", "", msg.role === "user" ? "你" : "AI"),
        make("p", "", msg.text),
      );
      chat.append(item);
    }
    if (!b.conversation.length)
      chat.append(
        make(
          "p",
          "muted",
          "例如：“保留指标的原始含义，把这段写得更简洁。”也可以先问 AI 为什么这样改。",
        ),
      );
    panel.append(chat);
    const q = make("textarea");
    q.id = "question";
    q.setAttribute("aria-label", "给 AI 的反馈");
    q.placeholder = "针对这一段提问或提出修改要求…";
    q.value = d.question;
    q.rows = 3;
    q.maxLength = 4000;
    q.disabled = !!pending;
    q.oninput = capture;
    q.onkeydown = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") emit("ask");
    };
    panel.append(q);
    const send = button("发送给 AI", () => emit("ask"), "primary");
    send.disabled = !!pending;
    panel.append(send);
    panel.append(
      make(
        "p",
        "muted",
        "AI 修改通过段落检查后会直接显示；无依据的新增会拦截，疑似问题会标注。所有草稿导出前还会审校全文。",
      ),
    );
  }
  layout.append(panel);
  root.append(layout);
  docScroll.scrollTop = scroll;
  panel.scrollTop = panelScroll;
  Streamlit.setFrameHeight(window.innerWidth < 800 ? 1350 : 910);
}
Streamlit.events.addEventListener(Streamlit.RENDER_EVENT, (event) => {
  const incoming = event.detail.args.payload;
  if (!incoming) return;
  if (data?.run_id !== incoming.run_id) {
    locals = {};
    pending = null;
    selected = null;
    notice = "";
    initialized = false;
  }
  const ack = event.detail.args.ack;
  if (pending && ack?.id === pending.id) {
    if (ack.status === "complete") {
      const keys =
        pending.action === "publish"
          ? Object.keys(pending.edits)
          : [pending.block_id];
      for (const key of keys) {
        const question =
          pending.action === "ask" ? "" : locals[key]?.question || "";
        delete locals[key];
        if (incoming.blocks[key])
          locals[key] = {
            text: incoming.blocks[key].text,
            facts: incoming.blocks[key].facts,
            question,
          };
      }
    }
    notice = ack.message;
    pending = null;
  }
  data = incoming;
  if (!selected || !data.blocks[selected])
    selected =
      Object.keys(data.blocks).find(
        (k) => data.blocks[k].editable && data.blocks[k].risks.length,
      ) ||
      Object.keys(data.blocks).find((k) => data.blocks[k].editable) ||
      Object.keys(data.blocks)[0];
  render();
  if (!initialized) {
    initialized = true;
    document
      .querySelector(`[data-block="${selected}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }
});
Streamlit.setComponentReady();
