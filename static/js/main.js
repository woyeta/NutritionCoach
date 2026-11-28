const ICON_PLAY = `<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="6,3 20,12 6,21"/></svg>`;
const ICON_SQUARE = `<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>`;

const state = {
  isRunning: false,
  currentResult: null,
};

let el = {};
let currentAbortController = null;

// ── Profile (stored in this browser only) ──

const PROFILE_KEY = 'nutritionCoachProfile';
const PROFILE_FIELDS = ['sex', 'age', 'height_cm', 'weight_kg', 'activity', 'goal', 'diet', 'allergies', 'conditions'];
const NUMBER_FIELDS = ['age', 'height_cm', 'weight_kg'];

function loadProfile() {
  try {
    const saved = JSON.parse(localStorage.getItem(PROFILE_KEY) || 'null');
    return saved && typeof saved === 'object' ? saved : null;
  } catch (err) {
    return null;
  }
}

function storeProfile(profile) {
  try {
    if (profile) {
      localStorage.setItem(PROFILE_KEY, JSON.stringify(profile));
    } else {
      localStorage.removeItem(PROFILE_KEY);
    }
    return true;
  } catch (err) {
    return false;
  }
}

function readProfileForm() {
  const form = el.profileForm.elements;
  const profile = {};
  PROFILE_FIELDS.forEach((name) => {
    const value = String(form[name].value || '').trim();
    if (value !== '') {
      profile[name] = NUMBER_FIELDS.includes(name) ? Number(value) : value;
    }
  });
  return Object.keys(profile).length ? profile : null;
}

function fillProfileForm(profile) {
  const form = el.profileForm.elements;
  PROFILE_FIELDS.forEach((name) => {
    form[name].value = profile && profile[name] != null ? profile[name] : '';
  });
}

function profileSummary(profile) {
  if (!profile) return 'No profile saved. Click to add one.';
  const parts = [];
  if (profile.sex) parts.push(profile.sex);
  if (profile.age) parts.push(`${profile.age} y`);
  if (profile.height_cm) parts.push(`${profile.height_cm} cm`);
  if (profile.weight_kg) parts.push(`${profile.weight_kg} kg`);
  if (profile.activity) parts.push(profile.activity.replace('_', ' '));
  if (profile.goal) parts.push(`goal: ${profile.goal}`);
  if (profile.diet) parts.push(profile.diet);
  if (profile.allergies) parts.push(`allergies: ${profile.allergies}`);
  if (profile.conditions) parts.push(profile.conditions);
  return `Used in every question: ${parts.join(', ')}`;
}

function renderProfileSummary() {
  el.profileSummaryText.textContent = profileSummary(loadProfile());
}

function toggleProfilePanel(show) {
  const open = show !== undefined ? show : el.profilePanel.hidden;
  el.profilePanel.hidden = !open;
  if (open) {
    fillProfileForm(loadProfile());
    el.profileStatus.textContent = '';
  }
}

function saveProfile() {
  const profile = readProfileForm();
  if (!el.profileForm.checkValidity()) {
    el.profileForm.reportValidity();
    return;
  }
  const ok = storeProfile(profile);
  el.profileStatus.textContent = ok
    ? (profile ? 'Saved. Your questions will use this profile.' : 'Nothing to save, so the profile is empty.')
    : "Couldn't save in this browser (storage is blocked). It will be used until you reload the page.";
  if (!ok) state.sessionProfile = profile;
  renderProfileSummary();
}

function clearProfile() {
  storeProfile(null);
  state.sessionProfile = null;
  fillProfileForm(null);
  el.profileStatus.textContent = 'Profile cleared.';
  renderProfileSummary();
}

function currentProfile() {
  return loadProfile() || state.sessionProfile || null;
}

const formatValue = (v) =>
  v == null ? '' : typeof v === 'object' ? JSON.stringify(v, null, 2) : String(v);

function init() {
  el = {
    prompt:         document.getElementById('promptInput'),
    runBtn:         document.getElementById('runBtn'),
    resetBtn:       document.getElementById('resetBtn'),
    traceSteps:     document.getElementById('traceSteps'),
    outputEmpty:    document.getElementById('outputEmpty'),
    thinking:       document.getElementById('thinkingIndicator'),
    status:         document.getElementById('statusIndicator'),
    profileBtn:         document.getElementById('profileBtn'),
    profilePanel:       document.getElementById('profilePanel'),
    profileForm:        document.getElementById('profileForm'),
    profileSaveBtn:     document.getElementById('profileSaveBtn'),
    profileClearBtn:    document.getElementById('profileClearBtn'),
    profileStatus:      document.getElementById('profileStatus'),
    profileSummary:     document.getElementById('profileSummary'),
    profileSummaryText: document.getElementById('profileSummaryText'),
  };

  renderProfileSummary();
  el.profileBtn.addEventListener('click', () => toggleProfilePanel());
  el.profileSummary.addEventListener('click', () => toggleProfilePanel(true));
  el.profileSaveBtn.addEventListener('click', saveProfile);
  el.profileClearBtn.addEventListener('click', clearProfile);

  document.querySelectorAll('.example-item').forEach((btn) => {
    btn.addEventListener('click', () => {
      el.prompt.value = btn.textContent.trim();
      updateRunButton();
      el.prompt.focus();
    });
  });

  el.prompt.addEventListener('input', updateRunButton);
  el.prompt.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      runAgent();
    }
  });

  el.runBtn.addEventListener('click', (e) => {
    e.preventDefault();
    if (state.isRunning) {
      stopAgent();
    } else {
      runAgent();
    }
  });

  el.resetBtn.addEventListener('click', (e) => {
    e.preventDefault();
    resetAgent();
  });
}

function updateRunButton() {
  const promptText = el.prompt.value.trim();
  const canRun = promptText !== '';
  el.runBtn.disabled = !canRun;
}

function stopAgent() {
  if (currentAbortController) {
    currentAbortController.abort();
    currentAbortController = null;
  }
  state.isRunning = false;
}

async function runAgent() {
  if (state.isRunning) return;

  const prompt = el.prompt.value.trim();

  if (!prompt) {
    showToast('Please enter a question.');
    return;
  }

  state.isRunning = true;

  el.runBtn.classList.add('is-stop');
  el.runBtn.innerHTML = `${ICON_SQUARE} Stop`;
  el.runBtn.disabled = false;

  el.outputEmpty.hidden = true;
  el.traceSteps.hidden = false;
  el.traceSteps.innerHTML = '';
  el.thinking.hidden = false;
  showStatus('running');

  const thinkingText = el.thinking.querySelector('.thinking-text');
  const startedAt = Date.now();
  const tick = () => {
    const seconds = Math.round((Date.now() - startedAt) / 1000);
    thinkingText.textContent = `thinking… ${seconds}s (questions with several steps can take a few minutes; the first one also loads the model)`;
  };
  tick();
  const timer = setInterval(tick, 1000);

  currentAbortController = new AbortController();

  try {
    const response = await fetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: prompt, profile: currentProfile() }),
      signal: currentAbortController.signal,
    });

    el.thinking.hidden = true;

    if (!response.ok) {
      const errData = await response.json().catch(() => ({}));
      throw new Error(errData.error || `Server error (HTTP ${response.status})`);
    }

    const data = await response.json();
    state.currentResult = data;

    const resultObj = data.result || data;
    const steps = (resultObj.steps || []).slice().reverse();

    for (let i = 0; i < steps.length; i++) {
      await new Promise((resolve) => setTimeout(resolve, 200));
      const stepNumber = steps[i].turn !== undefined ? steps[i].turn : (steps.length - i);
      renderTraceStep(steps[i], stepNumber);
    }

    if (resultObj.status && resultObj.status !== 'success') {
      renderStatusMessage(resultObj.status);
    }

    showStatus('done');
    el.resetBtn.hidden = false;
  } catch (err) {
    if (err.name !== 'AbortError') {
      console.error('Error during agent execution:', err);
      showToast(err.message || 'Execution error occurred');
    }
  } finally {
    clearInterval(timer);
    state.isRunning = false;
    currentAbortController = null;

    el.runBtn.classList.remove('is-stop');
    el.runBtn.innerHTML = `${ICON_PLAY} Run`;

    updateRunButton();
    el.thinking.hidden = true;
  }
}

function resetAgent() {
  el.traceSteps.innerHTML = '';
  el.traceSteps.hidden = true;
  el.outputEmpty.hidden = false;
  el.resetBtn.hidden = true;
  el.prompt.value = '';

  showStatus('hidden');
  state.currentResult = null;
  updateRunButton();
}

function createTraceCard(type, badgeText, contentText, toolName, indexNum) {
  const card = document.createElement('div');
  card.className = 'trace-card';
  card.dataset.type = type;

  const header = document.createElement('div');
  header.className = 'trace-card-header';

  const badge = document.createElement('span');
  badge.className = 'trace-badge';
  badge.textContent = badgeText;
  header.appendChild(badge);

  if (toolName) {
    const toolSpan = document.createElement('span');
    toolSpan.className = 'trace-tool-name';
    toolSpan.textContent = toolName;
    header.appendChild(toolSpan);
  }

  const stepSpan = document.createElement('span');
  stepSpan.className = 'trace-card-step';
  stepSpan.textContent = String(indexNum);
  header.appendChild(stepSpan);

  const body = document.createElement('div');
  body.className = 'trace-card-body';
  body.textContent = contentText;

  card.appendChild(header);
  card.appendChild(body);

  return card;
}

function renderTraceStep(step, index) {
  const stepType = step.type || 'thought_action';
  const n = step.turn !== undefined ? step.turn : index;

  const parts = [
    step.thought && { type: 'think', label: 'THINK', text: step.thought },
    step.action && { type: 'act', label: 'ACT', text: formatValue(step.action_input), tool: step.action },
    step.observation != null && { type: 'observe', label: 'OBSERVE', text: formatValue(step.observation) },
    (stepType === 'final_answer' || step.final_answer) && { type: 'answer', label: 'ANSWER', text: formatValue(step.final_answer) },
    (stepType === 'error' || step.error) && {
      type: 'error',
      label: 'ERROR',
      text: String(step.error || '') + (step.raw_response ? `\n\nModel output:\n${step.raw_response}` : ''),
    },
  ].filter(Boolean);

  parts.forEach((p) => el.traceSteps.appendChild(createTraceCard(p.type, p.label, p.text, p.tool, n)));

  el.traceSteps.lastElementChild?.scrollIntoView({ behavior: 'smooth', block: 'end' });
}

function renderStatusMessage(status) {
  const messages = {
    max_turns_reached: 'Agent reached maximum turns without a final answer',
    consecutive_errors: 'Agent encountered 3 consecutive errors',
    loop_detected: 'Agent stopped after repeating the same tool call',
    unverified: 'This answer failed an automatic check (a number not found in the tool results, or an allergen-free claim). Double-check it.',
  };

  const card = document.createElement('div');
  card.className = 'status-message-card';

  const icon = document.createElement('span');
  icon.className = 'status-icon';
  icon.textContent = '⚠️';

  const text = document.createElement('span');
  text.className = 'status-text';
  text.textContent = messages[status] || `Agent finished with status: ${status}`;

  card.append(icon, text);
  el.traceSteps.appendChild(card);
  card.scrollIntoView({ behavior: 'smooth', block: 'end' });
}

function showStatus(type) {
  el.status.className = 'status-indicator';
  el.status.textContent = '';

  if (type === 'hidden') return;

  el.status.classList.add(type === 'running' ? 'is-running' : 'is-done');

  const dot = document.createElement('span');
  dot.className = 'status-dot';

  const text = document.createElement('span');
  text.className = 'status-text';
  text.textContent = type === 'running' ? 'running' : 'done';

  el.status.append(dot, text);
}

function showToast(message) {
  const container = document.getElementById('toastContainer') || (() => {
    const c = document.createElement('div');
    c.id = 'toastContainer';
    c.className = 'toast-container';
    document.body.appendChild(c);
    return c;
  })();

  const toast = document.createElement('div');
  toast.className = 'error-toast';

  const icon = document.createElement('span');
  icon.className = 'toast-icon';
  icon.textContent = '❌';

  const msg = document.createElement('span');
  msg.className = 'toast-message';
  msg.textContent = message;

  const close = document.createElement('button');
  close.className = 'toast-close';
  close.textContent = '✕';
  close.addEventListener('click', () => toast.remove());

  toast.append(icon, msg, close);
  container.appendChild(toast);

  setTimeout(() => {
    if (!toast.parentNode) return;
    toast.classList.add('toast-fade-out');
    setTimeout(() => toast.remove(), 300);
  }, 5000);
}

document.addEventListener('DOMContentLoaded', init);
