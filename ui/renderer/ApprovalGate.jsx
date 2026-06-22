import { useEffect, useRef } from 'react'

function CheckIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M20 6 9 17l-5-5" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function XIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="m6 6 12 12M18 6 6 18" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
    </svg>
  )
}

export default function ApprovalGate({ preview, actionType, onConfirm, onCancel }) {
  const ref = useRef(null)

  useEffect(() => {
    if (!ref.current) {
      return undefined
    }

    const resize = () => {
      const height = ref.current.getBoundingClientRect().height + 32
      window.winky?.resizeApproval?.(Math.ceil(height))
    }

    resize()
    const observer = new ResizeObserver(resize)
    observer.observe(ref.current)
    return () => observer.disconnect()
  }, [preview])

  return (
    <div ref={ref} className="approval-card" aria-label={`Approve ${actionType}`}>
      <div className="approval-state-pill">
        <span className="approval-state-dot" />
        <span>Waiting for approval</span>
      </div>
      <p className="approval-preview">{preview}</p>
      <div className="approval-actions">
        <button onClick={onConfirm} className="approval-button approval-confirm" aria-label="Confirm">
          <CheckIcon className="approval-icon" />
        </button>
        <button onClick={onCancel} className="approval-button approval-cancel" aria-label="Cancel">
          <XIcon className="approval-icon" />
        </button>
      </div>
    </div>
  )
}
