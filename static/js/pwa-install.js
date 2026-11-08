/* Progressive install UI only; no application data, permissions or forced updates. */
(function () {
  'use strict';
  if (window.__rahhalPwaInstallInitialized) return;
  window.__rahhalPwaInstallInitialized = true;

  var components = [];
  var deferredPrompt = null;
  var pending = false;
  var installedThisPage = false;
  var domInitialized = false;
  var registrationError = false;
  var attempt = 0;
  var usedPrompts = new WeakSet();
  var displayMode = typeof window.matchMedia === 'function'
    ? window.matchMedia('(display-mode: standalone)') : null;

  function standalone() {
    return Boolean((displayMode && displayMode.matches) || navigator.standalone === true);
  }

  function sync() {
    var hide = installedThisPage || standalone();
    components.forEach(function (component) {
      component.root.hidden = hide;
      component.button.disabled = pending;
    });
  }

  function status(message) {
    components.forEach(function (component) {
      component.status.textContent = message;
      component.status.hidden = !message;
    });
  }

  function registrationFailed() {
    registrationError = true;
    // Registration is not proof of installation and never blocks the normal website.
    if (!pending && !installedThisPage && !standalone()) {
      components.forEach(function (component) {
        if (!component.status.textContent) {
          component.status.textContent = 'يمكنك متابعة استخدام رحّال من المتصفح؛ طريقة التثبيت متاحة أدناه.';
          component.status.hidden = false;
        }
      });
    }
  }

  function manual(component, message) {
    component.help.open = true;
    if (message) status(message);
    component.instructions.focus();
  }

  function resetIfHidden() {
    if (installedThisPage || standalone()) {
      deferredPrompt = null;
      pending = false;
      attempt += 1; // Ignore an old choice that settles after appinstalled/display-mode change.
      status('');
      components.forEach(function (component) { component.help.open = false; });
    }
    sync();
  }

  // Listen before DOM initialization; browsers choose whether/when to send this event.
  window.addEventListener('beforeinstallprompt', function (event) {
    if (typeof event.prompt !== 'function' || usedPrompts.has(event)
        || pending || installedThisPage || standalone()) return;
    event.preventDefault();
    deferredPrompt = event;
    sync();
  });

  window.addEventListener('appinstalled', function () {
    installedThisPage = true; // In-memory confirmation only, never a persistent installed flag.
    resetIfHidden();
  });

  if (displayMode) {
    if (typeof displayMode.addEventListener === 'function') {
      displayMode.addEventListener('change', resetIfHidden);
    } else if (typeof displayMode.addListener === 'function') {
      displayMode.addListener(resetIfHidden);
    }
  }

  function install(component) {
    if (pending || installedThisPage || standalone()) return;
    if (!deferredPrompt) {
      manual(component, 'اتبع خطوات المتصفح أدناه لإضافة رحّال إلى الشاشة الرئيسية.');
      return;
    }

    var event = deferredPrompt;
    deferredPrompt = null;
    usedPrompts.add(event);
    pending = true;
    var currentAttempt = ++attempt;
    status('أكمل طلب التثبيت في المتصفح.');
    sync();

    function isCurrent() {
      return currentAttempt === attempt && !installedThisPage && !standalone();
    }

    function failed() {
      if (!isCurrent()) return;
      pending = false;
      sync();
      manual(component, 'تعذّر فتح طلب التثبيت؛ يمكنك اتباع طريقة التثبيت أدناه ومتابعة استخدام الموقع.');
    }

    try {
      // Synchronous call inside the user's click keeps browser user activation intact.
      Promise.resolve(event.prompt()).then(function (result) {
        return event.userChoice || result;
      }).then(function (choice) {
        if (!isCurrent()) return;
        pending = false;
        sync();
        if (choice && choice.outcome === 'accepted') {
          status('وافق المتصفح على طلب التثبيت؛ أكمل خطوات التثبيت.');
        } else if (choice && choice.outcome === 'dismissed') {
          manual(component, 'لم يكتمل التثبيت؛ يمكنك المحاولة من قائمة المتصفح باتباع الخطوات أدناه.');
        } else {
          manual(component, 'تحقّق من خيارات التثبيت في المتصفح، أو اتبع الخطوات أدناه.');
        }
      }, failed);
    } catch (error) {
      failed();
    }
  }

  function init() {
    if (domInitialized) return;
    domInitialized = true;
    document.querySelectorAll('[data-pwa-install]').forEach(function (root) {
      var component = {
        root: root,
        button: root.querySelector('[data-pwa-install-button]'),
        help: root.querySelector('[data-pwa-install-help]'),
        summary: root.querySelector('[data-pwa-install-summary]'),
        instructions: root.querySelector('[data-pwa-install-instructions]'),
        status: root.querySelector('[data-pwa-install-status]')
      };
      if (!component.button || !component.help || !component.summary
          || !component.instructions || !component.status) return;
      components.push(component);
      component.button.addEventListener('click', function () { install(component); });
      component.summary.textContent = 'طريقة التثبيت';
      component.button.hidden = false;
    });
    resetIfHidden();
    if (registrationError) registrationFailed();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, {once: true});
  } else {
    init();
  }

  var location = window.location;
  var hostname = location.hostname;
  var loopback = hostname === 'localhost' || /\.localhost$/.test(hostname)
    || /^127(?:\.[0-9]{1,3}){3}$/.test(hostname)
    || hostname === '[::1]' || hostname === '::1';
  var allowedOrigin = location.protocol === 'https:'
    || (location.protocol === 'http:' && loopback);
  if (window.isSecureContext === true && allowedOrigin && navigator.serviceWorker
      && typeof navigator.serviceWorker.register === 'function') {
    try {
      Promise.resolve(navigator.serviceWorker.register('/service-worker.js', {
        scope: '/', updateViaCache: 'none'
      })).catch(registrationFailed);
    } catch (error) {
      registrationFailed();
    }
  }
})();
