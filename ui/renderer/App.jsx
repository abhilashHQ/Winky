import { useEffect, useState } from 'react'
import Notch from './Notch.jsx'

export default function App() {
  const [state, setState] = useState('IDLE')
  const [agentRunning, setAgentRunning] = useState(false)
  const [expanded, setExpanded] = useState(false)

  useEffect(() => {
    if (!window.winky?.onHarnessEvent) {
      return undefined
    }

    return window.winky.onHarnessEvent((msg) => {
      if (msg.type === 'state_change') {
        setState(msg.state)
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

      if (msg.type === 'approval_request') {
        setExpanded(true)
      }

      if (msg.type === 'dismiss') {
        setExpanded(false)
      }
    })
  }, [])

  return <Notch state={state} agentRunning={agentRunning} expanded={expanded} />
}
