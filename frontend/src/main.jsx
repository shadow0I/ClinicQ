import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'

import App from './App.jsx'
import PatientPortal from './PatientPortal.jsx'

const currentPath = window.location.pathname.replace(/\/+$/, '')

const page =
  currentPath === '/patient'
    ? <PatientPortal />
    : <App />

createRoot(document.getElementById('root')).render(
  <StrictMode>
    {page}
  </StrictMode>,
)