import React from 'react'
import { createRoot } from 'react-dom/client'
import CanvasOverlay from './CanvasOverlay.jsx'
import './canvas.css'

createRoot(document.getElementById('canvas-root')).render(
  <React.StrictMode>
    <CanvasOverlay />
  </React.StrictMode>,
)
