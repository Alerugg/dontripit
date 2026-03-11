const API_KEY_STORAGE = 'tcg_api_key'
const ADMIN_TOKEN_STORAGE = 'tcg_admin_token'

export function readStoredAuth() {
  if (typeof window === 'undefined') return { apiKey: '', adminToken: '' }
  return {
    apiKey: window.localStorage.getItem(API_KEY_STORAGE) || '',
    adminToken: window.localStorage.getItem(ADMIN_TOKEN_STORAGE) || '',
  }
}

export function saveApiKey(apiKey) {
  if (typeof window === 'undefined') return
  if (!apiKey) {
    window.localStorage.removeItem(API_KEY_STORAGE)
    return
  }
  window.localStorage.setItem(API_KEY_STORAGE, apiKey)
}

export function saveAdminToken(adminToken) {
  if (typeof window === 'undefined') return
  if (!adminToken) {
    window.localStorage.removeItem(ADMIN_TOKEN_STORAGE)
    return
  }
  window.localStorage.setItem(ADMIN_TOKEN_STORAGE, adminToken)
}
