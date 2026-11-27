const ICON_PLAY = `<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="6,3 20,12 6,21"/></svg>`;
const ICON_SQUARE = `<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>`;

const state = {
  isRunning: false,
  currentResult: null,
};

let el = {};
let currentAbortController = null;

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
  };

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

  currentAbortController = new AbortController();

  try {
    const response = await fetch('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: prompt }),
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
