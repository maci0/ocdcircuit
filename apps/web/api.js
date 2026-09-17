// Every studio call is a JSON POST to the one stdlib server (see apps/studio.py).
// No client router, no API client library: one function.
export async function api(path, body) {
  const r = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {}),
  });
  return r.json();
}
