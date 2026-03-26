import '@testing-library/jest-dom/vitest'
import { beforeEach } from 'vitest'

function createMemoryStorage(): Storage {
  const store = new Map<string, string>()

  return {
    get length() {
      return store.size
    },
    clear() {
      store.clear()
    },
    getItem(key: string) {
      return store.get(key) ?? null
    },
    key(index: number) {
      return Array.from(store.keys())[index] ?? null
    },
    removeItem(key: string) {
      store.delete(key)
    },
    setItem(key: string, value: string) {
      store.set(key, value)
    },
  }
}

function ensureTestLocalStorage() {
  if (
    typeof window.localStorage?.getItem === 'function'
    && typeof window.localStorage?.setItem === 'function'
    && typeof window.localStorage?.clear === 'function'
  ) {
    return window.localStorage
  }

  const storage = createMemoryStorage()
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    writable: true,
    value: storage,
  })
  return storage
}

export class MockWebSocket {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3
  static instances: MockWebSocket[] = []

  readonly url: string
  readonly sent: string[] = []
  readyState = MockWebSocket.CONNECTING
  onopen: ((event: Event) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }

  send(data: string) {
    if (this.readyState !== MockWebSocket.OPEN) {
      throw new Error('WebSocket is not open')
    }

    this.sent.push(data)
  }

  close() {
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.(new CloseEvent('close'))
  }

  mockOpen() {
    this.readyState = MockWebSocket.OPEN
    this.onopen?.(new Event('open'))
  }

  mockMessage(data: unknown) {
    const payload = typeof data === 'string' ? data : JSON.stringify(data)
    this.onmessage?.(new MessageEvent('message', { data: payload }))
  }

  mockError() {
    this.onerror?.(new Event('error'))
  }
}

export function getMockWebSocketInstances() {
  return MockWebSocket.instances
}

export function getLatestMockWebSocket() {
  return MockWebSocket.instances[MockWebSocket.instances.length - 1]
}

Object.defineProperty(globalThis, 'WebSocket', {
  configurable: true,
  writable: true,
  value: MockWebSocket,
})

ensureTestLocalStorage()

beforeEach(() => {
  MockWebSocket.instances.length = 0
  ensureTestLocalStorage().clear()
})
