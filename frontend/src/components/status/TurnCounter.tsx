import { useAgent } from '../../contexts/AgentContext'

function TurnCounter() {
  const { state } = useAgent()
  const turnCount = state.messages.filter(message => message.role === 'assistant').length

  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="text-gray-600">Turns:</span>
      <span className="font-medium text-gray-900">{turnCount}</span>
    </div>
  )
}

export default TurnCounter
