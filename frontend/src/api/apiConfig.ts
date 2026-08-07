function resolveApiBaseUrl() {
  const configured = (
    import.meta.env.VITE_API_BASE_URL || '/api'
  ).replace(/\/$/, '')

  if (configured.startsWith('/')) return configured

  try {
    const url = new URL(configured)
    const pageHostname = window.location.hostname
    const configuredIsLoopback = url.hostname === 'localhost'
      || url.hostname === '127.0.0.1'
    const pageIsLoopback = pageHostname === 'localhost'
      || pageHostname === '127.0.0.1'

    // Cookie dang nhap la host-only. Dung cung hostname cho frontend va API
    // de session hoat dong voi ca localhost va 127.0.0.1.
    if (configuredIsLoopback && pageIsLoopback) {
      url.hostname = pageHostname
    }

    return url.toString().replace(/\/$/, '')
  } catch {
    return configured
  }
}

export const API_BASE_URL = resolveApiBaseUrl()
