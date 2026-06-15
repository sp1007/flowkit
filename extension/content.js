/**
 * Content script — bridge between background.js and injected.js
 * Injects injected.js into MAIN world to access window.grecaptcha
 */
(function () {
  const s = document.createElement('script');
  s.src = chrome.runtime.getURL('injected.js');
  s.onload = () => s.remove();
  (document.head || document.documentElement).appendChild(s);
})();

chrome.runtime.onMessage.addListener((msg, _, reply) => {
  if (msg.type !== 'GET_CAPTCHA') return;

  const { requestId, pageAction } = msg;

  const handler = (e) => {
    if (e.detail?.requestId === requestId) {
      window.removeEventListener('CAPTCHA_RESULT', handler);
      clearTimeout(timer);
      reply({ token: e.detail.token, error: e.detail.error });
    }
  };

  const timer = setTimeout(() => {
    window.removeEventListener('CAPTCHA_RESULT', handler);
    reply({ error: 'CONTENT_TIMEOUT' });
  }, 25000);

  window.addEventListener('CAPTCHA_RESULT', handler);

  window.dispatchEvent(new CustomEvent('GET_CAPTCHA', {
    detail: { requestId, pageAction },
  }));

  return true; // keep channel open for async reply
});

chrome.runtime.onMessage.addListener(

  (msg, _, reply) => {

    if (
      msg.type !==
      'GET_MEDIA_URL'
    ) {

      return;

    }

    const {

      requestId,

      mediaId

    } = msg;

    const handler = (e) => {

      if (

        e.detail.requestId
        !== requestId

      ) {

        return;

      }

      window.removeEventListener(

        'GET_MEDIA_URL_RESULT',

        handler

      );

      clearTimeout(timer);

      reply(e.detail);

    };

    const timer =
      setTimeout(() => {

        window.removeEventListener(

          'GET_MEDIA_URL_RESULT',

          handler

        );

        reply({

          error:

            'MEDIA_TIMEOUT'

        });

      }, 15000);

    window.addEventListener(

      'GET_MEDIA_URL_RESULT',

      handler

    );

    window.dispatchEvent(

      new CustomEvent(

        'GET_MEDIA_URL',

        {

          detail: {

            requestId,

            mediaId

          }

        }

      )

    );

    return true;

  });

// ─── TRPC Media URL Monitor ─────────────────────────────────
// Forward intercepted TRPC responses with media URLs to background.js
window.addEventListener('TRPC_MEDIA_URLS', (e) => {
  const { url, body } = e.detail || {};
  if (!body) return;
  chrome.runtime.sendMessage({
    type: 'TRPC_MEDIA_URLS',
    trpcUrl: url,
    body,
  }).catch(() => { });
});
