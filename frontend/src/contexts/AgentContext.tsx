import React, { createContext, useContext, useRef, useEffect } from 'react'
import { AgentState, Message, ToolCall } from '../types/agent'

const AgentContext = createContext<{
  state: AgentState
  sendMessage: (content: string) => void
  updateModel: (model: string) => void
  resetSession: () => void
  clearError: () => void
} | null>(null)

const DEFAULT_STATE: AgentState = {
  messages: [],
  connected: false,
  currentModel: 'Qwen/Qwen3.5-35B-A3B-GPTQ-Int4',
  currentTurn: { inProgress: false },
  error: null,
}

function createSessionId() {
  return Math.random().toString(36).substring(7)
}

export function AgentProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = React.useState<AgentState>(DEFAULT_STATE)
  const [sessionVersion, setSessionVersion] = React.useState(0)

  const wsRef = useRef<WebSocket | null>(null)
  const currentModelRef = useRef<string>('Qwen/Qwen3.5-35B-A3B-GPTQ-Int4')
  const sessionIdRef = useRef<string>(createSessionId())

  useEffect(() => {
    const sessionId = createSessionId()
    sessionIdRef.current = sessionId

    console.log('[AgentContext] Connecting...')
    const ws = new WebSocket(`ws://localhost:8000/api/v1/chat/stream?session_id=${sessionId}&model=${currentModelRef.current}`)
    wsRef.current = ws

    ws.onopen = () => {
      if (wsRef.current !== ws) return

      console.log('[AgentContext] Connected!')
      setState(prev => ({ ...prev, connected: true }))
      ws.send(JSON.stringify({ type: 'init', model: currentModelRef.current }))
    }

    ws.onmessage = (event) => {
      if (wsRef.current !== ws) return

      try {
        const data = JSON.parse(event.data)
        console.log('[AgentContext] Received:', data.type)

        if (data.type === 'message_start') {
          setState(prev => {
            return { ...prev, messages: [...prev.messages, {
              id: data.messageId || Math.random().toString(36).substring(7),
              role: 'assistant' as const,
              content: '',
              timestamp: new Date(),
              blocks: [],
            }]}
          })
        } else if (data.type === 'text_chunk') {
          setState(prev => {
            const messages = [...prev.messages]
            const last = messages[messages.length - 1]
            if (last && last.role === 'assistant') {
              messages[messages.length - 1] = { ...last, content: last.content + data.content }
            }
            return { ...prev, messages }
          })
        } else if (data.type === 'tool_call' || data.type === 'tool_use') {
          setState(prev => ({
            ...prev,
            currentTurn: {
              ...prev.currentTurn,
              toolCalls: [...(prev.currentTurn.toolCalls || []), {
                name: data.toolName || data.name || 'unknown',
                id: data.toolId || data.id || Math.random().toString(36).substring(7),
                params: typeof data.toolParams === 'string' ? data.toolParams : data.params,
                status: 'executing' as const,
              } satisfies ToolCall]
            }
          }))
        } else if (data.type === 'tool_result' || data.type === 'tool_response') {
          setState(prev => {
            const messages = [...prev.messages]
            for (let i = messages.length - 2; i >= 0; i--) {
              if (messages[i].role === 'assistant') {
                const toolBlock = {
                  type: 'tool_result',
                  name: data.toolName || data.name || 'unknown',
                  content: data.toolResult || data.result || '',
                  is_error: data.toolError || data.error || false,
                } as any
                if (data.toolId) toolBlock.tool_use_id = data.toolId
                const blocks = messages[i].blocks || []
                blocks.push(toolBlock)
                messages[i] = { ...messages[i], blocks }
                const toolCalls = prev.currentTurn.toolCalls?.map(tc =>
                  tc.id === (data.toolId || data.id)
                    ? { ...tc, status: ((data.toolError || data.error) ? 'error' : 'complete') as ToolCall['status'] }
                    : tc
                ) || []
                return { ...prev, messages, currentTurn: { ...prev.currentTurn, toolCalls } }
              }
            }
            return { ...prev, messages }
          })
        } else if (data.type === 'message_end') {
          console.log('[AgentContext] Complete')
          setState(prev => ({
            ...prev,
            currentTurn: {
              inProgress: false,
              toolCalls: prev.currentTurn.toolCalls?.map(tc => ({ ...tc, status: 'complete' as const }))
            }
          }))
        } else if (data.type === 'error') {
          console.error('[AgentContext] Error:', data.error)
          setState(prev => ({
            ...prev,
            error: { name: 'INTERNAL_ERROR', message: String(data.error), timestamp: new Date() },
            currentTurn: { inProgress: false },
          }))
        }
      } catch (e) {
        console.error('[AgentContext] Parse error:', e)
      }
    }

    ws.onclose = () => {
      if (wsRef.current !== ws) return

      wsRef.current = null
      setState(prev => ({ ...prev, connected: false }))
    }
    ws.onerror = (err) => console.error('[AgentContext] Error:', err)

    return () => {
      if (wsRef.current === ws) {
        wsRef.current = null
      }

      ws.onopen = null
      ws.onmessage = null
      ws.onclose = null
      ws.onerror = null

      if (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN) {
        ws.close()
      }
    }
  }, [sessionVersion])

  const sendMessage = React.useCallback((content: string) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      setState(prev => ({ ...prev, error: { name: 'CONNECTION_ERROR', message: 'Not connected', timestamp: new Date() } }))
      return
    }
    const userMessage: Message = {
      id: Math.random().toString(36).substring(7),
      role: 'user',
      content,
      timestamp: new Date(),
    }
    setState(prev => ({
      ...prev,
      messages: [...prev.messages, userMessage],
      currentTurn: { inProgress: true },
    }))
    wsRef.current.send(JSON.stringify({ type: 'message', content }))
  }, [])

  const updateModel = React.useCallback((model: string) => {
    currentModelRef.current = model
    setState(prev => ({ ...prev, currentModel: model }))
  }, [])

  const resetSession = React.useCallback(() => {
    setState({ ...DEFAULT_STATE, currentModel: currentModelRef.current })
    setSessionVersion(version => version + 1)
  }, [])

  const clearError = React.useCallback(() => {
    setState(prev => ({ ...prev, error: null }))
  }, [])

  return (
    <AgentContext.Provider value={{ state, sendMessage, updateModel, resetSession, clearError }}>
      {children}
    </AgentContext.Provider>
  )
}

export function useAgent() {
  const context = useContext(AgentContext)
  if (!context) throw new Error('useAgent must be used within AgentProvider')
  return context
}
