const BAR_BASE = 'w-1 rounded-full bg-white/80 shadow-[0_0_12px_rgba(255,255,255,0.35)]'

function ListeningVisual() {
  return (
    <div className="flex h-5 items-center gap-1.5" aria-label="Listening">
      <span className={`${BAR_BASE} h-2 animate-pulse opacity-45`} />
      <span className={`${BAR_BASE} h-3 animate-pulse opacity-55 [animation-delay:120ms]`} />
      <span className={`${BAR_BASE} h-2 animate-pulse opacity-45 [animation-delay:240ms]`} />
    </div>
  )
}

function ThinkingVisual() {
  return (
    <div className="flex items-center justify-center" aria-label="Thinking">
      <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-white/90 shadow-[0_0_18px_rgba(255,255,255,0.55)]" />
    </div>
  )
}

function SpeakingVisual() {
  return (
    <div className="flex h-6 items-center gap-1.5" aria-label="Speaking">
      <span className={`${BAR_BASE} h-2 animate-bounce [animation-duration:700ms]`} />
      <span className={`${BAR_BASE} h-5 animate-bounce [animation-delay:100ms] [animation-duration:700ms]`} />
      <span className={`${BAR_BASE} h-3 animate-bounce [animation-delay:200ms] [animation-duration:700ms]`} />
      <span className={`${BAR_BASE} h-6 animate-bounce [animation-delay:300ms] [animation-duration:700ms]`} />
      <span className={`${BAR_BASE} h-2 animate-bounce [animation-delay:400ms] [animation-duration:700ms]`} />
    </div>
  )
}

function IdleVisual() {
  return <div className="h-1.5 w-16 rounded-full bg-white/20" aria-label="Idle" />
}

function AgentRunningVisual() {
  return (
    <div className="relative flex h-8 w-8 items-center justify-center" aria-label="Agent running">
      <div className="absolute inset-0 animate-spin rounded-full border border-white/15 border-t-white/90" />
      <div className="flex h-5 w-5 items-center justify-center rounded-md bg-white text-[11px] font-semibold leading-none text-black shadow-[0_0_18px_rgba(255,255,255,0.35)]">
        W
      </div>
    </div>
  )
}

function StateVisual({ state }) {
  if (state === 'LISTENING') {
    return <ListeningVisual />
  }

  if (state === 'THINKING') {
    return <ThinkingVisual />
  }

  if (state === 'SPEAKING') {
    return <SpeakingVisual />
  }

  return <IdleVisual />
}

export default function Notch({ state, agentRunning, expanded }) {
  return (
    <main className="flex h-screen w-screen items-start justify-center bg-black pt-2">
      <section
        className={`flex w-[560px] items-center justify-center overflow-hidden rounded-b-[12px] rounded-t-none bg-[rgba(0,0,0,0.85)] text-white shadow-[0_8px_28px_rgba(0,0,0,0.28)] backdrop-blur-[20px] transition-[height] duration-250 ease-out ${
          expanded ? 'h-[480px]' : 'h-9'
        }`}
      >
        {agentRunning ? <AgentRunningVisual /> : <StateVisual state={state} />}
      </section>
    </main>
  )
}
