import React, { useState } from 'react'
import { useAgent } from '../../contexts/AgentContext'

interface ChatInputProps {
  onSend?: (message: string) => void
}

function ChatInput({ onSend }: ChatInputProps) {
  const [message, setMessage] = useState('')
  const { sendMessage, state } = useAgent()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!message.trim() || state.currentTurn.inProgress) return

    await sendMessage(message)
    setMessage('')
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="border-t border-gray-200 bg-white p-4"
    >
      <div className="flex gap-3">
        <textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              handleSubmit(e)
            }
          }}
          placeholder="Type your message..."
          disabled={state.currentTurn.inProgress || !state.connected}
          rows={1}
          className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-none disabled:opacity-50 disabled:cursor-not-allowed"
        />
        <button
          type="submit"
          disabled={!message.trim() || state.currentTurn.inProgress || !state.connected}
          className="px-4 py-2 bg-blue-500 text-white rounded-lg text-sm font-medium hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {state.currentTurn.inProgress ? 'Processing...' : 'Send'}
        </button>
      </div>
    </form>
  )
}

export default ChatInput
