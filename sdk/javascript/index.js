class Client {
  constructor({ baseUrl, apiKey }) {
    this.baseUrl = (baseUrl || '').replace(/\/$/, '');
    this.apiKey = apiKey;
  }

  async _get(path, params = {}) {
    const url = new URL(`${this.baseUrl}${path}`);
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        url.searchParams.set(key, value);
      }
    });

    const headers = {};
    if (this.apiKey) {
      headers['X-API-Key'] = this.apiKey;
    }

    const response = await fetch(url.toString(), { headers });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    return payload;
  }

  games() { return this._get('/api/games'); }
  cards(params = {}) { return this._get('/api/cards', params); }
  prints(params = {}) { return this._get('/api/prints', params); }
  print(id) { return this._get(`/api/prints/${id}`); }
  sets(params = {}) { return this._get('/api/sets', params); }
  search(params = {}) { return this._get('/api/search', params); }
}

module.exports = { Client };
