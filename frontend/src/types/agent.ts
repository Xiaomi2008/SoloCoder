export type Role = 'user' | 'assistant'

export interface TextBlock {
  type: 'text'
  text: string
}

export interface ToolUseBlock {
  type: 'tool_use'
  name: string
  input: Record<string, unknown>
  id?: string
}

export interface ToolResultBlock {
  type: 'tool_result'
  name: string
  content: string
  tool_use_id?: string
  is_error?: boolean
}

export type Block = TextBlock | ToolUseBlock | ToolResultBlock

export interface Message {
  id: string
  role: Role
  content: string
  blocks?: Block[]
  timestamp: Date
}

export type AppErrorName =
  | 'NONE'
  | 'CONNECTION_ERROR'
  | 'AUTH_ERROR'
  | 'RATE_LIMIT_ERROR'
  | 'TIMEOUT_ERROR'
  | 'INTERNAL_ERROR'

export interface AppError {
  name: AppErrorName
  message: string
  timestamp: Date
}


export interface ToolCall {
  name: string
  id: string
  params?: Record<string, unknown> | string
  input?: Record<string, unknown>
  status: 'running' | 'executing' | 'complete' | 'error'
}

export interface CurrentTurnState {
  inProgress: boolean
  toolCalls?: ToolCall[]
}

export interface AgentState {
  messages: Message[]
  connected: boolean
  currentModel: string
  currentTurn: CurrentTurnState
  error: AppError | null
}
