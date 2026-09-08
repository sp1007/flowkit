/**
 * Flow Kit — Chrome Extension Background Service Worker
 *
 * Connects to local Python agent via WebSocket (agent runs WS server).
 * Captures bearer token, solves reCAPTCHA, proxies API calls through browser.
 */

const AGENT_WS_URL = 'ws://127.0.0.1:9200';   // 9222 là bản chính — đừng nối nhầm
// NOTE: This is a browser-restricted public API key — safe to ship in extension bundles.
const API_KEY = 'AIzaSyBtrm0o5ab1c-Ec8ZuLcGt3oJAA5VWt3pY';

// ─── Domain của Flow ────────────────────────────────────────
// Flow đang DI TRÚ từ labs.google/fx/tools/flow sang flow.google.com: giao diện mới (app
// Angular "boq-labs-ai-sandbox") nhưng CÙNG backend aisandbox-pa.googleapis.com và CÙNG site
// key reCAPTCHA, nên token bắt được và captcha lấy từ tab nào cũng dùng được.
// Chấp nhận CẢ HAI: tab labs.google (cũ) lẫn flow.google.com (mới) đều là "tab Flow" hợp lệ.
// Vẫn MỞ labs.google khi phải tự mở tab, vì tRPC (project/media) và `/fx/api/auth/session`
// mới chỉ có ở đó — flow.google.com trả HTML cho mọi đường dẫn ấy. Ngày labs.google tắt hẳn,
// nó sẽ tự chuyển hướng sang flow.google.com và các mẫu dưới đây vẫn khớp.
const LABS_TAB_URLS = [
  'https://labs.google/fx/tools/flow*',
  'https://labs.google/fx/*/tools/flow*',
];
// Trang app THẬT của giao diện mới là /project/<uuid> — chỉ ở đó grecaptcha mới được nạp
// (đo được: trang gọi POST recaptcha/enterprise/reload với đúng site key của Flow). Trang chủ
// flow.google.com/ là trang giới thiệu, không có grecaptcha; để nó lọt vào danh sách "tab
// Flow" là mọi lượt sinh hỏng với "grecaptcha not available".
// Tab ƯU TIÊN để hỏi reCAPTCHA. KHÔNG phải điều kiện bắt buộc: đo trực tiếp
// (GET /api/flow/boq/tabs) thấy trang chủ đã đăng nhập `/u/2/` CŨNG có
// `grecaptcha.enterprise`, và sinh ảnh chỉ với tab trang chủ thì chạy bình thường. Lần hỏng
// trước đây là trang chưa boot xong chứ không phải vì không ở trang dự án.
const FLOW_APP_TAB_URLS = [
  'https://flow.google.com/project/*',
  'https://flow.google.com/u/*/project/*',   // Chrome nhiều tài khoản: /u/2/project/<id>
];
// Danh sách CHẤP NHẬN thì rộng hơn: mọi trang flow.google.com đều dùng được.
const FLOW_TAB_URLS = [...LABS_TAB_URLS, ...FLOW_APP_TAB_URLS, 'https://flow.google.com/*'];
// Chỉ dùng cho nút bấm TAY ở popup — không còn đường nào tự mở tab nữa.
const FLOW_TAB_OPEN_URL = 'https://flow.google.com/';
const LABS_ORIGIN = 'https://labs.google';

// Host phát media ĐÃ ĐỔI và sẽ còn đổi. Đo ngày 2026-09-08: ảnh mới sinh trả về
// `https://flow-content.google/image/<uuid>?Expires=…&KeyName=labs-flow-prod-cdn-key&Signature=…`
// (Cloud CDN ký), video trên giao diện mới lấy từ `googlevideo.com/videoplayback`
// (`source=contrib_service_ai_sandbox`, URL RÀNG THEO IP người xem + hết hạn ~2 tiếng).
// `storage.googleapis.com/ai-sandbox-videofx` là host CŨ, còn gặp ở media đời trước.
// Đừng hardcode một host: khớp cả ba, và thêm host mới vào ĐÂY chứ không rải regex.
const MEDIA_URL_RE = /https:\/\/(?:flow-content\.google|storage\.googleapis\.com\/ai-sandbox-videofx)\/(?:image|video)\/[0-9a-f-]{36}\?[^"'\s]+/g;

let ws = null;
let flowKey = null;
let identity = null;   // { email, name, picture, sub } — Google account signed into Flow
let callbackSecret = null;  // Auth secret for HTTP callback, received from server on WS connect
let state = 'off'; // off | idle | running
let manualDisconnect = false;

// ─── Flow Music (flowmusic.app) state ───────────────────────
// Kiến trúc auth khác hẳn Flow video: bearer là JWT của Supabase (không phải ya29 của
// Google), route qua chính www.flowmusic.app/__api/* (Next.js API, session cookie +
// Authorization kèm thêm) — không có reCAPTCHA nào chặn các API đã khảo sát.
let musicKey = null;        // Supabase JWT access_token bắt được qua webRequest
let musicKeyCapturedAt = null;
let musicIdentity = null;   // { email, name, picture, sub } decode thẳng từ payload JWT
let metrics = {
  tokenCapturedAt: null,
  requestCount: 0,   // captcha-consuming requests only (gen image/video/upscale)
  successCount: 0,
  failedCount: 0,
  lastError: null,
};

// ─── URL → Log Type Classifier ─────────────────────────────

// Visible log types — only these appear in the request log
const _VISIBLE_TYPES = new Set(['GEN_IMG', 'GEN_VID', 'GEN_VID_REF', 'UPSCALE', 'UPS_IMG', 'TRACKING', 'URL_REFRESH']);

function _classifyApiUrl(url) {
  if (url.includes('uploadImage')) return 'UPLOAD';
  if (url.includes('batchGenerateImages')) return 'GEN_IMG';
  if (url.includes('UpsampleVideo')) return 'UPSCALE';
  if (url.includes('ReferenceImages')) return 'GEN_VID_REF';
  if (url.includes('batchAsyncGenerateVideo')) return 'GEN_VID';
  if (url.includes('batchCheckAsync')) return 'POLL';
  if (url.includes('upsampleImage')) return 'UPS_IMG';
  if (url.includes('/media/')) return 'MEDIA';
  if (url.includes('/credits')) return 'CREDITS';
  return 'API';
}

// ─── Request Log ────────────────────────────────────────────

let requestLog = [];

function addRequestLog(entry) {
  requestLog.unshift(entry);
  if (requestLog.length > 100) requestLog.pop();
  broadcastRequestLog();
}

function updateRequestLog(id, updates) {
  const entry = requestLog.find((e) => e.id === id);
  if (entry) Object.assign(entry, updates);
  broadcastRequestLog();
}

function broadcastRequestLog() {
  chrome.runtime.sendMessage({ type: 'REQUEST_LOG_UPDATE', log: requestLog }).catch(() => { });
}

// ─── Startup ────────────────────────────────────────────────

chrome.runtime.onInstalled.addListener(init);
chrome.runtime.onStartup.addListener(init);
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === 'reconnect') connectToAgent();
  if (alarm.name === 'keepAlive') keepAlive();
  if (alarm.name === 'token-refresh') {
    await captureTokenFromFlowTab();
    await fetchIdentity();
  }
  if (alarm.name === 'identity-refresh') await fetchIdentity();
  if (alarm.name === 'music-token-refresh') await ensureFlowMusicTab();
});

async function init() {
  const data = await chrome.storage.local.get([
    'flowKey', 'metrics', 'callbackSecret', 'identity',
    'musicKey', 'musicKeyCapturedAt', 'musicIdentity',
  ]);
  if (data.flowKey) flowKey = data.flowKey;
  if (data.identity) identity = data.identity;
  if (data.metrics) Object.assign(metrics, data.metrics);
  if (data.callbackSecret) callbackSecret = data.callbackSecret;
  if (data.musicKey) musicKey = data.musicKey;
  if (data.musicKeyCapturedAt) musicKeyCapturedAt = data.musicKeyCapturedAt;
  if (data.musicIdentity) musicIdentity = data.musicIdentity;
  connectToAgent();
  chrome.alarms.create('keepAlive', { periodInMinutes: 0.4 });
}

// ─── Token Capture ──────────────────────────────────────────

chrome.webRequest.onBeforeSendHeaders.addListener(
  (details) => {
    if (!details?.requestHeaders?.length) return;
    const authHeader = details.requestHeaders.find(
      (h) => h.name?.toLowerCase() === 'authorization',
    );
    const value = authHeader?.value || '';
    if (!value.startsWith('Bearer ya29.')) return;

    const token = value.replace(/^Bearer\s+/i, '').trim();
    if (!token) return;

    // Token ĐỔI = phiên mới: hết hạn tự gia hạn, hoặc người dùng vừa đổi tài khoản Google.
    // Trường hợp sau phải bắt ngay, nếu không agent còn tưởng là account cũ tới tận lần
    // alarm sau và sẽ cho thao tác lên nhầm tài khoản.
    const tokenChanged = token !== flowKey;

    // Always update — even if same token string, refresh the timestamp
    flowKey = token;
    metrics.tokenCapturedAt = Date.now();
    chrome.storage.local.set({ flowKey, metrics });
    console.log('[FlowAgent] Bearer token captured');

    // Notify agent
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'token_captured', flowKey }));
    }
    // Listener này chạy rất dày → chỉ dò lại khi CHƯA biết tài khoản, hoặc khi token vừa đổi.
    if (!identity || tokenChanged) fetchIdentity();
  },
  { urls: ['https://aisandbox-pa.googleapis.com/*', 'https://labs.google/*', 'https://flow.google.com/*'] },
  ['requestHeaders', 'extraHeaders'],
);

// Flow Music: bearer là Supabase JWT (tiền tố "eyJ", không phải "ya29." của Google) —
// gửi kèm cả trên các call same-origin tới www.flowmusic.app lẫn tới sb.flowmusic.app.
chrome.webRequest.onBeforeSendHeaders.addListener(
  (details) => {
    if (!details?.requestHeaders?.length) return;
    const authHeader = details.requestHeaders.find(
      (h) => h.name?.toLowerCase() === 'authorization',
    );
    const value = authHeader?.value || '';
    if (!value.startsWith('Bearer eyJ')) return;

    const token = value.replace(/^Bearer\s+/i, '').trim();
    if (!token || token === musicKey) {
      if (token) musicKeyCapturedAt = Date.now();
      return;
    }

    musicKey = token;
    musicKeyCapturedAt = Date.now();
    chrome.storage.local.set({ musicKey, musicKeyCapturedAt });
    console.log('[FlowAgent] Flow Music bearer token captured');

    const payload = _decodeJwtPayload(token);
    if (payload?.email || payload?.sub) {
      musicIdentity = {
        email: payload.email ? String(payload.email).trim().toLowerCase() : null,
        name: payload.user_metadata?.full_name || payload.user_metadata?.name || null,
        picture: payload.user_metadata?.picture || payload.user_metadata?.avatar_url || null,
        sub: payload.sub || null,
      };
      chrome.storage.local.set({ musicIdentity });
    }

    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'music_token_captured', musicKey }));
      if (musicIdentity) ws.send(JSON.stringify({ type: 'music_identity', identity: musicIdentity }));
    }
  },
  { urls: ['https://www.flowmusic.app/*', 'https://sb.flowmusic.app/*'] },
  ['requestHeaders', 'extraHeaders'],
);

/** Decode a JWT payload without verifying signature — chỉ để đọc email/sub cho hiển thị,
 *  không dùng cho mục đích xác thực (server tự verify khi request thật). */
function _decodeJwtPayload(token) {
  try {
    const part = token.split('.')[1];
    const b64 = part.replace(/-/g, '+').replace(/_/g, '/');
    const json = decodeURIComponent(
      atob(b64).split('').map((c) => '%' + c.charCodeAt(0).toString(16).padStart(2, '0')).join(''),
    );
    return JSON.parse(json);
  } catch {
    return null;
  }
}

async function ensureFlowMusicTab() {
  const tabs = await chrome.tabs.query({ url: ['https://www.flowmusic.app/*'] });
  if (tabs.length) return tabs[0];
  console.log('[FlowAgent] No Flow Music tab found — opening one in background');
  return await chrome.tabs.create({ url: 'https://www.flowmusic.app/', active: false });
}

async function captureTokenFromFlowTab() {
  // KHÔNG tự mở tab. Bản dựng mới không sống bằng token ya29 nữa — batchexecute dùng cookie
  // phiên — nên việc tự mở tab labs.google chỉ đẻ ra một đống tab: labs không còn phục vụ
  // tài khoản này, vòng chờ 10s thất bại, lần alarm sau lại mở tiếp. Có tab thì làm mới
  // token, không có thì thôi.
  const tab = await pickFlowTab();
  if (!tab) {
    console.log('[FlowAgent] Chưa có tab Flow nào — bỏ qua lượt làm mới token');
    return;
  }
  try {
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ['content.js'] });
    console.log('[FlowAgent] Token refresh triggered on Flow tab');
  } catch (e) {
    console.error('[FlowAgent] Token refresh failed:', e);
  }
}

// ─── Account identity ───────────────────────────────────────
// Mọi thứ trên Flow — project, media, credit — thuộc về TÀI KHOẢN Google đang đăng nhập
// trong Chrome. Agent cần biết tài khoản đó để không trộn dự án của account này sang
// account khác (media_id của account A không resolve được bằng token của account B).
//
// Nguồn chính: `/fx/api/auth/session` — Flow là một app NextAuth, endpoint này trả
// `{ user: { email, name, image }, access_token }` theo cookie phiên. Gọi thẳng từ service
// worker (đã có host permission labs.google/*) nên không phải cào DOM.
// Dự phòng: tokeninfo của chính bearer ya29 → cho `sub` (id Google bền vững) và thường cả
// email; dùng khi endpoint session đổi shape.

async function _identityFromSession() {
  const res = await fetch(`${LABS_ORIGIN}/fx/api/auth/session`, {
    credentials: 'include',
    headers: { accept: 'application/json' },
  });
  if (!res.ok) return null;
  const u = (await res.json())?.user || {};
  if (!u.email) return null;
  return {
    email: String(u.email).trim().toLowerCase(),
    name: u.name || null,
    picture: u.image || null,
    sub: u.id || null,
    source: 'session',
  };
}

/** Cùng endpoint, nhưng fetch TỪ TRONG tab Flow: request cùng origin nên cookie phiên chắc
 *  chắn được gửi kèm — dùng khi fetch từ service worker về rỗng (cookie SameSite). */
async function _identityFromFlowTab() {
  // CHỈ tab labs.google: `/fx/api/auth/session` là route của app Next.js cũ, trên
  // flow.google.com nó trả về vỏ HTML nên JSON.parse hỏng và probe này vô nghĩa.
  const tab = await pickFlowTab(LABS_TAB_URLS);  // tab đã bị Chrome discard thì chạy script trong đó
  if (!tab) return null;                         // cũng hỏng — xem pickFlowTab()
  const [res] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: async () => {
      try {
        const r = await fetch('/fx/api/auth/session', { headers: { accept: 'application/json' } });
        return r.ok ? await r.json() : null;
      } catch { return null; }
    },
  });
  const u = res?.result?.user;
  if (!u?.email) return null;
  return {
    email: String(u.email).trim().toLowerCase(),
    name: u.name || null,
    picture: u.image || null,
    sub: u.id || null,
    source: 'flow-tab',
  };
}

async function _identityFromTokenInfo() {
  if (!flowKey) return null;
  const res = await fetch(
    'https://oauth2.googleapis.com/tokeninfo?access_token=' + encodeURIComponent(flowKey),
  );
  if (!res.ok) return null;
  const d = await res.json();
  if (!d?.email && !d?.sub) return null;
  return {
    email: d.email ? String(d.email).trim().toLowerCase() : null,
    name: null,
    picture: null,
    sub: d.sub || null,
    source: 'tokeninfo',
  };
}

/** Lấy tài khoản đang đăng nhập; báo agent khi đổi account. Trả về identity hoặc null. */
async function fetchIdentity({ notify = true } = {}) {
  let next = null;
  for (const probe of [_identityFromSession, _identityFromFlowTab, _identityFromTokenInfo]) {
    try {
      next = await probe();
      if (next) break;
    } catch (e) {
      console.warn('[FlowAgent] identity probe failed:', probe.name, e?.message || e);
    }
  }
  if (!next) {
    console.warn('[FlowAgent] Không xác định được tài khoản Flow (chưa đăng nhập?)');
    return identity;   // giữ giá trị cũ, đừng xoá — mạng lỗi không có nghĩa là đã đăng xuất
  }
  const changed = next.email !== identity?.email || next.sub !== identity?.sub;
  identity = { ...next, fetchedAt: Date.now() };
  chrome.storage.local.set({ identity });
  if (changed) console.log('[FlowAgent] Tài khoản Flow:', identity.email || identity.sub);
  if (notify) sendIdentityToAgent();
  return identity;
}

function sendIdentityToAgent() {
  if (identity && ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'identity', identity }));
  }
}

// ─── WebSocket to Agent ─────────────────────────────────────

function connectToAgent() {
  if (manualDisconnect) return;
  if (ws?.readyState === WebSocket.CONNECTING) return;
  if (ws?.readyState === WebSocket.OPEN) return;

  try {
    ws = new WebSocket(AGENT_WS_URL);
  } catch (e) {
    console.error('[FlowAgent] WS connect error:', e);
    scheduleReconnect();
    return;
  }

  ws.onopen = () => {
    console.log('[FlowAgent] Connected to agent');
    chrome.alarms.clear('reconnect');
    setState('idle');

    // Token refresh alarm — 45 min gives buffer before ~60 min expiry
    chrome.alarms.create('token-refresh', { periodInMinutes: 45 });
    // Lưới an toàn cho việc đổi tài khoản: bình thường token đổi là bắt được ngay, nhưng nếu
    // người dùng đổi account ở tab khác mà chưa gọi API nào thì 2 phút sau vẫn nhận ra.
    chrome.alarms.create('identity-refresh', { periodInMinutes: 2 });
    // Flow Music: JWT Supabase cũng sống ~60 phút — giữ 1 tab mở để trang tự refresh token
    // (webRequest bắt lại passively), không cần tự dựng lại refresh_token flow.
    chrome.alarms.create('music-token-refresh', { periodInMinutes: 45 });

    // Send current state + resend token if we have one
    ws.send(JSON.stringify({
      type: 'extension_ready',
      flowKeyPresent: !!flowKey,
      tokenAge: flowKey && metrics.tokenCapturedAt ? Date.now() - metrics.tokenCapturedAt : null,
    }));
    // Agent không giữ state qua lần khởi động — gửi lại tài khoản đã biết ngay, rồi dò lại
    // nền phòng khi người dùng đã đổi account trong lúc agent tắt.
    sendIdentityToAgent();
    fetchIdentity();
    if (flowKey) {
      ws.send(JSON.stringify({ type: 'token_captured', flowKey }));
    }
    if (musicKey) {
      ws.send(JSON.stringify({ type: 'music_token_captured', musicKey }));
    }
    if (musicIdentity) {
      ws.send(JSON.stringify({ type: 'music_identity', identity: musicIdentity }));
    }
  };

  ws.onmessage = async ({ data }) => {
    try {
      const msg = JSON.parse(data);

      if (msg.method === 'api_request') {
        await handleApiRequest(msg);
      } else if (msg.method === 'trpc_request') {
        await handleTrpcRequest(msg);
      } else if (msg.method === 'music_api_request') {
        await handleMusicApiRequest(msg);
      } else if (msg.method === 'music_stream_request') {
        await handleMusicStreamRequest(msg);
      } else if (msg.method === 'boq_request') {
        await handleBoqRequest(msg);
      } else if (msg.method === 'probe_tabs') {
        await handleProbeTabs(msg);
      } else if (msg.method === 'boq_log') {
        sendToAgent({ id: msg.id, result: boqLog.slice(0, msg.params?.limit || 100) });
      } else if (msg.method === 'solve_captcha') {
        await handleSolveCaptcha(msg);
      } else if (msg.method === 'get_status') {
        sendToAgent({
          id: msg.id,
          result: {
            state,
            flowKeyPresent: !!flowKey,
            manualDisconnect,
            tokenAge: metrics.tokenCapturedAt ? Date.now() - metrics.tokenCapturedAt : null,
            metrics,
          },
        });
      } else if (msg.method === 'get_identity') {
        const id = msg.params?.refresh === false ? identity : await fetchIdentity({ notify: false });
        sendToAgent({ id: msg.id, result: id || null });
      } else if (msg.type === 'callback_secret') {
        callbackSecret = msg.secret;
        chrome.storage.local.set({ callbackSecret: msg.secret });
        console.log('[FlowAgent] Received callback secret');
      } else if (msg.type === 'pong') {
        // keepalive response
      }
    } catch (e) {
      console.error('[FlowAgent] Message error:', e);
    }
  };

  ws.onclose = () => {
    setState('off');
    chrome.alarms.clear('token-refresh');
    chrome.alarms.clear('identity-refresh');
    if (!manualDisconnect) scheduleReconnect();
  };

  ws.onerror = (e) => {
    console.error('[FlowAgent] WS error:', e);
    metrics.lastError = 'WS_ERROR';
    chrome.storage.local.set({ metrics });
  };
}

function scheduleReconnect() {
  chrome.alarms.create('reconnect', { delayInMinutes: 0.083 }); // ~5s
}

function keepAlive() {
  if (ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'ping' }));
  } else {
    connectToAgent();
  }
}

function sendToAgent(msg) {
  // API responses (with msg.id) go via HTTP — immune to WS disconnect
  if (msg.id) {
    fetch('http://127.0.0.1:8200/api/ext/callback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(msg),
    }).catch(() => {
      // HTTP failed — fallback to WS
      if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
    });
    return;
  }
  // Non-response messages (ping, status) or no secret yet — use WS
  if (ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(msg));
  }
}

// ─── reCAPTCHA Solving ──────────────────────────────────────

async function requestCaptchaFromTab(tabId, requestId, pageAction) {
  try {
    return await chrome.tabs.sendMessage(tabId, {
      type: 'GET_CAPTCHA',
      requestId,
      pageAction,
    });
  } catch (error) {
    const msg = error?.message || '';
    const shouldInject =
      msg.includes('Receiving end does not exist') ||
      msg.includes('Could not establish connection');
    if (!shouldInject) throw error;

    // Inject content script and retry
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ['content.js'],
    });
    await sleep(200);
    return await chrome.tabs.sendMessage(tabId, {
      type: 'GET_CAPTCHA',
      requestId,
      pageAction,
    });
  }
}

/**
 * Chọn tab Flow ĐANG SỐNG để hỏi reCAPTCHA, và đánh thức nó nếu cần.
 *
 * Trước đây lấy thẳng `tabs[0]`. Chrome tự DISCARD tab nền để tiết kiệm bộ nhớ: tab vẫn nằm
 * trên thanh tab, vẫn khớp truy vấn url, nhưng content script đã bị gỡ khỏi bộ nhớ nên
 * `sendMessage` không ai trả lời → hết 30s → CAPTCHA_TIMEOUT. Nhìn từ phía người dùng thì
 * "tab Flow vẫn mở" mà mọi lượt sinh đều hỏng, và hỏng thành cụm liền nhau đúng lúc máy rảnh.
 * Cũng vì vậy mà mở nhiều tab Flow lại hại: tabs[0] có thể là cái đã bị discard.
 *
 * Thứ tự ưu tiên: tab đang hiện (active) → tab còn sống → tab đã discard nhưng reload lại được.
 */
async function pickFlowTab(urls = FLOW_TAB_URLS) {
  const tabs = await chrome.tabs.query({ url: urls });
  if (!tabs.length) return null;
  const alive = tabs.filter((t) => !t.discarded);
  const best =
    alive.find((t) => t.active) || alive[0] || tabs[0];
  if (best.discarded) {
    // Đánh thức rồi chờ load xong — reload trả về ngay, content script chưa kịp gắn lại.
    await chrome.tabs.reload(best.id);
    for (let i = 0; i < 25; i++) {
      await sleep(400);
      const t = await chrome.tabs.get(best.id).catch(() => null);
      if (t && !t.discarded && t.status === 'complete') break;
    }
  }
  return best;
}

function _tabOrigin(tab) {
  try { return new URL(tab.url || '').hostname; } catch { return '?'; }
}

async function _captchaFromTab(tab, requestId, captchaAction) {
  return await Promise.race([
    requestCaptchaFromTab(tab.id, requestId, captchaAction),
    new Promise((_, rej) => setTimeout(() => rej(new Error('CAPTCHA_TIMEOUT')), 30000)),
  ]);
}

/**
 * Tab để hỏi reCAPTCHA — ƯU TIÊN labs.google, flow.google.com chỉ là đường lùi.
 *
 * Giao diện mới nạp grecaptcha theo chunk LƯỜI: cả shell HTML lẫn bundle gốc của
 * flow.google.com đều KHÔNG có `recaptcha/enterprise.js`, site key chỉ nằm trong WIZ config.
 * Nên tab trang chủ (hoặc app chưa boot xong) không có `window.grecaptcha` → injected.js chờ
 * 10s rồi trả "grecaptcha not available" → CAPTCHA_FAILED. labs.google nhúng sẵn nên luôn hỏi
 * được. Bỏ ưu tiên này là mọi lượt sinh hỏng ngay khi người dùng đang mở tab flow.google.com
 * (pickFlowTab chuộng tab ACTIVE).
 */
async function solveCaptcha(requestId, captchaAction) {
  // Thứ tự: tab labs đang mở → TỰ MỞ một tab labs → mới tới tab flow.google.com.
  // Bước giữa là bước hay bị bỏ sót: trước đây chỉ tự mở tab khi KHÔNG có tab Flow nào, nên
  // một tab flow.google.com đang mở là đủ để chặn việc mở labs, rồi hỏng vì trang mới không
  // có grecaptcha.
  // Chỉ dùng tab ĐANG MỞ. Tự mở tab là cách chắc chắn đẻ ra hàng chục tab khi domain
  // được mở không phải domain phục vụ tài khoản này.
  const tab = (await pickFlowTab(LABS_TAB_URLS)) || (await pickFlowTab(FLOW_TAB_URLS));
  if (!tab) return { error: 'NO_FLOW_TAB — hãy mở một tab Flow rồi thử lại' };

  try {
    const resp = await _captchaFromTab(tab, requestId, captchaAction);
    if (resp?.token) return resp;
    // Tab đầu không cho token — thử nốt domain còn lại trước khi bỏ cuộc. Lỗi kèm HOSTNAME
    // để lần sau không phải đoán tab nào đã hỏng.
    const other = await _otherDomainTab(tab);
    if (other) {
      const retry = await _captchaFromTab(other, requestId, captchaAction);
      if (retry?.token) return retry;
      return { error: `${retry?.error || 'NO_TOKEN'} @${_tabOrigin(other)}` };
    }
    return { error: `${resp?.error || 'NO_TOKEN'} @${_tabOrigin(tab)}` };
  } catch (e) {
    return { error: `${e.message} @${_tabOrigin(tab)}` };
  }
}

/** Tab Flow của domain KHÁC với tab đã thử — labs.google ↔ flow.google.com. */
async function _otherDomainTab(tried) {
  const host = _tabOrigin(tried);
  const urls = host === 'labs.google' ? FLOW_APP_TAB_URLS : LABS_TAB_URLS;
  const tab = await pickFlowTab(urls);
  return tab && tab.id !== tried.id ? tab : null;
}

async function handleSolveCaptcha(msg) {
  const { id, params } = msg;
  const result = await solveCaptcha(id, params?.captchaAction || 'VIDEO_GENERATION');

  // Standalone captcha solve counts as captcha-consuming
  metrics.requestCount++;
  if (result?.token) {
    metrics.successCount++;
  } else {
    metrics.failedCount++;
    metrics.lastError = result?.error || 'NO_TOKEN';
  }
  chrome.storage.local.set({ metrics });

  sendToAgent({ id, result });
}

// ─── API Request Proxy ──────────────────────────────────────

/** Đổi media_id thành URL GCS đã ký. Đi qua tab labs.google khi có (request cùng origin,
 *  cookie phiên chắc chắn kèm theo); không có tab nào thì service worker tự fetch — nó có
 *  host permission labs.google/* nên cũng gửi được cookie. Tab flow.google.com KHÔNG dùng
 *  được cho việc này: fetch sang labs.google từ đó là cross-origin, CORS chặn. */
async function _mediaUrlViaFetch(mediaId) {
  const resp = await fetch(
    `${LABS_ORIGIN}/fx/api/trpc/media.getMediaUrlRedirect?name=${encodeURIComponent(mediaId)}`,
    { credentials: 'include' },
  );
  return { status: resp.status, redirected: resp.redirected, url: resp.url };
}

async function getMediaUrl(mediaId) {
  const tab = await pickFlowTab(LABS_TAB_URLS);
  if (!tab) return await _mediaUrlViaFetch(mediaId);
  try {
    return await chrome.tabs.sendMessage(tab.id, {
      type: 'GET_MEDIA_URL',
      requestId: crypto.randomUUID(),
      mediaId,
    });
  } catch (e) {
    console.warn('[FlowAgent] GET_MEDIA_URL qua tab hỏng, fetch thẳng:', e?.message || e);
    return await _mediaUrlViaFetch(mediaId);
  }
}

async function handleTrpcRequest(msg) {
  const { id, params } = msg;
  const { url, method = 'POST', headers = {}, body } = params;

  if (!url || !(url.startsWith('https://labs.google/') || url.startsWith('https://flow.google.com/'))) {
    sendToAgent({ id, error: 'INVALID_TRPC_URL' });
    return;
  }

  setState('running');
  // TRPC calls don't consume captcha — don't count in metrics

  const logId = id;
  const logType = url.includes('createProject') ? 'CREATE_PROJECT' : 'TRPC';
  const imgType = url.includes('media.getMediaUrlRedirect') ? 'IMAGE' : 'TRPC';
  // TRPC calls are silent — don't show in request log

  if (imgType == 'IMAGE') {

    try {

      const mediaId =

        new URL(url)

          .searchParams

          .get('name');

      const media =

        await getMediaUrl(

          mediaId

        );

      sendToAgent({

        id,

        status:
          media.status,

        data: {

          url:
            media.url,

          redirected:

            media.redirected

        }

      });

    }

    catch (e) {

      sendToAgent({

        id,

        error:

          e.message

      });

    }

    return;

  }

  const fetchHeaders = { 'Content-Type': 'application/json', ...headers };
  if (flowKey) {
    fetchHeaders['authorization'] = `Bearer ${flowKey}`;
  }

  try {
    const resp = await fetch(url, {
      method,
      headers: fetchHeaders,
      body: body ? JSON.stringify(body) : undefined,
      credentials: 'include',
    });
    const data = await resp.json();
    chrome.storage.local.set({ metrics });
    updateRequestLog(logId, { status: 'success' });
    sendToAgent({ id, status: resp.status, data });
  } catch (e) {
    console.error('[FlowAgent] tRPC request failed:', e);
    chrome.storage.local.set({ metrics });
    updateRequestLog(logId, { status: 'failed', error: e.message || 'TRPC_FETCH_FAILED' });
    sendToAgent({ id, error: e.message || 'TRPC_FETCH_FAILED' });
  } finally {
    setState('idle');
  }
}

// ─── Flow Music: API relay + chat/SSE streaming ─────────────
// Khác Flow video: không có reCAPTCHA trên các endpoint đã khảo sát, auth chỉ cần
// Authorization Bearer (Supabase JWT, bắt qua webRequest ở trên) + cookie session (Chrome
// tự đính kèm nhờ credentials:'include' + host permission, không cần tự dựng cookie).

const MUSIC_WEB_ORIGIN = 'https://www.flowmusic.app';

async function handleMusicApiRequest(msg) {
  const { id, params } = msg;
  const { url, method = 'GET', headers = {}, body } = params || {};

  if (!url || !(url.startsWith('https://www.flowmusic.app/') || url.startsWith('https://sb.flowmusic.app/'))) {
    sendToAgent({ id, error: 'INVALID_MUSIC_URL' });
    return;
  }
  if (!musicKey) {
    sendToAgent({ id, status: 503, error: 'NO_MUSIC_KEY' });
    return;
  }

  const fetchHeaders = { 'content-type': 'application/json', ...headers, authorization: `Bearer ${musicKey}` };

  try {
    const resp = await fetch(url, {
      method,
      headers: fetchHeaders,
      credentials: 'include',
      body: (method === 'GET' || method === 'HEAD' || body === undefined) ? undefined : JSON.stringify(body),
    });
    const text = await resp.text();
    let data;
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    sendToAgent({ id, status: resp.status, data });
  } catch (e) {
    sendToAgent({ id, status: 500, error: e.message || 'MUSIC_API_REQUEST_FAILED' });
  }
}

/** Gửi 1 tin nhắn vào conversation (mới hoặc có sẵn), rồi đọc trọn vẹn SSE phản hồi tới khi
 *  agent phía Google xong lượt (event "final"), gộp lại thành 1 kết quả trả về 1 lần — khớp
 *  với mô hình request/response sẵn có (agent Python chờ 1 future), không cần thêm cơ chế
 *  push tăng dần qua WS cho ca dùng hiện tại (tạo nhạc, ~30-70s/lượt). */
async function handleMusicStreamRequest(msg) {
  const { id, params } = msg;
  const {
    content, conversation_id = null,
    client_context = {}, model_name = 'producer:standard', mode = 'standard',
    timeout_s = 180,
  } = params || {};

  if (!content) {
    sendToAgent({ id, error: 'MISSING_CONTENT' });
    return;
  }
  if (!musicKey) {
    sendToAgent({ id, status: 503, error: 'NO_MUSIC_KEY' });
    return;
  }

  const fetchHeaders = { 'content-type': 'application/json', authorization: `Bearer ${musicKey}` };

  try {
    const postBody = {
      conversation_id: conversation_id || null,
      parts: [{ content, part_kind: 'user-prompt' }],
      client_context: {
        current_song_id: null, song_queue: [], selected_model: null,
        lyrics_id_map: {}, ghostwriter_version: 'standard',
        ...client_context,
      },
      model_name,
      mode,
    };

    const submitResp = await fetch(`${MUSIC_WEB_ORIGIN}/__api/conversation`, {
      method: 'POST',
      headers: fetchHeaders,
      credentials: 'include',
      body: JSON.stringify(postBody),
    });
    const submitText = await submitResp.text();
    let submitData;
    try { submitData = submitText ? JSON.parse(submitText) : null; } catch { submitData = submitText; }

    if (!submitResp.ok || !submitData?.job_id) {
      sendToAgent({ id, status: submitResp.status || 502, error: 'MUSIC_SUBMIT_FAILED', data: submitData });
      return;
    }

    const jobId = submitData.job_id;
    const result = await _consumeMusicStream(jobId, fetchHeaders, timeout_s);
    sendToAgent({ id, status: 200, data: { job_id: jobId, ...result } });
  } catch (e) {
    sendToAgent({ id, status: 500, error: e.message || 'MUSIC_STREAM_FAILED' });
  }
}

/** Đọc SSE của /__api/messages/{jobId}/stream tới khi kết nối đóng (server tự đóng ngay
 *  sau event "final") hoặc hết timeout. Dùng resp.text() (đợi trọn response) thay vì
 *  ReadableStream.getReader() thủ công — đơn giản hơn, tránh incompat khi đọc stream trong
 *  service worker của extension. */
async function _consumeMusicStream(jobId, fetchHeaders, timeoutS) {
  const url = `${MUSIC_WEB_ORIGIN}/__api/messages/${jobId}/stream?last_id=0`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutS * 1000);
  let text = '';
  let timedOut = false;
  try {
    const resp = await fetch(url, {
      method: 'GET',
      headers: { ...fetchHeaders, accept: 'text/event-stream' },
      credentials: 'include',
      signal: controller.signal,
    });
    if (!resp.ok) throw new Error(`MUSIC_STREAM_HTTP_${resp.status}`);
    text = await resp.text();
  } catch (e) {
    if (controller.signal.aborted) timedOut = true;
    else throw e;
  } finally {
    clearTimeout(timer);
  }

  // Chuẩn hoá CRLF→LF trước khi tách frame theo dòng trống — server có thể dùng "\r\n\r\n".
  const frames = text.replace(/\r\n/g, '\n').split('\n\n');
  let conversationId = null;
  const partsByIndex = new Map(); // index -> part mới nhất (ưu tiên bản "final")
  let finished = false;

  for (const frame of frames) {
    const parsed = _parseSseFrame(frame);
    if (!parsed) continue; // dòng comment/ping thuần (": ping ...") hoặc frame rỗng

    if (parsed.event === 'conversation_id') {
      try { conversationId = JSON.parse(parsed.data)?.id || conversationId; } catch { /* ignore */ }
    } else if (parsed.event === 'part') {
      try {
        const evt = JSON.parse(parsed.data);
        if (evt && typeof evt.index === 'number') partsByIndex.set(evt.index, evt.part);
      } catch { /* ignore */ }
    } else if (parsed.event === 'final') {
      finished = true;
    }
    // "begin"/"complete"/"suggestion" — bỏ qua, đủ dữ liệu từ "part" + "final"
  }

  const parts = [...partsByIndex.entries()].sort((a, b) => a[0] - b[0]).map(([, p]) => p);
  const toolReturns = parts.filter((p) => p.part_kind === 'tool-return');
  const texts = parts.filter((p) => p.part_kind === 'text').map((p) => p.content);

  return {
    conversation_id: conversationId,
    parts,
    tool_returns: toolReturns,
    text: texts.join('\n\n'),
    done: finished,
    timed_out: timedOut && !finished,
  };
}

function _parseSseFrame(frame) {
  const lines = frame.split('\n');
  let event = null;
  const dataLines = [];
  for (const line of lines) {
    if (!line || line.startsWith(':')) continue; // comment/ping
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
  }
  if (!event && !dataLines.length) return null;
  return { event: event || 'message', data: dataLines.join('\n') };
}

async function handleApiRequest(msg) {
  const { id, params } = msg;
  const { url, method, headers, body, captchaAction } = params;

  if (!url) {
    sendToAgent({ id, error: 'MISSING_URL' });
    return;
  }

  if (!url.startsWith('https://aisandbox-pa.googleapis.com/')) {
    sendToAgent({ id, error: 'INVALID_URL' });
    return;
  }

  setState('running');
  const hasCaptcha = !!captchaAction;
  if (hasCaptcha) metrics.requestCount++;

  const logId = id;
  const logType = _classifyApiUrl(url);
  if (_VISIBLE_TYPES.has(logType)) {
    const payloadSummary = body ? JSON.stringify(body).slice(0, 200) : null;
    addRequestLog({ id: logId, type: logType, time: new Date().toISOString(), status: 'processing', error: null, outputUrl: null, url, payloadSummary });
  }

  try {
    // Step 1: Solve captcha if needed
    let captchaToken = null;
    if (captchaAction) {
      const captchaResult = await solveCaptcha(id, captchaAction);
      captchaToken = captchaResult?.token || null;
      if (!captchaToken) {
        // Cannot proceed without captcha — API will 403
        const err = captchaResult?.error || 'CAPTCHA_FAILED';
        console.error(`[FlowAgent] Captcha failed for ${captchaAction}: ${err}`);
        sendToAgent({ id, status: 403, error: `CAPTCHA_FAILED: ${err}` });
        if (hasCaptcha) { metrics.failedCount++; metrics.lastError = `CAPTCHA_FAILED: ${err}`; }
        chrome.storage.local.set({ metrics });
        updateRequestLog(logId, { status: 'failed', error: `CAPTCHA_FAILED: ${err}` });
        setState('idle');
        return;
      }
    }

    // Step 2: Inject captcha token into body
    let finalBody = body;
    if (captchaToken && finalBody) {
      finalBody = JSON.parse(JSON.stringify(finalBody)); // deep clone
      if (finalBody.clientContext?.recaptchaContext) {
        finalBody.clientContext.recaptchaContext.token = captchaToken;
      }
      if (finalBody.requests && Array.isArray(finalBody.requests)) {
        for (const req of finalBody.requests) {
          if (req.clientContext?.recaptchaContext) {
            req.clientContext.recaptchaContext.token = captchaToken;
          }
        }
      }
    }

    // Step 3: Use flowKey for auth
    const activeFlowKey = flowKey;
    if (!activeFlowKey) {
      sendToAgent({ id, status: 503, error: 'NO_FLOW_KEY' });
      if (hasCaptcha) { metrics.failedCount++; metrics.lastError = 'NO_FLOW_KEY'; }
      chrome.storage.local.set({ metrics });
      updateRequestLog(logId, { status: 'failed', error: 'NO_FLOW_KEY' });
      setState('idle');
      return;
    }

    const fetchHeaders = { ...(headers || {}) };
    fetchHeaders['authorization'] = `Bearer ${activeFlowKey}`;

    // Step 4: Make the API call from browser context
    const response = await fetch(url, {
      method: method || 'POST',
      headers: fetchHeaders,
      credentials: 'include',
      body: method === 'GET' ? undefined : JSON.stringify(finalBody),
    });

    let responseData;
    const responseText = await response.text();
    try {
      responseData = JSON.parse(responseText);
    } catch {
      responseData = responseText;
    }

    sendToAgent({
      id,
      status: response.status,
      data: responseData,
    });

    const responseSummary = responseText ? responseText.slice(0, 300) : null;
    if (response.ok) {
      if (hasCaptcha) { metrics.successCount++; metrics.lastError = null; }
      updateRequestLog(logId, { status: 'success', httpStatus: response.status, responseSummary });
    } else {
      if (hasCaptcha) { metrics.failedCount++; metrics.lastError = `API_${response.status}`; }
      updateRequestLog(logId, { status: 'failed', error: `API_${response.status}`, httpStatus: response.status, responseSummary });
    }
  } catch (e) {
    sendToAgent({
      id,
      status: 500,
      error: e.message || 'API_REQUEST_FAILED',
    });
    if (hasCaptcha) { metrics.failedCount++; metrics.lastError = e.message; }
    updateRequestLog(logId, { status: 'failed', error: e.message || 'API_REQUEST_FAILED' });
  }

  chrome.storage.local.set({ metrics });
  setState('idle');
}

// ─── State & Popup ──────────────────────────────────────────

function setState(newState) {
  state = newState;
  const badges = { idle: '●', running: '▶', off: '○' };
  const colors = { idle: '#22c55e', running: '#f59e0b', off: '#6b7280' };
  chrome.action.setBadgeText({ text: badges[state] || '' });
  chrome.action.setBadgeBackgroundColor({ color: colors[state] || '#000' });
  broadcastStatus();
}

function broadcastStatus() {
  chrome.runtime.sendMessage({ type: 'STATUS_PUSH' }).catch(() => { });
}

chrome.runtime.onMessage.addListener((msg, _, reply) => {
  if (msg.type === 'STATUS') {
    reply({
      connected: ws?.readyState === WebSocket.OPEN,
      agentConnected: ws?.readyState === WebSocket.OPEN,
      flowKeyPresent: !!flowKey,
      manualDisconnect,
      account: identity?.email || identity?.sub || null,
      tokenAge: metrics.tokenCapturedAt ? Date.now() - metrics.tokenCapturedAt : null,
      musicKeyPresent: !!musicKey,
      musicAccount: musicIdentity?.email || musicIdentity?.sub || null,
      musicTokenAge: musicKeyCapturedAt ? Date.now() - musicKeyCapturedAt : null,
      metrics: {
        requestCount: metrics.requestCount,
        successCount: metrics.successCount,
        failedCount: metrics.failedCount,
        lastError: metrics.lastError,
      },
      state,
    });
  }

  if (msg.type === 'MEDIA_REDIRECT') {

    console.log('MEDIA REDIRECT');

    console.log(msg.data);

    sendToAgent({

      type: 'MEDIA_REDIRECT',

      data: msg.data

    });

    reply({ ok: true });

    return true;

  }

  if (msg.type === 'DISCONNECT') {
    manualDisconnect = true;
    if (ws) ws.close();
    reply({ ok: true });
    return true;
  }

  if (msg.type === 'RECONNECT') {
    manualDisconnect = false;
    connectToAgent();
    reply({ ok: true });
    return true;
  }

  if (msg.type === 'REQUEST_LOG') {
    reply({ log: requestLog });
    return true;
  }

  if (msg.type === 'OPEN_FLOW_TAB') {
    chrome.tabs.query({
      url: FLOW_TAB_URLS,
    }).then((tabs) => {
      if (tabs.length) {
        chrome.tabs.update(tabs[0].id, { active: true });
        reply({ ok: true, tabId: tabs[0].id });
      } else {
        chrome.tabs.create({ url: FLOW_TAB_OPEN_URL })
          .then((tab) => reply({ ok: true, tabId: tab.id }))
          .catch((e) => reply({ error: e.message }));
      }
    }).catch((e) => reply({ error: e.message }));
    return true;
  }

  if (msg.type === 'REFRESH_TOKEN') {
    captureTokenFromFlowTab()
      .then(() => reply({ ok: true }))
      .catch((e) => reply({ error: e.message }));
    return true;
  }

  if (msg.type === 'TEST_CAPTCHA') {
    solveCaptcha(`test-${Date.now()}`, msg.pageAction || 'IMAGE_GENERATION')
      .then((r) => reply(r))
      .catch((e) => reply({ error: e.message }));
    return true;
  }

  if (msg.type === 'TRPC_MEDIA_URLS') {
    handleTrpcMediaUrls(msg.trpcUrl, msg.body);
    reply({ ok: true });
    return true;
  }

  return true;
});

// ─── BOQ batchexecute — tầng vận chuyển của giao diện mới ───
//
// flow.google.com KHÔNG gọi aisandbox-pa từ trình duyệt. Mọi thao tác đi qua một endpoint
// cùng origin:
//   POST /_/AiSandboxAngularFrontend/data/batchexecute?rpcids=<id>&source-path=/project/<pid>
//        &bl=<backend release>&f.sid=<session>&hl=<locale>&_reqid=<số>&rt=c
//   body: f.req=[[["<rpcid>","<json args>",null,"generic"]]]&at=<xsrf>
// Auth = COOKIE phiên + `at`, KHÔNG có Authorization: Bearer. Đây là lý do phải chuẩn bị:
// ngày labs.google tắt thì không còn ai phát token ya29 nữa, mà đường này thì không cần token.
//
// Ba tham số bắt buộc (`at`, `f.sid`, `bl`) nằm trong `WIZ_global_data` của trang, đọc bằng
// executeScript ở MAIN world. POST thì để service worker làm: có host permission nên cookie
// vẫn được gửi và không vướng CORS.
const BOQ_ORIGIN = 'https://flow.google.com';

/** Khoá WIZ_global_data — tên do BOQ đặt, giống nhau ở mọi app Google. */
// Tab nào của flow.google.com cũng có WIZ_global_data — kể cả trang chủ. Chỉ reCAPTCHA mới
// đòi trang /project/*, đừng bắt batchexecute chịu chung ràng buộc đó.
const BOQ_TAB_URLS = ['https://flow.google.com/*'];

async function _boqParams() {
  const tab = (await pickFlowTab(FLOW_APP_TAB_URLS)) || (await pickFlowTab(BOQ_TAB_URLS));
  if (!tab) return null;
  const [r] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: 'MAIN',
    func: () => {
      const w = window.WIZ_global_data || {};
      return {
        at: w.SNlM0e || null,        // token XSRF
        sid: w.FdrFJe || null,       // f.sid
        bl: w.cfb2h || null,         // nhánh backend, vd boq_labs-ai-sandbox-frontend_…
        app: w.qwAQke || null,       // AiSandboxAngularFrontend
        hl: w.PXcMTe || document.documentElement.lang || 'en-US',
        // Chrome nhiều tài khoản: mọi URL mang tiền tố /u/<N>/. Bỏ nó đi là gọi sang
        // NGỮ CẢNH TÀI KHOẢN KHÁC — không thấy dự án, hoặc 401.
        userPrefix: (location.pathname.match(/^\/u\/\d+/) || [''])[0],
        path: location.pathname,
      };
    },
  });
  const p = r?.result;
  return p?.at && p?.app ? p : null;
}

/**
 * Tách phản hồi batchexecute.
 *
 * Định dạng trên giấy là "một dòng ĐỘ DÀI rồi tới chừng ấy ký tự JSON", nhưng số đó KHÔNG
 * đáng tin: đo trên phản hồi thật của `nzlxg`, khối ghi 127 trong khi dòng JSON dài 125
 * (126 kể cả xuống dòng). Bám theo nó thì cắt lẹm sang khối sau và parse hỏng im lặng.
 * Nên bỏ qua số đếm, quét thẳng từng mảng JSON cân ngoặc — có để ý chuỗi và ký tự thoát
 * nên dấu ngoặc nằm trong chuỗi không làm lệch.
 */
function _scanJsonArrays(s) {
  const out = [];
  let i = 0;
  while (i < s.length) {
    const start = s.indexOf('[', i);
    if (start < 0) break;
    let depth = 0, inStr = false, esc = false, end = -1;
    for (let j = start; j < s.length; j++) {
      const ch = s[j];
      if (inStr) {
        if (esc) esc = false;
        else if (ch.charCodeAt(0) === 92) esc = true;   // 92 = dấu chéo ngược
        else if (ch === '"') inStr = false;
        continue;
      }
      if (ch === '"') inStr = true;
      else if (ch === '[') depth++;
      else if (ch === ']' && --depth === 0) { end = j; break; }
    }
    if (end < 0) break;
    try { out.push(JSON.parse(s.slice(start, end + 1))); } catch { /* khối vỡ — bỏ */ }
    i = end + 1;
  }
  return out;
}

function parseBoqResponse(text) {
  const out = [];
  for (const envelopes of _scanJsonArrays(text.replace(/^\)\]\}'/, ''))) {
    if (!Array.isArray(envelopes)) continue;
    for (const env of envelopes) {
      if (!Array.isArray(env)) continue;
      // "wrb.fr" = kết quả của một rpcid; "er" = lỗi của chính rpcid đó, phải giữ lại
      // chứ không phải bỏ qua — đó là chỗ đọc ra vì sao lời gọi hỏng.
      if (env[0] === 'wrb.fr') {
        let payload = env[2];
        if (typeof payload === 'string') {
          try { payload = JSON.parse(payload); } catch { /* để nguyên chuỗi */ }
        }
        // Lỗi CÓ THỂ nằm ngay trong envelope wrb.fr: payload null còn env[5] mang
        // google.rpc.ErrorInfo. Không moi ra thì mọi thất bại trông giống hệt nhau —
        // "data: null" — và ta đi dò sai chỗ. Đo thật: xin bản 2K trả
        // PUBLIC_ERROR_UNUSUAL_ACTIVITY_TOO_MUCH_TRAFFIC mà parser cũ nuốt mất.
        const item = { rpcid: env[1], data: payload };
        if (payload === null && env[5] != null) item.error = env[5];
        out.push(item);
      } else if (env[0] === 'er') {
        out.push({ rpcid: env[1] ?? null, error: env.slice(2) });
      }
    }
  }
  return out;
}

async function boqExecute(rpcid, args, { sourcePath } = {}) {
  const p = await _boqParams();
  if (!p) return { error: 'NO_FLOW_APP_TAB' };   // cần một tab flow.google.com/project/*

  const qs = new URLSearchParams({
    rpcids: rpcid,
    'source-path': sourcePath || p.path || '/',
    bl: p.bl || '',
    'f.sid': p.sid || '',
    hl: p.hl,
    _reqid: String(Math.floor(Math.random() * 900000) + 100000),
    rt: 'c',
  });
  const body = new URLSearchParams({
    'f.req': JSON.stringify([[[rpcid, JSON.stringify(args), null, 'generic']]]),
    at: p.at,
  });

  const resp = await fetch(`${BOQ_ORIGIN}${p.userPrefix || ''}/_/${p.app}/data/batchexecute?${qs}`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'content-type': 'application/x-www-form-urlencoded;charset=UTF-8' },
    body: body.toString(),
  });
  const text = await resp.text();
  return { status: resp.status, results: parseBoqResponse(text), raw: text.slice(0, 40000) };
}

/** Thay mọi chuỗi `__CAPTCHA__` trong args bằng token vừa lấy. Token reCAPTCHA dùng MỘT
 *  lần và sống ~2 phút, nên không thể chép lại token bắt được — phải lấy mới mỗi lượt. Để
 *  agent nhét chỗ trống rồi extension điền, agent khỏi phải biết gì về reCAPTCHA. */
const CAPTCHA_SLOT = '__CAPTCHA__';

// Site key của Flow — GIỐNG nhau ở labs.google và flow.google.com (đo trực tiếp trong
// WIZ_global_data của trang mới).
const RECAPTCHA_SITE_KEY = '6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV';

/**
 * Lấy token reCAPTCHA bằng cách chạy THẲNG trong MAIN world của tab, không qua
 * content.js → injected.js → CustomEvent → sendMessage.
 *
 * Cầu nối cũ có bốn mắt xích và mắt nào đứt cũng ra một lỗi khó đọc: lượt đầu chạy trên
 * flow.google.com trả "message channel closed before a response was received", tức
 * content script nhận tin rồi biến mất trước khi trả lời — không nói được là thiếu
 * grecaptcha hay injected.js chưa nạp. executeScript trả thẳng kết quả (Chrome tự chờ
 * Promise mà `func` trả về) nên lỗi nói đúng nguyên nhân.
 */
async function captchaFromMainWorld(tab, action) {
  try {
    const [r] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: 'MAIN',
      args: [RECAPTCHA_SITE_KEY, action],
      func: async (key, act) => {
        const g = window.grecaptcha;
        if (!g?.enterprise?.execute) return { error: 'NO_GRECAPTCHA' };
        try { return { token: await g.enterprise.execute(key, { action: act }) }; }
        catch (e) { return { error: String(e?.message || e) }; }
      },
    });
    return r?.result || { error: 'NO_RESULT' };
  } catch (e) {
    return { error: e?.message || 'EXECUTE_SCRIPT_FAILED' };
  }
}

function _fillCaptcha(node, token) {
  if (node === CAPTCHA_SLOT) return token;
  if (Array.isArray(node)) return node.map((x) => _fillCaptcha(x, token));
  if (node && typeof node === 'object') {
    const out = {};
    for (const k of Object.keys(node)) out[k] = _fillCaptcha(node[k], token);
    return out;
  }
  return node;
}

function _needsCaptcha(node) {
  if (node === CAPTCHA_SLOT) return true;
  if (Array.isArray(node)) return node.some(_needsCaptcha);
  if (node && typeof node === 'object') return Object.values(node).some(_needsCaptcha);
  return false;
}

/** Liệt kê mọi tab Flow kèm việc trang đó CÓ grecaptcha hay không.
 *
 *  Đây là câu hỏi thực dụng nhất khi lượt sinh hỏng: "phải mở dự án hay trang chủ là đủ?".
 *  Trước đây chỉ trả lời được bằng cách thử rồi đoán từ thông báo lỗi. Nay hỏi thẳng từng tab.
 *  `projectId` nằm trong payload nên KHÔNG cần mở đúng dự án đang sinh — tab chỉ để lấy
 *  reCAPTCHA và mấy tham số phiên. */
async function handleProbeTabs(msg) {
  const tabs = await chrome.tabs.query({ url: [...LABS_TAB_URLS, 'https://flow.google.com/*'] });
  const out = [];
  for (const t of tabs) {
    let grecaptcha = 'khong-doc-duoc';
    try {
      const [r] = await chrome.scripting.executeScript({
        target: { tabId: t.id }, world: 'MAIN',
        func: () => !!(window.grecaptcha && window.grecaptcha.enterprise && window.grecaptcha.enterprise.execute),
      });
      grecaptcha = r?.result === true;
    } catch (e) { grecaptcha = `loi: ${e?.message || e}`; }
    out.push({ url: t.url, active: t.active, discarded: t.discarded, grecaptcha });
  }
  sendToAgent({ id: msg.id, result: out });
}

async function handleBoqRequest(msg) {
  const { id, params } = msg;
  let { rpcid, args = null, source_path: sourcePath } = params || {};
  const captchaAction = params?.captcha_action || 'IMAGE_GENERATION';
  if (!rpcid) { sendToAgent({ id, error: 'MISSING_RPCID' }); return; }
  if (_needsCaptcha(args)) {
    // Cùng tab sẽ gửi batchexecute, nên token sinh ra đúng origin đang gọi.
    const tab = (await pickFlowTab(FLOW_APP_TAB_URLS)) || (await pickFlowTab(BOQ_TAB_URLS));
    if (!tab) { sendToAgent({ id, error: 'NO_FLOW_APP_TAB' }); return; }
    let cap = await captchaFromMainWorld(tab, captchaAction);
    if (!cap?.token) cap = await solveCaptcha(`boq-${id}`, captchaAction);   // cầu nối cũ
    if (!cap?.token) {
      sendToAgent({ id, error: `CAPTCHA_FAILED: ${cap?.error || 'NO_TOKEN'} @${_tabOrigin(tab)}${tab.url ? '' : ' (tab.url rỗng)'}` });
      return;
    }
    args = _fillCaptcha(args, cap.token);
  }
  try {
    const out = await boqExecute(rpcid, args, { sourcePath });
    if (out.error) sendToAgent({ id, error: out.error });
    else sendToAgent({ id, status: out.status, data: out });
  } catch (e) {
    sendToAgent({ id, error: e?.message || 'BOQ_FAILED' });
  }
}

// ─── Recon: ghi lại rpcid mà giao diện thật dùng ────────────
// Không có tài liệu nào cho biết rpcid nào ứng với việc gì; cách duy nhất là xem chính app
// gọi gì khi người dùng thao tác. Nghe thụ động, không chặn, không sửa request.
const boqLog = [];

/** Rút gọn chuỗi dài trong payload, GIỮ NGUYÊN khung mảng.
 *
 *  Lượt tải ảnh lên nhét cả ảnh dạng base64 vào `f.req`, nên payload vượt xa mọi ngưỡng cắt:
 *  cắt thẳng bằng slice thì mất luôn phần cấu trúc nằm SAU khối base64 — đúng phần cần đọc.
 *  Thay chuỗi dài bằng chỗ đánh dấu thì log vẫn nhẹ mà khung mảng còn nguyên. */
function _elideLong(node, max = 512) {
  if (typeof node === 'string') {
    return node.length > max ? `<${node.length} ký tự: ${node.slice(0, 24)}…>` : node;
  }
  if (Array.isArray(node)) return node.map((x) => _elideLong(x, max));
  return node;
}

function _reconPayload(raw) {
  if (raw.length <= 20000) return raw;
  // decodeURIComponent có thể ném URIError; dựng sẵn trong mảng thì nó ném TRƯỚC vòng lặp và
  // giết luôn cả bản ghi. Giải mã trong try riêng.
  const candidates = [raw];
  try { candidates.push(decodeURIComponent(raw.replace(/\+/g, ' '))); } catch { /* bỏ qua */ }
  for (const text of candidates) {
    try {
      const call = JSON.parse(text)?.[0]?.[0];
      if (call && typeof call[1] === 'string') {
        const args = _elideLong(JSON.parse(call[1]));
        return JSON.stringify([[[call[0], JSON.stringify(args), null, 'generic']]]);
      }
    } catch { /* thử cách giải mã tiếp theo */ }
  }
  return raw.slice(0, 100000);
}

chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    if (!details.url.includes('/data/batchexecute')) return;
    try {
      const raw = details.requestBody?.formData?.['f.req']?.[0];
      const entry = {
        ts: Date.now(),
        rpcids: new URL(details.url).searchParams.get('rpcids'),
        sourcePath: new URL(details.url).searchParams.get('source-path'),
        // 4000 CẮT MẤT phần cần nhất: payload tạo ảnh chứa token reCAPTCHA (~2-3KB) rồi
        // mới tới prompt, nên cắt ở 4000 là bắt được token mà mất prompt.
        req: raw ? _reconPayload(raw) : null,
      };
      boqLog.unshift(entry);
      if (boqLog.length > 300) boqLog.pop();
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'boq_call', entry }));
      }
    } catch { /* recon chỉ để quan sát — hỏng thì bỏ qua */ }
  },
  // Lọc rộng rồi kiểm trong hàm: URL thật mang tiền tố tài khoản `/u/2/_/…` nên mẫu
  // `/_/*/data/batchexecute*` KHÔNG khớp, bỏ lọt đúng những lượt cần xem nhất.
  { urls: [`${BOQ_ORIGIN}/*`] },
  ['requestBody'],
);

// ─── TRPC Media URL Extractor ──────────────────────────────

function handleTrpcMediaUrls(trpcUrl, bodyText) {
  try {
    const matches = bodyText.match(MEDIA_URL_RE) || [];
    if (!matches.length) return;

    // Deduplicate and parse
    const urlMap = {};
    for (const rawUrl of matches) {
      // Unescape JSON-escaped URLs
      const url = rawUrl.replace(/\\u0026/g, '&').replace(/\\/g, '');
      const mediaMatch = url.match(/\/(image|video)\/([0-9a-f-]{36})\?/);
      if (mediaMatch) {
        const [, mediaType, mediaId] = mediaMatch;
        // Keep last occurrence (freshest)
        urlMap[mediaId] = { mediaType, url, mediaId };
      }
    }

    const entries = Object.values(urlMap);
    if (!entries.length) return;

    console.log(`[FlowAgent] Captured ${entries.length} fresh media URLs from TRPC`);
    // URL refresh is silent — don't show in request log

    // Forward to agent for DB update
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        type: 'media_urls_refresh',
        urls: entries,
      }));
    }
  } catch (e) {
    console.error('[FlowAgent] Failed to extract TRPC media URLs:', e);
  }
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

// ─── Human-like Telemetry ──────────────────────────────────
// Periodically send tracking events to Google's analytics endpoints
// to mimic normal browser behavior.

const _UA = navigator.userAgent;
let _telemetrySessionId = `;${Date.now()}`;

function _rand(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; }

function _buildBatchLogPayload() {
  const events = [];
  const types = ['FLOW_IMAGE_LATENCY', 'FLOW_VIDEO_LATENCY'];
  const count = _rand(1, 3);
  for (let i = 0; i < count; i++) {
    events.push({
      event: types[_rand(0, types.length - 1)],
      eventProperties: [
        { key: 'CURRENT_TIME_MS', doubleValue: Date.now() },
        { key: 'DURATION_MS', doubleValue: _rand(150, 800) },
        { key: 'USER_AGENT', stringValue: _UA },
        { key: 'IS_DESKTOP', booleanValue: true },
      ],
      eventMetadata: { sessionId: _telemetrySessionId },
      eventTime: new Date().toISOString(),
    });
  }
  return { appEvents: events };
}

function _buildFrontendEventsPayload() {
  const eventTypes = [
    'FLOW_IMAGE_LATENCY', 'FLOW_VIDEO_LATENCY', 'GRID_SCROLL_DEPTH',
    'FLOW_PROJECT_OPEN', 'FLOW_SCENE_VIEW',
  ];
  const count = _rand(1, 4);
  const events = [];
  for (let i = 0; i < count; i++) {
    const et = eventTypes[_rand(0, eventTypes.length - 1)];
    const params = {
      USER_AGENT: { '@type': 'type.googleapis.com/google.protobuf.StringValue', value: _UA },
      IS_DESKTOP: { '@type': 'type.googleapis.com/google.protobuf.StringValue', value: 'true' },
    };
    if (et.includes('LATENCY')) {
      params.CURRENT_TIME_MS = { '@type': 'type.googleapis.com/google.protobuf.StringValue', value: String(Date.now()) };
      params.DURATION_MS = { '@type': 'type.googleapis.com/google.protobuf.StringValue', value: String(_rand(100, 600)) };
    }
    if (et === 'GRID_SCROLL_DEPTH') {
      params.MEDIA_GENERATION_PAYGATE_TIER = { '@type': 'type.googleapis.com/google.protobuf.StringValue', value: 'PAYGATE_TIER_TWO' };
    }
    events.push({
      eventType: et,
      metadata: {
        sessionId: _telemetrySessionId,
        createTime: new Date().toISOString(),
        additionalParams: params,
      },
    });
  }
  return { events };
}

async function sendTelemetry() {
  if (!flowKey || state === 'off') return;

  const headers = {
    'Content-Type': 'text/plain;charset=UTF-8',
    'authorization': `Bearer ${flowKey}`,
  };

  // Telemetry is silent — don't show in request log
  try {
    if (Math.random() < 0.5) {
      await fetch(`https://aisandbox-pa.googleapis.com/v1:batchLog`, {
        method: 'POST', headers, credentials: 'include',
        body: JSON.stringify(_buildBatchLogPayload()),
      });
    } else {
      await fetch(`https://aisandbox-pa.googleapis.com/v1/flow:batchLogFrontendEvents`, {
        method: 'POST', headers, credentials: 'include',
        body: JSON.stringify(_buildFrontendEventsPayload()),
      });
    }
  } catch { }
}

// Send telemetry at random intervals (45-120s) to look organic
function scheduleTelemetry() {
  const delay = _rand(45, 120) * 1000;
  setTimeout(async () => {
    await sendTelemetry();
    scheduleTelemetry(); // reschedule with new random interval
  }, delay);
}

// Refresh session ID every ~30min like a real user
setInterval(() => { _telemetrySessionId = `;${Date.now()}`; }, _rand(25, 35) * 60 * 1000);

scheduleTelemetry();

console.log('[FlowAgent] Extension loaded');
