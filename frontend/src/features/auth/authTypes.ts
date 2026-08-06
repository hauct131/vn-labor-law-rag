export type AuthUser = {
  id: string
  email: string
  display_name: string
  created_at: string
}

export type AuthResult = {
  user: AuthUser
  csrf_token: string
  migrated_anonymous_history: boolean
}
