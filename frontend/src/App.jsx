
import { useCallback, useEffect, useRef, useState } from 'react'
import './App.css'
import { QRCodeSVG } from 'qrcode.react'

async function apiRequest(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    credentials: 'include',
    headers: {
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...options.headers,
    },
  })

  const data = await response.json().catch(() => ({}))

  if (!response.ok) {
    throw new Error(data.detail || `Request failed (${response.status})`)
  }

  return data
}

function formatTime(value) {
  if (!value) return '—'

  const date = new Date(value)

  if (Number.isNaN(date.getTime())) return '—'

  return new Intl.DateTimeFormat('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function formatDate(value) {
  if (!value) return '—'

  const date = new Date(`${value}T00:00:00`)

  if (Number.isNaN(date.getTime())) return value

  return new Intl.DateTimeFormat('en-IN', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  }).format(date)
}

function getStatusLabel(status) {
  const labels = {
    WAITING: 'Waiting',
    IN_CONSULTATION: 'In consultation',
    COMPLETED: 'Completed',
    SKIPPED: 'Skipped',
    CANCELLED: 'Cancelled',
  }

  return labels[status] || status
}

function App() {
  const [currentUser, setCurrentUser] = useState(null)
  const [authChecking, setAuthChecking] = useState(true)

  const [clinicCode, setClinicCode] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [loginLoading, setLoginLoading] = useState(false)
  const [authError, setAuthError] = useState('')

  const [queue, setQueue] = useState(null)
  const [queueLoading, setQueueLoading] = useState(false)
  const [queueError, setQueueError] = useState('')
  const [notice, setNotice] = useState('')
  const [actionLoading, setActionLoading] = useState('')
  const [startWarning, setStartWarning] = useState('')
  const actionInFlightRef = useRef(false)
  const [registerLoading, setRegisterLoading] = useState(false)
  const [registration, setRegistration] = useState(null)
  const [registrationError, setRegistrationError] = useState('')
  const [codeCopied, setCodeCopied] = useState(false)
  const [patientName, setPatientName] = useState('')
  const [patientAge, setPatientAge] = useState('')
  const [patientSex, setPatientSex] = useState('')
  const [patientMobile, setPatientMobile] = useState('')
  const [visitReason, setVisitReason] = useState('General check-up')


  // Restore an existing staff session after a page refresh.
  useEffect(() => {
    let mounted = true

    async function checkSession() {
      try {
        const response = await fetch('/api/auth/me', {
          credentials: 'include',
        })

        if (response.ok) {
          const profile = await response.json()

          if (mounted) {
            setCurrentUser(profile)
          }
        } else if (response.status !== 401) {
          throw new Error('Unable to verify the staff session.')
        }
      } catch (error) {
        if (mounted) {
          setAuthError(error.message || 'Cannot connect to ClinicQ.')
        }
      } finally {
        if (mounted) {
          setAuthChecking(false)
        }
      }
    }

    checkSession()

    return () => {
      mounted = false
    }
  }, [])

  const refreshQueue = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setQueueLoading(true)

    setQueueError('')

    try {
      const data = await apiRequest('/api/staff/queue')
      setQueue(data)
    } catch (error) {
      setQueueError(error.message || 'Unable to load the clinic queue.')
    } finally {
      if (!silent) setQueueLoading(false)
    }
  }, [])

  // Load the queue whenever an authenticated staff session becomes active.
  useEffect(() => {
    if (currentUser) {
      refreshQueue()
    }
  }, [currentUser, refreshQueue])

  async function handleLogin(event) {
    event.preventDefault()

    setAuthError('')
    setLoginLoading(true)

    try {
      await apiRequest('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({
          clinic_code: clinicCode.trim().toUpperCase(),
          email: email.trim(),
          password,
        }),
      })

      const profile = await apiRequest('/api/auth/me')

      setCurrentUser(profile)
      setPassword('')
    } catch (error) {
      setAuthError(error.message || 'Login failed.')
    } finally {
      setLoginLoading(false)
    }
  }

  async function handleLogout() {
    setAuthError('')
    setQueueError('')

    try {
      await apiRequest('/api/auth/logout', {
        method: 'POST',
      })

      setCurrentUser(null)
      setQueue(null)
      setPassword('')
      setNotice('')
    } catch (error) {
      setAuthError(error.message || 'Logout failed.')
    }
  }


async function performAction(key, path, successMessage) {
  // Prevent duplicate clicks before React updates the UI.
  if (actionInFlightRef.current) {
    return
  }

  actionInFlightRef.current = true
  setActionLoading(key)

  setQueueError('')
  setNotice('')
  setStartWarning('')

  try {
    await apiRequest(path, {
      method: 'POST',
    })

    setNotice(successMessage)

    await refreshQueue({ silent: true })
  } catch (error) {
    setQueueError(error.message || 'The operation failed.')
  } finally {
    actionInFlightRef.current = false
    setActionLoading('')
  }
}

function handleStartConsultation(tokenId, tokenNumber) {
  // An active consultation already exists.
  if (activeTokenId) {
    setStartWarning(
      'Complete the active consultation before starting another patient.'
    )
    return
  }

  setStartWarning('')

  performAction(
    'start',
    `/api/staff/tokens/${tokenId}/start`,
    `Consultation started for token #${tokenNumber}.`
  )
}


async function handleRegisterPatient() {
  setRegisterLoading(true)
  setRegistrationError('')
  setNotice('')

  try {
    const data = await apiRequest('/api/staff/tokens', {
      method: 'POST',
      body: JSON.stringify({
  patient_name: patientName.trim(),
  age: Number(patientAge),
  sex: patientSex,
  mobile_number: patientMobile.trim(),
  visit_reason: visitReason,
}),
    })

    if (!data.patient_access_code || !data.token_number) {
      throw new Error('The server returned an incomplete token response.')
    }

    setRegistration(data)
    setCodeCopied(false)

    setNotice(
      `Token #${data.token_number} created. Save its access code before continuing.`
    )

    await refreshQueue({ silent: true })
  } catch (error) {
    setRegistrationError(
      error.message || 'Unable to register the patient.'
    )
  } finally {
    setRegisterLoading(false)
  }
}

async function handleCopyAccessCode() {
  if (!registration?.patient_access_code) return

  try {
    await navigator.clipboard.writeText(
      registration.patient_access_code
    )

    setCodeCopied(true)
    setRegistrationError('')
  } catch {
    setRegistrationError(
      'Clipboard unavailable. Select the access code and copy it manually.'
    )
  }
}

function handleDismissRegistration() {
  setRegistration(null)
  setCodeCopied(false)
  setRegistrationError('')
}


  if (authChecking) {
    return (
      <main className="app-loading">
        <div className="loading-indicator" />
        <p>Checking your ClinicQ session...</p>
      </main>
    )
  }

  if (!currentUser) {
    return (
      <main className="login-layout">
        <section className="brand-panel">
          <div className="brand-header">
            <div className="brand-mark">C</div>
            <span>ClinicQ</span>
          </div>

          <div className="brand-content">
            <p className="eyebrow">STAFF MANAGEMENT PORTAL</p>

            <h1>
              A clearer queue.
              <br />
              A better clinic experience.
            </h1>

            <p className="brand-description">
              Manage patient flow, monitor waiting times, and keep
              your clinic queue organised from one place.
            </p>

            <div className="feature-list">
              <div className="feature-item">
                <span className="feature-number">01</span>
                <div>
                  <strong>Queue management</strong>
                  <p>Register and manage patient tokens.</p>
                </div>
              </div>

              <div className="feature-item">
                <span className="feature-number">02</span>
                <div>
                  <strong>Consultation tracking</strong>
                  <p>Control the active consultation.</p>
                </div>
              </div>

              <div className="feature-item">
                <span className="feature-number">03</span>
                <div>
                  <strong>Waiting time estimates</strong>
                  <p>Help patients understand expected delays.</p>
                </div>
              </div>
            </div>
          </div>

          <p className="brand-footer">
            ClinicQ · Queue Prediction & Crowd Management
          </p>
        </section>

        <section className="form-panel">
          <div className="mobile-brand">
            <div className="brand-mark">C</div>
            <span>ClinicQ</span>
          </div>

          <div className="login-card">
            <p className="eyebrow">WELCOME BACK</p>
            <h2>Staff sign in</h2>

            <p className="form-description">
              Enter your clinic credentials to access the staff portal.
            </p>

            <form onSubmit={handleLogin}>
              <div className="form-group">
                <label htmlFor="clinicCode">Clinic code</label>

                <input
                  id="clinicCode"
                  type="text"
                  placeholder="Enter your clinic code"
                  value={clinicCode}
                  onChange={(event) =>
                    setClinicCode(event.target.value.toUpperCase())
                  }
                  autoComplete="organization"
                  required
                  minLength={4}
                  maxLength={20}
                />
              </div>

              <div className="form-group">
                <label htmlFor="email">Staff email</label>

                <input
                  id="email"
                  type="email"
                  placeholder="Enter your staff email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  autoComplete="username"
                  required
                />
              </div>

              <div className="form-group">
                <label htmlFor="password">Password</label>

                <div className="password-wrapper">
                  <input
                    id="password"
                    type={showPassword ? 'text' : 'password'}
                    placeholder="Enter your password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    autoComplete="current-password"
                    required
                  />

                  <button
                    className="password-toggle"
                    type="button"
                    onClick={() => setShowPassword(!showPassword)}
                  >
                    {showPassword ? 'Hide' : 'Show'}
                  </button>
                </div>
              </div>

              {authError && (
                <div className="error-message" role="alert">
                  {authError}
                </div>
              )}

              <button
                className="login-button"
                type="submit"
                disabled={loginLoading}
              >
                {loginLoading ? 'Signing in...' : 'Sign in to ClinicQ'}
              </button>
            </form>

            <p className="security-note">
              Your staff session is protected by secure authentication.
            </p>
          </div>

          <p className="form-footer">
            © 2026 ClinicQ. Staff access only.
          </p>
        </section>
      </main>
    )
  }

  const tokens = [...(queue?.tokens || [])].sort(
    (a, b) => a.token_number - b.token_number
  )

  const waitingTokens = tokens.filter(
    (token) => token.status === 'WAITING'
  )

  const completedCount = tokens.filter(
    (token) => token.status === 'COMPLETED'
  ).length

  const firstWaiting = waitingTokens[0]

  const activeToken = tokens.find(
    (token) => token.status === 'IN_CONSULTATION'
  )

  const consultation = queue?.current_consultation

  const activeTokenId =
    activeToken?.token_id || consultation?.token_id

  const activeTokenNumber =
    activeToken?.token_number ??
    consultation?.token_number

  const staffName =
    currentUser.display_name ||
    currentUser.email?.split('@')[0] ||
    'Staff'

  return (
    <main className="dashboard-shell">
      <aside className="dashboard-sidebar">
        <div className="dashboard-brand">
          <div className="brand-mark">C</div>
          <span>ClinicQ</span>
        </div>

        <div className="sidebar-label">WORKSPACE</div>

        <nav className="sidebar-nav">
          <div className="sidebar-link active">
            <span className="nav-icon">▦</span>
            Dashboard
          </div>

          <div className="sidebar-link">
            <span className="nav-icon">≡</span>
            Patient queue
          </div>

          <div className="sidebar-link">
            <span className="nav-icon">◷</span>
            Consultations
          </div>
        </nav>

        <div className="sidebar-bottom">
          <div className="sidebar-clinic">
            <span className="clinic-dot" />
            <div>
              <strong>{queue?.clinic_name || 'Your clinic'}</strong>
              <span>Staff workspace</span>
            </div>
          </div>

          <button
            className="sidebar-logout"
            type="button"
            onClick={handleLogout}
          >
            Sign out
          </button>
        </div>
      </aside>

      <section className="dashboard-content">
        <header className="dashboard-topbar">
          <div>
            <p className="topbar-caption">CLINIC MANAGEMENT</p>
            <h1>Queue dashboard</h1>
          </div>

          <div className="staff-profile">
            <div className="profile-avatar">
              {staffName.charAt(0).toUpperCase()}
            </div>

            <div className="profile-text">
              <strong>{staffName}</strong>
              <span>{currentUser.role || 'Staff'}</span>
            </div>
          </div>
        </header>

        <div className="dashboard-body">
          <div className="clinic-heading">
            <div>
              <h2>{queue?.clinic_name || 'Loading clinic...'}</h2>
              <p>
                {queue?.doctor_name
                  ? `Dr. ${queue.doctor_name}`
                  : 'Clinic queue overview'}
                {queue?.clinic_code
                  ? ` · ${queue.clinic_code}`
                  : ''}
              </p>
            </div>

            <div className="date-refresh">
              <span>{formatDate(queue?.queue_date)}</span>

              <button
                type="button"
                className="refresh-button"
                onClick={() => refreshQueue()}
                disabled={queueLoading}
              >
                {queueLoading ? 'Refreshing...' : '↻ Refresh'}
              </button>
            </div>
          </div>

          {queueError && (
            <div className="dashboard-alert error-message" role="alert">
              {queueError}
            </div>
          )}

          {notice && (
            <div className="dashboard-alert success-message" role="status">
              {notice}
            </div>
          )}

          {queue ? (
            <>
              <section className="stats-grid">
                <article className="stat-card">
                  <span className="stat-label">Waiting patients</span>
                  <strong>{queue.waiting_count}</strong>
                  <span className="stat-footnote">Patients in queue</span>
                </article>

                <article className="stat-card">
                  <span className="stat-label">Total tokens today</span>
                  <strong>{queue.total_tokens_today}</strong>
                  <span className="stat-footnote">Registered today</span>
                </article>

                <article className="stat-card">
                  <span className="stat-label">Completed consultations</span>
                  <strong>{completedCount}</strong>
                  <span className="stat-footnote">Completed tokens</span>
                </article>

                <article className="stat-card">
                  <span className="stat-label">Queue status</span>

                  <strong className="queue-status-value">
                    <span
                      className={
                        queue.queue_paused
                          ? 'status-dot paused'
                          : 'status-dot running'
                      }
                    />
                    {queue.queue_paused ? 'Paused' : 'Running'}
                  </strong>

                  <span className="stat-footnote">Current clinic status</span>
                </article>
              </section>

<section className="panel registration-panel">
  <div className="panel-heading">
    <div>
      <span className="panel-eyebrow">PATIENT REGISTRATION</span>
      <h3>Register a new patient</h3>
    </div>

    <span className="waiting-count-pill">Staff access</span>
  </div>

  <p className="registration-description">
    Create a new queue token and generate a private patient access code.
  </p>

<div className="patient-registration-form">
  <label>
    Patient name
    <input
      type="text"
      value={patientName}
      onChange={(e) => setPatientName(e.target.value)}
      placeholder="Enter patient name"
      required
    />
  </label>

  <label>
    Age
    <input
      type="number"
      min="1"
      max="120"
      value={patientAge}
      onChange={(e) => setPatientAge(e.target.value)}
      placeholder="Enter age"
      required
    />
  </label>

  <label>
    Sex
    <select value={patientSex} onChange={(e) => setPatientSex(e.target.value)} required>
      <option value="">Select sex</option>
      <option value="Male">Male</option>
      <option value="Female">Female</option>
      <option value="Other">Other</option>
      <option value="Prefer not to say">Prefer not to say</option>
    </select>
  </label>

  <label>
    Mobile number
    <input
      type="tel"
      maxLength="10"
      value={patientMobile}
      onChange={(e) => setPatientMobile(e.target.value.replace(/\D/g, '').slice(0, 10))}
      placeholder="10-digit mobile number"
      required
    />
  </label>

  <label>
    Visit reason
    <select value={visitReason} onChange={(e) => setVisitReason(e.target.value)}>
      <option value="General check-up">General check-up</option>
      <option value="Follow-up check-up">Follow-up check-up</option>
      <option value="Fever">Fever</option>
      <option value="Cold">Cold</option>
      <option value="Other">Other</option>
    </select>
  </label>
</div>

  <button
    className="register-action"
    type="button"
    disabled={registerLoading || Boolean(registration)}
    onClick={handleRegisterPatient}
  >
    {registerLoading
      ? 'Registering patient...'
      : registration
        ? 'Save the access code first'
        : '+ Register patient'}
  </button>

  {registrationError && (
    <div className="error-message" role="alert">
      {registrationError}
    </div>
  )}

  {registration && (
    <div className="registration-result">
      <div className="registration-result-heading">
        <span className="success-check">✓</span>
        <div>
          <strong>Patient registered successfully</strong>
          <p>Token #{registration.token_number}</p>
        </div>
      </div>

      <div className="registration-details">
        <div>
          <span>Clinic</span>
          <strong>{registration.clinic_name}</strong>
        </div>

        <div>
          <span>Doctor</span>
          <strong>{registration.doctor_name}</strong>
        </div>

        <div>
          <span>Queue status</span>
          <strong>{getStatusLabel(registration.patient_status)}</strong>
        </div>
      </div>

      <div className="patient-qr-section">
          <h3>Patient Dashboard QR Code</h3>

        <QRCodeSVG
          value={`${window.location.origin}/patient#qr=${encodeURIComponent(registration.qr_token)}`}
          size={200}
          level="H"
          marginSize={4}
        />

        <p>Scan this QR code to open your personal patient dashboard.</p>
      </div>

      <div className="access-code-section">
        <label>Private patient access code</label>

        <div className="access-code-row">
          <code>{registration.patient_access_code}</code>

          <button
            type="button"
            className="copy-code-button"
            onClick={handleCopyAccessCode}
          >
            {codeCopied ? 'Copied ✓' : 'Copy code'}
          </button>
        </div>

        <p className="access-code-warning">
          This code cannot be retrieved later. Save it and share it
          securely with the patient.
        </p>
      </div>

      <button
        type="button"
        className="dismiss-code-button"
        onClick={handleDismissRegistration}
      >
        I've saved the access code
      </button>
    </div>
  )}
</section>


              <section className="dashboard-action-grid">
                <article className="panel active-consultation-panel">
                  <div className="panel-heading">
                    <div>
                      <span className="panel-eyebrow">
                        LIVE CONSULTATION
                      </span>
                      <h3>Current patient</h3>
                    </div>

                    <span className="live-badge">
                      {activeTokenId ? 'ACTIVE' : 'NO ACTIVE PATIENT'}
                    </span>
                  </div>

                  {activeTokenId ? (
                    <>
                      <div className="active-patient">
                        <span className="active-token-label">
                          CURRENT TOKEN
                        </span>

                        <strong>
                          {activeTokenNumber
                            ? `#${activeTokenNumber}`
                            : 'Active'}
                        </strong>

                        <span>
                          Started at{' '}
                          {formatTime(
                            activeToken?.started_at ||
                              consultation?.started_at
                          )}
                        </span>
                      </div>

                      <button
                        className="primary-action"
                        type="button"
                        disabled={Boolean(actionLoading)}
                        onClick={() =>
                          performAction(
                            'complete',
                            `/api/staff/tokens/${activeTokenId}/complete`,
                            'Consultation completed successfully.'
                          )
                        }
                      >
                        {actionLoading === 'complete'
                          ? 'Processing...'
                          : '✓ Complete consultation'}
                      </button>
                    </>
                  ) : (
                    <div className="empty-consultation">
                      <div className="empty-icon">○</div>
                      <strong>No active consultation</strong>
                      <p>
                        Start the next waiting patient when the doctor
                        is ready.
                      </p>
                    </div>
                  )}
                </article>

                <article className="panel queue-control-panel">
                  <span className="panel-eyebrow">QUEUE CONTROLS</span>
                  <h3>Manage patient flow</h3>

                  <p className="control-description">
                    Temporarily stop or resume new consultations.
                    An active consultation is not automatically ended.
                  </p>

                  {queue.queue_paused ? (
                    <button
                      className="resume-action"
                      type="button"
                      disabled={Boolean(actionLoading)}
                      onClick={() =>
                        performAction(
                          'resume',
                          '/api/staff/queue/resume',
                          'Queue resumed successfully.'
                        )
                      }
                    >
                      {actionLoading === 'resume'
                        ? 'Processing...'
                        : '▶ Resume queue'}
                    </button>
                  ) : (
                    <button
                      className="pause-action"
                      type="button"
                      disabled={Boolean(actionLoading)}
                      onClick={() =>
                        performAction(
                          'pause',
                          '/api/staff/queue/pause',
                          'Queue paused successfully.'
                        )
                      }
                    >
                      {actionLoading === 'pause'
                        ? 'Processing...'
                        : 'Ⅱ Pause queue'}
                    </button>
                  )}

                  <div className="control-status">
                    <span
                      className={
                        queue.queue_paused
                          ? 'status-dot paused'
                          : 'status-dot running'
                      }
                    />
                    {queue.queue_paused
                      ? 'New consultations are paused'
                      : 'Queue is accepting consultations'}
                  </div>
                </article>
              </section>

              <section className="panel next-patient-panel">
                <div className="panel-heading">
                  <div>
                    <span className="panel-eyebrow">NEXT IN LINE</span>
                    <h3>Next waiting patient</h3>
                  </div>

                  <span className="waiting-count-pill">
                    {waitingTokens.length} waiting
                  </span>
                </div>

                {firstWaiting ? (
                  <div className="next-patient-content">
                    <div className="next-token-number">
                      #{firstWaiting.token_number}
                    </div>

                    <div className="next-patient-info">
                      <strong>Token #{firstWaiting.token_number}</strong>
                      <span>
                        Joined at {formatTime(firstWaiting.joined_at)}
                      </span>
                    </div>

                    <button
                      className="primary-action start-action"
                      type="button"
                      disabled={
  Boolean(actionLoading) ||
  queue.queue_paused
}
onClick={() =>
  handleStartConsultation(
    firstWaiting.token_id,
    firstWaiting.token_number
  )
}
                    >
                      {actionLoading === 'start'
                        ? 'Starting...'
                        : 'Start consultation'}
                    </button>
                  </div>
                ) : (
                  <p className="empty-queue-message">
                    No waiting patients. The queue is currently empty.
                  </p>
                )}

                {queue.queue_paused && (
                  <p className="inline-warning">
                    Resume the queue before starting a consultation.
                  </p>
                )}

                {startWarning && (
  <p className="inline-warning" role="alert">
    {startWarning}
  </p>
)}
              </section>

              <section className="panel token-list-panel">
                <div className="panel-heading">
                  <div>
                    <span className="panel-eyebrow">DAILY RECORD</span>
                    <h3>Today's patient queue</h3>
                  </div>

                  <span className="token-total">
                    {tokens.length} records
                  </span>
                </div>

                <div className="token-table-wrapper">
                  <table className="token-table">
                    <thead>
                      <tr>
                        <th>Token</th>
                        <th>Joined</th>
                        <th>Status</th>
                        <th>Actions</th>
                      </tr>
                    </thead>

                    <tbody>
                      {tokens.map((token) => {
                        const isWaiting = token.status === 'WAITING'
                        const isFirstWaiting =
                          firstWaiting?.token_id === token.token_id

                        return (
                          <tr key={token.token_id}>
                            <td>
                              <div>
  <strong className="table-token">
    #{token.token_number}
  </strong>

  <div className="table-patient-name">
    {token.patient_name || "Unknown"}
  </div>
</div>
                            </td>

                            <td>{formatTime(token.joined_at)}</td>

                            <td>
                              <span
                                className={`token-status ${token.status.toLowerCase()}`}
                              >
                                {getStatusLabel(token.status)}
                              </span>
                            </td>

                            <td>
                              {isWaiting ? (
                                <div className="row-actions">
                                  {isFirstWaiting && (
                                    <button
                                      type="button"
                                      className="small-action start-small"
                                     disabled={
  Boolean(actionLoading) ||
  queue.queue_paused
}
onClick={() =>
  handleStartConsultation(
    token.token_id,
    token.token_number
  )
}
                                    >
                                      Start
                                    </button>
                                  )}

                                  <button
                                    type="button"
                                    className="small-action skip-small"
                                    disabled={Boolean(actionLoading)}
                                    onClick={() =>
                                      performAction(
                                        `skip-${token.token_id}`,
                                        `/api/staff/tokens/${token.token_id}/skip`,
                                        `Token #${token.token_number} skipped.`
                                      )
                                    }
                                  >
                                    Skip
                                  </button>

                                  <button
                                    type="button"
                                    className="small-action cancel-small"
                                    disabled={Boolean(actionLoading)}
                                    onClick={() =>
                                      performAction(
                                        `cancel-${token.token_id}`,
                                        `/api/staff/tokens/${token.token_id}/cancel`,
                                        `Token #${token.token_number} cancelled.`
                                      )
                                    }
                                  >
                                    Cancel
                                  </button>
                                </div>
                              ) : (
                                <span className="no-action">—</span>
                              )}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>

                {tokens.length === 0 && (
                  <p className="empty-queue-message">
                    No tokens have been registered today.
                  </p>
                )}
              </section>

              <footer className="dashboard-footer">
                ClinicQ · Queue Prediction & Crowd Management
              </footer>
            </>
          ) : (
            <div className="queue-loading">
              <div className="loading-indicator" />
              <p>Loading clinic queue...</p>
            </div>
          )}
        </div>
      </section>
    </main>
  )
}

export default App
