const API_KEY_STORAGE = 'tcg_api_key'
const ADMIN_TOKEN_STORAGE = 'tcg_admin_token'

function canUseStorage() {
  return typeof window !== 'undefined' && Boolean(window.localStorage)
}

export function readStoredAuth() {
  if (!canUseStorage()) return { apiKey: '', adminToken: '' }
  return {
    apiKey: window.localStorage.getItem(API_KEY_STORAGE) || '',
    adminToken: window.localStorage.getItem(ADMIN_TOKEN_STORAGE) || '',
  }
}

export function readStoredApiKey() {
  if (!canUseStorage()) return ''
  return window.localStorage.getItem(API_KEY_STORAGE) || ''
}

export function saveApiKey(apiKey) {
  if (!canUseStorage()) return
  if (!apiKey) {
    window.localStorage.removeItem(API_KEY_STORAGE)
    return
  }
  window.localStorage.setItem(API_KEY_STORAGE, apiKey)
}

export function saveAdminToken(adminToken) {
  if (!canUseStorage()) return
  if (!adminToken) {
    window.localStorage.removeItem(ADMIN_TOKEN_STORAGE)
    return
  }
  window.localStorage.setItem(ADMIN_TOKEN_STORAGE, adminToken)
}
