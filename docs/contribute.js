const form = document.querySelector("#contributionForm");
const output = document.querySelector("#contributionOutput");
const preview = document.querySelector("#contributionPreview");
const status = document.querySelector("#contributionStatus");
const copyButton = document.querySelector("#copyContribution");
const downloadButton = document.querySelector("#downloadContribution");
const githubLink = document.querySelector("#githubContribution");
let payload = null;

const params = new URLSearchParams(location.search);
form.elements.player_id.value = params.get("player") || "";
form.elements.player_name.value = params.get("name") || "";
if (params.get("player")) form.elements.type.value = "identity-clue";

form.addEventListener("submit", event => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(form));
  payload = Object.fromEntries(Object.entries({
    schema: "china-chess-community-contribution/v1",
    created_at: new Date().toISOString(),
    type: data.type,
    player_name: data.player_name,
    player_id: data.player_id,
    event_ref: data.event_ref,
    event_name: data.event_name,
    evidence_url: data.evidence_url,
    notes: data.notes,
    contributor: data.nickname,
    contact: data.contact
  }).filter(([, value]) => String(value || "").trim()));
  preview.value = JSON.stringify(payload, null, 2);
  const title = `[数据贡献] ${payload.player_name || payload.event_name || payload.event_ref || payload.type}`;
  githubLink.href = `https://github.com/keluoke/china-chess-player-pgn/issues/new?title=${encodeURIComponent(title)}&body=${encodeURIComponent("```json\n" + preview.value + "\n```")}`;
  output.hidden = false;
  output.scrollIntoView({ behavior: "smooth", block: "start" });
  status.textContent = "";
});

copyButton.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(preview.value);
    status.textContent = "已复制，可直接粘贴到微信、邮件或项目社区。";
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
  payload = null;
});
