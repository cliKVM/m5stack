"use strict";
const $ = id => document.getElementById(id);
let token = "", sid = "", lastFrame = 0, blobUrl = "", latest = null, polling = false, generation = 0;
const labels = {planning:"Analizuję stan",awaiting_action:"Wykonuję działanie",awaiting_frame:"Czekam na nową obserwację",done:"Zadanie zakończone",blocked:"Potrzebna pomoc użytkownika",failed:"Wystąpił błąd",cancelled:"Zadanie zatrzymane"};

async function api(path, method="GET", body=undefined) {
  const headers = {Authorization:"Bearer " + token};
  if (method !== "GET") {
    headers["Idempotency-Key"] = crypto.randomUUID();
    if (body !== undefined) headers["Content-Type"] = "application/json";
  }
  const r = await fetch(path, {method,headers,body:body===undefined?undefined:JSON.stringify(body),cache:"no-store"});
  if (!r.ok) {
    let d;
    try { d = await r.json(); } catch { d = {detail:r.statusText}; }
    throw Error(typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail));
  }
  return r;
}
function error(e) { $("error").textContent = e.message || String(e); }
function selectSession(value) {
  generation++;
  sid = value || ""; lastFrame = 0; latest = null;
  if (blobUrl) URL.revokeObjectURL(blobUrl);
  blobUrl = "";
  $("screen").removeAttribute("src"); $("screen").hidden = true; $("empty").hidden = false;
  $("api-state").textContent = ""; $("api-state").hidden = true;
  $("approval").hidden = true; $("uncertain").hidden = true; $("simulation").hidden = true;
  $("run").disabled = true; $("capture").disabled = !sid; $("cancel").disabled = !sid;
  $("frame-label").textContent = ""; $("command").textContent = "";
  $("device-name").textContent = sid ? "Łączę z urządzeniem" : "Czekam na kontroler";
  $("status").textContent = sid ? "Pobieram aktualny stan" : "Brak aktywnych sesji";
}
async function refresh() {
  const previous = sid, credential = token;
  const response = await api("/v1/sessions");
  const result = await response.json();
  if (token !== credential) return;
  $("sessions").replaceChildren();
  for (const s of result.sessions) {
    if (s.status !== "active") continue;
    const option = document.createElement("option");
    option.value = s.session_id; option.textContent = s.device_id + (s.profile?.platform ? " · " + s.profile.platform : "") + (s.simulated ? " · SYMULACJA" : "");
    $("sessions").append(option);
  }
  if ([...$("sessions").options].some(o => o.value === previous)) $("sessions").value = previous;
  selectSession($("sessions").value);
  await poll();
}
async function poll() {
  if (!sid || polling) return;
  polling = true;
  const selected = sid, version = generation, valid = () => sid === selected && generation === version;
  try {
    const response = await api(`/v1/sessions/${selected}/status`);
    if (!valid()) return;
    const state = await response.json();
    if (!valid() || state.session_id !== selected) return;
    latest = state;
    $("device-name").textContent = state.device_id;
    $("simulation").hidden = !state.simulated;
    $("status").textContent = state.intent ? (labels[state.intent.status] || state.intent.status) + (state.intent.detail ? ": " + state.intent.detail : "") : "Gotowy na polecenie";
    $("command").textContent = state.action ? JSON.stringify(state.action.command,null,2) : "";
    $("run").disabled = true;
    if (state.latest_frame && state.latest_frame !== lastFrame) {
      if (state.observation_kind === "json-state") {
        const stateResponse = await api(`/v1/sessions/${selected}/observations/${state.latest_frame}`);
        if (!valid()) return;
        const observation = await stateResponse.json();
        if (!valid() || observation.frame_id !== state.latest_frame) return;
        if (blobUrl) URL.revokeObjectURL(blobUrl);
        blobUrl = ""; $("screen").removeAttribute("src"); $("screen").hidden = true;
        $("api-state").textContent = JSON.stringify(observation.state,null,2);
        $("api-state").hidden = false;
      } else {
        const frameResponse = await api(`/v1/sessions/${selected}/frames/${state.latest_frame}`);
        if (!valid()) return;
        const blob = await frameResponse.blob();
        if (!valid()) return;
        const next = URL.createObjectURL(blob);
        if (blobUrl) URL.revokeObjectURL(blobUrl);
        blobUrl = next; $("screen").src = blobUrl;
        $("screen").hidden = false; $("api-state").hidden = true;
      }
      $("empty").hidden = true;
      lastFrame = state.latest_frame;
      $("frame-label").textContent = (state.observation_kind === "json-state" ? "Stan JSON " : "Zrzut ") + lastFrame;
    }
    if (!valid()) return;
    $("run").disabled = state.observation_kind === "none" || !lastFrame || lastFrame !== state.latest_frame;
    $("capture").disabled = state.observation_kind === "none";
    $("approval").hidden = state.action?.state !== "awaiting_confirmation" || state.action.based_on_frame !== lastFrame;
    $("approval-text").textContent = state.action?.reason || "";
    $("uncertain").hidden = state.action?.state !== "uncertain";
    $("resolve").disabled = !state.action || lastFrame <= state.action.based_on_frame;
  } catch (e) { if (valid()) error(e); }
  finally { polling = false; }
}
function currentObservation() {
  if (!sid || !latest || latest.session_id !== sid || !lastFrame || latest.latest_frame !== lastFrame) {
    throw Error("Poczekaj na aktualną obserwację wybranego urządzenia.");
  }
  return {session:sid, frame:lastFrame, action:latest.action};
}
$("connect").onsubmit = async e => {
  e.preventDefault(); token = $("token").value.trim(); selectSession(""); $("error").textContent = "";
  try { await refresh(); $("workspace").hidden = false; $("connection").textContent = "Połączony"; $("token").value = ""; }
  catch (e) { token = ""; selectSession(""); $("workspace").hidden = true; error(e); }
};
$("refresh").onclick = () => refresh().catch(error);
$("sessions").onchange = () => { selectSession($("sessions").value); poll(); };
$("intent").onsubmit = async e => {
  e.preventDefault(); $("error").textContent = "";
  try { const view = currentObservation(); await api(`/v1/sessions/${view.session}/intent`,"POST",{text:$("goal").value,frame_id:view.frame}); await poll(); }
  catch (e) { error(e); }
};
$("capture").onclick = () => { if (sid) return api(`/v1/sessions/${sid}/capture`,"POST",{}).then(poll).catch(error); };
$("cancel").onclick = () => { if (sid) return api(`/v1/sessions/${sid}/cancel`,"POST",{}).then(poll).catch(error); };
async function confirmAction(approve) {
  try {
    const view = currentObservation();
    if (!view.action || view.action.state !== "awaiting_confirmation" || view.action.based_on_frame !== view.frame) return;
    await api(`/v1/sessions/${view.session}/actions/${view.action.action_id}/confirm`,"POST",{approve}); await poll();
  } catch (e) { error(e); }
}
$("approve").onclick = () => confirmAction(true);
$("reject").onclick = () => confirmAction(false);
$("resolve").onclick = async () => {
  try {
    const view = currentObservation();
    if (!view.action || view.action.state !== "uncertain" || view.frame <= view.action.based_on_frame) return;
    await api(`/v1/sessions/${view.session}/actions/${view.action.action_id}/resolve`,"POST",{resolution:"abandon",frame_id:view.frame,note:"Reviewed in operator dashboard"}); await poll();
  } catch (e) { error(e); }
};
setInterval(() => { if (token) poll(); },1500);
