// Two helpers every feature module needs, so they do not have to import the
// whole workshop runtime (which would be a cycle).
export const $ = id => document.getElementById(id);

// Every studio call is a JSON POST to the one stdlib server. A login reply
// means the session died mid-work: bounce to the gate.
export async function api(path, body) {
  const r = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {}),
  });
  const j = await r.json();
  if (j && j.login) { location.href = '/'; throw new Error('login'); }
  return j;
}
