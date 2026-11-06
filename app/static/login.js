(() => {
  const SESSION_TOKEN_KEY = 'ev_session_token';
  const USER_CACHE_KEY = 'ev_user_cache';

  function safeNextPath() {
    try {
      const params = new URLSearchParams(window.location.search || '');
      const raw = (params.get('next') || '').trim();
      if (raw && raw.startsWith('/') && !raw.startsWith('//') && !raw.startsWith('/login.html')) {
        return raw;
      }
    } catch (_) {}
    return '/';
  }

  function authHeaders() {
    const headers = { 'Content-Type': 'application/json' };
    try {
      const token = sessionStorage.getItem(SESSION_TOKEN_KEY) || '';
      if (/^[a-f0-9]{64}$/.test(token)) {
        headers['Authorization'] = 'Bearer ' + token;
      }
    } catch (_) {}
    return headers;
  }

  function bindPasswordToggle() {
    const input = document.getElementById('login-password');
    const toggle = document.getElementById('password-toggle');
    if (!input || !toggle) return;
    const eye = toggle.querySelector('.ev-icon-eye');
    const eyeOff = toggle.querySelector('.ev-icon-eye-off');
    toggle.addEventListener('click', () => {
      const reveal = input.type === 'password';
      input.type = reveal ? 'text' : 'password';
      toggle.setAttribute('aria-pressed', reveal ? 'true' : 'false');
      toggle.setAttribute('aria-label', reveal ? '隐藏密码' : '显示密码');
      if (eye) eye.hidden = reveal;
      if (eyeOff) eyeOff.hidden = !reveal;
      input.focus({ preventScroll: true });
    });
  }

  function showError(message) {
    const box = document.getElementById('login-error');
    if (!box) return;
    if (!message) {
      box.hidden = true;
      box.textContent = '';
      return;
    }
    box.textContent = message;
    box.hidden = false;
  }

  function showNotice(message) {
    const box = document.getElementById('login-notice');
    if (!box) return;
    if (!message) {
      box.hidden = true;
      box.textContent = '';
      return;
    }
    box.textContent = message;
    box.hidden = false;
  }

  async function checkExistingSession() {
    try {
      const params = new URLSearchParams(window.location.search || '');
      if (params.get('reason') === 'auth_required') {
        showNotice('请先登录受邀账号后再访问核验工作台。');
      } else if (params.get('reason') === 'logged_out') {
        showNotice('您已安全退出当前受邀会话。');
      }
      const res = await fetch('/api/auth/me', {
        method: 'GET',
        credentials: 'same-origin',
        headers: authHeaders(),
      });
      if (!res.ok) return;
      const data = await res.json();
      if (data && data.authenticated && data.user) {
        try {
          sessionStorage.setItem(USER_CACHE_KEY, JSON.stringify(data.user));
        } catch (_) {}
        window.location.replace(safeNextPath());
      }
    } catch (_) {}
  }

  function bindLoginForm() {
    const form = document.getElementById('login-form');
    const idInput = document.getElementById('login-identifier');
    const pwdInput = document.getElementById('login-password');
    const submitBtn = document.getElementById('login-submit');
    const submitText = document.getElementById('login-submit-text');
    if (!form || !idInput || !pwdInput || !submitBtn) return;

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      showError('');
      const identifier = (idInput.value || '').trim();
      const password = pwdInput.value || '';
      if (!identifier || !password) {
        showError('请输入受邀账号用户名/邮箱和登录密码。');
        (!identifier ? idInput : pwdInput).focus();
        return;
      }
      submitBtn.disabled = true;
      if (submitText) submitText.textContent = '正在核验受邀身份…';
      try {
        const res = await fetch('/api/auth/login', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ identifier, password }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.authenticated) {
          throw new Error(data.error || '登录失败：请检查用户名/邮箱与密码是否正确。');
        }
        try {
          if (data.session_token) {
            sessionStorage.setItem(SESSION_TOKEN_KEY, data.session_token);
          }
          if (data.user) {
            sessionStorage.setItem(USER_CACHE_KEY, JSON.stringify(data.user));
          }
        } catch (_) {}
        window.location.replace(safeNextPath());
      } catch (err) {
        showError(err.message || '登录请求失败，请稍后重试。');
      } finally {
        submitBtn.disabled = false;
        if (submitText) submitText.textContent = '登录并进入工作台';
      }
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    bindPasswordToggle();
    bindLoginForm();
    checkExistingSession();
  });
})();
