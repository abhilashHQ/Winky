import { useEffect, useState } from 'react'
import Notch from './Notch.jsx'

export default function App() {
  const [state, setState] = useState('IDLE')
  const [alwaysOnMode, setAlwaysOnMode] = useState(false)
  const [pushToTalkActive, setPushToTalkActive] = useState(false)
  const [speechActive, setSpeechActive] = useState(false)
  const [agentRunning, setAgentRunning] = useState(false)
  const [approvalRequest, setApprovalRequest] = useState(null)

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
        setSpeechActive(false)
        if (msg.alwaysOnMode) {
          setPushToTalkActive(false)
        }
      }

      if (msg.type === 'push_to_talk') {
        setPushToTalkActive(Boolean(msg.active))
      }

      if (msg.type === 'input_status') {
        setAlwaysOnMode(msg.mode === 'always_on' && Boolean(msg.active))
        setPushToTalkActive(msg.mode === 'push_to_talk' && Boolean(msg.active))
        if (!msg.active) {
          setSpeechActive(false)
        }
      }

      if (msg.type === 'speech_status') {
        setSpeechActive(Boolean(msg.active))
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
        setApprovalRequest({
          callId: msg.call_id,
          preview: msg.preview,
          actionType: msg.action_type,
        })
      }

      if (msg.type === 'dismiss') {
        setApprovalRequest(null)
      }
    })
  }, [])

  function respondToApproval(approved) {
    if (!approvalRequest) {
      return
    }

    window.winky?.sendToHarness?.({
      type: 'approval_response',
      call_id: approvalRequest.callId,
      approved,
    })
    setApprovalRequest(null)
  }

  return (
    <Notch
      state={state}
      alwaysOnMode={alwaysOnMode}
      pushToTalkActive={pushToTalkActive}
      speechActive={speechActive}
      agentRunning={agentRunning}
      approvalRequest={approvalRequest}
      onApprovalConfirm={() => respondToApproval(true)}
      onApprovalCancel={() => respondToApproval(false)}
    />
  )
}
