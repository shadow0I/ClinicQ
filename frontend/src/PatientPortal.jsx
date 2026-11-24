
import { useCallback, useEffect, useRef, useState } from 'react'
import { QRCodeSVG } from 'qrcode.react'
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
  const [showSelfRegistration, setShowSelfRegistration] = useState(false)
  const [publicClinics, setPublicClinics] = useState([])
  const [publicDoctors, setPublicDoctors] = useState([])
  const [selectedClinicCode, setSelectedClinicCode] = useState('')
  const [selectedDoctorId, setSelectedDoctorId] = useState('')
  const [selfRegistration, setSelfRegistration] = useState({
    patient_name: '', age: '', sex: '', mobile_number: '',
    visit_reason: 'General check-up', voice_reminder_consent: false,
  })
  const [selfRegisterLoading, setSelfRegisterLoading] = useState(false)
  const [selfRegisterError, setSelfRegisterError] = useState('')
  const [registrationReceipt, setRegistrationReceipt] = useState(null)
  const [receiptCopied, setReceiptCopied] = useState(false)
  const [voiceOptOutLoading, setVoiceOptOutLoading] = useState(false)

  useEffect(() => {
    if (!showSelfRegistration || publicClinics.length) return
    let active = true
    patientRequest('/api/patient/clinics')
      .then((data) => {
        if (active) setPublicClinics(data.clinics || [])
      })
      .catch((err) => {
        if (active) setSelfRegisterError(err.message || 'Unable to load clinics.')
      })
    return () => { active = false }
  }, [showSelfRegistration, publicClinics.length])

  useEffect(() => {
    if (!selectedClinicCode) {
      setPublicDoctors([])
      setSelectedDoctorId('')
      return
    }
    let active = true
    setPublicDoctors([])
    setSelectedDoctorId('')
    patientRequest(`/api/patient/clinics/${encodeURIComponent(selectedClinicCode)}/doctors`)
      .then((data) => {
        if (active) setPublicDoctors(data.doctors || [])
      })
      .catch((err) => {
        if (active) setSelfRegisterError(err.message || 'Unable to load doctors.')
      })
    return () => { active = false }
  }, [selectedClinicCode])

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

  async function handleSelfRegistration(event) {
    event.preventDefault()
    setSelfRegisterLoading(true)
    setSelfRegisterError('')
    setNotice('')

    try {
      if (!selectedClinicCode || !selectedDoctorId) {
        throw new Error('Select a clinic and doctor to continue.')
      }
      const receipt = await patientRequest('/api/patient/register', {
        method: 'POST',
        body: JSON.stringify({
          clinic_code: selectedClinicCode,
          doctor_id: selectedDoctorId,
          ...selfRegistration,
          age: Number(selfRegistration.age),
          patient_name: selfRegistration.patient_name.trim(),
          mobile_number: selfRegistration.mobile_number.trim(),
        }),
      })
      const data = await patientRequest('/api/patient/me')
      setRegistrationReceipt(receipt)
      setReceiptCopied(false)
      setPatient(data.patient)
      setNotice('Registration completed. Save your private access code before leaving this page.')
      setAccessCode('')
    } catch (err) {
      setSelfRegisterError(err.message || 'Unable to complete registration.')
    } finally {
      setSelfRegisterLoading(false)
    }
  }

  async function copyRegistrationCode() {
    if (!registrationReceipt?.patient_access_code) return
    try {
      await navigator.clipboard.writeText(registrationReceipt.patient_access_code)
      setReceiptCopied(true)
    } catch {
      setSelfRegisterError('Clipboard unavailable. Select the code and copy it manually.')
    }
  }

  async function optOutOfVoiceReminders() {
    setVoiceOptOutLoading(true)
    setError('')
    try {
      await patientRequest('/api/patient/voice-reminders/opt-out', { method: 'POST' })
      setPatient((previous) => previous ? { ...previous, voice_reminder_consent: false } : previous)
      setNotice('Automated voice reminders have been turned off for this visit.')
    } catch (err) {
      setError(err.message || 'Unable to update your reminder preference.')
    } finally {
      setVoiceOptOutLoading(false)
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
      setRegistrationReceipt(null)
      setSelfRegistration({
        patient_name: '', age: '', sex: '', mobile_number: '',
        visit_reason: 'General check-up', voice_reminder_consent: false,
      })
      setSelectedClinicCode('')
      setSelectedDoctorId('')
      setShowSelfRegistration(false)
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

            <div className="patient-register-divider"><span>NEW TO THIS CLINIC?</span></div>
            <button
              type="button"
              className="patient-register-toggle"
              onClick={() => {
                setShowSelfRegistration((value) => !value)
                setSelfRegisterError('')
              }}
            >
              {showSelfRegistration ? 'Hide registration form' : 'Register yourself for a visit'}
            </button>

            {showSelfRegistration && (
              <form className="patient-self-register-form" onSubmit={handleSelfRegistration}>
                <div className="patient-form-group">
                  <label htmlFor="selfClinic">Choose clinic</label>
                  <select
                    id="selfClinic"
                    value={selectedClinicCode}
                    onChange={(event) => setSelectedClinicCode(event.target.value)}
                    required
                  >
                    <option value="">Select a clinic</option>
                    {publicClinics.map((clinic) => (
                      <option key={clinic.clinic_code} value={clinic.clinic_code}>
                        {clinic.name} ({clinic.clinic_code})
                      </option>
                    ))}
                  </select>
                  {publicClinics.length === 0 && (
                    <small>Loading available clinics, or no clinic has enabled online registration.</small>
                  )}
                </div>

                <div className="patient-form-group">
                  <label htmlFor="selfDoctor">Choose doctor</label>
                  <select
                    id="selfDoctor"
                    value={selectedDoctorId}
                    onChange={(event) => setSelectedDoctorId(event.target.value)}
                    required
                    disabled={!selectedClinicCode || publicDoctors.length === 0}
                  >
                    <option value="">Select a doctor</option>
                    {publicDoctors.map((doctor) => (
                      <option key={doctor.id} value={doctor.id}>{doctor.display_name}</option>
                    ))}
                  </select>
                </div>

                <div className="patient-form-group">
                  <label htmlFor="selfPatientName">Full name</label>
                  <input id="selfPatientName" value={selfRegistration.patient_name} minLength={2} maxLength={100} required
                    onChange={(event) => setSelfRegistration((form) => ({ ...form, patient_name: event.target.value }))} />
                </div>
                <div className="patient-form-group">
                  <label htmlFor="selfPatientAge">Age</label>
                  <input id="selfPatientAge" type="number" min="0" max="120" value={selfRegistration.age} required
                    onChange={(event) => setSelfRegistration((form) => ({ ...form, age: event.target.value }))} />
                </div>
                <div className="patient-form-group">
                  <label htmlFor="selfPatientSex">Sex</label>
                  <select id="selfPatientSex" value={selfRegistration.sex} required
                    onChange={(event) => setSelfRegistration((form) => ({ ...form, sex: event.target.value }))}>
                    <option value="">Select</option><option>Male</option><option>Female</option><option>Other</option><option>Prefer not to say</option>
                  </select>
                </div>
                <div className="patient-form-group">
                  <label htmlFor="selfPatientMobile">Mobile number</label>
                  <input id="selfPatientMobile" type="tel" inputMode="numeric" pattern="[6-9][0-9]{9}" maxLength={10} value={selfRegistration.mobile_number} required
                    placeholder="10-digit Indian mobile number"
                    onChange={(event) => setSelfRegistration((form) => ({ ...form, mobile_number: event.target.value.replace(/\D/g, '').slice(0, 10) }))} />
                </div>
                <div className="patient-form-group">
                  <label htmlFor="selfVisitReason">Visit type</label>
                  <select id="selfVisitReason" value={selfRegistration.visit_reason}
                    onChange={(event) => setSelfRegistration((form) => ({ ...form, visit_reason: event.target.value }))}>
                    <option>General check-up</option><option>Follow-up check-up</option><option>Other</option>
                  </select>
                </div>

                <label className="patient-ivr-consent">
                  <input type="checkbox" checked={selfRegistration.voice_reminder_consent}
                    onChange={(event) => setSelfRegistration((form) => ({ ...form, voice_reminder_consent: event.target.checked }))} />
                  <span>Optional: I agree to receive an automated voice reminder when I am close to my turn. Standard call charges may apply.</span>
                </label>

                {selfRegisterError && <div className="patient-error" role="alert">{selfRegisterError}</div>}
                <button type="submit" className="patient-submit" disabled={selfRegisterLoading || publicDoctors.length === 0}>
                  {selfRegisterLoading ? 'Registering...' : 'Create my queue token'}
                </button>
                <p className="patient-register-privacy">Your details are shared only with the clinic you select. Voice reminders are sent only when you opt in and the clinic has enabled calling.</p>
              </form>
            )}

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

        {registrationReceipt && (
          <section className="patient-registration-receipt" aria-live="polite">
            <div>
              <p className="patient-eyebrow">REGISTRATION COMPLETE</p>
              <h2>Your token is #{registrationReceipt.token_number}</h2>
              <p>{registrationReceipt.clinic_name} · Dr. {registrationReceipt.doctor_name}</p>
              <div className="patient-receipt-code">
                <span>Private access code</span>
                <code>{registrationReceipt.patient_access_code}</code>
                <button type="button" onClick={copyRegistrationCode}>{receiptCopied ? 'Copied' : 'Copy code'}</button>
              </div>
              <small>Keep this code. You will need it to sign in again if you sign out or change devices.</small>
            </div>
            <div className="patient-receipt-qr">
              <QRCodeSVG value={`${window.location.origin}/patient#qr=${encodeURIComponent(registrationReceipt.qr_token)}`} size={124} level="H" />
              <span>Patient QR access</span>
            </div>
            <button type="button" className="patient-receipt-dismiss" onClick={() => setRegistrationReceipt(null)} aria-label="Dismiss registration details">×</button>
          </section>
        )}

        {patient.voice_reminder_consent && (
          <section className="patient-voice-consent" aria-label="Voice reminder preference">
            <div>
              <strong>Automated voice reminders are enabled for this visit.</strong>
              <p>You can turn them off at any time. Calls already in progress cannot be recalled.</p>
            </div>
            <button type="button" onClick={optOutOfVoiceReminders} disabled={voiceOptOutLoading}>
              {voiceOptOutLoading ? 'Updating...' : 'Turn off calls'}
            </button>
          </section>
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
