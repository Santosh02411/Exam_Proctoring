/* Adds a show/hide ("eye") button to every password field on the page,
 * automatically — this runs once from base.html, so it applies to login,
 * register, reset/change password, and anywhere else a password field
 * exists now or is added later, with no per-page wiring needed.
 *
 * Markup/CSS-light on purpose: wraps each <input type="password"> in a
 * positioned container and absolutely-positions a plain <button> over its
 * right edge, reusing the existing .icon-btn look rather than introducing
 * new styles. Toggling flips the input's type between "password" and
 * "text" — the value itself is never touched, so this can't affect what
 * gets submitted.
 */
(function () {
  const EYE = '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>';
  const EYE_OFF = '<path d="M3 3l18 18"/><path d="M10.6 5.1A10.4 10.4 0 0 1 12 5c6.5 0 10 7 10 7a17.6 17.6 0 0 1-3.2 4.2M6.6 6.6C4 8.3 2 12 2 12s3.5 7 10 7a9.7 9.7 0 0 0 4.4-1"/><path d="M9.5 10a3 3 0 0 0 4.2 4.2"/>';

  function svg(pathData) {
    return '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
      + 'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
      + pathData + '</svg>';
  }

  function addToggle(input) {
    if (input.dataset.hasToggle) return;
    input.dataset.hasToggle = '1';

    const wrap = document.createElement('div');
    wrap.style.cssText = 'position:relative;display:flex';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    // Room for the button so typed text never runs under it.
    const existingPadding = window.getComputedStyle(input).paddingRight;
    input.style.paddingRight = 'calc(' + (existingPadding || '12px') + ' + 30px)';

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.setAttribute('aria-label', 'Show password');
    btn.setAttribute('tabindex', '-1'); // keyboard users tab through the field, not this
    btn.style.cssText = 'position:absolute;right:4px;top:50%;transform:translateY(-50%);'
      + 'width:30px;height:30px;display:grid;place-items:center;background:transparent;border:0;'
      + 'border-radius:7px;color:var(--muted,#666);cursor:pointer;padding:0';
    btn.innerHTML = svg(EYE);
    btn.addEventListener('mouseenter', () => { btn.style.color = 'var(--text,#111)'; });
    btn.addEventListener('mouseleave', () => { btn.style.color = 'var(--muted,#666)'; });

    btn.addEventListener('click', () => {
      const showing = input.type === 'text';
      input.type = showing ? 'password' : 'text';
      btn.innerHTML = svg(showing ? EYE : EYE_OFF);
      btn.setAttribute('aria-label', showing ? 'Show password' : 'Hide password');
    });

    wrap.appendChild(btn);
  }

  function applyAll(root) {
    (root || document).querySelectorAll('input[type="password"]').forEach(addToggle);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => applyAll());
  } else {
    applyAll();
  }
  // Covers password fields added after initial load (e.g. a form section
  // revealed by other JS), the same pattern used for real-time timestamps.
  if (window.MutationObserver) {
    new MutationObserver((mutations) => {
      mutations.forEach((m) => { if (m.addedNodes.length) applyAll(); });
    }).observe(document.documentElement, { childList: true, subtree: true });
  }
})();
