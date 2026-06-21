import brainIcon from './assets/brain_01.png'

function getViewState(state, alwaysOnMode, pushToTalkActive, agentRunning) {
  if (state === 'SPEAKING') {
    return 'speaking'
  }

  if (state === 'THINKING' || agentRunning) {
    return 'thinking'
  }

  if (pushToTalkActive || state === 'LISTENING') {
    return 'listening'
  }

  if (alwaysOnMode) {
    return 'always-on'
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

function Waveform({ mode }) {
  return (
    <div className={`waveform waveform-${mode}`} aria-hidden="true">
      <span />
      <span />
      <span />
    </div>
  )
}

function Brain() {
  return (
    <div className="brain-orbit" aria-hidden="true">
      <img src={brainIcon} alt="" />
    </div>
  )
}

function StatusVisual({ viewState }) {
  if (viewState === 'thinking') {
    return <Brain />
  }

  if (viewState === 'speaking') {
    return <Waveform mode="speaking" />
  }

  if (viewState === 'listening') {
    return <Waveform mode="listening" />
  }

  return <Waveform mode="idle" />
}

export default function Notch({ state, alwaysOnMode, pushToTalkActive, agentRunning }) {
  const viewState = getViewState(state, alwaysOnMode, pushToTalkActive, agentRunning)
  const label = getLabel(viewState)

  return (
    <main className="notch-stage">
      <section className={`notch figma-notch state-${viewState}`} aria-label={`Winky status: ${label}`}>
        <svg className="notch-shape" viewBox="0 0 329 40" preserveAspectRatio="none" aria-hidden="true">
          <path d="M0 0H329V22C329 31.941 320.941 40 311 40H18C8.059 40 0 31.941 0 22V0Z" />
        </svg>

        <p className="notch-label">{label}</p>

        <div className="notch-visual">
          <StatusVisual viewState={viewState} />
        </div>
      </section>
    </main>
  )
}
