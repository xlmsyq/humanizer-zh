"use strict";
const $ = (id) => document.getElementById(id);
const source = $("source");
const engine = $("engine");
const rewrite = $("rewrite");
const status = $("status");
const example = "值得注意的是，这次更新增加了批量导出功能。此外，团队对 12 个页面进行了调整，让常用操作更容易找到。\n\n我们将深入探讨用户的反馈，在下一次更新中继续改进搜索体验。对于使用中的问题，请通过应用内的反馈入口告诉我们。";
let busy = false;
let currentResult = "";
let submittedText = "";
let configReady = false;
let providerHost = "配置的模型服务";

function setStatus(message, error = false) {
  status.textContent = message;
  status.classList.toggle("error", error);
}
function updateActions() {
  $("source-count").textContent = `${source.value.length} / 6000`;
  rewrite.disabled = busy || !source.value.trim() || (engine.value === "model" && !$("consent").checked);
  $("copy").disabled = busy || !currentResult;
  $("clear").disabled = busy || !source.value;
}
function resetResult() {
  currentResult = "";
  submittedText = "";
  $("result").textContent = "";
  $("result").hidden = true;
  $("empty-result").hidden = false;
  $("result-badge").hidden = true;
  $("result-count").textContent = "— 字";
  $("changes-panel").hidden = true;
  $("result-caption").textContent = "你的观点，你来定稿。";
  updateActions();
}
source.addEventListener("input", () => {
  updateActions();
  if (currentResult && source.value !== submittedText) setStatus("原文已变化；右侧为上一次结果，请重新润色。");
});
$("example").addEventListener("click", () => {
  source.value = example;
  resetResult();
  setStatus("这是一段虚构的产品更新示例。选一个语气，试着润色。");
  source.focus();
});
$("clear").addEventListener("click", () => {
  source.value = "";
  resetResult();
  setStatus("已清空当前页面的原文和结果。");
  source.focus();
});
engine.addEventListener("change", () => {
  const model = engine.value === "model";
  $("consent-row").hidden = !model;
  $("consent").checked = false;
  $("privacy-note").textContent = model
    ? `模型润色会通过服务器将原文发送至 ${providerHost}。本应用不保存正文；服务商可能保留请求。`
    : "规则演示由当前应用服务器处理，不调用外部模型。正文不写入本应用存储。";
  setStatus(model ? "请确认发送同意后再开始。模型输出可能改变原意，请核对事实。" : "规则演示只做有限的词句替换；完整语义改写需要配置模型。");
  updateActions();
});
$("consent").addEventListener("change", updateActions);

rewrite.addEventListener("click", async () => {
  if (rewrite.disabled) return;
  busy = true;
  source.readOnly = true;
  engine.disabled = true;
  $("example").disabled = true;
  document.querySelectorAll('input[name="tone"]').forEach((input) => { input.disabled = true; });
  $("rewrite-label").textContent = "正在润色";
  updateActions();
  setStatus("正在处理，原文会保留在左侧…");
  const input = source.value;
  const selectedEngine = engine.value;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 70000);
  try {
    const response = await fetch("/api/rewrite", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Humanizer-Request": "1" },
      body: JSON.stringify({ text: input, tone: document.querySelector('input[name="tone"]:checked').value, engine: selectedEngine, consent: $("consent").checked }),
      signal: controller.signal,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "输入无效，请检查字数和内容后重试。");
    currentResult = data.text;
    submittedText = input;
    $("result").textContent = data.text;
    $("result").hidden = false;
    $("empty-result").hidden = true;
    $("result-badge").hidden = false;
    $("result-badge").textContent = selectedEngine === "demo" ? "规则演示" : "模型润色";
    $("result-count").textContent = `${data.result_length} 字`;
    $("result-caption").textContent = `${data.tone} · 请核对原意`;
    $("changes").replaceChildren();
    const changes = data.changes.length ? data.changes : ["未命中演示规则，保留原文。规则演示不做全文语义改写。"];
    for (const change of changes) {
      const item = document.createElement("li");
      item.textContent = change;
      $("changes").appendChild(item);
    }
    $("changes-panel").hidden = false;
    setStatus(selectedEngine === "demo" ? `完成：${data.original_length} → ${data.result_length} 字。有限规则替换，请结合上下文判断。` : "模型润色完成。请逐句核对事实、数值与原意后使用。");
  } catch (error) {
    setStatus(error.name === "AbortError" ? "等待超时，原文已保留，请稍后重试。" : (error instanceof TypeError ? "无法连接应用服务，原文已保留，请检查服务是否运行。" : error.message), true);
  } finally {
    clearTimeout(timer);
    busy = false;
    source.readOnly = false;
    engine.disabled = !configReady;
    $("example").disabled = false;
    document.querySelectorAll('input[name="tone"]').forEach((input) => { input.disabled = false; });
    $("rewrite-label").textContent = "开始润色";
    updateActions();
  }
});
$("copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(currentResult);
    setStatus("已复制润色结果。");
  } catch {
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents($("result"));
    selection.removeAllRanges();
    selection.addRange(range);
    setStatus("浏览器未允许自动复制，已选中结果，请按 ⌘ / Ctrl + C。");
  }
});
document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    rewrite.click();
  }
});
engine.disabled = true;
fetch("/api/config")
  .then((response) => { if (!response.ok) throw new Error("config"); return response.json(); })
  .then((data) => {
    configReady = true;
    engine.disabled = false;
    if (data.model_available) {
      engine.options[1].disabled = false;
      engine.options[1].textContent = "模型润色";
      providerHost = data.provider_host;
      $("consent-text").textContent = `我同意将原文发送至 ${providerHost}，并了解服务商可能保存请求。`;
    }
  })
  .catch(() => { setStatus("无法读取服务配置。规则演示仍可尝试，请确认应用服务正常运行。", true); });
updateActions();
