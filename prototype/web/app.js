const seedState = [];
let promptState = "";
let latestResults = [];
let activeResultIndex = 0;
let latestRequestId = "";
let busy = false;
let searchVersion = 0;
let requestVersion = 0;

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (character) => {
    const entities = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return entities[character];
  });
}

function debounce(fn, wait) {
  let timeout;
  return (...args) => {
    clearTimeout(timeout);
    timeout = setTimeout(() => fn(...args), wait);
  };
}

function isSelected(trackId) {
  return seedState.some((track) => track.id === trackId);
}

function trackLabel(track) {
  return `${track.title} - ${track.artist}`;
}

function trackYear(track) {
  return Number.isInteger(track.year) && track.year > 0 ? track.year : "year unknown";
}

function resizePrompt() {
  const input = document.getElementById("prompt-query");
  const canvas = resizePrompt.canvas || (resizePrompt.canvas = document.createElement("canvas"));
  const context = canvas.getContext("2d");
  const style = getComputedStyle(input);
  context.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
  const longestLine = (input.value || input.placeholder).split("\n").reduce(
    (width, line) => Math.max(width, context.measureText(line).width), 0
  );
  const field = document.getElementById("seed-field");
  const fieldStyle = getComputedStyle(field);
  const available = field.clientWidth - parseFloat(fieldStyle.paddingLeft) - parseFloat(fieldStyle.paddingRight);
  input.style.width = `${Math.min(available, Math.max(90, longestLine + 16))}px`;
  input.style.height = "auto";
  input.style.height = `${input.scrollHeight}px`;
}

function updateRecommendButton() {
  const button = document.getElementById("recommend-btn");
  button.disabled = busy || (!seedState.length && !promptState);
}

function updateSeedCounter() {
  document.getElementById("seed-counter").textContent = seedState.length ? `${seedState.length} selected` : "";
}

function updateGhostSuggestion() {
  const ghost = document.getElementById("ghost-suggestion");
  const seedInput = document.getElementById("seed-query");
  const track = latestResults[activeResultIndex];
  const available = Boolean(track && seedInput.value.trim() && seedState.length < 50);
  document.getElementById("autocomplete-status").textContent = available
    ? `${trackLabel(track)}. Suggestion ${activeResultIndex + 1} of ${latestResults.length}.` : "";
  const rawValue = seedInput.value;
  const query = rawValue.trim();

  if (!track || !query || seedState.length >= 50) {
    ghost.innerHTML = "";
    ghost.classList.add("hidden");
    return;
  }

  const label = trackLabel(track);
  const prefixMatch = label.toLowerCase().startsWith(query.toLowerCase());
  const prefix = prefixMatch ? label.slice(0, query.length) : rawValue;
  const suffix = prefixMatch ? label.slice(query.length) : `  ${label}`;

  ghost.innerHTML = `
    <span class="ghost-prefix">${escapeHtml(prefix)}</span>
    <span class="ghost-rest">${escapeHtml(suffix)}</span>
  `;
  ghost.classList.remove("hidden");
}

function renderSelectedTracks() {
  const target = document.getElementById("selected-tracks");
  const seedInput = document.getElementById("seed-query");
  updateSeedCounter();
  updateRecommendButton();

  target.innerHTML = seedState
    .map(
      (track) => `
        <article class="selected-track-card">
          ${spotifyLink(track)}<span class="selected-copy">${escapeHtml(track.title)} · ${escapeHtml(track.artist)} · ${trackYear(track)} · ${escapeHtml(track.genre)}</span>
          <button class="remove-chip" type="button" data-track-id="${escapeHtml(track.id)}" aria-label="Remove ${escapeHtml(track.title)}">×</button>
        </article>
      `
    )
    .join("");

  if (seedState.length >= 50) {
    seedInput.placeholder = "";
  } else {
    seedInput.placeholder = "La Bachata - Manuel Turizo";
  }

  target.querySelectorAll(".remove-chip").forEach((button) => {
    button.addEventListener("click", () => {
      const index = seedState.findIndex((track) => track.id === button.dataset.trackId);
      if (index >= 0) {
        const card = button.closest(".selected-track-card");
        if (!card) {
          seedState.splice(index, 1);
          renderSelectedTracks();
          renderSearchResults(latestResults);
          seedInput.focus();
          return;
        }

        card.classList.add("is-exiting");
        window.setTimeout(() => {
          const currentIndex = seedState.findIndex((track) => track.id === button.dataset.trackId);
          if (currentIndex >= 0) seedState.splice(currentIndex, 1);
          renderSelectedTracks();
          renderSearchResults(latestResults);
          seedInput.focus();
        }, 170);
      }
    });
  });

  updateGhostSuggestion();
}

function selectTrack(track) {
  if (seedState.length >= 50 || isSelected(track.id)) {
    return;
  }

  searchVersion++;
  seedState.push(track);
  latestResults = [];
  activeResultIndex = 0;
  document.getElementById("seed-query").value = "";
  renderSelectedTracks();
  renderSearchResults([]);
  document.getElementById("seed-query").focus();
}

function renderSearchResults(results) {
  latestResults = results.filter((track) => !isSelected(track.id));

  if (activeResultIndex >= latestResults.length) {
    activeResultIndex = 0;
  }

  updateGhostSuggestion();
}

async function runSearch(query) {
  const version = ++searchVersion;
  if (!query.trim() || seedState.length >= 50) {
    latestResults = [];
    activeResultIndex = 0;
    updateGhostSuggestion();
    return;
  }

  try {
    const response = await fetch(`/search?q=${encodeURIComponent(query)}`);
    if (!response.ok) {
      latestResults = [];
      activeResultIndex = 0;
      updateGhostSuggestion();
      return;
    }
    const results = await response.json();
    if (version !== searchVersion || document.getElementById("seed-query").value !== query) return;
    activeResultIndex = 0;
    renderSearchResults(results);
  } catch (error) {
    latestResults = [];
    activeResultIndex = 0;
    updateGhostSuggestion();
  }
}

function clearResults() {
  searchVersion++;
  latestResults = [];
  activeResultIndex = 0;
  updateGhostSuggestion();
}

function resetComposer() {
  requestVersion++;
  busy = false;
  document.getElementById("recommend-btn").textContent = "Recommend";
  document.getElementById("request-status").textContent = "";
  seedState.length = 0;
  promptState = "";
  latestResults = [];
  activeResultIndex = 0;
  latestRequestId = "";

  const promptInput = document.getElementById("prompt-query");
  const seedInput = document.getElementById("seed-query");
  const resultsPanel = document.getElementById("results-panel");
  const resultList = document.getElementById("results");
  const feedbackForm = document.getElementById("feedback-form");

  promptInput.value = "";
  resizePrompt();
  seedInput.value = "";
  resultsPanel.classList.add("hidden");
  resultList.innerHTML = "";
  feedbackForm.reset();
  document.getElementById("feedback-status").textContent = "";

  renderSelectedTracks();
  clearResults();
  promptInput.focus();
}

function spotifyLink(track) {
  return `<a class="spotify-play" href="/spotify/open/${encodeURIComponent(track.id)}" target="_blank" rel="noopener noreferrer" aria-label="Open ${escapeHtml(track.title)} in Spotify" title="Open in Spotify"><img src="/static/spotify.svg" alt="" /></a>`;
}

async function spotifyStatus() {
  const status = document.getElementById("spotify-status");
  const params = new URLSearchParams(location.search);
  const outcome = params.get("spotify");
  const messages = {unconfigured: "Spotify sign-in is not configured yet.", cancelled: "Spotify sign-in cancelled.", failed: "Could not connect to Spotify. Please try again."};
  if (messages[outcome]) status.textContent = messages[outcome];
  if (outcome) { params.delete("spotify"); history.replaceState(null, "", location.pathname + (params.size ? "?" + params : "")); }
  try {
    const response = await fetch("/auth/spotify/status");
    if (!response.ok) throw new Error();
    const data = await response.json();
    document.getElementById("spotify-login").classList.toggle("hidden", data.connected);
    document.getElementById("spotify-connected").classList.toggle("hidden", !data.connected);
    document.getElementById("spotify-logout").classList.toggle("hidden", !data.connected);
  } catch { status.textContent = "Could not check Spotify connection."; }
}

function sourceLink(track) {
  if (!track.source_url) return "";
  try {
    const url = new URL(track.source_url);
    if (track.source === "taste-profile" && ["http:", "https:"].includes(url.protocol) && url.hostname === "millionsongdataset.com") {
      return '<a class="source-link" href="/static/catalogue-sources.html" target="_blank" rel="noopener noreferrer">Taste Profile ↗</a>';
    }
    if (url.protocol !== "https:" || !["music.apple.com", "itunes.apple.com", "musicbrainz.org", "freemusicarchive.org", "www.freemusicarchive.org"].includes(url.hostname)) return "";
    const label = track.source === "musicbrainz" ? "MusicBrainz" : track.source === "fma" ? "Free Music Archive" : "Apple Music";
    return `<a class="source-link" href="${escapeHtml(url.href)}" target="_blank" rel="noopener noreferrer">${label} ↗</a>`;
  } catch { return ""; }
}

function renderResults(payload) {
  const resultsPanel = document.getElementById("results-panel");
  const resultList = document.getElementById("results");
  latestRequestId = payload.request_id;
  resultsPanel.classList.remove("hidden");

  document.getElementById("request-status").textContent = (payload.warnings || []).join(" ");
  document.getElementById("feedback-form").reset();
  document.getElementById("feedback-status").textContent = "";

  resultList.innerHTML = payload.recommendations
    .map(
      (track) => `
        <li class="result-card">
          ${spotifyLink(track)}
          <div class="song-line" tabindex="0"><strong>${escapeHtml(track.title)}</strong><span class="result-meta"> · ${escapeHtml(track.artist)} · ${trackYear(track)} · ${escapeHtml(track.genre)}</span></div>
          ${sourceLink(track)}
        </li>
      `
    )
    .join("");
}

async function getRecommendations() {
  if (busy || (!seedState.length && !promptState)) {
    return;
  }

  const version = ++requestVersion;
  busy = true;
  document.getElementById("request-status").textContent = "";
  const payload = {
    seed_track_ids: seedState.map((track) => track.id),
    prompt: promptState,
    limit: 5,
  };

  const button = document.getElementById("recommend-btn");
  button.disabled = true;
  button.textContent = "Finding tracks...";

  try {
    const response = await fetch("/recommend", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const error = await response.json();
      if (version !== requestVersion) return;
      document.getElementById("request-status").textContent = typeof error.detail === "string"
        ? error.detail : "Check your prompt and selected songs.";
      return;
    }

    const data = await response.json();
    if (version === requestVersion) renderResults(data);
  } catch (error) {
    if (version === requestVersion) document.getElementById("request-status").textContent = "Connection failed. Please retry.";
  } finally {
    if (version !== requestVersion) return;
    busy = false;
    button.textContent = "Recommend";
    updateRecommendButton();
  }
}

async function submitFeedback(event) {
  event.preventDefault();
  if (!latestRequestId) {
    return;
  }

  const rating = event.currentTarget.querySelector("input[name=rating]:checked");
  if (!rating) return;
  const status = document.getElementById("feedback-status");
  const submitButton = event.currentTarget.querySelector("button[type='submit']");
  const payload = {
    request_id: latestRequestId,
    rating: Number(rating.value),
    comment: document.getElementById("feedback-comment").value.trim(),
  };

  submitButton.disabled = true;
  try {
    const response = await fetch("/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    status.textContent = response.ok ? "Saved" : "Could not save";
  } catch (error) {
    status.textContent = "Could not save";
  } finally {
    submitButton.disabled = false;
  }
}

function removeLastToken() {
  const seedInput = document.getElementById("seed-query");

  if (seedInput.value) {
    return;
  }

  if (seedState.length > 0) {
    seedState.pop();
    renderSelectedTracks();
    clearResults();
    return;
  }

  if (promptState) {
    promptState = "";
    document.getElementById("prompt-query").value = "";
    resizePrompt();
    renderSelectedTracks();
  }
}

function moveActiveResult(direction) {
  if (!latestResults.length) {
    return;
  }

  activeResultIndex =
    (activeResultIndex + direction + latestResults.length) % latestResults.length;
  renderSearchResults(latestResults);
}

function init() {
  spotifyStatus();
  document.getElementById("spotify-logout").addEventListener("click", async () => {
    try {
      const response = await fetch("/auth/spotify/logout", {method: "POST"});
      if (!response.ok) throw new Error();
      await spotifyStatus();
    } catch { document.getElementById("spotify-status").textContent = "Could not disconnect. Please try again."; }
  });
  const promptInput = document.getElementById("prompt-query");
  const seedInput = document.getElementById("seed-query");

  promptInput.addEventListener("input", (event) => {
    promptState = event.target.value.trim();
    resizePrompt();
    updateRecommendButton();
  });
  promptInput.addEventListener("keydown", (event) => {
    if (event.key === "Tab" && !event.shiftKey) {
      event.preventDefault();
      seedInput.focus();
    }
  });

  seedInput.addEventListener(
    "input",
    debounce(async (event) => {
      await runSearch(event.target.value);
    }, 180)
  );

  seedInput.addEventListener("keydown", async (event) => {
    if (event.key === "Tab" && latestResults[activeResultIndex]) {
      event.preventDefault();
      selectTrack(latestResults[activeResultIndex]);
      return;
    }

    if (event.key === "ArrowDown") {
      event.preventDefault();
      moveActiveResult(1);
      return;
    }

    if (event.key === "ArrowUp") {
      event.preventDefault();
      moveActiveResult(-1);
      return;
    }

    if (event.key === "Enter") {
      if (latestResults[activeResultIndex]) {
        event.preventDefault();
        selectTrack(latestResults[activeResultIndex]);
      }
      return;
    }

    if (event.key === "Backspace" && !seedInput.value) {
      event.preventDefault();
      removeLastToken();
      return;
    }

    if (event.key === "Escape") {
      clearResults();
    }
  });

  resizePrompt();
  window.addEventListener("resize", resizePrompt);
  if (document.fonts) document.fonts.ready.then(resizePrompt);
  renderSelectedTracks();
  document.getElementById("recommend-btn").addEventListener("click", getRecommendations);
  document.getElementById("restart-btn").addEventListener("click", resetComposer);
  document.getElementById("feedback-form").addEventListener("submit", submitFeedback);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
