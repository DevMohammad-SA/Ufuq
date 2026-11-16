/* Private trip notifications: progressive enhancement of native POST forms. */
(function () {
  'use strict';
  var kinds = {
    nominated: 'تم الترشيح', not_nominated: 'لم يتم الترشيح',
    closed: 'أُغلقت الرحلة', cancelled: 'أُلغيت الرحلة'
  };
  var interval = 30000;

  function safeURL(value) {
    if (typeof value !== 'string' || !value || value !== value.trim() || /[\\\s\u0000-\u001f\u007f]/.test(value)) return null;
    try {
      var url = new URL(value, window.location.href);
      return url.origin === window.location.origin && !url.username && !url.password ? url : null;
    } catch (error) { return null; }
  }
  function stamp(value) {
    return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
  }
  function count(value) { return Number.isSafeInteger(value) && value >= 0; }
  function stoppedError() {
    var error = new Error('Private notification session unavailable');
    error.authStop = true;
    return error;
  }
  function init(root) {
    if (root.dataset.notificationsInitialized) return;
    root.dataset.notificationsInitialized = 'true';
    var list = root.querySelector('[data-notifications-list]');
    var empty = root.querySelector('[data-notifications-empty]');
    var badge = root.querySelector('[data-notifications-count]');
    var status = root.querySelector('[data-notifications-status]');
    var refresh = root.querySelector('[data-notifications-refresh]');
    var seed = root.querySelector('#trip-notifications-initial');
    var csrf = root.querySelector('[data-notifications-csrf] input[name="csrfmiddlewaretoken"]');
    if (!list || !empty || !badge || !status || !refresh || !seed || !csrf) return;
    var trips = safeURL(root.dataset.tripsUrl);
    var feed = safeURL(root.dataset.notificationsUrl);
    var busy = false, stopped = false, pageActive = true, timer = null, controller = null;
    var current = null, signature = '', readFailure = false;
    function say(message, error) {
      status.textContent = message;
      status.classList.toggle('is-error', Boolean(error));
    }
    if (!trips || !/\/my-trips\/$/.test(trips.pathname) || trips.search || trips.hash ||
        !feed || feed.pathname !== trips.pathname + 'notifications/' || feed.search || feed.hash) {
      say('تعذّر تفعيل تحديث الإشعارات؛ أعد تحميل الصفحة.', true);
      return;
    }
    function validate(data) {
      if (!data || data.ok !== true || !Array.isArray(data.notifications) || data.notifications.length > 20 ||
          !count(data.unread_count) || !count(data.attention_count)) throw new Error('Invalid notification schema');
      var ids = new Set();
      data.notifications.forEach(function (item) {
        if (!item || !Number.isSafeInteger(item.id) || item.id <= 0 || ids.has(item.id) ||
            !Object.prototype.hasOwnProperty.call(kinds, item.kind) || typeof item.message !== 'string' ||
            item.message.length > 600 || !stamp(item.created_at) || (item.read_at !== null && !stamp(item.read_at))) {
          throw new Error('Invalid notification');
        }
        var detail = safeURL(item.url), read = safeURL(item.read_url);
        if (!detail || detail.pathname !== trips.pathname || detail.search || !/^#trip-[1-9]\d*$/.test(detail.hash) ||
            !read || read.pathname !== feed.pathname + item.id + '/read/' || read.search || read.hash) {
          throw new Error('Invalid notification URL');
        }
        ids.add(item.id);
      });
      return data;
    }
    function element(tag, cls, text) {
      var node = document.createElement(tag);
      if (cls) node.className = cls;
      if (text !== undefined) node.textContent = text;
      return node;
    }
    function time(value) {
      var node = element('time');
      node.setAttribute('datetime', value);
      try {
        node.textContent = new Date(value).toLocaleString('ar-SA', {
          calendar: 'gregory', year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
        });
      } catch (error) { node.textContent = value; }
      return node;
    }
    function navCount(value) {
      document.querySelectorAll('a.side-link, a.app-nav__link').forEach(function (link) {
        var url = safeURL(link.getAttribute('href'));
        if (!url || url.pathname !== trips.pathname || url.search || url.hash) return;
        var navBadge = link.querySelector('.app-nav__badge');
        if (!navBadge && value > 0) {
          navBadge = element('span', 'app-nav__badge');
          link.appendChild(navBadge);
        }
        if (navBadge) {
          if (value === 0) navBadge.remove();
          else navBadge.textContent = String(value);
        }
      });
    }
    function render(data) {
      var nextSignature = JSON.stringify(data);
      if (nextSignature === signature) return;
      var focused = document.activeElement;
      var focusedRow = focused && root.contains(focused) && focused.closest('[data-notification-id]');
      var focusedId = focusedRow && focusedRow.getAttribute('data-notification-id');
      var focusedLink = focused && focused.tagName === 'A';
      var fragment = document.createDocumentFragment();
      data.notifications.forEach(function (item) {
        var row = element('li', 'trip-notifications__item' + (item.read_at === null ? ' is-unread' : ''));
        row.setAttribute('data-notification-id', String(item.id));
        row.setAttribute('tabindex', '-1');
        var meta = element('div', 'trip-notifications__meta');
        meta.appendChild(element('span', 'trip-notifications__kind', kinds[item.kind]));
        meta.appendChild(element('span', 'trip-notifications__read-state', item.read_at === null ? 'غير مقروء' : 'مقروء'));
        meta.appendChild(time(item.created_at));
        row.appendChild(meta);
        row.appendChild(element('p', 'trip-notifications__message', item.message));
        var actions = element('div', 'trip-notifications__actions');
        var detail = element('a', 'trip-notifications__button', 'تفاصيل الرحلة');
        detail.setAttribute('href', safeURL(item.url).href);
        actions.appendChild(detail);
        if (item.read_at !== null) {
          var readStamp = element('span', 'trip-notifications__read-stamp', 'تم الاطلاع: ');
          readStamp.appendChild(time(item.read_at));
          actions.appendChild(readStamp);
        } else {
          var form = element('form');
          form.setAttribute('method', 'post');
          form.setAttribute('action', safeURL(item.read_url).href);
          form.setAttribute('data-notification-read', '');
          form.setAttribute('data-notification-id', String(item.id));
          [['csrfmiddlewaretoken', csrf.value], ['action', 'read']].forEach(function (field) {
            var input = element('input');
            input.setAttribute('type', 'hidden');
            input.setAttribute('name', field[0]);
            input.value = field[1];
            form.appendChild(input);
          });
          var button = element('button', 'trip-notifications__button', 'تم الاطلاع');
          button.setAttribute('type', 'submit');
          form.appendChild(button);
          actions.appendChild(form);
        }
        row.appendChild(actions);
        fragment.appendChild(row);
      });
      list.replaceChildren(fragment);
      empty.hidden = data.notifications.length !== 0;
      badge.textContent = String(data.unread_count);
      badge.setAttribute('aria-label', 'غير مقروء: ' + data.unread_count);
      navCount(data.attention_count);
      current = data;
      signature = nextSignature;
      if (focusedId) {
        var replacement = list.querySelector('[data-notification-id="' + focusedId + '"]');
        if (replacement) (replacement.querySelector(focusedLink ? 'a' : 'button') || replacement).focus();
      }
    }
    function active() {
      if (!pageActive || document.visibilityState !== 'visible') return false;
      if (window.top !== window.self) {
        try {
          var frame = window.frameElement;
          if (!frame || frame.hidden || !frame.getClientRects().length) return false;
          var style = window.getComputedStyle(frame);
          if (style.display === 'none' || style.visibility === 'hidden') return false;
        } catch (error) { return false; }
      }
      return true;
    }
    function cancelTimer() { if (timer !== null) window.clearTimeout(timer); timer = null; }
    function schedule() {
      cancelTimer();
      if (!stopped && active()) timer = window.setTimeout(function () { timer = null; update(false); }, interval);
    }
    function stop() {
      stopped = true;
      cancelTimer();
      refresh.disabled = true;
      say('توقف تحديث الإشعارات لتعذّر التحقق من جلسة الحساب. أعد تحميل الصفحة أو سجّل الدخول؛ لم تُؤكَّد أي قراءة جديدة.', true);
    }
    async function request(method, url, body) {
      controller = new window.AbortController();
      var timeout = window.setTimeout(function () { if (controller) controller.abort(); }, 20000);
      try {
        var response = await window.fetch(url.href, {
          method: method, credentials: 'same-origin', cache: 'no-store', redirect: 'manual',
          referrerPolicy: 'same-origin', headers: {Accept: 'application/json'},
          body: body, signal: controller.signal
        });
        if (response.type === 'opaqueredirect' || response.redirected || response.status === 401 || response.status === 403 ||
            (response.status >= 300 && response.status < 400)) throw stoppedError();
        var returnedURL = safeURL(response.url);
        if (!returnedURL || returnedURL.href !== url.href ||
            !/^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type') || '')) throw stoppedError();
        if (!response.ok) throw new Error('Notification HTTP failure');
        return validate(await response.json());
      } finally {
        window.clearTimeout(timeout);
        controller = null;
      }
    }
    async function update(manual) {
      if (busy || stopped || !active()) { schedule(); return; }
      busy = true;
      cancelTimer();
      refresh.disabled = true;
      try {
        var data = await request('GET', feed);
        if (!pageActive) return;
        var added = data.unread_count > current.unread_count;
        render(data);
        if (manual) { readFailure = false; say('الإشعارات محدثة.', false); }
        else if (!readFailure) say(added ? 'وصلت إشعارات رحلات جديدة.' : '', false);
      } catch (error) {
        if (!pageActive) return;
        if (error.authStop) stop();
        else if (!readFailure) say('تعذّر تحديث الإشعارات. بقيت القائمة كما هي؛ يمكنك المحاولة بزر التحديث.', true);
      } finally {
        busy = false;
        refresh.disabled = stopped;
        schedule();
      }
    }
    try {
      render(validate({ok: true, notifications: JSON.parse(seed.textContent),
        unread_count: Number(root.dataset.unreadCount), attention_count: Number(root.dataset.attentionCount)}));
    } catch (error) { say('تعذّر تفعيل تحديث الإشعارات؛ أعد تحميل الصفحة.', true); return; }
    if (typeof window.fetch !== 'function' || typeof window.AbortController !== 'function') return;
    refresh.hidden = false;
    refresh.addEventListener('click', function () { update(true); });
    root.addEventListener('submit', async function (event) {
      var form = event.target;
      if (!form.matches('form[data-notification-read]')) return;
      event.preventDefault();
      if (stopped || !pageActive) { say('تعذّر تأكيد القراءة. أعد تحميل الصفحة للتحقق من جلسة الحساب.', true); return; }
      if (busy) { say('يجري تحديث الإشعارات؛ أعد محاولة تأكيد الاطلاع بعد اكتمال الطلب.', true); return; }
      var id = Number(form.getAttribute('data-notification-id'));
      var item = current.notifications.find(function (entry) { return entry.id === id; });
      var url = safeURL(form.getAttribute('action'));
      if (!item || item.read_at !== null || !url || url.href !== safeURL(item.read_url).href) {
        say('تعذّر تأكيد القراءة. أعد تحميل الصفحة.', true); return;
      }
      var button = form.querySelector('button[type="submit"]');
      busy = true;
      cancelTimer();
      button.disabled = true;
      refresh.disabled = true;
      say('جارٍ تأكيد الاطلاع؛ لم تُؤكَّد القراءة بعد.', false);
      try {
        var body = new URLSearchParams();
        body.set('csrfmiddlewaretoken', csrf.value);
        body.set('action', 'read');
        var data = await request('POST', url, body);
        if (!pageActive) return;
        var acknowledged = data.notifications.find(function (entry) { return entry.id === id; });
        // A validated ok:true from this exact POST is the server ACK. With unread
        // priority, a newly read item can leave the capped list. Never invent its
        // timestamp; if it remains in the response, its read_at must confirm it.
        if (acknowledged && acknowledged.read_at === null) throw new Error('Missing read acknowledgement');
        render(data);
        readFailure = false;
        say('تم تأكيد الاطلاع.', false);
      } catch (error) {
        if (!pageActive) return;
        readFailure = true;
        if (error.authStop) stop();
        else say('تعذّر تأكيد الاطلاع؛ بقي الإشعار دون تغيير. اضغط «تم الاطلاع» للمحاولة مجددًا.', true);
      } finally {
        button.disabled = false;
        busy = false;
        refresh.disabled = stopped;
        schedule();
      }
    });
    document.addEventListener('visibilitychange', function () {
      if (active()) update(false); else cancelTimer();
    });
    window.addEventListener('focus', function () { if (active()) update(false); });
    window.addEventListener('pagehide', function () {
      pageActive = false;
      cancelTimer();
      if (controller) controller.abort();
    });
    window.addEventListener('pageshow', function (event) {
      if (event.persisted) { pageActive = true; if (active()) update(false); }
    });
    schedule();
  }
  function boot() { document.querySelectorAll('[data-trip-notifications]').forEach(init); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, {once: true});
  else boot();
}());
