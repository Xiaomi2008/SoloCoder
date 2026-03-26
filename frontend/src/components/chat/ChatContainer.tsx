import { useRef, useEffect } from 'react'
import { useAgent } from '../../contexts/AgentContext'
import ChatMessage from './ChatMessage'
import ChatInput from './ChatInput'

function ChatContainer() {
  const { state } = useAgent()
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [state.messages])

  return (
    <div className="h-full flex flex-col">
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto p-6"
      >
        {state.messages.length === 0 ? (
          <div className="h-full flex items-center justify-center">
            <div className="text-center text-gray-500 max-w-sm">
              <p className="text-lg font-medium mb-2">Welcome to SoloCoder</p>
              <p className="text-sm">
                Start a conversation with your AI coding assistant. Ask questions,
                get code reviews, or let the agent help you build something new.
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            {state.messages.map((message) => (
              <ChatMessage key={message.id} message={message} />
            ))}
          </div>
        )}
      </div>
      <ChatInput />
    </div>
  )
}

export default ChatContainer
