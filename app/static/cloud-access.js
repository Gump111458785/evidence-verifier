(() => {
  const SESSION_KEY = 'ev_session_token';
  const LEGACY_KEY = 'ev_cloud_access_code';
  const USER_CACHE_KEY = 'ev_user_cache';

  const EYE_SVG = '<svg class="ev-icon-eye" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.2 12s3.6-6.5 9.8-6.5 9.8 6.5 9.8 6.5-3.6 6.5-9.8 6.5S2.2 12 2.2 12Z"></path><circle cx="12" cy="12" r="3.1"></circle></svg>';
  const EYE_OFF_SVG = '<svg class="ev-icon-eye-off" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" hidden><path d="M3 3l18 18"></path><path d="M10.58 10.58a2 2 0 0 0 2.83 2.83"></path><path d="M9.88 5.09A10.4 10.4 0 0 1 12 4.8c6.2 0 9.8 7.2 9.8 7.2a16.8 16.8 0 0 1-3.16 4.19"></path><path d="M6.61 6.61A16.5 16.5 0 0 0 2.2 12s3.6 7.2 9.8 7.2a10.3 10.3 0 0 0 5.39-1.48"></path></svg>';

  let currentUser = null;
  let authRequired = false;

  const token = () => {
    try {
      return sessionStorage.getItem(SESSION_KEY) || sessionStorage.getItem(LEGACY_KEY) || '';
    } catch (_) {
      return '';
    }
  };

  const setToken = (val) => {
    try {
      if (val) sessionStorage.setItem(SESSION_KEY, val.trim());
      else {
        sessionStorage.removeItem(SESSION_KEY);
        sessionStorage.removeItem(LEGACY_KEY);
      }
    } catch (_) {}
  };

  const headers = (extra = {}) => {
    const t = token();
    return t ? { ...extra, Authorization: 'Bearer ' + t } : { ...extra };
  };

  const isDemoPreview = () => {
    try {
      const p = new URLSearchParams(window.location.search || '');
      return p.get('preview') === 'demo';
    } catch (_) {
      return false;
    }
  };

  const isLoginPage = () => {
    const p = window.location.pathname || '';
    return p.endsWith('/login.html') || p === '/login';
  };

  const redirectToLogin = (reason = 'auth_required') => {
    if (isLoginPage()) return;
    const next = encodeURIComponent((window.location.pathname || '/') + (window.location.search || '') + (window.location.hash || ''));
    window.location.replace(`/login.html?reason=${encodeURIComponent(reason)}&next=${next}`);
  };

  const formatTime = (ts) => {
    if (!ts) return '尚未登录';
    try {
      return new Date(Number(ts) * 1000).toLocaleString('zh-CN', { hour12: false });
    } catch (_) {
      return '—';
    }
  };

  const bindPasswordToggles = (root = document) => {
    root.querySelectorAll('[data-password-toggle]').forEach((btn) => {
      if (btn.dataset.boundToggle === '1') return;
      btn.dataset.boundToggle = '1';
      if (!btn.innerHTML.trim()) {
        btn.innerHTML = EYE_SVG + EYE_OFF_SVG;
      }
      btn.addEventListener('click', () => {
        const targetId = btn.getAttribute('data-password-toggle');
        const input = targetId ? document.getElementById(targetId) : btn.parentElement?.querySelector('input');
        if (!input) return;
        const reveal = input.type === 'password';
        input.type = reveal ? 'text' : 'password';
        btn.setAttribute('aria-pressed', reveal ? 'true' : 'false');
        btn.setAttribute('aria-label', reveal ? '隐藏密码' : '显示密码');
        const eye = btn.querySelector('.ev-icon-eye');
        const eyeOff = btn.querySelector('.ev-icon-eye-off');
        if (eye) eye.hidden = reveal;
        if (eyeOff) eyeOff.hidden = !reveal;
      });
    });
  };

  const updateTopbarUser = () => {
    const trigger = document.getElementById('cloud-access-trigger');
    const avatarText = document.getElementById('user-avatar-initial');
    const userLabel = document.getElementById('user-display-label');
    const rolePill = document.getElementById('user-role-pill');
    const adminMenuBtn = document.getElementById('menu-open-admin');

    if (currentUser) {
      const display = currentUser.display_name || currentUser.username || '受邀用户';
      if (avatarText) avatarText.textContent = display.slice(0, 1).toUpperCase();
      if (userLabel) userLabel.textContent = display;
      if (rolePill) {
        rolePill.textContent = currentUser.role === 'admin' ? '管理员' : '受邀成员';
        rolePill.className = 'pill ' + (currentUser.role === 'admin' ? 'entailed' : 'neutral');
      }
      if (trigger) trigger.textContent = `${display} · 个人中心`;
      if (adminMenuBtn) adminMenuBtn.hidden = currentUser.role !== 'admin';
    } else {
      if (avatarText) avatarText.textContent = '访';
      if (userLabel) userLabel.textContent = isDemoPreview() ? '公开演示预览' : '未登录';
      if (rolePill) {
        rolePill.textContent = isDemoPreview() ? '演示模式' : '受邀开放';
        rolePill.className = 'pill neutral';
      }
      if (trigger) trigger.textContent = '受邀账号登录';
      if (adminMenuBtn) adminMenuBtn.hidden = true;
    }
    renderAccountCenter();
  };

  const refreshBudget = async () => {
    const pill = document.getElementById('cloud-budget-pill');
    if (!pill) return null;
    if (!currentUser && !token()) {
      pill.textContent = isDemoPreview() ? '公开演示模式 · 零付费调用' : '受邀账号会话未登录';
      return null;
    }
    try {
      const res = await fetch('/api/budget', { credentials: 'same-origin', headers: headers() });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        pill.textContent = '受邀会话已就绪';
        return null;
      }
      const used = (data.reserved_millicny / 1000).toFixed(2);
      const limit = (data.limit_millicny / 1000).toFixed(2);
      pill.textContent = `试用额度 ¥${used} / ¥${limit}`;
      return data;
    } catch (_) {
      return null;
    }
  };

  const refreshAuthState = async () => {
    try {
      const [healthRes, meRes] = await Promise.all([
        fetch('/api/health', { credentials: 'same-origin' }).catch(() => null),
        fetch('/api/auth/me', { credentials: 'same-origin', headers: headers() }).catch(() => null),
      ]);
      const health = healthRes && healthRes.ok ? await healthRes.json().catch(() => ({})) : {};
      const me = meRes && meRes.ok ? await meRes.json().catch(() => ({})) : {};
      authRequired = Boolean(me.auth_required || health.cloud_mode === 'durable_step_jobs');
      if (me && me.authenticated && me.user) {
        currentUser = me.user;
        try {
          sessionStorage.setItem(USER_CACHE_KEY, JSON.stringify(currentUser));
        } catch (_) {}
      } else {
        currentUser = null;
        if (authRequired && !isDemoPreview() && !isLoginPage()) {
          redirectToLogin('auth_required');
          return null;
        }
      }
      updateTopbarUser();
      await refreshBudget();
      if (currentUser && currentUser.role === 'admin') {
        loadAdminUsers();
      }
      return currentUser;
    } catch (_) {
      return null;
    }
  };

  const renderAccountCenter = () => {
    const nameEl = document.getElementById('account-summary-name');
    const usernameEl = document.getElementById('account-summary-username');
    const emailEl = document.getElementById('account-summary-email');
    const roleEl = document.getElementById('account-summary-role');
    const lastLoginEl = document.getElementById('account-summary-last-login');
    const ownerEl = document.getElementById('account-summary-owner');
    const profileDisplay = document.getElementById('account-display-name');
    const profileEmail = document.getElementById('account-email');
    const adminSection = document.getElementById('account-admin-section');
    const guestBanner = document.getElementById('account-guest-banner');

    if (!currentUser) {
      if (guestBanner) guestBanner.hidden = false;
      if (adminSection) adminSection.hidden = true;
      if (nameEl) nameEl.textContent = '未登录（公开演示或本地模式）';
      if (usernameEl) usernameEl.textContent = '—';
      if (emailEl) emailEl.textContent = '—';
      if (roleEl) roleEl.textContent = '访客预览';
      if (lastLoginEl) lastLoginEl.textContent = '—';
      if (ownerEl) ownerEl.textContent = '—';
      return;
    }

    if (guestBanner) guestBanner.hidden = true;
    if (nameEl) nameEl.textContent = currentUser.display_name || currentUser.username;
    if (usernameEl) usernameEl.textContent = currentUser.username;
    if (emailEl) emailEl.textContent = currentUser.email || '—';
    if (roleEl) roleEl.textContent = currentUser.role === 'admin' ? '管理员 (Admin)' : '受邀研究成员 (Member)';
    if (lastLoginEl) lastLoginEl.textContent = formatTime(currentUser.last_login_at);
    if (ownerEl) ownerEl.textContent = currentUser.report_owner || `acct:${currentUser.id}`;
    if (profileDisplay && document.activeElement !== profileDisplay) {
      profileDisplay.value = currentUser.display_name || '';
    }
    if (profileEmail && document.activeElement !== profileEmail) {
      profileEmail.value = currentUser.email || '';
    }
    if (adminSection) {
      adminSection.hidden = currentUser.role !== 'admin';
    }
  };

  const setAccountFeedback = (id, message, isError = false) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (!message) {
      el.hidden = true;
      el.textContent = '';
      return;
    }
    el.hidden = false;
    el.className = isError ? 'notice error-notice' : 'notice ok-notice';
    el.textContent = message;
  };

  const loadAdminUsers = async () => {
    const tbody = document.getElementById('admin-users-tbody');
    if (!tbody || !currentUser || currentUser.role !== 'admin') return;
    try {
      const res = await fetch('/api/auth/admin/users', {
        credentials: 'same-origin',
        headers: headers(),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !Array.isArray(data.users)) return;
      tbody.replaceChildren();
      data.users.forEach((u) => {
        const tr = document.createElement('tr');
        const tdUser = document.createElement('td');
        tdUser.innerHTML = `<strong>${u.display_name || u.username}</strong><div class="muted mono">${u.username}</div>`;
        const tdEmail = document.createElement('td');
        tdEmail.textContent = u.email || '—';
        const tdRole = document.createElement('td');
        tdRole.innerHTML = `<span class="pill ${u.role === 'admin' ? 'entailed' : 'neutral'}">${u.role === 'admin' ? '管理员' : '受邀成员'}</span>`;
        const tdStatus = document.createElement('td');
        tdStatus.innerHTML = `<span class="pill ${u.status === 'active' ? 'entailed' : 'contradicted'}">${u.status === 'active' ? '正常启用' : '已停用'}</span>`;
        const tdLogin = document.createElement('td');
        tdLogin.className = 'muted';
        tdLogin.textContent = formatTime(u.last_login_at);
        const tdActions = document.createElement('td');
        tdActions.className = 'row';

        const resetBtn = document.createElement('button');
        resetBtn.type = 'button';
        resetBtn.className = 'secondary small-btn';
        resetBtn.textContent = '重置密码';
        resetBtn.onclick = async () => {
          setAccountFeedback('admin-user-feedback', '');
          const r = await fetch(`/api/auth/admin/users/${u.id}`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: headers({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ generate_password: true }),
          });
          const out = await r.json().catch(() => ({}));
          if (!r.ok) {
            setAccountFeedback('admin-user-feedback', out.error || '重置密码失败', true);
            return;
          }
          setAccountFeedback(
            'admin-user-feedback',
            `已为账号 ${u.username} 生成新临时密码：${out.generated_password}（请立即复制并安全发送给受邀人）`,
          );
          loadAdminUsers();
        };

        const toggleBtn = document.createElement('button');
        toggleBtn.type = 'button';
        toggleBtn.className = 'secondary small-btn';
        const nextStatus = u.status === 'active' ? 'disabled' : 'active';
        toggleBtn.textContent = u.status === 'active' ? '停用' : '启用';
        if (u.id === currentUser.id) {
          toggleBtn.disabled = true;
          toggleBtn.title = '不能停用当前登录的管理员自身';
        }
        toggleBtn.onclick = async () => {
          const r = await fetch(`/api/auth/admin/users/${u.id}`, {
            method: 'POST',
            credentials: 'same-origin',
            headers: headers({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ status: nextStatus }),
          });
          const out = await r.json().catch(() => ({}));
          if (!r.ok) {
            setAccountFeedback('admin-user-feedback', out.error || '更新账号状态失败', true);
            return;
          }
          setAccountFeedback('admin-user-feedback', `账号 ${u.username} 状态已更新为 ${nextStatus === 'active' ? '启用' : '停用'}。`);
          loadAdminUsers();
        };

        tdActions.append(resetBtn, toggleBtn);
        tr.append(tdUser, tdEmail, tdRole, tdStatus, tdLogin, tdActions);
        tbody.append(tr);
      });
    } catch (_) {}
  };

  const logout = async () => {
    try {
      await fetch('/api/auth/logout', {
        method: 'POST',
        credentials: 'same-origin',
        headers: headers({ 'Content-Type': 'application/json' }),
        body: '{}',
      });
    } catch (_) {}
    setToken('');
    try {
      sessionStorage.removeItem(USER_CACHE_KEY);
    } catch (_) {}
    currentUser = null;
    redirectToLogin('logged_out');
  };

  const openAccountView = (focusAdmin = false) => {
    if (!currentUser && authRequired) {
      redirectToLogin('auth_required');
      return;
    }
    const navBtn = document.querySelector('nav button[data-view="account"]');
    if (navBtn) navBtn.click();
    else {
      document.querySelectorAll('.view').forEach((v) => v.classList.toggle('active', v.id === 'account'));
    }
    renderAccountCenter();
    if (currentUser && currentUser.role === 'admin') {
      loadAdminUsers();
      if (focusAdmin) {
        document.getElementById('account-admin-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }
  };

  const request = async (path, body, method = body === undefined ? 'GET' : 'POST') => {
    const init = {
      method,
      credentials: 'same-origin',
      headers: headers(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    };
    if (body !== undefined) init.body = JSON.stringify(body);
    const res = await fetch(path, init);
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) {
      if (authRequired && !isLoginPage()) {
        redirectToLogin('auth_required');
      }
      throw new Error(data.error || '请先使用受邀账号登录');
    }
    if (!res.ok) throw new Error(data.error || data.reason || res.statusText);
    if (path.startsWith('/api/') && method === 'POST') refreshBudget();
    return data;
  };

  window.evCloudAccess = {
    token,
    headers,
    request,
    refresh: refreshBudget,
    refreshAuth: refreshAuthState,
    show: refreshAuthState,
    open: () => openAccountView(false),
    logout,
    user: () => currentUser,
  };

  window.addEventListener('DOMContentLoaded', () => {
    bindPasswordToggles(document);

    // User dropdown menu interactions
    const menuToggle = document.getElementById('user-menu-toggle');
    const menuDropdown = document.getElementById('user-dropdown-menu');
    if (menuToggle && menuDropdown) {
      menuToggle.addEventListener('click', (e) => {
        e.stopPropagation();
        const willOpen = menuDropdown.hidden;
        menuDropdown.hidden = !willOpen;
        menuToggle.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
      });
      document.addEventListener('click', (e) => {
        if (!menuDropdown.hidden && !menuDropdown.contains(e.target) && e.target !== menuToggle) {
          menuDropdown.hidden = true;
          menuToggle.setAttribute('aria-expanded', 'false');
        }
      });
    }

    document.getElementById('cloud-access-trigger')?.addEventListener('click', () => {
      if (!currentUser) {
        window.location.href = '/login.html';
      } else {
        openAccountView(false);
      }
    });

    document.getElementById('menu-open-account')?.addEventListener('click', () => {
      if (menuDropdown) menuDropdown.hidden = true;
      openAccountView(false);
    });
    document.getElementById('menu-open-admin')?.addEventListener('click', () => {
      if (menuDropdown) menuDropdown.hidden = true;
      openAccountView(true);
    });
    document.getElementById('menu-new-audit')?.addEventListener('click', () => {
      if (menuDropdown) menuDropdown.hidden = true;
      document.querySelector('nav button[data-view="audit"]')?.click();
    });
    document.getElementById('menu-logout')?.addEventListener('click', () => {
      if (menuDropdown) menuDropdown.hidden = true;
      logout();
    });
    document.getElementById('account-logout-btn')?.addEventListener('click', logout);

    // Profile update form
    document.getElementById('account-profile-form')?.addEventListener('submit', async (e) => {
      e.preventDefault();
      setAccountFeedback('account-profile-feedback', '');
      const display_name = document.getElementById('account-display-name')?.value || '';
      const email = document.getElementById('account-email')?.value || '';
      try {
        const res = await fetch('/api/auth/profile', {
          method: 'POST',
          credentials: 'same-origin',
          headers: headers({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ display_name, email }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || '更新个人资料失败');
        currentUser = data.user;
        updateTopbarUser();
        setAccountFeedback('account-profile-feedback', '个人资料已保存更新。');
      } catch (err) {
        setAccountFeedback('account-profile-feedback', err.message, true);
      }
    });

    // Password change form
    document.getElementById('account-password-form')?.addEventListener('submit', async (e) => {
      e.preventDefault();
      setAccountFeedback('account-password-feedback', '');
      const current_password = document.getElementById('account-current-password')?.value || '';
      const new_password = document.getElementById('account-new-password')?.value || '';
      try {
        const res = await fetch('/api/auth/password', {
          method: 'POST',
          credentials: 'same-origin',
          headers: headers({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ current_password, new_password }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || '密码更新失败');
        if (data.session_token) setToken(data.session_token);
        document.getElementById('account-current-password').value = '';
        document.getElementById('account-new-password').value = '';
        setAccountFeedback('account-password-feedback', '密码已更新，其余旧设备会话已自动失效。');
      } catch (err) {
        setAccountFeedback('account-password-feedback', err.message, true);
      }
    });

    // Admin create invited account form
    document.getElementById('admin-create-user-form')?.addEventListener('submit', async (e) => {
      e.preventDefault();
      setAccountFeedback('admin-user-feedback', '');
      const username = document.getElementById('admin-new-username')?.value || '';
      const email = document.getElementById('admin-new-email')?.value || '';
      const display_name = document.getElementById('admin-new-display-name')?.value || '';
      const role = document.getElementById('admin-new-role')?.value || 'member';
      const password = document.getElementById('admin-new-password')?.value || '';
      try {
        const res = await fetch('/api/auth/admin/users', {
          method: 'POST',
          credentials: 'same-origin',
          headers: headers({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ username, email, display_name, role, password }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || '创建受邀账号失败');
        const pwdHint = data.generated_password
          ? `初始随机密码：${data.generated_password}（请立即复制保存）`
          : '已使用您指定的初始密码创建';
        setAccountFeedback('admin-user-feedback', `受邀账号 ${data.user.username} 已开通！${pwdHint}`);
        e.target.reset();
        loadAdminUsers();
      } catch (err) {
        setAccountFeedback('admin-user-feedback', err.message, true);
      }
    });

    refreshAuthState();
  });
})();
