(function () {
  function csrfToken() {
    return window.IES_CSRF_TOKEN || '';
  }

  function errorMessage(data) {
    return data?.error?.message || data?.error || 'Неизвестная ошибка';
  }

  async function parseJsonSafe(res) {
    const text = await res.text();
    if (!text) return {};
    try {
      return JSON.parse(text);
    } catch (_) {
      return {
        ok: false,
        error: {
          message: `Сервер вернул некорректный ответ (HTTP ${res.status})`,
        },
      };
    }
  }

  function mergeHeaders(options) {
    const headers = new Headers(options?.headers || {});
    headers.set('Accept', 'application/json');
    headers.set('X-Requested-With', 'XMLHttpRequest');
    if (!headers.has('X-CSRFToken')) {
      headers.set('X-CSRFToken', csrfToken());
    }
    return headers;
  }

  async function apiFetchJson(url, options) {
    const requestOptions = Object.assign({credentials: 'same-origin'}, options || {});
    requestOptions.headers = mergeHeaders(options);
    try {
      const res = await fetch(url, requestOptions);
      const data = await parseJsonSafe(res);
      if (!res.ok) {
        if (typeof data !== 'object' || data === null) {
          return {ok: false, error: {message: `HTTP ${res.status}`}};
        }
        if (data.ok === undefined) data.ok = false;
        if (!data.error) data.error = {message: `HTTP ${res.status}`};
        return data;
      }
      if (typeof data !== 'object' || data === null) {
        return {ok: false, error: {message: 'Пустой ответ сервера'}};
      }
      if (data.ok === undefined) data.ok = true;
      return data;
    } catch (_) {
      return {
        ok: false,
        error: {message: 'Ошибка сети. Проверьте соединение и повторите.'},
      };
    }
  }

  window.IESApi = {
    apiFetchJson,
    errorMessage,
    parseJsonSafe,
    csrfToken,
  };
})();
