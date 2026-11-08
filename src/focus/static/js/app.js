/* keyboard, sheet, dopamine. no framework. */

function closeSheet() {
  const sheet = document.getElementById('sheet');
  if (sheet) sheet.innerHTML = '';
  document.querySelectorAll('.task.sel').forEach((t) => t.classList.remove('sel'));
  selected = -1;
}
window.closeSheet = closeSheet;

let selected = -1;
let selectedId = null;

const allTasks = () => Array.from(document.querySelectorAll('#list .task'));

function select(index) {
  const tasks = allTasks();
  if (!tasks.length) return;
  selected = Math.max(0, Math.min(tasks.length - 1, index));
  tasks.forEach((t, i) => t.classList.toggle('sel', i === selected));
  tasks[selected].scrollIntoView({ block: 'nearest' });
}

const WIDE_KEY = 'focus.sheet.wide';

function applyWide() {
  const sheet = document.getElementById('sheet');
  if (!sheet) return;
  const wide = localStorage.getItem(WIDE_KEY) === '1';
  sheet.classList.toggle('wide', wide);
  const btn = sheet.querySelector('#wide-toggle');
  if (btn) {
    btn.textContent = wide ? 'narrower' : 'wider';
    btn.setAttribute('aria-pressed', String(wide));
  }
}

window.toggleWide = () => {
  localStorage.setItem(WIDE_KEY, localStorage.getItem(WIDE_KEY) === '1' ? '0' : '1');
  applyWide();
};

// field-sizing: content does this natively where supported; this is the fallback
function fitNotes(el) {
  if (!el) return;
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight + 2, Math.round(window.innerHeight * 0.72)) + 'px';
}

document.addEventListener('input', (event) => {
  if (event.target.matches && event.target.matches('.sheet textarea[name="notes"]')) fitNotes(event.target);
});

applyWide();

let burstOrigin = null;

// remember where the tick was at click time — the row is replaced by the swap,
// so the element is detached by the time the celebrate event arrives
document.addEventListener('click', (event) => {
  const tick = event.target.closest && event.target.closest('.tick');
  if (!tick) return;
  const r = tick.getBoundingClientRect();
  burstOrigin = { x: r.left + r.width / 2, y: r.top + r.height / 2 };
}, true);

document.addEventListener('focus:celebrate', () => {
  if (window.burst) window.burst(burstOrigin);
  burstOrigin = null;
});
document.addEventListener('focus:close-sheet', closeSheet);

// small transient toast, used by 409s (e.g. deleting a project that still has tasks)
document.addEventListener('focus:notice', (event) => {
  const message = event.detail && event.detail.message;
  if (!message) return;
  let toast = document.getElementById('toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'toast';
    toast.setAttribute('role', 'status');
    document.body.append(toast);
  }
  toast.textContent = message;
  toast.classList.add('on');
  clearTimeout(toast._hide);
  toast._hide = setTimeout(() => toast.classList.remove('on'), 4200);
});

document.addEventListener('htmx:beforeSwap', () => {
  selectedId = document.querySelector('#list .task.sel')?.dataset.id || null;
});

document.addEventListener('htmx:afterSwap', (event) => {
  if (event.target.id === 'sheet') {
    applyWide();
    fitNotes(event.target.querySelector('textarea[name="notes"]'));
    event.target.querySelector('.sheet-title')?.focus();
    return;
  }
  if (event.target.id === 'list') {
    selected = -1;
    if (selectedId) {
      const index = allTasks().findIndex((t) => t.dataset.id === selectedId);
      if (index >= 0) select(index);
    }
  }
});

document.addEventListener('keydown', (event) => {
  if (event.metaKey || event.ctrlKey || event.altKey) return;

  const el = document.activeElement;
  const typing = el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' ||
    el.tagName === 'SELECT' || el.isContentEditable);

  if (event.key === 'Escape') {
    const sheet = document.getElementById('sheet');
    if (sheet && sheet.innerHTML.trim()) return closeSheet();
    if (typing) el.blur();
    document.querySelectorAll('.task.sel').forEach((t) => t.classList.remove('sel'));
    selected = -1;
    return;
  }

  if (typing) return;

  switch (event.key) {
    case 'n': {
      const input = document.getElementById('new-task');
      if (input) {
        event.preventDefault();
        input.focus();
      }
      break;
    }
    case 'j':
      event.preventDefault();
      select(selected + 1);
      break;
    case 'k':
      event.preventDefault();
      select(selected <= 0 ? 0 : selected - 1);
      break;
    case 'x': {
      const task = allTasks()[selected];
      if (task) {
        event.preventDefault();
        task.querySelector('.tick').click();
      }
      break;
    }
    case '/': {
      const search = document.getElementById('search');
      if (search) {
        event.preventDefault();
        search.focus();
      }
      break;
    }
  }
});

// per-device scale: cookie + immediate zoom, no round trip
const scalePick = document.getElementById("scale-pick");
if (scalePick) {
  scalePick.addEventListener("change", () => {
    document.cookie = `focus_scale=${scalePick.value}; path=/; max-age=31536000; samesite=lax`;
    document.documentElement.style.zoom = scalePick.value;
  });
}
