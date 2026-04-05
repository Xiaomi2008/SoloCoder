import React from 'react'
import { useAgent } from '../../contexts/AgentContext'

function TurnCounter() {
  const { state } = useAgent()

  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="text-gray-600">Turns:</span>
      <span className="font-medium text-gray-900">{state.turnCount}</span>
    </div>
  )
}

export default TurnCounter
