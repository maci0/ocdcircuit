// Every studio call is a JSON POST to the one stdlib server (see apps/studio.py).
// No client router, no API client library: one function.
export async function api(path, body) {
  let r;
  try {
    r = await fetch(path, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body || {}),
    });
  } catch {
    return {error: 'cannot reach the studio. Check that `python -m apps.studio` is running, then reload this page.'};
  }
  return r.json();
}
