import { useEffect, useMemo, useRef, useState } from 'react'

const AUTO_CLEAR_MS = 8000
const FADE_MS = 220

function getStyle(annotation) {
  const style = annotation.style || {}
  return {
    stroke: style.stroke || '#8ee7ff',
    fill: style.fill || 'rgba(142, 231, 255, 0.08)',
    strokeWidth: Number(style.strokeWidth) || 2,
  }
}

function pointsToString(points) {
  return (points || []).map((point) => `${point.x},${point.y}`).join(' ')
}

function ArrowMarker() {
  return (
    <defs>
      <marker id="winky-arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
        <path d="M0,0 L0,6 L9,3 z" fill="#8ee7ff" />
      </marker>
    </defs>
  )
}

function Annotation({ annotation }) {
  const style = getStyle(annotation)

  if (annotation.kind === 'line') {
    const [start, end] = annotation.points || []
    if (!start || !end) return null
    return <line className="canvas-shape" x1={start.x} y1={start.y} x2={end.x} y2={end.y} stroke={style.stroke} strokeWidth={style.strokeWidth} />
  }

  if (annotation.kind === 'arrow') {
    const [start, end] = annotation.points || []
    if (!start || !end) return null
    return <line className="canvas-shape" x1={start.x} y1={start.y} x2={end.x} y2={end.y} stroke={style.stroke} strokeWidth={style.strokeWidth} markerEnd="url(#winky-arrow)" />
  }

  if (annotation.kind === 'rect') {
    return <rect className="canvas-shape" x={annotation.x} y={annotation.y} width={annotation.w} height={annotation.h} rx="10" fill={style.fill} stroke={style.stroke} strokeWidth={style.strokeWidth} />
  }

  if (annotation.kind === 'circle') {
    return <ellipse className="canvas-shape" cx={annotation.x + annotation.w / 2} cy={annotation.y + annotation.h / 2} rx={annotation.w / 2} ry={annotation.h / 2} fill={style.fill} stroke={style.stroke} strokeWidth={style.strokeWidth} />
  }

  if (annotation.kind === 'triangle' || annotation.kind === 'polygon') {
    return <polygon className="canvas-shape" points={pointsToString(annotation.points)} fill={style.fill} stroke={style.stroke} strokeWidth={style.strokeWidth} />
  }

  if (annotation.kind === 'text' || annotation.kind === 'formula') {
    const className = annotation.kind === 'formula' ? 'canvas-text canvas-formula' : 'canvas-text'
    return <text className={className} x={annotation.x} y={annotation.y}>{annotation.text}</text>
  }

  return null
}

export default function CanvasOverlay() {
  const [annotations, setAnnotations] = useState([])
  const [fading, setFading] = useState(false)
  const clearTimer = useRef(null)
  const fadeTimer = useRef(null)
  const viewBox = useMemo(() => {
    if (typeof window === 'undefined') {
      return '0 0 1 1'
    }
    return `0 0 ${window.innerWidth || 1} ${window.innerHeight || 1}`
  }, [])

  useEffect(() => {
    if (!window.winky?.onCanvasEvent) {
      return undefined
    }

    return window.winky.onCanvasEvent((msg) => {
      if (msg.type !== 'draw_annotation') {
        return
      }

      const nextAnnotations = msg.step?.annotations || []
      if (nextAnnotations.some((annotation) => annotation.kind === 'clear')) {
        clearNow()
        return
      }

      setFading(false)
      setAnnotations((current) => [...current, ...nextAnnotations])
      scheduleAutoClear()
    })
  }, [])

  function scheduleAutoClear() {
    if (clearTimer.current) {
      clearTimeout(clearTimer.current)
    }
    if (fadeTimer.current) {
      clearTimeout(fadeTimer.current)
    }

    clearTimer.current = setTimeout(() => {
      setFading(true)
      fadeTimer.current = setTimeout(() => {
        setAnnotations([])
        setFading(false)
        window.winky?.canvasCleared?.()
      }, FADE_MS)
    }, AUTO_CLEAR_MS)
  }

  function clearNow() {
    if (clearTimer.current) {
      clearTimeout(clearTimer.current)
      clearTimer.current = null
    }
    if (fadeTimer.current) {
      clearTimeout(fadeTimer.current)
      fadeTimer.current = null
    }
    setAnnotations([])
    setFading(false)
    window.winky?.canvasCleared?.()
  }

  return (
    <main className={`canvas-overlay${fading ? ' canvas-overlay-fading' : ''}`} aria-hidden="true">
      <svg className="canvas-svg" viewBox={viewBox} preserveAspectRatio="none">
        <ArrowMarker />
        {annotations.map((annotation, index) => (
          <Annotation key={annotation.id || `${annotation.kind}-${index}`} annotation={annotation} />
        ))}
      </svg>
    </main>
  )
}
