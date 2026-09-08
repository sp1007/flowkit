/**
 * Injected into MAIN world on labs.google VÀ flow.google.com — has access to window.grecaptcha
 * (hai domain dùng CHUNG một site key reCAPTCHA nên token lấy ở đâu cũng dùng được).
 * Also intercepts TRPC fetch responses to capture fresh signed media URLs — chỉ có trên
 * labs.google; giao diện mới flow.google.com không đi qua tRPC nên nhánh đó im lặng.
 * GET_MEDIA_URL cũng vậy: background chỉ gửi tới tab labs.google (xem getMediaUrl).
 */
const SITE_KEY = '6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV';

// ─── TRPC Response Monitor ─────────────────────────────────
// Monkey-patch fetch to intercept TRPC responses containing media URLs.
// Fresh signed GCS URLs are extracted and forwarded to the agent.

const _originalFetch = window.fetch;
window.fetch = async function (...args) {
  const response = await _originalFetch.apply(this, args);
  try {
    const url = typeof args[0] === 'string' ? args[0] : args[0]?.url || '';
    // Only intercept TRPC calls on labs.google that return project/flow data
    if (url.includes('/fx/api/trpc/') && response.ok) {
      const clone = response.clone();
      clone.text().then(text => {
        // Host media đã đổi: ảnh mới ở flow-content.google (Cloud CDN), host GCS cũ chỉ
        // còn gặp ở media đời trước. Xem MEDIA_URL_RE ở background.js.
        if (text.includes('flow-content.google/') ||
            text.includes('storage.googleapis.com/ai-sandbox-videofx/')) {
          window.dispatchEvent(new CustomEvent('TRPC_MEDIA_URLS', {
            detail: { url, body: text },
          }));
        }
      }).catch(() => { });
    }
  } catch { }
  return response;
};


window.addEventListener('GET_CAPTCHA', async ({ detail }) => {
  const { requestId, pageAction } = detail;
  try {
    await waitForGrecaptcha();
    const token = await window.grecaptcha.enterprise.execute(SITE_KEY, {
      action: pageAction,
    });
    window.dispatchEvent(new CustomEvent('CAPTCHA_RESULT', {
      detail: { requestId, token },
    }));
  } catch (e) {
    window.dispatchEvent(new CustomEvent('CAPTCHA_RESULT', {
      detail: { requestId, error: e.message },
    }));
  }
});

function waitForGrecaptcha(timeout = 10000) {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    const check = () => {
      if (window.grecaptcha?.enterprise?.execute) return resolve();
      if (Date.now() - start > timeout) return reject(new Error('grecaptcha not available'));
      setTimeout(check, 200);
    };
    check();
  });
}

window.addEventListener(
  'GET_MEDIA_URL',

  async ({ detail }) => {

    const {
      requestId,
      mediaId
    } = detail;

    try {

      const apiUrl =
        `https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name=${mediaId}`;

      const resp =
        await fetch(apiUrl);

      window.dispatchEvent(

        new CustomEvent(
          'GET_MEDIA_URL_RESULT',

          {

            detail: {

              requestId,

              status:
                resp.status,

              redirected:
                resp.redirected,

              url:
                resp.url

            }

          }

        )

      );

    }

    catch (e) {

      window.dispatchEvent(

        new CustomEvent(
          'GET_MEDIA_URL_RESULT',

          {

            detail: {

              requestId,

              error:
                e.message

            }

          }

        )

      );

    }

  }

);
