document.addEventListener("DOMContentLoaded", () => {

// "" en accès direct à Oracle, personnalisable via le header x-oracle-api-base
// si Oracle est servi derrière un reverse-proxy.
const API_BASE = window.ORACLE_API_BASE || "";

// Droits/identité, résolus via /session-info au chargement (cf. plus bas).
let isAdmin = false;
let canUpload = false;
let csrfToken = "";
// Nombre d'échanges gardés en mémoire (réglage admin max_history_turns) --
// 0 tant que /session-info n'a pas répondu : applyContextWindow() ne fait
// rien dans ce cas (pas de faux marquage "hors mémoire" avant d'avoir la
// vraie valeur).
let maxHistoryTurns = 0;

const messagesEl  = document.getElementById("chat-messages");
const inputEl     = document.getElementById("chat-input");
const sendBtn     = document.getElementById("send-btn");
const convListEl  = document.getElementById("conversations-list");
const newChatBtn  = document.getElementById("new-chat-btn");
const topbarTitle = document.getElementById("topbar-title");
const chatAreaEl  = document.querySelector(".oracle-chat-area");
const welcomeTitleEl = document.getElementById("welcome-title");
const settingsToggleBtn0 = document.getElementById("settings-toggle-btn");

marked.setOptions({ breaks: true, gfm: true });

let sessionId = null;  // conversation active

// Réponses en cours de streaming, indexées par session_id -- permet de
// changer de conversation pendant qu'une réponse arrive et de la retrouver
// intacte (avec ce qui a déjà été généré) en y revenant, comme sur Claude.
const activeStreams = new Map(); // session_id -> { userRow, row, body, typingRow }

// Pièce jointe éphémère de la question en cours -- jamais indexée dans Qdrant.
let pendingAttachment = null;    // { filename, text, truncated } une fois extrait
let attachmentLoading = false;

// ── Toast ─────────────────────────────────────────────────────────────────

let toastContainer = null;
function showToast(message, type = "info") {
  if (!toastContainer) {
    toastContainer = document.createElement("div");
    toastContainer.className = "oracle-toast-container";
    document.body.appendChild(toastContainer);
  }
  const toast = document.createElement("div");
  toast.className = "oracle-toast" + (type === "error" ? " error" : "");
  toast.textContent = message;
  toastContainer.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add("visible"));
  setTimeout(() => {
    toast.classList.remove("visible");
    setTimeout(() => toast.remove(), 250);
  }, 4000);
}

// ── Helpers fetch ────────────────────────────────────────────────────────────

function apiFetch(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  if (opts.body && typeof opts.body === "object" && !(opts.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    opts = { ...opts, body: JSON.stringify(opts.body) };
  }
  return fetch(`${API_BASE}/${path}`, { ...opts, headers });
}

// ── Utilitaires DOM ──────────────────────────────────────────────────────────

function escapeHtml(str) {
  return String(str)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;")
    .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function scrollToBottom() { messagesEl.scrollTop = messagesEl.scrollHeight; }

const botAvatarHTML = `<div class="avatar bot">
  <svg viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
</div>`;

function buildSourcesBlock(sources) {
  if (!sources || !sources.length) return null;
  const unique = [...new Set(sources)];

  const fileChips = unique.map(s => {
    const label = s.split("/").pop();
    const isFullPath = s.startsWith("ingestion/");
    const href = `${API_BASE}/documents/serve?object_name=${encodeURIComponent(s)}`;
    return isFullPath
      ? `<a class="source-chip" href="${href}" target="_blank" rel="noopener">
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
          ${escapeHtml(label)}
        </a>`
      : `<span class="source-chip">
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
          ${escapeHtml(label)}
        </span>`;
  });

  const block = document.createElement("div");
  block.className = "sources-block";
  block.innerHTML = "<span>Sources :</span> " + fileChips.join("");
  return block;
}

// Panneau de debug retrieval : admins uniquement, montre tous les candidats
// avec leurs scores (tous sont transmis au LLM, rien n'est écarté).
function renderDebugPanel(debug) {
  const candidates = debug.candidates || [];

  const details = document.createElement("details");
  details.className = "debug-retrieval";

  const summary = document.createElement("summary");
  if (!debug.needs_search) {
    summary.textContent = "Debug retrieval : aucune recherche documentaire";
  } else if (candidates.length === 0) {
    summary.textContent = "Debug retrieval : recherche effectuée, aucun candidat";
  } else {
    summary.textContent = `Debug retrieval : ${candidates.length} candidat(s) transmis au modèle`;
  }
  details.appendChild(summary);

  if (debug.search_query) {
    const q = document.createElement("div");
    q.className = "debug-search-query";
    q.textContent = `Requête de recherche : "${debug.search_query}"`;
    details.appendChild(q);
  }

  for (const c of candidates) {
    const row = document.createElement("div");
    row.className = "debug-candidate";

    const head = document.createElement("div");
    head.className = "debug-candidate-head";
    const label = (c.source_file || "").split("/").pop();
    const vScore = c.vector_score != null ? c.vector_score.toFixed(3) : "?";
    const rScore = c.rerank_score != null ? c.rerank_score.toFixed(3) : "?";
    head.innerHTML = `
      <span class="debug-source">${escapeHtml(label)}${c.page ? ", page " + c.page : ""}</span>
      <span class="debug-scores">vecteur ${vScore} · rerank ${rScore}</span>
    `;
    row.appendChild(head);

    const text = document.createElement("div");
    text.className = "debug-candidate-text";
    text.textContent = c.text || "";
    row.appendChild(text);

    details.appendChild(row);
  }

  return details;
}

// ── Feedback (pouce haut/bas -- retour négatif via fenêtre modale, façon Claude) ──

async function submitFeedback(sessId, messageId, rating, category, comment) {
  try {
    const res = await apiFetch(`chat/${sessId}/messages/${messageId}/feedback`, {
      method: "POST",
      body: { rating, category: category || null, comment: comment || null },
    });
    if (!res.ok) throw new Error(`Erreur serveur (${res.status})`);
    showToast("Merci pour votre retour !", "success");
    return true;
  } catch (err) {
    showToast(`Impossible d'envoyer le retour (${err.message}).`, "error");
    return false;
  }
}

// Style Claude : icônes seules, sans cadre, gris discret au repos, icône
// pleine (remplie) une fois le vote posé -- pas de fond coloré permanent.
function setThumbIcon(btn, outlineClass, fillClass, filled) {
  const icon = btn.querySelector("i");
  icon.className = filled ? `bi ${fillClass}` : `bi ${outlineClass}`;
}

// Fenêtre modale de retour (haut ET bas, façon Claude) : un seul élément
// partagé dans le DOM (cf. index.html), réutilisé pour n'importe quel
// message et n'importe quel sens de vote -- le groupe catégorie n'est
// affiché que pour un retour négatif. Le contexte (session/message/rating/
// boutons concernés) est stocké le temps que la modale soit ouverte plutôt
// que dupliqué par message.
const feedbackModalOverlay       = document.getElementById("feedback-modal-overlay");
const feedbackModalTitleEl       = document.getElementById("feedback-modal-title");
const feedbackModalCategoryGroup = document.getElementById("feedback-modal-category-group");
const feedbackModalCategory      = document.getElementById("feedback-modal-category");
const feedbackModalComment       = document.getElementById("feedback-modal-comment");
const feedbackModalCancel        = document.getElementById("feedback-modal-cancel");
const feedbackModalSubmit        = document.getElementById("feedback-modal-submit");

let feedbackModalContext = null; // {sessId, messageId, rating, upBtn, downBtn}

function openFeedbackModal(sessId, messageId, rating, upBtn, downBtn, existingFeedback = null) {
  feedbackModalContext = { sessId, messageId, rating, upBtn, downBtn };

  if (rating === "up") {
    feedbackModalTitleEl.textContent = "Donner un retour positif";
    feedbackModalCategoryGroup.style.display = "none";
    feedbackModalComment.placeholder = "Dans quelle mesure cette réponse était-elle satisfaisante ?";
  } else {
    feedbackModalTitleEl.textContent = "Donner un retour négatif";
    feedbackModalCategoryGroup.style.display = "flex";
    feedbackModalComment.placeholder = "Dans quelle mesure cette réponse était-elle insatisfaisante ?";
  }

  // Ne pré-remplit que si le vote existant correspond au même sens -- sinon
  // on garderait par exemple un commentaire "négatif" affiché dans la
  // modale "positif" si l'utilisateur change d'avis.
  const matchesExisting = existingFeedback && existingFeedback.rating === rating;
  feedbackModalCategory.value = (matchesExisting && existingFeedback.category) || "";
  feedbackModalComment.value = (matchesExisting && existingFeedback.comment) || "";

  feedbackModalOverlay.classList.add("visible");
}

function closeFeedbackModal() {
  feedbackModalOverlay.classList.remove("visible");
  feedbackModalContext = null;
}

if (feedbackModalCancel) feedbackModalCancel.addEventListener("click", closeFeedbackModal);
if (feedbackModalOverlay) {
  feedbackModalOverlay.addEventListener("click", (e) => {
    if (e.target === feedbackModalOverlay) closeFeedbackModal();
  });
}
if (feedbackModalSubmit) {
  feedbackModalSubmit.addEventListener("click", async () => {
    if (!feedbackModalContext) return;
    const { sessId, messageId, rating, upBtn, downBtn } = feedbackModalContext;
    const category = rating === "down" ? (feedbackModalCategory.value || null) : null;
    const ok = await submitFeedback(sessId, messageId, rating, category, feedbackModalComment.value.trim());
    if (ok) {
      upBtn.classList.toggle("active", rating === "up");
      downBtn.classList.toggle("active", rating === "down");
      setThumbIcon(upBtn, "bi-hand-thumbs-up", "bi-hand-thumbs-up-fill", rating === "up");
      setThumbIcon(downBtn, "bi-hand-thumbs-down", "bi-hand-thumbs-down-fill", rating === "down");
      closeFeedbackModal();
    }
  });
}

function renderFeedbackBar(sessId, messageId, existingFeedback = null) {
  if (!messageId) return null;

  const wrap = document.createElement("div");
  wrap.className = "msg-feedback";

  const upBtn = document.createElement("button");
  upBtn.className = "feedback-btn up";
  upBtn.type = "button";
  upBtn.title = "Réponse utile";
  upBtn.innerHTML = `<i class="bi bi-hand-thumbs-up"></i>`;

  const downBtn = document.createElement("button");
  downBtn.className = "feedback-btn down";
  downBtn.type = "button";
  downBtn.title = "Réponse à améliorer";
  downBtn.innerHTML = `<i class="bi bi-hand-thumbs-down"></i>`;

  upBtn.addEventListener("click", () => {
    openFeedbackModal(sessId, messageId, "up", upBtn, downBtn, existingFeedback);
  });

  downBtn.addEventListener("click", () => {
    openFeedbackModal(sessId, messageId, "down", upBtn, downBtn, existingFeedback);
  });

  // Restitue l'état d'un vote déjà posé (rechargement de conversation).
  if (existingFeedback && existingFeedback.rating === "up") {
    upBtn.classList.add("active");
    setThumbIcon(upBtn, "bi-hand-thumbs-up", "bi-hand-thumbs-up-fill", true);
  } else if (existingFeedback && existingFeedback.rating === "down") {
    downBtn.classList.add("active");
    setThumbIcon(downBtn, "bi-hand-thumbs-down", "bi-hand-thumbs-down-fill", true);
  }

  wrap.appendChild(upBtn);
  wrap.appendChild(downBtn);
  return wrap;
}

function addUserMessage(text, attachmentFilename = null) {
  const row = document.createElement("div");
  row.className = "msg-row user";
  const attachmentHtml = attachmentFilename
    ? `<div class="msg-attachment-chip"><i class="bi bi-paperclip"></i>${escapeHtml(attachmentFilename)}</div>`
    : "";
  row.innerHTML = `<div class="msg-body">${attachmentHtml}${escapeHtml(text)}</div>`;
  messagesEl.appendChild(row);
  scrollToBottom();
  return row;
}

function addAssistantMessage(text, sources = [], messageId = null, feedback = null) {
  const row = document.createElement("div");
  row.className = "msg-row assistant";

  const body = document.createElement("div");
  body.className = "msg-body";
  body.innerHTML = marked.parse(text);

  const srcBlock = buildSourcesBlock(sources);
  if (srcBlock) body.appendChild(srcBlock);

  const feedbackBar = renderFeedbackBar(sessionId, messageId, feedback);
  if (feedbackBar) body.appendChild(feedbackBar);

  row.innerHTML = botAvatarHTML;
  row.appendChild(body);
  messagesEl.appendChild(row);
  scrollToBottom();
}

function buildTypingRow() {
  const row = document.createElement("div");
  row.className = "msg-row assistant";
  row.id = "typing-row";
  row.innerHTML = botAvatarHTML + `<div class="msg-body"><div class="typing-indicator"><span></span><span></span><span></span></div></div>`;
  return row;
}

function showTyping() {
  const row = buildTypingRow();
  messagesEl.appendChild(row);
  scrollToBottom();
  return row;
}

function removeTyping(el = document.getElementById("typing-row")) {
  if (el) el.remove();
}

function setWelcomeMode(active) {
  if (chatAreaEl) chatAreaEl.classList.toggle("welcome-mode", active);
}

function clearMessages() {
  messagesEl.innerHTML = "";
  setWelcomeMode(true);
}

// ── Panneau conversations ────────────────────────────────────────────────────

const PENCIL_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>`;
const CROSS_SVG  = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
const TRASH_SVG  = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>`;

function formatDate(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 86400 && now.getDate() === d.getDate()) return "Aujourd'hui";
  if (diff < 172800) return "Hier";
  return d.toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
}

function startRenaming(item, sid, titleEl) {
  if (item.classList.contains("editing")) return;
  item.classList.add("editing");

  const input = document.createElement("input");
  input.className = "oracle-conv-rename-input";
  input.value = titleEl.textContent === "Nouvelle conversation" ? "" : titleEl.textContent;
  input.placeholder = "Nom de la conversation";
  input.maxLength = 60;

  let committed = false;

  const doSave = async () => {
    if (committed) return;
    committed = true;
    const newTitle = input.value.trim() || "Nouvelle conversation";
    input.remove();
    item.classList.remove("editing");
    titleEl.textContent = newTitle;
    if (sid === sessionId) topbarTitle.textContent = newTitle;
    try {
      await apiFetch(`chat/conversations/${sid}/title`, {
        method: "PATCH",
        body: { title: newTitle },
      });
    } catch {}
  };

  const doCancel = () => {
    committed = true;
    input.remove();
    item.classList.remove("editing");
  };

  input.addEventListener("blur", doSave);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter")  { e.preventDefault(); input.blur(); }
    if (e.key === "Escape") { doCancel(); }
  });

  titleEl.insertAdjacentElement("afterend", input);
  input.focus();
  input.select();
}

function createConvItemEl(sid, title) {
  const item = document.createElement("div");
  item.className = "oracle-conv-item";
  item.dataset.sessionId = sid;

  const titleEl = document.createElement("div");
  titleEl.className = "oracle-conv-item-title";
  titleEl.textContent = title || "Nouvelle conversation";

  const renameBtn = document.createElement("button");
  renameBtn.className = "oracle-conv-rename-btn";
  renameBtn.title = "Renommer";
  renameBtn.innerHTML = PENCIL_SVG;
  renameBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    startRenaming(item, sid, titleEl);
  });

  const delBtn = document.createElement("button");
  delBtn.className = "oracle-conv-delete-btn";
  delBtn.title = "Supprimer";
  delBtn.innerHTML = CROSS_SVG;
  delBtn.addEventListener("click", async (e) => {
    e.stopPropagation();
    await deleteConversation(sid);
  });

  item.appendChild(titleEl);
  item.appendChild(renameBtn);
  item.appendChild(delBtn);
  item.addEventListener("click", () => openConversation(sid));
  return item;
}

function setActiveConvItem(sid) {
  convListEl.querySelectorAll(".oracle-conv-item").forEach(el => {
    el.classList.toggle("active", el.dataset.sessionId === sid);
  });
}

function updateConvTitle(sid, title) {
  const item = convListEl.querySelector(`.oracle-conv-item[data-session-id="${sid}"]`);
  if (item) {
    const titleEl = item.querySelector(".oracle-conv-item-title");
    if (titleEl) titleEl.textContent = title || "Nouvelle conversation";
  }
  if (sid === sessionId) {
    topbarTitle.textContent = title || "Assistant IA";
  }
}

async function loadConversations() {
  try {
    const res = await apiFetch("chat/conversations");
    if (!res.ok) throw new Error();
    const { conversations } = await res.json();
    renderConversationList(conversations);

    const saved = localStorage.getItem("oracle_session_id");
    const exists = conversations.find(c => c.session_id === saved);
    if (exists) {
      await openConversation(saved, false);
    } else if (conversations.length > 0) {
      await openConversation(conversations[0].session_id, false);
    } else {
      await startNewConversation();
    }
  } catch {
    await startNewConversation();
  }
}

function renderConversationList(conversations) {
  convListEl.innerHTML = "";
  if (!conversations.length) {
    convListEl.innerHTML = `<p class="oracle-conv-empty">Aucune conversation</p>`;
    return;
  }

  let lastDateLabel = null;
  for (const conv of conversations) {
    const dateLabel = formatDate(conv.last_activity);
    if (dateLabel !== lastDateLabel) {
      const sep = document.createElement("div");
      sep.className = "oracle-conv-date-label";
      sep.textContent = dateLabel;
      convListEl.appendChild(sep);
      lastDateLabel = dateLabel;
    }
    const item = createConvItemEl(conv.session_id, conv.title);
    if (conv.session_id === sessionId) item.classList.add("active");
    convListEl.appendChild(item);
  }
}

async function startNewConversation() {
  try {
    const res = await apiFetch("chat/conversations", { method: "POST" });
    if (!res.ok) throw new Error();
    const data = await res.json();
    sessionId = data.session_id;
  } catch {
    sessionId = crypto.randomUUID();
  }
  localStorage.setItem("oracle_session_id", sessionId);
  setActiveConvItem(sessionId);
  topbarTitle.textContent = "Assistant IA";

  const existing = convListEl.querySelector(`.oracle-conv-item[data-session-id="${sessionId}"]`);
  if (!existing) prependConvItem(sessionId, "Nouvelle conversation");

  sendBtn.disabled = false; // nouvelle session, jamais de génération en cours dessus
  clearMessages();
}

function prependConvItem(sid, title) {
  const firstLabel = convListEl.querySelector(".oracle-conv-date-label");
  if (!firstLabel || firstLabel.textContent !== "Aujourd'hui") {
    const sep = document.createElement("div");
    sep.className = "oracle-conv-date-label";
    sep.textContent = "Aujourd'hui";
    convListEl.prepend(sep);
  }

  const item = createConvItemEl(sid, title);
  item.classList.add("active");

  const todayLabel = convListEl.querySelector(".oracle-conv-date-label");
  if (todayLabel && todayLabel.textContent === "Aujourd'hui") {
    todayLabel.insertAdjacentElement("afterend", item);
  } else {
    convListEl.prepend(item);
  }

  setActiveConvItem(sid);

  const empty = convListEl.querySelector(".oracle-conv-empty");
  if (empty) empty.remove();
}

async function openConversation(sid, reload = true) {
  sessionId = sid;
  localStorage.setItem("oracle_session_id", sid);
  setActiveConvItem(sid);

  const stream = activeStreams.get(sid);

  try {
    const res = await apiFetch(`chat/conversations/${sid}/history`);
    if (!res.ok) throw new Error();
    const conv = await res.json();
    topbarTitle.textContent = conv.title || "Assistant IA";
    renderHistory(conv.messages || []);
  } catch {
    clearMessages();
  }

  // Une génération est en cours pour cette conversation (démarrée avant
  // qu'on la quitte) : elle n'est pas encore persistée côté serveur (add_turn
  // n'a lieu qu'à la fin), donc absente de l'historique ci-dessus -- on
  // réattache ici le DOM déjà accumulé (question + réponse partielle).
  sendBtn.disabled = !!stream;
  if (stream) {
    if (stream.userRow) messagesEl.appendChild(stream.userRow);
    if (stream.row) {
      messagesEl.appendChild(stream.row);
    } else if (stream.typingRow) {
      messagesEl.appendChild(stream.typingRow);
    }
    setWelcomeMode(false);
    scrollToBottom();
  }
}

async function deleteConversation(sid) {
  try {
    await apiFetch(`chat/conversations/${sid}`, { method: "DELETE" });
  } catch {}

  const item = convListEl.querySelector(`.oracle-conv-item[data-session-id="${sid}"]`);
  if (item) item.remove();

  convListEl.querySelectorAll(".oracle-conv-date-label").forEach(label => {
    const next = label.nextElementSibling;
    if (!next || next.classList.contains("oracle-conv-date-label")) label.remove();
  });

  if (sid === sessionId) {
    const first = convListEl.querySelector(".oracle-conv-item");
    if (first) {
      await openConversation(first.dataset.sessionId);
    } else {
      await startNewConversation();
    }
  }
}

function renderHistory(messages) {
  if (!messages.length) { clearMessages(); return; }
  messagesEl.innerHTML = "";
  setWelcomeMode(false);
  for (const msg of messages) {
    if (msg.role === "user") {
      addUserMessage(msg.content, msg.attachment_filename || null);
    } else {
      addAssistantMessage(
        msg.content,
        msg.sources || [],
        msg.message_id || null,
        msg.feedback || null
      );
    }
  }
  applyContextWindow();
}

// Marque/anime les messages qui sortent de la fenêtre de mémoire active
// (réglage admin max_history_turns, cf. memory.py add_turn()) : le backend
// tronque réellement l'historique Redis à max_history_turns*2 messages, donc
// une conversation rechargée depuis le serveur ne contient déjà plus que ce
// qui est "en mémoire" -- mais pendant une session en cours (sans recharger
// la page), le front garde tous les messages affichés localement même après
// que le backend les ait oubliés. Cette fonction rend cette différence
// visible : les messages au-delà de la fenêtre sont grisés, avec une
// animation la première fois qu'ils en sortent.
function applyContextWindow() {
  if (!maxHistoryTurns) return;
  const maxMessages = maxHistoryTurns * 2;
  const rows = Array.from(messagesEl.querySelectorAll(".msg-row"));
  const cutoff = rows.length - maxMessages;

  rows.forEach((row, i) => {
    const shouldBeOut = i < cutoff;
    const alreadyOut = row.classList.contains("out-of-context");
    if (shouldBeOut && !alreadyOut) {
      // Vient de sortir de la fenêtre : animation, puis état grisé permanent.
      row.classList.add("just-evicted");
      row.classList.add("out-of-context");
      setTimeout(() => row.classList.remove("just-evicted"), 700);
    } else if (shouldBeOut) {
      row.classList.add("out-of-context");
    } else {
      row.classList.remove("out-of-context", "just-evicted");
    }
  });
}

// ── Pièce jointe éphémère (glisser-déposer ou trombone) ─────────────────────
// Contrairement à la dropzone d'upload, jamais indexée : envoyée puis oubliée.

const chatInputArea        = document.getElementById("chat-input-area");
const attachBtn             = document.getElementById("attach-btn");
const chatFileInput         = document.getElementById("chat-file-input");
const pendingAttachmentChip = document.getElementById("pending-attachment-chip");

// Même liste que la dropzone d'upload (app/ingestion/routes.py::ALLOWED_EXTENSIONS côté Oracle).
const ATTACHMENT_ACCEPTED_EXTENSIONS = /\.(pdf|docx|pptx|xlsx|png|jpe?g)$/i;

function renderPendingAttachmentChip() {
  if (!pendingAttachmentChip) return;
  if (attachmentLoading) {
    pendingAttachmentChip.style.display = "flex";
    pendingAttachmentChip.innerHTML = `
      <span class="attachment-chip loading">
        <span class="status-dot dot-processing"></span>Lecture du fichier…
      </span>`;
    return;
  }
  if (!pendingAttachment) {
    pendingAttachmentChip.style.display = "none";
    pendingAttachmentChip.innerHTML = "";
    return;
  }
  pendingAttachmentChip.style.display = "flex";
  pendingAttachmentChip.innerHTML = `
    <span class="attachment-chip">
      <i class="bi bi-paperclip"></i>${escapeHtml(pendingAttachment.filename)}
      <button type="button" class="attachment-chip-remove" title="Retirer">
        <i class="bi bi-x"></i>
      </button>
    </span>`;
  pendingAttachmentChip.querySelector(".attachment-chip-remove").addEventListener("click", () => {
    pendingAttachment = null;
    renderPendingAttachmentChip();
  });
}

async function attachFile(file) {
  if (!ATTACHMENT_ACCEPTED_EXTENSIONS.test(file.name)) {
    showToast("Format non supporté (PDF, Word, TXT, JSON, Markdown).", "error");
    return;
  }
  attachmentLoading = true;
  pendingAttachment = null;
  renderPendingAttachmentChip();

  const fd = new FormData();
  fd.append("file", file);
  try {
    const res = await fetch(`${API_BASE}/documents/extract-preview`, {
      method: "POST",
      body: fd,
      headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {},
    });
    if (!res.ok) throw new Error();
    const data = await res.json();
    pendingAttachment = { filename: data.filename, text: data.text, truncated: data.truncated };
  } catch {
    pendingAttachment = null;
    showToast("Impossible de lire ce fichier.", "error");
  } finally {
    attachmentLoading = false;
    renderPendingAttachmentChip();
  }
}

if (attachBtn && chatFileInput) {
  attachBtn.addEventListener("click", () => chatFileInput.click());
  chatFileInput.addEventListener("change", () => {
    if (chatFileInput.files[0]) attachFile(chatFileInput.files[0]);
    chatFileInput.value = ""; // permet de re-sélectionner le même fichier ensuite
  });
}

if (chatInputArea) {
  ["dragenter", "dragover"].forEach(evt => {
    chatInputArea.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      chatInputArea.classList.add("dragover-attach");
    });
  });

  ["dragleave", "dragend"].forEach(evt => {
    chatInputArea.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      chatInputArea.classList.remove("dragover-attach");
    });
  });

  chatInputArea.addEventListener("drop", (e) => {
    e.preventDefault();
    e.stopPropagation();
    chatInputArea.classList.remove("dragover-attach");
    const files = e.dataTransfer.files;
    if (!files.length) return;
    // Un seul fichier à la fois pour l'instant : on prend le premier et on
    // prévient si plusieurs ont été déposés plutôt que de les ignorer en silence.
    if (files.length > 1) {
      showToast("Un seul fichier à la fois -- le premier a été pris en compte.", "error");
    }
    attachFile(files[0]);
  });
}

// ── Envoi de message ─────────────────────────────────────────────────────────

async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text) return;

  if (attachmentLoading) {
    showToast("Le fichier est encore en cours de lecture…", "error");
    return;
  }

  const attachmentToSend = pendingAttachment; // capturé avant réinitialisation
  const requestSessionId = sessionId; // capturé ici : la génération continue
  // en arrière-plan même si l'utilisateur change de conversation --
  // sessionId (variable globale) peut changer pendant l'attente.
  const isViewingThisSession = () => sessionId === requestSessionId;

  setWelcomeMode(false);
  const userRow = addUserMessage(text, attachmentToSend?.filename || null);
  inputEl.value = "";
  autoResize();
  sendBtn.disabled = true;
  const typingRow = showTyping();

  // Enregistrée dès maintenant : si l'utilisateur quitte puis revient sur
  // cette conversation avant la fin de la génération, openConversation()
  // la retrouve ici et réattache exactement où elle en est.
  const stream = { userRow, row: null, body: null, typingRow };
  activeStreams.set(requestSessionId, stream);

  pendingAttachment = null;
  renderPendingAttachmentChip();

  // Titre provisoire si première question de la conversation
  const item = convListEl.querySelector(`.oracle-conv-item[data-session-id="${requestSessionId}"]`);
  const titleEl = item?.querySelector(".oracle-conv-item-title");
  const isNew = titleEl && titleEl.textContent === "Nouvelle conversation";

  try {
    const res = await apiFetch("chat/new", {
      method: "POST",
      body: {
        session_id: requestSessionId,
        message: text,
        ...(attachmentToSend
          ? { attachment: { filename: attachmentToSend.filename, text: attachmentToSend.text } }
          : {}),
      },
    });

    if (!res.ok) throw new Error(`Erreur serveur (${res.status})`);

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let fullAnswer = "";
    let sources = [];
    let debugRetrieval = null;
    let timing = null;
    let messageId = null;

    const ensureRow = () => {
      if (stream.row) return;
      stream.row = document.createElement("div");
      stream.row.className = "msg-row assistant";
      stream.row.innerHTML = botAvatarHTML;
      stream.body = document.createElement("div");
      stream.body.className = "msg-body";
      stream.row.appendChild(stream.body);
      if (isViewingThisSession()) {
        removeTyping(stream.typingRow);
        setWelcomeMode(false);
        messagesEl.appendChild(stream.row);
      }
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const raw = line.slice(6).trim();
        if (raw === "[DONE]") break;
        try {
          const data = JSON.parse(raw);
          if (data.token) {
            ensureRow();
            if (!fullAnswer) removeTyping(stream.typingRow);
            fullAnswer += data.token;
            let answerEl = stream.body.querySelector(".msg-answer-text");
            if (!answerEl) {
              answerEl = document.createElement("div");
              answerEl.className = "msg-answer-text";
              stream.body.appendChild(answerEl);
            }
            answerEl.innerHTML = marked.parse(fullAnswer);
            if (isViewingThisSession()) scrollToBottom();
          }
          if (data.sources) sources = data.sources;
          if (data.debug_retrieval) debugRetrieval = data.debug_retrieval;
          if (data.timing) timing = data.timing;
          if (data.message_id) messageId = data.message_id;
          if (data.session_id && data.session_id !== requestSessionId) {
            // Le serveur a remplacé le session_id fourni (ex: localStorage
            // de ce navigateur encore sur l'id d'un précédent utilisateur --
            // cf. fix isolation des conversations par utilisateur, côté
            // Oracle : chat_stream()/session_belongs_to()). On persiste le
            // nouvel id pour que le prochain chargement reparte du bon
            // endroit ; la vue en cours n'est pas perturbée.
            localStorage.setItem("oracle_session_id", data.session_id);
          }
        } catch {}
      }
    }

    // Toujours appliqué à stream.body -- qu'elle soit attachée ou non, le
    // contenu est déjà à jour dès que l'utilisateur revient dessus.
    const body = stream.body;

    // Sources cliquables
    if (sources.length > 0 && body) {
      const srcBlock = buildSourcesBlock(sources);
      if (srcBlock) body.appendChild(srcBlock);
    }

    // Debug retrieval (admin uniquement, test/calibrage -- cf. renderDebugPanel()) :
    // en plus du panneau ci-dessus, montre aussi les candidats écartés et leurs scores.
    if (isAdmin && debugRetrieval && body) {
      body.appendChild(renderDebugPanel(debugRetrieval));
    }

    // Temps de réponse, visible par tous, jamais persisté.
    if (timing && body) {
      const timingEl = document.createElement("div");
      timingEl.className = "msg-timing";
      timingEl.innerHTML = `<i class="bi bi-stopwatch"></i> Répondu en ${timing.total_seconds}s`;
      body.appendChild(timingEl);
    }

    // Mise à jour du titre (premier message) -- l'item de la sidebar existe
    // quelle que soit la conversation affichée, mais le topbar ne concerne
    // que celle actuellement à l'écran.
    if (isNew && titleEl) {
      const newTitle = text.length > 55 ? text.slice(0, 52) + "…" : text;
      titleEl.textContent = newTitle;
      if (isViewingThisSession()) topbarTitle.textContent = newTitle;
    }

    // Feedback (thumbs up/down) -- messageId vient du final_payload SSE
    // (généré par generate_stream_answer(), même ID que celui persisté
    // ensuite côté serveur par add_turn()).
    if (body) {
      const feedbackBar = renderFeedbackBar(requestSessionId, messageId, null);
      if (feedbackBar) body.appendChild(feedbackBar);
    }

    if (isViewingThisSession()) {
      scrollToBottom();
      applyContextWindow();
    }
  } catch (err) {
    removeTyping(stream.typingRow);
    if (isViewingThisSession()) {
      addAssistantMessage(`Une erreur est survenue (${err.message}).`);
    }
  } finally {
    activeStreams.delete(requestSessionId);
    if (isViewingThisSession()) {
      sendBtn.disabled = false;
      inputEl.focus();
    }
  }
}

// ── Resize textarea ──────────────────────────────────────────────────────────

function autoResize() {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(inputEl.scrollHeight, 160) + "px";
}

inputEl.addEventListener("input", () => {
  autoResize();
  sendBtn.disabled = inputEl.value.trim().length === 0;
});

inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    if (!sendBtn.disabled) sendMessage();
  }
});

sendBtn.addEventListener("click", sendMessage);
newChatBtn.addEventListener("click", startNewConversation);

// ── Upload (dropzone + liste indexée -- vit dans l'onglet "Documents" de la
//    modale de paramètres, cf. section suivante) ───────────────────────────

const dropzone         = document.getElementById("dropzone");
const fileInput        = document.getElementById("file-input");
const uploadStatusList = document.getElementById("upload-status-list");
const indexedDocsList  = document.getElementById("indexed-docs-list");
const refreshDocsBtn   = document.getElementById("refresh-docs-btn");

if (refreshDocsBtn) {
  refreshDocsBtn.addEventListener("click", loadIndexedDocuments);
}

// ── Paramètres (modale façon Claude : sidebar de sections) ─────────────────
// "Général" (admin) : réglages RAG, un seul onglet listant tout, groupé par
// section. "Documents" (droit oracle_upload) : ajout + liste indexée, portés
// depuis l'ancien panneau upload autonome de la topbar.
//
// RAG_SETTINGS_FIELDS déclaré une seule fois ici : sert à la fois à générer
// le formulaire et à collecter les valeurs à l'enregistrement. Doit rester
// synchronisé avec RagSettingsUpdate côté app/settings/routes.py.

const RAG_SETTINGS_GROUPS = [
  { key: "ingestion", label: "Ingestion" },
  { key: "retrieval", label: "Recherche" },
  { key: "conversation", label: "Conversation" },
  { key: "prompts", label: "Prompts" },
];

const RAG_SETTINGS_FIELDS = [
  { key: "chunk_size", label: "Taille des chunks (caractères)", tab: "ingestion", type: "range", min: 100, max: 4000, step: 50,
    help: "Longueur d'un chunk de texte découpé dans un document indexé." },
  { key: "chunk_overlap", label: "Chevauchement des chunks (caractères)", tab: "ingestion", type: "range", min: 0, max: 1000, step: 10,
    help: "Chevauchement (« overlap ») entre deux chunks consécutifs, pour éviter qu'une information ne soit coupée pile à la frontière entre deux chunks." },
  { key: "max_file_size_mb", label: "Taille max d'un fichier (Mo)", tab: "ingestion", type: "range", min: 1, max: 1000, step: 5,
    help: "Taille maximale acceptée pour un document envoyé, que ce soit un ajout permanent à la base ou une pièce jointe éphémère jointe à une question." },

  { key: "top_k_retrieval", label: "Candidats recherchés (top_k retrieval)", tab: "retrieval", type: "range", min: 1, max: 200, step: 1,
    help: "Nombre de chunks remontés par la recherche vectorielle, tous transmis au modèle (le reranker les trie mais ne filtre plus rien)." },

  { key: "max_history_turns", label: "Échanges conservés en mémoire", tab: "conversation", type: "range", min: 1, max: 200, step: 1,
    help: "Nombre d'échanges (question + réponse) conservés dans l'historique d'une conversation avant que les plus anciens ne soient oubliés." },
  { key: "conversation_ttl_days", label: "Expiration des conversations (jours)", tab: "conversation", type: "range", min: 1, max: 365, step: 1,
    help: "Durée d'inactivité après laquelle une conversation (historique et titre) est supprimée automatiquement." },
  { key: "attachment_max_chars", label: "Taille max d'une pièce jointe (caractères)", tab: "conversation", type: "range", min: 500, max: 200000, step: 500,
    help: "Nombre de caractères maximum extraits d'une pièce jointe éphémère avant troncature." },
  { key: "temperature", label: "Température du modèle", tab: "conversation", type: "range", min: 0, max: 2, step: 0.1,
    help: "Contrôle la créativité du modèle : une valeur basse donne des réponses plus déterministes et factuelles, une valeur haute des réponses plus variées." },

  { key: "oracle_identity", label: "Identité d'Oracle", tab: "prompts", type: "textarea",
    help: "Préfixée à tous les prompts système (grounding, pièce jointe, reformulation, classification)." },
  { key: "system_prompt", label: "Prompt système (documents internes)", tab: "prompts", type: "textarea",
    help: "Corps du prompt système utilisé pour les réponses basées sur les documents internes (grounding, attribution des données à la bonne entité, gestion des recherches infructueuses)." },
  { key: "system_prompt_attachment", label: "Prompt système (pièce jointe utilisateur)", tab: "prompts", type: "textarea",
    help: "Corps du prompt système utilisé quand la question porte sur une pièce jointe fournie directement par l'utilisateur -- plus permissif que le prompt documents internes." },
  { key: "rewrite_prompt", label: "Prompt de reformulation de question", tab: "prompts", type: "textarea",
    help: "Consigne donnée au modèle pour reformuler la question en une question autonome. L'historique et la question sont ajoutés automatiquement après ce texte." },
  { key: "classify_prompt", label: "Prompt de classification / ciblage de document", tab: "prompts", type: "textarea",
    help: "Consigne donnée au modèle pour décider si une recherche documentaire est nécessaire et cibler un document précis. La liste des documents et la question sont ajoutées automatiquement après ce texte -- garde les 3 formes de réponse attendues (NON / OUI: ... / OUI: ... | DOCUMENT: ...)." },
];

// Sections de la sidebar -- "right" évalué à l'ouverture (isAdmin/canUpload
// résolus par initSession avant que le bouton ne soit même visible).
const SETTINGS_SECTIONS = [
  { key: "general", label: "Général", icon: "bi-sliders", right: () => isAdmin },
  { key: "documents", label: "Documents", icon: "bi-file-earmark-arrow-up", right: () => canUpload },
  { key: "feedback", label: "Retours", icon: "bi-hand-thumbs-up", right: () => isAdmin },
];

const settingsOverlay        = document.getElementById("settings-modal-overlay");
const settingsCloseBtn       = document.getElementById("settings-close-btn");
const settingsCancelBtn      = document.getElementById("settings-cancel-btn");
const settingsSaveBtn        = document.getElementById("settings-save-btn");
const settingsResetBtn       = document.getElementById("settings-reset-btn");
const settingsSidebarEl      = document.getElementById("settings-sidebar");
const settingsFooterEl       = document.getElementById("settings-footer");
const settingsPanelGeneral   = document.getElementById("settings-panel-general");
const settingsPanelDocuments = document.getElementById("settings-panel-documents");
const settingsPanelFeedback  = document.getElementById("settings-panel-feedback");
const feedbackAdminListEl    = document.getElementById("feedback-admin-list");

let activeSettingsSection = "general";
let settingsGeneralLoaded = false;
let feedbackAdminRatingFilter = "";

function availableSettingsSections() {
  return SETTINGS_SECTIONS.filter((s) => s.right());
}

function renderSettingsSidebar() {
  const sections = availableSettingsSections();
  settingsSidebarEl.innerHTML = sections.map((s) =>
    `<button class="oracle-modal-navitem${s.key === activeSettingsSection ? " active" : ""}" data-section="${s.key}">
       <i class="bi ${s.icon}"></i> ${s.label}
     </button>`
  ).join("");
  settingsSidebarEl.querySelectorAll(".oracle-modal-navitem").forEach((btn) => {
    btn.addEventListener("click", () => switchSettingsSection(btn.dataset.section));
  });
  return sections;
}

function switchSettingsSection(section) {
  activeSettingsSection = section;
  settingsSidebarEl.querySelectorAll(".oracle-modal-navitem").forEach((b) => {
    b.classList.toggle("active", b.dataset.section === section);
  });
  settingsPanelGeneral.classList.toggle("active", section === "general");
  settingsPanelDocuments.classList.toggle("active", section === "documents");
  settingsPanelFeedback.classList.toggle("active", section === "feedback");
  settingsFooterEl.style.display = section === "general" ? "flex" : "none";
  if (section === "documents") {
    loadIndexedDocuments();
  } else if (section === "general" && !settingsGeneralLoaded) {
    loadGeneralSettings();
  } else if (section === "feedback") {
    loadFeedbackAdmin();
  }
}

async function loadGeneralSettings() {
  settingsPanelGeneral.innerHTML = `<div class="oracle-settings-loading">Chargement…</div>`;
  try {
    const res = await apiFetch("settings/rag");
    if (!res.ok) throw new Error();
    renderSettingsForm(await res.json());
    settingsGeneralLoaded = true;
  } catch {
    settingsPanelGeneral.innerHTML = `<div class="oracle-settings-loading">Impossible de charger les réglages.</div>`;
  }
}

function renderSettingsForm(settings) {
  settingsPanelGeneral.innerHTML = RAG_SETTINGS_GROUPS.map((group) => {
    const fieldsHtml = RAG_SETTINGS_FIELDS.filter((f) => f.tab === group.key).map((f) => {
      const value = settings[f.key];
      const help = f.help ? `<p class="oracle-settings-help">${escapeHtml(f.help)}</p>` : "";
      if (f.type === "textarea") {
        return `<div class="oracle-settings-field">
          <label for="setting-${f.key}">${escapeHtml(f.label)}</label>
          <textarea id="setting-${f.key}" rows="8">${escapeHtml(value ?? "")}</textarea>
          ${help}
        </div>`;
      }
      return `<div class="oracle-settings-field">
        <label for="setting-${f.key}">${escapeHtml(f.label)} <span class="oracle-settings-value" id="setting-${f.key}-value">${value}</span></label>
        <input type="range" id="setting-${f.key}" value="${value ?? ""}" min="${f.min}" max="${f.max}" step="${f.step}">
        ${help}
      </div>`;
    }).join("");
    return `<div class="oracle-settings-group-title">${escapeHtml(group.label)}</div>${fieldsHtml}`;
  }).join("");

  // Valeur affichée à côté du label, mise à jour en direct pendant qu'on glisse le curseur.
  settingsPanelGeneral.querySelectorAll('input[type="range"]').forEach((input) => {
    input.addEventListener("input", () => {
      const valueEl = document.getElementById(`${input.id}-value`);
      if (valueEl) valueEl.textContent = input.value;
    });
  });
}

function collectSettingsPayload() {
  const payload = {};
  for (const f of RAG_SETTINGS_FIELDS) {
    const el = document.getElementById(`setting-${f.key}`);
    if (!el) continue;
    payload[f.key] = f.type === "textarea" ? el.value : parseFloat(el.value);
  }
  return payload;
}

// ── Retours (feedback) : écran admin de consultation ────────────────────────

const FEEDBACK_CATEGORY_LABELS = {
  trop_long: "Trop long",
  incorrect: "Incorrect",
  bug: "Bug",
  autre: "Autre",
};

if (settingsPanelFeedback) {
  settingsPanelFeedback.querySelectorAll(".feedback-admin-filter").forEach((btn) => {
    btn.addEventListener("click", () => {
      feedbackAdminRatingFilter = btn.dataset.rating || "";
      settingsPanelFeedback.querySelectorAll(".feedback-admin-filter").forEach((b) => {
        b.classList.toggle("active", b === btn);
      });
      loadFeedbackAdmin();
    });
  });
}

async function loadFeedbackAdmin() {
  feedbackAdminListEl.innerHTML = `<div class="oracle-settings-loading">Chargement…</div>`;
  try {
    const qs = feedbackAdminRatingFilter ? `?rating=${feedbackAdminRatingFilter}` : "";
    const res = await apiFetch(`chat/feedback${qs}`);
    if (!res.ok) throw new Error();
    const data = await res.json();
    renderFeedbackAdminList(data.feedback || []);
  } catch {
    feedbackAdminListEl.innerHTML = `<div class="oracle-settings-loading">Impossible de charger les retours.</div>`;
  }
}

function renderFeedbackAdminList(entries) {
  if (!entries.length) {
    feedbackAdminListEl.innerHTML = `<div class="oracle-settings-loading">Aucun retour pour l'instant.</div>`;
    return;
  }

  feedbackAdminListEl.innerHTML = "";
  for (const e of entries) {
    const row = document.createElement("div");
    row.className = "feedback-admin-row";

    const ratingBadge = e.rating === "up"
      ? `<span class="feedback-admin-badge up"><i class="bi bi-hand-thumbs-up"></i></span>`
      : `<span class="feedback-admin-badge down"><i class="bi bi-hand-thumbs-down"></i></span>`;
    const categoryBadge = e.category
      ? `<span class="feedback-admin-category">${escapeHtml(FEEDBACK_CATEGORY_LABELS[e.category] || e.category)}</span>`
      : "";
    const date = e.created_at ? new Date(parseFloat(e.created_at) * 1000).toLocaleString("fr-FR") : "";

    const question = e.question
      ? `<div class="feedback-admin-question">${escapeHtml(e.question)}</div>`
      : "";
    const answer = e.answer
      ? `<div class="feedback-admin-answer">${escapeHtml(e.answer)}</div>`
      : `<div class="feedback-admin-answer muted">Message non disponible (conversation expirée ou hors fenêtre de mémoire).</div>`;
    const comment = e.comment
      ? `<div class="feedback-admin-comment">« ${escapeHtml(e.comment)} »</div>`
      : "";

    row.innerHTML = `
      <div class="feedback-admin-head">
        ${ratingBadge}
        ${categoryBadge}
        <span class="feedback-admin-conv-title">${escapeHtml(e.conversation_title || "Conversation")}</span>
        <span class="feedback-admin-date">${date}</span>
        <button class="btn-delete-doc feedback-admin-delete" title="Supprimer ce retour">${TRASH_SVG}</button>
      </div>
      ${question}
      ${answer}
      ${comment}
    `;
    row
      .querySelector(".feedback-admin-delete")
      .addEventListener("click", () => deleteFeedbackEntry(e.session_id, e.message_id, row));
    feedbackAdminListEl.appendChild(row);
  }
}

async function deleteFeedbackEntry(sessionId, messageId, row) {
  if (!confirm("Supprimer ce retour ? Cette action est irréversible.")) return;
  try {
    const res = await apiFetch(
      `chat/feedback/${encodeURIComponent(sessionId)}/${encodeURIComponent(messageId)}`,
      { method: "DELETE" }
    );
    if (!res.ok) throw new Error();
    row.remove();
  } catch {
    alert("Échec de la suppression. Réessaie ou vérifie les logs serveur.");
  }
}

function openSettingsModal() {
  settingsGeneralLoaded = false;
  const sections = renderSettingsSidebar();
  if (!sections.length) return; // sécurité : le bouton est normalement déjà masqué
  settingsOverlay.classList.add("visible");
  switchSettingsSection(sections[0].key);
}

function closeSettingsModal() {
  settingsOverlay.classList.remove("visible");
}

if (settingsToggleBtn0) {
  settingsToggleBtn0.addEventListener("click", openSettingsModal);
}
if (settingsCloseBtn) settingsCloseBtn.addEventListener("click", closeSettingsModal);
if (settingsCancelBtn) settingsCancelBtn.addEventListener("click", closeSettingsModal);
if (settingsOverlay) {
  settingsOverlay.addEventListener("click", (e) => {
    if (e.target === settingsOverlay) closeSettingsModal();
  });
}
if (settingsSaveBtn) {
  settingsSaveBtn.addEventListener("click", async () => {
    settingsSaveBtn.disabled = true;
    try {
      const res = await apiFetch("settings/rag", { method: "PUT", body: collectSettingsPayload() });
      if (!res.ok) throw new Error();
      showToast("Réglages enregistrés.");
      closeSettingsModal();
    } catch {
      showToast("Échec de l'enregistrement des réglages.", "error");
    } finally {
      settingsSaveBtn.disabled = false;
    }
  });
}
if (settingsResetBtn) {
  settingsResetBtn.addEventListener("click", async () => {
    if (!confirm("Réinitialiser tous les réglages RAG aux valeurs par défaut ?")) return;
    try {
      const res = await apiFetch("settings/rag/reset", { method: "POST" });
      if (!res.ok) throw new Error();
      renderSettingsForm(await res.json());
      showToast("Réglages réinitialisés.");
    } catch {
      showToast("Échec de la réinitialisation.", "error");
    }
  });
}

// ── Documents indexés (liste + suppression) ─────────────────────────────────
// État réel de Qdrant (/documents/stats), rechargé à chaque ouverture du panneau.

async function loadIndexedDocuments() {
  if (!indexedDocsList) return;
  indexedDocsList.innerHTML = `<div class="indexed-docs-loading">Chargement…</div>`;
  try {
    const res = await apiFetch("documents/stats");
    if (!res.ok) throw new Error();
    const data = await res.json();
    renderIndexedDocsList(data.sources || []);
  } catch {
    indexedDocsList.innerHTML = `<div class="indexed-docs-loading">Liste indisponible.</div>`;
  }
}

function formatIndexedDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  if (now.toDateString() === d.toDateString()) {
    return `Aujourd'hui à ${d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}`;
  }
  return d.toLocaleDateString("fr-FR", { day: "numeric", month: "short", year: "numeric" });
}

function renderIndexedDocsList(sources) {
  if (!sources.length) {
    indexedDocsList.innerHTML = `<div class="indexed-docs-loading">Aucun document indexé.</div>`;
    return;
  }
  indexedDocsList.innerHTML = "";
  for (const { source_file, indexed_at } of sources) {
    const label = source_file.split("/").pop();
    const row = document.createElement("div");
    row.className = "indexed-doc-row";
    row.innerHTML = `
      <span class="indexed-doc-name" title="${escapeHtml(source_file)}">${escapeHtml(label)}</span>
      <span class="indexed-doc-date">${escapeHtml(formatIndexedDate(indexed_at))}</span>
      <button class="btn-delete-doc" title="Supprimer (MinIO + Qdrant)">${TRASH_SVG}</button>
    `;
    row
      .querySelector(".btn-delete-doc")
      .addEventListener("click", () => deleteIndexedDocument(source_file, label));
    indexedDocsList.appendChild(row);
  }
}

async function deleteIndexedDocument(source, label) {
  if (!confirm(`Supprimer "${label}" de MinIO et de Qdrant ? Cette action est irréversible.`)) return;
  try {
    const res = await apiFetch(`documents?object_name=${encodeURIComponent(source)}`, {
      method: "DELETE",
    });
    if (!res.ok) throw new Error();
    loadIndexedDocuments();
  } catch {
    alert("Échec de la suppression. Réessaie ou vérifie les logs serveur.");
  }
}

// ── Upload (glisser-déposer, multi-fichiers, dossiers) ──────────────────────

function renderUploadRow(docId, filename, status, chunks) {
  if (!uploadStatusList) return;
  let row = document.getElementById(`urow-${docId}`);
  if (!row) {
    row = document.createElement("div");
    row.id = `urow-${docId}`;
    row.className = "upload-row";
    uploadStatusList.prepend(row);
  }
  // "done" ne passe jamais par ici : dès l'indexation terminée, pollStatus()
  // retire la ligne et fait passer le document dans la liste "Documents
  // indexés" en dessous (avec sa date), plutôt que d'afficher "Indexé (N chunks)".
  const dotClass = { processing:"dot-processing", error:"dot-error", empty:"dot-empty" }[status] || "dot-processing";
  const label    = { processing:"Traitement…", error:"Erreur", empty:"Aucun texte" }[status] || status;
  row.innerHTML = `
    <span><span class="status-dot ${dotClass}"></span>${escapeHtml(filename)}</span>
    <span style="color:var(--text-muted);font-size:11px">${label}</span>
  `;
}

async function pollStatus(docId, filename) {
  let attempts = 0;
  const iv = setInterval(async () => {
    attempts++;
    try {
      const res = await apiFetch(`documents/status/${docId}`);
      if (!res.ok) throw new Error();
      const data = await res.json();
      if (data.status === "done") {
        clearInterval(iv);
        document.getElementById(`urow-${docId}`)?.remove();
        loadIndexedDocuments();
        return;
      }
      renderUploadRow(docId, filename, data.status, data.chunks);
      if (data.status !== "processing" || attempts >= 60) clearInterval(iv);
    } catch {
      renderUploadRow(docId, filename, "error", 0);
      clearInterval(iv);
    }
  }, 5000);
}

async function uploadOneFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  try {
    const res = await fetch(`${API_BASE}/documents/upload`, {
      method: "POST",
      body: fd,
      headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {},
    });
    if (!res.ok) { renderUploadRow(crypto.randomUUID(), file.name, "error", 0); return; }
    const data = await res.json();
    renderUploadRow(data.doc_id, file.name, "processing", 0);
    pollStatus(data.doc_id, file.name);
  } catch {
    renderUploadRow(crypto.randomUUID(), file.name, "error", 0);
  }
}

// Filtre par extension (le type MIME n'est pas fiable en drag & drop) --
// garder synchronisé avec ALLOWED_EXTENSIONS côté serveur.
const ACCEPTED_EXTENSIONS = /\.(pdf|docx|pptx|xlsx|png|jpe?g)$/i;

function uploadFiles(fileList) {
  const files = [...fileList];
  if (!files.length) return;

  const accepted = files.filter(f => ACCEPTED_EXTENSIONS.test(f.name));
  const rejected = files.filter(f => !accepted.includes(f));

  // Fichiers non supportés glissés par erreur : ligne "erreur" immédiate, pas d'appel réseau.
  for (const f of rejected) {
    renderUploadRow(crypto.randomUUID(), f.name, "error", 0);
  }

  // Uploads lancés en parallèle (pas d'attente séquentielle fichier par fichier) --
  // chaque ligne de statut apparaît et se met à jour indépendamment des autres.
  accepted.forEach(uploadOneFile);
}

if (dropzone && fileInput) {
  // Clic sur la dropzone → ouvre le sélecteur de fichiers natif
  dropzone.addEventListener("click", () => fileInput.click());

  fileInput.addEventListener("change", () => {
    uploadFiles(fileInput.files);
    fileInput.value = ""; // permet de re-sélectionner les mêmes fichiers ensuite
  });

  // Drag & drop
  ["dragenter", "dragover"].forEach(evt => {
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.add("dragover");
    });
  });

  ["dragleave", "dragend"].forEach(evt => {
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.remove("dragover");
    });
  });

  // Lecture récursive d'une FileSystemEntry -- permet d'accepter des dossiers
  // glissés dans la dropzone (dataTransfer.files seul les ignore).
  const readEntry = (entry) => new Promise((resolve) => {
    if (entry.isFile) {
      entry.file(
        (file) => resolve([file]),
        () => resolve([]) // fichier illisible : ignoré plutôt que de bloquer tout l'upload
      );
      return;
    }
    if (entry.isDirectory) {
      const reader = entry.createReader();
      const collected = [];
      // readEntries() ne renvoie pas tout en un appel -- réappeler jusqu'à un lot vide.
      const readNextBatch = () => {
        reader.readEntries(async (batch) => {
          if (!batch.length) {
            const nested = await Promise.all(collected.map(readEntry));
            resolve(nested.flat());
            return;
          }
          collected.push(...batch);
          readNextBatch();
        }, () => resolve([]));
      };
      readNextBatch();
      return;
    }
    resolve([]);
  });

  const collectFilesFromDataTransfer = async (dataTransfer) => {
    const items = dataTransfer.items;
    const supportsEntries = items && items.length && typeof items[0].webkitGetAsEntry === "function";
    if (!supportsEntries) {
      // Pas de support natif : liste plate, sans récursion dans les sous-dossiers.
      return [...dataTransfer.files];
    }

    const entries = [...items].map((item) => item.webkitGetAsEntry()).filter(Boolean);
    const nested = await Promise.all(entries.map(readEntry));
    return nested.flat();
  };

  dropzone.addEventListener("drop", async (e) => {
    e.preventDefault();
    e.stopPropagation();
    dropzone.classList.remove("dragover");
    const files = await collectFilesFromDataTransfer(e.dataTransfer);
    uploadFiles(files);
  });
}

// ── Init ─────────────────────────────────────────────────────────────────────
// Résout droits/identité via /session-info avant tout appel écrivant.

async function initSession() {
  try {
    const res = await fetch(`${API_BASE}/session-info`);
    if (res.ok) {
      const info = await res.json();
      isAdmin = !!info.is_admin;
      canUpload = !!info.can_upload;
      csrfToken = info.csrf_token || "";
      maxHistoryTurns = info.max_history_turns || 0;
      if (welcomeTitleEl) {
        welcomeTitleEl.textContent = info.username ? `Bonjour, ${info.username} !` : "Bonjour !";
      }
    }
  } catch {
  }
  if (settingsToggleBtn0 && (isAdmin || canUpload)) {
    settingsToggleBtn0.style.display = "";
  }
}

inputEl.focus();
initSession().then(loadConversations);

}); // DOMContentLoaded
