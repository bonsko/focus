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

document.addEventListener('focus:celebrate', () => window.burst && window.burst());
document.addEventListener('focus:close-sheet', closeSheet);

document.addEventListener('htmx:beforeSwap', () => {
  selectedId = document.querySelector('#list .task.sel')?.dataset.id || null;
});

document.addEventListener('htmx:afterSwap', (event) => {
  if (event.target.id === 'sheet') {
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
