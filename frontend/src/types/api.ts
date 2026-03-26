export interface ModelInfo {
  id: string
  name: string
  object: string
  owned_by: string
}

export interface ChatSession {
  id: string
  name: string
  created_at: string
  messages: Message[]
}

export interface Message {
  role: string
  content: string
}
