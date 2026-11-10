export type AppConfig = Readonly<{
  adminApiBaseUrl: string
  googleClientId: string | undefined
}>

declare global {
  interface ImportMetaEnv {
    readonly VITE_ADMIN_API_BASE_URL?: string
    readonly VITE_GOOGLE_CLIENT_ID?: string
  }
}

/** The only application boundary for public Vite environment configuration. */
export function getAppConfig(): AppConfig {
  return {
    adminApiBaseUrl: import.meta.env.VITE_ADMIN_API_BASE_URL || '/api/v1/admin',
    googleClientId: import.meta.env.VITE_GOOGLE_CLIENT_ID,
  }
}
