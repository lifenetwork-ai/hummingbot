type GoogleIdentity = {
  initialize(options: { client_id: string; callback: (response: { credential: string }) => void; auto_select: boolean }): void
  renderButton(element: HTMLElement, options: { theme: string; size: string; text: string; width: number }): void
}
declare global { interface Window { google?: { accounts: { id: GoogleIdentity } } } }

let loading: Promise<GoogleIdentity> | undefined

/** Share one official SDK load across mounts, including React StrictMode. */
export function loadGoogleIdentity(): Promise<GoogleIdentity> {
  if (window.google) return Promise.resolve(window.google.accounts.id)
  if (loading) return loading
  loading = new Promise<GoogleIdentity>((resolve, reject) => {
    const script = document.createElement('script')
    const fail = () => {
      window.clearTimeout(timeout)
      script.remove()
      loading = undefined
      reject(new Error('Failed to load Google sign-in'))
    }
    const timeout = window.setTimeout(fail, 15_000)
    script.src = 'https://accounts.google.com/gsi/client'
    script.async = true
    script.onerror = fail
    script.onload = () => {
      window.clearTimeout(timeout)
      if (window.google) resolve(window.google.accounts.id)
      else fail()
    }
    document.head.appendChild(script)
  })
  return loading
}
