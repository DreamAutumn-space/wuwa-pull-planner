import type { Account, CharacterCatalog, Database, DpsRecognitionDocument, OptimizeRequest, OptimizeResponse, PortraitAtlas, ReferenceDps } from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(detail || `请求失败（${response.status}）`)
  }
  return response.json() as Promise<T>
}

export function getHealth(): Promise<unknown> {
  return request('/api/health')
}

export function getPublicDatabase(): Promise<Database> {
  return request('/api/database', { cache: 'no-cache' })
}

export function getDemoDatabase(): Promise<Database> {
  return request('/api/demo-database', { cache: 'no-cache' })
}

export function getCharacterCatalog(): Promise<CharacterCatalog> {
  return request('/api/catalog', { cache: 'no-store' })
}

export function getReferenceDps(): Promise<ReferenceDps> {
  return request('/api/reference-dps')
}

export function getDpsRecognition(): Promise<DpsRecognitionDocument> {
  return request('/api/dps-recognition', { cache: 'no-cache' })
}

export function getPortraitAtlas(): Promise<PortraitAtlas> {
  return request('/api/portrait-atlas', { cache: 'no-cache' })
}

export function getExample(): Promise<{ account: Account; database?: Database; settings?: Partial<OptimizeRequest['settings']> }> {
  return request('/api/example')
}

export function getExampleAccount(): Promise<Account> {
  return request('/api/example-account', { cache: 'no-store' })
}

export function optimize(payload: OptimizeRequest): Promise<OptimizeResponse> {
  return request('/api/optimize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}
