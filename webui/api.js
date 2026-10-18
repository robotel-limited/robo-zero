/* Thin fetch wrapper shared by every webui module: JSON in/out, throws on
   a non-2xx response with the server's `detail` message, 204 → null. */

async function api(path, options) {
  const resp = await fetch(path, options);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `${resp.status}`);
  }
  return resp.status === 204 ? null : resp.json();
}
