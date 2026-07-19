const form = document.querySelector("#contributionForm");
const output = document.querySelector("#contributionOutput");
const preview = document.querySelector("#contributionPreview");
const status = document.querySelector("#contributionStatus");
const hint = document.querySelector("#contributionHint");
const copyButton = document.querySelector("#copyContribution");
const downloadButton = document.querySelector("#downloadContribution");
const githubButton = document.querySelector("#githubContribution");
const authPanel = document.querySelector("#deviceAuthorization");
const deviceCode = document.querySelector("#deviceCode");
const deviceLink = document.querySelector("#deviceLink");

const REPOSITORY = "keluoke/china-chess-player-pgn";
const GITHUB_API = "https://api.github.com";
let payload = null;
let fallbackIssueURL = "";

const params = new URLSearchParams(location.search);
const disputeFields = document.querySelector("#identityDisputeFields");
const disputeMembers = (params.get("members") || "").split(",").map(value => value.trim()).filter(Boolean);
form.elements.player_id.value = params.get("player") || "";
form.elements.player_name.value = params.get("name") || "";
form.elements.data_query.value = params.get("query") || "";
if (params.get("type") && [...form.elements.type.options].some(option => option.value === params.get("type"))) form.elements.type.value = params.get("type");
else if (params.get("player")) form.elements.type.value = "identity-clue";
configureDisputeFields();
form.elements.type.addEventListener("change", configureDisputeFields);

function configureDisputeFields() {
  const active = form.elements.type.value === "identity-dispute";
  disputeFields.hidden = !active;
  for (const name of ["dispute_member_a", "dispute_member_b"]) {
    const select = form.elements[name];
    select.required = active;
    select.innerHTML = disputeMembers.map(member => `<option value="${escapeAttribute(member)}">${escapeHTML(member)}</option>`).join("");
  }
  if (disputeMembers.length > 1) form.elements.dispute_member_b.selectedIndex = 1;
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(form));
  const extra = {};
  if (data.type === "identity-dispute") {
    if (params.get("group")) extra.groupID = params.get("group");
    if (data.dispute_whole_group) {
      extra.scope = "whole-group";
      extra.memberIDs = disputeMembers;
    } else {
      const pair = [data.dispute_member_a, data.dispute_member_b].filter(Boolean);
      if (pair.length !== 2 || pair[0] === pair[1]) {
        status.textContent = "请选择两条不同的记录。";
        return;
      }
      extra.scope = "pair";
      extra.memberIDs = pair;
      extra.pairHash = await identityPairHash(pair[0], pair[1]);
    }
  } else if (data.type === "identity-clue" && params.get("group")) {
    extra.groupID = params.get("group");
    extra.memberIDs = disputeMembers;
  }
  payload = Object.fromEntries(Object.entries({
    schema: "china-chess-community-contribution/v1",
    collection_policy: "target-only-no-scraped-content",
    created_at: new Date().toISOString(),
    type: data.type,
    player_name: data.player_name,
    player_id: data.player_id,
    event_ref: data.event_ref,
    data_query: data.data_query,
    event_name: data.event_name,
    evidence_url: data.evidence_url,
    notes: data.notes,
    contributor: data.nickname,
    ...extra
  }).filter(([, value]) => String(value || "").trim()));
  preview.value = JSON.stringify(payload, null, 2);
  output.hidden = false;
  output.scrollIntoView({ behavior: "smooth", block: "start" });
  status.textContent = "";
  if (payload.type === "privacy-request" || payload.type === "identity-dispute") {
    if (payload.type === "privacy-request") {
      hint.textContent = "删除或匿名化请求不会进入公开 Issue。请下载内容后，通过项目维护者的私密联系方式发送。";
      status.textContent = "隐私请求已在本机整理，未上传。";
    } else {
      hint.textContent = "身份质疑与合并纠纷请求不会进入公开 Issue。请下载内容后，通过私密联系方式线下发送给维护者。";
      status.textContent = "身份质疑已在本机整理，未上传。";
    }
    githubButton.hidden = true;
    return;
  }
  hint.textContent = data.contact
    ? "联系方式仅保留在当前表单，不会写进公开 Issue。正在准备 GitHub 授权。"
    : "正在准备 GitHub 授权；授权后向导会直接创建公开 Issue。";
  githubButton.hidden = false;
  await submitCurrentPayload();
});

async function identityPairHash(a, b) {
  const value = [a, b].sort().join("|");
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(bytes)].map(byte => byte.toString(16).padStart(2, "0")).join("").slice(0, 16);
}

githubButton.addEventListener("click", submitCurrentPayload);

async function submitCurrentPayload() {
  if (!payload || payload.type === "privacy-request" || payload.type === "identity-dispute") return;
  const title = `[数据贡献] ${payload.player_name || payload.event_name || payload.event_ref || payload.type}`;
  const body = issueBody(payload);
  fallbackIssueURL = `https://github.com/${REPOSITORY}/issues/new?title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}`;
  await submitToGitHub(title, body);
}

function issueBody(data) {
  return [
    `<!-- china-chess-contribution:v1 type=${data.type || "unknown"} -->`,
    "由网页贡献向导生成。请维护者核验公开证据后入库。",
    "",
    "```json",
    JSON.stringify(data, null, 2),
    "```"
  ].join("\n");
}

async function submitToGitHub(title, body) {
  status.textContent = "正在连接 GitHub…";
  try {
    const token = sessionStorage.getItem("chinaChessGithubToken") || await deviceFlowToken();
    const response = await fetch(`${GITHUB_API}/repos/${REPOSITORY}/issues`, {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${token}`,
        "X-GitHub-Api-Version": "2022-11-28"
      },
      body: JSON.stringify({ title, body })
    });
    if (response.status === 401) sessionStorage.removeItem("chinaChessGithubToken");
    if (!response.ok) throw new Error(`GitHub 返回 ${response.status}`);
    const issue = await response.json();
    authPanel.hidden = true;
    status.innerHTML = `提交成功：<a href="${escapeAttribute(issue.html_url)}" target="_blank" rel="noreferrer">查看 Issue #${Number(issue.number)}</a>`;
  } catch (error) {
    authPanel.hidden = true;
    status.innerHTML = `自动提交未完成（${escapeHTML(error.message || "浏览器阻止了授权")}）。<a href="${escapeAttribute(fallbackIssueURL)}" target="_blank" rel="noreferrer">打开已填好的 Issue</a>，无需再粘贴内容。`;
  }
}

async function deviceFlowToken() {
  const initResponse = await fetch("/api/github/device-code", { method: "POST" });
  if (!initResponse.ok) throw new Error("无法启动 GitHub 设备授权");
  const init = await initResponse.json();
  authPanel.hidden = false;
  deviceCode.textContent = init.user_code;
  deviceLink.href = init.verification_uri;
  status.textContent = "请在 GitHub 输入上方验证码；完成后本页会自动继续。";
  const deadline = Date.now() + Number(init.expires_in || 900) * 1000;
  let interval = Number(init.interval || 5) * 1000;
  while (Date.now() < deadline) {
    await new Promise(resolve => setTimeout(resolve, interval));
    const pollResponse = await fetch("/api/github/device-token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_code: init.device_code })
    });
    const result = await pollResponse.json();
    if (result.access_token) {
      sessionStorage.setItem("chinaChessGithubToken", result.access_token);
      return result.access_token;
    }
    if (result.error === "slow_down") interval += 5000;
    else if (result.error && result.error !== "authorization_pending") throw new Error(result.error_description || result.error);
  }
  throw new Error("GitHub 授权超时");
}

copyButton.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(preview.value);
    status.textContent = "已复制贡献内容。";
  } catch {
    preview.select();
    document.execCommand("copy");
    status.textContent = "已复制贡献内容。";
  }
});

downloadButton.addEventListener("click", () => {
  if (!payload) return;
  const blob = new Blob([`${preview.value}\n`], { type: "application/json;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `chess-contribution-${Date.now()}.json`;
  link.click();
  URL.revokeObjectURL(link.href);
  status.textContent = "贡献包已下载。";
});

form.addEventListener("reset", () => {
  output.hidden = true;
  authPanel.hidden = true;
  githubButton.hidden = false;
  payload = null;
});

function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}

function escapeAttribute(value) {
  return escapeHTML(value);
}
