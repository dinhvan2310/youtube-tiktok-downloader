const API = 'http://127.0.0.1:8765/api'

export async function api(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  const raw = await response.text()
  let body = {}
  try { body = raw ? JSON.parse(raw) : {} } catch { throw new Error(`Server returned ${response.status}: ${raw.slice(0, 200)}`) }
  if (!response.ok) {
    const detail = Array.isArray(body.detail) ? body.detail.join('\n') : typeof body.detail === 'object' ? body.detail.message || JSON.stringify(body.detail) : body.detail
    const error = new Error(detail || `Request failed (${response.status})`)
    error.status = response.status
    error.detail = body.detail
    throw error
  }
  return body
}

export const getSources = (includeArchived = false) => api(`/sources/stats?include_archived=${includeArchived}`)
export const getConfig = () => api('/config')
export const saveConfig = config => api('/config', { method: 'PUT', body: JSON.stringify(config) })
export const getOverview = () => api('/overview')
export const getCrawled = (sourceId, status = '') => api(`/crawled/${encodeURIComponent(sourceId)}?status=${encodeURIComponent(status)}`)
export const validateSource = payload => api('/sources/validate', { method: 'POST', body: JSON.stringify(payload) })
export const createSource = payload => api('/sources', { method: 'POST', body: JSON.stringify(payload) })
export const updateSource = (id, payload) => api(`/sources/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(payload) })
export const setSourceArchived = (id, archived) => api(`/sources/${encodeURIComponent(id)}/${archived ? 'archive' : 'restore'}`, { method: 'POST' })
export const getVideos = (filters = {}) => {
  const params = new URLSearchParams()
  const aliases = { sourceId: 'source_id', excludeKeyword: 'exclude_keyword', minDuration: 'min_duration', maxDuration: 'max_duration', minViews: 'min_views', publishedAfter: 'published_after', publishedBefore: 'published_before', metadataStatus: 'metadata_status', contentType: 'content_type' }
  Object.entries({ status: 'discovered', limit: 200, ...filters }).forEach(([key, value]) => {
    if (value !== '' && value != null) params.set(aliases[key] || key, value)
  })
  return api(`/videos?${params}`)
}
export const transitionVideos = payload => api('/videos/transition', { method: 'POST', body: JSON.stringify(payload) })
export const getQueue = () => api('/queue')
export const getJobs = () => api('/jobs?limit=50')
export const createJob = payload => api('/jobs', { method: 'POST', body: JSON.stringify(payload) })
export const ensureSync = () => api('/sync/ensure', { method: 'POST' })
export const stopJob = id => api(`/jobs/${encodeURIComponent(id)}/stop`, { method: 'POST' })
export const eventUrl = id => `${API}/jobs/${encodeURIComponent(id)}/events`
