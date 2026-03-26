import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { act, StrictMode } from 'react'
import { AgentProvider, useAgent } from '../AgentContext'
import { getLatestMockWebSocket, getMockWebSocketInstances, MockWebSocket } from '../../test/setup'
import type { Message } from '../../types/agent'

const STORAGE_KEY = 'solocoder_messages'

function TestConsumer() {
  const { state, sendMessage, resetSession } = useAgent()

  return (
    <>
      <div data-testid="message-count">{state.messages.length}</div>
      <button type="button" onClick={() => sendMessage('hello from test')}>
        Send message
      </button>
      <button type="button" onClick={resetSession}>
        Reset session
      </button>
    </>
  )
}

function renderAgentProvider() {
  return render(
    <AgentProvider>
      <TestConsumer />
    </AgentProvider>
  )
}

function createStoredMessage(): Message {
  return {
    id: 'stored-message',
    role: 'user',
    content: 'Restored from storage',
    timestamp: new Date('2026-03-24T12:00:00.000Z'),
  }
}

describe('AgentProvider live-tab initialization', () => {
  it('closes the previous socket during StrictMode remounts so only the latest session stays live', () => {
    render(
      <StrictMode>
        <AgentProvider>
          <TestConsumer />
        </AgentProvider>
      </StrictMode>
    )

    const [firstSocket, secondSocket] = getMockWebSocketInstances()

    expect(getMockWebSocketInstances()).toHaveLength(2)
    expect(firstSocket?.readyState).toBe(MockWebSocket.CLOSED)
    expect(secondSocket?.readyState).toBe(MockWebSocket.CONNECTING)
    expect(firstSocket?.url).not.toBe(secondSocket?.url)
  })

  it('starts with empty messages even when localStorage contains prior chat', () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([createStoredMessage()]))

    renderAgentProvider()

    expect(screen.getByTestId('message-count')).toHaveTextContent('0')
  })

  it('treats a fresh provider mount like a reload and starts empty instead of restoring prior messages', async () => {
    const firstRender = renderAgentProvider()

    await act(async () => {
      getLatestMockWebSocket()?.mockOpen()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => {
      expect(screen.getByTestId('message-count')).toHaveTextContent('1')
    })

    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()

    firstRender.unmount()
    renderAgentProvider()

    expect(screen.getByTestId('message-count')).toHaveTextContent('0')
  })

  it('keeps accumulating messages in memory during the current mounted session', async () => {
    renderAgentProvider()

    await act(async () => {
      getLatestMockWebSocket()?.mockOpen()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => {
      expect(screen.getByTestId('message-count')).toHaveTextContent('1')
    })

    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => {
      expect(screen.getByTestId('message-count')).toHaveTextContent('2')
    })
  })

  it('resetSession clears UI state and reconnects with a fresh live session', async () => {
    renderAgentProvider()

    const initialSocket = getLatestMockWebSocket()

    await act(async () => {
      initialSocket?.mockOpen()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => {
      expect(screen.getByTestId('message-count')).toHaveTextContent('1')
    })

    fireEvent.click(screen.getByRole('button', { name: 'Reset session' }))

    const replacementSocket = getLatestMockWebSocket()

    expect(getMockWebSocketInstances()).toHaveLength(2)
    expect(screen.getByTestId('message-count')).toHaveTextContent('0')
    expect(initialSocket?.readyState).toBe(MockWebSocket.CLOSED)
    expect(replacementSocket).not.toBe(initialSocket)
    expect(replacementSocket?.url).not.toBe(initialSocket?.url)

    await act(async () => {
      replacementSocket?.mockOpen()
    })

    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))

    await waitFor(() => {
      expect(screen.getByTestId('message-count')).toHaveTextContent('1')
    })

    expect(initialSocket?.sent).toHaveLength(2)
    expect(replacementSocket?.sent).toHaveLength(2)
  })
})
