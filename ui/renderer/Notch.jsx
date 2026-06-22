function getViewState(state, alwaysOnMode, pushToTalkActive, speechActive, agentRunning) {
  if (state === 'SPEAKING') {
    return 'speaking'
  }

  if (state === 'THINKING' || agentRunning) {
    return 'thinking'
  }

  if (alwaysOnMode) {
    return speechActive ? 'listening' : 'always-on'
  }

  if (pushToTalkActive || state === 'LISTENING') {
    return 'listening'
  }

  return 'listening'
}

function getLabel(viewState) {
  if (viewState === 'always-on') {
    return 'Always On'
  }

  if (viewState === 'thinking') {
    return 'Thinking...'
  }

  if (viewState === 'speaking') {
    return 'Speaking'
  }

  return 'Listening...'
}

function ListeningDots() {
  return (
    <div className="listening-dots" aria-hidden="true">
      <span />
      <span />
      <span />
    </div>
  )
}

function DotmSquare12() {
  return (
    <div className="dotm-square-12" aria-hidden="true">
      {Array.from({ length: 12 }, (_, index) => (
        <span key={index} />
      ))}
    </div>
  )
}

function SpeakingBars() {
  return (
    <div className="speaking-bars" aria-hidden="true">
      <span />
      <span />
      <span />
      <span />
    </div>
  )
}

function StatusVisual({ viewState }) {
  if (viewState === 'thinking') {
    return <DotmSquare12 />
  }

  if (viewState === 'speaking') {
    return <SpeakingBars />
  }

  if (viewState === 'listening') {
    return <ListeningDots />
  }

  return <ListeningDots />
}

export default function Notch({ state, alwaysOnMode, pushToTalkActive, speechActive, agentRunning }) {
  const viewState = getViewState(state, alwaysOnMode, pushToTalkActive, speechActive, agentRunning)
  const label = getLabel(viewState)

  return (
    <main className="notch-stage">
      <section className={`notch figma-notch state-${viewState}`} aria-label={`Winky status: ${label}`}>
        <div className="notch-shape" aria-hidden="true" />

        <div className="notch-label-wrap">
          <p key={label} className="notch-label">{label}</p>
        </div>

        <div className="notch-visual">
          <div key={viewState} className="notch-visual-transition">
            <StatusVisual viewState={viewState} />
          </div>
        </div>
      </section>
    </main>
  )
}
