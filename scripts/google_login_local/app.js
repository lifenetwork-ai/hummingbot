/* Local acceptance only. Never persist, print or include the ID token in URLs. */
let token = "";
const statusElement = document.getElementById("status");
const result = document.getElementById("result");
const refresh = document.getElementById("refresh");
const clear = document.getElementById("clear");

async function api(path, method = "GET") {
  const response = await fetch("/api/v1/admin" + path, {
    method, credentials: "omit", cache: "no-store", redirect: "error",
    headers: {Authorization: "Bearer " + token}
  });
  const body = await response.json();
  if (!response.ok) {
    const error = new Error(body.code || "request_failed");
    error.httpStatus = response.status;
    error.requestId = body.request_id;
    throw error;
  }
  return body;
}

async function check(login) {
  refresh.disabled = true;
  clear.disabled = true;
  statusElement.textContent = "Checking identity and account permissions…";
  try {
    const identity = await api(login ? "/access/login" : "/access/me", login ? "POST" : "GET");
    const bootstrap = identity.role === "admin" ? await api("/access/bootstrap") : undefined;
    const audit = await api("/audit?limit=10");
    result.textContent = JSON.stringify({identity, bootstrap, audit}, null, 2);
    statusElement.textContent = "Access verified. Review the identity, role and audit results below.";
  } catch (error) {
    result.textContent = JSON.stringify({code: error.message, http_status: error.httpStatus,
      request_id: error.requestId}, null, 2);
    statusElement.textContent = "Access check failed. Review the error code below.";
  } finally {
    refresh.disabled = !token;
    clear.disabled = !token;
  }
}

refresh.onclick = () => check(false);
clear.onclick = () => {
  token = "";
  refresh.disabled = clear.disabled = true;
  result.textContent = "Local sign-in cleared.";
  statusElement.textContent = "Choose a Google account to sign in again.";
};

async function initialize() {
  const response = await fetch("/config", {credentials: "omit", cache: "no-store"});
  if (!response.ok) throw new Error("Local configuration is unavailable.");
  const config = await response.json();
  await new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.onload = resolve;
    script.onerror = () => reject(new Error("Google sign-in could not be loaded."));
    document.head.appendChild(script);
  });
  google.accounts.id.initialize({client_id: config.client_id, auto_select: false,
    callback: response => {token = response.credential; check(true);}});
  google.accounts.id.renderButton(document.getElementById("google-button"), {
    theme: "outline", size: "large", text: "signin_with", width: 280
  });
  statusElement.textContent = "Sign in with your approved Google account.";
}
initialize().catch(error => {statusElement.textContent = error.message;});
