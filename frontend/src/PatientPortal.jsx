
import { useCallback, useEffect, useRef, useState } from 'react'
import './PatientPortal.css'
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || ''


async function patientRequest(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    credentials: 'include',
    headers: {
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...options.headers,
    },
  })

  const data = await response.json().catch(() => ({}))

  if (!response.ok) {
    const error = new Error(
      data.detail || `Request failed (${response.status})`
    )
    error.status = response.status
    throw error
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

function getStatusText(status) {
  const labels = {
    WAITING: 'Waiting',
    IN_CONSULTATION: 'In consultation',
    COMPLETED: 'Completed',
    SKIPPED: 'Skipped',
    CANCELLED: 'Cancelled',
  }

  return labels[status] || status || 'Unknown'
}

function PatientPortal() {
  const [checkingSession, setCheckingSession] = useState(true)
  const qrAuthStartedRef = useRef(false)
  const [clinicCode, setClinicCode] = useState('')
  const [tokenDate, setTokenDate] = useState('')
  const [tokenNumber, setTokenNumber] = useState('')
  const [accessCode, setAccessCode] = useState('')

  const [patient, setPatient] = useState(null)
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [liveStatus, setLiveStatus] = useState('connecting')

  // Restore an existing patient session when the page is opened.
  useEffect(() => {
    let active = true

    async function restoreSession() {
      try {
        const data = await patientRequest('/api/patient/me')

        if (active) {
          setPatient(data.patient)
        }
      } catch (err) {
        if (active && err.status !== 401) {
          setError(
            err.message || 'Unable to connect to the ClinicQ server.'
          )
        }
      } finally {
        if (active) {
          setCheckingSession(false)
        }
      }
    }

    restoreSession()

    return () => {
      active = false
    }
  }, [])

  useEffect(() => {
  const qrToken = new URLSearchParams(
  window.location.hash.slice(1)
).get('qr')
  if (!qrToken || qrAuthStartedRef.current) return

  qrAuthStartedRef.current = true

  async function verifyPatientQR() {
    setLoading(true)
    setError('')
    setNotice('')

    try {
      await patientRequest('/api/patient/qr-access', {
        method: 'POST',
        body: JSON.stringify({ qr_token: qrToken }),
      })

      const data = await patientRequest('/api/patient/me')

      setPatient(data.patient)
      setAccessCode('')
      setNotice('Patient access verified successfully.')

      window.history.replaceState(
        {},
        document.title,
        window.location.pathname
      )
    } catch (err) {
      qrAuthStartedRef.current = false
      setError(err.message || 'Unable to verify patient QR code.')
    } finally {
      setLoading(false)
    }
  }

  verifyPatientQR()
}, [])

  async function handleAccess(event) {
    event.preventDefault()

    setLoading(true)
    setError('')
    setNotice('')

    try {
      await patientRequest('/api/patient/access', {
        method: 'POST',
        body: JSON.stringify({
          clinic_code: clinicCode.trim().toUpperCase(),
          token_date: tokenDate,
          token_number: Number(tokenNumber),
          access_code: accessCode.trim(),
        }),
      })

      const data = await patientRequest('/api/patient/me')

      setPatient(data.patient)
      setAccessCode('')
      setNotice('Patient access verified successfully.')
    } catch (err) {
      setError(
        err.message || 'Unable to verify patient access.'
      )
    } finally {
      setLoading(false)
    }
  }

  const refreshPatientDetails = useCallback(async () => {
  setRefreshing(true)

  try {
    const data = await patientRequest('/api/patient/me')
    setPatient(data.patient)
    setError('')
  } catch (err) {
    setError(
      err.message || 'Unable to refresh patient information.'
    )

    if (err.status === 401) {
      setPatient(null)
    }
  } finally {
    setRefreshing(false)
  }
}, [])
useEffect(() => {
  if (!patient) return

  // The browser automatically sends the same-origin
  // HTTP-only patient session cookie.
  const eventSource = new EventSource(
  `${API_BASE_URL}/api/patient/events`,
  { withCredentials: true }
)

  eventSource.addEventListener('connected', () => {
    setLiveStatus('connected')
  })

  eventSource.addEventListener('queue_update', () => {
    refreshPatientDetails()
  })

  eventSource.addEventListener('session_expired', () => {
    eventSource.close()
    setPatient(null)
    setError('Your patient session has expired. Please sign in again.')
  })

  eventSource.onerror = () => {
    setLiveStatus('reconnecting')
  }

  return () => {
    eventSource.close()
  }
}, [patient, refreshPatientDetails])


  // Refresh more frequently while a doctor is consulting.
  useEffect(() => {
    if (!patient) return

    const activeConsultation =
      patient.patient_status === 'WAITING' &&
      patient.current_serving_token_number !== null &&
      patient.current_serving_token_number !== undefined

    const refreshMilliseconds = activeConsultation
      ? 15000
      : 60000

    const refreshInterval = window.setInterval(() => {
      refreshPatientDetails()
    }, refreshMilliseconds)

    return () => {
      window.clearInterval(refreshInterval)
    }
  }, [patient, refreshPatientDetails])


  async function handleLogout() {
    setError('')

    try {
      await patientRequest('/api/patient/logout', {
        method: 'POST',
      })

      setPatient(null)
      setAccessCode('')
      setTokenNumber('')
      setTokenDate('')
      setNotice('You have safely logged out.')
    } catch (err) {
      setError(err.message || 'Unable to log out.')
    }
  }

  if (checkingSession) {
    return (
      <main className="patient-loading">
        <div className="patient-spinner" />
        <p>Checking your patient session...</p>
      </main>
    )
  }

  if (!patient) {
    return (
      <main className="patient-page patient-login-page">
        

        <section className="patient-login-layout">
          <div className="patient-welcome">

  <a href="/" className="patient-login-brand">
    <span className="patient-brand-mark">C</span>
    <span>ClinicQ</span>
  </a>

  <p className="patient-eyebrow">PATIENT MANAGEMENT PORTAL</p>
            <h1>
              Your time matters.
              <br />
              Know your place in line.
            </h1>

            <p>
              Access your queue status and follow your clinic visit
              without repeatedly asking the reception desk.
            </p>

            <div className="patient-feature">
              <span>01</span>
              <div>
                <strong>Check your token</strong>
                <p>Know your current queue status.</p>
              </div>
            </div>

            <div className="patient-feature">
              <span>02</span>
              <div>
                <strong>Track your position</strong>
                <p>See how many patients are ahead of you.</p>
              </div>
            </div>

            <div className="patient-feature">
              <span>03</span>
              <div>
                <strong>Stay informed</strong>
                <p>Check updates from your clinic.</p>
              </div>
            </div>
          </div>

          <div className="patient-form-card">
            <p className="patient-eyebrow">PATIENT ACCESS</p>

            <h2>Check your token</h2>

            <p className="patient-form-description">
              Enter the details provided by your clinic.
            </p>

            <form onSubmit={handleAccess}>
              <div className="patient-form-group">
                <label htmlFor="patientClinic">Clinic code</label>

                <input
                  id="patientClinic"
                  type="text"
                  placeholder="Example: A001"
                  value={clinicCode}
                  onChange={(event) =>
                    setClinicCode(event.target.value.toUpperCase())
                  }
                  minLength={4}
                  maxLength={20}
                  required
                />
              </div>

              <div className="patient-form-group">
                <label htmlFor="patientDate">Token date</label>

                <input
                  id="patientDate"
                  type="date"
                  value={tokenDate}
                  onChange={(event) => setTokenDate(event.target.value)}
                  required
                />
              </div>

              <div className="patient-form-group">
                <label htmlFor="patientToken">Token number</label>

                <input
                  id="patientToken"
                  type="number"
                  min="1"
                  max="999999"
                  placeholder="Enter your token number"
                  value={tokenNumber}
                  onChange={(event) =>
                    setTokenNumber(event.target.value)
                  }
                  required
                />
              </div>

              <div className="patient-form-group">
                <label htmlFor="patientAccessCode">
                  Private access code
                </label>

                <input
                  id="patientAccessCode"
                  type="password"
                  placeholder="Enter your private access code"
                  value={accessCode}
                  onChange={(event) =>
                    setAccessCode(event.target.value)
                  }
                  minLength={20}
                  maxLength={64}
                  autoComplete="off"
                  required
                />
              </div>

              {error && (
                <div className="patient-error" role="alert">
                  {error}
                </div>
              )}

              {notice && (
                <div className="patient-notice" role="status">
                  {notice}
                </div>
              )}

              <button
                type="submit"
                className="patient-submit"
                disabled={loading}
              >
                {loading ? 'Verifying...' : 'Access my queue'}
              </button>
            </form>

            <p className="patient-security-note">
              Your private access code is verified securely. Never
              share it with anyone other than authorised clinic staff.
            </p>
            <div className="patient-login-footer">
  <span>© 2026 ClinicQ. Patient access only.</span>
  <a href="/">Staff portal →</a>
</div>
          </div>
        </section>

        
      </main>
    )
  }

  const status = patient.patient_status
  const isWaiting = status === 'WAITING'

  return (
    <main className="patient-page">
      <header className="patient-header">
        <a href="/" className="patient-brand">
          <span className="patient-brand-mark">C</span>
          <span>ClinicQ</span>
        </a>

        <div className="patient-header-actions">
          <span className="portal-label">PATIENT PORTAL</span>

          <button
            type="button"
            className="patient-logout"
            onClick={handleLogout}
          >
            Sign out
          </button>
        </div>
      </header>

      <section className="patient-dashboard">
        <div className="patient-dashboard-heading">
          <div>
            <p className="patient-eyebrow">YOUR CLINIC VISIT</p>
            <h1>Welcome to your queue</h1>
            <p>
              {patient.clinic_name} · Dr. {patient.doctor_name}
            </p>
          </div>
          <div className={`live-indicator ${liveStatus}`}>
  <span />
  {liveStatus === 'connected'
    ? 'Live updates connected'
    : liveStatus === 'reconnecting'
      ? 'Reconnecting...'
      : 'Connecting...'}
</div>

          <button
            type="button"
            className="patient-refresh"
            onClick={refreshPatientDetails}
            disabled={refreshing}
          >
            {refreshing ? 'Refreshing...' : '↻ Refresh status'}
          </button>
        </div>

        {error && (
          <div className="patient-error" role="alert">
            {error}
          </div>
        )}

        <section className="patient-token-card">
          <div className="patient-token-heading">
            <div>
              <span className="patient-eyebrow">YOUR TOKEN</span>
              <h2>#{patient.token_number}</h2>
            </div>

            <span className={`patient-status ${status?.toLowerCase()}`}>
              {getStatusText(status)}
            </span>
          </div>

          <div className="patient-token-meta">
            <div>
              <span>Clinic code</span>
              <strong>{patient.clinic_code}</strong>
            </div>

            <div>
              <span>Token date</span>
              <strong>{formatDate(patient.token_date)}</strong>
            </div>

            <div>
              <span>Joined queue</span>
              <strong>{formatTime(patient.joined_at)}</strong>
            </div>
          </div>
        </section>

        <section className="patient-metrics">
          <article className="patient-metric-card">
            <span>Queue position</span>

            <strong>
              {isWaiting ? `#${patient.queue_position}` : '—'}
            </strong>

            <p>
              {isWaiting
                ? 'Your current position'
                : 'Not currently waiting'}
            </p>
          </article>

          <article className="patient-metric-card">
            <span>Patients ahead</span>

            <strong>
              {isWaiting ? patient.waiting_ahead : '—'}
            </strong>

            <p>Patients waiting before you</p>
          </article>

          <article className="patient-metric-card">
            <span>Currently serving</span>

            <strong>
              {patient.current_serving_token_number
                ? `#${patient.current_serving_token_number}`
                : '—'}
            </strong>

            <p>Active consultation</p>
          </article>
        </section>



<section className="patient-wait-panel">
  <div className="patient-wait-icon">
    {patient.prediction_status === 'NEXT_UP'
      ? '✓'
      : patient.prediction_status === 'DOCTOR_TAKING_LONGER'
        ? '!'
        : '◷'}
  </div>

  <div>
    <span className="patient-eyebrow">
      {patient.prediction_status === 'NEXT_UP'
        ? 'YOUR TURN'
        : patient.prediction_status === 'DOCTOR_TAKING_LONGER'
          ? 'WAITING UPDATE'
          : 'ESTIMATED WAITING TIME'}
    </span>

    <h2>
      {patient.prediction_status === 'NEXT_UP'
        ? 'You are next in line!'
        : patient.prediction_status === 'DOCTOR_TAKING_LONGER'
          ? 'Doctor taking longer than usual'
          : patient.estimated_wait || 'Not available yet'}
    </h2>

    <p>
      {patient.prediction_label ||
        'Your waiting-time information will update as the queue progresses.'}
    </p>
  </div>
</section>

{/* ACTIVE EMERGENCY AND CLINIC MESSAGES */}
{(patient.active_disruptions || []).map((disruption) => {
  const isEmergency = disruption.event_type === 'EMERGENCY'

  return (
    <section
      key={disruption.id}
      className={`patient-disruption-alert ${
        isEmergency ? 'emergency' : 'custom'
      }`}
      role="alert"
    >
      <div className="patient-disruption-heading">
        <strong>
          {isEmergency ? 'EMERGENCY ALERT' : 'CLINIC UPDATE'}
        </strong>

        {disruption.expected_delay_minutes > 0 && (
          <span>
            +{disruption.expected_delay_minutes} min delay
          </span>
        )}
      </div>

      <p>{disruption.public_message}</p>
    </section>
  )
})}


        <section className="patient-clinic-status">
          <span
            className={
              patient.queue_paused
                ? 'patient-status-indicator paused'
                : 'patient-status-indicator running'
            }
          />

          <div>
            <strong>
              {patient.queue_paused
                ? 'Clinic queue is paused'
                : 'Clinic queue is active'}
            </strong>

            <p>
              {patient.queue_paused
                ? 'New consultations are temporarily paused.'
                : 'Your clinic queue is currently running.'}
            </p>
          </div>
        </section>

        <p className="patient-dashboard-note">
          Queue information may change as consultations progress.
          Use Refresh status to check for the latest update.
        </p>
      </section>

      <footer className="patient-footer">
        ClinicQ · Queue Prediction & Crowd Management
        <a href="/">Staff portal</a>
      </footer>
    </main>
  )
}

export default PatientPortal
