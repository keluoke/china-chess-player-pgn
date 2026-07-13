const CLIENT_ID = "Ov23liygsiiDeLhE2EJg";

export async function onRequestPost() {
  const body = new URLSearchParams({ client_id: CLIENT_ID, scope: "public_repo" });
  const response = await fetch("https://github.com/login/device/code", {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/x-www-form-urlencoded" },
    body
  });
  return githubResponse(response);
}

async function githubResponse(response) {
  return new Response(await response.text(), {
    status: response.status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" }
  });
}
