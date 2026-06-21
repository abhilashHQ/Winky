import { useEffect, useState } from 'react'
import Notch from './Notch.jsx'

export default function App() {
  const [state, setState] = useState('IDLE')
  const [alwaysOnMode, setAlwaysOnMode] = useState(false)
  const [pushToTalkActive, setPushToTalkActive] = useState(false)
  const [agentRunning, setAgentRunning] = useState(false)

  useEffect(() => {
    if (!window.winky?.onHarnessEvent) {
      return undefined
    }

    return window.winky.onHarnessEvent((msg) => {
      if (msg.type === 'state_change') {
        setState(msg.state)
      }

      if (msg.type === 'mode_change') {
        setAlwaysOnMode(Boolean(msg.alwaysOnMode))
      }

      if (msg.type === 'push_to_talk') {
        setPushToTalkActive(Boolean(msg.active))
      }

      if (msg.type === 'input_status') {
        setPushToTalkActive(msg.mode === 'push_to_talk' && Boolean(msg.active))
      }

      if (msg.type === 'agent_status') {
        setAgentRunning(Boolean(msg.running))
      }

      if (msg.type === 'agent_spawned') {
        setAgentRunning(true)
      }

      if (msg.type === 'agent_done') {
        setAgentRunning(false)
      }
    })
  }, [])

  return (
    <Notch
      state={state}
      alwaysOnMode={alwaysOnMode}
      pushToTalkActive={pushToTalkActive}
      agentRunning={agentRunning}
    />
  )
}
