import React from 'react'
import { useAgent } from '../../contexts/AgentContext'
import apiClient from '../../lib/api'
import TurnCounter from './TurnCounter'
import { ModelInfo } from '../../types/api'

function StatusPanel() {
  const { state, updateModel, resetSession } = useAgent()
  const [models, setModels] = React.useState<ModelInfo[]>([])

  React.useEffect(() => {
    const loadModels = async () => {
      try {
        const response = await apiClient.getModels()
        setModels(response.models)
      } catch (error) {
        console.error('Failed to load models:', error)
      }
    }

    loadModels()
  }, [])

  const handleNewSession = () => {
    resetSession()
  }

  return (
    <div className="w-80 bg-white border-r border-gray-200 flex flex-col">
      <div className="p-4 border-b border-gray-200">
        <h1 className="text-lg font-bold text-gray-900">SoloCoder</h1>
        <p className="text-sm text-gray-500">AI Coding Assistant</p>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        <div className="mb-6">
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Model
          </label>
          {models.length > 0 ? (
            <select
              value={state.currentModel}
              onChange={(e) => updateModel(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {models.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.name}
                </option>
              ))}
            </select>
          ) : (
            <div className="w-full py-2 px-3 text-sm text-center text-gray-500 bg-gray-50 rounded-md">
              Loading...
            </div>
          )}
        </div>

        <div className="mb-4">
          <div
            className={`flex items-center gap-2 px-3 py-2 rounded-md text-sm ${
              state.connected
                ? 'bg-green-50 text-green-800'
                : 'bg-red-50 text-red-800'
            }`}
          >
            <span
              className={`w-2 h-2 rounded-full ${
                state.connected ? 'bg-green-500' : 'bg-red-500'
              }`}
            ></span>
            {state.connected ? 'Connected' : 'Disconnected'}
          </div>
        </div>

        <div className="mb-4">
          <TurnCounter />
        </div>

        <hr className="border-gray-200 my-4" />

        <button
          onClick={handleNewSession}
          className="w-full py-2 px-4 border border-gray-300 rounded-md text-sm font-medium text-gray-700 bg-white hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          New Session
        </button>
      </div>
    </div>
  )
}

export default StatusPanel
