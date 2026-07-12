const CLIENT_ID = "Ov23liygsiiDeLhE2EJg";

export async function onRequestPost({ request }) {
  let input;
  try {
    input = await request.json();
  } catch {
    return Response.json({ error: "invalid_request" }, { status: 400 });
  }
  if (!/^[A-Za-z0-9_-]{20,200}$/.test(String(input.device_code || ""))) {
    return Response.json({ error: "invalid_device_code" }, { status: 400 });
  }
  const body = new URLSearchParams({
    client_id: CLIENT_ID,
    device_code: input.device_code,
    grant_type: "urn:ietf:params:oauth:grant-type:device_code"
  });
  const response = await fetch("https://github.com/login/oauth/access_token", {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/x-www-form-urlencoded" },
    body
  });
  return new Response(await response.text(), {
    status: response.status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" }
  });
}
