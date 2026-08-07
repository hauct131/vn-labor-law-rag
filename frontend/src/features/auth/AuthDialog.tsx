import { type FormEvent, useState } from 'react'
import { loginAccount, registerAccount } from './authApi'
import type { AuthResult } from './authTypes'

export function AuthDialog({
  onClose,
  onAuthenticated,
}: {
  onClose: () => void
  onAuthenticated: (result: AuthResult) => void
}) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [error, setError] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError('')
    setIsSubmitting(true)
    try {
      const result = mode === 'register'
        ? await registerAccount({
            email,
            password,
            display_name: displayName,
          })
        : await loginAccount({ email, password })
      onAuthenticated(result)
    } catch (requestError) {
      setError(requestError instanceof Error
        ? requestError.message
        : 'Không thể xác thực tài khoản.')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="auth-overlay" role="presentation" onMouseDown={onClose}>
      <section
        className="auth-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="auth-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <button
          className="auth-close"
          type="button"
          aria-label="Đóng"
          onClick={onClose}
        >
          ×
        </button>

        <p className="eyebrow">Tài khoản cá nhân</p>
        <h1 id="auth-title">
          {mode === 'login' ? 'Đăng nhập' : 'Tạo tài khoản'}
        </h1>
        <p className="auth-description">
          Lịch sử hội thoại và câu trả lời đã lưu được phân tách theo tài khoản.
        </p>

        <div className="auth-tabs" role="tablist">
          <button
            type="button"
            className={mode === 'login' ? 'active' : ''}
            onClick={() => {
              setMode('login')
              setError('')
            }}
          >
            Đăng nhập
          </button>
          <button
            type="button"
            className={mode === 'register' ? 'active' : ''}
            onClick={() => {
              setMode('register')
              setError('')
            }}
          >
            Đăng ký
          </button>
        </div>

        <form className="auth-form" onSubmit={submit}>
          {mode === 'register' && (
            <label>
              Tên hiển thị
              <input
                type="text"
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                minLength={1}
                maxLength={120}
                autoComplete="name"
                required
              />
            </label>
          )}
          <label>
            Email
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              maxLength={320}
              autoComplete="email"
              required
            />
          </label>
          <label>
            Mật khẩu
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              minLength={mode === 'register' ? 8 : 1}
              maxLength={128}
              autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
              required
            />
          </label>

          {error && <p className="auth-error" role="alert">{error}</p>}

          <button className="auth-submit" type="submit" disabled={isSubmitting}>
            {isSubmitting
              ? 'Đang xử lý…'
              : mode === 'login' ? 'Đăng nhập' : 'Tạo tài khoản'}
          </button>
        </form>
      </section>
    </div>
  )
}
