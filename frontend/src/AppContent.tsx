import { useAgent } from './contexts/AgentContext'
import ChatContainer from './components/chat/ChatContainer'
import StatusPanel from './components/status/StatusPanel'
import ErrorBanner from './components/ui/ErrorBanner'

function AppContent() {
  const { state, clearError } = useAgent()

  return (
    <div className="flex h-screen bg-gray-50">
      {state.error && (
        <div className="fixed top-0 left-0 right-0 z-50">
          <ErrorBanner error={state.error} onDismiss={clearError} />
        </div>
      )}

      <StatusPanel />

      <main className="flex-1 overflow-hidden">
        <ChatContainer />
      </main>
    </div>
  )
}

export default AppContent
