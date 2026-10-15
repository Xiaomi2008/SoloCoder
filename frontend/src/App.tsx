import { AgentProvider } from './contexts/AgentContext'
import AppContent from './AppContent'

function App() {
  return (
    <AgentProvider>
      <AppContent />
    </AgentProvider>
  )
}

export default App
