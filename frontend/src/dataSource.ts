/** A published provider/network/year is selected through a shareable URL. */
export function datasetId(): string | null {
  const value = new URLSearchParams(location.search).get('dataset')
  return value && /^[a-z0-9][a-z0-9_-]*\/[a-z0-9][a-z0-9_-]*\/\d{4}$/.test(value) ? value : null
}

export function projectDataUrl(filename: string): string {
  const id = datasetId()
  return id ? `/project-data/providers/${id}/${filename}` : `/project-data/${filename}`
}

export function providerQuery(): URLSearchParams {
  const id = datasetId()
  if (!id) return new URLSearchParams()
  const [provider_id, network_id] = id.split('/')
  return new URLSearchParams({ provider_id, network_id })
}
